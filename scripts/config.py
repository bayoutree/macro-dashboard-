"""
宏观经济指标看板 - 全局配置
"""
import os
import json
import logging
from datetime import datetime, date
from pathlib import Path

# ============================================================
# 路径配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# API 密钥
# ============================================================
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("macro-dashboard")


# ============================================================
# 通用工具函数
# ============================================================
def today_str() -> str:
    """返回今天日期字符串 YYYY-MM-DD"""
    return date.today().isoformat()


def now_iso() -> str:
    """返回当前 ISO 格式时间戳"""
    return datetime.now().isoformat(timespec="seconds")


def safe_float(val, default=None):
    """安全地将值转为 float，处理 NaN/None"""
    if val is None:
        return default
    try:
        import math
        f = float(val)
        return default if math.isnan(f) else round(f, 4)
    except (ValueError, TypeError):
        return default


def save_json(data: dict, filename: str):
    """将字典保存为格式化的 JSON 文件"""
    filepath = DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"已保存: {filepath}  ({filepath.stat().st_size / 1024:.1f} KB)")


def load_json(filename: str) -> dict:
    """读取 JSON 文件"""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        logger.warning(f"文件不存在: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def ts_to_date_str(ts) -> str:
    """将 pandas Timestamp 或 datetime 转为 YYYY-MM-DD"""
    if ts is None:
        return None
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


def quarter_str(dt) -> str:
    """将日期转为季度字符串，如 2026Q1"""
    try:
        dt = pd.Timestamp(dt)
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{q}"
    except Exception:
        return str(dt)[:7]


def series_to_history(s, freq="monthly", max_points=48):
    """
    将 pandas Series (index=日期, value=数值) 转为 history 数组。
    自动处理 NaN，保留最近 max_points 个点。
    """
    import pandas as pd
    s = s.dropna()
    if s.empty:
        return []
    s = s.tail(max_points)
    result = []
    for dt, val in s.items():
        if freq == "quarterly":
            date_label = quarter_str(dt)
        elif freq == "daily":
            date_label = pd.Timestamp(dt).strftime("%Y-%m-%d")
        else:
            date_label = pd.Timestamp(dt).strftime("%Y-%m")
        result.append({"date": date_label, "value": safe_float(val)})
    return result


# 导入 pandas 供工具函数使用
import pandas as pd
