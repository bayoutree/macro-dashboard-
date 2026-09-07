#!/usr/bin/env python3
"""
collect_microstructure.py - v3.4.6 第三步第一批真采集
====================================================
采集4个指标的真实数据：
  1. crowding_ratio     交易拥挤度（前5%个股成交额占比）
  2. break_net_rate     破净率（PB<1且PB>0个股占比）
  3. industry_concentration 行业成交集中度（HHI）
  4. concentration_trend    交易集中度趋势

数据源优先级：
  - 主源: akshare ak.stock_zh_a_spot_em()（东财push2）
  - 备用源: 新浪全A快照 API（云端实测可用 2026-09-07）
  - 行业板块: ak.stock_board_industry_name_em()（东财）→ 若不通则跳过置灰

写入目标: data/timing_scores.json 对应 indicators 节点
  - value/score 更新
  - history 追加真实周频点 {date, value}
  - 严禁 random/插值/静态快照冒充

用法:
  python collect_microstructure.py              # 正常运行
  python collect_microstructure.py --dry-run    # 仅打印不写入
"""

import sys
import os
import json
import math
import time
import argparse
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

# ============================================================
# 路径配置
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("collect_micro")


# ============================================================
# 工具函数
# ============================================================
def today_str():
    return date.today().isoformat()


def safe_float(val, default=None):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    except (ValueError, TypeError):
        return default


def load_timing_scores():
    fp = DATA_DIR / "timing_scores.json"
    if not fp.exists():
        logger.error("timing_scores.json 不存在！")
        return None
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def save_timing_scores(data):
    fp = DATA_DIR / "timing_scores.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    size_kb = fp.stat().st_size / 1024
    logger.info(f"💾 已保存 timing_scores.json ({size_kb:.1f} KB)")


def get_monday_of_week(d=None):
    """获取本周一日期（用于周频判断）"""
    if d is None:
        d = date.today()
    return d - timedelta(days=d.weekday())


def is_this_week_already_recorded(history_list):
    """检查 history 是否已有本周数据点"""
    if not history_list:
        return False
    this_monday = get_monday_of_week()
    for point in history_list:
        d_str = point.get("date", "")
        try:
            pd = datetime.strptime(d_str, "%Y-%m-%d").date()
            if get_monday_of_week(pd) == this_monday:
                return True
        except (ValueError, TypeError):
            continue
    return False


def append_or_update_history(history_list, new_point):
    """
    追加历史点，周频规则：
    - 若本周已有数据点，则更新
    - 否则追加新点
    """
    this_monday = get_monday_of_week()
    updated = False
    for i, point in enumerate(history_list):
        d_str = point.get("date", "")
        try:
            pd = datetime.strptime(d_str, "%Y-%m-%d").date()
            if get_monday_of_week(pd) == this_monday:
                history_list[i] = new_point
                updated = True
                logger.info(f"  📝 本周已有数据点，更新: {d_str} → {new_point['date']}")
                break
        except (ValueError, TypeError):
            continue
    if not updated:
        history_list.append(new_point)
        logger.info(f"  📝 追加新历史点: {new_point}")
    return history_list


# ============================================================
# 数据获取：东财 → 新浪降级
# ============================================================
def fetch_all_a_stocks_akshare():
    """
    主源：akshare stock_zh_a_spot_em()
    返回: list[dict] with keys: code, amount, pb, turnover_ratio, total_mv
    """
    logger.info("📡 [主源] 尝试 akshare stock_zh_a_spot_em()...")
    try:
        import akshare as ak
        import pandas as pd
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            raise ValueError("返回空数据")
        
        stocks = []
        for _, row in df.iterrows():
            amount = safe_float(row.get("成交额", 0))
            pb = safe_float(row.get("市净率"))
            if amount is None or amount <= 0:
                continue
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "amount": amount,
                "pb": pb,
            })
        logger.info(f"  ✅ akshare: {len(stocks)} 只有效股票")
        return stocks, "akshare"
    except Exception as e:
        logger.warning(f"  ❌ akshare 失败: {e}")
        return [], None


