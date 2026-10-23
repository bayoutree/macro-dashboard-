#!/usr/bin/env python3
"""
collect_fx_rates_sina.py - 采集 USDCNY/USDCNH 实时快照，自积累日频序列

数据源：新浪财经实时行情接口
- USDCNY: https://hq.sinajs.cn/list=fx_susdcny
- USDCNH: https://hq.sinajs.cn/list=fx_susdcnh
- 需要 Referer: https://finance.sina.com.cn

输出：data/fx_rates_sina.json
{
  "as_of": "YYYY-MM-DD",
  "daily": [
    {"date": "YYYY-MM-DD", "cny_mid": float, "cnh_mid": float, "spread_bp": float}
  ]
}

逻辑：
- 每次采集取 buy/sell 中值
- spread_bp = (cnh_mid - cny_mid) * 10000
- 同一天重复采集会更新当天数据
- 按日期升序排列

判定逻辑（在 update_data.py 中实现）：
- spread_bp > 40 连续 5 日 → F3④ 汇率阀门触发
- 单日 spread_bp > 70 → 立即触发
"""

import json
import requests
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "fx_rates_sina.json"

HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def fetch_sina_fx(symbol: str) -> dict:
    """
    解析新浪外汇快照
    
    返回格式：
    var hq_str_fx_susdcnh="17:29:31,6.669900,6.670100,6.672600,39,..."
    
    字段含义（根据实测）：
    - [0]: time (HH:MM:SS)
    - [1]: buy price
    - [2]: sell price
    - [-1]: date (YYYY-MM-DD)
    """
    url = f"https://hq.sinajs.cn/list={symbol}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'gbk'  # 新浪接口返回 GBK 编码
        text = resp.text.strip()
        
        # 解析: var hq_str_fx_susdcnh="..."
        if '=' not in text:
            raise ValueError(f"Invalid response format: {text[:100]}")
        
        data_str = text.split('="')[1].rstrip('";')
        fields = data_str.split(',')
        
        if len(fields) < 3:
            raise ValueError(f"Too few fields: {len(fields)}")
        
        # 提取日期（最后一个字段）
        date = fields[-1]
        
        # 提取买卖价
        buy = float(fields[1])
        sell = float(fields[2])
        mid = (buy + sell) / 2
        
        return {
            "date": date,
            "time": fields[0],
            "buy": buy,
            "sell": sell,
            "mid": round(mid, 4)
        }
    except Exception as e:
        print(f"  ⚠️  获取 {symbol} 失败: {e}")
        return None

def main():
    print("📊 采集 USDCNY/USDCNH 实时快照 (新浪源)...")
    
    # 获取今日数据
    cny = fetch_sina_fx("fx_susdcny")
    cnh = fetch_sina_fx("fx_susdcnh")
    
    if not cny or not cnh:
        print("  ❌ 采集失败，跳过")
        return
    
    if cny["date"] != cnh["date"]:
        print(f"  ⚠️  日期不匹配: CNY={cny['date']}, CNH={cnh['date']}")
        return
    
    today = cny["date"]
    spread_bp = (cnh["mid"] - cny["mid"]) * 10000
    
    print(f"  ✅ {today}: CNY={cny['mid']}, CNH={cnh['mid']}, spread={spread_bp:.1f}bp")
    
    # 读取现有数据
    existing_data = []
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                existing_data = existing.get("daily", [])
        except Exception as e:
            print(f"  ⚠️  读取现有数据失败: {e}")
    
    # 更新或追加今日数据
    new_entry = {
        "date": today,
        "cny_mid": cny["mid"],
        "cnh_mid": cnh["mid"],
        "spread_bp": round(spread_bp, 2)
    }
    
    # 检查是否已有今天的数据
    updated = False
    for i, entry in enumerate(existing_data):
        if entry["date"] == today:
            existing_data[i] = new_entry
            updated = True
            break
    
    if not updated:
        existing_data.append(new_entry)
    
    # 按日期排序
    existing_data.sort(key=lambda x: x["date"])
    
    # 写入文件
    output = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "daily": existing_data
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"  💾 已保存 {len(existing_data)} 天数据到 fx_rates_sina.json")

if __name__ == "__main__":
    main()
