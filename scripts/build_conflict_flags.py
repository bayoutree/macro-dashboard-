#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_conflict_flags.py — P0-⑤ 三看板冲突自动标注（数据契约生成器）。

依据：
- 投资分析《P0-5_三看板冲突判定规则_v1_20261101》（方向标准化/分层时钟/主从/三档冲突）
- CIO《P0-5_冲突标注_前端展示规格_CIO_v1_20261101》（conflict_flags.json 契约 + 五条验收用例）

契约约束：
- 只读三个源头 JSON（indicators.json / cycle_position_v4.json / timing_scores.json
  + timing_right_scores.json 的信号灯），不回写、不改写原看板任何字段
- 任一源头缺失/解析失败 → 该 board effective=false + status="source_missing"，
  conflict.level 按剩余板照常计算，禁止整块不产出（降级不断更）
- v4_clock.overdue 复用 P0-① 的 _meta.assessment_overdue 单源，不另算一份
- v4_clock.trigger_hit v1 保守默认 false（触发器=breached 计数「变化」，无历史基线
  无法判定变化），但记录 breached 计数快照供未来 diff 启用
"""
import json
import os
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INDICATORS = os.path.join(ROOT, "macro-econ-dashboard", "data", "indicators.json")
V4 = os.path.join(DATA, "cycle_position_v4.json")
TIMING = os.path.join(DATA, "timing_scores.json")
TIMING_RIGHT = os.path.join(DATA, "timing_right_scores.json")
OUT = os.path.join(DATA, "conflict_flags.json")

CST = timezone(timedelta(hours=8))
BOARD_NAMES = {"macro_cross": "宏观截面", "cycle_v4": "全球周期v4", "a_timing": "A股择时"}


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _board(board, direction, raw_value, as_of, effective, status):
    return {"board": board, "direction": direction, "raw_value": raw_value,
            "as_of": as_of, "effective": effective, "status": status}


def norm_macro_cross(ind):
    """宏观截面（五层 US）：asset_base 首位定方向；phase 含酝酿/筑底/观察降半档(归0)。"""
    if not ind:
        return _board("macro_cross", None, None, None, False, "source_missing")
    ec = ((ind.get("us") or {}).get("economic_cycle")) or {}
    phase = ec.get("phase") or ""
    asset_base = ec.get("asset_base") or ""
    as_of = (ind.get("update_time") or "")[:10] or None
    first = asset_base.split(">")[0].strip() if asset_base else ""
    if first == "股票":
        d = 1
    elif first == "债券":
        d = 0
    elif first in ("现金", "黄金"):
        d = -1
    else:
        return _board("macro_cross", None, f"{phase}/{asset_base}", as_of,
                      False, "source_missing")
    if any(k in phase for k in ("酝酿", "筑底", "观察")):
        d = 0  # 规则§一：降半档，边界情形归 0
    return _board("macro_cross", d, f"{phase}/{asset_base}", as_of, True, "normal")


def norm_cycle_v4(v4):
    """全球周期 v4：overall_score ≥60→+1；40-59→0；≤39→-1。逾期无触发器→待复核退出判定。"""
    if not v4:
        return (_board("cycle_v4", None, None, None, False, "source_missing"),
                {"assessment_date": None, "next_assessment_date": None,
                 "overdue": False, "trigger_hit": False, "state": "source_missing",
                 "breached_snapshot": None})
    meta = v4.get("_meta") or {}
    cc = v4.get("cycle_consensus") or {}
    score = cc.get("overall_score")
    if score is None:
        d = None
    elif score >= 60:
        d = 1
    elif score >= 40:
        d = 0
    else:
        d = -1
    overdue = bool(meta.get("assessment_overdue"))  # P0-① 单源
    # 触发器快照（规则§三.2：利率范式/大债务约束层 breached 计数）
    hrt = ((v4.get("cross_analysis") or {}).get("high_rate_tracker")) or {}
    crr = ((v4.get("cycle_layers") or {}).get("constraint_rate_regime") or {}).get("regime_state") or {}
    snapshot = {
        "high_rate_tracker": {k: hrt.get(k) for k in
                              ("structural_breached", "forward_looking_breached", "market_based_breached")},
        "constraint_rate_regime": {k: crr.get(k) for k in
                                   ("structural_breached", "forward_breached", "market_breached")},
    }
    trigger_hit = False  # v1 保守：无历史基线无法判定「计数变化」，快照供未来 diff
    if overdue and trigger_hit:
        state, effective = "reviewing", False
    elif overdue:
        state, effective = "pending_review", False
    else:
        state, effective = "normal", True
    board = _board("cycle_v4", d, f"共识度{score}" if score is not None else None,
                   meta.get("assessment_date"), effective, state if not effective else "normal")
    clock = {"assessment_date": meta.get("assessment_date"),
             "next_assessment_date": meta.get("next_assessment_date"),
             "overdue": overdue, "trigger_hit": trigger_hit, "state": state,
             "breached_snapshot": snapshot}
    return board, clock


def norm_a_timing(t, tr):
    """A股择时：composite_score≥65且非红灯→+1；≤35或红灯→-1；其余→0。信号灯取右侧文件。"""
    if not t:
        return _board("a_timing", None, None, None, False, "source_missing")
    score = t.get("composite_score")
    light = ((tr or {}).get("signal_light")) or "unknown"
    as_of = t.get("update_date")
    if score is None:
        return _board("a_timing", None, None, as_of, False, "source_missing")
    red = (light == "red")
    if score >= 65 and not red:
        d = 1
    elif score <= 35 or red:
        d = -1
    else:
        d = 0
    return _board("a_timing", d, f"{score}/{light}", as_of, True, "normal")


def pair_type(a, b):
    if a is None or b is None:
        return None
    if (a, b) in ((1, -1), (-1, 1)):
        return "hard"
    if a == b:
        return "aligned"
    return "divergence"


def build_conflict(boards):
    """pairs 全量列出（含 effective=false 的板，note 标注降级）；
    level 仅由双方均 effective 的 pair 决定。⛔critical=有反向对且任两板均不同向。"""
    pairs = []
    for i in range(len(boards)):
        for j in range(i + 1, len(boards)):
            A, B = boards[i], boards[j]
            t = pair_type(A["direction"], B["direction"])
            if t in (None, "aligned"):
                continue
            note = f'{BOARD_NAMES[A["board"]]} {A["direction"]:+d} vs {BOARD_NAMES[B["board"]]} {B["direction"]:+d}'
            downgraded = []
            if not A["effective"]:
                downgraded.append(f'{BOARD_NAMES[A["board"]]}({A["status"]})')
            if not B["effective"]:
                downgraded.append(f'{BOARD_NAMES[B["board"]]}({B["status"]})')
            if downgraded:
                note += f'（{"、".join(downgraded)}，按规则降级展示）'
            pairs.append({"a": A["board"], "b": B["board"], "type": t,
                          "both_effective": A["effective"] and B["effective"],
                          "note": note})
    eff_pairs = [p for p in pairs if p["both_effective"]]
    eff_boards = [b for b in boards if b["effective"] and b["direction"] is not None]
    hard = [p for p in eff_pairs if p["type"] == "hard"]
    if hard:
        # ⛔高冲突仅限「三块」：至少一对反向且无任何两板同向（规则§二原文）；
        # 仅剩两块 effective 时按 🔴hard 处理。
        dirs = [b["direction"] for b in eff_boards]
        level = ("critical" if len(eff_boards) >= 3 and len(set(dirs)) == len(dirs)
                 else "hard")
    elif any(p["type"] == "divergence" for p in eff_pairs):
        level = "divergence"
    else:
        level = "none"
    return {"level": level, "pairs": pairs}


def main():
    ind = _load(INDICATORS)
    v4 = _load(V4)
    t = _load(TIMING)
    tr = _load(TIMING_RIGHT)

    b_macro = norm_macro_cross(ind)
    b_v4, clock = norm_cycle_v4(v4)
    b_timing = norm_a_timing(t, tr)
    boards = [b_macro, b_v4, b_timing]
    conflict = build_conflict(boards)

    out = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "boards": boards,
        "v4_clock": clock,
        "conflict": conflict,
        "last_review": {"date": None, "conclusion": None, "by": None},
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[conflict_flags] level={conflict['level']} pairs={len(conflict['pairs'])} "
          f"boards={[(b['board'], b['direction'], b['status']) for b in boards]}")
    return out


if __name__ == "__main__":
    main()
