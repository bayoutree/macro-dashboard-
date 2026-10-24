#!/usr/bin/env python3
"""build_composite_reference.py — [CIO初核①a 改锚 2026-10-24 主人裁决：同构段≈滚动36月，单显示]

生成 R7 位置分位的参照分布文件 data/composite_reference_dist.json。

口径（P2-12 裁决 Q4 同构段的生产化）：
- 与 backtest_composite.py 完全同源的序列构建：逐月可用指标集+权重归一化、
  月末采样、分位类 history 即 score、原始值全样本分位近似、方向表同 P2-12 复核定稿
- 取最近 36 个【已完成】月（不含进行中月，避免当前值自我参照）：
  今日视窗 = 2023-10→2026-09，与同构段（2023-09 起 38 月）等价可比（11+ 指标口径段）
- 本脚本不动 acceptance 锚定文件 composite_backtest.json（P2-12 校准批统一刷新）

刷新节奏：每月首个交易日或 P2-12 校准时重跑。
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtest_composite as bt  # 复用 DIRECTION / PERCENTILE_AS_SCORE / percentile_scores / month_key

DATA = Path(__file__).parent.parent / "data"
WINDOW_MONTHS = 36
REF_MIN_N = 20


def build_monthly_composite():
    """与 backtest_composite.main() 同源的月频 composite 序列（主序列·不含待定组）。"""
    ts = json.loads((DATA / "timing_scores.json").read_text(encoding="utf-8"))
    dims = ts["dimensions"]
    series = {}
    for dn, dv in dims.items():
        if dn.startswith("_"):
            continue
        dim_w = dv.get("weight", 0)
        for ik, iv in dv.get("indicators", {}).items():
            sw = iv.get("sub_weight")
            key = f"{dn}.{ik}"
            if not sw or key not in bt.DIRECTION:
                continue
            h = iv.get("history") or []
            if len(h) < 24:
                continue
            direction = bt.DIRECTION[key]
            dates = [p["date"] for p in h]
            vals = [float(p["value"]) for p in h]
            if key in bt.PERCENTILE_AS_SCORE:
                scores = [min(100, max(0, v)) if direction > 0 else min(100, max(0, 100 - v)) for v in vals]
            else:
                scores = bt.percentile_scores(vals, +1)
                if direction is None:
                    direction = +1
                if direction < 0:
                    scores = [100 - s for s in scores]
            month_map = {}
            for d, s in zip(dates, scores):
                month_map[bt.month_key(d)] = s
            series[key] = {"month": month_map, "dim_w": dim_w, "sw": sw, "dir": bt.DIRECTION[key]}

    all_months = sorted({mk for s in series.values() for mk in s["month"].keys()})
    out, coverage = {}, {}
    for mk in all_months:
        num, den, used = 0.0, 0.0, 0
        for key, s in series.items():
            if s["dir"] is None:
                continue
            v = s["month"].get(mk)
            if v is None:
                continue
            w = s["dim_w"] * s["sw"]
            num += w * v
            den += w
            used += 1
        if den > 0:
            out[mk] = round(num / den, 1)
            coverage[mk] = used
    return out, coverage


def main():
    comp, coverage = build_monthly_composite()
    cur_mk = date.today().isoformat()[:7]
    completed = sorted(mk for mk in comp if mk < cur_mk)          # 仅已完成月
    window = completed[-WINDOW_MONTHS:]
    if len(window) < REF_MIN_N:
        raise SystemExit(f"参照分布不足 {REF_MIN_N} 个已完成月（仅 {len(window)}），中止")
    values = [comp[mk] for mk in window]
    vs = sorted(values)
    n = len(vs)
    q = lambda p: vs[min(n - 1, max(0, int(p * (n - 1))))]
    report = {
        "generated": date.today().isoformat(),
        "anchor": "rolling_36m_completed",
        "method": "P2-12 裁决Q4 同构段口径生产化：逐月可用指标集+权重归一化、月末采样、分位类history即score、原始值全样本分位近似；方向表=投资分析10-18复核定稿",
        "source_script": "scripts/build_composite_reference.py",
        "refresh": "每月首个交易日或 P2-12 校准时重跑",
        "n": n,
        "span": f"{window[0]}→{window[-1]}",
        "stats": {"min": vs[0], "p10": q(.10), "p25": q(.25), "p50": q(.50),
                  "p75": q(.75), "p90": q(.90), "max": vs[-1],
                  "mean": round(sum(vs) / n, 1)},
        "coverage_min": min(coverage[mk] for mk in window),
        "months": [{"month": mk, "composite": comp[mk]} for mk in window],
        "values": values,
    }
    out_path = DATA / "composite_reference_dist.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("n", "span", "stats", "coverage_min")}, ensure_ascii=False, indent=1))
    print("已落盘:", out_path)


if __name__ == "__main__":
    main()
