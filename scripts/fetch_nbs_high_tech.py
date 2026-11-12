#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集国家统计局「高技术制造业投资 / 高技术产业投资」1—N 月累计同比。

[CIO 2026-11-12 ⑤ 方案 A] 不再依赖 data.stats.gov.cn easyquery API
（本机与平台侧均 403 UrlACL），改走 www.stats.gov.cn 文章页解析：
取「固定资产投资统计司」月度/季度解读稿，从正文抽取累计同比。

已知数据可得性事实（2026-11-12 实测，必须随数据一起交付）：
  · 2020-04 ~ 2024-11：制造口径基本连续（季度末月亦有稿）
  · 2024-12 ~ 2026-01：**官方停发「高技术制造业投资」总量增速**，仅发布
    高技术服务业投资与分行业分项，形成 14 个月硬断档（不是抓取失败）
  · 2026-02 起恢复发布
  断档期不插值、不补造，在 cycle_position_v4 的 data_gap_note 中如实标注。

用法：
  python3 scripts/fetch_nbs_high_tech.py            # 增量（默认）
  python3 scripts/fetch_nbs_high_tech.py --full     # 全量重建
"""
import argparse
import gzip
import html as H
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "nbs_high_tech_raw.json"
BASE = "https://www.stats.gov.cn/sj/sjjd/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_PAGES = 70

PAT_MFG_YOY = re.compile(r"高技术制造业投资[^。；]{0,30}?同比(增长|下降|回落|减少)\s*([\d.]+)%")
PAT_MFG_ANN = re.compile(r"高技术制造业投资[^。；]{0,20}?比上年(增长|下降)\s*([\d.]+)%")
PAT_IND_YOY = re.compile(r"高技术产业投资[^。；]{0,30}?同比(增长|下降)\s*([\d.]+)%")
PAT_IND_ANN = re.compile(r"高技术产业投资[^。；]{0,20}?比上年(增长|下降)\s*([\d.]+)%")
NEG = {"下降", "回落", "减少"}
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


_COOKIE_JAR = str(Path(tempfile.gettempdir()) / ".nbs_sjjd_cookies.txt")
_UA_STR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get(url, tries=2):
    """抓取页面。

    NBS 有 WAF：无 cookie 的裸请求会被下发「Please enable JavaScript」挑战页
    （HTTP 200 但正文是 noscript 提示，正则全空），连续高频访问同样被拦。
    实测「首次请求取 Set-Cookie → 后续带 cookie + 完整浏览器头 + --compressed」
    可稳定通过，故用 curl 持久 cookie jar；并做退避重试。
    """
    for i in range(tries):
        try:
            r = subprocess.run(
                ["curl", "-sS", "--compressed", "-L",
                 "-A", _UA_STR,
                 "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9",
                 "-b", _COOKIE_JAR, "-c", _COOKIE_JAR,
                 "--max-time", "30", url],
                capture_output=True, timeout=45)
            if r.returncode == 0 and len(r.stdout) > 500:
                txt = r.stdout.decode("utf-8", "ignore")
                if "Please enable JavaScript" not in txt[:800]:
                    return txt
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return ""


def _strip(t):
    t = re.sub(r"<script[\s\S]*?</script>", "", t)
    t = re.sub(r"<style[\s\S]*?</style>", "", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t))


def _mon(s):
    if s in CN_NUM:
        return CN_NUM[s]
    return int(s) if s.isdigit() else None


def _period_from(text, pub_y):
    """从一段文本解析期次 -> (year, month)；失败 None。"""
    y = None
    my = re.search(r"(20\d{2})\s*年", text)
    if my:
        y = int(my.group(1))
    for kw, mm in (("前三季度", 9), ("上半年", 6), ("一季度", 3), ("全年", 12)):
        if kw in text:
            if kw == "全年" and y is None:
                return (pub_y - 1, 12)
            return (y or pub_y, mm)
    m = re.search(r"1\s*[—\-~至到]\s*([一二三四五六七八九十]{1,3}|\d{1,2})\s*月", text)
    if m:
        mm = _mon(m.group(1))
        if mm:
            return (y or pub_y, mm)
    m = re.search(r"(?<![1—\-])([一二三四五六七八九十]{1,3}|\d{1,2})\s*月份", text)
    if m:
        mm = _mon(m.group(1))
        if mm and 1 <= mm <= 12:
            return (y or pub_y, mm)
    return None


def period_of(title, body, pub_ym):
    """四级期次解析：标题 → 首个「高技术…投资」命中前 320 字窗口 → 全文 → 发布日锚定。
    [修复] 原实现只看 body[:800]，而 NBS 正文前 800 字全是 CSS/导航，必然失败。"""
    pub_y, pub_m = int(pub_ym[:4]), int(pub_ym[4:])
    p = _period_from(title, pub_y)
    if p:
        return p, "title"
    m = re.search(r"高技术(?:制造业|产业|服务业)?投资", body)
    if m:
        p = _period_from(body[max(0, m.start() - 320):m.start() + 150], pub_y)
        if p:
            return p, "body_win"
    p = _period_from(body, pub_y)
    if p:
        return p, "body_full"
    if pub_m <= 2:
        return (pub_y - 1, 12), "pub_anchor"
    return (pub_y, pub_m - 1), "pub_anchor"


def _val(pat_yoys, pat_ann, body):
    for pat in pat_yoys:
        m = pat.search(body)
        if m:
            v = float(m.group(2))
            return -v if m.group(1) in NEG else v
    for pat in pat_ann:
        m = pat.search(body)
        if m:
            v = float(m.group(2))
            return -v if m.group(1) in NEG else v
    return None


def crawl_index():
    """遍历 sj/sjjd 列表页，返回 [{u, ym, d, t}]。"""
    seen, rows = set(), []
    for p in range(MAX_PAGES):
        url = BASE + ("index.html" if p == 0 else "index_%d.html" % p)
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
        # 连续 2 页无新增即停（WAF 抖动时不要跑满重试，否则 CI 20 分钟预算被吃光）
        if len(rows) == n0:
            _empty += 1
            if _empty >= 2:
                break
        else:
            _empty = 0
        time.sleep(0.3)
    return rows


def work(item):
    body = _strip(_get(BASE + item["u"][2:]))
    if len(body) < 300:
        return None
    mfg = _val((PAT_MFG_YOY,), (PAT_MFG_ANN,), body)
    ind = _val((PAT_IND_YOY,), (PAT_IND_ANN,), body)
    if mfg is None and ind is None:
        return None
    per, how = period_of(item["t"], body, item["ym"])
    if not per:
        return None
    return {"date": "%d-%02d" % per, "pub": item["d"], "how": how,
            "title": item["t"], "url": BASE + item["u"][2:],
            "mfg": mfg, "ind": ind}


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
    old_series = (old.get("series") or {})

    rows = crawl_index()
    cand = [r for r in rows if "投资" in r["t"]]
    print("[nbs] 列表 %d 篇，投资类 %d 篇" % (len(rows), len(cand)), flush=True)

    hits = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(work, cand):
            if r:
                hits.append(r)
    print("[nbs] 命中 %d 篇" % len(hits), flush=True)

    # 同期次取发布日最晚者
    best = {}
    for r in hits:
        k = r["date"]
        if k not in best or r["pub"] > best[k]["pub"]:
            best[k] = r

    def series(field):
        pts = []
        for d in sorted(best):
            v = best[d][field]
            if v is None:
                continue
            pts.append({"date": d, "value": v})
        # 增量：合并历史点（老数据若本期未抓到则保留，但同一 date 以本次为准）
        merged = {p["date"]: p["value"] for p in old_series.get(
            "high_tech_mfg" if field == "mfg" else "high_tech_industry", [])}
        merged.update({p["date"]: p["value"] for p in pts})
        return [{"date": d, "value": merged[d]} for d in sorted(merged)]

    mfg_s = series("mfg")
    ind_s = series("ind")
    if len(mfg_s) < 20:
        print("[nbs] 制造口径点数不足（%d），放弃写入" % len(mfg_s), file=sys.stderr)
        return 1

    doc = {
        "fetch_time": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": "国家统计局 固定资产投资统计司 月度/季度投资数据解读稿",
        "source_url": BASE,
        "caliber": "1—N 月累计同比（%），全国固定资产投资口径下高技术制造业/高技术产业",
        "known_gap": {
            "from": "2024-12", "to": "2026-01",
            "reason": "国家统计局 2025 年停发「高技术制造业投资」总量增速，"
                      "仅发布高技术服务业投资与分行业分项；2026-02 恢复。"
                      "该断档为官方发布口径变更，非采集失败，不做插值补造。",
        },
        "series": {"high_tech_mfg": mfg_s, "high_tech_industry": ind_s},
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[nbs] 制造 %d 点 -> %s@%s" % (len(mfg_s), mfg_s[-1]["value"], mfg_s[-1]["date"]))
    print("[nbs] 产业 %d 点 -> %s@%s" % (len(ind_s), ind_s[-1]["value"], ind_s[-1]["date"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
