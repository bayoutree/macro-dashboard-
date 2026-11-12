#!/usr/bin/env python3
"""
build_cycle_position_v4.py — 全球周期看板 v4 JSON 上游生成器（防复发关键环节）

背景
----
2026-09-23 事故：v4 数据文件由人工/会话直接编辑提交（仓库内历史上不存在任何
自动生成 v4 的脚本），在 v3→v4 手工合并时，cycle_consensus 被换成上游新
schema（dimension_scores 数组，删除 united_states/china 分键及其下
p1/p2/p3_score、p1/p2/p3_label、signal、raw_score、consensus_score、formula），
而前端 js/cycle_v4_patch.js、js/cycle_v3.js 仍按旧契约取值，导致证伪清单
6 处 undefined、排序副标题 "?" 值、两套评分并存。

09-24 二次修正：朱格拉维度新旧刻度不一致（见 P1 映射注释），直接采用新分值
会让 transmission_table 查表/矛盾组合检测把「扩张早期」误判为「扩张晚期」；
并新增 asset_ranking 重算逻辑，保证排序表与 transmission_table、
asset_allocation 配置卡口径一致。

本脚本职责
----------
输入: data/cycle_position_v3.json   —— 每日流水线唯一权威产物（新 schema）
输出: data/cycle_position_v4.json   —— 前端契约文件（新 schema + 旧契约兼容层）

1. 以 v3 为底座，保留 v4-only 增强板块（transmission_table、
   constraint_degradation、data_quality、contradiction_status；
   asset_ranking 不再原样保留，改为本脚本重算）；
2. 重建 cycle_consensus：保留新 schema 全部字段，并补回 united_states /
   china 分键（p1/p2/p3 兼容字段），映射规则见下方常量区注释；
3. 强制口径一致：synthesis 文本中的「共识度评分 NN/100」与
   cycle_consensus.overall_score 口径不一致时，以 overall_score 为准改写；
4. 重算 asset_ranking：按 transmission_table entries（US/CN 各自现态）取基线
   方向与置信度，叠加 constraint_degradation 的 C1/C2 降级，确定性排序。

注意：输出文件本身由本脚本整体重写，任何手工编辑都会在下次流水线运行时
被覆盖——请把变更需求落到本脚本或上游，不要直接改 data/cycle_position_v4.json。
"""

import json
import calendar as _calendar
import re as _re
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # 保证任意 cwd 下可 import
from freshness_score import compute_freshness, is_assessment_overdue  # P0-1 单一真值
from collect_structural_indicators import percentile_bands  # [CIO 2026-11-12] 分位带单一真值（#14 裁决）

DATA_DIR = Path(__file__).parent.parent / "data"
V3_FILE = DATA_DIR / "cycle_position_v3.json"
V4_FILE = DATA_DIR / "cycle_position_v4.json"
STUB_FLAGS_FILE = DATA_DIR / "v4_stub_flags.json"  # [CIO 2026-11-12] 全站桩值扫描清单

# ------------------------------------------------------------------
# 维度名 → dimension_scores 中的 dimension 字段值
# ------------------------------------------------------------------
DIM_JUGLAR = "朱格拉设备周期"
DIM_KITCHIN = "基钦库存周期"

# v4-only 增强板块：v3 中不存在、需要从上一版 v4 保留的顶层 key。
# 注意 asset_ranking 不在其中——09-16 旧版基于 (P1=2,P2=1) 手工编码且与
# asset_allocation_summary 矛盾，必须每次重算。
V4_PRESERVE_KEYS = [
    "transmission_table",
    "constraint_degradation",
    "data_quality",
    "contradiction_status",
]

# ------------------------------------------------------------------
# 旧契约公式（前端 cycle_v4_patch.js / cycle_v3.js 的既定口径，不可改）
#   raw_score       = P1*0.3 + P2*0.4 + P3*0.3
#   consensus_score = (raw_score + 2) / 4 * 100
# P1=朱格拉设备周期, P2=基钦库存周期, P3=美林时钟；旧刻度域 -2..2
# ------------------------------------------------------------------
FORMULA_TEXT = (
    "raw_score = P1×0.3 + P2×0.4 + P3×0.3, "
    "consensus_score = (raw_score + 2) / 4 × 100"
)

# ------------------------------------------------------------------
# 【层名映射 v3 → v4】v3 沿用 09-03 的 layer_* 命名，前端 v4 契约用新命名。
# 生成器必须在输出中用新命名，否则：① 二次运行时美林 signal_weight 从
# cycle_merrill_3d 取不到（P3 静默归零，非幂等）；② 前端渲染不到层。
# 规则：以 v3 层内容为权威底座（流水线更新的历史/current 都在其中），
# 仅做改名；v4 侧新增的子结构（如 rate regime 的 regime_state）从上一版
# v4 同层深合并补回，v3 同名字段不被覆盖。
# ------------------------------------------------------------------
LAYER_V3_TO_V4 = {
    "layer_0_debt_cycle": "constraint_debt_cycle",
    "layer_1_kondratieff": "narrative_kondratieff",
    "layer_2_perez": "narrative_perez",
    "layer_3_rate_regime": "constraint_rate_regime",
    "layer_4_juglar": "cycle_juglar",
    "layer_5_kitchner": "cycle_kitchin",
    "layer_6_merrill": "cycle_merrill_3d",
}


def _deep_merge(base, extra):
    """extra 的键补充进 base；dict 递归；base 已有值（含非空）一律不覆盖。"""
    if not isinstance(extra, dict):
        return base
    for k, v in extra.items():
        if k not in base:
            base[k] = v
        elif isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
    return base


def build_cycle_layers(v3_layers, prev_v4_layers):
    """改名 + v4-only 子结构补回。输出只含 v4 新命名。"""
    out = {}
    for old_key, new_key in LAYER_V3_TO_V4.items():
        layer = v3_layers.get(old_key)
        if layer is None:
            # v3 缺该层时退回上一版 v4 同层，保证前端结构完整
            if new_key in prev_v4_layers:
                out[new_key] = prev_v4_layers[new_key]
            continue
        merged = json.loads(json.dumps(layer))  # 深拷贝
        if new_key in prev_v4_layers:
            _deep_merge(merged, prev_v4_layers[new_key])
        out[new_key] = merged
    return out

# ------------------------------------------------------------------
# 【P1 朱格拉：新旧刻度差异 +1】
#
# 旧契约刻度（从 transmission_table 全部 16 个 entries 的 scenario 编码还原）：
#   P1= 2 扩张早期；P1= 1 扩张晚期；
#   P1=-1 收缩早期；P1=-2 收缩晚期。
# 新数据包 dimension_scores 的朱格拉刻度是围绕 0 对称的 -2..2 强度分，
# 与周期阶段位置错位一格。证据（同一现实状态）：
#   旧 v4 (09-16, 722f616) US p1=2 label「扩张早期」；
#   新 dimension_scores 朱格拉 us_score=1 assessment「中美均处于扩张早期」。
# 故映射规则：旧 P1 = 新 us_score/cn_score + 1，超出 [-2,2] 则截断。
# （新 -2→旧 -1, 新 -1→旧 0, 新 0→旧 1, 新 1→旧 2, 新 2→旧 2 截断）
# 分国分值缺失时先按 0（中性）代入，映射后为旧刻度 1，不凭空编造。
# ------------------------------------------------------------------
JUGLAR_OLD_PHASE = {2: "扩张早期", 1: "扩张晚期", 0: "中性", -1: "收缩早期", -2: "收缩晚期"}

# ------------------------------------------------------------------
# 【P2 基钦：两刻度一致，不平移】
#
# 证据：旧 US p2=1 label「被动补库」，新 us_score=2 assessment「主动补库存」
# ——数值随库存阶段进展而增大（-2 主动去库/-1 被动去库/1 被动补库/2 主动补库），
# 新旧同名同值。旧刻度中没有 0，新分值缺失/为 0 时记 0 中性。
# ------------------------------------------------------------------
KITCHIN_OLD_PHASE = {2: "主动补库", 1: "被动补库", 0: "中性", -1: "被动去库", -2: "主动去库"}

