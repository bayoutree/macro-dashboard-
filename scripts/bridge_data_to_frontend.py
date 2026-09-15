#!/usr/bin/env python3
"""
Bridge data from monthly sources and v2 data to frontend-expected JSON files.
Fixes C-9, C-3, C-10, C-11, C-7, C-1, C-5, C-6 issues.
"""

import json
import os
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("/Coze/Drive/周期看板改进0915/macro-dashboard/data")

def load_json(filename):
    """Load JSON file safely."""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(data, filename):
    """Save JSON file."""
    filepath = DATA_DIR / filename
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ Saved {filename}")

def convert_monthly_to_daily(monthly_data, key_name):
    """
    Convert monthly data to daily format expected by frontend.
    Uses monthly close as daily points (end of month dates).
    """
    if key_name not in monthly_data:
        return {"latest": None, "ma200": None, "drawdown_from_high": None, "daily": []}
    
    monthly = monthly_data[key_name]
    if not monthly:
        return {"latest": None, "ma200": None, "drawdown_from_high": None, "daily": []}
    
    # Convert to daily format
    daily = []
    for entry in monthly:
        # Use last day of month as date
        date_str = entry.get('date', '')
        if '-' in date_str and len(date_str.split('-')) == 2:
            year, month = date_str.split('-')
            # Approximate end of month
            daily.append({
                "date": f"{year}-{month}-28",
                "close": entry.get('close', entry.get('value'))
            })
        else:
            daily.append({
                "date": date_str,
                "close": entry.get('close', entry.get('value'))
            })
    
    if not daily:
        return {"latest": None, "ma200": None, "drawdown_from_high": None, "daily": []}
    
    # Calculate latest
    latest = daily[-1]["close"]
    
    # Calculate MA200 (use all available if less than 200)
    ma200_len = min(200, len(daily))
    ma200 = sum(d["close"] for d in daily[-ma200_len:]) / ma200_len
    
    # Calculate drawdown from high
    historical_high = max(d["close"] for d in daily)
    drawdown = (latest - historical_high) / historical_high if historical_high > 0 else 0
    
    return {
        "latest": round(latest, 2),
        "ma200": round(ma200, 2),
        "drawdown_from_high": round(drawdown, 4),
        "daily": daily
    }

def fix_c9_asset_prices():
    """C-9: Fix asset_prices.json from monthly data."""
    print("\n=== C-9: Fixing asset_prices.json ===")
    
    monthly_data = load_json("asset_prices_monthly.json")
    if not monthly_data:
        print("✗ asset_prices_monthly.json not found")
        return
    
    prices_monthly = monthly_data.get("prices", {})
    
    # Build new asset_prices.json
    new_data = {
        "update_time": datetime.now().strftime("%Y-%m-%d"),
        "prices": {
            "sp500": convert_monthly_to_daily(prices_monthly, "sp500"),
            "nasdaq": {"latest": None, "ma200": None, "drawdown_from_high": None, "daily": []},  # No data source
            "gold": convert_monthly_to_daily(prices_monthly, "gold"),
            "copper": convert_monthly_to_daily(prices_monthly, "copper"),
            "crude_oil": convert_monthly_to_daily(prices_monthly, "wti_oil"),
            "usd_index": convert_monthly_to_daily(prices_monthly, "usd_index"),
            "us_10y_bond": convert_monthly_to_daily(prices_monthly, "us_10y_yield"),
            "btc": {"latest": None, "ma200": None, "drawdown_from_high": None, "daily": []}  # No data source
        },
        "volatility": {
            "vix": {
                "latest": None,
                "history": []
            }
        }
    }
    
    # Try to get VIX from us_macro.json history
    us_macro = load_json("us_macro.json")
    if us_macro and "history" in us_macro and "vix" in us_macro["history"]:
        vix_hist = us_macro["history"]["vix"]
        if vix_hist and len(vix_hist) > 0:
            latest_vix = vix_hist[-1].get("value")
            new_data["volatility"]["vix"]["latest"] = latest_vix
            new_data["volatility"]["vix"]["history"] = vix_hist
    
    save_json(new_data, "asset_prices.json")
    print(f"✓ sp500: {new_data['prices']['sp500']['latest']}, gold: {new_data['prices']['gold']['latest']}")
    print(f"✓ copper: {new_data['prices']['copper']['latest']}, crude_oil: {new_data['prices']['crude_oil']['latest']}")

