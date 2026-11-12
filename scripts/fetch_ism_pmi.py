#!/usr/bin/env python3
"""fetch_ism_pmi.py - ISM 美国制造业 PMI 真序列采集（CIO 2026-11-12）

背景：ISM 官网 ismworld.org 已强制 SSO 付费墙（202 重定向 ecommerce.ismworld.org/SSO/Login），
akshare macro_usa_ism_pmi（金十源）停在 2025-09，FRED 因版权已下架 ISM 系列。
CIO 曾建议改用 S&P Global 美国制造业 PMI 另立 sp_mfg_pmi（标注断点）。

本脚本走**更优路径**：ISM 官方新闻稿经 PR Newswire 全文分发（作者 ISM、电头 TEMPE, Ariz.），
属官方稿同源镜像而非二手解读，故可**续采原 ISM 序列，无断点**，无需另立 sp_mfg_pmi。

关键技巧：PR Newswire 的稿 URL 自带数值与月份，
  /news-releases/manufacturing-pmi-at-55-5-october-2026-ism-manufacturing-pmi-report-302925617.html
正则直接从 URL 提取 (值, 月, 年)，不依赖正文解析，抗页面改版。

输出：data/ism_pmi.json
  {"source":..., "caliber":..., "update_time":..., "history":[{"date":"YYYY-MM","value":x}, ...]}
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent.parent / "data"
OUT = DATA_DIR / "ism_pmi.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MON = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}
# 值在 URL 里被写成 55-5 形式（小数点换连字符）
URL_RE = re.compile(
    r"/news-releases/manufacturing-pmi-at-([\d]+(?:-[\d]+)?)-([a-z]+)-(\d{4})"
    r"-ism-manufacturing-pmi-report-\d+\.html")


def fetch_prnewswire(max_pages=6):
    """抓 PR Newswire 的 ISM 组织页，从稿 URL 提取 (数据月, 值)。"""
    found = {}
    for page in range(1, max_pages + 1):
        url = f"https://www.prnewswire.com/news/institute-for-supply-management/?page={page}"
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        except Exception as e:
            print(f"  ⚠️ page{page} 请求失败 {type(e).__name__}: {e}")
            break
        if r.status_code != 200:
            print(f"  ⚠️ page{page} status={r.status_code}")
            break
        hits = URL_RE.findall(r.text)
        if not hits:
            break
        for raw_v, mon, year in hits:
            if mon not in MON:
                continue
            val = float(raw_v.replace("-", "."))
            found[f"{year}-{MON[mon]:02d}"] = val
        print(f"  page{page}: +{len(hits)} 稿（累计 {len(found)} 个月）")
    return found


def fetch_akshare_history():
    """akshare macro_usa_ism_pmi 历史（金十源），日期为发布日，减 1 月得数据月。

    与 PR Newswire 重叠期校验：akshare 2025-09-02 今值 48.7 == PRN 2025-08 48.7，口径一致。
    """
    try:
        import akshare as ak
    except ImportError:
        print("  ⚠️ akshare 不可用，跳过历史补齐")
        return {}
    try:
        df = ak.macro_usa_ism_pmi()
    except Exception as e:
        print(f"  ⚠️ akshare macro_usa_ism_pmi 失败 {type(e).__name__}: {e}")
        return {}
    out = {}
    for _, row in df.iterrows():
        v = row.get("今值")
        if v is None or v != v:  # NaN
            continue
        d = str(row.get("日期"))[:10]
        m = re.match(r"^(\d{4})-(\d{2})", d)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        mo -= 1                      # 发布日 -> 数据月
        if mo == 0:
            y, mo = y - 1, 12
        out[f"{y}-{mo:02d}"] = float(v)
    print(f"  akshare 历史: {len(out)} 个月（{min(out)} ~ {max(out)}）" if out else "  akshare 历史: 空")
    return out


def main():
    print("📡 ISM 美国制造业 PMI 采集（PR Newswire 官方稿分发）")
    prn = fetch_prnewswire()
    if not prn:
        print("❌ 未从 PR Newswire 取到任何稿件，中止（不写空文件）")
        sys.exit(1)
    hist = fetch_akshare_history()

    # PR Newswire 为准（官方稿），akshare 只补 PRN 未覆盖的更早月份
    merged = dict(hist)
    overlap_conflict = []
    for d, v in prn.items():
        if d in merged and abs(merged[d] - v) > 1e-9:
            overlap_conflict.append((d, merged[d], v))
        merged[d] = v
    if overlap_conflict:
        print("  ⚠️ 重叠期口径不一致（以 PRN 为准）:")
        for d, a, b in overlap_conflict:
            print(f"     {d}: akshare={a} -> PRN={b}")

    rows = [{"date": d, "value": merged[d]} for d in sorted(merged)]
    tail = rows[-1]
    payload = {
        "source": "ISM® Manufacturing PMI®（ISM 官方新闻稿，PR Newswire 分发）",
        "caliber": "扩散指数，季调，50 为荣枯线；月度",
        "note": ("ISM 官网已 SSO 付费墙、akshare 金十源停 2025-09、FRED 因版权下架 ISM 系列；"
                 "本序列经 PR Newswire 的 ISM 官方稿续采，与原 ISM 序列同源，无断点。"
                 "2025-09 及之前来自 akshare（金十），重叠月 2025-08 双方一致（48.7）已互验。"),
        "update_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "latest": tail,
        "history": rows,
    }
    DATA_DIR.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✅ {OUT} 已写：{len(rows)} 个月，最新 {tail['value']}@{tail['date']}")


if __name__ == "__main__":
    main()
