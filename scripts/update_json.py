#!/usr/bin/env python3
"""update_json.py - 将FRED原始数据更新到cycle_position_v3.json"""

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
RAW_FILE = DATA_DIR / 'fred_raw.json'
V3_FILE = DATA_DIR / 'cycle_position_v3.json'

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_indicator_history(indicator, history_data):
    """用新数据更新指标的history"""
    if not history_data:
        return
    # 转换为dict方便查找
    existing = {h['date']: h['value'] for h in indicator.get('history', [])}
    for item in history_data:
        date = item['date']
        # 只保留年度或月度数据点（去重）
        if date not in existing:
            existing[date] = item['value']
    # 排序并更新
    indicator['history'] = sorted(
        [{'date': k, 'value': v} for k, v in existing.items()],
        key=lambda x: x['date']
    )
    # 更新current为最新值
    if indicator['history']:
        indicator['current'] = indicator['history'][-1]['value']
    indicator['last_updated'] = datetime.now().strftime('%Y-%m-%d')

def update_v3_json(fred_raw):
    """更新v3 JSON文件"""
    v3 = load_json(V3_FILE)
    series = fred_raw.get('series', {})
    
    # 映射FRED数据到JSON字段
    mappings = {
        # (json_path_keys, fred_series_key, indicator_key)
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'breakeven_5y5y'), 'dfii10'),
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'acm_term_premium'), 'termprem10'),
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'breakeven_30y'), 't30yiem'),
        (('cycle_layers', 'layer_3_rate_regime', 'market_based', 'hy_spread'), 'bamlh0a0hym2'),
        (('cycle_layers', 'layer_3_rate_regime', 'market_based', 'yield_spread_10y_2y'), 't10y2y'),
        (('cycle_layers', 'layer_3_rate_regime', 'market_based', 'core_pce'), 'cpilfesl'),
        (('cycle_layers', 'layer_6_merrill', 'us', 'indicators', 'unemployment_rate'), 'unrate'),
    }
    
    updated = 0
    for path, fred_key in mappings:
        if fred_key not in series:
            continue
        # Navigate to the indicator
        obj = v3
        for key in path:
            obj = obj.get(key)
            if obj is None:
                break
        if obj is not None and 'history' in obj:
            update_indicator_history(obj, series[fred_key])
            updated += 1
            print(f"  ✅ Updated {path[-1]} ({len(series[fred_key])} points)")
    
    # 更新_meta
    v3['_meta']['update_time'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')
    v3['_meta']['data_freshness_score'] = min(100, v3['_meta'].get('data_freshness_score', 60) + 5)
    
    save_json(V3_FILE, v3)
    print(f"✅ Updated {updated} indicators in cycle_position_v3.json")

if __name__ == '__main__':
    if not RAW_FILE.exists():
        print("❌ fred_raw.json not found. Run fetch_fred_data.py first.")
    else:
        fred_raw = load_json(RAW_FILE)
        update_v3_json(fred_raw)
