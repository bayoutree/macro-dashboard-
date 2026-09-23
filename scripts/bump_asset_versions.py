#!/usr/bin/env python3
"""
bump_asset_versions.py — 静态资源版本串（?v=...）自动维护工具

规则（详见 docs/ASSET_VERSIONING.md）：
- 版本 token = 资源文件内容 SHA1 的前 8 位；内容不变则 token 不变，内容一变
  token 必变，浏览器/CDN 必然重新拉取，彻底消除 max-age=600 窗口内
  新旧 JS 并存的隐患。
- 扫描仓库根目录下的 *.html（可传参数指定文件），将其中对 js/*.js、
  css/*.css、以及其它本地静态资源引用的 ?v=<token> 按文件实际内容重写；
  原引用无 ?v= 也会补上。
- 资源文件不存在（如外部 URL）时原样保留；HTML 无任何改动时退出码 0、
  不产生 diff。

用法:
  python scripts/bump_asset_versions.py                 # 处理根目录全部 *.html
  python scripts/bump_asset_versions.py index.html ...
  python scripts/bump_asset_versions.py --check         # CI 校验模式：
                                                         发现需 bump 即退出 1
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# 匹配 href/src 中以非协议开头的本地资源（含 ../ 相对路径），带可选 ?v=
ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)(?P<q>["'])(?P<path>(?!https?:|//|data:|javascript:|mailto:|#)[^"']+?)(?P<query>\?v=[^"']*)?(?P=q)"""
)


def token_for(rel_path: Path):
    if not rel_path.is_file():
        return None
    return hashlib.sha1(rel_path.read_bytes()).hexdigest()[:8]


def resolve_resource(src: str) -> Path:
    # 去掉 query/hash；HTML 在根目录，相对引用相对根目录解析
    clean = src.split("?", 1)[0].split("#", 1)[0]
    return (ROOT / clean).resolve()


def bump_html(html_path: Path):
    content = html_path.read_text(encoding="utf-8")
    changes = 0

    def repl(m):
        nonlocal changes
        src = m.group("path")
        res = resolve_resource(src)
        tok = token_for(res)
        if tok is None:
            return m.group(0)
        old_query = m.group("query") or ""
        new_ref = f"{m.group('attr')}{m.group('q')}{src}?v={tok}{m.group('q')}"
        old_ref = f"{m.group('attr')}{m.group('q')}{src}{old_query}{m.group('q')}"
        if new_ref != old_ref:
            changes += 1
        return new_ref

    new_content = ATTR_RE.sub(repl, content)
    return new_content, changes


def main():
    args = sys.argv[1:]
    check_mode = "--check" in args
    args = [a for a in args if a != "--check"]

    if args:
        targets = [ROOT / a for a in args]
    else:
        targets = sorted(ROOT.glob("*.html"))

    total_changes = 0
    for html in targets:
        if not html.is_file():
            print(f"✗ 文件不存在: {html}", file=sys.stderr)
            sys.exit(2)
        new_content, changes = bump_html(html)
        if changes:
            total_changes += changes
            print(f"{'✗' if check_mode else '✏️ '} {html.relative_to(ROOT)}: {changes} 处版本串需更新")
            if not check_mode:
                html.write_text(new_content, encoding="utf-8")

    if check_mode:
        if total_changes:
            print(
                "\n❌ 存在未 bump 的静态资源引用。请本地运行 "
                "`python scripts/bump_asset_versions.py` 后提交。"
            )
            sys.exit(1)
        print("✅ 所有静态资源版本串与文件内容一致")
    else:
        print(f"完成，共更新 {total_changes} 处版本串")


if __name__ == "__main__":
    main()
