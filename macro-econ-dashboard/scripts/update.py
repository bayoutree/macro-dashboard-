"""
update.py — 一键抓取并生成标准指标 JSON

本地运行：
    python3 scripts/update.py
    -> 在项目根目录的 data/indicators.json 写入结果

也可被 Vercel Serverless (api/fetch_data.py) 调用 build_payload()。
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime

# 让 `python3 scripts/update.py` 能直接 import 同级模块
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fetch_china import get_china_groups
from fetch_us import get_us_groups
from build_five_layer import build_five_layer_payload
from normalize import make_indicator

PROJECT_ROOT = os.path.dirname(_HERE)


# ----------------------------------------------------------------------------
# 交叉指标（cross）
# ----------------------------------------------------------------------------
def _diff_series(a, b):
    """按日期合并两个 (date,value) 序列，输出 a-b。"""
    if not a or not b:
        return None
    da = {d: v for d, v in a}
    db = {d: v for d, v in b}
    common = sorted(set(da) & set(db))
    if not common:
        return None
    return [(d, da[d] - db[d]) for d in common]


def build_cross(china_raw, us_raw, china_groups):
    cross = {}

    # 中美 10Y 利差（中国10Y - 美国10Y），单位 bps
    cn10 = china_raw.get("cn_10y")
    us10 = us_raw.get("ust_10y")
    if cn10 and us10:
        diff = _diff_series(cn10, us10)
        if diff:
            cross["cn_us_10y_spread"] = make_indicator(diff, unit="bps")

    # 美元兑人民币：直接复用中国金融项
    usdcny = china_groups.get("financial", {}).get("usdcny")
    if usdcny:
        cross["usdcny"] = usdcny

    return cross


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def build_payload():
    china_groups, china_raw = get_china_groups()
    us_groups, us_raw = get_us_groups()
    cross = build_cross(china_raw, us_raw, china_groups)

    # 五层结构 adapter：旧分组 → 前端五层
    return build_five_layer_payload(china_groups, us_groups, cross, china_raw, us_raw)


def main():
    payload = build_payload()
    out_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "indicators.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    cn_n = sum(len(v) for v in payload["china"].values())
    us_n = sum(len(v) for v in payload["us"].values())
    print(f"[update] 已写入 {out_path}")
    print(f"[update] 中国指标 {cn_n} 项，美国指标 {us_n} 项，交叉 {len(payload['cross'])} 项")
    print(f"[update] update_time = {payload['update_time']}")
    return payload


if __name__ == "__main__":
    main()
