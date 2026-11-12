#!/usr/bin/env python3
"""
获取全球资产价格数据 → data/asset_prices.json
数据源: yfinance
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import time
import pandas as pd
import numpy as np
from datetime import datetime
from config import logger, today_str, safe_float, save_json, series_to_history


# ============================================================
# 标的配置
# ============================================================
TICKERS = {
    "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
    "nasdaq": {"symbol": "^IXIC", "name": "Nasdaq Composite"},
    "gold": {"symbol": "GC=F", "name": "Gold Futures"},
    "crude_oil": {"symbol": "CL=F", "name": "WTI Crude Oil"},
    "copper": {"symbol": "HG=F", "name": "Copper Futures", "unit_factor": 2204.62},  # P0-2 裁决: 美元/磅×2204.62=美元/吨（写库时换算）
    "vix": {"symbol": "^VIX", "name": "VIX"},
    "usd_index": {"symbol": "DX-Y.NYB", "name": "US Dollar Index"},
    "us_10y": {"symbol": "^TNX", "name": "US 10Y Treasury"},
    "btc": {"symbol": "BTC-USD", "name": "Bitcoin"},
}

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


# ============================================================
# A股资产采集（akshare）—— P1 回填（2026-11-03 CIO 挂账①）
# 估值表 cn_stock/cn_bond 趋势列数据源。本机已实测 akshare 1.18.94 双通：
# 沪深300 stock_zh_index_daily 单次全历史（新浪源）；中债10Y bond_china_yield
# 月批回填（中债登，筛「中债国债收益率曲线」取「10年」列），限流0.5s+重试3次。
# 均不依赖东财。失败降级：仅告警不阻塞，对应 key 缺省→前端灰显「趋势 —」。
# ============================================================
def _month_ranges(n_months):
    """近 n 个月的 (start,end) YYYYMMDD 列表（旧→新）；当月 end 截断至今日。"""
    out = []
    now = datetime.now()
    for i in range(n_months - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        start = f"{y}{m:02d}01"
        nxt = pd.Timestamp(y + 1, 1, 1) if m == 12 else pd.Timestamp(y, m + 1, 1)
        end_ts = min(nxt - pd.Timedelta(days=1), pd.Timestamp(now.date()))
        if end_ts < pd.Timestamp(start):
            continue
        out.append((start, end_ts.strftime("%Y%m%d")))
    return out


def _hist_from_pairs(dates, values):
    """规整 (日期, 数值) 为 calc_metrics 兼容 DataFrame（Close 列）。"""
    return pd.DataFrame(
        {"Close": pd.to_numeric(values, errors="coerce").values},
        index=pd.to_datetime(dates).values,
    )


def _daily_tail(hist, n=60):
    close = hist["Close"].dropna().tail(n)
    return [{"date": idx.strftime("%Y-%m-%d"), "value": safe_float(v)} for idx, v in close.items()]


def fill_cn_assets(prices):
    """采集 hs300（沪深300 价格指数）+ cn_10y_yield（中债10Y收益率）写入 prices。"""
    try:
        import akshare as ak
    except ImportError:
        logger.warning("  akshare 不可用，跳过 A股资产采集")
        return

    # --- 沪深300：单次拉全历史切片 5 年 ---
    try:
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=365 * 5 + 30)
            df = df[df["date"] >= cutoff]
            hist = _hist_from_pairs(df["date"], df["close"])
            latest, ma200, dd, mom, dev = calc_metrics(hist)
            prices["hs300"] = {
                "latest": latest, "ma200": ma200, "drawdown_from_high": dd,
                "momentum_3m": mom, "ma200_dev_pct": dev,
                "daily": _daily_tail(hist),
                "name": "沪深300", "symbol": "akshare:sh000300",
                "update_time": today_str(), "_source": "akshare(新浪)",
            }
            logger.info(f"  ✓ 沪深300: {latest}（5年窗 {len(hist)} 点）")
    except Exception as e:
        logger.warning(f"  ✗ 沪深300 采集失败: {e}")

    # --- 中债10Y：月批回填 62 个月（5年窗冗余），限流+重试 ---
    try:
        frames = []
        for start, end in _month_ranges(62):
            sub = None
            for attempt in range(MAX_RETRIES):
                try:
                    sub = ak.bond_china_yield(start_date=start, end_date=end)
                    break
                except Exception as e:
                    logger.warning(f"  ✗ 中债 {start}: {e} (attempt {attempt + 1})")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY * (attempt + 1) / 5)
            if sub is not None and not sub.empty:
                gov = sub[sub["曲线名称"] == "中债国债收益率曲线"]
                if not gov.empty:
                    frames.append(gov[["日期", "10年"]])
            time.sleep(0.5)  # 限流（回填限流方案，CIO 2026-11-01 批复）
        if frames:
            all_df = (pd.concat(frames)
                      .drop_duplicates(subset=["日期"]).sort_values("日期"))
            hist = _hist_from_pairs(all_df["日期"], all_df["10年"])
            latest, ma200, dd, mom, dev = calc_metrics(hist)
            prices["cn_10y_yield"] = {
                "latest": latest, "ma200": ma200, "drawdown_from_high": dd,
                "momentum_3m": mom, "ma200_dev_pct": dev,
                "daily": _daily_tail(hist),
                "name": "中国10Y国债收益率", "symbol": "akshare:bond_china_yield",
                "unit": "%",
                "unit_note": "收益率口径：收益率↑=债价↓（与价格类资产反向，同美债卡）",
                "update_time": today_str(), "_source": "akshare(中债登)",
            }
            logger.info(f"  ✓ 中债10Y: {latest}%（{len(hist)} 点）")
    except Exception as e:
        logger.warning(f"  ✗ 中债10Y 采集失败: {e}")


def main_cn_only():
    """回填模式：仅采集 A股两序列 merge 进现有 asset_prices.json（保留 CI 产的其他序列原样）。
    用法: python3 fetch_asset_data.py --cn-only —— 本机 yfinance 不可达时回填用。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "asset_prices.json")
    with open(path, encoding="utf-8") as f:
        output = json.load(f)
    prices = {}
    fill_cn_assets(prices)
    merged = []
    for k in ("hs300", "cn_10y_yield"):
        if prices.get(k, {}).get("latest") is not None:
            output.setdefault("prices", {})[k] = prices[k]
            merged.append(k)
    output["update_time"] = today_str()
    save_json(output, "asset_prices.json")
    logger.info(f"✅ --cn-only 回填完成: {merged}（其余序列原样保留）")
    return output



