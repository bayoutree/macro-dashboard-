#!/usr/bin/env python3
"""
宏观看板 - 统一数据更新脚本
============================
核心功能：
  1. 计算 timing_scores.json (六维择时评分) —— 最核心、最复杂
  2. 编排所有其他数据文件的更新
  3. 错误隔离：单个数据源失败不影响整体

数据源:
  - AKShare: A股指数、PE/PB、国债收益率、宏观指标、资金流向
  - FRED API: 美国宏观数据
  - yfinance: 全球资产价格

用法:
  python update_data.py              # 完整更新
  python update_data.py --dry-run    # 仅检查接口连通性，不写文件
  python update_data.py --timing     # 仅更新 timing_scores.json
"""

import sys
import os
import time
import math
import argparse
import subprocess
import traceback

# [CI加固 2026-10-18] runner→中文数据源时段性挂死（Codex 3 连 dispatch 复现，
# 均卡在 Run data update 步骤 >5min 无输出；定时跑历史正常→疑似周末维护窗口
# 特定源 TCP 半开无响应）。akshare 内部 requests 多数不显式传 timeout 会无限等待。
# 全局 socket 默认超时覆盖所有未显式超时的网络调用（显式传 timeout 的不受影响）；
# 单路挂死由"无限等待"变为 ≤N 秒抛 socket.timeout → safe_ak_call 捕获→该路降级。
# 口径=单次 socket 阻塞操作无响应时长（非整体下载限时），慢速大数据下载不受影响。
import socket as _socket
_socket.setdefaulttimeout(int(os.environ.get("AK_NET_TIMEOUT_SECONDS", "60")))
import signal as _signal
import threading as _threading
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

# ============================================================
# 路径 & 配置
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(SCRIPT_DIR))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("update_data")

# ============================================================
# 通用工具
# ============================================================

def today_str() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_float(val, default=None):
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else round(f, 4)
    except (ValueError, TypeError):
        return default


# 关键 history 指标：保存前断言不得为空（空则中止，防止提交把真历史抹掉）
# 2026-09-27 股票看板 16 项 history 被抹事故后新增
_CRITICAL_HISTORY_INDICATORS = {
    ("capital_flow", "equity_fund_position"),
    ("capital_flow", "flexible_fund_position"),
    ("capital_flow", "margin_ratio"),
    ("capital_flow", "new_fund"),
    ("capital_flow", "industrial_capital"),
    ("capital_flow", "fiscal_expenditure"),
    ("sentiment", "fund_3y_annual"),
    ("sentiment", "new_account_opening"),
    ("sentiment", "margin_buying_ratio"),
    ("micro_structure", "industry_concentration"),
    ("micro_structure", "concentration_trend"),
    ("micro_structure", "csi1000_hs300_ratio"),
    ("liquidity", "fed_policy"),
    ("liquidity", "interest_rate"),
    ("valuation", "hs300_pe_percentile"),
    ("equity_bond", "cn_us_spread"),
}


def assert_critical_histories(data: dict):
    """关键 history 被清空时直接抛错，中止本次写入与后续提交。"""
    missing = []
    for dim_key, dim in data.get("dimensions", {}).items():
        for ind_key, ind in dim.get("indicators", {}).items():
            if (dim_key, ind_key) in _CRITICAL_HISTORY_INDICATORS:
                h = ind.get("history")
                if not isinstance(h, list) or len(h) < 2:
                    missing.append(f"{dim_key}.{ind_key}")
    if missing:
        raise RuntimeError(
            "关键 history 指标为空，已中止保存，防止真历史被抹: "
            + ", ".join(missing)
        )


def save_json(data: dict, filename: str):
    import json
    # 对 timing_scores.json 合并历史数据和说明字段
    if filename == "timing_scores.json":
        data = merge_history_description(data)
        assert_critical_histories(data)
    filepath = DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    size_kb = filepath.stat().st_size / 1024
    logger.info(f"  💾 已保存: {filepath.name} ({size_kb:.1f} KB)")


# 已验证为真实数据源回填的指标白名单（sync_timing_scores_history.py 写入）
# 这些指标的 history 允许在每日重建时继承；未列入的指标 history 一律不继承
# （v3.4.5 整改：防止 _legacy_FAKE 假序列复活；2026-09-19 改为白名单继承）
_VERIFIED_HISTORY_INDICATORS = {
    ("liquidity", "social_financing_trend"),
    ("liquidity", "m1_m2_scissors"),
    ("valuation", "hs300_pb_percentile"),
    ("valuation", "buffett_ratio"),
    ("valuation", "break_net_rate"),
    ("equity_bond", "dividend_bond_spread_hs300"),
    ("equity_bond", "dividend_bond_spread_red"),
    ("equity_bond", "hs300_erp"),
}

# [CI修复 2026-10-17] 由外部脚本累积历史的指标，即使无 _history_meta 也继承旧 history
# 防止数据源断连时静默丢失真历史（akshare 东财接口在海外 runner 被断连）
_EXTERNAL_HISTORY_INDICATORS = {
    ("sentiment", "turnover_rate"),        # 60点日频 history
    ("micro_structure", "crowding_ratio"), # collect_microstructure.py 周频累积
}


