#!/usr/bin/env python3
"""build_asset_prices_monthly.py — asset_prices_monthly 链路修复（P0-2，2026-11-01）

背景
----
月频文件原由 fetch_fred_data_v2.py / fetch_cn_macro_v2.py 直采生成，两脚本
均不在日常流水线（run_all.py）内 → 断档：update_time=2026-09-15、铜停在
2026-07，而日频 asset_prices.json 每日更新（CIO 核验 2026-11-01）。

修复
----
反转依赖：月频由日频 asset_prices.json 聚合生成（每月末最后交易日取值），
与既有月频历史合并（历史保留、近月覆盖/追加）。同时刷新
asset_prices_monthly_gold.json（宏观截面 fetch_us.py 的兜底源）。

口径红线（团队铁律：不缝合口径断点）
------------------------------------
历史月频（FRED 等源）与日频（yfinance）同月重叠值系统性偏差 >10%、或无重叠
但量级比超出 [0.5,2] 的序列，判定为口径断点（单位/篮子不同），**不合并**：
保留旧序列原样，series_meta 标 seam_mismatch 且 stale_10td=true，由页面
强制置灰，并等待口径裁决。实测断点（2026-11-01）：
  copper    FRED PCOPPUSDM(美元/吨) vs yfinance HG=F(美元/磅)，量级差 ~2000x
  usd_index FRED DTWEXBGS(广义篮子) vs yfinance DX-Y.NYB(DXY)，偏差 14-16%
可合并序列（同尺度，偏差为月均 vs 月末取值差）：
  sp500(0.4%) / wti_oil(7.0%) / us_10y_yield(6.7%) / gold(1.6%)
合并序列在 series_meta 记录 seam_deviation_pct 备查；历史段=FRED 月均、
2026-08 起=日频月末值，口径微差已标注（日频仅 60 点，无法回灌全史）。

输出 series_meta：每序列 last_month / last_src_date / effective_last_date /
trading_days_stale / stale_10td（>10 个交易日未更新，交易日按 自然日×5/7
近似，与 scripts/freshness_gate.py 同口径），供前端强制置灰。
"""

import json
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DAILY_FILE = DATA_DIR / "asset_prices.json"
MONTHLY_FILE = DATA_DIR / "asset_prices_monthly.json"
GOLD_MONTHLY_FILE = DATA_DIR / "asset_prices_monthly_gold.json"

# 日频 key → (月频 key, 值字段)
KEY_MAP = {
    "sp500": ("sp500", "close"),
    "copper": ("copper", "value"),
    "crude_oil": ("wti_oil", "value"),
    "usd_index": ("usd_index", "value"),
    "us_10y_bond": ("us_10y_yield", "value"),
}

SEAM_TOLERANCE_PCT = 10.0   # 全部重叠月偏差 ≤10% 视为同尺度可合并
BASIS_TOLERANCE_PCT = 5.0   # copper 专用：COMEX×2204.62 vs PCOPPUSDM(LME) 同月基差阈值（P0-2 裁决边界4）

# P0-2 裁决（2026-11-01）口径四元组登记
CALIBER_REGISTRY = {
    "copper": "月频准绳=FRED PCOPPUSDM(IMF/LME, 美元/吨) | 日频延伸=COMEX 主力连续(yfinance HG=F)×2204.62(写库时换算美元/吨) | 同月基差超±5%亮灰灯注记、不静默对齐",
    "usd_index": "主屏=DXY(ICE: EUR57.6/JPY13.6/GBP11.9/CAD9.1/SEK4.2/CHF3.6, 不含CNY/MXN, 1973起, 日频 yfinance DX-Y.NYB) | 月末最后交易日值",
    "usd_broad": "叙事参考=DTWEXBGS(美联储广义贸易加权, 2006起, H.10周频) | 展示位=C2利率层/美元环流叙事，不进信号灯、不进 asset_valuation 分位",
}
SCALE_RATIO_RANGE = (0.5, 2.0)  # 无重叠时新旧首尾量级比安全区
STALE_TRADING_DAYS = 10     # P0-2: 超 10 个交易日未更新 → 页面强制置灰


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def trading_days(d0: date, d1: date) -> int:
    """自然日 ×5/7 近似交易日（与 freshness_gate.py 同口径）。"""
    return max(0, round((d1 - d0).days * 5 / 7))


