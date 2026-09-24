# 上游流水线审计：cycle_position_v4.json 契约破坏（2026-09-23，09-24 二次修正）

- 审计人：松子
- 最近更新：2026-09-24（P1 刻度平移 + asset_ranking 重算 + 层名映射幂等修复）
- 审计范围：`bayoutree/macro-dashboard-` main 分支，v4 JSON 生成链路、
  GitHub Actions 工作流、静态资源版本机制
- 任务来源：HR 方案分工——CIO 负责线上热修，松子负责上游流水线适配（防复发）

## 一、结论（先给答案）

**仓库里不存在任何自动生成 `data/cycle_position_v4.json` 的脚本。** 该文件自
诞生（2026-09-15 `68eb8fb`）起就是人工/会话直接编辑提交的。09-23 的 v3→v4
手工合并（`86ae69a`）把 v3 的新 schema `cycle_consensus`（`dimension_scores`
数组）带到 v4，删掉了前端依赖的 `united_states/china` 分键；并且**朱格拉维度
新旧刻度错位一格**，直接采用新分值会让 transmission_table 查表把「扩张早期」
误判为「扩张晚期」。本次新增生成器 `scripts/build_cycle_position_v4.py`
（含 P1 刻度平移、层名改名、ranking 重算）并接入每日 16:00（北京）流水线，
根因闭环。

## 二、调用链排查证据

### 1. `.github/workflows/daily_update.yml`（每日 北京16:00）

```
daily_update.yml
└─ python scripts/run_all.py
   ├─ fetch_us_macro.py / fetch_cn_macro.py / fetch_asset_data.py
   ├─ calc_valuations.py / generate_summary.py
   ├─ fetch_fred_data.py        → data/fred_raw.json
   ├─ update_json.py            → 仅就地合并 data/cycle_position_v3.json
   ├─ calculate_indicators.py   → v3 衍生指标
   ├─ validate_json.py          → v3 校验
   ├─ build_cycle_position_v4.py → v4 前端契约（本次新增）
   ├─ collect_microstructure.py
   └─ sync_timing_scores_history.py
```

- `update_json.py`（约 85 行）只写 v3，无 v4/consensus 字样；**v3 是流水线
  唯一权威产物**。
- 全仓库 grep：`bridge_data_to_frontend.py:370-406` 的 C-5/C-6 只做
  v4→cycle_position.json 复制，且 `DATA_DIR` 硬编码 0915 旧路径，**三个
  workflow 均不调用它**。
- `update_data.py` 的 "v4.1" 是 timing 自身版本号，无 cycle_position 字样；
  `macro-econ-dashboard/` 下 5 个脚本同样 grep 不到。

### 2. Schema/刻度时间线

```
9e194c7 (09-03) v3 引入新 schema：overall_score=68 + dimension_scores[]
68eb8fb (09-15) v4 诞生，cycle_consensus 用旧契约（US/CN 分键 + p1/p2/p3）
722f616 (09-16) 旧契约：US p1=2「扩张早期」p2=1「被动补库」p3=1 → 82.5
                 CN p1=2 p2=1 p3=2 → 90.0
86ae69a (09-23) 手工合并：① 新 schema 覆盖旧契约，分键全删；
                 ② 新朱格拉 us/cn_score=1「扩张早期」与旧刻度 2 错位 ← 事故点
```

## 三、刻度差异核验（二次修正的核心证据）

### P1 朱格拉：旧 P1 = 新分值 + 1

旧刻度从 transmission_table **全部 16 个 entries 的 scenario 编码**完整还原：

| 旧 P1 | 阶段 | 旧 P1 | 阶段 |
|---|---|---|---|
| 2 | 扩张早期 | -1 | 收缩早期 |
| 1 | 扩张晚期 | -2 | 收缩晚期 |

同一现实状态对照：旧 v4（`722f616`）US p1=**2** label「扩张早期」；新
dimension_scores 朱格拉 us_score=**1** assessment「中美均处于扩张早期」。
两个刻度错位一格。映射 `旧P1 = 新 us/cn_score + 1`，截断到 [-2,2]。

交叉验证：CN P1 1→2、P2=1、P3=2 → raw=1.6 → **90.0**，与旧版 CN 90.0
完全吻合；旧 US(2,1,1) 手算 82.5 也一致。

### P2 基钦：两刻度一致，不平移

旧 US p2=1「被动补库」 vs 新 us_score=2「主动补库存」——数值随库存阶段
（-2 主动去/-1 被动去/1 被动补/2 主动补）进展，新旧同名同值。

### P3 美林：取 signal_weight