def fetch_all_a_stocks_sina():
    """
    备用源：新浪全A快照 API
    分页拉取全A股数据
    返回: list[dict] with keys: code, amount, pb, turnover_ratio
    """
    logger.info("📡 [备源] 使用新浪全A快照 API...")
    all_stocks = []
    page = 1
    base_url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    
    while True:
        url = f"{base_url}?page={page}&num=100&sort=amount&asc=0&node=hs_a"
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        except Exception as e:
            logger.error(f"  ❌ 新浪第{page}页失败: {e}")
            break
        
        if not data:
            break
        
        for item in data:
            amount = safe_float(item.get("amount", 0))
            pb = safe_float(item.get("pb"))
            if amount is None or amount <= 0:
                continue
            all_stocks.append({
                "code": str(item.get("code", "")),
                "name": str(item.get("name", "")),
                "amount": amount,
                "pb": pb,
            })
        
        if len(data) < 100:
            break
        page += 1
        
        # 避免请求过快
        if page % 10 == 0:
            time.sleep(0.3)
    
    logger.info(f"  ✅ 新浪: {len(all_stocks)} 只有效股票 (翻页{page}次)")
    return all_stocks, "sina" if all_stocks else None


def fetch_all_a_stocks():
    """
    获取全A股数据，东财优先→新浪降级
    返回: (stocks_list, source_name)
    """
    stocks, source = fetch_all_a_stocks_akshare()
    if stocks:
        return stocks, source
    logger.info("⚠️ 东财不通，降级到新浪...")
    stocks, source = fetch_all_a_stocks_sina()
    if stocks:
        return stocks, source
    logger.error("❌ 两个数据源均失败！")
    return [], None


def fetch_industry_boards_akshare():
    """
    获取行业板块成交额（东财）
    返回: list[dict] with keys: name, amount
    """
    logger.info("📡 [行业] 尝试 akshare stock_board_industry_name_em()...")
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            raise ValueError("返回空数据")
        
        boards = []
        for _, row in df.iterrows():
            name = str(row.get("板块名称", ""))
            amount = safe_float(row.get("总成交额", 0))
            if amount and amount > 0:
                boards.append({"name": name, "amount": amount})
        
        logger.info(f"  ✅ 行业板块: {len(boards)} 个")
        return boards, "akshare"
    except Exception as e:
        logger.warning(f"  ❌ 行业板块获取失败: {e}")
        return [], None


# ============================================================
# 指标计算
# ============================================================
def calc_crowding_ratio(stocks):
    """
    交易拥挤度：全A个股成交额降序，前5%个股成交额之和 ÷ 全市场总成交额
    返回: (value_pct, score)
      value_pct: 百分比数值（如 52.3 表示 52.3%）
      score: 0-100 评分
    """
    if not stocks:
        return None, None
    
    # 按成交额降序排列
    sorted_stocks = sorted(stocks, key=lambda x: x["amount"], reverse=True)
    total_amount = sum(s["amount"] for s in sorted_stocks)
    
    if total_amount <= 0:
        return None, None
    
    # 前5%个股
    top_n = max(1, int(len(sorted_stocks) * 0.05))
    top_amount = sum(s["amount"] for s in sorted_stocks[:top_n])
    
    crowding_pct = round(top_amount / total_amount * 100, 2)
    
    # 评分逻辑：
    # <35%: 25分（极度分散，市场冷淡）
    # 35-40%: 40分（正常偏低）
    # 40-48%: 55分（正常区间）
    # 48-55%: 70分（偏热）
    # 55-65%: 80分（过热）
    # >65%: 90分（极度集中）
    if crowding_pct < 35:
        score = 25
    elif crowding_pct < 40:
        score = 40
    elif crowding_pct < 48:
        score = 55
    elif crowding_pct < 55:
        score = 70
    elif crowding_pct < 65:
        score = 80
    else:
        score = 90
    
    logger.info(f"  📊 拥挤度: {crowding_pct}% (前{top_n}只占总成交额, score={score})")
    return crowding_pct, score


