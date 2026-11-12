#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monthly_consistency_check.py — 看板月度巡检 L2 层：数据一致性门禁

背景：数据端正确 ≠ 页面是新的。最常见故障是「双副本不同步」与「缓存版本号未 bump」，
二者在数据层门禁里都看不出来。

检查项：
  A. data/*.json 与 frontend/data/*.json 逐文件内容哈希一致（页面实际读的是后者）
  B. index.html 中 js/*.js?v=xxx 的 xxx == 该文件内容 sha1 前 8 位
  C. 前端 js/html 中引用的 data 文件真实存在
  D. 各文件「业务日期」概览（供人工对照 L1，不参与判定）

用法：
  python3 scripts/monthly_consistency_check.py [--repo DIR] [--json OUT.json]
退出码：0=全通过，1=存在 FAIL
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SKIP_EXT = {".png", ".jpg", ".zip", ".gz", ".bat", ".sh"}
DATA_REF_RE = re.compile(r"[\"'(]((?:\.\./)*(?:frontend/)?data/[A-Za-z0-9_\-]+\.json)")
VER_RE = re.compile(r"(js/[A-Za-z0-9_\-]+\.js)\?v=([0-9a-zA-Z]+)")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha1_8(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:8]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", default=None, help="报告输出路径")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    report = {"repo": str(repo), "checks": [], "pass": True, "fail": 0, "warn": 0}

    def check(label: str, ok: bool, detail: str = ""):
        report["checks"].append({"label": label, "ok": bool(ok), "detail": detail})
        if not ok:
            report["pass"] = False
            report["fail"] += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))

    def warn(label: str, detail: str = ""):
        report["checks"].append({"label": label, "ok": True, "warn": True, "detail": detail})
        report["warn"] += 1
        print(f"  [WARN] {label}  {detail}")

    # ---------- A. 双副本一致性 ----------
    print("[A] data/ ↔ frontend/data/ 内容一致性（页面读的是 frontend/data）")
    d1, d2 = repo / "data", repo / "frontend" / "data"
    if not d2.is_dir():
        warn("frontend/data 目录不存在，跳过 A", str(d2))
    else:
        f1 = {f.name for f in d1.glob("*.json")}
        f2 = {f.name for f in d2.glob("*.json")}
        for miss in sorted(f1 - f2):
            check(f"frontend/data 缺少 {miss}", False, "源有副本无，页面将取不到")
        for extra in sorted(f2 - f1):
            warn(f"frontend/data 多出 {extra}", "源无副本有，可能是遗留文件")
        diff = []
        for name in sorted(f1 & f2):
            if sha256(d1 / name) != sha256(d2 / name):
                diff.append(name)
        check("双副本逐文件哈希一致", not diff, f"不一致 {len(diff)} 个: {diff[:8]}" if diff else f"{len(f1 & f2)} 个文件全部一致")

    # ---------- B. 缓存版本号 ----------
    print("[B] index.html ?v= 与 js 文件内容 sha1 一致性（防浏览器缓存旧代码）")
    idx = repo / "index.html"
    if not idx.is_file():
        check("index.html 存在", False, str(idx))
    else:
        html = idx.read_text(encoding="utf-8", errors="ignore")
        pairs = VER_RE.findall(html)
        if not pairs:
            warn("index.html 中未发现 ?v= 版本号", "可能未启用缓存击穿机制")
        bad = []
        for rel, ver in pairs:
            f = repo / rel
            if not f.is_file():
                bad.append(f"{rel} 文件不存在")
                continue
            if sha1_8(f) != ver:
                bad.append(f"{rel}: 声明 {ver} ≠ 实际 {sha1_8(f)}")
        check(f"{len(pairs)} 个 js 版本号与内容一致", not bad, "; ".join(bad[:5]))

    # ---------- C. 数据引用存在性 ----------
    print("[C] 前端引用的 data 文件是否真实存在")
    missing = set()
    refs = set()
    for f in list((repo / "js").glob("*.js")) + [idx]:
        if not f.is_file():
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for ref in DATA_REF_RE.findall(txt):
            norm = ref.replace("../", "")
            refs.add(norm)
            if not (repo / norm).is_file():
                missing.add(f"{f.name} → {norm}")
    check(f"{len(refs)} 个 data 引用全部存在", not missing, "; ".join(sorted(missing)[:8]))

    # ---------- D. 业务日期概览（不判定） ----------
    print("[D] 关键文件业务日期概览（供人工对照 L1，不参与判定）")
    dates = {}
    for name in ("feargreed_pointer.json", "timing_scores.json", "cycle_position_v4.json",
                 "us_macro.json", "cn_macro.json", "asset_prices.json"):
        p = d1 / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k in ("as_of", "update_date", "updated", "date"):
            if isinstance(doc, dict) and doc.get(k):
                dates[name] = f"{k}={doc[k]}"
                break
        else:
            dates[name] = "(未找到 as_of/update_date 字段)"
        print(f"    {name:28s} {dates[name]}")
    report["business_dates"] = dates

    print(f"\nL2 判定: {'PASS' if report['pass'] else 'FAIL'}  (FAIL {report['fail']} / WARN {report['warn']})")
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {a.json}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
