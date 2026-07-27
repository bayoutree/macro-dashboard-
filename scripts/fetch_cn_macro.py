#!/usr/bin/env python3
"""
获取中国宏观经济数据 → data/cn_macro.json
数据源: AKShare
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config import logger, today_str, safe_float, save_json, series_to_history, ts_to_date_str


def safe_call(func, *args, **kwargs):
    """安全调用 AKShare 接口，失败返回空 DataFrame"""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, pd.DataFrame):
            logger.info(f"  ✓ {func.__name__}: {len(result)} 行")
        else:
            logger.info(f"  ✓ {func.__name__}: 返回成功")
        return result
    except Exception as e:
        logger.warning(f"  ✗ {func.__name__}: {e}")
        return pd.DataFrame()


def main():
    logger.info("=" * 60)
    logger.info("开始获取中国宏观数据 (AKShare)")
    logger.info("=" * 60)

    import akshare as ak

    # ============================================================
    # 获取数据
    # ============================================================

    # ---------- 领先指标 ----------
    logger.info("\n[1/6] 领先指标: PMI, 社融...")
    pmi_df = safe_call(ak.macro_china_pmi)
    shrzgm_df = safe_call(ak.macro_china_shrzgm)

    # ---------- 同步指标 ----------
    logger.info("\n[2/6] 同步指标: GDP...")
    gdp_df = safe_call(ak.macro_china_gdp)

    # ---------- 滞后指标 ----------
    logger.info("\n[3/6] 滞后指标: CPI, PPI, M2...")
    cpi_df = safe_call(ak.macro_china_cpi)
    ppi_df = safe_call(ak.macro_china_ppi)
    m2_df = safe_call(ak.macro_china_m2_yearly)

    # ---------- A股指数 ----------
    logger.info("\n[4/6] A股指数: 上证, 沪深300...")
    sse_df = safe_call(ak.stock_zh_index_daily, symbol="sh000001")
    time.sleep(1)
    hs300_df = safe_call(ak.stock_zh_index_daily, symbol="sh000300")

    # ---------- 中国国债收益率 ----------
    logger.info("\n[5/6] 中国国债收益率...")
    cn_bond = safe_call(ak.bond_gb_zh_sina, symbol="中国10年期国债")

    # ---------- A股估值 ----------
    logger.info("\n[6/6] A股估值: PE/PB...")
    hs300_pe_df = safe_call(ak.stock_index_pe_lg, symbol="沪深300")
    time.sleep(1)
    csi500_pe_df = safe_call(ak.stock_index_pe_lg, symbol="中证500")

    # ============================================================
    # 解析各指标最新值 & 构建历史数据
    # ============================================================
    logger.info("\n解析指标值 & 构建历史数据...")

    # --- PMI (数据按时间降序，第一行最新) ---
    pmi_val, pmi_date = None, None
    pmi_history = []
    if not pmi_df.empty:
        try:
            pmi_val = safe_float(pmi_df['制造业-指数'].iloc[0])
            raw_month = str(pmi_df['月份'].iloc[0])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                pmi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  PMI: {pmi_val}, 日期: {pmi_date}")

            # 构建 PMI 历史: 反转数据框使其按时间升序
            pmi_df_sorted = pmi_df.iloc[::-1].reset_index(drop=True)
            for _, row in pmi_df_sorted.iterrows():
                try:
                    val = safe_float(row['制造业-指数'])
                    raw = str(row['月份'])
                    m = re.search(r'(\d{4})年(\d{1,2})月', raw)
                    if m and val is not None:
                        date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                        pmi_history.append({"date": date_label, "value": val})
                except Exception:
                    pass
            # 只保留最近36个月
            pmi_history = pmi_history[-36:]
            logger.info(f"  PMI 历史: {len(pmi_history)} 个数据点")
        except Exception as e:
            logger.warning(f"  解析PMI失败: {e}")

    # --- 社融 (数据按时间升序，最后一行最新) ---
    shrzgm_val, shrzgm_date = None, None
    shrzgm_history = []
    if not shrzgm_df.empty:
        try:
            shrzgm_val = safe_float(shrzgm_df['社会融资规模增量'].iloc[-1])
            raw_month = str(shrzgm_df['月份'].iloc[-1])
            if len(raw_month) >= 6:
                shrzgm_date = f"{raw_month[:4]}-{raw_month[4:6]}"
            logger.info(f"  社融: {shrzgm_val} 亿元, 日期: {shrzgm_date}")

            # 构建社融历史
            for _, row in shrzgm_df.iterrows():
                try:
                    val = safe_float(row['社会融资规模增量'])
                    raw = str(row['月份'])
                    if val is not None and len(raw) >= 6:
                        date_label = f"{raw[:4]}-{raw[4:6]}"
                        shrzgm_history.append({"date": date_label, "value": val})
                except Exception:
                    pass
            shrzgm_history = shrzgm_history[-36:]
            logger.info(f"  社融历史: {len(shrzgm_history)} 个数据点")
        except Exception as e:
            logger.warning(f"  解析社融失败: {e}")

    # --- GDP (数据按时间降序) ---
    gdp_val, gdp_date = None, None
    gdp_history = []
    if not gdp_df.empty:
        try:
            for idx in range(len(gdp_df)):
                quarter_str_val = str(gdp_df['季度'].iloc[idx])
                if '第1季度' in quarter_str_val and '第1-' not in quarter_str_val:
                    gdp_val = safe_float(gdp_df['国内生产总值-同比增长'].iloc[idx])
                    gdp_date = quarter_str_val
                    break
            if gdp_val is None:
                gdp_val = safe_float(gdp_df['国内生产总值-同比增长'].iloc[0])
                gdp_date = str(gdp_df['季度'].iloc[0])
            logger.info(f"  GDP: {gdp_val}%, 日期: {gdp_date}")

            # 构建 GDP 历史: 只取单季度数据（含"第1季度"但不含"第1-"），反转使其升序
            gdp_quarterly = []
            for _, row in gdp_df.iterrows():
                q = str(row['季度'])
                val = safe_float(row['国内生产总值-同比增长'])
                if val is not None:
                    # 标准化季度名称
                    # 格式如 "2026年第1季度" 或 "2025年第1-4季度"
                    m_match = re.match(r'(\d{4})年', q)
                    if m_match:
                        year = m_match.group(1)
                        if '第1季度' in q and '第1-' not in q:
                            gdp_quarterly.append({"date": f"{year}Q1", "value": val})
                        elif '第2季度' in q and '第1-' not in q and '第1-2' not in q:
                            gdp_quarterly.append({"date": f"{year}Q2", "value": val})
                        elif '第3季度' in q and '第1-' not in q and '第1-3' not in q:
                            gdp_quarterly.append({"date": f"{year}Q3", "value": val})
                        elif '第4季度' in q or '第1-4季度' in q:
                            gdp_quarterly.append({"date": f"{year}Q4", "value": val})

            # 去重（保留最后出现的即最新的）
            seen = {}
            for item in gdp_quarterly:
                seen[item["date"]] = item["value"]
            gdp_history = [{"date": k, "value": v} for k, v in sorted(seen.items())]
            gdp_history = gdp_history[-16:]  # 最近16个季度
            logger.info(f"  GDP 历史: {len(gdp_history)} 个数据点")
        except Exception as e:
            logger.warning(f"  解析GDP失败: {e}")

    # --- CPI (数据按时间降序) ---
    cpi_val, cpi_date = None, None
    cpi_history = []
    if not cpi_df.empty:
        try:
            cpi_val = safe_float(cpi_df['全国-同比增长'].iloc[0])
            raw_month = str(cpi_df['月份'].iloc[0])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                cpi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  CPI: {cpi_val}%, 日期: {cpi_date}")

            # 构建 CPI 历史: 反转使其升序
            cpi_df_sorted = cpi_df.iloc[::-1].reset_index(drop=True)
            for _, row in cpi_df_sorted.iterrows():
                try:
                    val = safe_float(row['全国-同比增长'])
                    raw = str(row['月份'])
                    m = re.search(r'(\d{4})年(\d{1,2})月', raw)
                    if m and val is not None:
                        date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                        cpi_history.append({"date": date_label, "value": val})
                except Exception:
                    pass
            cpi_history = cpi_history[-36:]
            logger.info(f"  CPI 历史: {len(cpi_history)} 个数据点")
        except Exception as e:
            logger.warning(f"  解析CPI失败: {e}")

    # --- PPI (数据按时间降序) ---
    ppi_val, ppi_date = None, None
    ppi_history = []
    if not ppi_df.empty:
        try:
            ppi_val = safe_float(ppi_df['当月同比增长'].iloc[0])
            raw_month = str(ppi_df['月份'].iloc[0])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                ppi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  PPI: {ppi_val}%, 日期: {ppi_date}")

            # 构建 PPI 历史: 反转使其升序
            ppi_df_sorted = ppi_df.iloc[::-1].reset_index(drop=True)
            for _, row in ppi_df_sorted.iterrows():
                try:
                    val = safe_float(row['当月同比增长'])
                    raw = str(row['月份'])
                    m = re.search(r'(\d{4})年(\d{1,2})月', raw)
                    if m and val is not None:
                        date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                        ppi_history.append({"date": date_label, "value": val})
                except Exception:
                    pass
            ppi_history = ppi_history[-36:]
            logger.info(f"  PPI 历史: {len(ppi_history)} 个数据点")
        except Exception as e:
            logger.warning(f"  解析PPI失败: {e}")

    # --- M2 ---
    m2_val, m2_date = None, None
    if not m2_df.empty:
        try:
            m2_sorted = m2_df.dropna(subset=['今值'])
            if not m2_sorted.empty:
                last_row = m2_sorted.iloc[-1]
                m2_val = safe_float(last_row['今值'])
                m2_date = str(last_row['日期'])[:7]
            logger.info(f"  M2: {m2_val}%, 日期: {m2_date}")
        except Exception as e:
            logger.warning(f"  解析M2失败: {e}")

    # --- A股指数 ---
    sse_val, sse_date = None, None
    sse_series = pd.Series(dtype=float)
    if not sse_df.empty and 'close' in sse_df.columns:
        try:
            sse_df['date'] = pd.to_datetime(sse_df['date'])
            sse_series = sse_df.set_index('date')['close'].dropna()
            sse_val = safe_float(sse_series.iloc[-1])
            sse_date = str(sse_series.index[-1])[:10]
            logger.info(f"  上证: {sse_val}, 日期: {sse_date}")
        except Exception as e:
            logger.warning(f"  解析上证失败: {e}")

    hs300_val, hs300_date = None, None
    hs300_series = pd.Series(dtype=float)
    if not hs300_df.empty and 'close' in hs300_df.columns:
        try:
            hs300_df['date'] = pd.to_datetime(hs300_df['date'])
            hs300_series = hs300_df.set_index('date')['close'].dropna()
            hs300_val = safe_float(hs300_series.iloc[-1])
            hs300_date = str(hs300_series.index[-1])[:10]
            logger.info(f"  沪深300: {hs300_val}, 日期: {hs300_date}")
        except Exception as e:
            logger.warning(f"  解析沪深300失败: {e}")

    # --- 中国10Y国债收益率 ---
    cn10y_val, cn10y_date = None, None
    cn10y_history = []
    if not cn_bond.empty and 'close' in cn_bond.columns:
        try:
            cn_bond['date'] = pd.to_datetime(cn_bond['date'])
            bond_series = cn_bond.set_index('date')['close'].dropna()
            cn10y_val = safe_float(bond_series.iloc[-1])
            cn10y_date = str(bond_series.index[-1])[:10]
            cn10y_history = series_to_history(bond_series, freq="daily", max_points=60)
            logger.info(f"  中国10Y国债: {cn10y_val}%, 日期: {cn10y_date}")
        except Exception as e:
            logger.warning(f"  解析国债收益率失败: {e}")

    # --- 沪深300 PE ---
    hs300_pe_val = None
    hs300_pe_pct = None
    hs300_pe_history = []
    if not hs300_pe_df.empty:
        try:
            pe_col = '滚动市盈率'
            if pe_col in hs300_pe_df.columns:
                hs300_pe_val = safe_float(hs300_pe_df[pe_col].dropna().iloc[-1])
                pe_series = hs300_pe_df.set_index('日期')[pe_col].dropna()
                pe_series.index = pd.to_datetime(pe_series.index)
                if len(pe_series) > 100:
                    hs300_pe_pct = safe_float((pe_series < hs300_pe_val).mean() * 100)
                hs300_pe_history = series_to_history(pe_series, freq="daily", max_points=60)
            logger.info(f"  沪深300 PE(TTM): {hs300_pe_val}, 分位: {hs300_pe_pct}%")
        except Exception as e:
            logger.warning(f"  解析沪深300 PE失败: {e}")

    # --- 中证500 PE ---
    csi500_pe_val = None
    csi500_pe_history = []
    if not csi500_pe_df.empty:
        try:
            pe_col = '滚动市盈率'
            if pe_col in csi500_pe_df.columns:
                csi500_pe_val = safe_float(csi500_pe_df[pe_col].dropna().iloc[-1])
                pe_series = csi500_pe_df.set_index('日期')[pe_col].dropna()
                pe_series.index = pd.to_datetime(pe_series.index)
                csi500_pe_history = series_to_history(pe_series, freq="daily", max_points=60)
            logger.info(f"  中证500 PE(TTM): {csi500_pe_val}")
        except Exception as e:
            logger.warning(f"  解析中证500 PE失败: {e}")

    # ============================================================
    # 构建历史数据字典
    # ============================================================
    history = {}

    # 上证历史
    if not sse_series.empty:
        history["sse_index"] = series_to_history(sse_series, freq="daily", max_points=120)

    # 沪深300历史
    if not hs300_series.empty:
        history["hs300_index"] = series_to_history(hs300_series, freq="daily", max_points=120)

    # 中国10Y国债
    history["cn_10y_bond"] = cn10y_history

    # PE 历史
    history["hs300_pe"] = hs300_pe_history

    # === 新增：补齐缺失指标的历史数据 ===

    # PMI 历史
    if pmi_history:
        history["pmi"] = pmi_history

    # 社融历史
    if shrzgm_history:
        history["social_financing"] = shrzgm_history

    # GDP 历史
    if gdp_history:
        history["gdp_growth"] = gdp_history

    # CPI 历史
    if cpi_history:
        history["cpi_yoy"] = cpi_history

    # PPI 历史
    if ppi_history:
        history["ppi_yoy"] = ppi_history

    # 中证500 PE 历史
    if csi500_pe_history:
        history["csi500_pe"] = csi500_pe_history

    # ============================================================
    # 输出 JSON
    # ============================================================
    output = {
        "update_time": today_str(),
        "leading": {
            "pmi": {
                "value": pmi_val,
                "date": pmi_date,
                "trend": "expanding" if pmi_val and pmi_val > 50 else "contracting" if pmi_val else "unknown"
            },
            "social_financing": {
                "value": shrzgm_val,
                "date": shrzgm_date,
                "unit": "亿元"
            }
        },
        "coincident": {
            "gdp_growth": {
                "value": gdp_val,
                "date": gdp_date,
                "unit": "%"
            }
        },
        "lagging": {
            "cpi_yoy": {"value": cpi_val, "date": cpi_date, "unit": "%"},
            "ppi_yoy": {"value": ppi_val, "date": ppi_date, "unit": "%"},
            "m2_yoy": {"value": m2_val, "date": m2_date, "unit": "%"}
        },
        "stock_index": {
            "sse_composite": {"value": sse_val, "date": sse_date},
            "hs300": {"value": hs300_val, "date": hs300_date}
        },
        "bond": {
            "cn_10y_yield": {"value": cn10y_val, "date": cn10y_date}
        },
        "valuation": {
            "hs300_pe": {"value": hs300_pe_val, "percentile": hs300_pe_pct, "date": today_str()},
            "hs300_pb": {"value": None, "date": today_str()},
            "csi500_pe": {"value": csi500_pe_val, "date": today_str()}
        },
        "history": history
    }

    save_json(output, "cn_macro.json")
    logger.info("\n✅ cn_macro.json 生成完成!")

    # 打印历史数据摘要
    logger.info("\n📊 历史数据汇总:")
    for key, val in history.items():
        logger.info(f"  {key}: {len(val)} 个数据点")

    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 获取中国宏观数据失败: {e}", exc_info=True)
        sys.exit(1)