def calc_break_net_rate(stocks):
    """
    破净率：PB<1 且 PB>0 个股占比（排除负净资产异常）
    返回: (value_pct, score)
      value_pct: 百分比数值（如 7.7 表示 7.7%）
      score: 0-100 评分
    """
    if not stocks:
        return None, None
    
    # 过滤有效PB数据
    valid_pb = [s for s in stocks if s.get("pb") is not None]
    total_with_pb = len(valid_pb)
    
    if total_with_pb == 0:
        logger.warning("  ⚠️ 无有效PB数据")
        return None, None
    
    # PB<1 且 PB>0
    below_net = [s for s in valid_pb if 0 < s["pb"] < 1]
    rate_pct = round(len(below_net) / total_with_pb * 100, 2)
    
    # 评分逻辑：破净率高 → 底部信号 → 低分(安全)
    # >10%: 25分（极端底部信号）
    # 7-10%: 35分
    # 5-7%: 45分
    # 3-5%: 55分
    # <3%: 70分（市场高位）
    if rate_pct > 10:
        score = 25
    elif rate_pct > 7:
        score = 35
    elif rate_pct > 5:
        score = 45
    elif rate_pct > 3:
        score = 55
    else:
        score = 70
    
    logger.info(f"  📊 破净率: {rate_pct}% ({len(below_net)}/{total_with_pb}, score={score})")
    return rate_pct, score


def calc_industry_concentration(boards):
    """
    行业成交集中度：HHI = Σ(占比²)
    返回: (hhi_value, score, top3_names, top3_pcts)
    """
    if not boards:
        return None, None, [], []
    
    total_amount = sum(b["amount"] for b in boards)
    if total_amount <= 0:
        return None, None, [], []
    
    # 计算各行业占比和HHI
    hhi = 0
    board_shares = []
    for b in boards:
        share = b["amount"] / total_amount * 100  # 百分比
        board_shares.append((b["name"], share))
        hhi += (share / 100) ** 2  # HHI 用小数计算
    
    # 按占比降序排列
    board_shares.sort(key=lambda x: x[1], reverse=True)
    top3 = board_shares[:3]
    top3_names = [t[0] for t in top3]
    top3_pcts = [round(t[1], 2) for t in top3]
    top3_sum = sum(top3_pcts)
    
    # HHI 归一化到 0-1
    hhi_rounded = round(hhi, 6)
    
    # 评分逻辑：
    # HHI < 0.05: 30分（分散）
    # 0.05-0.08: 45分
    # 0.08-0.12: 55分
    # 0.12-0.18: 70分
    # >0.18: 80分（高度集中）
    if hhi_rounded < 0.05:
        score = 30
    elif hhi_rounded < 0.08:
        score = 45
    elif hhi_rounded < 0.12:
        score = 55
    elif hhi_rounded < 0.18:
        score = 70
    else:
        score = 80
    
    logger.info(f"  📊 HHI: {hhi_rounded} (score={score})")
    logger.info(f"  📊 前三行业: {top3_names} ({top3_pcts}%, 合计{top3_sum:.1f}%)")
    
    return hhi_rounded, score, top3_names, top3_pcts


