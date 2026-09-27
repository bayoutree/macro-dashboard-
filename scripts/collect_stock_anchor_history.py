#!/usr/bin/env python3
"""
collect_stock_anchor_history.py
================================
股票看板基金仓位锚点卡 —— 真源周频续采（2026-09-27）。

指标：
  capital_flow.equity_fund_position   股票型基金仓位（乐咕乐股周频）
  capital_flow.flexible_fund_position 灵活配置型基金仓位（乐咕乐股周频）

数据源：akshare fund_stock_position_lg / fund_linghuo_position_lg
（legulegu.com 官方序列，date/close/position）。

规则：
  - 全量确定性重建 history（按日期升序），与既有 history 做日期并集：
    真源覆盖的日期以真源为准，其余保留；
  - 打 _history_meta.source 真源标记，update_data.py 每日重建时据此继承；
  - 无真源的指标不在本脚本处理（不碰 HHI/趋势等其他序列）。

用法:
  python collect_stock_anchor_history.py            # 拉取并写入
  python collect_stock_anchor_history.py --dry-run  # 仅打印不写入
"""

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stock_anchor")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
TIMING_PATH = DATA_DIR / "timing_scores.json"

INDICATORS = [
    ("capital_flow", "equity_fund_position", "fund_stock_position_lg",
     "akshare fund_stock_position_lg (legulegu stock fund weekly position)"),
    ("capital_flow", "flexible_fund_position", "fund_linghuo_position_lg",
     "akshare fund_linghuo_position_lg (legulegu flexible allocation fund weekly position)"),
]


def fetch_series(ak_func: str):
    import akshare as ak
    df = getattr(ak, ak_func)()
    pts = []
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        try:
            v = float(row["position"])
        except (TypeError, ValueError):
            continue
        pts.append({"date": d, "value": round(v, 2)})
    pts.sort(key=lambda p: p["date"])
    return pts


def merge_points(old_hist, new_pts):
    merged = {p["date"]: p["value"] for p in (old_hist or [])}
    for p in new_pts:
        merged[p["date"]] = p["value"]  # real source wins
    return [{"date": d, "value": merged[d]} for d in sorted(merged)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(TIMING_PATH, "r", encoding="utf-8") as f:
        timing = json.load(f)

    for sec, key, ak_func, source in INDICATORS:
        ind = timing["dimensions"][sec]["indicators"][key]
        new_pts = fetch_series(ak_func)
        merged = merge_points(ind.get("history"), new_pts)
        logger.info("%s.%s: %d pts (source %d), last=%s",
                    sec, key, len(merged), len(new_pts), merged[-1] if merged else None)
        if not args.dry_run:
            ind["history"] = merged
            ind["value"] = merged[-1]["value"] if merged else ind.get("value")
            ind["_history_meta"] = {"source": source, "synced_at": "2026-09-27"}

    if not args.dry_run:
        with open(TIMING_PATH, "w", encoding="utf-8") as f:
            json.dump(timing, f, ensure_ascii=False, indent=2)
        logger.info("saved timing_scores.json (%.1f KB)",
                    TIMING_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
