#!/usr/bin/env python3
"""collect_fedwatch.py — [P1-8 美联储双层 2026-10-18 投资分析裁决]

数据源：cme-fedwatch PyPI 包（CME 官方 FedWatch 方法学重组：30天联邦基金期货
结算价 product 305 + FRED EFFR/目标区间 + FOMC 日程，免 key）。
取数时点：每日结算后一次（裁决③，防月末/会议临近日内抖动），CI workflow 固定时段。

产出 data/fed_watch.json：
- as_of：本次取数日
- target_range：[low, high] 当前联邦基金目标区间（鹰派自动轨：区间上移检测）
- meetings：未来 2 次会议 [{date, hike_prob}]
- cumulative_hike_prob：1-(1-p1)(1-p2)（裁决①，独立性假设与 CME Cumulative 同构偏保守）
- prev_target_range：上一次采集的目标区间（区间上移判定对照，从旧文件继承）

降级（裁决③）：采集失败保留旧文件（继承前日）；update_data 侧另有手工配置兜底。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "fed_watch.json"


def main():
    import cme_fedwatch as fw

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}

    low, high = fw.fetch_target_range()
    probs = fw.get_probabilities()

    # 真实返回结构（0.2.1 源码 __init__.py 文档串实证）：
    # {"effr":..., "current_target":"3.75%-4.00%", "trade_date":...,
    #  "meetings":[{"date":"2026-10-28","contract":"ZQV6",
    #               "probabilities":{"3.75%-4.00%":42.4,"4.00%-4.25%":57.6}}]}
    # hike_prob = 概率质量和（区间下限 >= 当前区间上限 high → 该档为加息档）
    import re as _re

    def _hike_prob(prob_map, cur_high):
        if not isinstance(prob_map, dict) or not prob_map:
            return None
        total, hike = 0.0, 0.0
        for label, pct in prob_map.items():
            m = _re.match(r"^\s*([\d.]+)%", str(label))
            if not m:
                continue
            try:
                lower = float(m.group(1))
                v = float(pct)
            except (TypeError, ValueError):
                continue
            total += v
            if lower >= cur_high - 1e-9:  # 区间整体高于当前目标区间 → 加息
                hike += v
        if total <= 0:
            return None
        return round(hike / 100.0, 4)  # 百分数→0-1

    meetings = []
    raw_meetings = probs.get("meetings") if isinstance(probs, dict) else None
    if isinstance(raw_meetings, list):
        for item in raw_meetings[:2]:
            if not isinstance(item, dict):
                continue
            meetings.append({
                "date": item.get("date"),
                "hike_prob": _hike_prob(item.get("probabilities"), float(high)),
            })

    p1 = meetings[0]["hike_prob"] if len(meetings) > 0 else None
    p2 = meetings[1]["hike_prob"] if len(meetings) > 1 else None
    cum = None
    if p1 is not None and p2 is not None:
        cum = round(1 - (1 - p1) * (1 - p2), 4)

    # F3①-2b: DFEDTAR 日频目标区间历史（FRED DFEDTARU/DFEDTARL）
    # 提供每日生效的目标区间，比月频 fed_funds 快 2 周检测加息事件
    from datetime import date as _date, timedelta as _td
    tr_history = []
    try:
        _end = _date.today()
        _start = _end - _td(days=365)
        _ranges = fw.fetch_target_range_history(_start, _end)
        for d in sorted(_ranges.keys()):
            tr_history.append({
                "date": str(d),
                "lower": _ranges[d][0],
                "upper": _ranges[d][1]
            })
    except Exception as e:
        # 降级：不阻断主流程
        import sys
        print(f"WARN DFEDTAR history failed: {e}", file=sys.stderr)

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "cme-fedwatch 0.2.1 (CME 30D Fed Funds futures settlements + FRED)",
        "target_range": [float(low), float(high)],
        "prev_target_range": old.get("target_range"),
        "target_range_history": tr_history,
        "meetings": meetings,
        "cumulative_hike_prob": cum,
        "note": "累计口径=1-(1-p1)(1-p2)（独立性假设偏保守，投资分析2026-10-18裁决）",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK fed_watch.json: target={low}-{high} cum_hike={cum} meetings={len(meetings)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 采集失败：保留旧文件（继承前日），非零退出让 CI 显式告警
        print(f"WARN fedwatch 采集失败（保留前日数据）: {e}", file=sys.stderr)
        sys.exit(1)
