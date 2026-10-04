#!/usr/bin/env python3
"""中国货币供应采集（akshare macro_china_money_supply）。

统一提供 M1/M2 同比与「M1-M2 剪刀差」序列，供 update_data.py 与
sync_timing_scores_history.py 共用，避免多处硬编码 / 多源漂移。

数据源：中国人民银行（经 akshare macro_china_money_supply，含同比列，更新至最新）。
旧源 ak.macro_china_m2_yearly（金十）已停更，不再使用。
"""
from __future__ import annotations

import re

import pandas as pd


def fetch_money_supply() -> list:
    """返回 [{'date':'YYYY-MM','m1_yoy':..,'m2_yoy':..}]，按时间升序。"""
    import akshare as ak

    df = ak.macro_china_money_supply()
    rows = []
    for _, r in df.iterrows():
        raw = str(r.get("月份", ""))
        m = re.search(r"(\d{4})年(\d{1,2})月", raw)
        if not m:
            continue
        m1 = r.get("货币(M1)-同比增长")
        m2 = r.get("货币和准货币(M2)-同比增长")
        if pd.isna(m1) or pd.isna(m2):
            continue
        rows.append({
            "date": f"{m.group(1)}-{m.group(2).zfill(2)}",
            "m1_yoy": round(float(m1), 1),
            "m2_yoy": round(float(m2), 1),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def fetch_m1_m2_scissors(max_points: int | None = None) -> list:
    """M1-M2 剪刀差 = M1同比 − M2同比（月频）。返回 [{'date','value'}] 升序。"""
    out = [
        {"date": r["date"], "value": round(r["m1_yoy"] - r["m2_yoy"], 1)}
        for r in fetch_money_supply()
    ]
    if max_points:
        out = out[-max_points:]
    return out


if __name__ == "__main__":
    s = fetch_m1_m2_scissors()
    print(f"points={len(s)}")
    print("head:", s[:2])
    print("tail:", s[-3:])
