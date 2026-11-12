#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
乐咕（legulegu）依赖面探针 + 替代源可行性验证 —— 裁决④ 配套工具

背景（2026-11-12 CIO 裁决④）：legulegu 两项（all_a_gxl_spread / break_net_asset_rate）
11-26 前换源，换不到下线。核查发现这两项其实是**孤儿文件**（无采集器、页面无卡位），
真正需要换源的是 akshare 对 legulegu 的封装调用：

  ak.stock_index_pe_lg(symbol="沪深300" / "中证800")   → 沪深300 PE分位、中证800 ERP ★主指标
  ak.stock_index_pb_lg(symbol="沪深300")               → 沪深300 PB分位

本探针做三件事，全部只读、不写 data/：
  1. 现状：逐条调用上述 akshare 接口，记录是否可用、返回行数、数据末点日期
  2. 替代源：中证指数官方估值文件（csindex indicator.xls，000300 / 000985 / 000905），
     记录可达性、字段、末点日期 —— 用于判断能否替代 PE 分位 / 股息率链
  3. 结论：逐项给出 可用 / 不可用 / 需人工判断，并落 JSON 证据

用法：
  python3 tools/healthcheck/probe_legulegu_alt.py                    # 全量
  python3 tools/healthcheck/probe_legulegu_alt.py --out xxx.json     # 指定输出
  python3 tools/healthcheck/probe_legulegu_alt.py --skip-akshare     # 只探替代源（无 akshare 环境）

退出码：始终 0（探针不是门禁），结论看 JSON / stdout 表格。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CSINDEX_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
    "autofile/indicator/{symbol}indicator.xls"
)
CSINDEX_SYMBOLS = ["000300", "000985", "000905"]

AKSHARE_TARGETS = [
    ("stock_index_pe_lg", {"symbol": "沪深300"}, "沪深300 PE 分位 / ERP 链"),
    ("stock_index_pb_lg", {"symbol": "沪深300"}, "沪深300 PB 分位（参考卡）"),
    ("stock_index_pe_lg", {"symbol": "中证800"}, "中证800 ERP ★主指标"),
]


def _last_date(series_like) -> str | None:
    """从 DataFrame / Series 里取最大日期，容忍字符串与 Timestamp。"""
    try:
        import pandas as pd  # noqa: F401

        s = series_like
        if hasattr(s, "columns"):
            for col in ("日期", "date"):
                if col in s.columns:
                    s = s[col]
                    break
            else:
                return None
        vals = [v for v in list(s) if v is not None]
        if not vals:
            return None
        parsed = []
        for v in vals:
            try:
                parsed.append(dt.datetime.fromisoformat(str(v)[:19]))
            except Exception:
                continue
        if not parsed:
            return None
        return max(parsed).date().isoformat()
    except Exception:
        return None


def probe_akshare() -> list[dict]:
    out = []
    try:
        import akshare as ak
    except Exception as e:  # pragma: no cover
        return [
            {
                "target": f"{fn}({kw})",
                "purpose": purpose,
                "status": "SKIP",
                "detail": f"akshare 不可用：{type(e).__name__}: {e}",
            }
            for fn, kw, purpose in AKSHARE_TARGETS
        ]

    for fn, kw, purpose in AKSHARE_TARGETS:
        name = f"{fn}({', '.join(f'{k}={v}' for k, v in kw.items())})"
        rec = {"target": name, "purpose": purpose}
        try:
            func = getattr(ak, fn)
            df = func(**kw)
            if df is None or len(df) == 0:
                rec.update(status="FAIL", detail="返回空数据")
            else:
                rec.update(
                    status="PASS",
                    rows=int(len(df)),
                    columns=[str(c) for c in list(df.columns)][:12],
                    last_date=_last_date(df),
                )
        except Exception as e:
            rec.update(status="FAIL", detail=f"{type(e).__name__}: {str(e)[:200]}")
        out.append(rec)
    return out


