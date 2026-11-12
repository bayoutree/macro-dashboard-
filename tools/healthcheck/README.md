# 月度数据健康巡检（制度 v1 执行体）

`monthly_audit.py` 是「看板月度数据健康巡检制度 v1」的代码侧执行体，三层判定，产物即证据。

| 层 | 回答什么问题 | 依赖 |
|---|---|---|
| L1 数据层 | 每个指标还活着吗？值是脏的吗？ | 离线，读 `data/freshness_manifest.json` |
| L2 发布层 | 线上页面拿到的数据是仓库最新的吗？ | 网络（GitHub Pages） |
| L3 呈现层 | 页面**实际显示**的值等于源值吗？徽标是真的吗？ | playwright + chromium |

## 跑法

```bash
# 全量（线上真实页面，月度巡检标准动作）
python3 tools/healthcheck/monthly_audit.py --strict

# 只跑数据层（无网络环境）
python3 tools/healthcheck/monthly_audit.py --offline --strict

# 发布前预检（打本地静态服务，不依赖 Pages 部署完成）
python3 tools/healthcheck/monthly_audit.py --serve-local --strict

# 指定评估日（复盘历史某天）
python3 tools/healthcheck/monthly_audit.py --eval-date 2026-10-31 --strict
```

退出码：`0` 无 FAIL / `1` 有 FAIL（阻断）/ `2` 运行错误。
产物：`tools/healthcheck/output/monthly_audit_<时间戳>.json|.md` + `screenshots/*.png`。
留档：把当次产物复制到 `tools/healthcheck/evidence/<YYYY-MM-DD>/` 并提交，报告内附 commit hash。

## 判定口径

- **PASS** 通过；**WARN** 需跟踪（落后 1 个周期、占位符、裸露标识符等）；**FAIL** 阻断；**SKIP** 不适用（deprecated / 无浏览器），SKIP 不算通过。
- 月频/季频用**期次口径**判定：以"按 lag 应已发布的最新期"为基准，而不是自然日差——避免 `2026-09-01` 这类期初日期表示导致系统性误报一个月。
- 徽标失真分两级：落后 1 个周期 = WARN（发布窗口/口径边界，需人工复核）；超 2 个周期仍标「✅最新」= FAIL。

## 登记表（新增卡片/数据文件必填）

- `render_registry.json`：呈现层断言，卡片 DOM id → 源文件 + JSON 路径 + 容差。未登记卡片不计覆盖率。
- `file_tiers.json`：数据文件分级。Tier A 在 `freshness_manifest.json`；Tier B 中间产物（验存在+可解析）；Tier C 回测/日志/归档。未登记文件 → `HC-D-REG` FAIL（strict）。

## 与 `scripts/monthly_consistency_check.py` 的关系

后者查"仓库内静态一致性"（双副本、cache-buster 版本号、引用存在性）；本脚本查"运行期真实链路"（数据新鲜度、线上发布、页面显示值）。两者互补，月度巡检都要跑。
