#!/usr/bin/env python3
"""collect_fx_rates_em.py — F3①-3 USDCNY + USDCNH 日频汇率（东财源）

数据源：akshare forex_hist_em 底层（东财 push2his.eastmoney.com）
- USDCNYC（market_code=120）：在岸美元/人民币
- USDCNH（market_code=133）：离岸美元/人民币

产出 data/fx_rates_em.json：
- as_of：采集日
- usdcny_daily：[{date, close}]，在岸日频
- usdcnh_daily：[{date, close}]，离岸日频
- latest_spread_bp：(USDCNH - USDCNY) * 10000，单位基点，正=CNH 贬值

用途：
- F3④ 汇率阀门：CNH-CNY 价差判定（>0.4% 连续 5 日 / 单日 >0.7% 触发）
- USDCNY 年贬幅度（现价/250日前 − 1）
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "fx_rates_em.json"

# 东财外汇 symbol → market code（来自 akshare.forex.cons）
SYMBOL_MARKET_MAP = {
    "USDCNYC": 120,
    "USDCNH": 133,
}


def fetch_em_fx(symbol: str, max_retries: int = 3) -> list:
    """直接 requests 东财接口（绕过 akshare 封装，避免 ConnectionError 重试不够）"""
    import requests

    market_code = SYMBOL_MARKET_MAP[symbol]
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"{market_code}.{symbol}",
        "klt": "101", "fqt": "1", "lmt": "50000", "end": "20500000",
        "iscca": "1",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64",
        "ut": "f057cbcbce2a86e2866ab8877db1d059", "forcect": 1,
    }
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            klines = data.get("data", {}).get("klines", [])
            result = []
            for line in klines:
                parts = line.split(",")
                # f51=日期 f53=最新价
                result.append({"date": parts[0][:10], "close": float(parts[2])})
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise


def main():
    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "source": "eastmoney push2his (USDCNYC=120, USDCNH=133)",
        "usdcny_daily": [],
        "usdcnh_daily": [],
        "latest_spread_bp": None,
        "note": "F3①-3 USDCNY+USDCNH 东财日频双腿",
    }

    # USDCNY
    try:
        cny = fetch_em_fx("USDCNYC")
        out["usdcny_daily"] = cny
        print(f"OK USDCNYC: {len(cny)} 条 (最新 {cny[-1] if cny else 'N/A'})")
    except Exception as e:
        print(f"FAIL USDCNYC: {e}", file=sys.stderr)

    time.sleep(0.5)

    # USDCNH
    try:
        cnh = fetch_em_fx("USDCNH")
        out["usdcnh_daily"] = cnh
        print(f"OK USDCNH: {len(cnh)} 条 (最新 {cnh[-1] if cnh else 'N/A'})")
    except Exception as e:
        print(f"FAIL USDCNH: {e}", file=sys.stderr)

    # 价差
    if out["usdcny_daily"] and out["usdcnh_daily"]:
        cny_close = out["usdcny_daily"][-1]["close"]
        cnh_close = out["usdcnh_daily"][-1]["close"]
        spread = round((cnh_close - cny_close) * 10000, 2)
        out["latest_spread_bp"] = spread
        print(f"OK Spread: CNH-CNY = {spread}bp (CNH={cnh_close} CNY={cny_close})")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK fx_rates_em.json: CNY={len(out['usdcny_daily'])} CNH={len(out['usdcnh_daily'])} spread={out['latest_spread_bp']}bp")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL fx_rates 采集失败: {e}", file=sys.stderr)
        sys.exit(1)
