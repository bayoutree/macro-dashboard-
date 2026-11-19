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
  currency_boc_safe          -> fx_central_parity_safe      (1.20.42，外汇类前缀统一为 fx_)
  macro_china_czsr           -> macro_china_fiscal_revenue   (1.20.42)
  macro_china_gdzctz         -> macro_china_fixed_asset_investment (1.20.42)
  macro_china_gyzjz          -> macro_china_industrial_value_added (1.20.42)
  macro_china_shrzgm         -> macro_china_social_financing  (1.20.42)
  macro_china_shibor_all     -> interest_rate_shibor_js       (1.20.48，二次改名)

已彻底移除、无替代（调用方须自行降级，本层不伪造）：
  macro_usa_ism_pmi 等 84 个金十源宏观接口（金十数据中心 2025-09 起停更）
  → ISM 走 scripts/fetch_ism_pmi.py（PR Newswire 官方稿镜像），其余走 FRED/继承。
  macro_china_cx_pmi_yearly / macro_china_industrial_production_yoy / macro_china_m2_yearly（1.20.42 金十源移除）
  → 用 ADAPTERS 转成旧列口径（日期/今值），保证调用方零改动。

2026-11-19 补丁：1.20.42 第二批改名漏网（18 个旧名中 9 个未覆盖）导致
  macro-econ-dashboard「Update Macro Econ Dashboard」在 `grab(ak.macro_china_shrzgm)` 直接
  AttributeError 崩掉；本次一并补齐别名 + 两个「移除但有替代源」的列口径适配。

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
    "currency_boc_safe": "fx_central_parity_safe",
    "macro_china_czsr": "macro_china_fiscal_revenue",
    "macro_china_gdzctz": "macro_china_fixed_asset_investment",
    "macro_china_gyzjz": "macro_china_industrial_value_added",
    "macro_china_shrzgm": "macro_china_social_financing",
    "macro_china_shibor_all": "interest_rate_shibor_js",
}

# 「旧名已移除、但新接口有等价数据」的列口径适配：产出旧列名（日期/今值），调用方零改动。
ADAPTERS = {
    # 金十 macro_china_industrial_production_yoy（日期=发布日, 今值）
    # -> 统计局工业增加值（月份=数据月, 同比增长, 发布时间）
    "macro_china_industrial_production_yoy": (
        "macro_china_industrial_value_added",
        {"发布时间": "日期", "同比增长": "今值"},
    ),
    # 金十 macro_china_cx_pmi_yearly（日期=发布日, 今值）
    # -> 财新制造业 PMI（日期=月末, 制造业PMI）
    "macro_china_cx_pmi_yearly": (
        "macro_china_pmi_manufacturing_cx",
        {"制造业PMI": "今值"},
    ),
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


def apply_adapters(verbose=False):
    """为「已移除但有等价源」的旧接口装适配器（旧列口径输出）。"""
    applied = []
    for old, (new, rename) in ADAPTERS.items():
        if hasattr(ak, old):
            continue
        fn = getattr(ak, new, None)
        if fn is None:
            continue

        def _wrap(fn=fn, rename=rename):
            df = fn()
            try:
                df = df.rename(columns=rename)
            except Exception:
                pass
            return df

        _wrap.__name__ = old
        setattr(ak, old, _wrap)
        applied.append(f"{old} ~> {new}（列适配）")
    if verbose and applied:
        print(f"[ak_compat] 已装 {len(applied)} 个列口径适配器: {', '.join(applied)}")
    return applied


APPLIED = apply_aliases()
ADAPTED = apply_adapters()


def missing(verbose=True):
    """返回当前 akshare 版本下仍缺失的旧名（无别名可补），供健康检查用。"""
    gone = [old for old in ALIASES if not hasattr(ak, old)]
    if verbose and gone:
        print(f"[ak_compat] 无替代的旧接口: {gone}")
    return gone
