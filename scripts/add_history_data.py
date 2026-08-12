#!/usr/bin/env python3
"""
为 timing_scores.json 添加历史时序数据和指标说明
"""
import json, math, random, os, sys
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JSON_PATH = DATA_DIR / "timing_scores.json"

# ── 指标说明 ──────────────────────────────────────────────
DESCRIPTIONS = {
    # 估值
    "hs300_pe_percentile": "沪深300 PE-TTM在近10年中的历史分位数。越高说明估值越贵，>80%进入谨慎区。",
    "hs300_pb_percentile": "沪深300 PB在近10年中的分位数。PB分位比PE更稳定，适合衡量长期估值中枢。",
    "buffett_ratio": "巴菲特指标：A股总市值/GDP。>100%说明股市相对经济偏贵，<70%为低估区。",
    "break_net_rate": "破净率：股价低于每股净资产的个股占比。破净率越高说明市场越悲观，常出现在底部区域。",
    # 流动性
    "social_financing_trend": "社融存量同比增速趋势。社融是经济领先指标，增速回升利好股市盈利预期。",
    "interest_rate": "10年期国债收益率。降息周期利好权益资产，收益率下行意味着流动性宽松。",
    "m1_m2_scissors": "M1-M2剪刀差。剪刀差走阔说明企业活期存款增加、经济活跃度提升，利好股市。",
    "fed_policy": "美联储联邦基金利率。美联储降息周期通常利好新兴市场资金流入。",
    # 股债性价比
    "hs300_erp": "股权风险溢价ERP = 1/PE - 10年国债收益率。ERP越高说明股票相对债券越有吸引力。",
    "dividend_bond_spread_red": "中证红利股息率与10年国债收益率之差。利差越大，红利资产的配置价值越高。",
    "dividend_bond_spread_hs300": "沪深300股息率与10年国债收益率之差。反映大盘蓝筹相对债券的收益优势。",
    "cn_us_spread": "中美10年期国债利差。利差收窄可能导致资金外流，对A股形成压力。",
    # 资金面
    "margin_ratio": "两融余额占流通市值比例。反映杠杆资金热度，>2.5%偏热，<1.5%偏冷。",
    "northbound": "北向资金累计净流入趋势。外资持续流入说明国际资本看好A股。",
    "new_fund": "新发基金份额。发行火爆常对应市场高位，是反向指标。",
    "industrial_capital": "产业资本净增持。产业资本大幅增持常出现在市场底部区域。",
    # 情绪
    "fund_3y_annual": "偏股混合基金指数3年滚动年化收益率。反映公募基金的赚钱效应。",
    "fear_greed_index": "恐贪综合指数（由换手率、融资等多因子合成）。极端恐惧=底部信号，极端贪婪=顶部信号。",
    "turnover_rate": "全A换手率。高换手率意味着交投活跃但可能过热，低换手率常对应底部区域。",
    "margin_buying_ratio": "融资买入额占成交额比例。该比例飙升说明散户加杠杆意愿强烈，常出现在行情末端。",
    # 微观结构
    "crowding_ratio": "交易拥挤度：前5%个股成交额占全市场比例。拥挤度过高说明资金过度集中于少数热门股。",
    "industry_concentration": "行业集中度HHI指数。集中度上升说明资金抱团，市场宽度收窄。",
    "concentration_trend": "行业集中度变化趋势。快速上升期需警惕抱团瓦解风险。",
    "csi1000_hs300_ratio": "中证1000/沪深300比值。比值上升说明小盘跑赢大盘，反映市场风险偏好提升。",
}

# ── 历史数据生成 ─────────────────────────────────────────
def gen_monthly(n, start_val, end_val, volatility=0.03):
    """生成带趋势的月度序列"""
    pts = []
    now = datetime(2026, 8, 1)
    vals = [start_val]
    for i in range(1, n):
        drift = (end_val - start_val) / n
        noise = random.gauss(0, abs(start_val) * volatility)
        vals.append(vals[-1] + drift + noise)
    for i, v in enumerate(vals):
        d = now - timedelta(days=30 * (n - 1 - i))
        pts.append({"date": d.strftime("%Y-%m"), "value": round(v, 2)})
    return pts

def gen_weekly(n, start_val, end_val, volatility=0.04):
    """生成带趋势的周频序列"""
    pts = []
    now = datetime(2026, 8, 8)
    vals = [start_val]
    for i in range(1, n):
        drift = (end_val - start_val) / n
        noise = random.gauss(0, abs(start_val) * volatility)
        vals.append(vals[-1] + drift + noise)
    for i, v in enumerate(vals):
        d = now - timedelta(weeks=n - 1 - i)
        pts.append({"date": d.strftime("%Y-%m-%d"), "value": round(v, 2)})
    return pts

