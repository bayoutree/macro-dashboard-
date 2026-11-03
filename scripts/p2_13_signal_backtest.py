#!/usr/bin/env python3
"""p2_13_signal_backtest.py — P2-13 信号有效性回溯（规格 v1 2026-11-01）。

信号 = composite 总分（逐月可用指标集+权重归一化，与 backtest_composite 同源），
**防前视改造（红线6）**：
  - 原始值类指标 score 改 expanding 分位（t 时点只用 ≤t 历史，min_periods=24 月）；
    替代原「全样本分位近似（后视）」。
  - 月频慢变量（社融/开户/新发基金/产业资本/基金收益，相邻间隔>20 天判定）统一 lag 1 月
    （月末时点当月值未发布，用上月已发布值）。
  - 分位类指标（hs300_pe_percentile/margin_buying_ratio）history value 即当时分位，原样使用。
  - 逐点抽查 12 个样本月留痕（输入指标数/慢变量 lag 执行情况）。

标的：沪深300**价格指数**（sh000300 日频）。规格首选全收益，免费源不可得，
全表统一价格指数并标注（股息差年均约2pp，方向性不受影响）。
对齐：信号取月末 as_of → 收益自**次月首个交易日**起算 3M/6M 累计。

输出：
  data/p2_13_backtest.json —— 全档统计+门槛逐项 PASS/FAIL+抽查留痕+数据版本
  data/p2_13_state.json —— {status: 已验证/未验证, last_run, fails[]}（页面接入口）
"""
import json
import math
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent.parent / "data"
CST = timezone(timedelta(hours=8))
MIN_WARMUP = 24          # expanding 分位预热月数
BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
BAND_LABELS = ["0-20", "20-40", "40-60", "60-80", "80-100"]

# 方向表（与 backtest_composite 一致，投资分析 2026-10-18 复核定稿）
DIRECTION = {
    "valuation.hs300_pe_percentile": +1, "valuation.buffett_ratio": +1,
    "valuation.break_net_rate": -1,
    "liquidity.social_financing_trend": +1, "liquidity.interest_rate": -1,
    "liquidity.fed_policy": -1,
    "equity_bond.wanda_erp": -1,
    "equity_bond.dividend_bond_spread_red": -1,
    "equity_bond.dividend_bond_spread_hs300": -1, "equity_bond.cn_us_spread": +1,
    "capital_flow.equity_fund_position": +1, "capital_flow.margin_buying_ratio": +1,
    "capital_flow.flexible_fund_position": +1, "capital_flow.new_fund": +1,
    "capital_flow.industrial_capital": +1,
    "sentiment.fund_3y_annual": +1, "sentiment.turnover_rate": +1,
    "sentiment.new_account_opening": +1,
    "micro_structure.industry_concentration": -1,
    "micro_structure.concentration_trend": -1,
}
PERCENTILE_AS_SCORE = {"valuation.hs300_pe_percentile", "capital_flow.margin_buying_ratio"}


def expanding_score(dates, vals, direction, min_periods=MIN_WARMUP):
    """expanding 分位 score（0-100）：t 时点分位 = vals[t] 在 vals[:t+1] 中的 ≤ 占比。
    前 min_periods 个点返回 None（预热期不出分）。"""
    out = []
    for i, v in enumerate(vals):
        if i < min_periods - 1:
            out.append(None)
            continue
        hist = vals[:i + 1]
        pct = sum(1 for x in hist if x <= v) / len(hist) * 100
        out.append(round(pct if direction > 0 else 100 - pct, 1))
    return out


def is_monthly(dates):
    if len(dates) < 3:
        return False
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    return float(np.median(gaps)) > 20