def calc_concentration_trend(hhi_current, timing_data):
    """
    交易集中度趋势：对比上期HHI判断"加速集中/分散"
    首次运行建立基线。
    返回: (value_str, score)
    """
    # 获取历史HHI
    hist = (timing_data
            .get("dimensions", {})
            .get("micro_structure", {})
            .get("indicators", {})
            .get("industry_concentration", {})
            .get("history", []))
    
    if not hist or len(hist) == 0:
        return "基线建立中", 50
    
    # 取上一个有效HHI值
    prev_hhi = None
    for point in reversed(hist):
        v = point.get("value")
        if isinstance(v, (int, float)) and not math.isnan(v):
            prev_hhi = v
            break
    
    if prev_hhi is None:
        return "基线建立中", 50
    
    # 计算变化
    delta = hhi_current - prev_hhi
    delta_pct = delta / prev_hhi * 100 if prev_hhi != 0 else 0
    
    if delta_pct > 10:
        val_str = f"加速集中(HHI↑{delta_pct:.1f}%)"
        score = 75
    elif delta_pct > 3:
        val_str = f"温和集中(HHI↑{delta_pct:.1f}%)"
        score = 60
    elif delta_pct > -3:
        val_str = f"持平(HHI {delta_pct:+.1f}%)"
        score = 50
    elif delta_pct > -10:
        val_str = f"温和分散(HHI↓{abs(delta_pct):.1f}%)"
        score = 40
    else:
        val_str = f"加速分散(HHI↓{abs(delta_pct):.1f}%)"
        score = 30
    
    logger.info(f"  📊 集中度趋势: {val_str} (score={score})")
    return val_str, score


# ============================================================
# 主采集流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="微观结构+估值指标真采集")
    parser.add_argument("--dry-run", action="store_true", help="仅打印不写入")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("🔬 v3.4.6 第三步第一批真采集 - 微观结构+估值")
    logger.info(f"   日期: {today_str()}")
    logger.info("=" * 60)
    
    # ---- Step 1: 获取全A股数据 ----
    stocks, source = fetch_all_a_stocks()
    if not stocks:
        logger.error("❌ 全A股数据获取失败，无法采集任何指标")
        # 写入失败标记
        if not args.dry_run:
            ts = load_timing_scores()
            if ts:
                _mark_all_pending(ts)
                save_timing_scores(ts)
        return False
    
    logger.info(f"\n✅ 数据源: {source}, {len(stocks)} 只有效股票")
    
    # ---- Step 2: 计算拥挤度 ----
    logger.info("\n--- [1/4] 交易拥挤度 ---")
    crowding_val, crowding_score = calc_crowding_ratio(stocks)
    
    # ---- Step 3: 计算破净率 ----
    logger.info("\n--- [2/4] 破净率 ---")
    breaknet_val, breaknet_score = calc_break_net_rate(stocks)
    
    # ---- Step 4: 获取行业板块数据，计算集中度 ----
    logger.info("\n--- [3/4] 行业成交集中度 ---")
    boards, board_source = fetch_industry_boards_akshare()
    
    hhi_val, hhi_score = None, None
    top3_names, top3_pcts = [], []
    
    if boards:
        hhi_val, hhi_score, top3_names, top3_pcts = calc_industry_concentration(boards)
    else:
        logger.warning("  ⚠️ 行业板块数据不可用，行业集中度本次跳过置灰")
    
    # ---- Step 5: 计算集中度趋势 ----
    logger.info("\n--- [4/4] 交易集中度趋势 ---")
    ts = load_timing_scores()
    if not ts:
        logger.error("❌ timing_scores.json 加载失败")
        return False
    
    trend_val, trend_score = None, None
    if hhi_val is not None:
        trend_val, trend_score = calc_concentration_trend(hhi_val, ts)
    else:
        trend_val, trend_score = "数据待获取(依赖HHI)", None
        logger.info("  ⚠️ HHI不可用，趋势无法计算")
    
    # ---- 合理性检查 ----
    logger.info("\n--- 合理性检查 ---")
    if crowding_val is not None:
        if 20 <= crowding_val <= 80:
            logger.info(f"  ✅ 拥挤度 {crowding_val}% 在合理区间 (20-80%)")
        else:
            logger.warning(f"  ⚠️ 拥挤度 {crowding_val}% 偏离正常区间 (30-60%)")
    
    if breaknet_val is not None:
        if 0.5 <= breaknet_val <= 30:
            logger.info(f"  ✅ 破净率 {breaknet_val}% 在合理区间 (0.5-30%)")
        else:
            logger.warning(f"  ⚠️ 破净率 {breaknet_val}% 偏离正常区间 (3-15%)")
    
    if hhi_val is not None:
        if 0.01 <= hhi_val <= 0.5:
            logger.info(f"  ✅ HHI {hhi_val} 在合理区间 (0.01-0.5)")
        else:
            logger.warning(f"  ⚠️ HHI {hhi_val} 偏离正常区间")
    
    # ---- Step 6: 写入 timing_scores.json ----
    if args.dry_run:
        logger.info("\n🔸 DRY-RUN 模式，不写入文件")
        _print_results(crowding_val, crowding_score, breaknet_val, breaknet_score,
                       hhi_val, hhi_score, top3_names, top3_pcts, trend_val, trend_score)
        return True
    
    _update_timing_scores(ts, source,
                          crowding_val, crowding_score,
                          breaknet_val, breaknet_score,
                          hhi_val, hhi_score, top3_names, top3_pcts,
                          trend_val, trend_score)
    save_timing_scores(ts)
    
    # ---- 输出采集摘要 ----
    logger.info("\n" + "=" * 60)
    logger.info("📋 采集摘要")
    logger.info("=" * 60)
    logger.info(f"  数据源: {source}")
    logger.info(f"  股票数量: {len(stocks)}")
    logger.info(f"  crowding_ratio: {crowding_val}% (score={crowding_score})")
    logger.info(f"  break_net_rate: {breaknet_val}% (score={breaknet_score})")
    logger.info(f"  industry_concentration HHI: {hhi_val} (score={hhi_score})")
    logger.info(f"  concentration_trend: {trend_val} (score={trend_score})")
    logger.info(f"  行业板块源: {board_source or '不可用'}")
    logger.info("=" * 60)
    
    return True


