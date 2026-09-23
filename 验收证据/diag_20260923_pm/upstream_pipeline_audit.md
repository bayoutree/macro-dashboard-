# 上游流水线审计：cycle_position_v4.json 契约破坏（2026-09-23）

- 审计人：松子
- 审计时间：2026-09-24
- 审计范围：`bayoutree/macro-dashboard-` main 分支，v4 JSON 生成链路、
  GitHub Actions 工作流、静态资源版本机制
- 任务来源：HR 方案分工——CIO 负责线上热修，松子负责上游流水线适配（防复发）

## 一、结论（先给答案）

**仓库里不存在任何自动生成 `data/cycle_position_v4.json` 的脚本。** 该文件自
诞生（2026-09-15 commit `68eb8fb`）起就是人工/会话直接编辑提交的。09-23 的
v3→v4 手工合并（commit `86ae69a`，20:49 前后）把 v3 的新 schema
`cycle_consensus`（`dimension_scores` 数组）整体带到 v4，删掉了前端依赖的
`united_states/china` 分键，而没有任何生成器负责补回兼容字段——这是事故的
上游根因。本次新增生成器 `scripts/build_cycle_position_v4.py` 并接入每日
16:00（北京）流水线，根因闭环。

## 二、调用链排查证据

### 1. `.github/workflows/daily_update.yml`（每日 北京16:00）

调用链：

```
daily_update.yml
└─ python scripts/run_all.py
   ├─ fetch_us_macro.py
   ├─ fetch_cn_macro.py
   ├─ fetch_asset_data.py
   ├─ calc_valuations.py
   ├─ generate_summary.py
   ├─ fetch_fred_data.py        → data/fred_raw.json
   ├─ update_json.py            → 仅就地合并更新 data/cycle_position_v3.json
   ├─ calculate_indicators.py   → v3 衍生指标
   ├─ validate_json.py          → v3 校验
   ├─ collect_microstructure.py
   └─ sync_timing_scores_history.py
```

- `scripts/update_json.py`（全文约 85 行）：只 `open` v3、按 7 条固定 mapping
  合并 FRED history、回写 **v3**，无 `cycle_position_v4` / `cycle_consensus`
  字样。**v3 是流水线唯一权威产物。**
- 全仓库 `grep -rn cycle_position_v4`（py/yml/sh）命中：
  - `scripts/bridge_data_to_frontend.py:370-406`：`fix_c5_cycle_position()`
    只做 v4→cycle_position.json 复制，`fix_c6_phase_field()` 只补 phase；
    且该文件 `DATA_DIR` 硬编码为
    `/Coze/Drive/周期看板改进0915/macro-dashboard/data`（0915 一次性修复脚本），
    **没有任何 workflow 调用它**（三个 yml 全部核对过）。
  - 无其它脚本写入 v4。
- `scripts/update_data.py`：2800+ 行均为 timing_scores 六维择时逻辑
  （"v4.1" 只是其自身版本号），无 `cycle_position` 字样。

### 2. 另外两个工作流（均已核对，与 v4 无关）

- `.github/workflows/macro-econ-dashboard.yml`：跑
  `macro-econ-dashboard/scripts/update.py`，只提交 `macro-econ-dashboard/data/`；
  该目录下 5 个脚本（build_five_layer/fetch_china/fetch_us/normalize/update）
  grep 不到 `cycle_position`，产出的是五层看板数据。
- `.github/workflows/update-data.yml`：跑 `scripts/update_data.py`，产出
  timing_scores 等，不触碰 cycle_position*。

### 3. 新 schema 从哪来

```
3262766 (09-03 前) v3 cycle_consensus 为空
9e194c7 (09-03 16:30) v3 引入新 schema：overall_score=68 + dimension_scores[]
         ↑ 外部数据包随 v3 JSON 手工提交，此后流水线只做就地合并、schema 延续
68eb8fb (09-15 10:35) v4 文件诞生，cycle_consensus 用旧契约
                       （united_states/china + p1/p2/p3 + formula）
722f616 (09-16)        Phase 4-B 继续维护旧契约
86ae69a (09-23 21:50)  手工合并：v4 的 cycle_consensus 被 v3 新 schema 覆盖
                       （1339 增 / 710 删），旧契约字段全部丢失 ← 事故点
```

