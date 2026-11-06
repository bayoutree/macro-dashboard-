#!/usr/bin/env python3
"""
bump_asset_versions.py — 静态资源版本串（?v=...）自动维护工具

规则（详见 docs/ASSET_VERSIONING.md）：
- 版本 token = 资源文件内容 SHA1 的前 8 位；内容不变则 token 不变，内容一变
  token 必变，浏览器/CDN 必然重新拉取，彻底消除 max-age=600 窗口内
  新旧 JS 并存的隐患。
- 扫描仓库全部 *.html（含子目录，可传参数指定文件），将其中对 js/*.js、
  css/*.css、以及其它本地静态资源引用的 ?v=<token> 按文件实际内容重写；
  原引用无 ?v= 也会补上。相对引用一律相对 HTML 文件所在目录解析
  （修复：曾相对仓库根解析，导致子目录 HTML 的引用被跳过或错用根目录
  同名文件哈希——CIO 打回#2，2026-11-01）。
- 资源文件不存在（如外部 URL）时原样保留；HTML 无任何改动时退出码 0、
  不产生 diff。
- 迭代至不动点（2026-11-05 修复：HTML 间 iframe 引用形成依赖链——如根
  index.html 引用 macro-econ-dashboard/index.html?v=<子页hash>——单趟按序
  处理时父页先取子页旧 hash，子页随后写盘即令父页 token 过期，--check 必红。
  现按轮初快照迭代：每轮全部目标基于轮初内容重算，有变化则再轮，直至
  一轮零变化（收敛），上限 8 轮防死循环；--check 在内存中等价模拟）。

用法:
  python scripts/bump_asset_versions.py                 # 处理根目录全部 *.html
  python scripts/bump_asset_versions.py index.html ...
  python scripts/bump_asset_versions.py --check         # CI 校验模式：
                                                         不动点不一致即退出 1
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MAX_ROUNDS = 8

# 匹配 href/src 中以非协议开头的本地资源（含 ../ 相对路径），带可选 ?v=
ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)(?P<q>["'])(?P<path>(?!https?:|//|data:|javascript:|mailto:|#)[^"']+?)(?P<query>\?v=[^"']*)?(?P=q)"""
)


def token_for(rel_path: Path, snapshot: dict | None = None):
    """资源内容 SHA1 前 8 位；snapshot 提供时优先取内存内容（轮初快照语义）。"""
    key = str(rel_path)
    if snapshot is not None and key in snapshot:
        return hashlib.sha1(snapshot[key]).hexdigest()[:8]
    if not rel_path.is_file():
        return None
    return hashlib.sha1(rel_path.read_bytes()).hexdigest()[:8]


def resolve_resource(src: str, html_dir: Path) -> Path:
    # 去掉 query/hash；相对引用相对 HTML 文件所在目录解析
    clean = src.split("?", 1)[0].split("#", 1)[0]
    return (html_dir / clean).resolve()


def bump_html_content(content: str, html_path: Path, snapshot: dict | None = None):
    """纯函数：对 HTML 文本重算版本串。返回 (新文本, 变化数)。"""
    changes = 0

    def repl(m):
        nonlocal changes
        src = m.group("path")
        res = resolve_resource(src, html_path.parent)
        tok = token_for(res, snapshot)
        if tok is None:
            return m.group(0)
        old_query = m.group("query") or ""
        new_ref = f"{m.group('attr')}{m.group('q')}{src}?v={tok}{m.group('q')}"
        old_ref = f"{m.group('attr')}{m.group('q')}{src}{old_query}{m.group('q')}"
        if new_ref != old_ref:
            changes += 1
        return new_ref

    return ATTR_RE.sub(repl, content), changes


def main():
    args = sys.argv[1:]
    check_mode = "--check" in args
    args = [a for a in args if a != "--check"]

    if args:
        targets = [ROOT / a for a in args]
    else:
        targets = sorted(
            p for p in ROOT.rglob("*.html") if ".git" not in p.parts
        )

    for html in targets:
        if not html.is_file():
            print(f"✗ 文件不存在: {html}", file=sys.stderr)
            sys.exit(2)

    # 轮次迭代至不动点：contents = 当前（内存）内容；每轮以轮初快照算 token
    contents = {str(h.resolve()): h.read_bytes() for h in targets}
    total_changes = 0
    rounds = 0
    while True:
        rounds += 1
        snapshot = dict(contents)  # 轮初快照：本轮所有 token 基于同一状态
        round_changes = 0
        for html in targets:
            key = str(html.resolve())
            old_text = contents[key].decode("utf-8")
            new_text, n = bump_html_content(old_text, html, snapshot)
            if n:
                round_changes += n
                contents[key] = new_text.encode("utf-8")
                print(f"{'✗' if check_mode else '✏️ '} {html.relative_to(ROOT)}: {n} 处版本串需更新 (轮{rounds})")
        total_changes += round_changes
        if round_changes == 0:
            break
        if rounds >= MAX_ROUNDS:
            print(f"❌ {MAX_ROUNDS} 轮仍未收敛，存在循环引用或异常", file=sys.stderr)
            sys.exit(2)

    if check_mode:
        dirty = [h for h in targets
                 if contents[str(h.resolve())] != h.read_bytes()]
        if dirty:
            print(
                "\n❌ 存在未 bump 的静态资源引用（不动点不一致）。请本地运行 "
                "`python scripts/bump_asset_versions.py` 后提交。"
            )
            sys.exit(1)
        print("✅ 所有静态资源版本串与文件内容一致（不动点收敛）")
    else:
        written = 0
        for html in targets:
            key = str(html.resolve())
            if contents[key] != html.read_bytes():
                html.write_bytes(contents[key])
                written += 1
        print(f"完成，共更新 {total_changes} 处版本串（{rounds} 轮收敛，写盘 {written} 个文件）")


if __name__ == "__main__":
    main()
