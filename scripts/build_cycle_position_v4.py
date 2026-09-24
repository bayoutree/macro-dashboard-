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
import re
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
V3_FILE = DATA_DIR / "cycle_position_v3.json"
V4_FILE = DATA_DIR / "cycle_position_v4.json"

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

    # ---- 3. cycle_consensus：新 schema + 旧契约兼容分键 ----
    us = build_region_contract("us", dimensions, jug_idx, kit_idx, prev_v4)
    cn = build_region_contract("cn", dimensions, jug_idx, kit_idx, prev_v4)
    out_consensus = dict(consensus)
    out_consensus["united_states"] = us
    out_consensus["china"] = cn
    out_consensus["formula"] = FORMULA_TEXT
    out["cycle_consensus"] = out_consensus

    # ---- 4. asset_ranking 重算（不再保留旧版） ----
    if "transmission_table" in out:
        out["asset_ranking"] = recompute_asset_ranking(out, us, cn)
    else:
        print("⚠ 无 transmission_table，本次不输出 asset_ranking")

    # ---- 5. _meta 标记生成方式 ----
    meta = dict(out.get("_meta", {}))
    meta["v4_builder"] = "scripts/build_cycle_position_v4.py"
    meta["v4_built_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    out["_meta"] = meta

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
