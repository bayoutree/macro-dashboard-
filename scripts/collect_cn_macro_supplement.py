#!/usr/bin/env python3
"""collect_cn_macro_supplement.py — F3①-3 + R7 catalyst 数据源补充

数据源：
- akshare macro_china_fx_gold：国家外汇储备（SAFE 月频，亿美元）
- akshare macro_china_money_supply：M1/M2 同比（央行月频，%）

产出 data/cn_macro_supplement.json：
- as_of：采集日
- fx_reserves：[{date, value_billion_usd}]，外储月度
- m1_yoy：[{date, value_pct}]，M1 同比
- m1_m2_scissors：[{date, value_pct}]，M1-M2 剪刀差

用途：
- F3④ 汇率阀门：外储连降月数
- R7 catalyst 流动性代理：M1 同比 / M1-M2 剪刀差连续 2 月改善
- 消费端（update_data.py）读取本文件后 merge 进 cn_macro.json
"""
import json
import sys
import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "cn_macro_supplement.json"


def parse_month(s: str) -> str:
    """'2026年09月份' → '2026-09'"""
    m = re.search(r"(\d{4})年(\d{1,2})月", str(s))
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else str(s)


def main():
    import akshare as ak

    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare (macro_china_fx_gold + macro_china_money_supply)",
        "fx_reserves": [],
        "m1_yoy": [],
        "m1_m2_scissors": [],
        "note": "F3①-3+R7：外储/ M1同比/ M1-M2剪刀差",
    }

    # 1. 外储（SAFE 月频）
    try:
        df = ak.macro_china_fx_gold()
        rows = []
        for _, row in df.iterrows():
            val = row.get("国家外汇储备-数值")
            if val is not None and not (isinstance(val, float) and val != val):
                rows.append({"date": parse_month(row["月份"]), "value_billion_usd": round(float(val), 2)})
        out["fx_reserves"] = rows
        print(f"OK fx_reserves: {len(rows)} 条 (最新 {rows[-1] if rows else 'N/A'})")
    except Exception as e:
        print(f"FAIL fx_reserves: {e}", file=sys.stderr)

    # 2. M1/M2 同比
    try:
        df = ak.macro_china_money_supply()
        m1, scissors = [], []
        for _, row in df.iterrows():
            d = parse_month(row["月份"])
            m1v = row.get("货币(M1)-同比增长")
            m2v = row.get("货币和准货币(M2)-同比增长")
            m1_ok = m1v is not None and not (isinstance(m1v, float) and m1v != m1v)
            m2_ok = m2v is not None and not (isinstance(m2v, float) and m2v != m2v)
            if m1_ok:
                m1.append({"date": d, "value_pct": round(float(m1v), 2)})
            if m1_ok and m2_ok:
                scissors.append({"date": d, "value_pct": round(float(m1v) - float(m2v), 2)})
        out["m1_yoy"] = m1
        out["m1_m2_scissors"] = scissors
        print(f"OK m1_yoy: {len(m1)} 条 (最新 {m1[-1] if m1 else 'N/A'})")
        print(f"OK m1_m2_scissors: {len(scissors)} 条 (最新 {scissors[-1] if scissors else 'N/A'})")
    except Exception as e:
        print(f"FAIL m1/m2: {e}", file=sys.stderr)

    # 按日期升序排序
    for key in ["fx_reserves", "m1_yoy", "m1_m2_scissors"]:
        out[key] = sorted(out[key], key=lambda x: x.get("date", ""))

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK cn_macro_supplement.json: reserves={len(out['fx_reserves'])} m1={len(out['m1_yoy'])} scissors={len(out['m1_m2_scissors'])}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL cn_macro_supplement 采集失败: {e}", file=sys.stderr)
        sys.exit(1)
