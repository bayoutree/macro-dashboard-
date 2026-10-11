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
    # 守卫：纯数字（如 pandas 行号 560）不是日期，禁止被下游解析成"0560年"坏点
    if re.fullmatch(r"\d+", s):
        return None
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


def grab_series(fn, retries=3, timeout=30, sleep=2):
    """模块级取数包装（重试 + 线程超时）；供门禁脚本离线/在线复用。
    语义与 get_china_groups 内的 grab 一致：全失败返回 None 并打告警。"""
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
        print(f"[china-warn] {getattr(fn, '__name__', '<lambda>')} 连续 {retries} 次失败: {last}",
              file=sys.stderr)
    return None

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
        ds = date_fn(d) if date_fn else str(d)
        if ds is None:
            continue
        out.append((ds, fv))
    return out or None


# ----------------------------------------------------------------------------
# #12 出口系列：NBS 官方(A) → 东财(B) 降级链（CIO 终裁 2026-10-11 · 裁决_#12）
#   旧源 jin10 系（macro_china_exports_yoy / imports_yoy / trade_balance）：
#     - date 为「发布日」（如 2025-08-07），非自然月 → #12 年份错位；
#     - 最新有效值止于 2025-07，滞后约 1 年。
#   新源（同口径 = 海关总署）：
#     A 国家统计局 ak.macro_china_nbs_nation("月度数据", "对外经济>货物进出口总额")
#       出口总值_同比增长(%) / 进口总值_同比增长(%)  → 当月同比(%)
#       进出口差额_当期值(千美元)                   → 当月差额(亿美元, ×1e-5)
#     B 东方财富 ak.macro_china_hgjck()（RPT_ECONOMY_CUSTOMS）
#       当月出口额-同比增长 / 当月进口额-同比增长 → 当月同比(%)
#       (当月出口额-金额 − 当月进口额-金额) ×1e-5 → 当月差额(亿美元)
#   两源 2026-08 逐项一致（出口 +25.0 / 进口 +28.2 / 差额 1190.9 亿美元）；
#   实测版本差 ≤0.1pp，A/B 交叉校验容差取 0.2pp（CIO 裁决）。
# ----------------------------------------------------------------------------
NBS_EXIM_PATH = "对外经济>货物进出口总额"

# NBS 指标行名（index）→ 输出键；差额为「当期值」，NBS 无「差额同比」行。
NBS_EXT_ROWS = {
    "export": "出口总值_同比增长(%)",
    "import": "进口总值_同比增长(%)",
    "trade_balance": "进出口差额_当期值(千美元)",
}
NBS_EXT_UNITS = {"export": "%", "import": "%", "trade_balance": "亿美元"}

def cn_month_date(s):
    """月度标签 → 自然月 'YYYY-MM'（与其它中国指标一致）。
    支持 '2026年8月' / '2026年08月份' / '2026-08' / '2026-08-01'。
    返回 None 表示无法识别（调用方跳过该点，不猜日期）。"""
    s = str(s).strip()
    m = re.search(r"(\d{4})年\s*(\d{1,2})\s*月", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{4})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None

def _nbs_extract(nbs_df, key, date_fn=cn_month_date):
    """从 NBS 透视表（index=指标名，columns=自然月）取单指标序列。
    trade_balance 单位千美元 → 亿美元（×1e-5）。"""
    if nbs_df is None or len(nbs_df) == 0:
        return None
    row = NBS_EXT_ROWS.get(key)
    if row is None:
        return None
    if row not in nbs_df.index:
        cand = [i for i in nbs_df.index if row in str(i)]
        if not cand:
            return None
        row = cand[0]
    scale = 1e-5 if key == "trade_balance" else 1.0
    out = []
    for col in nbs_df.columns:
        v = nbs_df.loc[row, col]
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(fv):
            continue
        ds = date_fn(col)
        if ds is None:
            continue
        out.append((ds, fv * scale))
    return out or None

def fetch_em_external(em_df=None):
    """东财「海关进出口增减情况一览表」→ {key: [(YYYY-MM, v)]}（B 降级源）。

    em_df 缺省时自行调用 ak.macro_china_hgjck()（RPT_ECONOMY_CUSTOMS）。
    口径：当月出口额-同比增长 / 当月进口额-同比增长 → 当月同比(%)；
          当月差额 = (当月出口额 − 当月进口额) × 1e-5 → 亿美元。
    """
    if em_df is None:
        try:
            em_df = ak.macro_china_hgjck()
        except Exception:
            return None
    if em_df is None or len(em_df) == 0:
        return None
    date_col = _find_col(em_df.columns, ["月份"])
    ex_col = _find_col(em_df.columns, ["当月出口额-同比增长", "出口额-同比增长"])
    im_col = _find_col(em_df.columns, ["当月进口额-同比增长", "进口额-同比增长"])
    ex_lvl = _find_col(em_df.columns, ["当月出口额-金额"])
    im_lvl = _find_col(em_df.columns, ["当月进口额-金额"])
    if date_col is None or ex_col is None or im_col is None:
        return None
    out = {"export": [], "import": [], "trade_balance": []}
    for i in range(len(em_df)):
        d = cn_month_date(em_df.iloc[i][date_col])
        if d is None:
            continue
        ex_s, im_s = em_df.iloc[i][ex_col], em_df.iloc[i][im_col]
        if ex_s == ex_s and ex_s is not None:
            out["export"].append((d, float(ex_s)))
        if im_s == im_s and im_s is not None:
            out["import"].append((d, float(im_s)))
        if ex_lvl and im_lvl:
            ex, im = em_df.iloc[i][ex_lvl], em_df.iloc[i][im_lvl]
            if ex == ex and im == im and ex is not None and im is not None:
                out["trade_balance"].append((d, (float(ex) - float(im)) / 1e5))
    out = {k: v for k, v in out.items() if v}
    return out or None

