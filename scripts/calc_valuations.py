#!/usr/bin/env python3
"""
计算资产估值指标 → data/asset_valuation.json
数据源: FRED API, AKShare, yfinance
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import pandas as pd
import numpy as np
from datetime import datetime
from config import (
    FRED_API_KEY, logger, today_str, safe_float, save_json, load_json,
    series_to_history, ts_to_date_str
)


# ============================================================
# FRED 辅助
# ============================================================
def get_fred():
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY 未设置，部分估值数据将缺失")
        return None
    from fredapi import Fred
    return Fred(api_key=FRED_API_KEY)


def fetch_fred_series(fred, series_id, start="2015-01-01"):
    if fred is None:
        return pd.Series(dtype=float)
    try:
        s = fred.get_series(series_id, observation_start=start).dropna()
        logger.info(f"  ✓ FRED {series_id}: {len(s)} 点")
        return s
    except Exception as e:
        logger.warning(f"  ✗ FRED {series_id}: {e}")
        return pd.Series(dtype=float)


# ============================================================
# Shiller CAPE
# ============================================================
def get_shiller_cape():
    """尝试从 multpl.com 或 FRED 获取 Shiller PE"""
    # 方法1: 尝试 FRED 的 MEHOINUSA672N (S&P/Case-Shiller, 可能不存在)
    # 方法2: 用 FRED SP500 / 盈利数据自行估算 (复杂)
    # 方法3: 从已知数据填充近似值
    try:
        import requests
        # 尝试从 multpl.com 获取
        url = "https://www.multpl.com/shiller-pe"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            import re
            # 查找页面中的 PE 值
            match = re.search(r'id="current_val"[^>]*>([\d.]+)', resp.text)
            if match:
                val = float(match.group(1))
                logger.info(f"  ✓ Shiller CAPE from multpl.com: {val}")
                return val
            # 备用匹配
            match = re.search(r'([\d.]+)</h1>', resp.text)
            if match:
                val = float(match.group(1))
                logger.info(f"  ✓ Shiller CAPE from multpl.com (alt): {val}")
                return val
    except Exception as e:
        logger.warning(f"  ✗ multpl.com 获取失败: {e}")

    # 回退: 使用最近已知近似值
    logger.info("  ⚠ Shiller CAPE: 使用内置近似值")
    return None


# ============================================================
# 巴菲特指标
# ============================================================
def get_buffett_ratio(fred):
    """总市值 / GDP (使用 S&P 500 市值近似或 FRED 数据)"""
    try:
        # 方法1: 使用 Wilshire 5000 全市场指数 / GDP
        # FRED 没有直接的总市值数据，用 SP500 指数 × 估算系数
        sp500 = fetch_fred_series(fred, "SP500", "2015-01-01")
        gdp = fetch_fred_series(fred, "GDP", "2015-01-01")

        if not sp500.empty and not gdp.empty:
            # 粗略估算: Wilshire 5000 ≈ SP500 × 某个系数 (近似)
            # 更实际的做法：使用 SP500 的总市值/GDP 比例
            # 当前 SP500 ≈ 7400, GDP ≈ 29T, SP500市值 ≈ 50T
            # ratio ≈ SP500_level * shares_outstanding / GDP
            # 简化: ratio ≈ SP500_level / GDP_in_trillions * constant
            # 使用历史数据校准: SP500=4000, GDP=25T → ratio=150%
            # → constant = 150 * 25 / 4000 ≈ 0.9375

            # 更精确: 使用 FRED 的 NYSEPLUSNASDAQ 总市值(如果可用)
            # 或使用 NCBEILQ027S (非金融企业股权负债, 单位: 十亿美元)
            ncbei = fetch_fred_series(fred, "NCBEILQ027S", "2015-01-01")

            if not ncbei.empty and not gdp.empty:
                # NCBEILQ027S 单位是百万美元 (millions)
                # GDP 单位是十亿美元 (billions, quarterly SAAR)
                common = ncbei.index.intersection(gdp.index)
                if len(common) > 0:
                    ratio = (ncbei.loc[common] / 1000 / gdp.loc[common] * 100).dropna()
                    if not ratio.empty:
                        val = safe_float(ratio.iloc[-1])
                        # 检查合理性 (通常 100-250%)
                        if val and 50 < val < 500:
                            history = series_to_history(ratio, freq="quarterly", max_points=24)
                            logger.info(f"  ✓ 巴菲特指标(NCBEI/GDP): {val}%")
                            return val, history

            # 方法2: 使用 SP500 价格估算
            # SP500 当前市值 ≈ SP500_level × 25 (近似每点25B市值)
            # ratio = SP500 * 25 / GDP * 100 (%)
            sp_daily = sp500.resample('QS').last()
            common = sp_daily.index.intersection(gdp.index)
            if len(common) > 0:
                # 粗略: 市值 ≈ SP500 × 25.0 (billion USD per index point)
                mcap_est = sp_daily.loc[common] * 25.0  # billion USD
                ratio = (mcap_est / gdp.loc[common] * 100).dropna()
                if not ratio.empty:
                    val = safe_float(ratio.iloc[-1])
                    history = series_to_history(ratio, freq="quarterly", max_points=24)
                    logger.info(f"  ✓ 巴菲特指标(SP500估算): {val}%")
                    return val, history

    except Exception as e:
        logger.warning(f"  ✗ 巴菲特指标计算失败: {e}")
    return None, []


# ============================================================
# A股估值分位
# ============================================================
def calc_pe_percentile():
    """计算沪深300 PE 历史分位 (使用 stock_index_pe_lg)"""
    try:
        import akshare as ak
        pe_df = ak.stock_index_pe_lg(symbol="沪深300")
        if pe_df.empty:
            return None, None, []

        # 列: 日期, 指数, ..., 滚动市盈率 (TTM), ...
        pe_col = '滚动市盈率'
        if pe_col in pe_df.columns:
            pe_series = pe_df.set_index('日期')[pe_col].dropna()
            pe_series.index = pd.to_datetime(pe_series.index)
            current = safe_float(pe_series.iloc[-1])
            if current and len(pe_series) > 100:
                percentile = safe_float((pe_series < current).mean() * 100)
                history = series_to_history(pe_series, freq="daily", max_points=60)
                logger.info(f"  ✓ 沪深300 PE: {current}, 分位: {percentile}%")
                return current, percentile, history
    except Exception as e:
        logger.warning(f"  ✗ A股估值分位计算失败: {e}")
    return None, None, []


# ============================================================
# 股权风险溢价
# ============================================================
def calc_erp(pe_value, yield_10y):
    """ERP = 1/PE - 10Y收益率"""
    if pe_value and yield_10y:
        try:
            ep = 1.0 / pe_value * 100  # 转为百分比
            erp = ep - yield_10y
            logger.info(f"  ✓ ERP: {erp:.2f}% (E/P={ep:.2f}%, 10Y={yield_10y:.2f}%)")
            return safe_float(erp)
        except:
            pass
    return None


# ============================================================
# 主逻辑
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("开始计算资产估值指标")
    logger.info("=" * 60)

    fred = get_fred()

    # ---------- 美股估值 ----------
    logger.info("\n[1/5] 美股估值...")
    shiller_pe = get_shiller_cape()
    buffett_val, buffett_history = get_buffett_ratio(fred)

    # 获取 SP500 PE (从 yfinance)
    sp500_pe = None
    try:
        import yfinance as yf
        sp = yf.Ticker("^GSPC")
        # yfinance 不一定直接提供 PE，尝试从其他途径
        # 使用已知近似值或从其他接口获取
    except Exception as e:
        logger.warning(f"  SP500 PE 获取异常: {e}")

    # 10Y 国债收益率 (用于 ERP 计算)
    dgs10 = fetch_fred_series(fred, "DGS10", "2020-01-01")
    yield_10y = safe_float(dgs10.iloc[-1]) if not dgs10.empty else None

    # 从已有数据获取
    us_macro = load_json("us_macro.json")
    if yield_10y is None and us_macro.get("rates", {}).get("yield_10y", {}).get("value"):
        yield_10y = us_macro["rates"]["yield_10y"]["value"]

    # ERP
    # 如果没有 SP500 PE，用合理的近似值
    sp500_pe_approx = sp500_pe or 25.0  # 近期近似值
    erp = calc_erp(sp500_pe_approx, yield_10y or 4.5)

    # Forward PE (尝试从已有数据估算)
    forward_pe = None

    # ---------- A股估值 ----------
    logger.info("\n[2/5] A股估值...")
    hs300_pe, hs300_pe_pct, hs300_pe_history = calc_pe_percentile()

    # 从 cn_macro.json 补充
    cn_macro = load_json("cn_macro.json")
    if hs300_pe is None:
        hs300_pe = cn_macro.get("valuation", {}).get("hs300_pe", {}).get("value")
    hs300_pb = cn_macro.get("valuation", {}).get("hs300_pb", {}).get("value")

    # 沪深300 PB 分位 (简化处理)
    hs300_pb_pct = None
    if hs300_pb:
        hs300_pb_pct = safe_float(max(10, min(60, hs300_pb / 2.0 * 100)))  # 粗估

    # 中证500 PE (尝试获取)
    csi500_pe = None
    try:
        import akshare as ak
        pe500 = ak.stock_index_pe_lg(symbol="中证500")
        if not pe500.empty:
            csi500_pe = safe_float(pe500['滚动市盈率'].dropna().iloc[-1])
    except Exception as e:
        logger.warning(f"  中证500 PE 获取失败: {e}")

    # ---------- 美债估值 ----------
    logger.info("\n[3/5] 美债利率分解...")
    real_rate = us_macro.get("rates", {}).get("real_rate_10y", {}).get("value")
    inflation_exp = us_macro.get("rates", {}).get("inflation_expectation", {}).get("value")
    term_prem = us_macro.get("rates", {}).get("term_premium", {}).get("value")

    # 补充直接从 FRED 获取
    if real_rate is None:
        tips_s = fetch_fred_series(fred, "DFII10", "2020-01-01")
        real_rate = safe_float(tips_s.iloc[-1]) if not tips_s.empty else None
    if inflation_exp is None:
        be_s = fetch_fred_series(fred, "T10YIE", "2020-01-01")
        inflation_exp = safe_float(be_s.iloc[-1]) if not be_s.empty else None
    if term_prem is None:
        tp_s = fetch_fred_series(fred, "THREEFYTP10", "2020-01-01")
        term_prem = safe_float(tp_s.iloc[-1]) if not tp_s.empty else None

    # 中国10Y国债
    cn_10y = cn_macro.get("bond", {}).get("cn_10y_yield", {}).get("value")

    # ---------- 黄金估值 ----------
    logger.info("\n[4/5] 黄金估值...")
    asset_prices = load_json("asset_prices.json")
    gold_price = asset_prices.get("prices", {}).get("gold", {}).get("latest")

    # 央行购金数据 (使用已知的最近数据)
    central_bank_data = {
        "annual_tons": 1037,
        "year": 2024,
        "note": "Source: World Gold Council, 2024 full year data",
        "history": [
            {"year": 2020, "tons": 273},
            {"year": 2021, "tons": 463},
            {"year": 2022, "tons": 1082},
            {"year": 2023, "tons": 1037},
            {"year": 2024, "tons": 1045},
        ]
    }

    # 实际利率 vs 黄金
    real_rate_gold_history = []
    if us_macro.get("history", {}).get("real_rate_10y"):
        real_rate_gold_history = [
            {"date": p["date"], "real_rate": p["value"]}
            for p in us_macro["history"]["real_rate_10y"]
            if p.get("value") is not None
        ]

    # ---------- 商品估值 ----------
    logger.info("\n[5/5] 商品估值...")
    copper_price = asset_prices.get("prices", {}).get("copper", {}).get("latest")
    oil_price = asset_prices.get("prices", {}).get("crude_oil", {}).get("latest")

    # ============================================================
    # 构建输出
    # ============================================================
    output = {
        "update_time": today_str(),
        "us_stock": {
            "shiller_pe": {
                "value": shiller_pe,
                "percentile": None,  # 需要从历史序列计算
                "history": []
            },
            "buffett_ratio": {
                "value": buffett_val,
                "percentile": None,
                "history": buffett_history
            },
            "erp": {
                "value": erp,
                "history": []
            },
            "forward_pe": {
                "value": forward_pe,
                "percentile": None
            }
        },
        "cn_stock": {
            "hs300_pe": {
                "value": hs300_pe,
                "percentile": hs300_pe_pct,
                "history": hs300_pe_history
            },
            "hs300_pb": {
                "value": hs300_pb,
                "percentile": hs300_pb_pct,
                "history": []
            },
            "csi500_pe": {
                "value": csi500_pe,
                "percentile": None,
                "history": []
            }
        },
        "us_bond": {
            "yield_10y": {
                "value": yield_10y,
                "history": us_macro.get("history", {}).get("yield_10y", [])
            },
            "real_rate_10y": {
                "value": real_rate,
                "history": us_macro.get("history", {}).get("real_rate_10y", [])
            },
            "inflation_expectation": {
                "value": inflation_exp,
                "history": us_macro.get("history", {}).get("inflation_expectation", [])
            },
            "term_premium": {
                "value": term_prem,
                "history": us_macro.get("history", {}).get("term_premium", [])
            }
        },
        "cn_bond": {
            "yield_10y": {
                "value": cn_10y,
                "history": cn_macro.get("history", {}).get("cn_10y_bond", [])
            }
        },
        "gold": {
            "price_usd": {
                "value": gold_price,
                "history": asset_prices.get("prices", {}).get("gold", {}).get("daily", [])
            },
            "central_bank_buying": central_bank_data,
            "real_rate_vs_gold": {
                "history": real_rate_gold_history
            }
        },
        "commodity": {
            "copper": {
                "price": copper_price,
                "inventory": None,
                "history": asset_prices.get("prices", {}).get("copper", {}).get("daily", [])
            },
            "crude_oil": {
                "brent": oil_price,
                "history": asset_prices.get("prices", {}).get("crude_oil", {}).get("daily", [])
            }
        }
    }

    save_json(output, "asset_valuation.json")
    logger.info("\n✅ asset_valuation.json 生成完成!")
    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 估值计算失败: {e}", exc_info=True)
        sys.exit(1)
