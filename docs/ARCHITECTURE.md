# 宏观经济指标看板 - 架构设计文档

## 项目概述

一个实时更新的宏观经济指标看板网页，用于指导大类资产配置决策。覆盖A股、中国债券、美股、美债、商品、黄金六大类资产。

## 五层分析框架

```
L1 经济周期 → L2 宏观指标 → L3 资产估值 → L4 技术走势 → L5 综合结论
```

## 技术架构

```
┌──────────────────────────────────────────────┐
│              前端 (GitHub Pages)               │
│                                              │
│  index.html (单页应用)                        │
│  ├── TailwindCSS (CDN) - 样式                │
│  ├── ECharts (CDN) - 图表引擎                │
│  ├── /data/*.json - 数据文件                  │
│  │   ├── cycle_position.json    (L1周期定位)  │
│  │   ├── us_macro.json          (L2美国宏观)  │
│  │   ├── cn_macro.json          (L2中国宏观)  │
│  │   ├── asset_valuation.json   (L3估值)      │
│  │   ├── asset_prices.json      (L4价格走势)  │
│  │   └── dashboard_summary.json (L5结论)      │
│  └── js/                                     │
│      ├── app.js       - 主入口               │
│      ├── cycle.js     - L1渲染               │
│      ├── macro.js     - L2渲染               │
│      ├── valuation.js - L3渲染               │
│      ├── technical.js - L4渲染               │
│      └── summary.js  - L5渲染                │
└──────────────────────────────────────────────┘
           │ 读取JSON
┌──────────▼───────────────────────────────────┐
│         数据层 (GitHub Actions Cron)           │
│                                              │
│  scripts/                                    │
│  ├── fetch_us_macro.py   → FRED API          │
│  ├── fetch_cn_macro.py   → AKShare           │
│  ├── fetch_asset_data.py → yfinance          │
│  ├── calc_valuations.py  → 计算估值指标       │
│  ├── generate_summary.py → L5结论生成        │
│  └── requirements.txt                        │
│                                              │
│  .github/workflows/                          │
│  └── daily_update.yml    → 每日16:00 CST运行  │
└──────────────────────────────────────────────┘
```

## 数据JSON Schema

### cycle_position.json (L1)
```json
{
  "update_time": "2026-07-26",
  "kongbo": {
    "current_phase": "第五轮萧条尾声→第六轮回升初期",
    "confidence": "medium",
    "key_evidence": ["AI技术突破", "旧动能出清", "中美周期收敛"],
    "start_year": 2025,
    "phase_history": [
      {"round": 5, "phase": "回升", "start": 1991, "end": 2007},
      {"round": 5, "phase": "衰退", "start": 2007, "end": 2020},
      {"round": 5, "phase": "萧条", "start": 2020, "end": 2025}
    ]
  },
  "juglar": {
    "us": {"phase": "上升段", "start": 2020, "expected_end": "2027-2029"},
    "cn": {"phase": "上升初期", "start": 2025, "expected_end": "2032-2033"},
    "eu": {"phase": "下行末期→接近触底", "expected_bottom": "2026H2-2027H1"}
  },
  "kitchin": {
    "us": {"phase": "补库中后期", "risk": "Q3后可能转向被动补库"},
    "cn": {"phase": "补库初期(K型分化)", "detail": "上游补库,下游去库"},
    "eu": {"phase": "去库尾声", "detail": "行业极度分化"}
  },
  "resonance": {
    "matrix": "康波切换+朱格拉上行+基钦分化",
    "regime": "总量弱复苏+结构强分化",
    "window": "2026H2-2027是三周期共振确认的关键窗口"
  }
}
```

### us_macro.json (L2 - 美国)
```json
{
  "update_time": "2026-07-26",
  "leading": {
    "ism_pmi": {"value": 53.3, "date": "2026-06", "trend": "expanding"},
    "ism_new_orders": {"value": null, "date": null},
    "yield_curve_10y_2y": {"value": null, "date": null},
    "lei": {"value": null, "date": null}
  },
  "coincident": {
    "gdp_growth": {"value": 2.1, "date": "2026Q1"},
    "unemployment": {"value": 4.2, "date": "2026-06"},
    "industrial_production": {"value": 1.1, "date": "2026-06"},
    "nonfarm_payrolls_yoy": {"value": null, "date": null}
  },
  "lagging": {
    "cpi_yoy": {"value": 4.2, "date": "2026-05"},
    "ppi_yoy": {"value": 6.5, "date": "2026-05"},
    "fed_funds_rate": {"value": "3.50-3.75%", "date": "2026-07"}
  },
  "history": {
    "gdp_growth": [{"date": "2024Q1", "value": 1.6}, ...],
    "cpi_yoy": [{"date": "2024-01", "value": 3.1}, ...],
    "unemployment": [{"date": "2024-01", "value": 3.7}, ...]
  }
}
```

