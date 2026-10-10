#!/usr/bin/env python3
"""
ssot_check.py — T-1/P0-3 单一真值(SSOT)跨文件一致性校验 + 单位自洽校验

职责
----
1. 读 scripts/ssot_registry.json（冻结的「指标 → 唯一主源」登记表）。
2. 对每个指标：主源取值 vs 各下游文件取值，差值超容差即判 FAIL。
3. 对 unit_rules：校验单位字符串与量级自洽（如利差标 bps 时量级必须按 ×100 换算）。
4. --fix：把主源值/日期回写到下游文件（下游只引用、不各自重算），并打 ssot_source 标记。
5. --self-test：把数据复制到临时目录，故意注入一个不一致值，验证能被拦截。

退出码：0 = 通过（无冲突）；1 = 存在冲突（构建期应据此阻断发布）。

用法：
    python3 scripts/ssot_check.py                 # 校验（CI 门禁）
    python3 scripts/ssot_check.py --fix           # 回写下游 + 再校验
    python3 scripts/ssot_check.py --self-test     # 自检（注入冲突→拦截→修复→通过）
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
REGISTRY = os.path.join(HERE, "ssot_registry.json")


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def resolve(doc, dotted_path):
    """按点号路径取叶子。返回 (parent, key, node)；路径不存在返回 (None,None,None)。"""
    keys = dotted_path.split(".")
    node = doc
    parent = None
    key = None
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None, None, None
        parent, key, node = node, k, node[k]
    return parent, key, node


def leaf_value(node):
    """从叶子取数值。叶子可以是 {'value': x} 或裸数值。"""
    if isinstance(node, dict):
        return node.get("value")
    return node


def as_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("%", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def rel_path(abs_path):
    return os.path.relpath(abs_path, REPO_ROOT)


# ----------------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------------
def check(root, registry, verbose=True):
    """返回 violations 列表。root 为仓库根（便于 self-test 指向临时目录）。"""
    tol = registry.get("tolerance", {})
    rel_tol = float(tol.get("relative", 0.01))
    abs_tol = float(tol.get("absolute", 0.05))
    violations = []

    def log(msg):
        if verbose:
            print(msg)

    log("=" * 72)
    log("SSOT 一致性校验（T-1/P0-3）")
    log("=" * 72)

    for ind in registry.get("indicators", []):
        m = ind["master"]
        m_doc = load_json(os.path.join(root, m["file"]))
        _, _, m_node = resolve(m_doc, m["path"])
        m_val = as_float(leaf_value(m_node)) if m_node is not None else None
        if m_val is None:
            violations.append(
                f"[主源缺失] {ind['id']} ({ind['name']}): 主源 {m['file']}#{m['path']} 无有效数值"
            )
            log(f"  ✗ {ind['id']:20s} 主源缺失 {m['file']}#{m['path']}")
            continue
        log(f"  · {ind['id']:20s} 主源 = {m_val} ({m['file']}#{m['path']})")
        for sec in ind.get("secondaries", []):
            s_doc = load_json(os.path.join(root, sec["file"]))
            _, _, s_node = resolve(s_doc, sec["path"])
            s_val = as_float(leaf_value(s_node)) if s_node is not None else None
            if s_val is None:
                violations.append(
                    f"[下游缺失] {ind['id']}: {sec['file']}#{sec['path']} 无有效数值"
                )
                log(f"      ✗ 下游缺失 {sec['file']}#{sec['path']}")
                continue
            diff = abs(m_val - s_val)
            allowed = max(abs_tol, rel_tol * max(abs(m_val), abs(s_val)))
            if diff > allowed:
                violations.append(
                    f"[冲突] {ind['id']} ({ind['name']}): 主源 {m_val} vs "
                    f"{sec['file']}#{sec['path']} = {s_val} (diff={diff:.4f} > {allowed:.4f})"
                )
                log(f"      ✗ 冲突 {sec['file']}#{sec['path']} = {s_val} (diff={diff:.4f})")
            else:
                log(f"      ✓ 一致 {sec['file']}#{sec['path']} = {s_val}")

    log("-" * 72)
    log("单位自洽校验")
    for rule in registry.get("unit_rules", []):
        doc = load_json(os.path.join(root, rule["file"]))
        _, _, node = resolve(doc, rule["path"])
        if node is None:
            violations.append(f"[单位] {rule['id']}: 路径 {rule['file']}#{rule['path']} 不存在")
            log(f"  ✗ {rule['id']}: 路径不存在")
            continue
        unit = node.get("unit") if isinstance(node, dict) else None
        val = as_float(leaf_value(node))
        ok = True
        if unit != rule["unit"]:
            ok = False
            violations.append(
                f"[单位] {rule['id']}: unit={unit!r} 与登记 {rule['unit']!r} 不符"
            )
        if val is None or not (rule["min_abs"] <= abs(val) <= rule["max_abs"]):
            ok = False
            violations.append(
                f"[单位] {rule['id']}: value={val} 量级不在 "
                f"[{rule['min_abs']},{rule['max_abs']}]（{rule.get('hint','')}）"
            )
        log(f"  {'✓' if ok else '✗'} {rule['id']}: unit={unit} value={val}")

    log("-" * 72)
    for ref in registry.get("frontend_refs", []):
        log(f"  ⏳ [前端待引用·{ref.get('owner','')}] {ref['file']} {ref['indicator']}: {ref.get('status','')}")

    log("=" * 72)
    if violations:
        log(f"结果：FAIL（{len(violations)} 项冲突）")
        for v in violations:
            log("  - " + v)
    else:
        log("结果：PASS（主源唯一，跨文件一致，单位自洽）")
    log("=" * 72)
    return violations


# ----------------------------------------------------------------------------
# 回写：把主源值写进下游
# ----------------------------------------------------------------------------
def fix(root, registry, verbose=True):
    changed = 0
    for ind in registry.get("indicators", []):
        m = ind["master"]
        m_doc = load_json(os.path.join(root, m["file"]))
        _, _, m_node = resolve(m_doc, m["path"])
        if m_node is None:
            continue
        m_val = leaf_value(m_node)
        m_date = m_node.get("date") if isinstance(m_node, dict) else None
        for sec in ind.get("secondaries", []):
            sec_path = os.path.join(root, sec["file"])
            s_doc = load_json(sec_path)
            parent, key, s_node = resolve(s_doc, sec["path"])
            if parent is None:
                continue
            if isinstance(s_node, dict):
                if s_node.get("value") == m_val:
                    continue
                s_node["value"] = m_val
                if m_date is not None and "date" in s_node:
                    s_node["date"] = m_date
                s_node["ssot_source"] = f"{m['file']}#{m['path']}"
            else:
                if s_node == m_val:
                    continue
                parent[key] = m_val
            save_json(sec_path, s_doc)
            changed += 1
            if verbose:
                print(f"  ↻ 回写 {sec['file']}#{sec['path']} ← 主源 {m_val}")

    # 单位自洽修复：unit 不一致 / 量级超界时按 fix 规则换算
    for rule in registry.get("unit_rules", []):
        fixspec = rule.get("fix")
        if not fixspec:
            continue
        doc_path = os.path.join(root, rule["file"])
        doc = load_json(doc_path)
        _, _, node = resolve(doc, rule["path"])
        if node is None or not isinstance(node, dict):
            continue
        val = as_float(node.get("value"))
        out_of_range = val is None or not (rule["min_abs"] <= abs(val) <= rule["max_abs"])
        unit_bad = node.get("unit") != rule["unit"]
        if not (out_of_range or unit_bad):
            continue
        scale = float(fixspec.get("scale", 1.0))
        if scale != 1.0:
            if val is not None:
                node["value"] = round(val * scale, 4)
            if fixspec.get("apply_to_history") and isinstance(node.get("history"), list):
                for h in node["history"]:
                    if isinstance(h, dict) and isinstance(h.get("value"), (int, float)):
                        h["value"] = round(h["value"] * scale, 4)
        if unit_bad:
            node["unit"] = rule["unit"]
        node["unit_note"] = fixspec.get("note", "unit/量级自洽修正")
        save_json(doc_path, doc)
        changed += 1
        if verbose:
            print(f"  ↻ 单位修正 {rule['file']}#{rule['path']} ×{scale} → unit={rule['unit']}")

    if verbose:
        print(f"回写完成，共 {changed} 处下游引用/单位已对齐主源。")
    return changed


# ----------------------------------------------------------------------------
# 自检：注入冲突 → 应被拦截 → 修复 → 应通过
# ----------------------------------------------------------------------------
def self_test():
    print("### SSOT 校验器自检（故意注入一个不一致值，验证能被拦截）###")
    registry = load_json(REGISTRY)
    tmp = tempfile.mkdtemp(prefix="ssot_selftest_")
    try:
        files = set()
        for ind in registry["indicators"]:
            files.add(ind["master"]["file"])
            for s in ind.get("secondaries", []):
                files.add(s["file"])
        for r in registry.get("unit_rules", []):
            files.add(r["file"])
        for rel in files:
            src = os.path.join(REPO_ROOT, rel)
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

        # 选一个下游叶子注入明显不一致值
        target = None
        for ind in registry["indicators"]:
            if ind.get("secondaries"):
                target = (ind["id"], ind["secondaries"][0])
                break
        assert target, "登记表里没有可注入的下游"
        ind_id, sec = target
        sec_path = os.path.join(tmp, sec["file"])
        doc = load_json(sec_path)
        parent, key, node = resolve(doc, sec["path"])
        if isinstance(node, dict):
            node["value"] = 999999.0
        else:
            parent[key] = 999999.0
        save_json(sec_path, doc)
        print(f"已向 {sec['file']}#{sec['path']} 注入 999999.0（指标 {ind_id}）")

        print("\n[1/2] 注入后运行校验，期望 FAIL ...")
        v_bad = check(tmp, registry, verbose=False)
        if not v_bad:
            print("❌ 自检失败：注入的不一致值未被拦截")
            return 1
        print(f"✅ 已拦截：{len(v_bad)} 项冲突")

        print("\n[2/2] --fix 回写后再次校验，期望 PASS ...")
        fix(tmp, registry, verbose=False)
        v_ok = check(tmp, registry, verbose=False)
        if v_ok:
            print("❌ 自检失败：回写后仍有冲突：")
            for v in v_ok:
                print("   - " + v)
            return 1
        print("✅ 回写后通过")
        print("\n### 自检 PASS：不一致值可被拦截，回写后可归零 ###")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="SSOT 跨文件一致性校验（T-1/P0-3）")
    ap.add_argument("--fix", action="store_true", help="把主源值回写到下游文件后再校验")
    ap.add_argument("--self-test", action="store_true", help="自检：注入冲突→拦截→修复→通过")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    registry = load_json(REGISTRY)
    if args.fix:
        fix(REPO_ROOT, registry)
    violations = check(REPO_ROOT, registry)
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