def _mark_all_pending(ts):
    """所有指标置为'数据待获取'（灰灯）"""
    today = today_str()
    dims = ts.get("dimensions", {})
    
    # crowding_ratio
    ind = dims.get("micro_structure", {}).get("indicators", {}).get("crowding_ratio", {})
    ind["value"] = "数据待获取"
    ind["score"] = 50
    
    # break_net_rate
    ind2 = dims.get("valuation", {}).get("indicators", {}).get("break_net_rate", {})
    ind2["value"] = "数据待获取"
    ind2["score"] = 50
    
    # industry_concentration
    ind3 = dims.get("micro_structure", {}).get("indicators", {}).get("industry_concentration", {})
    ind3["value"] = "数据待获取"
    ind3["score"] = 50
    
    # concentration_trend
    ind4 = dims.get("micro_structure", {}).get("indicators", {}).get("concentration_trend", {})
    ind4["value"] = "数据待获取"
    ind4["score"] = 50
    
    logger.warning("⚠️ 所有指标已置灰（数据待获取）")


def _update_timing_scores(ts, source,
                          crowding_val, crowding_score,
                          breaknet_val, breaknet_score,
                          hhi_val, hhi_score, top3_names, top3_pcts,
                          trend_val, trend_score):
    """更新 timing_scores.json"""
    today = today_str()
    dims = ts.get("dimensions", {})
    
    # ---- 1. crowding_ratio (micro_structure) ----
    ms = dims.get("micro_structure", {})
    cr = ms.get("indicators", {}).get("crowding_ratio", {})
    if crowding_val is not None:
        cr["value"] = f"{crowding_val}%"
        cr["score"] = crowding_score
        cr["data_source"] = source
        cr["collect_time"] = datetime.now().isoformat(timespec="seconds")
        # 初始化 history（如果没有）
        if "history" not in cr or not isinstance(cr["history"], list):
            cr["history"] = []
        # 周频追加
        cr["history"] = append_or_update_history(cr["history"], {"date": today, "value": crowding_val})
        logger.info(f"  ✅ crowding_ratio 已更新: {crowding_val}%, history点数={len(cr['history'])}")
    else:
        cr["value"] = "数据待获取"
        cr["score"] = 50
        logger.warning("  ⚠️ crowding_ratio 数据不可用，置灰")
    
    # ---- 2. break_net_rate (valuation) ----
    val_dim = dims.get("valuation", {})
    bnr = val_dim.get("indicators", {}).get("break_net_rate", {})
    if breaknet_val is not None:
        bnr["value"] = round(breaknet_val, 1)
        bnr["score"] = breaknet_score
        bnr["data_source"] = source
        bnr["collect_time"] = datetime.now().isoformat(timespec="seconds")
        if "history" not in bnr or not isinstance(bnr["history"], list):
            bnr["history"] = []
        bnr["history"] = append_or_update_history(bnr["history"], {"date": today, "value": breaknet_val})
        logger.info(f"  ✅ break_net_rate 已更新: {breaknet_val}%, history点数={len(bnr['history'])}")
    else:
        bnr["value"] = "数据待获取"
        bnr["score"] = 50
        logger.warning("  ⚠️ break_net_rate 数据不可用，置灰")
    
    # ---- 3. industry_concentration (micro_structure) ----
    ic = ms.get("indicators", {}).get("industry_concentration", {})
    if hhi_val is not None:
        top3_str = "/".join(top3_names) if top3_names else "N/A"
        top3_pct_str = "/".join([f"{p:.1f}%" for p in top3_pcts]) if top3_pcts else "N/A"
        ic["value"] = f"HHI={hhi_val} (前三:{top3_str})"
        ic["hhi"] = hhi_val
        ic["top3_names"] = top3_names
        ic["top3_pcts"] = top3_pcts
        ic["score"] = hhi_score
        ic["data_source"] = "akshare_board"
        ic["collect_time"] = datetime.now().isoformat(timespec="seconds")
        if "history" not in ic or not isinstance(ic["history"], list):
            ic["history"] = []
        ic["history"] = append_or_update_history(ic["history"], {"date": today, "value": hhi_val})
        logger.info(f"  ✅ industry_concentration 已更新: HHI={hhi_val}, history点数={len(ic['history'])}")
    else:
        ic["value"] = "数据待获取"
        ic["score"] = 50
        # 不追加 history
        logger.warning("  ⚠️ industry_concentration 置灰（行业板块数据不可用）")
    
    # ---- 4. concentration_trend (micro_structure) ----
    ct = ms.get("indicators", {}).get("concentration_trend", {})
    if trend_val is not None:
        ct["value"] = trend_val
        ct["score"] = trend_score if trend_score is not None else 50
        ct["data_source"] = "derived_from_hhi"
        ct["collect_time"] = datetime.now().isoformat(timespec="seconds")
        # 集中度趋势不需要独立 history（它依赖 HHI history）
        logger.info(f"  ✅ concentration_trend 已更新: {trend_val}")
    else:
        ct["value"] = "数据待获取"
        ct["score"] = 50
        logger.warning("  ⚠️ concentration_trend 置灰")
    
    # ---- 更新 update_date ----
    ts["update_date"] = today


def _print_results(crowding_val, crowding_score, breaknet_val, breaknet_score,
                   hhi_val, hhi_score, top3_names, top3_pcts, trend_val, trend_score):
    """打印结果（dry-run模式）"""
    logger.info("\n" + "=" * 40)
    logger.info("📊 采集结果 (dry-run)")
    logger.info("=" * 40)
    logger.info(f"  拥挤度:     {crowding_val}% → score={crowding_score}")
    logger.info(f"  破净率:     {breaknet_val}% → score={breaknet_score}")
    logger.info(f"  HHI:        {hhi_val} → score={hhi_score}")
    if top3_names:
        logger.info(f"  前三行业:   {top3_names} ({top3_pcts}%)")
    logger.info(f"  集中度趋势: {trend_val} → score={trend_score}")


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
