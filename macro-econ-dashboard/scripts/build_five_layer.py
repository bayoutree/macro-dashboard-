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

from normalize import quantile_rank  # P1-10: M1 段内分位（双序列并行）

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
    "dgorder_yoy": "制造商新订单同比(DGORDER)",
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
    "hy_spread": "HY信用利差(HY OAS)",
    "ig_spread": "投资级信用利差(BAA−AAA)",
    "fed_funds": "2Y美债收益率",  # P0-4 收尾（裁决2026-11-01）：展示名定稿
    "dxy": "美元指数DXY",
    "usd_broad": "广义美元指数(叙事参考)",
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
    "fiscal_revenue_growth": "财政收入增速",
    "consumer_confidence": "消费者信心指数",
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
    # ig_spread(BAA−AAA) 为 FRED 月频；hy_spread(HY OAS BAMLH0A0HYM2) 为日频
    "weekly": ["bill_rate", "eia_crude", "mba_mortgage", "nfib"],
    "monthly": [
        "pmi", "cpi", "ppi", "m1", "m2", "social_financing", "gdp", "nonfarm",
        "dgorder_yoy", "industrial_production", "fiscal_balance", "trade_balance",
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
            {"desc": "CPI同比破2%"},  # P0-3 C6：口径对齐 headline CPI；食品分项见 caliber_notes
        ],
        "fail": "若PPI回落至2%以下且DR007维持低位，回归复苏路径",
        "caliber_notes": ["C6 口径注记：原「食品项推升CPI」食品分项无免费数据源，暂以 headline CPI 判定（P0-3 裁决）"],
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
            {"desc": "Fed开始降息(目标区间上限≤4.5%)"},  # P0-3 U2 裁决：改接 fed_watch 真实目标区间
            {"desc": "失业率突破4.2%"},
        ],
        "fail": "若CPI回落且美联储开启降息周期，则向典型衰退/软着陆切换",
        "confidence": "满足3/3=85% · 2/3=60% · 1/3=30%",
    },
    {
        "from": "深度滞胀", "to": "滞胀延续", "prob": 35,
        "conditions": [
            {"desc": "CPI维持3%以上"},
            {"desc": "Fed维持高利率(目标区间下限≥5.0%)"},  # P0-3 U5 裁决：目标区间下限
        ],
        "fail": "若通胀回落且美联储转鸽，滞胀延续概率下降",
        "data_gaps": ["薪资增速：数据缺口（待接 FRED AHE CES0500000003）——已按 P0-3 裁决移除，不再参与判定"],
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


def _percentile(vals, pct):
    """纯 Python 分位数（线性插值）。"""
    if not vals:
        return None
    xs = sorted(vals)
    k = (len(xs) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _drawdown_from_peak(history, window=252, min_points=60):
    """N 个观测窗口内的峰值回撤：(窗口峰值-最新)/峰值。数据不足返回 None（P0-3 U9）。"""
    vals = [x.get("value") for x in (history or []) if x.get("value") is not None]
    if len(vals) < min_points:
        return None
    win = vals[-window:]
    peak = max(win)
    if peak <= 0:
        return None
    return (peak - win[-1]) / peak


def _eval_condition(desc, ctx):
    """根据条件文本 + 真实指标，确定性地返回 met 三态。

    P0-3 工程准备（2026-11-01 CIO）：
      True  = 条件满足；False = 有真实数据且明确不满足；
      None  = 数据缺口，无法评估（无指标映射 / 历史不足 / 关键字段缺失）。
    旧版「无法判定→False」把数据缺口伪装成已评估的红灯
    （如「薪资增速维持4%以上」无指标映射，恒显示 met=False 正在误导页面）。
    """
    if not desc:
        return None

    # P0-3 裁决①（投资分析 2026-11-01）：Fed 政策事实层条件改接 fed_watch 真实目标区间
    # （与流动性「市场定价层」分工，禁止互套锚）。fed_watch 缺失 → None（数据缺口不落 False）。
    if ("Fed" in desc or "联邦基金" in desc) and ("降息" in desc or "高利率" in desc):
        fw = ctx.get("fed_watch")
        tr = (fw or {}).get("range") or []
        if len(tr) != 2 or tr[0] is None or tr[1] is None:
            return None
        thr_m = re.search(r"(\d+(?:\.\d+)?)\s*%", desc)
        if not thr_m:
            return None
        thr = float(thr_m.group(1))
        if "降息" in desc:
            return tr[1] <= thr   # U2: 目标区间上限 ≤ 阈值
        return tr[0] >= thr       # U5: 目标区间下限 ≥ 阈值

    # P0-3 裁决②：美股回撤超20%（U9 接线，252 交易日窗口峰值回撤）
    if "回撤" in desc and "美股" in desc:
        sp = ctx.get("sp500")
        if not sp:
            return None
        dd = _drawdown_from_peak(sp.get("history"))
        if dd is None:
            return None
        thr_m = re.search(r"(\d+(?:\.\d+)?)\s*%", desc)
        thr = float(thr_m.group(1)) / 100.0 if thr_m else 0.20
        return dd >= thr

    metric = _detect_metric(desc, ctx)

    # 连续回升 且 >X%
    m_inc = re.search(r"连续\s*(\d+)\s*月\s*回升", desc)
    if m_inc:
        if metric is None:
            return None
        n = int(m_inc.group(1))
        h = metric.get("history") or []
        vals = [x.get("value") for x in h[-n:]] if len(h) >= n else []
        if len(vals) < n or any(v is None for v in vals):
            return None  # 历史不足 = 数据缺口，不是「不满足」
        ok = _consecutive_increase(metric, n)
        gt = re.search(r">\s*(\d+(?:\.\d+)?)\s*%", desc)
        if gt:
            v = metric.get("value")
            if v is None:
                return None
            ok = ok and v > float(gt.group(1))
        return bool(ok)

    # 连续负增
    m_neg = re.search(r"连续\s*(\d+)\s*月\s*负增", desc)
    if m_neg:
        if metric is None:
            return None
        n = int(m_neg.group(1))
        h = metric.get("history") or []
        vals = [x.get("value") for x in h[-n:]] if len(h) >= n else []
        if len(vals) < n or any(v is None for v in vals):
            return None
        return bool(_consecutive_negative(metric, n))

    # P0-3 裁决③：「突破 X%」防抖——连续 2 期 ≥ 阈值，或单期 ≥ 阈值+0.2pp
    if "突破" in desc:
        lm = re.search(r"突破\s*(\d+(?:\.\d+)?)\s*%", desc)
        if not lm or metric is None:
            return None
        thr = float(lm.group(1))
        vals = [x.get("value") for x in (metric.get("history") or []) if x.get("value") is not None]
        if not vals:
            return None
        if vals[-1] >= thr + 0.2:
            return True
        return len(vals) >= 2 and vals[-1] >= thr and vals[-2] >= thr

    # P0-3 裁决④：「走阔至 X 分位以上」防抖（日频贴线抖动）——阈值水平由 5 年窗口
    # 分位数反解，要求最近 2 个观测均在阈值上方。
    m_widen = re.search(r"走阔至\s*(\d+(?:\.\d+)?)\s*分位以上", desc)
    if m_widen:
        if metric is None:
            return None
        thr = float(m_widen.group(1))
        vals = [x.get("value") for x in (metric.get("history") or []) if x.get("value") is not None]
        if len(vals) < 30:
            return None
        level = _percentile(vals[-1260:], thr)
        if level is None:
            return None
        return all(v > level for v in vals[-2:])

    if metric is None:
        return None  # 无指标映射 = 数据缺口

    # 分位比较
    pm = re.search(r"(\d+(?:\.\d+)?)\s*分位", desc)
    if pm:
        thr = float(pm.group(1))
        op = "below" if ("以下" in desc or "跌破" in desc) else "above"
        p = metric.get("pct_5y")
        if p is None:
            return None
        return (p < thr) if op == "below" else (p > thr)

    # 水平值比较
    lm = re.search(r"(?:<|>|破|回落至|跌破|维持|超过|突破|至)\s*(\d+(?:\.\d+)?)\s*%", desc)
    if lm:
        thr = float(lm.group(1))
        op = "below" if ("<" in desc or "以下" in desc or "回落至" in desc or "跌破" in desc) else "above"
        v = metric.get("value")
        if v is None:
            return None
        return (v < thr) if op == "below" else (v > thr)

    return None  # 条件文本无法解析 = 不假装评估


def _build_evolution_paths(tmpl, ctx, from_phase=None):
    """构建情景路径。

    T-2(2026-10-10) 情景基准动态化：`from` 不再沿用模板里硬编码的「深度滞胀」，
    而是由当前定位 phase 派生（与 positioning 出自同一判定函数 _us_phase），
    消除「定位=复苏 vs 情景基准=深度滞胀」互斥（P0-4）。
    """
    out = []
    for p in tmpl:
        conds = []
        for c in p.get("conditions", []):
            met = _eval_condition(c.get("desc", ""), ctx)
            # P0-3：met 三态 True/False/None；evaluated=False 时前端灰灯「数据缺口·未评估」
            conds.append({"desc": c.get("desc", ""),
                          "met": met,
                          "evaluated": met is not None})
        # from 由定位派生；未传入时回退模板值（保持向后兼容）
        from_label = from_phase or p.get("from")
        ep = {
            "from": from_label,
            "from_source": "economic_cycle.phase" if from_phase else "template",
            "to": p.get("to"),
            "prob": p.get("prob"),
            "conditions": conds,
            "trigger": p.get("trigger"),
            "fail": p.get("fail"),
            "confidence": p.get("confidence"),
            "data_gaps": p.get("data_gaps") or [],
            "caliber_notes": p.get("caliber_notes") or [],
            # 同时暴露为场景字段，供前端 conclusion.scenarios 渲染
            "name": f"{from_label}→{p.get('to')}",
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
            "m1": _m1_enhanced(_named(m1, "m1"), g),  # P1-10：双序列并行+双护栏
            "shrzgm": _sf_enhanced(_named(shrzgm, "shrzgm"), g),  # P1-10b：社融缺口锚
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
    # P1-9 归属表：M1 唯一计算点=信用维度（货币-信用层）；validation 领先组改只读引用
    if validation["leading"].get("m1") and economic_cycle["credit_indicators"].get("m1"):
        validation["leading"]["m1"] = _ref_copy(
            economic_cycle["credit_indicators"]["m1"],
            "china.economic_cycle.credit_indicators.m1",
            "NBS 货币供应量 M1（akshare macro_china_money_supply），同比%、月末；2025-01 起修订口径",
        )
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


# P0-4（2026-11-01 CIO）：政策利率口径修复。
# 旧版把 2Y 美债冠名冒充政策利率——2Y 含降息预期，
# 混用系统性高估紧缩（评估报告 P0-4：2Y=4.61% vs 目标区间中值 3.875%）。
# 修复：流动性块并列展示「Fed 目标区间中值（引仓库 data/fed_watch.json
# target_range）+ 2Y 美债」，命名与文案不再混淆两层口径。
def _load_fed_watch():
    """读取仓库根 data/fed_watch.json，返回 {mid, range, as_of}；失败 None。"""
    fw_path = os.path.join(os.path.dirname(PROJECT_ROOT), "data", "fed_watch.json")
    try:
        with open(fw_path, encoding="utf-8") as f:
            fw = json.load(f)
        tr = fw.get("target_range") or []
        if len(tr) != 2 or tr[0] is None or tr[1] is None:
            return None
        return {"mid": round((tr[0] + tr[1]) / 2, 4), "range": tr, "as_of": fw.get("as_of"),
                "cumulative_hike_prob": fw.get("cumulative_hike_prob")}  # 裁决②加息定价注记用
    except Exception as e:
        print(f"[five-layer] fed_watch 读取失败（降级无双排）: {e}")
        return None


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


def _us_liq_caliber_note(fw):
    """流动性判定口径文案（投资分析 2026-11-01 裁决定稿，事实层/定价层分工）。"""
    rng_txt = (f"当前{fw['range'][0]}-{fw['range'][1]}%，中值{fw['mid']}%"
               if fw else "目标区间数据缺失")
    return ("流动性松紧按2Y美债收益率近5年分位判定，反映市场对政策路径的定价"
            "（含降息/加息预期与期限溢价），非美联储当前政策利率；"
            f"政策利率事实见联邦基金目标区间（{rng_txt}）。")


def _us_liq_divergence_note(fed, fw):
    """|2Y−目标区间中值|>75bp 时 🟡 提示背离（只提示不翻转）。当前 73.5bp 贴线不触发。"""
    if not fw or not fed or fed.get("value") is None:
        return None
    gap_bp = abs(fed["value"] - fw["mid"]) * 100
    if gap_bp > 75:
        return (f"🟡 市场定价与政策利率背离较大（{gap_bp:.0f}bp>75bp，"
                "定价深度降息/加息预期）")
    return None


# 投资分析《复核裁决_美元分位差与加息定价注记》2026-11-01 ②：
# 政策事实层（U2：已降息）与市场定价层（期货累计加息定价）背离 → 主副双行注记。
# 数值全部绑定实时字段（target_range/cumulative_hike_prob/2Y−区间上限），禁硬编码；
# 副注记"解读"标由前端渲染。该背离不自动判输 v4，仅作复核输入项（裁决实现要求2）。
HIKE_PRICING_NOTE_MIN_PROB = 0.5   # 累计加息定价≥50% 方构成"背离"（实现阈值，投资分析可调）


def _us_hike_pricing_note(fed, fw):
    """返回 (主注记, 副注记) 或 (None, None)。触发：U2 已触发（上限≤4.5%）且加息定价≥阈值。"""
    if not fw:
        return None, None
    tr = fw.get("range") or []
    hp = fw.get("cumulative_hike_prob")
    if len(tr) != 2 or tr[0] is None or tr[1] is None or hp is None:
        return None, None
    if tr[1] > 4.5:            # 与 U2 判定同源（区间上限≤阈值即降息触发），未降息无背离
        return None, None
    if hp < HIKE_PRICING_NOTE_MIN_PROB:
        return None, None
    ust2y = (fed or {}).get("value")
    if ust2y is None:
        spread_clause = "2Y 美债数据缺失"
    else:
        spread_bp = round((ust2y - tr[1]) * 100)
        if spread_bp >= 0:
            spread_clause = f"2Y 美债高于区间上限约 {spread_bp}bp"
        else:
            spread_clause = f"2Y 美债低于区间上限约 {abs(spread_bp)}bp"
    main = (f"政策事实与市场定价背离：联邦基金目标区间已降至 {tr[0]}-{tr[1]}%"
            f"（“Fed开始降息”触发=true），但联邦基金期货累计加息定价 {hp * 100:.2f}%，"
            f"{spread_clause}。")
    sub = ("市场定价降息路径不可持续（通胀反复→政策反转风险），与“温和复苏”框架前提存在张力；"
           "若加息定价兑现，宏观截面相位与周期看板情景需同步复核。")
    return main, sub


def _us_liq_desc(fed, liq_state, fw=None):
    """P0-4：目标区间中值（真政策利率）与 2Y 美债并列；2Y 不再冠名政策利率。"""
    tag = "紧" if "紧" in liq_state else ("宽" if "宽" in liq_state else "中性")
    fw_txt = (f"Fed目标区间中值={fw['mid']}%（{fw['range'][0]}~{fw['range'][1]}%，"
              f"fed_watch {fw['as_of']}）") if fw else None
    if not fed or fed.get("value") is None:
        ust_txt = "2Y美债数据缺失"
    else:
        v, p = fed.get("value"), fed.get("pct_5y")
        ust_txt = f"2Y美债={v}%（近5年分位{p}%，{tag}）"
    if fw_txt:
        return f"{fw_txt}；{ust_txt}"
    return f"{ust_txt}（fed_watch 目标区间数据缺失）"


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
    # 口径纠正：该位置为 DGORDER 同比，不是 ISM PMI（真实 ISM PMI 源已断更）
    dgorder = g.get("leading", {}).get("dgorder_yoy")
    if dgorder:
        corporate["dgorder_yoy"] = _named(dgorder, "dgorder_yoy")
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


# 投资分析复裁 2026-11-01：Δpct 注记弃用固定阈值，改两档分布锚定
# （1260日滚动 Δpct 分布，满5年窗子集 n=4217 实测：P75=14 / P90=24）。
# 阈值年度随管道窗口重算并登记口径词典（看板改进11月/口径词典条目_铜与美元_20261101.md）。
USD_DELTA_NOTE_WATCH_PP = 14.0   # 🟡 关注档（P75）
USD_DELTA_NOTE_STRONG_PP = 24.0  # 🔴 强信号档（P90）


def _build_us_policy(g):
    monetary = {}
    if g.get("financial", {}).get("ust_2y"):
        monetary["ust_2y"] = _named(g["financial"]["ust_2y"], "ust_2y")
    if g.get("financial", {}).get("dxy"):
        monetary["dxy"] = _named(g["financial"]["dxy"], "dxy")
    # P0-2 裁决：DTWEXBGS 保留为叙事参考序列（展示位=利率层/美元环流叙事）
    if g.get("financial", {}).get("usd_broad"):
        monetary["usd_broad"] = _named(g["financial"]["usd_broad"], "usd_broad")
    # 投资分析裁决 2026-11-01 ①升级建议：Δpct=usd_broad−DXY（同 5 年分位窗）超阈值亮注记，
    # 数值绑定实时分位字段，随刷新自动更新。
    _dxy_m, _ub_m = monetary.get("dxy"), monetary.get("usd_broad")
    if _dxy_m and _ub_m:
        _p5_dxy, _p5_ub = _dxy_m.get("pct_5y"), _ub_m.get("pct_5y")
        if _p5_dxy is not None and _p5_ub is not None:
            _delta = round(_p5_ub - _p5_dxy, 1)
            _ub_m["delta_vs_dxy_pp"] = _delta
            if _delta > USD_DELTA_NOTE_STRONG_PP:
                _ub_m["delta_note_level"] = "strong"
                _ub_m["delta_note"] = (
                    f"广义美元相对 G10 显著偏强（分位差 {_delta:.0f}pp>P90），新兴市场货币承压")
            elif _delta > USD_DELTA_NOTE_WATCH_PP:
                _ub_m["delta_note_level"] = "watch"
                _ub_m["delta_note"] = (
                    f"广义美元相对 G10 偏强（分位差 {_delta:.0f}pp>P75），新兴市场货币承压")
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
    fed = g.get("financial", {}).get("ust_2y")  # 流动性判定锚（2Y 美债，市场定价层）
    tips = g.get("financial", {}).get("tips_10y")
    ust10 = g.get("financial", {}).get("ust_10y")
    hy = g.get("financial", {}).get("hy_spread")
    ig = g.get("financial", {}).get("ig_spread")
    unemployment = g.get("lagging", {}).get("unemployment")

    fw = _load_fed_watch()  # P0-4：政策利率真值（目标区间中值）
    _hike_note = _us_hike_pricing_note(fed, fw)  # 裁决②：(主注记, 副注记) 或 (None, None)
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
            "desc": _us_liq_desc(fed, liq_state, fw),
            "score": {"宽流动性": -1, "中性流动性": 0, "紧流动性": 1}.get(liq_state, 0),
            "reasons": [_us_liq_desc(fed, liq_state, fw)],
            # P0-4：真政策利率节点（目标区间中值），与 2Y 美债并列展示
            "target_rate_mid": ({"name": "Fed目标区间中值", "value": fw["mid"],
                                 "unit": "%", "date": fw["as_of"], "decimals": 2,
                                 "source": "data/fed_watch.json (CME FedWatch target_range)"}
                                if fw else None),
            # 口径文案与背离注记（投资分析裁决 2026-11-01 定稿）
            "caliber_note": _us_liq_caliber_note(fw),
            "divergence_note": _us_liq_divergence_note(fed, fw),
            # 裁决②：加息定价背离双行注记（主=事实层；副=解读层，前端带"解读"标）
            "hike_pricing_note": _hike_note[0],
            "hike_pricing_note_sub": _hike_note[1],
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
            "ig_spread": _named(ig, "ig_spread"),
        },
        "evolution_paths": _build_evolution_paths(
            evo_tmpl,
            {"fed_funds": fed, "cpi": cpi, "unemployment": unemployment, "hy_spread": hy,
             "fed_watch": fw,  # P0-3 U2/U5：政策事实层（降息/高利率判定）
             "sp500": g.get("market", {}).get("sp500")},  # P0-3 U9：252日回撤
            from_phase=phase,
        ),
    }

    # 裁决②：U2 条件旁精简注记（主副双行全文见「流动性第三轴」卡，避免长文重复）
    if _hike_note[0] and fw and fw.get("cumulative_hike_prob") is not None:
        _hp_txt = f"{fw['cumulative_hike_prob'] * 100:.2f}%"
        for _p in economic_cycle["evolution_paths"]:
            if any("Fed开始降息" in (c.get("desc") or "") for c in _p.get("conditions", [])):
                _p.setdefault("caliber_notes", []).append(
                    f"注：政策事实层已降息 vs 市场定价层累计加息定价 {_hp_txt} 存在背离，"
                    "主副双行注记见「流动性第三轴」卡（裁决2026-11-01）。")

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


# ============================================================================
# P1-8（2026-11-02）：估值表趋势列 —— 位置（估值分位）与趋势同屏
#
# 数据源：仓库根 data/asset_prices.json（scripts/fetch_asset_data.py 产出，日频）。
#   ma200 / drawdown_from_high 由采集端基于 5 年全历史计算；
#   momentum_3m 为 63 个交易日动量（存档窗口不足时采集端以 mom_3m_source 标注近似口径）。
# 映射：L5 资产卡 → asset_prices 键；us_bond 为收益率口径（收益率↑=债价↓）；
#   cn_stock / cn_bond / cn_realestate 无日频价格序列 → trend=None（前端灰显「—」）。
# ============================================================================
# ============================================================================
# P1-10 · M1 双序列并行（裁决方案③）+ 双护栏锚定（2026-11-03）
#   口径节点 2025-01（人民银行修订：纳入个人活期存款+非银支付机构客户备付金）；
#   分位段内计算，严禁跨口径混算；绝对锚 NBS 实测 2015-2019 n=60 锁定；
#   冲突规则：分位管常态相对位置、绝对锚管 regime 切换，冲突以绝对锚为准亮🟡注记。
# ============================================================================
M1_CALIBER_CHANGE = "2025-01"
M1_ANCHOR_RED_PP = -5.0     # 红线：利差<−5pp→紧信用🔴（≈历史P20-25下沿，触发率约17%）
M1_ANCHOR_CENTER_PP = 1.0   # 中枢 +1pp（中位数；替换早期+2pp估算，抗2016扭曲）
M1_ANCHOR_LOOSE_PP = 4.0    # 宽信用确认 >+4pp🟢（中枢+3pp，≈P60-65）


def _ym_to_idx(ym):
    y, m = str(ym).split("-")[:2]
    return int(y) * 12 + int(m)


def _interp_monthly(qpts, target_ym):
    """季频 [(YYYY-MM,value)] 线性插值到目标月；范围外取最近端点。"""
    pts = sorted((_ym_to_idx(d), v) for d, v in qpts)
    if not pts:
        return None
    ti = _ym_to_idx(target_ym)
    if ti <= pts[0][0]:
        return pts[0][1]
    if ti >= pts[-1][0]:
        return pts[-1][1]
    for (x0, v0), (x1, v1) in zip(pts, pts[1:]):
        if x0 <= ti <= x1:
            return v0 + (v1 - v0) * (ti - x0) / (x1 - x0)
    return pts[-1][1]


def _m1_dual_caliber(m1):
    """M1 双序列并行后处理（就地改 dict）：
    - 旧口径段（2008-01(akshare可得)~2024-12）/ 新口径段（2025-01 起）分位各自段内计算；
    - pct_5y 覆盖为新口径段内分位（样本 n 如实标注）；旧口径段内分位归档可查；
    - 永久口径节点标注。重叠期（2025年内）双显窗口已过——当前主显新口径、旧口径归档。"""
    if not m1 or not m1.get("history"):
        return
    hist = [(h["date"], h["value"]) for h in m1["history"]]
    old_seg = [(d, v) for d, v in hist if d < M1_CALIBER_CHANGE]
    new_seg = [(d, v) for d, v in hist if d >= M1_CALIBER_CHANGE]
    if new_seg:
        vals = [v for _, v in new_seg]
        m1["pct_5y"] = quantile_rank(vals[-1], vals)
        m1["pct_5y_caliber"] = f"新口径段内（{M1_CALIBER_CHANGE}起, n={len(vals)}）"
    if old_seg:
        cutoff_y = int(M1_CALIBER_CHANGE[:4]) - 5
        win = [v for d, v in old_seg if d >= f"{cutoff_y}-01"]
        m1["pct_5y_old_caliber"] = quantile_rank(old_seg[-1][1], win) if win else None
        m1["old_caliber_latest"] = {"date": old_seg[-1][0], "value": old_seg[-1][1]}
    m1["caliber_change_date"] = M1_CALIBER_CHANGE
    m1["caliber_note"] = ("2025-01 起 M1 为人民银行修订口径（纳入个人活期存款+非银支付机构客户备付金）；"
                          "分位段内计算，严禁跨口径混算（P1-10 裁决方案③）")


def _m1_double_guard(m1, nominal_gdp):
    """分位+绝对锚双护栏：利差=M1同比−名义GDP同比（现价累计同比，季频线性插值到月）。
    档位：利差<−5pp→紧信用🔴；−5~+1pp→中性🟡；>+4pp→宽信用确认🟢。
    冲突：分位判定与绝对锚背离→以绝对锚为准+🟡注记（不静默二选一）。"""
    if not m1 or not nominal_gdp:
        return None
    m1h = m1.get("history") or []
    gh = nominal_gdp.get("history") or []
    if len(m1h) < 2 or len(gh) < 2:
        return None
    qpts = [(h["date"], h["value"]) for h in gh]
    latest = m1h[-1]
    gdp_at = _interp_monthly(qpts, latest["date"])
    if gdp_at is None:
        return None
    spread = round(latest["value"] - gdp_at, 2)
    if spread < M1_ANCHOR_RED_PP:
        zone, light = "紧信用", "red"
    elif spread > M1_ANCHOR_LOOSE_PP:
        zone, light = "宽信用确认", "green"
    else:
        zone, light = "中性", "yellow"
    pct = m1.get("pct_5y")
    if pct is None:
        qzone = qlight = None
    elif pct < 30:
        qzone, qlight = "分位偏低", "red"
    elif pct > 60:
        qzone, qlight = "分位偏高", "green"
    else:
        qzone, qlight = "分位中性", "yellow"
    conflict = None
    if qlight and qlight != light:
        conflict = (f"分位判定（{qzone}）与绝对锚（{zone}）背离——以绝对锚为准"
                    f"（口径变更期分位易失真，P1-10 冲突规则）")
    return {
        "spread_pp": spread,
        "nominal_gdp_yoy": round(gdp_at, 2),
        "m1_latest": {"date": latest["date"], "value": latest["value"]},
        "anchor": {"red_pp": M1_ANCHOR_RED_PP, "center_pp": M1_ANCHOR_CENTER_PP,
                   "loose_pp": M1_ANCHOR_LOOSE_PP,
                   "source": "NBS 实测 2015-2019 n=60 锁定（P1-10 裁决 2026-11-01）"},
        "anchor_zone": zone, "anchor_light": light,
        "quantile_zone": qzone, "quantile_light": qlight,
        "final_light": light,
        "conflict_note": conflict,
        "note": ("利差=M1同比−名义GDP同比（现价累计同比，季频线性插值到月）；2025-01 前 M1 为旧口径、"
                 "之后为新口径（新旧口径差约1.5-2pp，估算待复核；满12个月新口径样本后复核锚值）"),
    }


def _ref_copy(home_node, home_path, caliber):
    """P1-9 归属表（2026-11-03）：引用位只读拷贝——展示字段直引唯一计算点，
    附 ref/as_of/caliber 元信息与 ref_only 标记；禁止二次加工（本地重算分位/趋势）。
    history_guard「重复计算点」检查据此校验：非 home 节点必须 ref_only 且数值与 home 一致。"""
    if not home_node:
        return None
    out = {k: home_node.get(k) for k in
           ("name", "value", "unit", "change", "pct_5y", "pct_5y_caliber",
            "date", "history", "double_guard", "caliber_note")}
    out["ref"] = home_path
    out["field"] = home_path.rsplit(".", 1)[-1]
    out["as_of"] = home_node.get("date")
    out["caliber"] = caliber
    out["ref_only"] = True
    return out


def _m1_enhanced(m1_node, g):
    """P1-10 挂载点：双序列并行（段内分位+口径标注）+ 双护栏锚定块。"""
    if not m1_node:
        return m1_node
    _m1_dual_caliber(m1_node)
    ng = ((g or {}).get("coincident") or {}).get("nominal_gdp_yoy")
    m1_node["double_guard"] = _m1_double_guard(m1_node, ng)
    return m1_node


# ---- P1-10b：社融缺口锚（红线<0 生效；中枢估算灰灯待投分复核）----
SF_GAP_ANCHOR_RED_PP = 0.0
SF_GAP_ANCHOR_CENTER_EST = "+1~+3"


def _sf_gap_anchor(shrzgm_node, ng_node):
    """社融缺口锚：缺口=社融存量同比−名义GDP同比（季频插值到月）。
    红线 <0 生效（信用扩张跑输名义增长=收缩🔴）；中枢 +1~+3pp 为估算灰灯，
    待投资分析复核锁定（P1-10 验收用例③）。
    口径：社融同比=央行各年表自带官方增速列（2018-07/09、2019-12 口径完善
    由官方衔接，避免跨年自算跨口径失真）；历史分位待中枢复核时一并定。"""
    if not shrzgm_node or not ng_node:
        return None
    hist = shrzgm_node.get("history") or []
    ng_hist = (ng_node or {}).get("history") or []
    if not hist or not ng_hist:
        return None
    ng_pts = [(h["date"], h["value"]) for h in ng_hist if h.get("value") is not None]
    if not ng_pts:
        return None
    latest = hist[-1]
    ng_latest = _interp_monthly(ng_pts, latest["date"])
    if ng_latest is None:
        return None
    gap = round(latest["value"] - ng_latest, 2)
    breach = gap < SF_GAP_ANCHOR_RED_PP
    return {
        "gap_pp": gap,
        "shrzgm_yoy": latest.get("value"),
        "nominal_gdp_yoy": round(ng_latest, 2),
        "anchor": {"red_pp": SF_GAP_ANCHOR_RED_PP,
                   "center_est_pp": SF_GAP_ANCHOR_CENTER_EST,
                   "center_status": "估算待回填"},
        "light": "red" if breach else "gray",
        "zone": ("红线触发·信用收缩（社融跑输名义增长）" if breach
                 else "红线未触发·中枢估算待复核"),
        "note": ("P1-10 社融缺口锚：红线<0生效；中枢+1~+3pp为估算灰灯，待投资分析复核锁定。"
                 "口径：社融同比=央行官方增速列（2018-07/09、2019-12口径完善由官方衔接）；"
                 "名义GDP=现价同比季频插值。历史分位待中枢复核时一并锁定。"),
        "as_of": latest.get("date"),
    }


def _sf_enhanced(shrzgm_node, g):
    if not shrzgm_node:
        return shrzgm_node
    ng = ((g or {}).get("coincident") or {}).get("nominal_gdp_yoy")
    shrzgm_node["gap_anchor"] = _sf_gap_anchor(shrzgm_node, ng)
    return shrzgm_node


_ASSET_TREND_MAP = {
    "us_stock": ("sp500", "price"),
    "us_bond": ("us_10y_bond", "yield"),
    "usd": ("usd_index", "price"),
    "gold": ("gold", "price"),
    "commodity": ("copper", "price"),
    # P1 回填（2026-11-03）：A股两序列（akshare，fetch_asset_data.py）
    "cn_stock": ("hs300", "price"),
    "cn_bond": ("cn_10y_yield", "yield"),  # 收益率口径同美债（收益率↑=债价↓）
}
_ASSET_PRICES_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), "data", "asset_prices.json")