def month_end(month_str: str) -> date:
    y, m = int(month_str[:4]), int(month_str[5:7])
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def copper_basis_check(agg, prev_meta=None):
    """P0-2 裁决边界4：copper 日频延伸(COMEX×2204.62) vs 准绳 FRED PCOPPUSDM(LME) 同月基差。

    注意：合并后历史段同月已被日频覆盖，seam_check 重叠偏差恒 0、监控会失效——
    基差必须由独立准绳源（FRED API）比对。返回 meta 补充 dict；取数失败保留旧记录。
    """
    prev = prev_meta or {}
    keep = {k: prev[k] for k in ("basis_dev_pct", "basis_month", "basis_breach") if k in prev}
    try:
        import os
        import urllib.request
        key = os.environ.get("FRED_API_KEY", "")
        if not key:
            return keep
        url = ("https://api.stlouisfed.org/fred/series/observations?series_id=PCOPPUSDM"
               f"&api_key={key}&file_type=json&sort_order=desc&limit=8")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            obs = json.loads(r.read().decode()).get("observations", [])
        bench = {o["date"][:7]: float(o["value"]) for o in obs if o["value"] not in ("", ".")}
        overlaps = [(m, agg[m], bench[m]) for m in agg if m in bench]
        if not overlaps:
            return keep
        devs = [(abs(cv - bv) / abs(bv) * 100, m) for m, cv, bv in overlaps]
        max_dev, max_m = max(devs)
        out = {"basis_dev_pct": round(max_dev, 2), "basis_month": max_m}
        if max_dev > BASIS_TOLERANCE_PCT:
            out["basis_breach"] = True
        return out
    except Exception as e:
        print(f"  ⚠ copper 基差准绳(FRED PCOPPUSDM)取数失败，保留旧记录: {e}")
        return keep


def daily_to_monthly(daily):
    """日频 [{date,value}] → {YYYY-MM: 月末最后交易日值}（日频升序，后写即月末）。"""
    out = {}
    for pt in daily:
        d, v = pt.get("date"), pt.get("value")
        if d and v is not None:
            out[d[:7]] = v
    return out


def seam_check(legacy_series, agg, field, tolerance_pct=SEAM_TOLERANCE_PCT):
    """口径闸门。返回 (ok, max_dev_pct 或 None, reason)。tolerance_pct: 重叠月偏差阈值（copper 用 5%）。"""
    legacy = {p["date"]: p.get(field) for p in (legacy_series or [])
              if isinstance(p, dict) and p.get("date")}
    overlaps = [(m, legacy[m], v) for m, v in agg.items()
                if m in legacy and legacy[m] not in (None, 0)]
    if overlaps:
        devs = [abs(v - lv) / abs(lv) * 100 for _, lv, v in overlaps]
        max_dev = max(devs)
        if max_dev <= tolerance_pct:
            return True, round(max_dev, 2), f"同尺度(重叠月偏差≤{tolerance_pct:.0f}%)"
        return False, round(max_dev, 2), f"重叠月系统性偏差 {max_dev:.1f}%>{tolerance_pct:.0f}%（疑口径/篮子不同）"
    # 无重叠：比量级（新序列首月值 vs 旧序列末月值）
    if legacy_series and agg:
        lv = legacy_series[-1].get(field)
        nv = agg[sorted(agg)[0]]
        if lv and nv:
            ratio = nv / lv
            if SCALE_RATIO_RANGE[0] <= ratio <= SCALE_RATIO_RANGE[1]:
                return True, None, f"无重叠，量级比 {ratio:.2f} 在[0.5,2]内"
            return False, None, f"无重叠，量级比 {ratio:.4f} 超界（疑单位不同）"
    return True, None, "无旧序列可比"


def merge_series(existing, monthly_new, field):
    """合并：历史保留，新月份覆盖/追加。"""
    merged = {p["date"]: p for p in (existing or []) if isinstance(p, dict) and p.get("date")}
    for month, val in monthly_new.items():
        merged[month] = {"date": month, field: val}
    return [merged[k] for k in sorted(merged)]


def mark_stale(meta, today):
    last_month = meta.get("last_month")
    if not last_month:
        return meta
    eff = meta.get("last_src_date") or month_end(last_month).isoformat()
    meta["effective_last_date"] = eff
    try:
        td = trading_days(date.fromisoformat(eff[:10]), today)
    except ValueError:
        return meta
    meta["trading_days_stale"] = td
    meta["stale_10td"] = td > STALE_TRADING_DAYS
    return meta


