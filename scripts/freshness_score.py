#!/usr/bin/env python3
"""freshness_score.py — data_freshness_score 单一真值算法（P0-1 修复）

事故背景
--------
旧实现（update_json.py，2026-10 及以前）：每次流水线运行把
_meta.data_freshness_score 机械 +5（封顶 100），与数据真实年龄完全脱钩。
2026-11-01 CIO 核验：页面显示「新鲜度 100」，实际框架层数据停在
2026-08-06，assessment_date=2026-09-01 而 next_assessment_date=2026-10-01
已逾期 1 个月——「新鲜度在撒谎」。

修复口径（CIO P0-1，2026-11-01）
--------------------------------
1. 每层新鲜度 = max(0, 100 - 该层 last_updated 距今天数)，自然日线性衰减；
2. data_freshness_score = 各层最小值（最旧一层决定整体新鲜度）；
3. data_cutoff_date = 各层 last_updated 的最小值（数据截止日），
   与 assessment_date（评估日）组成双时间戳同时展示；
4. 无 last_updated 的派生层（朱格拉/基钦/美林等每日由 v3 输入重算的层）
   不参与 min，但列入 freshness_detail.excluded_no_date 备查；
5. 本模块是唯一算法实现：update_json.py（v3）与
   build_cycle_position_v4.py（v4）都从这里取数，禁止第三套口径。
"""

from datetime import date, datetime

_METHOD = (
    "min(layer.score); layer.score = max(0, 100 - days_since(last_updated)); "
    "自然日线性衰减（P0-1 修复 2026-11-01，scripts/freshness_score.py 单一真值）"
)


def _parse_date(s):
    """宽松解析日期字符串，返回 datetime.date；失败返回 None。"""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    candidates = [s[:10]]
    if "T" in s:
        candidates.append(s[:19])
    for c in candidates:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(c, fmt).date()
            except ValueError:
                continue
    return None


def layer_freshness(last_updated, today=None):
    """单层新鲜度：max(0, 100 - 距今天数)。日期无法解析返回 None。"""
    d = _parse_date(last_updated)
    if d is None:
        return None
    today = today or date.today()
    return max(0, 100 - (today - d).days)


def compute_freshness(cycle_layers, today=None):
    """按 CIO P0-1 口径计算整体新鲜度。

    返回 (score, cutoff_date, detail)：
      score       int 0-100，各层最小值；全部无日期时返回 0（宁可报警不可造假）
      cutoff_date str，各层 last_updated 最小值（数据截止日）；无则 None
      detail      dict，每层得分明细 + 被排除层，供 _meta.freshness_detail 备查
    """
    today = today or date.today()
    layers, excluded = {}, []
    for name, layer in (cycle_layers or {}).items():
        lu = layer.get("last_updated") if isinstance(layer, dict) else None
        sc = layer_freshness(lu, today)
        if sc is None:
            excluded.append(name)
        else:
            layers[name] = {"last_updated": lu, "score": sc}
    if layers:
        score = min(v["score"] for v in layers.values())
        cutoff = min(v["last_updated"] for v in layers.values())
    else:
        score, cutoff = 0, None
    detail = {
        "method": _METHOD,
        "as_of": today.strftime("%Y-%m-%d"),
        "layers": layers,
        "excluded_no_date": excluded,
    }
    return score, cutoff, detail


def is_assessment_overdue(next_assessment_date, today=None):
    """now > next_assessment_date 即逾期。"""
    d = _parse_date(next_assessment_date)
    if d is None:
        return False
    today = today or date.today()
    return today > d


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).parent.parent / "data" / "cycle_position_v4.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    score, cutoff, detail = compute_freshness(data.get("cycle_layers", {}))
    meta = data.get("_meta", {})
    print(f"file={path}")
    print(f"score={score}  cutoff={cutoff}")
    print(f"assessment_date={meta.get('assessment_date')}  "
          f"next={meta.get('next_assessment_date')}  "
          f"overdue={is_assessment_overdue(meta.get('next_assessment_date'))}")
    for name, v in detail["layers"].items():
        print(f"  {name}: {v['last_updated']} -> {v['score']}")
    if detail["excluded_no_date"]:
        print(f"  excluded(no date): {', '.join(detail['excluded_no_date'])}")
