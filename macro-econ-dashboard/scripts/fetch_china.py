"""
fetch_china.py — 使用 akshare 抓取中国宏观数据

设计原则：
  1. 每个指标独立 try/except + 线程超时（25s），单个接口失败/超时不影响整体。
  2. 列名用「模糊匹配」而非硬编码，兼容 akshare 不同版本。
  3. 日期统一清洗为 YYYY-MM / YYYY-MM-DD（见 cn_date）。
  4. 抓取失败时该指标在结果中缺省（不出现），下游与前端需容忍缺失。
  5. 返回 (groups, raw)：groups 是干净的展示结构；raw 保留 10Y 国债等
     完整序列，供 cross 指标（中美利差）精确计算分位数使用。
"""
from __future__ import annotations

import os
import re
import sys
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import akshare as ak
except Exception:
    ak = None

from normalize import make_indicator

# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def run_with_timeout(fn, timeout=25, default=None):
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except Exception:
            return default


def cn_date(s):
    s = str(s).strip()
    m = re.search(r"(\d{4})年.*?第(\d+)(?:-(\d+))?季度", s)
    if m:
        y, q = m.group(1), (m.group(3) or m.group(2))
        month = {"1": "03", "2": "06", "3": "09", "4": "12"}.get(q, "03")
        return f"{y}-{month}"
    m = re.search(r"(\d{4})年(\d{1,2})月", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)  # 保留完整 YYYY-MM-DD（日频数据如 DR007）
    m = re.search(r"(\d{4}-\d{2})", s)
    if m:
        return m.group(1)
    return s


def _find_col(cols, kws):
    cols = [str(c) for c in cols]
    lower = {c.lower(): c for c in cols}
    for kw in kws:
        k = str(kw).lower()
        if k in lower:
            return lower[k]
    for kw in kws:
        k = str(kw).lower()
        for c, orig in lower.items():
            if k in c:
                return orig
    return None


def extract_series(df, date_kws, value_kws, date_fn=cn_date, index_as_date=False):
    if df is None or len(df) == 0:
        return None
    date_col = _find_col(df.columns, date_kws)
    use_index = (date_col is None) or index_as_date
    val_col = None
    for kw in value_kws:
        c = _find_col(df.columns, [kw])
        if c:
            val_col = c
            break
    if val_col is None:
        return None
    out = []
    for i in range(len(df)):
        d = df.index[i] if use_index else df.iloc[i][date_col]
        v = df.iloc[i][val_col]
        if v is None:
            continue
        try:
            fv = float(v)
        except Exception:
            continue
        if math.isnan(fv):
            continue
        out.append((date_fn(d) if date_fn else str(d), fv))
    return out or None


def _house_price_series(df):
    if df is None or len(df) == 0:
        return None
    val_col = _find_col(df.columns, ["新建商品住宅价格指数-同比", "新建商品住宅价格指数"])
    date_col = _find_col(df.columns, ["日期"])
    if val_col is None or date_col is None:
        return None
    g = df.groupby(date_col)[val_col].mean()
    return [(cn_date(d), float(v)) for d, v in g.items() if v == v]