def _load_asset_trends():
    """读取 asset_prices.json，返回 {L5资产键: 趋势块|None}。读数失败返回 {}（不阻塞构建）。"""
    try:
        with open(_ASSET_PRICES_PATH, encoding="utf-8") as f:
            prices = (json.load(f) or {}).get("prices") or {}
    except Exception:
        return {}
    trends = {}
    for asset_key, (price_key, caliber) in _ASSET_TREND_MAP.items():
        node = prices.get(price_key) or {}
        latest = node.get("latest")
        ma200_dev = node.get("ma200_dev_pct")
        dd52 = node.get("drawdown_from_high")
        mom3 = node.get("momentum_3m")
        if latest is None and ma200_dev is None and dd52 is None and mom3 is None:
            trends[asset_key] = None
            continue
        daily = node.get("daily") or []
        trends[asset_key] = {
            "ma200_dev_pct": ma200_dev,   # 最新价相对 200 日均线偏离 %
            "dd_52w_pct": dd52,           # 距 52 周高点回撤 %（价格口径恒 ≤0）
            "mom_3m_pct": mom3,           # 3 个月动量 %（63 个交易日；窗口近似时见 approx）
            "date": (daily[-1] or {}).get("date") if daily else None,
            "caliber": caliber,           # price=价格口径 / yield=收益率口径（与债价反向）
            "approx": bool(node.get("mom_3m_source")),  # 动量为存档窗口近似（CI 升级后消失）
        }
    return trends


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

    trends = _load_asset_trends()

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
            # P1-8 趋势列（无日频序列资产为 None，前端灰显「—」）
            "trend": trends.get(key),
        }
    return out