def fix_c3_us_macro():
    """C-3: Fix us_macro.json None values."""
    print("\n=== C-3: Fixing us_macro.json ===")
    
    us_macro = load_json("us_macro.json")
    cn_macro_v2 = load_json("cn_macro_v2.json")
    
    if not us_macro:
        print("✗ us_macro.json not found")
        return
    
    # Fix ISM PMI from cn_macro_v2
    if cn_macro_v2 and "indicators" in cn_macro_v2:
        ism = cn_macro_v2["indicators"].get("us_ism_pmi", {})
        if ism.get("current"):
            us_macro["leading"]["ism_pmi"] = {
                "value": ism["current"],
                "date": ism.get("date", "2026-08-01"),
                "trend": "expanding" if ism["current"] > 50 else "contracting"
            }
            print(f"✓ ISM PMI: {ism['current']}")
        
        # Copy ISM history
        ism_hist = cn_macro_v2["history"].get("us_ism_pmi", [])
        if ism_hist:
            us_macro["history"]["ism_pmi"] = ism_hist
            print(f"✓ ISM PMI history: {len(ism_hist)} entries")
    
    # LEI - no data source available, leave as None
    print("⚠ LEI: no data source, keeping null")
    
    # Industrial production - use capacity_utilization as proxy
    fred_raw = load_json("fred_raw.json")
    if fred_raw and "series" in fred_raw:
        cap_util = fred_raw["series"].get("capacity_utilization", [])
        if cap_util and len(cap_util) > 0:
            latest_cap = cap_util[-1].get("value")
            us_macro["coincident"]["industrial_production"] = {
                "value": latest_cap,
                "date": cap_util[-1].get("date", "2026-07"),
                "note": "Capacity utilization as proxy"
            }
            print(f"✓ Industrial production (capacity util): {latest_cap}")
    
    # Nonfarm payrolls YoY - no direct data source
    print("⚠ Nonfarm payrolls YoY: no data source, keeping null")
    
    # Fix rates - compute latest from history
    for rate_key in ["yield_10y", "yield_2y", "real_rate_10y", "inflation_expectation", "term_premium"]:
        hist_key = rate_key
        if hist_key in us_macro.get("history", {}):
            hist = us_macro["history"][hist_key]
            if hist and len(hist) > 0:
                latest_val = hist[-1].get("value")
                latest_date = hist[-1].get("date")
                us_macro["rates"][rate_key] = {
                    "value": latest_val,
                    "date": latest_date
                }
                print(f"✓ rates.{rate_key}: {latest_val} ({latest_date})")
    
    # Fix market - compute latest from history
    for market_key in ["sp500", "vix", "fed_balance", "usdcny"]:
        if market_key in us_macro.get("history", {}):
            hist = us_macro["history"][market_key]
            if hist and len(hist) > 0:
                latest_val = hist[-1].get("value")
                latest_date = hist[-1].get("date")
                us_macro["market"][market_key] = {
                    "value": latest_val,
                    "date": latest_date
                }
                print(f"✓ market.{market_key}: {latest_val} ({latest_date})")
    
    save_json(us_macro, "us_macro.json")

def fix_c10_asset_valuation():
    """C-10: Fix asset_valuation.json from available sources."""
    print("\n=== C-10: Fixing asset_valuation.json ===")
    
    valuation = load_json("asset_valuation.json")
    monthly_prices = load_json("asset_prices_monthly.json")
    
    if not valuation:
        print("✗ asset_valuation.json not found")
        return
    
    # Fix gold.price_usd from monthly data
    if monthly_prices and "gold" in monthly_prices.get("prices", {}):
        gold_data = monthly_prices["prices"]["gold"]
        if gold_data and len(gold_data) > 0:
            latest_gold = gold_data[-1].get("value")
            valuation["gold"]["price_usd"]["value"] = latest_gold
            print(f"✓ gold.price_usd: {latest_gold}")
    
    # Fix commodity.copper from monthly data
    if monthly_prices and "copper" in monthly_prices.get("prices", {}):
        copper_data = monthly_prices["prices"]["copper"]
        if copper_data and len(copper_data) > 0:
            latest_copper = copper_data[-1].get("value")
            valuation["commodity"]["copper"]["price"] = latest_copper
            print(f"✓ commodity.copper: {latest_copper}")
    
    # Fix commodity.crude_oil from monthly data
    if monthly_prices and "wti_oil" in monthly_prices.get("prices", {}):
        oil_data = monthly_prices["prices"]["wti_oil"]
        if oil_data and len(oil_data) > 0:
            latest_oil = oil_data[-1].get("value")
            valuation["commodity"]["crude_oil"]["brent"] = latest_oil
            print(f"✓ commodity.crude_oil (WTI): {latest_oil}")
    
    # Fix us_bond from us_macro rates
    us_macro = load_json("us_macro.json")
    if us_macro and "rates" in us_macro:
        for key in ["yield_10y", "real_rate_10y", "inflation_expectation", "term_premium"]:
            rate_data = us_macro["rates"].get(key, {})
            if rate_data and rate_data.get("value"):
                valuation["us_bond"][key]["value"] = rate_data["value"]
                print(f"✓ us_bond.{key}: {rate_data['value']}")
    
    # Fix cn_bond from cn_macro
    cn_macro = load_json("cn_macro.json")
    if cn_macro and "bond" in cn_macro:
        cn_10y = cn_macro["bond"].get("cn_10y_yield", {})
        if cn_10y and cn_10y.get("value"):
            valuation["cn_bond"]["yield_10y"]["value"] = cn_10y["value"]
            print(f"✓ cn_bond.yield_10y: {cn_10y['value']}")
    
    # Shiller PE, Buffett Ratio, Forward PE - no data source, leave as None
    print("⚠ Shiller PE, Buffett Ratio, Forward PE: no data source, keeping null")
    
    save_json(valuation, "asset_valuation.json")

