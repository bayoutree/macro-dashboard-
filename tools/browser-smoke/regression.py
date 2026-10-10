#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周期看板 · 第2层浏览器门禁（DOM 可见文本 + 图形断言）
- 打开每个看板/每个 Tab，采集 Console 真实错误(pageerror + console.error)
- 断言: 关键可见文本、图形容器数量、已渲染 canvas 数量
- 硬闸门: 真实错误数==0 且 所有断言通过 -> exit 0，否则 exit 1
输出: 截图 + report.json 到 $OUT
"""
import os, sys, json, re
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8899").rstrip("/")
OUT  = os.environ.get("OUT", ".")
os.makedirs(OUT, exist_ok=True)

BENIGN = ["favicon", "net::ERR_", "Failed to load resource",
          "cdn.tailwindcss.com should not be used in production",
          "Download the React DevTools", "ERR_BLOCKED_BY_CLIENT", "ERR_NAME_NOT_RESOLVED"]
def benign(m): return any(b.lower() in m.lower() for b in BENIGN)

# click: 进入页面后需点击的元素（切 Tab）；charts: 统计图形容器的选择器
PAGES = [
    dict(name="cycle_global", path="/index.html", title_sub="宏观",
         click="button[data-tab=cycle]", chart_scope="#cycle-content",
         min_chart_divs=40, min_graphics=1, must_text=["全球周期"], must_not_text=[]),
    dict(name="stock", path="/stock_dashboard.html", title_sub="A股",
         chart_scope="body", min_chart_divs=10, min_graphics=5,
         must_text=["新鲜度"], must_not_text=[]),
    dict(name="macro_econ", path="/macro-econ-dashboard/index.html", title_sub="宏观",
         chart_scope="body", min_chart_divs=20, min_graphics=10, must_text=[], must_not_text=[]),
]

def run():
    report = {"base": BASE, "pages": [], "pass": True}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        for d in PAGES:
            rec = {"name": d["name"], "url": BASE + d["path"], "console_errors": [], "pageerrors": [],
                   "real_errors": 0, "checks": [], "pass": True}
            page = browser.new_page(viewport={"width": 1440, "height": 2400})
            errs = []
            page.on("console", lambda m: errs.append(("console", m.text)) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append(("pageerror", str(e))))
            try:
                page.goto(BASE + d["path"], wait_until="load", timeout=60000)
                page.wait_for_timeout(3000)
                if d.get("click"):
                    page.click(d["click"], timeout=15000)
                    page.wait_for_timeout(4000)
                title = page.title()
                scope = d["chart_scope"]
                stats = page.evaluate("""(scope) => {
                    const root = document.querySelector(scope) || document.body;
                    const divs = root.querySelectorAll('[id^=chart], .chart, [id$=-sparkline]').length;
                    const canvas = root.querySelectorAll('canvas').length;
                    const svg = root.querySelectorAll('svg').length;
                    return {divs, canvas, svg, graphics: canvas+svg, textLen: root.innerText.length};
                }""", scope)
                body_text = page.inner_text("body")
            except Exception as e:
                rec["checks"].append(["goto/click", False, f"导航/交互失败: {e}"]); rec["pass"] = False
                report["pages"].append(rec); page.close(); continue

            real = [m for (k, m) in errs if not benign(m)]
            rec["console_errors"] = [m for (k, m) in errs if k == "console" and not benign(m)]
            rec["pageerrors"]     = [m for (k, m) in errs if k == "pageerror" and not benign(m)]
            rec["real_errors"]    = len(real)

            def check(label, cond, detail=""):
                rec["checks"].append([label, bool(cond), detail])
                if not cond: rec["pass"] = False

            check("title含'%s'" % d["title_sub"], d["title_sub"] in title, f"title={title!r}")
            check("图形容器>=%d" % d["min_chart_divs"], stats["divs"] >= d["min_chart_divs"], f"chart_divs={stats['divs']}")
            check("图形元素(canvas+svg)>=%d" % d["min_graphics"], stats["graphics"] >= d["min_graphics"], f"graphics={stats['graphics']}(canvas={stats['canvas']},svg={stats['svg']})")
            for t in d["must_text"]:    check(f"可见文本含{t!r}", t in body_text)
            for t in d["must_not_text"]: check(f"可见文本不含{t!r}", t not in body_text)
            check("Console真实错误==0", len(real) == 0, "; ".join(real[:5]))
            try: page.screenshot(path=os.path.join(OUT, f"{d['name']}.png"))
            except Exception as e: rec["checks"].append(["screenshot", False, str(e)])

            rec["raw_identifier_tokens"] = sorted(set(re.findall(r"\b[a-z]{2,}(?:_[a-z0-9]+){1,}\b", body_text)))
            rec["title"] = title; rec["stats"] = stats
            if not rec["pass"]: report["pass"] = False
            report["pages"].append(rec)
            page.close()
        browser.close()

    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n================ 第2层浏览器门禁报告 ================")
    for r in report["pages"]:
        print(f"[{'PASS' if r['pass'] else 'FAIL'}] {r['name']:13s} {r.get('stats','')} real_err={r['real_errors']}")
        for c in r["checks"]:
            if not c[1]: print(f"      ✗ {c[0]}  {c[2]}")
    print("\n---------- T-3 检查（可见文本中的裸露 snake_case 标识符，报告项） ----------")
    for r in report["pages"]:
        toks = r.get("raw_identifier_tokens", [])
        print(f"  {r['name']:13s} 裸露标识符 {len(toks)} 个: {toks}")
    print(f"\n总判定: {'PASS' if report['pass'] else 'FAIL'}  (report.json -> {OUT})")
    return 0 if report["pass"] else 1

if __name__ == "__main__":
    sys.exit(run())