def main():
    if not DAILY_FILE.exists():
        raise SystemExit(f"✗ 缺少日频文件 {DAILY_FILE}")
    daily = load_json(DAILY_FILE).get("prices", {})
    monthly_doc = load_json(MONTHLY_FILE) if MONTHLY_FILE.exists() else {"prices": {}}
    prices = dict(monthly_doc.get("prices", {}))
    today = date.today()
    series_meta = {}

    # ---- 0) 金价：先合并兜底文件（全史），主文件 gold 与其同源 ----
    gold_series, gold_meta = None, None
    gold_daily = (daily.get("gold") or {}).get("daily") or []
    if gold_daily and GOLD_MONTHLY_FILE.exists():
        gdoc = load_json(GOLD_MONTHLY_FILE)
        g_old = (gdoc.get("prices", {}) or {}).get("gold")
        agg = daily_to_monthly(gold_daily)
        ok, dev, reason = seam_check(g_old, agg, "close")
        if ok:
            gold_series = merge_series(g_old, agg, "close")
            gdoc.setdefault("prices", {})["gold"] = gold_series
            gdoc["update_time"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            gdoc["generated_by"] = "scripts/build_asset_prices_monthly.py (P0-2: 日频聚合)"
            save_json(GOLD_MONTHLY_FILE, gdoc)
            gold_meta = {
                "last_month": gold_series[-1]["date"],
                "last_src_date": max(p["date"][:10] for p in gold_daily),
                "source": "daily_aggregate(asset_prices.json)",
                "seam": f"合并成功（{reason}" + (f"，max_dev={dev}%" if dev else "") + "）",
            }
            if dev is not None:
                gold_meta["seam_deviation_pct"] = dev
            print(f"✅ gold: {len(gold_series)} pts, tail={gold_meta['last_month']}（{reason}）")
        else:
            gold_meta = {
                "last_month": g_old[-1]["date"] if g_old else None,
                "last_src_date": None,
                "source": "legacy_preserved(口径断点未合并)",
                "seam": f"seam_mismatch: {reason}",
                "seam_mismatch": True,
            }
            print(f"⛔ gold: {reason} → 不合并，保留旧序列")

    # ---- 1) 日频聚合 + 口径闸门 + 合并 ----
    for dkey, (mkey, field) in KEY_MAP.items():
        d = (daily.get(dkey) or {}).get("daily") or []
        agg = daily_to_monthly(d)
        if not agg:
            series_meta[mkey] = {"last_month": (prices.get(mkey) or [{}])[-1].get("date") if prices.get(mkey) else None,
                                 "last_src_date": None, "source": "legacy_preserved(日频无数据)"}
            print(f"⚠ {dkey}: 日频无数据，{mkey} 保留旧值")
            continue
        old = prices.get(mkey)
        # P0-2 裁决边界4：copper 同月基差阈值 ±5%（COMEX vs LME），超差仍接入但亮灰灯注记
        tol = BASIS_TOLERANCE_PCT if mkey == "copper" else SEAM_TOLERANCE_PCT
        ok, dev, reason = seam_check(old, agg, field, tolerance_pct=tol)
        if not ok and mkey == "copper" and dev is not None:
            series = merge_series(old, agg, field)
            prices[mkey] = series
            series_meta[mkey] = {
                "last_month": series[-1]["date"],
                "last_src_date": max(p["date"][:10] for p in d),
                "source": "daily_aggregate(asset_prices.json)",
                "seam": f"基差 {dev}%>±5%（COMEX×2204.62 vs PCOPPUSDM/LME，P0-2 裁决边界4：灰灯注记，不静默对齐）",
                "seam_mismatch": True,
                "basis_breach": True,
                "basis_dev_pct": dev,
                "caliber": CALIBER_REGISTRY.get(mkey),
            }
            print(f"⚠ {mkey}: COMEX-LME 基差 {dev}%>±5% → 已接入+灰灯注记（不静默对齐）")
            continue
        if not ok:
            series_meta[mkey] = {
                "last_month": old[-1]["date"] if old else None,
                "last_src_date": None,
                "source": "legacy_preserved(口径断点未合并)",
                "seam": f"seam_mismatch: {reason}",
                "seam_mismatch": True,
            }
            print(f"⛔ {mkey}: {reason} → 不合并，保留旧序列（待口径裁决）")
            continue
        series = merge_series(old, agg, field)
        prices[mkey] = series
        series_meta[mkey] = {
            "last_month": series[-1]["date"],
            "last_src_date": max(p["date"][:10] for p in d),
            "source": "daily_aggregate(asset_prices.json)",
            "seam": f"合并成功（{reason}" + (f"，max_dev={dev}%" if dev else "") + "）",
        }
        if dev is not None:
            series_meta[mkey]["seam_deviation_pct"] = dev
        if mkey in CALIBER_REGISTRY:
            series_meta[mkey]["caliber"] = CALIBER_REGISTRY[mkey]
        if mkey == "copper":
            basis = copper_basis_check(agg, prev_meta=(monthly_doc.get("prices", {}) or {}).get("series_meta", {}).get("copper"))
            series_meta[mkey].update(basis)
            if basis.get("basis_breach"):
                series_meta[mkey]["seam_mismatch"] = True
                series_meta[mkey]["seam"] = (f"基差 {basis['basis_dev_pct']}%>±5%（{basis.get('basis_month')}，"
                                             "COMEX×2204.62 vs PCOPPUSDM/LME，P0-2 裁决边界4：灰灯注记，不静默对齐）")
                print(f"⚠ copper: 基差 {basis['basis_dev_pct']}%>±5% → 灰灯注记")
            elif basis.get("basis_dev_pct") is not None:
                print(f"  copper 基差监控: {basis['basis_dev_pct']}%（{basis.get('basis_month')}）≤±5% ✅")
        print(f"✅ {mkey}: {len(series)} pts, tail={series[-1]['date']}（{reason}）")

    # ---- 2) gold 入主文件（与兜底文件同源全史）----
    if gold_series:
        prices["gold"] = gold_series
        series_meta["gold"] = gold_meta

    # ---- 3) us_10y_price 由重建后的收益率推导（沿用 fetch_fred_data_v2 公式）----
    yields = prices.get("us_10y_yield") or []
    if yields and not series_meta.get("us_10y_yield", {}).get("seam_mismatch"):
        prices["us_10y_price"] = [
            {"date": y["date"], "value": round(100 / (1 + y["value"] / 100) ** 10, 2)}
            for y in yields if y.get("value") is not None
        ]
        series_meta["us_10y_price"] = {
            "last_month": prices["us_10y_price"][-1]["date"],
            "last_src_date": series_meta.get("us_10y_yield", {}).get("last_src_date"),
            "source": "derived(us_10y_yield)",
        }
        print(f"✅ us_10y_price: {len(prices['us_10y_price'])} pts (derived)")

    # ---- 4) 遗留序列（hs300/cn_10y_yield 等中国源，日频文件不覆盖）标注 ----
    old_meta = (monthly_doc.get("prices", {}) or {}).get("series_meta", {}) or {}
    for mkey, series in prices.items():
        if mkey in series_meta or mkey.endswith("_meta") or not isinstance(series, list) or not series:
            continue
        if mkey in CALIBER_REGISTRY:
            if mkey in old_meta:  # P0-2: 保留已登记 meta（如 usd_broad 叙事参考降级注记）
                series_meta[mkey] = dict(old_meta[mkey])
            continue
        series_meta[mkey] = {
            "last_month": series[-1].get("date"),
            "last_src_date": None,
            "source": "legacy_preserved(原直采链，已停更待修)",
        }

    # ---- 5) 统一计算断更标记（>10 个交易日）----
    for mkey in series_meta:
        mark_stale(series_meta[mkey], today)

    prices["series_meta"] = series_meta
    out = {
        "update_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_by": "scripts/build_asset_prices_monthly.py (P0-2: 日频聚合+口径闸门)",
        "prices": prices,
    }
    save_json(MONTHLY_FILE, out)
    stale_list = [k for k, m in series_meta.items() if m.get("stale_10td")]
    seam_list = [k for k, m in series_meta.items() if m.get("seam_mismatch")]
    print(f"\n✅ asset_prices_monthly.json 重建完成: {len(prices) - 1} 序列")
    print(f"   断更(>{STALE_TRADING_DAYS}交易日→页面置灰): {stale_list or '无'}")
    print(f"   口径断点(待裁决): {seam_list or '无'}")


if __name__ == "__main__":
    main()