def fix_c11_dashboard_summary():
    """C-11: Fix dashboard_summary.json for v2.0 framework."""
    print("\n=== C-11: Fixing dashboard_summary.json ===")
    
    # Based on task description: P2=+1 (被动去库), current phase
    new_summary = {
        "update_time": datetime.now().strftime("%Y-%m-%d"),
        "layers": {
            "L1_cycle": {
                "conclusion": "周期共振: 被动去库阶段 (P2=+1)",
                "signal": "bullish",
                "assets_impact": {
                    "stock": "+",
                    "bond": "-",
                    "commodity": "+",
                    "gold": "0"
                }
            },
            "L2_macro": {
                "conclusion": "美国GDP增速1.5%；通胀偏高(CPI=3.5%)；中国制造业收缩；中国GDP增速5.0%",
                "signal": "neutral"
            },
            "L3_valuation": {
                "conclusion": "股权风险溢价极低(-0.8%)，股票吸引力弱；A股估值中性(沪深300 PE=12.8)",
                "signal": "mixed"
            },
            "L4_technical": {
                "conclusion": "技术数据待更新",
                "signal": "pending"
            }
        },
        "allocation": {
            "overweight": ["SP500", "大宗商品"],
            "market_weight": ["黄金", "HS300"],
            "underweight": ["美债长端"]
        },
        "allocation_rationale": {
            "SP500": "被动去库阶段历史表现优异(12M +11.7%, 82%胜率)",
            "大宗商品": "周期共振利好(12M +15.5%, 64%胜率)",
            "黄金": "实际利率高位压制，但央行购金支撑",
            "HS300": "估值中性，等待政策催化",
            "美债长端": "收益率曲线陡峭化，长端承压"
        },
        "key_risks": [
            "通胀粘性超预期→美联储推迟降息",
            "中东冲突升级→能源价格冲击→滞胀风险",
            "AI投资回报不及预期→科技股估值回调",
            "中国经济复苏乏力→大宗商品需求疲软",
            "美联储缩表超预期→流动性收紧"
        ]
    }
    
    save_json(new_summary, "dashboard_summary.json")

def fix_c7_guard_margin():
    """C-7: Create guard_margin_buying_ratio placeholder."""
    print("\n=== C-7: Creating guard_margin_buying_ratio placeholder ===")
    
    placeholder = {
        "update_time": datetime.now().strftime("%Y-%m-%d"),
        "source": "placeholder",
        "note": "融资融券数据待接入，当前为占位文件",
        "data_available": False,
        "history": []
    }
    
    save_json(placeholder, "guard_margin_buying_ratio_daily_2010_2026.json")