def download_with_retry(symbol, period="5y", max_retries=MAX_RETRIES):
    """带重试的数据下载"""
    import yfinance as yf
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)
            if hist is not None and not hist.empty:
                logger.info(f"  ✓ {symbol}: {len(hist)} 条数据")
                return hist
            else:
                logger.warning(f"  ⚠ {symbol}: 返回空数据 (attempt {attempt+1})")
                # [2026-11-12 修复] 空数据分支原来不 sleep，下一轮立刻重打，
                # 正是触发 yfinance 429 限流的原因之一。一并退避。
                if attempt < max_retries - 1:
                    _d = RETRY_DELAY * (2 ** attempt)
                    logger.info(f"    退避 {_d}s 后重试")
                    time.sleep(_d)
        except Exception as e:
            logger.warning(f"  ✗ {symbol}: {e} (attempt {attempt+1})")
            if attempt < max_retries - 1:
                # 指数退避 5s → 10s → 20s，替代原线性 5s → 10s
                _d = RETRY_DELAY * (2 ** attempt)
                logger.info(f"    退避 {_d}s 后重试")
                time.sleep(_d)
    logger.error(f"  ❌ {symbol}: 所有重试均失败")
    return pd.DataFrame()


def calc_metrics(hist):
    """计算技术指标: 最新价、MA200、距52周高点回撤、3个月动量、MA200偏离度

    P1-8（2026-11-02）：估值表趋势列需要 momentum_3m（63 个交易日）与
    ma200_dev_pct（最新价相对 MA200 偏离 %），与 ma200/drawdown 同源同窗口，
    从 5 年全历史计算（不受 daily 60 点存档窗口限制）。
    """
    if hist.empty or 'Close' not in hist.columns:
        return None, None, None, None, None

    close = hist['Close'].dropna()
    if close.empty:
        return None, None, None, None, None

    latest = safe_float(close.iloc[-1])

    # MA200
    ma200 = None
    if len(close) >= 200:
        ma200 = safe_float(close.rolling(200).mean().iloc[-1])

    # 52周高点回撤
    drawdown = None
    if len(close) >= 252:
        high_52w = close.tail(252).max()
        if high_52w > 0:
            drawdown = safe_float((latest - high_52w) / high_52w * 100)

    # P1-8: 3个月动量（63 个交易日 ≈ 3 个自然月）
    momentum_3m = None
    if len(close) >= 64:
        base = close.iloc[-64]
        if base and base > 0:
            momentum_3m = safe_float((latest / base - 1) * 100)

    # P1-8: MA200 偏离度 %
    ma200_dev = None
    if ma200:
        ma200_dev = safe_float((latest - ma200) / ma200 * 100)

    return latest, ma200, drawdown, momentum_3m, ma200_dev


