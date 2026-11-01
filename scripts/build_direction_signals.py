#!/usr/bin/env python3
"""
build_direction_signals.py — P0-⑤ 冲突自动标注·数据通路准备（2026-11-01 CIO）

把三看板（宏观截面 / 全球周期v4 / A股择时）的方向信号归一化抽取到
data/direction_signals.json，供冲突规则引擎与前端标注消费。

边界说明：
- 本脚本只做「数据通路」：抽取原文信号 + 提供临时方向映射（PROVISIONAL_DIRECTION_MAP，
  明确标记为临时口径）。真正的冲突判定规则（何种不一致算冲突、严重度、展示文案）
  等 CIO 规则规格落地后接入 conflicts[]，当前恒为空数组。
- 每条信号带 as_of 与 age_days，供规则引擎按数据新旧加权（v4 评估日滞后是已知问题，
  见 P0-1 data_cutoff_date）。
"""
import json
import os
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠ 临时方向映射（等 CIO 规则规格替换）：仅把各看板「自己声明的方向」归一到
# bull/bear/neutral 三值；宏观截面的 phase 映射是工程占位，未做投资判断。
PROVISIONAL_DIRECTION_MAP = {
    # cycle_consensus.overall_signal 原生就是 bull/bear 语义
    "bullish": "bull", "bearish": "bear", "neutral": "neutral",
    # timing_right_scores.signal_direction 原生
    "bull": "bull", "bear": "bear",
    # 宏观截面 phase → 方向（临时映射，待裁决）
    "复苏": "bull", "复苏酝酿": "bull", "经济复苏": "bull",
    "过热": "neutral",
    "滞胀": "bear", "滞胀加剧": "bear", "深度滞胀": "bear",
    "衰退": "bear", "衰退中段": "bear", "典型衰退": "bear", "经济筑底": "bear",
    "类滞胀": "bear",
}


def _load(rel):
    p = os.path.join(ROOT, rel)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[direction-signals] 读取失败 {rel}: {e}")
        return None


def _age_days(dstr, today):
    if not dstr:
        return None
    try:
        return (today - datetime.strptime(str(dstr)[:10], "%Y-%m-%d").date()).days
    except Exception:
        return None


def _map_direction(raw):
    if raw is None:
        return None
    return PROVISIONAL_DIRECTION_MAP.get(str(raw), "neutral")


def extract_macro_econ(today):
    """宏观截面（macro-econ-dashboard）: CN/US phase + 货币/信用/流动性轴。"""
    d = _load("macro-econ-dashboard/data/indicators.json")
    if not d:
        return None
    cn = d.get("china", {}).get("economic_cycle", {})
    us = d.get("us", {}).get("economic_cycle", {})
    as_of = (d.get("_meta") or {}).get("update_time") or d.get("update_time")
    return {
        "board": "宏观截面",
        "file": "macro-econ-dashboard/data/indicators.json",
        "as_of": as_of,
        "age_days": _age_days(as_of, today),
        "raw": {
            "cn_phase": cn.get("phase"),
            "cn_monetary": (cn.get("monetary") or {}).get("state"),
            "cn_credit": (cn.get("credit") or {}).get("state"),
            "us_phase": us.get("phase"),
            "us_liquidity": (us.get("liquidity") or {}).get("state"),
        },
        # 临时方向（待 CIO 规则规格替换）：取 CN phase 作宏观截面对 A 股的方向含义
        "direction_provisional": _map_direction(cn.get("phase")),
    }


def extract_cycle_v4(today):
    """全球周期 v4：cycle_consensus 原生方向 + 共识度评分。"""
    d = _load("data/cycle_position_v4.json")
    if not d:
        return None
    cc = d.get("cycle_consensus") or {}
    meta = d.get("_meta") or {}
    as_of = meta.get("assessment_date")
    return {
        "board": "全球周期v4",
        "file": "data/cycle_position_v4.json",
        "as_of": as_of,
        "age_days": _age_days(as_of, today),
        "raw": {
            "overall_signal": cc.get("overall_signal"),
            "overall_score": cc.get("overall_score"),
            "overall_assessment": cc.get("overall_assessment"),
            "assessment_date": as_of,
            "data_cutoff_date": meta.get("data_cutoff_date"),
        },
        "direction_provisional": _map_direction(cc.get("overall_signal")),
    }


def extract_a_share_timing(today):
    """A股择时：左侧 timing_scores + 右侧 timing_right_scores 双信号。"""
    t = _load("data/timing_scores.json")
    tr = _load("data/timing_right_scores.json")
    if not t and not tr:
        return None
    as_of = (tr or {}).get("update_date") or (t or {}).get("update_date")
    return {
        "board": "A股择时",
        "file": "data/timing_scores.json + data/timing_right_scores.json",
        "as_of": as_of,
        "age_days": _age_days(as_of, today),
        "raw": {
            "composite_score": (t or {}).get("composite_score"),
            "stage": (t or {}).get("stage"),
            "position_range": (t or {}).get("position_range"),
            "market_status": (t or {}).get("market_status"),
            "signal_light": (tr or {}).get("signal_light"),
            "signal_direction": (tr or {}).get("signal_direction"),
            "bull_score": (tr or {}).get("bull_score"),
            "bear_score": (tr or {}).get("bear_score"),
            "trend_bias": (tr or {}).get("trend_bias"),
        },
        "direction_provisional": _map_direction((tr or {}).get("signal_direction")),
    }


def main():
    today = date.today()
    sources = {
        "macro_econ": extract_macro_econ(today),
        "cycle_v4": extract_cycle_v4(today),
        "a_share_timing": extract_a_share_timing(today),
    }
    sources = {k: v for k, v in sources.items() if v}
    out = {
        "version": "0.1-prep",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("P0-⑤ 数据通路准备：三看板方向信号归一化。conflicts 恒为空——"
                 "冲突判定规则待 CIO 规则规格；direction_provisional 为临时映射，"
                 "未做投资判断，禁止前端据此直接展示方向结论。"),
        "sources": sources,
        "conflicts": [],
        "conflict_rule_spec": "pending CIO (2026-11-01)",
    }
    out_path = os.path.join(ROOT, "data", "direction_signals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[direction-signals] -> {out_path}")
    for k, v in sources.items():
        print(f"  {v['board']}: direction={v['direction_provisional']} "
              f"as_of={v['as_of']} age={v['age_days']}d")
    return out


if __name__ == "__main__":
    main()