# ============================================================================
# P2-3 · 指标级 source/caliber 四元组登记（value / source / caliber / date）
#   CIO 实测：indicators.json 指标级节点 56 个，source/caliber 覆盖 0.0%。
#   本表按 (region, key) 登记主源与口径；_annotate_sources() 在生成末尾统一注入，
#   任何节点缺失即抛错（防止新增指标静默无来源）。region ∈ {china, us, cross}。
# ============================================================================
SOURCE_REGISTRY = {
    # ---------------- 中国 ----------------
    ("china", "dr007"): ("akshare：银行间质押式回购加权利率 DR007（中国货币网）", "7 天期、加权平均、%"),
    ("china", "m2"): ("NBS 货币供应量 M2（akshare macro_china_money_supply）", "同比 %、月末"),
    ("china", "m1"): ("NBS 货币供应量 M1（akshare macro_china_money_supply）", "同比 %、月末"),
    ("china", "retail"): ("NBS 社会消费品零售总额（akshare macro_china_consumer_goods_retail）", "当月同比 %"),
    ("china", "house_price"): ("NBS 70 城新建商品住宅价格指数（akshare macro_china_new_house_price）", "同比 %、70 城均值"),
    ("china", "industrial_profit"): ("NBS 工业企业利润总额累计同比（akshare macro_china_nbs_nation 工业>工业企业主要经济指标）", "累计同比 %（非当月）"),
    ("china", "mfg_investment"): ("NBS 固定资产投资·制造业（akshare macro_china_gdzctz）", "自年初累计同比 %"),
    ("china", "pmi"): ("NBS 制造业 PMI（akshare macro_china_pmi）", "扩散指数、50 为荣枯线"),
    ("china", "fiscal_revenue_growth"): ("财政部 公共财政收入（akshare macro_china_czsr）", "当月同比 %"),
    ("china", "export"): ("海关总署·NBS 对外经济>货物进出口总额（东财 RPT_ECONOMY_CUSTOMS 降级）", "当月同比 %、自然月"),
    ("china", "import"): ("海关总署·NBS 对外经济>货物进出口总额（东财 RPT_ECONOMY_CUSTOMS 降级）", "当月同比 %、自然月"),
    ("china", "trade_balance"): ("海关总署·NBS 进出口差额_当期值（东财 RPT_ECONOMY_CUSTOMS 降级）", "当月差额 亿美元"),
    ("china", "gdp"): ("NBS 国内生产总值（akshare macro_china_gdp）", "当季同比 %"),
    ("china", "industrial_prod"): ("NBS 工业增加值（akshare macro_china_gyzjz）", "当月同比 %"),
    ("china", "cpi"): ("NBS 居民消费价格指数（akshare macro_china_cpi_yearly）", "当月同比 %"),
    ("china", "ppi"): ("NBS 工业生产者出厂价格指数（akshare macro_china_ppi_yearly）", "当月同比 %"),
    ("china", "unemployment"): ("NBS 城镇调查失业率（akshare macro_china_urban_unemployment）", "%、月末"),
    ("china", "csi300_pe"): ("乐咕乐股 沪深300 市盈率（akshare stock_index_pe_lg）", "滚动市盈率 x"),
    # ---------------- 美国 ----------------
    ("us", "gdp"): ("FRED GDP", "实际 GDP 同比 %、4 季滚动（消除季调噪声）"),
    ("us", "cpi"): ("FRED CPIAUCSL", "同比 %、季调"),
    ("us", "ppi"): ("FRED PPIFIS", "最终服务 PPI 同比 %（核心 PPI 口径）"),
    ("us", "core_cpi"): ("FRED CPILFESL", "核心 CPI 同比 %"),
    ("us", "unemployment"): ("FRED UNRATE", "失业率 %、季调"),
    ("us", "fed_funds"): ("FRED DGS2", "2Y 美债收益率 %（非政策利率；政策利率=Fed 目标区间中值，引 data/fed_watch.json）"),
    ("us", "ust_2y"): ("FRED DGS2", "2Y 美债收益率 %"),
    ("us", "ust_10y"): ("FRED DGS10", "10Y 美债收益率 %"),
    ("us", "tips_10y"): ("FRED DFII10", "10Y TIPS 实际利率 %"),
    ("us", "hy_spread"): ("FRED BAMLH0A0HYM2（ICE BofA 美国高收益债 OAS）", "OAS %（非 BAA−AAA；两口径禁止对齐）"),
    ("us", "ig_spread"): ("FRED BAA / AAA", "投资级信用利差 BAA−AAA、bps（月频）"),
    ("us", "dxy"): ("DXY 洲际交易所美元指数（yfinance DX-Y.NYB）", "ICE 六货币篮子 EUR57.6/JPY13.6/GBP11.9/CAD9.1/SEK4.2/CHF3.6、不含 CNY/MXN、1973 起、日频；主屏口径（P0-2 裁决 2026-11-01）"),
    ("us", "usd_broad"): ("FRED DTWEXBGS", "美联储广义贸易加权美元指数、2006 起、H.10 周频；叙事参考（C2 利率层/美元环流），不进信号灯、不进 asset_valuation 分位（P0-2 裁决 2026-11-01）"),
    ("us", "pce"): ("FRED PCEC96", "实际个人消费支出同比 %"),
    ("us", "dgorder_yoy"): ("FRED DGORDER", "耐用品新订单同比 %"),
    ("us", "industrial_prod"): ("FRED INDPRO", "工业生产指数同比 %"),
    ("us", "nonfarm"): ("FRED PAYEMS", "非农就业人数同比 %"),
    ("us", "fed_debt"): ("FRED GFDEBTN", "联邦债务总额 亿美元"),
    ("us", "fed_deficit"): ("FRED MTSDS133FMS", "联邦财政赤字 亿美元"),
    ("us", "fed_receipts"): ("FRED W006RC1Q027SBEA", "联邦经常性收入 十亿美元"),
    ("us", "fed_spending"): ("FRED W006RC1Q027SBEA + MTSDS133FMS 派生", "联邦支出 = 收入 + 赤字，十亿美元"),
    ("us", "trade_balance"): ("FRED BOPGSTB", "货物贸易差额 亿美元"),
    ("us", "current_account"): ("FRED IEABC", "经常账户差额 亿美元"),
    ("us", "oecd_cli"): ("OECD 综合领先指标（FRED USALOLITOAASTSAM）", "振幅调整、100 为长期趋势"),
    ("us", "lei"): ("OECD 综合领先指标（FRED USALOLITOAASTSAM）", "美国 LEI 代理、振幅调整"),
    ("us", "yield_curve_10y2y"): ("FRED DGS10 − DGS2", "期限利差 bps（10Y−2Y）"),
    ("us", "consumer_confidence"): ("FRED UMCSENT", "密歇根消费者信心指数"),
    ("us", "sp500"): ("FRED SP500", "标普500 指数点"),
    ("us", "vix"): ("FRED VIXCLS", "VIX 波动率指数"),
    ("us", "sp500_pe"): ("FRED CAPE / USACAPE", "席勒周期调整市盈率 x"),
    ("us", "gold"): ("akshare macro_cons_gold / 仓库月度金价回退", "金价序列"),
    ("us", "commodity"): ("akshare 商品指数", "商品价格指数"),
    ("us", "retail"): ("FRED RSAFS", "零售销售同比 %"),
    # ---------------- 交叉 ----------------
    ("cross", "cn_us_10y_spread"): ("中债 10Y 国债 − FRED DGS10", "利差 bps（pp ×100）"),
    ("cross", "usdcny"): ("中国外汇交易中心 USDCNY 即期", "汇率、人民币/美元"),
}
SOURCE_FALLBACK = ("未登记（请补 SOURCE_REGISTRY）", "口径待补")


