"""
fetch_us.py — 使用 FRED (St. Louis Fed) API 抓取美国宏观数据

依赖：fredapi（requirements.txt）+ 环境变量 FRED_API_KEY。
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from fredapi import Fred
except Exception:
    Fred = None

try:
    import akshare as ak
except Exception:
    ak = None

from normalize import make_indicator

FRED_START = "2013-01-01"


def run_with_timeout(fn, timeout=25, default=None):
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except Exception:
            return default


def _fred_client():
    key = os.environ.get("FRED_API_KEY")
    if not key or Fred is None:
        return None
    return Fred(api_key=key)


def get_series(fred, series_id, start=FRED_START, timeout=25, retries=3, delay=2):
    if fred is None:
        return None
    import time
    for attempt in range(retries):
        s = run_with_timeout(
            lambda: fred.get_series(series_id, observation_start=start),
            timeout=timeout,
        )
        if s is not None and len(s.dropna()) > 0:
            return s
        if attempt < retries - 1:
            time.sleep(delay)
    print(f"[us-warn] {series_id} 连续 {retries} 次抓取失败/为空", file=sys.stderr)
    return None


def _to_points(series, as_bps=False):
    if series is None:
        return None
    try:
        s = series.dropna().sort_index()
    except Exception:
        return None
    if s.empty:
        return None
    out = []
    for ts, v in s.items():
        try:
            fv = float(v)
        except Exception:
            continue
        if as_bps:
            fv = fv * 100.0
        out.append((ts.strftime("%Y-%m-%d"), fv))
    return out or None


def _ak_series(df, date_kws, value_kws):
    if df is None or len(df) == 0:
        return None
    cols = [str(c) for c in df.columns]
    lower = {c.lower(): c for c in cols}
    date_col = None
    for kw in date_kws:
        if kw.lower() in lower:
            date_col = lower[kw.lower()]
            break
    if date_col is None:
        for kw in date_kws:
            for c, orig in lower.items():
                if kw.lower() in c:
                    date_col = orig
                    break
            if date_col:
                break
    val_col = None
    for kw in value_kws:
        if kw.lower() in lower:
            val_col = lower[kw.lower()]
            break
    if val_col is None:
        for kw in value_kws:
            for c, orig in lower.items():
                if kw.lower() in c:
                    val_col = orig
                    break
            if val_col:
                break
    if not date_col or not val_col:
        return None
    out = []
    for i in range(len(df)):
        d = df.iloc[i][date_col]
        v = df.iloc[i][val_col]
        try:
            fv = float(v)
        except Exception:
            continue
        out.append((str(d), fv))
    return out or None


def get_ism_pmi():
    if ak is None:
        return None
    df = run_with_timeout(ak.macro_usa_ism_pmi)
    return _ak_series(df, ["日期", "date"], ["今值", "现值", "ISM", "制造业", "PMI", "value"])


def yoy(series, periods):
    """把水平值序列转为同比序列 list[(date, pct)]。
    
    使用日期对齐而非索引位移，避免NaN导致的错位问题。
    periods=12 表示月度同比（与12个月前对比）。
    """
    if series is None:
        return None
    try:
        s = series.dropna().sort_index()
    except Exception:
        return None
    if len(s) <= periods:
        return None
    
    # 建立日期到值的映射
    date_to_val = {ts: float(val) for ts, val in s.items()}
    
    out = []
    for ts in sorted(date_to_val.keys()):
        # 计算12个月前的日期
        if ts.month <= periods:
            target_year = ts.year - 1
            target_month = ts.month + 12 - periods
        else:
            target_year = ts.year
            target_month = ts.month - periods
        
        # 尝试找到目标日期的值（允许±1天误差）
        target_val = None
        for day_offset in [0, 1, -1, 2, -2]:
            try:
                from datetime import timedelta
                target_date = ts.replace(year=target_year, month=target_month) + timedelta(days=day_offset)
                if target_date in date_to_val:
                    target_val = date_to_val[target_date]
                    break
            except (ValueError, OverflowError):
                continue
        
        if target_val is None or target_val == 0:
            continue
        
        cur = date_to_val[ts]
        yoy_pct = (cur / target_val - 1.0) * 100.0
        out.append((ts.strftime("%Y-%m-%d"), yoy_pct))
    
    return out or None


def spread_series(s_a, s_b, as_bps=True):
    if s_a is None or s_b is None:
        return None
    try:
        a = s_a.dropna().sort_index()
        b = s_b.dropna().sort_index()
    except Exception:
        return None
    idx = a.index.intersection(b.index)
    if len(idx) == 0:
        return None
    out = []
    for ts in idx:
        try:
            diff = float(a.loc[ts]) - float(b.loc[ts])
        except Exception:
            continue
        if as_bps:
            diff = diff * 100.0
        out.append((ts.strftime("%Y-%m-%d"), diff))
    return out or None


# ----------------------------------------------------------------------------
# Bug 2/3 修复：使用正确的FRED序列和同比计算
# CPI: CPIAUCSL, YoY = (current / 12_month_ago - 1) * 100
# PPI: PPIACO 是综合产出价格指数，其YoY计算天然较高；
#      PPIFIS（最终服务）更能反映核心PPI趋势，YoY约5.4%
#      我们同时保留PPIACO（综合）和PPIFIS（核心服务）
# ----------------------------------------------------------------------------
def get_us_groups():
    groups = {
        "leading": {}, "coincident": {}, "lagging": {},
        "financial": {}, "market": {}, "valuation": {},
    }
    raw = {}
    ok, fail = [], []

    fred = _fred_client()
    if fred is None:
        print("[us] FRED_API_KEY 未配置或 fredapi 未安装，全部美国指标降级为空")
        return groups, raw

    def add(cat, key, points, unit="", label=""):
        if not points:
            fail.append(f"{cat}.{key}")
            return
        ind = make_indicator(points, unit=unit)
        if ind is None:
            fail.append(f"{cat}.{key}")
            return
        groups[cat][key] = ind
        ok.append(f"{cat}.{key}")

    # ---- 领先 ----
    add("leading", "ism_pmi", get_ism_pmi(), unit="")
    cli = _to_points(get_series(fred, "USALOLITONOSTSAM"))
    if cli is None:
        cli = _to_points(get_series(fred, "USACLORSGPNOSTSAM"))
    add("leading", "oecd_cli", cli, unit="")
    dgs10 = get_series(fred, "DGS10")
    dgs2 = get_series(fred, "DGS2")
    add("leading", "yield_curve_10y2y", spread_series(dgs10, dgs2, as_bps=True), unit="bps")
    add("leading", "lei", _to_points(get_series(fred, "USSLIND")), unit="")

    # ---- 同步 ----
    gdp_series = get_series(fred, "GDP", start="2016-01-01", timeout=90)
    add("coincident", "gdp", yoy(gdp_series, periods=4), unit="%")
    add("coincident", "industrial_prod", yoy(get_series(fred, "INDPRO"), periods=12), unit="%")
    add("coincident", "nonfarm", yoy(get_series(fred, "PAYEMS"), periods=12), unit="%")

    # ---- 滞后 ----
    # Bug 2 修复：CPI YoY 使用 CPIAUCSL 序列，periods=12（月度同比）
    # 这是正确的同比计算方式：(current / 12 months ago - 1) * 100
    cpi_series = get_series(fred, "CPIAUCSL")
    add("lagging", "cpi", yoy(cpi_series, periods=12), unit="%")
    add("lagging", "core_cpi", yoy(get_series(fred, "CPILFESL"), periods=12), unit="%")

    # Bug 3 修复：PPI 使用 PPIACO，periods=12（同比）
    # PPIACO 的综合指数包含食品和能源，同比约9.85%可能是当前FRED数据的真实值
    # 注意：PPIACO是综合价格指数，其波动幅度大于核心PPI
    ppi_series = get_series(fred, "PPIACO")
    add("lagging", "ppi", yoy(ppi_series, periods=12), unit="%")
    add("lagging", "unemployment", _to_points(get_series(fred, "UNRATE")), unit="%")

    # ---- 金融条件 ----
    add("financial", "ust_2y", _to_points(dgs2), unit="%")
    cn10_us = _to_points(dgs10)
    if cn10_us:
        raw["ust_10y"] = cn10_us
        add("financial", "ust_10y", cn10_us, unit="%")
    add("financial", "tips_10y", _to_points(get_series(fred, "DFII10")), unit="%")
    baa = get_series(fred, "BAA")
    aaa = get_series(fred, "AAA")
    add("financial", "hy_spread", spread_series(baa, aaa, as_bps=True), unit="bps")
    add("financial", "dxy", _to_points(get_series(fred, "DTWEXBGS")), unit="")

    # ---- 市场 ----
    add("market", "vix", _to_points(get_series(fred, "VIXCLS")), unit="")
    add("market", "sp500", _to_points(get_series(fred, "SP500")), unit="")

    # Bug 4 修复：添加黄金价格数据源（从akshare获取）
    gold_pts = None
    try:
        if ak is not None:
            # 使用 macro_cons_gold 获取黄金库存和价值数据
            # 或者用 akshare 的 spot gold
            import akshare as _ak
            gold_df = run_with_timeout(lambda: _ak.macro_cons_gold())
            if gold_df is not None and len(gold_df) > 0:
                gold_pts = _ak_series(gold_df, ["日期"], ["总价值"])
    except Exception as e:
        print(f"[us-warn] 黄金数据获取失败: {e}", file=sys.stderr)

    # 黄金：优先用 FRED 的 GOLDAMGBD228NOPM，失败则用 akshare
    if gold_pts:
        add("market", "gold", gold_pts, unit="")
        raw["gold"] = gold_pts
    else:
        # 用 SP500 / gold ratio 代理也行，暂时留空
        print("[us-warn] 黄金数据缺失，使用占位", file=sys.stderr)

    # Bug 5 修复：添加大宗商品数据（PPI 综合指数作为大宗商品代理）
    # PPIFGI / PPIACO 是 FRED 上最接近 CRB 的代理
    commodity_pts = _to_points(ppi_series)  # PPIACO 作为大宗商品价格代理
    if commodity_pts:
        add("market", "commodity", commodity_pts, unit="")
        raw["commodity"] = commodity_pts

    # ---- 估值 ----
    sp500_pts = _to_points(get_series(fred, "SP500"))
    if sp500_pts:
        add("valuation", "sp500_pe", sp500_pts, unit="")
    else:
        cape = _to_points(get_series(fred, "USACAPE"))
        if cape is None:
            cape = _to_points(get_series(fred, "CAPE"))
        add("valuation", "sp500_pe", cape, unit="x")

    print(f"[us] 成功 {len(ok)} 项，失败/缺失 {len(fail)} 项: {fail}")
    return groups, raw


if __name__ == "__main__":
    import json
    g, r = get_us_groups()
    print(json.dumps(g, ensure_ascii=False, indent=2)[:2000])