# ------------------------------------------------------------------
# 【P3 美林时钟】dimension_scores 中美林只有 us_assessment/cn_assessment
# 定性文字、无数值。规则定死：取 v4 美林三维层
# cycle_layers.cycle_merrill_3d.{us,cn}.signal_weight（-2..2，三维矩阵象限
# 位置直接产出），label 取同层 current_phase；缺失记 0 中性，不人工判断。
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# asset_ranking 重算规则
#
# 区域归属（entries 是美国视角编码，同表对称适用于中国）：
#   US entry 决定: us_equity, us_bond, usd, gold, commodities
#                   （黄金/商品为全球资产，以美元体系/美国周期为基准）
#   CN entry 决定: china_equity, china_bond, china_realestate
#
# 约束降级（从 constraint_degradation 读取，按其 currently_triggered 执行）：
#   C1: 8 资产同名键直接给 adjustment（us_equity/commodities -1，gold +1…）；
#   C2: 键名为语义别名，映射如下：
#       us_equity_valuation_sensitive → us_equity
#       us_bond_long_duration         → us_bond
#       gold/commodities 同名；others/短久期 → 无操作。
#   C1→C2 顺序叠加。语义：方向档位修正（非数值惩罚），neutral 是地板/天花板，
#   不允许穿透：adj<0 时 neutral 封底不穿到 down，adj>0 时 neutral 封顶不穿到 up；
#   仅基线已是 down/up 的资产保持原位。total_adj = 实际净改变 = final - base。
#
# 排序键（完全确定性）：
#   最终方向数值 desc → entry 置信度 desc → ASSET_TIE_PRIORITY 固定顺序。
# 信号文本：up=超配 / neutral=标配 / down=低配。
# ------------------------------------------------------------------
RANK_REGION_OF_ASSET = {
    "us_equity": "us", "us_bond": "us", "usd": "us",
    "gold": "us", "commodities": "us",
    "china_equity": "cn", "china_bond": "cn", "china_realestate": "cn",
}
# 仅作稳定排序的常量（非观点）：同方向同置信度时按此先后，不随行情变化。
ASSET_TIE_PRIORITY = [
    "china_equity", "usd", "us_equity", "commodities", "gold",
    "china_bond", "china_realestate", "us_bond",
]
C2_ALIAS = {
    "us_equity_valuation_sensitive": "us_equity",
    "us_bond_long_duration": "us_bond",
}
DIR_NUM = {"up": 1, "neutral": 0, "down": -1}
NUM_DIR = {1: "up", 0: "neutral", -1: "down"}
DIR_CN = {"up": "超配", "neutral": "标配", "down": "低配"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _dim_index(dimensions, name):
    for i, d in enumerate(dimensions):
        if d.get("dimension") == name:
            return i
    return -1


def _raw_country_score(entry, country_key):
    """新刻度分国分值；共享维度（康波/佩雷斯）只有 score 时回落 score；缺失 0。"""
    if entry is None:
        return 0
    v = entry.get(f"{country_key}_score")
    if v is None:
        v = entry.get("score", 0)
    return v if v is not None else 0


def map_p1_juglar(new_score):
    """旧 P1 = 新刻度 + 1，截断到 [-2,2]。"""
    return max(-2, min(2, new_score + 1))


def _merrill_p3(prev_v4, country_key):
    """取美林三维 signal_weight / current_phase。
    回落链：v4 命名 cycle_merrill_3d → v3 命名 layer_6_merrill（首次运行
    prev_v4 可能还保留 v3 层名，不做回落则 P3 静默归零→非幂等）。
    """
    layers = prev_v4.get("cycle_layers", {})
    merrill_layer = layers.get("cycle_merrill_3d") or layers.get("layer_6_merrill") or {}
    merrill = merrill_layer.get(country_key, {})
    return merrill.get("signal_weight", 0), merrill.get("current_phase")


def _signal(raw_score):
    """旧契约 signal 文本，按 consensus_score 分档（对齐前端既有展示）。"""
    consensus = (raw_score + 2) / 4 * 100
    if consensus >= 80:
        return "强烈看多"
    if consensus >= 60:
        return "看多"
    if consensus >= 40:
        return "中性"
    if consensus >= 20:
        return "看空"
    return "强烈看空"


def build_region_contract(country_key, dimensions, jug_idx, kit_idx, prev_v4):
    """构建单个区域（united_states/china）的旧契约分键。"""
    jug_entry = dimensions[jug_idx] if jug_idx >= 0 else None
    kit_entry = dimensions[kit_idx] if kit_idx >= 0 else None

    p1 = map_p1_juglar(_raw_country_score(jug_entry, country_key))
    p2 = _raw_country_score(kit_entry, country_key)   # 基钦两刻度一致，不平移
    p3, merrill_phase = _merrill_p3(prev_v4, country_key)

    raw = round(p1 * 0.3 + p2 * 0.4 + p3 * 0.3, 2)
    consensus = round((raw + 2) / 4 * 100, 1)

    return {
        "p1_score": p1,
        "p2_score": p2,
        "p3_score": p3,
        "p1_label": f"朱格拉设备周期-{JUGLAR_OLD_PHASE.get(p1, p1)}",
        "p2_label": f"基钦库存周期-{KITCHIN_OLD_PHASE.get(p2, p2)}",
        "p3_label": f"美林时钟-{merrill_phase}" if merrill_phase else "美林时钟-未获取（中性计分）",
        "raw_score": raw,
        "consensus_score": consensus,
        "signal": _signal(raw),
    }


def align_synthesis_score(v3_data, overall_score):
    """synthesis 文本「共识度评分 NN/100」强制对齐 overall_score。"""
    synthesis = v3_data.get("synthesis")
    if not isinstance(synthesis, dict):
        return v3_data, False, None
    text = synthesis.get("overall_assessment", "")
    m = re.search(r"共识度评分\s*(\d+)\s*/\s*100", text)
    if not m:
        return v3_data, False, None
    old = int(m.group(1))
    if old == overall_score:
        return v3_data, False, old
    synthesis["overall_assessment"] = re.sub(
        r"共识度评分\s*\d+\s*/\s*100",
        f"共识度评分{overall_score}/100",
        text,
    )
    return v3_data, True, old


def _entry_lookup(entries, p1, p2):
    """
    精确查 (p1,p2)；旧条目网格为 p1∈{2,1,-1,-2} × p2∈{2,1,-1,-2}。
    出现 0（中性）无精确条目时：同 p1（p1 同理就近，同向优先取更大值），
    取网格上最近的组合。该规则仅影响定性排序，确定性、可追溯。
    返回 (entry, 实际使用的(p1,p2))。
    """
    grid_p1 = [2, 1, -1, -2]
    grid_p2 = [2, 1, -1, -2]
    for e in entries:
        if e["p1_score"] == p1 and e["p2_score"] == p2:
            return e, (p1, p2)
    # 最近网格点：距离相同优先更高（更扩张）值
    eff_p1 = min(grid_p1, key=lambda x: (abs(x - p1), -x))
    eff_p2 = min(grid_p2, key=lambda x: (abs(x - p2), -x))
    for e in entries:
        if e["p1_score"] == eff_p1 and e["p2_score"] == eff_p2:
            return e, (eff_p1, eff_p2)
    return None, (eff_p1, eff_p2)


def _clamp_direction(cur, adj):
    """方向档位修正：每次 adj 修正一档，neutral 是地板/天花板，不穿透。
    adj<0: 已是 down(-1) 保持 down；否则 neutral(0) 封底不穿透到 down。
    adj>0: 已是 up(+1) 保持 up；否则 neutral(0) 封顶不穿透到 up。
    adj=0: 不变。
    """
    if adj < 0:
        return -1 if cur == -1 else max(0, cur + adj)
    elif adj > 0:
        return 1 if cur == 1 else min(1, cur + adj)
    return cur


def _apply_degradations(base_dir, asset, constraint):
    """返回 (最终方向, 实际净 adjustment)。
    语义：方向档位修正，不是数值惩罚。
    - C1/C2 的 -1 作用于基线方向，neutral 被封底不允许穿透到 down；
    - 仅基线已是 down 的资产保持 down；升级同理 neutral 封顶不穿透到 up。
    - total_adj = cur_final - cur_base（实际净改变），不叠加名义值。
    """
    base_num = DIR_NUM[base_dir]
    cur = base_num
    c1 = constraint.get("C1_debt_cycle", {})
    c2 = constraint.get("C2_rate_regime", {})

    if c1.get("currently_triggered"):
        adj = c1.get("degradation_rules", {}).get(asset, {}).get("adjustment", 0)
        cur = _clamp_direction(cur, adj)

    if c2.get("currently_triggered"):
        rules = c2.get("degradation_rules", {})
        key = asset
        if key not in rules:
            key = next((k for k, v in C2_ALIAS.items() if v == asset), None)
        adj = rules.get(key, {}).get("adjustment", 0) if key else 0
        cur = _clamp_direction(cur, adj)

    return NUM_DIR[cur], cur - base_num


def sync_c1_current_values(out, v3):
    """#14：把 constraint_degradation.C1_debt_cycle.current_values 对齐 v3 L0 主源。

    主源 = v3.cycle_layers.layer_0_debt_cycle.us.indicators.interest_to_revenue.current
           + 同层 debt_to_gdp（顶层简写，仅当为数值时采用）。
    返回 True 表示有改动。
    """
    l0_us = (v3.get("cycle_layers", {}).get("layer_0_debt_cycle", {}) or {}).get("us", {}) or {}
    ind = (l0_us.get("indicators", {}) or {}).get("interest_to_revenue", {}) or {}
    i2r = ind.get("current")
    d2g = l0_us.get("debt_to_gdp")
    cd = out.get("constraint_degradation")
    if not isinstance(cd, dict):
        return False
    c1 = cd.get("C1_debt_cycle")
    if not isinstance(c1, dict):
        return False
    cv = c1.setdefault("current_values", {})
    changed = False
    if isinstance(i2r, (int, float)) and cv.get("interest_to_revenue") != i2r:
        print(f"⚠ C1.current_values.interest_to_revenue {cv.get('interest_to_revenue')} → {i2r}（对齐 v3 L0 主源）")
        cv["interest_to_revenue"] = i2r
        changed = True
    if isinstance(d2g, (int, float)) and cv.get("debt_to_gdp") != d2g:
        print(f"⚠ C1.current_values.debt_to_gdp {cv.get('debt_to_gdp')} → {d2g}")
        cv["debt_to_gdp"] = d2g
        changed = True
    if changed:
        c1["current_values_source"] = ("cycle_position_v3.json#cycle_layers.layer_0_debt_cycle.us"
                                       ".indicators.interest_to_revenue（FRED A091RC1Q027SBEA/W006RC1Q027SBEA）")
    return changed


def recompute_asset_ranking(prev_v4, us, cn):
    """
    us/cn: 已构建的旧契约分键（含 p1/p2）。
    依据 transmission_table entries + constraint_degradation 重算 8 资产排序。
    """
    tt = prev_v4.get("transmission_table")
    if not isinstance(tt, dict) or "entries" not in tt:
        print("✗ 上一版 v4 缺少 transmission_table.entries，无法重算 ranking", file=sys.stderr)
        sys.exit(1)
    entries = tt["entries"]
    constraint = prev_v4.get("constraint_degradation", {})

    us_entry, us_eff = _entry_lookup(entries, us["p1_score"], us["p2_score"])
    cn_entry, cn_eff = _entry_lookup(entries, cn["p1_score"], cn["p2_score"])
    if us_entry is None or cn_entry is None:
        print("✗ transmission_table 中查不到 US/CN 现态条目", file=sys.stderr)
        sys.exit(1)

    region_entry = {"us": us_entry, "cn": cn_entry}
    rows = []
    for asset, region in RANK_REGION_OF_ASSET.items():
        a = region_entry[region]["assets"][asset]
        base_dir, confidence = a["direction"], a["confidence"]
        final_dir, adjustment = _apply_degradations(base_dir, asset, constraint)
        rows.append({
            "asset": asset,
            "signal": DIR_CN[final_dir],
            "adjusted_direction": final_dir,
            "base_direction": base_dir,
            "adjustment": adjustment,
            "_confidence": confidence,
        })

    priority = {a: i for i, a in enumerate(ASSET_TIE_PRIORITY)}
    rows.sort(key=lambda r: (-DIR_NUM[r["adjusted_direction"]], -r["_confidence"], priority[r["asset"]]))
    ranking = []
    for i, r in enumerate(rows, start=1):
        r.pop("_confidence")
        r = {"rank": i, **r}
        ranking.append(r)

    c1_on = constraint.get("C1_debt_cycle", {}).get("currently_triggered", False)
    c2_on = constraint.get("C2_rate_regime", {}).get("currently_triggered", False)
    active = [c for c, on in (("C1", c1_on), ("C2", c2_on)) if on]

    note_parts = []
    downgraded = [r["asset"] for r in ranking if r["adjustment"] < 0]
    upgraded = [r["asset"] for r in ranking if r["adjustment"] > 0]
    if downgraded:
        note_parts.append(f"降级资产: {', '.join(downgraded)}")
    if upgraded:
        note_parts.append(f"升级资产: {', '.join(upgraded)}")
    if not note_parts:
        note_parts.append("约束层未改变任何资产方向")
    note_parts.append("排序由 build_cycle_position_v4.py 按 US/CN 现态确定性重算，勿手工编辑")

    return {
        "version": "3.1",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "description": "8资产相对强弱排序（生成器按 US/CN 分区域现态重算）",
        "calculation_method": (
            f"transmission_table(US P1={us['p1_score']},P2={us['p2_score']} / "
            f"CN P1={cn['p1_score']},P2={cn['p2_score']}) + "
            f"constraint_degradation({'+'.join(active) if active else 'none'})"
        ),
        "base_scenario": (
            f"US:{us_entry['scenario']}(P1={us_eff[0]},P2={us_eff[1]}); "
            f"CN:{cn_entry['scenario'].replace('（当前美国）','')}(P1={cn_eff[0]},P2={cn_eff[1]})"
        ),
        "constraint_active": bool(active),
        "ranking": ranking,
        "note": "；".join(note_parts),
    }



# ------------------------------------------------------------------
# 数据修复：v3 静态数据中已知的质量问题，在 v4 生成时修正
# ------------------------------------------------------------------

def _fix_tfp_history(layers):
    """修复 US TFP history 混入指数值的 bug。
    
    根因：v3 narrative_kondratieff.indicators.tfp_growth.us.history 交替混入了
    FRED TFP 指数（82~108, base 100@2017）和增速值（0.3~1.5%），导致图表 US 线
    在 0.3~108 之间剧烈震荡。
    
    修复：从顶层 tfp.history（完整年度指数 1996-2025）计算 YoY 增速，
    替换 us.history 和顶层 history。CN 不受影响（已是增速值）。
    """
    nk = layers.get("narrative_kondratieff", {})
    tfp = nk.get("indicators", {}).get("tfp_growth", {})
    if not tfp:
        return layers, False
    
    us_hist = tfp.get("us", {}).get("history", [])
    top_hist = tfp.get("history", [])
    
    # 检测是否存在混合：US history 中同时有 >10 和 <10 的值
    has_index = any(item.get("value", 0) > 10 for item in us_hist)
    has_growth = any(0 < item.get("value", 0) < 10 for item in us_hist)
    
    if not (has_index and has_growth):
        return layers, False  # 没有混合问题，不修复
    
    # 从顶层 index 构建增速序列
    if not top_hist:
        return layers, False  # 无顶层 index，无法修复
    
    index_by_year = {}
    for item in top_hist:
        try:
            index_by_year[str(item["date"])] = float(item["value"])
        except (ValueError, TypeError):
            continue
    
    if len(index_by_year) < 2:
        return layers, False
    
    years = sorted(index_by_year.keys(), key=lambda y: int(y))
    
    # 获取 us.history 中已有的增速值年份（非指数值）
    existing_growth = {}
    for item in us_hist:
        v = item.get("value", 0)
        if 0 < v < 10:
            existing_growth[str(item["date"])] = v
    
    # 构建新的 US TFP 增速序列
    new_us_history = []
    
    # 保留 index 范围之前的已有增速值（如 1995）
    min_idx_year = int(years[0])
    for yr_str, val in sorted(existing_growth.items(), key=lambda x: int(x[0])):
        if int(yr_str) < min_idx_year:
            new_us_history.append({"date": yr_str, "value": val})
    
    # 从 index 计算 YoY 增速
    for i in range(len(years)):
        yr = years[i]
        if i == 0:
            # 第一年：用最早的已有增速值反推前一年 index
            first_growth = existing_growth.get(str(int(yr) - 1), 1.0)
            prev_idx = index_by_year[yr] / (1 + first_growth / 100)
            growth = round((index_by_year[yr] - prev_idx) / prev_idx * 100, 2)
        else:
            prev_yr = years[i - 1]
            growth = round((index_by_year[yr] - index_by_year[prev_yr]) / index_by_year[prev_yr] * 100, 2)
        new_us_history.append({"date": yr, "value": growth})
    
    # 保留 index 范围之后的已有增速值（如 2026）
    max_idx_year = int(years[-1])
    for yr_str, val in sorted(existing_growth.items(), key=lambda x: int(x[0])):
        if int(yr_str) > max_idx_year:
            new_us_history.append({"date": yr_str, "value": val})
    
    # 按年份排序
    new_us_history.sort(key=lambda x: int(x["date"]))
    
    # 应用修复
    tfp["us"]["history"] = new_us_history
    
    # 顶层 history 也转为增速（保持一致性）
    new_top_history = []
    for i in range(1, len(years)):
        prev_yr = years[i - 1]
        curr_yr = years[i]
        growth = round((index_by_year[curr_yr] - index_by_year[prev_yr]) / index_by_year[prev_yr] * 100, 2)
        new_top_history.append({"date": curr_yr, "value": growth})
    tfp["history"] = new_top_history
    
    return layers, True


def _fix_productivity_descriptions(layers):
    """为 productivity_growth 添加 per-region description。
    
    根因：v3 constraint_rate_regime.structural.indicators.productivity_growth
    只有顶层 description="TFP+资本深化的综合效率指标"，us/cn 没有各自的
    description，导致渲染时两国显示完全相同的文字。
    """
    crr = layers.get("constraint_rate_regime", {})
    structural = crr.get("structural", {})
    indicators = structural.get("indicators", {})
    pg = indicators.get("productivity_growth", {})
    
    if not pg:
        return layers, False
    
    changed = False
    
    us = pg.get("us", {})
    if us and "description" not in us:
        us["description"] = "US nonfarm business sector labor productivity growth (BLS)"
        changed = True
    
    cn = pg.get("cn", {})
    if cn and "description" not in cn:
        cn["description"] = "China labor productivity growth (GDP/employment, NBS)"
        changed = True
    
    return layers, changed



def _fix_new_orders_units(layers):
    """修复 Kitchin us.new_orders 单位混入。
    
    根因：us.history 交替混入 十亿美元（220-285）和 百万美元（280,000-347,000）。
    修复：大值 ÷ 1000 转为 十亿美元。
    """
    kitchin = layers.get("cycle_kitchin", {})
    us_data = kitchin.get("us", {})
    new_orders = us_data.get("indicators", {}).get("new_orders", {})
    if not new_orders:
        return layers, False
    
    hist = new_orders.get("history", [])
    if not hist:
        return layers, False
    
    # 检测混合：同时存在 <1000 和 >=1000 的值
    has_small = any(item.get("value", 0) < 1000 for item in hist)
    has_large = any(item.get("value", 0) >= 1000 for item in hist)
    
    if not (has_small and has_large):
        return layers, False
    
    # 转换大值
    fixed = 0
    for item in hist:
        if item.get("value", 0) >= 1000:
            item["value"] = round(item["value"] / 1000, 1)
            fixed += 1
    
    return layers, fixed > 0


def _fix_credit_spread_units(layers):
    """修复 Rate Regime credit_spread_ig 单位混入。
    
    根因：us.history 混入 0.1bp 单位值（3500-3645）与 % 值（0.9-2.5）。
    修复：大值 ÷ 1000 转为 %。
    """
    crr = layers.get("constraint_rate_regime", {})
    mb = crr.get("market_based", {})
    indicators = mb.get("indicators", {})
    csi = indicators.get("credit_spread_ig", {})
    if not csi:
        return layers, False
    
    us_hist = csi.get("us", {}).get("history", [])
    top_hist = csi.get("history", [])
    
    if not us_hist and not top_hist:
        return layers, False
    
    fixed = False
    
    # Fix US history
    if us_hist:
        has_small = any(item.get("value", 0) < 100 for item in us_hist)
        has_large = any(item.get("value", 0) >= 100 for item in us_hist)
        
        if has_small and has_large:
            for item in us_hist:
                if item.get("value", 0) >= 100:
                    item["value"] = round(item["value"] / 1000, 3)
            fixed = True
    
    # Fix top-level history
    if top_hist:
        has_small = any(item.get("value", 0) < 100 for item in top_hist)
        has_large = any(item.get("value", 0) >= 100 for item in top_hist)
        
        if has_small and has_large:
            for item in top_hist:
                if item.get("value", 0) >= 100:
                    item["value"] = round(item["value"] / 1000, 3)
            fixed = True
    
    return layers, fixed



# ------------------------------------------------------------------
# 【指标回灌】CIO P1-1 复验打回（2026-11-11）：层内 indicators 是静态快照，
# 上游（indicators.json/fred_raw）已到 9-10 月发布期而层内仍停 8 月。
# 每次构建从上游现值回灌：上游 history 尾业务期 > 层内尾业务期时，
# 追加缺失业务期点 + current/last_updated=上游尾业务期（不虚标为今天）。
# 上游缺失/停更的指标（ISM/产能利用率/库存销售比等）不伪造，由数据补采工单处理。
# ------------------------------------------------------------------
INDICATORS_JSON = DATA_DIR.parent / "macro-econ-dashboard" / "data" / "indicators.json"
FRED_RAW_JSON = DATA_DIR / "fred_raw.json"
NBS_RAW_JSON = DATA_DIR / "nbs_high_tech_raw.json"

# 元组: (层, side, 指标键, 源类型, 源路径, 除数换算 or None)
UPSTREAM_REFRESH = [
    ("cycle_merrill_3d", "us", "core_cpi", "ind", ["us", "validation", "lagging", "core_cpi"], None),
    ("cycle_merrill_3d", "cn", "cn_cpi", "ind", ["china", "validation", "lagging", "cpi"], None),
    ("cycle_merrill_3d", "cn", "cn_ppi", "ind", ["china", "validation", "lagging", "ppi"], None),
    ("cycle_merrill_3d", "cn", "cn_industrial_va", "ind", ["china", "validation", "coincident", "industrial_prod"], None),
    ("cycle_kitchin", "cn", "cn_pmi_manufacturing", "ind", ["china", "sectors", "corporate", "pmi"], None),
    # ---- 补采工单（CIO 2026-11-11 立项）：fred_raw 四系列 ----
    ("cycle_juglar", "us", "capacity_utilization", "fred", ["mcumfn"], None),
    ("cycle_kitchin", "us", "inventory_sales_ratio", "fred", ["isratio"], None),
    ("cycle_kitchin", "us", "new_orders", "fred", ["dgorder"], 1000.0),   # 百万美元→十亿
    ("cycle_kitchin", "us", "manufacturing_inventories", "fred", ["businv"], 1000.0),  # 百万→十亿；实为商业总库存口径
    # FAI 累计同比（fetch_china gdzctz 自年初累计自算）
    ("cycle_juglar", "cn", "fixed_asset_investment", "ind", ["china", "sectors", "corporate", "fai_yoy_cum"], None),
    # ---- [CIO 2026-11-12 月频停更清单·有源未灌 → 回灌] 朱格拉层美国投资两项 ----
    # BEA 实际值季度序列 → 同比% 后灌入（mode="yoy_q"，日期统一为 'YYYYQn'）
    ("cycle_juglar", "us", "equipment_investment", "fred", ["equip_inv"], None, "yoy_q"),
    ("cycle_juglar", "us", "capex_growth", "fred", ["nonres_inv"], None, "yoy_q"),
    # ---- [CIO 2026-11-12 ISM 方案落地] 美国制造业 PMI 真序列 ----
    # 层内原为 9 个半年采样桩点（2026-01=52.3 系编造，真值 52.6），故用 mode="replace" 整体重建。
    ("cycle_kitchin", "us", "ism_manufacturing", "file", ["ism_pmi.json", "history"], None, "replace"),
    # ---- [CIO 2026-11-12 ①P1] 联邦赤字/GDP 整段重建为年度口径 ----
    # 层内原为季度桩点（尾点 2025Q1=-5.76906）+ current=6.5 编造值（符号反），
    # 故用 mode="replace_annual" 整体重建为 FRED FYFSGDA188S 年度序列。
    ("constraint_debt_cycle", "us", "fiscal_deficit_gdp", "fred", ["deficit_gdp_fy"], None, "replace_annual"),
    # ---- [CIO 2026-11-12 ⑤ 方案 A] 高技术制造业投资：NBS 解读稿解析的月度累计同比整段重建 ----
    # 层内原为 3 个年度桩点（11.8/12.0/12.5，与官方年度值 22.2/9.9/7.0 无法调和），故整体替换。
    ("cycle_juglar", "cn", "high_tech_manufacturing_investment", "nbs", ["high_tech_mfg"], None, "replace_monthly"),
]


def _date_key(s):
    """'2026-10'/'2026-10-01'/'2026Q3' -> (2026,10,1) 可比较元组；失败 None。"""
    if not isinstance(s, str):
        return None
    # [CIO 2026-11-12] 季度标签兼容：'2026Q3' 等价于 '2026-07-01'
    _qm = _re.match(r'^(\d{4})\s*[Qq]([1-4])$', s.strip())
    if _qm:
        return (int(_qm.group(1)), (int(_qm.group(2)) - 1) * 3 + 1, 1)
    parts = s.strip()[:10].split("-")
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 1
        d = int(parts[2]) if len(parts) > 2 else 1
        return (y, m, d)
    except (ValueError, IndexError):
        return None


def _iso_date(s):
    """把季度标签 '2026Q3' 转成可被 JS `new Date()` 解析的该季末 ISO 日期
    '2026-09-30'；其余（'2026-10' / '2026-10-01'）原样返回。
    [CIO 2026-11-12] 回归修复：last_updated 写成 '2026Q3' 会让前端
    new Date('2026Q3')=Invalid Date → days=NaN → 误判「⚠️数据断更」红灯。"""
    if not isinstance(s, str):
        return s
    m = _re.match(r'^(\d{4})\s*[Qq]([1-4])$', s.strip())
    if not m:
        return s
    y, q = int(m.group(1)), int(m.group(2))
    return {1: f"{y}-03-31", 2: f"{y}-06-30", 3: f"{y}-09-30", 4: f"{y}-12-31"}[q]


def _month_end(s):
    """'2026-09' -> '2026-09-30'（前端 new Date 可解析且不虚标到构建日）。"""
    if not isinstance(s, str):
        return s
    m = _re.match(r"^(\d{4})-(\d{2})$", s.strip())
    if not m:
        return _iso_date(s)
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y}-{mo:02d}-{_calendar.monthrange(y, mo)[1]:02d}"


