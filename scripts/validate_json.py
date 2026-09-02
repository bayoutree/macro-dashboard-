#!/usr/bin/env python3
"""validate_json.py - 验证cycle_position_v3.json格式正确性"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
V3_FILE = DATA_DIR / 'cycle_position_v3.json'

REQUIRED_TOP_KEYS = ['_meta', 'cycle_layers', 'cross_analysis', 'asset_allocation', 'synthesis']
REQUIRED_LAYERS = [
    'layer_0_debt_cycle', 'layer_1_kondratieff', 'layer_2_perez',
    'layer_3_rate_regime', 'layer_4_juglar', 'layer_5_kitchner', 'layer_6_merrill'
]

def validate():
    errors = []
    warnings = []
    
    try:
        with open(V3_FILE, 'r', encoding='utf-8') as f:
            v3 = json.load(f)
    except json.JSONDecodeError as e:
        print(f" JSON parse error: {e}")
        sys.exit(1)
    
    # Check top-level keys
    for key in REQUIRED_TOP_KEYS:
        if key not in v3:
            errors.append(f"Missing top-level key: {key}")
    
    # Check cycle layers
    layers = v3.get('cycle_layers', {})
    for layer in REQUIRED_LAYERS:
        if layer not in layers:
            errors.append(f"Missing cycle layer: {layer}")
    
    # Check _meta
    meta = v3.get('_meta', {})
    if 'update_time' not in meta:
        warnings.append("_meta.update_time missing")
    if 'data_freshness_score' not in meta:
        warnings.append("_meta.data_freshness_score missing")
    
    # Check each layer has signal_weight
    for layer_name, layer_data in layers.items():
        if isinstance(layer_data, dict):
            for region in ['us', 'cn']:
                r = layer_data.get(region)
                if isinstance(r, dict) and 'signal_weight' not in r:
                    warnings.append(f"{layer_name}.{region} missing signal_weight")
    
    # Check asset_allocation
    alloc = v3.get('asset_allocation', {})
    if 'current' not in alloc:
        errors.append("asset_allocation.current missing")
    else:
        for item in alloc['current']:
            if 'asset' not in item:
                errors.append("asset_allocation.current item missing 'asset'")
            if 'risk_reward_score' not in item:
                warnings.append(f"asset {item.get('asset','?')} missing risk_reward_score")
    
    # Check cross_analysis
    cross = v3.get('cross_analysis', {})
    if 'consensus' not in cross:
        warnings.append("cross_analysis.consensus missing")
    if 'us_china_matrix' not in cross:
        warnings.append("cross_analysis.us_china_matrix missing")
    
    # Print results
    if errors:
        print(f" {len(errors)} errors:")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print(f"️ {len(warnings)} warnings:")
        for w in warnings:
            print(f"  - {w}")
    if not errors and not warnings:
        print("✅ JSON validation passed!")
    
    file_size = V3_FILE.stat().st_size
    print(f"📄 File size: {file_size:,} bytes")
    
    if errors:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    validate()
