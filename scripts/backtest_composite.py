#!/usr/bin/env python3
"""backtest_composite.py — [P2-12 中性映射校准 2026-10-18 投资分析裁决 Q4/Q5]

裁决要点：
- Q4 全量可重算段+逐段口径注记；全段 vs 2023-09 同构段两套分布对照，差异大以同构段为准
- Q5 本期只出分布报告+偏离结论，不动阈值（阈值平移另立 P3）

方法（执行发现如实注记）：
- 严格同构不可行：crowding_ratio 仅 1 条（2026-10）、buffett/turnover 2026 中新上线
  → 逐月"可用指标集+权重归一化"（裁决"全量可重算"的可执行解释），每月指标覆盖清单留痕
- score 近似：分位类指标 history 即 score 口径（pe/margin 系列）；
  原始值类用"全样本分位"近似 score（后视分位，分布校准用途下自洽）
- 方向映射：人工核对表（DIRECTION 表），方向存疑指标进 PENDING 组，
  主序列输出 含/不含待定组 两版，方向表附报告待投资分析复核
- 频率：月末采样（月线级执行口径）
"""
import json
import math
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

# 方向表：+1=值高热高分(正向) / -1=值高冷低分(逆向) / None=存疑进待定组
# 依据：当日 value/score 对 + 指标语义 + 阈值注记（待投资分析复核）
DIRECTION = {
    "valuation.hs300_pe_percentile": +1,      # 分位=score 直接（高=热）
    "valuation.buffett_ratio": +1,            # 比率高=贵=热
    "valuation.break_net_rate": -1,           # 破净率高=便宜=冷
    "liquidity.social_financing_trend": +1,   # 社融增速高=宽松（7.11低位→score25 ✓）
    "liquidity.interest_rate": -1,            # 利率高=紧=冷
    "liquidity.fed_policy": -1,               # 联邦利率高=紧=冷
    "equity_bond.wanda_erp": -1,              # ERP高=股票便宜=冷（score20=ERP高位 ✓）
    "equity_bond.dividend_bond_spread_red": -1,   # 利差高=便宜=冷（3.0→score20 ✓）
    "equity_bond.dividend_bond_spread_hs300": -1, # 同上
    "equity_bond.cn_us_spread": +1,           # [投资分析复核10-18] 利差走阔(倒挂收窄)=外部友好=高分
    "capital_flow.equity_fund_position": +1,  # 仓位高=热（95.89→score85 ✓）
    "capital_flow.margin_buying_ratio": +1,   # 分位=score（76.4/76.4 ✓）
    "capital_flow.flexible_fund_position": +1,
    "capital_flow.new_fund": +1,              # 新发多=热
    "capital_flow.industrial_capital": +1,    # [复核10-18] 净增持=产业资本抄底=底部信号=高分
    "sentiment.fund_3y_annual": +1,           # 收益高=热（阈值映射，分位近似）
    "sentiment.turnover_rate": +1,            # 换手高=热
    "sentiment.new_account_opening": +1,      # 开户多=热
    "micro_structure.crowding_ratio": +1,     # 仅1条，实际不入序列
    "micro_structure.industry_concentration": -1,    # [复核10-18] HHI高=抱团=结构脆弱=低分(卡片释义一致)
    "micro_structure.concentration_trend": -1,       # [复核10-18] 动量正=抱团加剧=低分(卡片释义一致)
}
PERCENTILE_AS_SCORE = {"valuation.hs300_pe_percentile", "capital_flow.margin_buying_ratio"}  # history value 即分位/score


def percentile_scores(values, direction):
    """全样本分位 → score 序列（0-100）；direction=-1 取 100-分位"""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            pct[order[k]] = avg / (n - 1) * 100 if n > 1 else 50.0
        i = j + 1
    return [p if direction > 0 else 100 - p for p in pct]


def month_key(d):
    return d[:7]


