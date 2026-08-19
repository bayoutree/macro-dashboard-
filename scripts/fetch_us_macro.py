#!/usr/bin/env python3
"""
获取美国宏观经济数据 → data/us_macro.json
数据源: FRED API (fredapi)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime
from config import (
    FRED_API_KEY, logger, today_str, safe_float, save_json,
    series_to_history, ts_to_date_str
)

# ============================================================
# FRED 连接
# ============================================================
def get_fred():
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY 环境变量未设置")
    from fredapi import Fred
    return Fred(api_key=FRED_API_KEY)


def fetch_series(fred, series_id, start="2020-01-01"):
    """安全获取单个 FRED 序列"""
    try:
        s = fred.get_series(series_id, observation_start=start)
        s = s.dropna()
        logger.info(f"  ✓ {series_id}: {len(s)} 个数据点, 最新={s.index[-1]}")
        return s
    except Exception as e:
        logger.warning(f"  ✗ {series_id}: {e}")
        return pd.Series(dtype=float)


# ============================================================
# 主逻辑
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("开始获取美国宏观数据 (FRED)")
    logger.info("=" * 60)

    fred = get_fred()

    # ---------- 领先指标 ----------
    logger.info("\n[1/4] 领先指标...")
    # NAPM (ISM PMI) may not be available in current FRED API; try alternatives
    pmi = fetch_series(fred, "NAPM", "2019-01-01")
    if pmi.empty:
        # Fallback: try ISM Manufacturing PMI via alternative FRED series
        pmi = fetch_series(fred, "MANPISMI", "2019-01-01")
    if pmi.empty:
        logger.info("  ⚠ ISM PMI 不可用，将从 ISM 网站获取或留空")
    # OECD Composite Leading Indicator (CLI)
    # Primary FRED series; if stale, try alternatives
    oecd_cli = fetch_series(fred, "USALORSGPNOSTSAM", "2019-01-01")
    # Check staleness: if data older than 6 months, try fallback series
    oecd_cli_stale = False
    if not oecd_cli.empty:
        from datetime import timedelta as _td
        _cutoff = pd.Timestamp.today() - _td(days=180)
        if pd.Timestamp(oecd_cli.index[-1]) < _cutoff:
            oecd_cli_stale = True
            logger.warning(f"  ⚠ OECD CLI 数据陈旧 (最新: {oecd_cli.index[-1]}), 尝试备用系列...")
            # Try alternative FRED series for OECD CLI
            oecd_cli_alt = fetch_series(fred, "CLIMNTH02_USA_OECDST", "2019-01-01")
            if oecd_cli_alt.empty:
                oecd_cli_alt = fetch_series(fred, "USAORSGPNOSTSAM", "2019-01-01")
            if not oecd_cli_alt.empty and pd.Timestamp(oecd_cli_alt.index[-1]) > pd.Timestamp(oecd_cli.index[-1]):
                oecd_cli = oecd_cli_alt
                oecd_cli_stale = False
                logger.info(f"  ✓ 使用备用 OECD CLI 序列, 最新: {oecd_cli.index[-1]}")

    # ---------- 同步指标 ----------
    logger.info("\n[2/4] 同步指标...")
    gdp = fetch_series(fred, "A191RL1Q225SBEA", "2019-01-01")
    unemployment = fetch_series(fred, "UNRATE", "2019-01-01")

    # ---------- 滞后指标 ----------
    logger.info("\n[3/4] 滞后指标...")
    cpi = fetch_series(fred, "CPIAUCSL", "2019-01-01")
    ppi = fetch_series(fred, "PPIACO", "2019-01-01")
    fed_funds = fetch_series(fred, "FEDFUNDS", "2019-01-01")

    # ---------- 利率 & 市场 ----------
    logger.info("\n[4/4] 利率与市场...")
    dgs10 = fetch_series(fred, "DGS10", "2019-01-01")
    dgs2 = fetch_series(fred, "DGS2", "2019-01-01")
    tips = fetch_series(fred, "DFII10", "2019-01-01")
    breakeven = fetch_series(fred, "T10YIE", "2019-01-01")
    term_prem = fetch_series(fred, "THREEFYTP10", "2019-01-01")
    sp500 = fetch_series(fred, "SP500", "2019-01-01")
    vix = fetch_series(fred, "VIXCLS", "2019-01-01")
    walcl = fetch_series(fred, "WALCL", "2019-01-01")

    # ============================================================
    # 计算衍生指标
    # ============================================================
    # CPI 同比
    cpi_yoy = pd.Series(dtype=float)
    if not cpi.empty and len(cpi) >= 13:
        cpi_yoy = (cpi / cpi.shift(12) - 1) * 100
        cpi_yoy = cpi_yoy.dropna()

    # PPI 同比
    ppi_yoy = pd.Series(dtype=float)
    if not ppi.empty and len(ppi) >= 13:
        ppi_yoy = (ppi / ppi.shift(12) - 1) * 100
        ppi_yoy = ppi_yoy.dropna()

    # 10Y-2Y 利差
    yield_spread = pd.Series(dtype=float)
    if not dgs10.empty and not dgs2.empty:
        common_idx = dgs10.index.intersection(dgs2.index)
        if len(common_idx) > 0:
            yield_spread = dgs10.loc[common_idx] - dgs2.loc[common_idx]

    # ============================================================
    # 构建输出 JSON
    # ============================================================
    logger.info("\n构建输出 JSON...")

    def latest_val(s):
        if s.empty:
            return None, None
        return safe_float(s.iloc[-1]), ts_to_date_str(s.index[-1])

    def latest_month(s):
        if s.empty:
            return None, None
        dt = s.index[-1]
        return safe_float(s.iloc[-1]), pd.Timestamp(dt).strftime("%Y-%m")

    pmi_val, pmi_date = latest_month(pmi)
    unemp_val, unemp_date = latest_month(unemployment)
    gdp_val, gdp_date_raw = latest_val(gdp)
    cpi_yoy_val, cpi_yoy_date = latest_month(cpi_yoy)
    ppi_yoy_val, ppi_yoy_date = latest_month(ppi_yoy)
    ff_val, ff_date = latest_month(fed_funds)
    d10_val, _ = latest_val(dgs10)
    d2_val, _ = latest_val(dgs2)
    spread_val, _ = latest_val(yield_spread)

    # GDP 日期格式化为 Qx
    gdp_date_str = None
    if gdp_date_raw:
        try:
            dt = pd.Timestamp(gdp_date_raw)
            q = (dt.month - 1) // 3 + 1
            gdp_date_str = f"{dt.year}Q{q}"
        except:
            gdp_date_str = str(gdp_date_raw)[:7]

    # PMI 趋势判断
    pmi_trend = "unknown"
    if pmi_val is not None:
        if pmi_val > 50:
            pmi_trend = "expanding"
        else:
            pmi_trend = "contracting"

    # 联邦基金利率格式化
    ff_str = None
    if ff_val is not None:
        ff_str = f"{ff_val:.2f}%"

    # ---------- 历史数据 ----------
    history = {}

    # GDP 历史 (季度)
    if not gdp.empty:
        h = []
        for dt, val in gdp.tail(16).items():
            try:
                ts = pd.Timestamp(dt)
                q = (ts.month - 1) // 3 + 1
                h.append({"date": f"{ts.year}Q{q}", "value": safe_float(val)})
            except:
                pass
        history["gdp_growth"] = h

    # CPI 同比历史 (月度)
    history["cpi_yoy"] = series_to_history(cpi_yoy, freq="monthly", max_points=36)

    # PPI 同比历史
    history["ppi_yoy"] = series_to_history(ppi_yoy, freq="monthly", max_points=36)

    # 失业率历史
    history["unemployment"] = series_to_history(unemployment, freq="monthly", max_points=36)

    # PMI 历史
    history["ism_pmi"] = series_to_history(pmi, freq="monthly", max_points=36)

    # 联邦基金利率历史
    history["fed_funds"] = series_to_history(fed_funds, freq="monthly", max_points=36)

    # 10Y 国债收益率历史 (日频)
    history["yield_10y"] = series_to_history(dgs10, freq="daily", max_points=60)

    # 2Y 国债收益率历史
    history["yield_2y"] = series_to_history(dgs2, freq="daily", max_points=60)

    # 期限溢价历史
    history["term_premium"] = series_to_history(term_prem, freq="daily", max_points=60)

    # 通胀预期历史
    history["inflation_expectation"] = series_to_history(breakeven, freq="daily", max_points=60)

    # 实际利率历史
    history["real_rate_10y"] = series_to_history(tips, freq="daily", max_points=60)

    # S&P 500 历史
    history["sp500"] = series_to_history(sp500, freq="daily", max_points=60)

    # VIX 历史
    history["vix"] = series_to_history(vix, freq="daily", max_points=60)

    # 美联储资产负债表历史
    history["fed_balance"] = series_to_history(walcl, freq="daily", max_points=60)

    # 10Y-2Y 利差历史
    history["yield_spread_10y_2y"] = series_to_history(yield_spread, freq="daily", max_points=60)

    # OECD CLI 历史
    history["oecd_cli"] = series_to_history(oecd_cli, freq="monthly", max_points=36)

    # ============================================================
    # 最终 JSON
    # ============================================================
    output = {
        "update_time": today_str(),
        "leading": {
            "ism_pmi": {"value": pmi_val, "date": pmi_date, "trend": pmi_trend},
            "oecd_cli": {"value": safe_float(oecd_cli.iloc[-1]) if not oecd_cli.empty else None,
                         "date": ts_to_date_str(oecd_cli.index[-1]) if not oecd_cli.empty else None,
                         "stale": oecd_cli_stale if not oecd_cli.empty else True},
            "yield_curve_10y_2y": {"value": safe_float(spread_val), "date": ts_to_date_str(yield_spread.index[-1]) if not yield_spread.empty else None},
            "lei": {"value": None, "date": None}
        },
        "coincident": {
            "gdp_growth": {"value": gdp_val, "date": gdp_date_str},
            "unemployment": {"value": unemp_val, "date": unemp_date},
            "industrial_production": {"value": None, "date": None},
            "nonfarm_payrolls_yoy": {"value": None, "date": None}
        },
        "lagging": {
            "cpi_yoy": {"value": cpi_yoy_val, "date": cpi_yoy_date},
            "ppi_yoy": {"value": ppi_yoy_val, "date": ppi_yoy_date},
            "fed_funds_rate": {"value": ff_str, "date": ff_date}
        },
        "rates": {
            "yield_10y": {"value": safe_float(d10_val), "date": ts_to_date_str(dgs10.index[-1]) if not dgs10.empty else None},
            "yield_2y": {"value": safe_float(d2_val), "date": ts_to_date_str(dgs2.index[-1]) if not dgs2.empty else None},
            "real_rate_10y": {"value": safe_float(tips.iloc[-1]) if not tips.empty else None,
                              "date": ts_to_date_str(tips.index[-1]) if not tips.empty else None},
            "inflation_expectation": {"value": safe_float(breakeven.iloc[-1]) if not breakeven.empty else None,
                                      "date": ts_to_date_str(breakeven.index[-1]) if not breakeven.empty else None},
            "term_premium": {"value": safe_float(term_prem.iloc[-1]) if not term_prem.empty else None,
                             "date": ts_to_date_str(term_prem.index[-1]) if not term_prem.empty else None}
        },
        "market": {
            "sp500": {"value": safe_float(sp500.iloc[-1]) if not sp500.empty else None,
                      "date": ts_to_date_str(sp500.index[-1]) if not sp500.empty else None},
            "vix": {"value": safe_float(vix.iloc[-1]) if not vix.empty else None,
                    "date": ts_to_date_str(vix.index[-1]) if not vix.empty else None},
            "fed_balance": {"value": safe_float(walcl.iloc[-1]) if not walcl.empty else None,
                            "date": ts_to_date_str(walcl.index[-1]) if not walcl.empty else None}
        },
        "history": history
    }

    save_json(output, "us_macro.json")
    logger.info("\n✅ us_macro.json 生成完成!")
    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 获取美国宏观数据失败: {e}", exc_info=True)
        sys.exit(1)