def cross_check_dual_source(nbs_df, em_ext, tolerance_pp=0.2, recent=3, tol_relative=0.02):
    """A(NBS) vs B(东财) 双源交叉校验（CIO 终裁 2026-10-11：容差 ≥0.2pp）。

    - 仅比对**最近 recent 个重叠月**（看板展示的即最近值；更早月份存在官方修订，
      两源版本差会放大，不参与判定）。
    - 同比字段（export/import）：绝对容差 tolerance_pp 个百分点。
    - 差额（亿美元）：官方对初值有修订，用相对容差 tol_relative（2%），绝对下限 2 亿美元。
    返回 (recent_max_diff, breaches, full_history_max_diff)。仅告警，不阻断（以 A 为准）。
    """
    if not em_ext:
        return None, [], None
    breaches, recent_worst, full_worst = [], 0.0, 0.0
    for key in NBS_EXT_UNITS:
        a = dict(_nbs_extract(nbs_df, key) or [])
        b = dict(em_ext.get(key) or [])
        common = sorted(set(a) & set(b))
        if not common:
            continue
        for d in common:
            full_worst = max(full_worst, abs(a[d] - b[d]))
        for d in common[-recent:]:
            diff = abs(a[d] - b[d])
            recent_worst = max(recent_worst, diff)
            if key == "trade_balance":
                allowed = max(2.0, tol_relative * abs(a[d]))
            else:
                allowed = tolerance_pp
            if diff > allowed + 1e-9:
                breaches.append(f"{key} {d}: A={a[d]} vs B={b[d]} (Δ{diff:.2f} > {allowed:.2f})")
    return recent_worst, breaches, full_worst

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
    # 口径纠正(2026-09 P0)：外管局「美元」列为人民币元/百美元(如 674.89)，
    # 统一换算为直盘汇率 USDCNY(6.7489)，与 us_market 口径及国际行情一致
    _usdcny_raw = extract_series(df, ["日期"], ["美元", "USDCNY", "美元兑人民币"])
    _usdcny = [(d, round(v / 100.0, 4)) for d, v in _usdcny_raw] if _usdcny_raw else None
    add("financial", "usdcny", _usdcny, unit="")

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
    # #12 换源（CIO 终裁 2026-10-11）：旧 jin10 系 date=发布日且止于 2025-07（滞后约1年）；
    # 改用 A(NBS 官方) → B(东财) 降级链；两源均为当月同比 + 自然月日期，双源皆失败才置灰。
    nbs_ext = grab(lambda: ak.macro_china_nbs_nation(
        kind="月度数据", path=NBS_EXIM_PATH, period="2014-"))
    em_ext = grab(lambda: fetch_em_external())
    ext_vals, ext_src = {}, {}
    for key in NBS_EXT_UNITS:
        s_ = _nbs_extract(nbs_ext, key)
        if s_:
            ext_vals[key], ext_src[key] = s_, "NBS国家统计局"
    for key in NBS_EXT_UNITS:                     # A 缺失项由 B 补齐（降级链）
        if key not in ext_vals and (em_ext or {}).get(key):
            ext_vals[key] = em_ext[key]
            ext_src[key] = "东方财富"
    # A/B 双源交叉校验（CIO 容差 0.2pp）：仅告警不阻断，以 A 为准
    recent_worst, breaches, full_worst = cross_check_dual_source(nbs_ext, em_ext)
    raw["external_cross_check"] = {
        "recent_max_diff": recent_worst, "tolerance_pp": 0.2,
        "full_history_max_diff": full_worst, "breaches": breaches[:5]}
    if breaches:
        print(f"[china-warn] 进出口 A/B 双源超差(近3月 maxΔ={recent_worst:.2f}): {breaches[:3]}",
              file=sys.stderr)
    for key, unit in NBS_EXT_UNITS.items():
        add("external", key, ext_vals.get(key), unit=unit)
        if key in groups["external"]:
            groups["external"][key]["source"] = (
                f"{ext_src[key]}·海关总署口径（#12 换源 2026-10-11，自然月）")
    if not ext_vals:
        print("[china-warn] 对外三指标：NBS 与东财源均不可用 → 置灰", file=sys.stderr)

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
