/**
 * valuation.js - L3 资产估值渲染
 * 美股/A股/美债/黄金/商品的估值图表和仪表盘
 */

const ValuationModule = (() => {
  const charts = {};

  /** 初始化图表（自动管理尺寸） */
  function initChart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) { charts[id].dispose(); }
    const chart = echarts.init(el);
    charts[id] = chart;
    return chart;
  }

  /** 通用 ECharts 折线图 */
  function lineChart(id, dates, series, opts = {}) {
    const chart = initChart(id);
    if (!chart) return;

    chart.setOption({
      ...CONFIG.echartsDefaults,
      legend: { show: series.length > 1, textStyle: { color: '#9ca3af', fontSize: 11 }, top: 0 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#6b7280', fontSize: 10 }, axisLine: { lineStyle: { color: '#374151' } } },
      yAxis: { type: 'value', axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } } },
      series: series,
      ...opts,
    });
  }

  /** 仪表盘渲染 */
  function renderGauge(id, value, label, maxVal = 100) {
    const chart = initChart(id);
    if (!chart) return;

    const color = value > 80 ? '#ef4444' : value > 50 ? '#f59e0b' : '#22c55e';

    chart.setOption({
      backgroundColor: 'transparent',
      series: [{
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: maxVal,
        radius: '90%',
        progress: { show: true, width: 12, itemStyle: { color } },
        pointer: { show: true, length: '60%', width: 4, itemStyle: { color: '#fff' } },
        axisLine: { lineStyle: { width: 12, color: [[1, '#1e293b']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        title: { offsetCenter: [0, '70%'], fontSize: 11, color: '#9ca3af' },
        detail: { offsetCenter: [0, '40%'], fontSize: 18, fontWeight: 'bold', color, formatter: '{value}%' },
        data: [{ value: value, name: label }],
      }],
    });
  }

  /** 指标度量卡 */
  function renderMetricCard(id, label, value, unit, percentile) {
    const el = document.getElementById(id);
    if (!el) return;
    const pctStr = percentile !== null && percentile !== undefined ? `历史分位 ${percentile}%` : '';
    const color = percentile > 80 ? '#ef4444' : percentile > 50 ? '#f59e0b' : percentile !== null ? '#22c55e' : '#6b7280';
    el.innerHTML = `
      <div class="mc-label">${label}</div>
      <div class="mc-value" style="color:${color}">${value !== null && value !== undefined ? value + (unit || '') : '-'}</div>
      <div class="mc-pct" style="color:${color}">${pctStr}</div>
    `;
  }

  /** 渲染美股估值 */
  function renderUSStock(data) {
    if (!data) return;

    // 指标卡
    renderMetricCard('shiller-pe-card', 'Shiller PE', data.shiller_pe?.value, '', data.shiller_pe?.percentile);
    renderMetricCard('buffett-card', '巴菲特指标', data.buffett_ratio?.value, '%', data.buffett_ratio?.percentile);
    renderMetricCard('erp-card', '股权风险溢价', data.erp?.value, '%', null);
    renderMetricCard('fwd-pe-card', 'Forward PE', data.forward_pe?.value, '', data.forward_pe?.percentile);

    // Shiller PE 历史折线
    if (data.shiller_pe?.history?.length) {
      const h = data.shiller_pe.history;
      lineChart('chart-shiller-pe', h.map(d => d.date), [{
        name: 'Shiller PE',
        type: 'line',
        data: h.map(d => d.value),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#3b82f6', width: 2 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(59,130,246,0.2)' },
          { offset: 1, color: 'transparent' },
        ])},
        markLine: data.shiller_pe.value ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#ef4444', type: 'dashed' },
          data: [{ yAxis: data.shiller_pe.value, label: { formatter: `当前 ${data.shiller_pe.value}`, color: '#ef4444' } }],
        } : undefined,
      }], { title: { text: 'Shiller PE 历史', textStyle: { color: '#9ca3af', fontSize: 12 }, left: 10, top: 0 } });
    }
  }

  /** 渲染A股估值 */
  function renderCNStock(data) {
    if (!data) return;

    renderMetricCard('hs300-pe-card', '沪深300 PE', data.hs300_pe?.value, '', data.hs300_pe?.percentile);
    renderMetricCard('hs300-pb-card', '沪深300 PB', data.hs300_pb?.value, '', data.hs300_pb?.percentile);
    renderMetricCard('csi500-pe-card', '中证500 PE', data.csi500_pe?.value, '', data.csi500_pe?.percentile);

    // PE/PB 历史
    if (data.hs300_pe?.history?.length) {
      const h = data.hs300_pe.history;
      const series = [{
        name: '沪深300 PE',
        type: 'line',
        data: h.map(d => d.value),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#f97316', width: 2 },
      }];
      if (data.hs300_pb?.history?.length) {
        series.push({
          name: '沪深300 PB',
          type: 'line',
          yAxisIndex: 1,
          data: data.hs300_pb.history.map(d => d.value),
          smooth: true,
          symbol: 'none',
          lineStyle: { color: '#8b5cf6', width: 2 },
        });
      }
      lineChart('chart-hs300', h.map(d => d.date), series, {
        yAxis: [
          { type: 'value', axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } } },
          { type: 'value', axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
        ],
      });
    }
  }

  /** 渲染美债利率 */
  function renderUSBond(data) {
    if (!data) return;

    renderMetricCard('yield-10y-card', '10Y 收益率', data.yield_10y?.value, '%', null);
    renderMetricCard('real-rate-card', '实际利率', data.real_rate_10y?.value, '%', null);
    renderMetricCard('inf-exp-card', '通胀预期', data.inflation_expectation?.value, '%', null);

    // 10Y收益率分解（堆叠面积图）
    if (data.term_premium?.history?.length && data.inflation_expectation?.history?.length) {
      const tp = data.term_premium.history;
      const dates = tp.map(d => d.date);
      // 预期短利率 = 10Y收益率 - 期限溢价
      const expectedShort = data.yield_10y?.history?.map((d, i) => {
        const tpVal = tp[i]?.value;
        return d.value !== null && tpVal !== null ? +(d.value - tpVal).toFixed(2) : null;
      }) || [];

      lineChart('chart-yield-decomp', dates, [
        {
          name: '预期短利率',
          type: 'line',
          stack: 'yield',
          data: expectedShort,
          smooth: true,
          symbol: 'none',
          areaStyle: { color: 'rgba(59,130,246,0.5)' },
          lineStyle: { color: '#3b82f6' },
        },
        {
          name: '期限溢价',
          type: 'line',
          stack: 'yield',
          data: tp.map(d => d.value),
          smooth: true,
          symbol: 'none',
          areaStyle: { color: 'rgba(249,115,22,0.5)' },
          lineStyle: { color: '#f97316' },
        },
      ]);
    }
  }

  /** 渲染黄金 */
  function renderGold(data) {
    if (!data) return;

    renderMetricCard('gold-price-card', '金价(USD/oz)', data.price_usd?.value, '', null);
    const cb = data.central_bank_buying;
    renderMetricCard('gold-central-bank-card', `央行购金(${cb?.year || '-'})`, cb?.annual_tons, ' 吨', null);

    // 金价 vs 实际利率 对比图
    if (data.real_rate_vs_gold?.history?.length) {
      const h = data.real_rate_vs_gold.history;
      lineChart('chart-gold-realrate', h.map(d => d.date), [
        {
          name: '金价',
          type: 'line',
          data: h.map(d => d.gold),
          smooth: true, symbol: 'none',
          lineStyle: { color: '#eab308', width: 2 },
          yAxisIndex: 0,
        },
        {
          name: '实际利率(右轴)',
          type: 'line',
          data: h.map(d => d.real_rate),
          smooth: true, symbol: 'none',
          lineStyle: { color: '#3b82f6', width: 2 },
          yAxisIndex: 1,
        },
      ], {
        yAxis: [
          { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } }, name: '金价$', nameTextStyle: { color: '#eab308' } },
          { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { show: false }, name: '实际利率%', nameTextStyle: { color: '#3b82f6' } },
        ],
      });
    }

    // 央行购金量柱状图
    if (data.central_bank_buying?.history?.length) {
      const h = data.central_bank_buying.history;
      const chart = initChart('chart-gold-central');
      if (chart) {
        chart.setOption({
          ...CONFIG.echartsDefaults,
          xAxis: { type: 'category', data: h.map(d => d.year), axisLabel: { color: '#6b7280' } },
          yAxis: { type: 'value', axisLabel: { color: '#6b7280' }, splitLine: { lineStyle: { color: CONFIG.colors.chart.grid } }, name: '吨' },
          series: [{
            name: '央行购金量',
            type: 'bar',
            data: h.map(d => d.tons),
            itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#eab308' },
              { offset: 1, color: '#92400e' },
            ])},
          }],
        });
      }
    }
  }

  /** 渲染商品 */
  function renderCommodity(data) {
    if (!data) return;

    // 铜
    if (data.copper?.history) {
      const h = typeof data.copper.history === 'object' && !Array.isArray(data.copper.history)
        ? data.copper.history.price || []
        : data.copper.history;
      if (h.length) {
        lineChart('chart-copper', h.map(d => d.date), [{
          name: '铜价',
          type: 'line',
          data: h.map(d => d.value ?? d.price),
          smooth: true, symbol: 'none',
          lineStyle: { color: '#d97706', width: 2 },
          areaStyle: { color: 'rgba(217,119,6,0.1)' },
        }], { title: { text: '铜价走势', textStyle: { color: '#9ca3af', fontSize: 12 }, left: 10, top: 0 } });
      }
    }

    // 原油
    if (data.crude_oil?.history?.length) {
      lineChart('chart-crude', data.crude_oil.history.map(d => d.date), [{
        name: 'Brent 原油',
        type: 'line',
        data: data.crude_oil.history.map(d => d.value ?? d.price ?? d.brent),
        smooth: true, symbol: 'none',
        lineStyle: { color: '#78716c', width: 2 },
        areaStyle: { color: 'rgba(120,113,108,0.1)' },
      }], { title: { text: '原油价格走势', textStyle: { color: '#9ca3af', fontSize: 12 }, left: 10, top: 0 } });
    }
  }

  /** 渲染估值仪表盘 */
  function renderGauges(data) {
    if (!data) return;
    if (data.us_stock?.shiller_pe?.percentile != null) {
      renderGauge('gauge-shiller', data.us_stock.shiller_pe.percentile, 'Shiller PE');
    }
    if (data.us_stock?.forward_pe?.percentile != null) {
      renderGauge('gauge-fwdpe', data.us_stock.forward_pe.percentile, 'Fwd PE');
    }
    if (data.cn_stock?.hs300_pe?.percentile != null) {
      renderGauge('gauge-hs300pe', data.cn_stock.hs300_pe.percentile, 'HS300 PE');
    }
    if (data.cn_stock?.hs300_pb?.percentile != null) {
      renderGauge('gauge-hs300pb', data.cn_stock.hs300_pb.percentile, 'HS300 PB');
    }
  }

  /** 主入口 */
  function render(data) {
    if (!data) return;
    renderUSStock(data.us_stock);
    renderCNStock(data.cn_stock);
    renderUSBond(data.us_bond);
    renderGold(data.gold);
    renderCommodity(data.commodity);
    renderGauges(data);
  }

  /** 销毁图表 */
  function dispose() {
    Object.values(charts).forEach(c => c.dispose());
  }

  return { render, dispose };
})();

// 窗口resize时自动调整图表大小
window.addEventListener('resize', () => {
  Object.values(ValuationModule?.charts || {}).forEach(c => c?.resize?.());
});
