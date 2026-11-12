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

### 分级阻断（裁决① 2026-11-12）

`--strict` **不再**把所有 STALE/GAP 一刀切判 FAIL，改按 critical 分级：

| 情形 | 判定 | 后果 |
|---|---|---|
| `critical=true` 或 `severity_on_gap=block`，且 STALE/GAP 已超发布窗口 | **FAIL** | 阻断发布 + 当月数据类交付 |
| 非 critical，STALE/GAP 超 60 天未处置 | **FAIL** | 同上（升级条款） |
| 非 critical，STALE/GAP 在 60 天内 | **WARN** | 进待处置清单，详情带"剩余 X 天后升 FAIL" |
| **G3 页面值 ≠ 源值**（任意指标，不看 critical） | **FAIL** | **一律阻断** |
| `status=deprecated` | SKIP | 不评估、不阻断，季度复评 |

理由：原一刀切实现下 3 个非 critical 指标长期红灯，制度会被绕过。分级后真实断更仍阻断，噪声进清单。

### 核心卡值断言覆盖率（裁决⑤）

`render_registry.json` 的 `core_cards` 登记 12 张核心卡（清单见定稿§5），产物 `coverage.core_cards_covered/total` 给出当期覆盖率。
目标：2026-12-06 ≥ 6/12、2027-01-09 ≥ 9/12、2027-03-12 = 12/12。未覆盖卡在报告里显式列出，不允许"没登记=没坏"。

## 乐咕依赖面探针（裁决④）

```bash
python3 tools/healthcheck/probe_legulegu_alt.py            # 需 pandas/akshare，建议在 CI 跑
python3 tools/healthcheck/probe_legulegu_alt.py --skip-akshare   # 只探中证指数替代源
```

探 akshare 的乐咕封装（`stock_index_pe_lg` / `stock_index_pb_lg`，喂中证800 ERP 主指标与沪深300 PE/PB 分位）当日可用性，以及中证指数官方估值文件的字段（有 PE/股息率，**无 PB**）。
已挂进 `monthly_audit.yml` 的 `Probe legulegu dependency` 步骤，产物随 artifact 留档；探针退出码恒 0，不做门禁。

## 登记表（新增卡片/数据文件必填）

- `render_registry.json`：呈现层断言，卡片 DOM id → 源文件 + JSON 路径 + 容差。未登记卡片不计覆盖率。
- `file_tiers.json`：数据文件分级。Tier A 在 `freshness_manifest.json`；Tier B 中间产物（验存在+可解析）；Tier C 回测/日志/归档。未登记文件 → `HC-D-REG` FAIL（strict）。

## 与 `scripts/monthly_consistency_check.py` 的关系（两入口分工，2026-11-12 定）

**不合并、不互相调用，各自独立跑，报告互相引用。**

| | `scripts/monthly_consistency_check.py`（CIO） | `tools/healthcheck/monthly_audit.py`（本脚本） |
|---|---|---|
| 定位 | **L2 静态侧**：仓库内部一致性 | **运行期三层**：真实链路端到端 |
| 查什么 | `data/` ↔ `frontend/data/` 哈希一致、cache-buster 版本号、跨文件引用存在性 | L1 数据新鲜度/脏值、L2 线上发布是否最新、L3 页面**实际显示值** vs 源值 |
| 依赖 | 纯离线，无网络无浏览器 | L2 需网络，L3 需 playwright+chromium |
| 触发 | 手工 / 日更前置 | 每月 1 日自动 + 手工 workflow_dispatch |
| 输出 | 终端 PASS/WARN + 退出码 | JSON+MD 报告 + 截图，artifact 留档 90 天 |
| 职责边界 | 静态一致性归 CIO | 运行期健康与呈现层归本脚本 |

月度巡检标准动作：**先跑本脚本（含 L3 真实页面），再跑 `monthly_consistency_check.py` 补静态一致性**，两份报告一起归档；结论冲突时以本脚本的 L3 为准（页面显示值是一等公民）。