def fill_from_fred(prices):
    """用 FRED 数据补充 yfinance 缺失的标的"""
    import os
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        logger.info("  无 FRED_API_KEY，跳过 FRED 补充")
        return

    try:
        from fredapi import Fred
        fred = Fred(api_key=fred_key)

        # FRED → yfinance 映射
        fred_map = {
            "sp500": "SP500",
            "vix": "VIXCLS",
            "us_10y": "DGS10",
        }

        for key, fred_id in fred_map.items():
            if prices.get(key, {}).get("latest") is None:
                try:
                    s = fred.get_series(fred_id, observation_start="2021-01-01").dropna()
                    if not s.empty:
                        latest = safe_float(s.iloc[-1])
                        # MA200
                        ma200 = safe_float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
                        # 52周高点回撤
                        drawdown = None
                        if len(s) >= 252:
                            high_52w = s.tail(252).max()
                            if high_52w > 0:
                                drawdown = safe_float((latest - high_52w) / high_52w * 100)
                        # P1-8: 3个月动量（63 个交易日）+ MA200 偏离度
                        momentum_3m = None
                        if len(s) >= 64:
                            base3m = s.iloc[-64]
                            if base3m and base3m > 0:
                                momentum_3m = safe_float((latest / base3m - 1) * 100)
                        ma200_dev = safe_float((latest - ma200) / ma200 * 100) if ma200 else None

                        # 日频历史
                        close = s.tail(60)
                        daily_data = [
                            {"date": idx.strftime("%Y-%m-%d"), "value": safe_float(val)}
                            for idx, val in close.items()
                        ]

                        prices[key] = {
                            "latest": latest,
                            "ma200": ma200,
                            "drawdown_from_high": drawdown,
                            "momentum_3m": momentum_3m,
                            "ma200_dev_pct": ma200_dev,
                            "daily": daily_data,
                            "name": TICKERS.get(key, {}).get("name", fred_id),
                            "symbol": f"FRED:{fred_id}",
                            "update_time": today_str(),
                            "_source": "FRED"
                        }
                        logger.info(f"  ✓ FRED 补充 {key}: {latest} (from {fred_id})")
                except Exception as e:
                    logger.warning(f"  ✗ FRED {fred_id}: {e}")
    except ImportError:
        logger.warning("  fredapi 不可用")
    except Exception as e:
        logger.warning(f"  FRED 补充失败: {e}")


