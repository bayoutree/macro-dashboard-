#!/usr/bin/env python3
"""
build_five_layer.py — 适配器 + 周期规则引擎

职责：
  把 fetch_china / fetch_us 产出的「旧分组结构」（leading/coincident/lagging/
  sectors/external/valuation/financial/market）重建成「五层结构」指标 JSON，
  供前端 data_render.js 直接消费。

设计原则（与任务一致）：
  1. 不手填任何真实指标值 —— 所有 value / pct_5y / change / date / history
     全部来自 fetch 的真实产出（chian_groups / us_groups / cross）。
  2. 规则引擎输出确定性 —— 给定指标分位，周期定位 + 资产基准唯一确定。
  3. evolution_paths 的条件 met 标记，读取 indicators.json 里定义的条件
     文本，用当前真实值重新判定（不沿用手填的 met）。
  4. asset_valuation 保留框架（name / note），pct_5y / pct_10y 用真实数据补充。
  5. frequency_policy 原样保留。

用法：
  from build_five_layer import build_five_layer_payload
  payload = build_five_layer_payload(china_groups, us_groups, cross)
"""
from __future__ import annotations

import os
import re
import sys
import json
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PROJECT_ROOT = os.path.dirname(_HERE)
INDICATORS_PATH = os.path.join(PROJECT_ROOT, "data", "indicators.json")


# ============================================================================
# 展示名映射（只改 label，不碰真实数据）
# ============================================================================
NAME_MAP = {
    "dr007": "DR007",
    "m2": "M2同比",
    "m1": "M1同比",
    "shrzgm": "社融存量同比",
    "cpi": "CPI同比",
    "ppi": "PPI同比",
    "gdp": "GDP同比",
    "pmi": "制造业PMI",
    "ism_pmi": "ISM制造业PMI",
    "retail": "社零同比",
    "retail_sales": "社零同比",
    "house_price": "70城房价同比",
    "house_price_index": "70城房价同比",
    "industrial_profit": "工业企业利润同比",
    "mfg_investment": "制造业投资同比",
    "industrial_prod": "工业产出同比",
    "nonfarm": "非农就业同比",
    "export": "出口同比",
    "import": "进口同比",
    "trade_balance": "贸易差额",
    "core_cpi": "核心CPI同比",
    "unemployment": "失业率",
    "ust_2y": "2Y美债",
    "ust_10y": "10Y美债",
    "tips_10y": "10Y实际利率",
    "hy_spread": "HY信用利差",
    "fed_funds": "联邦基金(2Y代理)",
    "dxy": "美元指数",
    "vix": "VIX恐慌指数",
    "sp500": "标普500",
    "csi300_pe": "沪深300PE",
    "cn_10y": "中债10Y",
    "usdcny": "美元兑人民币",
    "oecd_cli": "OECD综合领先",
    "yield_curve_10y2y": "10Y-2Y利差",
    "lei": "美国LEI",
    "tax_revenue": "税收收入",
    "pce": "个人消费支出",
    "retail_sales": "零售销售",
    "consumer_conf": "消费者信心",
    "fed_deficit": "联邦赤字",
    "fed_debt": "联邦债务",
    "current_account": "经常账户",
    "fed_spending": "联邦支出",
    "fed_receipts": "联邦收入",
}

ASSET_NAMES = {
    "cn_stock": "A股(沪深300)",
    "cn_bond": "中债(10Y国债)",
    "cn_realestate": "中房地产(70城)",
    "us_stock": "美股(标普500)",
    "us_bond": "美债(10Y)",
    "usd": "美元指数",
    "gold": "黄金",
    "commodity": "大宗商品(CRB)",
}


# ============================================================================
# 框架模板（作为 indicators.json 不可用时的兜底；优先从 indicators.json 读取）
# ============================================================================
FREQUENCY_POLICY = {
    "daily": ["dr007", "cn_10y_yield", "ust_2y", "ust_10y", "usdcny", "sp500", "vix", "hy_spread"],
    "weekly": ["bill_rate", "eia_crude", "mba_mortgage", "nfib"],
    "monthly": [
        "pmi", "cpi", "ppi", "m1", "m2", "social_financing", "gdp", "nonfarm",
        "ism_pmi", "industrial_production", "fiscal_balance", "trade_balance",
        "credit_impulse", "fci",
    ],
}