def main():
    ts = json.loads((DATA / "timing_scores.json").read_text(encoding="utf-8"))
    dims = ts["dimensions"]

    # 收集 sw>0 且有 history 的指标
    series = {}   # key -> {"month": {mk: score}, "weight": dim_w, "sw": sub_w, "dir": d}
    for dn, dv in dims.items():
        if dn.startswith("_"):
            continue
        dim_w = dv.get("weight", 0)
        for ik, iv in dv.get("indicators", {}).items():
            sw = iv.get("sub_weight")
            key = f"{dn}.{ik}"
            if not sw or key not in DIRECTION:
                continue
            h = iv.get("history") or []
            if len(h) < 24:   # 太短不入（crowding 1条/buffett 120条? buffett=120月? 120条日频=2026-04起）
                continue
            direction = DIRECTION[key]
            dates = [p["date"] for p in h]
            vals = [float(p["value"]) for p in h]
            if key in PERCENTILE_AS_SCORE:
                scores = [min(100, max(0, v)) if direction > 0 else min(100, max(0, 100 - v)) for v in vals]
            else:
                scores = percentile_scores(vals, +1)  # 先全样本分位
                if direction is None:
                    direction = +1  # 待定组先按正向，输出两版对照
                if direction < 0:
                    scores = [100 - s for s in scores]
            # 月末采样：每月最后一个可得值
            month_map = {}
            for d, s in zip(dates, scores):
                month_map[month_key(d)] = s
            series[key] = {"month": month_map, "dim": dn, "dim_w": dim_w, "sw": sw,
                           "dir": DIRECTION[key], "n": len(h), "start": dates[0]}

    # 全月集合
    all_months = sorted({mk for s in series.values() for mk in s["month"].keys()})

    def composite_for(months, include_pending):
        out = {}
        coverage = {}
        for mk in months:
            num, den = 0.0, 0.0
            used = []
            for key, s in series.items():
                if not include_pending and s["dir"] is None:
                    continue
                v = s["month"].get(mk)
                if v is None:
                    continue
                w = s["dim_w"] * s["sw"]
                num += w * v
                den += w
                used.append(key)
            if den > 0:
                out[mk] = round(num / den, 1)
                coverage[mk] = len(used)
        return out, coverage

    full_main, cov_main = composite_for(all_months, include_pending=False)
    full_pend, _ = composite_for(all_months, include_pending=True)
    iso_months = [m for m in all_months if m >= "2023-09"]
    iso_main, _ = composite_for(iso_months, include_pending=False)

    def dist(series_map, label):
        if not series_map:
            return {"label": label, "n": 0}
        vs = sorted(series_map.values())
        n = len(vs)
        def q(p):
            i = min(n - 1, max(0, int(p * (n - 1))))
            return vs[i]
        cur = 47.7  # 当前 composite
        rank = sum(1 for v in vs if v <= cur) / n * 100
        return {"label": label, "n": n,
                "min": vs[0], "p10": q(.10), "p25": q(.25), "p50": q(.50),
                "p75": q(.75), "p90": q(.90), "max": vs[-1],
                "mean": round(sum(vs) / n, 1),
                "cur_47_7_percentile": round(rank, 1),
                "span": f"{min(series_map)}→{max(series_map)}"}

    report = {
        "generated": "2026-10-18", "direction_review": "投资分析2026-10-18复核：4待定组全部定向(cn_us+1/industrial+1/HHI×2−1)，待定组清空",
        "method": "逐月可用指标集+权重归一化；分位类history即score；原始值全样本分位近似；方向表人工核对(待复核)",
        "indicators_used": {k: {"start": v["start"], "n": v["n"], "dir": v["dir"]}
                            for k, v in series.items()},
        "pending_group": [k for k, v in series.items() if v["dir"] is None],
        "distributions": [
            dist(full_main, "全段·主序列(不含待定组)"),
            dist(full_pend, "全段·含待定组(正向假设)"),
            dist(iso_main, "2023-09起·主序列"),
        ],
        "coverage_sample": {mk: cov_main[mk] for mk in
                            [list(cov_main)[0], list(cov_main)[len(cov_main)//2], list(cov_main)[-1]] if cov_main},
    }
    out_path = DATA / "composite_backtest.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["distributions"], ensure_ascii=False, indent=1))
    print("指标数:", len(series), "| 待定组:", report["pending_group"])
    print("已落盘:", out_path)


if __name__ == "__main__":
    main()
