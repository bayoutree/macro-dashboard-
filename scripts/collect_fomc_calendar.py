#!/usr/bin/env python3
"""collect_fomc_calendar.py — F3①-2 FOMC 会议日历 + FRED DFEDTARU/DFEDTARL 日频目标区间

数据源：
- cme-fedwatch 包内置 FOMC_MEETINGS + get_upcoming_meetings() + fetch_target_range_history()
- FRED DFEDTARU/DFEDTARL 日频（通过 cme-fedwatch 封装调用，免 key）

产出 data/fomc_calendar.json：
- as_of：本次采集日
- upcoming_meetings：未来 FOMC 会议日期列表
- past_meetings：近 2 年历史会议日期
- target_range_history：日频目标区间 [{date, lower, upper}]，近 1 年
- latest_range：当前生效目标区间 [lower, upper]

降级：DFEDTAR 拉取失败时 target_range_history 留空数组，不阻断。
"""
import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "fomc_calendar.json"


def main():
    import cme_fedwatch as fw

    # 1. 未来 FOMC 会议日期
    upcoming = [str(d) for d in fw.get_upcoming_meetings()]

    # 2. 历史会议（近 2 年）
    cutoff = date.today() - timedelta(days=730)
    past = [str(d) for d in fw.FOMC_MEETINGS if d >= cutoff and d <= date.today()]

    # 3. DFEDTAR 日频目标区间历史（近 1 年）
    tr_history = []
    try:
        end = date.today()
        start = end - timedelta(days=365)
        ranges = fw.fetch_target_range_history(start, end)
        for d in sorted(ranges.keys()):
            tr_history.append({
                "date": str(d),
                "lower": ranges[d][0],
                "upper": ranges[d][1]
            })
    except Exception as e:
        print(f"WARN DFEDTAR history failed: {e}", file=sys.stderr)

    # 当前生效区间
    latest = [tr_history[-1]["lower"], tr_history[-1]["upper"]] if tr_history else [None, None]

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "cme-fedwatch (FOMC_MEETINGS + FRED DFEDTARU/DFEDTARL)",
        "upcoming_meetings": upcoming,
        "past_meetings": past,
        "target_range_history": tr_history,
        "latest_range": latest,
        "note": "日频目标区间由 FRED DFEDTARU/L 直接获取，比月频 fed_funds 快 2 周检测加息事件",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK fomc_calendar.json: upcoming={len(upcoming)} past={len(past)} dfedtar_days={len(tr_history)} latest={latest}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL fomc_calendar 采集失败: {e}", file=sys.stderr)
        sys.exit(1)