# 中国 evolution_paths 框架（条件文本 + from/to/prob/fail/confidence 来自 indicators.json）
EVOLUTION_CN = [
    {
        "from": "复苏酝酿", "to": "经济复苏", "prob": 45,
        "conditions": [
            {"desc": "M1同比连续3月回升且>5%"},
            {"desc": "社融存量同比反弹至40分位以上"},
            {"desc": "DR007维持40分位以下(宽货币延续)"},
        ],
        "fail": "若M1再度跌破30分位 或 GDP实际增速跌破4.5%，自动降级",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
    {
        "from": "复苏酝酿", "to": "类滞胀", "prob": 30,
        "conditions": [
            {"desc": "DR007反弹至40分位以上(货币边际收紧)"},
            {"desc": "PPI同比加速至4%以上"},
            {"desc": "食品项推升CPI同比破2%"},
        ],
        "fail": "若PPI回落至2%以下且DR007维持低位，回归复苏路径",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
    {
        "from": "复苏酝酿", "to": "衰退中段", "prob": 25,
        "conditions": [
            {"desc": "M1跌破30分位"},
            {"desc": "GDP实际增速跌破4.5%"},
            {"desc": "出口同比连续2月负增"},
        ],
        "fail": "若政策加码(降准降息)且M1回升，撤回衰退判断",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
]

# 美国 evolution_paths 框架
EVOLUTION_US = [
    {
        "from": "深度滞胀", "to": "典型衰退", "prob": 40,
        "conditions": [
            {"desc": "CPI同比回落至2.5%以下"},
            {"desc": "Fed开始降息(联邦基金利率<4.5%)"},
            {"desc": "失业率突破4.2%"},
        ],
        "fail": "若CPI回落且美联储开启降息周期，则向典型衰退/软着陆切换",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
    {
        "from": "深度滞胀", "to": "滞胀延续", "prob": 35,
        "conditions": [
            {"desc": "CPI维持3%以上"},
            {"desc": "Fed维持高利率(联邦基金利率>5%)"},
            {"desc": "薪资增速维持4%以上"},
        ],
        "fail": "若通胀回落且美联储转鸽，滞胀延续概率下降",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
    {
        "from": "深度滞胀", "to": "硬着陆", "prob": 25,
        "conditions": [
            {"desc": "失业率突破4.5%"},
            {"desc": "HY信用利差走阔至80分位以上"},
            {"desc": "美股回撤超20%"},
        ],
        "fail": "若就业韧性保持且信用利差稳定，硬着陆风险暂缓",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
]


def load_framework():
    """读取 indicators.json 里的框架；失败则回退到内置常量。"""
    try:
        with open(INDICATORS_PATH, "r", encoding="utf-8") as f:
            t = json.load(f)
        fp = t.get("frequency_policy") or FREQUENCY_POLICY
        av = t.get("asset_valuation") or {}
        ecn = (t.get("china", {}).get("economic_cycle", {}).get("evolution_paths")) or EVOLUTION_CN
        eus = (t.get("us", {}).get("economic_cycle", {}).get("evolution_paths")) or EVOLUTION_US
        return fp, av, ecn, eus
    except Exception:
        return FREQUENCY_POLICY, {}, EVOLUTION_CN, EVOLUTION_US


# ============================================================================
# 工具
# ============================================================================
def _named(metric, key):
    """返回带展示名拷贝；不改变原始 fetch 数据。"""
    if not metric or not isinstance(metric, dict):
        return None
    m = dict(metric)
    m["name"] = NAME_MAP.get(key, key)
    return m


def _pct(metric):
    if not metric or not isinstance(metric, dict):
        return None
    return metric.get("pct_5y")


def _name_group(group):
    out = {}
    for k, v in (group or {}).items():
        if v:
            out[k] = _named(v, k)
    return out


def count_metrics(node):
    """统计结构里「真实指标」的数量（含 value + history 的叶子 dict）。"""
    if isinstance(node, dict):
        if "value" in node and "history" in node:
            return 1
        return sum(count_metrics(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_metrics(v) for v in node)
    return 0


# ============================================================================
# 条件判定引擎（evolution_paths 的 met 重算）
# ============================================================================
def _detect_metric(desc, ctx):
    if "DR007" in desc:
        return ctx.get("dr007")
    if "社融" in desc or "shrzgm" in desc.lower():
        return ctx.get("shrzgm")
    if "M1" in desc:
        return ctx.get("m1")
    if "PPI" in desc:
        return ctx.get("ppi")
    if "CPI" in desc or "cpi" in desc.lower():
        return ctx.get("cpi")
    if "GDP" in desc or "gdp" in desc.lower():
        return ctx.get("gdp")
    if "出口" in desc:
        return ctx.get("export")
    if "联邦基金" in desc or "Fed" in desc:
        return ctx.get("fed_funds")
    if "失业率" in desc:
        return ctx.get("unemployment")
    if "HY" in desc or "信用利差" in desc:
        return ctx.get("hy_spread")
    return None


def _consecutive_increase(metric, n):
    if not metric:
        return False
    h = metric.get("history") or []
    if len(h) < n:
        return False
    vals = [x.get("value") for x in h[-n:]]
    if any(v is None for v in vals):
        return False
    return all(vals[i] > vals[i - 1] for i in range(1, n))


def _consecutive_negative(metric, n):
    if not metric:
        return False
    h = metric.get("history") or []
    if len(h) < n:
        return False
    vals = [x.get("value") for x in h[-n:]]
    if any(v is None for v in vals):
        return False
    return all(v < 0 for v in vals)


def _eval_condition(desc, ctx):
    """根据条件文本 + 真实指标，确定性地返回 met (bool)。无法判定时返回 False。"""
    if not desc:
        return False

    metric = _detect_metric(desc, ctx)

    # 连续回升 且 >X%
    m_inc = re.search(r"连续\s*(\d+)\s*月\s*回升", desc)
    if m_inc:
        n = int(m_inc.group(1))
        ok = _consecutive_increase(metric, n)
        gt = re.search(r">\s*(\d+(?:\.\d+)?)\s*%", desc)
        if gt and metric:
            v = metric.get("value")
            ok = ok and (v is not None and v > float(gt.group(1)))
        return bool(ok)

    # 连续负增
    m_neg = re.search(r"连续\s*(\d+)\s*月\s*负增", desc)
    if m_neg:
        n = int(m_neg.group(1))
        return bool(_consecutive_negative(metric, n))

    if metric is None:
        return False

    # 分位比较
    pm = re.search(r"(\d+(?:\.\d+)?)\s*分位", desc)
    if pm:
        thr = float(pm.group(1))
        op = "below" if ("以下" in desc or "跌破" in desc) else "above"
        p = metric.get("pct_5y")
        if p is None:
            return False
        return (p < thr) if op == "below" else (p > thr)

    # 水平值比较
    lm = re.search(r"(?:<|>|破|回落至|跌破|维持|超过|突破|至)\s*(\d+(?:\.\d+)?)\s*%", desc)
    if lm:
        thr = float(lm.group(1))
        op = "below" if ("<" in desc or "以下" in desc or "回落至" in desc or "跌破" in desc) else "above"
        v = metric.get("value")
        if v is None:
            return False
        return (v < thr) if op == "below" else (v > thr)

    return False


def _build_evolution_paths(tmpl, ctx):
    out = []
    for p in tmpl:
        conds = []
        for c in p.get("conditions", []):
            met = _eval_condition(c.get("desc", ""), ctx)
            conds.append({"desc": c.get("desc", ""), "met": bool(met)})
        ep = {
            "from": p.get("from"),
            "to": p.get("to"),
            "prob": p.get("prob"),
            "conditions": conds,
            "trigger": p.get("trigger"),
            "fail": p.get("fail"),
            "confidence": p.get("confidence"),
            # 同时暴露为场景字段，供前端 conclusion.scenarios 渲染
            "name": f"{p.get('from')}→{p.get('to')}",
            "probability": p.get("prob"),
            "description": p.get("fail") or "",
        }
        out.append(ep)
    return out


# ============================================================================
# 中国 — 货币-信用模型
# ============================================================================
def _cn_money_state(dr007):
    p = _pct(dr007)
    if p is None:
        return "中性货币", "🟡"
    if p < 40:
        return "宽货币", "🟢"
    if p > 70:
        return "紧货币", "🔴"
    return "中性货币", "🟡"


def _cn_credit_state(m1):
    p = _pct(m1)
    if p is None:
        return "中性信用", "🟡"
    if p > 60:
        return "宽信用", "🟢"
    if p < 30:
        return "紧信用", "🔴"
    return "中性信用", "🟡"


_CN_PHASE_TABLE = {
    ("loose", "loose"): ("复苏确认", "🟢🟢", "股票>商品>债券>现金"),
    ("loose", "neutral"): ("复苏酝酿", "🟢", "股票>债券>商品>现金"),
    ("loose", "tight"): ("衰退后期/政策观察", "🟢", "债券>现金>股票>商品"),
    ("neutral", "loose"): ("复苏确认", "🟢", "股票>商品>债券>现金"),
    ("neutral", "neutral"): ("经济筑底", "🟡", "债券>股票>现金>商品"),
    ("neutral", "tight"): ("信用收缩", "🟡", "现金>债券>股票>商品"),
    ("tight", "loose"): ("过热/滞胀风险", "🔴", "商品>现金>债券>股票"),
    ("tight", "neutral"): ("货币收紧", "🔴", "现金>债券>商品>股票"),
    ("tight", "tight"): ("深度滞胀", "🔴🔴", "现金>黄金>商品>债券>股票"),
}


def _cn_phase(m_state, c_state):
    m = "loose" if "宽" in m_state else ("tight" if "紧" in m_state else "neutral")
    c = "loose" if "宽" in c_state else ("tight" if "紧" in c_state else "neutral")
    return _CN_PHASE_TABLE[(m, c)]


def _cn_asset_desc(m_state, c_state, phase, dr007, m1, shrzgm):
    money_txt = {
        "宽货币": "货币宽松，短端利率下行利好债券久期与权益估值",
        "中性货币": "货币中性，政策利率平稳",
        "紧货币": "货币收紧，流动性溢价上升压制估值",
    }.get(m_state, "货币方向待定")
    credit_txt = {
        "宽信用": "信用扩张，实体融资改善利好风险资产",
        "中性信用": "信用平稳，社融温和",
        "紧信用": "信用收缩，融资需求疲弱",
    }.get(c_state, "信用方向待定")
    return f"{money_txt}；{credit_txt}。当前定位「{phase}」，按货币-信用框架做资产映射。"


def _cn_money_desc(dr007, m_state):
    if not dr007:
        return "DR007 数据缺失"
    v = dr007.get("value")
    p = dr007.get("pct_5y")
    if v is None:
        return "DR007 数据缺失"
    tag = "宽松" if "宽" in m_state else ("收紧" if "紧" in m_state else "中性")
    return f"DR007={v}%，近5年分位{p}%（{tag}）"


def _cn_credit_desc(m1, shrzgm, c_state):
    if not m1:
        return "M1 数据缺失"
    v = m1.get("value")
    p = m1.get("pct_5y")
    base = f"M1同比{v}%，近5年分位{p}%"
    sv = shrzgm.get("value") if shrzgm else None
    if sv is not None:
        base += f"；社融存量同比{sv}%"
    tag = "宽" if "宽" in c_state else ("紧" if "紧" in c_state else "中性")
    base += f"（{tag}信用）"
    return base


def _main_contradiction(country, m_state, c_state, ec):
    if country == "china":
        if m_state == "宽货币" and c_state == "宽信用":
            return "货币与信用双宽，关键在于宽信用能否持续传导至实体需求。"
        if m_state == "宽货币" and c_state in ("中性信用", "紧信用"):
            return "宽货币尚未有效转化为宽信用，融资需求疲弱是核心约束。"
        if m_state == "紧货币" and c_state == "宽信用":
            return "政策收紧与信用扩张背离，需警惕流动性拐点与估值压力。"
        if m_state == "紧货币" and c_state in ("中性信用", "紧信用"):
            return "货币与信用双紧，经济面临深度调整与通缩压力。"
        return "增长与通胀信号交织，货币与信用方向尚不明朗。"
    q = (ec.get("quadrant") or {}).get("state")
    l = (ec.get("liquidity") or {}).get("state")
    if q == "过热":
        return "需求强劲叠加通胀高企，政策收紧风险上升。"
    if q == "滞胀":
        return "通胀粘性与需求走弱并存，政策陷入两难。"
    if q == "衰退":
        return "增长走弱，关注宽松何时兑现以托底经济。"
    if q == "复苏":
        return "需求边际改善，复苏持续性仍待更多数据确认。"
    return "信号交织，方向尚不明朗。"


def _build_cn_sectors(g):
    household = {}
    retail = g.get("sectors", {}).get("retail_sales") or g.get("coincident", {}).get("retail")
    if retail:
        household["retail"] = _named(retail, "retail")
    hp = g.get("sectors", {}).get("house_price_index")
    if hp:
        household["house_price"] = _named(hp, "house_price")

    corporate = {}
    ip = g.get("sectors", {}).get("industrial_profit")
    if ip:
        corporate["industrial_profit"] = _named(ip, "industrial_profit")
    mi = g.get("sectors", {}).get("mfg_investment")
    if mi:
        corporate["mfg_investment"] = _named(mi, "mfg_investment")
    pmi = g.get("leading", {}).get("pmi")
    if pmi:
        corporate["pmi"] = _named(pmi, "pmi")

    external = {}
    ex = g.get("external", {}).get("export")
    if ex:
        external["export"] = _named(ex, "export")
    im = g.get("external", {}).get("import")
    if im:
        external["import"] = _named(im, "import")
    tb = g.get("external", {}).get("trade_balance")
    if tb:
        external["trade_balance"] = _named(tb, "trade_balance")

    # 政府部门
    government = {}
    frg = g.get("government", {}).get("fiscal_revenue_growth")
    if frg:
        government["fiscal_revenue_growth"] = _named(frg, "fiscal_revenue_growth")

    return {"household": household, "corporate": corporate, "government": government, "external": external}


def _build_cn_policy(g):
    monetary = {}
    if g.get("financial", {}).get("dr007"):
        monetary["dr007"] = _named(g["financial"]["dr007"], "dr007")
    if g.get("lagging", {}).get("m2"):
        monetary["m2"] = _named(g["lagging"]["m2"], "m2")
    fiscal = {}
    frg = g.get("government", {}).get("fiscal_revenue_growth")
    if frg:
        fiscal["fiscal_revenue_growth"] = _named(frg, "fiscal_revenue_growth")
    return {"monetary": monetary, "fiscal": fiscal}


def _build_china(g, evo_tmpl):
    dr007 = g.get("financial", {}).get("dr007")
    m2 = g.get("lagging", {}).get("m2")
    m1 = g.get("leading", {}).get("m1")
    shrzgm = g.get("leading", {}).get("shrzgm")
    cpi = g.get("lagging", {}).get("cpi")
    ppi = g.get("lagging", {}).get("ppi")
    gdp = g.get("coincident", {}).get("gdp")
    export = g.get("external", {}).get("export")

    m_state, m_icon = _cn_money_state(dr007)
    c_state, c_icon = _cn_credit_state(m1)
    phase, icon, asset_base = _cn_phase(m_state, c_state)
    asset_desc = _cn_asset_desc(m_state, c_state, phase, dr007, m1, shrzgm)

    economic_cycle = {
        "method": "货币-信用模型",
        "phase": phase,
        "icon": icon,
        "asset_base": asset_base,
        "asset_desc": asset_desc,
        "monetary": {"state": m_state, "icon": m_icon, "desc": _cn_money_desc(dr007, m_state)},
        "credit": {"state": c_state, "icon": c_icon, "desc": _cn_credit_desc(m1, shrzgm, c_state)},
        "monetary_indicators": {
            "dr007": _named(dr007, "dr007"),
            "m2": _named(m2, "m2"),
        },
        "credit_indicators": {
            "m1": _named(m1, "m1"),
            "shrzgm": _named(shrzgm, "shrzgm"),
        },
        "evolution_paths": _build_evolution_paths(
            evo_tmpl,
            {"dr007": dr007, "m2": m2, "m1": m1, "shrzgm": shrzgm,
             "cpi": cpi, "ppi": ppi, "gdp": gdp, "export": export},
        ),
    }

    sectors = _build_cn_sectors(g)
    policy = _build_cn_policy(g)
    validation = {
        "leading": _name_group(g.get("leading", {})),
        "coincident": _name_group(g.get("coincident", {})),
        "lagging": _name_group(g.get("lagging", {})),
    }
    conclusion = _build_conclusion(economic_cycle, m_state, c_state, economic_cycle["evolution_paths"], "china")
    return {
        "economic_cycle": economic_cycle,
        "sectors": sectors,
        "policy": policy,
        "validation": validation,
        "conclusion": conclusion,
    }


# ============================================================================
# 美国 — 美林时钟 + 流动性第三轴
# ============================================================================
def _us_quadrant(gdp, cpi):
    gp = _pct(gdp)
    cp = _pct(cpi)
    gp = 50 if gp is None else gp
    cp = 50 if cp is None else cp
    if gp > 50 and cp > 50:
        return "过热", "🔴"
    if gp < 50 and cp > 50:
        return "滞胀", "🟡"
    if gp < 50 and cp < 50:
        return "衰退", "⚪"
    return "复苏", "🟢"


def _us_liquidity(fed):
    p = _pct(fed)
    if p is None:
        return "中性流动性", "🟡"
    if p > 70:
        return "紧流动性", "🔴"
    if p < 30:
        return "宽流动性", "🟢"
    return "中性流动性", "🟡"


_US_PHASE_TABLE = {
    ("过热", "紧流动性"): ("滞胀深化", "🔴", "现金>黄金>商品>债券>股票"),
    ("过热", "宽流动性"): ("过热", "🔴", "商品>股票>现金>债券"),
    ("过热", "中性流动性"): ("过热", "🔴", "商品>股票>现金>债券"),
    ("滞胀", "紧流动性"): ("滞胀加剧", "🔴", "现金>黄金>商品>债券>股票"),
    ("滞胀", "宽流动性"): ("滞胀", "🟡", "现金>商品>债券>股票"),
    ("滞胀", "中性流动性"): ("滞胀", "🟡", "现金>商品>债券>股票"),
    ("衰退", "宽流动性"): ("衰退尾声", "⚪", "债券>股票>现金>商品"),
    ("衰退", "紧流动性"): ("衰退", "⚪", "债券>现金>股票>商品"),
    ("衰退", "中性流动性"): ("衰退", "⚪", "债券>现金>股票>商品"),
    ("复苏", "宽流动性"): ("复苏强化", "🟢", "股票>债券>现金>商品"),
    ("复苏", "紧流动性"): ("复苏", "🟢", "股票>债券>现金>商品"),
    ("复苏", "中性流动性"): ("复苏", "🟢", "股票>债券>现金>商品"),
}


def _us_phase(q_state, liq_state):
    return _US_PHASE_TABLE[(q_state, liq_state)]


def _us_liq_desc(fed, liq_state):
    if not fed:
        return "政策利率（2Y美债代理）数据缺失"
    v = fed.get("value")
    p = fed.get("pct_5y")
    if v is None:
        return "政策利率数据缺失"
    tag = "紧" if "紧" in liq_state else ("宽" if "宽" in liq_state else "中性")
    return f"政策利率代理（2Y美债）={v}%，近5年分位{p}%（{tag}）"


def _us_asset_desc(q_state, liq_state, phase, gdp, cpi, fed):
    q_txt = {
        "过热": "经济过热，通胀高企",
        "滞胀": "滞胀，增长弱而通胀粘",
        "衰退": "衰退，增长走弱",
        "复苏": "复苏，需求边际改善",
    }.get(q_state, "方向待定")
    l_txt = {
        "紧流动性": "流动性收紧压制估值",
        "宽流动性": "流动性宽松托底风险资产",
        "中性流动性": "流动性中性",
    }.get(liq_state, "流动性中性")
    return f"{q_txt}（美林象限）；{l_txt}（第三轴）。当前定位「{phase}」，按美林+流动性框架做资产映射。"


def _build_us_sectors(g):
    household = {}
    pce = g.get("household", {}).get("pce")
    if pce:
        household["pce"] = _named(pce, "pce")
    retail = g.get("household", {}).get("retail_sales")
    if retail:
        household["retail_sales"] = _named(retail, "retail_sales")
    conf = g.get("household", {}).get("consumer_conf")
    if conf:
        household["consumer_conf"] = _named(conf, "consumer_conf")

    corporate = {}
    pmi = g.get("leading", {}).get("ism_pmi")
    if pmi:
        corporate["pmi"] = _named(pmi, "pmi")
    ip = g.get("coincident", {}).get("industrial_prod")
    if ip:
        corporate["industrial_prod"] = _named(ip, "industrial_prod")
    nf = g.get("coincident", {}).get("nonfarm")
    if nf:
        corporate["nonfarm"] = _named(nf, "nonfarm")

    government = {}
    deficit = g.get("government", {}).get("fed_deficit")
    if deficit:
        government["fed_deficit"] = _named(deficit, "fed_deficit")
    debt = g.get("government", {}).get("fed_debt")
    if debt:
        government["fed_debt"] = _named(debt, "fed_debt")

    external = {}
    tb = g.get("external", {}).get("trade_balance")
    if tb:
        external["trade_balance"] = _named(tb, "trade_balance")
    ca = g.get("external", {}).get("current_account")
    if ca:
        external["current_account"] = _named(ca, "current_account")

    return {"household": household, "corporate": corporate, "government": government, "external": external}


def _build_us_policy(g):
    monetary = {}
    if g.get("financial", {}).get("ust_2y"):
        monetary["ust_2y"] = _named(g["financial"]["ust_2y"], "ust_2y")
    if g.get("financial", {}).get("dxy"):
        monetary["dxy"] = _named(g["financial"]["dxy"], "dxy")
    fiscal = {}
    spending = g.get("fiscal", {}).get("fed_spending")
    if spending:
        fiscal["fed_spending"] = _named(spending, "fed_spending")
    receipts = g.get("fiscal", {}).get("fed_receipts")
    if receipts:
        fiscal["fed_receipts"] = _named(receipts, "fed_receipts")
    return {"monetary": monetary, "fiscal": fiscal}


def _build_us(g, evo_tmpl):
    gdp = g.get("coincident", {}).get("gdp")
    cpi = g.get("lagging", {}).get("cpi")
    ppi = g.get("lagging", {}).get("ppi")
    fed = g.get("financial", {}).get("ust_2y")  # Fed 政策利率代理（2Y 美债）
    tips = g.get("financial", {}).get("tips_10y")
    ust10 = g.get("financial", {}).get("ust_10y")
    hy = g.get("financial", {}).get("hy_spread")
    unemployment = g.get("lagging", {}).get("unemployment")

    q_state, q_icon = _us_quadrant(gdp, cpi)
    liq_state, liq_icon = _us_liquidity(fed)
    phase, icon, asset_base = _us_phase(q_state, liq_state)

    economic_cycle = {
        "method": "经典美林 + 流动性第三轴",
        "phase": phase,
        "icon": icon,
        "asset_base": asset_base,
        "asset_desc": _us_asset_desc(q_state, liq_state, phase, gdp, cpi, fed),
        "quadrant": {"state": q_state, "icon": q_icon},
        "liquidity": {
            "state": liq_state,
            "icon": liq_icon,
            "desc": _us_liq_desc(fed, liq_state),
            "score": {"宽流动性": -1, "中性流动性": 0, "紧流动性": 1}.get(liq_state, 0),
            "reasons": [_us_liq_desc(fed, liq_state)],
        },
        "quadrant_indicators": {
            "gdp": _named(gdp, "gdp"),
            "cpi": _named(cpi, "cpi"),
            "ppi": _named(ppi, "ppi"),
        },
        "liquidity_indicators": {
            "fed_funds": _named(fed, "fed_funds"),
            "tips_10y": _named(tips, "tips_10y"),
            "ust_10y": _named(ust10, "ust_10y"),
            "hy_spread": _named(hy, "hy_spread"),
        },
        "evolution_paths": _build_evolution_paths(
            evo_tmpl,
            {"fed_funds": fed, "cpi": cpi, "unemployment": unemployment, "hy_spread": hy},
        ),
    }

    sectors = _build_us_sectors(g)
    policy = _build_us_policy(g)
    validation = {
        "leading": _name_group(g.get("leading", {})),
        "coincident": _name_group(g.get("coincident", {})),
        "lagging": _name_group(g.get("lagging", {})),
    }
    conclusion = _build_conclusion(economic_cycle, q_state, liq_state, economic_cycle["evolution_paths"], "us")
    return {
        "economic_cycle": economic_cycle,
        "sectors": sectors,
        "policy": policy,
        "validation": validation,
        "conclusion": conclusion,
    }


# ============================================================================
# 第五层 conclusion（从 economic_cycle 自动推导）
# ============================================================================
def _build_conclusion(ec, m_state, c_state, evo, country):
    positioning = f"{ec['phase']}（{ec['icon']}）— 资产基准：{ec['asset_base']}"
    contradiction = _main_contradiction(country, m_state, c_state, ec)
    # scenarios 直接复用 evolution_paths（已含 name/probability/description 字段）
    scenarios = evo
    return {
        "positioning": positioning,
        "main_contradiction": contradiction,
        "scenarios": scenarios,
        "asset_signals": [],
    }


# ============================================================================
# asset_valuation（框架保留，真实分位从 fetch 补充）
# ============================================================================
def _asset_note(key, p5):
    if p5 is None:
        return "数据缺失，估值待更新"
    if p5 < 35:
        return f"近5年分位{p5}%，处相对低位"
    if p5 > 65:
        return f"近5年分位{p5}%，处相对高位"
    return f"近5年分位{p5}%，中性区间"


def _build_asset_valuation(cn_g, us_g, cross, av_tmpl):
    cn_stock_src = cn_g.get("valuation", {}).get("csi300_pe")
    cn_bond_src = cn_g.get("financial", {}).get("cn_10y")
    cn_re_src = cn_g.get("sectors", {}).get("house_price_index")
    us_stock_src = us_g.get("valuation", {}).get("sp500_pe")
    us_bond_src = us_g.get("financial", {}).get("ust_10y")
    usd_src = us_g.get("financial", {}).get("dxy")
    gold_src = us_g.get("market", {}).get("gold")
    commodity_src = us_g.get("market", {}).get("commodity")

    mapping = {
        "cn_stock": cn_stock_src,
        "cn_bond": cn_bond_src,
        "cn_realestate": cn_re_src,
        "us_stock": us_stock_src,
        "us_bond": us_bond_src,
        "usd": usd_src,
        "gold": gold_src,
        "commodity": commodity_src,
    }

    out = {}
    for key, src in mapping.items():
        p5 = src.get("pct_5y") if src else None
        p10 = src.get("pct_10y") if src else None
        name = ((av_tmpl.get(key) or {}).get("name")) or ASSET_NAMES.get(key, key)
        out[key] = {
            "name": name,
            "pct_5y": p5,
            "pct_10y": p10,
            "note": _asset_note(key, p5),
        }
    return out


# ============================================================================
# 主入口
# ============================================================================
def build_five_layer_payload(china_groups, us_groups, cross, china_raw=None, us_raw=None):
    """把旧分组结构重建成五层结构。

    :param china_groups: fetch_china.get_china_groups() 的返回（旧结构）
    :param us_groups:     fetch_us.get_us_groups() 的返回（旧结构）
    :param cross:         build_cross(...) 的返回
    :return: 五层结构 dict（含 update_time / frequency_policy / asset_valuation /
             china / us / cross），可直接写入 indicators.json
    """
    fp, av_tmpl, ecn_tmpl, eus_tmpl = load_framework()
    update_time = datetime.now().strftime("%Y-%m-%d")

    china = _build_china(china_groups, ecn_tmpl)
    us = _build_us(us_groups, eus_tmpl)
    asset_valuation = _build_asset_valuation(china_groups, us_groups, cross, av_tmpl)

    return {
        "update_time": update_time,
        "frequency_policy": fp,
        "asset_valuation": asset_valuation,
        "china": china,
        "us": us,
        "cross": cross or {},
    }


if __name__ == "__main__":
    # 简易自检：在没有任何网络数据的情况下构造空结构，验证不崩
    import pprint
    demo = build_five_layer_payload({}, {}, {})
    print("update_time =", demo["update_time"])
    print("china.phase =", demo["china"]["economic_cycle"]["phase"])
    print("us.phase =", demo["us"]["economic_cycle"]["phase"])
    print("asset_valuation keys =", list(demo["asset_valuation"].keys()))
    print("frequency_policy keys =", list(demo["frequency_policy"].keys()))
    print("china metrics =", count_metrics(demo["china"]))
    print("us metrics =", count_metrics(demo["us"]))