def merge_history_description(new_data: dict) -> dict:
    """合并旧 JSON 的 description 字段与已验证真实 history。

    [v3.4.5 数据真实性整改] 禁止从旧 JSON 盲目继承 history（旧 history 系
    _legacy_FAKE_add_history_data.py 生成的随机数假序列）。
    [2026-09-19 修复 pipeline 清空根因] 对带 _history_meta.source 标记的
    真实采集 history 予以继承，避免每日 update_data.py 重建
    timing_scores.json 时反复清空人工/脚本回补的真历史。
    [2026-09-27 修复股票看板清空事故] 白名单机制过窄，导致 capital_flow /
    micro_structure 等 16 项真 history 在 9/24 被抹。_history_meta.source
    本身就是真源凭证，继承判断改为只认该标记、不再受白名单限制；
    无 _history_meta 的 history（如 _legacy_FAKE 假序列）仍然不继承。
    """
    import json
    try:
        filepath = DATA_DIR / "timing_scores.json"
        if not filepath.exists():
            return new_data
        with open(filepath, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return new_data
    if not old or "dimensions" not in old:
        return new_data
    for dim_key, dim in new_data.get("dimensions", {}).items():
        old_dim = old.get("dimensions", {}).get(dim_key, {})
        for ind_key, ind in dim.get("indicators", {}).items():
            old_ind = old_dim.get("indicators", {}).get(ind_key, {})
            if "description" in old_ind and "description" not in ind:
                ind["description"] = old_ind["description"]
            # 新数据自身无 history、旧版带真源标记且点数>=2 时继承
            # （2026-09-27: 白名单 -> 只认 _history_meta.source 真源凭证）
            # [CI修复 2026-10-17] 外部累积历史的指标也继承（即使无 _history_meta）
            _is_external = (dim_key, ind_key) in _EXTERNAL_HISTORY_INDICATORS
            _new_hist = ind.get("history") or []
            _old_hist = old_ind.get("history") or []
            _old_has_meta = (isinstance(old_ind.get("_history_meta"), dict)
                             and old_ind["_history_meta"].get("source"))
            # 条件A: 新数据无history，旧数据有真源标记 → 继承
            _cond_a = (not _new_hist and len(_old_hist) >= 1
                       and (_old_has_meta or _is_external))
            # 条件B: [CI修复] 新数据history比旧数据短，且旧数据有真源标记 → 保留旧版
            _cond_b = (len(_new_hist) < len(_old_hist) and len(_old_hist) >= 1
                       and (_old_has_meta or _is_external))
            if _cond_a or _cond_b:
                ind["history"] = old_ind["history"]
                if old_ind.get("_history_meta"):
                    ind["_history_meta"] = old_ind["_history_meta"]
                else:
                    ind["_history_meta"] = {
                        "source": f"inherited external history ({len(_old_hist)} pts)",
                        "inherited_at": "2026-10-17"
                    }
        # [CI修复 2026-10-17] 保留旧数据中存在但新数据未生成的指标
        # (如 median_pe_all_a: 由外部脚本写入，compute_all 不重生，
        #  数据源断连时 compute_all 不会产出该指标，但旧 history 必须保留)
        new_ind_keys = set(dim.get("indicators", {}).keys())
        for old_ind_key, old_ind in old_dim.get("indicators", {}).items():
            if old_ind_key not in new_ind_keys:
                old_hist = old_ind.get("history", [])
                old_meta = old_ind.get("_history_meta")
                if (isinstance(old_hist, list) and len(old_hist) >= 2
                        and isinstance(old_meta, dict) and old_meta.get("source")):
                    dim.setdefault("indicators", {})[old_ind_key] = old_ind
                    logger.info(f"    🔒 保留旧指标(新数据未生成): {dim_key}.{old_ind_key} ({len(old_hist)}点)")

    # [CI修复 2026-10-17] 顶层节继承：confirm_signals 等顶层数据由外部采集器
    # (collect_confirm_signals.py) 写入，compute_all 不生成这些节。
    # 当 runner 上采集失败时，旧节必须保留，否则每日 CI 都会丢。
    _TOP_LEVEL_PRESERVE_KEYS = {"confirm_signals"}
    for _tl_key in _TOP_LEVEL_PRESERVE_KEYS:
        if _tl_key not in new_data and _tl_key in old:
            new_data[_tl_key] = old[_tl_key]
            logger.info(f"    🔒 保留顶层节(新数据未生成): {_tl_key}")

    return new_data


def load_json(filename: str) -> dict:
    import json
    filepath = DATA_DIR / filename
    if not filepath.exists():
        logger.warning(f"  文件不存在: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# [F1 2026-10-23] 单路墙钟硬超时（秒，env 可调）——socket 不活动超时只覆盖"挂死"，
# 不覆盖"慢滴"（CI 实证：stock_share_hold_change_szse 在 runner 上 6205 页×1.5it/s
# ≈69 分钟，数据持续流动 socket 超时永不触发，拖死 update 步骤致 timing_history 断流）。
_AK_CALL_TIMEOUT = int(os.environ.get("AK_CALL_TIMEOUT_SECONDS", "180"))


class _AkCallTimeout(Exception):
    """safe_ak_call 墙钟超时（慢滴拦截）"""


def _ak_alarm_handler(signum, frame):
    raise _AkCallTimeout()


def safe_ak_call(func, *args, **kwargs):
    """安全调用 AKShare 接口（10-18 加固：逐路起始/耗时日志；10-23 加固：墙钟硬超时）。

    起始日志（⟳）配合 CI 的 PYTHONUNBUFFERED=1 实时输出——runner 再挂死时，
    日志中最后一条 ⟳ 即为挂死数据源，直接定位无需复现。
    墙钟超时用 SIGALRM 实现，仅主线程生效（子线程/非 POSIX 自动退化为仅日志）。
    """
    fname = getattr(func, "__name__", str(func))
    t0 = time.time()
    logger.info(f"    ⟳ ak.{fname} ...")
    use_alarm = (
        _AK_CALL_TIMEOUT > 0
        and hasattr(_signal, "SIGALRM")
        and _threading.current_thread() is _threading.main_thread()
    )
    old_handler = None
    if use_alarm:
        old_handler = _signal.signal(_signal.SIGALRM, _ak_alarm_handler)
        _signal.setitimer(_signal.ITIMER_REAL, _AK_CALL_TIMEOUT)
    try:
        result = func(*args, **kwargs)
        el = time.time() - t0
        if isinstance(result, pd.DataFrame):
            logger.info(f"    ✓ ak.{fname}: {len(result)} 行 ({el:.1f}s)")
        else:
            logger.info(f"    ✓ ak.{fname}: OK ({el:.1f}s)")
        return result
    except _AkCallTimeout:
        logger.warning(
            f"    ✗ ak.{fname} ({time.time() - t0:.1f}s): AkCallTimeout(>{_AK_CALL_TIMEOUT}s 慢滴拦截)")
        return pd.DataFrame() if hasattr(func, '__name__') and 'stock' in fname else None
    except Exception as e:
        logger.warning(f"    ✗ ak.{fname} ({time.time() - t0:.1f}s): {type(e).__name__}: {e}")
        return pd.DataFrame() if hasattr(func, '__name__') and 'stock' in func.__name__ else None
    finally:
        if use_alarm:
            _signal.setitimer(_signal.ITIMER_REAL, 0)
            _signal.signal(_signal.SIGALRM, old_handler)


def _ak_industry_capital_df(ak):
    """产业资本(董监高/重要股东)增减持——按可用接口顺序降级。

    P2-2(2026-10-10)：旧代码调用 akshare 不存在的 `stock_share_change` 导致
    AttributeError，产业资本信号长期缺失。此处改为可用的交易所/东财接口链。
    """
    # [F1 2026-10-23] 顺序对调：ggcg_em(东财) 实证 ~90s/295页 全量可用（10-18 本地）；
    # szse 在 GitHub runner 上 6205 页×1.5it/s ≈69min 慢滴（F1 断流根因），降为备用，
    # 且每路均有 safe_ak_call 180s 墙钟硬超时兜底。
    for name, kwargs in (
        ("stock_ggcg_em", {"symbol": "全部"}),
        ("stock_share_hold_change_szse", {"symbol": "全部"}),
    ):
        fn = getattr(ak, name, None)
        if fn is None:
            continue
        # 2026-10-18 加固：走 safe_ak_call（起始/耗时日志+socket 超时降级），
        # 失败返回空 df 不抛出，降级链语义不变
        df = safe_ak_call(fn, **kwargs)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return pd.DataFrame()


def percentile_rank(series, value):
    """计算 value 在 series 中的百分位排名"""
    valid = series.dropna()
    if valid.empty or len(valid) < 50:
        return None
    return round((valid < value).mean() * 100, 1)


def score_from_percentile(val, pct, reverse=False):
    """
    从百分位生成分值 (0-100)
    reverse=True 表示值越大越危险（如估值分位）
    """
    if pct is None:
        return 50
    if reverse:
        # 高百分位 = 高估 = 高分（危险信号）
        return round(min(100, max(0, pct)), 0)
    else:
        # 高百分位 = 利好 = 低分（安全信号）
        return round(min(100, max(0, 100 - pct)), 0)



# ============================================================
# P1-7: 非线性权重 + 极端锚定状态机 (2026-10-16)
# ============================================================
# 设计意图：当维度分极端时（>85 或 <15），该维度权重×2
# 滞回逻辑防止跨月边界反复触发（进>85/<15，退<80/>20）
# 已知特性：情绪/资金流极端时常同向触发，是设计意图（解决双重稀释），
# 但属对相关维度的隐性重复计数——投资分析专业校验已确认接受此特性。

P17_ENTER_HIGH = 85
P17_EXIT_HIGH = 80
P17_ENTER_LOW = 15
P17_EXIT_LOW = 20

P17_A_STAR_ENTER = 20
P17_A_STAR_EXIT = 23
P17_D_STAR_ENTER = 70
P17_D_STAR_EXIT = 67
P17_COOLDOWN_DAYS = 90  # 3个月

# P1-9: 预警中间态阈值（非对称响应）
P19_WARNING_ENTER_COMPOSITE = 62
P19_WARNING_ENTER_ACCEL = 55
P19_WARNING_ACCEL_THRESHOLD = 8
P19_WARNING_EXIT_COMPOSITE = 50
P19_WARNING_UPGRADE_COMPOSITE = 70
P19_WARNING_TARGET_EQUITY = 40
P19_WARNING_MONTHLY_DECAY = 2
# P1-6 联动：强空头趋势下预警触发①放宽（投资分析终版规格 2026-10-18）
P19_WARNING_ENTER_TREND_BEAR = 55

# P2-11: 仓位映射迟滞（投资分析 2026-10-18 裁决：升温 h=0 一碰即走/降温 h=2 需确认）
P211_HYSTERESIS_BAND = 2
P211_STAGE_ORDER = ["A", "B", "C", "D"]
P211_STAGE_THRESHOLDS = [25, 50, 75]  # A<25<=B<50<=C<75<=D


def apply_stage_hysteresis(composite_score, prev_stage):
    """[P2-11 2026-10-18 裁决] 阶段映射迟滞状态机：
    - 升温方向（A→B→C→D，卖侧减仓）：h=0，一碰即走（"卖必须早"，渐进由 P1-9 预警态承担）
    - 降温方向（D→C→B→A，买侧加仓）：h=2，需穿越 边界-2 才确认（吸收 49/50 边界横跳，主人 10-18 实测）
    - 无/非法 prev_stage → 裸映射 fallback
    裁决注记：渐进卖出由 P1-9 预警态（>62）承担，阶段层卖侧可激进。
    """
    cs = composite_score
    raw = "A" if cs < 25 else ("B" if cs < 50 else ("C" if cs < 75 else "D"))
    if prev_stage not in P211_STAGE_ORDER:
        return raw
    prev_idx = P211_STAGE_ORDER.index(prev_stage)
    raw_idx = P211_STAGE_ORDER.index(raw)
    if raw_idx >= prev_idx:
        return raw  # 升温方向（含不变）h=0 直接采用
    # 降温方向：自 prev 向 raw 逐边界检查，需每边界 ≤ boundary-2 才继续穿
    for b in range(prev_idx - 1, raw_idx - 1, -1):
        if cs <= P211_STAGE_THRESHOLDS[b] - P211_HYSTERESIS_BAND:
            continue
        return P211_STAGE_ORDER[b + 1]  # 未确认穿越，停在该边界暖侧阶段
    return raw


# P2-13: 信号 IC/胜率检验（投资分析 2026-10-18 裁决 Q6-Q8）
# 四类信号+composite IC；20/60 交易日双窗；弱多头/弱空头不计胜负；
# 披露门槛 n≥20 显示百分比 / n≥50 允许研报引用 / n<10 只显示"n 累积中"
P213_WINDOWS = (20, 60)
P213_MIN_DISCLOSE_N = 20
P213_MIN_CITE_N = 50


def _p213_direction_of_stage(from_s, to_s):
    order = "ABCD"
    if from_s not in order or to_s not in order:
        return None
    return "reduce" if order.index(to_s) > order.index(from_s) else "add"


def p213_diff_signals(prev_rec, cur_rec, price_today):
    """[P2-13 裁决Q6/Q7] 相邻决策态 diff → 信号列表。
    direction: add=加仓向（未来涨=胜）/ reduce=减仓向（未来跌=胜）。
    弱多头/弱空头切换不生成信号（裁决Q7：方向模糊会稀释统计）。
    锚定/预警仅记录进入事件（解除走冷却状态机，不作反向信号）。
    """
    sigs = []
    date = cur_rec.get("date")
    comp = cur_rec.get("composite_score")

    # ① 阶段切换
    ps, cs = prev_rec.get("stage"), cur_rec.get("stage")
    # from=None=机制上线首日伪前态（如P1-6前无trend字段），不生成信号防统计污染
    if ps and cs and ps != cs:
        d = _p213_direction_of_stage(ps, cs)
        if d:
            sigs.append({"type": "stage", "from": ps, "to": cs, "direction": d})

    # ② 预警进出（进入=减仓向；退出=恢复=加仓向）
    pw = (prev_rec.get("warning_state") or {}).get("current")
    cw = (cur_rec.get("warning_state") or {}).get("current")
    if pw is not None and pw != cw:
        if cw == "warning":
            sigs.append({"type": "warning_enter", "from": pw, "to": cw, "direction": "reduce"})
        elif pw == "warning":
            sigs.append({"type": "warning_exit", "from": pw, "to": cw, "direction": "add"})

    # ③ A*/D* 锚定进入（A*=极端恐惧逆向加仓向 / D*=极端贪婪减仓向）
    pa = (prev_rec.get("anchor_state") or {}).get("current")
    ca = (cur_rec.get("anchor_state") or {}).get("current")
    if pa is not None and pa != ca:
        if ca == "A_star":
            sigs.append({"type": "anchor", "from": pa, "to": ca, "direction": "add"})
        elif ca == "D_star":
            sigs.append({"type": "anchor", "from": pa, "to": ca, "direction": "reduce"})

    # ④ 趋势切换（仅强多头/强空头计胜负；弱态不生成信号）
    pt = (prev_rec.get("trend_state") or {}).get("trend")
    ct = (cur_rec.get("trend_state") or {}).get("trend")
    if pt is not None and pt != ct:
        if ct == "strong_bull":
            sigs.append({"type": "trend", "from": pt, "to": ct, "direction": "add"})
        elif ct == "strong_bear":
            sigs.append({"type": "trend", "from": pt, "to": ct, "direction": "reduce"})

    for s in sigs:
        s.update({"date": date, "composite": comp, "price": price_today,
                  "settled20": None, "settled60": None})
    return sigs


def _p213_close_map(index_df):
    """日K DataFrame → {date_str: close}（腾讯源 date 列为 'YYYY-MM-DD' 字符串或 Timestamp）"""
    out = {}
    if index_df is None:
        return out
    for d, c in zip(index_df["date"], index_df["close"]):
        out[str(d)[:10]] = float(c)
    return out


def p213_settle(entries, close_map):
    """结算：每条含 date/price 的记录，满窗口交易日数则写 settled{win}={date, ret}。
    窗口按收盘价序列的交易日索引推进（用排序后的收盘日期表）。
    信号日无收盘（周末/假日）→ 以次一交易日收盘为基准价（信号次日执行口径），
    记 base_date 显形偏移。"""
    dates = sorted(close_map.keys())
    idx = {d: i for i, d in enumerate(dates)}
    n_settled = 0
    for e in entries:
        d0 = e.get("date")
        if not d0:
            continue
        if d0 in idx:
            base_i, base_d = idx[d0], d0
        else:
            later = [d for d in dates if d > d0]
            if not later:
                continue
            base_d = later[0]
            base_i = idx[base_d]
        for win in P213_WINDOWS:
            key = f"settled{win}"
            if e.get(key) is not None:
                continue
            j = base_i + win
            if j < len(dates):
                d1 = dates[j]
                p0 = e.get("price") or close_map.get(base_d)
                if p0:
                    st = {"date": d1, "ret": round(close_map[d1] / p0 - 1, 4)}
                    if base_d != d0:
                        st["base_date"] = base_d  # 假日信号：基准日=次一交易日
                    e[key] = st
                    n_settled += 1
    return n_settled


def _p213_win(direction, ret):
    """胜=方向一致；ret==0 记 None（平局，不入胜负分子分母）"""
    if ret is None:
        return None
    if abs(ret) < 1e-12:
        return None
    return (ret > 0) if direction == "add" else (ret < 0)


def _spearman(xs, ys):
    """秩相关（无 scipy：rank 后 pearson；并列秩取均值）"""
    n = len(xs)
    if n < 3:
        return None
    def _rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((v - mx) ** 2 for v in rx)
    vy = sum((v - my) ** 2 for v in ry)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / (vx ** 0.5 * vy ** 0.5), 4)


def p213_compute_stats(log):
    """聚合统计：分类型 20/60 窗 n/胜率/平均收益 + composite IC（裁决Q6 元指标）"""
    stats = {"by_type": {}, "ic": {}, "total_signals": len(log.get("signals", []))}
    for sig in log.get("signals", []):
        t = sig["type"]
        bucket = stats["by_type"].setdefault(t, {})
        for win in P213_WINDOWS:
            st = sig.get(f"settled{win}")
            b = bucket.setdefault(f"w{win}", {"n": 0, "win": 0, "draw": 0, "ret_sum": 0.0})
            if st is None:
                continue
            b["n"] += 1
            b["ret_sum"] += st["ret"]
            w = _p213_win(sig["direction"], st["ret"])
            if w is True:
                b["win"] += 1
            elif w is None:
                b["draw"] += 1
    # 收敛输出：胜率=win/(n-draw)，披露门槛由前端按 n 判断
    for t, bucket in stats["by_type"].items():
        for win in P213_WINDOWS:
            b = bucket.get(f"w{win}")
            if not b:
                continue
            eff = b["n"] - b["draw"]
            b["win_rate"] = round(b["win"] / eff, 4) if eff > 0 else None
            b["avg_ret"] = round(b["ret_sum"] / b["n"], 4) if b["n"] > 0 else None
            del b["ret_sum"]
    # composite IC：每日决策点（ic_points 已结算窗）
    for win in P213_WINDOWS:
        pts = [(p["composite"], p[f"ret{win}"]) for p in log.get("ic_points", [])
               if p.get(f"ret{win}") is not None and p.get("composite") is not None]
        if len(pts) >= 10:
            stats["ic"][f"ic{win}"] = _spearman([p[0] for p in pts], [p[1] for p in pts])
            stats["ic"][f"ic{win}_n"] = len(pts)
        else:
            stats["ic"][f"ic{win}"] = None
            stats["ic"][f"ic{win}_n"] = len(pts)
    stats["disclose_min_n"] = P213_MIN_DISCLOSE_N
    stats["cite_min_n"] = P213_MIN_CITE_N
    return stats


def p213_update(history, fetch_index_fn=None, log_name="signal_log.json"):
    """[P2-13] 主入口：diff 最新两条决策态生成信号 + 结算 + IC 日点 + 统计。
    返回 stats（供 timing_scores.p2_13_state 下发）；任何子步骤失败仅告警不阻断主流程。
    fetch_index_fn 默认 None → 惰性解析 fetch_trend_index_daily（定义在后文 P1-6 区块）。
    """
    if fetch_index_fn is None:
        fetch_index_fn = fetch_trend_index_daily
    log = load_json(log_name) or {"signals": [], "ic_points": []}
    log.setdefault("signals", [])
    log.setdefault("ic_points", [])

    df = fetch_index_fn()
    close_map = _p213_close_map(df)

    # ① 新信号：diff 末两条 record（同日重跑去重：信号 id=date+type+from+to）
    if len(history) >= 2:
        prev_rec, cur_rec = history[-2], history[-1]
        if prev_rec.get("date") != cur_rec.get("date"):
            px = close_map.get(cur_rec.get("date"))
            new_sigs = p213_diff_signals(prev_rec, cur_rec, px)
            existing = {(s["date"], s["type"], s.get("from"), s.get("to")) for s in log["signals"]}
            for s in new_sigs:
                key = (s["date"], s["type"], s.get("from"), s.get("to"))
                if key not in existing:
                    log["signals"].append(s)

    # ② IC 日点：最新 record 当日（同日去重）
    if history:
        cur = history[-1]
        d, comp = cur.get("date"), cur.get("composite_score")
        if d and comp is not None and d in close_map:
            if not any(p["date"] == d for p in log["ic_points"]):
                log["ic_points"].append({"date": d, "composite": comp,
                                         "price": close_map[d], "ret20": None, "ret60": None})

    # ③ 结算：信号（用 settled 结构）+ IC 日点（用 ret{win} 扁平结构）
    p213_settle(log["signals"], close_map)
    for pt in log["ic_points"]:
        dates = sorted(close_map.keys())
        if pt["date"] not in dates:
            continue
        i0 = dates.index(pt["date"])
        for win in P213_WINDOWS:
            k = f"ret{win}"
            if pt.get(k) is None and i0 + win < len(dates) and pt.get("price"):
                pt[k] = round(close_map[dates[i0 + win]] / pt["price"] - 1, 4)

    log["stats"] = p213_compute_stats(log)
    log["updated"] = date.today().isoformat()
    save_json(log, log_name)
    return log["stats"]


# P1-8: 美联储双层（投资分析 2026-10-18 裁决终版）
# 数据源：collect_fedwatch.py（cme-fedwatch 包，每日结算后取数一次）→ data/fed_watch.json
P18_HIKE_PROB_THRESHOLD = 0.85   # 预期层概率单路阈值 [F3③ A1 主人裁决 2026-10-23]：累计加息概率>85%=市场定价近乎确定，独立触发（AND 时代 0.60 辅助水位废止）
P18_YIELD_10Y_THRESHOLD = 5.2    # 预期层：10Y 美债收益率阈值 %
P18_EXPECT_CAP = 32              # 预期层仓位上限 %（25-32 区间取上沿，禁加成长股：创业板+科创50）
P18_EVENT_SOFT_CAP = 32          # 事件层自动轨：检测到目标区间上移 → 软压 32%（不钉死）
P18_EVENT_HARD_CAP = 25          # 事件层人工轨：会后人工确认 hawkish=true → 钉 25%
P18_MANUAL_CONFIRM_LIMIT_DAYS = 2  # 人工轨 2 个交易日时限，超时维持软事件层 32%（防人工位空转）
P18_COOLDOWN_DAYS = 180          # Fed_D* 冷却：层解除后 180 天内不重复触发
P18_STALE_DAYS = 4               # fed_watch.json 超过 N 天未更新视为 stale → 继承前日状态

# F3①: 加息周期检测（投资分析 2026-10-23 裁决）
# hiking_cycle：fed_funds 当前值 > 3个月前 + 10bp（月均半步容差，9/16 实例 +12bp 可检出）
F3_HIKING_CYCLE_WINDOW_MONTHS = 3   # 对比窗口
F3_HIKING_CYCLE_THRESHOLD_BP = 10.0 # 加息阈值（基点）——[松子 F3 联调] 月均口径容差：单次25bp加息的月均仅+12bp半步，12.5 会漏检（9/16 实例 3.63→3.75），10 吸收半步且高于平台期噪声(±3bp)
# FOMC 双保险：会议已过 + CME 加息概率 > 90% → 自动置位
F3_FOMC_AUTO_THRESHOLD = 0.90       # hike_prob > 90%
# USDCNY 年贬幅度
F3_USDCNY_ANNUAL_WINDOW_DAYS = 250  # ≈ 1 年交易日

# F3④: 全球主导权仲裁三阈值（2026-10-23 主人裁决 B1+豁免）
# ≥2 满足 = 全球流动性压倒国内面 → 仓位上限 25% + 冻结全部加仓触发器；
# 豁免 = R7 has_catalyst=true 时保留 A 段左侧布局（其余加仓仍冻结），全程注记可观测
F3_DOM_HIKE_SLOPE_BP_YR = 300.0    # a. 加息斜率年化阈值（bp）
F3_DOM_QT_3M_PCT = -0.5            # a. QT：fed_balance 近3月降幅 %
F3_DOM_USDCNY_DEPREC_PCT = 4.5     # b. USDCNY 年贬阈值 %（文档 4-5% 取中值，主人认可）
F3_DOM_FX_RESERVE_DECLINE_M = 3    # b. 外储连降月数
F3_CNH_CNY_STREAK_BP = 40          # b. CNH-CNY 价差连判阈值 bp（=0.4%≈300pips，投资分析参数 2026-10-23）
F3_CNH_CNY_STREAK_DAYS = 5         # b. 连判交易日数
F3_CNH_CNY_SINGLE_BP = 70          # b. 单日触发阈值 bp（=0.7%≈500pips）
F3_DOM_CREDIT_GAP_PP = 2.0         # c. M2-社融存量同比裂口（pp）
F3_DOM_VETO_CAP = 25               # 否决仓位上限 %

# R7: 冲突状态卡 + 催化剂（v2 P1，2026-10-23 主人确认；参数=投资分析交付，全部参数化供 P2-12 校准）
R7_EXTREME_PCT = 15.0            # 位置分位：极端区上界 %
R7_NORMAL_HI_PCT = 50.0          # 普通区上界 %
R7_EXPENSIVE_PCT = 75.0          # 偏高区上界（>75=极贵）
R7_FAST_BEAR_3M_PCT = -15.0      # 快熊：近3月跌幅 ≥15%（63 交易日）
R7_SLOW_BEAR_MIN_MONTHS = 3      # 阴熊：连续 ≥3 月负动量（未达快熊）
R7_REBOUND_ONLY_CAP = 55         # 极端区无催化剂：交易性仓位上限 %（2012 型反弹）
R7_REVERSAL_TARGET = 70          # 极端区有催化剂：反转候选目标 %（2018Q4/2024.9 型）
R7_PCT_WINDOW_DAYS = 365         # 位置分位窗口（与前端 R1/R5 同口径：含重建段）
CATALYST_K_LIQ = 2               # 流动性路连续月数
CATALYST_K_ERN = 2               # 盈利路连续月数
CATALYST_M = 2                   # 有/无判定：≥2 路=有（反转候选）
CATALYST_POLICY_TTL_MONTHS = 6   # 政策路人工位有效期（政策窗口期）

def compute_f3_usdcny_and_fx(us_macro_data):
    """F3①-3: USDCNY 年贬幅度 + 外储月度（入 us_macro history）

    - usdcny_annual_depreciation_pct: 现价/250日前收盘 − 1（×100），正=贬值
    - fx_reserves_billion_usd: 国家外汇储备（亿美元），月度，源 akshare macro_china_fx_gold
    返回 dict，由 update_data 主流程 merge 进 us_macro.json 的 history。
    """
    import logging
    logger = logging.getLogger(__name__)
    out = {}

    # --- USDCNY 年贬幅度 ---
    uc = ((us_macro_data or {}).get("history", {}) or {}).get("usdcny", []) or []
    if len(uc) >= F3_USDCNY_ANNUAL_WINDOW_DAYS:
        try:
            latest = float(uc[-1]["value"])
            year_ago = float(uc[-F3_USDCNY_ANNUAL_WINDOW_DAYS]["value"])
            dep_pct = (latest - year_ago) / year_ago * 100.0
            out["usdcny_annual_depreciation"] = {
                "date": uc[-1]["date"],
                "value": round(dep_pct, 4),
                "unit": "%",
                "note": f"现价{latest:.4f}/250日前{year_ago:.4f}，正=人民币贬值"
            }
            logger.info(f"  ✓ F3① USDCNY 年贬: {dep_pct:.2f}% (1yr {year_ago:.4f}→{latest:.4f})")
        except Exception as e:
            logger.warning(f"  ⚠ F3① USDCNY 年贬计算失败: {e}")
    else:
        logger.warning(f"  ⚠ F3① USDCNY 历史不足 {F3_USDCNY_ANNUAL_WINDOW_DAYS} 条（{len(uc)}），跳过")

    # --- 外储月度（akshare macro_china_fx_gold）---
    try:
        import akshare as ak
        df = safe_ak_call(ak.macro_china_fx_gold)
        if df is not None and not df.empty:
            last_row = df.iloc[-1]
            m_date = str(last_row.get("月份", ""))
            # 解析 "2026年09月份" → "2026-09"
            m_match = re.search(r"(\d{4})年(\d{1,2})月", m_date)
            if m_match:
                iso_date = f"{m_match.group(1)}-{int(m_match.group(2)):02d}"
                reserves_val = float(last_row.get("国家外汇储备-数值", 0))
                out["fx_reserves"] = {
                    "date": iso_date,
                    "value": round(reserves_val, 2),
                    "unit": "亿美元",
                    "source": "akshare macro_china_fx_gold（央行口径）"
                }
                logger.info(f"  ✓ F3① 外储: {reserves_val:.2f}亿美元 ({iso_date})")
        else:
            logger.warning("  ⚠ F3① 外储：akshare macro_china_fx_gold 返回空")
    except Exception as e:
        logger.warning(f"  ⚠ F3① 外储采集失败: {e}")

    return out


# P1-6: 价格趋势过滤（投资分析终版规格 2026-10-18）
# 标的：中证全指（万得全 A 免费替代，覆盖全部 A 股；腾讯源，CI 已验证可用）
P16_INDEX_SYMBOL = "sh000985"
P16_MA_PERIOD = 120              # 120 日线
P16_MA_DIRECTION_LOOKBACK = 20   # 均线方向：今日 MA120 vs 20 交易日前 MA120（防单日噪音）
P16_MOMENTUM_LOOKBACK = 250      # 12 个月动量：现价/250 日前收盘−1，阈值 0
P16_A_STAR_BEAR_TARGET = 55      # A* 触发+强空头 → 降档 55%（A 区间下沿）

def apply_nonlinear_weights(dimensions, prev_hysteresis):
    """
    P1-7: 对极端维度应用权重×2，带滞回防跳变
    进入带：score > 85 或 score < 15
    退出带：score < 80（高）或 score > 20（低）
    Returns: (adjusted_dimensions, today_hysteresis)
    """
    import copy
    adjusted_dimensions = copy.deepcopy(dimensions)
    today_hysteresis = {}

    for dim_name, dim_data in adjusted_dimensions.items():
        if dim_name.startswith("_"):
            continue

        score = dim_data.get("score", 50)
        orig_weight = dim_data.get("_orig_weight", dim_data.get("weight", 15))
        dim_data["_orig_weight"] = orig_weight

        prev_state = prev_hysteresis.get(dim_name, {"active": False, "direction": None})
        was_active = prev_state.get("active", False)
        last_dir = prev_state.get("direction")

        is_extreme = False
        direction = None

        if score > P17_ENTER_HIGH:
            is_extreme = True
            direction = "high"
        elif score < P17_ENTER_LOW:
            is_extreme = True
            direction = "low"
        elif was_active:
            if last_dir == "high" and score < P17_EXIT_HIGH:
                is_extreme = False
            elif last_dir == "low" and score > P17_EXIT_LOW:
                is_extreme = False
            else:
                is_extreme = True
                direction = last_dir

        if is_extreme:
            new_weight = orig_weight * 2
            dim_data["weight"] = new_weight
            dim_data["weighted_score"] = round(score * new_weight / 100, 2)
            today_hysteresis[dim_name] = {"active": True, "direction": direction}
        else:
            dim_data["weight"] = orig_weight
            dim_data["weighted_score"] = round(score * orig_weight / 100, 2)
            today_hysteresis[dim_name] = {"active": False, "direction": None}

    return adjusted_dimensions, today_hysteresis


def apply_extreme_anchoring(composite_score, prev_anchor_state, today):
    """
    P1-7: 极端锚定状态机
    A* (composite<20 → target 65%), D* (>70 → target 15%)
    滞回：退出条件 composite>23 / <67
    冷却期：解除后3个月内不重复触发
    Returns: (anchor_state, target_equity, anchor_note)
    """
    from datetime import datetime, timedelta

    current_anchor = prev_anchor_state.get("current", "normal")
    cooldown_until = prev_anchor_state.get("cooldown_until")
    since_date = prev_anchor_state.get("since")

    in_cooldown = False
    if cooldown_until:
        try:
            cooldown_date = datetime.strptime(cooldown_until, "%Y-%m-%d").date()
            if datetime.now().date() < cooldown_date:
                in_cooldown = True
        except:
            pass

    new_anchor = current_anchor
    anchor_note = ""

    if current_anchor == "normal":
        if in_cooldown:
            new_anchor = "normal"
            anchor_note = f"冷却中（至{cooldown_until}）"
        elif composite_score < P17_A_STAR_ENTER:
            new_anchor = "A_star"
            anchor_note = "A* 极端恐惧锚定（target 65%）"
        elif composite_score > P17_D_STAR_ENTER:
            new_anchor = "D_star"
            anchor_note = "D* 极端贪婪锚定（target 15%）"
    elif current_anchor == "A_star":
        if composite_score > P17_A_STAR_EXIT:
            new_anchor = "normal"
            cooldown_date = datetime.now() + timedelta(days=P17_COOLDOWN_DAYS)
            cooldown_until = cooldown_date.strftime("%Y-%m-%d")
            anchor_note = f"A* 解除，进入冷却（至{cooldown_until}）"
        else:
            anchor_note = "A* 持续中（target 65%）"
    elif current_anchor == "D_star":
        if composite_score < P17_D_STAR_EXIT:
            new_anchor = "normal"
            cooldown_date = datetime.now() + timedelta(days=P17_COOLDOWN_DAYS)
            cooldown_until = cooldown_date.strftime("%Y-%m-%d")
            anchor_note = f"D* 解除，进入冷却（至{cooldown_until}）"
        else:
            anchor_note = "D* 持续中（target 15%）"

    if new_anchor == "A_star":
        target_equity = 65
    elif new_anchor == "D_star":
        target_equity = 15
    else:
        target_equity = None

    anchor_state = {
        "current": new_anchor,
        "since": since_date if new_anchor == current_anchor else today,
        "cooldown_until": cooldown_until if (new_anchor == "normal" and current_anchor != "normal") else prev_anchor_state.get("cooldown_until"),
        "last_trigger": today if new_anchor != current_anchor else prev_anchor_state.get("last_trigger")
    }

    return anchor_state, target_equity, anchor_note


def apply_warning_state(composite_score, composite_history, prev_warning_state, fear_greed_data, anchor_state=None, trend_state=None):
    """P1-9: 预警中间态状态机（非对称响应）
    
    预警态触发条件（满足任一）：
    ① composite > 62
    ② composite > 55 且单周变化 > +8
    ③ 恐贪 > 85 且 QVIX < 14（P0-3 统一阈值）
    ④ 强空头趋势下条件①放宽到 > 55（待 P1-6 联动）
    
    预警态退出：
    - composite < 50 → 回夏长
    - composite > 70 → 升级接 D* 锚定（P1-7 接管）
    
    Args:
        composite_score: 当日综合分
        composite_history: 近7日composite列表（用于计算单周变化）
        prev_warning_state: 上一日预警状态
        fear_greed_data: 恐贪指针数据（含 score 和 qvix）
    
    Returns: (warning_state, warning_target_equity, warning_note)
    """
    current_state = prev_warning_state.get("current", "normal")  # normal / warning / escalated
    since_date = prev_warning_state.get("since")
    target_equity = prev_warning_state.get("target_equity", P19_WARNING_TARGET_EQUITY)
    
    # 计算单周变化（口径：history[-5] 即 5 条记录≈4-5 个交易日间隔，投资分析 2026-10-18 确认可接受、偏保守方向）
    weekly_change = 0
    if len(composite_history) >= 5:
        weekly_change = composite_score - composite_history[-5]
    
    # 获取恐贪数据
    fg_score = fear_greed_data.get("score", 50)
    fg_qvix = fear_greed_data.get("qvix", 20)
    
    # 判断是否满足预警触发条件
    should_enter = False
    trigger_reason = ""
    
    if current_state == "normal":
        # 条件①: composite > 62（P1-6 联动：强空头趋势下放宽到 > 55）
        _enter_threshold = P19_WARNING_ENTER_COMPOSITE
        if trend_state and trend_state.get("trend") == "strong_bear":
            _enter_threshold = P19_WARNING_ENTER_TREND_BEAR
        if composite_score > _enter_threshold:
            should_enter = True
            trigger_reason = f"composite>{_enter_threshold}" + ("（强空头放宽）" if _enter_threshold != P19_WARNING_ENTER_COMPOSITE else "")
        # 条件②: composite > 55 且单周变化 > +8
        elif composite_score > P19_WARNING_ENTER_ACCEL and weekly_change > P19_WARNING_ACCEL_THRESHOLD:
            should_enter = True
            trigger_reason = f"composite>{P19_WARNING_ENTER_ACCEL}且单周+{weekly_change:.1f}>{P19_WARNING_ACCEL_THRESHOLD}"
        # 条件③: 恐贪 > 85 且 QVIX < 14
        elif fg_score > 85 and fg_qvix < 14:
            should_enter = True
            trigger_reason = f"恐贪{fg_score:.1f}>85且QVIX{fg_qvix:.1f}<14"
    
    # 状态转换逻辑
    if current_state == "normal":
        if should_enter:
            new_state = "warning"
            since_date = today_str()
            target_equity = P19_WARNING_TARGET_EQUITY
            note = f"进入预警态（{trigger_reason}），仓位压至{target_equity}%"
        else:
            new_state = "normal"
            target_equity = None
            note = None
    elif current_state == "warning":
        # 升级条件：composite > 70 → 升级接 D* 锚定
        if composite_score > P19_WARNING_UPGRADE_COMPOSITE:
            new_state = "escalated"
            target_equity = 15  # D* 锚定仓位
            note = f"预警升级至D*锚定（composite={composite_score}>{P19_WARNING_UPGRADE_COMPOSITE}），仓位钉死15%"
        # 退出条件：composite < 50 → 回夏长
        elif composite_score < P19_WARNING_EXIT_COMPOSITE:
            new_state = "normal"
            target_equity = None
            note = f"退出预警态回夏长（composite={composite_score}<{P19_WARNING_EXIT_COMPOSITE}）"
        else:
            # 维持预警态，逐月递减仓位
            new_state = "warning"
            # 计算已预警月数
            from datetime import datetime
            if since_date:
                days_in_warning = (datetime.strptime(today_str(), "%Y-%m-%d") - 
                                  datetime.strptime(since_date, "%Y-%m-%d")).days
                months_in_warning = days_in_warning // 30
                target_equity = max(30, P19_WARNING_TARGET_EQUITY - months_in_warning * P19_WARNING_MONTHLY_DECAY)
                note = f"维持预警态（已{days_in_warning}天），目标仓位{target_equity}%"
            else:
                note = f"维持预警态，目标仓位{target_equity}%"
    elif current_state == "escalated":
        # P1-7 D* 锚定是否仍在激活
        d_star_active = anchor_state and anchor_state.get("current") == "D_star"
        if d_star_active:
            # D* 仍在，保持 escalated
            new_state = "escalated"
            target_equity = 15
            note = f"D*锚定生效中，仓位钉死15%"
        else:
            # D* 已解除，escalated 跟随退出
            if composite_score > P19_WARNING_ENTER_COMPOSITE:
                # 仍满足预警条件 → 回 warning
                new_state = "warning"
                target_equity = P19_WARNING_TARGET_EQUITY
                since_date = today_str()
                note = f"D*解除后回落至预警态（composite={composite_score}>{P19_WARNING_ENTER_COMPOSITE}），仓位{target_equity}%"
            elif composite_score < P19_WARNING_EXIT_COMPOSITE:
                # composite < 50 → 回 normal
                new_state = "normal"
                target_equity = None
                note = f"D*解除后退出预警态（composite={composite_score}<{P19_WARNING_EXIT_COMPOSITE}），恢复正常流转"
            else:
                # 50 <= composite <= 62 → 维持 warning（预警态不受冷却期限制）
                new_state = "warning"
                target_equity = P19_WARNING_TARGET_EQUITY
                since_date = today_str()
                note = f"D*解除后维持预警态（composite={composite_score}在50-62之间），仓位{target_equity}%"
    
    warning_state = {
        "current": new_state,
        "since": since_date if (new_state != "normal" and current_state == "normal") else prev_warning_state.get("since"),
        "target_equity": target_equity,
        "weekly_change": round(weekly_change, 1),
        "trigger_reason": trigger_reason if should_enter else prev_warning_state.get("trigger_reason", "")
    }
    
    return warning_state, target_equity, note


def compute_catalyst_state(cn_supplement=None, cn_macro_data=None, manual_cfg=None, today=None):
    """R7 催化剂三路代理（投资分析参数 2026-10-23：政策/流动性/盈利，≥2 路=有）

    政策路：人工确认位（config_overrides.json "policy_pivot"：{"confirmed": true, "pivot_date": "YYYY-MM-DD", "note": "..."}），
            落地工具才算、喊话不算；true 后 6 个月有效期，过期自动归 false。运营：事件驱动年≤5 次+日历提醒（铁律⑧）。
    流动性路：M1 同比连续 k_liq=2 月回升，或 M1-M2 剪刀差连续 2 月收窄（负值收敛方向）。
    盈利路：工业企业利润同比（无源→None）或 PPI 同比连续 k_ern=2 月回升。
    """
    from datetime import datetime as _dt
    today = today or _dt.now().date()

    def _series(rows, key):
        out = []
        for r in (rows or []):
            try:
                out.append((str(r.get("date")), float(r.get(key))))
            except (TypeError, ValueError, AttributeError):
                continue
        return out

    def _rising(vals, k):
        """最新值连续 k 次环比改善（需 k+1 个点）；不足返回 None"""
        if len(vals) < k + 1:
            return None
        return all(vals[-i][1] > vals[-i - 1][1] for i in range(1, k + 1))

    sup = cn_supplement or {}
    # --- 政策路（人工位 + TTL）---
    pp = ((manual_cfg or {}).get("policy_pivot") or {})
    policy_pass = False
    pivot_date = pp.get("pivot_date")
    expires = None
    if pp.get("confirmed") and pivot_date:
        try:
            d0 = _dt.strptime(str(pivot_date), "%Y-%m-%d").date()
            m = d0.month + CATALYST_POLICY_TTL_MONTHS
            y = d0.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            exp_d = d0.replace(year=y, month=m)
            expires = exp_d.isoformat()
            policy_pass = today <= exp_d
        except Exception:
            policy_pass = False

    # --- 流动性路 ---
    m1 = _series(sup.get("m1_yoy"), "value_pct")
    sc = _series(sup.get("m1_m2_scissors"), "value_pct")
    m1_turn = _rising(m1, CATALYST_K_LIQ)
    spread_narrow = _rising(sc, CATALYST_K_LIQ)  # 负值变大=收窄
    liquidity_pass = bool(m1_turn or spread_narrow)

    # --- 盈利路 ---
    cn_hist = (cn_macro_data or {}).get("history", {}) or {}
    ppi = _series(cn_hist.get("ppi_yoy"), "value")
    ppi_turn = _rising(ppi, CATALYST_K_ERN)
    profit_turn = None  # 工业企业利润同比：东财可采，源未接入（下批）
    earnings_pass = bool(profit_turn or ppi_turn)

    score = int(policy_pass) + int(liquidity_pass) + int(earnings_pass)
    return {
        "asof": today.isoformat() if hasattr(today, "isoformat") else str(today),
        "policy": {"pass": bool(policy_pass), "manual_pivot": bool(pp.get("confirmed")),
                   "pivot_date": pivot_date, "expires": expires,
                   "note": pp.get("note", "") if pp.get("confirmed") else "默认 false（保守）；落地工具才算、喊话不算"},
        "liquidity": {"pass": liquidity_pass, "m1_turn": m1_turn, "spread_narrow": spread_narrow,
                      "latest_m1_yoy": (m1[-1][1] if m1 else None)},
        "earnings": {"pass": earnings_pass, "profit_turn": profit_turn, "ppi_turn": ppi_turn},
        "catalyst_score": score,
        "has_catalyst": bool(score >= CATALYST_M),
        "params": {"k_liq": CATALYST_K_LIQ, "k_ern": CATALYST_K_ERN, "m": CATALYST_M,
                   "policy_ttl_months": CATALYST_POLICY_TTL_MONTHS},
    }


def compute_r7_state(timing_history, trend_state, trend_df, catalyst_state, today=None):
    """R7 冲突状态卡（v2 P1，2026-10-23 主人确认；裁决表证据=CIO 197 月回测+投分 4 案例）

    状态识别：位置分位区（近365天窗口，与前端 R1 同口径含重建段）× 趋势（P1-6 四态）× 熊形态
    （快熊=近3月跌幅≥15%；阴熊=连续≥3月负动量未达快熊）→ 信号优先级裁决。
    冲突A（便宜+跌势）/冲突B（贵+涨势）自动点亮。催化剂耦合：仅极端区作定性开关
    （有→反转候选目标 70%；无→仅反弹 ≤55% 需 trend 确认），普通区权重 0 仅展示。
    """
    from datetime import datetime as _dt, timedelta as _td
    today = today or _dt.now().date()

    # --- 位置分位（近 365 天窗口，含重建段，与 R1/R5 前端口径一致）---
    position_pct = None
    window_n = 0
    hist = [r for r in (timing_history or []) if isinstance(r, dict) and isinstance(r.get("composite_score"), (int, float)) and r.get("date")]
    if len(hist) >= 20:
        last_d = _dt.strptime(str(hist[-1]["date"])[:10], "%Y-%m-%d").date()
        cut = (last_d - _td(days=R7_PCT_WINDOW_DAYS)).isoformat()
        window = [r for r in hist if str(r["date"])[:10] >= cut]
        window_n = len(window)
        if window_n >= 20:
            cur = float(window[-1]["composite_score"])
            position_pct = round(sum(1 for r in window if float(r["composite_score"]) < cur) / window_n * 100, 1)
    if position_pct is None:
        zone = "unknown"
    elif position_pct < R7_EXTREME_PCT:
        zone = "extreme"
    elif position_pct < R7_NORMAL_HI_PCT:
        zone = "normal"
    elif position_pct < R7_EXPENSIVE_PCT:
        zone = "high"
    else:
        zone = "extreme_rich"

    # --- 趋势（P1-6 四态）---
    trend = (trend_state or {}).get("trend", "unknown")
    trend_negative = trend in ("weak_bear", "strong_bear")
    trend_positive = trend in ("weak_bull", "strong_bull")

    # --- 熊形态（与 P1-6 同源 sh000985 日K）---
    bear_form = "none"
    bear_detail = {"decline_3m_pct": None, "monthly_neg_streak": 0}
    try:
        if trend_df is not None and len(trend_df) > 64:
            closes = trend_df["close"].astype(float).values
            decline_3m = round((closes[-1] / closes[-64] - 1) * 100, 1)
            bear_detail["decline_3m_pct"] = decline_3m
            # 月负动量：按月聚合收盘，连续负月收益计数
            mdf = (trend_df.assign(date=pd.to_datetime(trend_df["date"]))
                   .set_index("date")["close"].astype(float).resample("ME").last().dropna())
            streak = 0
            for i in range(len(mdf) - 1, 0, -1):
                if mdf.iloc[i] < mdf.iloc[i - 1]:
                    streak += 1
                else:
                    break
            bear_detail["monthly_neg_streak"] = streak
            if decline_3m <= R7_FAST_BEAR_3M_PCT:
                bear_form = "fast_bear"
            elif streak >= R7_SLOW_BEAR_MIN_MONTHS:
                bear_form = "slow_bear"
    except Exception:
        bear_form = "unknown"

    # --- 冲突自动点亮 ---
    conflict = None
    if position_pct is not None:
        if position_pct < R7_NORMAL_HI_PCT and trend_negative:
            conflict = "A"   # 便宜+跌势（历史 25% 月份，位置派 vs 趋势派 8:7，必须分档）
        elif position_pct >= R7_NORMAL_HI_PCT and trend_positive:
            conflict = "B"   # 贵+涨势

    # --- 裁决表（四行，证据见 v2 文档 §三 R7）---
    if zone == "extreme" and bear_form == "fast_bear":
        verdict, verdict_ref = "位置优先·左侧买", "2018Q4 +9.0%、2024/2 A* +20.2%"
    elif zone == "extreme" and bear_form == "slow_bear":
        verdict, verdict_ref = "只做反弹·止盈不加满", "2011-12 comp 2.6 仍再跌 -22.5%（CIO 回测反例）"
    elif zone == "normal" and trend_negative:
        verdict, verdict_ref = "趋势优先（价值陷阱）", "2021-22 趋势空仓避过 -41%"
    else:
        verdict, verdict_ref = "按既有框架（P1-7/P1-9 信号）", "非冲突分档区"

    # --- 催化剂耦合（仅极端区定性开关）---
    has_cat = bool((catalyst_state or {}).get("has_catalyst"))
    if zone == "extreme":
        coupling = {
            "zone_active": True, "has_catalyst": has_cat,
            "mode": ("reversal_candidate" if has_cat else "rebound_only"),
            "cap_pct": (R7_REVERSAL_TARGET if has_cat else R7_REBOUND_ONLY_CAP),
            "note": ("极端区+催化剂≥2路=反转候选：左侧买点，目标可看 A 区上限 70%，可突破常态锚定" if has_cat
                     else "极端区+催化剂≤1路=仅反弹（2012 型）：交易性仓位 ≤55%，需 trend 确认方可延续，反弹高度有限"),
        }
    else:
        coupling = {"zone_active": False, "has_catalyst": has_cat, "mode": "display_only",
                    "cap_pct": None, "note": "普通区催化剂不参与裁决（权重 0 仅展示），防与 composite 加分双重计数"}

    return {
        "as_of": today.isoformat() if hasattr(today, "isoformat") else str(today),
        "position_pct": position_pct, "position_zone": zone, "window_n": window_n,
        "trend": trend, "trend_negative": trend_negative,
        "bear_form": bear_form, "bear_detail": bear_detail,
        "conflict": conflict,
        "verdict": verdict, "verdict_ref": verdict_ref,
        "catalyst_coupling": coupling,
        "params": {"extreme_pct": R7_EXTREME_PCT, "normal_hi_pct": R7_NORMAL_HI_PCT,
                   "fast_bear_3m_pct": R7_FAST_BEAR_3M_PCT, "slow_bear_min_months": R7_SLOW_BEAR_MIN_MONTHS,
                   "rebound_cap": R7_REBOUND_ONLY_CAP, "reversal_target": R7_REVERSAL_TARGET,
                   "window_days": R7_PCT_WINDOW_DAYS},
        "note": "R7 冲突状态卡（v2 P1）：位置分位区×趋势×熊形态→信号优先级；参数未经样本外验证，待 P2-12 校准，首批 2 次真实触发人工复核",
    }


def compute_global_dominance(us_macro_data, cn_macro_data=None, fx_rates=None, cn_supplement=None, fx_sina=None, r7_has_catalyst=None, today=None):
    """F3④ 全球主导权仲裁（2026-10-23 主人裁决 B1+豁免，松子实现）

    三阈值（满足 ≥2 → veto=True）：
      a. 加息斜率：fed_funds 近3月变化×4 年化 > 300bp；或 QT：fed_balance 近3月降幅 > 0.5%
      b. 汇率阀门：USDCNY 年贬 > 4.5% 且（外储3月连降 或 CNH-CNY价差超阈）——北向代理已废弃（无数据源）
      c. 信用形态：M2 同比近2月上行 且 M2-社融存量同比裂口 > 2pp（宽货币紧信用空转）
    豁免：R7 has_catalyst=true → 保留 A 段左侧布局（其余加仓仍冻结），注记可观测。
    数据缺失腿按"未知"处理（不计满足），b 腿三元态照常输出供前端展示。
    """
    from datetime import datetime as _dt, timedelta as _td
    today = today or _dt.now().date()
    us_hist = (us_macro_data or {}).get("history", {}) or {}

    def _us_series(name):
        rows = []
        for r in (us_hist.get(name) or []):
            try:
                rows.append((str(r.get("date")), float(r.get("value"))))
            except (TypeError, ValueError, AttributeError):
                continue
        return rows

    # ---------- a. 加息斜率 / QT ----------
    a_met = False
    a_detail = []
    slope_bp = None
    ff = _us_series("fed_funds")
    if len(ff) >= 4:
        slope_bp = round((ff[-1][1] - ff[-4][1]) * 100 * 4, 1)  # 月频3月差×4年化，%→bp
        if slope_bp > F3_DOM_HIKE_SLOPE_BP_YR:
            a_met = True
        a_detail.append(f"加息斜率年化 {slope_bp:.0f}bp（阈值 300bp）")
    else:
        a_detail.append("fed_funds 历史不足 4 个月")
    qt_pct = None
    fb = _us_series("fed_balance")
    if len(fb) >= 2:
        try:
            end_d, end_v = fb[-1]
            cutoff = (_dt.strptime(end_d, "%Y-%m-%d") - _td(days=90)).strftime("%Y-%m-%d")
            base = max((x for x in fb if x[0] <= cutoff), key=lambda x: x[0], default=None)
            if base:
                qt_pct = round((end_v - base[1]) / base[1] * 100, 3)
                if qt_pct < F3_DOM_QT_3M_PCT:
                    a_met = True
                a_detail.append(f"fed_balance 近3月 {qt_pct:+.2f}%（QT 阈值 {F3_DOM_QT_3M_PCT}%）")
        except Exception:
            a_detail.append("fed_balance 日期解析失败")
    else:
        a_detail.append("fed_balance 历史不足")

    # ---------- b. 汇率阀门（三元态：开/关/采集中） ----------
    def _fx_val(row):
        if isinstance(row, dict):
            for k in ("close", "value", "price"):
                if row.get(k) is not None:
                    return float(row[k])
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            return float(row[1])
        return None

    deprec_pct = None
    fx_rates = fx_rates or {}
    # 数据源优先级：①us_macro.history.usdcny（既有 SSOT，250 条日频）②fx_rates_em.json（Codex 采集，待网络环境）
    uc = _us_series("usdcny")
    uc_src = "us_macro.history.usdcny"
    if len(uc) < F3_USDCNY_ANNUAL_WINDOW_DAYS:
        uc = [ (str(r.get("date")), _fx_val(r)) for r in (fx_rates.get("usdcny_daily") or []) ]
        uc = [x for x in uc if x[1] is not None]
        uc_src = "fx_rates_em.usdcny_daily"
    if len(uc) >= F3_USDCNY_ANNUAL_WINDOW_DAYS:
        try:
            now_v, yr_v = uc[-1][1], uc[-F3_USDCNY_ANNUAL_WINDOW_DAYS][1]
            if now_v and yr_v:
                deprec_pct = round((now_v - yr_v) / yr_v * 100, 3)
        except Exception:
            deprec_pct = None
    # CNH-CNY 价差判定（投资分析参数 2026-10-23 CIO 转达，验收按此桩测）：
    # (USDCNH−USDCNY)/USDCNY > 0.4%(40bp) 连续 5 交易日，或单日 > 0.7%(70bp) → 触发
    # 源① fx_rates_sina.json daily 自积累（cny_mid/cnh_mid/spread_bp）②fx_rates_em 双腿按日期对齐（备）
    spread_rows = []
    for r in ((fx_sina or {}).get("daily") or []):
        try:
            spread_rows.append((str(r.get("date")), float(r.get("spread_bp"))))
        except (TypeError, ValueError, AttributeError):
            continue
    if not spread_rows:
        _cny = {}
        for x in (fx_rates.get("usdcny_daily") or []):
            try: _cny[str(x.get("date"))] = _fx_val(x)
            except Exception: pass
        _cnh = {}
        for x in (fx_rates.get("usdcnh_daily") or []):
            try: _cnh[str(x.get("date"))] = _fx_val(x)
            except Exception: pass
        for d in sorted(set(_cny) & set(_cnh)):
            if _cny[d] and _cnh[d]:
                spread_rows.append((d, (_cnh[d] - _cny[d]) / _cny[d] * 10000))
    spread_latest_bp = None
    spread_triggered = None      # None=无数据
    spread_detail = "CNH-CNY 无数据源"
    if spread_rows:
        spread_latest_bp = round(spread_rows[-1][1], 1)
        single_hit = spread_rows[-1][1] > F3_CNH_CNY_SINGLE_BP
        streak_hit = None
        if len(spread_rows) >= F3_CNH_CNY_STREAK_DAYS:
            streak_hit = all(v > F3_CNH_CNY_STREAK_BP for _, v in spread_rows[-F3_CNH_CNY_STREAK_DAYS:])
        spread_triggered = bool(single_hit or streak_hit)
        spread_detail = (f"最新价差 {spread_latest_bp:+.0f}bp（阈值：单日>{F3_CNH_CNY_SINGLE_BP}bp 或 连续{F3_CNH_CNY_STREAK_DAYS}日>{F3_CNH_CNY_STREAK_BP}bp）；"
                         f"序列 {len(spread_rows)} 日" + ("（连判已可判）" if streak_hit is not None else "（不足5日，连判暂不可判）"))
    spread_met = spread_triggered
    reserve_decline = None
    fr = ((cn_supplement or {}).get("fx_reserves")) or []
    if len(fr) >= F3_DOM_FX_RESERVE_DECLINE_M + 1:
        try:
            vals = [float(x.get("value_billion_usd")) for x in fr[-(F3_DOM_FX_RESERVE_DECLINE_M + 1):]]
            reserve_decline = all(vals[i + 1] < vals[i] for i in range(len(vals) - 1))
        except (TypeError, ValueError):
            reserve_decline = None
    if deprec_pct is not None:
        b_met = bool(deprec_pct > F3_DOM_USDCNY_DEPREC_PCT and (reserve_decline or spread_met))
        b_status = "ok"
    else:
        b_met = False
        b_status = "pending_data"  # USDCNY 采集缺位（东财需网络环境，Codex 修复中）→ 未知，不计满足

    # ---------- c. 信用形态（宽货币紧信用空转） ----------
    cn_hist = (cn_macro_data or {}).get("history", {}) or {}

    def _cn_series(name):
        rows = []
        for r in (cn_hist.get(name) or []):
            try:
                rows.append((str(r.get("date")), float(r.get("value"))))
            except (TypeError, ValueError, AttributeError):
                continue
        return rows

    m2 = _cn_series("m2_yoy")
    sf = _cn_series("social_financing_stock")
    m2_up = None
    if len(m2) >= 3:
        m2_up = bool(m2[-1][1] > m2[-3][1])
    gap_pp = None
    if m2 and len(sf) >= 13:
        if abs(sf[-1][1]) < 50:  # 已是同比口径
            sf_yoy = sf[-1][1]
        else:                    # 存量水平 → 12 个月前同比
            sf_yoy = (sf[-1][1] / sf[-13][1] - 1) * 100 if sf[-13][1] else None
        if sf_yoy is not None:
            gap_pp = round(m2[-1][1] - sf_yoy, 2)
    c_met = bool(m2_up and gap_pp is not None and gap_pp > F3_DOM_CREDIT_GAP_PP)

    satisfied_n = int(bool(a_met)) + int(bool(b_met)) + int(bool(c_met))
    veto = satisfied_n >= 2
    exemption_active = bool(veto and r7_has_catalyst)
    valve_open = (None if b_status == "pending_data" else bool(deprec_pct > F3_DOM_USDCNY_DEPREC_PCT and (reserve_decline or spread_met)))

    return {
        "as_of": today.isoformat() if hasattr(today, "isoformat") else str(today),
        "thresholds": {
            "a_hike_slope_or_qt": {
                "met": bool(a_met), "slope_bp_annualized": slope_bp, "qt_3m_pct": qt_pct,
                "detail": "；".join(a_detail),
            },
            "b_fx_valve": {
                "met": bool(b_met), "data_status": b_status, "valve_open": valve_open,
                "usdcny_annual_deprec_pct": deprec_pct, "threshold_deprec_pct": F3_DOM_USDCNY_DEPREC_PCT,
                "usdcny_source": (uc_src if deprec_pct is not None else None),
                "fx_reserve_3m_decline": reserve_decline,
                "cnh_cny_spread_latest_bp": spread_latest_bp, "cnh_cny_spread_triggered": spread_triggered,
                "cnh_cny_params": {"streak_bp": F3_CNH_CNY_STREAK_BP, "streak_days": F3_CNH_CNY_STREAK_DAYS, "single_bp": F3_CNH_CNY_SINGLE_BP},
                "cnh_cny_detail": spread_detail,
                "note": "USDCNY年贬>4.5% ∧（外储3月连降 ∨ CNH-CNY价差触发）；北向代理已废弃（无数据源）",
            },
            "c_credit_form": {
                "met": bool(c_met), "m2_yoy_2m_up": m2_up, "m2_sf_gap_pp": gap_pp,
                "threshold_gap_pp": F3_DOM_CREDIT_GAP_PP,
            },
        },
        "satisfied_n": satisfied_n,
        "veto": bool(veto),
        "veto_cap": (F3_DOM_VETO_CAP if veto else None),
        "r7_has_catalyst": r7_has_catalyst,
        "exemption_active": exemption_active,
        "note": "F3④ 全球主导权仲裁（主人裁决 2026-10-23 B1+豁免）：≥2/3 满足→仓位上限25%+冻结全部加仓触发器；R7 has_catalyst=true 时保留 A 段左侧布局（其余加仓仍冻结）。数据缺失腿按未知处理不计满足。",
    }


def apply_fed_layer(fed_watch, us_macro, prev_fed_state, manual_cfg, today=None):
    """[P1-8 美联储双层 2026-10-18 投资分析裁决终版]

    预期层：累计加息概率(1-(1-p1)(1-p2))>60% 且 10Y>5.2% 且趋势限定
            （20日均线上行 或 60日新高）→ 仓位压至 32%，禁加成长股（创业板+科创50）
    事件层自动轨：目标区间较上期上移 → 软事件层压 32%（不钉死）
    事件层人工轨：会后人工确认 hawkish=true → 钉 25%；2 交易日时限超时维持软层 32%
    降级链：fed_watch.json 缺失/stale(>4天) → 继承前日状态（裁决③）
    冷却：层解除后 180 天内不重复进入（Fed_D*，复用 P1-7 cooldown 模式）
    返回 (fed_state, fed_cap)；fed_cap=None 表示不限制。
    """
    from datetime import date as _date, timedelta as _td
    today = today or _date.today()
    prev = prev_fed_state or {}
    prev_layer = prev.get("layer", "none")
    # ---- 冷却期检查（Fed_D*）----
    cd_until = prev.get("cooldown_until")
    in_cooldown = False
    if cd_until:
        try:
            in_cooldown = today <= _date.fromisoformat(cd_until)
        except Exception:
            in_cooldown = False

    # ---- 输入降级：fed_watch.json 缺失/stale → 继承前日状态 ----
    fw_ok = False
    fw_stale_reason = ""
    if fed_watch and fed_watch.get("as_of"):
        try:
            as_of = _date.fromisoformat(str(fed_watch["as_of"]))
            if (today - as_of).days <= P18_STALE_DAYS:
                fw_ok = True
            else:
                fw_stale_reason = f"fed_watch.json 数据滞后 {(today - as_of).days} 天"
        except Exception:
            fw_stale_reason = "fed_watch.json as_of 解析失败"
    else:
        fw_stale_reason = "fed_watch.json 缺失"

    base_state = {
        "layer": "none", "cap": None,
        "cumulative_hike_prob": None, "yield_10y": None,
        "triggers": [], "as_of": str(today),
        "cooldown_until": cd_until,
        "growth_ban": [], "note": "",
        "source": "cme-fedwatch + FRED DGS10 + us_macro fed_funds（F3①加息周期检测）",
        "hiking_cycle": False,
        "fomc_auto": False,
    }
    if not fw_ok:
        # 继承前日（降级链），显式标注
        st = dict(base_state)
        st["layer"] = prev_layer if prev_layer in ("expect", "event_soft", "event_hard") else "none"
        st["cap"] = prev.get("cap")
        st["cumulative_hike_prob"] = prev.get("cumulative_hike_prob")
        st["note"] = f"【降级】{fw_stale_reason}，继承前日层状态（{st['layer']}）"
        st["growth_ban"] = prev.get("growth_ban", [])
        cap = st["cap"] if st["layer"] != "none" else None
        return st, cap

    # ---- 输入解析 ----
    cum = fed_watch.get("cumulative_hike_prob")
    tr = fed_watch.get("target_range") or []
    tr_prev = fed_watch.get("prev_target_range") or []
    # 10Y 收益率与趋势
    y10_hist = ((us_macro or {}).get("history", {}) or {}).get("yield_10y", []) or []
    y10_now = None
    ma20_up = False
    high_60 = False
    if len(y10_hist) >= 25:
        vals = [float(x["value"]) for x in y10_hist if x.get("value") is not None]
        if len(vals) >= 65:
            y10_now = vals[-1]
            ma20_today = sum(vals[-20:]) / 20
            ma20_5d_ago = sum(vals[-25:-5]) / 20
            ma20_up = ma20_today > ma20_5d_ago
            high_60 = y10_now >= max(vals[-60:])

    triggers = []
    # ---- F3①-1: hiking_cycle 加息周期检测 ----
    hiking_cycle = False
    hiking_cycle_detail = ""
    ff_hist = ((us_macro or {}).get("history", {}) or {}).get("fed_funds", []) or []
    if len(ff_hist) >= F3_HIKING_CYCLE_WINDOW_MONTHS + 1:
        try:
            ff_now = float(ff_hist[-1]["value"])
            ff_3m_ago = float(ff_hist[-(F3_HIKING_CYCLE_WINDOW_MONTHS + 1)]["value"])
            if ff_now > ff_3m_ago + F3_HIKING_CYCLE_THRESHOLD_BP / 100.0:
                hiking_cycle = True
                hiking_cycle_detail = (
                    f"fed_funds {ff_now:.2f}% > 3月前 {ff_3m_ago:.2f}% + "
                    f"{F3_HIKING_CYCLE_THRESHOLD_BP}bp（加息周期确认）"
                )
        except Exception:
            pass

    # ---- F3①-2: FOMC 双保险（会议已过 + CME 概率>90%）----
    fomc_auto = False
    fomc_auto_detail = ""
    meetings = (fed_watch or {}).get("meetings", []) or []
    for m in meetings:
        try:
            m_date = m.get("date", "")
            m_hike = m.get("hike_prob")
            if m_date and m_hike is not None and float(m_hike) >= F3_FOMC_AUTO_THRESHOLD:
                if str(m_date) <= str(today):
                    fomc_auto = True
                    fomc_auto_detail = f"FOMC {m_date} 已过 + hike_prob={float(m_hike)*100:.0f}%≥90%（双保险置位）"
                    break
        except Exception:
            pass

    # ---- 事件层自动轨：目标区间上移 OR hiking_cycle OR FOMC 双保险 ----
    event_auto = False
    if len(tr) == 2 and len(tr_prev) == 2:
        try:
            event_auto = (float(tr[0]) > float(tr_prev[0]) or float(tr[1]) > float(tr_prev[1]))
        except Exception:
            event_auto = False
    # F3①: 三源合并（区间上移 | hiking_cycle | FOMC双保险）
    event_auto = event_auto or hiking_cycle or fomc_auto
    if event_auto:
        _ev_reasons = []
        if len(tr) == 2 and len(tr_prev) == 2:
            try:
                if float(tr[0]) > float(tr_prev[0]) or float(tr[1]) > float(tr_prev[1]):
                    _ev_reasons.append("目标区间上移")
            except Exception:
                pass
        if hiking_cycle:
            _ev_reasons.append(hiking_cycle_detail)
        if fomc_auto:
            _ev_reasons.append(fomc_auto_detail)
        triggers.append(" | ".join(_ev_reasons) if _ev_reasons else "事件层触发")

    # ---- 事件层人工轨：hawkish 确认（2 交易日时限）----
    hw = (manual_cfg or {}).get("fed_hawkish", {}) or {}
    manual_hard = False
    manual_expired = False
    if hw.get("confirmed") is True and hw.get("confirmed_at"):
        try:
            c_at = _date.fromisoformat(str(hw["confirmed_at"]))
            # 时限：P18_MANUAL_CONFIRM_LIMIT_DAYS 个交易日 ≈ ×2 自然日（含周末保守上沿）
            if (today - c_at).days <= P18_MANUAL_CONFIRM_LIMIT_DAYS * 2:
                manual_hard = True
                triggers.append("人工确认鹰派")
            else:
                manual_expired = True
        except Exception:
            pass

    # ---- 预期层判定 [F3③ A1 主人裁决 2026-10-23：任一触发] ----
    # 概率单路：cum>85%（9/16 漏检根因=AND 被 10Y 差 8bp 挡住；A1 下会前即预警）
    # 利率路径：10Y>5.2% 且趋势确认（保留趋势过滤防平台期毛刺，5.2% 不动）
    expect_hit = False
    if cum is not None and cum > P18_HIKE_PROB_THRESHOLD:
        expect_hit = True
        triggers.append(f"累计加息概率{cum*100:.0f}%>85%（概率单路·A1）")
    if (not expect_hit) and y10_now is not None and y10_now > P18_YIELD_10Y_THRESHOLD and (ma20_up or high_60):
        expect_hit = True
        triggers.append(
            f"10Y {y10_now:.2f}%>5.2% + " + ("20MA上行" if ma20_up else "60日新高") + "（利率路径·A1）")

    # ---- 层裁决（事件层优先于预期层；冷却期不重复进入）----
    st = dict(base_state)
    st["cumulative_hike_prob"] = cum
    st["yield_10y"] = y10_now
    st["triggers"] = triggers
    st["hiking_cycle"] = hiking_cycle
    st["fomc_auto"] = fomc_auto

    if manual_hard:
        st["layer"], st["cap"] = "event_hard", P18_EVENT_HARD_CAP
        st["note"] = "事件层人工轨：会后人工确认鹰派 → 仓位钉 25%"
    elif event_auto or manual_expired:
        # [CIO验收D1修复] 裁决语义：人工确认超时（>2交易日）维持软事件层32%，防人工位空转；
        # 解除路径=人工撤销 config_overrides.fed_hawkish 标志（撤销后次采集周期落 none+冷却）
        st["layer"], st["cap"] = "event_soft", P18_EVENT_SOFT_CAP
        if manual_expired and not event_auto:
            st["note"] = ("事件层维持：人工确认超时（>2交易日）→ 维持软压 32%（防人工位空转）；"
                          "解除路径=人工撤销 fed_hawkish 标志")
            triggers.append("人工确认超时·维持软层")
        else:
            note = "事件层自动轨：检测到目标区间上移 → 软压 32%（不钉死）"
            if manual_expired:
                note += "；人工确认超时（>2交易日），维持软层"
            st["note"] = note
    elif expect_hit:
        st["layer"], st["cap"] = "expect", P18_EXPECT_CAP
        st["note"] = "预期层：加息概率+长端利率双高 → 仓位上限 32%，禁加成长股"
    else:
        st["layer"], st["cap"] = "none", None
        # 层解除 → 若此前在层内，落冷却 180 天
        if prev_layer in ("expect", "event_soft", "event_hard"):
            st["cooldown_until"] = str(today + _td(days=P18_COOLDOWN_DAYS))
            st["note"] = f"Fed 层解除，进入 {P18_COOLDOWN_DAYS} 天冷却（至 {st['cooldown_until']}）"

    # 冷却期防护：非事件硬轨下，冷却内不重复进入 expect/event_soft
    if in_cooldown and st["layer"] in ("expect", "event_soft"):
        st["layer"], st["cap"] = "none", None
        st["note"] = f"Fed_D* 冷却期内（至 {cd_until}），不重复触发"
        st["growth_ban"] = []
        st["cooldown_until"] = cd_until
        return st, None

    if st["layer"] in ("expect", "event_soft", "event_hard"):
        st["growth_ban"] = ["创业板指", "科创50"]
        st["cooldown_until"] = None
    return st, st["cap"]



# ============================================================
# P1-6: 价格趋势过滤 (2026-10-18)
# ============================================================
def fetch_trend_index_daily():
    """P1-6: 拉取中证全指日K（腾讯源，CI 已验证可用）。失败返回 None。"""
    import akshare as ak
    df = safe_ak_call(ak.stock_zh_index_daily_tx, symbol=P16_INDEX_SYMBOL)
    if df is None or len(df) < P16_MOMENTUM_LOOKBACK + 1:
        return None
    return df


def compute_trend_state(df, prev_trend_state, today):
    """P1-6: 价格趋势四态判定（投资分析终版规格 2026-10-18）

    判定口径：
    ① 价格位置：最新收盘 vs MA120
    ② 均线方向：今日 MA120 vs 20 交易日前 MA120（防单日噪音）
    ③ 四态组合：价上+均上=strong_bull 强多头 / 价上+均下=weak_bull 弱多头
                价下+均上=weak_bear 弱空头 / 价下+均下=strong_bear 强空头
    ④ 12 月动量 = 现价/250 日前收盘 − 1，阈值 0（TSMOM，缓冲由组合逻辑承担）

    断连容错：采集失败时继承前日趋势（同 P1-7 外部指标继承模式）。
    Returns: (trend_state, trend_note)
    """
    if df is None:
        if prev_trend_state and prev_trend_state.get("trend") not in (None, "unknown"):
            inherited = dict(prev_trend_state)
            inherited["source"] = "inherited"
            return inherited, "指数日K采集失败，继承前日趋势"
        return {"trend": "unknown", "source": "unavailable"}, "指数日K不可用，趋势未知"

    closes = df["close"].astype(float).values
    ma = df["close"].astype(float).rolling(P16_MA_PERIOD).mean().values
    close_now = closes[-1]
    ma_now = ma[-1]
    ma_prev = ma[-1 - P16_MA_DIRECTION_LOOKBACK]
    price_above = close_now > ma_now
    ma_up = ma_now > ma_prev  # 均线方向：保留计算供展示，不参与判定（投资分析 10-18 裁决）

    # 判定矩阵（投资分析 2026-10-18 裁决：动量参与判定，非仅展示）
    # 价上+动量正=强多头；价上+动量负=弱多头（熊市反弹风险）
    # 价下+动量正=弱空头（长期趋势未破）；价下+动量负=强空头
    momentum = close_now / closes[-1 - P16_MOMENTUM_LOOKBACK] - 1
    if price_above and momentum > 0:
        trend, annotation = "strong_bull", ""
    elif price_above and momentum <= 0:
        trend, annotation = "weak_bull", "熊市反弹风险"
    elif not price_above and momentum > 0:
        trend, annotation = "weak_bear", "长期趋势未破"
    else:
        trend, annotation = "strong_bear", ""

    trend_state = {
        "trend": trend,
        "annotation": annotation,
        "close": round(float(close_now), 2),
        "ma120": round(float(ma_now), 2),
        "ma120_direction": "up" if ma_up else "down",
        "momentum_12m": round(float(momentum), 4),
        "as_of": str(df["date"].iloc[-1])[:10],
        "source": f"tencent_tx {P16_INDEX_SYMBOL}"
    }
    label = {"strong_bull": "强多头", "weak_bull": "弱多头",
             "weak_bear": "弱空头", "strong_bear": "强空头"}[trend]
    note = (f"中证全指 {close_now:.0f} vs MA120 {ma_now:.0f}（价{'上' if price_above else '下'}）"
            f"+ 12月动量 {momentum:+.1%} → {label}" + (f"（{annotation}）" if annotation else ""))
    return trend_state, note


# ============================================================
# 六维择时评分计算引擎
# ============================================================

class TimingScoreEngine:
    """
    A股六维择时评分系统
    ==================
    六维度:
      1. 估值 (valuation)       - 权重 18%
      2. 宏观流动性 (liquidity) - 权重 17%
      3. 股债性价比 (equity_bond) - 权重 18%
      4. 资金面 (capital_flow)  - 权重 15%
      5. 市场情绪 (sentiment)   - 权重 18%
      6. 微观结构 (micro_structure) - 权重 14%
    """

    def __init__(self):
        self.ak = None
        self.cache = {}  # 缓存已获取的数据

    def init_akshare(self):
        """延迟加载 akshare"""
        if self.ak is None:
            import akshare as ak
            self.ak = ak
        return self.ak

    # ----------------------------------------------------------
    # 维度1: 估值 (valuation)
    # ----------------------------------------------------------
    def calc_valuation(self) -> dict:
        """
        估值维度
        指标:
          - 沪深300 PE分位 (近10年): 权重35%
          - 沪深300 PB分位 (近10年): 权重20%
          - 巴菲特指标: 权重25%
          - 破净率: 权重20%
        """
        logger.info("  [维度1/6] 估值计算...")
        ak = self.init_akshare()
        indicators = {}

        # --- 沪深300 PE 分位 ---
        hs300_pe_val, hs300_pe_pct = None, None
        try:
            pe_df = safe_ak_call(ak.stock_index_pe_lg, symbol="沪深300")
            if pe_df is not None and not pe_df.empty:
                pe_col = '滚动市盈率'
                if pe_col in pe_df.columns:
                    pe_series = pe_df.set_index('日期')[pe_col].dropna()
                    pe_series.index = pd.to_datetime(pe_series.index)
                    # 近10年分位
                    recent = pe_series.tail(2500)  # ~10年
                    hs300_pe_val = safe_float(recent.iloc[-1])
                    hs300_pe_pct = percentile_rank(recent, hs300_pe_val)
                    logger.info(f"      沪深300 PE(TTM): {hs300_pe_val}, 10Y分位: {hs300_pe_pct}%")
        except Exception as e:
            logger.warning(f"      PE分位计算失败: {e}")

        # --- 沪深300 PB 分位 ---
        hs300_pb_val, hs300_pb_pct = None, None
        try:
            pb_df = safe_ak_call(ak.stock_index_pb_lg, symbol="沪深300")
            if pb_df is not None and not pb_df.empty:
                pb_col = '市净率'
                if pb_col in pb_df.columns:
                    pb_series = pb_df.set_index('日期')[pb_col].dropna()
                    pb_series.index = pd.to_datetime(pb_series.index)
                    recent = pb_series.tail(2500)
                    hs300_pb_val = safe_float(recent.iloc[-1])
                    hs300_pb_pct = percentile_rank(recent, hs300_pb_val)
                    logger.info(f"      沪深300 PB: {hs300_pb_val}, 10Y分位: {hs300_pb_pct}%")
        except Exception as e:
            logger.warning(f"      PB分位计算失败: {e}")

        # PE 分位评分 (高估 → 高分 = 危险信号)
        pe_score = score_from_percentile(hs300_pe_val, hs300_pe_pct, reverse=True)
        pb_score = score_from_percentile(hs300_pb_val, hs300_pb_pct, reverse=True)

        indicators["hs300_pe_percentile"] = {
            "name": "沪深300 PE分位",
            "value": safe_float(hs300_pe_pct) if hs300_pe_pct else None,
            "unit": "%分位",
            "score": pe_score,
            "sub_weight": 40,
            "bottom_threshold": "<10%",
            "top_threshold": ">90%"
        }

        indicators["hs300_pb_percentile"] = {
            "name": "沪深300 PB分位",
            "value": safe_float(hs300_pb_pct) if hs300_pb_pct else None,
            "unit": "%分位",
            "score": pb_score,
            "sub_weight": 0,
            "note": "【参考】PB分位仅作辅助参考",
            "bottom_threshold": "<10%",
            "top_threshold": ">90%"
        }

        # --- 巴菲特指标 (总市值/GDP) ---
        buffett_val = self._calc_buffett_ratio()
        buffett_score = self._score_buffett(buffett_val)
        indicators["buffett_ratio"] = {
            "name": "巴菲特指标",
            "value": safe_float(buffett_val) if buffett_val else None,
            "unit": "%",
            "score": buffett_score,
            "sub_weight": 35,
            "bottom_threshold": "<70%",
            "top_threshold": ">100%"
        }

        # --- 破净率 ---
        break_net_rate = self._calc_break_net_rate()
        break_score = self._score_break_net(break_net_rate)
        indicators["break_net_rate"] = {
            "name": "破净率",
            "value": safe_float(break_net_rate) if break_net_rate else None,
            "unit": "%",
            "score": break_score,
            "sub_weight": 25,
            "bottom_threshold": ">10%",
            "top_threshold": "<3%"
        }

        # 维度总分 = 加权子指标得分
        dim_score = self._weighted_score(indicators)
        signal = self._valuation_signal(dim_score, hs300_pe_pct)

        return {
            "name": "估值",
            "score": dim_score,
            "weight": 18,
            "weighted_score": round(dim_score * 0.18, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_buffett_ratio(self):
        """巴菲特指标: A股总市值/GDP"""
        try:
            ak = self.init_akshare()
            # 尝试获取A股总市值
            # 方法: 使用 stock_market_cap 或手动估算
            # akshare 有 macro_china_stock_market_cap (如果可用)
            # 简化方案: 使用沪深300总市值近似 / 最新GDP
            # 更可靠: 从已加载的数据读取

            # 尝试从上期数据获取
            prev = load_json("timing_scores.json")
            prev_val = prev.get("dimensions", {}).get("valuation", {}).get("indicators", {}).get("buffett_ratio", {}).get("value")

            # 尝试从 akshare 获取中国 GDP
            gdp_df = safe_ak_call(ak.macro_china_gdp)
            if gdp_df is not None and not gdp_df.empty:
                latest_gdp = safe_float(gdp_df['国内生产总值-绝对值'].iloc[0])
                if latest_gdp:
                    # 单位: 亿元 → 万亿
                    gdp_wan_yi = latest_gdp / 10000
                    # 粗略估算A股总市值 ≈ 沪深300市值 * 2.5
                    # 或从其他数据源获取
                    # 使用上期比值作为近似 (每季度更新一次)
                    if prev_val:
                        logger.info(f"      巴菲特指标: 使用上期值 {prev_val}% (待季度更新)")
                        return prev_val
        except Exception as e:
            logger.warning(f"      巴菲特指标计算异常: {e}")

        # fallback
        prev = load_json("timing_scores.json")
        return prev.get("dimensions", {}).get("valuation", {}).get("indicators", {}).get("buffett_ratio", {}).get("value")

    def _score_buffett(self, val):
        if val is None:
            return 50
        if val < 70:
            return 30  # 低估
        elif val < 85:
            return 45
        elif val < 100:
            return 55
        elif val < 120:
            return 70
        else:
            return 85  # 高估

    def _calc_break_net_rate(self):
        """破净率: 破净股数量/总股数"""
        try:
            ak = self.init_akshare()
            df = safe_ak_call(ak.stock_a_below_net_asset_statistics)
            if df is not None and not df.empty and 'below_net_asset_ratio' in df.columns:
                ratio = safe_float(df['below_net_asset_ratio'].iloc[-1])
                if ratio is not None:
                    pct = round(ratio * 100, 1)  # 0.0812 → 8.12%
                    below_count = int(df['below_net_asset'].iloc[-1])
                    total_count = int(df['total_company'].iloc[-1])
                    logger.info(f"      破净率: {pct}% (破净{below_count}只/{total_count}只)")
                    return pct
        except Exception as e:
            logger.warning(f"      破净率获取异常: {e}")
        # fallback: 上期数据
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("valuation", {}).get("indicators", {}).get("break_net_rate", {}).get("value")
        if prev_val:
            logger.info(f"      破净率: 使用上期值 {prev_val}%")
        return prev_val

    def _score_break_net(self, val):
        """破净率评分: 破净率高 → 市场低迷 → 底部信号 → 低分(安全)"""
        if val is None:
            return 50
        if val > 10:
            return 25  # 大量破净 = 底部
        elif val > 7:
            return 40
        elif val > 5:
            return 50
        elif val > 3:
            return 60
        else:
            return 75  # 极少破净 = 市场高位

    def _valuation_signal(self, score, pe_pct):
        if score >= 80:
            return "高估/极端偏热"
        elif score >= 65:
            return "偏高/接近谨慎区"
        elif score >= 45:
            return "中性"
        elif score >= 30:
            return "偏低/有吸引力"
        else:
            return "极度低估"

    # ----------------------------------------------------------
    # 维度2: 宏观流动性 (liquidity)
    # ----------------------------------------------------------
    def calc_liquidity(self) -> dict:
        """
        宏观流动性维度 (v4.1)
        指标:
          - 社融增速趋势(加速度法): 权重40%
          - 利率水平(国内分档): 权重35%
          - M1-M2剪刀差: 参考(权重0%)
          - 美联储政策: 权重25%
        """
        logger.info("  [维度2/6] 宏观流动性...")
        ak = self.init_akshare()
        indicators = {}

        # --- 社融增速趋势 ---
        sf_score, sf_value = self._calc_social_financing()
        # value 为 TTM 同比数值，由 sync_timing_scores_history 从真源 history 末点写入；
        # 此处拿不到则保持 None，严禁伪造。结论文本独立放 value_label。
        indicators["social_financing_trend"] = {
            "name": "社融增速趋势",
            "value": None,
            "value_label": sf_value or None,
            "score": sf_score,
            "sub_weight": 40,
            "bottom_signal": "触底回升",
            "top_signal": "见顶回落"
        }

        # --- 利率水平 ---
        rate_score, rate_value = self._calc_interest_rate()
        indicators["interest_rate"] = {
            "name": "利率水平",
            "value": rate_value or "数据待更新",
            "score": rate_score,
            "sub_weight": 35,
            "bottom_signal": "降息周期",
            "top_signal": "加息周期"
        }

        # --- M1-M2剪刀差 ---
        m1m2_score, m1m2_value = self._calc_m1_m2_scissors()
        indicators["m1_m2_scissors"] = {
            "name": "M1-M2剪刀差",
            "value": m1m2_value or "数据待更新",
            "score": m1m2_score,
            "sub_weight": 0,
            "note": "【参考】M1-M2剪刀差仅作辅助参考",
            "bottom_signal": "从-8%收窄",
            "top_signal": ">+5%扩张"
        }

        # --- 美联储政策 ---
        fed_score, fed_value = self._calc_fed_policy()
        indicators["fed_policy"] = {
            "name": "美联储政策",
            "value": fed_value or "中性",
            "score": fed_score,
            "sub_weight": 25,
            "bottom_signal": "降息周期",
            "top_signal": "加息周期"
        }

        dim_score = self._weighted_score(indicators)
        signal = "宽松" if dim_score < 40 else "中性偏松" if dim_score < 50 else "中性" if dim_score < 60 else "中性偏紧" if dim_score < 75 else "收紧"

        return {
            "name": "宏观流动性",
            "score": dim_score,
            "weight": 15,
            "weighted_score": round(dim_score * 0.15, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_social_financing(self):
        """社融增速趋势 (v4.1 加速度法)
        铁律: 低分=紧缩=便宜(恐惧); 高分=宽松=已反映(贪婪)
        """
        try:
            ak = self.init_akshare()
            sf_df = safe_ak_call(ak.macro_china_shrzgm)
            if sf_df is not None and not sf_df.empty:
                col = '社会融资规模增量'
                if col in sf_df.columns:
                    sf_series = sf_df[col].dropna()
                    if len(sf_series) >= 6:
                        # v4.1: 加速度法 — 环比动量
                        recent_3m = sf_series.tail(3).mean()
                        prev_3m = sf_series.iloc[-6:-3].mean()
                        delta = recent_3m - prev_3m  # 加速度

                        # 评分: 低分=紧缩=便宜(恐惧); 高分=宽松=已反映(贪婪)
                        if delta > 2 and recent_3m > 0:
                            score = 75  # 强宽松
                            label = "强宽松"
                        elif delta > 0:
                            score = 60  # 宽松趋势
                            label = "宽松加速"
                        elif delta > -2 and recent_3m > 0:
                            score = 45  # 宽松见顶
                            label = "宽松见顶"
                        else:
                            score = 25  # 紧缩
                            label = "紧缩"

                        val_str = f"{label}(Δ{delta:.1f})"
                        logger.info(f"      社融加速度: Δ={delta:.1f}, 近3月={recent_3m:.0f} → {label} score={score}")
                        return score, val_str
        except Exception as e:
            logger.warning(f"      社融计算异常: {e}")

        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("social_financing_trend", {}).get("value", "未知")
        prev_score = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("social_financing_trend", {}).get("score", 50)
        return prev_score, prev_val

    def _calc_interest_rate(self):
        """利率水平评分 (v4.1 国内分档)
        铁律: 低分=紧缩=便宜(恐惧); 高分=宽松=已反映(贪婪)
        """
        try:
            ak = self.init_akshare()
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is not None and not bond_df.empty and 'close' in bond_df.columns:
                bond_df['date'] = pd.to_datetime(bond_df['date'])
                bond_series = bond_df.set_index('date')['close'].dropna()
                cn10y = safe_float(bond_series.iloc[-1])

                if cn10y:
                    # v4.1: 国内分档
                    # 低分=紧缩=便宜(恐惧); 高分=宽松=已反映(贪婪)
                    if cn10y <= 1.5:
                        score = 70  # 历史极底=过热
                        label = "历史极底/过热"
                    elif cn10y <= 1.8:
                        score = 60  # 极低=偏热
                        label = "极低/偏热"
                    elif cn10y <= 2.2:
                        score = 50  # 偏低=中性
                        label = "偏低/中性"
                    elif cn10y <= 2.8:
                        score = 40  # 中位
                        label = "中位"
                    elif cn10y <= 3.2:
                        score = 30  # 偏高=紧缩=便宜
                        label = "偏高/紧缩"
                    else:
                        score = 20  # 极度紧缩=逆向机会
                        label = "极度紧缩/逆向机会"

                    val_str = f"{label}({cn10y:.2f}%)"
                    logger.info(f"      10Y国债: {cn10y:.2f}%, 分档: {label}, score={score}")
                    return score, val_str
        except Exception as e:
            logger.warning(f"      利率计算异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {})
                .get("interest_rate", {}).get("score", 50), "数据待更新")

    def _calc_m1_m2_scissors(self):
        """M1-M2剪刀差 = M1同比 − M2同比（月频真值）。

        数据源: akshare macro_china_money_supply（央行口径，含 M1/M2 同比列）。
        旧源 macro_china_m2_yearly（金十）已停更，弃用。
        评分映射: 底部信号 -8% → 0 分；顶部信号 +5% → 100 分（线性）。
        """
        try:
            from cn_money_supply import fetch_m1_m2_scissors
            series = fetch_m1_m2_scissors()
            if series:
                latest = series[-1]
                v = latest["value"]
                score = int(max(0, min(100, round((v + 8.0) / 13.0 * 100))))
                value = f"{v}%({latest['date']})"
                logger.info(f"      M1-M2剪刀差: {value} (score={score})")
                return score, value
        except Exception as e:
            logger.warning(f"      M1-M2计算异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {})
                .get("m1_m2_scissors", {}).get("score", 50),
                prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {})
                .get("m1_m2_scissors", {}).get("value", "数据待更新"))

    def _calc_fed_policy(self):
        """美联储政策方向"""
        try:
            # 从 us_macro 获取联邦基金利率
            us = load_json("us_macro.json")
            ff = us.get("lagging", {}).get("fed_funds_rate", {}).get("value")
            if ff:
                # 解析利率字符串 "4.50%" → 4.5
                if isinstance(ff, str):
                    ff_num = float(ff.replace('%', ''))
                else:
                    ff_num = float(ff)

                # 判断政策方向
                if ff_num > 5.0:
                    score = 70  # 紧缩
                    val = "高利率维持"
                elif ff_num > 4.0:
                    score = 55  # 中性偏紧
                    val = "中性偏高"
                elif ff_num > 3.0:
                    score = 45  # 中性
                    val = "中性"
                elif ff_num > 2.0:
                    score = 35  # 宽松
                    val = "宽松"
                else:
                    score = 25  # 极度宽松
                    val = "降息周期"

                logger.info(f"      联邦基金利率: {ff_num}%, 判断: {val}")
                return score, val
        except Exception as e:
            logger.warning(f"      美联储政策判断异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {})
                .get("fed_policy", {}).get("score", 50), "中性")

    # ----------------------------------------------------------
    # 维度3: 股债性价比 (equity_bond)
    # ----------------------------------------------------------
    def calc_equity_bond(self) -> dict:
        """
        股债性价比维度 (v4.1)
        指标:
          - 沪深300 ERP: 参考(权重0%)
          - 万得全A ERP ★主指标: 权重40%
          - 红利指数股债利差: 权重20%
          - 股息率-国债利差(沪深300): 权重20%
          - 中美双视角利差: 权重20%
        """
        logger.info("  [维度3/6] 股债性价比...")
        ak = self.init_akshare()
        indicators = {}

        # --- 沪深300 ERP ---
        erp_val, erp_pct, erp_score = self._calc_erp()
        indicators["hs300_erp"] = {
            "name": "沪深300 ERP (参考)",
            "value": f"{erp_val:.2f}%" if erp_val else "数据待更新",
            "percentile": f"{erp_pct:.1f}%" if erp_pct is not None else "数据待更新",
            "score": erp_score,
            "sub_weight": 0,
            "note": "【参考】沪深300口径仅作参考，主指标为中证800 ERP（全A代理）",
            "bottom_threshold": ">6%",
            "top_threshold": "<2.5%"
        }

        # --- 万得全A ERP ★主指标 ---
        wanda_erp_val, wanda_erp_pct, wanda_erp_score = self._calc_wanda_erp()
        if wanda_erp_val is not None:
            indicators["wanda_erp"] = {
                "name": "中证800 ERP ★主指标（全A代理）",
                "value": f"{wanda_erp_val:.2f}%",
                "percentile": f"{wanda_erp_pct:.1f}%" if wanda_erp_pct is not None else "数据待更新",
                "score": wanda_erp_score,
                "sub_weight": 40,
                "note": "【P1-10⑦ 2026-10-18裁决】乐咕下线全A口径，切中证800（市值覆盖70%，19.7年月频）；分位口径股债拐点与全A一致",
                "bottom_threshold": ">4%",
                "top_threshold": "<1.5%"
            }
            erp_val = wanda_erp_val
            erp_pct = wanda_erp_pct
            erp_score = wanda_erp_score

        # --- 红利指数股债利差 ---
        div_spread, div_score = self._calc_dividend_spread()
        indicators["dividend_bond_spread_red"] = {
            "name": "红利指数股债利差",
            "value": f"{div_spread:.2f}%" if div_spread else "数据待更新",
            "score": div_score,
            "sub_weight": 20,
            "bottom_threshold": ">3%",
            "top_threshold": "<0.5%"
        }

        # --- 股息率-国债利差(沪深300) ---
        hs300_spread, hs300_sp_score = self._calc_hs300_div_spread()
        indicators["dividend_bond_spread_hs300"] = {
            "name": "股息率-国债利差(沪深300)",
            "value": f"{hs300_spread:.2f}%" if hs300_spread else "数据待更新",
            "score": hs300_sp_score,
            "sub_weight": 20,
            "bottom_threshold": ">3%",
            "top_threshold": "<0.3%"
        }

        # --- 中美双视角利差 ---
        cn_us_score, cn_us_val = self._calc_cn_us_spread()
        indicators["cn_us_spread"] = {
            "name": "中美双视角利差",
            "value": cn_us_val or "分歧",
            "score": cn_us_score,
            "sub_weight": 20,
            "bottom_signal": "内外资均认为便宜",
            "top_signal": "内外资均认为贵"
        }

        dim_score = self._weighted_score(indicators)
        signal = "股票极具吸引力" if dim_score < 30 else "股票有吸引力" if dim_score < 45 else "中性" if dim_score < 60 else "债券更有吸引力" if dim_score < 75 else "股票明显高估"

        return {
            "name": "股债性价比",
            "score": dim_score,
            "weight": 20,
            "weighted_score": round(dim_score * 0.20, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_erp(self):
        """
        ERP = 1/PE (盈利收益率) - 10年国债收益率
        ERP越高 → 股票越便宜 → 低分(底部信号)
        """
        try:
            ak = self.init_akshare()

            # 1. 获取沪深300 PE
            pe_df = safe_ak_call(ak.stock_index_pe_lg, symbol="沪深300")
            if pe_df is None or pe_df.empty:
                raise ValueError("PE数据为空")
            pe_col = '滚动市盈率'
            pe_val = safe_float(pe_df[pe_col].dropna().iloc[-1])
            if not pe_val or pe_val <= 0:
                raise ValueError(f"PE值异常: {pe_val}")

            # 2. 获取10年国债收益率
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is None or bond_df.empty:
                raise ValueError("国债数据为空")
            bond_df['date'] = pd.to_datetime(bond_df['date'])
            cn10y = safe_float(bond_df.set_index('date')['close'].dropna().iloc[-1])
            if not cn10y:
                raise ValueError("10Y国债收益率异常")

            # 3. 计算 ERP
            ep = 1.0 / pe_val * 100  # 盈利收益率 (%)
            erp = ep - cn10y

            # 4. 计算历史分位
            pe_series = pe_df.set_index('日期')[pe_col].dropna()
            pe_series.index = pd.to_datetime(pe_series.index)
            erp_series = (1.0 / pe_series * 100) - cn10y  # 近似历史ERP
            erp_pct = percentile_rank(erp_series, erp)

            # 5. 评分
            # ERP > 6% → 极度便宜 → 20分
            # ERP 4-6% → 偏便宜 → 35分
            # ERP 2.5-4% → 中性 → 50分
            # ERP < 2.5% → 偏贵 → 70分
            if erp > 6:
                score = 20
            elif erp > 5:
                score = 30
            elif erp > 4:
                score = 40
            elif erp > 3:
                score = 50
            elif erp > 2.5:
                score = 60
            else:
                score = 75

            logger.info(f"      ERP: {erp:.2f}% (E/P={ep:.2f}%, 10Y={cn10y:.2f}%), 分位={erp_pct}%")
            return erp, erp_pct, score

        except Exception as e:
            logger.warning(f"      ERP计算失败: {e}")

        prev = load_json("timing_scores.json")
        prev_erp = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("hs300_erp", {})
        prev_val_str = prev_erp.get("value", "5.00%")
        prev_score = prev_erp.get("score", 50)
        prev_pct = prev_erp.get("percentile", "50%")
        try:
            return float(prev_val_str.replace('%', '')), float(prev_pct.replace('%', '')), prev_score
        except:
            return 5.0, 50.0, 50

    def _calc_wanda_erp(self):
        """
        中证800 ERP（全A代理）= 1/PE_TTM(中证800) - 10年国债收益率
        [P1-10⑦ 2026-10-18 投资分析裁决] 乐咕已下线'中证A股' symbol（symbol_map 无全A口径），
        免费全A PE 长历史源绝迹；中证800 覆盖 A 股市值约 70%，月频 2007 起 19.7 年连续，
        为乐咕现存最接近全A的代理。分位口径下 300/800/全A 股债拐点一致（裁决原文）。
        PE来源: akshare stock_index_pe_lg (symbol="中证800")
        阈值: >4%底部 / >3%便宜 / >2%中性 / >1.5%偏贵 / <1.5%昂贵
        """
        try:
            ak = self.init_akshare()
            pe_df = safe_ak_call(ak.stock_index_pe_lg, symbol="中证800")
            if pe_df is None or pe_df.empty:
                raise ValueError("中证800 PE数据为空")

            pe_col = None
            for col in ['滚动市盈率', '市盈率(TTM)', 'PE']:
                if col in pe_df.columns:
                    pe_col = col
                    break
            if not pe_col:
                raise ValueError(f"PE列不存在: {pe_df.columns.tolist()}")

            pe_val = safe_float(pe_df[pe_col].dropna().iloc[-1])
            if not pe_val or pe_val <= 0:
                raise ValueError(f"全A PE值异常: {pe_val}")

            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is None or bond_df.empty:
                raise ValueError("国债数据为空")
            bond_df['date'] = pd.to_datetime(bond_df['date'])
            cn10y = safe_float(bond_df.set_index('date')['close'].dropna().iloc[-1])
            if not cn10y:
                raise ValueError("10Y国债收益率异常")

            ep = 1.0 / pe_val * 100
            erp = ep - cn10y

            pe_series = pe_df.set_index('日期')[pe_col].dropna()
            pe_series.index = pd.to_datetime(pe_series.index)
            erp_series = (1.0 / pe_series * 100) - cn10y
            erp_pct = percentile_rank(erp_series, erp)

            if erp > 4.0:
                score = 20
            elif erp > 3.0:
                score = 35
            elif erp > 2.0:
                score = 50
            elif erp > 1.5:
                score = 65
            else:
                score = 80

            logger.info(f"      万得全A ERP: {erp:.2f}% (PE={pe_val:.1f}, 10Y={cn10y:.2f}%), 分位={erp_pct}%")
            return erp, erp_pct, score

        except Exception as e:
            logger.warning(f"      万得全A ERP计算失败: {e}")
            prev = load_json("timing_scores.json")
            prev_erp = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("wanda_erp", {})
            if prev_erp:
                try:
                    return float(prev_erp.get("value", "0%").replace('%', '')), \
                           float(prev_erp.get("percentile", "50%").replace('%', '')), \
                           prev_erp.get("score", 50)
                except:
                    pass
            return None, None, 50

    def _calc_dividend_spread(self):
        """红利指数股息率 - 10年国债"""
        try:
            # 红利指数股息率 ≈ 4.5-5% (近期范围)
            # 从上期数据获取并微调
            prev = load_json("timing_scores.json")
            prev_val = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("dividend_bond_spread_red", {}).get("value", "2.81%")
            prev_score = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("dividend_bond_spread_red", {}).get("score", 30)

            # 尝试获取最新国债收益率来微调
            ak = self.init_akshare()
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is not None and not bond_df.empty and 'close' in bond_df.columns:
                bond_df['date'] = pd.to_datetime(bond_df['date'])
                cn10y = safe_float(bond_df.set_index('date')['close'].dropna().iloc[-1])
                if cn10y:
                    # 红利股息率约 4.6% (较稳定)
                    div_yield = 4.6
                    spread = div_yield - cn10y
                    if spread > 3:
                        score = 20
                    elif spread > 2:
                        score = 30
                    elif spread > 1:
                        score = 45
                    else:
                        score = 60
                    logger.info(f"      红利股债利差: {spread:.2f}% (红利股息{div_yield}%-国债{cn10y:.2f}%)")
                    return spread, score
        except Exception as e:
            logger.warning(f"      红利利差计算异常: {e}")

        try:
            return float(prev_val.replace('%', '')), prev_score
        except:
            return 2.81, 30

    def _calc_hs300_div_spread(self):
        """沪深300股息率 - 10年国债"""
        try:
            ak = self.init_akshare()
            # 沪深300股息率 ≈ 2.8-3.0%
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is not None and not bond_df.empty and 'close' in bond_df.columns:
                bond_df['date'] = pd.to_datetime(bond_df['date'])
                cn10y = safe_float(bond_df.set_index('date')['close'].dropna().iloc[-1])
                if cn10y:
                    hs300_div = 2.81  # 近期沪深300股息率
                    spread = hs300_div - cn10y
                    if spread > 3:
                        score = 20
                    elif spread > 1.5:
                        score = 35
                    elif spread > 0.5:
                        score = 50
                    elif spread > 0:
                        score = 60
                    else:
                        score = 75
                    logger.info(f"      沪深300股息率-国债: {spread:.2f}% ({hs300_div}%-{cn10y:.2f}%)")
                    return spread, score
        except Exception as e:
            logger.warning(f"      沪深300利差异常: {e}")

        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("dividend_bond_spread_hs300", {}).get("value", "0.99%")
        prev_score = prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {}).get("dividend_bond_spread_hs300", {}).get("score", 48)
        try:
            return float(prev_val.replace('%', '')), prev_score
        except:
            return 0.99, 48

    def _calc_cn_us_spread(self):
        """中美双视角利差"""
        try:
            # 中国ERP vs 美国ERP 的比较
            us = load_json("us_macro.json")
            us_10y = us.get("rates", {}).get("yield_10y", {}).get("value")

            # 中国10Y
            ak = self.init_akshare()
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            cn_10y = None
            if bond_df is not None and not bond_df.empty and 'close' in bond_df.columns:
                bond_df['date'] = pd.to_datetime(bond_df['date'])
                cn_10y = safe_float(bond_df.set_index('date')['close'].dropna().iloc[-1])

            if cn_10y and us_10y:
                # 中国ERP大致 vs 美国ERP
                cn_ep = 1.0 / 13.5 * 100  # 假设沪深300 PE≈13.5
                cn_erp = cn_ep - cn_10y
                us_ep = 1.0 / 25.0 * 100  # 假设SP500 PE≈25
                us_erp = us_ep - us_10y

                if cn_erp > us_erp + 2:
                    val = "A股更便宜"
                    score = 35
                elif cn_erp > us_erp:
                    val = "A股略便宜"
                    score = 45
                elif cn_erp > us_erp - 2:
                    val = "分歧"
                    score = 55
                else:
                    val = "美股更便宜"
                    score = 65

                logger.info(f"      中美利差: CN ERP≈{cn_erp:.1f}% vs US ERP≈{us_erp:.1f}% → {val}")
                return score, val
        except Exception as e:
            logger.warning(f"      中美利差异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("equity_bond", {}).get("indicators", {})
                .get("cn_us_spread", {}).get("score", 55), "分歧")

    # ----------------------------------------------------------
    # 维度4: 资金面 (capital_flow)
    # ----------------------------------------------------------
    def calc_capital_flow(self) -> dict:
        """
        资金面维度 (v4.1)
        指标:
          - 股基仓位: 权重25%
          - 融资买入占比: 权重20%
          - 灵基仓位: 权重20%
          - 新发基金热度: 权重20%
          - 产业资本增减持: 权重15%
          - 北向资金: 已停更(权重0%)
          - 两融/流通市值: 参考(权重0%)
          - 财政支出: 参考(权重0%)
        """
        logger.info("  [维度4/6] 资金面...")
        ak = self.init_akshare()
        indicators = {}

        # --- 两融/流通市值 ---
        margin_score, margin_val = self._calc_margin_ratio()
        # margin_val 是数字 (百分比)，如 2.66；value 保留展示串，value_numeric 供前端数值运算
        if isinstance(margin_val, (int, float)):
            margin_display = f"{margin_val:.2f}%"
            margin_numeric = round(float(margin_val), 4)
        else:
            margin_display = margin_val or "数据待更新"
            margin_numeric = None
        indicators["margin_ratio"] = {
            "name": "融资余额250日分位（沪市）",
            "value": f"{margin_numeric:.1f}%分位" if margin_numeric is not None else "数据待更新",
            "value_numeric": margin_numeric,
            "unit": "分位%",
            "score": margin_score,
            "sub_weight": 0,
            "note": "【P1-10① 2026-10-18裁决】口径=沪市融资余额250日分位（逆向：分位高=杠杆热=score低）。原两融/流通市值分母无源弃用。局限：仅沪市（深市45%缺失偏保守）；250日窗口无法识别跨周期泡沫（2015式），V2恢复ratio口径",
            "bottom_threshold": "分位<10%（冰点）",
            "top_threshold": "分位>90%（过热）"
        }

        # --- 股基仓位 (v4.1新增) ---
        efp_score, efp_val = self._calc_equity_fund_position()
        indicators["equity_fund_position"] = {
            "name": "股基仓位",
            "value": efp_val or "数据待更新",
            "score": efp_score,
            "sub_weight": 25,
            "bottom_signal": "仓位极低(恐慌)",
            "top_signal": "仓位极高(狂热)"
        }

        # --- 融资买入占比 (v4.1从情绪移入) ---
        mb_score, mb_val, mb_extra = self._calc_margin_buying()
        indicators["margin_buying_ratio"] = {
            "name": "融资买入占比",
            "value": mb_val if mb_val is not None else "数据待更新",
            "score": mb_score,
            "sub_weight": 20,
            "bottom_threshold": "<7%",
            "top_threshold": ">11%",
            **{k: v for k, v in (mb_extra or {}).items()
               if k in ("tier", "ma20", "pctile_2016", "as_of", "freq", "source")}
        }

        # --- 灵基仓位 (v4.1新增) ---
        ffp_score, ffp_val = self._calc_flexible_fund_position()
        indicators["flexible_fund_position"] = {
            "name": "灵基仓位",
            "value": ffp_val or "数据待更新",
            "score": ffp_score,
            "sub_weight": 20,
            "bottom_signal": "仓位极低",
            "top_signal": "仓位极高"
        }

        # --- 北向资金趋势 ---
        north_score, north_val = self._calc_northbound()
        # [P1-10④ 2026-10-18 投资分析裁决] 北向删卡（披露制度消灭免费高频源），
        # 卡位腾给两融余额变化（沪市融资余额，杠杆资金情绪，免费日频衔接①修复）；
        # 原 northbound sub_weight=0 → 本指标同 0（换子指标不动权重，不进 composite）
        mb_balance, mb_w_chg, mb_asof, mb_hist = self._calc_margin_balance()
        if mb_balance is None:
            # [2026-10-24 删史防线] stock_margin_sse 源断（CI 限流 JSONDecodeError）时整体继承上期，
            # history 绝不写空数组（history_guard 阻断项）；stale_inherited 显形标记可观测。
            _prev_mb = (((load_json("timing_scores.json") or {}).get("dimensions") or {})
                        .get("capital_flow", {}).get("indicators", {}) or {}).get("margin_balance")
            if isinstance(_prev_mb, dict) and _prev_mb.get("history"):
                _mb = dict(_prev_mb)
                _mb["stale_inherited"] = True
                _mb["note"] = (str(_prev_mb.get("note", "")) + "｜源暂断·继承上期").strip("｜")
                indicators["margin_balance"] = _mb
                logger.warning(f"      两融余额源断 → 整体继承上期（history {len(_prev_mb['history'])}点保留）")
            else:
                indicators["margin_balance"] = {
                    "name": "两融余额（沪市）", "value": "数据待更新", "week_change": None,
                    "score": 50, "sub_weight": 0,
                    "note": "【P1-10④】北向卡位替代：沪市融资余额（深市仅单日查询无法历史回填），周变化=杠杆资金进出方向",
                    "as_of": None, "freq": "日频", "history": []
                }
        else:
            indicators["margin_balance"] = {
                "name": "两融余额（沪市）",
                "value": f"{mb_balance:.2f}万亿",
                "week_change": f"{mb_w_chg:+.0f}亿" if mb_w_chg is not None else None,
                "score": 50,
                "sub_weight": 0,
                "note": "【P1-10④】北向卡位替代：沪市融资余额（深市仅单日查询无法历史回填），周变化=杠杆资金进出方向",
                "as_of": mb_asof,
                "freq": "日频",
                "history": mb_hist or []
            }

        # --- 新发基金热度 ---
        fund_score, fund_val = self._calc_new_fund()
        indicators["new_fund"] = {
            "name": "新发基金热度",
            "value": fund_val or "中等",
            "score": fund_score,
            "sub_weight": 20,
            "bottom_signal": "冰点(月<200亿)",
            "top_signal": "天量(月>1000亿)"
        }

        # --- 产业资本增减持 ---
        capital_score, capital_val = self._calc_industrial_capital()
        indicators["industrial_capital"] = {
            "name": "产业资本增减持",
            "value": capital_val or "平衡",
            "score": capital_score,
            "sub_weight": 15,
            "bottom_signal": "净增持/回购潮",
            "top_signal": "大举减持"
        }

        # --- 财政支出 (v4.1新增, 参考) ---
        fe_score, fe_val = self._calc_fiscal_expenditure()
        indicators["fiscal_expenditure"] = {
            "name": "财政支出",
            "value": fe_val or "数据待更新",
            "score": fe_score,
            "sub_weight": 0,
            "note": "【参考】财政支出仅作辅助参考",
            "bottom_signal": "财政紧缩",
            "top_signal": "财政扩张"
        }

        dim_score = self._weighted_score(indicators)
        signal = "资金充裕" if dim_score < 35 else "正常偏高" if dim_score < 50 else "正常" if dim_score < 60 else "正常偏低" if dim_score < 75 else "资金紧张"

        return {
            "name": "资金面",
            "score": dim_score,
            "weight": 17,
            "weighted_score": round(dim_score * 0.17, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_margin_ratio(self):
        """[P1-10① 2026-10-18 投资分析裁决] 沪市融资余额 250 日分位（逆向）。
        原口径"两融/流通市值"分母无长历史免费源→弃用；250 日窗口内趋势失真小，
        捕捉近一年杠杆边际热度对月频择时足够。
        score = 100 − 分位×100（分位越高=杠杆越热=score 越低，与其他分位子指标同构）。
        裁决注记：①仅沪市——深市约占两融总量45%且偏成长/激进，缺失使杠杆情绪系统性
        偏保守估计；②250 日窗口无法识别跨周期绝对杠杆率泡沫（2015 式 4%+），
        V2 若流通市值源可得恢复 ratio 口径。
        返回 (score, 分位数值0-100)。"""
        try:
            ak = self.init_akshare()
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=550)).strftime("%Y%m%d")
            df = safe_ak_call(ak.stock_margin_sse, start_date=start, end_date=end)
            if df is None or df.empty:
                raise ValueError("margin_sse 为空")
            df = df.copy()
            df['date'] = pd.to_datetime(df['信用交易日期'], format='%Y%m%d')
            df = df.set_index('date').sort_index()
            bal = df['融资余额'].astype(float)
            win = bal.tail(250)
            cur = float(bal.iloc[-1])
            pctile = float((win <= cur).mean() * 100)
            score = round(100 - pctile, 1)
            logger.info(f"      融资余额250日分位（沪市）: {pctile:.1f}% → score={score}（逆向）")
            return score, round(pctile, 1)
        except Exception as e:
            logger.warning(f"      融资余额分位计算失败（继承上期）: {e}")
            prev = load_json("timing_scores.json")
            ind = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("margin_ratio", {})
            pv = ind.get("value_numeric")
            try:
                return ind.get("score", 50), float(pv) if pv is not None else None
            except (TypeError, ValueError):
                return ind.get("score", 50), None

    def _calc_margin_balance(self):
        """[P1-10④] 沪市融资余额+周变化（北向卡位替代指标，参考权重0）"""
        try:
            ak = self.init_akshare()
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=550)).strftime("%Y%m%d")
            df = safe_ak_call(ak.stock_margin_sse, start_date=start, end_date=end)
            if df is None or df.empty:
                raise ValueError("margin_sse 为空")
            df = df.copy()
            df['date'] = pd.to_datetime(df['信用交易日期'], format='%Y%m%d')
            df = df.set_index('date').sort_index()
            bal = df['融资余额'].astype(float)
            cur = float(bal.iloc[-1]) / 1e12  # 万亿
            w_ago = float(bal.iloc[-6]) if len(bal) >= 6 else float(bal.iloc[0])
            w_chg = (bal.iloc[-1] - w_ago) / 1e8  # 亿
            # 近250交易日余额 history（亿元），供前端 sparkline
            hist = [{"date": str(d.date()), "value": round(float(v) / 1e8, 0)}
                    for d, v in bal.tail(250).items()]
            logger.info(f"      两融余额（沪市）: {cur:.2f}万亿, 周变化 {w_chg:+.0f}亿, history {len(hist)}点")
            return round(cur, 2), round(float(w_chg), 0), str(bal.index[-1].date()), hist
        except Exception as e:
            logger.warning(f"      两融余额计算失败: {e}")
            return None, None, None, None

    def _calc_northbound(self):
        """北向资金趋势"""
        try:
            ak = self.init_akshare()
            # 使用 stock_hsgt_hist_em 获取北向资金历史数据
            hgt_df = safe_ak_call(ak.stock_hsgt_hist_em, symbol="沪股通")
            sgt_df = safe_ak_call(ak.stock_hsgt_hist_em, symbol="深股通")

            # 合并沪股通+深股通
            combined = pd.Series(dtype=float)
            for df in [hgt_df, sgt_df]:
                if df is not None and not df.empty and '当日成交净买额' in df.columns:
                    dates = pd.to_datetime(df['日期'])
                    net_buy = pd.to_numeric(df['当日成交净买额'], errors='coerce')
                    series = pd.Series(net_buy.values, index=dates.values).dropna()
                    # 单位: 万元 → 亿元
                    series = series / 10000
                    combined = combined.add(series, fill_value=0)

            if not combined.empty and len(combined) >= 3:
                recent_5d = combined.tail(5).sum()
                recent_3d = combined.tail(3).sum()

                if recent_5d > 100:
                    score = 30
                    val = "连续净流入"
                elif recent_5d > 0:
                    score = 42
                    val = "小幅净流入"
                elif recent_5d > -100:
                    score = 55
                    val = "震荡"
                else:
                    score = 70
                    val = "持续净流出"

                logger.info(f"      北向5日净流入: {recent_5d:.0f}亿 → {val}")
                return score, val
        except Exception as e:
            logger.warning(f"      北向资金异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {})
                .get("northbound", {}).get("score", 50),
                prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {})
                .get("northbound", {}).get("value", "震荡"))

    def _calc_new_fund(self):
        """新发基金热度"""
        # 新发基金数据较难自动获取，使用上期值
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("new_fund", {}).get("value", "中等")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("new_fund", {}).get("score", 50)
        logger.info(f"      新发基金: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_industrial_capital(self):
        """产业资本增减持"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("industrial_capital", {}).get("value", "平衡")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("industrial_capital", {}).get("score", 45)
        logger.info(f"      产业资本: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_equity_fund_position(self):
        """股基仓位 (v4.1新增)"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("equity_fund_position", {}).get("value", "数据待更新")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("equity_fund_position", {}).get("score", 50)
        logger.info(f"      股基仓位: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_flexible_fund_position(self):
        """灵基仓位 (v4.1新增)"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("flexible_fund_position", {}).get("value", "数据待更新")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("flexible_fund_position", {}).get("score", 50)
        logger.info(f"      灵基仓位: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_fiscal_expenditure(self):
        """财政支出 (v4.1新增, 参考指标)"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("fiscal_expenditure", {}).get("value", "数据待更新")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("fiscal_expenditure", {}).get("score", 50)
        logger.info(f"      财政支出: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_new_account_opening(self):
        """新增开户数 (v4.1从资金面移入情绪)"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("new_account_opening", {}).get("value", "数据待更新")
        prev_score = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("new_account_opening", {}).get("score", 50)
        logger.info(f"      新增开户数: {prev_val} (使用上期值)")
        return prev_score, prev_val

    # ----------------------------------------------------------
    # 维度5: 市场情绪 (sentiment)
    # ----------------------------------------------------------
    def calc_sentiment(self) -> dict:
        """
        市场情绪维度 (v4.1)
        指标:
          - 恐贪指数: 权重35%
          - 全A换手率: 权重25%
          - 偏股基金滚动3年年化: 权重20%
          - 新增开户数: 权重20%
          - 融资买入占比: 参考(已移至资金面, 权重0%)
        """
        logger.info("  [维度5/6] 市场情绪...")
        ak = self.init_akshare()
        indicators = {}

        # --- 偏股基金3年年化 ---
        fund_score, fund_val = self._calc_fund_3y()
        indicators["fund_3y_annual"] = {
            "name": "偏股基金滚动3年年化",
            "value": fund_val or "数据待更新",
            "score": fund_score,
            "sub_weight": 20,
            "bottom_threshold": "<-10%",
            "top_threshold": ">30%",
            "source": "中证偏股型基金指数 930950（csindex 官方）",
            "note": "【P1-10② 2026-10-18裁决】885001万得偏股混→930950中证偏股（付费→官方免费替代，重叠段差0.40pp）"
        }

        # --- 恐贪指数 (P0-2: 单一真值源 feargreed_pointer.json → score) ---
        fg_score, fg_val = self._calc_fear_greed()
        if fg_val is None:
            # 降级路径：主源缺失 / 字段为 null / 非法值 → 中性占位并显式标注，不留僵尸值
            indicators["fear_greed_index"] = {
                "name": "恐贪指数",
                "value": "数据待获取",
                "score": 50,
                "sub_weight": 35,
                "bottom_threshold": "<20",
                "top_threshold": ">85且QVIX<14",
                "note": "主源 data/feargreed_pointer.json 缺 score，降级中性"
            }
        else:
            indicators["fear_greed_index"] = {
                "name": "恐贪指数",
                "value": fg_val,
                "score": fg_score,
                "sub_weight": 35,
                "bottom_threshold": "<20",
                "top_threshold": ">85且QVIX<14",
                "source": "data/feargreed_pointer.json#score"
            }

        # --- 全A换手率 ---
        turnover_score, turnover_val = self._calc_turnover()
        indicators["turnover_rate"] = {
            "name": "全A换手率",
            "value": turnover_val or "数据待更新",
            "score": turnover_score,
            "sub_weight": 25,
            "bottom_threshold": "<1.5%",
            "top_threshold": ">5%"
        }

        # --- 融资买入占比 ---
        fb_score, fb_val, fb_extra = self._calc_margin_buying()
        indicators["margin_buying_ratio"] = {
            "name": "融资买入占比",
            "value": fb_val if fb_val is not None else "数据待更新",
            "score": fb_score,
            "sub_weight": 0,
            "note": "【已移至资金面】",
            "bottom_threshold": "<7%",
            "top_threshold": ">11%",
            **{k: v for k, v in (fb_extra or {}).items()
               if k in ("tier", "ma20", "pctile_2016", "as_of", "freq", "source")}
        }

        # --- 新增开户数 (从资金面移入) ---
        nao_score, nao_val = self._calc_new_account_opening()
        indicators["new_account_opening"] = {
            "name": "新增开户数",
            "value": nao_val or "数据待更新",
            "score": nao_score,
            "sub_weight": 20,
            "bottom_threshold": "<80万",
            "top_threshold": ">200万"
        }

        dim_score = self._weighted_score(indicators)
        signal = "极度恐惧" if dim_score < 25 else "偏恐惧" if dim_score < 40 else "中性" if dim_score < 60 else "偏贪婪" if dim_score < 75 else "极度贪婪"

        return {
            "name": "市场情绪",
            "score": dim_score,
            "weight": 16,
            "weighted_score": round(dim_score * 0.16, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_fund_3y(self):
        """偏股基金滚动3年年化 —— [P1-10② 2026-10-18 投资分析裁决]
        数据源：中证偏股型基金指数 930950（csindex 官方免费 JSON，替代已不可得的 885001 万得偏股混合型基金指数；
        与万得全A→中证全指同一替代逻辑，重叠段差 0.40pp 趋势一致，慢变量可忽略）。
        score 阈值映射（裁决原案，规避 2023 起仅 194 点分位无法覆盖完整牛熊的硬伤）：
        x<-10→90 / [-10,0)→70 / [0,10)→50 / [10,20)→30 / [20,30)→20 / ≥30→10
        （赚钱效应越高 score 越低=逆向；V2 可选 ±1pp 滞后带防边界抖动，本期不做）
        """
        try:
            closes = self._fetch_csi_index_close("930950", days=4000)
            if closes is None or len(closes) < 756:
                raise ValueError("930950 历史不足")
            # 3年滚动年化 = (close_t / close_{t-756})^(1/3) - 1
            ann = (closes.iloc[-1] / closes.iloc[-756]) ** (1/3) - 1
            x = float(ann) * 100
            if x < -10: score = 90
            elif x < 0: score = 70
            elif x < 10: score = 50
            elif x < 20: score = 30
            elif x < 30: score = 20
            else: score = 10
            val = f"{x:.2f}%"
            logger.info(f"      基金3年年化(930950): {val} → score={score}")
            return score, val
        except Exception as e:
            logger.warning(f"      基金3年年化计算失败（继承上期）: {e}")
            prev = load_json("timing_scores.json")
            ind = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("fund_3y_annual", {})
            return ind.get("score", 50), ind.get("value", "数据待更新")

    @staticmethod
    def _fetch_csi_index_close(index_code, days=4000):
        """中证指数官网免费日频收盘 JSON（CI 美国 runner 已验证可达）。
        返回 pd.Series（index=date, values=close 升序）或 None。"""
        import requests
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf"
               f"?indexCode={index_code}&startDate={start}&endDate={end}")
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        rows = r.json().get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
        return df.set_index("date")["close"].astype(float).sort_index()

    def _fear_greed_score_ssot(self):
        """[P0-1/P0-2 单一真值源 2026-10-10] 恐贪 score 只来自 data/feargreed_pointer.json。

        score = 100 - QVIX 滚动252日百分位：高=贪婪 / 低=恐惧。
        返回 float（已钳制到 [0,100]）或 None（文件缺失 / 缺 score 字段 / 字段为 null /
        非法值），由调用方走降级路径。
        """
        fg = load_json("feargreed_pointer.json")
        if not fg:
            return None
        raw = fg.get("score")
        if raw is None:
            return None
        try:
            return max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError):
            return None

    def _calc_fear_greed(self):
        """恐贪指数 - 单一真值源: data/feargreed_pointer.json → score。

        [P0-2 修复 2026-10-10] 旧实现用上证成交量估算、并以 timing_scores.json 上期值
        为种子，结果被钳制在 [10,90]，长期卡死在 10（僵尸值），与 feargreed_pointer.json
        的真实恐贪值（98.41，极度贪婪）方向相反、相差近 10 倍。现统一读单一真值源。
        返回 (score, value)；读不到时返回 (None, None) 走降级路径。
        """
        score = self._fear_greed_score_ssot()
        if score is None:
            logger.warning("      恐贪指数: 主源 data/feargreed_pointer.json 缺 score，降级为『数据待获取』")
            return None, None
        fg = load_json("feargreed_pointer.json")
        logger.info(f"      恐贪指数: {score} (源=feargreed_pointer.json, as_of={fg.get('as_of')})")
        return score, round(score, 2)

    def _calc_turnover(self):
        """全A换手率 - 基于SSE成交量估算"""
        try:
            ak = self.init_akshare()
            sse_df = safe_ak_call(ak.stock_zh_index_daily, symbol="sh000001")
            if sse_df is not None and not sse_df.empty:
                if 'volume' in sse_df.columns:
                    vol = pd.to_numeric(sse_df['volume'], errors='coerce').dropna()
                    if len(vol) >= 5:
                        # vol 单位: 股 (SSE全市场成交量)
                        # 估算全A成交量: SSE约占60%
                        sse_avg_vol = vol.tail(5).mean()
                        total_a_shares = sse_avg_vol / 0.60

                        # 估算全A流通股本 ≈ 5.5万亿股 (约55000亿股)
                        # 全A流通市值 ≈ 65万亿元
                        float_shares = 5.5e12  # 5.5万亿股
                        turnover = (total_a_shares / float_shares) * 100

                        # 合理性约束 (正常范围 0.5% - 8%)
                        turnover = max(0.5, min(8.0, turnover))

                        # 估算日成交额 (用于日志)
                        est_amount = total_a_shares * 15 / 1e8  # 假设均价15元

                        if turnover < 1.5:
                            score = 30
                        elif turnover < 2.5:
                            score = 45
                        elif turnover < 4.0:
                            score = 55
                        elif turnover < 5.0:
                            score = 65
                        else:
                            score = 80

                        val_str = f"{turnover:.1f}%"
                        logger.info(f"      全A换手率: {val_str} (SSE量={sse_avg_vol/1e8:.0f}亿股, 估全A={est_amount:.0f}亿)")
                        return score, val_str
        except Exception as e:
            logger.warning(f"      换手率异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {})
                .get("turnover_rate", {}).get("score", 45),
                prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {})
                .get("turnover_rate", {}).get("value", "2.5%"))

    def _calc_margin_buying(self):
        """融资买入占比（P1-10 修复 2026-10-18，工单6a口径）
        = 沪市融资买入额 / 全A成交额 × 100（仅沪市分子：深市接口仅支持单日查询无法历史回填，
        沪市占两市55-60%，历史同口径下分位相对比较有效；分母=中证全指成交额，
        腾讯 sh000985 amount，与 P1-6 同链路）
        score = max(2016后分位, 护栏floor≥15%→90/≥11%→75/≥9%→60)；低分=冰点(积极) 高分=过热(风险)
        返回 (score, value, extra)；失败降级抄上期。
        """
        prev = load_json("timing_scores.json")
        prev_ind = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("margin_buying_ratio", {})
        try:
            ak = self.init_akshare()
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
            m_df = safe_ak_call(ak.stock_margin_sse, start_date=start, end_date=end)
            idx_df = safe_ak_call(ak.stock_zh_index_daily_tx, symbol="sh000985")
            if m_df is None or m_df.empty or idx_df is None or idx_df.empty:
                raise ValueError("两融或指数数据为空")
            m_df = m_df.copy()
            m_df['date'] = pd.to_datetime(m_df['信用交易日期'], format='%Y%m%d')
            m_df = m_df.set_index('date').sort_index()
            idx_df = idx_df.copy()
            idx_df['date'] = pd.to_datetime(idx_df['date'])
            amount = idx_df.set_index('date')['amount'].astype(float) * 1000  # 千元→元
            j = m_df[['融资买入额']].astype(float).join(amount.rename('amount'), how='inner')
            if j.empty:
                raise ValueError("两融与成交额日期无交集")
            ratio = j['融资买入额'] / j['amount'] * 100
            cur = float(ratio.iloc[-1])
            ma20 = float(ratio.rolling(20).mean().iloc[-1]) if len(ratio) >= 20 else None
            as_of = str(ratio.index[-1].date())

            # 2016后分位：基于落盘 history（回填3486条）+ 当日值
            hist = prev_ind.get("history", [])
            hist_vals = [h["value"] for h in hist
                         if isinstance(h.get("value"), (int, float)) and h.get("date", "") >= "2016-01-01"]
            if hist_vals:
                pct = sum(1 for v in hist_vals if v < cur) / len(hist_vals) * 100
            else:
                pct = 50.0  # 无历史兜底
            floor = 90 if cur >= 15 else 75 if cur >= 11 else 60 if cur >= 9 else 0
            score = round(max(pct, floor), 1)
            tier = ('extreme_greed' if pct >= 95 or cur >= 15 else
                    'greed' if pct >= 85 or cur >= 11 else
                    'watch' if pct >= 70 or cur >= 9 else
                    'neutral' if pct >= 30 else
                    'fear' if pct >= 5 else 'extreme_fear')
            logger.info(f"      融资买入占比: {cur:.2f}% (2016后分位{pct:.1f}%, 20MA {ma20:.2f}%, {as_of})")
            return score, round(cur, 2), {"tier": tier, "ma20": round(ma20, 2) if ma20 else None,
                                          "pctile_2016": round(pct, 1), "as_of": as_of,
                                          "freq": "日频", "source": "沪市stock_margin_sse/全A成交额(腾讯sh000985)"}
        except Exception as e:
            logger.warning(f"      融资买入占比计算失败: {e}，抄上期")
            prev_val = prev_ind.get("value", "8-10%")
            prev_score = prev_ind.get("score", 48)
            return prev_score, prev_val, dict(prev_ind) if isinstance(prev_ind, dict) else {}

    # ----------------------------------------------------------
    # 维度6: 微观结构 (micro_structure)
    # ----------------------------------------------------------
    def calc_micro_structure(self) -> dict:
        """
        微观结构维度 (v4.1)
        指标:
          - 交易拥挤度(前5%占比): 权重40%
          - 行业成交集中度(HHI): 权重35%
          - 交易集中度趋势: 权重25%
          - 中证1000/沪深300比值: 参考(权重0%)
        """
        logger.info("  [维度6/6] 微观结构...")
        ak = self.init_akshare()
        indicators = {}

        # --- 交易拥挤度 ---
        crowd_score, crowd_val, crowd_pct = self._calc_crowding()
        indicators["crowding_ratio"] = {
            "name": "交易拥挤度(前5%占比)",
            "value": crowd_val or "数据待更新",
            "percentile": crowd_pct,
            "score": crowd_score,
            "sub_weight": 40,
            "bottom_threshold": "从极端回落至40%以下",
            "top_threshold": ">48%"
        }

        # --- 行业成交集中度 ---
        ind_score, ind_val = self._calc_industry_concentration()
        indicators["industry_concentration"] = {
            "name": "行业成交集中度",
            "value": ind_val or "数据待更新",
            "score": ind_score,
            "sub_weight": 35,
            "bottom_threshold": "前三行业<25%",
            "top_threshold": "前三行业>40%"
        }

        # --- 交易集中度趋势 ---
        trend_score, trend_val = self._calc_concentration_trend()
        indicators["concentration_trend"] = {
            "name": "交易集中度趋势",
            "value": trend_val or "数据待更新",
            "score": trend_score,
            "sub_weight": 25,
            "bottom_signal": "从集中到分散",
            "top_signal": "加速集中"
        }

        # --- 中证1000/沪深300比值 ---
        ratio_score, ratio_val = self._calc_small_large_ratio()
        indicators["csi1000_hs300_ratio"] = {
            "name": "中证1000/沪深300比值",
            "value": ratio_val or "数据待更新",
            "score": ratio_score,
            "sub_weight": 0,
            "note": "【参考】中证1000/沪深300比值仅作辅助参考",
            "bottom_signal": "比值触底回升",
            "top_signal": "比值加速下跌"
        }

        dim_score = self._weighted_score(indicators)
        signal = "极度分散/冷清" if dim_score < 30 else "偏冷" if dim_score < 45 else "中性" if dim_score < 60 else "偏热" if dim_score < 75 else "极度拥挤"

        return {
            "name": "微观结构",
            "score": dim_score,
            "weight": 14,
            "weighted_score": round(dim_score * 0.14, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_crowding(self):
        """交易拥挤度: 前5%个股成交额占全市场比"""
        try:
            ak = self.init_akshare()
            # 简化版: 使用行业板块成交集中度代替
            # 获取行业板块数据
            industry_df = safe_ak_call(ak.stock_board_industry_name_em)
            if industry_df is not None and not industry_df.empty:
                # 找到成交额列
                amount_col = None
                for col in industry_df.columns:
                    if '成交额' in str(col) or 'amount' in str(col).lower():
                        amount_col = col
                        break

                if amount_col:
                    amounts = pd.to_numeric(industry_df[amount_col], errors='coerce').dropna()
                    if len(amounts) >= 10:
                        total = amounts.sum()
                        top5 = amounts.nlargest(5).sum()
                        ratio = top5 / total * 100 if total > 0 else 0

                        # 评分
                        if ratio > 48:
                            score = 80
                        elif ratio > 40:
                            score = 65
                        elif ratio > 30:
                            score = 50
                        else:
                            score = 35

                        # 百分位 (简化)
                        pct = min(99, max(10, ratio * 2))

                        val_str = f"{ratio:.1f}%"
                        pct_str = f"{pct:.1f}%"
                        logger.info(f"      拥挤度(前5行业占比): {val_str}, 分位≈{pct_str}")
                        return score, val_str, pct_str
        except Exception as e:
            logger.warning(f"      拥挤度异常: {e}")

        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {}).get("crowding_ratio", {}).get("value", "51.4%")
        prev_pct = prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {}).get("crowding_ratio", {}).get("percentile", "98.3%")
        prev_score = prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {}).get("crowding_ratio", {}).get("score", 80)
        return prev_score, prev_val, prev_pct

    def _calc_industry_concentration(self):
        """行业成交集中度"""
        try:
            ak = self.init_akshare()
            industry_df = safe_ak_call(ak.stock_board_industry_name_em)
            if industry_df is not None and not industry_df.empty:
                amount_col = None
                for col in industry_df.columns:
                    if '成交额' in str(col) or 'amount' in str(col).lower():
                        amount_col = col
                        break
                name_col = None
                for col in industry_df.columns:
                    if '板块名称' in str(col) or '名称' in str(col):
                        name_col = col
                        break

                if amount_col:
                    amounts = pd.to_numeric(industry_df[amount_col], errors='coerce').dropna()
                    total = amounts.sum()
                    top3 = amounts.nlargest(3).sum()
                    ratio = top3 / total * 100 if total > 0 else 0

                    # 评分
                    if ratio > 40:
                        score = 75
                    elif ratio > 30:
                        score = 60
                    elif ratio > 25:
                        score = 50
                    else:
                        score = 35

                    top_names = ""
                    if name_col:
                        top3_idx = amounts.nlargest(3).index
                        names = industry_df.loc[top3_idx, name_col].tolist() if name_col else []
                        top_names = "、".join(str(n) for n in names[:3])

                    val_str = f"{top_names}集中" if top_names else f"{ratio:.0f}%"
                    logger.info(f"      行业集中度(Top3): {ratio:.1f}% → {val_str}")
                    return score, val_str
        except Exception as e:
            logger.warning(f"      行业集中度异常: {e}")

        # P2-1 修复(2026-10-10): akshare 失败时，回退到既有 HHI 历史的最后一点，
        # 不再留「数据待获取」空值（该 history 由 collect_microstructure.py 真采集）。
        prev = load_json("timing_scores.json")
        prev_ind = (prev.get("dimensions", {}).get("micro_structure", {})
                    .get("indicators", {}).get("industry_concentration", {}))
        hist = prev_ind.get("history") or []
        if hist:
            last = hist[-1].get("value")
            if last is not None:
                logger.info(f"      行业集中度(HHI): 回退历史末点 {last}")
                return prev_ind.get("score", 65), last
        return prev_ind.get("score", 65), prev_ind.get("value", "电子通信拥挤")

    def _calc_concentration_trend(self):
        """交易集中度趋势"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {}).get("concentration_trend", {}).get("value", "加速集中")
        prev_score = prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {}).get("concentration_trend", {}).get("score", 65)
        logger.info(f"      集中度趋势: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_small_large_ratio(self):
        """中证1000/沪深300比值"""
        try:
            ak = self.init_akshare()
            # 获取中证1000和沪深300
            csi1000_df = safe_ak_call(ak.stock_zh_index_daily, symbol="sh000852")
            hs300_df = safe_ak_call(ak.stock_zh_index_daily, symbol="sh000300")

            if (csi1000_df is not None and not csi1000_df.empty and
                hs300_df is not None and not hs300_df.empty and
                'close' in csi1000_df.columns and 'close' in hs300_df.columns):

                csi1000_df['date'] = pd.to_datetime(csi1000_df['date'])
                hs300_df['date'] = pd.to_datetime(hs300_df['date'])

                csi_close = csi1000_df.set_index('date')['close'].astype(float).dropna()
                hs300_close = hs300_df.set_index('date')['close'].astype(float).dropna()

                # 对齐日期
                common = csi_close.index.intersection(hs300_close.index)
                if len(common) >= 60:
                    ratio = csi_close.loc[common] / hs300_close.loc[common]

                    current_ratio = ratio.iloc[-1]
                    # 计算布林线 %B
                    if len(ratio) >= 250:
                        ma242 = ratio.tail(242).mean()
                        std242 = ratio.tail(242).std()
                        upper = ma242 + 2 * std242
                        lower = ma242 - 2 * std242
                        pct_b = (current_ratio - lower) / (upper - lower) if upper != lower else 0.5
                    else:
                        pct_b = 0.5

                    # 趋势: 20日均线 vs 60日均线
                    ma20 = ratio.tail(20).mean()
                    ma60 = ratio.tail(60).mean()
                    trend = "回升" if ma20 > ma60 else "下行"

                    # 评分: 比值下行 → 小盘弱 → 评分偏中性 (不追涨信号)
                    if pct_b < 0.1:
                        score = 55  # 极弱但有反转潜力
                        status = f"小盘极弱(%B={pct_b:.2f})"
                    elif pct_b < 0.3:
                        score = 50
                        status = f"小盘偏弱(%B={pct_b:.2f})"
                    elif pct_b < 0.7:
                        score = 45
                        status = f"小盘中性(%B={pct_b:.2f})"
                    else:
                        score = 35  # 小盘走强 → 底部信号
                        status = f"小盘走强(%B={pct_b:.2f})"

                    val_str = f"{status}"
                    logger.info(f"      中证1000/300: {current_ratio:.4f}, %B={pct_b:.2f}, 趋势={trend}")
                    return score, val_str
        except Exception as e:
            logger.warning(f"      大小盘比值异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {})
                .get("csi1000_hs300_ratio", {}).get("score", 62),
                prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {})
                .get("csi1000_hs300_ratio", {}).get("value", "数据待获取"))

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------
    def _weighted_score(self, indicators: dict) -> float:
        """计算维度加权得分"""
        total_weight = 0
        total_score = 0
        for key, ind in indicators.items():
            w = ind.get("sub_weight", 0)
            s = ind.get("score", 50)
            if w > 0 and s is not None:
                total_weight += w
                total_score += w * s
        if total_weight == 0:
            return 50.0
        return round(total_score / total_weight, 1)

    # ----------------------------------------------------------
    # 信号 & 仓位建议
    # ----------------------------------------------------------
    def generate_signals(self, dimensions):
        """生成顶底信号列表"""
        bottom_signals = []
        top_signals = []

        # 从各维度指标中生成
        val = dimensions.get("valuation", {}).get("indicators", {})
        liq = dimensions.get("liquidity", {}).get("indicators", {})
        eb = dimensions.get("equity_bond", {}).get("indicators", {})
        cf = dimensions.get("capital_flow", {}).get("indicators", {})
        sent = dimensions.get("sentiment", {}).get("indicators", {})
        micro = dimensions.get("micro_structure", {}).get("indicators", {})

        # 底部信号
        def check_bottom(condition, dim, signal_text, stars):
            if condition:
                bottom_signals.append({"dimension": dim, "signal": signal_text, "stars": stars, "active": True})
            else:
                bottom_signals.append({"dimension": dim, "signal": signal_text, "stars": stars, "active": False})

        def check_top(condition, dim, signal_text, stars):
            if condition:
                top_signals.append({"dimension": dim, "signal": signal_text, "stars": stars, "active": True})
            else:
                top_signals.append({"dimension": dim, "signal": signal_text, "stars": stars, "active": False})

        # 估值
        pe_pct = val.get("hs300_pe_percentile", {}).get("value")
        check_bottom(pe_pct and pe_pct < 10, "估值", "PE/PB处于历史10%分位以下", 5)
        check_bottom(val.get("break_net_rate", {}).get("value") and val.get("break_net_rate", {}).get("value", 0) > 10, "估值", "破净率>10%", 4)
        check_bottom(val.get("buffett_ratio", {}).get("value") and val.get("buffett_ratio", {}).get("value", 100) < 70, "估值", "巴菲特指标<70%", 3)

        # 流动性
        sf_label = liq.get("social_financing_trend", {}).get("value_label", "") or ""
        check_bottom("回升" in sf_label or "触底" in sf_label, "宏观流动性", "社融增速触底回升", 5)
        rate_val = liq.get("interest_rate", {}).get("value", "")
        check_bottom("降息" in rate_val, "宏观流动性", "降息降准周期开启", 4)

        # 股债性价比
        erp_val_str = eb.get("hs300_erp", {}).get("value", "0%")
        try:
            erp_num = float(erp_val_str.replace('%', ''))
        except:
            erp_num = 5
        check_bottom(erp_num > 6, "股债性价比", "ERP>6%", 4)

        div_val_str = eb.get("dividend_bond_spread_red", {}).get("value", "0%")
        try:
            div_num = float(div_val_str.replace('%', ''))
        except:
            div_num = 2.8
        check_bottom(div_num > 3, "股债性价比", "红利指数股债利差>3%", 3)

        # 情绪
        fg_val = sent.get("fear_greed_index", {}).get("value", 50)
        if isinstance(fg_val, str):
            try:
                fg_val = float(fg_val)
            except:
                fg_val = 50
        # P0-3: 统一极度贪婪判断 = score>85 且 QVIX<14
        fgp_data = load_json("feargreed_pointer.json")
        qvix_val = fgp_data.get("qvix_close") if fgp_data else None
        is_extreme_greed = (fg_val > 85) and (qvix_val is not None and qvix_val < 14)
        check_bottom(fg_val < 20, "市场情绪", "恐贪指数<20(极度恐惧)", 3)
        check_top(is_extreme_greed, "市场情绪", "恐贪指数>85且QVIX<14(极度贪婪)", 4)

        turnover_str = sent.get("turnover_rate", {}).get("value", "2.5%")
        try:
            turnover_num = float(turnover_str.replace('%', ''))
        except:
            turnover_num = 2.5
        check_bottom(turnover_num < 1.5, "市场情绪", "换手率<1.5%(地量)", 2)
        check_top(turnover_num > 5, "市场情绪", "换手率>5%且量价背离", 3)

        # 资金面
        margin_str = cf.get("margin_ratio", {}).get("value", "2.66%")
        try:
            margin_num = float(margin_str.replace('%', ''))
        except:
            margin_num = 2.66
        check_bottom(margin_num < 2.0, "资金面", "两融<2%", 3)
        check_top(margin_num > 3.5, "资金面", "两融>3.5%", 4)

        # 估值顶部
        check_top(pe_pct and pe_pct > 90, "估值", "PE/PB>90%历史分位", 5)
        check_top(val.get("buffett_ratio", {}).get("value") and val.get("buffett_ratio", {}).get("value", 100) > 100, "估值", "巴菲特指标>100%", 4)

        # 微观结构
        crowd_str = micro.get("crowding_ratio", {}).get("value", "0%")
        try:
            crowd_num = float(crowd_str.replace('%', ''))
        except:
            crowd_num = 50
        check_top(crowd_num > 48, "微观结构", "交易集中度>48%", 4)
        check_bottom(crowd_num < 40, "微观结构", "交易拥挤度从极端回落至40%以下", 2)

        return {"bottom": bottom_signals, "top": top_signals}

    def generate_triggers(self, dimensions):
        """生成加减仓触发条件"""
        add_triggers = []
        reduce_triggers = []

        eb = dimensions.get("equity_bond", {}).get("indicators", {})
        liq = dimensions.get("liquidity", {}).get("indicators", {})
        sent = dimensions.get("sentiment", {}).get("indicators", {})
        cf = dimensions.get("capital_flow", {}).get("indicators", {})
        micro = dimensions.get("micro_structure", {}).get("indicators", {})

        # 加仓条件
        sf_label = liq.get("social_financing_trend", {}).get("value_label", "") or ""
        add_triggers.append({"condition": "社融增速触底回升(连续3个月环比改善)", "met": "回升" in sf_label or "触底" in sf_label})

        # [P0-1 修复 2026-10-10] 恐贪比较对象统一读单一真值源 feargreed_pointer.json → score，
        # 不再取 timing_scores 内的僵尸值；缺源时保守取中性 50（两条恐贪触发均不触发）。
        fg_val = self._fear_greed_score_ssot()
        if fg_val is None:
            fg_val = 50
        # P0-3: 读取 QVIX 用于极度贪婪双确认
        fgp_data = load_json("feargreed_pointer.json")
        qvix_val = fgp_data.get("qvix_close") if fgp_data else None
        add_triggers.append({"condition": "恐贪指数回落至20以下(极度恐惧)", "met": fg_val < 20})

        crowd_str = micro.get("crowding_ratio", {}).get("value", "0%")
        try: crowd_num = float(crowd_str.replace('%', ''))
        except: crowd_num = 50
        add_triggers.append({"condition": "交易拥挤度回落至40%以下", "met": crowd_num < 40})

        erp_str = eb.get("hs300_erp", {}).get("value", "0%")
        try: erp_num = float(erp_str.replace('%', ''))
        except: erp_num = 5
        add_triggers.append({"condition": "ERP突破6%", "met": erp_num > 6})

        # 减仓条件
        margin_str = cf.get("margin_ratio", {}).get("value", "2.66%")
        try: margin_num = float(margin_str.replace('%', ''))
        except: margin_num = 2.66
        reduce_triggers.append({"condition": "两融占比突破3.5%", "met": margin_num > 3.5})
        reduce_triggers.append({"condition": "恐贪指数>85且QVIX<14(极度贪婪双确认)", 
                     "met": (fg_val > 85) and (qvix_val is not None and qvix_val < 14)})

        sf_score = liq.get("social_financing_trend", {}).get("score", 50)
        reduce_triggers.append({"condition": "社融增速进一步下行", "met": sf_score > 65})

        # F3①-4: 美联储重启加息 — 跟随 hiking_cycle 状态（非硬编码）
        _fed_st = self._fed_state_for_triggers if hasattr(self, '_fed_state_for_triggers') else {}
        reduce_triggers.append({"condition": "美联储重启加息", "met": bool(_fed_st.get("hiking_cycle", False))})

        # 估值触发
        div_str = eb.get("dividend_bond_spread_hs300", {}).get("value", "0%")
        try: div_num = float(div_str.replace('%', ''))
        except: div_num = 1
        reduce_triggers.append({"condition": "股息率-国债利差(沪深300)降至0.3%以下", "met": div_num < 0.3})

        # [P0-1] 输出前断言：方向必须与单一真值一致（score>85且QVIX<14 → 减仓项 true；score<20 → 加仓项 true）
        _fg_add = [t for t in add_triggers if t["condition"].startswith("恐贪指数回落至20以下")]
        _fg_red = [t for t in reduce_triggers if t["condition"].startswith("恐贪指数>85")]
        is_extreme_greed_for_assert = (fg_val > 85) and (qvix_val is not None and qvix_val < 14)
        if is_extreme_greed_for_assert:
            assert _fg_red and _fg_red[0]["met"] is True, \
                f"P0-1 断言失败: 恐贪={fg_val}>85且QVIX<14 但极度贪婪触发未置 true"
        elif fg_val < 20:
            assert _fg_add and _fg_add[0]["met"] is True, \
                f"P0-1 断言失败: 恐贪={fg_val}<20 但『恐贪指数回落至20以下』未置 true"
        else:
            assert not (_fg_add and _fg_add[0]["met"]) and not (_fg_red and _fg_red[0]["met"]), \
                f"P0-1 断言失败: 恐贪={fg_val} 落在 20~80 区间但恐贪触发被置 true"

        # F3④ B1: 全球主导权否决 → 冻结全部加仓触发器（豁免：R7 催化剂确认保留 A 段左侧布局）
        _dom = getattr(self, '_dominance_for_triggers', None)
        if _dom and _dom.get("veto"):
            if _dom.get("exemption_active"):
                add_triggers.append({"condition": "F3④ 全球主导权否决中·R7 催化剂豁免生效：保留 A 段左侧布局（其余加仓信号维持冻结判定）", "met": True, "f3_exemption": True})
            else:
                for _t in add_triggers:
                    _t["met"] = False
                    _t["frozen_by_f3_dominance"] = True
                add_triggers.append({"condition": "F3④ 全球主导权否决：全部加仓信号冻结（三阈值满足≥2）", "met": True, "f3_veto": True})

        return {"add_position": add_triggers, "reduce_position": reduce_triggers}

    def generate_position_advice(self, composite_score, dimensions):
        """生成仓位建议 (v4.1 四级等宽)"""
        # 四级等宽仓位映射
        if composite_score < 25:
            equity_range = "55%-70%"
            base_equity = 62
            status = "极度恐惧，逆向布局"
        elif composite_score < 50:
            equity_range = "40%-55%"
            base_equity = 47
            status = "中性偏恐惧，正常配置"
        elif composite_score < 75:
            equity_range = "25%-40%"
            base_equity = 32
            status = "中性偏贪婪，逐步兑现"
        else:
            equity_range = "10%-25%"
            base_equity = 17
            status = "极度贪婪，过热清仓"

        cash_low = 100 - base_equity
        cash_range = f"{cash_low-5}%-{cash_low+10}%"

        # 风格配置
        micro_score = dimensions.get("micro_structure", {}).get("score", 50)
        crowd_str = dimensions.get("micro_structure", {}).get("indicators", {}).get("crowding_ratio", {}).get("value", "50%")
        try: crowd_num = float(crowd_str.replace('%', ''))
        except: crowd_num = 50

        # 红利/高股息
        eb_score = dimensions.get("equity_bond", {}).get("score", 50)
        if eb_score < 40:
            dividend_range = "25%-35%"
            dividend_note = "股债性价比突出，高股息安全边际充足"
        elif eb_score < 55:
            dividend_range = "20%-30%"
            dividend_note = "股息率-国债利差显示安全边际充足"
        else:
            dividend_range = "15%-20%"
            dividend_note = "股债性价比一般，适度配置"

        # 科技成长
        if crowd_num > 48:
            tech_range = "5%-10%"
            tech_note = "拥挤度仍高，降低配置"
        elif crowd_num > 40:
            tech_range = "10%-15%"
            tech_note = "拥挤度偏高，适度降低"
        else:
            tech_range = "15%-20%"
            tech_note = "拥挤度回落，可适度增配"

        # 大盘价值
        value_range = "10%-15%"
        value_note = "维持"

        # 小盘 (v4.1: 从style_rotation读取)
        style_rot = dimensions.get("_style_rotation", {})
        ls_status = style_rot.get("large_small", {}).get("status", "")
        if "偏弱" in ls_status or "极弱" in ls_status:
            small_range = "5%-8%"
            small_note = "比值仍处下行通道，暂不追涨"
        elif "走强" in ls_status or "占优" in ls_status:
            small_range = "8%-12%"
            small_note = "小盘走强，可适度增配"
        else:
            small_range = "8%-12%"
            small_note = "大小盘均衡，中性配置"

        # status already set in 4-level mapping above

        return {
            "equity_range": equity_range,
            "breakdown": {
                "红利_高股息": {"range": dividend_range, "note": dividend_note},
                "科技成长": {"range": tech_range, "note": tech_note},
                "大盘价值": {"range": value_range, "note": value_note},
                "小盘_中证1000": {"range": small_range, "note": small_note},
                "现金_债券": {"range": cash_range, "note": "防御配置"}
            }
        }, status

    # ----------------------------------------------------------
    # 主计算方法
    # ----------------------------------------------------------
    def compute_all(self) -> dict:
        """计算完整的六维择时评分"""
        logger.info("\n" + "=" * 60)
        logger.info("开始计算六维择时评分 (timing_scores.json)")
        logger.info("=" * 60)

        dimensions = {}

        # 逐维度计算，单个失败不影响其他
        for name, method in [
            ("valuation", self.calc_valuation),
            ("liquidity", self.calc_liquidity),
            ("equity_bond", self.calc_equity_bond),
            ("capital_flow", self.calc_capital_flow),
            ("sentiment", self.calc_sentiment),
            ("micro_structure", self.calc_micro_structure),
        ]:
            try:
                dimensions[name] = method()
                logger.info(f"    → {dimensions[name]['name']}: {dimensions[name]['score']}分 ({dimensions[name]['signal']})")
            except Exception as e:
                logger.error(f"    ✗ {name} 计算失败: {e}")
                traceback.print_exc()
                # 使用上期数据
                prev = load_json("timing_scores.json")
                dimensions[name] = prev.get("dimensions", {}).get(name, {
                    "name": name, "score": 50, "weight": 15,
                    "weighted_score": 7.5, "signal": "数据异常", "indicators": {}
                })

        # ============================================================
        # P1-7: 非线性权重×2 + 滞回 (2026-10-16)
        # ============================================================
        # 加载上一日滞回状态
        prev_history = load_json("timing_history.json")
        prev_hysteresis = {}
        prev_anchor_state = {}
        if prev_history and isinstance(prev_history, list) and len(prev_history) > 0:
            last_record = prev_history[-1]
            prev_hysteresis = last_record.get("hysteresis_state", {})
            prev_anchor_state = last_record.get("anchor_state", {})

        # 应用非线性权重调整
        dimensions, today_hysteresis = apply_nonlinear_weights(dimensions, prev_hysteresis)

        # 打印非线性权重调整结果
        for dim_name, dim_data in dimensions.items():
            if dim_name.startswith("_"):
                continue
            orig_w = dim_data.get("_orig_weight", dim_data.get("weight", 15))
            curr_w = dim_data.get("weight", 15)
            if curr_w != orig_w:
                logger.info(f"    ⚡ P1-7 {dim_data.get('name', dim_name)}: 权重 {orig_w}% → {curr_w}% (维度分{dim_data['score']}极端)")

        # 综合得分
        total_weighted = sum(d.get("weighted_score", 0) for d in dimensions.values())
        total_weight = sum(d.get("weight", 0) for d in dimensions.values())
        composite_score = round(total_weighted / total_weight * 100 / 100, 1) if total_weight > 0 else 50.0
        # 简化: 直接加权平均
        composite_score = round(sum(d.get("weighted_score", 0) for d in dimensions.values()) / sum(d.get("weight", 0) for d in dimensions.values()) * 100, 1) if sum(d.get("weight", 0) for d in dimensions.values()) > 0 else 50.0

        # 重新计算: weighted_score = score * weight/100
        composite_score = round(
            sum(d["score"] * d["weight"] for d in dimensions.values()) /
            sum(d["weight"] for d in dimensions.values()),
            1
        )

        # ============================================================
        # P1-7: 极端锚定状态机 (2026-10-16)
        # ============================================================
        today = today_str()
        anchor_state, target_equity, anchor_note = apply_extreme_anchoring(
            composite_score, prev_anchor_state, today
        )
        if anchor_note:
            logger.info(f"    🔗 P1-7 锚定状态: {anchor_state['current']} — {anchor_note}")
        if target_equity is not None:
            logger.info(f"    🎯 P1-7 目标仓位: {target_equity}% (覆盖常规映射)")

        # R7: 催化剂三路代理（提前计算：R7 状态机耦合 + F3④ 豁免输入共用本次新值，不读昨日文件）
        manual_cfg = load_json("config_overrides.json") or {}
        _cn_supplement = load_json("cn_macro_supplement.json")
        _cn_macro_for_cat = load_json("cn_macro.json")
        try:
            catalyst_state = compute_catalyst_state(_cn_supplement, _cn_macro_for_cat, manual_cfg)
        except Exception as e:
            logger.warning(f"  ⚠ R7 催化剂计算失败（不阻断主流程）: {e}")
            catalyst_state = None

        # ============================================================
        # P1-6: 价格趋势过滤 (2026-10-18)
        # ============================================================
        prev_trend_state = {}
        if prev_history and isinstance(prev_history, list) and len(prev_history) > 0:
            prev_trend_state = prev_history[-1].get("trend_state", {})
        trend_df = fetch_trend_index_daily()
        trend_state, trend_note = compute_trend_state(trend_df, prev_trend_state, today)
        if trend_note:
            logger.info(f"    📈 P1-6 趋势状态: {trend_state.get('trend')} — {trend_note}")

        # R7: 冲突状态卡（位置分位区×趋势×熊形态→信号优先级；催化剂耦合极端区定性开关）
        try:
            r7_state = compute_r7_state(prev_history, trend_state, trend_df, catalyst_state)
            logger.info(f"    ⚖️ R7: {r7_state.get('position_zone')}({r7_state.get('position_pct')}%) × {trend_state.get('trend')} × {r7_state.get('bear_form')} → {r7_state.get('verdict')}" + (f" [冲突{r7_state.get('conflict')}]" if r7_state.get('conflict') else ""))
        except Exception as e:
            logger.warning(f"  ⚠ R7 状态机失败（不阻断主流程）: {e}")
            r7_state = None

        # ============================================================
        # P1-9: 预警中间态状态机 (2026-10-18)
        # ============================================================
        prev_warning_state = {}
        prev_fed_state = {}
        if prev_history and isinstance(prev_history, list) and len(prev_history) > 0:
            prev_warning_state = prev_history[-1].get("warning_state", {})
            prev_fed_state = prev_history[-1].get("fed_state", {})
        # 取近5日composite历史（用于计算单周变化）
        recent_composites = []
        if prev_history and isinstance(prev_history, list):
            recent_composites = [r.get("composite_score", 50) for r in prev_history[-5:]]
        recent_composites.append(composite_score)
        # 读取恐贪数据（P0-3 统一阈值判断用）
        fg_data = load_json("feargreed_pointer.json") or {}
        warning_state, warning_target_equity, warning_note = apply_warning_state(
            composite_score, recent_composites, prev_warning_state, fg_data, anchor_state, trend_state
        )
        if warning_note:
            logger.info(f"    ⚠️ P1-9 预警状态: {warning_state['current']} — {warning_note}")
        # P1-9 覆盖逻辑：仅在 P1-7 未锚定且预警态激活时覆盖
        if warning_target_equity is not None and anchor_state.get("current", "normal") == "normal":
            target_equity = warning_target_equity
            logger.info(f"    🎯 P1-9 目标仓位: {target_equity}% (覆盖常规映射)")

        # P1-8: 美联储双层数据加载（层判定在 A* 联动之后取小——[CIO验收D2修复]
        # 防止 A*+strong_bear 无条件赋值 55% 击穿 Fed cap 32%/25%）
        fed_watch_data = load_json("fed_watch.json") or {}
        us_macro_data = load_json("us_macro.json") or {}
        fed_state, fed_cap = apply_fed_layer(fed_watch_data, us_macro_data, prev_fed_state, manual_cfg)

        # F3①-3: USDCNY 年贬 + 外储（合入 us_macro history；不立即落盘，由主流程 ssot 统一写）
        try:
            f3_fx = compute_f3_usdcny_and_fx(us_macro_data)
            if f3_fx:
                us_macro_history = (us_macro_data.get("history") or {})
                for k, v in f3_fx.items():
                    if v and isinstance(v, dict) and "date" in v:
                        lst = us_macro_history.setdefault(k, [])
                        # 同日期去重
                        lst = [x for x in lst if x.get("date") != v["date"]]
                        lst.append({kk: vv[0] if isinstance(vv, tuple) else vv for kk, vv in v.items()})
                        lst.sort(key=lambda x: x.get("date", ""))
                        us_macro_history[k] = lst
                us_macro_data["history"] = us_macro_history
                logger.info(f"  ✓ F3① USDCNY/外储 合入 us_macro history: {list(f3_fx.keys())}")
        except Exception as e:
            logger.warning(f"  ⚠ F3① USDCNY/外储合流失败（不阻断主流程）: {e}")

        # F3④: 全球主导权仲裁（三阈值≥2 → 仓位否决 B1+豁免，松子实现；catalyst 已在趋势段前算好）
        try:
            dominance_state = compute_global_dominance(
                us_macro_data,
                cn_macro_data=_cn_macro_for_cat,
                fx_rates=load_json("fx_rates_em.json"),
                cn_supplement=_cn_supplement,
                fx_sina=load_json("fx_rates_sina.json"),
                r7_has_catalyst=(catalyst_state or {}).get("has_catalyst"))
        except Exception as e:
            logger.warning(f"  ⚠ F3④ 主导权仲裁失败（不阻断主流程）: {e}")
            dominance_state = None

        # P1-6: A* 与趋势联动（D* 方向不 veto——卖侧无条件优先）
        if anchor_state.get("current") == "A_star" and target_equity is not None:
            _trend = trend_state.get("trend")
            if _trend == "strong_bear":
                target_equity = P16_A_STAR_BEAR_TARGET
                logger.info(f"    📉 P1-6 A* 联动: 强空头 → target 降档 {P16_A_STAR_BEAR_TARGET}%（A 区间下沿）")
            elif _trend == "weak_bear":
                trend_state["a_star_unconfirmed"] = True
                logger.info(f"    📉 P1-6 A* 联动: 弱空头 → 维持 65% 但页面标注「趋势未确认」")

        # P1-8 取小：A* 联动/预警/锚定之后的最终保守者（Fed cap 不可被击穿）
        if fed_state.get("layer", "none") != "none":
            logger.info(f"    🦅 P1-8 Fed 层: {fed_state['layer']} cap={fed_state['cap']}% — {fed_state.get('note','')}")
        if fed_cap is not None:
            if target_equity is None or fed_cap < target_equity:
                target_equity = fed_cap
                logger.info(f"    🎯 P1-8 Fed 仓位上限生效: {fed_cap}%")

        # F3④ 否决取小（B1）：与 Fed cap 同位取最小，任何情况不可被击穿
        if dominance_state and dominance_state.get("veto"):
            _vcap = dominance_state.get("veto_cap", F3_DOM_VETO_CAP)
            if target_equity is None or _vcap < target_equity:
                target_equity = _vcap
            logger.info(f"    🌐 F3④ 全球主导权否决: 仓位上限 {_vcap}%（{dominance_state.get('satisfied_n')}/3 阈值满足）")


        # 风格轮动 (从上期数据继承) — v4.1: 提前计算以供 generate_position_advice 使用
        prev = load_json("timing_scores.json")
        style_rotation = prev.get("style_rotation", {})
        # 更新大小盘比值
        micro = dimensions.get("micro_structure", {}).get("indicators", {})
        ratio_data = micro.get("csi1000_hs300_ratio", {})
        if ratio_data:
            _rv = str(ratio_data.get("value", ""))
            if "偏弱" in _rv or "极弱" in _rv:
                _st, _sig = "小盘偏弱", "不追涨"
            elif "走强" in _rv or "占优" in _rv:
                _st, _sig = "小盘走强", "可关注"
            else:
                _st, _sig = "大小盘均衡", "中性均衡"
            style_rotation["large_small"] = {
                "indicator": "中证1000/沪深300比值",
                "current_value": ratio_data.get("value", ""),
                "status": _st,
                "signal": _sig,
                "detail": f"小盘风格{ratio_data.get('value', '待更新')}"
            }

        # 将 style_rotation 传入 dimensions 供 generate_position_advice 使用
        dimensions["_style_rotation"] = style_rotation

        # 生成信号和建议
        signals = self.generate_signals(dimensions)
        # F3①-4: 传递 fed_state 到 generate_triggers（供"美联储重启加息"判定）
        self._fed_state_for_triggers = fed_state
        # F3④: 传递主导权状态（供加仓触发器冻结判定）
        self._dominance_for_triggers = dominance_state
        triggers = self.generate_triggers(dimensions)
        position_advice, market_status = self.generate_position_advice(composite_score, dimensions)

        # 清理 _orig_weight 内部字段（不写入 JSON）
        for _dn, _dd in dimensions.items():
            _dd.pop("_orig_weight", None)

        # P1-7/P1-9/P1-8: 如果锚定/预警/Fed层激活，覆盖仓位区间
        if target_equity is not None:
            position_advice["equity_range"] = f"{target_equity}%"
            position_advice["base_equity"] = target_equity
            position_advice["anchor_override"] = True
            if warning_state.get("current", "normal") != "normal" and anchor_state.get("current", "normal") == "normal":
                position_advice["warning_override"] = True
            if fed_state.get("layer", "none") != "none":
                position_advice["fed_override"] = True
                position_advice["fed_layer"] = fed_state["layer"]
        # P1-8 禁加成长股标注（裁决：预期层起即禁加创业板+科创50）
        if fed_state.get("growth_ban"):
            position_advice["fed_growth_ban"] = fed_state["growth_ban"]
            position_advice["fed_note"] = fed_state.get("note", "")

        # [P2-11] 迟滞阶段（SSOT 下发前端，替代双端裸映射）
        prev_stage_for_hyst = prev_history[-1].get("stage") if prev_history else None
        stage_hysteresis = apply_stage_hysteresis(composite_score, prev_stage_for_hyst)
        if prev_stage_for_hyst and stage_hysteresis != prev_stage_for_hyst:
            logger.info(f"    🔄 P2-11 阶段切换: {prev_stage_for_hyst} → {stage_hysteresis}（composite={composite_score}）")
        elif prev_stage_for_hyst:
            _raw = "A" if composite_score < 25 else ("B" if composite_score < 50 else ("C" if composite_score < 75 else "D"))
            if _raw != stage_hysteresis:
                logger.info(f"    🛡️ P2-11 迟滞生效: 裸映射={_raw} 维持={stage_hysteresis}（composite={composite_score}，降温需确认）")

        output = {
            "update_date": today_str(),
            "version": "v4.1",
            "composite_score": composite_score,
            "stage": stage_hysteresis,
            "position_range": position_advice["equity_range"],
            "market_status": market_status,
            "dimensions": dimensions,
            "style_rotation": style_rotation,
            "signals": signals,
            "triggers": triggers,
            "position_advice": position_advice,
            "p1_7_state": {
                "hysteresis": today_hysteresis,
                "anchor": anchor_state
            },
            "p1_9_state": warning_state,
            "p1_6_state": trend_state,
            "p1_8_state": fed_state,
            "global_dominance": dominance_state,
            "catalyst_state": catalyst_state,
            "r7_state": r7_state
        }

        logger.info(f"\n  📊 综合得分: {composite_score}")
        logger.info(f"  📊 仓位区间: {position_advice['equity_range']}")
        logger.info(f"  📊 市场状态: {market_status}")

        return output


# ============================================================
# 右侧趋势确认评分引擎 (v2.0 两层混合)
# ============================================================

class TimingRightEngine:
    """
    右侧趋势确认模块
    =================
    24个信号 (牛市12 + 熊市12)，满分各120分
    第一层：信号灯（条件触发 OR逻辑，逐级升级）
    第二层：打分制补充（仅无灯时启用）
    """

    def __init__(self):
        self.ak = None
        self._idx_df = None  # 缓存沪深300日K线
        self._idx_weekly = None  # 缓存周K线
        self._idx_monthly = None  # 缓存月K线
        self._today = today_str()

    def init_akshare(self):
        if self.ak is None:
            import akshare as ak
            self.ak = ak
        return self.ak

    # ---------- 数据获取辅助 ----------

    def _get_index_daily(self, symbol="sh000300"):
        """获取指数日K线（沪深300）"""
        if self._idx_df is not None:
            return self._idx_df
        try:
            ak = self.init_akshare()
            # [P1-10 2026-10-18] MACD两源统一：默认腾讯源(stock_zh_index_daily_tx)，与 confirm_signals
            # 同链路（月线MACD SSOT=193.228）。修复前新浪源月DIFF=147.45 与 SSOT 不一致（P0-4遗留）。
            # 注：sh880009(全A中位数)腾讯源无数据，_sig_median_sync_* 调用点保留新浪源，不改本函数。
            df = safe_ak_call(ak.stock_zh_index_daily_tx, symbol=symbol)
            if df is not None and not df.empty:
                # 腾讯源无 volume(成交量)列，以 amount(成交额)替代——量能类信号口径随之变为成交额，
                # 相对判断（放量/缩量）结论不受影响（价格短期变动小，量额高度同向）
                if 'volume' not in df.columns and 'amount' in df.columns:
                    df['volume'] = df['amount']
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                self._idx_df = df
                return df
        except Exception as e:
            logger.warning(f"      获取指数日K失败: {e}")
        return pd.DataFrame()

    def _get_weekly_kline(self, symbol="sh000300"):
        """将日K重采样为周K"""
        if self._idx_weekly is not None:
            return self._idx_weekly
        df = self._get_index_daily(symbol)
        if df.empty:
            return pd.DataFrame()
        try:
            df = df.set_index('date')
            weekly = df.resample('W').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()
            weekly = weekly.reset_index()
            self._idx_weekly = weekly
            return weekly
        except Exception as e:
            logger.warning(f"      周K重采样失败: {e}")
            return pd.DataFrame()

    def _get_monthly_kline(self, symbol="sh000300"):
        """将日K重采样为月K"""
        if self._idx_monthly is not None:
            return self._idx_monthly
        df = self._get_index_daily(symbol)
        if df.empty:
            return pd.DataFrame()
        try:
            df = df.set_index('date')
            monthly = df.resample('ME').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()
            monthly = monthly.reset_index()
            # [P1-10 2026-10-18] 月K构造对齐 confirm_signals SSOT：末月交易日<10 视为不完整月排除
            # （月线信号以走完的K线为准，防月内波动致信号反复；P0-4 裁决 DIFF=193.228 即此口径）
            if len(monthly) > 0:
                last_period = monthly['date'].iloc[-1].to_period('M')
                last_days = int((df.index.to_period('M') == last_period).sum())
                if last_days < 10:
                    monthly = monthly.iloc[:-1]
            self._idx_monthly = monthly
            return monthly
        except Exception as e:
            logger.warning(f"      月K重采样失败: {e}")
            return pd.DataFrame()

    def _calc_macd(self, close_series, fast=12, slow=26, signal=9):
        """计算MACD: 返回 (diff, dea, macd_hist)"""
        ema_fast = close_series.ewm(span=fast, adjust=False).mean()
        ema_slow = close_series.ewm(span=slow, adjust=False).mean()
        diff = ema_fast - ema_slow
        dea = diff.ewm(span=signal, adjust=False).mean()
        hist = (diff - dea) * 2
        return diff, dea, hist

    # ---------- 牛市早期信号 ----------

    def _sig_standing_60ma(self):
        """站稳60日线：连续5日站上60日线 + 60日线拐头向上"""
        df = self._get_index_daily()
        if df.empty or len(df) < 65:
            return False, "数据不足"
        try:
            df['ma60'] = df['close'].rolling(60).mean()
            last5 = df.tail(5)
            above = (last5['close'] > last5['ma60']).all()
            # 60日线拐头: 近5日MA60斜率>0
            ma60_recent = df['ma60'].tail(10).dropna()
            turning_up = len(ma60_recent) >= 5 and ma60_recent.iloc[-1] > ma60_recent.iloc[-5]
            triggered = bool(above and turning_up)
            val = f"{'站上' if above else '未站稳'}60日线, MA60{'上行' if turning_up else '走平/下行'}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_weekly_macd_gold(self):
        """周线MACD金叉：周线DIFF上穿DEA"""
        df = self._get_weekly_kline()
        if df.empty or len(df) < 30:
            return False, "数据不足"
        try:
            diff, dea, _ = self._calc_macd(df['close'])
            # 金叉: 当前DIF>DEA 且 前一期DIF<=DEA
            cross_up = (diff.iloc[-1] > dea.iloc[-1]) and (diff.iloc[-2] <= dea.iloc[-2])
            # 或近4周内有金叉
            recent_cross = False
            for i in range(-4, 0):
                if i-1 >= -len(diff) and diff.iloc[i] > dea.iloc[i] and diff.iloc[i-1] <= dea.iloc[i-1]:
                    recent_cross = True
            triggered = bool(cross_up or recent_cross)
            val = f"DIFF={diff.iloc[-1]:.2f}, DEA={dea.iloc[-1]:.2f}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_margin_recovery(self):
        """两融余额回升：连续4周环比正增长"""
        try:
            ak = self.init_akshare()
            df = safe_ak_call(ak.stock_margin_underlying_info_szse)
            # 尝试获取两融汇总数据
            df2 = safe_ak_call(ak.stock_margin_sse, start_date=(date.today() - timedelta(days=120)).strftime("%Y%m%d"))
            if df2 is not None and not df2.empty:
                # 取近5周数据
                if '融资余额' in df2.columns:
                    weekly_margin = df2['融资余额'].tail(5).values
                    if len(weekly_margin) >= 5:
                        rising = all(weekly_margin[i] > weekly_margin[i-1] for i in range(-3, 0))
                        val = f"近周融资余额: {weekly_margin[-1]:.0f}亿"
                        return bool(rising), val
            # fallback
            prev = load_json("timing_right_scores.json")
            sig = self._find_signal(prev, "两融余额回升")
            if sig:
                return sig.get("triggered", False), sig.get("current_value", "数据待获取")
            return False, "数据待获取"
        except Exception as e:
            return False, f"数据获取异常: {e}"

    def _sig_volume_expansion(self):
        """成交放量：20日均量上穿60日均量"""
        df = self._get_index_daily()
        if df.empty or len(df) < 65:
            return False, "数据不足"
        try:
            df['vol_ma20'] = df['volume'].rolling(20).mean()
            df['vol_ma60'] = df['volume'].rolling(60).mean()
            # 当前20日均量>60日均量
            cross = df['vol_ma20'].iloc[-1] > df['vol_ma60'].iloc[-1]
            # 且近期发生上穿
            recent_cross = False
            for i in range(-5, 0):
                if (df['vol_ma20'].iloc[i] > df['vol_ma60'].iloc[i] and
                    df['vol_ma20'].iloc[i-1] <= df['vol_ma60'].iloc[i-1]):
                    recent_cross = True
            triggered = bool(cross and (recent_cross or df['vol_ma20'].iloc[-5:].gt(df['vol_ma60'].iloc[-5:]).all()))
            val = f"20日均量/60日均量={df['vol_ma20'].iloc[-1]/df['vol_ma60'].iloc[-1]:.2f}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_breadth_repair(self):
        """市场宽度修复：站上20日均线个股占比>50%且扩大"""
        try:
            ak = self.init_akshare()
            # 使用上证全A指数近似的宽度
            # 简化：用沪深300成分股站上20MA的比例
            df = self._get_index_daily()
            if df.empty or len(df) < 25:
                return False, "数据不足"
            df['ma20'] = df['close'].rolling(20).mean()
            above_pct = (df['close'].iloc[-1] > df['ma20'].iloc[-1])
            # 精确计算需要全市场个股数据，这里用指数近似
            # 宽度修复更多需要全市场统计，简化为指数在MA20上方
            expanding = df['close'].iloc[-5:].values[-1] > df['ma20'].iloc[-5:].values[0] if len(df) >= 25 else False
            val = f"指数{'站上' if above_pct else '未站上'}20日线"
            # 由于无法获取全市场个股数据，标记为近似
            return False, val + " (需全市场数据确认)"
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_industry_capital_buyback(self):
        """产业资本转增持：净增持转正且连续2周"""
        try:
            ak = self.init_akshare()
            # P2-2 修复(2026-10-10): akshare 无 stock_share_change 接口（AttributeError），
            # 产业资本(董监高/重要股东增减持)改用可用的交易所/东财接口，带 hasattr 守卫。
            df = safe_ak_call(_ak_industry_capital_df, ak)
            if df is not None and not df.empty:
                # 简化处理
                prev = load_json("timing_right_scores.json")
                sig = self._find_signal(prev, "产业资本转增持")
                if sig:
                    return sig.get("triggered", False), sig.get("current_value", "数据待获取")
            return False, "数据待获取"
        except Exception as e:
            return False, f"数据获取异常: {e}"

    def _sig_northbound_inflow(self):
        """北向配置型资金持续净流入：连续2周净流入>100亿"""
        try:
            ak = self.init_akshare()
            # P2-2 修复(2026-10-10): akshare 无 stock_hsgt_north_net_flow_in_em；
            # 改用 stock_hsgt_hist_em(symbol="北向资金")，并显式识别 2024-08 起停更。
            df = safe_ak_call(ak.stock_hsgt_hist_em, symbol="北向资金")
            if df is not None and not df.empty:
                col = '当日成交净买额' if '当日成交净买额' in df.columns else ('当日净流入' if '当日净流入' in df.columns else None)
                if col and '日期' in df.columns:
                    d = df[['日期', col]].dropna()
                    if d.empty:
                        return False, "北向资金已停更（2024-08 起无净买额数据）"
                    d = d.copy()
                    d['date'] = pd.to_datetime(d['日期'])
                    weekly_flow = d.set_index('date')[col].resample('W').sum()
                    if len(weekly_flow) >= 2:
                        last2 = weekly_flow.tail(2)
                        triggered = bool((last2 > 100).all())
                        val = f"近2周净流入: {last2.iloc[0]:.0f}亿, {last2.iloc[1]:.0f}亿"
                        return triggered, val
            return False, "数据待获取"
        except Exception as e:
            return False, f"数据获取异常: {e}"

    def _sig_volume_price_coord(self):
        """量价配合（正向）：指数上涨伴随成交额放大，量价齐升5日以上"""
        df = self._get_index_daily()
        if df.empty or len(df) < 10:
            return False, "数据不足"
        try:
            last5 = df.tail(5)
            price_up = last5['close'].iloc[-1] > last5['close'].iloc[0]
            vol_expanding = last5['volume'].iloc[-1] > last5['volume'].iloc[0]
            # 量价齐升: 上涨日成交量>下跌日
            coord_days = 0
            for i in range(1, len(last5)):
                if (last5['close'].iloc[i] > last5['close'].iloc[i-1] and
                    last5['volume'].iloc[i] > last5['volume'].iloc[i-1]):
                    coord_days += 1
                elif (last5['close'].iloc[i] < last5['close'].iloc[i-1] and
                      last5['volume'].iloc[i] < last5['volume'].iloc[i-1]):
                    coord_days += 1
            triggered = bool(price_up and vol_expanding and coord_days >= 3)
            val = f"5日量价配合度: {coord_days}/4天"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    # ---------- 牛市确认信号 ----------

    def _sig_above_250ma(self):
        """突破年线并站稳：连续5日站上250日线且不回落"""
        df = self._get_index_daily()
        if df.empty or len(df) < 255:
            return False, "数据不足"
        try:
            df['ma250'] = df['close'].rolling(250).mean()
            last5 = df.tail(5)
            above = (last5['close'] > last5['ma250']).all()
            val = f"{'站上' if above else '未站稳'}250日线, 当前{df['close'].iloc[-1]:.0f} vs MA250={df['ma250'].iloc[-1]:.0f}"
            return bool(above), val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_monthly_macd_gold(self):
        """月线MACD金叉：月线DIFF上穿DEA或站上零轴"""
        df = self._get_monthly_kline()
        if df.empty or len(df) < 15:
            return False, "数据不足"
        try:
            diff, dea, _ = self._calc_macd(df['close'])
            cross_up = (diff.iloc[-1] > dea.iloc[-1]) and (diff.iloc[-2] <= dea.iloc[-2])
            above_zero = diff.iloc[-1] > 0 and diff.iloc[-2] <= 0
            triggered = bool(cross_up or above_zero)
            val = f"月DIFF={diff.iloc[-1]:.2f}, DEA={dea.iloc[-1]:.2f}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_median_sync_up(self):
        """全A中位数同步上涨：880009指数与宽基指数同步上行"""
        try:
            ak = self.init_akshare()
            # 尝试获取万得全A中位数指数 880009
            df_median = safe_ak_call(ak.stock_zh_index_daily, symbol="sh880009")
            df_300 = self._get_index_daily()
            if (df_median is not None and not df_median.empty and
                not df_300.empty and len(df_median) >= 20 and len(df_300) >= 20):
                med_up = df_median['close'].iloc[-1] > df_median['close'].iloc[-20]
                idx_up = df_300['close'].iloc[-1] > df_300['close'].iloc[-20]
                triggered = bool(med_up and idx_up)
                val = f"中位数20日涨幅:{(df_median['close'].iloc[-1]/df_median['close'].iloc[-20]-1)*100:.1f}%, 沪深300:{(df_300['close'].iloc[-1]/df_300['close'].iloc[-20]-1)*100:.1f}%"
                return triggered, val
            return False, "数据待获取"
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_earning_growth_positive(self):
        """盈利增速转正：全A非金融净利润TTM同比转正"""
        # 季度数据，需要从财报获取，通常滞后
        # 简化：从现有数据或上期数据获取
        try:
            prev = load_json("timing_right_scores.json")
            sig = self._find_signal(prev, "盈利增速转正")
            if sig and sig.get("current_value") != "数据待获取":
                return sig.get("triggered", False), sig.get("current_value", "数据待获取")
            return False, "数据待获取(需季度财报)"
        except Exception:
            return False, "数据待获取"

    # ---------- 熊市早期信号 ----------

    def _sig_below_60ma(self):
        """跌破60日线：连续5日收于60日线下方"""
        df = self._get_index_daily()
        if df.empty or len(df) < 65:
            return False, "数据不足"
        try:
            df['ma60'] = df['close'].rolling(60).mean()
            last5 = df.tail(5)
            below = (last5['close'] < last5['ma60']).all()
            val = f"{'跌破' if below else '仍在'}60日线{'下方' if below else '上方'}"
            return bool(below), val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_weekly_macd_dead(self):
        """周线MACD死叉：周线DIFF下穿DEA"""
        df = self._get_weekly_kline()
        if df.empty or len(df) < 30:
            return False, "数据不足"
        try:
            diff, dea, _ = self._calc_macd(df['close'])
            cross_down = (diff.iloc[-1] < dea.iloc[-1]) and (diff.iloc[-2] >= dea.iloc[-2])
            recent_cross = False
            for i in range(-4, 0):
                if i-1 >= -len(diff) and diff.iloc[i] < dea.iloc[i] and diff.iloc[i-1] >= dea.iloc[i-1]:
                    recent_cross = True
            triggered = bool(cross_down or recent_cross)
            val = f"DIFF={diff.iloc[-1]:.2f}, DEA={dea.iloc[-1]:.2f}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_margin_decline(self):
        """两融余额回落：连续4周下降"""
        try:
            ak = self.init_akshare()
            df2 = safe_ak_call(ak.stock_margin_sse, start_date=(date.today() - timedelta(days=120)).strftime("%Y%m%d"))
            if df2 is not None and not df2.empty and '融资余额' in df2.columns:
                weekly_margin = df2['融资余额'].tail(5).values
                if len(weekly_margin) >= 5:
                    declining = all(weekly_margin[i] < weekly_margin[i-1] for i in range(-3, 0))
                    val = f"近周融资余额: {weekly_margin[-1]:.0f}亿"
                    return bool(declining), val
            prev = load_json("timing_right_scores.json")
            sig = self._find_signal(prev, "两融余额回落")
            if sig:
                return sig.get("triggered", False), sig.get("current_value", "数据待获取")
            return False, "数据待获取"
        except Exception as e:
            return False, f"数据获取异常: {e}"

    def _sig_volume_shrink(self):
        """成交萎缩：成交量持续低于60日均量"""
        df = self._get_index_daily()
        if df.empty or len(df) < 65:
            return False, "数据不足"
        try:
            df['vol_ma60'] = df['volume'].rolling(60).mean()
            last5 = df.tail(5)
            below = (last5['volume'] < last5['vol_ma60']).all()
            val = f"近5日成交量{'均低于' if below else '未完全低于'}60日均量"
            return bool(below), val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_breadth_deterioration(self):
        """宽度恶化：站上20日均线个股占比<30%且持续缩小"""
        # 同 _sig_breadth_repair 类似，需要全市场数据
        return False, "需全市场数据确认"

    def _sig_industry_capital_sell(self):
        """产业资本减持放大：净减持/成交额≥0.3%"""
        prev = load_json("timing_right_scores.json")
        sig = self._find_signal(prev, "产业资本减持放大")
        if sig:
            return sig.get("triggered", False), sig.get("current_value", "数据待获取")
        return False, "数据待获取"

    def _sig_northbound_outflow(self):
        """北向配置型资金持续净流出：连续2周净流出>100亿"""
        try:
            ak = self.init_akshare()
            # P2-2 修复(2026-10-10): 同 _sig_northbound_inflow，改用 stock_hsgt_hist_em。
            df = safe_ak_call(ak.stock_hsgt_hist_em, symbol="北向资金")
            if df is not None and not df.empty:
                col = '当日成交净买额' if '当日成交净买额' in df.columns else ('当日净流入' if '当日净流入' in df.columns else None)
                if col and '日期' in df.columns:
                    d = df[['日期', col]].dropna()
                    if d.empty:
                        return False, "北向资金已停更（2024-08 起无净买额数据）"
                    d = d.copy()
                    d['date'] = pd.to_datetime(d['日期'])
                    weekly_flow = d.set_index('date')[col].resample('W').sum()
                    if len(weekly_flow) >= 2:
                        last2 = weekly_flow.tail(2)
                        triggered = bool((last2 < -100).all())
                        val = f"近2周净流入: {last2.iloc[0]:.0f}亿, {last2.iloc[1]:.0f}亿"
                        return triggered, val
            return False, "数据待获取"
        except Exception as e:
            return False, f"数据获取异常: {e}"

    def _sig_volume_price_divergence(self):
        """量价背离（顶部）：指数创新高但成交额未同步放大"""
        df = self._get_index_daily()
        if df.empty or len(df) < 25:
            return False, "数据不足"
        try:
            last20 = df.tail(20)
            price_high = last20['close'].iloc[-1] >= last20['close'].max() * 0.99
            vol_high = last20['volume'].iloc[-1] >= last20['volume'].max() * 0.9
            divergence = price_high and not vol_high
            # 检查持续5日
            if divergence:
                div_days = 0
                for i in range(-5, 0):
                    p_new = df['close'].iloc[i] >= df['close'].iloc[i-20] * 0.99 if (i-20) >= -len(df) else False
                    v_weak = df['volume'].iloc[i] < df['volume'].rolling(20).max().iloc[i] * 0.9
                    if p_new and v_weak:
                        div_days += 1
                triggered = div_days >= 3
                val = f"量价背离{div_days}天"
            else:
                triggered = False
                val = "无量价背离"
            return bool(triggered), val
        except Exception as e:
            return False, f"计算异常: {e}"

    # ---------- 熊市确认信号 ----------

    def _sig_below_250ma_failed_rebounce(self):
        """跌破年线且反抽失败"""
        df = self._get_index_daily()
        if df.empty or len(df) < 255:
            return False, "数据不足"
        try:
            df['ma250'] = df['close'].rolling(250).mean()
            # 跌破: 最近20日内有跌破年线的情况
            last20 = df.tail(20)
            broke_below = (last20['close'] < last20['ma250']).any()
            # 反抽失败: 尝试站回但未能连续站回
            if broke_below:
                # 检查最近5日是否在年线下方
                last5 = df.tail(5)
                failed_rebounce = (last5['close'] < last5['ma250']).sum() >= 3
                triggered = bool(failed_rebounce)
                val = f"{'跌破年线且反抽失败' if triggered else '跌破年线但尚未确认反抽失败'}"
            else:
                triggered = False
                val = "仍在年线上方"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_monthly_macd_dead(self):
        """月线MACD死叉：月线DIFF下穿DEA"""
        df = self._get_monthly_kline()
        if df.empty or len(df) < 15:
            return False, "数据不足"
        try:
            diff, dea, _ = self._calc_macd(df['close'])
            cross_down = (diff.iloc[-1] < dea.iloc[-1]) and (diff.iloc[-2] >= dea.iloc[-2])
            below_zero = diff.iloc[-1] < 0 and diff.iloc[-2] >= 0
            triggered = bool(cross_down or below_zero)
            val = f"月DIFF={diff.iloc[-1]:.2f}, DEA={dea.iloc[-1]:.2f}"
            return triggered, val
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_median_divergence_down(self):
        """全A中位数背离下跌：宽基指数未跌但880009已下跌"""
        try:
            ak = self.init_akshare()
            df_median = safe_ak_call(ak.stock_zh_index_daily, symbol="sh880009")
            df_300 = self._get_index_daily()
            if (df_median is not None and not df_median.empty and
                not df_300.empty and len(df_median) >= 20):
                med_change = (df_median['close'].iloc[-1] / df_median['close'].iloc[-20] - 1) * 100
                idx_change = (df_300['close'].iloc[-1] / df_300['close'].iloc[-20] - 1) * 100
                divergence = idx_change >= -2 and med_change < -3
                val = f"中位数:{med_change:.1f}%, 沪深300:{idx_change:.1f}%"
                return bool(divergence), val
            return False, "数据待获取"
        except Exception as e:
            return False, f"计算异常: {e}"

    def _sig_earning_growth_negative(self):
        """盈利增速转负"""
        prev = load_json("timing_right_scores.json")
        sig = self._find_signal(prev, "盈利增速转负")
        if sig and sig.get("current_value") != "数据待获取":
            return sig.get("triggered", False), sig.get("current_value", "数据待获取")
        return False, "数据待获取(需季度财报)"

    # ---------- 辅助方法 ----------

    def _find_signal(self, prev_data, name):
        """从历史数据中查找指定信号"""
        if not prev_data or 'signals' not in prev_data:
            return None
        for group_key, signals in prev_data['signals'].items():
            for s in signals:
                if s.get('name') == name:
                    return s
        return None

    def _make_signal(self, name, triggered, score, current_value, data_source):
        return {
            "name": name,
            "triggered": bool(triggered),
            "score": score,
            "current_value": str(current_value),
            "data_source": data_source,
            "update_time": self._today
        }

    # ---------- 主计算 ----------

    def compute_all(self) -> dict:
        """计算完整的右侧趋势确认"""
        logger.info("\n" + "=" * 60)
        logger.info("开始计算右侧趋势确认 (timing_right_scores.json)")
        logger.info("=" * 60)

        today = self._today

        # ===== 牛市信号 =====
        logger.info("  [牛市信号计算]")

        # 早期牛市
        early_bull = []
        t, v = self._sig_standing_60ma()
        logger.info(f"    站稳60日线: {t} ({v})")
        early_bull.append(self._make_signal("站稳60日线", t, 12, v, "交易所行情"))

        t, v = self._sig_weekly_macd_gold()
        logger.info(f"    周线MACD金叉: {t} ({v})")
        early_bull.append(self._make_signal("周线MACD金叉", t, 10, v, "交易所行情"))

        t, v = self._sig_margin_recovery()
        logger.info(f"    两融余额回升: {t} ({v})")
        early_bull.append(self._make_signal("两融余额回升", t, 10, v, "交易所两融数据"))

        t, v = self._sig_volume_expansion()
        logger.info(f"    成交放量: {t} ({v})")
        early_bull.append(self._make_signal("成交放量", t, 8, v, "交易所行情"))

        t, v = self._sig_breadth_repair()
        logger.info(f"    市场宽度修复: {t} ({v})")
        early_bull.append(self._make_signal("市场宽度修复", t, 8, v, "交易所全市场统计"))

        t, v = self._sig_industry_capital_buyback()
        logger.info(f"    产业资本转增持: {t} ({v})")
        early_bull.append(self._make_signal("产业资本转增持", t, 7, v, "交易所公告汇总"))

        t, v = self._sig_northbound_inflow()
        logger.info(f"    北向配置型资金持续净流入: {t} ({v})")
        early_bull.append(self._make_signal("北向配置型资金持续净流入", t, 10, v, "沪深港通数据"))

        t, v = self._sig_volume_price_coord()
        logger.info(f"    量价配合: {t} ({v})")
        early_bull.append(self._make_signal("量价配合", t, 5, v, "交易所行情"))

        # 确认牛市
        confirm_bull = []
        t, v = self._sig_above_250ma()
        logger.info(f"    突破年线并站稳: {t} ({v})")
        confirm_bull.append(self._make_signal("突破年线并站稳", t, 15, v, "交易所行情"))

        t, v = self._sig_monthly_macd_gold()
        logger.info(f"    月线MACD金叉: {t} ({v})")
        confirm_bull.append(self._make_signal("月线MACD金叉", t, 10, v, "交易所行情"))

        t, v = self._sig_median_sync_up()
        logger.info(f"    全A中位数同步上涨: {t} ({v})")
        confirm_bull.append(self._make_signal("全A中位数同步上涨", t, 10, v, "交易所全市场统计"))

        # 最强牛市
        strong_confirm_bull = []
        t, v = self._sig_earning_growth_positive()
        logger.info(f"    盈利增速转正: {t} ({v})")
        strong_confirm_bull.append(self._make_signal("盈利增速转正", t, 15, v, "上市公司财报"))

        # ===== 熊市信号 =====
        logger.info("  [熊市信号计算]")

        early_bear = []
        t, v = self._sig_below_60ma()
        logger.info(f"    跌破60日线: {t} ({v})")
        early_bear.append(self._make_signal("跌破60日线", t, 12, v, "交易所行情"))

        t, v = self._sig_weekly_macd_dead()
        logger.info(f"    周线MACD死叉: {t} ({v})")
        early_bear.append(self._make_signal("周线MACD死叉", t, 10, v, "交易所行情"))

        t, v = self._sig_margin_decline()
        logger.info(f"    两融余额回落: {t} ({v})")
        early_bear.append(self._make_signal("两融余额回落", t, 10, v, "交易所两融数据"))

        t, v = self._sig_volume_shrink()
        logger.info(f"    成交萎缩: {t} ({v})")
        early_bear.append(self._make_signal("成交萎缩", t, 8, v, "交易所行情"))

        t, v = self._sig_breadth_deterioration()
        logger.info(f"    宽度恶化: {t} ({v})")
        early_bear.append(self._make_signal("宽度恶化", t, 8, v, "交易所全市场统计"))

        t, v = self._sig_industry_capital_sell()
        logger.info(f"    产业资本减持放大: {t} ({v})")
        early_bear.append(self._make_signal("产业资本减持放大", t, 7, v, "交易所公告汇总"))

        t, v = self._sig_northbound_outflow()
        logger.info(f"    北向配置型资金持续净流出: {t} ({v})")
        early_bear.append(self._make_signal("北向配置型资金持续净流出", t, 10, v, "沪深港通数据"))

        t, v = self._sig_volume_price_divergence()
        logger.info(f"    量价背离: {t} ({v})")
        early_bear.append(self._make_signal("量价背离", t, 5, v, "交易所行情"))

        confirm_bear = []
        t, v = self._sig_below_250ma_failed_rebounce()
        logger.info(f"    跌破年线且反抽失败: {t} ({v})")
        confirm_bear.append(self._make_signal("跌破年线且反抽失败", t, 15, v, "交易所行情"))

        t, v = self._sig_monthly_macd_dead()
        logger.info(f"    月线MACD死叉: {t} ({v})")
        confirm_bear.append(self._make_signal("月线MACD死叉", t, 10, v, "交易所行情"))

        t, v = self._sig_median_divergence_down()
        logger.info(f"    全A中位数背离下跌: {t} ({v})")
        confirm_bear.append(self._make_signal("全A中位数背离下跌", t, 10, v, "交易所全市场统计"))

        strong_confirm_bear = []
        t, v = self._sig_earning_growth_negative()
        logger.info(f"    盈利增速转负: {t} ({v})")
        strong_confirm_bear.append(self._make_signal("盈利增速转负", t, 15, v, "上市公司财报"))

        # ===== 计分 =====
        bull_score = sum(s['score'] for s in early_bull if s['triggered'])
        bull_score += sum(s['score'] for s in confirm_bull if s['triggered'])
        bull_score += sum(s['score'] for s in strong_confirm_bull if s['triggered'])

        bear_score = sum(s['score'] for s in early_bear if s['triggered'])
        bear_score += sum(s['score'] for s in confirm_bear if s['triggered'])
        bear_score += sum(s['score'] for s in strong_confirm_bear if s['triggered'])

        # ===== 第一层：信号灯判定 =====
        # 规则：逐级升级，不允许跳级
        # 先确定最高触发的层级
        has_early_bull = any(s['triggered'] for s in early_bull)
        has_confirm_bull = any(s['triggered'] for s in confirm_bull)
        has_strong_bull = any(s['triggered'] for s in strong_confirm_bull)

        has_early_bear = any(s['triggered'] for s in early_bear)
        has_confirm_bear = any(s['triggered'] for s in confirm_bear)
        has_strong_bear = any(s['triggered'] for s in strong_confirm_bear)

        # 确定信号灯 (逐级升级)
        signal_light = "none"
        signal_direction = None

        # 牛市方向
        if has_strong_bull and has_confirm_bull:
            signal_light = "green"
            signal_direction = "bull"
        elif has_confirm_bull and has_early_bull:
            signal_light = "orange"
            signal_direction = "bull"
        elif has_early_bull:
            signal_light = "yellow"
            signal_direction = "bull"

        # 熊市方向 (优先级更高——顶部确认更快)
        if has_strong_bear and has_confirm_bear:
            signal_light = "red"
            signal_direction = "bear"
        elif has_confirm_bear and has_early_bear:
            if signal_light in ("none",):
                signal_light = "orange"
            elif signal_light == "yellow":
                signal_light = "orange"  # 不跳级
            signal_direction = "bear"
        elif has_early_bear:
            if signal_light == "none":
                signal_light = "yellow"
                signal_direction = "bear"
            # 不覆盖已有的bull方向

        # ===== 第二层：打分制补充（仅无灯时启用）=====
        if signal_light == "none":
            if bull_score > 40 and bear_score < 30:
                trend_bias = "warm"
            elif bear_score > 40 and bull_score < 30:
                trend_bias = "cool"
            elif bull_score > 30 and bear_score > 30:
                trend_bias = "chaos"
            elif bull_score < 25 and bear_score < 25:
                trend_bias = "vacuum"
            else:
                trend_bias = "neutral"
        else:
            trend_bias = "neutral"

        logger.info(f"\n  📊 牛分: {bull_score}/120, 熊分: {bear_score}/120")
        logger.info(f"  🚦 信号灯: {signal_light}, 方向: {signal_direction}")
        logger.info(f"  📊 趋势偏向: {trend_bias}")

        # ===== 风格轮动 =====
        style_rotation = self._calc_style_rotation()

        output = {
            "update_date": today,
            "version": "v4.1",
            "signal_light": signal_light,
            "signal_direction": signal_direction,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "trend_bias": trend_bias,
            "signals": {
                "early_bull": early_bull,
                "confirm_bull": confirm_bull,
                "strong_confirm_bull": strong_confirm_bull,
                "early_bear": early_bear,
                "confirm_bear": confirm_bear,
                "strong_confirm_bear": strong_confirm_bear,
            },
            "style_rotation": style_rotation,
        }

        return output

    def _calc_style_rotation(self):
        """计算风格轮动信息"""
        result = {
            "large_small": {"ratio": None, "trend": "数据待获取", "signal": "--"},
            "dividend_premium": {"value": None, "signal": "--"}
        }
        try:
            # 大小盘比值: 中证1000/沪深300
            ak = self.init_akshare()
            df_1000 = safe_ak_call(ak.stock_zh_index_daily, symbol="sh000852")
            df_300 = self._get_index_daily("sh000300")
            if (df_1000 is not None and not df_1000.empty and
                not df_300.empty and len(df_1000) >= 5 and len(df_300) >= 5):
                ratio = df_1000['close'].iloc[-1] / df_300['close'].iloc[-1]
                prev_ratio = df_1000['close'].iloc[-20] / df_300['close'].iloc[-20] if len(df_1000) >= 20 else ratio
                trend = "大盘占优" if ratio < prev_ratio else "小盘占优"
                result["large_small"] = {
                    "ratio": round(ratio, 4),
                    "trend": trend,
                    "signal": "小盘偏弱" if ratio < prev_ratio else "小盘走强"
                }
        except Exception as e:
            logger.warning(f"      风格轮动计算异常: {e}")

        return result


# ============================================================
# 其他数据文件更新 (编排已有脚本)
# ============================================================

def run_subscript(script_name: str, description: str) -> bool:
    """运行子脚本"""
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        logger.warning(f"  脚本不存在: {script_path}")
        return False

    logger.info(f"\n{'─' * 50}")
    logger.info(f"▶ {description} ({script_name})")
    logger.info(f"{'─' * 50}")

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=False,
            timeout=300,
            cwd=str(PROJECT_ROOT)
        )
        if result.returncode == 0:
            logger.info(f"  ✅ {script_name} 完成")
            return True
        else:
            logger.error(f"  ❌ {script_name} 失败 (exit {result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"  ❌ {script_name} 超时 (>300s)")
        return False
    except Exception as e:
        logger.error(f"  ❌ {script_name} 异常: {e}")
        return False


# ============================================================
# dry-run 模式: 检查接口连通性
# ============================================================

def dry_run_check():
    """检查所有数据源接口连通性"""
    logger.info("=" * 60)
    logger.info("🔍 DRY-RUN: 检查数据源接口连通性")
    logger.info("=" * 60)

    results = {}

    # 1. AKShare
    logger.info("\n[AKShare] 检查接口...")
    try:
        import akshare as ak
        logger.info(f"  akshare 版本: {ak.__version__}")

        # 测试核心接口
        tests = [
            ("stock_index_pe_lg(沪深300)", lambda: ak.stock_index_pe_lg(symbol="沪深300")),
            ("bond_gb_zh_sina(10Y国债)", lambda: ak.bond_gb_zh_sina(symbol="中国10年期国债")),
            ("macro_china_pmi", lambda: ak.macro_china_pmi()),
            ("macro_china_gdp", lambda: ak.macro_china_gdp()),
            ("macro_china_cpi", lambda: ak.macro_china_cpi()),
            ("stock_zh_index_daily(上证)", lambda: ak.stock_zh_index_daily(symbol="sh000001")),
        ]

        for name, func in tests:
            try:
                result = func()
                if isinstance(result, pd.DataFrame) and not result.empty:
                    logger.info(f"  ✓ {name}: {len(result)} 行")
                    results[name] = "OK"
                else:
                    logger.warning(f"  ⚠ {name}: 返回空数据")
                    results[name] = "EMPTY"
            except Exception as e:
                logger.error(f"  ✗ {name}: {e}")
                results[name] = f"FAIL: {e}"

    except ImportError:
        logger.error("  ✗ akshare 未安装")
        results["akshare"] = "NOT INSTALLED"
    except Exception as e:
        logger.error(f"  ✗ AKShare 初始化失败: {e}")
        results["akshare"] = f"FAIL: {e}"

    # 2. FRED API
    logger.info("\n[FRED API] 检查...")
    fred_key = os.environ.get("FRED_API_KEY", "")
    if fred_key:
        try:
            from fredapi import Fred
            fred = Fred(api_key=fred_key)
            s = fred.get_series("DGS10", observation_start="2025-01-01")
            if not s.empty:
                logger.info(f"  ✓ FRED DGS10: {len(s)} 点, 最新={s.iloc[-1]}")
                results["FRED"] = "OK"
            else:
                logger.warning("  ⚠ FRED: 返回空数据")
                results["FRED"] = "EMPTY"
        except Exception as e:
            logger.error(f"  ✗ FRED: {e}")
            results["FRED"] = f"FAIL: {e}"
    else:
        logger.warning("  ⚠ FRED_API_KEY 未设置 (美国宏观数据不可用)")
        results["FRED"] = "NO KEY"

    # 3. yfinance
    logger.info("\n[yfinance] 检查...")
    try:
        import yfinance as yf
        t = yf.Ticker("^GSPC")
        hist = t.history(period="5d")
        if not hist.empty:
            logger.info(f"  ✓ yfinance S&P500: {len(hist)} 行")
            results["yfinance"] = "OK"
        else:
            logger.warning("  ⚠ yfinance: 返回空数据")
            results["yfinance"] = "EMPTY"
    except ImportError:
        logger.error("  ✗ yfinance 未安装")
        results["yfinance"] = "NOT INSTALLED"
    except Exception as e:
        logger.error(f"  ✗ yfinance: {e}")
        results["yfinance"] = f"FAIL: {e}"

    # 汇总
    logger.info(f"\n{'=' * 60}")
    logger.info("📋 接口连通性检查汇总:")
    logger.info(f"{'=' * 60}")
    ok_count = sum(1 for v in results.values() if v == "OK")
    for name, status in results.items():
        icon = "✓" if status == "OK" else "⚠" if "EMPTY" in str(status) or "NO KEY" in str(status) else "✗"
        logger.info(f"  {icon} {name}: {status}")

    logger.info(f"\n总计: {ok_count}/{len(results)} 接口可用")
    return results


# ============================================================
# 主入口
# ============================================================

def append_timing_history(timing_data, right_data, history_path="data/timing_history.json"):
    """[P0-1 2026-10-16] 决策态日志 - 每日追加 composite/六维/阶段/触发器快照"""
    import json
    from datetime import date
    from pathlib import Path
    
    history = []
    p = Path(DATA_DIR) / history_path.replace("data/", "")
    if p.exists():
        try:
            history = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            history = []
    
    today = date.today().isoformat()
    cs = timing_data.get("composite_score", 0)
    # [P2-11] 阶段=迟滞状态机；主流程 SSOT 已算则直接用，否则自算（函数独立可用）
    stage = timing_data.get("stage")
    if stage not in ("A", "B", "C", "D"):
        prev_stage = history[-1].get("stage") if history else None
        stage = apply_stage_hysteresis(cs, prev_stage)
    
    dims = {}
    for dname, dval in timing_data.get("dimensions", {}).items():
        if dname.startswith("_"):
            continue
        dims[dname] = dval.get("score")
    
    triggers = {}
    if timing_data:
        for side, trig_list in timing_data.get("triggers", {}).items():
            triggers[side] = {t.get("condition", ""): t.get("met", False) for t in trig_list}
    # Also capture signal_light/direction from right_data if available
    if right_data:
        triggers["signal_light"] = right_data.get("signal_light", "")
        triggers["signal_direction"] = right_data.get("signal_direction", "")

    # [P1-7 2026-10-16] 持久化非线性状态
    p17 = timing_data.get("p1_7_state", {})
    hysteresis_state = p17.get("hysteresis", {})
    anchor_state = p17.get("anchor", {})
    p19 = timing_data.get("p1_9_state", {})
    warning_state = p19
    trend_state = timing_data.get("p1_6_state", {})
    fed_state = timing_data.get("p1_8_state", {})

    record = {
        "date": today,
        "composite_score": cs,
        "stage": stage,
        "dimensions": dims,
        "triggers": triggers,
        "hysteresis_state": hysteresis_state,
        "anchor_state": anchor_state,
        "warning_state": warning_state,
        "trend_state": trend_state,
        "fed_state": fed_state
    }
    history = [r for r in history if r.get("date") != today]
    history.append(record)
    history.sort(key=lambda x: x.get("date", ""))
    p.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"✅ timing_history.json 已追加: date={today}, composite={cs}, stage={stage}")

    # [P2-13] 信号 IC/胜率累积（失败仅告警，不阻断决策态日志）
    try:
        stats = p213_update(history)
        p213_state = {
            "total_signals": stats.get("total_signals", 0),
            "by_type": stats.get("by_type", {}),
            "ic": stats.get("ic", {}),
            "disclose_min_n": P213_MIN_DISCLOSE_N,
            "cite_min_n": P213_MIN_CITE_N,
            "note": "P2-13 样本外信号统计（2026-10-18起累积；n<10只显示累积中，n≥20显示百分比，n≥50方可研报引用）",
        }
        timing_data["p2_13_state"] = p213_state
        # 主流程 save_json 在 append 之前 → 直接注入已落盘的 timing_scores.json
        ts_on_disk = load_json("timing_scores.json")
        if isinstance(ts_on_disk, dict):
            ts_on_disk["p2_13_state"] = p213_state
            save_json(ts_on_disk, "timing_scores.json")
        logger.info(f"    📊 P2-13 信号统计: 累计信号 {stats.get('total_signals', 0)} 条")
    except Exception as e:
        logger.warning(f"⚠️ P2-13 信号统计失败（不阻断）: {e}")
    return record


def main():
    parser = argparse.ArgumentParser(description="宏观看板数据更新脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅检查接口连通性，不写文件")
    parser.add_argument("--timing", action="store_true", help="仅更新 timing_scores.json")
    parser.add_argument("--skip-subscripts", action="store_true", help="跳过子脚本(仅计算timing)")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info("🚀 宏观看板 - 数据更新")
    logger.info(f"   时间: {datetime.now().isoformat()}")
    logger.info(f"   模式: {'dry-run' if args.dry_run else 'timing-only' if args.timing else '完整更新'}")
    logger.info("=" * 60)

    # dry-run 模式
    if args.dry_run:
        results = dry_run_check()
        elapsed = time.time() - start_time
        logger.info(f"\n耗时: {elapsed:.1f}s")
        return

    # timing-only 模式
    if args.timing or args.skip_subscripts:
        # [修复 2026-10-10] 原实现该分支 return None，导致 main() 返回 None ->
        # sys.exit(0 if None else 1) 恒为 1，workflow 的 timing 步骤永远"失败"、
        # 后续 commit/push 被跳过（数据算了却不入库）。改为返回真实成功标志。
        timing_ok = False
        engine = TimingScoreEngine()
        try:
            timing_data = engine.compute_all()
            save_json(timing_data, "timing_scores.json")
            logger.info("\n✅ timing_scores.json 更新完成!")
            timing_ok = True
        except Exception as e:
            logger.error(f"❌ timing_scores 计算失败: {e}", exc_info=True)

        # 右侧趋势确认
        right_engine = TimingRightEngine()
        try:
            right_data = right_engine.compute_all()
            save_json(right_data, "timing_right_scores.json")
            logger.info("✅ timing_right_scores.json 更新完成!")
        except Exception as e:
            logger.error(f"❌ timing_right_scores 计算失败: {e}", exc_info=True)

        # [P0-1 fix 2026-10-16] --timing 分支也追加决策态日志
        try:
            append_timing_history(timing_data, right_data)
        except Exception as e:
            logger.warning(f"⚠️ timing_history.json 追加失败: {e}")
        return timing_ok

    # ============================================================
    # 完整更新模式
    # ============================================================
    script_results = {}

    # Step 1: timing_scores (最核心)
    logger.info("\n" + "🔥" * 30)
    logger.info("Step 1/6: 计算择时评分 (timing_scores.json)")
    logger.info("🔥" * 30)
    try:
        engine = TimingScoreEngine()
        timing_data = engine.compute_all()
        save_json(timing_data, "timing_scores.json")
        script_results["timing_scores"] = True
    except Exception as e:
        logger.error(f"❌ timing_scores 失败: {e}", exc_info=True)
        script_results["timing_scores"] = False

    # Step 1b: 右侧趋势确认
    logger.info("\n" + "🔥" * 30)
    logger.info("Step 1b: 右侧趋势确认 (timing_right_scores.json)")
    logger.info("🔥" * 30)
    right_data = None
    try:
        right_engine = TimingRightEngine()
        right_data = right_engine.compute_all()
        save_json(right_data, "timing_right_scores.json")
        script_results["timing_right_scores"] = True
    except Exception as e:
        logger.error(f"❌ timing_right_scores 失败: {e}", exc_info=True)
        script_results["timing_right_scores"] = False

    # [F1 fix 2026-10-23] 决策态日志独立于右侧引擎成败（原嵌套在 Step1b try 内——
    # 右侧被慢滴拖死/步骤 kill 时日志断流，10-19~10-22 四天缺口根因之一）
    try:
        append_timing_history(timing_data, right_data)
    except Exception as e:
        logger.warning(f"⚠️ timing_history.json 追加失败: {e}")

    # Step 2-6: 运行子脚本
    subscripts = [
        ("fetch_cn_macro.py", "中国宏观数据"),
        ("fetch_us_macro.py", "美国宏观数据"),
        ("fetch_asset_data.py", "全球资产价格"),
        ("calc_valuations.py", "资产估值"),
        ("generate_summary.py", "看板综合结论"),
    ]

    for i, (script, desc) in enumerate(subscripts, 2):
        logger.info(f"\nStep {i}/6: {desc}")
        success = run_subscript(script, desc)
        script_results[script] = success

    # ============================================================
    # 汇总
    # ============================================================
    elapsed = time.time() - start_time
    ok_count = sum(1 for v in script_results.values() if v)

    logger.info(f"\n{'=' * 60}")
    logger.info("📊 更新汇总")
    logger.info(f"{'=' * 60}")
    for name, success in script_results.items():
        icon = "✅" if success else "❌"
        logger.info(f"  {icon} {name}")

    logger.info(f"\n总计: {ok_count}/{len(script_results)} 成功, 耗时 {elapsed:.1f}s")

    # 数据文件检查
    expected_files = [
        "timing_scores.json", "cn_macro.json", "us_macro.json",
        "asset_prices.json", "asset_valuation.json", "dashboard_summary.json"
    ]
    logger.info(f"\n数据文件:")
    for f in expected_files:
        path = DATA_DIR / f
        if path.exists():
            size = path.stat().st_size / 1024
            logger.info(f"  ✓ {f:30s} {size:.1f} KB")
        else:
            logger.info(f"  ✗ {f:30s} 缺失")

    if ok_count == len(script_results):
        logger.info("\n🎉 所有数据更新成功!")
    else:
        logger.warning(f"\n⚠️ {len(script_results) - ok_count} 项失败，请检查日志")

    return ok_count == len(script_results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