dimension_scores 美林只有定性文字；取
`cycle_layers.cycle_merrill_3d.{us,cn}.signal_weight`（-2..2，三维矩阵象限
产出），label 取 current_phase；缺失记 0，不人工判断。

## 四、本次改动清单（仅限上游/工具/文档，不动 js/index.html/线上数据）

| 文件 | 类型 | 说明 |
|---|---|---|
| `scripts/build_cycle_position_v4.py` | 新增 | v4 生成器：P1 平移 + P2 直通 + P3 取 weight；层名 v3→v4 改名；ranking 重算 |
| `scripts/run_all.py` | 修改 | 生成器接入 validate_json 之后；文件检查增加 v4 |
| `scripts/bump_asset_versions.py` | 新增 | `?v=` 内容哈希 bump + `--check` 门禁 |
| `.github/workflows/daily_update.yml` | 修改 | bump + check 步骤；`git add` 增加 index.html |
| `docs/ASSET_VERSIONING.md` | 新增 | 版本串规范 |
| `验收证据/diag_20260923_pm/upstream_pipeline_audit.md` | 新增 | 本文件 |

## 五、生成器规则（全部代码化，禁止人工判断）

1. **底座**：v3 全部内容；`transmission_table`、`constraint_degradation`、
   `data_quality`、`contradiction_status` 从上一版 v4（CIO 热修版/自身上一次
   输出）继承。
2. **cycle_layers 改名**（`LAYER_V3_TO_V4`）：以 v3 层内容为权威底座仅改名；
   v4 侧新增子结构（如 rate regime 的 `regime_state`）深合并补回、v3 同名字段
   不覆盖。此步保证二次运行时美林 weight 不丢（修复了自测发现的非幂等缺陷）。
3. **兼容分键**：US/CN 各 10 字段（p1/p2/p3_score+label、raw_score、
   consensus_score、signal）；公式
   `raw=P1×0.3+P2×0.4+P3×0.3`，`consensus=(raw+2)/4×100`；signal 按
   ≥80 强烈看多/≥60 看多/≥40 中性/≥20 看空 分档；任何分值缺失记 0，不猜测。
4. **synthesis 口径强制**：文本「共识度评分 NN/100」正则对齐 overall_score
   （实测 62→68）。
5. **asset_ranking 重算（v3.1，不再原样保留旧版）**：
   - 区域归属：US entry 决定 us_equity/us_bond/usd/gold/commodities；
     CN entry 决定 china_equity/china_bond/china_realestate；
   - C1 按 8 资产同名键给 adjustment；C2 语义别名映射
     （valuation_sensitive→us_equity、long_duration→us_bond）；C1→C2 顺序
     叠加，方向封底下限 down；
   - 排序键确定性：最终方向 desc → entry 置信度 desc → `ASSET_TIE_PRIORITY`；
   - 信号 up 超配/neutral 标配/down 低配。
6. **失败即拒**：v3 缺失、consensus 异常、朱格拉/基钦维度缺失、overall_score
   非数值、查表失败 → exit 1，不出半截文件。

## 六、事故现场实跑结果

```
$ python scripts/build_cycle_position_v4.py
⚠ synthesis 文本评分 62 → 68
✅ data/cycle_position_v4.json 生成完成
  US: P1=2 P2=2 P3=1 raw=1.7 consensus=92.5 signal=强烈看多
  CN: P1=2 P2=1 P3=2 raw=1.6 consensus=90.0 signal=强烈看多
  ranking: 1.china_equity(超配) > 2.usd(超配) > 3.commodities(标配)
           > 4.gold(标配) > 5.china_bond(标配) > 6.china_realestate(标配)
           > 7.us_equity(低配) > 8.us_bond(低配)
```

边界攻击测试（全部 PASS，模拟 prev=86ae69a/CIO 热修版）：
刻度交叉验证、层名 v4 化、us_equity up→down(adj=-2)、us_bond 封底(adj=-1)、
4 板块保留、**二次运行完全幂等**、上游异常拒绝、无约束 adj=0、新刻度极端值
(-2→旧-1、+2 截断)。

## 七、需要 CIO/复核知悉的两处口径冲突（生成器按规则表执行，未自行发明规则）

1. **us_equity 实际被 C1+C2 连降两档（up→down，低配）**：按
   constraint_degradation 的规则表，C1 us_equity -1、C2
   us_equity_valuation_sensitive -1 顺序叠加即 down。而同文件
   `current_effective_degradations` 的叙述是「已 neutral 不再降」（最终
   neutral）。规则表与叙述不一致，生成器以**规则表**为准。
