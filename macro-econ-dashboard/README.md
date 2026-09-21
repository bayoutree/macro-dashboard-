# 中美双国宏观经济看板 — 后端（Vercel Serverless）

自动抓取中美宏观数据，计算最新值 / 环比 / 5年&10年历史分位数，输出标准 JSON。

## 目录结构
```
api/fetch_data.py     Vercel Serverless Function 入口（每日 cron 触发）
scripts/
  fetch_china.py      akshare 抓中国数据（模糊列名 + 超时降级）
  fetch_us.py         FRED API 抓美国数据（fredapi）
  normalize.py        分位数 / 环比 / 历史分位计算（纯 Python，可单测）
  update.py           一键编排 + 交叉指标（中美利差等），本地也可跑
requirements.txt      akshare / fredapi / pandas / requests
vercel.json           cron 定时 + serverless 配置
.env.example          FRED_API_KEY 模板
```

## 本地运行
```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 FRED_API_KEY
python3 scripts/update.py   # 生成 data/indicators.json
```

## 部署到 Vercel
```bash
# 在 Vercel Dashboard -> Settings -> Environment Variables 配置 FRED_API_KEY
vercel deploy
```
- 每日 08:00 UTC 自动触发 `GET /api/fetch_data`（见 vercel.json crons）。
- 也可手动访问 `https://<your-app>/api/fetch_data` 立即刷新。
- 函数内存 1024MB，超时 60s（akshare 较重，单接口 25s 超时降级，保证整体不崩）。

## 输出结构
```json
{
  "update_time": "2026-09-19",
  "china": { "leading": {...}, "coincident": {...}, "lagging": {...},
             "financial": {...}, "sectors": {...}, "external": {...}, "valuation": {...} },
  "us":     { "leading": {...}, "coincident": {...}, "lagging": {...},
             "financial": {...}, "market": {...}, "valuation": {...} },
  "cross":  { "cn_us_10y_spread": {...}, "usdcny": {...} }
}
```
每个指标字段：`value / unit / change / pct_5y / pct_10y / date / history`。

## 容错说明
- 任一 akshare / FRED 接口超时或失败，该指标在结果中缺省，不影响其它指标与整体 JSON。
- US 数据需要 `FRED_API_KEY`；缺失时 `us` 为空对象，中国数据仍尽力抓取。
- `DR007` 优先取质押式回购，失败则用 Shibor 1W 近似；制造业投资/工业企业利润在无独立接口时以固定资产投资同比等代理。
