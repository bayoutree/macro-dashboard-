/**
 * 宏观经济指标看板 - Main Application
 * Redesigned dashboard with professional card-based layout
 */

// ========== Configuration ==========
const CONFIG = {
  dataDir: 'data',
  chartColorUp: '#10b981',
  chartColorDown: '#ef4444',
  chartColorNeutral: '#3b82f6',
  chartGradientOpacity: 0.15,
  defaultTimeRange: '1y',
};

// ========== Utility Functions ==========
const Utils = {
  formatValue(val, unit = '') {
    if (val === null || val === undefined) return '--';
    if (typeof val === 'string') return val;
    if (Number.isInteger(val)) return val.toLocaleString();
    if (Math.abs(val) >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 1 });
    return val.toFixed(2);
  },

  getTrendClass(change) {
    if (change === null || change === undefined) return 'change-neutral';
    return change > 0 ? 'change-up' : change < 0 ? 'change-down' : 'change-neutral';
  },

  getTrendArrow(change) {
    if (change === null || change === undefined) return '→';
    return change > 0 ? '↑' : change < 0 ? '↓' : '→';
  },

  getImpactArrow(direction) {
    if (direction === '+') return '↑';
    if (direction === '-') return '↓';
    return '→';
  },

  getImpactClass(direction) {
    if (direction === '+') return 'impact-positive';
    if (direction === '-') return 'impact-negative';
    return 'impact-neutral';
  },

  filterHistoryByRange(history, range) {
    if (!history || history.length === 0) return [];
    if (range === 'all') return history;

    const now = new Date();
    const years = range === '1y' ? 1 : range === '2y' ? 2 : 5;
    const cutoff = new Date(now.getFullYear() - years, now.getMonth(), now.getDate());

    return history.filter(item => {
      const d = this.parseDate(item.date);
      return d && d >= cutoff;
    });
  },

  parseDate(dateStr) {
    if (!dateStr) return null;
    // Handle quarterly format like "2022Q1"
    if (/^\d{4}Q\d$/.test(dateStr)) {
      const [year, q] = dateStr.split('Q');
      return new Date(parseInt(year), (parseInt(q) - 1) * 3, 1);
    }
    // Handle Chinese format like "2026年第1季度"
    if (dateStr.includes('季度')) {
      const match = dateStr.match(/(\d{4}).*?(\d)季度/);
      if (match) return new Date(parseInt(match[1]), (parseInt(match[2]) - 1) * 3, 1);
    }
    // Handle YYYY-MM
    if (/^\d{4}-\d{2}$/.test(dateStr)) {
      const [y, m] = dateStr.split('-');
      return new Date(parseInt(y), parseInt(m) - 1, 1);
    }
    // Handle YYYY-MM-DD
    const d = new Date(dateStr);
    return isNaN(d.getTime()) ? null : d;
  },

  computeChange(history) {
    if (!history || history.length < 2) return null;
    const latest = history[history.length - 1].value;
    const prev = history[history.length - 2].value;
    if (latest === null || prev === null) return null;
    return latest - prev;
  },

  signalToText(signal) {
    const map = {
      'bullish': '看多',
      'bearish': '看空',
      'neutral': '中性',
      'neutral_bullish': '中性偏多',
      'neutral_bearish': '中性偏空',
      'mixed': '分化',
      'pending': '待更新',
    };
    return map[signal] || signal;
  },

  signalToClass(signal) {
    const map = {
      'bullish': 'signal-bullish',
      'bearish': 'signal-bearish',
      'neutral': 'signal-neutral',
      'neutral_bullish': 'signal-neutral-bullish',
      'neutral_bearish': 'signal-neutral-bearish',
      'mixed': 'signal-neutral',
      'pending': 'signal-neutral',
    };
    return map[signal] || 'signal-neutral';
  },

  getPercentileClass(pctl) {
    if (pctl === null || pctl === undefined) return '';
    if (pctl >= 80) return 'pctl-high';
    if (pctl >= 40) return 'pctl-mid';
    return 'pctl-low';
  },

  escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  },

  /**
   * 生成数据时间戳 banner HTML
   * @param {string|string[]} updateTimes - 单个时间字符串或数组（多个数据源）
   * @param {string} label - 可选的标签前缀，如 "中国宏观"
   * @returns {string} HTML string
   */
  renderTimestampBanner(updateTimes, label = '') {
    if (!updateTimes) return '';
    const times = Array.isArray(updateTimes) ? updateTimes.filter(Boolean) : [updateTimes].filter(Boolean);
    if (!times.length) return '';

    // 计算距今天数（取最早的时间来判断）
    const now = new Date();
    let maxDaysAgo = 0;
    times.forEach(t => {
      const d = new Date(t);
      if (!isNaN(d.getTime())) {
        const days = Math.floor((now - d) / (1000 * 60 * 60 * 24));
        if (days > maxDaysAgo) maxDaysAgo = days;
      }
    });

    let colorClass = '';
    let warningIcon = '';
    if (maxDaysAgo > 14) {
      colorClass = 'ts-banner-stale';
      warningIcon = '⚠️';
    } else if (maxDaysAgo > 7) {
      colorClass = 'ts-banner-warn';
      warningIcon = '⚡';
    }

    const timeStr = times.join(' / ');
    const prefix = label ? `${label} · ` : '';

    return `<div class="data-timestamp-banner ${colorClass}">
      <span class="ts-banner-icon">🕐</span>
      <span class="ts-banner-text">${prefix}数据截至: ${timeStr}</span>
      ${warningIcon ? `<span class="ts-banner-warn">${warningIcon} ${maxDaysAgo}天未更新</span>` : ''}
    </div>`;
  },
};

// ========== Chart Manager ==========
const ChartManager = {
  charts: new Map(),

  createMiniChart(containerId, data, options = {}) {
    const container = document.getElementById(containerId);
    if (!container || !data || data.length === 0) return;

    // Dispose existing chart
    if (this.charts.has(containerId)) {
      this.charts.get(containerId).dispose();
    }

    const chart = echarts.init(container);
    this.charts.set(containerId, chart);

    const dates = data.map(d => d.date);
    const values = data.map(d => d.value);
    const color = options.color || CONFIG.chartColorNeutral;
    const showArea = options.showArea !== false;

    const option = {
      animation: true,
      animationDuration: 600,
      grid: {
        top: 8,
        right: 8,
        bottom: 20,
        left: 8,
        containLabel: false,
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: {
          color: '#e2e8f0',
          fontSize: 11,
          fontFamily: 'JetBrains Mono',
        },
        formatter: (params) => {
          const p = params[0];
          return `<div style="font-size:10px;color:#64748b">${p.name}</div><div style="font-weight:600">${Utils.formatValue(p.value)}</div>`;
        },
      },
      xAxis: {
        type: 'category',
        data: dates,
        show: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          show: true,
          fontSize: 9,
          color: '#475569',
          interval: Math.max(0, Math.floor(dates.length / 4)),
          rotate: 0,
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        show: false,
        scale: true,
      },
      series: [{
        type: 'line',
        data: values,
        smooth: 0.3,
        symbol: 'none',
        lineStyle: {
          width: 2,
          color: color,
        },
        areaStyle: showArea ? {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: color + '30' },
            { offset: 0.5, color: color + '10' },
            { offset: 1, color: color + '00' },
          ]),
        } : undefined,
      }],
    };

    chart.setOption(option);

    // Handle resize
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);

    return chart;
  },

  createMultiLineChart(containerId, datasets, options = {}) {
    const container = document.getElementById(containerId);
    if (!container || !datasets || datasets.length === 0) return;

    if (this.charts.has(containerId)) {
      this.charts.get(containerId).dispose();
    }

    const chart = echarts.init(container);
    this.charts.set(containerId, chart);

    const series = datasets.map(ds => ({
      name: ds.name,
      type: 'line',
      data: ds.data.map(d => [d.date, d.value]),
      smooth: 0.3,
      symbol: 'none',
      lineStyle: { width: 2, color: ds.color },
      areaStyle: ds.showArea ? {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: ds.color + '25' },
          { offset: 1, color: ds.color + '00' },
        ]),
      } : undefined,
    }));

    const option = {
      animation: true,
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 11, fontFamily: 'JetBrains Mono' },
      },
      legend: {
        show: options.showLegend !== false,
        top: 0,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        icon: 'roundRect',
        itemWidth: 12,
        itemHeight: 3,
      },
      grid: { top: 36, right: 16, bottom: 24, left: 16, containLabel: true },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: '#1e293b' } },
        axisLabel: { color: '#64748b', fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLine: { show: false },
        axisLabel: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
      },
      series,
    };

    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);
    return chart;
  },

  createAreaStackChart(containerId, datasets, options = {}) {
    const container = document.getElementById(containerId);
    if (!container || !datasets || datasets.length === 0) return;

    if (this.charts.has(containerId)) {
      this.charts.get(containerId).dispose();
    }

    const chart = echarts.init(container);
    this.charts.set(containerId, chart);

    const allDates = new Set();
    datasets.forEach(ds => ds.data.forEach(d => allDates.add(d.date)));
    const dates = Array.from(allDates).sort();

    const series = datasets.map(ds => {
      const valueMap = {};
      ds.data.forEach(d => valueMap[d.date] = d.value);
      return {
        name: ds.name,
        type: 'line',
        stack: 'total',
        areaStyle: { opacity: 0.4 },
        smooth: 0.3,
        symbol: 'none',
        lineStyle: { width: 1.5, color: ds.color },
        itemStyle: { color: ds.color },
        data: dates.map(d => valueMap[d] !== undefined ? valueMap[d] : null),
      };
    });

    const option = {
      animation: true,
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
      },
      legend: {
        top: 0,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        icon: 'roundRect',
        itemWidth: 12,
        itemHeight: 3,
      },
      grid: { top: 36, right: 16, bottom: 24, left: 16, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#1e293b' } },
        axisLabel: { color: '#64748b', fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 6)) },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLine: { show: false },
        axisLabel: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
      },
      series,
    };

    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);
    return chart;
  },

  resizeAll() {
    this.charts.forEach(chart => chart.resize());
  },

  disposeAll() {
    this.charts.forEach(chart => chart.dispose());
    this.charts.clear();
  }
};