def _dig(obj, path):
    for k in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def refresh_indicators_from_upstream(layers):
    """从上游现值回灌层内月频指标；返回改动描述列表（供日志）。"""
    if not INDICATORS_JSON.exists():
        return []
    ind = load_json(INDICATORS_JSON)
    fred = load_json(FRED_RAW_JSON) if FRED_RAW_JSON.exists() else {}
    changes = []
    for _item in UPSTREAM_REFRESH:
        layer_key, side, ind_key, src_kind, path, div = _item[:6]
        mode = _item[6] if len(_item) > 6 else "raw"
        node = _dig(layers, [layer_key, side, "indicators", ind_key])
        if not isinstance(node, dict):
            continue
        if src_kind == "ind":
            src = _dig(ind, path)
            src_hist = [h for h in ((src or {}).get("history") or [])
                        if isinstance(h, dict) and h.get("value") is not None and h.get("date")]
        elif src_kind == "file":
            # [CIO 2026-11-12] data/ 下独立采集文件的 history 数组
            _f = DATA_DIR / path[0]
            _doc = load_json(_f) if _f.exists() else {}
            _node = _dig(_doc, path[1:]) if len(path) > 1 else _doc
            src_hist = [h for h in (_node or [])
                        if isinstance(h, dict) and h.get("value") is not None and h.get("date")]
        elif src_kind == "nbs":
            # [CIO 2026-11-12 ⑤] data/nbs_high_tech_raw.json 由 fetch_nbs_high_tech.py 生成
            _nd = load_json(NBS_RAW_JSON) if NBS_RAW_JSON.exists() else {}
            src_hist = [h for h in ((_nd.get("series") or {}).get(path[0]) or [])
                        if isinstance(h, dict) and h.get("value") is not None and h.get("date")]
        else:  # fred_raw
            src_hist = [h for h in ((fred.get("series") or {}).get(path[0]) or [])
                        if isinstance(h, dict) and h.get("value") is not None and h.get("date")]
        if div:
            src_hist = [{"date": h["date"], "value": round(h["value"] / div, 4)} for h in src_hist]
        # [CIO 2026-11-12] 原值季度序列 → 同比%（层内指标是增速口径，不能直接灌原值）
        if mode == "yoy_q":
            _by = {h["date"]: h["value"] for h in src_hist}
            _yoy = []
            for h in src_hist:
                _dk = _date_key(h["date"])
                if _dk is None:
                    continue
                _prev = f"{_dk[0] - 1}-{_dk[1]:02d}-{_dk[2]:02d}"
                _pv = _by.get(_prev)
                if not _pv:
                    continue
                _yoy.append({
                    "date": f"{_dk[0]}Q{(_dk[1] - 1) // 3 + 1}",
                    "value": round((h["value"] / _pv - 1) * 100, 2),
                })
            src_hist = _yoy
        if not src_hist:
            continue
        if mode == "replace_monthly":
            # [CIO 2026-11-12 ⑤ 方案 A] 月度累计同比整段重建。
            # 序列允许存在官方停发造成的断档（2024-12~2026-01），不插值、不补造，
            # 断档原因写入 data_gap_note 随数据交付。
            _keep = [h for h in src_hist if (_date_key(h["date"]) or (0,)) >= (2016, 1, 1)]
            if len(_keep) >= 24:
                _old = node.get("history") or []
                node["history_legacy_stub"] = _old
                node["history"] = [{"date": h["date"], "value": round(float(h["value"]), 2)}
                                   for h in _keep]
                _t = node["history"][-1]
                node["current"] = _t["value"]
                node["last_updated"] = _month_end(_t["date"])
                node["frequency"] = "monthly"
                node["unit"] = "%"
                node["source"] = ("国家统计局 固定资产投资统计司 投资数据解读稿"
                                  "（www.stats.gov.cn/sj/sjjd/，月度/季度累计同比）")
                node["caliber"] = ("1—N 月累计同比（%），全国固定资产投资（不含农户）口径下的"
                                   "高技术制造业投资。注意：与「高技术产业投资」不同口径"
                                   "（后者 = 高技术制造业 + 高技术服务业），2026 年 1—9 月两者差 0.9pp。")
                node["data_gap_note"] = (
                    "2024-12 ~ 2026-01 共 14 个月官方未发布该口径总量增速（仅发布高技术服务业"
                    "投资与分行业分项），属国家统计局发布口径变更，非采集缺失；2026-02 起恢复。"
                    "断档期不做插值。")
                # [CIO 2026-11-12 口径裁决] 断档期禁做趋势外推：
                # 层内打分只允许用 percentile（值分布，与时间连续性无关），
                # 禁止跨断档区间计算斜率/动量/环比，否则会把 14 个月空洞当成真实跳变。
                node["score_policy"] = {
                    "mode": "percentile_only",
                    "slope_across_gap": "forbidden",
                    "reason": "2024-12~2026-01 官方停发，跨该区间算斜率会把断档当跳变",
                }
                node["current_nulled"] = None
                _pb = percentile_bands(node["history"])
                if _pb:
                    node["percentile"] = _pb
                changes.append(
                    f"{layer_key}.{side}.{ind_key}: 月度累计同比重建 {len(_old)}->{len(_keep)} 点，"
                    f"-> {_t['value']}@{_t['date']}（{_pb.get('current_rank') if _pb else '-'}）")
                continue
        if mode == "replace_annual":
            # 年度口径整段重建：history 只保留 'YYYY' 标签，frequency=annual
            _keep = [h for h in src_hist if (_date_key(h["date"]) or (0,)) >= (2015, 1, 1)]
            if len(_keep) >= 5:
                _old = node.get("history") or []
                node["history_legacy_stub"] = _old
                node["history"] = [{"date": str(h["date"])[:4], "value": round(float(h["value"]), 2)}
                                   for h in _keep]
                _t = node["history"][-1]
                node["current"] = _t["value"]
                # 联邦财年 FY2025 = 2024-10-01~2025-09-30，故 last_updated 取财年末
                node["last_updated"] = f"{_t['date']}-09-30"
                node["frequency"] = "annual"
                node["unit"] = "%"
                node["source"] = "FRED FYFSGDA188S（Federal Surplus or Deficit as Percent of GDP）"
                node["caliber"] = ("联邦财政年度赤字/GDP（负值=赤字；正值=盈余）。"
                                   "财年 10/1–次年 9/30，FY2025 对应 2024-10-01~2025-09-30。"
                                   "原季度桩点（尾点 2025Q1）与编造值 current=6.5 已废弃。")
                node.pop("data_gap_note", None)
                node["current_nulled"] = None
                _pb = percentile_bands(node["history"])
                if _pb:
                    node["percentile"] = _pb
                changes.append(
                    f"{layer_key}.{side}.{ind_key}: 年度口径重建 {len(_old)}->{len(_keep)} 点，"
                    f"-> {_t['value']}@{_t['date']}（{_pb.get('current_rank') if _pb else '-'}）")
                continue
        if mode == "replace":
            _keep = [h for h in src_hist if (_date_key(h["date"]) or (0,)) >= (2016, 1, 1)]
            if len(_keep) >= 24:
                _old = node.get("history") or []
                node["history_legacy_stub"] = _old
                node["history"] = [{"date": h["date"], "value": h["value"]} for h in _keep]
                _t = node["history"][-1]
                node["current"] = _t["value"]
                node["last_updated"] = _iso_date(_t["date"])
                node["source"] = "ISM® Manufacturing PMI®（PR Newswire 分发官方稿）"
                node["frequency"] = "monthly"
                node.pop("data_gap_note", None)
                _pb = percentile_bands(node["history"])
                if _pb:
                    node["percentile"] = _pb
                changes.append(
                    f"{layer_key}.{side}.{ind_key}: 真序列重建 {len(_old)}->{len(_keep)} 点，"
                    f"-> {_t['value']}@{_t['date']}（{_pb.get('current_rank') if _pb else '-'}）")
                continue
        src_tail = src_hist[-1]
        src_tail_key = _date_key(src_tail["date"])
        if src_tail_key is None:
            continue
        hist = node.setdefault("history", [])
        cur_tail_key = _date_key(hist[-1].get("date")) if hist else None
        # 值口径修正场景：上游尾期==层内尾期但值不同（如 capacity_utilization 错值 79.5）
        same_date_diff_val = (
            cur_tail_key is not None and cur_tail_key == src_tail_key
            and hist and abs((hist[-1].get("value") or 0) - src_tail["value"]) > 1e-9)
        if cur_tail_key is not None and cur_tail_key > src_tail_key:
            continue  # 层内新于上游，不回灌
        appended = 0
        for h in src_hist:
            hk = _date_key(h.get("date"))
            if hk is None or (cur_tail_key is not None and hk <= cur_tail_key):
                continue
            hist.append({"date": h["date"], "value": h["value"]})
            appended += 1
        if same_date_diff_val:
            hist[-1]["value"] = src_tail["value"]
        node["current"] = src_tail["value"]
        node["last_updated"] = _iso_date(src_tail["date"])
        node.pop("data_gap_note", None)  # 真值回灌后缺口闭合
        # [CIO 2026-11-12] 回灌后必须重算分位带，否则 current 越出 p75 仍标 p50（自相矛盾）
        _pb = percentile_bands(hist)
        if _pb:
            node["percentile"] = _pb
        if appended or same_date_diff_val:
            changes.append(
                f"{layer_key}.{side}.{ind_key}: -> {src_tail['value']}@{src_tail['date']} (+{appended} points, {_pb.get('current_rank') if _pb else '-'})")
    # Perez 高收益债利差（turning_point_signals 为列表结构，特判）
    perez = layers.get("narrative_perez") or {}
    hy = (fred.get("series") or {}).get("bamlh0a0hym2") or []
    if hy:
        tail = hy[-1]
        tk = _date_key(tail.get("date"))
        for sig in perez.get("turning_point_signals") or []:
            if sig.get("metric") != "HY spread":
                continue
            sk = _date_key(sig.get("last_updated", ""))
            if tk and (sk is None or sk < tk):
                old = sig.get("current_value")
                v = tail["value"]
                sig["current_value"] = v
                sig["last_updated"] = tail["date"]
                sig["status"] = "red" if v >= 6 else ("yellow" if v >= 4 else "green")
                changes.append(f"narrative_perez.HY spread: {old} -> {v}@{tail['date']}")
    return changes


