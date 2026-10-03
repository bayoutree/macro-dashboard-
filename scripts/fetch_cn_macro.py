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


# ---------- 中国 GDP / M2 采集辅助（2026-10-04 投分裁决修复） ----------
_CN_QUARTER_MAP = {"一": 1, "二": 2, "三": 3, "四": 4}


def _cn_quarter_label(s):
    """'2026年第二季度' -> '2026Q2'；无法解析返回 None。"""
    m = re.search(r"(\d{4})\s*年\s*第\s*([一二三四1-4])\s*季度", str(s))
    if not m:
        return None
    y = m.group(1)
    q = _CN_QUARTER_MAP.get(m.group(2))
    if q is None:
        try:
            q = int(m.group(2))
        except Exception:
            return None
    return f"{y}Q{q}"


def _cn_quarter_sort_key(label):
    m = re.match(r"(\d{4})Q([1-4])", str(label))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _fetch_cn_gdp_index():
    """国家统计局季度数据：国内生产总值指数(上年同期=100)当季值 → 单季同比。"""
    import akshare as ak
    return ak.macro_china_nbs_nation(
        kind="季度数据",
        path="国民经济核算>国内生产总值指数",
        period="2010-",
    )



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

    # 社融: 央行官方源（调查统计司 -> 统计数据 -> 社会融资规模）
    # 原商务部 data.mofcom.gov.cn 源自 2026-04 起停更，切换至央行官方 xlsx。
    pbc_sf = {"stock": [], "flow": []}
    try:
        from fetch_pbc_social_financing import fetch_social_financing as _fetch_pbc_sf
        pbc_sf = _fetch_pbc_sf()
        logger.info(
            f"  ✓ 央行社融: 存量 {len(pbc_sf['stock'])} 点 / 增量 {len(pbc_sf['flow'])} 点"
        )
    except Exception as _e:
        logger.warning(f"  ✗ 央行社融采集失败: {_e}")

    # ---------- 同步指标 ----------
    logger.info("\n[2/6] 同步指标: GDP...")
    # GDP: 改用国家统计局季度"国内生产总值指数(上年同期=100)当季值"（单季同比）。
    # 原 macro_china_gdp 为累计同比（第1/第1-2/第1-3/第1-4季度），且旧解析只认第1季度。
    gdp_idx_df = safe_call(_fetch_cn_gdp_index)

    # ---------- 滞后指标 ----------
    logger.info("\n[3/6] 滞后指标: CPI, PPI, M2...")
    cpi_df = safe_call(ak.macro_china_cpi)
    ppi_df = safe_call(ak.macro_china_ppi)
    # M2: 改用 macro_china_money_supply（国家统计局/央行口径，含同比列，更新至最新）。
    # 原 macro_china_m2_yearly（金十源）自 2025-08 停更，已弃用。
    m2_df = safe_call(ak.macro_china_money_supply)

    # ---------- A股指数 ----------
    logger.info("\n[4/6] A股指数: 上证, 沪深300...")
    # 使用新浪数据源 (更稳定)
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
    # 解析各指标最新值
    # ============================================================
    logger.info("\n解析指标值...")

    # --- PMI (数据按时间降序，第一行最新) ---
    pmi_val, pmi_date = None, None
    if not pmi_df.empty:
        try:
            # 列: 月份, 制造业-指数, 制造业-同比增长, 非制造业-指数, 非制造业-同比增长
            pmi_val = safe_float(pmi_df['制造业-指数'].iloc[0])
            # 解析月份: "2026年06月份" → "2026-06"
            raw_month = str(pmi_df['月份'].iloc[0])
            import re
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                pmi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  PMI: {pmi_val}, 日期: {pmi_date}")
        except Exception as e:
            logger.warning(f"  解析PMI失败: {e}")

    # --- GDP 单季同比 (国家统计局季度指数: 上年同期=100, 当季值; 单季同比 = 指数 - 100) ---
    gdp_val, gdp_date = None, None
    gdp_history = []
    if not gdp_idx_df.empty:
        try:
            row_key = None
            for ix in gdp_idx_df.index:
                s = str(ix)
                if "国内生产总值指数" in s and "当季值" in s:
                    row_key = ix
                    break
            if row_key is not None:
                for col in gdp_idx_df.columns:
                    label = _cn_quarter_label(col)
                    v = safe_float(gdp_idx_df.loc[row_key, col])
                    if label is None or v is None:
                        continue
                    gdp_history.append({"date": label, "value": round(v - 100.0, 1)})
                gdp_history.sort(key=lambda x: _cn_quarter_sort_key(x["date"]))
                gdp_history = gdp_history[-48:] if len(gdp_history) > 48 else gdp_history
                if gdp_history:
                    gdp_val = gdp_history[-1]["value"]
                    gdp_date = gdp_history[-1]["date"]
            logger.info(f"  GDP: {gdp_val}%, 日期: {gdp_date}")
        except Exception as e:
            logger.warning(f"  解析GDP失败: {e}")

    # --- CPI (数据按时间降序) ---
    cpi_val, cpi_date = None, None
    if not cpi_df.empty:
        try:
            # 列: 月份, 全国-当月, 全国-同比增长, 全国-环比增长, ...
            cpi_val = safe_float(cpi_df['全国-同比增长'].iloc[0])
            raw_month = str(cpi_df['月份'].iloc[0])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                cpi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  CPI: {cpi_val}%, 日期: {cpi_date}")
        except Exception as e:
            logger.warning(f"  解析CPI失败: {e}")

    # --- PPI (数据按时间降序) ---
    ppi_val, ppi_date = None, None
    if not ppi_df.empty:
        try:
            # 列: 月份, 当月, 当月同比增长, 累计
            ppi_val = safe_float(ppi_df['当月同比增长'].iloc[0])
            raw_month = str(ppi_df['月份'].iloc[0])
            m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
            if m:
                ppi_date = f"{m.group(1)}-{m.group(2).zfill(2)}"
            logger.info(f"  PPI: {ppi_val}%, 日期: {ppi_date}")
        except Exception as e:
            logger.warning(f"  解析PPI失败: {e}")

    # --- M2 (源: macro_china_money_supply; 列: 月份 '2026年08月份', '货币和准货币(M2)-同比增长'; 按时间降序) ---
    m2_val, m2_date = None, None
    m2_history = []
    if not m2_df.empty and '货币和准货币(M2)-同比增长' in m2_df.columns:
        try:
            for _, row in m2_df.iterrows():
                raw = str(row.get('月份', ''))
                m = re.search(r'(\d{4})年(\d{1,2})月', raw)
                v = safe_float(row['货币和准货币(M2)-同比增长'])
                if m and v is not None:
                    m2_history.append({"date": f"{m.group(1)}-{m.group(2).zfill(2)}", "value": v})
            m2_history.sort(key=lambda x: x["date"])
            m2_history = m2_history[-48:] if len(m2_history) > 48 else m2_history
            if m2_history:
                m2_val = m2_history[-1]["value"]
                m2_date = m2_history[-1]["date"]
            logger.info(f"  M2: {m2_val}%, 日期: {m2_date}")
        except Exception as e:
            logger.warning(f"  解析M2失败: {e}")

    # --- 社融 (数据按时间升序，最后一行最新) ---
    shrzgm_val, shrzgm_date = None, None
    shrzgm_stock_val, shrzgm_stock_date = None, None
    if pbc_sf.get('flow'):
        try:
            last = pbc_sf['flow'][-1]
            shrzgm_val, shrzgm_date = float(last['value']), last['date']
            logger.info(f"  社融增量: {shrzgm_val} 亿元, 日期: {shrzgm_date}")
        except Exception as e:
            logger.warning(f"  解析社融增量失败: {e}")
    if pbc_sf.get('stock'):
        try:
            last = pbc_sf['stock'][-1]
            shrzgm_stock_val, shrzgm_stock_date = float(last['value']), last['date']
            logger.info(f"  社融存量: {shrzgm_stock_val} 万亿元, 日期: {shrzgm_stock_date}")
        except Exception as e:
            logger.warning(f"  解析社融存量失败: {e}")

    # --- A股指数 (stock_zh_index_daily 返回 date/open/high/low/close/volume) ---
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

    # --- 中国10Y国债收益率 (bond_gb_zh_sina: date/open/high/low/close/volume) ---
    cn10y_val, cn10y_date = None, None
    cn10y_history = []
    if not cn_bond.empty and 'close' in cn_bond.columns:
        try:
            cn_bond['date'] = pd.to_datetime(cn_bond['date'])
            bond_series = cn_bond.set_index('date')['close'].dropna()
            cn10y_val = safe_float(bond_series.iloc[-1])
            cn10y_date = str(bond_series.index[-1])[:10]
            cn10y_history = series_to_history(bond_series, freq="daily", max_points=250)
            logger.info(f"  中国10Y国债: {cn10y_val}%, 日期: {cn10y_date}")
        except Exception as e:
            logger.warning(f"  解析国债收益率失败: {e}")

    # --- 沪深300 PE/PB (stock_index_pe_lg) ---
    hs300_pe_val = None
    hs300_pe_pct = None
    hs300_pe_history = []
    if not hs300_pe_df.empty:
        try:
            # 列: 日期, 指数, 等权静态市盈率, 静态市盈率, 静态市盈率中位数, 等权滚动市盈率, 滚动市盈率, 滚动市盈率中位数
            pe_col = '滚动市盈率'  # 即 TTM PE
            pb_col = None  # PE接口不含PB
            if pe_col in hs300_pe_df.columns:
                hs300_pe_val = safe_float(hs300_pe_df[pe_col].dropna().iloc[-1])

                # 计算历史分位
                pe_series = hs300_pe_df.set_index('日期')[pe_col].dropna()
                pe_series.index = pd.to_datetime(pe_series.index)
                if len(pe_series) > 100:
                    hs300_pe_pct = safe_float((pe_series < hs300_pe_val).mean() * 100)
                hs300_pe_history = series_to_history(pe_series, freq="daily", max_points=250)
            logger.info(f"  沪深300 PE(TTM): {hs300_pe_val}, 分位: {hs300_pe_pct}%")
        except Exception as e:
            logger.warning(f"  解析沪深300 PE失败: {e}")

    # --- 中证500 PE ---
    csi500_pe_val = None
    if not csi500_pe_df.empty:
        try:
            pe_col = '滚动市盈率'
            if pe_col in csi500_pe_df.columns:
                csi500_pe_val = safe_float(csi500_pe_df[pe_col].dropna().iloc[-1])
            logger.info(f"  中证500 PE(TTM): {csi500_pe_val}")
        except Exception as e:
            logger.warning(f"  解析中证500 PE失败: {e}")

    # --- 中证500 PE 历史 ---
    csi500_pe_history = []
    if not csi500_pe_df.empty:
        try:
            pe_col = '滚动市盈率'
            if pe_col in csi500_pe_df.columns:
                csi500_pe_series = csi500_pe_df.set_index('日期')[pe_col].dropna()
                csi500_pe_series.index = pd.to_datetime(csi500_pe_series.index)
                csi500_pe_history = series_to_history(csi500_pe_series, freq="daily", max_points=250)
            logger.info(f"  中证500 PE 历史: {len(csi500_pe_history)} 条")
        except Exception as e:
            logger.warning(f"  解析中证500 PE历史失败: {e}")

    # ============================================================
    # 构建历史数据
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
    history["csi500_pe"] = csi500_pe_history

    # --- PMI 历史 (从 pmi_df 的 "制造业-指数" 列) ---
    if not pmi_df.empty and '制造业-指数' in pmi_df.columns:
        try:
            # pmi_df 按时间降序，需反转为升序
            pmi_hist_df = pmi_df.copy().iloc[::-1]
            # 解析月份列: "2026年06月份" → "2026-06"
            dates = []
            for raw in pmi_hist_df['月份']:
                raw_s = str(raw)
                m = re.search(r'(\d{4})年(\d{1,2})月', raw_s)
                if m:
                    dates.append(f"{m.group(1)}-{m.group(2).zfill(2)}")
                else:
                    dates.append(None)
            pmi_hist_df = pmi_hist_df.copy()
            pmi_hist_df['parsed_date'] = dates
            pmi_hist_df = pmi_hist_df.dropna(subset=['parsed_date', '制造业-指数'])
            pmi_history = []
            for _, row in pmi_hist_df.iterrows():
                v = safe_float(row['制造业-指数'])
                if v is not None:
                    pmi_history.append({"date": row['parsed_date'], "value": v})
            # 保留最近 max_points 条
            pmi_history = pmi_history[-48:] if len(pmi_history) > 48 else pmi_history
            history["pmi"] = pmi_history
            logger.info(f"  PMI 历史: {len(pmi_history)} 条")
        except Exception as e:
            logger.warning(f"  构建PMI历史失败: {e}")

    # --- 社融历史（央行官方源）---
    # social_financing        : 月度增量（亿元），前端"社融增量"卡片契约
    # social_financing_stock  : 月度存量（万亿元），供 social_financing_trend 计算存量同比
    if pbc_sf.get('flow'):
        try:
            shrzgm_history = [
                {"date": d["date"], "value": float(d["value"])} for d in pbc_sf['flow']
            ]
            shrzgm_history = shrzgm_history[-48:] if len(shrzgm_history) > 48 else shrzgm_history
            history["social_financing"] = shrzgm_history
            logger.info(f"  社融增量历史: {len(shrzgm_history)} 条")
        except Exception as e:
            logger.warning(f"  构建社融增量历史失败: {e}")
    if pbc_sf.get('stock'):
        try:
            sf_stock_history = [
                {"date": d["date"], "value": float(d["value"])} for d in pbc_sf['stock']
            ]
            history["social_financing_stock"] = sf_stock_history
            logger.info(f"  社融存量历史: {len(sf_stock_history)} 条")
        except Exception as e:
            logger.warning(f"  构建社融存量历史失败: {e}")

    # --- GDP 历史（单季同比，来自国家统计局季度指数，见上） ---
    if gdp_history:
        history["gdp_growth"] = gdp_history
        logger.info(f"  GDP 历史: {len(gdp_history)} 条")

    # --- M2 历史（同比，来自 macro_china_money_supply） ---
    if m2_history:
        history["m2_yoy"] = m2_history
        logger.info(f"  M2 历史: {len(m2_history)} 条")

    # --- CPI 历史 (从 cpi_df 的 "全国-同比增长" 列) ---
    if not cpi_df.empty and '全国-同比增长' in cpi_df.columns:
        try:
            # cpi_df 按时间降序，需反转
            cpi_hist_df = cpi_df.copy().iloc[::-1]
            cpi_history = []
            for _, row in cpi_hist_df.iterrows():
                raw_month = str(row.get('月份', ''))
                m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
                if m:
                    date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                    v = safe_float(row['全国-同比增长'])
                    if v is not None:
                        cpi_history.append({"date": date_label, "value": v})
            cpi_history = cpi_history[-48:] if len(cpi_history) > 48 else cpi_history
            history["cpi_yoy"] = cpi_history
            logger.info(f"  CPI 历史: {len(cpi_history)} 条")
        except Exception as e:
            logger.warning(f"  构建CPI历史失败: {e}")

    # --- PPI 历史 (从 ppi_df 的 "当月同比增长" 列) ---
    if not ppi_df.empty and '当月同比增长' in ppi_df.columns:
        try:
            # ppi_df 按时间降序，需反转
            ppi_hist_df = ppi_df.copy().iloc[::-1]
            ppi_history = []
            for _, row in ppi_hist_df.iterrows():
                raw_month = str(row.get('月份', ''))
                m = re.search(r'(\d{4})年(\d{1,2})月', raw_month)
                if m:
                    date_label = f"{m.group(1)}-{m.group(2).zfill(2)}"
                    v = safe_float(row['当月同比增长'])
                    if v is not None:
                        ppi_history.append({"date": date_label, "value": v})
            ppi_history = ppi_history[-48:] if len(ppi_history) > 48 else ppi_history
            history["ppi_yoy"] = ppi_history
            logger.info(f"  PPI 历史: {len(ppi_history)} 条")
        except Exception as e:
            logger.warning(f"  构建PPI历史失败: {e}")

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
            },
            "social_financing_stock": {
                "value": shrzgm_stock_val,
                "date": shrzgm_stock_date,
                "unit": "万亿元"
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
    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 获取中国宏观数据失败: {e}", exc_info=True)
        sys.exit(1)
