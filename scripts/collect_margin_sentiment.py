#!/usr/bin/env python3
"""collect_margin_sentiment.py — P1-7 情绪复合指数·融资类原始序列采集存档。

数据源（akshare）：
  融资余额/融资买入额 = ak.stock_margin_account_info()（沪深两市合计，亿元，日频，
    2012-09-27 起，融资融券证券公司报送口径）。
  两市总市值（分母代理）= ak.macro_china_stock_market_cap()（月频，亿元，
    市价总值-上海+深圳；⚠️ 为总市值口径，非流通市值——融资占比分母代理，
    月内阶梯填充到日；最新月未发布时沿用最近可得月并标 as_of）。
  两市成交金额（月频，亿元）同表，供 mb_ratio/换手率月频计算。

输出：data/margin_sentiment.json
  margin_daily: [{date, margin_balance, margin_buy}]（亿元）
  mcap_monthly: [{month, mcap, turnover}]（亿元；month=YYYY-MM）
  as_of / source / note（口径披露）

幂等：全量重抓覆盖；缺数不造——任一端拉取失败保留原文件并非零退出。
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "margin_sentiment.json"
CST = timezone(timedelta(hours=8))


def main():
    try:
        import akshare as ak
    except ImportError:
        print("❌ akshare 未安装")
        sys.exit(1)

    # 1) 融资融券（沪深合计，日频，亿元）
    try:
        mg = ak.stock_margin_account_info()
    except Exception as e:
        print(f"❌ 融资融券拉取失败: {e}")
        sys.exit(1)
    margin_daily = []
    for _, r in mg.iterrows():
        d = str(r["日期"])[:10]
        mb, mbuy = r.get("融资余额"), r.get("融资买入额")
        if mb is None or mbuy is None:
            continue
        margin_daily.append({"date": d,
                             "margin_balance": round(float(mb), 2),
                             "margin_buy": round(float(mbuy), 2)})
    margin_daily.sort(key=lambda x: x["date"])
    if len(margin_daily) < 1000:
        print(f"❌ 融资序列异常短（n={len(margin_daily)}），保留原文件")
        sys.exit(1)

    # 2) 两市总市值+成交金额（月频，亿元）
    try:
        mc = ak.macro_china_stock_market_cap()
    except Exception as e:
        print(f"❌ 市值表拉取失败: {e}")
        sys.exit(1)
    mcap_monthly = []
    for _, r in mc.iterrows():
        ds = str(r["数据日期"])  # '2026年09月份'
        try:
            y = int(ds[:4]); m = int(ds[5:7])
        except Exception:
            continue
        sh, sz = r.get("市价总值-上海"), r.get("市价总值-深圳")
        tsh, tsz = r.get("成交金额-上海"), r.get("成交金额-深圳")
        if sh != sh or sz != sz:  # NaN
            continue
        mcap_monthly.append({
            "month": f"{y:04d}-{m:02d}",
            "mcap": round(float(sh) + float(sz), 2),
            "turnover": (round(float(tsh) + float(tsz), 2)
                         if tsh == tsh and tsz == tsz else None),
        })
    mcap_monthly.sort(key=lambda x: x["month"])
    if len(mcap_monthly) < 100:
        print(f"❌ 市值序列异常短（n={len(mcap_monthly)}），保留原文件")
        sys.exit(1)

    out = {
        "update_time": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"),
        "as_of": margin_daily[-1]["date"],
        "source": "akshare: stock_margin_account_info + macro_china_stock_market_cap",
        "note": ("P1-7 情绪复合指数原料。融资余额/买入额=沪深两市合计亿元（2012-09-27 起）。"
                 "分母=两市市价总值（总市值口径代理流通市值，月频，月内阶梯填充）；"
                 "成交金额月频供 mb_ratio/换手率。总市值最新月未发布时沿用最近可得月。"),
        "margin_daily": margin_daily,
        "mcap_monthly": mcap_monthly,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {OUT.name}: margin_daily n={len(margin_daily)} "
          f"({margin_daily[0]['date']}~{margin_daily[-1]['date']}), "
          f"mcap_monthly n={len(mcap_monthly)} "
          f"({mcap_monthly[0]['month']}~{mcap_monthly[-1]['month']})")


if __name__ == "__main__":
    main()
