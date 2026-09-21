"""
normalize.py — 指标标准化计算

职责：
  给定一个时间序列（list of (date_str, value)），计算：
    - value        最新值
    - unit         单位（调用方传入）
    - change       环比变化（最新值 - 上一期值）
    - pct_5y       最新值在「近 5 年」窗口内的历史分位数 (0-100)
    - pct_10y      最新值在「近 10 年」窗口内的历史分位数 (0-100)
    - date         最新数据日期
    - history      近期历史序列 [{date, value}]

纯 Python 实现（仅依赖标准库 + 可选 pandas 用于日期解析），
保证在无 akshare/fredapi 的环境下也能运行，方便做单元测试。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

try:
    import pandas as pd
    _HAS_PANDAS = True
except Exception:  # pragma: no cover
    _HAS_PANDAS = False


# ----------------------------------------------------------------------------
# 日期解析
# ----------------------------------------------------------------------------
def _to_ts(date_str):
    """将各种格式的日期字符串解析为可比较的时间戳。失败返回 None。"""
    if date_str is None:
        return None
    if _HAS_PANDAS:
        try:
            return pd.Timestamp(str(date_str))
        except Exception:
            pass
    s = str(date_str).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m", "%Y%m%d", "%Y%m", "%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    # 退化：尝试取前 4 位年份
    try:
        return datetime(int(s[:4]), 1, 1)
    except Exception:
        return None


def _normalize_date(date_str):
    """把日期统一成 YYYY-MM 或 YYYY-MM-DD。
    若原始字符串只有「年-月」（无日），保留 YYYY-MM，以匹配展示规范。"""
    ts = _to_ts(date_str)
    if ts is None:
        return str(date_str)
    s = str(ts)
    if re.match(r"^\d{4}-\d{1,2}$", str(date_str).strip()):
        return str(date_str).strip()[:7]
    if len(s) >= 10:
        return s[:10]
    return s


# ----------------------------------------------------------------------------
# 分位数
# ----------------------------------------------------------------------------
def quantile_rank(value, arr):
    """
    经验分位数：数组中 <= value 的元素占比 * 100，结果取整 [0,100]。
    空数组或只有一个元素时返回 None（无法判定位置）。
    """
    arr = [float(x) for x in arr if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not arr:
        return None
    if len(arr) == 1:
        return 50
    le = sum(1 for x in arr if x <= value)
    return int(round(le / len(arr) * 100))


# ----------------------------------------------------------------------------
# 主函数
# ----------------------------------------------------------------------------
def make_indicator(series, unit="", hist_limit=120, change_round=4):
    """
    构建单个指标的标准字典。

    :param series: list of (date_str, value)，可乱序、可含 None
    :param unit:   单位字符串，如 "%"、"bps"、"x"、""
    :param hist_limit: history 保留最近多少期（默认 120）
    :return: dict 或 None（无有效数据）
    """
    if not series:
        return None

    clean = []
    for d, v in series:
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(fv):
            continue
        clean.append((_normalize_date(d), fv))

    if not clean:
        return None

    # 按时间排序（统一用时间戳，避免 Timestamp vs str 混排报错）
    def _sort_key(x):
        ts = _to_ts(x[0])
        return ts if ts is not None else datetime.min
    clean.sort(key=_sort_key)

    dates = [d for d, _ in clean]
    values = [v for _, v in clean]

    latest_date = dates[-1]
    latest_val = values[-1]

    change = None
    if len(values) >= 2:
        change = round(latest_val - values[-2], change_round)

    latest_ts = _to_ts(latest_date)

    def _window(years):
        if latest_ts is None:
            return values
        try:
            cutoff = latest_ts - timedelta(days=365 * years)
        except Exception:
            # pandas Timestamp 路径
            cutoff = latest_ts - __import__("pandas").DateOffset(years=years)
        return [v for d, v in clean if (_to_ts(d) or datetime.min) >= cutoff]

    pct_5y = quantile_rank(latest_val, _window(5))
    pct_10y = quantile_rank(latest_val, _window(10))

    history = [{"date": d, "value": round(v, change_round)} for d, v in clean[-hist_limit:]]

    return {
        "value": round(latest_val, change_round),
        "unit": unit,
        "change": None if change is None else round(change, change_round),
        "pct_5y": pct_5y,
        "pct_10y": pct_10y,
        "date": latest_date,
        "history": history,
    }


def maybe(ind):
    """便捷判空：None 或 value 为 None 时返回 None。"""
    if not ind or ind.get("value") is None:
        return None
    return ind


if __name__ == "__main__":
    # 简单自检
    import json
    demo = [
        (f"2026-{m:02d}", 100 + i + (i % 3)) for i, m in enumerate(list(range(1, 13)) * 3)
    ]
    out = make_indicator(demo, unit="")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("quantile(105, [100,105,110]) =", quantile_rank(105, [100, 105, 110]))