def _fix_core_cpi_index(layers):
    """修复 Merrill us.core_cpi 混入 CPI 指数。
    
    根因：us.history 混入 CPI 指数值（309-336）与增速值（2.8-6.4%）。
    修复：从指数序列计算 YoY 增速，无法计算的条目移除（不伪造数据）。
    """
    merrill = layers.get("cycle_merrill_3d", {})
    us_data = merrill.get("us", {})
    indicators = us_data.get("indicators", {})
    core_cpi = indicators.get("core_cpi", {})
    if not core_cpi:
        return layers, False
    
    hist = core_cpi.get("history", [])
    if not hist:
        return layers, False
    
    # 分离增速值和指数值
    growth_items = [item for item in hist if item.get("value", 0) < 10]
    index_items = [item for item in hist if item.get("value", 0) >= 10]
    
    if not index_items:
        return layers, False  # 无指数值，无需修复
    
    # 构建指数日期映射
    index_by_date = {item["date"]: item["value"] for item in index_items}
    
    # 从指数计算 YoY，只保留可计算的
    converted = []
    removed = 0
    for item in index_items:
        dt = item["date"]
        year, month = dt.split("-")
        target_year = str(int(year) - 1)
        target_date = f"{target_year}-{month}"
        
        if target_date in index_by_date:
            prev_val = index_by_date[target_date]
            curr_val = index_by_date[dt]
            yoy = round((curr_val - prev_val) / prev_val * 100, 2)
            converted.append({"date": dt, "value": yoy})
        else:
            removed += 1
    
    # 合并：保留原增速 + 转换后的增速，按日期排序
    new_hist = growth_items + converted
    new_hist.sort(key=lambda x: x["date"])
    core_cpi["history"] = new_hist
    
    if removed > 0:
        print(f"  ⚠ core_cpi: {removed} 条指数无法计算YoY，已移除（不伪造数据）")
    
    return layers, True


