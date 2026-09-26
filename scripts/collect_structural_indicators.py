#!/usr/bin/env python3
"""collect_structural_indicators.py

为以下此前「无趋势图」的指标采集/构造真实历史序列，写入 data/cycle_position_v3.json，
随后由 build_cycle_position_v4.py 自然流入 v4 前端契约：

  达里奥·大债务周期（layer_0_debt_cycle.us）
    - interest_to_revenue：用 FRED A091RC1Q027SBEA（联邦政府利息支出）/
      W006RC1Q027SBEA（联邦政府经常性收入）构造季频比率，替换原有 6 个粗点
  高利率时代跟踪 high_rate_tracker（9 槽中补 5 槽）
    - [0] 全球储蓄-投资缺口：World Bank 世界总量 NY.GNS.ICTR.ZS − NE.GDI.FTOT.ZS
    - [1] 美国利息/财政收入：与 L0 同源季频比率
    - [2] TFP 5 年均值：PWT 10.0 rtfpna 逐年增速的 5 年滚动均值（美国）
    - [3] ECI 5 年均值：Harvard Growth Lab ECI（eci_hs92）逐年增速的 5 年滚动均值（美国）
    - [8] 核心 PCE：FRED PCEPILFE 指数同比
  结构性指标（layer_3_rate_regime.structural.income_inequality）
    - us/cn 基尼系数：World Bank SI.POV.GINI（分数口径，0–1）
  朱格拉周期·中国（layer_4_juglar.cn）
    - industrial_capacity_utilization：国家统计局季度新闻稿序列
      data/external/cn_capacity_utilization.csv（逐点附来源 URL）

设计原则：
  * 只写真源数据；外部源拉取失败时回退 data/external/ 已归档文件，并在输出中标注；
    两者都不可用则跳过该指标（保留原状），绝不留造数。
  * 全量重算覆盖 history，确定性输出；多次运行结果一致（仅 last_updated/顶层时间变化）。
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
EXTERNAL_DIR = DATA_DIR / 'external'
V3_FILE = DATA_DIR / 'cycle_position_v3.json'

FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
FRED_BASE = 'https://api.stlouisfed.org/fred/series/observations'
WB_BASE = 'https://api.worldbank.org/v2/country/{country}/indicator/{indicator}'
CST = timezone(timedelta(hours=8))


def today_str():
    return datetime.now(CST).strftime('%Y-%m-%d')


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- FRED ----------------

def fetch_fred(series_id, start='1947-01-01'):
    """返回 [(iso_date, float), ...]；失败返回 None。"""
    # 1) 在线 API
    if FRED_API_KEY:
        url = (f"{FRED_BASE}?series_id={series_id}&api_key={FRED_API_KEY}"
               f"&file_type=json&observation_start={start}&sort_order=asc")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
            out = []
            for obs in payload.get('observations', []):
                if obs.get('value') not in ('.', '', None):
                    out.append((obs['date'], float(obs['value'])))
            if out:
                print(f"  [FRED] {series_id}: {len(out)} 点（在线）")
                return out
        except Exception as e:
            print(f"  [FRED] {series_id} 在线失败: {e}")
    else:
        print(f"  [FRED] FRED_API_KEY 未设置，{series_id} 尝试本地归档")
    # 2) 本地归档
    path = EXTERNAL_DIR / f'{series_id}.csv'
    if path.exists():
        out = []
        with open(path, newline='') as f:
            for row in csv.DictReader(f):
                v = row.get(series_id, '')
                if v not in ('.', '', None):
                    out.append((row['observation_date'], float(v)))
        print(f"  [FRED] {series_id}: {len(out)} 点（本地归档）")
        return out
    print(f"  [FRED] {series_id}: 无可用数据")
    return None


def to_quarter(points):
    """(iso_date, value) 月度/日度点 → {YYYYQn: value}，每季度保留最后一个观测。"""
    qmap = {}
    for d, v in points:
        y, m = int(d[:4]), int(d[5:7])
        q = (m - 1) // 3 + 1
        qmap[f'{y}Q{q}'] = v
    return qmap


# ---------------- World Bank ----------------

def fetch_wb(indicator, country='USA', min_year=None):
    """返回 [(year:int, value:float), ...]；失败返回 None。"""
    url = (WB_BASE.format(country=country, indicator=indicator)
           + '?format=json&per_page=300')
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        out = []
        for r in rows:
            if r.get('value') is not None:
                y = int(r['date'])
                if min_year is None or y >= min_year:
                    out.append((y, float(r['value'])))
        out.sort()
        print(f"  [WB] {indicator} {country}: {len(out)} 点（在线）")
        return out
    except Exception as e:
        print(f"  [WB] {indicator} {country} 失败: {e}")
        return None


# ---------------- 本地外部文件 ----------------

def load_pwt_rtfpna():
    """返回 {country: [(year, rtfpna), ...]}。"""
    path = EXTERNAL_DIR / 'pwt_rtfpna.csv'
    if not path.exists():
        return {}
    out = {}
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            out.setdefault(row['countrycode'], []).append(
                (int(row['year']), float(row['rtfpna'])))
    for k in out:
        out[k].sort()
    print(f"  [PWT] rtfpna: {', '.join(f'{k} {len(v)}点' for k, v in out.items())}")
    return out


def load_eci(country='USA'):
    """Harvard Growth Lab ECI eci_hs92，返回 [(year, eci), ...]。"""
    path = EXTERNAL_DIR / 'eci_all.csv'
    if not path.exists():
        return None
    out = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            if row['country_iso3_code'] == country and row['eci_hs92'] != '':
                out.append((int(row['year']), float(row['eci_hs92'])))
    out.sort()
    print(f"  [ECI] {country}: {len(out)} 点")
    return out


def load_capacity():
    """返回 [(date, value, source_url), ...]。"""
    path = EXTERNAL_DIR / 'cn_capacity_utilization.csv'
    if not path.exists():
        return None
    out = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            out.append((row['date'], float(row['value']), row['source_url']))
    print(f"  [NBS] 产能利用率: {len(out)} 点（人工核验序列）")
    return out


# ---------------- 计算 ----------------

def growth_5y_avg(points, decimals=2):
    """[(year, index)] → [(year, 5年滚动平均同比增速%)]。

    严禁把指数原值当百分比：先逐年 pct_change，再做 5 年滚动均值。
    """
    growth = []
    for i in range(1, len(points)):
        y0, v0 = points[i - 1]
        y1, v1 = points[i]
        if y1 == y0 + 1 and v0:
            growth.append((y1, (v1 - v0) / v0 * 100.0))
    out = []
    for i in range(len(growth)):
        window = [g for yy, g in growth[max(0, i - 4):i + 1]]
        if len(window) == 5:
            out.append((growth[i][0], round(sum(window) / 5, decimals)))
    return out


def yoy_monthly(points, decimals=2):
    """[(iso_date, 指数)] 月度点 → [(date, 同比%)]，同月对齐。"""
    by_date = {d: v for d, v in points}
    out = []
    for d, v in points:
        y, m = int(d[:4]), int(d[5:7])
        prev = f'{y - 1}-{m:02d}-01'
        if prev in by_date and by_date[prev]:
            out.append((d[:7], round((v - by_date[prev]) / by_date[prev] * 100, decimals)))
    return out


def percentile_bands(history):
    vals = sorted(h['value'] for h in history if h['value'] is not None)
    if len(vals) < 3:
        return None
    n = len(vals)
    return {'p25': round(vals[int(n * 0.25)], 2),
            'p50': round(vals[int(n * 0.50)], 2),
            'p75': round(vals[int(n * 0.75)], 2),
            'current_rank': 'p50'}


def hrt_status(value, threshold_type, threshold_value):
    """按「越过阈值=告警红；反向越过=安全绿；中间=黄」重算状态。"""
    if value is None:
        return 'gray'
    if threshold_type.startswith('greater'):
        if value > threshold_value:
            return 'red'
        if value < threshold_value * 0.8 if threshold_value > 0 else value < threshold_value:
            return 'green'
        return 'yellow'
    else:  # less_than
        if value < threshold_value:
            return 'red'
        if value > threshold_value + 1:
            return 'green'
        return 'yellow'


# ---------------- 主流程 ----------------

def main():
    print(f"📡 collect_structural_indicators @ {datetime.now(CST).isoformat()}")
    v3 = load_json(V3_FILE)
    layers = v3['cycle_layers']
    hrt = v3['cross_analysis']['high_rate_tracker']['indicators']

    # ---- FRED 原始序列 ----
    interest = fetch_fred('A091RC1Q027SBEA')   # 联邦利息支出
    revenue = fetch_fred('W006RC1Q027SBEA')    # 联邦经常性收入
    pce = fetch_fred('PCEPILFE')               # 核心 PCE 价格指数

    # ---- 利息/财政收入 季频比率 ----
    interest_ratio = None
    if interest and revenue:
        qi, qr = to_quarter(interest), to_quarter(revenue)
        interest_ratio = [(q, round(qi[q] / qr[q] * 100, 2))
                          for q in sorted(qi) if q in qr and qr[q]]
        print(f"  → 利息/财政收入季频比率: {len(interest_ratio)} 点，"
              f"末值 {interest_ratio[-1][1]}% ({interest_ratio[-1][0]})")

    # ========== L0 达里奥：interest_to_revenue ==========
    l0_us = layers['layer_0_debt_cycle']['us']['indicators']
    if interest_ratio:
        ind = l0_us['interest_to_revenue']
        ind['history'] = [{'date': d, 'value': v} for d, v in interest_ratio]
        ind['current'] = interest_ratio[-1][1]
        ind['frequency'] = 'quarterly'
        ind['last_updated'] = today_str()
        ind['source'] = 'FRED：联邦政府利息支出 A091RC1Q027SBEA / 经常性收入 W006RC1Q027SBEA'
        ind['source_url'] = 'https://fred.stlouisfed.org/series/A091RC1Q027SBEA'
        ind['percentile'] = percentile_bands(ind['history'])
        print(f"  ✅ L0.interest_to_revenue 已替换为 {len(interest_ratio)} 季频真点")

    # ========== HRT[1] 美国利息/财政收入 ==========
    if interest_ratio:
        h = hrt[1]
        h['history'] = [{'date': d, 'value': v} for d, v in interest_ratio]
        h['current_value'] = interest_ratio[-1][1]
        h['frequency'] = 'quarterly'
        h['status'] = hrt_status(h['current_value'], h['threshold_type'], h['threshold_value'])
        print(f"  ✅ HRT[1] 美国利息/财政收入 current={h['current_value']} status={h['status']}")

    # ========== World Bank：基尼系数（双区域） ==========
    gini_us = fetch_wb('SI.POV.GINI', 'USA')
    gini_cn = fetch_wb('SI.POV.GINI', 'CHN')
    income_ineq = layers['layer_3_rate_regime']['structural']['indicators']['income_inequality']
    for region, data in (('us', gini_us), ('cn', gini_cn)):
        if not data:
            continue
        sub = income_ineq[region]
        sub['history'] = [{'date': str(y), 'value': round(v / 100, 3)} for y, v in data]
        sub['current'] = round(data[-1][1] / 100, 3)
        sub['unit'] = '分数(0-1)'
        sub['last_updated'] = today_str()
        print(f"  ✅ income_inequality.{region}: {len(data)} 点，最新 {data[-1]}")
    income_ineq['source'] = 'World Bank WDI：SI.POV.GINI（基尼系数）'
    income_ineq['source_url'] = 'https://data.worldbank.org/indicator/SI.POV.GINI'

    # ========== HRT[0] 全球储蓄-投资缺口 ==========
    gns = fetch_wb('NY.GNS.ICTR.ZS', 'WLD')   # 总储蓄/GDP
    gdi = fetch_wb('NE.GDI.FTOT.ZS', 'WLD')   # 资本形成/GDP
    if gns and gdi:
        m_gns, m_gdi = dict(gns), dict(gdi)
        gap = [(y, round(m_gns[y] - m_gdi[y], 2)) for y in sorted(m_gns) if y in m_gdi]
        h = hrt[0]
        h['history'] = [{'date': str(y), 'value': v} for y, v in gap]
        h['current_value'] = gap[-1][1]
        h['status'] = hrt_status(h['current_value'], h['threshold_type'], h['threshold_value'])
        print(f"  ✅ HRT[0] 储蓄-投资缺口: {len(gap)} 点，末值 {gap[-1]}")

    # ========== HRT[2] TFP 5 年均值 ==========
    pwt = load_pwt_rtfpna()
    if 'USA' in pwt:
        tfp5 = growth_5y_avg(pwt['USA'])
        h = hrt[2]
        h['history'] = [{'date': str(y), 'value': v} for y, v in tfp5]
        h['current_value'] = tfp5[-1][1]
        h['status'] = hrt_status(h['current_value'], h['threshold_type'], h['threshold_value'])
        print(f"  ✅ HRT[2] TFP 5年均值: {len(tfp5)} 点，末值 {tfp5[-1]} status={h['status']}")

    # ========== HRT[3] ECI 5 年均值 ==========
    eci = load_eci('USA')
    if eci:
        eci5 = growth_5y_avg(eci)
        h = hrt[3]
        h['history'] = [{'date': str(y), 'value': v} for y, v in eci5]
        h['current_value'] = eci5[-1][1]
        h['status'] = hrt_status(h['current_value'], h['threshold_type'], h['threshold_value'])
        print(f"  ✅ HRT[3] ECI 5年均值: {len(eci5)} 点，末值 {eci5[-1]} status={h['status']}")

    # ========== HRT[8] 核心 PCE 同比 ==========
    if pce:
        pce_yoy = yoy_monthly(pce)
        h = hrt[8]
        h['history'] = [{'date': d, 'value': v} for d, v in pce_yoy]
        h['current_value'] = pce_yoy[-1][1]
        h['status'] = hrt_status(h['current_value'], h['threshold_type'], h['threshold_value'])
        print(f"  ✅ HRT[8] 核心PCE: {len(pce_yoy)} 点，末值 {pce_yoy[-1]} status={h['status']}")

    # ========== 朱格拉·中国：工业产能利用率 ==========
    capacity = load_capacity()
    if capacity:
        ind = layers['layer_4_juglar']['cn']['indicators']['industrial_capacity_utilization']
        ind['history'] = [{'date': d, 'value': v} for d, v, _ in capacity]
        ind['current'] = capacity[-1][1]
        ind['last_updated'] = today_str()
        ind['source'] = '国家统计局季度规模以上工业产能利用率新闻稿（逐点人工核验）'
        ind['source_url'] = capacity[-1][2]
        ind['percentile'] = percentile_bands(ind['history'])
        print(f"  ✅ 产能利用率: {len(capacity)} 点，current={ind['current']}")

    # ---- 顶层元信息 ----
    v3['_meta']['update_time'] = datetime.now(CST).strftime('%Y-%m-%dT%H:%M:%S+08:00')
    save_json(V3_FILE, v3)
    print("✅ 已写入 data/cycle_position_v3.json")


if __name__ == '__main__':
    main()