def _annotate_sources(node, region="", leaf=""):
    """递归为每个指标级节点（含 value + history）注入 source/caliber（P2-3 四元组）。

    四元组 = value / source / caliber / date。缺失来源直接抛错（不静默留白），
    以保证「新增指标必须登记来源」。返回注入的节点数。
    """
    n = 0
    if isinstance(node, dict):
        keys = set(node.keys())
        if "value" in keys and "history" in keys:
            src, cal = SOURCE_REGISTRY.get((region, leaf), SOURCE_FALLBACK)
            if src == SOURCE_FALLBACK[0]:
                raise KeyError(f"P2-3 未登记来源: region={region} key={leaf}")
            node["source"] = src
            node["caliber"] = cal
            return 1
        for k, v in node.items():
            child_leaf = leaf if k.isdigit() else k
            n += _annotate_sources(v, region if region else (k if k in ("china", "us", "cross") else ""),
                                   child_leaf)
    elif isinstance(node, list):
        for v in node:
            n += _annotate_sources(v, region, leaf)
    return n

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

    payload = {
        "update_time": update_time,
        "frequency_policy": fp,
        "asset_valuation": asset_valuation,
        "china": china,
        "us": us,
        "cross": cross or {},
    }
    # P2-3：统一注入 source/caliber（四元组），未登记来源即抛错
    n_src = _annotate_sources(payload)
    print(f"[five-layer] P2-3 source/caliber 已注入 {n_src} 个指标级节点")
    return payload


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
