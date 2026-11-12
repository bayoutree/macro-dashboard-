# legulegu 两项：换源尝试结论（裁决④）

- 评估日：2026-11-12
- 依据：CIO 裁决④（legulegu 两项 11-26 前换源，换不到下线；破净率有 akshare 自算链可替代）
- 结论先行：**这两项不是"源停了要换源"，而是"孤儿文件"——仓库里没有采集器、页面里没有卡位，数据自 2026-09-11 冻住后再无链路触碰。因此换源不适用，直接按裁决④"换不到下线"执行下线即可。真正的乐咕依赖面在别处（见 §3）。**

---

## 1. 核查事实

| 检查项 | 结果 |
|---|---|
| `data/all_a_gxl_spread.json` 末点 | `update_date = 2026-09-11` |
| `data/break_net_asset_rate.json` 末点 | `update_date = 2026-09-11` |
| 仓库内采集器 | **无**。全仓 `*.py` 中对这两个文件名/`legulegu` 的写入命中为 0（`collect_stock_anchor_history.py` 里的 legulegu 只是注释说明 akshare 封装来源，与这两个文件无关） |
| 页面卡位 | **无**。全仓 `*.html` / `*.js` 对这两个文件名的引用命中为 0 |
| 重复登记 | **有**。manifest 里同一文件被登记两次：`legulegu_gxl_spread`(active) 与 `all_a_gxl_spread`(deprecated)、`legulegu_break_net`(active) 与 `break_net_asset_rate`(deprecated)。active 的那条每天在巡检里报 FAIL |
| 替代链是否在跑 | **是**。破净率走 `scripts/collect_microstructure.py` 的 akshare 自算（`ak.stock_zh_a_spot_em()` 全A快照，PB<1 且 PB>0 占比），写入 `timing_scores.json → valuation.indicators.break_net_rate`，**2026-11-12 实测值 9.2，当日新鲜**；股息利差走 `update_data.py:_calc_dividend_spread`（红利指数股息率 − akshare 10Y 国债），也不依赖这两个文件 |

## 2. 已执行处置

1. `legulegu_gxl_spread` / `legulegu_break_net` 标 `status: deprecated`、`severity_on_gap: report`、阈值置 9999，note 写明裁决④与零引用证据 → **不再进 FAIL**。
2. 合并重复登记：删除 `all_a_gxl_spread` / `break_net_asset_rate` 两条重复项（同文件同口径），manifest 由 53 条降为 **51 条（48 active + 3 deprecated：oecd_cli、legulegu×2）**。
3. 同步 `frontend/data/freshness_manifest.json` 镜像（FE-DRIFT 一致性门禁要求，否则日更会被判 CRITICAL）。
4. 文件本身保留在 `data/` 作为历史存档（裁决④"下线"= 摘出活跃指标，不是删数据）。

**验证**：`monthly_audit.py --offline --strict` → `PASS 34 / WARN 15 / FAIL 0 / SKIP 3`，legulegu 两项转为 SKIP（deprecated 不评估）。

## 3. 真正的乐咕依赖面（这才是 11-26 前要盯的）

`scripts/update_data.py` 通过 akshare 的乐咕封装取数，共 3 条调用、影响 3 个指标（其中 1 个是主指标）：

| 调用 | 影响指标 | 权重/级别 | 断源后果 |
|---|---|---|---|
| `ak.stock_index_pe_lg(symbol="中证800")` | 中证800 ERP ★**主指标** | sub_weight 40 | 主指标静默保留旧值，页面不报错 |
| `ak.stock_index_pe_lg(symbol="沪深300")` | 沪深300 PE 分位 | sub_weight 40 | 分位失真 |
| `ak.stock_index_pb_lg(symbol="沪深300")` | 沪深300 PB 分位 | 参考（sub_weight 0） | 参考卡失真 |

风险点：这些调用被 `try/except` 包住，失败只 `logger.warning`，**不写 null、不置灰、不影响 manifest**——页面会继续显示上一次的旧值（`hs300_pb_percentile` 当前 12.7 且节点内无日期字段，肉眼无法判断新旧）。这与 F1（恐贪静默断裂）是同一类失效模型 A。

## 4. 替代源验证（探针已落地，结论待 CI 出）

新增 `tools/healthcheck/probe_legulegu_alt.py`：

- 逐条调用上述 3 个 akshare 乐咕封装，记录可用性 / 行数 / 数据末点；
- 探中证指数官方估值文件（`000300` / `000985` / `000905` 的 `indicator.xls`），记录可达性、字段（是否含市盈率/股息率/市净率）、末点；
- 输出 JSON 证据 + 结论行，退出码恒 0（探针不是门禁）。

已挂进 `.github/workflows/monthly_audit.yml` 的 `Probe legulegu dependency & csindex alternative` 步骤（`continue-on-error`，产物随 artifact 留档）。

**本机限制**：本机无 pandas/akshare，且对 `oss-ch.csindex.com.cn` 的下载被截断在 8192 B（三次代码均恰好 8192 B，属本机网络侧限制），故字段级结论必须在 CI 跑。CI 装 `pandas openpyxl xlrd requests akshare`，与 `update-data.yml` 同环境。

**已知字段预判**（据 akshare 源码 `stock_zh_index_value_csindex`）：中证指数官方文件字段为 `日期/指数代码/中文全称/中文简称/英文全称/英文简称/市盈率1/市盈率2/股息率1/股息率2` —— **有 PE 与股息率，没有 PB**。因此：
- 股息率类链（如全A股息率-10Y利差）→ 可用官方源替代；
- 沪深300 PB 分位 → 官方无 PB，需走"东财全A快照自算"（与破净率同法），或在 11-26 前接受"该卡降级为无源参考并置灰"。

## 5. 给 CIO 的两个决策点

1. **11-26 节点**：建议把风险项从"legulegu 两项"改写为"**乐咕 akshare 封装 3 条调用（含中证800 ERP 主指标）**"，两项孤儿文件本日即完成下线，不占 11-26 名额。
2. **PB 分位**：官方源无 PB 字段，是否接受"东财自算 PB 分位"或"该参考卡置灰"。我倾向自算——自算链已有现成代码（`calc_break_net_rate` 同源快照，含 pb 字段），改造成本低于新增采集器。
