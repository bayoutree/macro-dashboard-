#!/usr/bin/env python3
"""update_json.py - 将FRED原始数据更新到cycle_position_v3.json"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # 保证 run_all 以任意 cwd 调用时都能 import
from freshness_score import compute_freshness, is_assessment_overdue  # P0-1: 新鲜度单一真值算法

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
        # [V22-5 2026-11-07] 对齐 v3 实际结构：forward_looking/market_based 均带
        # indicators 层，旧键名（breakeven_5y5y/breakeven_30y/acm_term_premium/
        # hy_spread/core_pce）在 v3 不存在→静默落空，breakeven_inflation 停更
        # 2026-09-02 两月余（CIO 11-07 实测配套发现）。删除无目标键映射。
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'indicators', 'breakeven_inflation'), 't30yiem'),
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'indicators', 'real_rate_forward'), 'dfii10'),
        (('cycle_layers', 'layer_3_rate_regime', 'forward_looking', 'indicators', 'term_premium'), 'termprem10'),
        (('cycle_layers', 'layer_3_rate_regime', 'market_based', 'indicators', 'yield_curve_2s10s'), 't10y2y'),
        # [V22-3 2026-11-07] IG利差接真源（BAMLC0A0CM，%口径）——原值停2026-09-02且
        # 198/203点混入0.1bp单位(3572.23等)，由下方清洗段先÷1000归一+建顶层history
        (('cycle_layers', 'layer_3_rate_regime', 'market_based', 'indicators', 'credit_spread_ig'), 'ig_oas'),
        (('cycle_layers', 'layer_6_merrill', 'us', 'indicators', 'unemployment_rate'), 'unrate'),
    }
    
    # [V22-3 2026-11-07] market_based 三序列修复前置：
    # ①credit_spread_ig 历史单位混入（≥100 判 0.1bp，÷1000 → %，与 build_cycle_position_v4 同口径）
    # ②credit_spread_ig/realized_volatility 无顶层 history（数据只在 us.history）→ 迁移建顶层，
    #   否则 mappings 的 'history' in obj 判空静默跳过（breakeven 同款静默断更的教训）
    _mb = v3.get('cycle_layers', {}).get('layer_3_rate_regime', {}).get('market_based', {}).get('indicators', {})
    _csi = _mb.get('credit_spread_ig')
    if _csi is not None:
        # 幂等单位归一：≥100 判 0.1bp 单位 ÷1000 → %（顶层/us 两侧都清，已归一的 <100 不再动）
        _fixed = 0
        for _hk in ('history',):
            for _h in (_csi.get(_hk) or []):
                if isinstance(_h, dict) and _h.get('value', 0) >= 100:
                    _h['value'] = round(_h['value'] / 1000, 4); _fixed += 1
        for _h in ((_csi.get('us') or {}).get('history') or []):
            if isinstance(_h, dict) and _h.get('value', 0) >= 100:
                _h['value'] = round(_h['value'] / 1000, 4); _fixed += 1
        # 顶层 history 缺失时从 us.history 迁移（防 mappings 'history' in obj 判空静默跳过）
        if not _csi.get('history') and (_csi.get('us') or {}).get('history'):
            _csi['history'] = list(_csi['us']['history'])
            print(f"  🔧 credit_spread_ig 顶层history建立: {len(_csi['history'])}点")
        if _fixed:
            print(f"  🔧 credit_spread_ig 单位归一: {_fixed}点 0.1bp→%")
        _csi['unit'] = '%'
        _csi['source'] = 'FRED BAMLC0A0CM'
    _rv = _mb.get('realized_volatility')
    if _rv is not None and not _rv.get('history'):
        _uh = (_rv.get('us') or {}).get('history') or []
        if _uh:
            _rv['history'] = list(_uh)
            print(f"  🔧 realized_volatility 顶层history建立: {len(_uh)}点")

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
    
    # [V22-3 2026-11-07] realized_volatility 自算续更：sp500 日收益 20 日年化实现波动率
    # 原值停 2026-09-02 无源（标 CBOE 但无 fetch 链路）；源=asset_prices.json sp500.daily
    try:
        import math as _math
        _ap = json.load(open(DATA_DIR / 'asset_prices.json', encoding='utf-8'))
        _sp = (_ap.get('prices', {}).get('sp500', {}) or {}).get('daily') or []
        _closes = [(d['date'], float(d['value'])) for d in _sp
                   if isinstance(d, dict) and d.get('value') is not None]  # daily条目为{date,value}
        _rv = _mb.get('realized_volatility')
        if _rv is not None and len(_closes) > 21:
            _exist = {h['date'] for h in (_rv.get('history') or [])}
            _added = 0
            for i in range(21, len(_closes)):
                _rets = [_math.log(_closes[j][1] / _closes[j-1][1]) for j in range(i - 20, i)]
                _mu = sum(_rets) / 20
                _var = sum((r - _mu) ** 2 for r in _rets) / 19
                _rv20 = round(_math.sqrt(_var * 252) * 100, 2)  # 年化%
                _dt = _closes[i][0]
                if _dt not in _exist:
                    _rv.setdefault('history', []).append({'date': _dt, 'value': _rv20})
                    _exist.add(_dt); _added += 1
            if _added:
                _rv['history'].sort(key=lambda x: x['date'])
                _rv['current'] = _rv['history'][-1]['value']
                _rv['last_updated'] = datetime.now().strftime('%Y-%m-%d')
                _rv['source'] = '自算: sp500日收益20日年化(资产价格层)'
                print(f"  ✅ realized_volatility 自算续更 +{_added}点 尾{_rv['history'][-1]['date']}={_rv['current']}")
    except Exception as _e:
        print(f"  ⚠ realized_volatility 自算失败(不阻断): {_e}")

    # [V22-3 2026-11-07] cn_real_rate 自算续更：中债10Y月频 - CPI同比月频
    # 原值12点半年频停2026-01无源(标NBS/FRED无链路)；月频口径，仅追加新月份不覆盖旧半年点
    try:
        _ap = json.load(open(DATA_DIR / 'asset_prices_monthly.json', encoding='utf-8'))
        _y10 = {d['date']: d['yield'] for d in _ap.get('prices', {}).get('cn_10y_yield') or []
                if isinstance(d, dict) and d.get('yield') is not None}
        _cn = json.load(open(DATA_DIR / 'cn_macro.json', encoding='utf-8'))
        _cpi = {d['date']: d['value'] for d in _cn.get('history', {}).get('cpi_yoy') or []
                if isinstance(d, dict) and d.get('value') is not None}
        _crr = _mb.get('cn_real_rate')
        _crrh = (_crr.get('cn') or {}).get('history') if _crr else None
        if _crr is not None:
            _tgt = _crr.setdefault('history', [])
            _exist = {h['date'] for h in _tgt}
            _added = 0
            for _m in sorted(set(_y10) & set(_cpi)):
                if _m not in _exist:
                    _tgt.append({'date': _m, 'value': round(_y10[_m] - _cpi[_m], 2)})
                    _exist.add(_m); _added += 1
            if _added:
                _tgt.sort(key=lambda x: x['date'])
                _crr['current'] = _tgt[-1]['value']
                _crr['last_updated'] = datetime.now().strftime('%Y-%m-%d')
                _crr['source'] = '自算: 中债10Y月频-CPI同比(资产价格层+宏观层)'
                print(f"  ✅ cn_real_rate 自算续更 +{_added}点 尾{_tgt[-1]['date']}={_crr['current']}")
    except Exception as _e:
        print(f"  ⚠ cn_real_rate 自算失败(不阻断): {_e}")

    # 更新_meta
    v3['_meta']['update_time'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')
    # P0-1（2026-11-01 CIO）：废除「每次运行 +5 封顶 100」的假新鲜度——
    # 它与数据真实年龄脱钩，导致页面长期谎报 100。改为按各层
    # last_updated 推算（min 口径），算法单一真值见 scripts/freshness_score.py
    _score, _cutoff, _detail = compute_freshness(v3.get('cycle_layers', {}))
    v3['_meta']['data_freshness_score'] = _score
    v3['_meta']['data_cutoff_date'] = _cutoff
    v3['_meta']['freshness_detail'] = _detail
    v3['_meta']['assessment_overdue'] = is_assessment_overdue(v3['_meta'].get('next_assessment_date'))  # Codex评审建议: 与v4契约对称
    
    save_json(V3_FILE, v3)
    print(f"✅ Updated {updated} indicators in cycle_position_v3.json")

if __name__ == '__main__':
    if not RAW_FILE.exists():
        print("❌ fred_raw.json not found. Run fetch_fred_data.py first.")
    else:
        fred_raw = load_json(RAW_FILE)
        update_v3_json(fred_raw)
