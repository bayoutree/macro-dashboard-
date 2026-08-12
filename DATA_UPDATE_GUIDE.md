# 宏观看板自动数据更新系统

## 📦 已创建文件

### 1. 数据更新脚本
**路径**: `scripts/update_data.py` (2069行)

**功能**:
- ✅ 计算六维择时评分 (timing_scores.json)
  - 估值维度: PE/PB分位、巴菲特指标、破净率
  - 宏观流动性: 社融增速、利率水平、M1-M2剪刀差、美联储政策
  - 股债性价比: ERP、红利利差、中美利差
  - 资金面: 两融占比、北向资金、新发基金、产业资本
  - 市场情绪: 基金收益、恐贪指数、换手率、融资买入
  - 微观结构: 拥挤度、行业集中度、大小盘比值
- ✅ 自动调用其他数据脚本 (cn_macro, us_macro, asset_prices等)
- ✅ 生成加减仓信号和仓位建议
- ✅ 完整的错误处理和日志输出

### 2. GitHub Actions 工作流
**路径**: `.github/workflows/daily-update.yml` (135行)

**功能**:
- ⏰ 每日北京时间 18:00 自动运行 (工作日)
- 🔄 支持手动触发
- 📊 自动提交数据更新到仓库
- 🔔 失败时发送通知

## 🚀 使用方法

### 本地运行

```bash
# 完整更新 (所有数据文件)
python scripts/update_data.py

# 仅更新择时评分
python scripts/update_data.py --timing

# 检查接口连通性
python scripts/update_data.py --dry-run
```

### GitHub 自动更新

1. **配置 FRED API Key** (必需)
   - 访问 https://fred.stlouisfed.org/docs/api/api_key.html
   - 免费注册获取 API Key
   - 在 GitHub 仓库设置中添加 Secret:
     - Settings → Secrets and variables → Actions → New repository secret
     - Name: `FRED_API_KEY`
     - Value: 你的 API Key

2. **推送代码到 GitHub**
   ```bash
   git add scripts/update_data.py .github/workflows/daily-update.yml
   git commit -m "添加自动数据更新系统"
   git push origin main
   ```

3. **验证自动更新**
   - 访问 GitHub 仓库 → Actions 标签页
   - 应该能看到 "Daily Data Update" 工作流
   - 点击 "Run workflow" 手动触发测试
   - 检查是否成功提交数据更新

## 📊 数据源说明

### AKShare (免费)
- ✅ 沪深300 PE/PB 历史数据
- ✅ 中国10年国债收益率
- ✅ 宏观经济指标 (PMI, CPI, PPI, GDP, M2, 社融)
- ✅ 破净股统计
- ✅ 行业板块数据
- ✅ 北向资金流向

### FRED API (免费，需注册)
- ✅ 美国宏观经济数据
- ✅ 联邦基金利率
- ✅ 美国国债收益率
- ✅ 通胀数据

### yfinance (免费)
- ✅ 全球资产价格 (S&P 500, 黄金, 原油等)
- ✅ 汇率数据

## 🔧 评分逻辑

### 综合得分计算
```
综合得分 = Σ(维度得分 × 维度权重)

维度权重:
- 估值: 18%
- 宏观流动性: 17%
- 股债性价比: 18%
- 资金面: 15%
- 市场情绪: 18%
- 微观结构: 14%
```

### 得分解读
| 综合得分 | 市场状态 | 建议仓位 |
|---------|---------|---------|
| 0-25 | 极度低估 | 70-80% |
| 25-40 | 偏低/有吸引力 | 55-70% |
| 40-60 | 中性区间 | 40-55% |
| 60-75 | 偏高/谨慎 | 25-40% |
| 75-100 | 极度高估 | 15-25% |

## ⚠️ 注意事项

1. **接口限制**
   - AKShare 数据来自东方财富等网站，频率过高可能被封 IP
   - 建议每日最多运行 2-3 次
   - 脚本已添加错误处理，单个接口失败不影响整体

2. **数据缓存**
   - 部分指标 (如巴菲特指标、破净率) 使用上期数据缓存
   - 避免频繁调用耗时的全市场统计接口

3. **评分基准**
   - 评分基于历史分位数，需要至少 10 年数据
   - 新数据源可能需要调整评分阈值

## 📈 当前运行结果 (2026-08-12)

```
综合得分: 49.1
仓位区间: 40%-55%
市场状态: 中性区间，略偏暖

维度得分:
- 估值: 59.8 (中性)
- 宏观流动性: 48.6 (中性偏松)
- 股债性价比: 33.8 (股票有吸引力)
- 资金面: 49.5 (正常偏高)
- 市场情绪: 41.7 (中性)
- 微观结构: 66.2 (偏热)
```

## 🔍 故障排查

### 常见问题

1. **接口超时**
   ```
   ConnectionError: Connection aborted
   ```
   解决: 等待几分钟后重试，或检查网络连接

2. **FRED API 失败**
   ```
   FRED API error: Invalid API key
   ```
   解决: 检查 GitHub Secrets 中的 FRED_API_KEY 是否正确

3. **数据为空**
   ```
   Warning: 数据为空，使用缓存值
   ```
   解决: 正常现象，某些接口可能临时不可用

### 查看日志

```bash
# 查看详细日志
python scripts/update_data.py --timing 2>&1 | tee update.log

# 查看 GitHub Actions 日志
# 访问仓库 → Actions → 选择运行记录 → 查看 Job 日志
```

## 📝 维护建议

1. **定期检查**
   - 每周查看一次 GitHub Actions 运行状态
   - 检查数据文件更新时间戳

2. **更新依赖**
   ```bash
   pip install -r scripts/requirements.txt --upgrade
   ```

3. **调整评分参数**
   - 根据市场变化调整各维度权重
   - 修改 `update_data.py` 中的 `DIMENSION_WEIGHTS` 配置

## 🎯 下一步优化

- [ ] 添加更多数据源备份 (如 Tushare)
- [ ] 实现增量更新 (仅更新变化的数据)
- [ ] 添加数据质量检查
- [ ] 生成可视化报告
- [ ] 集成到前端看板自动刷新

---

**创建时间**: 2026-08-12  
**版本**: v1.0  
**维护者**: AI Assistant
