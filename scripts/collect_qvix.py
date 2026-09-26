#!/usr/bin/env python3
"""collect_qvix.py — 50ETF 期权 QVIX 日频采集（Bayoutree 2026-09-26 批准恢复）。

数据源：AKShare index_option_50etf_qvix()（上交所 50ETF 期权隐含波动率指数）。
输出：
  data/feargreed_pointer.json
    - history：全量日点 [{date, value, qvix, zone}, ...]，value=100−分位（见下）
    - 顶层 as_of / score / qvix_close / qvix_pctile_252 / zone_confirmed
  与历史口径完全一致（score = 100 - percentile_rank(close, rolling 252 trading days)）。

幂等：全量重算，逐日点值确定；多次运行仅 as_of 随交易日推进。
缺数不造：拉取失败则保留原文件，退出非零让流水线记录。
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
FG_FILE = DATA_DIR / 'feargreed_pointer.json'
WINDOW = 252
CST = timezone(timedelta(hours=8))


def classify_zone(score, qvix):
    if score < 15:
        return '极度恐惧'
    if score < 35:
        return '恐惧'
    if score <= 65:
        return '中性'
    if score > 85 and qvix < 14:
        return '极度贪婪'
    return '贪婪'


def percentile_rank(window, value):
    """value 在滚动窗口中的百分位（含自身，与历史口径一致：<= 占比）。"""
    below = sum(1 for v in window if v <= value)
    return round(below / len(window) * 100, 2)


def main():
    try:
        import akshare as ak
    except ImportError:
        print("❌ akshare 未安装")
        sys.exit(1)

    fg = json.load(open(FG_FILE, encoding='utf-8'))

    df = ak.index_option_50etf_qvix()
    # 兼容列名
    date_col = 'date' if 'date' in df.columns else df.columns[0]
    close_col = 'close' if 'close' in df.columns else ('qvix' if 'qvix' in df.columns else df.columns[-1])
    df = df[[date_col, close_col]].dropna()
    df[date_col] = df[date_col].astype(str).str.slice(0, 10)
    df = df.sort_values(date_col).drop_duplicates(date_col, keep='last')
    closes = list(df[close_col].astype(float))
    dates = list(df[date_col])
    print(f"📡 QVIX 拉取成功: {len(dates)} 行，{dates[0]} → {dates[-1]}，close={closes[-1]}")

    history = []
    for i in range(len(dates)):
        lo = max(0, i - WINDOW + 1)
        window = closes[lo:i + 1]
        # 与历史口径一致：必须满 252 个交易日窗口才出点（旧序列即从窗口满日 2016-02-24 起）
        if len(window) < WINDOW:
            continue
        pct = percentile_rank(window, closes[i])
        score = round(100 - pct, 2)
        zone = classify_zone(score, closes[i])
        history.append({'date': dates[i], 'value': score, 'qvix': round(closes[i], 2), 'zone': zone})

    if not history:
        print("❌ 无有效历史点")
        sys.exit(1)

    last = history[-1]
    fg['history'] = history
    fg['as_of'] = last['date']
    fg['score'] = last['value']
    fg['qvix_close'] = last['qvix']
    fg['qvix_pctile_252'] = round(100 - last['value'], 2)
    fg['zone_confirmed'] = last['zone']

    json.dump(fg, open(FG_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)
    print(f"✅ 写入 {FG_FILE}: {len(history)} 点，as_of={last['date']} "
          f"score={last['value']} qvix={last['qvix']} zone={last['zone']}")


if __name__ == '__main__':
    main()
