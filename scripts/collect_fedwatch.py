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

    meetings = []
    if isinstance(probs, dict):
        for date_str, p in sorted(probs.items())[:2]:
            hike = None
            if isinstance(p, dict):
                for k in ("hike", "raise", "increase", "up"):
                    if k in p:
                        hike = p[k]
                        break
            meetings.append({"date": date_str, "hike_prob": hike})

    def _norm(p):
        if p is None:
            return None
        p = float(p)
        return round(p / 100 if p > 1 else p, 4)

    p1 = _norm(meetings[0]["hike_prob"]) if len(meetings) > 0 else None
    p2 = _norm(meetings[1]["hike_prob"]) if len(meetings) > 1 else None
    cum = None
    if p1 is not None and p2 is not None:
        cum = round(1 - (1 - p1) * (1 - p2), 4)

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "cme-fedwatch 0.2.1 (CME 30D Fed Funds futures settlements + FRED)",
        "target_range": [float(low), float(high)],
        "prev_target_range": old.get("target_range"),
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
