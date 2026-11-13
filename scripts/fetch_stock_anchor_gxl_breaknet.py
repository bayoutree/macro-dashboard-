#!/usr/bin/env python3
"""
fetch_stock_anchor_gxl_breaknet.py
==================================
股市锚点卡 —— 股息率利差 / 破净率 真源续采（2026-11-13 新建）。

背景（2026-11-13 主人令）：
  data/all_a_gxl_spread.json、data/break_net_asset_rate.json 两个文件停在
  2026-09-11（63 天），且全仓 grep 不到任何脚本写它们 —— 采集链在 9 月
  重构时被摘掉后未补回，文件成了孤儿数据。这两个指标直接支撑「股债性价比」
  判断，属于结构性判断的输入，必须恢复采集。

指标与口径（均已用 2026-09-11 存量值反向验证一致）：
  1. 股息率利差 = 上证A股股息率 − 中国10年期国债收益率
     - 上证A股股息率：akshare stock_a_gxl_lg(symbol='上证A股')
       （乐咕乐股 legulegu.com/stockdata/guxilv）
       验证：2026-09-11 = 2.60，与存量 gxl 字段完全一致
     - 10Y 国债：akshare bond_zh_us_rate()['中国国债收益率10年']
       （中债国债收益率曲线，经乐咕中转）
       验证：2026-09-11 = 1.6899，与存量 bond10y 字段完全一致
  2. 破净率 = 全A PB<1 个股数 / 全A个股总数
     - akshare stock_a_below_net_asset_statistics(symbol='全部A股')
       验证：2026-09-11 存量 8.3%（433/5219）与接口同日值一致

⚠ 口径修正说明（2026-11-13 查实）：
  文件名与历史 formula 写的是「全A股息率」，但实际取的是**上证A股**股息率
  （legulegu 该接口只提供 上证A股/深证A股/创业板/科创板，无「全部A股」口径）。
  本次不改文件名（避免打断前端引用），但 source/caliber 如实标注为上证A股，
  并置 caliber_corrected=True，供前端名称后续更正。

规则：
  - 真源覆盖的日期以真源为准，真源缺失的日期保留存量值（并集，不丢历史）；
  - value 一律由 gxl/bond10y 重算，不沿用存量 value；
  - 打 update_time，供 freshness_gate 判定。

用法:
  python fetch_stock_anchor_gxl_breaknet.py            # 拉取并写入
  python fetch_stock_anchor_gxl_breaknet.py --dry-run  # 仅打印不写入
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("anchor_gxl_breaknet")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
SPREAD_PATH = DATA_DIR / "all_a_gxl_spread.json"
BREAKNET_PATH = DATA_DIR / "break_net_asset_rate.json"


def _load(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("%s 读取失败: %s", path.name, e)
    return {}


def fetch_gxl():
    """上证A股股息率 {date: value}。"""
    import akshare as ak
    df = ak.stock_a_gxl_lg(symbol="上证A股")
    out = {}
    for _, r in df.iterrows():
        d = str(r["日期"])[:10]
        v = r["股息率"]
        if v is not None and v == v:  # 非 NaN
            out[d] = round(float(v), 4)
    logger.info("上证A股股息率: %d 点, %s ~ %s", len(out), min(out), max(out))
    return out


def fetch_bond10y():
    """中国10年期国债收益率 {date: value}。"""
    import akshare as ak
    df = ak.bond_zh_us_rate(start_date="20060101")
    out = {}
    for _, r in df.iterrows():
        d = str(r["日期"])[:10]
        v = r.get("中国国债收益率10年")
        if v is not None and v == v:
            out[d] = round(float(v), 4)
    logger.info("中国10Y国债: %d 点, %s ~ %s", len(out), min(out), max(out))
    return out


def fetch_breaknet():
    """全A破净率 {date: (ratio%, below, total)}。"""
    import akshare as ak
    df = ak.stock_a_below_net_asset_statistics(symbol="全部A股")
    out = {}
    for _, r in df.iterrows():
        d = str(r["date"])[:10]
        ratio = r.get("below_net_asset_ratio")
        if ratio is None or ratio != ratio:
            continue
        below = r.get("below_net_asset")
        total = r.get("total_company")
        out[d] = (
            round(float(ratio) * 100, 2),
            int(below) if below == below and below is not None else None,
            int(total) if total == total and total is not None else None,
        )
    logger.info("破净率: %d 点, %s ~ %s", len(out), min(out), max(out))
    return out


def build_spread(gxl, bond10y, old):
    """合并：真源优先，存量补缺；value 全部重算。"""
    pts = {}
    # 先铺存量（含旧字段，便于追溯）
    for x in (old.get("history") or []):
        d = str(x.get("date") or "")[:10]
        if d:
            pts[d] = dict(x)
    new_n = 0
    for d, g in gxl.items():
        b = bond10y.get(d)
        if b is None:
            continue
        pts[d] = {"date": d, "gxl": g, "bond10y": b,
                  "value": round(g - b, 4)}
        new_n += 1
    hist = [pts[d] for d in sorted(pts)]
    # value 一律重算，防存量脏值
    for x in hist:
        if x.get("gxl") is not None and x.get("bond10y") is not None:
            x["value"] = round(float(x["gxl"]) - float(x["bond10y"]), 4)
    logger.info("股息率利差: 真源覆盖 %d 日, 合并后 %d 点, 尾点 %s",
                new_n, len(hist), hist[-1] if hist else None)
    return hist


def build_breaknet(bn, old):
    pts = {}
    for x in (old.get("history") or []):
        d = str(x.get("date") or "")[:10]
        if d:
            pts[d] = dict(x)
    for d, (ratio, below, total) in bn.items():
        pts[d] = {"date": d, "value": ratio, "below": below, "total": total}
    hist = [pts[d] for d in sorted(pts)]
    logger.info("破净率: 真源覆盖 %d 日, 合并后 %d 点, 尾点 %s",
                len(bn), len(hist), hist[-1] if hist else None)
    return hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    gxl = fetch_gxl()
    bond = fetch_bond10y()
    bn = fetch_breaknet()

    old_sp = _load(SPREAD_PATH)
    old_bn = _load(BREAKNET_PATH)

    sp_hist = build_spread(gxl, bond, old_sp)
    bn_hist = build_breaknet(bn, old_bn)

    if args.dry_run:
        logger.info("--dry-run，不写入")
        return

    spread = {
        "update_date": sp_hist[-1]["date"] if sp_hist else None,
        "update_time": now,
        "source": "akshare stock_a_gxl_lg(上证A股) + bond_zh_us_rate(中国国债收益率10年)",
        "formula": "上证A股股息率 − 中国10Y国债收益率",
        "unit": "%",
        "caliber": "上证A股股息率（非全A）− 中债10年期国债到期收益率；日频",
        "caliber_corrected": True,
        "caliber_note": (
            "历史文件名与 formula 写作「全A股息率」，但 legulegu 该接口仅提供"
            "上证A股/深证A股/创业板/科创板四个口径，无「全部A股」。经 2026-09-11"
            "存量值反验（2.60 vs 接口同日 2.60）确认实际取的是上证A股。"
            "2026-11-13 起如实标注，线上名称应同步改为「上证A股股息率」。"),
        "history": sp_hist,
    }
    breaknet = {
        "update_date": bn_hist[-1]["date"] if bn_hist else None,
        "update_time": now,
        "source": "akshare stock_a_below_net_asset_statistics(全部A股)",
        "formula": "below_net_asset / total_company",
        "unit": "%",
        "caliber": "全A（沪深全部上市A股）市净率<1 的个股数 / 全部A股个股数；日频",
        "history": bn_hist,
    }

    SPREAD_PATH.write_text(json.dumps(spread, ensure_ascii=False, indent=2), encoding="utf-8")
    BREAKNET_PATH.write_text(json.dumps(breaknet, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已写入 %s (%.1f KB) / %s (%.1f KB)",
                SPREAD_PATH.name, SPREAD_PATH.stat().st_size / 1024,
                BREAKNET_PATH.name, BREAKNET_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
