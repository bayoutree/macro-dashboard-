#!/usr/bin/env python3
"""fetch_fred_data.py - 从FRED API拉取宏观经济指标数据"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Installing requests...")
    os.system("pip install requests")
    import requests

FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
DATA_DIR = Path(__file__).parent.parent / 'data'
OUTPUT_FILE = DATA_DIR / 'cycle_position_v3.json'

# FRED系列代码映射
FRED_SERIES = {
    'dfii10': 'DFII10',           # 5Y-5Y远期实际利率
    'termprem10': 'TERMPREM10',    # ACM期限溢价
    't30yiem': 'T30YIEM',          # 30Y盈亏平衡通胀
    'bamlh0a0hym2': 'BAMLH0A0HYM2', # 高收益债利差
    't10y2y': 'T10Y2Y',            # 10Y-2Y利差
    'cpilfesl': 'CPILFESL',        # 核心PCE
    'ecicomp': 'ECICOMP',          # ECI雇佣成本
    'gfdegdq188s': 'GFDEGDQ188S',  # 联邦债务/GDP
    'walcl': 'WALCL',              # 美联储总资产
    'unrate': 'UNRATE',            # 失业率
}

def fetch_fred_series(series_id, observation_start='2015-01-01', observation_end=None):
    """拉取单个FRED系列数据"""
    if not FRED_API_KEY:
        print(f"⚠️ FRED_API_KEY not set, skipping {series_id}")
        return None
    
    url = f"https://api.stlouisfed.org/fred/series/observations"
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
            for obs in observations if obs['value'] != '.'
        ]
    except Exception as e:
        print(f"❌ Failed to fetch {series_id}: {e}")
        return None

def fetch_all():
    """拉取所有FRED数据"""
    print(f"📡 Fetching FRED data ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    results = {}
    for key, series_id in FRED_SERIES.items():
        print(f"  Fetching {series_id}...", end=' ')
        data = fetch_fred_series(series_id)
        if data:
            results[key] = data
            print(f"✅ {len(data)} observations")
        else:
            print("❌")
    return results

def save_raw_data(fred_data):
    """保存原始FRED数据"""
    raw_file = DATA_DIR / 'fred_raw.json'
    with open(raw_file, 'w', encoding='utf-8') as f:
        json.dump({
            'fetch_time': datetime.now().isoformat(),
            'series': fred_data
        }, f, ensure_ascii=False, indent=2)
    print(f" Saved raw data to {raw_file}")

if __name__ == '__main__':
    fred_data = fetch_all()
    if fred_data:
        save_raw_data(fred_data)
        print(f"✅ Fetched {len(fred_data)} series")
    else:
        print("️ No data fetched. Check FRED_API_KEY environment variable.")
        sys.exit(1)