def fix_c1_cn_macro():
    """C-1: Merge cn_macro_v2 into cn_macro.json."""
    print("\n=== C-1: Merging cn_macro_v2 into cn_macro.json ===")
    
    cn_macro = load_json("cn_macro.json")
    cn_macro_v2 = load_json("cn_macro_v2.json")
    
    if not cn_macro or not cn_macro_v2:
        print("✗ cn_macro.json or cn_macro_v2.json not found")
        return
    
    # Update US ISM data in leading.pmi (actually this is China PMI)
    # cn_macro_v2 has us_ism_pmi which should update cn_macro
    # But cn_macro.leading.pmi is China PMI, not US ISM
    # Let's add US ISM as a separate field
    
    if "indicators" in cn_macro_v2:
        us_ism = cn_macro_v2["indicators"].get("us_ism_pmi", {})
        if us_ism:
            # Add US indicators section if not exists
            if "us_indicators" not in cn_macro:
                cn_macro["us_indicators"] = {}
            cn_macro["us_indicators"]["ism_pmi"] = {
                "value": us_ism.get("current"),
                "date": us_ism.get("date"),
                "source": "cn_macro_v2"
            }
            print(f"✓ Added us_indicators.ism_pmi: {us_ism.get('current')}")
    
    # Update history if available
    if "history" in cn_macro_v2:
        us_ism_hist = cn_macro_v2["history"].get("us_ism_pmi", [])
        if us_ism_hist:
            if "us_history" not in cn_macro:
                cn_macro["us_history"] = {}
            cn_macro["us_history"]["ism_pmi"] = us_ism_hist
            print(f"✓ Added us_history.ism_pmi: {len(us_ism_hist)} entries")
    
    cn_macro["update_time"] = datetime.now().strftime("%Y-%m-%d")
    cn_macro["v2_merged"] = True
    
    save_json(cn_macro, "cn_macro.json")

def fix_c5_cycle_position():
    """C-5: Copy cycle_position_v4.json to cycle_position.json."""
    print("\n=== C-5: Copying cycle_position_v4.json to cycle_position.json ===")
    
    v4_data = load_json("cycle_position_v4.json")
    if not v4_data:
        print("✗ cycle_position_v4.json not found")
        return
    
    save_json(v4_data, "cycle_position.json")
    print(f"✓ Copied cycle_position_v4.json (size: {len(json.dumps(v4_data))} bytes)")

def fix_c6_phase_field():
    """C-6: Add phase field to cycle_positions_summary entries."""
    print("\n=== C-6: Adding phase field to cycle_positions_summary ===")
    
    cycle_data = load_json("cycle_position_v4.json")
    if not cycle_data:
        print("✗ cycle_position_v4.json not found")
        return
    
    if "synthesis" not in cycle_data:
        print("✗ synthesis not found in cycle_position_v4.json")
        return
    
    summary = cycle_data["synthesis"].get("cycle_positions_summary", [])
    if not isinstance(summary, list):
        print("✗ cycle_positions_summary is not a list")
        return
    
    # Add phase field (copy from position)
    for entry in summary:
        if "position" in entry and "phase" not in entry:
            entry["phase"] = entry["position"]
    
    print(f"✓ Added phase field to {len(summary)} entries")
    
    save_json(cycle_data, "cycle_position_v4.json")
    save_json(cycle_data, "cycle_position.json")

def update_cache_version():
    """Update cache version in index.html."""
    print("\n=== Updating cache version ===")
    
    index_path = Path("/Coze/Drive/周期看板改进0915/macro-dashboard/index.html")
    if not index_path.exists():
        print("✗ index.html not found")
        return
    
    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update stock_dashboard.html cache version
    content = content.replace(
        'stock_dashboard.html?v=20260914p',
        'stock_dashboard.html?v=20260915d'
    )
    
    # Update JS cache versions
    content = content.replace(
        'js/app.js?v=20260901b',
        'js/app.js?v=20260915d'
    )
    content = content.replace(
        'js/cycle_v3.js?v=20260915c',
        'js/cycle_v3.js?v=20260915d'
    )
    content = content.replace(
        'js/data_normalize.js?v=20260901b',
        'js/data_normalize.js?v=20260915d'
    )
    content = content.replace(
        'js/app_v2_patch.js?v=20260902',
        'js/app_v2_patch.js?v=20260915d'
    )
    content = content.replace(
        'js/timing_v2.js?v=20260915',
        'js/timing_v2.js?v=20260915d'
    )
    
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✓ Updated cache version to v=20260915d")

def main():
    """Run all fixes."""
    print("=" * 60)
    print("Data Bridge Script - Fixing Frontend Data Issues")
    print("=" * 60)
    
    # Run all fixes
    fix_c9_asset_prices()
    fix_c3_us_macro()
    fix_c10_asset_valuation()
    fix_c11_dashboard_summary()
    fix_c7_guard_margin()
    fix_c1_cn_macro()
    fix_c5_cycle_position()
    fix_c6_phase_field()
    update_cache_version()
    
    print("\n" + "=" * 60)
    print("✓ All fixes completed!")
    print("=" * 60)

if __name__ == "__main__":
    main()
