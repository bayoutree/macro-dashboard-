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



# ----------------------------------------------------------------------------
# 删史守卫（团队硬性约束：严禁脚本静默删 history）
# ----------------------------------------------------------------------------
def _is_series(node):
    """指标序列：含非空 history 列表的 dict。"""
    return (isinstance(node, dict)
            and isinstance(node.get("history"), list)
            and len(node["history"]) > 0)


def _contains_series(node):
    if _is_series(node):
        return True
    if isinstance(node, dict):
        return any(_contains_series(v) for v in node.values())
    return False


def _restore_lost_series(new, old, path, restored):
    """将旧 payload 中存在、新 payload 中整键丢失的指标序列回填（last-known-good）。

    采集源瞬时失败时，add() 会直接丢弃该序列，导致全量重建把既有历史静默抹掉。
    此守卫把「整键消失」的序列用上一版回填，并记录告警；仅处理含 history 的
    真实序列，不动派生字段（派生块键始终存在，不受影响）。
    """
    if not isinstance(new, dict) or not isinstance(old, dict):
        return
    for key, old_val in old.items():
        child = path + [str(key)]
        if key not in new:
            if _contains_series(old_val):
                new[key] = old_val
                restored.append("/".join(child))
        else:
            _restore_lost_series(new[key], old_val, child, restored)


def _apply_no_delete_guard(payload, out_path):
    """读取既有 indicators.json，回填本轮丢失的序列；返回被回填的路径列表。"""
    restored = []
    if not os.path.exists(out_path):
        return restored
    try:
        with open(out_path, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception as e:
        print(f"[update] WARN 读取既有 indicators.json 失败，跳过删史守卫：{e}")
        return restored
    _restore_lost_series(payload, prev, [], restored)
    if restored:
        print(f"[update] WARN 删史守卫：检测到 {len(restored)} 个序列本轮丢失"
              f"（采集源瞬时失败），已回填 last-known-good：")
        for r in restored:
            print(f"[update]   - {r}")
    return restored


def main():
    payload = build_payload()
    out_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "indicators.json")

    # ---- 删史守卫：源瞬时失败不得静默删除既有序列 ----
    _apply_no_delete_guard(payload, out_path)

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

