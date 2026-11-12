#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monthly_audit.py — 看板月度数据健康巡检（可复跑，输出证据留档）

三层结构，逐层可独立运行：
  L1 数据层  ：按 data/freshness_manifest.json 逐指标判定 FRESH/WATCH/STALE/GAP，
              并追加"值层"体检（空值写入 / 零值污染 / 序列停滞 / 量级异常）。
  L2 发布层  ：线上 Pages 实际服务的 JSON ←→ 仓库 HEAD 数据，逐文件比对 as_of 与内容。
              这是"代码端/数据端正常但页面不是最新"的直接探针。
  L3 呈现层  ：真实浏览器打开页面，读取 DOM 实际显示值，与数据源逐项比对；
              并检查「✅最新」徽标是否失真、mock 兜底是否静默生效、
              是否残留占位符/裸露英文标识符。需要 playwright（缺失则 SKIP，不静默通过）。

用法：
  python3 tools/healthcheck/monthly_audit.py                 # 全量（含线上+渲染）
  python3 tools/healthcheck/monthly_audit.py --offline       # 只跑 L1（无网络）
  python3 tools/healthcheck/monthly_audit.py --no-render     # L1+L2
  python3 tools/healthcheck/monthly_audit.py --serve-local   # L3 打本地静态服务而非线上
  python3 tools/healthcheck/monthly_audit.py --eval-date 2026-11-30

退出码：0=无 FAIL；1=有 FAIL；2=运行错误。
产物：tools/healthcheck/output/monthly_audit_<时间戳>.json / .md（证据留档）

判定口径：
  PASS = 该指标/该层检查通过
  WARN = 未超容忍但需跟踪（如 warn 级指标 STALE、WATCH 状态、内容差异但 as_of 相同）
  FAIL = 触发硬门禁（critical 指标 STALE/GAP、线上落后仓库、页面值≠源值、徽标失真、mock 静默生效）
  SKIP = 该检查本轮不适用（deprecated 指标、无浏览器等），SKIP 不计入通过
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_LIVE = "https://bayoutree.github.io/macro-dashboard-/"
REGISTRY = SCRIPT_DIR / "render_registry.json"
OUT_DIR = SCRIPT_DIR / "output"

# 线上服务必须与仓库一致的核心数据文件（其余按 manifest 汇总）
CORE_FILES = [
    "data/timing_scores.json",
    "data/timing_right_scores.json",
    "data/feargreed_pointer.json",
    "data/dashboard_summary.json",
    "data/cycle_position_v4.json",
    "data/us_macro.json",
    "data/cn_macro.json",
    "data/asset_prices.json",
    "macro-econ-dashboard/data/indicators.json",
]

R = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP"}


# ------------------------------------------------------------------ 基础设施
class Result:
    __slots__ = ("code", "layer", "target", "status", "detail", "extra")

    def __init__(self, code, layer, target, status, detail="", extra=None):
        self.code, self.layer, self.target = code, layer, target
        self.status, self.detail, self.extra = status, detail, extra or {}

    def as_dict(self):
        d = {"code": self.code, "layer": self.layer, "target": self.target,
             "status": self.status, "detail": self.detail}
        if self.extra:
            d["extra"] = self.extra
        return d


def log(msg):
    print(msg, flush=True)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")


def resolve(obj, path: str):
    """支持 a.b[-1].c 形式路径；不可达返回 None。"""
    if not path:
        return obj
    cur = obj
    for m in _PATH_TOKEN.finditer(path):
        key, idx = m.group(1), m.group(2)
        if key is not None:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return None
        else:
            i = int(idx)
            if isinstance(cur, list) and -len(cur) <= i < len(cur):
                cur = cur[i]
            else:
                return None
    return cur


