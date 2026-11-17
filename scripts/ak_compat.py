#!/usr/bin/env python3
"""ak_compat.py — AKShare 接口改名兼容层（2026-11-17）

背景（update-data 2026-11-15~17 连续失败根因）：
AKShare 1.20.41~1.20.48 分批**删除**旧接口名（旧名不再保留，调用即 AttributeError），
CI 里 `pip install akshare` 不带版本，每次跑都自动升到最新，于是静默炸掉：
  bond_zh_us_rate            -> bond_zh_us_yield_em         (1.20.41)
  macro_china_fx_gold        -> macro_china_fx_gold_em      (1.20.42)
  macro_china_money_supply   -> macro_china_money_supply_em (1.20.42)
  macro_china_hgjck          -> macro_china_customs_trade   (1.20.42)
  stock_zh_index_daily       -> index_zh_daily_sina         (1.20.44)
  stock_zh_index_daily_em    -> index_zh_daily_em           (1.20.44)
  stock_zh_index_daily_tx    -> index_zh_daily_tx           (1.20.44)
  stock_zh_index_hist_csindex-> index_hist_csindex          (1.20.44)
  macro_china_caijin_pmi     -> macro_china_pmi_manufacturing_cx（列名同时变化，见 fetch_china.py）

已彻底移除、无替代（调用方须自行降级，本层不伪造）：
  macro_usa_ism_pmi 等 84 个金十源宏观接口（金十数据中心 2025-09 起停更）
  → ISM 走 scripts/fetch_ism_pmi.py（PR Newswire 官方稿镜像），其余走 FRED/继承。

做法：导入本模块时，把旧名以属性方式指到新实现（仅当旧名缺失、新名存在）。
旧版本 akshare 下 hasattr(old) 为真 → 跳过，不干扰。
"""
import akshare as ak

ALIASES = {
    "bond_zh_us_rate": "bond_zh_us_yield_em",
    "macro_china_fx_gold": "macro_china_fx_gold_em",
    "macro_china_money_supply": "macro_china_money_supply_em",
    "macro_china_hgjck": "macro_china_customs_trade",
    "stock_zh_index_daily": "index_zh_daily_sina",
    "stock_zh_index_daily_em": "index_zh_daily_em",
    "stock_zh_index_daily_tx": "index_zh_daily_tx",
    "stock_zh_index_hist_csindex": "index_hist_csindex",
    "macro_china_caijin_pmi": "macro_china_pmi_manufacturing_cx",
}


def apply_aliases(verbose=False):
    """给 akshare 补旧名别名，返回实际生效的映射列表。"""
    applied = []
    for old, new in ALIASES.items():
        if hasattr(ak, old):
            continue
        fn = getattr(ak, new, None)
        if fn is None:
            continue
        setattr(ak, old, fn)
        applied.append(f"{old} -> {new}")
    if verbose and applied:
        print(f"[ak_compat] 已补 {len(applied)} 个旧接口名别名: {', '.join(applied)}")
    return applied


APPLIED = apply_aliases()


def missing(verbose=True):
    """返回当前 akshare 版本下仍缺失的旧名（无别名可补），供健康检查用。"""
    gone = [old for old in ALIASES if not hasattr(ak, old)]
    if verbose and gone:
        print(f"[ak_compat] 无替代的旧接口: {gone}")
    return gone
