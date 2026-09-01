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


def save_json(data: dict, filename: str):
    import json
    # 对 timing_scores.json 合并历史数据和说明字段
    if filename == "timing_scores.json":
        data = merge_history_description(data)
    filepath = DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    size_kb = filepath.stat().st_size / 1024
    logger.info(f"  💾 已保存: {filepath.name} ({size_kb:.1f} KB)")


def merge_history_description(new_data: dict) -> dict:
    """将旧 JSON 中的 history 和 description 字段合并到新数据中"""
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
            if "history" in old_ind and "history" not in ind:
                ind["history"] = old_ind["history"]
            if "description" in old_ind and "description" not in ind:
                ind["description"] = old_ind["description"]
    return new_data


def load_json(filename: str) -> dict:
    import json
    filepath = DATA_DIR / filename
    if not filepath.exists():
        logger.warning(f"  文件不存在: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_ak_call(func, *args, **kwargs):
    """安全调用 AKShare 接口"""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, pd.DataFrame):
            logger.info(f"    ✓ ak.{func.__name__}: {len(result)} 行")
        else:
            logger.info(f"    ✓ ak.{func.__name__}: OK")
        return result
    except Exception as e:
        logger.warning(f"    ✗ ak.{func.__name__}: {e}")
        return pd.DataFrame() if hasattr(func, '__name__') and 'stock' in func.__name__ else None


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
            "sub_weight": 35,
            "bottom_threshold": "<10%",
            "top_threshold": ">90%"
        }

        indicators["hs300_pb_percentile"] = {
            "name": "沪深300 PB分位",
            "value": safe_float(hs300_pb_pct) if hs300_pb_pct else None,
            "unit": "%分位",
            "score": pb_score,
            "sub_weight": 20,
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
            "sub_weight": 25,
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
            "sub_weight": 20,
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
        宏观流动性维度
        指标:
          - 社融增速趋势: 权重35%
          - 利率水平: 权重30%
          - M1-M2剪刀差: 权重20%
          - 美联储政策: 权重15%
        """
        logger.info("  [维度2/6] 宏观流动性...")
        ak = self.init_akshare()
        indicators = {}

        # --- 社融增速趋势 ---
        sf_score, sf_value = self._calc_social_financing()
        indicators["social_financing_trend"] = {
            "name": "社融增速趋势",
            "value": sf_value or "数据待更新",
            "score": sf_score,
            "sub_weight": 35,
            "bottom_signal": "触底回升",
            "top_signal": "见顶回落"
        }

        # --- 利率水平 ---
        rate_score, rate_value = self._calc_interest_rate()
        indicators["interest_rate"] = {
            "name": "利率水平",
            "value": rate_value or "数据待更新",
            "score": rate_score,
            "sub_weight": 30,
            "bottom_signal": "降息周期",
            "top_signal": "加息周期"
        }

        # --- M1-M2剪刀差 ---
        m1m2_score, m1m2_value = self._calc_m1_m2_scissors()
        indicators["m1_m2_scissors"] = {
            "name": "M1-M2剪刀差",
            "value": m1m2_value or "数据待更新",
            "score": m1m2_score,
            "sub_weight": 20,
            "bottom_signal": "从-8%收窄",
            "top_signal": ">+5%扩张"
        }

        # --- 美联储政策 ---
        fed_score, fed_value = self._calc_fed_policy()
        indicators["fed_policy"] = {
            "name": "美联储政策",
            "value": fed_value or "中性",
            "score": fed_score,
            "sub_weight": 15,
            "bottom_signal": "降息周期",
            "top_signal": "加息周期"
        }

        dim_score = self._weighted_score(indicators)
        signal = "宽松" if dim_score < 40 else "中性偏松" if dim_score < 50 else "中性" if dim_score < 60 else "中性偏紧" if dim_score < 75 else "收紧"

        return {
            "name": "宏观流动性",
            "score": dim_score,
            "weight": 17,
            "weighted_score": round(dim_score * 0.17, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_social_financing(self):
        """社融增速趋势"""
        try:
            ak = self.init_akshare()
            sf_df = safe_ak_call(ak.macro_china_shrzgm)
            if sf_df is not None and not sf_df.empty:
                col = '社会融资规模增量'
                if col in sf_df.columns:
                    # 数据按时间升序排列，tail = 最新
                    sf_series = sf_df[col].dropna()
                    if len(sf_series) >= 24:
                        # 近3月均值 (最新) vs 前3月
                        recent_3m = sf_series.tail(3).mean()
                        prev_3m = sf_series.iloc[-6:-3].mean()

                        # 社融增速 = 近12月累计 / 前12月累计
                        recent_12m = sf_series.tail(12).sum()
                        prev_12m = sf_series.iloc[-24:-12].sum()
                        growth_rate = (recent_12m / prev_12m - 1) * 100 if prev_12m > 0 else 0

                        # 环比趋势
                        trend = (recent_3m / prev_3m - 1) * 100 if prev_3m > 0 else 0

                        val_str = f"{growth_rate:.1f}%{'企稳' if abs(trend) < 5 else '回升' if trend > 0 else '回落'}"

                        # 评分: 社融回升 → 利好 → 低分(安全)
                        if growth_rate > 15:
                            score = 30
                        elif growth_rate > 8:
                            score = 45
                        elif growth_rate > 5:
                            score = 50
                        elif growth_rate > 0:
                            score = 55
                        else:
                            score = 65

                        # 触底回升加分
                        if trend > 5:
                            score = max(20, score - 15)

                        logger.info(f"      社融增速: {growth_rate:.1f}%, 环比: {trend:.1f}% → {val_str}")
                        return score, val_str
        except Exception as e:
            logger.warning(f"      社融计算异常: {e}")

        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("social_financing_trend", {}).get("value", "未知")
        prev_score = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("social_financing_trend", {}).get("score", 50)
        return prev_score, prev_val

    def _calc_interest_rate(self):
        """利率水平评分"""
        try:
            ak = self.init_akshare()
            # 中国10年国债收益率
            bond_df = safe_ak_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")
            if bond_df is not None and not bond_df.empty and 'close' in bond_df.columns:
                bond_df['date'] = pd.to_datetime(bond_df['date'])
                bond_series = bond_df.set_index('date')['close'].dropna()
                cn10y = safe_float(bond_series.iloc[-1])

                if cn10y:
                    # 判断利率周期
                    # 历史中枢约 2.8-3.2%
                    avg_1y = bond_series.tail(250).mean() if len(bond_series) >= 250 else cn10y
                    trend = "降息周期" if cn10y < avg_1y * 0.95 else "加息周期" if cn10y > avg_1y * 1.05 else "中性"

                    # 评分: 低利率 → 利好 → 低分
                    if cn10y < 1.5:
                        score = 25
                    elif cn10y < 2.0:
                        score = 35
                    elif cn10y < 2.5:
                        score = 45
                    elif cn10y < 3.0:
                        score = 55
                    else:
                        score = 65

                    val_str = f"{trend}({cn10y:.2f}%)"
                    logger.info(f"      10Y国债: {cn10y}%, 判断: {trend}")
                    return score, val_str
        except Exception as e:
            logger.warning(f"      利率计算异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {})
                .get("interest_rate", {}).get("score", 50), "数据待更新")

    def _calc_m1_m2_scissors(self):
        """M1-M2剪刀差"""
        try:
            ak = self.init_akshare()
            # M1, M2 同比
            m2_df = safe_ak_call(ak.macro_china_m2_yearly)
            # 尝试获取 M1
            m1_df = safe_ak_call(ak.macro_china_m1_yearly) if hasattr(ak, 'macro_china_m1_yearly') else None

            if m2_df is not None and not m2_df.empty:
                # 从上期数据或 cn_macro 获取
                cn = load_json("cn_macro.json")
                m2_val = cn.get("lagging", {}).get("m2_yoy", {}).get("value")

                if m2_val:
                    # M1 数据可能不可用，使用上期值
                    prev = load_json("timing_scores.json")
                    prev_m1m2 = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("m1_m2_scissors", {}).get("value", "")
                    prev_score = prev.get("dimensions", {}).get("liquidity", {}).get("indicators", {}).get("m1_m2_scissors", {}).get("score", 50)

                    if "负值收窄" in str(prev_m1m2):
                        score = max(30, prev_score - 5)
                    elif "正值" in str(prev_m1m2):
                        score = max(25, prev_score - 3)
                    else:
                        score = prev_score

                    logger.info(f"      M1-M2剪刀差: {prev_m1m2} (使用上期+微调)")
                    return score, prev_m1m2
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
        股债性价比维度
        指标:
          - 沪深300 ERP: 权重50%
          - 红利指数股债利差: 权重20%
          - 股息率-国债利差(沪深300): 权重15%
          - 中美双视角利差: 权重15%
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
            "sub_weight": 25,
            "note": "【参考】沪深300口径仅供参考，主指标为万得全A ERP",
            "bottom_threshold": ">6%",
            "top_threshold": "<2.5%"
        }

        # --- 万得全A ERP ★主指标 ---
        wanda_erp_val, wanda_erp_pct, wanda_erp_score = self._calc_wanda_erp()
        if wanda_erp_val is not None:
            indicators["wanda_erp"] = {
                "name": "万得全A ERP ★主指标",
                "value": f"{wanda_erp_val:.2f}%",
                "percentile": f"{wanda_erp_pct:.1f}%" if wanda_erp_pct is not None else "数据待更新",
                "score": wanda_erp_score,
                "sub_weight": 50,
                "note": "【新】股债性价比主指标，取代沪深300 ERP",
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
            "sub_weight": 15,
            "bottom_threshold": ">3%",
            "top_threshold": "<0.3%"
        }

        # --- 中美双视角利差 ---
        cn_us_score, cn_us_val = self._calc_cn_us_spread()
        indicators["cn_us_spread"] = {
            "name": "中美双视角利差",
            "value": cn_us_val or "分歧",
            "score": cn_us_score,
            "sub_weight": 15,
            "bottom_signal": "内外资均认为便宜",
            "top_signal": "内外资均认为贵"
        }

        dim_score = self._weighted_score(indicators)
        signal = "股票极具吸引力" if dim_score < 30 else "股票有吸引力" if dim_score < 45 else "中性" if dim_score < 60 else "债券更有吸引力" if dim_score < 75 else "股票明显高估"

        return {
            "name": "股债性价比",
            "score": dim_score,
            "weight": 18,
            "weighted_score": round(dim_score * 0.18, 2),
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
        万得全A ERP = 1/PE_TTM(全A) - 10年国债收益率
        PE来源: akshare stock_index_pe_lg (symbol="中证A股")
        阈值: >4%底部 / >3%便宜 / >2%中性 / >1.5%偏贵 / <1.5%昂贵
        """
        try:
            ak = self.init_akshare()
            pe_df = safe_ak_call(ak.stock_index_pe_lg, symbol="中证A股")
            if pe_df is None or pe_df.empty:
                raise ValueError("中证A股PE数据为空")

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
        资金面维度
        指标:
          - 两融/流通市值: 权重35%
          - 北向资金趋势: 权重25%
          - 新发基金热度: 权重20%
          - 产业资本增减持: 权重20%
        """
        logger.info("  [维度4/6] 资金面...")
        ak = self.init_akshare()
        indicators = {}

        # --- 两融/流通市值 ---
        margin_score, margin_val = self._calc_margin_ratio()
        # margin_val 是数字 (百分比)，如 2.66
        if isinstance(margin_val, (int, float)):
            margin_display = f"{margin_val:.2f}%"
        else:
            margin_display = margin_val or "数据待更新"
        indicators["margin_ratio"] = {
            "name": "两融/流通市值",
            "value": margin_display,
            "score": margin_score,
            "sub_weight": 35,
            "bottom_threshold": "<2.0%",
            "top_threshold": ">3.5%"
        }

        # --- 北向资金趋势 ---
        north_score, north_val = self._calc_northbound()
        indicators["northbound"] = {
            "name": "北向资金趋势",
            "value": "⚠️数据已停更(2025-09起)",
            "score": 50,
            "sub_weight": 0,
            "status": "deprecated",
            "note": "AKShare北向资金接口停更，本版本移除该指标",
            "bottom_signal": "—",
            "top_signal": "—"
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
            "sub_weight": 20,
            "bottom_signal": "净增持/回购潮",
            "top_signal": "大举减持"
        }

        dim_score = self._weighted_score(indicators)
        signal = "资金充裕" if dim_score < 35 else "正常偏高" if dim_score < 50 else "正常" if dim_score < 60 else "正常偏低" if dim_score < 75 else "资金紧张"

        return {
            "name": "资金面",
            "score": dim_score,
            "weight": 15,
            "weighted_score": round(dim_score * 0.15, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_margin_ratio(self):
        """两融余额/流通市值"""
        try:
            ak = self.init_akshare()
            # 两融数据
            margin_df = safe_ak_call(ak.stock_margin_sse, start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))
            if margin_df is not None and not margin_df.empty:
                # 最新两融余额
                latest = margin_df.iloc[-1] if len(margin_df) > 0 else None
                if latest is not None:
                    # 获取融资融券余额
                    balance_col = None
                    for col in margin_df.columns:
                        if '余额' in str(col) or 'balance' in str(col).lower():
                            balance_col = col
                            break

                    if balance_col:
                        margin_balance = safe_float(margin_df[balance_col].iloc[-1])
                        if margin_balance:
                            # 估算流通市值 (约 60-80万亿)
                            # 简化: 从上期数据获取比例
                            prev = load_json("timing_scores.json")
                            prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("margin_ratio", {}).get("value", "2.66%")
                            prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("margin_ratio", {}).get("score", 48)

                            logger.info(f"      两融余额: {margin_balance/1e8:.0f}亿, 使用上期比例 {prev_val}")
                            try:
                                return prev_score, float(prev_val.replace('%', ''))
                            except:
                                return 48, 2.66
        except Exception as e:
            logger.warning(f"      两融数据异常: {e}")

        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("margin_ratio", {}).get("value", "2.66%")
        prev_score = prev.get("dimensions", {}).get("capital_flow", {}).get("indicators", {}).get("margin_ratio", {}).get("score", 48)
        try:
            return prev_score, float(prev_val.replace('%', ''))
        except:
            return 48, 2.66

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

    # ----------------------------------------------------------
    # 维度5: 市场情绪 (sentiment)
    # ----------------------------------------------------------
    def calc_sentiment(self) -> dict:
        """
        市场情绪维度
        指标:
          - 偏股基金滚动3年年化: 权重25%
          - 恐贪指数: 权重25%
          - 全A换手率: 权重25%
          - 融资买入占比: 权重25%
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
            "sub_weight": 25,
            "bottom_threshold": "<-10%",
            "top_threshold": ">30%"
        }

        # --- 恐贪指数 ---
        fg_score, fg_val = self._calc_fear_greed()
        indicators["fear_greed_index"] = {
            "name": "恐贪指数",
            "value": fg_val if fg_val else 50,
            "score": fg_score,
            "sub_weight": 25,
            "bottom_threshold": "<20",
            "top_threshold": ">80"
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
        fb_score, fb_val = self._calc_margin_buying()
        indicators["margin_buying_ratio"] = {
            "name": "融资买入占比",
            "value": fb_val or "数据待更新",
            "score": fb_score,
            "sub_weight": 25,
            "bottom_threshold": "<7%",
            "top_threshold": ">11%"
        }

        dim_score = self._weighted_score(indicators)
        signal = "极度恐惧" if dim_score < 25 else "偏恐惧" if dim_score < 40 else "中性" if dim_score < 60 else "偏贪婪" if dim_score < 75 else "极度贪婪"

        return {
            "name": "市场情绪",
            "score": dim_score,
            "weight": 18,
            "weighted_score": round(dim_score * 0.18, 2),
            "signal": signal,
            "indicators": indicators
        }

    def _calc_fund_3y(self):
        """偏股基金滚动3年年化"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("fund_3y_annual", {}).get("value", "4.10%")
        prev_score = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("fund_3y_annual", {}).get("score", 48)
        logger.info(f"      基金3年年化: {prev_val} (使用上期值)")
        return prev_score, prev_val

    def _calc_fear_greed(self):
        """恐贪指数 - 综合多项情绪指标"""
        try:
            # 简化版恐贪指数: 基于成交量、涨跌停比等
            # 使用换手率 + 北向资金 + PE分位 综合估算
            ak = self.init_akshare()

            # 获取上证指数成交量来估算市场活跃度
            sse_df = safe_ak_call(ak.stock_zh_index_daily, symbol="sh000001")
            if sse_df is not None and not sse_df.empty and 'volume' in sse_df.columns:
                vol = pd.to_numeric(sse_df['volume'], errors='coerce').dropna()
                if len(vol) >= 60:
                    recent_vol = vol.tail(20).mean()
                    avg_vol = vol.tail(250).mean() if len(vol) >= 250 else vol.mean()

                    # 量比
                    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1

                    # 从上期获取其他数据
                    prev = load_json("timing_scores.json")
                    prev_fg = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("fear_greed_index", {}).get("value", 50)

                    # 简易估算: 上期值 ± 量比调整
                    if isinstance(prev_fg, str):
                        prev_fg = 50
                    fg = max(10, min(90, prev_fg + (vol_ratio - 1) * 15))

                    if fg < 20:
                        val = f"{fg:.0f}"
                        score = max(15, fg)
                    elif fg < 40:
                        val = f"{fg:.0f}"
                        score = fg
                    elif fg < 60:
                        val = f"{fg:.0f}"
                        score = fg
                    else:
                        val = f"{fg:.0f}"
                        score = min(85, fg)

                    logger.info(f"      恐贪指数: {fg:.0f} (量比={vol_ratio:.2f})")
                    return score, int(fg)
        except Exception as e:
            logger.warning(f"      恐贪指数异常: {e}")

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {})
                .get("fear_greed_index", {}).get("score", 40),
                prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {})
                .get("fear_greed_index", {}).get("value", 33))

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
        """融资买入占比"""
        prev = load_json("timing_scores.json")
        prev_val = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("margin_buying_ratio", {}).get("value", "8-10%")
        prev_score = prev.get("dimensions", {}).get("sentiment", {}).get("indicators", {}).get("margin_buying_ratio", {}).get("score", 48)
        logger.info(f"      融资买入占比: {prev_val} (使用上期值)")
        return prev_score, prev_val

    # ----------------------------------------------------------
    # 维度6: 微观结构 (micro_structure)
    # ----------------------------------------------------------
    def calc_micro_structure(self) -> dict:
        """
        微观结构维度
        指标:
          - 交易拥挤度(前5%占比): 权重35%
          - 行业成交集中度: 权重25%
          - 交易集中度趋势: 权重20%
          - 中证1000/沪深300比值: 权重20%
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
            "sub_weight": 35,
            "bottom_threshold": "从极端回落至40%以下",
            "top_threshold": ">48%"
        }

        # --- 行业成交集中度 ---
        ind_score, ind_val = self._calc_industry_concentration()
        indicators["industry_concentration"] = {
            "name": "行业成交集中度",
            "value": ind_val or "数据待更新",
            "score": ind_score,
            "sub_weight": 25,
            "bottom_threshold": "前三行业<25%",
            "top_threshold": "前三行业>40%"
        }

        # --- 交易集中度趋势 ---
        trend_score, trend_val = self._calc_concentration_trend()
        indicators["concentration_trend"] = {
            "name": "交易集中度趋势",
            "value": trend_val or "数据待更新",
            "score": trend_score,
            "sub_weight": 20,
            "bottom_signal": "从集中到分散",
            "top_signal": "加速集中"
        }

        # --- 中证1000/沪深300比值 ---
        ratio_score, ratio_val = self._calc_small_large_ratio()
        indicators["csi1000_hs300_ratio"] = {
            "name": "中证1000/沪深300比值",
            "value": ratio_val or "数据待更新",
            "score": ratio_score,
            "sub_weight": 20,
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

        prev = load_json("timing_scores.json")
        return (prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {})
                .get("industry_concentration", {}).get("score", 65),
                prev.get("dimensions", {}).get("micro_structure", {}).get("indicators", {})
                .get("industry_concentration", {}).get("value", "电子通信拥挤"))

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
                .get("csi1000_hs300_ratio", {}).get("value", "偏弱(%B=0.17)"))

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
        sf_val = liq.get("social_financing_trend", {}).get("value", "")
        check_bottom("回升" in sf_val or "触底" in sf_val, "宏观流动性", "社融增速触底回升", 5)
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
        check_bottom(fg_val < 20, "市场情绪", "恐贪指数<20(极度恐惧)", 3)
        check_top(fg_val > 80, "市场情绪", "恐贪指数>80(极度贪婪)", 4)

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
        sf_val = liq.get("social_financing_trend", {}).get("value", "")
        add_triggers.append({"condition": "社融增速触底回升(连续3个月环比改善)", "met": "回升" in sf_val})

        fg_val = sent.get("fear_greed_index", {}).get("value", 50)
        if isinstance(fg_val, str):
            try: fg_val = float(fg_val)
            except: fg_val = 50
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
        reduce_triggers.append({"condition": "恐贪指数回到80以上", "met": fg_val > 80})

        sf_score = liq.get("social_financing_trend", {}).get("score", 50)
        reduce_triggers.append({"condition": "社融增速进一步下行", "met": sf_score > 65})

        reduce_triggers.append({"condition": "美联储重启加息", "met": False})

        # 估值触发
        div_str = eb.get("dividend_bond_spread_hs300", {}).get("value", "0%")
        try: div_num = float(div_str.replace('%', ''))
        except: div_num = 1
        reduce_triggers.append({"condition": "股息率-国债利差(沪深300)降至0.3%以下", "met": div_num < 0.3})

        return {"add_position": add_triggers, "reduce_position": reduce_triggers}

    def generate_position_advice(self, composite_score, dimensions):
        """生成仓位建议"""
        # 根据综合得分确定仓位区间
        if composite_score < 25:
            equity_range = "60%-75%"
            base_equity = 67
        elif composite_score < 35:
            equity_range = "50%-65%"
            base_equity = 57
        elif composite_score < 45:
            equity_range = "40%-55%"
            base_equity = 47
        elif composite_score < 55:
            equity_range = "35%-50%"
            base_equity = 42
        elif composite_score < 65:
            equity_range = "25%-40%"
            base_equity = 32
        else:
            equity_range = "15%-30%"
            base_equity = 22

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

        # 小盘
        ratio_val = dimensions.get("micro_structure", {}).get("indicators", {}).get("csi1000_hs300_ratio", {}).get("value", "")
        if "极弱" in ratio_val or "偏弱" in ratio_val:
            small_range = "5%-8%"
            small_note = "比值仍处下行通道，暂不追涨"
        else:
            small_range = "8%-12%"
            small_note = "小盘有企稳迹象"

        status = "中性区间，略偏暖" if 40 <= composite_score <= 60 else "偏低/有吸引力" if composite_score < 40 else "偏高/谨慎" if composite_score > 60 else "中性"

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

        # 仓位区间
        if composite_score < 30:
            pos_range = "55%-70%"
        elif composite_score < 40:
            pos_range = "45%-60%"
        elif composite_score < 50:
            pos_range = "40%-55%"
        elif composite_score < 60:
            pos_range = "30%-45%"
        else:
            pos_range = "20%-35%"

        # 生成信号和建议
        signals = self.generate_signals(dimensions)
        triggers = self.generate_triggers(dimensions)
        position_advice, market_status = self.generate_position_advice(composite_score, dimensions)

        # 风格轮动 (从上期数据继承)
        prev = load_json("timing_scores.json")
        style_rotation = prev.get("style_rotation", {})
        # 更新大小盘比值
        micro = dimensions.get("micro_structure", {}).get("indicators", {})
        ratio_data = micro.get("csi1000_hs300_ratio", {})
        if ratio_data:
            style_rotation["large_small"] = {
                "indicator": "中证1000/沪深300比值",
                "current_value": ratio_data.get("value", ""),
                "status": "小盘偏弱" if "偏弱" in str(ratio_data.get("value", "")) else "小盘走强",
                "signal": "不追涨" if "偏弱" in str(ratio_data.get("value", "")) else "可关注",
                "detail": f"小盘风格{ratio_data.get('value', '待更新')}"
            }

        output = {
            "update_date": today_str(),
            "version": "v2.0",
            "composite_score": composite_score,
            "position_range": pos_range,
            "market_status": market_status,
            "dimensions": dimensions,
            "style_rotation": style_rotation,
            "signals": signals,
            "triggers": triggers,
            "position_advice": position_advice
        }

        logger.info(f"\n  📊 综合得分: {composite_score}")
        logger.info(f"  📊 仓位区间: {pos_range}")
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
            df = safe_ak_call(ak.stock_zh_index_daily, symbol=symbol)
            if df is not None and not df.empty:
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
            df = safe_ak_call(ak.stock_share_change)
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
            df = safe_ak_call(ak.stock_hsgt_north_net_flow_in_em, symbol="北向")
            if df is not None and not df.empty:
                if 'value' in df.columns or '当日净流入' in df.columns:
                    col = '当日净流入' if '当日净流入' in df.columns else 'value'
                    df['date'] = pd.to_datetime(df.get('date', df.index))
                    weekly_flow = df.set_index('date')[col].resample('W').sum()
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
            df = safe_ak_call(ak.stock_hsgt_north_net_flow_in_em, symbol="北向")
            if df is not None and not df.empty:
                col = '当日净流入' if '当日净流入' in df.columns else 'value'
                df['date'] = pd.to_datetime(df.get('date', df.index))
                weekly_flow = df.set_index('date')[col].resample('W').sum()
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
            "version": "v2.0",
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
        engine = TimingScoreEngine()
        try:
            timing_data = engine.compute_all()
            save_json(timing_data, "timing_scores.json")
            logger.info("\n✅ timing_scores.json 更新完成!")
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
        return

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
    try:
        right_engine = TimingRightEngine()
        right_data = right_engine.compute_all()
        save_json(right_data, "timing_right_scores.json")
        script_results["timing_right_scores"] = True
    except Exception as e:
        logger.error(f"❌ timing_right_scores 失败: {e}", exc_info=True)
        script_results["timing_right_scores"] = False

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
