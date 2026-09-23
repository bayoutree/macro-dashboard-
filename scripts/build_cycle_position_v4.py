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

本脚本职责
----------
输入: data/cycle_position_v3.json   —— 每日流水线唯一权威产物（新 schema）
输出: data/cycle_position_v4.json   —— 前端契约文件（新 schema + 旧契约兼容层）

1. 以 v3 为底座，保留 v4-only 增强板块（transmission_table、
   constraint_degradation、data_quality、asset_ranking、contradiction_status）；
2. 重建 cycle_consensus：保留新 schema 全部字段，并补回 united_states /
   china 分键（p1/p2/p3 兼容字段），映射规则见下方常量区注释；
3. 强制口径一致：synthesis 文本中的「共识度评分 NN/100」与
   cycle_consensus.overall_score 不一致时，以 overall_score 为准改写文本。

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

# v4-only 增强板块：v3 中不存在、需要从上一版 v4 保留的顶层 key
V4_PRESERVE_KEYS = [
    "transmission_table",
    "constraint_degradation",
    "data_quality",
    "asset_ranking",
    "contradiction_status",
]

# ------------------------------------------------------------------
# 旧契约公式（前端 cycle_v4_patch.js / cycle_v3.js 的既定口径，不可改）
#   raw_score       = P1*0.3 + P2*0.4 + P3*0.3
#   consensus_score = (raw_score + 2) / 4 * 100
# P1=朱格拉设备周期, P2=基钦库存周期, P3=美林时钟；分值域 -2..2
# ------------------------------------------------------------------
FORMULA_TEXT = (
    "raw_score = P1×0.3 + P2×0.4 + P3×0.3, "
    "consensus_score = (raw_score + 2) / 4 × 100"
)

# ------------------------------------------------------------------
# P1/P2 取值规则：从 dimension_scores 映射
#   朱格拉: us_score / cn_score   （事故基线 us=1, cn=1）
#   基  钦: us_score / cn_score   （事故基线 us=2, cn=1）
# dimension_scores 中对应条目或分国分值缺失时记 0（中性），不猜测。
#
# P3 美林时钟取值规则（dimension_scores 中美林只有定性描述、无数值，
# 必须在生成器侧定死，禁止人工判断）：
#   读取 v4 cycle_layers.cycle_merrill_3d.{us,cn}.signal_weight。
#   该字段由美林三维矩阵的象限位置产出，取值 -2..2，与 P1/P2 同尺度，
#   可直接作为 P3；缺失时记 0（中性）并在 label 中体现。
# ------------------------------------------------------------------
SCORE_MISSING = 0


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


def _country_score(entry, country_key):
    """分国分值优先；共享维度（康波/佩雷斯）只有 score 时回落 score。"""
    if entry is None:
        return SCORE_MISSING
    v = entry.get(f"{country_key}_score")
    if v is None:
        v = entry.get("score", SCORE_MISSING)
    return v if v is not None else SCORE_MISSING


def _merrill_p3(prev_v4, country_key):
    """
    P3 规则：美林层 signal_weight（-2..2）。
    country_key: 'us' / 'cn'
    """
    merrill = (
        prev_v4.get("cycle_layers", {})
        .get("cycle_merrill_3d", {})
        .get(country_key, {})
    )
    w = merrill.get("signal_weight")
    if w is None:
        return SCORE_MISSING, merrill.get("current_phase")
    return w, merrill.get("current_phase")


def _p_label(prefix, phase):
    if not phase:
        return f"{prefix}-未获取（中性计分）"
    return f"{prefix}-{phase}"


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

    p1 = _country_score(jug_entry, country_key)
    p2 = _country_score(kit_entry, country_key)
    p3, merrill_phase = _merrill_p3(prev_v4, country_key)

    jug_phase = jug_entry.get("assessment") if jug_entry is not None else None
    kit_phase = kit_entry.get("assessment") if kit_entry is not None else None

    raw = round(p1 * 0.3 + p2 * 0.4 + p3 * 0.3, 2)
    consensus = round((raw + 2) / 4 * 100, 1)

    return {
        "p1_score": p1,
        "p2_score": p2,
        "p3_score": p3,
        "p1_label": _p_label("朱格拉设备周期", jug_phase),
        "p2_label": _p_label("基钦库存周期", kit_phase),
        "p3_label": _p_label("美林时钟", merrill_phase),
        "raw_score": raw,
        "consensus_score": consensus,
        "signal": _signal(raw),
    }


def align_synthesis_score(v3_data, overall_score):
    """
    口径一致性强制修复：
    synthesis.overall_assessment 中「共识度评分 NN/100」必须等于
    cycle_consensus.overall_score；不一致则以 overall_score 为准就地改写。
    返回 (新v3_data, 是否发生改写, 旧值)。
    """
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
    out = dict(v3)

    # 保留 v4-only 增强板块（原样不动）
    for key in V4_PRESERVE_KEYS:
        if key in prev_v4:
            out[key] = prev_v4[key]

    # ---- 3. cycle_consensus：新 schema + 旧契约兼容分键 ----
    out_consensus = dict(consensus)
    out_consensus["united_states"] = build_region_contract(
        "us", dimensions, jug_idx, kit_idx, prev_v4
    )
    out_consensus["china"] = build_region_contract(
        "cn", dimensions, jug_idx, kit_idx, prev_v4
    )
    out_consensus["formula"] = FORMULA_TEXT
    out["cycle_consensus"] = out_consensus

    # ---- 4. _meta 标记生成方式，便于线上排障 ----
    meta = dict(out.get("_meta", {}))
    meta["v4_builder"] = "scripts/build_cycle_position_v4.py"
    meta["v4_built_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    out["_meta"] = meta

    save_json(V4_FILE, out)

    us = out_consensus["united_states"]
    cn = out_consensus["china"]
    print("✅ data/cycle_position_v4.json 生成完成")
    print(
        f"  US: P1={us['p1_score']} P2={us['p2_score']} P3={us['p3_score']} "
        f"raw={us['raw_score']} consensus={us['consensus_score']} signal={us['signal']}"
    )
    print(
        f"  CN: P1={cn['p1_score']} P2={cn['p2_score']} P3={cn['p3_score']} "
        f"raw={cn['raw_score']} consensus={cn['consensus_score']} signal={cn['signal']}"
    )
    print(f"  overall_score={overall_score}; 增强板块保留: "
          f"{[k for k in V4_PRESERVE_KEYS if k in prev_v4]}")


if __name__ == "__main__":
    build()
