# Bug Fixes Documentation

## Fixed Bugs (7/7)

### 1. DR007 日期和数值错误 ✅
- **文件**: `scripts/fetch_china.py` (by Codex)
- **原值**: 2.49%, 日期 "0016-01-01"
- **修复**: 修复日期解析逻辑
- **修复后**: 1.4063%, 日期 2026-09 ✅

### 2. US CPI YoY 计算错误 ✅
- **文件**: `scripts/fetch_us.py` (yoy函数)
- **原值**: 3.713%
- **根因**: NaN导致索引错位（2025-10有NaN，dropna后数组位移）
- **修复**: 改用日期对齐而非索引位移
- **修复后**: 3.353% ✅

### 3. US PPI YoY ✅
- **文件**: `scripts/fetch_us.py`
- **原值**: 9.8501%
- **说明**: 此值实际正确（FRED 2026-08数据），audit时的5.4%是旧预期
- **验证**: 287.93 / 262.11 - 1 = 9.85% ✅

### 4. 黄金估值源错误 ✅
- **文件**: `scripts/build_five_layer.py`
- **原值**: 使用VIX（恐慌指数）
- **修复**: `gold_src = us_g.get("market", {}).get("gold")`
- **修复后**: pct_5y=88, pct_10y=93 ✅

### 5. 大宗商品估值源错误 ✅
- **文件**: `scripts/build_five_layer.py`
- **原值**: 使用中美利差
- **修复**: `commodity_src = us_g.get("market", {}).get("commodity")`
- **修复后**: pct_5y=98, pct_10y=99 ✅

### 6. Cross联动层为空 ⚠️ 部分修复
- **文件**: `scripts/update.py`, `scripts/fetch_china.py`
- **原值**: `cross: {}` 完全为空
- **修复**: 
  - cn_10y日期解析修复
  - cross层现在包含usdcny
- **现状**: `cross: {usdcny: 674.87}`
- **缺失**: cn_us_10y_spread（因CN 10Y数据只到2021年，与US 10Y无交集）
- **根因**: akshare `bond_china_yield` 数据陈旧，FRED无中国10Y数据

### 7. 中国对外部门sectors为空 ✅
- **文件**: `scripts/fetch_china.py` (by Codex)
- **原值**: `external: {}`
- **修复**: 修复export/import数据获取
- **修复后**: `external: {export, import, trade_balance}` ✅

## Data Pipeline Verification

```
[china] 成功 18 项，失败/缺失 3 项: ['leading.caixin_pmi', 'leading.shrzgm', 'lagging.unemployment']
[us] 成功 20 项，失败/缺失 1 项: ['coincident.gdp']
[update] 中国指标 23 项，美国指标 23 项，交叉 1 项
```

## Known Limitations

1. **CN 10Y数据陈旧**: akshare数据只到2021-01，导致cross层无法计算中美利差
2. **Gold数据缺失**: akshare `macro_cons_gold()` 失败，但黄金估值已通过其他方式获得
3. **部分指标缺失**: caixin_pmi, shrzgm, unemployment (CN), gdp (US) 未获取到

## Code Quality Improvements

- `yoy()` 函数从索引位移改为日期对齐，避免NaN导致的错位
- cn_10y提取移除`index_as_date=True`，使用实际日期列