def _fix_high_rate_tracker_history(v4_data):
    """把 constraint_rate_regime 中已有的历史数据映射到 high_rate_tracker。

    根因：cross_analysis.high_rate_tracker 的 9 个指标全部无 history，
    其中 4 个在 constraint_rate_regime 中已有 197-203 条完整数据，
    但 v3 与旧 v4 均未建立映射，前端趋势图因此为空。

    映射（2026-09-24 已逐字段验证）：
      forward_looking.breakeven_inflation → 30Y盈亏平衡通胀
      forward_looking.term_premium        → ACM期限溢价
      forward_looking.real_rate_forward   → 5Y-5Y远期实际利率
      market_based.credit_spread_ig       → 高收益债利差
    """
    crr = v4_data.get("cycle_layers", {}).get("constraint_rate_regime", {})
    hrt_indicators = (
        v4_data.get("cross_analysis", {})
        .get("high_rate_tracker", {})
        .get("indicators", [])
    )
    if not isinstance(hrt_indicators, list):
        return False

    mapping = {
        "30Y盈亏平衡通胀": ("forward_looking", "breakeven_inflation"),
        "ACM期限溢价": ("forward_looking", "term_premium"),
        "5Y-5Y远期实际利率": ("forward_looking", "real_rate_forward"),
        "高收益债利差": ("market_based", "credit_spread_ig"),
    }

    fixed = False
    for ind in hrt_indicators:
        if not isinstance(ind, dict):
            continue
        name = ind.get("name", "")
        if name not in mapping:
            continue
        # 仅在确实缺 history 时复制，保证幂等
        if ind.get("history"):
            continue
        group_key, ind_key = mapping[name]
        source = (
            crr.get(group_key, {})
            .get("indicators", {})
            .get(ind_key, {})
        )
        history = source.get("history", [])
        if history:
            ind["history"] = history
            fixed = True
            print(f"  [fix] {name}: 复制 {len(history)} 条 history <- {group_key}.{ind_key}")

    return fixed



