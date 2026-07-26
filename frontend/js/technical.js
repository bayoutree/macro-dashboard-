/**
 * technical.js - L4 技术走势渲染
 * 多资产归一化对比、资产独立走势、波动率VIX
 */

const TechnicalModule = (() => {
  const charts = {};
  let priceData = null;
  let currentRange = '3Y';
  let currentAsset = 'sp500';

  /** 资产名称映射 */
  const assetNames = {
    sp500: 'S&P 500',
    nasdaq: '纳斯达克',
    sse: '上证综指',
    hs300: '沪深300',
    cn_10y_bond: '中国10Y国债',
    us_10y_bond: '美国10Y国债',
    gold: '黄金',
    copper: '铜',
    crude_oil: '原油',
    btc: '比特币',
    vix: 'VIX',
  };

  function initChart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    const chart = echarts.init(el);
    charts[id] = chart;
    return chart;
  }

  /** 时间范围过滤 */
  function filterByRange(dailyData, range) {
    if (!dailyData || dailyData.length === 0) return [];
    if (range === 'MAX') return dailyData;

    const yearsMap = { '1Y': 1, '3Y': 3, '5Y': 5, '10Y': 10 };
    const years = yearsMap[range] || 3;

    // 获取最新日期
    const lastDate = new Date(dailyData[dailyData.length - 1].date);
    const startDate = new Date(lastDate);
    startDate.setFullYear(startDate.getFullYear() - years);

    return dailyData.filter(d => new Date(d.date) >= startDate);
  }

  /** 归一化到基准=100 */
  function normalizeToBase100(data) {
    if (!data || data.length === 0) return [];
    const baseVal = data[0].value;
    if (!baseVal) return [];
    return data.map(d => ({
      date: d.date,
      value: baseVal ? +((d.value / baseVal) * 100).toFixed(2) : null,
    }));
  }

  /** 渲染多资产归一化走势 */
  function renderNormalized(prices) {
    const chart = initChart('chart-normalized');
    if (!chart || !prices) return;

    const assets = ['sp500', 'gold', 'cn_10y_bond', 'crude_oil'];
    const series = [];

    assets.forEach(key => {
      const assetData = prices[key]?.daily;
      if (!assetData || assetData.length === 0) return;

      const filtered = filterByRange(assetData, currentRange);
      const normalized = normalizeToBase100(filtered);

      series.push({
        name: assetNames[key],
        type: 'line',
        data: normalized.map(d => d.value),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, color: CONFIG.colors.chart.assets[key] },
      });
    });

    const dates = filterByRange(prices.sp500?.daily || [], currentRange).map(d => d.date);

    chart.setOption({
      ...CONFIG.echartsDefaults,
      legend: { textStyle: { color: '#9ca3af', fontSize: 11 }, top: 0 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#6b7280', fontSize: 10, rotate: 30 }, axisLine: { lineStyle: { color: '#374151' } } },
      yAxis: { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } }, name: '基准=100' },
      series,
    });
  }

  /** 渲染时间范围按钮 */
  function renderRangeButtons(containerId, callback) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    CONFIG.timeRanges.forEach(range => {
      const btn = document.createElement('button');
      btn.className = `range-btn ${range === currentRange ? 'active' : ''}`;
      btn.textContent = range;
      btn.onclick = () => {
        currentRange = range;
        container.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        callback();
      };
      container.appendChild(btn);
    });
  }

  /** 渲染资产标签页 */
  function renderAssetTabs() {
    const container = document.getElementById('asset-tabs');
    if (!container || !priceData) return;
    container.innerHTML = '';

    const assetKeys = Object.keys(priceData.prices || {}).filter(k => priceData.prices[k]?.daily?.length > 0);
    assetKeys.forEach(key => {
      const btn = document.createElement('button');
      btn.className = `range-btn ${key === currentAsset ? 'active' : ''}`;
      btn.textContent = assetNames[key] || key;
      btn.onclick = () => {
        currentAsset = key;
        container.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderAssetDetail();
      };
      container.appendChild(btn);
    });
  }

  /** 渲染单资产详情 */
  function renderAssetDetail() {
    const chart = initChart('chart-asset-detail');
    if (!chart || !priceData) return;

    const assetData = priceData.prices?.[currentAsset];
    if (!assetData || !assetData.daily) return;

    const filtered = filterByRange(assetData.daily, currentRange);
    const dates = filtered.map(d => d.date);
    const values = filtered.map(d => d.value ?? d.price);

    const series = [{
      name: assetNames[currentAsset] || currentAsset,
      type: 'line',
      data: values,
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 2, color: CONFIG.colors.chart.assets[currentAsset] || '#3b82f6' },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: (CONFIG.colors.chart.assets[currentAsset] || '#3b82f6') + '25' },
        { offset: 1, color: 'transparent' },
      ])},
    }];

    // MA200 均线
    if (assetData.ma200 && values.length >= 200) {
      const ma200 = [];
      for (let i = 0; i < values.length; i++) {
        if (i < 199) { ma200.push(null); continue; }
        const sum = values.slice(i - 199, i + 1).reduce((a, b) => a + (b || 0), 0);
        ma200.push(+(sum / 200).toFixed(2));
      }
      series.push({
        name: 'MA200',
        type: 'line',
        data: ma200,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: '#f59e0b', type: 'dashed' },
      });
    }

    chart.setOption({
      ...CONFIG.echartsDefaults,
      legend: { show: series.length > 1, textStyle: { color: '#9ca3af' }, top: 0 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#6b7280', fontSize: 10, rotate: 30 }, axisLine: { lineStyle: { color: '#374151' } } },
      yAxis: { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } } },
      series,
      dataZoom: [{ type: 'inside' }],
    });

    // 更新元数据
    renderAssetMeta(assetData);
  }

  /** 资产元数据（最新价、距高点回撤等） */
  function renderAssetMeta(assetData) {
    const container = document.getElementById('asset-detail-meta');
    if (!container) return;

    const latest = assetData.latest;
    const dd = assetData.drawdown_from_high;
    const ma200 = assetData.ma200;

    container.innerHTML = `
      <div class="indicator-item">
        <span class="ind-name">最新价格</span>
        <span class="ind-value">${latest ?? '-'}</span>
      </div>
      <div class="indicator-item">
        <span class="ind-name">距高点回撤</span>
        <span class="ind-value" style="color:${dd && dd < 0 ? '#ef4444' : '#22c55e'}">${dd ? dd.toFixed(1) + '%' : '-'}</span>
      </div>
      <div class="indicator-item">
        <span class="ind-name">MA200</span>
        <span class="ind-value">${ma200 ?? '-'}</span>
      </div>
      <div class="indicator-item">
        <span class="ind-name">vs MA200</span>
        <span class="ind-value" style="color:${latest && ma200 ? (latest > ma200 ? '#22c55e' : '#ef4444') : '#6b7280'}">
          ${latest && ma200 ? ((latest - ma200) / ma200 * 100).toFixed(1) + '%' : '-'}
        </span>
      </div>
    `;
  }

  /** 渲染VIX波动率 */
  function renderVIX(volData) {
    const chart = initChart('chart-vix');
    if (!chart || !volData) return;

    const vixHistory = volData.vix?.history || [];
    if (vixHistory.length === 0) {
      chart.setOption({ ...CONFIG.echartsDefaults, title: { text: 'VIX 数据待更新', textStyle: { color: '#6b7280' }, left: 'center', top: 'center' } });
      return;
    }

    chart.setOption({
      ...CONFIG.echartsDefaults,
      legend: { textStyle: { color: '#9ca3af' }, top: 0 },
      xAxis: { type: 'category', data: vixHistory.map(d => d.date), axisLabel: { color: '#6b7280', fontSize: 10 }, axisLine: { lineStyle: { color: '#374151' } } },
      yAxis: { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } }, name: 'VIX' },
      series: [
        {
          name: 'VIX',
          type: 'line',
          data: vixHistory.map(d => d.value),
          smooth: true,
          symbol: 'none',
          lineStyle: { color: '#ec4899', width: 2 },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(236,72,153,0.3)' },
            { offset: 1, color: 'transparent' },
          ])},
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#f59e0b', type: 'dashed' },
            data: [
              { yAxis: 20, label: { formatter: '低波动 20', color: '#22c55e' } },
              { yAxis: 30, label: { formatter: '高波动 30', color: '#ef4444' } },
            ],
          },
        },
      ],
    });
  }

  /** 主入口 */
  function render(data) {
    if (!data) return;
    priceData = data;

    renderRangeButtons('norm-range-btns', () => renderNormalized(priceData.prices));
    renderRangeButtons('asset-range-btns', () => renderAssetDetail());
    renderNormalized(data.prices);
    renderAssetTabs();
    renderAssetDetail();
    renderVIX(data.volatility);
  }

  function dispose() {
    Object.values(charts).forEach(c => c.dispose());
  }

  return { render, dispose };
})();

// 响应式
window.addEventListener('resize', () => {
  Object.values(TechnicalModule?.charts || {}).forEach(c => c?.resize?.());
});