def main():
    logger.info("=" * 60)
    logger.info("开始获取全球资产价格数据 (yfinance + FRED fallback)")
    logger.info("=" * 60)

    prices = {}

    # ============================================================
    # 下载所有标的 (yfinance)
    # ============================================================
    for key, cfg in TICKERS.items():
        logger.info(f"\n下载 {cfg['name']} ({cfg['symbol']})...")
        hist = download_with_retry(cfg['symbol'])

        # P0-2 裁决（2026-11-01）：copper 换算在写库时完成（美元/磅×2204.62=美元/吨），
        # 展示层不再换算；月频准绳=FRED PCOPPUSDM(IMF/LME)。
        factor = cfg.get("unit_factor")
        if factor and not hist.empty and 'Close' in hist.columns:
            hist['Close'] = hist['Close'] * factor

        latest, ma200, drawdown, momentum_3m, ma200_dev = calc_metrics(hist)

        # 日频历史数据 (最近60个交易日)
        daily_data = []
        if not hist.empty and 'Close' in hist.columns:
            close = hist['Close'].dropna().tail(60)
            daily_data = [
                {"date": idx.strftime("%Y-%m-%d"), "value": safe_float(val)}
                for idx, val in close.items()
            ]

        prices[key] = {
            "latest": latest,
            "ma200": ma200,
            "drawdown_from_high": drawdown,
            "momentum_3m": momentum_3m,
            "ma200_dev_pct": ma200_dev,
            "daily": daily_data,
            "name": cfg["name"],
            "symbol": cfg["symbol"],
            "update_time": today_str()
        }

        # 间隔一小段时间避免限流
        time.sleep(1)

    # ============================================================
    # FRED 补充
    # ============================================================
    logger.info("\n[FRED 补充] 尝试用 FRED 数据填充缺失标的...")
    fill_from_fred(prices)

    # ============================================================
    # A股资产采集（akshare：沪深300 + 中债10Y）
    # ============================================================
    logger.info("\n[A股资产] 采集沪深300/中债10Y（akshare）...")
    fill_cn_assets(prices)

    # ============================================================
    # VIX 单独处理 (history)
    # ============================================================
    vix_history = []
    if "vix" in prices and prices["vix"].get("daily"):
        vix_history = prices["vix"]["daily"]

    # ============================================================
    # 构建输出 JSON
    # ============================================================
    output = {
        "update_time": today_str(),
        "prices": {
            "sp500": {
                "latest": prices.get("sp500", {}).get("latest"),
                "ma200": prices.get("sp500", {}).get("ma200"),
                "drawdown_from_high": prices.get("sp500", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("sp500", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("sp500", {}).get("ma200_dev_pct"),
                "daily": prices.get("sp500", {}).get("daily", [])
            },
            "nasdaq": {
                "latest": prices.get("nasdaq", {}).get("latest"),
                "ma200": prices.get("nasdaq", {}).get("ma200"),
                "drawdown_from_high": prices.get("nasdaq", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("nasdaq", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("nasdaq", {}).get("ma200_dev_pct"),
                "daily": prices.get("nasdaq", {}).get("daily", [])
            },
            "gold": {
                "latest": prices.get("gold", {}).get("latest"),
                "ma200": prices.get("gold", {}).get("ma200"),
                "drawdown_from_high": prices.get("gold", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("gold", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("gold", {}).get("ma200_dev_pct"),
                "daily": prices.get("gold", {}).get("daily", [])
            },
            "copper": {
                "latest": prices.get("copper", {}).get("latest"),
                "ma200": prices.get("copper", {}).get("ma200"),
                "drawdown_from_high": prices.get("copper", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("copper", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("copper", {}).get("ma200_dev_pct"),
                "daily": prices.get("copper", {}).get("daily", []),
                "unit": "USD/t",
                "unit_note": "COMEX 主力连续(yfinance HG=F)×2204.62 写库时换算（P0-2 裁决2026-11-01）；月频准绳=FRED PCOPPUSDM(IMF/LME)"
            },
            "crude_oil": {
                "latest": prices.get("crude_oil", {}).get("latest"),
                "ma200": prices.get("crude_oil", {}).get("ma200"),
                "drawdown_from_high": prices.get("crude_oil", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("crude_oil", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("crude_oil", {}).get("ma200_dev_pct"),
                "daily": prices.get("crude_oil", {}).get("daily", [])
            },
            "usd_index": {
                "latest": prices.get("usd_index", {}).get("latest"),
                "ma200": prices.get("usd_index", {}).get("ma200"),
                "drawdown_from_high": prices.get("usd_index", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("usd_index", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("usd_index", {}).get("ma200_dev_pct"),
                "daily": prices.get("usd_index", {}).get("daily", [])
            },
            "us_10y_bond": {
                "latest": prices.get("us_10y", {}).get("latest"),
                # P1-8（2026-11-02）：美债卡趋势列。口径=10Y 收益率本身（非价格）：
                # 收益率↑=债价↓，前端以「收益率口径」标注，不与价格类资产混排解读。
                "ma200": prices.get("us_10y", {}).get("ma200"),
                "drawdown_from_high": prices.get("us_10y", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("us_10y", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("us_10y", {}).get("ma200_dev_pct"),
                "daily": prices.get("us_10y", {}).get("daily", [])
            },
            "btc": {
                "latest": prices.get("btc", {}).get("latest"),
                "ma200": prices.get("btc", {}).get("ma200"),
                "drawdown_from_high": prices.get("btc", {}).get("drawdown_from_high"),
                "momentum_3m": prices.get("btc", {}).get("momentum_3m"),
                "ma200_dev_pct": prices.get("btc", {}).get("ma200_dev_pct"),
                "daily": prices.get("btc", {}).get("daily", [])
            },
            "hs300": prices.get("hs300") or {"latest": None, "ma200": None, "drawdown_from_high": None, "momentum_3m": None, "ma200_dev_pct": None, "daily": []},
            "cn_10y_yield": prices.get("cn_10y_yield") or {"latest": None, "ma200": None, "drawdown_from_high": None, "momentum_3m": None, "ma200_dev_pct": None, "daily": []}
        },
        "volatility": {
            "vix": {
                "latest": prices.get("vix", {}).get("latest"),
                "history": vix_history
            }
        }
    }

    save_json(output, "asset_prices.json")
    logger.info("\n✅ asset_prices.json 生成完成!")
    return output


if __name__ == "__main__":
    try:
        if "--cn-only" in sys.argv:
            main_cn_only()
        else:
            main()
    except Exception as e:
        logger.error(f"❌ 获取资产价格数据失败: {e}", exc_info=True)
        sys.exit(1)
