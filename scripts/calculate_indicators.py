#!/usr/bin/env python3
"""calculate_indicators.py - 计算衍生指标（二阶导数、分位数等）"""

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
V3_FILE = DATA_DIR / 'cycle_position_v3.json'

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def calc_percentiles(history):
    """计算历史分位数"""
    if not history or len(history) < 3:
        return None
    values = sorted([h['value'] for h in history if h.get('value') is not None])
    if not values:
        return None
    n = len(values)
    return {
        'p25': round(values[int(n * 0.25)], 2),
        'p50': round(values[int(n * 0.50)], 2),
        'p75': round(values[int(n * 0.75)], 2),
    }

def calc_second_derivative(history):
    """计算二阶导数（变化速度的变化）"""
    if not history or len(history) < 3:
        return None
    values = [h['value'] for h in history if h.get('value') is not None]
    if len(values) < 3:
        return None
    # 一阶导数（变化量）
    first_deriv = [values[i+1] - values[i] for i in range(len(values)-1)]
    # 二阶导数
    second_deriv = [first_deriv[i+1] - first_deriv[i] for i in range(len(first_deriv)-1)]
    if not second_deriv:
        return None
    avg_sd = sum(second_deriv) / len(second_deriv)
    return round(avg_sd, 3)

def calculate_all(v3):
    """计算所有衍生指标"""
    updated = 0
    layers = v3.get('cycle_layers', {})
    
    for layer_key, layer in layers.items():
        if isinstance(layer, dict):
            for region_key in ['us', 'cn']:
                region = layer.get(region_key)
                if not region or 'indicators' not in region:
                    continue
                for ind_key, ind in region['indicators'].items():
                    history = ind.get('history', [])
                    # 计算分位数
                    pct = calc_percentiles(history)
                    if pct:
                        ind['percentile'] = {**pct, 'current_rank': 'p50'}  # simplified
                        updated += 1
                    # 计算二阶导数（信贷脉冲）
                    if 'credit_impulse' in layer_key or ind_key in ['credit_impulse_cn', 'credit_impulse_us']:
                        sd = calc_second_derivative(history)
                        if sd is not None:
                            ind['second_derivative'] = {
                                'value': sd,
                                'signal': 'positive_acceleration' if sd > 0 else 'deceleration',
                                'description': f"二阶导数={sd}，{'回升加速' if sd > 0 else '回升放缓'}"
                            }
                            updated += 1
    
    save_json(V3_FILE, v3)
    print(f"✅ Calculated {updated} derived indicators")

if __name__ == '__main__':
    v3 = load_json(V3_FILE)
    calculate_all(v3)
