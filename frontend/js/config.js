/**
 * config.js - 全局配置
 * 颜色、API路径、图表默认选项等
 */

const CONFIG = {
  // 数据文件路径（相对于前端根目录）
  dataPath: 'data',

  // 数据文件名映射
  dataFiles: {
    cycle: 'cycle_position.json',
    usMacro: 'us_macro.json',
    cnMacro: 'cn_macro.json',
    valuation: 'asset_valuation.json',
    prices: 'asset_prices.json',
    summary: 'dashboard_summary.json',
  },

  // 颜色配置
  colors: {
    bull: '#22c55e',
    bear: '#ef4444',
    neutral: '#6b7280',
    mixed: '#f59e0b',
    pending: '#8b5cf6',
    // 图表配色
    chart: {
      bg: 'transparent',
      text: '#9ca3af',
      line: '#3b82f6',
      area: 'rgba(59,130,246,0.1)',
      grid: 'rgba(75,85,99,0.3)',
      // 多资产颜色
      assets: {
        sp500: '#3b82f6',
        nasdaq: '#6366f1',
        sse: '#ef4444',
        hs300: '#f97316',
        cn_10y_bond: '#22c55e',
        us_10y_bond: '#14b8a6',
        gold: '#eab308',
        copper: '#d97706',
        crude_oil: '#78716c',
        btc: '#a855f7',
        vix: '#ec4899',
      },
    },
  },

  // 信号到颜色的映射
  signalColor: {
    bullish: '#22c55e',
    bearish: '#ef4444',
    neutral: '#6b7280',
    neutral_bullish: '#22c55e',
    neutral_bearish: '#ef4444',
    mixed: '#f59e0b',
    pending: '#8b5cf6',
  },

  // 信号中文标签
  signalLabel: {
    bullish: '看多',
    bearish: '看空',
    neutral: '中性',
    neutral_bullish: '偏多',
    neutral_bearish: '偏空',
    mixed: '分化',
    pending: '待定',
  },

  // ECharts 通用配置
  echartsDefaults: {
    backgroundColor: 'transparent',
    textStyle: { color: '#9ca3af' },
    grid: {
      left: '5%', right: '5%', top: '12%', bottom: '15%',
      containLabel: true,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#374151',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
    },
  },

  // 时间范围选项（L4技术走势）
  timeRanges: ['1Y', '3Y', '5Y', '10Y', 'MAX'],
};