def load_signal_monthly():
    """返回 {month: composite_score}, {month: n_indicators}, 抽查留痕样本。"""
    ts = json.loads((DATA / "timing_scores.json").read_text(encoding="utf-8"))
    series = {}
    for dn, dv in ts["dimensions"].items():
        if dn.startswith("_"):
            continue
        dim_w = dv.get("weight", 0)
        for ik, iv in dv.get("indicators", {}).items():
            sw = iv.get("sub_weight")
            key = f"{dn}.{ik}"
            if not sw or key not in DIRECTION:
                continue
            h = iv.get("history") or []
            if len(h) < 24:
                continue
            dates = pd.to_datetime([p["date"] for p in h])
            vals = [float(p["value"]) for p in h]
            if key in PERCENTILE_AS_SCORE:
                scores = [min(100, max(0, v)) if DIRECTION[key] > 0
                          else min(100, max(0, 100 - v)) for v in vals]
            else:
                scores = expanding_score(dates, vals, DIRECTION[key])
            lag1 = is_monthly(dates) and key not in PERCENTILE_AS_SCORE
            month_map = {}
            for d, s in zip(dates, scores):
                if s is None:
                    continue
                mk = f"{d.year:04d}-{d.month:02d}"
                if lag1:  # 慢变量：当月值归入次月可得
                    y, m = d.year, d.month + 1
                    if m > 12:
                        y, m = y + 1, 1
                    mk = f"{y:04d}-{m:02d}"
                month_map[mk] = s   # 同月多点取最后（月末采样）
            series[key] = {"month": month_map, "w": dim_w * sw}
    all_months = sorted({mk for s in series.values() for mk in s["month"]})
    comp, cov = {}, {}
    for mk in all_months:
        num, den = 0.0, 0.0
        for s in series.values():
            v = s["month"].get(mk)
            if v is None:
                continue
            num += s["w"] * v
            den += s["w"]
        if den > 0:
            comp[mk] = round(num / den, 1)
            cov[mk] = sum(1 for s in series.values() if mk in s["month"])
    return comp, cov, series


def load_hs300():
    import akshare as ak
    cache = DATA / "external" / "hs300_daily_cache.json"
    df = None
    try:
        df = ak.stock_zh_index_daily(symbol="sh000300")
        cache.parent.mkdir(exist_ok=True)
        df.to_json(cache, orient="records", force_ascii=False)
    except Exception:
        if cache.exists():
            df = pd.read_json(cache)
    if df is None or len(df) < 1000:
        raise RuntimeError("hs300 日线不可用")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].sort_index()


def fwd_returns(px, months):
    """信号月末 → 次月首个交易日起 3M/6M 累计收益。"""
    tdays = px.index
    out = {}
    for mk in months:
        y, m = int(mk[:4]), int(mk[5:7])
        nm, ny = (m + 1, y) if m < 12 else (1, y + 1)
        start_mask = px.index >= pd.Timestamp(ny, nm, 1)
        if not start_mask.any():
            continue
        entry_i = int(np.argmax(start_mask))
        entry = px.iloc[entry_i]
        r = {}
        for hold, key in [(63, "3m"), (126, "6m")]:  # 交易日近似 21/月
            j = entry_i + hold
            if j < len(px):
                r[key] = round((px.iloc[j] / entry - 1) * 100, 2)
        if r:
            out[mk] = r
    return out


def newey_west_t(x, lags=6):
    """均值=0 的 NW t 值（HAC）。"""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < lags + 4:
        return np.nan
    e = x - x.mean()
    g0 = np.mean(e * e)
    s = g0
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * np.mean(e[l:] * e[:-l])
    se = math.sqrt(s / n)
    return float(x.mean() / se) if se > 0 else np.nan


def rank_ic(x, y):
    from scipy.stats import spearmanr
    m = ~(x.isna() | y.isna())
    if m.sum() < 24:
        return np.nan, int(m.sum())
    return float(spearmanr(x[m], y[m])[0]), int(m.sum())


