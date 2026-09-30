#!/usr/bin/env python3
"""
freshness_gate.py — 数据新鲜度/结构回归硬门禁

职责（v1.0 最小闭环）：
  1. 拦截关键序列最后业务日期倒退；
  2. 拦截关键 history 为空或点数少于上一版本；
  3. 拦截关键字段从有值变为 null。

默认基线为 git HEAD（每日 workflow 在生成新数据后、commit 前调用）。
本脚本不做插值、不修复数据，只验证并输出可复核的错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "freshness_manifest.json"

_MISSING = object()
_REQUIRED_TOP_KEYS = ("version", "updated", "calendar", "items")
_REQUIRED_ITEM_KEYS = (
    "id", "file", "date_path", "freq", "lag_days",
    "critical", "severity_on_gap",
)
_VALID_FREQ = {"daily", "weekly", "monthly", "quarterly", "special"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是 object: {path}")
    return data


def resolve(obj: Any, path: str | None, default: Any = _MISSING) -> Any:
    """按点号取嵌套字段；空路径返回对象本身。"""
    if path is None or path == "":
        return obj
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            if default is not _MISSING:
                return default
            raise KeyError(path)
        cur = cur[part]
    return cur


def date_key(value: Any) -> tuple[int, int, int]:
    """把 YYYY-MM-DD / YYYY-MM / YYYY 转为可比较元组。"""
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        raise ValueError(f"无法解析业务日期: {value!r}")
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    day = int(m.group(3) or 1)
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(f"非法业务日期: {value!r}")
    return year, month, day


def validate_manifest(manifest: dict[str, Any]) -> None:
    errors: list[str] = []
    for key in _REQUIRED_TOP_KEYS:
        if key not in manifest:
            errors.append(f"manifest 缺少顶层字段 {key}")
    if errors:
        raise ValueError("; ".join(errors))

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest.items 必须是非空数组")

    seen: set[str] = set()
    for i, item in enumerate(items):
        prefix = f"items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须是 object")
            continue
        for key in _REQUIRED_ITEM_KEYS:
            if key not in item:
                errors.append(f"{prefix}({item.get('id', i)}) 缺少字段 {key}")
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in seen:
                errors.append(f"{prefix} id 重复: {item_id}")
            seen.add(item_id)
        if item.get("freq") not in _VALID_FREQ:
            errors.append(f"{prefix}({item_id}) freq 非法: {item.get('freq')}")
        if not isinstance(item.get("critical"), bool):
            errors.append(f"{prefix}({item_id}) critical 必须是布尔值")
        if item.get("severity_on_gap") not in {"block", "warn", "report"}:
            errors.append(f"{prefix}({item_id}) severity_on_gap 非法")
        if not isinstance(item.get("lag_days"), int) or item.get("lag_days") < 0:
            errors.append(f"{prefix}({item_id}) lag_days 必须是非负整数")
        has_fresh_threshold = any(
            key in item
            for key in (
                "fresh_max_trading_days",
                "fresh_max_days",
                "fresh_after_period_end_days",
                "expected_publish_day",
            )
        )
        if not has_fresh_threshold:
            errors.append(f"{prefix}({item_id}) 缺少 fresh 阈值字段")
    if errors:
        raise ValueError("; ".join(errors))


def baseline_document(file: str, baseline_ref: str, repo_root: Path) -> dict[str, Any] | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{baseline_ref}:{file}"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


def get_node(doc: dict[str, Any], item: dict[str, Any]) -> Any:
    return resolve(doc, item.get("node_path"))


def get_history(node: dict[str, Any], item: dict[str, Any]) -> list[Any]:
    history = resolve(node, item.get("history_path", "history"), default=None)
    if history is None:
        return []
    if not isinstance(history, list):
        raise ValueError("history 必须是数组")
    return history


def get_date(node: dict[str, Any], item: dict[str, Any], history: list[Any]) -> Any:
    date_path = item.get("date_path")
    # 对没有独立 date 字段、仅在 history 末点记录业务日期的结构提供稳定锚点。
    if date_path == "history.last.date":
        if not history or not isinstance(history[-1], dict) or history[-1].get("date") is None:
            raise KeyError(date_path)
        return history[-1]["date"]
    date_value = resolve(node, date_path, default=_MISSING)
    if date_value is not _MISSING:
        return date_value
    if history and isinstance(history[-1], dict) and history[-1].get("date") is not None:
        return history[-1]["date"]
    raise KeyError(date_path)


def get_value(node: dict[str, Any], item: dict[str, Any]) -> Any:
    if "value_path" not in item:
        return resolve(node, "value", default=None)
    return resolve(node, item.get("value_path"), default=None)


def evaluate(args: argparse.Namespace) -> list[str]:
    current_root = Path(args.current_root).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = load_json(manifest_path)
    validate_manifest(manifest)

    errors: list[str] = []
    checked = 0
    skipped_no_baseline = 0
    baseline_cache: dict[str, dict[str, Any] | None] = {}

    for item in manifest["items"]:
        if not item.get("critical") or item.get("severity_on_gap") != "block":
            continue
        item_id = item["id"]
        rel_file = item["file"]
        current_path = current_root / rel_file

        try:
            current_doc = load_json(current_path)
            current_node = get_node(current_doc, item)
            if not isinstance(current_node, dict):
                raise ValueError("指标节点缺失或不是 object")
            current_history = get_history(current_node, item)
            current_date = get_date(current_node, item, current_history)
            date_key(current_date)
            current_value = get_value(current_node, item)
        except Exception as exc:
            errors.append(f"BLOCK {item_id}: 当前文件无法通过结构校验（{rel_file}）: {exc}")
            continue

        if not current_history:
            errors.append(f"BLOCK {item_id}: history 为空（{rel_file}）")

        if args.baseline_root:
            baseline_path = Path(args.baseline_root).resolve() / rel_file
            try:
                baseline_doc = load_json(baseline_path)
            except FileNotFoundError:
                baseline_doc = None
            except Exception:
                baseline_doc = None
        else:
            if rel_file not in baseline_cache:
                baseline_cache[rel_file] = baseline_document(
                    rel_file, args.baseline_ref, PROJECT_ROOT
                )
            baseline_doc = baseline_cache[rel_file]

        if baseline_doc is None:
            skipped_no_baseline += 1
            checked += 1
            continue

        try:
            baseline_node = get_node(baseline_doc, item)
            if not isinstance(baseline_node, dict):
                # 基线没有该节点属于新增/恢复，不阻断。
                checked += 1
                continue
            baseline_history = get_history(baseline_node, item)
            baseline_date = get_date(baseline_node, item, baseline_history)
            baseline_value = get_value(baseline_node, item)

            if baseline_history and len(current_history) < len(baseline_history):
                errors.append(
                    f"BLOCK {item_id}: history 点数倒退 "
                    f"{len(baseline_history)} -> {len(current_history)}"
                )

            if date_key(current_date) < date_key(baseline_date):
                errors.append(
                    f"BLOCK {item_id}: 最后业务日期倒退 "
                    f"{baseline_date} -> {current_date}"
                )

            if baseline_value is not None and current_value is None:
                errors.append(
                    f"BLOCK {item_id}: 关键字段从有值变 null "
                    f"{baseline_value!r} -> null"
                )
        except Exception as exc:
            errors.append(f"BLOCK {item_id}: 基线结构无法比较: {exc}")

        checked += 1

    if checked == 0:
        errors.append("manifest 中没有 critical + block 条目，门禁为空")
    if errors:
        print(f"[freshness-gate] FAIL：检查 {checked} 个关键条目，发现 {len(errors)} 个阻断项",
              file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    else:
        print(f"[freshness-gate] PASS：检查 {checked} 个关键条目；"
              f"{skipped_no_baseline} 个条目无旧版本基线（新增/恢复不阻断）")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freshness structural regression gate")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help="freshness manifest 路径")
    parser.add_argument("--current-root", default=str(PROJECT_ROOT),
                        help="当前数据仓库根目录")
    parser.add_argument("--baseline-root", default=None,
                        help="可选：基线仓库根目录；不传则从 git 读取")
    parser.add_argument("--baseline-ref", default="HEAD",
                        help="git 基线引用，默认 HEAD")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        found_errors = evaluate(parse_args())
    except Exception as exc:
        print(f"[freshness-gate] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(1 if found_errors else 0)
