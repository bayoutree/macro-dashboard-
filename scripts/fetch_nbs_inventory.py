#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集中国库存周期两项（CIO 2026-11-12 收尾①，原为静态桩值）：

  1. pmi_finished_goods                  PMI 产成品库存分项（扩散指数，<50=收缩）
  2. industrial_finished_goods_inventory_yoy  规模以上工业企业产成品存货同比（%）

数据源均为 www.stats.gov.cn 文章页（不走 data.stats.gov.cn easyquery，403 UrlACL）：
  · PMI 稿《X月中国采购经理指数运行情况》正文含「分类指数」表，**每篇带最近
    13 个月**，抓全部历史稿合并即可拼出长序列。
  · 利润稿《1—N月份全国规模以上工业企业利润增长X%》正文含
    「产成品存货 X 万亿元，增长 Y%」。

用法：
  python3 scripts/fetch_nbs_inventory.py            # 增量（默认，复用已有 json）
  python3 scripts/fetch_nbs_inventory.py --full     # 全量重建
"""
import argparse
import html as H
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "nbs_inventory_raw.json"
LIST_BASE = "https://www.stats.gov.cn/sj/zxfb/"
MAX_PAGES = 60

_COOKIE_JAR = str(Path(tempfile.gettempdir()) / ".nbs_zxfb_cookies.txt")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get(url, tries=2):
    """NBS 有 WAF：裸请求返回 200 但正文是 JS 挑战页；需持久 cookie + 浏览器头。"""
    for i in range(tries):
        try:
            r = subprocess.run(
                ["curl", "-sS", "--compressed", "-L", "-A", _UA,
                 "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9",
                 "-b", _COOKIE_JAR, "-c", _COOKIE_JAR,
                 "--max-time", "30", url],
                capture_output=True, timeout=45)
            if r.returncode == 0 and len(r.stdout) > 500:
                t = r.stdout.decode("utf-8", "ignore")
                if "Please enable JavaScript" not in t[:800]:
                    return t
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return ""


def _text(raw):
    t = re.sub(r"<script[\s\S]*?</script>", "", raw)
    t = re.sub(r"<style[\s\S]*?</style>", "", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t))


def crawl_index():
    rows, seen = [], set()
    _empty = 0
    for p in range(MAX_PAGES):
        url = LIST_BASE + ("index.html" if p == 0 else "index_%d.html" % p)
        t = _get(url)
        if not t:
            break
        n0 = len(rows)
        for m in re.finditer(r'href="(\./(\d{6})/t(\d{8})_\d+\.html)"[^>]*>([^<]{6,110})', t):
            k = m.group(1)
            if k in seen:
                continue
            seen.add(k)
            rows.append({"u": k, "ym": m.group(2), "d": m.group(3), "t": m.group(4).strip()})
        # 单页空（WAF 抖动）不立即放弃，连续 2 页无新增才停
        if len(rows) == n0:
            _empty += 1
            if _empty >= 2:
                break
        else:
            _empty = 0
        time.sleep(0.3)
    return rows


# ---------------- PMI 产成品库存 ----------------

def parse_pmi_tables(raw):
    """从 PMI 稿的 HTML 表格里取「产成品库存」列，返回 [(YYYY-MM, value)]。

    表格列头形如：新出口订单|进口|采购量|主要原材料购进价格|出厂价格|产成品库存|...
    必须按列头定位，不能按固定下标——不同年份表格列序会变。
    """
    out = []
    for tab in re.findall(r"<table[\s\S]*?</table>", raw):
        trs = re.findall(r"<tr[\s\S]*?</tr>", tab)
        if not trs:
            continue
        head = [re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", c))
                for c in re.findall(r"<t[dh][\s\S]*?</t[dh]>", trs[0])]
        idx = None
        for i, h in enumerate(head):
            if "产成品库存" in h:
                idx = i
                break
        if idx is None:
            continue
        for tr in trs[1:]:
            cells = [re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", c))
                     for c in re.findall(r"<t[dh][\s\S]*?</t[dh]>", tr)]
            if len(cells) <= idx:
                continue
            mm = re.match(r"^(\d{4})年(\d{1,2})月$", cells[0])
            if not mm:
                continue
            try:
                v = float(cells[idx])
            except ValueError:
                continue
            out.append(("%s-%02d" % (mm.group(1), int(mm.group(2))), v))
    return out


def work_pmi(item):
    raw = _get(LIST_BASE + item["u"][2:])
    if len(raw) < 800:
        return []
    return parse_pmi_tables(raw)


# ---------------- 工业企业产成品存货同比 ----------------

PAT_INV = re.compile(r"产成品存货[^。；]{0,30}?(?:增长|下降)\s*([\d.]+)\s*%")
PAT_NEG = re.compile(r"产成品存货[^。；]{0,30}?(下降)\s*([\d.]+)\s*%")


def period_from_title(title, pub_ym):
    """利润稿期次：'2026年1—9月份…' -> 2026-09；'2025年…增长0.6%'（全年）-> 2025-12。"""
    m = re.search(r"(20\d{2})\s*年\s*1\s*[—\-~]\s*(\d{1,2})\s*月", title)
    if m:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = re.search(r"(20\d{2})\s*年\s*(?:全国|规模以上)", title)
    if m:
        return "%s-12" % m.group(1)
    y, mo = int(pub_ym[:4]), int(pub_ym[4:])
    return "%s-%02d" % (y - 1, 12) if mo <= 2 else "%s-%02d" % (y, mo - 1)


def work_profit(item):
    raw = _get(LIST_BASE + item["u"][2:])
    if len(raw) < 800:
        return None
    txt = _text(raw)
    m = PAT_INV.search(txt)
    if not m:
        return None
    v = float(m.group(1))
    if PAT_NEG.search(txt):
        v = -v
    return (period_from_title(item["t"], item["ym"]), v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    old = {}
    if OUT.exists() and not args.full:
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    old_s = old.get("series") or {}

    rows = crawl_index()
    print("[nbs-inv] zxfb 列表 %d 篇" % len(rows), flush=True)

    pmi_items = [r for r in rows if "采购经理指数运行情况" in r["t"]]
    profit_items = [r for r in rows if "规模以上工业企业利润" in r["t"]]
    print("[nbs-inv] PMI 稿 %d 篇 / 利润稿 %d 篇" % (len(pmi_items), len(profit_items)), flush=True)

    pmi_map = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for pts in ex.map(work_pmi, pmi_items):
            for d, v in pts:
                pmi_map.setdefault(d, v)

    inv_map = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(work_profit, profit_items):
            if r:
                inv_map.setdefault(r[0], r[1])

    def merge(key, cur):
        m = {}
        for p in (old_s.get(key) or []):
            m[p["date"]] = p["value"]
        m.update(cur)
        return [{"date": d, "value": m[d]} for d in sorted(m)]

    pmi_s = merge("pmi_finished_goods", pmi_map)
    inv_s = merge("industrial_finished_goods_inventory_yoy", inv_map)

    if len(pmi_s) < 24 and len(inv_s) < 24:
        print("[nbs-inv] 两项点数均不足，放弃写入", file=sys.stderr)
        return 1

    doc = {
        "fetch_time": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": "国家统计局 sj/zxfb 采购经理指数运行情况 / 规模以上工业企业利润",
        "source_url": LIST_BASE,
        "caliber": {
            "pmi_finished_goods": "PMI 产成品库存分项（扩散指数，50 为荣枯线，<50=库存收缩）",
            "industrial_finished_goods_inventory_yoy":
                "规模以上工业企业产成品存货同比（%），1—N 月累计口径",
        },
        "series": {
            "pmi_finished_goods": pmi_s,
            "industrial_finished_goods_inventory_yoy": inv_s,
        },
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[nbs-inv] PMI 产成品库存 %d 点 -> %s@%s" %
          (len(pmi_s), pmi_s[-1]["value"], pmi_s[-1]["date"]))
    print("[nbs-inv] 产成品存货同比 %d 点 -> %s@%s" %
          (len(inv_s), inv_s[-1]["value"], inv_s[-1]["date"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
