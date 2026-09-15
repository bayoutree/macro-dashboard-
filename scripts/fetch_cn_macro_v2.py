#!/usr/bin/env python3
"""fetch_cn_macro_v2.py - 扩展版中国宏观数据抓取，含PMI分项/设备投资/资产价格"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import re
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'


def safe_call(func, *args, **kwargs):
    """安全调用AKShare接口"""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, pd.DataFrame):
            print(f"  ✓ {func.__name__}: {len(result)} 行")
        else:
            print(f"  ✓ {func.__name__}: 返回成功")
        return result
    except Exception as e:
        print(f"  ✗ {func.__name__}: {e}")
        return pd.DataFrame()


def fetch_cn_macro():
    """获取中国宏观经济数据（扩展版）"""
    import akshare as ak
    print(f"📡 Fetching 中国宏观数据 v2 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    
    result = {
        'update_time': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'indicators': {},
        'prices': {},
        'history': {},
    }

    # ============================================================
    # 1. ISM美国PMI (用于美林时钟美国端)
    # ============================================================
    print("\n[1/8] ISM美国制造业PMI...")
    ism = safe_call(ak.macro_usa_ism_pmi)
    if not ism.empty:
        ism_sorted = ism.sort_values('日期').reset_index(drop=True)
        ism_history = []
        for _, row in ism_sorted.iterrows():
            date_val = str(row['日期'])[:10]
            val = float(row['今值']) if pd.notna(row['今值']) else None
            if val is not None:
                ism_history.append({'date': date_val, 'value': val})
        result['history']['us_ism_pmi'] = ism_history
        result['indicators']['us_ism_pmi'] = {
            'current': ism_history[-1]['value'] if ism_history else None,
            'date': ism_history[-1]['date'] if ism_history else None,
            'history_count': len(ism_history),
        }
        print(f"  ISM PMI: {len(ism_history)} pts")

    # ============================================================
    # 2. 沪深300月频
    # ============================================================
    print("\n[2/8] 沪深300指数...")
    hs300 = safe_call(ak.stock_zh_index_daily, symbol="sh000300")
    if not hs300.empty and 'close' in hs300.columns:
        hs300['date'] = pd.to_datetime(hs300['date'])
        monthly = hs300.set_index('date')['close'].resample('ME').last().dropna()
        result['prices']['hs300'] = [
            {'date': d.strftime('%Y-%m'), 'close': round(v, 2)}
            for d, v in zip(monthly.index, monthly.values)
        ]
        result['indicators']['hs300'] = {
            'current': round(float(monthly.iloc[-1]), 2),
            'date': monthly.index[-1].strftime('%Y-%m'),
        }
        print(f"  沪深300月频: {len(result['prices']['hs300'])} months ({monthly.index[0].strftime('%Y-%m')} ~ {monthly.index[-1].strftime('%Y-%m')})")

    # ============================================================
    # 3. 中国10Y国债收益率月频
    # ============================================================
    print("\n[3/8] 中国10Y国债收益率...")
    cn_bond = safe_call(ak.bond_china_yield, start_date='20220101', end_date='20260915')
    if not cn_bond.empty and '10年' in cn_bond.columns:
        gov = cn_bond[cn_bond['曲线名称'] == '中债国债收益率曲线'].copy()
        if not gov.empty:
            gov['日期'] = pd.to_datetime(gov['日期'])
            ten_y = gov.set_index('日期')['10年'].dropna()
            monthly = ten_y.resample('ME').last()
            result['prices']['cn_10y_yield'] = [
                {'date': d.strftime('%Y-%m'), 'yield': round(v, 4)}
                for d, v in zip(monthly.index, monthly.values)
            ]
            result['indicators']['cn_10y_yield'] = {
                'current': round(float(monthly.iloc[-1]), 4),
                'date': monthly.index[-1].strftime('%Y-%m'),
            }
            print(f"  中债10Y月频: {len(result['prices']['cn_10y_yield'])} months")
    
    # 备用: sina数据（更长一点的历史）
    if 'cn_10y_yield' not in result['prices']:
        cn10y_sina = safe_call(ak.bond_gb_zh_sina, symbol='中国10年期国债')
        if not cn10y_sina.empty and 'close' in cn10y_sina.columns:
            cn10y_sina['date'] = pd.to_datetime(cn10y_sina['date'])
            monthly = cn10y_sina.set_index('date')['close'].resample('ME').last().dropna()
            result['prices']['cn_10y_yield'] = [
                {'date': d.strftime('%Y-%m'), 'yield': round(v, 4)}
                for d, v in zip(monthly.index, monthly.values)
            ]
            result['indicators']['cn_10y_yield'] = {
                'current': round(float(monthly.iloc[-1]), 4),
                'date': monthly.index[-1].strftime('%Y-%m'),
            }
            print(f"  中债10Y月频(sina): {len(result['prices']['cn_10y_yield'])} months")

    # ============================================================
    # 4. 中国工业增加值 (替代产能利用率)
    # ============================================================
    print("\n[4/8] 中国工业增加值...")
    ind_prod = safe_call(ak.macro_china_industrial_production_yoy)
    if not ind_prod.empty:
        ind_sorted = ind_prod.sort_values('日期').reset_index(drop=True)
        ind_history = []
        for _, row in ind_sorted.iterrows():
            date_val = str(row['日期'])[:10]
            val = float(row['今值']) if pd.notna(row['今值']) else None
            if val is not None:
                ind_history.append({'date': date_val, 'value': val})
        result['history']['cn_industrial_value_added'] = ind_history
        result['indicators']['cn_industrial_value_added'] = {
            'current': ind_history[-1]['value'] if ind_history else None,
            'date': ind_history[-1]['date'] if ind_history else None,
            'history_count': len(ind_history),
        }
        print(f"  工业增加值: {len(ind_history)} pts")

    # ============================================================
    # 5. 中国PMI主指数 (已有, 补充月频历史)
    # ============================================================
    print("\n[5/8] 中国PMI主指数...")
    pmi = safe_call(ak.macro_china_pmi)
    if not pmi.empty and '制造业-指数' in pmi.columns:
        pmi_sorted = pmi.sort_values('月份').reset_index(drop=True)
        pmi_history = []
        for _, row in pmi_sorted.iterrows():
            raw = str(row['月份'])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw)
            if m:
                date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                val = float(row['制造业-指数']) if pd.notna(row['制造业-指数']) else None
                if val is not None:
                    pmi_history.append({'date': date_label, 'value': val})
        result['history']['cn_pmi'] = pmi_history
        result['indicators']['cn_pmi'] = {
            'current': pmi_history[-1]['value'] if pmi_history else None,
            'date': pmi_history[-1]['date'] if pmi_history else None,
        }
        print(f"  PMI历史: {len(pmi_history)} months")

    # ============================================================
    # 6. 财新PMI (作为PMI分项的替代)
    # ============================================================
    print("\n[6/8] 财新PMI...")
    cx_pmi = safe_call(ak.macro_china_cx_pmi_yearly)
    if not cx_pmi.empty:
        cx_sorted = cx_pmi.sort_values('日期').reset_index(drop=True)
        cx_history = []
        for _, row in cx_sorted.iterrows():
            date_val = str(row['日期'])[:10]
            val = float(row['今值']) if pd.notna(row['今值']) else None
            if val is not None:
                cx_history.append({'date': date_val, 'value': val})
        result['history']['cn_caixin_pmi'] = cx_history
        result['indicators']['cn_caixin_pmi'] = {
            'current': cx_history[-1]['value'] if cx_history else None,
            'date': cx_history[-1]['date'] if cx_history else None,
        }
        print(f"  财新PMI: {len(cx_history)} pts")

    # ============================================================
    # 7. 社融存量 (用于信用周期)
    # ============================================================
    print("\n[7/8] 社会融资规模...")
    shrzgm = safe_call(ak.macro_china_shrzgm)
    if not shrzgm.empty:
        shrzgm_sorted = shrzgm.sort_values('月份').reset_index(drop=True)
        sf_history = []
        for _, row in shrzgm_sorted.iterrows():
            raw_month = str(row['月份'])
            if len(raw_month) >= 6:
                date_label = f"{raw_month[:4]}-{raw_month[4:6]}"
                val = float(row['社会融资规模增量']) if pd.notna(row['社会融资规模增量']) else None
                if val is not None:
                    sf_history.append({'date': date_label, 'value': val})
        result['history']['cn_social_financing'] = sf_history
        result['indicators']['cn_social_financing'] = {
            'current': sf_history[-1]['value'] if sf_history else None,
            'date': sf_history[-1]['date'] if sf_history else None,
        }
        print(f"  社融: {len(sf_history)} pts")

    # ============================================================
    # 8. 中国黄金 (上海金 - 可能失败)
    # ============================================================
    print("\n[8/8] 中国黄金价格...")
    try:
        gold = safe_call(ak.spot_golden_benchmark_sge)
        if not gold.empty:
            gold['date'] = pd.to_datetime(gold.iloc[:, 0]) if gold.columns[0] != 'date' else pd.to_datetime(gold['date'])
            price_col = [c for c in gold.columns if '价' in str(c) or 'close' in str(c).lower()]
            if price_col:
                monthly = gold.set_index('date')[price_col[0]].resample('ME').last().dropna()
                result['prices']['cn_gold'] = [
                    {'date': d.strftime('%Y-%m'), 'price': round(float(v), 2)}
                    for d, v in zip(monthly.index, monthly.values)
                ]
                print(f"  黄金月频: {len(result['prices']['cn_gold'])} months")
    except Exception as e:
        print(f"  ⚠️ 黄金数据获取失败: {e}")

    # ============================================================
    # 标注缺失项
    # ============================================================
    result['data_availability'] = {
        'available': [
            'us_ism_pmi', 'cn_pmi', 'cn_caixin_pmi',
            'cn_industrial_value_added', 'cn_social_financing',
            'hs300', 'cn_10y_yield',
        ],
        'unavailable': [
            'cn_pmi_inventory_subitem (AKShare不暴露PMI分项)',
            'cn_pmi_new_orders_subitem (AKShare不暴露PMI分项)',
            'cn_equipment_investment (FAI分项AKShare无接口)',
            'cn_capacity_utilization (中国无直接数据)',
            'cn_industrial_inventory (工业企业库存数据AKShare无)',
            'cn_gold (上海金连接失败)',
        ],
        'alternatives_suggested': {
            'cn_capacity_utilization': '使用工业增加值同比作为代理',
            'cn_equipment_investment': '使用高技术制造业投资或工业增加值',
            'cn_pmi_inventory': '使用财新PMI作为替代参考',
        }
    }

    return result


def save_results(result):
    """保存结果"""
    DATA_DIR.mkdir(exist_ok=True)

    # 保存完整的中国宏观数据
    output_path = DATA_DIR / 'cn_macro_v2.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ cn_macro_v2.json saved")

    # 同时更新资产价格合并文件
    prices_path = DATA_DIR / 'asset_prices_monthly.json'
    if prices_path.exists():
        with open(prices_path, 'r') as f:
            prices_data = json.load(f)
    else:
        prices_data = {'update_time': '', 'prices': {}}

    # 合并中国资产价格
    if 'prices' in result:
        for key, data in result['prices'].items():
            prices_data['prices'][key] = data
    prices_data['update_time'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    with open(prices_path, 'w', encoding='utf-8') as f:
        json.dump(prices_data, f, ensure_ascii=False, indent=2)
    print(f"✅ asset_prices_monthly.json updated with CN data")

    return output_path, prices_path


if __name__ == '__main__':
    result = fetch_cn_macro()
    save_results(result)
    print("\n🎉 中国宏观数据v2抓取完成")
    
    # 打印数据可用性摘要
    avail = result.get('data_availability', {})
    print(f"\n✅ 可获取: {len(avail.get('available', []))} 项")
    print(f"❌ 不可获取: {len(avail.get('unavailable', []))} 项")
    for item in avail.get('unavailable', []):
        print(f"  - {item}")
