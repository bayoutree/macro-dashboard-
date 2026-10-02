#!/usr/bin/env python3
"""
freshness_gate.py — 数据新鲜度 / 结构回归门禁（v1.0-final 口径）

依据：《freshness_manifest.json 字段最终口径裁决（冻结稿 v1.0-final）》2026-10-01。

职责：
  1. 按 manifest 计算每个序列 FRESH / WATCH / STALE / GAP 状态（仅看业务日期）；
  2. critical 条目三类结构回归拦截：
       (a) 最后业务日期倒退；
       (b) history 为空，或点数倒退超过 history_count_tolerance（默认 5%）；
       (c) value_path 有值变 null；
  3. warn/report 条目只记录不阻断。

基线取法（裁决 3.2 / 对 Codex Q2）：
  --baseline-api  ：GitHub Contents API 拉 origin/main 原文件（本地推送前用，默认）；
  --baseline-git  ：git show 取基线（CI 中先 git fetch 后用）；
  --baseline-root ：本地目录基线（测试用）；
  无基线的条目按新增/恢复处理，结构部分不阻断（状态仍计算并报告）。

本脚本不插值、不修复数据。状态只按数据点业务日期判定，文件构建时间永不参与。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "freshness_manifest.json"

REPO_API = "https://api.github.com/repos/bayoutree/macro-dashboard-"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

_MISSING = object()
REQUIRED_TOP_KEYS = ("version", "updated", "calendar", "items")
REQUIRED_ITEM_KEYS = (
    "id", "file", "date_path", "freq", "lag_days",
    "critical", "severity_on_gap",
)
VALID_FREQ = {"daily", "weekly", "monthly", "quarterly"}
VALID_SEVERITY = {"block", "warn", "report"}
VALID_STATUS = {"active", "deprecated"}


# ---------------------------------------------------------------- json helpers

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def deep_get(obj: Any, path: str | None, default: Any = _MISSING) -> Any:
    """点号路径取值；空/None 路径返回对象本身；路径不可达返回 default。"""
    if path is None or path == "":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            if default is not _MISSING:
                return default
            raise KeyError(path)
    return cur


def expand_paths(obj: Any, path: str | None) -> list[str]:
    """
    把含 `*` 的点号路径展开为实际路径列表（`*` 对应 dict 的每个 key）。
    返回展开后的具体路径（不含 `*`）。对象本身或路径不含 `*` 时原样返回。
    """
    if not path or "*" not in path:
        return [path] if path else [""]

    parts = path.split(".")
    results: list[list[str]] = [[]]
    cur_nodes: list[Any] = [obj]

    for part in parts:
        if part == "*":
            new_nodes: list[Any] = []
            new_results: list[list[str]] = []
            for node, prefix in zip(cur_nodes, results):
                if isinstance(node, dict):
                    for key in node.keys():
                        new_nodes.append(node[key])
                        new_results.append(prefix + [key])
            cur_nodes = new_nodes
            results = new_results
        else:
            new_nodes = []
            new_results = []
            for node, prefix in zip(cur_nodes, results):
                if isinstance(node, dict) and part in node:
                    new_nodes.append(node[part])
                    new_results.append(prefix + [part])
                elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                    new_nodes.append(node[int(part)])
                    new_results.append(prefix + [part])
            cur_nodes = new_nodes
            results = new_results
    return [".".join(p) for p in results]


# ---------------------------------------------------------------- date handling

_PERIOD_RE = re.compile(r"^(\d{4})\s*Q([1-4])$", re.I)
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")


def date_key(value: Any) -> tuple:
    """
    业务日期转可比较元组：
      '2026-09-30' -> (2026, 9, 30)
      '2026-08'    -> (2026, 8, 0)   （月频，day=0）
      '2026Q2'     -> (2026, 5, 0)   （季度映射到季末月）
      '2026'       -> (2026, 0, 0)
    """
    s = str(value).strip()
    m = _PERIOD_RE.match(s)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return (year, q * 3 - 1, 0)  # Q1->2月 Q2->5月 Q3->8月 Q4->11月（季内月份，用于排序）
    m = _DATE_RE.match(s)
    if not m:
        raise ValueError(f"无法解析业务日期: {value!r}")
    year = int(m.group(1))
    month = int(m.group(2) or 0)
    day = int(m.group(3) or 0)
    if month and not 1 <= month <= 12:
        raise ValueError(f"非法业务日期: {value!r}")
    if day and not 1 <= day <= 31:
        raise ValueError(f"非法业务日期: {value!r}")
    return (year, month, day)


def fmt_key(key: tuple) -> str:
    y, m, d = key
    if d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    if m:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}"


# ---------------------------------------------------------------- trading calendar

def trading_days_between(start: tuple, end: tuple, calendar: str) -> int:
    """
    两个日期之间的交易日数（简化口径：自然工作日，扣除周末；CN 长假按裁决由日历扣除——
    此处用工作日近似，长假在 2/5 与 9/16 阈值上影响不改变 STALE 判定方向）。
    start < end 时返回负数语义由调用方保证 start<=end。
    """
    import datetime as dt
    if not (start[0] and start[1]):
        # 月/季锚点无法精确到交易日；返回自然日 // 1.5 的保守近似（不应在日频出现）
        d0 = dt.date(start[0], max(start[1], 1), max(start[2], 1))
        d1 = dt.date(end[0], max(end[1], 1), max(end[2], 1))
        return max(0, int(round((d1 - d0).days * 5 / 7)))
    d0 = dt.date(start[0], start[1], max(start[2], 1))
    d1 = dt.date(end[0], end[1], max(end[2], 1))
    if d1 < d0:
        return 0
    n = 0
    cur = d0
    while cur < d1:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


# ---------------------------------------------------------------- manifest self-check

def validate_manifest(manifest: Any) -> None:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        raise ValueError("manifest 根节点必须是 object")
    for key in REQUIRED_TOP_KEYS:
        if key not in manifest:
            errors.append(f"manifest 缺少顶层字段 {key}")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest.items 必须是非空数组；" + "; ".join(errors))

    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{i}] 必须是 object")
            continue
        iid = item.get("id", f"#{i}")
        for key in REQUIRED_ITEM_KEYS:
            if key not in item:
                errors.append(f"items[{i}]({iid}) 缺少字段 {key}")
        if isinstance(item.get("id"), str):
            if item["id"] in seen:
                errors.append(f"items[{i}] id 重复: {item['id']}")
            seen.add(item["id"])
        if item.get("freq") not in VALID_FREQ:
            errors.append(f"items[{i}]({iid}) freq 非法: {item.get('freq')}")
        if not isinstance(item.get("critical"), bool):
            errors.append(f"items[{i}]({iid}) critical 必须是布尔值")
        if item.get("severity_on_gap") not in VALID_SEVERITY:
            errors.append(f"items[{i}]({iid}) severity_on_gap 非法")
        status = item.get("status", "active")
        if status not in VALID_STATUS:
            errors.append(f"items[{i}]({iid}) status 非法: {status}")
        if not isinstance(item.get("lag_days"), int) or item.get("lag_days", -1) < 0:
            errors.append(f"items[{i}]({iid}) lag_days 必须是非负整数")

        if status == "deprecated":
            continue

        freq = item.get("freq")
        if freq in ("daily", "weekly"):
            for key in ("fresh_max_trading_days", "watch_max_trading_days"):
                if not isinstance(item.get(key), int):
                    errors.append(f"items[{i}]({iid}) {freq} 条目缺少整数 {key}")
        else:
            cal_mode = "expected_publish_day" in item
            day_mode = "fresh_max_days" in item or "watch_max_days" in item
            if not cal_mode and not day_mode:
                errors.append(
                    f"items[{i}]({iid}) {freq} 条目必须二选一：发布日历"
                    f"(expected_publish_day) 或自然日兜底(fresh_max_days/watch_max_days)")
            if cal_mode and day_mode:
                errors.append(f"items[{i}]({iid}) 发布日历模式与兜底模式不可同时填写")
            if cal_mode:
                if not isinstance(item.get("expected_publish_day"), int):
                    errors.append(f"items[{i}]({iid}) expected_publish_day 必须是整数")
                for key in ("publish_tolerance_days", "watch_publish_delay_days"):
                    if not isinstance(item.get(key), int):
                        errors.append(f"items[{i}]({iid}) 发布日历模式缺少整数 {key}")
            if day_mode:
                for key in ("fresh_max_days", "watch_max_days"):
                    if not isinstance(item.get(key), int):
                        errors.append(f"items[{i}]({iid}) 兜底模式缺少整数 {key}")

        if item.get("critical"):
            for key in ("history_path", "value_path"):
                if key not in item:
                    errors.append(f"items[{i}]({iid}) critical 条目必须带 {key}")

    if errors:
        raise ValueError("manifest 自校验失败:\n  - " + "\n  - ".join(errors))


# ---------------------------------------------------------------- status computation

def _period_index(key: tuple) -> int:
    """把 (year, month/day...) 映射为连续周期序号，用于缺期数计算。"""
    y, m, _ = key
    return y * 12 + max(m, 1)



# ---------------------------------------------------------------- per-subsequence frequency (mixed groups)

# 子序列真实频率覆盖表（裁决 §5：分组条目按子序列真实频率判定；
# key=分组id，value={子序列key: freq}；未列出的子序列再走间隔推断）。
SUBSEQ_FREQ = {
    "timing_valuation": {
        "hs300_pe_percentile": "daily",
        "hs300_pb_percentile": "daily",
        "buffett_ratio": "daily",
        "break_net_rate": "daily",
    },
    "timing_liquidity": {
        "social_financing_trend": "monthly",
        "interest_rate": "monthly",        # 文件以月末锚点存储(利率快照)，40/55
        "m1_m2_scissors": "monthly",
        "fed_policy": "monthly",
    },
    "timing_equity_bond": {
        "hs300_erp": "daily",
        "dividend_bond_spread_red": "daily",
        "dividend_bond_spread_hs300": "daily",
        "cn_us_spread": "monthly",
    },
    "timing_capital_flow": {
        "margin_ratio": "monthly",
        "equity_fund_position": "daily",
        "flexible_fund_position": "daily",
        "new_fund": "monthly",
        "industrial_capital": "monthly",
        "fiscal_expenditure": "monthly",
    },
    "timing_sentiment": {
        "fund_3y_annual": "weekly",        # §4：3年年化按周频 9/16
        "margin_buying_ratio": "monthly",
        "new_account_opening": "monthly",
    },
    "timing_micro_structure": {
        "industry_concentration": "weekly",
        "concentration_trend": "weekly",
        "csi1000_hs300_ratio": "weekly",
    },
}

_DAILY_D, _DAILY_W = 2, 5
_WEEKLY_D, _WEEKLY_W = 9, 16
_MONTH_DAYS_D, _MONTH_DAYS_W = 40, 55
_QUARTER_DAYS_D, _QUARTER_DAYS_W = 100, 135

# 分组特例：股基/灵基仓位 4/7（§4 直接照填）
def _group_special(item_id, seg):
    if item_id == "timing_capital_flow" and seg in ("equity_fund_position", "flexible_fund_position"):
        return ("daily", 4, 7)
    return None


def infer_subseq_freq(history: list) -> str:
    """
    从最近若干点的日期自然日间隔中位数推断子序列频率：
      <=10 天 daily；<=24 天 weekly；<=75 天 monthly；否则 quarterly。
    月锚点序列（'YYYY-MM'）间隔按整月天数计。
    """
    import datetime as dt
    parsed = []
    for el in history[-13:]:
        if isinstance(el, dict) and el.get("date"):
            try:
                k = date_key(el["date"])
                parsed.append(k)
            except Exception:
                pass
    if len(parsed) < 2:
        return "daily"
    gaps = []
    for a, b in zip(parsed, parsed[1:]):
        # 同为月/季锚点（无 day）时直接按周期差给标准间隔
        if not a[2] and not b[2] and a[1] and b[1]:
            months = (b[0] - a[0]) * 12 + (b[1] - a[1])
            gaps.append(months * 30)
            continue
        d0 = dt.date(a[0], max(a[1], 1), max(a[2], 1))
        d1 = dt.date(b[0], max(b[1], 1), max(b[2], 1))
        gaps.append((d1 - d0).days)
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return "daily"
    med = sorted(gaps)[len(gaps) // 2]
    if med <= 10:
        return "daily"
    if med <= 24:
        return "weekly"
    if med <= 75:
        return "monthly"
    return "quarterly"


def period_end_date(key: tuple) -> tuple:
    """月/季锚点映射到周期末日：2026-08 -> (2026,8,31)；季度 2026Q2 -> (2026,6,30)。"""
    import datetime as dt
    y, m, d = key
    if d:
        return key
    if m:
        import calendar as _cal
        last = _cal.monthrange(y, m)[1]
        return (y, m, last)
    return (y, 12, 31)


def subseq_status(item_id, history: list, eval_key: tuple, cal: str,
                  group_freq: str, seg_name: str = "",
                  explicit_freq: str | None = None) -> str:
    """单个子序列：按自身频率 + 阈值判定。"""
    seg_freq = explicit_freq or SUBSEQ_FREQ.get(item_id, {}).get(seg_name)
    if seg_freq is None:
        # 未登记真实频率的子序列：按分组条目 freq 判定（不从数据自动推断，
        # 与裁决「发布日/频率维护走人工小版本」一致）
        seg_freq = group_freq if group_freq in VALID_FREQ else "daily"
    last_el = history[-1]
    as_of = date_key(last_el.get("date") if isinstance(last_el, dict) else last_el)

    special = _group_special(item_id, "")
    if seg_freq in ("daily", "weekly"):
        if special:
            f, td_d, td_w = special
        elif item_id == "timing_micro_structure":
            f, td_d, td_w = "weekly", 14, 21
        elif seg_freq == "daily":
            f, td_d, td_w = "daily", _DAILY_D, _DAILY_W
        else:
            f, td_d, td_w = "weekly", _WEEKLY_D, _WEEKLY_W
        td = trading_days_between(as_of, eval_key, cal)
        if td <= td_d:
            return "FRESH"
        if td <= td_w:
            return "WATCH"
        return "STALE"

    # 月/季：自然日兜底（锚点周期末日）
    anchor = period_end_date(as_of)
    import datetime as dt
    d0 = dt.date(anchor[0], anchor[1], anchor[2])
    d1 = dt.date(eval_key[0], eval_key[1], eval_key[2])
    days = (d1 - d0).days
    if seg_freq == "monthly":
        fd, wd = _MONTH_DAYS_D, _MONTH_DAYS_W
    else:
        fd, wd = _QUARTER_DAYS_D, _QUARTER_DAYS_W
    if days <= fd:
        return "FRESH"
    if days <= wd:
        return "WATCH"
    return "STALE"


def compute_status(item: dict, doc: Any, eval_key: tuple, default_cal: str) -> str:
    """
    返回 FRESH / WATCH / STALE / GAP。
    - 非分组条目（date_path 不含 `*`）：直接按条目阈值判定；
    - 分组条目：对每个可达 history 子序列按自身频率分别判定，取最差状态。
    """
    cal = item.get("calendar", default_cal)
    date_path = item["date_path"]

    if "*" not in (date_path or ""):
        try:
            raw = deep_get(doc, date_path, default=_MISSING)
        except Exception:
            return "GAP"
        if raw is _MISSING or raw is None:
            return "GAP"
        as_of_raw = raw
        if isinstance(raw, list):
            if not raw:
                return "GAP"
            last = raw[-1]
            as_of_raw = last.get("date") if isinstance(last, dict) else last
        try:
            as_of = date_key(as_of_raw)
        except Exception:
            return "GAP"
        return _single_status(item, as_of, eval_key)

    # grouped: expand date_path (points to per-subseq history arrays)
    worst = None
    order = {"FRESH": 0, "WATCH": 1, "STALE": 2}
    found = False
    for p in expand_paths(doc, date_path):
        h = deep_get(doc, p, default=None)
        if not isinstance(h, list) or not h:
            continue  # 无 history 的占位子序列跳过（裁决 §5）
        found = True
        seg_name = p.split(".")[-2] if p.endswith(".history") else p.split(".")[-1]
        st = subseq_status(item["id"], h, eval_key, cal, item["freq"],
                           seg_name=seg_name)
        if worst is None or order[st] > order[worst]:
            worst = st
    if not found:
        return "GAP"
    return worst or "GAP"


def _single_status(item: dict, as_of: tuple, eval_key: tuple) -> str:
    freq = item["freq"]
    cal = item.get("calendar", "CN")
    if freq in ("daily", "weekly"):
        td = trading_days_between(as_of, eval_key, cal)
        if td <= item["fresh_max_trading_days"]:
            return "FRESH"
        if td <= item["watch_max_trading_days"]:
            return "WATCH"
        return "STALE"
    if "expected_publish_day" in item:
        return _calendar_mode_status(item, as_of, eval_key)
    anchor = period_end_date(as_of) if not as_of[2] else as_of
    import datetime as dt
    d0 = dt.date(anchor[0], anchor[1], anchor[2])
    d1 = dt.date(eval_key[0], eval_key[1], eval_key[2])
    days = (d1 - d0).days
    if days <= item["fresh_max_days"]:
        return "FRESH"
    if days <= item["watch_max_days"]:
        return "WATCH"
    return "STALE"


def _calendar_mode_status(item: dict, as_of: tuple, eval_key: tuple) -> str:
    """
    发布日历模式。约定：周期 n 的数据在「周期 n+1 的 expected_publish_day」发布
      月频：8 月数据 -> 9 月 ep_day 发布
      季频：Q2 数据 -> Q3 首月 ep_day 发布（即 7 月 ep_day）
    判定逻辑：
      1. 计算 eval 时刻的 due_period（应已发布的最新数据周期）
      2. 计算 as_of 的数据周期
      3. as_of >= due → FRESH（数据已到位）
      4. as_of < due → 先查硬线（missing >= hard → STALE），再按时间窗口：
         delta <= tol → FRESH（宽限）; <= watch → WATCH; > watch → STALE
    """
    import datetime as dt
    freq = item["freq"]
    ep_day = item["expected_publish_day"]
    tol = item["publish_tolerance_days"]
    watch = item["watch_publish_delay_days"]
    hard = item.get("stale_max_missing_periods")
    is_quarterly = (freq == "quarterly")

    ey, em, ed = eval_key[0], eval_key[1], eval_key[2]
    eval_date = dt.date(ey, em, ed)

    # ---------- 当前周期的发布日 ----------
    if is_quarterly:
        q_idx = (em - 1) // 3
        q_start_month = q_idx * 3 + 1
        pub = dt.date(ey, q_start_month, min(ep_day, 28))
        current_period = ey * 4 + q_idx
    else:
        pub = dt.date(ey, em, min(ep_day, 28))
        current_period = ey * 12 + (em - 1)

    # ---------- due_period：eval 时刻应已发布的最新周期 ----------
    if eval_date < pub:
        due_period = current_period - 2
    else:
        due_period = current_period - 1

    # ---------- as_of 的周期索引 ----------
    ay, am = as_of[0], max(as_of[1], 1)
    if is_quarterly:
        as_of_period = ay * 4 + (am - 1) // 3
    else:
        as_of_period = ay * 12 + (am - 1)

    # ---------- 核心判定 ----------
    if as_of_period >= due_period:
        return "FRESH"

    # 数据没到位 → 缺期硬线
    missing = due_period - as_of_period
    if isinstance(hard, int) and missing >= hard:
        return "STALE"

    # 时间窗口判定
    delta = max((eval_date - pub).days, 0)
    if delta <= tol:
        return "FRESH"
    elif delta <= watch:
        return "WATCH"
    else:
        return "STALE"


# ---------------------------------------------------------------- structural checks

def check_structure(item: dict, cur_doc: Any, base_doc: Any | None) -> list[str]:
    """三类 BLOCK 检查；`*` 路径逐子序列检查。返回 BLOCK 消息列表。"""
    blocks: list[str] = []
    iid = item["id"]
    tol = item.get("history_count_tolerance", 0.05)
    hp = item.get("history_path")
    vp = item.get("value_path")

    cur_hist_paths = expand_paths(cur_doc, hp) if hp else []
    cur_val_paths = expand_paths(cur_doc, vp) if vp else []

    # --- 仅当前侧：history 空检查（逐子序列；找不到子序列对象不算空，按 GAP 另计）
    cur_hists: dict[str, list] = {}
    for p in cur_hist_paths:
        h = deep_get(cur_doc, p, default=None)
        if isinstance(h, list):
            cur_hists[p] = h
    for p, h in cur_hists.items():
        if len(h) == 0:
            blocks.append(f"BLOCK {iid}: history 为空 ({p})")

    # --- 日期 / 点数 / null：需要基线
    if base_doc is None:
        return blocks

    base_hist_paths = expand_paths(base_doc, hp) if hp else []
    base_val_paths = expand_paths(base_doc, vp) if vp else []

    # 1) 日期倒退：date_path 逐子序列（能配对的路径）
    date_pairs = _pair_paths(
        expand_paths(cur_doc, item["date_path"]),
        expand_paths(base_doc, item["date_path"]),
        item["date_path"], item["date_path"],
    )
    for cp, bp in date_pairs:
        try:
            cv = deep_get(cur_doc, cp, default=None)
            bv = deep_get(base_doc, bp, default=None)
            if isinstance(cv, list):
                cv = cv[-1].get("date") if cv and isinstance(cv[-1], dict) else None
            if isinstance(bv, list):
                bv = bv[-1].get("date") if bv and isinstance(bv[-1], dict) else None
            if cv is None or bv is None:
                continue
            ck, bk = date_key(cv), date_key(bv)
            if ck < bk:
                blocks.append(
                    f"BLOCK {iid}: date regressed {fmt_key(bk)} -> {fmt_key(ck)} ({cp})")
        except Exception:
            continue

    # 2) 点数倒退 > tol
    hist_pairs = _pair_paths(cur_hist_paths, base_hist_paths, hp, hp)
    for cp, bp in hist_pairs:
        ch = deep_get(cur_doc, cp, default=None)
        bh = deep_get(base_doc, bp, default=None)
        if not isinstance(ch, list) or not isinstance(bh, list):
            continue
        old_n, new_n = len(bh), len(ch)
        if old_n > 0 and (old_n - new_n) / old_n > tol + 1e-9:
            pct = (old_n - new_n) / old_n * 100
            blocks.append(
                f"BLOCK {iid}: history points {old_n} -> {new_n} (−{pct:.1f}%) ({cp})")

    # 3) 有值变 null
    val_pairs = _pair_paths(cur_val_paths, base_val_paths, vp, vp)
    for cp, bp in val_pairs:
        bv = deep_get(base_doc, bp, default=None)
        cv = deep_get(cur_doc, cp, default=None)
        if bv is not None and cv is None:
            blocks.append(
                f"BLOCK {iid}: value went null (was {bv}) ({cp})")
    return blocks


def _wild_key(path: str, template: str) -> str:
    """取展开路径中 `*` 对应的子序列 key（跨 cur/base 配对用）。"""
    tparts = template.split(".")
    pparts = path.split(".")
    if "*" in tparts:
        i = tparts.index("*")
        if i < len(pparts):
            return pparts[i]
    return path


def _pair_paths(cur: list[str], base: list[str],
                cur_template: str = "", base_template: str = ""):
    """
    配对当前/基线展开路径：完整路径相同直接配对（非通配）；
    否则按模板 `*` 位置对应的子序列 key 配对。
    """
    common = set(cur) & set(base)
    pairs = [(p, p) for p in sorted(common)]
    cur_rest = [p for p in cur if p not in common]
    base_rest = [p for p in base if p not in common]
    ct = cur_template if (cur_template and "*" in cur_template) else ""
    bt = base_template if (base_template and "*" in base_template) else ""
    cur_map = {_wild_key(p, ct): p for p in cur_rest}
    base_map = {_wild_key(p, bt): p for p in base_rest}
    for key in sorted(set(cur_map) & set(base_map)):
        pairs.append((cur_map[key], base_map[key]))
    return pairs


# ---------------------------------------------------------------- baseline fetch

def api_fetch_file(rel_file: str, ref: str, timeout: int = 45,
                   retries: int = 2) -> Any | None:
    """GitHub Contents API 拉文件；大文件偶发 IncompleteRead，重试后失败则报错。"""
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        url = f"{REPO_API}/contents/{rel_file}?ref={ref}"
        req = urllib.request.Request(url)
        if GITHUB_TOKEN:
            req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw)
            content = base64.b64decode(payload["content"])
            return json.loads(content.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_exc = e
        except Exception as e:  # IncompleteRead / timeout
            last_exc = e
    raise RuntimeError(str(last_exc))


def git_fetch_file(rel_file: str, ref: str, cwd: Path) -> Any | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:{rel_file}"], cwd=cwd,
            stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- main

def evaluate(args: argparse.Namespace) -> int:
    manifest = load_json(Path(args.manifest).
                         resolve() if not Path(args.manifest).is_absolute()
                         else Path(args.manifest))
    validate_manifest(manifest)

    current_root = Path(args.current_root).resolve()
    eval_date = args.eval_date or _today_str()
    eval_key = date_key(eval_date)

    blocks: list[str] = []
    warnings: list[str] = []
    status_lines: list[str] = []
    n_crit = n_warn = n_dep = n_skipped = 0
    baseline_cache: dict[str, Any | None] = {}

    for item in manifest["items"]:
        iid = item["id"]
        if item.get("status") == "deprecated":
            n_dep += 1
            status_lines.append(f"  · {iid:26s} DEPRECATED（不评估）")
            continue

        rel_file = item["file"]
        cur_path = current_root / rel_file
        if not cur_path.exists():
            status = "GAP"
            cur_doc = None
        else:
            cur_doc = load_json(cur_path)
            try:
                status = compute_status(item, cur_doc, eval_key,
                                        manifest["calendar"])
            except Exception as exc:
                status = "GAP"
                warnings.append(f"{iid}: 状态计算异常 {exc}")

        tag = "C" if item["critical"] else "W"
        status_lines.append(f"  · {iid:26s} [{tag}] {status:6s} {rel_file}")

        # 基线（结构回归）
        if cur_doc is not None:
            if args.baseline_root:
                bp = Path(args.baseline_root).resolve() / rel_file
                base_doc = load_json(bp) if bp.exists() else None
            else:
                if rel_file not in baseline_cache:
                    try:
                        if args.baseline_api:
                            base_doc = api_fetch_file(rel_file, args.baseline_ref)
                        else:
                            base_doc = git_fetch_file(rel_file, args.baseline_ref,
                                                      PROJECT_ROOT)
                    except Exception as exc:
                        warnings.append(
                            f"{iid}: 基线拉取失败（{args.baseline_ref}）: {exc}；"
                            f"结构检查跳过")
                        base_doc = _BASELINE_ERROR
                    baseline_cache[rel_file] = base_doc
                base_doc = baseline_cache[rel_file]
                if base_doc is _BASELINE_ERROR:
                    base_doc = None
                    n_skipped += 1
            item_blocks = check_structure(item, cur_doc, base_doc)
        else:
            item_blocks = []

        if item["critical"]:
            n_crit += 1
            if item_blocks:
                blocks.extend(item_blocks)
            if status in ("GAP", "STALE") and item["severity_on_gap"] == "block":
                blocks.append(
                    f"BLOCK {iid}: {status}（last business date 不满足阈值；{rel_file}）")
        else:
            n_warn += 1
            if item_blocks:
                warnings.extend(item_blocks)
            if status in ("GAP", "STALE", "WATCH"):
                warnings.append(f"{iid}: {status}（{rel_file}，不阻断）")

    print("[freshness-gate] 新鲜度状态（评估日 %s，基线 %s）："
          % (eval_date, args.baseline_ref))
    for line in status_lines:
        print(line)

    if warnings:
        print("\n[freshness-gate] WARN（仅记录，不阻断）：")
        for w in warnings:
            print(f"  ⚠ {w}")

    if blocks:
        print(f"\n[freshness-gate] FAIL：{n_crit} critical / {n_warn} warn / "
              f"{n_dep} deprecated；{len(blocks)} 个阻断：", file=sys.stderr)
        for b in blocks:
            print(f"  🚫 {b}", file=sys.stderr)
        return 1

    print(f"\n[freshness-gate] PASS：{n_crit} critical 全通过；"
          f"{n_warn} warn / {n_dep} deprecated；{n_skipped} 项基线不可用已跳过结构检查")
    return 0


_BASELINE_ERROR = object()


def _today_str() -> str:
    import datetime as dt
    return dt.date.today().isoformat()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Freshness & structural regression gate")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--current-root", default=str(PROJECT_ROOT))
    p.add_argument("--baseline-root", default=None,
                   help="本地基线目录（测试用）；优先于 api/git")
    p.add_argument("--baseline-ref", default="origin/main",
                   help="基线引用，默认 origin/main")
    p.add_argument("--baseline-api", action="store_true", default=True,
                   help="基线走 GitHub Contents API（默认）")
    p.add_argument("--baseline-git", dest="baseline_api", action="store_false",
                   help="基线走 git show（CI 用）")
    p.add_argument("--eval-date", default=None,
                   help="评估日 YYYY-MM-DD，默认今天")
    return p.parse_args()


if __name__ == "__main__":
    try:
        rc = evaluate(parse_args())
    except Exception as exc:
        print(f"[freshness-gate] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(rc)
