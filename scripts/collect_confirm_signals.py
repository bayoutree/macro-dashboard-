#!/usr/bin/env python3
"""
确认信号采集器：均线排列(月线) + MACD(月线) + 月线成交量趋势
数据源：沪深300月K线（腾讯 stock_zh_index_daily_tx）
输出：data/timing_scores.json 的 confirm_signals 字段
"""

import os
import sys
import json
import warnings
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
JSON_PATH = os.path.join(PROJECT_DIR, 'data', 'timing_scores.json')


def collect_confirm_signals():
    """采集确认信号三卡数据（沪深300月线）"""
    import akshare as ak
    import pandas as pd
    import numpy as np

    # 拉沪深300日K线（腾讯接口，含成交额）
    df = ak.stock_zh_index_daily_tx(symbol="sh000300")
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    df['amount_yi'] = df['amount'] / 100000  # 千元→亿元

    # 按月聚合
    monthly = df.groupby(df.index.to_period('M')).agg(
        close=('close', 'last'),
        amount=('amount_yi', 'sum')
    ).reset_index()
    monthly['date'] = monthly['date'].astype(str)

    closes = monthly['close'].values
    amounts = monthly['amount'].values
    dates = monthly['date'].tolist()

    # 从 2006-09 开始（对齐 mockData 起点）
    start_date = '2006-09'
    if start_date in dates:
        start_idx = dates.index(start_date)
        dates = dates[start_idx:]
        closes = closes[start_idx:]
        amounts = amounts[start_idx:]

    # 排除不完整月（最后一个月份如果当月交易日 < 10 天则排除）
    last_month = dates[-1]
    last_month_days = len(df[df.index.to_period('M').astype(str) == last_month])
    if last_month_days < 10:
        dates = dates[:-1]
        closes = closes[:-1]
        amounts = amounts[:-1]

    # ========== 1. 均线排列(月线) ==========
    ma5 = pd.Series(closes).rolling(5).mean().values
    ma10 = pd.Series(closes).rolling(10).mean().values
    ma20 = pd.Series(closes).rolling(20).mean().values
    ma60 = pd.Series(closes).rolling(60).mean().values

    ma_dates, ma_scores = [], []
    for i in range(len(closes)):
        if np.isnan(ma60[i]):
            continue
        s = 0
        s += 0.5 if closes[i] > ma5[i] else -0.5
        s += 0.5 if ma5[i] > ma10[i] else -0.5
        s += 0.5 if ma10[i] > ma20[i] else -0.5
        s += 0.5 if ma20[i] > ma60[i] else -0.5
        ma_dates.append(dates[i])
        ma_scores.append(round(s, 2))

    # ========== 2. MACD(月线) ==========
    ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd_hist = (dif - dea) * 2

    macd_dates, macd_dif, macd_dea, macd_histogram = [], [], [], []
    for i in range(len(dates)):
        if i >= 34 and not np.isnan(dif[i]):
            macd_dates.append(dates[i])
            macd_dif.append(round(float(dif[i]), 3))
            macd_dea.append(round(float(dea[i]), 3))
            macd_histogram.append(round(float(macd_hist[i]), 3))

    # ========== 3. 月线成交量趋势 ==========
    ma12_amount = pd.Series(amounts).rolling(12).mean().values

    vol_dates, vol_history, vol_history_ma = [], [], []
    for i in range(len(dates)):
        if not np.isnan(ma12_amount[i]):
            vol_dates.append(dates[i])
            vol_history.append(round(float(amounts[i]), 0))
            vol_history_ma.append(round(float(ma12_amount[i]), 0))

    return {
        'ma_arrangement': {
            'name': '均线排列(月线)',
            'freq': '月频',
            'unit': '',
            'dates': ma_dates,
            'history': ma_scores,
            'source': '沪深300月K线·腾讯',
            'caliber': 'close>MA5(+0.5),MA5>MA10(+0.5),MA10>MA20(+0.5),MA20>MA60(+0.5),范围-2~+2'
        },
        'macd_monthly': {
            'name': 'MACD(月线)',
            'freq': '月频',
            'unit': '',
            'dates': macd_dates,
            'dif': macd_dif,
            'dea': macd_dea,
            'histogram': macd_histogram,
            'source': '沪深300月K线·腾讯',
            'caliber': '标准MACD(12,26,9)'
        },
        'volume_monthly': {
            'name': '月线成交量趋势',
            'freq': '月频',
            'unit': '亿',
            'dates': vol_dates,
            'history': vol_history,
            'historyMA': vol_history_ma,
            'source': '沪深300月K线·腾讯',
            'caliber': '月成交额(亿)+12月均线'
        }
    }


def update_timing_scores(confirm_signals):
    """将确认信号数据写入 timing_scores.json"""
    with open(JSON_PATH) as f:
        ts = json.load(f)

    ts['confirm_signals'] = confirm_signals

    # 更新 update_date
    from datetime import datetime
    ts['update_date'] = datetime.now().strftime('%Y-%m-%d')

    with open(JSON_PATH, 'w') as f:
        json.dump(ts, f, ensure_ascii=False, indent=2)

    print(f"confirm_signals updated: "
          f"ma={len(confirm_signals['ma_arrangement']['dates'])}pt last={confirm_signals['ma_arrangement']['dates'][-1]}, "
          f"macd={len(confirm_signals['macd_monthly']['dates'])}pt last={confirm_signals['macd_monthly']['dates'][-1]}, "
          f"vol={len(confirm_signals['volume_monthly']['dates'])}pt last={confirm_signals['volume_monthly']['dates'][-1]}")


def main():
    print("=== collect_confirm_signals ===")
    try:
        data = collect_confirm_signals()
        update_timing_scores(data)
        print("Done.")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
