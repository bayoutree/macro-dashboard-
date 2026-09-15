#!/usr/bin/env python3
"""fetch_fred_data_v2.py - 扩展版FRED数据抓取，含产能利用率/库存/ISM/资产价格"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'requests', '-q'])
    import requests

FRED_API_KEY = os.environ.get('FRED_API_KEY', '712fbffa4e6e99945445842d2a799e58')
DATA_DIR = Path(__file__).parent.parent / 'data'

# ============================================================
# FRED序列映射 (扩展版)
# ============================================================

# 原有P0/P1序列（用于周期定位）
FRED_MACRO = {
    # --- 原有 ---
    'dfii10': 'DFII10',
    'termprem10': 'TERMPREM10',
    't30yiem': 'T30YIEM',
    'bamlh0a0hym2': 'BAMLH0A0HYM2',
    't10y2y': 'T10Y2Y',
    'cpilfesl': 'CPILFESL',
    'ecicomp': 'ECICOMP',
    'gfdegdq188s': 'GFDEGDQ188S',
    'walcl': 'WALCL',
    'unrate': 'UNRATE',
    # --- 新增 P0 ---
    'capacity_utilization': 'TCU',           # 产能利用率 (1967-)
    'inventory_sales_ratio': 'ISRATIO',      # 制造商库存/销售比 (1992-)
    'fed_funds_rate': 'FEDFUNDS',            # 联邦基金利率
    # --- 新增 P1 ---
    'wholesale_inventory': 'WHLSLRIMSA',     # 批发商库存
}

# 资产价格序列（日频→需转月频）
FRED_PRICES_DAILY = {
    'sp500': 'SP500',              # S&P 500
    'wti_oil': 'DCOILWTICO',      # WTI原油
    'copper': 'PCOPPUSDM',        # 铜价
    'usd_index': 'DTWEXBGS',      # 美元指数（广义）
    'us_10y_yield': 'DGS10',      # 10Y美债收益率
}


def fetch_fred_series(series_id, observation_start='2005-01-01', observation_end=None):
    """拉取单个FRED系列数据"""
    if not FRED_API_KEY:
        return None

    url = 'https://api.stlouisfed.org/fred/series/observations'
    params = {
        'series_id': series_id,
        'api_key': FRED_API_KEY,
        'file_type': 'json',
        'observation_start': observation_start,
        'sort_order': 'asc',
    }
    if observation_end:
        params['observation_end'] = observation_end

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        observations = data.get('observations', [])
        return [
            {'date': obs['date'], 'value': float(obs['value']) if obs['value'] != '.' else None}
            for obs in observations
            if obs['value'] != '.'
        ]
    except Exception as e:
        print(f"  ❌ {series_id}: {e}")
        return None


def daily_to_monthly(data_points):
    """日频数据转月频：取每月最后一个交易日值"""
    if not data_points:
        return []
    monthly = {}
    for dp in data_points:
        if dp['value'] is None:
            continue
        month_key = dp['date'][:7]  # YYYY-MM
        monthly[month_key] = dp['value']
    return [{'date': k, 'value': v} for k, v in sorted(monthly.items())]


def fetch_all():
    """拉取所有FRED数据"""
    print(f"📡 Fetching FRED data v2 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    results = {'macro': {}, 'prices': {}}

    # 1. 宏观指标
    print("\n[1/2] 宏观指标...")
    for key, series_id in FRED_MACRO.items():
        print(f"  {series_id:20s}...", end=' ', flush=True)
        data = fetch_fred_series(series_id)
        if data:
            results['macro'][key] = data
            print(f"✅ {len(data)} pts ({data[0]['date']} ~ {data[-1]['date']})")
        else:
            print("❌")

    # 2. 资产价格（日频→月频）
    print("\n[2/2] 资产价格...")
    for key, series_id in FRED_PRICES_DAILY.items():
        print(f"  {series_id:20s}...", end=' ', flush=True)
        daily = fetch_fred_series(series_id)
        if daily:
            monthly = daily_to_monthly(daily)
            results['prices'][key] = monthly
            print(f"✅ {len(daily)} daily → {len(monthly)} monthly")
        else:
            print("❌")

    # 计算10Y债券价格近似值（从收益率）
    # 简化：price ≈ 100 / (1 + yield/100)^10 的相对变化
    if 'us_10y_yield' in results['prices']:
        yields = results['prices']['us_10y_yield']
        if yields:
            base_yield = yields[0]['value']
            base_price = 100 / (1 + base_yield / 100) ** 10
            price_series = []
            for y in yields:
                if y['value'] is not None:
                    price = 100 / (1 + y['value'] / 100) ** 10
                    price_series.append({'date': y['date'], 'value': round(price, 2)})
            results['prices']['us_10y_price'] = price_series
            print(f"  10Y债券价格: 计算完成 ({len(price_series)} pts)")

    return results


def save_results(results):
    """保存结果"""
    DATA_DIR.mkdir(exist_ok=True)

    # 保存原始FRED数据（兼容原有格式）
    fred_raw = {
        'update_time': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'series': results['macro'],
    }
    raw_path = DATA_DIR / 'fred_raw.json'
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(fred_raw, f, ensure_ascii=False, indent=2)
    print(f"\n✅ fred_raw.json saved ({len(results['macro'])} series)")

    # 保存资产价格月频数据
    prices_output = {
        'update_time': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'prices': results['prices'],
    }
    prices_path = DATA_DIR / 'asset_prices_monthly.json'
    with open(prices_path, 'w', encoding='utf-8') as f:
        json.dump(prices_output, f, ensure_ascii=False, indent=2)
    print(f"✅ asset_prices_monthly.json saved ({len(results['prices'])} series)")

    return fred_raw, prices_output


if __name__ == '__main__':
    results = fetch_all()
    save_results(results)
    print("\n🎉 FRED数据抓取完成")