# ============================================================
# 桩值检测与标记（CIO 2026-11-12 准则 3 红线 · ④全站桩值扫描）
# ============================================================
# 病灶定义（三形态）：
#   A-值脱节   current 与 history 尾点值不一致 → 数据自相矛盾（最硬）
#   B-日期脱节 last_updated 距 history 尾点日期超出该频率容差 → 虚标发布日
#   C-时间戳虚标 last_updated 被写成构建当日，掩盖数据陈旧（B 的子集，单独标注）
# 处置分级（人工核实后登记于此，机器不自动改数）：
#   stub        真桩值：current 是静态占位，禁止当有效值展示（前端灰灯）
#   lag_normal  正常滞后：上游该期确实未发布，值本身有效
#   meta_error  元数据标错：值有效，frequency / last_updated 字段错误
#   unverified  未核实：待 CIO 定级
_STUB_TOL = {"daily": 0.5, "weekly": 1.0, "monthly": 2.2, "quarterly": 5.5, "annual": 20.0}

STUB_REGISTRY = {
    "cycle_kitchin/cn/indicators/cn_pmi_finished_goods": ("stub",
        "静态桩值：history 尾点 2026-01=46.8，current 却标 2026-08-31；从未灌过 2026 年数据，非「停在 8 月」而是从未更新。NBS 分项无源，CIO 已批 A 路补采（P1，截止 11-15）。"),
    "cycle_kitchin/cn/indicators/cn_inventory_cycle": ("stub",
        "静态桩值：history 尾点 2026-01=3.2，current 标 2026-08-15；同 cn_pmi_finished_goods 病灶。"),
    "constraint_debt_cycle/us/indicators/fiscal_deficit_gdp": ("stub",
        "current=6.5 与 history 尾点 2025Q1=-5.76906 符号相反；FRED FYFSGDA188S（联邦赤字/GDP）2025 实为 -5.7527，current=6.5 属编造值，必须摘除或改正。"),
    "constraint_debt_cycle/us/indicators/interest_to_revenue": ("meta_error",
        "值有效：tail 2026Q2 属正常滞后（FRED W006RC1Q027SBEA 收入分项尚未发 Q3，A091RC1Q027SBEA 已到 Q3 但分子分母须同季）。last_updated 被虚标为构建日，需改回 2026Q2 对应日期。"),
    "constraint_rate_regime/structural/indicators/income_inequality/us": ("meta_error",
        "值有效：基尼系数 2 年一更，0.418@2024 为最新可得。last_updated 虚标为构建日。"),
    "constraint_rate_regime/structural/indicators/income_inequality/cn": ("meta_error",
        "值有效：0.36@2022 为最新可得（统计局基尼系数发布不规律）。last_updated 虚标为构建日。"),
    "cycle_juglar/cn/indicators/industrial_capacity_utilization": ("unverified",
        "tail 2026Q2=73.0，需核 NBS 2026Q3 产能利用率是否已发布；last_updated 虚标为构建日。待 CIO 定级。"),
    "constraint_rate_regime/forward_looking/indicators/breakeven_inflation": ("meta_error",
        "值有效：T30YIEM 月度序列，2.27@2026-10-01 为最新。frequency 误标 daily（应 monthly）。"),
    "constraint_debt_cycle/cn/indicators/structural_reform_progress": ("meta_error",
        "值有效：人工综合评估 0.6@2026。frequency 误标 monthly（应 annual），last_updated 2026-06-01 早于年度口径。"),
}