事故时字段对比（`git show 86ae69a^` vs 当前）：

| 契约 | 事故前 | 事故后 |
|---|---|---|
| 顶层 | `last_updated, formula, united_states, china` | `last_updated, overall_score=68, overall_assessment, overall_signal, dimension_scores[], asset_allocation_summary, key_risks, cycle_nesting` |
| 区域分键 | `p1/p2/p3_score, p1/p2/p3_label, raw_score, consensus_score, signal` | 全部删除 |

前端受影响位置（CIO 已热修，此处仅记录契约依据）：
`js/cycle_v4_patch.js:54-55,73-90,98-99,168,182-217,347-393`、
`js/cycle_v3.js:562-596`。

## 三、改动清单（本次提交，仅限上游/工具/文档）

| 文件 | 类型 | 说明 |
|---|---|---|
| `scripts/build_cycle_position_v4.py` | 新增 | **v4 生成器（根因修复）**，规则全部代码化、可注释追溯 |
| `scripts/run_all.py` | 修改 | SCRIPTS 增加生成器（位于 validate_json 之后），输出文件检查增加 v4 |
| `scripts/bump_asset_versions.py` | 新增 | 静态资源 `?v=` 内容哈希自动 bump + `--check` 校验 |
| `.github/workflows/daily_update.yml` | 修改 | 提交前自动 bump + `--check` 门禁；`git add` 增加 index.html |
| `docs/ASSET_VERSIONING.md` | 新增 | `?v=` 规范（规则、工具、强制接入点、边界） |
| `验收证据/diag_20260923_pm/upstream_pipeline_audit.md` | 新增 | 本文件 |

**未触碰**（CIO 热修边界）：`data/cycle_position_v4.json`、`data/cycle_position.json`、
`js/`、`index.html`。数据文件由生成器在下次流水线自动产出，本次提交不含
重新生成的 v4 数据，避免与线上热修互相覆盖。

## 四、生成器契约规则（防复发的核心，全部写死，禁止人工判断）

输入 `data/cycle_position_v3.json`，输出整体重写 `data/cycle_position_v4.json`：

1. **底座**：v3 全部内容；v4-only 增强顶层板块（`transmission_table`、
   `constraint_degradation`、`data_quality`、`asset_ranking`、
   `contradiction_status`）从上一版 v4 原样保留。
2. **cycle_consensus 兼容分键**（新 schema 字段全部保留，另补）：
   - `united_states` / `china` 各含 `p1_score/p2_score/p3_score`、
     `p1_label/p2_label/p3_label`、`raw_score`、`consensus_score`、`signal`；
   - **P1 朱格拉** ← `dimension_scores` 中「朱格拉设备周期」条目的
     `us_score` / `cn_score`（事故基线 us=1, cn=1）；
   - **P2 基钦** ← 「基钦库存周期」条目的 `us_score` / `cn_score`
     （事故基线 us=2, cn=1）；
   - **P3 美林**：dimension_scores 中美林只有 `us_assessment/cn_assessment`
     定性文字、无数值。规则定为读取 v4 美林三维层
     `cycle_layers.cycle_merrill_3d.{us,cn}.signal_weight`（-2..2，三维矩阵
     象限位置直接产出，与 P1/P2 同尺度）作为 P3；
   - 任何分值缺失（条目缺失/字段缺失/美林层缺失）→ 记 **0（中性）**，
     label 标注「未获取（中性计分）」，**不猜测**；
   - 公式沿用旧契约（前端既定口径）：
     `raw = P1×0.3 + P2×0.4 + P3×0.3`；
     `consensus = (raw + 2) / 4 × 100`；
   - `signal` 按 consensus 分档：≥80 强烈看多 / ≥60 看多 / ≥40 中性 /
     ≥20 看空 / 其余强烈看空；
   - 顶层补 `formula` 文本。
