#!/usr/bin/env python3
"""
生成看板综合结论 → data/dashboard_summary.json
基于规则引擎，读取各层 JSON 数据生成 L5 结论
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import logger, today_str, save_json, load_json


# ============================================================
# 规则引擎
# ============================================================
def analyze_cycle(cycle_data):
    """L1: 周期信号分析"""
    if not cycle_data:
        return {
            "conclusion": "周期数据未加载",
            "signal": "pending",
            "assets_impact": {"stock": "0", "bond": "0", "commodity": "0", "gold": "0"}
        }

    # 分析共振状态
    juglar = cycle_data.get("juglar", {})
    kitchin = cycle_data.get("kitchin", {})

    # 朱格拉周期方向
    juglar_bullish = False
    for region in ["us", "cn", "eu"]:
        phase = juglar.get(region, {}).get("phase", "")
        if "上升" in phase or "上行" in phase:
            juglar_bullish = True

    # 基钦周期(库存)
    inventory_bullish = False
    for region in ["us", "cn", "eu"]:
        phase = kitchin.get(region, {}).get("phase", "")
        if "补库" in phase:
            inventory_bullish = True

    # 综合判断
    if juglar_bullish and inventory_bullish:
        signal = "bullish"
        impact = {"stock": "+", "bond": "-", "commodity": "+", "gold": "0"}
    elif juglar_bullish:
        signal = "neutral_bullish"
        impact = {"stock": "+", "bond": "0", "commodity": "+", "gold": "0"}
    else:
        signal = "neutral"
        impact = {"stock": "0", "bond": "+", "commodity": "-", "gold": "+"}

    regime = cycle_data.get("resonance", {}).get("regime", "未知")
    conclusion = f"周期共振: {regime}"

    return {
        "conclusion": conclusion,
        "signal": signal,
        "assets_impact": impact
    }


def analyze_macro(us_data, cn_data):
    """L2: 宏观信号分析"""
    conclusions = []
    signal = "neutral"

    # 美国 PMI
    us_pmi = us_data.get("leading", {}).get("ism_pmi", {})
    pmi_val = us_pmi.get("value")
    if pmi_val is not None:
        if pmi_val > 55:
            conclusions.append(f"美国制造业强劲扩张(PMI={pmi_val})")
            signal = "bullish"
        elif pmi_val > 50:
            conclusions.append(f"美国制造业温和扩张(PMI={pmi_val})")
            signal = "neutral_bullish"
        else:
            conclusions.append(f"美国制造业收缩(PMI={pmi_val})")
            signal = "bearish"

    # 美国 GDP
    gdp = us_data.get("coincident", {}).get("gdp_growth", {})
    gdp_val = gdp.get("value")
    if gdp_val is not None:
        conclusions.append(f"美国GDP增速{gdp_val}%")

    # 通胀方向
    cpi = us_data.get("lagging", {}).get("cpi_yoy", {})
    cpi_val = cpi.get("value")
    if cpi_val is not None:
        if cpi_val > 3:
            conclusions.append(f"通胀偏高(CPI={cpi_val}%)")
        elif cpi_val > 2:
            conclusions.append(f"通胀温和(CPI={cpi_val}%)")
        else:
            conclusions.append(f"通胀回落(CPI={cpi_val}%)")

    # 中国
    cn_pmi = cn_data.get("leading", {}).get("pmi", {})
    cn_pmi_val = cn_pmi.get("value")
    if cn_pmi_val is not None:
        if cn_pmi_val > 50:
            conclusions.append("中国制造业扩张")
        else:
            conclusions.append("中国制造业收缩")

    cn_gdp = cn_data.get("coincident", {}).get("gdp_growth", {})
    cn_gdp_val = cn_gdp.get("value")
    if cn_gdp_val is not None:
        conclusions.append(f"中国GDP增速{cn_gdp_val}%")

    conclusion = "；".join(conclusions) if conclusions else "宏观数据待更新"

    return {
        "conclusion": conclusion,
        "signal": signal
    }


def analyze_valuation(val_data):
    """L3: 估值信号分析"""
    conclusions = []
    overweight = []
    underweight = []

    # 美股估值
    us_stock = val_data.get("us_stock", {})
    shiller = us_stock.get("shiller_pe", {})
    shiller_val = shiller.get("value")
    if shiller_val:
        if shiller_val > 35:
            conclusions.append(f"美股估值极高(Shiller PE={shiller_val})")
            underweight.append("美股")
        elif shiller_val > 25:
            conclusions.append(f"美股估值偏高(Shiller PE={shiller_val})")
            underweight.append("美股")
        elif shiller_val < 15:
            conclusions.append(f"美股估值偏低(Shiller PE={shiller_val})")
            overweight.append("美股")

    # 巴菲特指标
    buffett = us_stock.get("buffett_ratio", {})
    buffett_val = buffett.get("value")
    if buffett_val:
        if buffett_val > 150:
            conclusions.append(f"巴菲特指标偏高({buffett_val:.0f}%)")

    # ERP
    erp = us_stock.get("erp", {})
    erp_val = erp.get("value")
    if erp_val is not None:
        if erp_val < 1:
            conclusions.append(f"股权风险溢价极低({erp_val:.1f}%)，股票吸引力弱")
        elif erp_val < 3:
            conclusions.append(f"股权风险溢价适中({erp_val:.1f}%)")

    # A股估值
    cn_stock = val_data.get("cn_stock", {})
    hs300_pe = cn_stock.get("hs300_pe", {})
    hs300_pe_val = hs300_pe.get("value")
    hs300_pe_pct = hs300_pe.get("percentile")
    if hs300_pe_val:
        if hs300_pe_pct and hs300_pe_pct < 30:
            conclusions.append(f"A股低估(沪深300 PE={hs300_pe_val}, 分位{hs300_pe_pct:.0f}%)")
            overweight.append("A股")
        elif hs300_pe_pct and hs300_pe_pct > 80:
            conclusions.append(f"A股高估(沪深300 PE={hs300_pe_val}, 分位{hs300_pe_pct:.0f}%)")
            underweight.append("A股")
        else:
            conclusions.append(f"A股估值中性(沪深300 PE={hs300_pe_val})")

    # 黄金
    gold = val_data.get("gold", {})
    gold_price = gold.get("price_usd", {}).get("value")
    real_rate = val_data.get("us_bond", {}).get("real_rate_10y", {}).get("value")
    if gold_price:
        conclusions.append(f"黄金 ${gold_price:,.0f}")

    # 综合信号
    if len(overweight) > len(underweight):
        signal = "bullish"
    elif len(underweight) > len(overweight):
        signal = "bearish"
    else:
        signal = "mixed"

    conclusion = "；".join(conclusions) if conclusions else "估值数据待更新"

    return {
        "conclusion": conclusion,
        "signal": signal
    }


def analyze_technical(price_data):
    """L4: 技术信号分析"""
    conclusions = []
    signals = {}

    prices = price_data.get("prices", {})

    for name, key in [("S&P500", "sp500"), ("黄金", "gold"), ("原油", "crude_oil")]:
        data = prices.get(key, {})
        latest = data.get("latest")
        ma200 = data.get("ma200")
        drawdown = data.get("drawdown_from_high")

        if latest and ma200:
            if latest > ma200:
                conclusions.append(f"{name}在200日均线上方(多头)")
            else:
                conclusions.append(f"{name}在200日均线下方(空头)")

        if drawdown is not None:
            if drawdown < -10:
                conclusions.append(f"{name}距高点回撤{drawdown:.1f}%")
            elif drawdown > -3:
                conclusions.append(f"{name}接近高点(回撤{drawdown:.1f}%)")

    # VIX
    vix = price_data.get("volatility", {}).get("vix", {}).get("latest")
    if vix:
        if vix > 30:
            conclusions.append(f"VIX高位({vix:.1f})，市场恐慌")
        elif vix > 20:
            conclusions.append(f"VIX偏高({vix:.1f})，市场谨慎")
        else:
            conclusions.append(f"VIX低位({vix:.1f})，市场平静")

    if not conclusions:
        return {"conclusion": "技术数据待更新", "signal": "pending"}

    # 综合
    bullish_count = sum(1 for c in conclusions if "多头" in c or "低位" in c or "接近高点" in c)
    bearish_count = sum(1 for c in conclusions if "空头" in c or "恐慌" in c or "回撤" in c)

    if bullish_count > bearish_count:
        signal = "bullish"
    elif bearish_count > bullish_count:
        signal = "bearish"
    else:
        signal = "neutral"

    return {
        "conclusion": "；".join(conclusions),
        "signal": signal
    }


def generate_allocation(layers):
    """生成配置建议"""
    overweight = []
    market_weight = []
    underweight = []

    l1 = layers.get("L1_cycle", {})
    l3 = layers.get("L3_valuation", {})

    # 基于估值信号
    impact = l1.get("assets_impact", {})

    # 铜: 结构性紧缺 + 新能源需求
    overweight.append("铜")
    # A股: 低估值
    if "A股" in l3.get("conclusion", "").lower() or "低估" in l3.get("conclusion", ""):
        overweight.append("A股")
    # 黄金: 避险+央行购金
    market_weight.append("黄金")
    # 中国国债
    market_weight.append("中国国债")
    # 科技成长
    market_weight.append("科技成长股")

    # 美股: 高估值
    underweight.append("美股")
    # 美债: 利率高位但可能下行
    if impact.get("bond") == "+":
        underweight.append("美债")
    else:
        market_weight.append("美债")
    # 原油
    underweight.append("原油")
    # 比特币
    underweight.append("比特币")

    return {
        "overweight": overweight,
        "market_weight": market_weight,
        "underweight": underweight
    }


def generate_risks(us_data, cn_data):
    """生成关键风险列表"""
    risks = []

    # 通胀风险
    cpi = us_data.get("lagging", {}).get("cpi_yoy", {}).get("value")
    if cpi and cpi > 3:
        risks.append("通胀粘性超预期→美联储推迟降息")
    elif cpi and cpi > 2:
        risks.append("通胀回落缓慢→实际利率维持高位")

    # 地缘政治
    risks.append("中东冲突升级→能源价格冲击→滞胀风险")

    # 估值风险
    risks.append("AI投资回报不及预期→科技股估值回调")

    # 中国经济
    cn_pmi = cn_data.get("leading", {}).get("pmi", {}).get("value")
    if cn_pmi and cn_pmi < 50:
        risks.append("中国经济复苏乏力→大宗商品需求疲软")

    # 流动性
    risks.append("美联储缩表超预期→流动性收紧")

    return risks[:5]  # 最多5条


# ============================================================
# 主逻辑
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("开始生成看板综合结论")
    logger.info("=" * 60)

    # 加载所有数据
    cycle_data = load_json("cycle_position.json")
    us_data = load_json("us_macro.json")
    cn_data = load_json("cn_macro.json")
    val_data = load_json("asset_valuation.json")
    price_data = load_json("asset_prices.json")

    # 生成各层结论
    logger.info("\n[1/4] L1 周期信号...")
    l1 = analyze_cycle(cycle_data)
    logger.info(f"  → {l1['conclusion']}")

    logger.info("\n[2/4] L2 宏观信号...")
    l2 = analyze_macro(us_data, cn_data)
    logger.info(f"  → {l2['conclusion']}")

    logger.info("\n[3/4] L3 估值信号...")
    l3 = analyze_valuation(val_data)
    logger.info(f"  → {l3['conclusion']}")

    logger.info("\n[4/4] L4 技术信号...")
    l4 = analyze_technical(price_data)
    logger.info(f"  → {l4['conclusion']}")

    # 配置建议
    layers = {
        "L1_cycle": l1,
        "L2_macro": l2,
        "L3_valuation": l3,
        "L4_technical": l4,
    }
    allocation = generate_allocation(layers)
    key_risks = generate_risks(us_data, cn_data)

    # ============================================================
    # 输出
    # ============================================================
    output = {
        "update_time": today_str(),
        "layers": layers,
        "allocation": allocation,
        "key_risks": key_risks
    }

    save_json(output, "dashboard_summary.json")
    logger.info("\n✅ dashboard_summary.json 生成完成!")
    return output


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ 生成综合结论失败: {e}", exc_info=True)
        sys.exit(1)