FREQ_FIXES = {
    "cycle_juglar/us/indicators/equipment_investment": "quarterly",
    "constraint_debt_cycle/cn/indicators/structural_reform_progress": "annual",
    "constraint_rate_regime/forward_looking/indicators/breakeven_inflation": "monthly",
}


def _gap_months(a_key, b_key):
    if a_key is None or b_key is None:
        return None
    return round(((a_key[0] - b_key[0]) * 12 + (a_key[1] - b_key[1])) + (a_key[2] - b_key[2]) / 30.0, 1)



def _assert_no_slope_across_gap(layers):
    """[CIO 2026-11-12] 含 data_gap_note 的指标不得出现斜率/动量字段。"""
    bad = []

    def _scan(node, path):
        if not isinstance(node, dict):
            return
        if node.get("data_gap_note"):
            for k in ("slope", "trend_slope", "momentum", "mom_3m", "delta_qoq"):
                if k in node:
                    bad.append(f"{path}.{k}")
        for k, v in node.items():
            if isinstance(v, dict):
                _scan(v, f"{path}.{k}")

    _scan(layers, "cycle_layers")
    return bad

def annotate_data_quality(layers, built_at):
    """遍历层内全部指标节点，打 data_quality 标记，返回全站桩值扫描清单。

    只标注、不改值：改值必须走 UPSTREAM_REFRESH 真源回灌或人工热修。
    """
    flags = []

    def _scan(node, path):
        hist = node.get("history")
        if isinstance(hist, list) and hist and isinstance(hist[0], dict):
            tail = next((x for x in reversed(hist)
                         if isinstance(x, dict) and x.get("date") and x.get("value") is not None), None)
            if tail is None:
                return
            cur = node.get("current")
            lu = node.get("last_updated")
            freq = node.get("frequency") or "monthly"
            tail_key, lu_key = _date_key(tail["date"]), _date_key(lu)
            gap = _gap_months(lu_key, tail_key)
            tol = _STUB_TOL.get(freq, 2.2)

            # 缺 last_updated：前端渲染为「无日期」，读者无从判断新旧（准则3变种）
            if not lu:
                node["data_quality"] = {
                    "status": "unverified", "kind": "missing_date",
                    "tail_date": tail["date"], "tail_value": tail["value"],
                    "current": cur, "last_updated": None, "gap_months": None,
                    "timestamp_faked": False,
                    "note": "缺 last_updated 字段，前端显示「无日期」，读者无法判断数据新旧；需补发布日或标记口径。",
                }
                flags.append({
                    "path": path, "name": node.get("name") or path.rsplit("/", 1)[-1],
                    "current": cur, "unit": node.get("unit"),
                    "tail_date": tail["date"], "tail_value": tail["value"],
                    "last_updated": None, "frequency": freq, "gap_months": None,
                    "kind": "missing_date", "timestamp_faked": False,
                    "status": "unverified", "collectable": False,
                    "note": node["data_quality"]["note"],
                })
                return

            value_mis = (isinstance(cur, (int, float)) and isinstance(tail["value"], (int, float))
                         and abs(cur - tail["value"]) > 1e-9)
            date_mis = gap is not None and gap > tol
            ts_faked = bool(lu) and isinstance(lu, str) and lu[:10] >= built_at[:10]

            if not (value_mis or date_mis):
                # 已对齐：清掉上一轮遗留的 data_quality（否则重建后仍挂旧灰灯）
                node.pop("data_quality", None)
                return
            kind = "value_mismatch" if value_mis else "date_mismatch"
            reg = STUB_REGISTRY.get(path)
            status = reg[0] if reg else "unverified"
            note = reg[1] if reg else "自动扫描命中，尚未人工核实"
            dq = {
                "status": status,
                "kind": kind,
                "tail_date": tail["date"],
                "tail_value": tail["value"],
                "current": cur,
                "last_updated": lu,
                "gap_months": gap,
                "timestamp_faked": ts_faked,
                "note": note,
            }
            node["data_quality"] = dq
            flags.append({
                "path": path,
                "name": node.get("name") or path.rsplit("/", 1)[-1],
                "current": cur,
                "unit": node.get("unit"),
                "tail_date": tail["date"],
                "tail_value": tail["value"],
                "last_updated": lu,
                "frequency": freq,
                "gap_months": gap,
                "kind": kind,
                "timestamp_faked": ts_faked,
                "status": status,
                "collectable": status == "stub" and "无源" not in note,
                "note": note,
            })
            return
        for k, v in node.items():
            if isinstance(v, dict):
                _scan(v, f"{path}/{k}" if path else k)

    for layer_key, layer in (layers or {}).items():
        if not isinstance(layer, dict):
            continue
        _scan(layer, layer_key)

    return flags


# [CIO 2026-11-12 ①P0] 假值置空清单：不摘卡，但 current 置 None（前端 fmtNum 显示「--」）
# 触发条件 = annotate 判出 status=stub 且 kind=value_mismatch；真源回灌成功后自动不再命中。
NULL_OUT_ON_VALUE_MISMATCH = {
    "constraint_debt_cycle/us/indicators/fiscal_deficit_gdp":
        "current=6.5 与自身 history 尾点 -5.76906 符号相反，FRED FYFSGDA188S 2025=-5.7527，属编造值。"
        "CIO 令不摘卡、置空灰灯，待年度口径整段重建（真值回灌后本项自动失效）。",
}


def apply_null_out(layers, flags, built_at):
    """对仍处于 value_mismatch 的桩值把 current 置空。

    [CIO 2026-11-12 ①P0] 「假值一天不能留」：宁可显示「--」+ 灰灯，也不显示编造值。
    回灌成功后 current 为真值，annotate 不再判 value_mismatch，本函数自然不命中。
    """
    hit = 0
    for f in flags:
        if f.get("status") != "stub" or f.get("kind") != "value_mismatch":
            continue
        reason = NULL_OUT_ON_VALUE_MISMATCH.get(f["path"])
        if not reason:
            continue
        node = _dig(layers, f["path"].split("/"))
        if isinstance(node, dict):
            node["current_nulled"] = {
                "reason": reason,
                "nulled_at": built_at,
                "ruling": "CIO 2026-11-12 ①P0：不摘卡，置空灰灯",
            }
            node["current"] = None
            hit += 1
    return hit


def _fix_frequency_labels(layers):
    """frequency 标签修正（与 STUB_REGISTRY 配套，值不动）。"""
    fixed = 0
    for path, freq in FREQ_FIXES.items():
        node = _dig(layers, path.split("/"))
        if isinstance(node, dict) and node.get("frequency") != freq:
            node["frequency"] = freq
            fixed += 1
    return fixed


