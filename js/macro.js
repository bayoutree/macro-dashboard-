/**
 * macro.js - L2 宏观指标渲染
 * 处理美国和中国宏观指标：领先/同步/滞后
 */

const MacroModule = (() => {
  // 图表实例存储（避免内存泄漏）
  const chartInstances = {};

  /** 趋势箭头 */
  function trendArrow(trend) {
    if (!trend) return { arrow: '→', color: '#6b7280' };
    const map = {
      expanding: { arrow: '↑', color: '#22c55e' },
      rising: { arrow: '↑', color: '#22c55e' },
      improving: { arrow: '↑', color: '#22c55e' },
      contracting: { arrow: '↓', color: '#ef4444' },
      falling: { arrow: '↓', color: '#ef4444' },
      deteriorating: { arrow: '↓', color: '#ef4444' },
      stable: { arrow: '→', color: '#6b7280' },
      neutral: { arrow: '→', color: '#6b7280' },
    };
    return map[trend] || { arrow: '→', color: '#6b7280' };
  }

  /** 格式化指标值 */
  function formatValue(val, unit) {
    if (val === null || val === undefined) return '-';
    if (unit === '%') return `${val}%`;
    if (unit === 'bp') return `${val} bp`;
    if (typeof val === 'string') return val;
    return val.toString();
  }

  /** 指标中文名映射 */
  const indicatorNames = {
    // 美国
    ism_pmi: 'ISM 制造业PMI',
    ism_new_orders: 'ISM 新订单',
    yield_curve_10y_2y: '收益率曲线(10Y-2Y)',
    lei: '领先经济指标(LEI)',
    gdp_growth: 'GDP 同比增速',
    unemployment: '失业率',
    industrial_production: '工业生产',
    nonfarm_payrolls_yoy: '非农就业 YoY',
    cpi_yoy: 'CPI 同比',
    ppi_yoy: 'PPI 同比',
    fed_funds_rate: '联邦基金利率',
    oecd_cli: 'OECD CLI',
    // 中国
    official_pmi: '官方制造业PMI',
    caixin_pmi: '财新PMI',
    social_financing: '社融增速',
    m2_growth: 'M2 增速',
    ppi_yoy_cn: 'PPI 同比',
    cpi_yoy_cn: 'CPI 同比',
    gdp_growth_cn: 'GDP 同比增速',
    industrial_production_cn: '工业增加值',
    retail_sales: '社零同比',
    fixed_asset_inv: '固投累计同比',
  };

  /** 渲染单个指标卡 */
  function renderIndicator(container, key, data, historyData) {
    const name = indicatorNames[key] || key;
    const val = data.value;
    const dateStr = data.date || '';
    const trend = data.trend || '';
    const { arrow, color } = trendArrow(trend);

    const unit = key.includes('pmi') ? '%' :
                 key.includes('unemployment') ? '%' :
                 key.includes('cpi') ? '%' :
                 key.includes('ppi') ? '%' :
                 key.includes('growth') ? '%' :
                 key.includes('m2') ? '%' :
                 key.includes('social') ? '%' :
                 key.includes('yield_curve') ? ' bp' : '';

    const item = document.createElement('div');
    item.className = 'indicator-item';
    item.innerHTML = `
      <span class="ind-name">${name}</span>
      <div class="flex items-baseline gap-1">
        <span class="ind-value" style="color:${val !== null && val !== undefined ? '#fff' : '#6b7280'}">
          ${formatValue(val, unit)}
        </span>
        <span style="color:${color}; font-size:1.2em">${arrow}</span>
      </div>
      <span class="ind-date">${dateStr}</span>
      <div class="ind-chart" id="spark-${key}"></div>
    `;
    container.appendChild(item);

    // 如果有历史数据，画迷你折线图
    if (historyData && historyData.length > 0) {
      renderSparkline(`spark-${key}`, historyData);
    }
  }

  /** 迷你折线图 */
  function renderSparkline(containerId, historyData) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const chart = echarts.init(el);
    chartInstances[containerId] = chart;

    const dates = historyData.map(d => d.date);
    const values = historyData.map(d => d.value).filter(v => v !== null && v !== undefined);

    if (values.length === 0) return;

    const lastVal = values[values.length - 1];
    const firstVal = values[0];
    const lineColor = lastVal >= firstVal ? '#22c55e' : '#ef4444';

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 0, right: 0, top: 2, bottom: 2 },
      xAxis: { type: 'category', show: false, data: dates },
      yAxis: { type: 'value', show: false, min: Math.min(...values) * 0.98, max: Math.max(...values) * 1.02 },
      series: [{
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: lineColor },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: lineColor + '30' },
          { offset: 1, color: 'transparent' },
        ])},
      }],
    });
  }

  /** 渲染一组指标 */
  function renderGroup(containerId, groupData, historyObj) {
    const container = document.getElementById(containerId);
    if (!container || !groupData) return;

    container.innerHTML = '';
    Object.entries(groupData).forEach(([key, data]) => {
      const historyData = historyObj && historyObj[key] ? historyObj[key] : null;
      renderIndicator(container, key, data, historyData);
    });
  }

  /** 主渲染入口 */
  function render(usData, cnData) {
    // 美国
    if (usData) {
      renderGroup('us-leading', usData.leading, usData.history);
      renderGroup('us-coincident', usData.coincident, usData.history);
      renderGroup('us-lagging', usData.lagging, usData.history);
    }

    // 中国
    if (cnData) {
      renderGroup('cn-leading', cnData.leading, cnData.history);
      renderGroup('cn-coincident', cnData.coincident, cnData.history);
      renderGroup('cn-lagging', cnData.lagging, cnData.history);
    }
  }

  /** 销毁图表 */
  function dispose() {
    Object.values(chartInstances).forEach(c => c.dispose());
  }

  return { render, dispose };
})();