def probe_csindex() -> list[dict]:
    out = []
    try:
        import pandas as pd
        import requests
    except Exception as e:  # pragma: no cover
        return [
            {
                "target": f"csindex:{s}",
                "status": "SKIP",
                "detail": f"pandas/requests 不可用：{type(e).__name__}: {e}",
            }
            for s in CSINDEX_SYMBOLS
        ]

    headers = {"User-Agent": "Mozilla/5.0 (compatible; macro-dashboard-probe/1.0)"}
    for sym in CSINDEX_SYMBOLS:
        url = CSINDEX_URL.format(symbol=sym)
        rec = {"target": f"csindex:{sym}", "url": url}
        try:
            r = requests.get(url, headers=headers, timeout=40)
            rec["http"] = r.status_code
            rec["bytes"] = len(r.content)
            if r.status_code != 200 or not r.content:
                rec.update(status="FAIL", detail="HTTP 非 200 或空响应")
                out.append(rec)
                continue
            from io import BytesIO

            df = pd.read_excel(BytesIO(r.content))
            cols = [str(c) for c in list(df.columns)]
            rec.update(
                status="PASS" if len(df) else "FAIL",
                rows=int(len(df)),
                columns=cols[:12],
                last_date=_last_date(df),
                has_pb=any("市净率" in c for c in cols),
                has_dividend=any("股息率" in c for c in cols),
                detail=None if len(df) else "文件可读但无数据行",
            )
        except Exception as e:
            rec.update(status="FAIL", detail=f"{type(e).__name__}: {str(e)[:200]}")
        out.append(rec)
    return out


def verdict(ak_res: list[dict], cs_res: list[dict]) -> list[str]:
    lines = []
    ak_ok = [r for r in ak_res if r.get("status") == "PASS"]
    ak_bad = [r for r in ak_res if r.get("status") == "FAIL"]
    if ak_bad:
        lines.append(
            f"❌ legulegu 封装接口 {len(ak_bad)}/{len(ak_res)} 条不可用 → "
            "PE/PB 分位与中证800 ERP 主指标存在静默保留旧值风险，须换源"
        )
    elif ak_res:
        lines.append(f"✅ legulegu 封装接口 {len(ak_ok)}/{len(ak_res)} 条可用（当日可拉取）")

    cs_ok = [r for r in cs_res if r.get("status") == "PASS"]
    if cs_ok:
        has_div = [r for r in cs_ok if r.get("has_dividend")]
        has_pb = [r for r in cs_ok if r.get("has_pb")]
        lines.append(
            f"中证指数官方文件可达 {len(cs_ok)}/{len(cs_res)} 个代码；"
            f"含股息率字段 {len(has_div)} 个、含市净率字段 {len(has_pb)} 个"
        )
        if has_div and not has_pb:
            lines.append(
                "→ 可替代「股息率」类链（如全A股息率-10Y利差）；"
                "PB 分位无官方字段，需另找源或用东财 PB 自算（同破净率做法）"
            )
    else:
        lines.append("⚠ 中证指数官方文件本次全部不可达，换源需另评估")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="JSON 证据输出路径")
    ap.add_argument("--skip-akshare", action="store_true", help="跳过 akshare 探测")
    ap.add_argument("--eval-date", default=None)
    args = ap.parse_args()

    eval_date = args.eval_date or dt.date.today().isoformat()
    ak_res = [] if args.skip_akshare else probe_akshare()
    cs_res = probe_csindex()
    lines = verdict(ak_res, cs_res)

    print(f"=== 乐咕依赖面探针 {eval_date} ===")
    for r in ak_res:
        print(f"[{r.get('status'):4}] {r['target']} → {r.get('detail') or r.get('last_date')}")
    for r in cs_res:
        print(
            f"[{r.get('status'):4}] {r['target']} http={r.get('http')} "
            f"rows={r.get('rows')} last={r.get('last_date')} "
            f"pb={r.get('has_pb')} div={r.get('has_dividend')} → {r.get('detail') or ''}"
        )
    print("--- 结论 ---")
    for ln in lines:
        print(ln)

    payload = {
        "eval_date": eval_date,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "akshare": ak_res,
        "csindex": cs_res,
        "verdict": lines,
    }
    out = Path(args.out) if args.out else (
        ROOT / "tools" / "healthcheck" / "output" / f"legulegu_probe_{eval_date}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据已写入 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
