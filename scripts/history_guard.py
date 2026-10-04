#!/usr/bin/env python3
"""历史序列删史防线（pre-commit 阻断门禁）。

目的
----
把「历史序列点数只增不减」从「事后告警 / 运行时回填」升级为「事前拦截」：
比对基线 ref 与当前状态（工作区或指定 ref）中所有数据 JSON 的历史数组，
若任一历史数组 **点数减少 / 被移除 / 结构被改**，则退出码非 0，**阻断提交**。
用于防「源瞬时失败导致静默删史」类 bug 复发（曾发生：indicators.json 的
china.sectors.external.export.history 两次 120 → 0）。

与 freshness_gate.py 的分工
--------------------------
- freshness_gate：判新鲜度 / GAP，只覆盖 manifest 登记条目，入库**后**跑，**非阻断**；
- history_guard ：只判历史点数单调性，覆盖**全部**数据 JSON，入库**前**跑，**阻断**。
两者互补。本守卫不判 GAP/STALE，因此可安全前置阻断，而不影响
「真实数据缺口也应入库（先 commit 再跑 freshness）」的既定策略。

用法
----
python scripts/history_guard.py [--baseline-ref HEAD] [--current-ref <ref>]
                                [--root .] [--allowlist scripts/history_guard_allowlist.json]
                                [--strict-dates]

退出码：0 = 通过；1 = 发现删史（阻断）；2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

# 受守卫的数据目录（相对仓库根）
DATA_DIRS = ("data", "macro-econ-dashboard/data", "frontend/data")


def _git(root: Path, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )


def _git_show(root: Path, ref: str, path: str):
    r = _git(root, ["show", f"{ref}:{path}"])
    if r.returncode != 0:
        return None
    return r.stdout


def _load(txt):
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def _discover_files(root: Path) -> list:
    r = _git(root, ["ls-files"])
    if r.returncode != 0:
        raise RuntimeError(f"git ls-files 失败: {r.stderr.strip()}")
    files = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.endswith(".json"):
            continue
        if any(line == d or line.startswith(d + "/") for d in DATA_DIRS):
            files.append(line)
    return sorted(set(files))


def _collect_history_arrays(obj, prefix: str = "") -> dict:
    """递归收集「列表且元素为含 date 的 dict」的历史数组，键为点分路径。"""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            out.update(_collect_history_arrays(v, sub))
    elif isinstance(obj, list):
        if obj and all(isinstance(e, dict) and "date" in e for e in obj):
            out[prefix] = obj
    return out


def _get(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _last_date(hist: list):
    for e in reversed(hist):
        if isinstance(e, dict) and e.get("date"):
            return str(e["date"])
    return None


def _match_allow(allow: list, file: str, path: str):
    for ent in allow:
        f = ent.get("file", "*")
        p = ent.get("path", "*")
        if fnmatch.fnmatch(file, f) and fnmatch.fnmatch(path, p):
            return ent
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="历史序列删史防线（pre-commit 阻断门禁）")
    ap.add_argument("--baseline-ref", default="HEAD", help="基线 ref（默认 HEAD）")
    ap.add_argument("--current-ref", default=None,
                    help="当前 ref；缺省则读工作区文件")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--allowlist", default=None,
                    help="允许缩减的白名单 JSON（默认 scripts/history_guard_allowlist.json）")
    ap.add_argument("--strict-dates", action="store_true",
                    help="末点日期倒退也视为阻断（默认仅告警）")
    ap.add_argument("--abs-tol", type=int, default=2,
                    help="绝对容差：点数减少不超过该值不告警（默认 2，吸收滚动窗口噪声）")
    ap.add_argument("--pct-tol", type=float, default=0.05,
                    help="相对容差：减少比例不超过该值不告警（默认 0.05）")
    args = ap.parse_args()
    import math

    root = Path(args.root).resolve()
    allowlist_path = Path(args.allowlist) if args.allowlist else (
        Path(__file__).resolve().parent / "history_guard_allowlist.json")
    allow = []
    if allowlist_path.exists():
        try:
            allow = json.loads(allowlist_path.read_text(encoding="utf-8")).get("allowed", [])
        except Exception as e:
            print(f"[history-guard] 白名单解析失败: {e}", file=sys.stderr)
            return 2

    try:
        files = _discover_files(root)
    except Exception as e:
        print(f"[history-guard] {e}", file=sys.stderr)
        return 2

    def _tol(n_base: int) -> int:
        # 容忍额度 = max(绝对容差, 相对容差*点数)。超过才判为删史。
        return max(args.abs_tol, int(math.ceil(args.pct_tol * n_base)))

    violations = []
    warnings = []
    allowed_hits = []
    checked = 0

    for path in files:
        base_doc = _load(_git_show(root, args.baseline_ref, path))
        if base_doc is None:
            continue  # 基线无此文件 → 无法比较（新增文件）
        if args.current_ref:
            cur_doc = _load(_git_show(root, args.current_ref, path))
        else:
            fp = root / path
            cur_doc = _load(fp.read_text(encoding="utf-8")) if fp.exists() else None

        base_hists = _collect_history_arrays(base_doc)
        cur_hists = _collect_history_arrays(cur_doc) if cur_doc is not None else {}
        if not base_hists:
            continue

        for hpath, bhist in base_hists.items():
            checked += 1
            n_base = len(bhist)
            chist = cur_hists.get(hpath)
            if chist is None:
                cur_raw = _get(cur_doc, hpath) if cur_doc is not None else None
                if cur_raw is None:
                    msg = f"REMOVED  {path}#{hpath}  (基线 {n_base} 点 → 已移除)"
                elif isinstance(cur_raw, list):
                    msg = f"EMPTIED  {path}#{hpath}  (基线 {n_base} 点 → 空数组)"
                else:
                    msg = f"TYPE_CHG {path}#{hpath}  (基线 {n_base} 点 → {type(cur_raw).__name__})"
                if n_base <= _tol(n_base):
                    warnings.append(msg)          # 极小序列(<=容差)的移除/清空，仅告警
                elif _match_allow(allow, path, hpath):
                    allowed_hits.append(msg)
                else:
                    violations.append(msg)
                continue

            n_cur = len(chist)
            if n_cur < n_base:
                msg = f"SHRUNK   {path}#{hpath}  ({n_base} → {n_cur} 点, -{n_base - n_cur})"
                if (n_base - n_cur) <= _tol(n_base):
                    warnings.append(msg)          # 容差内波动，仅告警
                elif _match_allow(allow, path, hpath):
                    allowed_hits.append(msg)
                else:
                    violations.append(msg)

            ld_b, ld_c = _last_date(bhist), _last_date(chist)
            if ld_b and ld_c and ld_c < ld_b:
                dmsg = f"DATE_BACK {path}#{hpath}  (末点 {ld_b} → {ld_c})"
                if args.strict_dates and not _match_allow(allow, path, hpath):
                    violations.append(dmsg)
                else:
                    warnings.append(dmsg)

    print(f"[history-guard] 基线={args.baseline_ref} 当前={args.current_ref or '工作区'}；"
          f"扫描 {len(files)} 个数据文件 / {checked} 个历史数组")
    for w in warnings:
        print(f"  ⚠ {w}")
    for a in allowed_hits:
        print(f"  · 白名单豁免 {a}")
    if violations:
        print(f"[history-guard] FAIL：{len(violations)} 处历史序列被缩减/移除 —— 阻断提交：")
        for v in violations:
            print(f"  🚫 {v}")
        print("[history-guard] 若确属合理缩减，请在 scripts/history_guard_allowlist.json 登记原因。")
        return 1
    print("[history-guard] PASS：无历史序列点数减少/移除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
