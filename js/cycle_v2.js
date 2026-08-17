/**
 * cycle_v2.js - 宏观经济周期看板 V2 渲染模块
 * 替换原有 cycle.js，提供更丰富的可视化效果
 * 
 * 数据结构: cycle_position_v2.json
 */

const CycleV2Module = (() => {
  let chartInstances = [];
  let resizeObserver = null;

  // ========== Color Constants ==========
  const COLORS = {
    bullish: '#10b981',
    bearish: '#ef4444',
    neutral: '#f59e0b',
    blue: '#3b82f6',
    purple: '#8b5cf6',
    cyan: '#06b6d4',
    textPrimary: '#e2e8f0',
    textSecondary: '#94a3b8',
    textMuted: '#64748b',
    bgCard: '#111827',
    borderSubtle: '#1e293b',
    tooltipBg: '#1e293b',
    tooltipBorder: '#334155',
  };

  // ========== Common ECharts Tooltip ==========
  function tooltipConfig() {
    return {
      backgroundColor: COLORS.tooltipBg,
      borderColor: COLORS.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: COLORS.textPrimary, fontSize: 12, fontFamily: 'JetBrains Mono' },
      padding: [8, 12],
    };
  }

  // ========== Common ECharts Grid ==========
  function gridConfig(extra = {}) {
    return Object.assign({ top: 36, right: 16, bottom: 28, left: 16, containLabel: true }, extra);
  }

  // ========== Chart Utilities ==========
  function createChart(containerId, option) {
    const container = document.getElementById(containerId);
    if (!container) return null;
    const chart = echarts.init(container, null, { renderer: 'canvas' });
    chart.setOption(option);
    chartInstances.push(chart);
    return chart;
  }

  function disposeAll() {
    chartInstances.forEach(c => { try { c.dispose(); } catch(e) {} });
    chartInstances = [];
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }
  }

  function setupResizeObserver() {
    if (resizeObserver) resizeObserver.disconnect();
    resizeObserver = new ResizeObserver(() => {
      chartInstances.forEach(c => { try { c.resize(); } catch(e) {} });
    });
    chartInstances.forEach(c => {
      if (c.getDom()) resizeObserver.observe(c.getDom());
    });
  }

  // ========== Signal Helpers ==========
  function signalToText(signal) {
    const map = { bullish: '看多', bearish: '看空', neutral: '中性', neutral_bullish: '中性偏多', neutral_bearish: '中性偏空', mixed: '分化', pending: '待更新' };
    return map[signal] || signal || '--';
  }

  function signalToColor(signal) {
    if (!signal) return COLORS.neutral;
    if (signal === 'bullish' || signal === 'neutral_bullish') return COLORS.bullish;
    if (signal === 'bearish' || signal === 'neutral_bearish') return COLORS.bearish;
    return COLORS.neutral;
  }

  function signalToClass(signal) {
    if (!signal) return 'neutral';
    if (signal === 'bullish') return 'bullish';
    if (signal === 'bearish') return 'bearish';
    return 'neutral';
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ========== Section 0: Overview ==========
  function renderOverviewHTML(data) {
    const summary = data?.synthesis?.cycle_positions_summary || [];
    if (!summary.length) return '';

    let cards = '';
    summary.forEach(item => {
      const sigClass = signalToClass(item.signal);
      const sigColor = signalToColor(item.signal);
      cards += `
        <div class="cycle-overview-card signal-${sigClass}">
          <div class="cycle-name">${escapeHtml(item.name || item.cycle || '')}</div>
          <div class="cycle-position" style="color:${sigColor}">${escapeHtml(item.position || item.phase || '--')}</div>
          <div class="cycle-signal ${sigClass}">${signalToText(item.signal)}</div>
        </div>`;
    });

    return `
      <div class="cycle-section" id="section-overview">
        <div class="cycle-section-title">
          <span class="section-icon">🎯</span>
          <h2>综合速览</h2>
          <span class="section-desc">九大周期维度当前位置与信号</span>
        </div>
        <div class="cycle-overview-grid">${cards}</div>
      </div>`;
  }

  // ========== Section 1: Unified Timeline ==========
  function renderTimelineHTML(data) {
    return `
      <div class="cycle-section" id="section-timeline">
        <div class="cycle-section-title">
          <span class="section-icon">📅</span>
          <h2>统一时间轴</h2>
          <span class="section-desc">1991-2026 多周期叠加视图</span>
        </div>
        <div class="cycle-timeline-container" id="chart-timeline"></div>
      </div>`;
  }

  function renderTimelineChart(data) {
    const timeline = data?.unified_timeline;
    if (!timeline || !timeline.cycles) return;

    const cycles = timeline.cycles;
    const categories = cycles.map(c => c.name);

    // Build data items for custom series (gantt bars)
    const ganttData = [];
    const colorMap = {
      '扩张': COLORS.bullish, '回升': COLORS.bullish, '复苏': '#22c55e',
      '衰退': COLORS.bearish, '收缩': COLORS.bearish, '萧条': '#dc2626',
      '高峰': COLORS.neutral, '峰值': COLORS.neutral,
      '谷底': '#a78bfa', '触底': '#a78bfa',
      '繁荣': '#22c55e', '安装期': COLORS.purple, '协同期': COLORS.cyan,
      '破裂期': COLORS.bearish, '酝酿期': '#6366f1',
    };

    cycles.forEach((cycle, ci) => {
      if (cycle.phases) {
        cycle.phases.forEach(phase => {
          const start = typeof phase.start === 'number' ? phase.start : new Date(phase.start).getFullYear();
          const end = typeof phase.end === 'number' ? phase.end : (phase.end ? new Date(phase.end).getFullYear() : 2026);
          // Use explicit color from data first, then fallback to colorMap
          const color = phase.color || colorMap[phase.name] || colorMap[phase.phase] || COLORS.blue;
          ganttData.push({
            value: [ci, start, end, phase.name || phase.phase],
            itemStyle: { color: color, opacity: 0.8 }
          });
        });
      }
    });

    const option = {
      tooltip: Object.assign(tooltipConfig(), {
        formatter: function(params) {
          const d = params.data;
          if (!d || !d.value) return '';
          const catIdx = d.value[0];
          const catName = cycles[catIdx]?.name || '';
          return `<div style="font-size:11px;color:#64748b">${catName}</div>
                  <div style="font-weight:600;color:#e2e8f0;margin-top:4px">${d.value[3]}</div>
                  <div style="font-size:11px;color:#94a3b8;margin-top:2px">${d.value[1]} - ${d.value[2]}</div>`;
        }
      }),
      grid: { top: 24, right: 40, bottom: 44, left: 130 },
      xAxis: {
        type: 'value',
        min: 1990,
        max: 2027,
        axisLine: { lineStyle: { color: COLORS.borderSubtle } },
        axisLabel: { color: COLORS.textMuted, fontSize: 11, formatter: '{value}', margin: 12 },
        splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { 
          color: COLORS.textSecondary, 
          fontSize: 11, 
          fontWeight: 600,
          margin: 8,
        },
      },
      series: [
        {
          type: 'custom',
          renderItem: function(params, api) {
            const catIdx = api.value(0);
            const start = api.coord([api.value(1), catIdx]);
            const end = api.coord([api.value(2), catIdx]);
            const height = api.size([0, 1])[1] * 0.5;
            const rectShape = echarts.graphic.clipRectByRect({
              x: start[0],
              y: start[1] - height / 2,
              width: end[0] - start[0],
              height: height,
            }, { x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height });
            return rectShape && {
              type: 'rect',
              transition: ['shape'],
              shape: rectShape,
              style: api.style(),
            };
          },
          encode: { x: [1, 2], y: 0 },
          data: ganttData,
        },
        // Current time marker
        {
          type: 'line',
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#f97316', width: 2.5, type: 'dashed' },
            label: { 
              show: true, 
              formatter: function() { return '▼ 当前'; },
              color: '#f97316', 
              fontSize: 11, 
              fontWeight: 700,
              position: 'insideEndTop' 
            },
            data: [{ xAxis: timeline.current_year_mark || 2026 }]
          },
          data: []
        }
      ]
    };

    createChart('chart-timeline', option);
  }

  // ========== Section 2: Kongbo (Kondratieff Wave) ==========
  function renderKongboHTML(data) {
    const kb = data?.kongbo;
    if (!kb) return '';

    const phaseName = kb.current_phase || kb.phase || '数据待更新';
    const confidence = kb.confidence || 'medium';
    const confMap = { high: '高', medium: '中', low: '低' };
    const usage = kb.investment_usage || kb.usage || '';
    const advice = kb.investment_advice || kb.advice || [];

    // History timeline
    const history = kb.history || [];
    let historyTags = '';
    history.forEach(h => {
      const isCurrent = !h.end || h.current;
      const endStr = h.end || '至今';
      historyTags += `<span class="kongbo-timeline-tag ${isCurrent ? 'current' : ''}">
        第${h.round || '?'}轮 ${h.name || h.phase || ''} (${h.start || '?'}-${endStr})
      </span>`;
    });

    // Indicators
    const indicators = kb.indicators || {};
    let indicatorCards = '';
    const indicatorList = [
      { key: 'tfp_growth', name: 'TFP增速（中美对比）', color: COLORS.blue },
      { key: 'gdp_10y_avg', name: 'GDP 10年均速（中美对比）', color: COLORS.purple },
      { key: 'capital_deepening', name: '资本深化（中美对比）', color: COLORS.cyan },
    ];

    indicatorList.forEach(ind => {
      const indData = indicators[ind.key];
      indicatorCards += `
        <div class="cycle-indicator-card">
          <div class="indicator-title">${ind.name}</div>
          <div class="indicator-chart" id="chart-kongbo-${ind.key}"></div>
        </div>`;
    });

    // Evidence
    const evidence = kb.key_evidence || kb.evidence || [];
    let evidenceHTML = '';
    if (evidence.length) {
      evidenceHTML = '<div class="cycle-evidence-list mt-4">' +
        evidence.map(e => `<div class="cycle-evidence-item"><span class="ev-dot"></span><span>${escapeHtml(e)}</span></div>`).join('') +
        '</div>';
    }

    return `
      <div class="cycle-section" id="section-kongbo">
        <div class="cycle-section-title">
          <span class="section-icon">🌊</span>
          <h2>康德拉季耶夫长波</h2>
          <span class="section-desc">~50-60年技术创新周期</span>
        </div>
        <div class="kongbo-layout">
          <div class="kongbo-phase-display">
            <div class="phase-name">${escapeHtml(phaseName)}</div>
            <span class="confidence-badge">置信度: ${confMap[confidence] || confidence}</span>
            ${evidenceHTML}
          </div>
          <div class="kongbo-invest-panel">
            ${usage ? `<div class="cycle-advice-box">
              <div class="advice-title">💡 投资用途</div>
              <p style="font-size:12px;color:#94a3b8;line-height:1.6">${escapeHtml(usage)}</p>
            </div>` : ''}
            ${advice.length ? `<div class="cycle-advice-box">
              <div class="advice-title">📋 投资建议</div>
              <ul class="advice-list">${advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
            </div>` : ''}
          </div>
        </div>
        <div class="cycle-indicator-grid">${indicatorCards}</div>
        ${history.length ? `<div style="margin-top:20px">
          <div style="font-size:13px;font-weight:600;color:#94a3b8;margin-bottom:8px">📜 历史康波周期</div>
          <div class="kongbo-timeline-bar">${historyTags}</div>
        </div>` : ''}
      </div>`;
  }

  function renderKongboCharts(data) {
    const kb = data?.kongbo;
    if (!kb) return;
    const indicators = kb.indicators || {};

    const indicatorConfigs = [
      { key: 'tfp_growth', series: ['us', 'cn'], names: ['美国TFP', '中国TFP'], colors: [COLORS.blue, COLORS.bearish] },
      { key: 'gdp_10y_avg', series: ['us', 'cn'], names: ['美国GDP', '中国GDP'], colors: [COLORS.blue, COLORS.bearish] },
      { key: 'capital_deepening', series: ['us', 'cn'], names: ['美国资本深化', '中国资本深化'], colors: [COLORS.blue, COLORS.bearish] },
    ];

    indicatorConfigs.forEach(cfg => {
      const indData = indicators[cfg.key];
      if (!indData) return;

      const seriesData = [];
      cfg.series.forEach((region, i) => {
        const regionObj = indData[region] || indData[cfg.names[i]] || null;
        if (!regionObj) return;
        // Handle both {history: [{date, value}]} and raw array formats
        const regionData = regionObj.history || (Array.isArray(regionObj) ? regionObj : null);
        if (regionData && regionData.length) {
          seriesData.push({
            name: cfg.names[i],
            type: 'line',
            data: regionData.map(d => [d.date || d.year || d.x, d.value || d.y]),
            smooth: 0.3,
            symbol: 'none',
            lineStyle: { width: 2, color: cfg.colors[i] },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: cfg.colors[i] + '25' },
                { offset: 1, color: cfg.colors[i] + '00' },
              ])
            },
          });
        }
      });

      if (seriesData.length) {
        createChart(`chart-kongbo-${cfg.key}`, {
          tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
          legend: { show: true, top: 0, textStyle: { color: COLORS.textSecondary, fontSize: 10 }, icon: 'roundRect', itemWidth: 12, itemHeight: 3 },
          grid: gridConfig({ top: 32 }),
          xAxis: { type: 'time', axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { show: false } },
          yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
          series: seriesData,
        });
      }
    });
  }

  // ========== Section 3: Perez (Technological Revolution) ==========
  function renderPerezHTML(data) {
    const pz = data?.perez;
    if (!pz) return '';

    const stageName = pz.current_stage || pz.phase || '数据待更新';
    const usage = pz.investment_usage || pz.usage || '';
    const advice = pz.investment_advice || pz.advice || [];
    const financialSignals = pz.financial_signals || pz.financial_indicators || [];
    const realSignals = pz.real_signals || pz.real_indicators || [];
    const indicators = pz.indicators || {};

    let signalItems = '';
    financialSignals.forEach(s => {
      signalItems += `<div class="perez-signal-item"><span class="signal-dot financial"></span><span>${escapeHtml(s)}</span></div>`;
    });
    realSignals.forEach(s => {
      signalItems += `<div class="perez-signal-item"><span class="signal-dot real"></span><span>${escapeHtml(s)}</span></div>`;
    });

    // Indicator cards
    const indicatorDefs = [
      { key: 'vc_pe_funding', name: 'VC/PE融资额' },
      { key: 'ipo_density', name: 'IPO数量' },
      { key: 'passive_fund_ratio', name: '被动基金占比' },
      { key: 'hy_spread', name: '高收益债利差' },
      { key: 'yield_curve', name: '利率曲线' },
    ];

    let indicatorCards = '';
    indicatorDefs.forEach(def => {
      indicatorCards += `
        <div class="cycle-indicator-card">
          <div class="indicator-title">${def.name}</div>
          <div class="indicator-chart ${def.tall ? 'tall' : ''}" id="chart-perez-${def.key}"></div>
        </div>`;
    });

    return `
      <div class="cycle-section" id="section-perez">
        <div class="cycle-section-title">
          <span class="section-icon">🔬</span>
          <h2>佩雷斯技术革命周期</h2>
          <span class="section-desc">~40-60年技术革命与金融资本周期</span>
        </div>
        <div class="perez-layout">
          <div class="perez-stage-card">
            <div class="stage-name">${escapeHtml(stageName)}</div>
            <div class="perez-signal-list">${signalItems}</div>
          </div>
          <div class="kongbo-invest-panel">
            ${usage ? `<div class="cycle-advice-box">
              <div class="advice-title">💡 投资用途</div>
              <p style="font-size:12px;color:#94a3b8;line-height:1.6">${escapeHtml(usage)}</p>
            </div>` : ''}
            ${advice.length ? `<div class="cycle-advice-box">
              <div class="advice-title">📋 投资建议</div>
              <ul class="advice-list">${advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
            </div>` : ''}
          </div>
        </div>
        <div class="cycle-indicator-grid">${indicatorCards}</div>
        <div id="perez-descriptions"></div>
      </div>`;
  }

  function renderPerezCharts(data) {
    const pz = data?.perez;
    if (!pz) return;
    const indicators = pz.indicators || {};

    const indicatorConfigs = [
      { key: 'vc_pe_funding' },
      { key: 'ipo_density' },
      { key: 'passive_fund_ratio' },
      { key: 'hy_spread' },
      { key: 'yield_curve' },
    ];

    indicatorConfigs.forEach(cfg => {
      const indData = indicators[cfg.key];
      if (!indData) return;
      const hist = indData.data || indData.history || (Array.isArray(indData) ? indData : []);
      if (!hist || !hist.length) return;

      const series = [{
        type: 'line',
        data: hist.map(d => [d.date || d.year || d.x, d.value || d.y]),
        smooth: 0.3,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { width: 2.5, color: COLORS.purple },
        areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:COLORS.purple+'25'},{offset:1,color:COLORS.purple+'00'}]) },
      }];

      const markLine = cfg.thresholdLines && indData.thresholds ? {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', width: 1 },
        label: { fontSize: 10 },
        data: (indData.thresholds || []).map(t => ({
          yAxis: t.value,
          lineStyle: { color: t.color || COLORS.neutral },
          label: { formatter: t.label || '', color: t.color || COLORS.neutral }
        }))
      } : null;

      if (markLine) series[0].markLine = markLine;

      createChart(`chart-perez-${cfg.key}`, {
        tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
        grid: gridConfig(),
        xAxis: { type: 'category', data: hist.map(d => d.date || d.year || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 10, rotate: hist.length > 8 ? 30 : 0 } },
        yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
        series: series,
      });
    });
  }

  // ========== Section 4: Juglar (Equipment Investment) ==========
  function renderJuglarHTML(data) {
    const juglar = data?.juglar;
    if (!juglar) return '';

    function renderRegion(region, flag, label) {
      const d = juglar[region];
      if (!d) return `<div class="cycle-region-card"><div class="region-header"><span class="region-flag">${flag}</span><span class="region-title">${label}</span></div><p style="color:#64748b;font-size:13px">数据待更新</p></div>`;

      const phase = d.phase || d.current_phase || '未知';
      const signal = d.signal || 'neutral';
      const phaseColor = signalToColor(signal);
      const start = d.start || d.started || '';
      const end = d.expected_end || d.end_estimate || '';
      const usage = d.investment_usage || d.usage || '';
      const advice = d.investment_advice || d.advice || [];

      return `
        <div class="cycle-region-card">
          <div class="region-header">
            <span class="region-flag">${flag}</span>
            <span class="region-title">${label}</span>
          </div>
          <div class="region-phase" style="color:${phaseColor}">${escapeHtml(phase)}</div>
          <div class="region-meta">${start ? '起始: ' + escapeHtml(start) : ''}${end ? ' · 预计结束: ' + escapeHtml(end) : ''}</div>
          <div class="cycle-position-gauge" id="chart-juglar-gauge-${region}"></div>
          ${usage ? `<div class="cycle-advice-box mt-3">
            <div class="advice-title">💡 投资用途</div>
            <p style="font-size:12px;color:#94a3b8;line-height:1.6">${escapeHtml(usage)}</p>
          </div>` : ''}
          ${advice.length ? `<div class="cycle-advice-box">
            <div class="advice-title">📋 投资建议</div>
            <ul class="advice-list">${advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
          </div>` : ''}
          <div class="cycle-indicator-grid grid-2 mt-4">
            <div class="cycle-indicator-card">
              <div class="indicator-title">产能利用率</div>
              <div class="indicator-chart" id="chart-juglar-capacity-${region}"></div>
            </div>
            <div class="cycle-indicator-card">
              <div class="indicator-title">设备投资增速</div>
              <div class="indicator-chart" id="chart-juglar-equip-${region}"></div>
            </div>
          </div>
        </div>`;
    }

    return `
      <div class="cycle-section" id="section-juglar">
        <div class="cycle-section-title">
          <span class="section-icon">⚙️</span>
          <h2>朱格拉设备投资周期</h2>
          <span class="section-desc">~8-10年产能投资周期</span>
        </div>
        <div class="cycle-dual-region">
          ${renderRegion('us', '🇺🇸', '美国')}
          ${renderRegion('cn', '🇨🇳', '中国')}
        </div>
      </div>`;
  }

  function renderJuglarCharts(data) {
    const juglar = data?.juglar;
    if (!juglar) return;

    ['us', 'cn'].forEach(region => {
      const d = juglar[region];
      if (!d) return;

      // Gauge chart
      const phaseProgress = d.phase_progress || d.progress || 0.5;
      const signal = d.signal || 'neutral';
      const gaugeColor = signalToColor(signal);

      createChart(`chart-juglar-gauge-${region}`, {
        series: [{
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 1,
          radius: '100%',
          center: ['50%', '75%'],
          splitNumber: 5,
          axisLine: {
            lineStyle: {
              width: 14,
              color: [
                [0.25, COLORS.bullish],
                [0.5, COLORS.neutral],
                [0.75, COLORS.bearish],
                [1, COLORS.purple],
              ]
            }
          },
          pointer: {
            icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
            length: '55%',
            width: 8,
            offsetCenter: [0, '-10%'],
            itemStyle: { color: gaugeColor }
          },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          title: { show: true, offsetCenter: [0, '20%'], fontSize: 13, color: COLORS.textSecondary, fontWeight: 600 },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, '-5%'],
            fontSize: 20,
            fontWeight: 800,
            color: gaugeColor,
            formatter: function(val) { return (val * 100).toFixed(0) + '%'; }
          },
          data: [{ value: phaseProgress, name: d.phase || '' }]
        }]
      });

      // Indicator charts
      const indicators = d.indicators || {};

      // Capacity utilization
      const capRaw = indicators.capacity_utilization || indicators.cap_util || null;
      const capData = capRaw?.history || (Array.isArray(capRaw) ? capRaw : null);
      if (capData && capData.length) {
        createChart(`chart-juglar-capacity-${region}`, {
          tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
          grid: gridConfig(),
          xAxis: { type: 'category', data: capData.map(d => d.date || d.period || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(capData.length / 4) } },
          yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
          series: [{ type: 'line', data: capData.map(d => d.value), smooth: 0.3, symbol: 'none', lineStyle: { width: 2, color: region === 'us' ? COLORS.blue : COLORS.bearish }, areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:(region==='us'?COLORS.blue:COLORS.bearish)+'25'},{offset:1,color:(region==='us'?COLORS.blue:COLORS.bearish)+'00'}]) } }],
        });
      }

      // Equipment investment
      const equipRaw = indicators.equipment_investment || indicators.equip_invest || null;
      const equipData = equipRaw?.history || (Array.isArray(equipRaw) ? equipRaw : null);
      if (equipData && equipData.length) {
        createChart(`chart-juglar-equip-${region}`, {
          tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
          grid: gridConfig(),
          xAxis: { type: 'category', data: equipData.map(d => d.date || d.period || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(equipData.length / 4) } },
          yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
          series: [{ type: 'line', data: equipData.map(d => d.value), smooth: 0.3, symbol: 'none', lineStyle: { width: 2, color: COLORS.cyan }, areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:COLORS.cyan+'25'},{offset:1,color:COLORS.cyan+'00'}]) } }],
        });
      }
    });
  }

  // ========== Section 5: Kitchin (Inventory Cycle) ==========
  function renderKitchinHTML(data) {
    const kitchin = data?.kitchin;
    if (!kitchin) return '';

    function renderRegion(region, flag, label) {
      const d = kitchin[region];
      if (!d) return `<div class="cycle-region-card"><div class="region-header"><span class="region-flag">${flag}</span><span class="region-title">${label}</span></div><p style="color:#64748b;font-size:13px">数据待更新</p></div>`;

      const phase = d.phase || d.current_phase || '未知';
      const signal = d.signal || 'neutral';
      const phaseColor = signalToColor(signal);
      const usage = d.investment_usage || d.usage || '';
      const advice = d.investment_advice || d.advice || [];

      return `
        <div class="cycle-region-card">
          <div class="region-header">
            <span class="region-flag">${flag}</span>
            <span class="region-title">${label}</span>
          </div>
          <div class="region-phase" style="color:${phaseColor}">${escapeHtml(phase)}</div>
          <div class="kitchin-wheel" id="chart-kitchin-wheel-${region}"></div>
          ${usage ? `<div class="cycle-advice-box mt-3">
            <div class="advice-title">💡 投资用途</div>
            <p style="font-size:12px;color:#94a3b8;line-height:1.6">${escapeHtml(usage)}</p>
          </div>` : ''}
          ${advice.length ? `<div class="cycle-advice-box">
            <div class="advice-title">📋 投资建议</div>
            <ul class="advice-list">${advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
          </div>` : ''}
          <div class="cycle-indicator-grid mt-4">
            <div class="cycle-indicator-card">
              <div class="indicator-title">${region === 'cn' ? '工业企业库存增速' : '库存/销售比'}</div>
              <div class="indicator-chart" id="chart-kitchin-inv-sales-${region}"></div>
            </div>
            <div class="cycle-indicator-card">
              <div class="indicator-title">${region === 'cn' ? 'PMI新订单指数' : 'ISM新订单'}</div>
              <div class="indicator-chart" id="chart-kitchin-pmi-new-${region}"></div>
            </div>
            <div class="cycle-indicator-card">
              <div class="indicator-title">${region === 'cn' ? 'PPI同比' : 'PMI库存差'}</div>
              <div class="indicator-chart" id="chart-kitchin-pmi-diff-${region}"></div>
            </div>
          </div>
          <div id="kitchin-${region}-descriptions"></div>
        </div>`;
    }

    return `
      <div class="cycle-section" id="section-kitchin">
        <div class="cycle-section-title">
          <span class="section-icon">📦</span>
          <h2>基钦库存周期</h2>
          <span class="section-desc">~3-4年库存波动周期</span>
        </div>
        <div class="cycle-dual-region">
          ${renderRegion('us', '🇺🇸', '美国')}
          ${renderRegion('cn', '🇨🇳', '中国')}
        </div>
      </div>`;
  }

  function renderKitchinCharts(data) {
    const kitchin = data?.kitchin;
    if (!kitchin) return;

    const phases = ['被动去库', '主动补库', '被动补库', '主动去库'];
    const phaseAngles = [
      { start: -45, end: 45 },    // 被动去库 (top-right)
      { start: 45, end: 135 },    // 主动补库 (top-left)
      { start: 135, end: 225 },   // 被动补库 (bottom-left)
      { start: 225, end: 315 },   // 主动去库 (bottom-right)
    ];
    const phaseColorsArr = [COLORS.bullish, COLORS.blue, COLORS.neutral, COLORS.bearish];

    ['us', 'cn'].forEach(region => {
      const d = kitchin[region];
      if (!d) return;

      const currentPhaseIdx = d.current_phase_index || d.phase_index || 0;
      const currentPhase = phases[currentPhaseIdx] || d.phase || '未知';

      // Kitchin wheel - circular diagram
      const wheelData = phases.map((name, i) => ({
        value: 25,
        name: name,
        itemStyle: {
          color: i === currentPhaseIdx ? phaseColorsArr[i] : phaseColorsArr[i] + '40',
          borderColor: '#0a1628',
          borderWidth: 2,
        },
        label: {
          show: true,
          color: i === currentPhaseIdx ? '#fff' : COLORS.textMuted,
          fontSize: i === currentPhaseIdx ? 13 : 11,
          fontWeight: i === currentPhaseIdx ? 800 : 400,
        }
      }));

      createChart(`chart-kitchin-wheel-${region}`, {
        tooltip: Object.assign(tooltipConfig(), {
          formatter: function(p) { return p.name + (phases.indexOf(p.name) === currentPhaseIdx ? ' ← 当前' : ''); }
        }),
        series: [{
          type: 'pie',
          radius: ['45%', '75%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: true,
          startAngle: 135,
          label: {
            show: true,
            position: 'inside',
            formatter: '{b}',
          },
          emphasis: {
            scaleSize: 6,
          },
          data: wheelData,
        },
        // Center text
        {
          type: 'pie',
          radius: ['0', '35%'],
          center: ['50%', '50%'],
          silent: true,
          label: {
            show: true,
            position: 'center',
            formatter: function() { return '📍\n' + currentPhase; },
            fontSize: 14,
            fontWeight: 700,
            color: phaseColorsArr[currentPhaseIdx],
            lineHeight: 22,
          },
          data: [{ value: 1, itemStyle: { color: 'transparent' } }],
        }]
      });

      // Indicator charts
      const indicators = d.indicators || {};

      const chartConfigs = [
        { key: region === 'cn' ? 'inventory_stock' : 'inventory_to_sales', id: `chart-kitchin-inv-sales-${region}`, color: COLORS.blue },
        { key: region === 'cn' ? 'pmi_new_orders' : 'ism_new_orders', id: `chart-kitchin-pmi-new-${region}`, color: COLORS.bullish },
        { key: region === 'cn' ? 'ppi_yoy' : 'pmi_inventory_diff', id: `chart-kitchin-pmi-diff-${region}`, color: COLORS.purple },
      ];

      chartConfigs.forEach(cfg => {
        const raw = indicators[cfg.key];
        if (!raw) return;
        const indData = Array.isArray(raw) ? raw : (raw.history || raw.data || []);
        if (!indData || !indData.length) return;

        createChart(cfg.id, {
          tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
          grid: gridConfig(),
          xAxis: { type: 'category', data: indData.map(dd => dd.date || dd.period || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(indData.length / 4) } },
          yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
          series: [{ type: 'line', data: indData.map(dd => dd.value), smooth: 0.3, symbol: 'circle', symbolSize: 4, lineStyle: { width: 2.5, color: cfg.color }, areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:cfg.color+'25'},{offset:1,color:cfg.color+'00'}]) } }],
        });
      });
    });
  }

  // ========== Section 6: Merrill Clock ==========
  function renderMerrillClockHTML(data) {
    const mc = data?.merrill_clock;
    if (!mc) return '';

    function renderRegion(region, flag, label) {
      const d = mc[region];
      if (!d) return `<div class="cycle-region-card"><div class="region-header"><span class="region-flag">${flag}</span><span class="region-title">${label}</span></div><p style="color:#64748b;font-size:13px">数据待更新</p></div>`;

      const quadrant = d.current_quadrant || d.quadrant || '未知';
      const signal = d.signal || 'neutral';
      const phaseColor = signalToColor(signal);
      const history = d.quadrant_history || d.history || [];
      const usage = d.investment_usage || d.usage || '';
      const advice = d.investment_advice || d.advice || [];

      let historyHTML = '';
      if (history.length) {
        historyHTML = `<div style="margin-top:12px;font-size:11px;color:#64748b">
          <span style="font-weight:600;color:#94a3b8">象限变化: </span>
          ${history.map(h => `<span style="margin-right:8px">${escapeHtml(h.period || h.date || '')}: ${escapeHtml(h.quadrant || h.phase || '')}</span>`).join('→')}
        </div>`;
      }

      return `
        <div class="cycle-region-card">
          <div class="region-header">
            <span class="region-flag">${flag}</span>
            <span class="region-title">${label}</span>
          </div>
          <div class="region-phase" style="color:${phaseColor}">${escapeHtml(quadrant)}</div>
          <div class="merrill-quadrant" id="chart-merrill-${region}"></div>
          ${historyHTML}
          ${usage ? `<div class="cycle-advice-box mt-3">
            <div class="advice-title">💡 投资用途</div>
            <p style="font-size:12px;color:#94a3b8;line-height:1.6">${escapeHtml(usage)}</p>
          </div>` : ''}
          ${advice.length ? `<div class="cycle-advice-box">
            <div class="advice-title">📋 投资建议</div>
            <ul class="advice-list">${advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
          </div>` : ''}
          <div class="cycle-indicator-grid grid-2 mt-4">
            <div class="cycle-indicator-card">
              <div class="indicator-title">产出缺口</div>
              <div class="indicator-chart" id="chart-merrill-output-${region}"></div>
            </div>
            <div class="cycle-indicator-card">
              <div class="indicator-title">${region === 'cn' ? '核心CPI' : '核心PCE'}</div>
              <div class="indicator-chart" id="chart-merrill-cpi-${region}"></div>
            </div>
            <div class="cycle-indicator-card">
              <div class="indicator-title">PPI同比</div>
              <div class="indicator-chart" id="chart-merrill-ppi-${region}"></div>
            </div>
            ${region === 'us' ? `<div class="cycle-indicator-card">
              <div class="indicator-title">Breakeven通胀</div>
              <div class="indicator-chart" id="chart-merrill-breakeven-${region}"></div>
            </div>` : ''}
          </div>
          <div id="merrill-${region}-descriptions"></div>
        </div>`;
    }

    return `
      <div class="cycle-section" id="section-merrill">
        <div class="cycle-section-title">
          <span class="section-icon">🕐</span>
          <h2>美林时钟</h2>
          <span class="section-desc">增长 × 通胀四象限配置模型</span>
        </div>
        <div class="cycle-dual-region">
          ${renderRegion('us', '🇺🇸', '美国')}
          ${renderRegion('cn', '🇨🇳', '中国')}
        </div>
      </div>`;
  }

  function renderMerrillClockCharts(data) {
    const mc = data?.merrill_clock;
    if (!mc) return;

    const quadrants = [
      { name: '复苏\n增长↑ 通胀↓', x: [-1, 1], y: [0, 1], color: COLORS.bullish + '20', border: COLORS.bullish },
      { name: '过热\n增长↑ 通胀↑', x: [0, 1], y: [0, 1], color: COLORS.neutral + '20', border: COLORS.neutral },
      { name: '滞胀\n增长↓ 通胀↑', x: [-1, 0], y: [0, 1], color: COLORS.bearish + '20', border: COLORS.bearish },
      { name: '衰退\n增长↓ 通胀↓', x: [-1, 0], y: [-1, 0], color: COLORS.blue + '20', border: COLORS.blue },
    ];

    // Quadrant mapping based on data
    const quadrantPositions = {
      '复苏': [0.5, 0.5], 'recovery': [0.5, 0.5],
      '过热': [0.5, 0.5], 'overheat': [0.5, 0.5],
      '滞胀': [-0.5, 0.5], 'stagflation': [-0.5, 0.5],
      '衰退': [-0.5, -0.5], 'recession': [-0.5, -0.5],
    };

    ['us', 'cn'].forEach(region => {
      const d = mc[region];
      if (!d) return;

      const currentQ = d.current_quadrant || d.quadrant || '复苏';
      // Determine current position
      let currentPos = [0.5, 0.5];
      if (currentQ.includes('复苏') || currentQ.toLowerCase().includes('recovery')) currentPos = [0.5, 0.5];
      else if (currentQ.includes('过热') || currentQ.toLowerCase().includes('overheat')) currentPos = [0.5, 0.5];
      else if (currentQ.includes('滞胀') || currentQ.toLowerCase().includes('stagflation')) currentPos = [-0.5, 0.5];
      else if (currentQ.includes('衰退') || currentQ.toLowerCase().includes('recession')) currentPos = [-0.5, -0.5];

      const qColors = [COLORS.bullish, COLORS.neutral, COLORS.bearish, COLORS.blue];
      const qNames = ['复苏\n增长↑ 通胀↓', '过热\n增长↑ 通胀↑', '滞胀\n增长↓ 通胀↑', '衰退\n增长↓ 通胀↓'];
      const qAreas = [
        { x: 0, y: 0, width: 1, height: 1 },  // top-right: 复苏
        { x: 0, y: 0, width: 1, height: 1 },  // top-left: 过热 (will be mapped differently)
      ];

      createChart(`chart-merrill-${region}`, {
        tooltip: Object.assign(tooltipConfig(), { trigger: 'item' }),
        grid: { top: 30, right: 30, bottom: 30, left: 40 },
        xAxis: {
          type: 'value', min: -1, max: 1,
          name: '增长 →', nameLocation: 'middle', nameGap: 20,
          nameTextStyle: { color: COLORS.textSecondary, fontSize: 12 },
          axisLine: { lineStyle: { color: COLORS.borderSubtle } },
          axisLabel: { show: false },
          splitLine: { lineStyle: { color: 'rgba(30,41,59,0.3)', type: 'dashed' } },
        },
        yAxis: {
          type: 'value', min: -1, max: 1,
          name: '通胀 →', nameLocation: 'middle', nameGap: 28,
          nameTextStyle: { color: COLORS.textSecondary, fontSize: 12 },
          axisLine: { lineStyle: { color: COLORS.borderSubtle } },
          axisLabel: { show: false },
          splitLine: { lineStyle: { color: 'rgba(30,41,59,0.3)', type: 'dashed' } },
        },
        series: [{
          type: 'scatter',
          symbolSize: 20,
          data: [[currentPos[0], currentPos[1]]],
          itemStyle: { color: signalToColor(d.signal || 'neutral'), shadowBlur: 12, shadowColor: signalToColor(d.signal || 'neutral') + '60' },
          z: 10,
          markArea: {
            silent: true,
            data: [
              [{ coord: [0, 0], itemStyle: { color: COLORS.bullish + '10' }, label: { show: true, position: 'insideTopRight', formatter: '复苏', color: COLORS.bullish, fontSize: 12, fontWeight: 600 } }, { coord: [1, 1] }],
              [{ coord: [-1, 0], itemStyle: { color: COLORS.neutral + '10' }, label: { show: true, position: 'insideTopLeft', formatter: '过热', color: COLORS.neutral, fontSize: 12, fontWeight: 600 } }, { coord: [0, 1] }],
              [{ coord: [-1, -1], itemStyle: { color: COLORS.bearish + '10' }, label: { show: true, position: 'insideBottomLeft', formatter: '滞胀', color: COLORS.bearish, fontSize: 12, fontWeight: 600 } }, { coord: [0, 0] }],
              [{ coord: [0, -1], itemStyle: { color: COLORS.blue + '10' }, label: { show: true, position: 'insideBottomRight', formatter: '衰退', color: COLORS.blue, fontSize: 12, fontWeight: 600 } }, { coord: [1, 0] }],
            ]
          }
        }],
        graphic: [
          { type: 'line', shape: { x1: '50%', y1: 0, x2: '50%', y2: '100%' }, style: { stroke: COLORS.borderSubtle, lineWidth: 1, lineDash: [4, 4] }, z: 0, left: '50%', top: 30, bottom: 30 },
        ],
      });

      // Indicator charts
      const indicators = d.indicators || {};
      const indConfigs = [
        { key: 'output_gap', id: `chart-merrill-output-${region}`, color: COLORS.blue },
        { key: 'core_cpi', id: `chart-merrill-cpi-${region}`, color: COLORS.bearish },
        { key: 'ppi_yoy', id: `chart-merrill-ppi-${region}`, color: COLORS.neutral },
      ];
      if (region === 'us') {
        indConfigs.push({ key: 'breakeven_inflation', id: `chart-merrill-breakeven-${region}`, color: COLORS.purple });
      }

      indConfigs.forEach(cfg => {
        const raw = indicators[cfg.key];
        if (!raw) return;
        const indData = Array.isArray(raw) ? raw : (raw.history || raw.data || []);
        if (!indData || !indData.length) return;

        createChart(cfg.id, {
          tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
          grid: gridConfig(),
          xAxis: { type: 'category', data: indData.map(dd => dd.date || dd.period || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(indData.length / 4) } },
          yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
          series: [{ type: 'line', data: indData.map(dd => dd.value), smooth: 0.3, symbol: 'circle', symbolSize: 4, lineStyle: { width: 2.5, color: cfg.color }, areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:cfg.color+'25'},{offset:1,color:cfg.color+'00'}]) } }],
        });
      });
    });
  }

  // ========== Section 7: Credit Impulse ==========
  function renderCreditImpulseHTML(data) {
    const ci = data?.credit_impulse;
    if (!ci) return '';

    const global = ci.global || {};
    const cn = ci.cn || {};
    const us = ci.us || {};

    return `
      <div class="cycle-section" id="section-credit">
        <div class="cycle-section-title">
          <span class="section-icon">💳</span>
          <h2>信贷脉冲</h2>
          <span class="section-desc">最重要的领先指标</span>
        </div>
        <div class="credit-pulse-banner">
          <span class="banner-icon">⚡</span>
          <div class="banner-content">
            <div class="banner-title">信贷脉冲是预测大类资产走势最有效的领先指标</div>
            <div class="banner-text">中国信贷脉冲领先全球经济增长约2-3个季度，是预判大宗商品、工业金属和新兴市场资产走势的核心指标。美国高收益债利差则是信用周期松紧的实时温度计。</div>
          </div>
        </div>
        <div class="cycle-triple-region">
          <!-- Global -->
          <div class="cycle-region-card">
            <div class="region-header">
              <span class="region-flag">🌍</span>
              <span class="region-title">全球</span>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">全球流动性指数</div>
              <div class="indicator-chart tall" id="chart-credit-global-liquidity"></div>
            </div>
            ${global.current_status ? `<div style="margin-top:12px;font-size:12px;color:#94a3b8">${escapeHtml(global.current_status)}</div>` : ''}
          </div>
          <!-- China -->
          <div class="cycle-region-card">
            <div class="region-header">
              <span class="region-flag">🇨🇳</span>
              <span class="region-title">中国</span>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">社融增速</div>
              <div class="indicator-chart" id="chart-credit-cn-tsrf"></div>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">信贷脉冲 (Δ社融增速)</div>
              <div class="indicator-chart" id="chart-credit-cn-impulse"></div>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">M2 vs 名义GDP</div>
              <div class="indicator-chart" id="chart-credit-cn-m2-gdp"></div>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">企业中长期贷款占比</div>
              <div class="indicator-chart" id="chart-credit-cn-lt-loan"></div>
            </div>
            ${cn.current_status ? `<div style="margin-top:12px;font-size:12px;color:#94a3b8">${escapeHtml(cn.current_status)}</div>` : ''}
          </div>
          <!-- US -->
          <div class="cycle-region-card">
            <div class="region-header">
              <span class="region-flag">🇺🇸</span>
              <span class="region-title">美国</span>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">高收益债利差</div>
              <div class="indicator-chart" id="chart-credit-us-hy-spread"></div>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">美联储总资产</div>
              <div class="indicator-chart" id="chart-credit-us-fed-assets"></div>
            </div>
            <div class="cycle-indicator-card mt-2">
              <div class="indicator-title">银行信贷增速</div>
              <div class="indicator-chart" id="chart-credit-us-bank-credit"></div>
            </div>
            ${us.current_status ? `<div style="margin-top:12px;font-size:12px;color:#94a3b8">${escapeHtml(us.current_status)}</div>` : ''}
          </div>
        </div>
        ${ci.investment_usage || ci.advice ? `
        <div class="cycle-advice-box mt-4">
          <div class="advice-title">💡 基于信贷脉冲的投资指引</div>
          ${ci.investment_usage ? `<p style="font-size:12px;color:#94a3b8;line-height:1.6;margin-bottom:8px">${escapeHtml(ci.investment_usage)}</p>` : ''}
          ${ci.advice && ci.advice.length ? `<ul class="advice-list">${ci.advice.map(a => `<li>${escapeHtml(a)}</li>`).join('')}</ul>` : ''}
        </div>` : ''}
      </div>`;
  }

  function renderCreditImpulseCharts(data) {
    const ci = data?.credit_impulse;
    if (!ci) return;

    function renderLineChart(containerId, indData, color) {
      if (!indData || !indData.length) return;
      createChart(containerId, {
        tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
        grid: gridConfig(),
        xAxis: { type: 'category', data: indData.map(d => d.date || d.period || ''), axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(indData.length / 4) } },
        yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
        series: [{ type: 'line', data: indData.map(d => d.value), smooth: 0.3, symbol: 'none', lineStyle: { width: 2, color: color }, areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:color+'25'},{offset:1,color:color+'00'}]) } }],
      });
    }

    function renderDualLineChart(containerId, data1, data2, color1, color2, name1, name2) {
      if ((!data1 || !data1.length) && (!data2 || !data2.length)) return;
      const series = [];
      if (data1 && data1.length) {
        series.push({ name: name1, type: 'line', data: data1.map(d => [d.date || d.period || '', d.value]), smooth: 0.3, symbol: 'none', lineStyle: { width: 2, color: color1 } });
      }
      if (data2 && data2.length) {
        series.push({ name: name2, type: 'line', data: data2.map(d => [d.date || d.period || '', d.value]), smooth: 0.3, symbol: 'none', lineStyle: { width: 2, color: color2 } });
      }

      const allDates = [...new Set([
        ...(data1 || []).map(d => d.date || d.period || ''),
        ...(data2 || []).map(d => d.date || d.period || ''),
      ])].sort();

      createChart(containerId, {
        tooltip: Object.assign(tooltipConfig(), { trigger: 'axis' }),
        legend: { show: true, top: 0, textStyle: { color: COLORS.textSecondary, fontSize: 10 }, icon: 'roundRect', itemWidth: 12, itemHeight: 3 },
        grid: gridConfig({ top: 32 }),
        xAxis: { type: 'category', data: allDates, axisLine: { lineStyle: { color: COLORS.borderSubtle } }, axisLabel: { color: COLORS.textMuted, fontSize: 9, interval: Math.floor(allDates.length / 4) } },
        yAxis: { type: 'value', scale: true, axisLine: { show: false }, axisLabel: { color: COLORS.textMuted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(30,41,59,0.4)', type: 'dashed' } } },
        series: series,
      });
    }

    // Global
    const global = ci.global || {};
    renderLineChart('chart-credit-global-liquidity', global.liquidity_index || global.data, COLORS.cyan);

    // China
    const cn = ci.cn || {};
    const cnIndicators = cn.indicators || cn;
    renderLineChart('chart-credit-cn-tsrf', cnIndicators.tsrf_growth || cnIndicators.social_financing, COLORS.blue);
    renderLineChart('chart-credit-cn-impulse', cnIndicators.credit_impulse || cnIndicators.impulse, COLORS.bearish);
    renderDualLineChart('chart-credit-cn-m2-gdp',
      cnIndicators.m2_growth || cnIndicators.m2,
      cnIndicators.nominal_gdp_growth || cnIndicators.nominal_gdp,
      COLORS.purple, COLORS.cyan, 'M2增速', '名义GDP增速'
    );
    renderLineChart('chart-credit-cn-lt-loan', cnIndicators.lt_loan_ratio || cnIndicators.long_term_loan, COLORS.neutral);

    // US
    const us = ci.us || {};
    const usIndicators = us.indicators || us;
    renderLineChart('chart-credit-us-hy-spread', usIndicators.hy_spread || usIndicators.high_yield_spread, COLORS.bearish);
    renderLineChart('chart-credit-us-fed-assets', usIndicators.fed_assets || usIndicators.fed_total_assets, COLORS.blue);
    renderLineChart('chart-credit-us-bank-credit', usIndicators.bank_credit || usIndicators.bank_credit_growth, COLORS.bullish);
  }

  // ========== Section 8: Synthesis ==========
  function renderSynthesisHTML(data) {
    const synthesis = data?.synthesis;
    if (!synthesis) return '';

    const assets = synthesis.asset_views || synthesis.assets || [];
    const risks = synthesis.key_risks || synthesis.risks || [];
    const observations = synthesis.key_observations || synthesis.observations || [];

    // Asset view cards
    const assetIcons = {
      'A股': '🇨🇳📈', '美股': '🇺🇸📈', '中国利率债': '🏦', '美国国债': '🏛️',
      '工业金属': '⚙️', '黄金': '🥇', '原油': '🛢️', '比特币': '₿',
    };

    let assetCards = '';
    assets.forEach(asset => {
      const name = asset.name || asset.asset || '';
      const allocation = asset.allocation || asset.view || '标配';
      const allocClass = allocation.includes('超配') ? 'overweight' : allocation.includes('低配') ? 'underweight' : 'market-weight';
      const reason = asset.reason || asset.rationale || '';
      const icon = assetIcons[name] || '📊';

      assetCards += `
        <div class="asset-view-card ${allocClass}">
          <div class="asset-icon">${icon}</div>
          <div class="asset-name">${escapeHtml(name)}</div>
          <div class="asset-allocation ${allocClass}">${escapeHtml(allocation)}</div>
          <div class="asset-reason">${escapeHtml(reason)}</div>
        </div>`;
    });

    // Risks
    let risksHTML = '';
    if (risks.length) {
      risksHTML = `<div style="margin-top:24px">
        <div style="font-size:14px;font-weight:700;color:#ef4444;margin-bottom:12px;display:flex;align-items:center;gap:6px">⚠️ 关键风险</div>
        <div class="cycle-risk-list">${risks.map(r => `
          <div class="cycle-risk-item">
            <span class="risk-icon">⚠</span>
            <span>${escapeHtml(r.text || r)}</span>
          </div>`).join('')}
        </div>
      </div>`;
    }

    // Observations
    let obsHTML = '';
    if (observations.length) {
      obsHTML = `<div style="margin-top:20px">
        <div style="font-size:14px;font-weight:700;color:#3b82f6;margin-bottom:12px;display:flex;align-items:center;gap:6px">🔍 关键观察点</div>
        <div class="cycle-observation-list">${observations.map(o => `
          <div class="cycle-observation-item">
            <span class="obs-icon">🔍</span>
            <span>${escapeHtml(o.text || o)}</span>
          </div>`).join('')}
        </div>
      </div>`;
    }

    return `
      <div class="cycle-section" id="section-synthesis">
        <div class="cycle-section-title">
          <span class="section-icon">🎯</span>
          <h2>综合结论</h2>
          <span class="section-desc">基于多周期共振的大类资产观点</span>
        </div>
        <div class="cycle-asset-grid">${assetCards}</div>
        ${risksHTML}
        ${obsHTML}
      </div>`;
  }

  // ========== Indicator Descriptions ===========
  const INDICATOR_META = {
    // Perez
    'vc_pe_funding': { name: 'VC/PE融资额', def: '全球风险投资和私募股权融资总额', source: 'Crunchbase, PitchBook', freq: '季度', watch: 'Frenzy期融资额激增是泡沫信号；Turning Point后大幅收缩' },
    'ipo_density': { name: 'IPO数量', def: '全球IPO上市数量', source: 'Renaissance Capital', freq: '年度', watch: 'IPO密集发行是市场过热的典型特征，>1500家/年需警惕' },
    'passive_fund_ratio': { name: '被动基金占比', def: '被动型基金净流入占总净流入比例', source: 'ICI, EPFR', freq: '季度', watch: '>30%表示Frenzy顶部区域，资金从主动管理流向被动指数' },
    'hy_spread': { name: '高收益债利差', def: '高收益债与国债的信用利差', source: 'ICE BofA Index', freq: '日度', watch: '利差急剧收窄→市场狂热；急剧走阔→Turning Point信号' },
    'yield_curve': { name: '美国10Y-2Y利差', def: '10年期与2年期国债收益率之差', source: 'Fed', freq: '日度', watch: '曲线倒挂→金融条件收紧→Turning Point信号' },
    // Kitchin
    'inventory_stock': { name: '工业企业库存增速', def: '中国工业企业产成品存货同比增速', source: '国家统计局', freq: '月度', watch: '增速由负转正→补库启动；由正转负→去库开始' },
    'inventory_to_sales': { name: '库存/销售比', def: '美国制造业库存与销售比值', source: 'Census Bureau', freq: '月度', watch: '比值上升→被动补库；比值下降→主动去库' },
    'pmi_new_orders': { name: 'PMI新订单指数', def: '采购经理指数中的新订单分项', source: '国家统计局/ISM', freq: '月度', watch: '>50扩张，<50收缩；领先库存周期1-2个季度' },
    'ism_new_orders': { name: 'ISM新订单指数', def: '美国ISM制造业新订单指数', source: 'ISM', freq: '月度', watch: '>50扩张，<50收缩；美国库存周期领先指标' },
    'ppi_yoy': { name: 'PPI同比', def: '工业生产者出厂价格指数同比', source: '国家统计局', freq: '月度', watch: 'PPI转正→企业盈利改善→全面补库信号' },
    'pmi_inventory_diff': { name: 'PMI库存差', def: 'PMI产成品库存-原材料库存差值', source: 'ISM', freq: '月度', watch: '差值扩大→被动补库；差值缩小→主动去库' },
    // Merrill Clock
    'output_gap': { name: '产出缺口', def: '实际GDP与潜在GDP的偏差百分比', source: 'IMF/国家统计局', freq: '季度', watch: '缺口由负转正→经济过热；由正转负→衰退风险' },
    'core_cpi': { name: '核心CPI', def: '剔除食品和能源的消费者物价指数', source: 'BLS/国家统计局', freq: '月度', watch: '核心CPI持续>2%→通胀压力；<1%→通缩风险' },
    'breakeven_inflation': { name: 'Breakeven通胀', def: '名义国债与TIPS的收益率差，反映市场通胀预期', source: 'Fed', freq: '日度', watch: 'Breakeven上升→通胀预期升温；下降→通缩预期' },
    // Credit Impulse
    'credit_impulse': { name: '信贷脉冲', def: '社融增量/GDP的环比变化，衡量信贷扩张加速度', source: '央行/PBOC', freq: '月度', watch: '脉冲由负转正→领先经济增长2-3个季度，是最有效的领先指标' },
  };

  function renderIndicatorDescriptions(sectionId, indicators) {
    const container = document.getElementById(sectionId + '-descriptions');
    if (!container || !indicators) return;
    
    let html = '<div class="indicator-descriptions-grid">';
    Object.keys(indicators).forEach(function(key) {
      var meta = INDICATOR_META[key];
      var ind = indicators[key];
      if (!meta || !ind) return;
      var currentVal = ind.current !== undefined ? ind.current : (ind.history && ind.history.length ? ind.history[ind.history.length-1].value : '--');
      var unit = ind.unit || '';
      html += '<div class="indicator-desc-card">' +
        '<div class="indicator-desc-name">' + meta.name + '</div>' +
        '<div class="indicator-desc-def">' + meta.def + '</div>' +
        '<div class="indicator-desc-meta">' +
          '<span>📊 来源: ' + meta.source + '</span>' +
          '<span>🔄 频率: ' + meta.freq + '</span>' +
          '<span>📍 当前: ' + currentVal + unit + '</span>' +
        '</div>' +
        '<div class="indicator-desc-watch">👁️ 关注: ' + meta.watch + '</div>' +
      '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
  }

    // ========== Main Render ==========
  function render(data) {
    const container = document.getElementById('cycle-content');
    if (!container || !data) return;

    // Data timestamp banner
    let html = '';
    if (typeof Utils !== 'undefined' && Utils.renderTimestampBanner) {
      html += Utils.renderTimestampBanner(data.update_time, '全球周期');
    } else if (data.update_time) {
      html += `<div class="data-timestamp-banner"><span class="ts-banner-icon">🕐</span><span class="ts-banner-text">全球周期 · 数据截至: ${escapeHtml(data.update_time)}</span></div>`;
    }
    html += renderOverviewHTML(data);
    html += renderTimelineHTML(data);
    html += renderKongboHTML(data);
    html += renderPerezHTML(data);
    html += renderJuglarHTML(data);
    html += renderKitchinHTML(data);
    html += renderMerrillClockHTML(data);
    html += renderCreditImpulseHTML(data);
    html += renderSynthesisHTML(data);

    container.innerHTML = html;

    // Render charts after DOM is ready
    requestAnimationFrame(() => {
      renderTimelineChart(data);
      renderKongboCharts(data);
      renderPerezCharts(data);
      renderJuglarCharts(data);
      renderKitchinCharts(data);
      renderMerrillClockCharts(data);
      renderCreditImpulseCharts(data);
      // Render indicator descriptions
      if (data.perez?.indicators) renderIndicatorDescriptions('perez', data.perez.indicators);
      if (data.kitchin?.cn?.indicators) renderIndicatorDescriptions('kitchin-cn', data.kitchin.cn.indicators);
      if (data.kitchin?.us?.indicators) renderIndicatorDescriptions('kitchin-us', data.kitchin.us.indicators);
      if (data.merrill_clock?.cn?.indicators) renderIndicatorDescriptions('merrill-cn', data.merrill_clock.cn.indicators);
      if (data.merrill_clock?.us?.indicators) renderIndicatorDescriptions('merrill-us', data.merrill_clock.us.indicators);
      // Setup resize observer after all charts created
      setupResizeObserver();
    });
  }

  return { render, dispose: disposeAll };
})();