# ----------------------------------------------------------------------------
# 抓取主流程
# ----------------------------------------------------------------------------
def get_china_groups():
    groups = {
        "leading": {}, "coincident": {}, "lagging": {},
        "financial": {}, "sectors": {}, "external": {}, "valuation": {},
        "government": {}, "fiscal": {},
    }
    raw = {}
    ok, fail = [], []

    if ak is None:
        print("[china] akshare 未安装，全部中国指标降级为空")
        return groups, raw

    def grab(fn, retries=3, timeout=30, sleep=2):
        last = None
        for i in range(retries):
            try:
                r = run_with_timeout(fn, timeout=timeout)
                if r is not None and len(r) > 0:
                    return r
            except Exception as e:
                last = e
            if i < retries - 1:
                time.sleep(sleep)
        if last is not None:
            print(f"[china-warn] {fn.__name__ if hasattr(fn, '__name__') else '<lambda>'} 连续 {retries} 次失败: {last}", file=sys.stderr)
        return None

    def add(cat, key, points, unit=""):
        if not points:
            fail.append(f"{cat}.{key}")
            return
        ind = make_indicator(points, unit=unit)
        if ind is None:
            fail.append(f"{cat}.{key}")
            return
        groups[cat][key] = ind
        ok.append(f"{cat}.{key}")

    # ---- 领先指标 ----
    df = grab(ak.macro_china_pmi)
    add("leading", "pmi", extract_series(df, ["月份"], ["制造业-指数", "制造业 PMI"]), unit="")

    df = grab(lambda: ak.macro_china_caijin_pmi())
    add("leading", "caixin_pmi", extract_series(df, ["月份"], ["制造业-指数", "制造业- PMI"]), unit="")

    df = grab(ak.macro_china_shrzgm)
    add("leading", "shrzgm", extract_series(df, ["月份"], ["同比增速", "同比"]), unit="%")

    df = grab(ak.macro_china_money_supply)
    add("leading", "m1", extract_series(df, ["月份"], ["货币(M1)-同比增长", "M1-同比增长"]), unit="%")

    # ---- 同步指标 ----
    df = grab(ak.macro_china_gdp)
    add("coincident", "gdp", extract_series(df, ["季度"], ["国内生产总值-同比增长", "GDP-同比增长"]), unit="%")

    df = grab(ak.macro_china_gyzjz)
    add("coincident", "industrial_prod",
        extract_series(df, ["月份"], ["同比增长", "今值", "同比"]), unit="%")

    df = grab(ak.macro_china_consumer_goods_retail)
    add("coincident", "retail",
        extract_series(df, ["月份"], ["同比增长", "今值"]), unit="%")

    # ---- 滞后指标 ----
    df = grab(ak.macro_china_cpi)
    add("lagging", "cpi", extract_series(df, ["月份"], ["今值", "同比增长", "CPI"]), unit="%")

    df = grab(ak.macro_china_ppi)
    add("lagging", "ppi", extract_series(df, ["月份"], ["今值", "同比增长"]), unit="%")

    df = grab(ak.macro_china_money_supply)
    add("lagging", "m2",
        extract_series(df, ["月份"], ["货币和准货币(M2)-同比增长", "M2-同比增长"]), unit="%")

    df = grab(ak.macro_china_urban_unemployment)
    add("lagging", "unemployment",
        extract_series(df, ["月份"], ["今值", "失业率", "调查失业率", "城镇调查"]), unit="%")

    # ---- 金融条件 ----
    # Bug 1 修复：DR007 使用 Shibor 1W 作为代理
    # repo_rate_hist 数据陈旧（2020年），且列名不匹配（FDR007 而非 DR007）
    # Shibor 1W 是当前最稳定的货币市场利率代理，与 DR007 高度相关
    dr_series = None
    df = grab(ak.macro_china_shibor_all)
    if df is not None and len(df) > 0:
        dr_series = extract_series(df, ["日期"], ["1W-定价"])
    add("financial", "dr007", dr_series, unit="%")

    # 10Y 国债收益率（东方财富 - 数据到最新，覆盖 2002 至今）
    # 替代 bond_china_yield（只到 2021-01，无法与 US 10Y 计算利差）
    cn10 = None
    df_bond = grab(ak.bond_zh_us_rate)
    if df_bond is not None and len(df_bond) > 0:
        # 列名: 日期, 中国国债收益率10年, 美国国债收益率10年, ...
        date_col = None
        val_col = None
        for c in df_bond.columns:
            cs = str(c).lower()
            if "日期" in cs or "date" in cs:
                date_col = c
            if "中国国债收益率10年" in cs or ("中国" in cs and "10" in cs):
                val_col = c
        if date_col and val_col:
            cn10 = []
            for _, row in df_bond.iterrows():
                try:
                    d = str(row[date_col]).strip()
                    v = float(row[val_col])
                    if math.isnan(v):
                        continue
                    # 保留完整 YYYY-MM-DD 日期
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", d)
                    if m:
                        cn10.append((m.group(1), v))
                except Exception:
                    continue
            cn10 = cn10 or None
    if cn10:
        raw["cn_10y"] = cn10
        add("financial", "cn_10y", cn10, unit="%")
        groups["financial"]["cgb_10y"] = groups["financial"]["cn_10y"]

    df = grab(ak.currency_boc_safe)
    add("financial", "usdcny",
        extract_series(df, ["日期"], ["美元", "USDCNY", "美元兑人民币"]), unit="")

    # ---- 居民 / 企业（sectors）----
    if "retail" in groups["coincident"]:
        groups["sectors"]["retail_sales"] = groups["coincident"]["retail"]

    df = grab(ak.macro_china_new_house_price)
    add("sectors", "house_price_index", _house_price_series(df), unit="%")

    s = None
    for fn_name in ("macro_china_industrial_profit", "macro_china_industrial_profit_yoy"):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        df = grab(fn)
        s = extract_series(df, ["月份", "日期"], ["同比增长", "今值", "利润"])
        if s:
            break
    if s is None:
        df = grab(ak.macro_china_gyzjz)
        s = extract_series(df, ["月份"], ["同比增长", "今值"])
    if s:
        add("sectors", "industrial_profit", s, unit="%")
    else:
        fail.append("sectors.industrial_profit")

    df = grab(ak.macro_china_gdzctz)
    add("sectors", "mfg_investment",
        extract_series(df, ["月份"], ["同比增长", "制造业", "自年初累计同比增长"]), unit="%")

    # ---- 对外（external）----
    # Bug 7 修复：确保对外部门数据正确填充
    df = grab(ak.macro_china_exports_yoy)
    export_series = extract_series(df, ["日期"], ["今值", "同比增长", "出口"])
    if export_series:
        add("external", "export", export_series, unit="%")

    df = grab(ak.macro_china_imports_yoy)
    import_series = extract_series(df, ["日期"], ["今值", "同比增长", "进口"])
    if import_series:
        add("external", "import", import_series, unit="%")

    df = grab(ak.macro_china_trade_balance)
    tb_series = extract_series(df, ["月份"], ["今值", "贸易差额", "差额", "净值"])
    if tb_series:
        add("external", "trade_balance", tb_series, unit="亿美元")

    # ---- 政府部门（government）----
    # 财政收入同比增速：使用 macro_china_czsr（数据到最新月份）
    df = grab(ak.macro_china_czsr)
    if df is not None and len(df) > 0:
        # 列名: 月份, 当月, 当月-同比增长, 当月-环比增长, 累计, 累计-同比增长
        fiscal_rev = extract_series(df, ["月份"], ["当月-同比增长"])
        if fiscal_rev:
            add("government", "fiscal_revenue_growth", fiscal_rev, unit="%")

    # ---- 财政政策（fiscal）----
    # 暂无独立数据源，财政相关指标从 government 部门映射

    # ---- 估值 ----
    df = grab(ak.stock_index_pe_lg)
    add("valuation", "csi300_pe",
        extract_series(df, ["日期"], ["滚动市盈率", "市盈率", "静态市盈率"]), unit="x")

    print(f"[china] 成功 {len(ok)} 项，失败/缺失 {len(fail)} 项: {fail}")
    return groups, raw


if __name__ == "__main__":
    import json
    g, r = get_china_groups()
    print(json.dumps(g, ensure_ascii=False, indent=2)[:2000])
