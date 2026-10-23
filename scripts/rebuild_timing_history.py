#!/usr/bin/env python3
"""rebuild_timing_history.py — [F2 2026-10-23 v2呈现层升级] 12个月决策态重建回填

口径（沿 P2-12 backtest_composite.py CIO/投资分析已验口径，扩展至交易日频）：
- 窗口：近 12 个月交易日，止于真实日志首日前（真实段 2026-10-16 起不动——"优先用真实日更产物"）
- 网格：margin_buying_ratio 的日频日期（交易日对齐代理）
- 指标可用性（防前视）：
    月度序列（期间 YYYY-MM 或间隔中位≥25天）→ 该月最后一个交易日才可用（backtest 月末采样同口径）
    周度/日度序列 → 当日可用
    LOCF 沿用，staleness 上限 45 天（超期剔除出当日计算，入 manifest）
- 分数变换：PERCENTILE_AS_SCORE 直接取值；其余全样本分位×方向（后视分位——
    分布/趋势展示用途自洽，水印显形"非当日可得口径"，与 backtest 一致）
- 权重：dim_w × sub_weight，按当日可用指标集权重归一化（backtest 同法），逐日覆盖数留痕
- 阶段：P2-11 迟滞状态机逐日重放（首日 prev=None 裸映射），import 自 update_data 保口径
- 趋势态：P1-6 同口径（中证全指 sh000985，价 vs MA120 + 250 日动量四态）
- 不重建：triggers / P1-7 非线性权重滞回 / 锚定 / P1-8 / P1-9（模块无历史状态，水印注明）
- 水印：record.reconstructed=true + reconstruction{method, at, coverage_n, watermark:"重建数据"}
    前端走势图/状态卡据此显形区分重建段与真实日志（F2 硬性要求，R3 消费）
- 幂等：重跑先剔除窗口内 reconstructed 记录再重建；真实记录永不触碰
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest_composite import DIRECTION, PERCENTILE_AS_SCORE, percentile_scores  # noqa: E402

DATA = Path(__file__).parent.parent / "data"
STALENESS_CAP_DAYS = 45
WATERMARK = "重建数据"


def log(msg):
    print(f"[F2] {msg}", flush=True)


def parse_date(s):
    return date.fromisoformat(s) if len(s) == 10 else date.fromisoformat(s + "-01")


def month_last_grid_day(y, m, grid_set):
    if m == 12:
        nxt = date(y + 1, 1, 1)
    else:
        nxt = date(y, m + 1, 1)
    d = nxt - timedelta(days=1)
    while d >= date(y, m, 1):
        if d in grid_set:
            return d
        d -= timedelta(days=1)
    return None


def load_stage_machine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ud", Path(__file__).parent / "update_data.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.apply_stage_hysteresis


def fetch_index_daily():
    """中证全指日K（P1-6 同数据源 tencent_tx）"""
    import akshare as ak
    df = ak.stock_zh_index_daily_tx(symbol="sh000985")
    df = df[["date", "close"]].copy()
    df["date"] = df["date"].astype(str).str[:10]
    df["close"] = df["close"].astype(float)
    return df


def trend_by_day(df, grid):
    """逐网格日计算 P1-6 趋势四态（价 vs MA120 + 250日动量）"""
    dates = [str(x)[:10] for x in df["date"]]
    closes = list(df["close"])
    out = {}
    j = 0
    for g in grid:
        # 用 ≤g 的最后一个指数交易日
        i = None
        lo, hi = 0, len(dates) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if dates[mid] <= g:
                i = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if i is None or i < 370:  # 需 MA120 + 250日动量窗口
            continue
        ma_now = sum(closes[i - 119:i + 1]) / 120
        ma_prev = sum(closes[i - 139:i - 19]) / 120
        mom = closes[i] / closes[i - 250] - 1
        above = closes[i] > ma_now
        if above and mom > 0:
            trend, ann = "strong_bull", ""
        elif above and mom <= 0:
            trend, ann = "weak_bull", "熊市反弹风险"
        elif not above and mom > 0:
            trend, ann = "weak_bear", "长期趋势未破"
        else:
            trend, ann = "strong_bear", ""
        out[g] = {
            "trend": trend, "annotation": ann,
            "close": round(closes[i], 2), "ma120": round(ma_now, 2),
            "ma120_direction": "up" if ma_now > ma_prev else "down",
            "momentum_12m": round(mom, 4), "as_of": dates[i],
            "source": "reconstructed tencent_tx sh000985",
        }
    return out


def main():
    ts = json.loads((DATA / "timing_scores.json").read_text(encoding="utf-8"))
    hist_path = DATA / "timing_history.json"
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    real_dates = sorted(r["date"] for r in hist if not r.get("reconstructed"))
    assert real_dates, "无真实记录锚点"
    window_end = date.fromisoformat(real_dates[0]) - timedelta(days=1)
    window_start = date.fromisoformat(real_dates[0]) - timedelta(days=365)
    log(f"窗口 {window_start} ~ {window_end}（真实段 {real_dates[0]} 起 {len(real_dates)} 条不动）")

    # ---- 指标序列提取 ----
    dims = ts["dimensions"]
    raw_series = {}  # key -> {dates[], scores[], dim, dim_w, sw}
    for dn, dv in dims.items():
        if dn.startswith("_"):
            continue
        for ik, iv in dv.get("indicators", {}).items():
            sw = iv.get("sub_weight")
            key = f"{dn}.{ik}"
            if not sw or key not in DIRECTION:
                continue
            h = iv.get("history") or []
            if len(h) < 24:
                continue
            dates = [p["date"] for p in h]
            vals = [float(p["value"]) for p in h]
            if key in PERCENTILE_AS_SCORE:
                scores = [min(100, max(0, v)) if DIRECTION[key] > 0
                          else min(100, max(0, 100 - v)) for v in vals]
            else:
                scores = percentile_scores(vals, +1)
                if DIRECTION[key] < 0:
                    scores = [100 - s for s in scores]
            raw_series[key] = {"dates": dates, "scores": scores, "dim": dn,
                               "dim_w": dv.get("weight", 0), "sw": sw}
    log(f"可用指标 {len(raw_series)} 路: {sorted(raw_series)}")

    # ---- 交易日网格 ----
    grid = sorted(d for d in raw_series["capital_flow.margin_buying_ratio"]["dates"]
                  if window_start <= date.fromisoformat(d) <= window_end)
    grid_set = set(grid)
    log(f"交易日网格 {len(grid)} 天")

    # ---- 可用性映射（防前视）----
    for key, s in raw_series.items():
        ds = s["dates"]
        gaps = [(parse_date(b) - parse_date(a)).days for a, b in zip(ds[-11:-1], ds[-10:])]
        med = sorted(gaps)[len(gaps) // 2] if gaps else 1
        is_monthly = med >= 25
        avail = {}
        for d, sc in zip(ds, s["scores"]):
            if is_monthly:
                pdt = parse_date(d)
                ad = month_last_grid_day(pdt.year, pdt.month, grid_set)
                if ad is None:
                    continue
            else:
                if len(d) != 10:
                    continue
                ad = d
            avail[ad] = sc  # 同日取最后值
        s["avail"] = sorted(avail.items())
        s["monthly"] = is_monthly

    # ---- 趋势（P1-6 同口径）----
    log("拉取中证全指日K ...")
    try:
        idx = fetch_index_daily()
        trends = trend_by_day(idx, grid)
        log(f"趋势覆盖 {len(trends)}/{len(grid)} 天")
    except Exception as e:
        log(f"⚠️ 指数日K拉取失败({type(e).__name__}: {e})，趋势态缺省")
        trends = {}

    # ---- 逐日重放 ----
    apply_hyst = load_stage_machine()
    records = []
    prev_stage = None
    for g in grid:
        num = den = 0.0
        dim_acc = {}   # dim -> [num, den]
        used = 0
        for key, s in raw_series.items():
            val = None
            for ad, sc in reversed(s["avail"]):
                if ad <= g:
                    if (date.fromisoformat(g) - date.fromisoformat(ad)).days <= STALENESS_CAP_DAYS:
                        val = sc
                    break
            if val is None:
                continue
            w = s["dim_w"] * s["sw"]
            num += w * val
            den += w
            a = dim_acc.setdefault(s["dim"], [0.0, 0.0])
            a[0] += s["sw"] * val
            a[1] += s["sw"]
            used += 1
        if den == 0:
            continue
        comp = round(num / den, 1)
        stage = apply_hyst(comp, prev_stage)
        prev_stage = stage
        rec = {
            "date": g,
            "composite_score": comp,
            "stage": stage,
            "dimensions": {dn: round(n / d, 1) for dn, (n, d) in dim_acc.items() if d > 0},
            "reconstructed": True,
            "reconstruction": {
                "method": "F2 rebuild v1 (backtest口径交易日频扩展: 可用指标集权重归一化+月末可用性+全样本分位)",
                "at": datetime.now().strftime("%Y-%m-%d"),
                "coverage_n": used,
                "watermark": WATERMARK,
                "caveats": "分数为全样本分位近似(后视),非当日可得口径; 无triggers/P1-7/P1-8/P1-9状态",
            },
        }
        if g in trends:
            rec["trend_state"] = trends[g]
        records.append(rec)
    covs = [r["reconstruction"]["coverage_n"] for r in records]
    log(f"重建 {len(records)} 条; 覆盖数 min={min(covs)} max={max(covs)}")

    # ---- 幂等合并：剔除窗口内旧重建记录，保留全部真实记录 ----
    kept = [r for r in hist if not r.get("reconstructed")]
    merged = sorted(kept + records, key=lambda r: r["date"])
    for path in (hist_path, Path(__file__).parent.parent / "frontend" / "data" / "timing_history.json"):
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"落盘 {len(merged)} 条（真实 {len(kept)} + 重建 {len(records)}）→ data/ + frontend/data/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