def last_date_of(value):
    """从标量/序列/字典里取出最后一个业务日期（YYYY-MM-DD / YYYY-MM / YYYYQn 归一为元组）。"""
    def parse(v):
        s = str(v).strip()
        m = re.match(r"^(\d{4})\s*[Qq]([1-4])$", s)
        if m:
            return (int(m.group(1)), int(m.group(2)) * 3, 0)
        m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$", s)
        if m:
            return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))
        return None

    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return parse(value)
    if isinstance(value, dict):
        for k in ("date", "period", "as_of", "update_date", "last_updated"):
            if k in value:
                p = parse(value[k])
                if p:
                    return p
        return None
    if isinstance(value, list):
        for el in reversed(value):
            p = None
            if isinstance(el, dict):
                for k in ("date", "period"):
                    if k in el:
                        p = parse(el[k])
                        if p:
                            break
            elif isinstance(el, (list, tuple)) and el:
                p = parse(el[0])
            elif isinstance(el, str):
                p = parse(el)
            if p:
                return p
    return None


def fmt_key(k):
    if not k:
        return "-"
    y, m, d = k
    if m == 0:
        return f"{y}"
    if d == 0:
        return f"{y}-{m:02d}"
    return f"{y}-{m:02d}-{d:02d}"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def http_get(url, timeout=45, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "Cache-Control": "no-cache, no-store", "Pragma": "no-cache",
                "User-Agent": "macro-dashboard-monthly-audit/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception as e:  # 网络抖动重试一次
            last = e
            time.sleep(2 + i * 3)
    raise last  # type: ignore[misc]


# ------------------------------------------------------------------ L1 数据层
RATE_LIKE = re.compile(r"(增速|同比|环比|率|占比|百分位|偏离|溢价|折价|差$)")


def _is_rate_like(item):
    """率/增速/占比类指标（单位应为 %），用于量级体检，避免把亿元/点误判。"""
    name = str(item.get("name") or "")
    if re.search(r"(总额|余额|金额|亿元|万|点|指数|领先指标)", name):
        return False
    return bool(RATE_LIKE.search(name))


def _period_verdict(item, last, eval_date: dt.date):
    """月频/季频指标的"期次口径"判定：以"应已发布的最新期"为基准，而非自然日差。

    背景：indicators.json 里月频日期常存期初（2026-09-01），用自然日阈值会系统性高估
    一个月，造成稳定误报（us_cpi / cn_house_price / us_federal_debt 均属此类）。
    规则：publish(period) = period_end + lag_days；取 publish <= eval_date 的最大期次为 expected。
      data_period == expected      → PASS
      data_period == expected-1 期 → WARN（发布窗口内，等 1 期）
      更旧                          → FAIL（真实断更）
    """
    freq = item.get("freq")
    lag = int(item.get("lag_days") or 0)
    if not last or freq not in ("monthly", "quarterly"):
        return None
    y, m, d = last
    m = m or 1
    span = 1 if freq == "monthly" else 3
    start_idx = (y * 12 + (m - 1)) // span
    eval_idx = (eval_date.year * 12 + eval_date.month - 1) // span

    def period_end(idx):
        y2, m2 = divmod(idx * span, 12)
        m2 += 1
        nxt = dt.date(y2 + (1 if m2 == 12 else 0), 1 if m2 == 12 else m2 + 1, 1)
        return nxt - dt.timedelta(days=1)

    def publish(idx):
        return period_end(idx) + dt.timedelta(days=lag)

    expected = eval_idx
    while expected > start_idx and publish(expected) > eval_date:
        expected -= 1
    gap = expected - start_idx
    if gap <= 0:
        return R["PASS"], f"期次对齐：数据期 {y}-{m:02d}，应已发布最新期 {period_end(expected).strftime('%Y-%m')}"
    if gap == 1:
        return R["WARN"], f"期次落后 1 期：数据期 {y}-{m:02d}，应已发布至 {period_end(expected).strftime('%Y-%m')}（发布窗口内）"
    return R["FAIL"], (f"期次落后 {gap} 期：数据期 {y}-{m:02d}，应已发布至 "
                       f"{period_end(expected).strftime('%Y-%m')}（真实断更）")


def layer_data(root: Path, eval_key, eval_date, results: list, coverage: dict, strict: bool = False):
    """逐指标：新鲜度（复用 freshness_gate 口径）+ 值层体检。"""
    sys.path.insert(0, str(root / "scripts"))
    try:
        import freshness_gate as F  # noqa: N813
    except Exception as exc:
        results.append(Result("HC-D-000", "L1", "freshness_gate", R["FAIL"],
                              f"无法加载 scripts/freshness_gate.py：{exc}"))
        return

    manifest = load_json(root / "data" / "freshness_manifest.json")
    try:
        F.validate_manifest(manifest)
    except Exception as exc:
        results.append(Result("HC-D-001", "L1", "manifest", R["FAIL"], f"manifest 结构非法：{exc}"))
        return

    cache = {}
    n_active = 0
    for idx, item in enumerate(manifest["items"]):
        mid, rel = item["id"], item["file"]
        code = f"HC-D-{idx + 1:03d}"
        if item.get("status") == "deprecated":
            results.append(Result(code, "L1", mid, R["SKIP"], "deprecated，不评估"))
            continue
        n_active += 1
        path = root / rel
        if not path.exists():
            results.append(Result(code, "L1", mid, R["FAIL"], f"数据文件缺失 {rel}"))
            continue
        if rel not in cache:
            try:
                cache[rel] = load_json(path)
            except Exception as exc:
                results.append(Result(code, "L1", mid, R["FAIL"], f"{rel} JSON 解析失败：{exc}"))
                continue
        doc = cache[rel]

        # 1) 新鲜度
        try:
            status = F.compute_status(item, doc, eval_key, manifest.get("calendar", "CN"))
        except Exception as exc:
            results.append(Result(code, "L1", mid, R["FAIL"], f"新鲜度计算异常：{exc}"))
            continue

        # 2) 最后业务日期
        paths = F.expand_paths(doc, item.get("date_path"))
        dates = [last_date_of(F.deep_get(doc, p, None)) for p in paths]
        dates = [d for d in dates if d]
        last = max(dates) if dates else None

        critical = bool(item.get("critical"))
        sev = item.get("severity_on_gap")
        if status == "FRESH":
            st = R["PASS"]
        elif status == "WATCH":
            st = R["WARN"]
        else:  # STALE / GAP
            if strict:
                st = R["FAIL"]
            else:
                st = R["FAIL"] if (critical or sev == "block") else R["WARN"]

        detail = (f"{item.get('freq')} · 末点 {fmt_key(last)} · lag={item.get('lag_days')}d · "
                  f"critical={critical} · sev={sev}")

        # 月频/季频以"期次口径"为准（避免期初日期表示导致系统性误报）
        pv = _period_verdict(item, last, eval_date)
        if pv:
            pst, pdetail = pv
            if pst == R["FAIL"] and not (critical or sev == "block") and not strict:
                pst = R["WARN"]
            st = pst
            detail = f"{pdetail} | 自然日口径={status} · {detail}"

        results.append(Result(code, "L1", mid, st, f"{status} | {detail}",
                              {"status": status, "last": fmt_key(last), "freq": item.get("freq"),
                               "file": rel, "critical": critical}))

        # 3) 值层体检（只对 active 且有 value_path 的指标）
        vp = item.get("value_path")
        if not vp:
            continue
        val = F.deep_get(doc, vp, None)
        hp = item.get("history_path")
        hist = F.deep_get(doc, hp, None) if hp else None
        tail = None
        if isinstance(hist, list) and hist:
            tail = hist[-1].get("value") if isinstance(hist[-1], dict) else hist[-1]

        # 3a 空值写入：快照为空但历史有值
        if val is None and tail is not None:
            results.append(Result(f"{code}v", "L1", f"{mid}.value", R["FAIL"],
                                  f"空值写入：value=null 但 history 尾点={tail}（禁止用空值覆盖）"))
        # 3b 零值污染：value=0 而历史非零
        elif isinstance(val, (int, float)) and val == 0 and isinstance(tail, (int, float)) and abs(tail) > 1e-9:
            results.append(Result(f"{code}v", "L1", f"{mid}.value", R["FAIL"],
                                  f"零值污染：value=0 但 history 尾点={tail}（上游失效被写成 0）"))
        # 3c 序列停滞：日频最近 5 点完全相同（同值不算断更，但需跟踪）
        elif item.get("freq") == "daily" and isinstance(hist, list) and len(hist) >= 5:
            vals = [h.get("value") if isinstance(h, dict) else h for h in hist[-5:]]
            if all(isinstance(v, (int, float)) for v in vals) and len(set(vals)) == 1:
                results.append(Result(f"{code}v", "L1", f"{mid}.history", R["WARN"],
                                      f"序列停滞：最近 5 点均为 {vals[0]}（疑似沿用旧值）"))
        # 3d 量级异常：仅对"率/增速/占比"类指标生效，绝对值超 ±60 视为口径或缩放错误
        elif isinstance(val, (int, float)) and _is_rate_like(item) and abs(val) > 60:
            results.append(Result(f"{code}v", "L1", f"{mid}.value", R["WARN"],
                                  f"量级异常：{mid}={val}（率/增速类指标超 ±60，疑似口径或缩放错误）"))

    coverage["l1_active_items"] = n_active
    coverage["l1_manifest_items"] = len(manifest["items"])
    _check_file_registry(root, manifest, results, coverage, strict)


def _check_file_registry(root: Path, manifest, results: list, coverage: dict, strict: bool):
    """数据文件登记对账：data/ 下每个 JSON 必须属于 Tier A（manifest）/B/C（file_tiers）。"""
    tiers_path = SCRIPT_DIR / "file_tiers.json"
    tiers = load_json(tiers_path) if tiers_path.exists() else {"tier_b": [], "tier_c": [], "ignore": []}
    registered = {it["file"] for it in manifest["items"]}
    known = registered | set(tiers.get("tier_b", [])) | set(tiers.get("tier_c", [])) | set(tiers.get("ignore", []))

    actual = sorted(str(p.relative_to(root)) for p in (root / "data").glob("*.json"))
    actual += sorted(str(p.relative_to(root)) for p in (root / "macro-econ-dashboard" / "data").glob("*.json"))
    orphans = [f for f in actual if f not in known]

    # Tier B/C 只验可解析，避免"文件在但内容是垃圾"
    bad = []
    for rel in tiers.get("tier_b", []):
        p = root / rel
        if not p.exists():
            bad.append(f"{rel}(缺失)")
            continue
        try:
            load_json(p)
        except Exception:
            bad.append(f"{rel}(解析失败)")

    coverage["l1_data_files"] = len(actual)
    coverage["l1_registered_files"] = len(known & set(actual))
    coverage["l1_orphan_files"] = len(orphans)

    st = R["PASS"] if not orphans and not bad else (R["FAIL"] if strict else R["WARN"])
    detail = (f"数据文件 {len(actual)} 个，已登记 {len(known & set(actual))} 个"
              + (f"；未登记 {len(orphans)} 个：{orphans[:6]}" if orphans else "")
              + (f"；Tier B 异常：{bad[:4]}" if bad else ""))
    results.append(Result("HC-D-REG", "L1", "data_file_registry", st, detail,
                          {"orphans": orphans, "tier_b_bad": bad}))


# ------------------------------------------------------------------ L2 发布层
def _json_diff(a, b, path="", limit=5, acc=None):
    """返回 (差异处数, 前若干处路径)；用于"as_of 相同但内容不同"的定位。"""
    if acc is None:
        acc = []
    n = [0]

    def walk(x, y, p):
        if len(acc) >= limit:
            n[0] += 1
            return
        if type(x) is not type(y) and not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
            acc.append(f"{p or '/'}(类型 {type(x).__name__}→{type(y).__name__})")
            n[0] += 1
            return
        if isinstance(x, dict):
            for k in x.keys() | y.keys():
                if k not in x or k not in y:
                    acc.append(f"{p}/{k}(缺失)")
                    n[0] += 1
                    if len(acc) >= limit:
                        break
                else:
                    walk(x[k], y[k], f"{p}/{k}")
        elif isinstance(x, list):
            if len(x) != len(y):
                acc.append(f"{p}(长度 {len(x)}→{len(y)})")
                n[0] += 1
            for i in range(min(len(x), len(y))):
                walk(x[i], y[i], f"{p}[{i}]")
        elif x != y:
            acc.append(f"{p}: {x!r}→{y!r}")
            n[0] += 1

    walk(a, b, path)
    return n[0], acc[:limit]


def layer_publish(root: Path, live_base: str, results: list, coverage: dict):
    """线上 Pages 实际服务内容 ←→ 仓库 HEAD。核心探针：线上 as_of 是否落后仓库。"""
    if not live_base:
        results.append(Result("HC-P-000", "L2", "live", R["SKIP"], "--offline，跳过发布层"))
        return

    manifest = load_json(root / "data" / "freshness_manifest.json")
    items_by_file = {}
    for it in manifest["items"]:
        if it.get("status") == "deprecated":
            continue
        items_by_file.setdefault(it["file"], []).append(it)

    files = list(dict.fromkeys(CORE_FILES + list(items_by_file)))
    ok = 0
    for i, rel in enumerate(files):
        code = f"HC-P-{i + 1:03d}"
        local_path = root / rel
        if not local_path.exists():
            results.append(Result(code, "L2", rel, R["FAIL"], "仓库内文件缺失"))
            continue
        local = local_path.read_bytes()
        try:
            status, live = http_get(live_base.rstrip("/") + "/" + rel)
        except Exception as exc:
            results.append(Result(code, "L2", rel, R["FAIL"], f"线上不可达：{exc}"))
            continue
        if status != 200:
            results.append(Result(code, "L2", rel, R["FAIL"], f"线上 HTTP {status}"))
            continue
        ok += 1

        try:
            lj, vj = json.loads(local), json.loads(live)
        except Exception as exc:
            results.append(Result(code, "L2", rel, R["FAIL"], f"线上 JSON 解析失败：{exc}"))
            continue

        def as_of(doc, it=None):
            if it:
                paths = [it.get("date_path")]
            else:
                paths = ["update_date", "as_of", "update_time", "_meta.assessment_date"]
            best = None
            for p in paths:
                k = last_date_of(resolve(doc, p))
                if k and (best is None or k > best):
                    best = k
            return best

        it = (items_by_file.get(rel) or [None])[0]
        lk, vk = as_of(lj, it), as_of(vj, it)
        same = sha(local) == sha(live)
        semantic_same = lj == vj
        if lk and vk and vk < lk:
            results.append(Result(code, "L2", rel, R["FAIL"],
                                  f"线上落后仓库：线上 as_of={fmt_key(vk)} < 仓库 {fmt_key(lk)}（部署/发布未完成）",
                                  {"live_as_of": fmt_key(vk), "repo_as_of": fmt_key(lk)}))
        elif lk and vk and vk > lk:
            results.append(Result(code, "L2", rel, R["WARN"],
                                  f"本地仓库落后线上：仓库 as_of={fmt_key(lk)} < 线上 {fmt_key(vk)}（先 git pull 再复核）",
                                  {"live_as_of": fmt_key(vk), "repo_as_of": fmt_key(lk)}))
        elif semantic_same:
            results.append(Result(code, "L2", rel, R["PASS"],
                                  f"线上与仓库语义一致 as_of={fmt_key(vk) or '-'}"
                                  + ("" if same else "（仅字节差异：空白/键序）")))
        else:
            diff = _json_diff(lj, vj, limit=5)
            results.append(Result(code, "L2", rel, R["WARN"],
                                  f"as_of 一致（{fmt_key(vk) or '-'}）但内容不同，差异字段 {diff[0]} 处：{diff[1]}",
                                  {"live_as_of": fmt_key(vk), "repo_as_of": fmt_key(lk)}))

    # 页面骨架可达性（页面能否打开是呈现的前提）
    for i, page in enumerate(["index.html", "stock_dashboard.html", "macro-econ-dashboard/index.html"]):
        code = f"HC-P-P{i + 1:02d}"
        try:
            status, body = http_get(live_base.rstrip("/") + "/" + page)
            okk = status == 200 and len(body) > 5000
            results.append(Result(code, "L2", page, R["PASS"] if okk else R["FAIL"],
                                  f"HTTP {status} · {len(body)}B"))
        except Exception as exc:
            results.append(Result(code, "L2", page, R["FAIL"], f"不可达：{exc}"))

    coverage["l2_files"] = len(files)
    coverage["l2_files_reachable"] = ok


# ------------------------------------------------------------------ L3 呈现层
def _serve(root: Path, port: int):
    import http.server
    import socketserver
    import threading

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **kw)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def layer_render(root: Path, base: str, registry: dict, results: list, coverage: dict, shots: Path):
    """真实浏览器：页面显示值 ←→ 数据源值；徽标失真；mock 静默；占位符/裸标识符。"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:
        results.append(Result("HC-R-000", "L3", "playwright", R["SKIP"],
                              f"无浏览器环境，呈现层未执行（不得视为通过）：{type(exc).__name__}"))
        coverage["l3_skipped"] = True
        return

    from playwright.sync_api import sync_playwright

    BENIGN = ("favicon", "net::ERR_", "Failed to load resource",
              "cdn.tailwindcss.com should not be used in production",
              "Download the React DevTools", "ERR_BLOCKED_BY_CLIENT", "ERR_NAME_NOT_RESOLVED")

    def benign(m):
        return any(b.lower() in m.lower() for b in BENIGN)

    cache = {}
    checked = 0
    badges_checked = 0
    today = dt.date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        for page_def in registry["pages"]:
            name = page_def["name"]
            url = base.rstrip("/") + page_def["url"]
            rec_errs = []
            page = browser.new_page(viewport={"width": 1440, "height": 2400})
            page.on("console", lambda m: rec_errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: rec_errs.append(str(e)))
            try:
                page.goto(url, wait_until="load", timeout=90000)
                page.wait_for_timeout(page_def.get("wait_ms", 5000))
                if page_def.get("click"):
                    try:
                        page.click(page_def["click"], timeout=20000)
                        page.wait_for_timeout(5000)
                    except Exception as exc:
                        results.append(Result(f"HC-R-{name}-nav", "L3", name, R["FAIL"],
                                              f"Tab 切换失败：{exc}"))
                body_text = page.inner_text("body")
            except Exception as exc:
                results.append(Result(f"HC-R-{name}-nav", "L3", name, R["FAIL"], f"页面打开失败：{exc}"))
                page.close()
                continue

            # 3a Console 真实错误
            real = [m for m in rec_errs if not benign(m)]
            results.append(Result(f"HC-R-{name}-err", "L3", name,
                                  R["PASS"] if not real else R["FAIL"],
                                  f"Console 真实错误 {len(real)} 个" + (f"：{real[:3]}" if real else "")))

            # 3b 显示值 ←→ 源值
            for v in page_def.get("values", []):
                code = f"HC-R-{name}-{v['id']}"
                try:
                    txt = page.inner_text(f"#{v['id']}").strip()
                except Exception:
                    results.append(Result(code, "L3", v["label"], R["FAIL"], "DOM 元素不存在（卡片未渲染）"))
                    continue
                num = re.search(r"-?\d+(?:\.\d+)?", txt.replace(",", ""))
                if not num:
                    results.append(Result(code, "L3", v["label"], R["FAIL"], f"页面无数值：{txt!r}"))
                    continue
                shown = float(num.group(0))
                rel = v["file"]
                if rel not in cache:
                    try:
                        cache[rel] = load_json(root / rel)
                    except Exception as exc:
                        results.append(Result(code, "L3", v["label"], R["FAIL"], f"源文件读取失败：{exc}"))
                        continue
                src = resolve(cache[rel], v["path"])
                if isinstance(src, str):  # "3.88%" 之类
                    mm = re.search(r"-?\d+(?:\.\d+)?", src.replace(",", ""))
                    src = float(mm.group(0)) if mm else None
                if not isinstance(src, (int, float)):
                    results.append(Result(code, "L3", v["label"], R["FAIL"],
                                          f"源值非数值（{v['file']}:{v['path']} = {src!r}）"))
                    continue
                checked += 1
                tol = float(v.get("tolerance", 0.01))
                if abs(shown - float(src)) <= tol:
                    results.append(Result(code, "L3", v["label"], R["PASS"],
                                          f"页面 {shown} ≈ 源 {src}", {"shown": shown, "source": src}))
                else:
                    results.append(Result(code, "L3", v["label"], R["FAIL"],
                                          f"页面显示 {shown} ≠ 源 {src}（容差 {tol}）",
                                          {"shown": shown, "source": src}))

            # 3c 徽标失真：✅最新 的日期必须在频率周期内
            rules = registry["badge_rules"]["cycle_days"]
            try:
                badge_texts = page.eval_on_selector_all(
                    ".freshness-badge, [class*='freshness-badge']",
                    "els => els.map(e => e.innerText)")
            except Exception:
                badge_texts = []
            stale_fresh = []
            lag_fresh = []
            for t in badge_texts:
                if "最新" not in t:
                    continue
                m = re.search(r"(\d{4}-\d{2}(?:-\d{2})?)", t)
                if not m:
                    continue
                badges_checked += 1
                try:
                    d = dt.date.fromisoformat(m.group(1) if len(m.group(1)) == 10 else m.group(1) + "-01")
                except Exception:
                    continue
                days = (today - d).days
                limit = 60
                for k, lim in rules.items():
                    if k in t:
                        limit = lim
                        break
                if days > limit:
                    # 落后 1 个周期内 = WARN（发布窗口/口径边界）；超 2 个周期 = 真失真 FAIL
                    (stale_fresh if days > 2 * limit else lag_fresh).append(
                        (t.strip()[:40], days, limit))
            st = R["PASS"] if not stale_fresh and not lag_fresh else (R["FAIL"] if stale_fresh else R["WARN"])
            det = f"检查 {badges_checked} 个徽标"
            if stale_fresh:
                det += f"；「✅最新」真失真 {len(stale_fresh)} 处（超 2 个周期）：{stale_fresh[:3]}"
            if lag_fresh:
                det += f"；落后 1 个周期 {len(lag_fresh)} 处（待复核是否应更新）：{lag_fresh[:3]}"
            results.append(Result(f"HC-R-{name}-badge", "L3", name, st, det,
                                  {"badges": badges_checked,
                                   "stale": stale_fresh[:10], "lag1": lag_fresh[:10]}))

            # 3d mock 兜底静默生效
            markers = [t for t in registry["mock_markers"]["tokens"] if t in body_text]
            results.append(Result(f"HC-R-{name}-mock", "L3", name,
                                  R["PASS"] if not markers else R["FAIL"],
                                  "无 mock 残留" if not markers else
                                  f"mock 兜底未覆盖：可见文本出现 {markers}（真实数据未生效）"))

            # 3e 占位符 / 裸露英文标识符
            ph = len(re.findall(r"数据待获取", body_text)) + len(re.findall(r"(?<![\d.])--(?![\d.])", body_text))
            results.append(Result(f"HC-R-{name}-ph", "L3", name, R["PASS"] if ph <= 3 else R["WARN"],
                                  f"占位符 {ph} 处"))
            toks = sorted(set(re.findall(r"\b[a-z]{2,}(?:_[a-z0-9]+){1,}\b", body_text)))
            results.append(Result(f"HC-R-{name}-raw", "L3", name, R["PASS"] if not toks else R["WARN"],
                                  f"裸露英文标识符 {len(toks)} 个" + (f"：{toks[:5]}" if toks else ""),
                                  {"tokens": toks[:20]}))

            try:
                shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shots / f"{name}.png"), full_page=False)
            except Exception:
                pass
            page.close()
        browser.close()

    coverage["l3_value_assertions"] = checked
    coverage["l3_badges"] = badges_checked


# ------------------------------------------------------------------ 报告
def write_report(root: Path, results: list, coverage: dict, meta: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta["generated_at"].replace(":", "").replace(" ", "_")[:15]
    counts = {k: sum(1 for r in results if r.status == k) for k in R}
    verdict = "FAIL" if counts["FAIL"] else "PASS"
    report = {"meta": meta, "coverage": coverage,
              "summary": {"total": len(results), **counts, "verdict": verdict},
              "results": [r.as_dict() for r in results]}
    jpath = out_dir / f"monthly_audit_{stamp}.json"
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    L = []
    L.append(f"# 看板月度数据健康巡检报告 · {meta['eval_date']}")
    L.append("")
    L.append(f"- 生成时间：{meta['generated_at']}")
    L.append(f"- 仓库：{meta['repo']} @ `{meta['git_sha']}`")
    L.append(f"- 线上基准：{meta['live_base'] or '（离线）'}")
    L.append(f"- 结论：**{verdict}**（PASS {counts['PASS']} / WARN {counts['WARN']} / "
             f"FAIL {counts['FAIL']} / SKIP {counts['SKIP']}，共 {len(results)} 项）")
    L.append("")
    if counts["FAIL"]:
        L.append("## 阻断项（FAIL）")
        L.append("")
        L.append("| 编号 | 层 | 对象 | 说明 |")
        L.append("|---|---|---|---|")
        for r in results:
            if r.status == "FAIL":
                L.append(f"| `{r.code}` | {r.layer} | {r.target} | {r.detail} |")
        L.append("")
    L.append("## 跟踪项（WARN）")
    L.append("")
    L.append("| 编号 | 层 | 对象 | 说明 |")
    L.append("|---|---|---|---|")
    for r in results:
        if r.status == "WARN":
            L.append(f"| `{r.code}` | {r.layer} | {r.target} | {r.detail} |")
    L.append("")
    L.append("## 全量明细")
    L.append("")
    L.append("| 编号 | 层 | 对象 | 状态 | 说明 |")
    L.append("|---|---|---|---|---|")
    for r in results:
        L.append(f"| `{r.code}` | {r.layer} | {r.target} | {r.status} | {r.detail} |")
    L.append("")
    L.append("## 覆盖率")
    L.append("")
    for k, v in coverage.items():
        L.append(f"- {k}: {v}")
    L.append("")
    mpath = out_dir / f"monthly_audit_{stamp}.md"
    mpath.write_text("\n".join(L), encoding="utf-8")
    return jpath, mpath, verdict, counts


def git_sha(root: Path):
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=15).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="看板月度数据健康巡检（L1 数据 / L2 发布 / L3 呈现）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="仓库根目录")
    ap.add_argument("--live-base", default=DEFAULT_LIVE, help="线上基准 URL（空字符串=跳过 L2）")
    ap.add_argument("--offline", action="store_true", help="跳过 L2/L3，仅 L1")
    ap.add_argument("--strict", action="store_true", help="严格模式：任何 STALE/GAP 均判 FAIL（月度巡检推荐）")
    ap.add_argument("--no-render", action="store_true", help="跳过 L3")
    ap.add_argument("--serve-local", action="store_true", help="L3 打本地静态服务而非线上")
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--eval-date", default=None, help="评估日 YYYY-MM-DD，默认今天")
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not (root / "data" / "freshness_manifest.json").exists():
        log(f"[FAIL] 不是看板仓库：{root}")
        return 2

    eval_date = args.eval_date or dt.date.today().isoformat()
    results: list = []
    coverage: dict = {}
    meta = {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "eval_date": eval_date, "repo": str(root), "git_sha": git_sha(root),
            "live_base": "" if args.offline else args.live_base}

    sys.path.insert(0, str(root / "scripts"))
    import freshness_gate as F  # noqa: N813
    eval_key = F.date_key(eval_date)

    log(f"== L1 数据层（eval_date={eval_date}）")
    layer_data(root, eval_key, dt.date.fromisoformat(eval_date), results, coverage, strict=args.strict)

    if not args.offline:
        log("== L2 发布层（线上 ←→ 仓库）")
        layer_publish(root, args.live_base, results, coverage)

    if not args.offline and not args.no_render:
        log("== L3 呈现层（真实浏览器）")
        base = args.live_base
        httpd = None
        if args.serve_local:
            httpd = _serve(root, args.port)
            base = f"http://127.0.0.1:{args.port}"
            log(f"   本地静态服务 {base}")
        try:
            registry = load_json(Path(args.registry))
            layer_render(root, base, registry, results, coverage,
                         Path(args.out) / "screenshots")
        finally:
            if httpd:
                httpd.shutdown()

    jpath, mpath, verdict, counts = write_report(root, results, coverage, meta, Path(args.out))
    log("")
    log(f"[{verdict}] PASS {counts['PASS']} / WARN {counts['WARN']} / FAIL {counts['FAIL']} / SKIP {counts['SKIP']}")
    for r in results:
        if r.status == "FAIL":
            log(f"  ✗ {r.code} {r.target}：{r.detail}")
    log(f"证据：{jpath}")
    log(f"      {mpath}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[monthly-audit] 运行错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