// ========== Component Renderers ==========
const Components = {

  // Render section header
  sectionHeader(icon, title, desc) {
    return `
      <div class="section-header">
        <div class="section-title">${icon} ${Utils.escapeHtml(title)}</div>
        ${desc ? `<div class="section-desc">${Utils.escapeHtml(desc)}</div>` : ''}
      </div>
    `;
  },

  // Render indicator card
  indicatorCard(id, config) {
    const {
      title, date, value, unit, change, history,
      assetImpacts, signalText, sourceUrl, sourceLabel,
      chartColor, timeRanges,
    } = config;

    const trendClass = Utils.getTrendClass(change);
    const trendArrow = Utils.getTrendArrow(change);
    const changeText = change !== null && change !== undefined
      ? `${trendArrow} ${Math.abs(change).toFixed(2)}`
      : '';

    const color = chartColor || CONFIG.chartColorNeutral;
    const ranges = timeRanges || ['1y', '2y', 'all'];

    // Asset impacts HTML
    const impactsHtml = assetImpacts ? `
      <div class="asset-impact">
        <span class="asset-impact-label">资产影响</span>
        ${Object.entries(assetImpacts).map(([asset, dir]) => `
          <span class="asset-impact-item">
            <span>${asset}</span>
            <span class="impact-arrow ${Utils.getImpactClass(dir)}">${Utils.getImpactArrow(dir)}</span>
          </span>
        `).join('')}
      </div>
    ` : '';

    // Signal interpretation
    const signalHtml = signalText ? `
      <div class="signal-box">
        <div class="signal-label">💡 当前信号解读</div>
        <div class="signal-text">${Utils.escapeHtml(signalText)}</div>
      </div>
    ` : '';

    // Source link
    const sourceHtml = sourceUrl ? `
      <a href="${sourceUrl}" target="_blank" class="source-link">
        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
        </svg>
        查看来源
      </a>
    ` : '';

    return `
      <div class="indicator-card" data-card-id="${id}">
        <div class="card-header">
          <div class="card-title">${Utils.escapeHtml(title)}</div>
          <div class="card-date">${date || '--'}</div>
        </div>
        <div class="card-value-row">
          <span class="card-value" style="color: ${color}">${Utils.formatValue(value)}</span>
          ${unit ? `<span class="card-unit">${unit}</span>` : ''}
          ${changeText ? `<span class="card-change ${trendClass}">${changeText}</span>` : ''}
        </div>
        <div class="flex items-center justify-between">
          <div class="time-range-btns">
            ${ranges.map(r => `
              <button class="time-range-btn ${r === '1y' ? 'active' : ''}"
                      data-range="${r}" data-card-id="${id}"
                      onclick="app.switchTimeRange('${id}', '${r}')">
                ${r === '1y' ? '1年' : r === '2y' ? '2年' : r === '5y' ? '5年' : '全部'}
              </button>
            `).join('')}
          </div>
        </div>
        <div class="card-chart" id="chart-${id}"></div>
        ${impactsHtml}
        ${signalHtml}
        ${sourceHtml}
      </div>
    `;
  },

  // Render investment advice banner
  adviceBanner(assets, summaryData) {
    const allocation = summaryData?.allocation || {};
    const allOverweight = allocation.overweight || [];
    const allMarketWeight = allocation.market_weight || [];
    const allUnderweight = allocation.underweight || [];

    return `
      <div class="advice-banner">
        ${assets.map(asset => {
          const signal = this.getAssetSignal(asset.name, allOverweight, allMarketWeight, allUnderweight);
          return `
            <div class="advice-card">
              <div class="advice-icon">${asset.icon}</div>
              <div class="advice-asset-name">${asset.name}</div>
              <div class="advice-signal ${Utils.signalToClass(signal)}">
                ${Utils.signalToText(signal)}
              </div>
              <div class="advice-detail">${Utils.escapeHtml(asset.detail || '')}</div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  },

  getAssetSignal(name, overweight, marketWeight, underweight) {
    // Direct match
    if (overweight.includes(name)) return 'bullish';
    if (underweight.includes(name)) return 'bearish';
    if (marketWeight.includes(name)) return 'neutral';

    // Fuzzy mapping for common aliases
    const aliasMap = {
      'A股': ['A股', '科技成长股'],
      '中国债券': ['中国国债'],
      '美股': ['美股'],
      '美债': ['美债'],
      '商品': ['铜', '原油'],
    };

    const aliases = aliasMap[name] || [name];
    for (const alias of aliases) {
      if (overweight.includes(alias)) return 'bullish';
      if (underweight.includes(alias)) return 'bearish';
      if (marketWeight.includes(alias)) return 'neutral';
    }
    return 'neutral';
  },

  // Render valuation card
  valuationCard(id, config) {
    const { title, value, unit, percentile, percentileLabel, history, chartColor } = config;
    const color = chartColor || CONFIG.chartColorNeutral;
    const pctlClass = Utils.getPercentileClass(percentile);

    return `
      <div class="val-card" data-card-id="${id}">
        <div class="card-header">
          <div class="card-title">${Utils.escapeHtml(title)}</div>
        </div>
        <div class="val-metric" style="margin-top: 8px;">
          <span class="val-value" style="color: ${color}">${Utils.formatValue(value)}</span>
          ${unit ? `<span class="card-unit">${unit}</span>` : ''}
          ${percentile !== null && percentile !== undefined ? `
            <span class="val-percentile ${pctlClass}">
              P${percentile.toFixed(0)} ${percentileLabel || ''}
            </span>
          ` : ''}
        </div>
        <div class="card-chart" id="chart-${id}" style="margin-top: 8px;"></div>
      </div>
    `;
  },
};