3. **口径一致性强制**：`synthesis.overall_assessment` 中
   「共识度评分 NN/100」必须等于 `cycle_consensus.overall_score`；不一致时
   以 **overall_score 为准**就地正则改写（事故时 文本62 → 字段68，实测修复）。
4. **失败即拒**：v3 缺失、cycle_consensus 结构异常、朱格拉/基钦维度缺失、
   overall_score 非数值 → exit 1，不产出半截文件。
5. `_meta` 写入 `v4_builder` / `v4_built_at`，线上可一眼识别文件来源。

## 五、验证结果

### 1. 以事故现场数据实跑

```
$ python scripts/build_cycle_position_v4.py
⚠ synthesis 文本评分 62 → 68（以 overall_score 为准）
✅ data/cycle_position_v4.json 生成完成
  US: P1=1 P2=2 P3=1 raw=1.4 consensus=85.0 signal=强烈看多
  CN: P1=1 P2=1 P3=2 raw=1.3 consensus=82.5 signal=强烈看多
  overall_score=68; 增强板块保留: ['transmission_table','constraint_degradation',
                                    'data_quality','asset_ranking','contradiction_status']
```

- P1/P2 与维度基线（朱格拉 us=1/cn=1、基钦 us=2/cn=1）完全一致；
- P3 来自美林层 signal_weight（us=1「复苏→过热」/ cn=2「复苏中期」）；
- 6 处前端取值点全部有值：证伪清单、排序副标题 P1/P2、hero 卡片、矛盾组合
  检测所需字段齐备；
- 文本评分与字段统一为 68；
- 5 个增强板块无损保留。

### 2. 边界攻击测试（全部 PASS）

1. 缺 v3 文件 → 拒绝；
2. dimension_scores 缺朱格拉/基钦 → 拒绝；
3. overall_score 非数值 → 拒绝；
4. 无上一版 v4（美林 weight 缺失）→ P3=0 兜底、label 标注；
   手算验证 P1=1,P2=2,P3=0 → raw=1.1 → consensus=77.5；
5. synthesis 无评分文本 → 不崩；
6. 用自己的输出再跑一次 → 幂等，字段完全一致。

### 3. 版本串工具验证

- 首次运行 index.html：13 处引用刷新为 8 位内容哈希；
- 二次运行：0 改动（幂等）；
- 改动 js/app.js 后 `--check`：exit 1 并指出 1 处需 bump；
- 外部 URL / data: / # 引用原样保留；资源不存在不报错。

## 六、下次流水线（北京 16:00）不会再覆盖兼容字段的验证方式

生成器已在 `run_all.py` 的 SCRIPTS 序列中（validate_json 之后），每日流水线
必然执行并整体重写 v4，输出结构由本审计第四节固定。可按以下任一方式复核：

1. **看当天 Actions 运行日志**：`Daily Data Update` 中出现
   `✅ V4周期-生成v4前端契约JSON` 及生成器打印的 US/CN P1/P2/P3 行；
   随后 `Verify asset versions are consistent` 步骤通过；
2. **看提交 diff**：当天提交的 `data/cycle_position_v4.json` 中
   `cycle_consensus.united_states` / `.china` 两个分键存在且含全部 10 个字段，
   `_meta.v4_builder = scripts/build_cycle_position_v4.py`；
3. **看线上文件**：`https://bayoutree.github.io/macro-dashboard-/data/cycle_position_v4.json`
   中兼容分键可被 JSONPath
   `$.cycle_consensus.united_states.consensus_score` 取到数值；
4. **强制演练**：在 GitHub Actions 手动 `workflow_dispatch` 触发一次，
   产物符合上述即证明链路闭环（可在今天 16:00 前随时演练）。

## 七、遗留说明

- 生成器目前信任上一版 v4 的美林层 signal_weight 作为 P3 来源；若未来流水线
  能直接产出 v4 命名层或美林数值，只需改 `_merrill_p3()` 一个函数，规则变更
  仍必须落在脚本里并注释，不允许回到人工编辑 JSON。
- `bridge_data_to_frontend.py` 为 0915 一次性脚本（硬编码旧路径），建议后续
  清理或归档，本次未动以缩小变更面。
