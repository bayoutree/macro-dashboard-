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
    'termprem10': 'THREEFYTP10',
        'ig_oas': 'BAMLC0A0CM',       # [V22-3] ICE BofA美国IG公司债OAS(%)——credit_spread_ig真源，替09-02写死旧值      # ACM期限溢价（V22-5：TERMPREM10 在FRED无此序列→拉取静默失败，改纽储ACM官方代码）
    't30yiem': 'T30YIEM',          # 30Y盈亏平衡通胀
    'bamlh0a0hym2': 'BAMLH0A0HYM2', # 高收益债利差
    't10y2y': 'T10Y2Y',            # 10Y-2Y利差
    'cpilfesl': 'CPILFESL',        # 核心PCE
    'ecicomp': 'ECIALLCIV',          # ECI雇佣成本
    'gfdegdq188s': 'GFDEGDQ188S',  # 联邦债务/GDP
    'walcl': 'WALCL',              # 美联储总资产
    'unrate': 'UNRATE',            # 失业率
    # ---- 补采工单（CIO 2026-11-11 立项，截止 11-14）：v4 层内停更指标真源 ----
    'mcumfn': 'MCUMFN',            # 制造业产能利用率(%)——juglar.us.capacity_utilization 真源
    'isratio': 'ISRATIO',          # 商业库存销售比(倍)——kitchin.us.inventory_sales_ratio 真源
    'dgorder': 'DGORDER',          # 耐用品新订单(百万美元)——kitchin.us.new_orders 真源（/1000=十亿）
    'businv': 'BUSINV',            # 商业总库存(百万美元)——kitchin.us.manufacturing_inventories 实际口径（/1000=十亿）
    # ---- [CIO 2026-11-12 月频停更清单·有源未灌] 朱格拉层美国投资两项真源 ----
    'equip_inv': 'Y033RC1Q027SBEA',  # 实际私人固定投资:设备(十亿2017$)——juglar.us.equipment_investment 真源
    'nonres_inv': 'PNFIC1',
    # ---- [CIO 2026-11-12 ①P1] 联邦赤字/GDP 年度真源（重建 fiscal_deficit_gdp） ----
    'deficit_gdp_fy': 'FYFSGDA188S',  # 联邦财政盈余/赤字占GDP（年度，负值=赤字）          # 实际私人非住宅固定投资(十亿2017$)——juglar.us.capex_growth 真源
    # ---- [2026-11-13 假数据事故4] 非农劳动生产率真源 ----
    # 根因：constraint_rate_regime.productivity_growth.us 的 history 只有 4 个点
    # （2010Q1=3.0 / 2015Q1=1.8 / 2020Q1=1.0 / 2026Q1=1.2），间隔 5 年、全为 Q1，
    # 是从 data/cycle_position.json（v1 原始文件）继承下来的手编示意序列，
    # 全仓无任何脚本采集过该指标。真值 2026Q3 YoY = 2.06%，与写死的 1.2% 差 0.86pct。
    # OPHNFB = Nonfarm Business Sector: Real Output Per Hour (index 2017=100)，季频。
    'ophnfb': 'OPHNFB',
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
