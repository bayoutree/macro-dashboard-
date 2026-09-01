# A股研究 Tab v4.0 变更说明

**变更基准：** 基于 Coze 宏观看板 A股研究 Tab v3 升级包  
**日期：** 2026-08-31  
**变更级别：** 中等（向后兼容，新增指标，不破坏现有逻辑）

---

## 变更清单

### 1. ERP口径切换（数据层）⭐ 核心

**文件：** `scripts/update_data.py`

| 指标 | 变更 | 说明 |
|------|------|------|
| 万得全A ERP | **新增为主指标** | `1/PE_TTM(全A) - 10年国债`，阈值 `>4%底部 / >3%便宜 / >2%中性 / >1.5%偏贵` |
| 沪深300 ERP | 降为参考（权重25%）| 标注"仅供参考，主指标为万得全A ERP" |

**函数：** `_calc_wanda_erp()`（新增，约50行）  
**PE来源：** `ak.stock_index_pe_lg(symbol="中证A股")`，fallback至"沪深300 ERP"

---

### 2. 北向资金正式移除（数据层）

**文件：** `scripts/update_data.py`

| 指标 | 变更 |
|------|------|
| 北向资金趋势 | 标记 `status: "deprecated"`，`value: "⚠️数据已停更(2025-09起)"`，权重归零 |

原因：AKShare北向资金接口停更，保留占位但前端自动忽略该指标。

---

### 3. 吴伟志四季定位（显示层）⭐ 新增

**文件：** `js/timing_v2.js`

在结论卡新增"四季定位"格子，与豆包ABCD双标：

| 得分区间 | 四季 | 豆包段位 | 含义 |
|----------|------|----------|------|
| <35分 | 🌱 春播季 | A段 | 底部区域，左侧布局期 |
| 35-58分 | ☀️ 夏长季 | B段 | 牛市中期，持有为主 |
| 58-80分 | 🍂 秋收季 | C段 | 牛市末期/顶部区域 |
| ≥80分 | ❄️ 冬藏季 | D段 | 熊市，清仓观望 |

注：四季阈值与豆包ABCD不完全对应，仅作**定性辅助**，不参与评分。

---

### 4. 信号触发数X/Y显示（显示层）

**文件：** `js/timing_v2.js`

右侧累计分数条新增触发数标注：

```
🐂 牛分  58/120  (3/12项触发)   ← 绿色
🐻 熊分  21/120  (1/12项触发)   ← 红色
```

- 分子 = 已触发信号数（早期+确认+最强确认之和）
- 分母 = 总信号数（12项）
- 直接显示，不用看展开列表也能判断多空力量对比

---

### 5. 修复：percentile空值判断

**文件：** `scripts/update_data.py`

`f"{erp_pct:.1f}%" if erp_pct else None`  
→ `f"{erp_pct:.1f}%" if erp_pct is not None else None`

防止 ERP分位=0%（有效值）被错误显示为"数据待更新"。

---

## Coze Studio 同步操作指引

### Step 1：同步 update_data.py
1. 打开 Coze Studio → 进入你的宏观看板 Bot
2. 找到 `update_data.py` 对应的代码节点（通常在"数据更新"workflow节点内）
3. 将 `/tmp/v3out/macro-dashboard/scripts/update_data.py` 全文替换
4. **注意**：只改动了 `calc_equity_bond()` 和 `_calc_northbound()` 两处，其他代码不变

### Step 2：同步 timing_v2.js
1. 找到 Coze 工作流中渲染 A股研究 Tab 的 JS 代码节点
2. 将 `/tmp/v3out/macro-dashboard/js/timing_v2.js` 全文替换
3. **注意**：只改动了 `renderConclusion()` 和 `renderScoreBars()` 两处

### Step 3：验证
1. 保存 → 重新发布
2. 访问看板 → A股研究 Tab
3. 检查结论卡是否出现"四季定位"行
4. 检查右侧分数条是否有"(X/Y项触发)"

---

## 文件清单

| 文件 | 变更 |
|------|------|
| `scripts/update_data.py` | ERP新增+北向停更+空值修复 |
| `js/timing_v2.js` | 四季定位+触发数显示 |

---

## 回滚方案

如需回滚：
- `update_data.py`：删除 `_calc_wanda_erp()` 函数调用，将 `northbound` 权重改回25，`hs300_erp` 权重改回50
- `timing_v2.js`：删除四季定位相关代码行，将分数条标签还原
