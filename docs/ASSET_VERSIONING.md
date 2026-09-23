# 静态资源版本串（?v=）规范

## 为什么要有这个规范

站点通过 GitHub Pages 发布，资源响应头为 `max-age=600`。当一次发布同时改了
HTML 和 JS/CSS 时，存在一个 10 分钟窗口：部分用户拿到新 HTML + 旧缓存 JS，
或反之。2026-09-23 的全球周期事故中，新旧脚本/数据契约并存放大了故障表现。

**结论：HTML 中每一个本地静态资源引用都必须带 `?v=<token>`，且 token 必须
随文件内容自动变化。禁止手写日期型版本串（如 `?v=20260918a`）——它依赖人
记得 bump，而人会忘。**

## Token 规则

- `token = sha1(文件内容)` 的前 8 位十六进制字符；
- 内容不变 → token 不变，浏览器继续用缓存，零开销；
- 内容任意改动 → token 必变，URL 变化，浏览器/CDN 必然回源拉新版。

## 工具

`scripts/bump_asset_versions.py`：

```bash
# 提交前：把根目录所有 HTML 中的本地资源引用刷新为内容哈希
python scripts/bump_asset_versions.py

# 只处理指定文件
python scripts/bump_asset_versions.py index.html

# CI 校验：发现引用与内容不一致（有资源改了但没 bump）即失败
python scripts/bump_asset_versions.py --check
```

- 只处理根目录 `*.html` 中的**本地** `href/src` 引用；`http(s)://`、`//`、
  `data:`、`#` 等外部/特殊引用原样保留；
- 引用的资源文件不存在时原样保留（不报错、不编造 token）；
- 幂等：连续运行第二次 0 改动。

## 强制接入点

1. **每日流水线（`.github/workflows/daily_update.yml`）**：提交前自动运行
   bump 脚本，并将 `index.html` 的版本串变更一并提交。这样即使未来流水线
   开始产出 JS/CSS，版本串也不会漏 bump。
2. **人工推送**：改动 `js/`、`css/` 后，推送前必须运行 bump 脚本；建议
   结合 `scripts/pre_push_check.sh` 门禁使用。
3. **新增 HTML 入口**：放在仓库根目录即可被默认扫描；放在子目录的页面，
   在流水线/命令中显式传文件路径。

## 边界与注意事项

- 本规范只管「资源 URL 是否随内容变化」，不替代 JS 语法检查
  （`pre_push_check.sh` 的 `node --check` 仍然必须过）；
- JSON 数据文件不使用本机制，各页面按既有方式加时间戳/no-store 拉取；
- 不要把资源引用改回无版本串形式，也不要手写 token。
