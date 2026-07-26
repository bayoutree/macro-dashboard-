#!/usr/bin/env python3
"""
获取全球资产价格数据 → data/asset_prices.json
数据源: yfinance
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import pandas as pd
import numpy as np
from datetime import datetime
from config import logger, today_str, safe_float, save_json, series_to_history


# ============================================================
# 标的配置
# ============================================================
TICKERS = {
    "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
    "nasdaq": {"symbol": "^IXIC", "name": "Nasdaq Composite"},
    "gold": {"symbol": "GC=F", "name": "Gold Futures"},
    "crude_oil": {"symbol": "CL=F", "name": "WTI Crude Oil"},
    "copper": {"symbol": "HG=F", "name": "Copper Futures"},
    "vix": {"symbol": "^VIX", "name": "VIX"},
    "usd_index": {"symbol": "DX-Y.NYB", "name": "US Dollar Index"},
    "us_10y": {"symbol": "^TNX", "name": "US 10Y Treasury"},
    "btc": {"symbol": "BTC-USD", "name": "Bitcoin"},
}

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def download_with_retry(symbol, period="5y", max_retries=MAX_RETRIES):
    """带重试的数据下载"""
    import yfinance as yf
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)
            if hist is not None and not hist.empty:
                logger.info(f"  ✓ {symbol}: {len(hist)} 条数据")
                return hist
            else:
                logger.warning(f"  ⚠ {symbol}: 返回空数据 (attempt {attempt+1})")
        except Exception as e:
            logger.warning(f"  ✗ {symbol}: {e} (attempt {attempt+1})")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    logger.error(f"  ❌ {symbol}: 所有重试均失败")
    return pd.DataFrame()


def calc_metrics(hist):
    """计算技术指标: 最新价、MA200、距52周高点回撤"""
    if hist.empty or 'Close' not in hist.columns:
        return None, None, None

    close = hist['Close'].dropna()
    if close.empty:
        return None, None, None

    latest = safe_float(close.iloc[-1])

    # MA200
    ma200 = None
    if len(close) >= 200:
        ma200 = safe_float(close.rolling(200).mean().iloc[-1])

    # 52周高点回撤
    drawdown = None
    if len(close) >= 252:
        high_52w = close.tail(252).max()
        if high_52w > 0:
            drawdown = safe_float((latest - high_52w) / high_52w * 100)

    return latest, ma200, drawdown


def fill_from_fred(prices):
    """用 FRED 数据补充 yfinance 缺失的标的"""
    import os
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        logger.info("  无 FRED_API_KEY，跳过 FRED 补充")
        return

    try:
        from fredapi import Fred
        fred = Fred(api_key=fred_key)

        # FRED → yfinance 映射
        fred_map = {
            "sp500": "SP500",
            "vix": "VIXCLS",
            "us_10y": "DGS10",
        }

        for key, fred_id in fred_map.items():
            if prices.get(key, {}).get("latest") is None:
                try:
                    s = fred.get_series(fred_id, observation_start="2021-01-01").dropna()
                    if not s.empty:
                        latest = safe_float(s.iloc[-1])
                        # MA200
                        ma200 = safe_float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
                        # 52周高点回撤
                        drawdown = None
                        if len(s) >= 252:
                            high_52w = s.tail(252).max()
                            if high_52w > 0:
                                drawdown = safe_float((latest - high_52w) / high_52w * 100)

                        # 日频历史
                        close = s.tail(60)
                        daily_data = [
                            {"date": idx.strftime("%Y-%m-%d"), "value": safe_float(val)}
                            for idx, val in close.items()
                        ]

                        prices[key] = {
                            "latest": latest,
                            "ma200": ma200,
                            "drawdown_from_high": drawdown,
                            "daily": daily_data,
                            "name": TICKERS.get(key, {}).get("name", fred_id),
                            "symbol": f"FRED:{fred_id}",
                            "update_time": today_str(),
                            "_source": "FRED"
                        }
                        logger.info(f"  ✓ FRED 补充 {key}: {latest} (from {fred_id})")
                except Exception as e:
                    logger.warning(f"  ✗ FRED {fred_id}: {e}")
    except ImportError:
        logger.warning("  fredapi 不可用")
    except Exception as e:
        logger.warning(f"  FRED 补充失败: {e}")


def main():
    logger.info("=" * 60)
    logger.info("开始获取全球资产价格数据 (yfinance + FRED fallback)")
    logger.info("=" * 60)

    prices = {}

    # ============================================================
    # 下载所有标的 (yfinance)
    # ============================================================
    for key, cfg in TICKERS.items():
        logger.info(f"\n下载 {cfg['name']} ({cfg['symbol']})...")
        hist = download_with_retry(cfg['symbol'])

        latest, ma200, drawdown = calc_metrics(hist)

        # 日频历史数据 (最近60个交易日)
        daily_data = []
        if not hist.empty and 'Close' in hist.columns:
            close = hist['Close'].dropna().tail(60)
            daily_data = [
                {"date": idx.strftime("%Y-%m-%d"), "value": safe_float(val)}
                for idx, val in close.items()
            ]

        prices[key] = {
            "latest": latest,
            "ma200": ma200,
            "drawdown_from_high": drawdown,
            "daily": daily_data,
            "name": cfg["name"],
            "symbol": cfg["symbol"],
            "update_time": today_str()
        }

        # 间隔一小段时间避免限流
        time.sleep(1)

    # ============================================================
    # FRED 补充
    # ============================================================
    logger.info("\n[FRED 补充] 尝试用 FRED 数据填充缺失标的...")
    fill_from_fred(prices)

    # ============================================================
    # VIX 单独处理 (history)
    # ============================================================
    vix_history = []
    if "vix" in prices and prices["vix"].get("daily"):
        vix_history = prices["vix"]["daily"]

    # ============================================================
    # 构建输出 JSON
    # ============================================================
    output = {
        "update_time": today_str(),
        "prices": {
            "sp500": {
                "latest": prices.get("sp500", {}).get("latest"),
                "ma200": prices.get("sp500", {}).get("ma200"),
                "drawdown_from_high": prices.get("sp500", {}).get("drawdown_from_high"),
                "daily": prices.get("sp500", {}).get("daily", [])
            },
            "nasdaq": {
                "latest": prices.get("nasdaq", {}).get("latest"),
                "ma200": prices.get("nasdaq", {}).get("ma200"),
                "drawdown_from_high": prices.get("nasdaq", {}).get("drawdown_from_high"),
                "daily": prices.get("nasdaq", {}).get("daily", [])
            },
            "gold": {
                "latest": prices.get("gold", {}).get("latest"),
                "ma200": prices.get("gold", {}).get("ma200"),
                "drawdown_from_high": prices.get("gold", {}).get("drawdown_from_high"),
                "daily": prices.get("gold", {}).get("daily", [])
            },
            "copper": {
                "latest": prices.get("copper", {}).get("latest"),
                "ma200": prices.get("copper", {}).get("ma200"),
                "drawdown_from_high": prices.get("copper", {}).get("drawdown_from_high"),
                "daily": prices.get("copper", {}).get("daily", [])
            },
            "crude_oil": {
                "latest": prices.get("crude_oil", {}).get("latest"),
                "ma200": prices.get("crude_oil", {}).get("ma200"),
                "drawdown_from_high": prices.get("crude_oil", {}).get("drawdown_from_high"),
                "daily": prices.get("crude_oil", {}).get("daily", [])
            },
            "usd_index": {
                "latest": prices.get("usd_index", {}).get("latest"),
                "ma200": prices.get("usd_index", {}).get("ma200"),
                "drawdown_from_high": prices.get("usd_index", {}).get("drawdown_from_high"),
                "daily": prices.get("usd_index", {}).get("daily", [])
            },
            "us_10y_bond": {
                "latest": prices.get("us_10y", {}).get("latest"),
                "daily": prices.get("us_10y", {}).get("daily", [])
            },
            "btc": {
                "latest": prices.get("btc", {}).get("latest"),
                "ma200": prices.get("btc", {}).get("ma200"),
                "drawdown_from_high": prices.get("btc", {}).get("drawdown_from_high"),
                "daily": prices.get("btc", {}).get("daily", [])
            }
        },
        "volatility": {
            "vix": {
                "latest": prices.get("vix", {}).get("latest"),
                "history": vix_history
            }
        }
    }

    save_json(output, "asset_prices.json")
    logger.info("\n✅ asset_prices.json 生成完成!")
    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 获取资产价格数据失败: {e}", exc_info=True)
        sys.exit(1)