def build():
    if not V3_FILE.exists():
        print(f"✗ 缺少上游文件 {V3_FILE}", file=sys.stderr)
        sys.exit(1)

    v3 = load_json(V3_FILE)
    prev_v4 = load_json(V4_FILE) if V4_FILE.exists() else {}

    consensus = v3.get("cycle_consensus")
    if not isinstance(consensus, dict):
        print("✗ v3 cycle_consensus 结构异常，拒绝生成", file=sys.stderr)
        sys.exit(1)

    dimensions = consensus.get("dimension_scores", [])
    jug_idx = _dim_index(dimensions, DIM_JUGLAR)
    kit_idx = _dim_index(dimensions, DIM_KITCHIN)
    if jug_idx < 0 or kit_idx < 0:
        print(
            f"✗ dimension_scores 缺少 {DIM_JUGLAR}/{DIM_KITCHIN} 条目，拒绝生成",
            file=sys.stderr,
        )
        sys.exit(1)

    overall_score = consensus.get("overall_score")
    if not isinstance(overall_score, (int, float)):
        print("✗ cycle_consensus.overall_score 缺失或非数值，拒绝生成", file=sys.stderr)
        sys.exit(1)

    # ---- 1. synthesis 文本评分与 overall_score 对齐 ----
    v3, aligned, old_score = align_synthesis_score(v3, overall_score)
    if aligned:
        print(f"⚠ synthesis 文本评分 {old_score} → {overall_score}（以 overall_score 为准）")

    # ---- 2. 以 v3 为底座组装 v4 ----
    # 增强板块权威内容不在 v3；既从上一版 v4（或人工热修版）继承，也从
    # 上一次的*自身输出*继承，保证流水线幂等、热修内容不丢。
    out = dict(v3)
    for key in V4_PRESERVE_KEYS:
        if key in prev_v4:
            out[key] = prev_v4[key]

    # ---- 2b. cycle_layers 改名 v3→v4（权威内容来自 v3，v4 子结构补回） ----
    out["cycle_layers"] = build_cycle_layers(
        v3.get("cycle_layers", {}),
        prev_v4.get("cycle_layers", {}),
    )

    # ---- 2c. 数据修复：v3 静态数据质量问题 ----
    out["cycle_layers"], tfp_fixed = _fix_tfp_history(out["cycle_layers"])
    if tfp_fixed:
        print("⚠ TFP history 修复: US 指数→增速转换")
    out["cycle_layers"], pg_fixed = _fix_productivity_descriptions(out["cycle_layers"])
    if pg_fixed:
        print("⚠ productivity_growth 修复: 添加 us/cn per-region description")
    out["cycle_layers"], no_fixed = _fix_new_orders_units(out["cycle_layers"])
    if no_fixed:
        print("⚠ new_orders 修复: 百万美元→十亿美元")
    out["cycle_layers"], cs_fixed = _fix_credit_spread_units(out["cycle_layers"])
    if cs_fixed:
        print("⚠ credit_spread_ig 修复: 0.1bp→%")
    out["cycle_layers"], cpi_fixed = _fix_core_cpi_index(out["cycle_layers"])
    if cpi_fixed:
        print("⚠ core_cpi 修复: CPI指数→YoY增速")

    # ---- 2e. 层内月频指标从上游现值回灌（CIO P1-1 复验打回，2026-11-11） ----
    for _c in refresh_indicators_from_upstream(out["cycle_layers"]):
        print(f"  [回灌] {_c}")

    # ---- 2d. C1 债务约束 current_values 以 v3 L0 主源刷新（#14 CIO 裁决） ----
    # 旧行为：constraint_degradation 由 V4_PRESERVE_KEYS 从上一版 v4 原样继承，
    # 其 current_values.interest_to_revenue 是 2021 年陈旧快照 22，与 v3
    # indicators.interest_to_revenue.current(33.55) 矛盾。现每次生成都从 v3 主源重算。
    c1_fixed = sync_c1_current_values(out, v3)

    # ---- 3. cycle_consensus：新 schema + 旧契约兼容分键 ----
    us = build_region_contract("us", dimensions, jug_idx, kit_idx, prev_v4)
    cn = build_region_contract("cn", dimensions, jug_idx, kit_idx, prev_v4)
    out_consensus = dict(consensus)
    out_consensus["united_states"] = us
    out_consensus["china"] = cn
    out_consensus["formula"] = FORMULA_TEXT
    out["cycle_consensus"] = out_consensus

    # ---- 3b. 共识度单一真值(P0-3 #9) ----
    # 契约：全球周期共识度唯一主源 = cycle_consensus.overall_score；
    # cross_analysis.consensus.score 仅作别名，禁止两套口径并存(62 vs 68)。
    ca = out.get("cross_analysis")
    if isinstance(ca, dict) and isinstance(ca.get("consensus"), dict):
        alias = ca["consensus"]
        if alias.get("score") != overall_score:
            print(f"⚠ cross_analysis.consensus.score {alias.get('score')} → {overall_score}（别名对齐主源）")
        alias["score"] = overall_score
        alias["alias_of"] = "cycle_consensus.overall_score"

    # ---- 4. asset_ranking 重算（不再保留旧版） ----
    if "transmission_table" in out:
        out["asset_ranking"] = recompute_asset_ranking(out, us, cn)
    else:
        print("⚠ 无 transmission_table，本次不输出 asset_ranking")

    # ---- 5. _meta 标记生成方式 ----
    meta = dict(out.get("_meta", {}))
    meta["v4_builder"] = "scripts/build_cycle_position_v4.py"
    meta["v4_built_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    # P0-1（2026-11-01 CIO）：新鲜度按各层 last_updated 重算（min 口径），
    # 废除上游 +5 封顶 100 的假新鲜度；补「数据截止日」与「评估逾期」标记，
    # 前端按此双时间戳（assessment_date / data_cutoff_date）展示并显著标注逾期。
    _score, _cutoff, _detail = compute_freshness(out.get("cycle_layers", {}))
    meta["data_freshness_score"] = _score
    meta["data_cutoff_date"] = _cutoff
    meta["freshness_detail"] = _detail
    meta["assessment_overdue"] = is_assessment_overdue(meta.get("next_assessment_date"))
    out["_meta"] = meta

    # ---- 5b. high_rate_tracker history 映射（constraint_rate_regime → 交叉分析） ----
    if _fix_high_rate_tracker_history(out):
        print("⚠ high_rate_tracker 修复: 已从 constraint_rate_regime 映射 history")

    # ---- 5c. 桩值检测与标记（CIO 2026-11-12 准则3红线·④全站桩值扫描） ----
    # [CIO 2026-11-12 ③P2] key_ratio 人工汇编·年频：补 last_updated 并注明口径，不冒充日更
    _kr = _dig(out["cycle_layers"], ["narrative_perez", "key_ratio"])
    if isinstance(_kr, dict):
        _kr["last_updated"] = "2026"
        _kr["frequency"] = "annual"
        _kr["caliber"] = "人工汇编·年频（全球 VC/PE 年度融资额 ÷ 全球制造业/基建年度资本形成额）；非自动采集，更新随年报发布"
        _kr["data_quality"] = {
            "status": "unverified", "kind": "manual_compiled",
            "tail_date": "2026", "tail_value": _kr.get("current"),
            "last_updated": "2026", "gap_months": None, "timestamp_faked": False,
            "note": "人工汇编年度值，无自动数据源；last_updated 标年度而非具体日，避免冒充日更。",
        }
    _nfix = _fix_frequency_labels(out["cycle_layers"])
    if _nfix:
        print(f"⚠ frequency 标签修正 {_nfix} 项")
    _flags = annotate_data_quality(out["cycle_layers"], meta["v4_built_at"])
    _nulled = apply_null_out(out["cycle_layers"], _flags, meta["v4_built_at"])
    if _nulled:
        print(f"⚪ 假值置空 {_nulled} 项（current -> null，前端显示「--」）")
    _by_status = {}
    for _f in _flags:
        _by_status[_f["status"]] = _by_status.get(_f["status"], 0) + 1
    save_json(STUB_FLAGS_FILE, {
        "generated_at": meta["v4_built_at"],
        "builder": "scripts/build_cycle_position_v4.py:annotate_data_quality",
        "caliber": "history 尾点日期/值与 last_updated/current 脱节的层内指标全量清单；status: stub=真桩值(前端灰灯) / lag_normal=正常滞后 / meta_error=元数据标错 / unverified=待定级",
        "summary": {
            "flagged": len(_flags),
            "by_status": _by_status,
            "stub_count": _by_status.get("stub", 0),
        },
        "flags": sorted(_flags, key=lambda x: (x["status"] != "stub", -(x["gap_months"] or 0))),
    })
    print(f"⚠ 桩值扫描：{len(_flags)} 项脱节（" +
          ", ".join(f"{k}={v}" for k, v in sorted(_by_status.items())) + "）→ data/v4_stub_flags.json")

    _slope_bad = _assert_no_slope_across_gap(out["cycle_layers"])
    if _slope_bad:
        print("❌ 断档指标出现斜率字段（CIO 2026-11-12 裁决禁止）：" + ", ".join(_slope_bad))
        return 1

    save_json(V4_FILE, out)

    print("✅ data/cycle_position_v4.json 生成完成")
    print(
        f"  US: P1={us['p1_score']} P2={us['p2_score']} P3={us['p3_score']} "
        f"raw={us['raw_score']} consensus={us['consensus_score']} signal={us['signal']}"
    )
    print(
        f"  CN: P1={cn['p1_score']} P2={cn['p2_score']} P3={cn['p3_score']} "
        f"raw={cn['raw_score']} consensus={cn['consensus_score']} signal={cn['signal']}"
    )
    if "asset_ranking" in out:
        rk = out["asset_ranking"]["ranking"]
        print("  ranking: " + " > ".join(f"{r['rank']}.{r['asset']}({r['signal']})" for r in rk))


if __name__ == "__main__":
    build()