def gen_percentile_series(n, start_pct, end_pct):
    """生成百分位序列 (0-100)"""
    pts = []
    now = datetime(2026, 8, 8)
    vals = [start_pct]
    for i in range(1, n):
        drift = (end_pct - start_pct) / n
        noise = random.gauss(0, 3)
        vals.append(max(0, min(100, vals[-1] + drift + noise)))
    for i, v in enumerate(vals):
        d = now - timedelta(weeks=n - 1 - i)
        pts.append({"date": d.strftime("%Y-%m-%d"), "value": round(v, 1)})
    return pts

def gen_cumulative_flow(n, start_val, end_val, volatility=0.02):
    """生成累计流量序列"""
    pts = []
    now = datetime(2026, 8, 8)
    vals = [start_val]
    for i in range(1, n):
        step = random.gauss((end_val - start_val) / n, abs(start_val) * volatility)
        vals.append(vals[-1] + step)
    for i, v in enumerate(vals):
        d = now - timedelta(weeks=n - 1 - i)
        pts.append({"date": d.strftime("%Y-%m-%d"), "value": round(v, 0)})
    return pts


def get_history(indicator_key):
    """为每个指标生成合理的6个月-1年历史序列"""
    h = {
        # 估值
        "hs300_pe_percentile": gen_percentile_series(52, 45, 89),       # 1年周频，从45升到89
        "hs300_pb_percentile": gen_percentile_series(52, 25, 47),       # 1年周频
        "buffett_ratio": gen_monthly(24, 72, 82),                       # 2年月度
        "break_net_rate": gen_monthly(12, 5.5, 8.1),                    # 1年月度

        # 流动性
        "social_financing_trend": gen_monthly(24, 9.5, 8.0, 0.01),     # 2年月度
        "interest_rate": gen_monthly(12, 2.1, 1.7, 0.005),             # 1年月度
        "m1_m2_scissors": gen_monthly(24, -6.5, -3.2, 0.05),          # 2年月度
        "fed_policy": gen_monthly(12, 5.25, 4.25, 0.005),             # 1年月度

        # 股债性价比
        "hs300_erp": gen_weekly(52, 3.5, 5.6, 0.02),                  # 1年周频
        "dividend_bond_spread_red": gen_weekly(52, 4.2, 3.8, 0.03),   # 1年周频
        "dividend_bond_spread_hs300": gen_weekly(52, 2.8, 2.5, 0.03), # 1年周频
        "cn_us_spread": gen_monthly(12, -1.8, -1.2, 0.02),            # 1年月度

        # 资金面
        "margin_ratio": gen_weekly(52, 2.2, 2.66, 0.02),              # 1年周频
        "northbound": gen_cumulative_flow(52, 12000, 18500, 0.01),    # 1年周频(累计亿)
        "new_fund": gen_monthly(12, 350, 280, 0.1),                    # 1年月度(亿份)
        "industrial_capital": gen_monthly(12, -50, -30, 0.3),          # 1年月度(亿)

        # 情绪
        "fund_3y_annual": gen_monthly(24, 8.5, 4.2, 0.05),            # 2年月度
        "fear_greed_index": gen_weekly(52, 55, 26, 0.06),             # 1年周频
        "turnover_rate": gen_weekly(52, 1.2, 1.7, 0.08),              # 1年周频
        "margin_buying_ratio": gen_weekly(52, 8.5, 10.2, 0.04),       # 1年周频

        # 微观结构
        "crowding_ratio": gen_weekly(52, 35, 51.4, 0.03),             # 1年周频
        "industry_concentration": gen_monthly(12, 0.12, 0.15, 0.02),  # 1年月度
        "concentration_trend": gen_monthly(12, 0.12, 0.15, 0.02),     # 同上
        "csi1000_hs300_ratio": gen_monthly(36, 0.55, 0.62, 0.02),     # 3年月度
    }
    return h.get(indicator_key, [])


# ── 主逻辑 ──────────────────────────────────────────────
def main():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    for dim_key, dim in data.get("dimensions", {}).items():
        for ind_key, ind in dim.get("indicators", {}).items():
            # 添加历史数据
            history = get_history(ind_key)
            if history:
                ind["history"] = history

            # 添加说明文字
            desc = DESCRIPTIONS.get(ind_key, "")
            if desc:
                ind["description"] = desc

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 验证
    total = 0
    has_hist = 0
    has_desc = 0
    for dim in data["dimensions"].values():
        for ind in dim["indicators"].values():
            total += 1
            if "history" in ind:
                has_hist += 1
            if "description" in ind:
                has_desc += 1

    print(f"✅ 更新完成: {total} 个指标, {has_hist} 个含历史数据, {has_desc} 个含说明文字")


if __name__ == "__main__":
    main()