### asset_valuation.json (L3)
```json
{
  "update_time": "2026-07-26",
  "us_stock": {
    "shiller_pe": {"value": 40.5, "percentile": 97, "history": [...]},
    "buffett_ratio": {"value": null, "percentile": null, "history": [...]},
    "erp": {"value": null, "history": [...]},
    "forward_pe": {"value": 21.9, "percentile": 88}
  },
  "cn_stock": {
    "hs300_pe": {"value": null, "percentile": null, "history": [...]},
    "hs300_pb": {"value": null, "percentile": null, "history": [...]},
    "csi500_pe": {"value": null, "percentile": null, "history": [...]}
  },
  "us_bond": {
    "yield_10y": {"value": 4.71, "history": [...]},
    "real_rate_10y": {"value": 2.43, "history": [...]},
    "inflation_expectation": {"value": 2.26, "history": [...]},
    "term_premium": {"value": 0.78, "history": [...]}
  },
  "cn_bond": {
    "yield_10y": {"value": 1.7, "history": [...]}
  },
  "gold": {
    "price_usd": {"value": 4068, "history": [...]},
    "central_bank_buying": {"annual_tons": 863, "year": 2025, "history": [...]},
    "real_rate_vs_gold": {"history": [...]}
  },
  "commodity": {
    "copper": {"price": null, "inventory": null, "history": {...}},
    "crude_oil": {"brent": 85, "history": [...]}
  }
}
```

### asset_prices.json (L4)
```json
{
  "update_time": "2026-07-26",
  "prices": {
    "sp500": {"latest": 7412, "ma200": null, "drawdown_from_high": -2.2, "daily": [...]},
    "nasdaq": {"latest": null, "ma200": null, "drawdown_from_high": null, "daily": [...]},
    "sse": {"latest": 3867, "ma200": null, "drawdown_from_high": null, "daily": [...]},
    "hs300": {"latest": null, "ma200": null, "daily": [...]},
    "cn_10y_bond": {"latest": 1.7, "daily": [...]},
    "us_10y_bond": {"latest": 4.71, "daily": [...]},
    "gold": {"latest": 4068, "ma200": null, "drawdown_from_high": null, "daily": [...]},
    "copper": {"latest": null, "daily": [...]},
    "crude_oil": {"latest": 85, "daily": [...]},
    "btc": {"latest": 66500, "daily": [...]}
  },
  "volatility": {
    "vix": {"latest": null, "history": [...]},
    "vx_n": {"latest": null, "history": [...]}
  }
}
```

### dashboard_summary.json (L5)
```json
{
  "update_time": "2026-07-26",
  "layers": {
    "L1_cycle": {
      "conclusion": "三周期共振拐点，康波萧条尾声→回升，朱格拉上行，库存分化",
      "signal": "neutral_bullish",
      "assets_impact": {"stock": "+", "bond": "-", "commodity": "+", "gold": "+"}
    },
    "L2_macro": {
      "conclusion": "美国温和扩张但通胀粘性，中国新旧动能切换加速",
      "signal": "neutral"
    },
    "L3_valuation": {
      "conclusion": "美股高估值，A股估值洼地，黄金中性，铜结构性紧缺",
      "signal": "mixed"
    },
    "L4_technical": {
      "conclusion": "待数据更新后生成",
      "signal": "pending"
    }
  },
  "allocation": {
    "overweight": ["铜", "A股"],
    "market_weight": ["黄金", "中国国债", "科技成长股"],
    "underweight": ["美股", "美债", "原油", "比特币"]
  },
  "key_risks": [
    "中东冲突升级→滞胀",
    "美联储超预期加息",
    "AI投资回报不及预期"
  ]
}
```

## 前端设计规范

### 布局
- 顶部导航栏：标题 + 最后更新时间 + 刷新按钮
- L1区域：周期定位卡片（3个经济体 × 3个周期 = 9个卡片矩阵）
- L2区域：宏观指标仪表盘（领先/同步/滞后三列，中/美分栏）
- L3区域：估值图表区（每个资产一个ECharts图表）
- L4区域：技术走势图（多资产叠加走势 + 回撤图）
- L5区域：结论卡片（各层信号 + 配置建议）

### 样式
- 深色主题（参考三个看板风格）
- 响应式（桌面优先，移动端可浏览）
- 颜色规范：看涨绿色(#22c55e)，看跌红色(#ef4444)，中性灰色(#6b7280)
  - 注意：中国市场习惯红涨绿跌，但本看板统一使用国际惯例（绿涨红跌）
  - 或者做成可切换的？

### 图表类型
- 折线图：价格走势、宏观指标时间序列
- 柱状图：年度回报、估值分位数
- 热力图：月度涨跌矩阵
- 仪表盘：当前指标值 + 历史分位
- 卡片：周期定位、结论文字

## GitHub Actions 工作流

```yaml
name: Daily Data Update
on:
  schedule:
    - cron: '0 8 * * *'  # UTC 08:00 = CST 16:00
  workflow_dispatch:      # 手动触发

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r scripts/requirements.txt
      - run: python scripts/fetch_us_macro.py
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
      - run: python scripts/fetch_cn_macro.py
      - run: python scripts/fetch_asset_data.py
      - run: python scripts/calc_valuations.py
      - run: python scripts/generate_summary.py
      - run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add data/
          git diff --cached --quiet || git commit -m "Daily data update $(date +%Y-%m-%d)"
          git push
```

## 开发里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| M0 | 数据源调研与验证 | 进行中 |
| M1 | 数据获取脚本（FRED + yfinance） | 待启动 |
| M2 | 数据获取脚本（AKShare + 其他） | 待启动 |
| M3 | 前端框架 + L1/L5静态展示 | 待启动 |
| M4 | 前端L2/L3/L4图表实现 | 待启动 |
| M5 | GitHub Actions自动化 | 待启动 |
| M6 | 联调测试 + 部署上线 | 待启动 |
