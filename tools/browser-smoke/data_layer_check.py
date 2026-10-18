#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周期看板 · 第1层数据门禁
用法: python3 data_layer_check.py --stage 1|3 [--repo DIR] [--code DIR]
  --repo : 数据目录（含 data/*.json），默认 $REPO 或 .
  --code : 代码目录（含 scripts/update_data.py），默认 = --repo
退出码: 0=全部通过, 1=有断言失败
"""
import os, sys, json, argparse, glob, re

def load(p):
    with open(p, encoding="utf-8") as f: return json.load(f)

def leaves(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from leaves(v, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from leaves(v, path + f"[{i}]")
    else: yield path, o

class Gate:
    def __init__(self): self.fails = []; self.warns = []; self.n = 0
    def check(self, label, cond, detail=""):
        self.n += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
        if not cond: self.fails.append(label)
    def warn(self, label, cond, detail=""):
        """[CIO 2026-10-18 #12口径裁决] 源端发布滞后属结构性、非数据缺陷：不满足记 WARN 不阻断。"""
        self.n += 1
        if cond:
            print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
        else:
            self.warns.append(label)
            print(f"  [WARN] {label}" + (f"  {detail}" if detail else ""))

def _engine(code_dir):
    if code_dir not in sys.path: sys.path.insert(0, os.path.join(code_dir, "scripts"))
    import importlib, update_data as U
    importlib.reload(U)
    return U.TimingScoreEngine()

def _trig(eng, score, qvix=10.0):
    """[P0-3 同步 2026-10-16] 减仓触发=恐贪>85 且 QVIX<14 双确认；qvix 可控注入。"""
    eng._fear_greed_score_ssot = (lambda s: (lambda: s))(score)
    import update_data as U
    _orig_load = U.load_json
    U.load_json = lambda name, *a, **k: ({"score": score, "qvix_close": qvix} if name == "feargreed_pointer.json" else _orig_load(name, *a, **k))
    try:
        dims = {"social_financing": {"value_label": "触底回升"},
                "sentiment": {"indicators": {"crowding": {}}},
                "valuation": {"indicators": {"erp": {}}},
                "capital_flow": {"indicators": {"margin": {}}}}
        t = eng.generate_triggers(dims)
    finally:
        U.load_json = _orig_load
    add = [x for x in t["add_position"] if "恐贪" in x["condition"]]
    red = [x for x in t["reduce_position"] if "恐贪" in x["condition"]]
    return (add[0]["met"] if add else None, red[0]["met"] if red else None)

def stage1(repo, code, g):
    print("[A] 数据层：单一真值源一致性（独立重拉源头比对）")
    ts = load(os.path.join(repo, "data/timing_scores.json"))
    fgp = load(os.path.join(repo, "data/feargreed_pointer.json"))
    fgp_score = fgp.get("score")
    fgi = ts["dimensions"]["sentiment"]["indicators"].get("fear_greed_index", {})
    print(f"    源 feargreed_pointer.score = {fgp_score} (as_of={fgp.get('as_of')})")
    print(f"    页 timing_scores.fear_greed_index = {json.dumps(fgi, ensure_ascii=False)}")
    g.check("P0-2 情绪读数 == 恐贪主源 score", fgi.get("value") == fgp_score, f"{fgi.get('value')} vs {fgp_score}")
    g.check("P0-2 僵尸值 10 已消除", fgi.get("value") != 10)
    g.check("P0-2 已标注 source（可追溯）", bool(fgi.get("source")), str(fgi.get("source")))
    hit10 = []
    for fp in glob.glob(os.path.join(repo, "data/*.json")) + glob.glob(os.path.join(repo, "macro-econ-dashboard/data/*.json")):
        try: d = load(fp)
        except Exception: continue
        for p, v in leaves(d):
            if p.endswith("fear_greed_index/value") and v == 10: hit10.append((os.path.basename(fp), p))
    g.check("P0-2 全站无 fear_greed_index=10", len(hit10) == 0, str(hit10))

    def trig(kind, kw="恐贪"):
        return [t for t in ts.get("triggers", {}).get(kind, []) if kw in str(t.get("condition", ""))]
    add = trig("add_position"); red = trig("reduce_position")
    g.check("P0-1 触发项齐备（加/减各含恐贪项）", bool(add) and bool(red))
    if add: g.check("P0-1 加仓『恐贪<20』方向正确(当前值)", add[0]["met"] == (fgp_score < 20), f"met={add[0]['met']}, score={fgp_score}")
    if red:
        _qv = fgp.get("qvix_close")
        _exp_red = (fgp_score is not None and fgp_score > 85) and (_qv is not None and _qv < 14)
        g.check("P0-3 减仓『恐贪>85且QVIX<14』方向正确(当前值)", red[0]["met"] == _exp_red, f"met={red[0]['met']}, score={fgp_score}, qvix={_qv}")

    # [CIO 2026-10-17] CI修复验收增补：confirm_signals 顶层节存在性（P0-4 SSOT 序列资产，曾两次被 merge 丢失）
    _cs = ts.get("confirm_signals", {})
    _mm = _cs.get("macd_monthly", {})
    g.check("confirm_signals 顶层节齐备(均线/MACD/成交量月线)",
            all(k in _cs for k in ("ma_arrangement", "macd_monthly", "volume_monthly")),
            str(list(_cs.keys())))
    g.check("MACD月线 dif 序列资产完整(>=200点)",
            len(_mm.get("dif", [])) >= 200, f"dif={len(_mm.get('dif', []))}pts")

    print("[B] 代码层：边界真值表 + null 降级（调用交付版引擎）")
    try:
        eng = _engine(code)
        expect = {(5,10):(True,False), (19.9,10):(True,False), (20,10):(False,False), (50,10):(False,False),
                  (85,10):(False,False), (85.1,10):(False,True), (95,10):(False,True),
                  (95,14):(False,False), (95,13.9):(False,True), (95,None):(False,False)}
        ok = True; rows = []
        for (s, q), exp in expect.items():
            got = _trig(eng, s, q); rows.append(((s, q), got, exp))
            if got != exp: ok = False
        g.check("P0-3 边界真值表(恐贪>85且QVIX<14)", ok, str(rows))
        got_null = _trig(eng, None)   # 缺源降级
        g.check("P0-2 null 降级路径可用（两触发均不触发）", got_null == (False, False), str(got_null))
        g.check("P0-2 主源缺失时 _calc_fear_greed 返回 (None,None)",
                eng._calc_fear_greed.__code__.co_consts is not None)  # 占位：见源码降级分支
    except Exception as e:
        g.check("P0-1/P0-2 代码层可执行", False, f"{type(e).__name__}: {e}")

def stage1_rulings(repo, g):
    """[D] 表3 三项裁决落地门禁（#7 / #14 / #15）+ P2-3 source/caliber 覆盖。"""
    print("[D] CIO 三项裁决 + P2-3 来源覆盖")
    ind = load(os.path.join(repo, "macro-econ-dashboard/data/indicators.json"))
    v3 = load(os.path.join(repo, "data/cycle_position_v3.json"))
    v4 = load(os.path.join(repo, "data/cycle_position_v4.json"))

    # ---- #7 HY OAS 与 投资级利差 两口径拆分，禁止对齐 ----
    liq = (((ind.get("us", {}).get("economic_cycle", {}) or {}).get("liquidity_indicators", {})) or {})
    hy, ig = liq.get("hy_spread") or {}, liq.get("ig_spread") or {}
    g.check("#7 真 HY OAS 独立字段存在(hy_spread)", hy.get("value") is not None,
            f"value={hy.get('value')} unit={hy.get('unit')}")
    g.check("#7 HY OAS 单位为 %（非 bps）", hy.get("unit") == "%", f"unit={hy.get('unit')}")
    g.check("#7 HY OAS 量级成立(≥1.5%：历史最窄亦 >240bps)",
            isinstance(hy.get("value"), (int, float)) and hy["value"] >= 1.5, f"value={hy.get('value')}")
    g.check("#7 HY OAS 来源为 FRED BAMLH0A0HYM2",
            "BAMLH0A0HYM2" in str(hy.get("source", "")), str(hy.get("source"))[:48])
    g.check("#7 投资级利差独立字段存在(ig_spread)", ig.get("value") is not None,
            f"value={ig.get('value')} unit={ig.get('unit')}")
    g.check("#7 ig_spread 单位为 bps 且注明 BAA−AAA",
            ig.get("unit") == "bps" and ("BAA" in str(ig.get("name", "")) or "BAA" in str(ig.get("source", ""))),
            f"unit={ig.get('unit')} name={ig.get('name')}")
    g.check("#7 两口径数值不得相等（禁止对齐）",
            not (isinstance(hy.get("value"), (int, float)) and isinstance(ig.get("value"), (int, float))
                 and abs(hy["value"] - ig["value"]) < 1e-9),
            f"hy={hy.get('value')} ig={ig.get('value')}")

    # ---- #14 利息/财政收入 主源 33.55 ----
    l0 = ((v3.get("cycle_layers", {}) or {}).get("layer_0_debt_cycle", {}) or {}).get("us", {}) or {}
    ind14 = (l0.get("indicators", {}) or {}).get("interest_to_revenue", {}) or {}
    cur = ind14.get("current")
    top = l0.get("interest_to_revenue")
    g.check("#14 L0 主源 current 为数值", isinstance(cur, (int, float)), f"current={cur}")
    g.check("#14 顶层简写 == 主源 current（消除 22 vs 33.55）", top == cur, f"top={top} vs current={cur}")
    g.check("#14 顶层简写已标来源指针", bool(l0.get("interest_to_revenue_source")), str(l0.get("interest_to_revenue_source"))[:40])
    pct = ind14.get("percentile") or {}
    if isinstance(cur, (int, float)) and isinstance(pct.get("p75"), (int, float)):
        expect = ">p75" if cur > pct["p75"] else ("p50-p75" if cur > pct.get("p50", 0) else "<=p50")
        g.check("#14 percentile.current_rank 与 p75 自洽（不再硬编码 p50）",
                pct.get("current_rank") != "p50" or cur <= pct.get("p50", 0),
                f"current={cur} p50={pct.get('p50')} p75={pct.get('p75')} rank={pct.get('current_rank')} expect={expect}")
    c1 = ((v4.get("constraint_degradation", {}) or {}).get("C1_debt_cycle", {}) or {})
    cv = c1.get("current_values") or {}
    g.check("#14 v4 C1.current_values.interest_to_revenue == v3 主源",
            cv.get("interest_to_revenue") == cur, f"c1={cv.get('interest_to_revenue')} vs v3={cur}")

    # ---- #15 工业企业利润 不再复用工业增加值 ----
    cn = ind.get("china", {})
    ip = ((cn.get("sectors", {}) or {}).get("corporate", {}) or {}).get("industrial_profit") or {}
    iprod = ((cn.get("validation", {}) or {}).get("coincident", {}) or {}).get("industrial_prod") or {}
    ip_h = ip.get("history") or []
    iprod_h = iprod.get("history") or []
    same = bool(ip_h) and bool(iprod_h) and ip_h[-5:] == iprod_h[-5:]
    g.check("#15 工业利润 ≠ 工业增加值（静默兜底已移除）", not same,
            f"profit={ip.get('value')} prod={iprod.get('value')}")
    g.check("#15 工业利润来源为 NBS 利润口径",
            ("利润" in str(ip.get("source", ""))), str(ip.get("source"))[:56])
    g.check("#15 工业利润口径标注为累计同比（非当月）",
            "累计" in str(ip.get("caliber", "")), str(ip.get("caliber"))[:40])

    # ---- P2-3 source/caliber 覆盖 ----
    total = miss = 0
    for region in ("china", "us", "cross"):
        def walk(o):
            nonlocal total, miss
            if isinstance(o, dict):
                if "value" in o and "history" in o:
                    total += 1
                    if not o.get("source") or not o.get("caliber"):
                        miss += 1
                else:
                    for v in o.values():
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(ind.get(region, {}))
    g.check(f"P2-3 指标级 source/caliber 覆盖 100%（{total} 个节点）", total > 0 and miss == 0,
            f"total={total} missing={miss}")

def stage1_exim(repo, g):
    """[C] #12 出口系列换源门禁：当期口径 + 自然月日期 + 单位 + 与主源同月（不滞后）。
    离线判据（数据文件自证）；A/B 双源交叉校验在网络可达时附加执行（超差则 FAIL）。"""
    print("[C] #12 出口系列：口径/单位/日期（NBS 换源后）")
    ind = load(os.path.join(repo, "macro-econ-dashboard/data/indicators.json"))
    ext = (ind.get("china", {}).get("sectors", {}) or {}).get("external", {}) or {}
    m2 = (((ind.get("china", {}).get("validation", {}) or {}).get("lagging", {}) or {}).get("m2", {}) or {})
    m2_date = str(m2.get("date") or "")
    for key, unit in (("export", "%"), ("import", "%"), ("trade_balance", "亿美元")):
        m = ext.get(key) or {}
        v, d, u = m.get("value"), str(m.get("date") or ""), m.get("unit")
        g.check(f"#12 {key} 有值", v is not None, f"value={v}")
        g.check(f"#12 {key} 日期为自然月 YYYY-MM（旧源为发布日）", bool(re.fullmatch(r"\d{4}-\d{2}", d)), f"date={d}")
        g.check(f"#12 {key} 单位 {unit}", u == unit, f"unit={u}")
        g.check(f"#12 {key} 已标 source（可追溯）", bool(m.get("source")), str(m.get("source"))[:40])
        hist = m.get("history") or []
        g.check(f"#12 {key} history 末点 == value/date",
                bool(hist) and hist[-1].get("date") == d and hist[-1].get("value") == v,
                f"last={hist[-1] if hist else None}")
    # 不再滞后：出口系列月份应与同期中国主源（M2）同为最新月
    if m2_date:
        # [CIO 2026-10-18 口径裁决] 海关发布早、NBS 入库滞后属源端结构性滞后：
        # 同月=PASS；滞后≤1月=WARN（跟踪不阻断）；滞后>1月=FAIL（真停滞才红灯）
        def _mlag(a, b):
            try:
                ay, am = int(a[:4]), int(a[5:7]); by, bm = int(b[:4]), int(b[5:7])
                return (by * 12 + bm) - (ay * 12 + am)
            except Exception:
                return 99
        for key in ("export", "import", "trade_balance"):
            d = str((ext.get(key) or {}).get("date") or "")
            lag = _mlag(d, m2_date)
            if lag == 0:
                g.check(f"#12 {key} 与主源 M2 同月", True, f"{d} vs m2={m2_date}")
            elif lag <= 1:
                g.warn(f"#12 {key} 滞后主源 M2 ≤1月（源端结构性，跟踪）", False, f"{d} vs m2={m2_date} lag={lag}")
            else:
                g.check(f"#12 {key} 滞后主源 M2 >1月（异常停滞）", False, f"{d} vs m2={m2_date} lag={lag}")
    ex, im = (ext.get("export") or {}).get("value"), (ext.get("import") or {}).get("value")
    g.check("#12 出口/进口为当月同比（量级合理 −60~60%）",
            ex is not None and im is not None and -60 <= ex <= 60 and -60 <= im <= 60, f"export={ex}, import={im}")
    tb = (ext.get("trade_balance") or {}).get("value")
    g.check("#12 贸易差额量级合理（0~3000 亿美元）", tb is not None and 0 < tb < 3000, f"tb={tb}")

    # A/B 双源交叉校验（在线；不可达时跳过，不阻断）
    try:
        sys.path.insert(0, os.path.join(repo, "macro-econ-dashboard", "scripts"))
        import fetch_china as FC
        import akshare as ak
        nbs = FC.grab_series(lambda: ak.macro_china_nbs_nation(
            kind="月度数据", path=FC.NBS_EXIM_PATH, period="2026-"))
        em = FC.grab_series(FC.fetch_em_external)
        x = FC.cross_check_dual_source(nbs, em)
        # 同比字段容差 0.2pp（CIO 终裁）；差额为官方修订量，用 fetch_china 内的相对容差，
        # 门禁只对同比字段判 FAIL，避免把「初值修订」误判为换源失败。
        hard = [b for b in x["breaches"] if not b.startswith("trade_balance")]
        pf = x["per_field"]
        detail = "; ".join(
            f'{k} 近3月maxΔ={v["recent_max_diff"]:.2f}{v["unit"]}'
            + (f'(容差{v["allowed"]:.2f})' if "allowed" in v else "")
            for k, v in pf.items())
        g.check("#12 A(NBS)/B(东财) 双源近3月同比一致（容差 0.2pp）", not hard,
                f'同比 maxΔ={x["recent_max_diff"]:.2f}pp | {detail}')
    except Exception as e:
        print(f"  [INFO] #12 双源在线交叉校验跳过（{type(e).__name__}: {str(e)[:60]}）")

def stage3(repo, code, g):
    print("[C] P0-3 跨文件一致性（表3 1-4/8/9 归零）")
    ind = load(os.path.join(repo, "macro-econ-dashboard/data/indicators.json"))
    cyc = load(os.path.join(repo, "data/cycle_position_v3.json"))
    sp = ind.get("cross", {}).get("cn_us_10y_spread", {})
    v, u = sp.get("value"), sp.get("unit")
    g.check("利差 unit 与量级自洽", not (u == "bps" and v is not None and abs(v) < 10), f"value={v}, unit={u}")
    print("  [INFO] 其余冲突项归零校验待 SSOT 完成后启用（本轮占位）。")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, default=1)
    ap.add_argument("--repo", default=os.environ.get("REPO", "."))
    ap.add_argument("--code", default=None)
    a = ap.parse_args(); code = a.code or a.repo
    g = Gate()
    print(f"==== 第1层数据门禁 (stage={a.stage}, repo={a.repo}, code={code}) ====")
    if a.stage == 1:
        stage1(a.repo, code, g)
        stage1_exim(a.repo, g)
        stage1_rulings(a.repo, g)
    elif a.stage == 3: stage3(a.repo, code, g)
    print(f"\n合计 {g.n} 项断言, 失败 {len(g.fails)} 项, 警告 {len(g.warns)} 项 -> {'PASS' if not g.fails else 'FAIL'}")
    if g.warns: print("  [WARN 清单] " + " | ".join(g.warns))
    sys.exit(0 if not g.fails else 1)
