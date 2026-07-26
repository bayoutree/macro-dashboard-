# 宏观经济指标看板

基于五层分析框架（L1周期→L2宏观→L3估值→L4技术→L5结论）的实时宏观经济看板，覆盖A股、中国债券、美股、美债、商品、黄金六大类资产。

## 🏗 项目结构

```
macro-dashboard/
├── data/                          # JSON 数据文件（GitHub Pages 可直接访问）
│   ├── cycle_position.json        # L1 周期定位
│   ├── us_macro.json              # L2 美国宏观数据
│   ├── cn_macro.json              # L2 中国宏观数据
│   ├── asset_valuation.json       # L3 估值数据
│   ├── asset_prices.json          # L4 资产价格
│   └── dashboard_summary.json     # L5 综合结论
├── frontend/                      # 前端文件
├── scripts/                       # 数据获取脚本
│   ├── config.py                  # 全局配置与工具函数
│   ├── fetch_us_macro.py          # 美国宏观 (FRED API)
│   ├── fetch_cn_macro.py          # 中国宏观 (AKShare)
│   ├── fetch_asset_data.py        # 全球资产价格 (yfinance)
│   ├── calc_valuations.py         # 估值计算
│   ├── generate_summary.py        # L5 结论生成
│   ├── run_all.py                 # 一键运行
│   └── requirements.txt           # Python 依赖
├── .github/workflows/
│   └── daily_update.yml           # GitHub Actions 自动更新
└── README.md
```

## 🚀 本地运行

### 环境要求
- Python 3.11+
- FRED API Key（免费申请: https://fred.stlouisfed.org/docs/api/api_key.html）

### 安装与运行

```bash
# 1. 安装依赖
pip install -r scripts/requirements.txt

# 2. 设置 FRED API Key
export FRED_API_KEY=your_api_key_here

# 3. 一键运行所有脚本
python scripts/run_all.py

# 或单独运行某个脚本
python scripts/fetch_us_macro.py
```

## 📊 数据源说明

| 数据源 | 库 | 用途 | 状态 |
|--------|-----|------|------|
| FRED API | fredapi | 美国宏观/利率/期限溢价 | ⭐⭐⭐⭐⭐ 极稳定 |
| AKShare | akshare | 中国宏观/A股/中国债券 | ⭐⭐⭐⭐ 稳定 |
| Yahoo Finance | yfinance | 美股/商品/汇率/BTC | ⭐⭐⭐ 偶有限流 |

## 🔧 GitHub 部署

### 1. 配置 Secrets
在 GitHub 仓库 → Settings → Secrets → Actions 中添加:
- `FRED_API_KEY`: FRED API 密钥

### 2. 启用 GitHub Pages
- Settings → Pages → Source: Deploy from a branch
- Branch: `main`, Folder: `/ (root)` (或 `/docs` 如果将 index.html 放到 docs 目录)

### 3. 自动更新
GitHub Actions 每个工作日 UTC 08:00 (CST 16:00) 自动运行数据更新脚本，获取最新数据后 commit & push。

也可通过 Actions 页面手动触发: Actions → Daily Data Update → Run workflow

## 📋 输出文件

所有数据文件输出到 `data/` 目录，格式为 JSON (UTF-8, indent=2):

- **us_macro.json**: 美国 GDP、CPI、PPI、失业率、PMI、利率系列、期限溢价等
- **cn_macro.json**: 中国 PMI、GDP、CPI、PPI、M2、社融、A股指数、国债收益率等
- **asset_prices.json**: S&P500、纳斯达克、黄金、原油、铜、VIX、美元指数、BTC 等日频数据
- **asset_valuation.json**: Shiller CAPE、巴菲特指标、ERP、沪深300 PE/PB 分位、利率三因子分解等
- **dashboard_summary.json**: 基于规则引擎的五层分析结论与配置建议

## ⚠️ 注意事项

- FRED API 免费限额约 120 次/分钟，日常使用完全足够
- yfinance 为非官方接口，可能偶尔被限流（脚本内置重试机制）
- AKShare 接口可能因数据源网站改版临时失效，建议保持库版本更新
- 所有日期格式统一为 YYYY-MM-DD，时间序列已处理 NaN/NaT