2. **ranking 与 asset_allocation 配置卡仍有口径差**：ranking 中 gold/commodities
   均为标配，但 `cycle_consensus.asset_allocation_summary` 写商品超配；
   `asset_allocation.current` 写黄金超配。生成器只保证 ranking↔transmission_table
   ↔constraint 三者一致；配置卡文本来自外部数据包、生成器未改写。是否统一
   由 CIO 裁定。

## 八、下次流水线（北京 16:00）不覆盖兼容字段的验证方式

1. Actions 日志出现 `✅ V4周期-生成v4前端契约JSON`（含 US/CN 评分行）且
   `Verify asset versions are consistent` 通过；
2. 提交 diff 中 `cycle_consensus.united_states/china` 含全部字段，
   `cycle_layers` 全部 v4 新命名，`_meta.v4_builder` 有值；
3. 线上 JSON：`$.cycle_consensus.united_states.consensus_score` 与
   `$.asset_ranking.ranking[0].asset` 均可取到；
4. 16:00 前可 workflow_dispatch 手动演练。

---

## 九、v3 修复：d308d65→当前（_apply_degradations 方向语义 + _merrill_p3 v3回落）

### 9.1 根因

`_apply_degradations` 将 C1/C2 adjustment 当**数值惩罚**叠加（`cur = max(-1, cur + adj)`），
导致 us_equity 被 C1(-1) 降到 neutral 后，C2 再 -1 穿透到 down。而
`constraint_degradation.current_effective_degradations` 叙述是「已在 neutral 不再降」——
**叙述是对的，代码错了**。正确语义是「方向档位修正」，neutral 是地板/天花板，不允许穿透。

### 9.2 修复内容

#### `_clamp_direction(cur, adj)`（新函数）
```
adj < 0: 已 down(-1) 保持 down；否则 max(0, cur+adj)，neutral 封底不穿到 down
adj > 0: 已 up(+1) 保持 up；否则 min(1, cur+adj)，neutral 封顶不穿到 up
adj = 0: 不变
```

#### `_apply_degradations` total_adj 语义变更
- 旧：`total_adj = C1_adj + C2_adj`（名义叠加）
- 新：`total_adj = cur_final - cur_base`（实际净改变）

#### `_merrill_p3` v3 层名回落
首次运行时 prev_v4 可能只有 v3 命名（`layer_6_merrill`），`_merrill_p3` 增加回落链：
`cycle_merrill_3d → layer_6_merrill`，防止 P3 静默归零（非幂等缺陷）。

### 9.3 修复后 ranking 验证

| # | asset | base | final | adj | signal |
|---|-------|------|-------|-----|--------|
| 1 | china_equity | up | up | 0 | 超配 |
| 2 | usd | up | up | 0 | 超配 |
| 3 | us_equity | up | neutral | -1 | 标配 |
| 4 | commodities | up | neutral | -1 | 标配 |
| 5 | gold | neutral | neutral | 0 | 标配 |
| 6 | china_bond | neutral | neutral | 0 | 标配 |
| 7 | china_realestate | neutral | neutral | 0 | 标配 |
| 8 | us_bond | down | down | 0 | 低配 |

- us_equity: base up → C1-1 → neutral → C2-1 封底 neutral → adj = 0-1 = -1 ✅
- us_bond: base down(基线即down) → C2-1 保持 down → adj = -1-(-1) = 0 ✅
- gold: base neutral → C1+1 → up → C2-1 → neutral → adj = 0-0 = 0 ✅
- 与 `current_effective_degradations` 叙述完全一致 ✅

### 9.4 边界测试结果（修复后）

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 事故现场复现 US=92.5/CN=90.0 | ✅ |
| 2 | 层名全部v4化(7新/0旧) | ✅ |
| 3 | ranking方向语义+adj数值(9项) | ✅ |
| 4 | 幂等性(run2==run3) | ✅ |
| 5 | synthesis文本评分对齐 | ✅ |
| 6 | 约束降级叙述一致性 | ✅ |
| 7 | **★首次prev_v4仅有v3层名时P3仍正确** | ✅ |
| 8 | 无transmission_table拒绝输出 | ✅ |
| 9 | 刻度截断边界(6项) | ✅ |

**48/48 PASS**（较d308d65版本+4项：v3回落×4 + ranking语义×3 + 叙述一致×2）

### 9.5 口径冲突解决

**第七节冲突①已解决**：us_equity 叙述「已 neutral 不再降」→代码现在也封底在 neutral，
叙述与代码一致。冲突②（配置卡超配 vs ranking标配）不在生成器职责，维持原结论。