// ========== Tab Renderers ==========
const TabRenderers = {

  // ===== China Macro Tab =====
  renderChina(data, summary) {
    if (!data) return;

    // Investment advice banner
    const cnVal = data.valuation?.hs300_pe?.percentile;
    const goldData = app.data.assetValuation?.gold?.central_bank_buying;
    const cnAssets = [
      { name: 'A股', icon: '📈', detail: `沪深300 PE ${data.valuation?.hs300_pe?.value || '--'}x, P${cnVal ? cnVal.toFixed(0) : '--'}分位` },
      { name: '中国债券', icon: '🏦', detail: `10Y国债 ${data.bond?.cn_10y_yield?.value || '--'}%` },
      { name: '黄金', icon: '🥇', detail: goldData ? `央行购金 ${goldData.annual_tons}吨(${goldData.year})` : '货币对冲+避险' },
      { name: '商品', icon: '🛢️', detail: `PPI ${data.lagging?.ppi_yoy?.value || '--'}%` },
      { name: '人民币', icon: '💴', detail: `M2增速 ${data.lagging?.m2_yoy?.value || '--'}%` },
    ];

    // Data timestamp banner
    const tsBanner = Utils.renderTimestampBanner(data.update_time, '中国宏观');
    document.getElementById('china-advice-banner').innerHTML = tsBanner + Components.adviceBanner(cnAssets, summary);

    // Leading indicators
    const leadingHtml = Components.sectionHeader('📈', '领先指标', '领先于经济周期的指标，用于预判未来经济走势');
    const leadingCards = [];

    // PMI
    const pmiHist = data.history?.pmi || [];
    const pmiChange = Utils.computeChange(pmiHist.length ? pmiHist : null);
    leadingCards.push(Components.indicatorCard('cn-pmi', {
      title: '制造业PMI',
      date: data.leading?.pmi?.date || '--',
      value: data.leading?.pmi?.value,
      unit: data.leading?.pmi?.value >= 50 ? '(扩张)' : '(收缩)',
      change: pmiChange,
      history: pmiHist,
      chartColor: data.leading?.pmi?.value >= 50 ? CONFIG.chartColorUp : CONFIG.chartColorDown,
      assetImpacts: { 'A股': '+', '债券': '-', '黄金': '0', '商品': '+', '人民币': '0' },
      signalText: `PMI ${data.leading?.pmi?.value || '--'}，${data.leading?.pmi?.value >= 50 ? '制造业重回扩张区间' : '制造业仍在收缩区间'}，${pmiChange > 0 ? '较前值改善' : pmiChange < 0 ? '较前值走弱' : ''}`,
    }));

    // Social Financing
    leadingCards.push(Components.indicatorCard('cn-social-fin', {
      title: '社融增量',
      date: data.leading?.social_financing?.date || '--',
      value: data.leading?.social_financing?.value,
      unit: data.leading?.social_financing?.unit || '亿元',
      change: null,
      history: data.history?.social_financing || [],
      chartColor: '#8b5cf6',
      assetImpacts: { 'A股': '+', '债券': '-', '黄金': '0', '商品': '+', '人民币': '0' },
      signalText: `社融增量 ${data.leading?.social_financing?.value || '--'} 亿元，反映信用扩张力度`,
    }));

    // Valuation: HS300 PE as a leading indicator for market
    const hs300PeHist = data.history?.hs300_pe || [];
    leadingCards.push(Components.indicatorCard('cn-hs300-pe', {
      title: '沪深300 PE',
      date: data.valuation?.hs300_pe?.date || '--',
      value: data.valuation?.hs300_pe?.value,
      unit: '倍',
      change: Utils.computeChange(hs300PeHist),
      history: hs300PeHist,
      chartColor: '#06b6d4',
      assetImpacts: { 'A股': data.valuation?.hs300_pe?.percentile > 70 ? '-' : '+', '债券': '0', '黄金': '0', '商品': '0', '人民币': '0' },
      signalText: `沪深300 PE ${data.valuation?.hs300_pe?.value || '--'}倍，历史分位 ${data.valuation?.hs300_pe?.percentile?.toFixed(1) || '--'}%，${data.valuation?.hs300_pe?.percentile > 70 ? '估值偏高' : data.valuation?.hs300_pe?.percentile > 40 ? '估值中性' : '估值偏低'}`,
    }));

    document.getElementById('china-leading').innerHTML = leadingHtml + `<div class="grid-3">${leadingCards.join('')}</div>`;

    // Coincident indicators
    const coincHtml = Components.sectionHeader('📊', '同步指标', '与経済周期同步变化的指标，确认当前经济状态');
    const coincCards = [];

    coincCards.push(Components.indicatorCard('cn-gdp', {
      title: 'GDP增速',
      date: data.coincident?.gdp_growth?.date || '--',
      value: data.coincident?.gdp_growth?.value,
      unit: '%',
      change: null,
      history: data.history?.gdp_growth || [],
      chartColor: CONFIG.chartColorUp,
      assetImpacts: { 'A股': '+', '债券': '-', '黄金': '0', '商品': '+', '人民币': '+' },
      signalText: `GDP增速 ${data.coincident?.gdp_growth?.value || '--'}%，${data.coincident?.gdp_growth?.value >= 5 ? '经济增长稳健' : '经济增长放缓'}`,
    }));

    // SSE Index as coincident market indicator
    const sseHist = data.history?.sse_index || [];
    coincCards.push(Components.indicatorCard('cn-sse', {
      title: '上证综指',
      date: data.stock_index?.sse_composite?.date || '--',
      value: data.stock_index?.sse_composite?.value,
      unit: '点',
      change: Utils.computeChange(sseHist),
      history: sseHist,
      chartColor: CONFIG.chartColorUp,
      assetImpacts: { 'A股': '+', '债券': '-', '黄金': '0', '商品': '0', '人民币': '0' },
      signalText: `上证综指 ${data.stock_index?.sse_composite?.value ? Utils.formatValue(data.stock_index.sse_composite.value) : '--'} 点`,
    }));

    coincCards.push(Components.indicatorCard('cn-hs300-idx', {
      title: '沪深300指数',
      date: data.stock_index?.hs300?.date || '--',
      value: data.stock_index?.hs300?.value,
      unit: '点',
      change: Utils.computeChange(data.history?.hs300_index || []),
      history: data.history?.hs300_index || [],
      chartColor: '#8b5cf6',
      assetImpacts: { 'A股': '+', '债券': '-', '黄金': '0', '商品': '0', '人民币': '0' },
      signalText: `沪深300 ${data.stock_index?.hs300?.value ? Utils.formatValue(data.stock_index.hs300.value) : '--'} 点`,
    }));

    document.getElementById('china-coincident').innerHTML = coincHtml + `<div class="grid-3">${coincCards.join('')}</div>`;

    // Lagging indicators
    const lagHtml = Components.sectionHeader('📉', '滞后指标', '滞后于经济周期的指标，确认经济周期转折');
    const lagCards = [];

    const cpiHist = data.history?.cpi_yoy || [];
    lagCards.push(Components.indicatorCard('cn-cpi', {
      title: 'CPI同比',
      date: data.lagging?.cpi_yoy?.date || '--',
      value: data.lagging?.cpi_yoy?.value,
      unit: '%',
      change: Utils.computeChange(cpiHist),
      history: cpiHist,
      chartColor: data.lagging?.cpi_yoy?.value > 2 ? CONFIG.chartColorDown : CONFIG.chartColorNeutral,
      assetImpacts: { 'A股': data.lagging?.cpi_yoy?.value > 2 ? '-' : '+', '债券': data.lagging?.cpi_yoy?.value > 2 ? '-' : '+', '黄金': '+', '商品': '+', '人民币': '0' },
      signalText: `CPI同比 ${data.lagging?.cpi_yoy?.value || '--'}%，${data.lagging?.cpi_yoy?.value < 1 ? '通缩压力仍存' : data.lagging?.cpi_yoy?.value > 3 ? '通胀偏高' : '物价温和'}`,
    }));

    const ppiHist = data.history?.ppi_yoy || [];
    lagCards.push(Components.indicatorCard('cn-ppi', {
      title: 'PPI同比',
      date: data.lagging?.ppi_yoy?.date || '--',
      value: data.lagging?.ppi_yoy?.value,
      unit: '%',
      change: Utils.computeChange(ppiHist),
      history: ppiHist,
      chartColor: '#f59e0b',
      assetImpacts: { 'A股': '+', '债券': '0', '黄金': '+', '商品': '+', '人民币': '0' },
      signalText: `PPI同比 ${data.lagging?.ppi_yoy?.value || '--'}%，${data.lagging?.ppi_yoy?.value > 0 ? '工业品价格回升' : '工业品价格下降'}`,
    }));

    const cnBondHist = data.history?.cn_10y_bond || [];
    lagCards.push(Components.indicatorCard('cn-10y-bond', {
      title: '10Y中国国债收益率',
      date: data.bond?.cn_10y_yield?.date || '--',
      value: data.bond?.cn_10y_yield?.value,
      unit: '%',
      change: Utils.computeChange(cnBondHist),
      history: cnBondHist,
      chartColor: CONFIG.chartColorDown,
      assetImpacts: { 'A股': '-', '债券': '-', '黄金': '+', '商品': '0', '人民币': '+' },
      signalText: `10Y国债 ${data.bond?.cn_10y_yield?.value || '--'}%，${data.bond?.cn_10y_yield?.value < 2 ? '利率处于历史低位' : '利率正常化'}`,
    }));

    document.getElementById('china-lagging').innerHTML = lagHtml + `<div class="grid-3">${lagCards.join('')}</div>`;

    // A-Share Valuation Section
    const valHtml = Components.sectionHeader('💰', 'A股估值', '核心估值指标与历史分位');
    const valCards = [];

    valCards.push(Components.valuationCard('cn-val-hs300pe', {
      title: '沪深300 PE',
      value: data.valuation?.hs300_pe?.value,
      unit: '倍',
      percentile: data.valuation?.hs300_pe?.percentile,
      percentileLabel: '分位',
      history: data.history?.hs300_pe || [],
      chartColor: '#06b6d4',
    }));

    valCards.push(Components.valuationCard('cn-val-csi500pe', {
      title: '中证500 PE',
      value: data.valuation?.csi500_pe?.value,
      unit: '倍',
      percentile: null,
      history: data.history?.csi500_pe || [],
      chartColor: '#8b5cf6',
    }));

    document.getElementById('china-valuation').innerHTML = valHtml + `<div class="grid-3">${valCards.join('')}</div>`;

    // Render charts after DOM is ready
    requestAnimationFrame(() => {
      this.renderChinaCharts(data);
    });
  },

  renderChinaCharts(data) {
    // Render all mini charts for China tab
    this.renderMiniChartsForContainer('cn-pmi', data.history?.pmi || []);
    this.renderMiniChartsForContainer('cn-social-fin', data.history?.social_financing || []);
    this.renderMiniChartsForContainer('cn-hs300-pe', data.history?.hs300_pe || []);
    this.renderMiniChartsForContainer('cn-gdp', data.history?.gdp_growth || []);
    this.renderMiniChartsForContainer('cn-sse', data.history?.sse_index || []);
    this.renderMiniChartsForContainer('cn-hs300-idx', data.history?.hs300_index || []);
    this.renderMiniChartsForContainer('cn-cpi', data.history?.cpi_yoy || []);
    this.renderMiniChartsForContainer('cn-ppi', data.history?.ppi_yoy || []);
    this.renderMiniChartsForContainer('cn-10y-bond', data.history?.cn_10y_bond || []);
    this.renderMiniChartsForContainer('cn-val-hs300pe', data.history?.hs300_pe || []);
    this.renderMiniChartsForContainer('cn-val-csi500pe', data.history?.csi500_pe || []);
  },

  // ===== US Macro Tab =====
  renderUS(data, summary) {
    if (!data) return;

    const usAssets = [
      { name: '美股', icon: '📈', detail: `S&P500 ${data.market?.sp500?.value ? Utils.formatValue(data.market.sp500.value) : '--'}, VIX ${data.market?.vix?.value || '--'}` },
      { name: '美债', icon: '🏛️', detail: `10Y ${data.rates?.yield_10y?.value || '--'}%, 实际利率 ${data.rates?.real_rate_10y?.value || '--'}%` },
      { name: '黄金', icon: '🥇', detail: `通胀预期 ${data.rates?.inflation_expectation?.value || '--'}%` },
      { name: '商品', icon: '🛢️', detail: `PPI ${data.lagging?.ppi_yoy?.value ? data.lagging.ppi_yoy.value.toFixed(1) : '--'}%` },
      { name: '美元', icon: '💵', detail: `联邦基金利率 ${data.lagging?.fed_funds_rate?.value ? data.lagging.fed_funds_rate.value + '%' : '--'}` },
    ];

    // Data timestamp banner
    const tsBanner = Utils.renderTimestampBanner(data.update_time, '美国宏观');
    document.getElementById('us-advice-banner').innerHTML = tsBanner + Components.adviceBanner(usAssets, summary);

    // Leading indicators
    const leadingHtml = Components.sectionHeader('📈', '领先指标', 'Leading indicators for US economy');
    const leadingCards = [];

    const oecdHist = data.history?.oecd_cli || [];
    leadingCards.push(Components.indicatorCard('us-oecd', {
      title: 'OECD CLI',
      date: data.leading?.oecd_cli?.date || '--',
      value: data.leading?.oecd_cli?.value,
      change: Utils.computeChange(oecdHist),
      history: oecdHist,
      chartColor: CONFIG.chartColorNeutral,
      assetImpacts: { '美股': '+', '美债': '-', '黄金': '0', '商品': '+', '美元': '0' },
      signalText: `OECD CLI ${data.leading?.oecd_cli?.value ? data.leading.oecd_cli.value.toFixed(2) : '--'}，${data.leading?.oecd_cli?.value > 100 ? '高于趋势水平' : '低于趋势水平'}`,
    }));

    const yieldSpreadHist = data.history?.yield_spread_10y_2y || [];
    leadingCards.push(Components.indicatorCard('us-yield-spread', {
      title: '收益率曲线 (10Y-2Y)',
      date: data.leading?.yield_curve_10y_2y?.date || '--',
      value: data.leading?.yield_curve_10y_2y?.value,
      unit: '%',
      change: Utils.computeChange(yieldSpreadHist),
      history: yieldSpreadHist,
      chartColor: data.leading?.yield_curve_10y_2y?.value > 0 ? CONFIG.chartColorUp : CONFIG.chartColorDown,
      assetImpacts: { '美股': data.leading?.yield_curve_10y_2y?.value > 0 ? '+' : '-', '美债': '0', '黄金': '+', '商品': '0', '美元': '0' },
      signalText: `10Y-2Y利差 ${data.leading?.yield_curve_10y_2y?.value || '--'}%，${data.leading?.yield_curve_10y_2y?.value > 0 ? '曲线正常化' : '曲线倒挂，衰退信号'}`,
    }));

    // ISM PMI placeholder
    leadingCards.push(Components.indicatorCard('us-ism-pmi', {
      title: 'ISM PMI',
      date: data.leading?.ism_pmi?.date || '待更新',
      value: data.leading?.ism_pmi?.value,
      change: null,
      history: data.history?.ism_pmi || [],
      chartColor: '#f59e0b',
      assetImpacts: { '美股': '+', '美债': '-', '黄金': '0', '商品': '+', '美元': '0' },
      signalText: 'ISM PMI 数据待更新',
    }));

    document.getElementById('us-leading').innerHTML = leadingHtml + `<div class="grid-3">${leadingCards.join('')}</div>`;

    // Coincident indicators
    const coincHtml = Components.sectionHeader('📊', '同步指标', 'Coincident indicators confirming current economic state');
    const coincCards = [];

    coincCards.push(Components.indicatorCard('us-gdp', {
      title: 'GDP增速',
      date: data.coincident?.gdp_growth?.date || '--',
      value: data.coincident?.gdp_growth?.value,
      unit: '%',
      change: null,
      history: data.history?.gdp_growth || [],
      chartColor: CONFIG.chartColorUp,
      assetImpacts: { '美股': '+', '美债': '-', '黄金': '0', '商品': '+', '美元': '+' },
      signalText: `GDP增速 ${data.coincident?.gdp_growth?.value || '--'}%，${data.coincident?.gdp_growth?.value >= 2.5 ? '经济增长强劲' : data.coincident?.gdp_growth?.value >= 1.5 ? '温和扩张' : '增长放缓'}`,
    }));

    const unempHist = data.history?.unemployment || [];
    coincCards.push(Components.indicatorCard('us-unemp', {
      title: '失业率',
      date: data.coincident?.unemployment?.date || '--',
      value: data.coincident?.unemployment?.value,
      unit: '%',
      change: Utils.computeChange(unempHist),
      history: unempHist,
      chartColor: CONFIG.chartColorDown,
      assetImpacts: { '美股': '-', '美债': '+', '黄金': '+', '商品': '-', '美元': '-' },
      signalText: `失业率 ${data.coincident?.unemployment?.value || '--'}%，${data.coincident?.unemployment?.value > 4.5 ? '劳动力市场走弱' : data.coincident?.unemployment?.value < 4 ? '劳动力市场紧张' : '劳动力市场平稳'}`,
    }));

    coincCards.push(Components.indicatorCard('us-sp500', {
      title: 'S&P 500',
      date: data.market?.sp500?.date || '--',
      value: data.market?.sp500?.value,
      change: Utils.computeChange(data.history?.sp500 || []),
      history: data.history?.sp500 || [],
      chartColor: CONFIG.chartColorUp,
      assetImpacts: { '美股': '+', '美债': '-', '黄金': '0', '商品': '0', '美元': '0' },
      signalText: `S&P 500 ${data.market?.sp500?.value ? Utils.formatValue(data.market.sp500.value) : '--'}`,
    }));

    document.getElementById('us-coincident').innerHTML = coincHtml + `<div class="grid-3">${coincCards.join('')}</div>`;

    // Lagging indicators
    const lagHtml = Components.sectionHeader('📉', '滞后指标', 'Lagging indicators confirming cycle turns');
    const lagCards = [];

    const cpiHist = data.history?.cpi_yoy || [];
    lagCards.push(Components.indicatorCard('us-cpi', {
      title: 'CPI同比',
      date: data.lagging?.cpi_yoy?.date || '--',
      value: data.lagging?.cpi_yoy?.value,
      unit: '%',
      change: Utils.computeChange(cpiHist),
      history: cpiHist,
      chartColor: CONFIG.chartColorDown,
      assetImpacts: { '美股': '-', '美债': '-', '黄金': '+', '商品': '+', '美元': '+' },
      signalText: `CPI同比 ${data.lagging?.cpi_yoy?.value ? data.lagging.cpi_yoy.value.toFixed(2) : '--'}%，${data.lagging?.cpi_yoy?.value > 3 ? '通胀粘性偏高' : '通胀可控'}`,
    }));

    const ppiHist = data.history?.ppi_yoy || [];
    lagCards.push(Components.indicatorCard('us-ppi', {
      title: 'PPI同比',
      date: data.lagging?.ppi_yoy?.date || '--',
      value: data.lagging?.ppi_yoy?.value,
      unit: '%',
      change: Utils.computeChange(ppiHist),
      history: ppiHist,
      chartColor: '#f59e0b',
      assetImpacts: { '美股': '0', '美债': '-', '黄金': '+', '商品': '+', '美元': '0' },
      signalText: `PPI同比 ${data.lagging?.ppi_yoy?.value ? data.lagging.ppi_yoy.value.toFixed(2) : '--'}%，${data.lagging?.ppi_yoy?.value > 5 ? '上游涨价压力大' : '价格压力温和'}`,
    }));

    lagCards.push(Components.indicatorCard('us-fed-rate', {
      title: '联邦基金利率',
      date: data.lagging?.fed_funds_rate?.date || '--',
      value: data.lagging?.fed_funds_rate?.value,
      change: null,
      history: data.history?.fed_funds || [],
      chartColor: CONFIG.chartColorDown,
      assetImpacts: { '美股': '-', '美债': '-', '黄金': '-', '商品': '-', '美元': '+' },
      signalText: `联邦基金利率 ${data.lagging?.fed_funds_rate?.value ? data.lagging.fed_funds_rate.value + '%' : '--'}，${(data.lagging?.fed_funds_rate?.value || 0) < 4 ? '降息周期进行中' : '政策利率高位'}`,
    }));

    document.getElementById('us-lagging').innerHTML = lagHtml + `<div class="grid-3">${lagCards.join('')}</div>`;

    // US Rates section (三因子分解)
    const ratesHtml = Components.sectionHeader('🏦', '美债利率结构', '10Y收益率三因子分解：实际利率 + 通胀预期 + 期限溢价');
    document.getElementById('us-rates').innerHTML = ratesHtml + `
      <div class="grid-2">
        <div class="val-card">
          <div class="card-title mb-3">利率三因子分解走势</div>
          <div id="chart-us-rates-decomposition" class="multi-asset-chart"></div>
        </div>
        <div class="val-card">
          <div class="card-title mb-3">当前利率水平</div>
          <div class="grid grid-cols-2 gap-4 mt-4">
            <div>
              <div class="text-xs text-text-muted mb-1">10Y国债收益率</div>
              <div class="text-xl font-bold font-mono text-accent-blue">${data.rates?.yield_10y?.value || '--'}%</div>
            </div>
            <div>
              <div class="text-xs text-text-muted mb-1">2Y国债收益率</div>
              <div class="text-xl font-bold font-mono text-accent-purple">${data.rates?.yield_2y?.value || '--'}%</div>
            </div>
            <div>
              <div class="text-xs text-text-muted mb-1">实际利率 (10Y)</div>
              <div class="text-xl font-bold font-mono text-accent-green">${data.rates?.real_rate_10y?.value || '--'}%</div>
            </div>
            <div>
              <div class="text-xs text-text-muted mb-1">通胀预期</div>
              <div class="text-xl font-bold font-mono text-accent-amber">${data.rates?.inflation_expectation?.value || '--'}%</div>
            </div>
            <div>
              <div class="text-xs text-text-muted mb-1">期限溢价</div>
              <div class="text-xl font-bold font-mono text-accent-cyan">${data.rates?.term_premium?.value || '--'}%</div>
            </div>
            <div>
              <div class="text-xs text-text-muted mb-1">VIX恐慌指数</div>
              <div class="text-xl font-bold font-mono" style="color: ${(data.market?.vix?.value || 0) > 20 ? '#ef4444' : '#10b981'}">${data.market?.vix?.value || '--'}</div>
            </div>
          </div>
        </div>
      </div>
    `;

    // Render charts after DOM is ready
    requestAnimationFrame(() => {
      this.renderUSCharts(data);
    });
  },

  renderUSCharts(data) {
    this.renderMiniChartsForContainer('us-oecd', data.history?.oecd_cli || []);
    this.renderMiniChartsForContainer('us-yield-spread', data.history?.yield_spread_10y_2y || []);
    this.renderMiniChartsForContainer('us-ism-pmi', data.history?.ism_pmi || []);
    this.renderMiniChartsForContainer('us-gdp', data.history?.gdp_growth || []);
    this.renderMiniChartsForContainer('us-unemp', data.history?.unemployment || []);
    this.renderMiniChartsForContainer('us-sp500', data.history?.sp500 || []);
    this.renderMiniChartsForContainer('us-cpi', data.history?.cpi_yoy || []);
    this.renderMiniChartsForContainer('us-ppi', data.history?.ppi_yoy || []);
    this.renderMiniChartsForContainer('us-fed-rate', data.history?.fed_funds || []);

    // US Rates decomposition chart
    ChartManager.createMultiLineChart('chart-us-rates-decomposition', [
      { name: '实际利率', data: data.history?.real_rate_10y || [], color: '#10b981' },
      { name: '通胀预期', data: data.history?.inflation_expectation || [], color: '#f59e0b' },
      { name: '期限溢价', data: data.history?.term_premium || [], color: '#06b6d4' },
      { name: '10Y收益率', data: data.history?.yield_10y || [], color: '#3b82f6' },
    ], { showLegend: true });
  },

  // ===== Cycle Tab =====
  renderCycle(data) {
    if (!data) return;

    const kongbo = data.kongbo || {};
    const juglar = data.juglar || {};
    const kitchin = data.kitchin || {};
    const resonance = data.resonance || {};

    // Data timestamp banner
    let html = Utils.renderTimestampBanner(data.update_time, '全球周期');

    // Konbō Cycle
    html += Components.sectionHeader('🌊', '康波周期（长波 ~50-60年）', '技术革命驱动的长周期，当前正处于第五轮萧条尾声→第六轮初期');
    html += `
      <div class="kongbo-big mb-6">
        <div class="kongbo-phase">${Utils.escapeHtml(kongbo.current_phase || '未知')}</div>
        <div class="kongbo-confidence">
          ⚡ 置信度: ${kongbo.confidence === 'high' ? '高' : kongbo.confidence === 'medium' ? '中' : '低'}
        </div>
        ${kongbo.key_evidence ? `
          <div class="cycle-evidence mt-4 text-left max-w-lg mx-auto">
            ${kongbo.key_evidence.map(e => `
              <div class="evidence-item">
                <span class="evidence-dot"></span>
                <span>${Utils.escapeHtml(e)}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
      </div>
    `;

    // Konbō History Timeline
    if (kongbo.history && kongbo.history.length) {
      html += `
        <div class="val-card mb-6">
          <div class="card-title mb-4">📜 五轮康波历史</div>
          <div class="timeline">
            ${kongbo.history.map((item, i) => `
              <div class="timeline-item">
                <div class="timeline-dot ${i === kongbo.history.length - 1 ? 'active' : ''}"></div>
                <div class="flex items-baseline gap-3">
                  <span class="text-sm font-bold text-text-primary">第${item.round}轮 · ${Utils.escapeHtml(item.name)}</span>
                  <span class="text-xs text-text-muted font-mono">${item.start} - ${item.end || '至今'}</span>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    // Juglar Cycle
    html += Components.sectionHeader('🔄', '朱格拉周期（设备投资 ~8-10年）', '以企业设备投资和产能扩张为驱动的中周期');
    html += `<div class="grid-3 mb-6">`;
    ['us', 'cn', 'eu'].forEach(region => {
      const r = juglar[region] || {};
      const flag = region === 'us' ? '🇺🇸' : region === 'cn' ? '🇨🇳' : '🇪🇺';
      const name = region === 'us' ? '美国' : region === 'cn' ? '中国' : '欧洲';
      const phaseClass = r.phase_code === 'expansion' || r.phase_code === 'early_expansion' ? 'cycle-phase-bullish'
        : r.phase_code === 'late_contraction' ? 'cycle-phase-bearish' : 'cycle-phase-neutral';
      html += `
        <div class="cycle-card">
          <div class="flex items-center gap-2 mb-2">
            <span>${flag}</span>
            <span class="text-sm font-semibold text-text-primary">${name}</span>
          </div>
          <div class="cycle-phase ${phaseClass}">${Utils.escapeHtml(r.phase || '未知')}</div>
          ${r.start ? `<div class="text-xs text-text-muted mt-1">开始: ${r.start}${r.expected_end ? ` → 预计结束: ${r.expected_end}` : ''}</div>` : ''}
          ${r.expected_bottom ? `<div class="text-xs text-text-muted mt-1">预计触底: ${r.expected_bottom}</div>` : ''}
          ${r.key_driver ? `<div class="text-xs text-accent-blue mt-2">核心驱动: ${Utils.escapeHtml(r.key_driver)}</div>` : ''}
          ${r.key_issue ? `<div class="text-xs text-accent-amber mt-2">核心问题: ${Utils.escapeHtml(r.key_issue)}</div>` : ''}
          ${r.evidence ? `
            <div class="cycle-evidence">
              ${r.evidence.map(e => `
                <div class="evidence-item">
                  <span class="evidence-dot"></span>
                  <span>${Utils.escapeHtml(e)}</span>
                </div>
              `).join('')}
            </div>
          ` : ''}
        </div>
      `;
    });
    html += `</div>`;

    // Kitchin Cycle
    html += Components.sectionHeader('📦', '基钦周期（库存周期 ~3-4年）', '以企业库存变动为驱动的短周期');
    html += `<div class="grid-3 mb-6">`;
    ['us', 'cn', 'eu'].forEach(region => {
      const k = kitchin[region] || {};
      const flag = region === 'us' ? '🇺🇸' : region === 'cn' ? '🇨🇳' : '🇪🇺';
      const name = region === 'us' ? '美国' : region === 'cn' ? '中国' : '欧洲';
      const phaseClass = k.phase_code?.includes('restocking') ? 'cycle-phase-bullish'
        : k.phase_code?.includes('destocking') ? 'cycle-phase-bearish' : 'cycle-phase-neutral';
      html += `
        <div class="cycle-card">
          <div class="flex items-center gap-2 mb-2">
            <span>${flag}</span>
            <span class="text-sm font-semibold text-text-primary">${name}</span>
          </div>
          <div class="cycle-phase ${phaseClass}">${Utils.escapeHtml(k.phase || '未知')}</div>
          ${k.detail ? `<div class="text-xs text-text-secondary mt-2">${Utils.escapeHtml(k.detail)}</div>` : ''}
          ${k.risk ? `<div class="text-xs text-accent-amber mt-2">⚠️ ${Utils.escapeHtml(k.risk)}</div>` : ''}
          ${k.evidence ? `
            <div class="cycle-evidence">
              ${k.evidence.map(e => `
                <div class="evidence-item">
                  <span class="evidence-dot"></span>
                  <span>${Utils.escapeHtml(e)}</span>
                </div>
              `).join('')}
            </div>
          ` : ''}
        </div>
      `;
    });
    html += `</div>`;

    // Resonance Summary
    html += Components.sectionHeader('🎯', '三周期共振总结', '三大周期叠加形成的综合判断');
    html += `<div class="resonance-card">`;

    if (resonance.matrix) {
      html += `<div class="grid-3 mb-4">`;
      ['us', 'cn', 'eu'].forEach(region => {
        const m = resonance.matrix[region] || {};
        const flag = region === 'us' ? '🇺🇸' : region === 'cn' ? '🇨🇳' : '🇪🇺';
        const name = region === 'us' ? '美国' : region === 'cn' ? '中国' : '欧洲';
        html += `
          <div class="p-3 rounded-lg" style="background: rgba(255,255,255,0.02);">
            <div class="text-sm font-semibold text-text-primary mb-2">${flag} ${name}</div>
            <div class="text-xs text-text-secondary space-y-1">
              <div>康波: <span class="text-text-primary">${Utils.escapeHtml(m.kongbo || '')}</span></div>
              <div>朱格拉: <span class="text-text-primary">${Utils.escapeHtml(m.juglar || '')}</span></div>
              <div>基钦: <span class="text-text-primary">${Utils.escapeHtml(m.kitchin || '')}</span></div>
            </div>
            <div class="text-xs mt-2 text-accent-cyan font-medium">${Utils.escapeHtml(m.combined || '')}</div>
          </div>
        `;
      });
      html += `</div>`;
    }

    if (resonance.overall_regime) {
      html += `
        <div class="text-center py-3 border-t border-border-subtle">
          <div class="text-xs text-text-muted mb-1">总体格局</div>
          <div class="text-base font-bold text-text-primary">${Utils.escapeHtml(resonance.overall_regime)}</div>
          ${resonance.key_window ? `<div class="text-xs text-accent-amber mt-1">🔑 ${Utils.escapeHtml(resonance.key_window)}</div>` : ''}
        </div>
      `;
    }

    // Bull/Bear signals
    if (resonance.bull_signals || resonance.bear_signals) {
      html += `<div class="grid grid-cols-2 gap-4 mt-4">`;
      if (resonance.bull_signals) {
        html += `
          <div>
            <div class="text-xs font-semibold text-accent-green mb-2">🐂 看多确认信号</div>
            <div class="space-y-1">
              ${resonance.bull_signals.map(s => `<div class="text-xs text-text-secondary flex items-start gap-1"><span class="text-accent-green">✓</span>${Utils.escapeHtml(s)}</div>`).join('')}
            </div>
          </div>
        `;
      }
      if (resonance.bear_signals) {
        html += `
          <div>
            <div class="text-xs font-semibold text-accent-red mb-2">🐻 看空警示信号</div>
            <div class="space-y-1">
              ${resonance.bear_signals.map(s => `<div class="text-xs text-text-secondary flex items-start gap-1"><span class="text-accent-red">✗</span>${Utils.escapeHtml(s)}</div>`).join('')}
            </div>
          </div>
        `;
      }
      html += `</div>`;
    }

    // Asset implications
    if (resonance.asset_implication) {
      html += `
        <div class="mt-4 pt-4 border-t border-border-subtle">
          <div class="text-xs font-semibold text-text-secondary mb-2">💡 资产含义</div>
          <div class="grid grid-cols-2 gap-3">
            ${Object.entries(resonance.asset_implication).map(([asset, text]) => `
              <div class="text-xs text-text-secondary">
                <span class="font-medium text-text-primary">${asset}:</span> ${Utils.escapeHtml(text)}
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    html += `</div>`;

    document.getElementById('cycle-content').innerHTML = html;
  },

  // ===== Allocation Tab =====
  renderAllocation(valData, priceData, summaryData) {
    // Data timestamp banner (asset_prices.json + asset_valuation.json)
    let html = Utils.renderTimestampBanner(
      [priceData?.update_time, valData?.update_time],
      '资产配置'
    );

    // Asset Valuation Dashboard
    html += Components.sectionHeader('💰', '资产估值仪表板', '核心估值指标与历史分位');

    const usStock = valData?.us_stock || {};
    const cnStock = valData?.cn_stock || {};
    const usBond = valData?.us_bond || {};

    html += `<div class="grid-3 mb-6">`;

    // US Stock Valuation
    html += `
      <div class="val-card">
        <div class="card-title mb-3">🇺🇸 美股估值</div>
        <div class="space-y-3">
          <div>
            <div class="text-xs text-text-muted">Shiller PE (CAPE)</div>
            <div class="val-metric">
              <span class="val-value text-accent-amber">${usStock.shiller_pe?.value || '--'}</span>
              ${usStock.shiller_pe?.percentile ? `<span class="val-percentile ${Utils.getPercentileClass(usStock.shiller_pe.percentile)}">P${usStock.shiller_pe.percentile}</span>` : ''}
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">巴菲特指标</div>
            <div class="val-metric">
              <span class="val-value text-accent-red">${usStock.buffett_ratio?.value ? usStock.buffett_ratio.value.toFixed(0) + '%' : '--'}</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">股权风险溢价 (ERP)</div>
            <div class="val-metric">
              <span class="val-value ${usStock.erp?.value < 0 ? 'text-accent-red' : 'text-accent-green'}">${usStock.erp?.value ? usStock.erp.value.toFixed(1) + '%' : '--'}</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">Forward PE</div>
            <div class="val-metric">
              <span class="val-value text-accent-amber">${usStock.forward_pe?.value || '--'}</span>
              ${usStock.forward_pe?.percentile ? `<span class="val-percentile ${Utils.getPercentileClass(usStock.forward_pe.percentile)}">P${usStock.forward_pe.percentile}</span>` : ''}
            </div>
          </div>
        </div>
      </div>
    `;

    // CN Stock Valuation
    html += `
      <div class="val-card">
        <div class="card-title mb-3">🇨🇳 A股估值</div>
        <div class="space-y-3">
          <div>
            <div class="text-xs text-text-muted">沪深300 PE</div>
            <div class="val-metric">
              <span class="val-value text-accent-cyan">${cnStock.hs300_pe?.value || '--'}</span>
              ${cnStock.hs300_pe?.percentile ? `<span class="val-percentile ${Utils.getPercentileClass(cnStock.hs300_pe.percentile)}">P${cnStock.hs300_pe.percentile.toFixed(0)}</span>` : ''}
            </div>
            <div id="chart-val-hs300pe" class="card-chart mt-2"></div>
          </div>
          <div>
            <div class="text-xs text-text-muted">中证500 PE</div>
            <div class="val-metric">
              <span class="val-value text-accent-purple">${cnStock.csi500_pe?.value || '--'}</span>
            </div>
          </div>
        </div>
      </div>
    `;

    // US Bond Rate Structure
    html += `
      <div class="val-card">
        <div class="card-title mb-3">🏛️ 美债利率结构</div>
        <div class="space-y-3">
          <div>
            <div class="text-xs text-text-muted">10Y收益率</div>
            <div class="val-metric">
              <span class="val-value text-accent-blue">${usBond.yield_10y?.value || '--'}%</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">实际利率 (10Y TIPS)</div>
            <div class="val-metric">
              <span class="val-value text-accent-green">${usBond.real_rate_10y?.value || '--'}%</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">通胀预期 (Breakeven)</div>
            <div class="val-metric">
              <span class="val-value text-accent-amber">${usBond.inflation_expectation?.value || '--'}%</span>
            </div>
          </div>
          <div>
            <div class="text-xs text-text-muted">期限溢价</div>
            <div class="val-metric">
              <span class="val-value text-accent-cyan">${usBond.term_premium?.value || '--'}%</span>
            </div>
          </div>
        </div>
      </div>
    `;
    html += `</div>`;

    // Multi-asset normalized chart
    html += Components.sectionHeader('📈', '多资产走势', '主要资产近期价格走势对比');
    html += `
      <div class="val-card mb-6">
        <div id="chart-multi-asset" class="multi-asset-chart"></div>
      </div>
    `;

    // Allocation recommendations
    const alloc = summaryData?.allocation || {};
    html += Components.sectionHeader('🎯', '配置建议', '基于当前宏观环境的资产配置建议');
    html += `
      <div class="grid-3 mb-6">
        <div class="val-card">
          <div class="flex items-center gap-2 mb-3">
            <span class="alloc-tag alloc-overweight">▲ 超配</span>
          </div>
          <div class="flex flex-wrap gap-2">
            ${(alloc.overweight || []).map(a => `<span class="text-sm text-text-primary bg-accent-green/10 px-3 py-1 rounded-lg">${Utils.escapeHtml(a)}</span>`).join('')}
            ${(alloc.overweight || []).length === 0 ? '<span class="text-sm text-text-muted">暂无</span>' : ''}
          </div>
        </div>
        <div class="val-card">
          <div class="flex items-center gap-2 mb-3">
            <span class="alloc-tag alloc-market">● 标配</span>
          </div>
          <div class="flex flex-wrap gap-2">
            ${(alloc.market_weight || []).map(a => `<span class="text-sm text-text-primary bg-accent-amber/10 px-3 py-1 rounded-lg">${Utils.escapeHtml(a)}</span>`).join('')}
          </div>
        </div>
        <div class="val-card">
          <div class="flex items-center gap-2 mb-3">
            <span class="alloc-tag alloc-underweight">▼ 低配</span>
          </div>
          <div class="flex flex-wrap gap-2">
            ${(alloc.underweight || []).map(a => `<span class="text-sm text-text-primary bg-accent-red/10 px-3 py-1 rounded-lg">${Utils.escapeHtml(a)}</span>`).join('')}
          </div>
        </div>
      </div>
    `;

    // Key Risks
    const risks = summaryData?.key_risks || [];
    if (risks.length) {
      html += Components.sectionHeader('⚠️', '关键风险', '需要关注的下行风险');
      html += `<div class="grid-2 mb-6">`;
      risks.forEach(risk => {
        html += `
          <div class="risk-item">
            <span class="risk-icon">⚠</span>
            <span>${Utils.escapeHtml(risk)}</span>
          </div>
        `;
      });
      html += `</div>`;
    }

    document.getElementById('allocation-content').innerHTML = html;

    // Render charts
    requestAnimationFrame(() => {
      this.renderAllocationCharts(valData, priceData);
    });
  },

  renderAllocationCharts(valData, priceData) {
    // HS300 PE chart
    const hs300PeHist = valData?.cn_stock?.hs300_pe?.history || [];
    if (hs300PeHist.length > 0) {
      ChartManager.createMiniChart('chart-val-hs300pe', hs300PeHist, { color: '#06b6d4' });
    }

    // Multi-asset chart
    const prices = priceData?.prices || {};
    const datasets = [];

    if (prices.sp500?.daily?.length) {
      // Normalize to 100 base
      const baseVal = prices.sp500.daily[0].value;
      datasets.push({
        name: 'S&P 500',
        data: prices.sp500.daily.map(d => ({ date: d.date, value: (d.value / baseVal * 100) })),
        color: '#10b981',
      });
    }

    if (prices.us_10y_bond?.daily?.length) {
      datasets.push({
        name: 'US 10Y Bond',
        data: prices.us_10y_bond.daily.map(d => ({ date: d.date, value: d.value })),
        color: '#3b82f6',
      });
    }

    if (datasets.length > 0) {
      ChartManager.createMultiLineChart('chart-multi-asset', datasets, { showLegend: true });
    }
  },

  // ===== Helper: Render mini chart with time range switching =====
  renderMiniChartsForContainer(cardId, fullHistory, currentRange = '1y') {
    const containerId = `chart-${cardId}`;
    const container = document.getElementById(containerId);
    if (!container || !fullHistory || fullHistory.length === 0) return;

    const filtered = Utils.filterHistoryByRange(fullHistory, currentRange);
    if (filtered.length === 0) return;

    // Determine color based on trend
    const lastVal = filtered[filtered.length - 1].value;
    const firstVal = filtered[0].value;
    const isUp = lastVal >= firstVal;
    const color = isUp ? CONFIG.chartColorUp : CONFIG.chartColorDown;

    ChartManager.createMiniChart(containerId, filtered, { color, showArea: true });
  },
};

// ========== Main Application ==========
const app = {
  data: {
    cnMacro: null,
    usMacro: null,
    cyclePosition: null,
    assetValuation: null,
    assetPrices: null,
    dashboardSummary: null,
  },
  currentTab: 'china',

  async init() {
    try {
      await this.loadAllData();
      this.renderAll();
      this.hideLoading();
    } catch (err) {
      console.error('Init error:', err);
      this.hideLoading();
    }
  },

  async loadAllData() {
    const files = [
      ['cnMacro', 'cn_macro.json'],
      ['usMacro', 'us_macro.json'],
      ['cyclePosition', 'cycle_position.json'],
      ['assetValuation', 'asset_valuation.json'],
      ['assetPrices', 'asset_prices.json'],
      ['dashboardSummary', 'dashboard_summary.json'],
    ];

    const results = await Promise.allSettled(
      files.map(([, filename]) =>
        fetch(`${CONFIG.dataDir}/${filename}`).then(r => {
          if (!r.ok) throw new Error(`Failed to load ${filename}`);
          return r.json();
        })
      )
    );

    results.forEach((result, i) => {
      if (result.status === 'fulfilled') {
        this.data[files[i][0]] = result.value;
      } else {
        console.warn(`Failed to load ${files[i][1]}:`, result.reason);
      }
    });
  },

  renderAll() {
    // Update time
    const updateTimes = [
      this.data.cnMacro?.update_time,
      this.data.usMacro?.update_time,
      this.data.dashboardSummary?.update_time,
    ].filter(Boolean);
    if (updateTimes.length > 0) {
      document.getElementById('update-time').textContent = `更新于 ${updateTimes[0]}`;
    }

    // Render current tab
    this.renderTab(this.currentTab);
  },

  renderTab(tab) {
    switch (tab) {
      case 'china':
        TabRenderers.renderChina(this.data.cnMacro, this.data.dashboardSummary);
        break;
      case 'us':
        TabRenderers.renderUS(this.data.usMacro, this.data.dashboardSummary);
        break;
      case 'cycle':
        TabRenderers.renderCycle(this.data.cyclePosition);
        break;
      case 'allocation':
        TabRenderers.renderAllocation(this.data.assetValuation, this.data.assetPrices, this.data.dashboardSummary);
        break;
    }
  },

  switchTab(tab) {
    if (tab === this.currentTab) return;
    this.currentTab = tab;

    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
      content.classList.toggle('active', content.id === `tab-${tab}`);
      content.classList.toggle('hidden', content.id !== `tab-${tab}`);
    });

    // Dispose old charts and render new tab
    ChartManager.disposeAll();
    this.renderTab(tab);
  },

  switchTimeRange(cardId, range) {
    // Update button states
    const card = document.querySelector(`[data-card-id="${cardId}"]`);
    if (card) {
      card.querySelectorAll('.time-range-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.range === range);
      });
    }

    // Re-render the chart with filtered data
    let fullHistory = null;
    const data = this.data;

    // Find the correct history data for this card
    const historyMap = {
      // China
      'cn-pmi': data.cnMacro?.history?.pmi,
      'cn-social-fin': data.cnMacro?.history?.social_financing,
      'cn-hs300-pe': data.cnMacro?.history?.hs300_pe,
      'cn-gdp': data.cnMacro?.history?.gdp_growth,
      'cn-sse': data.cnMacro?.history?.sse_index,
      'cn-hs300-idx': data.cnMacro?.history?.hs300_index,
      'cn-cpi': data.cnMacro?.history?.cpi_yoy,
      'cn-ppi': data.cnMacro?.history?.ppi_yoy,
      'cn-10y-bond': data.cnMacro?.history?.cn_10y_bond,
      'cn-val-hs300pe': data.cnMacro?.history?.hs300_pe,
      'cn-val-csi500pe': data.cnMacro?.history?.csi500_pe,
      // US
      'us-oecd': data.usMacro?.history?.oecd_cli,
      'us-yield-spread': data.usMacro?.history?.yield_spread_10y_2y,
      'us-ism-pmi': data.usMacro?.history?.ism_pmi,
      'us-gdp': data.usMacro?.history?.gdp_growth,
      'us-unemp': data.usMacro?.history?.unemployment,
      'us-sp500': data.usMacro?.history?.sp500,
      'us-cpi': data.usMacro?.history?.cpi_yoy,
      'us-ppi': data.usMacro?.history?.ppi_yoy,
      'us-fed-rate': data.usMacro?.history?.fed_funds,
    };

    fullHistory = historyMap[cardId] || [];
    if (fullHistory && fullHistory.length > 0) {
      TabRenderers.renderMiniChartsForContainer(cardId, fullHistory, range);
    }
  },

  async refresh() {
    const btn = document.getElementById('refresh-btn');
    btn.classList.add('refreshing');

    ChartManager.disposeAll();
    await this.loadAllData();
    this.renderTab(this.currentTab);

    // Update time display
    const updateTimes = [
      this.data.cnMacro?.update_time,
      this.data.usMacro?.update_time,
    ].filter(Boolean);
    if (updateTimes.length > 0) {
      document.getElementById('update-time').textContent = `更新于 ${updateTimes[0]}`;
    }

    setTimeout(() => btn.classList.remove('refreshing'), 1000);
  },

  hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
      overlay.style.opacity = '0';
      overlay.style.transition = 'opacity 0.4s ease';
      setTimeout(() => overlay.style.display = 'none', 400);
    }
  },
};

// Handle window resize
window.addEventListener('resize', () => {
  ChartManager.resizeAll();
});

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  app.init();
});