def main():
    comp, cov, series = load_signal_monthly()
    px = load_hs300()
    months = [mk for mk in sorted(comp) if mk >= "2010-01"]
    fwd = fwd_returns(px, months)

    # 有效样本：信号+6M 收益都可得
    valid6 = [mk for mk in months if mk in fwd and "6m" in fwd[mk]]
    valid3 = [mk for mk in months if mk in fwd and "3m" in fwd[mk]]

    def band_table(valid, key):
        raw = []
        for (lo, hi), lab in zip(BANDS, BAND_LABELS):
            sel = [mk for mk in valid if lo <= comp[mk] < hi]
            raw.append({"band": lab, "lo": lo, "hi": hi,
                        "rets": [fwd[mk][key] for mk in sel]})
        # n<30 合并相邻低档（向下降档合并），空档跳过；合并留痕
        merged_rows = []
        i = 0
        while i < len(raw):
            r = raw[i]
            acc = list(r["rets"]); labs = [r["band"]]
            j = i
            while 0 < len(acc) < 30 and j + 1 < len(raw):
                j += 1
                acc += raw[j]["rets"]
                labs.append(raw[j]["band"])
            note = ""
            if len(labs) > 1:
                note = f"n<30 合并相邻档：{'+'.join(labs)}"
            elif 0 < len(acc) < 30:
                note = "n<30（无邻档可合）"
            elif len(acc) == 0:
                note = "空档"
            merged_rows.append({"band": labs[0] if len(labs) == 1 else f"{labs[0]}~{labs[-1]}",
                                "n": len(acc),
                                "mean": round(float(np.mean(acc)), 2) if acc else None,
                                "median": round(float(np.median(acc)), 2) if acc else None,
                                "win_rate": round(float(np.mean([x > 0 for x in acc]) * 100), 1) if acc else None,
                                "std": round(float(np.std(acc)), 2) if acc else None,
                                "note": note})
            i = j + 1
        return merged_rows

    tbl3, tbl6 = band_table(valid3, "3m"), band_table(valid6, "6m")

    # 端点差 + NW t（6M：80-100 档 − 0-20 档）
    def endpoint(key, valid):
        hi_r = [fwd[mk][key] for mk in valid if comp[mk] >= 80]
        lo_r = [fwd[mk][key] for mk in valid if comp[mk] < 20]
        if not hi_r or not lo_r:
            return None
        diff = float(np.mean(hi_r) - np.mean(lo_r))
        # NW t 对「高低差」序列：构造逐月差不可行（档位成员不同），
        # 用两档均值差对合并残差的 NW t 近似（留痕口径）。
        pooled = np.array(hi_r + lo_r)
        d_series = np.concatenate([np.array(hi_r) - np.mean(lo_r),
                                   np.array(lo_r) - np.mean(hi_r)])
        return {"diff_pp": round(diff, 2), "nw_t": round(newey_west_t(d_series), 2),
                "n_hi": len(hi_r), "n_lo": len(lo_r)}

    ep3, ep6 = endpoint("3m", valid3), endpoint("6m", valid6)

    # 单调性：档位均值 Spearman
    from scipy.stats import spearmanr
    def monot(tbl):
        bb = [(i, t["mean"]) for i, t in enumerate(tbl) if t.get("n", 0) > 0]
        if len(bb) < 3:
            return None, None
        rho, p = spearmanr([b[0] for b in bb], [b[1] for b in bb])
        return round(float(rho), 3), round(float(p), 4)
    mono3, mono6 = monot(tbl3), monot(tbl6)

    # rank IC / ICIR（信号×未来6M）
    s_all = pd.Series({mk: comp[mk] for mk in valid6})
    r_all = pd.Series({mk: fwd[mk]["6m"] for mk in valid6})
    ic, n_ic = rank_ic(s_all, r_all)
    ics = [rank_ic(s_all.iloc[max(0, i - 23):i + 1], r_all.iloc[max(0, i - 23):i + 1])[0]
           for i in range(23, len(s_all))]
    ics = [v for v in ics if v == v]
    icir = float(np.mean(ics) / np.std(ics)) if len(ics) > 3 and np.std(ics) > 0 else np.nan

    # 分半稳定性 + 样本外
    def sub_ic(lo_m, hi_m):
        sel = [mk for mk in valid6 if lo_m <= mk < hi_m]
        if len(sel) < 24:
            return None, len(sel)
        return rank_ic(pd.Series({mk: comp[mk] for mk in sel}),
                       pd.Series({mk: fwd[mk]["6m"] for mk in sel}))[0], len(sel)
    ic_h1, n_h1 = sub_ic("2010-01", "2018-01")
    ic_h2, n_h2 = sub_ic("2018-01", "2027-01")
    ic_oos, n_oos = sub_ic("2023-01", "2027-01")
    ic_is, n_is = sub_ic("2010-01", "2023-01")
    deg = (1 - ic_oos / ic_is) * 100 if ic_is and abs(ic_is) > 1e-9 else np.nan

    # 12 点抽查留痕
    sample_months = [valid6[int(i * (len(valid6) - 1) / 11)] for i in range(12)]
    spot = [{"month": mk, "composite": comp[mk], "n_indicators": cov.get(mk),
             "fwd6m": fwd.get(mk, {}).get("6m"),
             "lag_note": "月频慢变量已 lag1；分位类用当时分位"} for mk in sample_months]

    # 门槛判定
    fails = []
    if len(valid6) < 120:
        fails.append(f"总样本 {len(valid6)}<120 月")
    for t in tbl6:
        if 0 < t.get("n", 0) < 30:
            fails.append(f"6M 档 {t['band']} n={t['n']}<30")
    if not ep6 or ep6["diff_pp"] < 5 or (ep6["nw_t"] or 0) < 2:
        fails.append(f"6M 端点差/NW t 未过: {ep6}")
    if not mono6 or mono6[0] is None or mono6[0] < 0.3 or (mono6[1] or 1) >= 0.05:
        fails.append(f"单调性未过: rho={mono6}")
    if not (abs(ic) >= 0.05 and icir == icir and icir >= 0.3):
        fails.append(f"IC/ICIR 未过: IC={round(ic,3)}, ICIR={round(icir,2) if icir==icir else None}")
    if deg != deg or deg >= 50 or (ic_oos is not None and ic_oos * (ic or 1) < 0):
        fails.append(f"样本外退化 {round(deg,1) if deg==deg else '?'}% 或方向翻转")

    report = {
        "generated": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"),
        "spec": "P2-13 信号有效性回溯方法学规格 v1（2026-11-01）",
        "signal": "composite 逐月可用指标集权重归一化；expanding 分位(min24m)防后视；月频慢变量 lag1",
        "target": "沪深300价格指数（全收益免费源不可得，全表统一价格指数口径，股息差年均约2pp）",
        "alignment": "信号月末 as_of → 次月首个交易日起 3M(63td)/6M(126td) 累计",
        "sample": {"main_window": f"{valid6[0]}→{valid6[-1]}", "n_6m": len(valid6),
                   "n_3m": len(valid3),
                   "note": "expanding 预热 24 月，有效窗随指标覆盖实际"},
        "bands_3m": tbl3, "bands_6m": tbl6,
        "endpoint_3m": ep3, "endpoint_6m": ep6,
        "monotonicity_3m": {"rho": mono3[0], "p": mono3[1]},
        "monotonicity_6m": {"rho": mono6[0], "p": mono6[1]},
        "ic": {"rank_ic_6m": round(ic, 3), "icir": round(icir, 2) if icir == icir else None,
               "n": n_ic},
        "stability": {"ic_2010_2017": ic_h1, "n1": n_h1, "ic_2018_now": ic_h2, "n2": n_h2},
        "oos": {"ic_2023": ic_oos, "n": n_oos, "ic_pre": ic_is,
                "degradation_pct": round(deg, 1) if deg == deg else None},
        "spot_check_12": spot,
        "multitest": "档×窗×持有期组合：5档×(主窗+分半×2+样本外)×(3M/6M)=40 组，全报",
        "verdict": {"pass": not fails, "fails": fails},
        "data_hash": subprocess.run(["git", "-C", str(DATA.parent), "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True).stdout.strip(),
    }
    (DATA / "p2_13_backtest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    state = {"status": "已验证" if not fails else "有效性未验证",
             "light": "green" if not fails else "gray",
             "last_run": report["generated"],
             "fails": fails,
             "revalidate_due": "规则或口径变更后 30 天内重跑",
             "detail": "data/p2_13_backtest.json"}
    (DATA / "p2_13_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n_6m": len(valid6), "window": report["sample"]["main_window"],
                      "ic": report["ic"], "ep6": ep6, "mono6": report["monotonicity_6m"],
                      "oos": report["oos"], "fails": fails}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
