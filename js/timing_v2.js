/**
 * A股择时框架 v2.0 - Tab Renderer
 * Six-dimensional scoring radar + position guidance
 */

const TimingTab = {
  data: null,
  radarChart: null,
  scoreGaugeChart: null,
  miniCharts: [],
  miniChartObserver: null,

  async init() {
    try {
      const resp = await fetch('data/timing_scores.json');
      if (!resp.ok) throw new Error('Failed to load timing data');
      this.data = await resp.json();
      this.render();
    } catch (err) {
      console.error('TimingTab init error:', err);
      document.getElementById('timing-content').innerHTML = `
        <div class="val-card" style="text-align:center;padding:60px 20px;">
          <div style="font-size:32px;margin-bottom:12px;">⚠️</div>
          <div style="font-size:14px;color:#94a3b8;">择时数据加载失败，请稍后刷新重试</div>
        </div>
      `;
    }
  },

  render() {
    if (!this.data) return;
    const d = this.data;
    let html = '';

    // Update info
    html += `
      <div class="timing-update-info">
        <span>📅</span>
        <span>数据基准日: ${d.update_date}</span>
        <span style="margin-left:8px;">·</span>
        <span style="margin-left:8px;">框架版本: ${d.version || 'v2.0'}</span>
      </div>
    `;

    // Hero: Composite Score + Radar
    html += this.renderHero(d);

    // Dimension Detail Cards
    html += this.renderDimensions(d);

    // Style Rotation
    html += this.renderStyleRotation(d);

    // Position Advice
    html += this.renderPositionAdvice(d);

    // Bottom/Top Signals
    html += this.renderSignals(d);

    // Triggers
    html += this.renderTriggers(d);

    document.getElementById('timing-content').innerHTML = html;

    // Render charts after DOM is ready
    requestAnimationFrame(() => {
      this.renderRadarChart(d);
      this.renderScoreGauge(d);
      this.renderMiniCharts();
    });
  },

  renderHero(d) {
    const scoreColor = this.getScoreColor(d.composite_score);
    const statusClass = this.getStatusClass(d.composite_score);

    return `
      <div class="timing-hero">
        <div class="timing-score-panel">
          <div id="timing-score-gauge" class="timing-score-ring"></div>
          <div class="timing-score-label">综合得分 (0-100)</div>
          <div class="timing-status-badge ${statusClass}">
            <span>${d.market_status}</span>
          </div>
          <div style="text-align:center;margin-top:4px;">
            <span style="font-size:12px;color:#64748b;">建议权益仓位</span>
            <div class="timing-position-value" style="color:${scoreColor};">${d.position_range}</div>
          </div>
        </div>
        <div class="timing-detail-panel">
          <div>
            <div style="font-size:12px;font-weight:600;color:#64748b;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.05em;">六维评分雷达</div>
            <div id="timing-radar-chart" class="timing-radar-chart"></div>
          </div>
          <div class="timing-position-card">
            <div class="timing-position-title">综合评分定位</div>
            <div class="timing-position-value" style="color:${scoreColor};">${d.composite_score}<span class="timing-score-unit"> 分</span></div>
            <div class="timing-position-bar">
              <div class="timing-position-fill" style="width:${d.composite_score}%;background:${scoreColor};"></div>
            </div>
            <div class="timing-position-scale">
              <span>0 大底</span>
              <span>35 中性</span>
              <span>65 预警</span>
              <span>100 大顶</span>
            </div>
            <div style="margin-top:8px;">
              <div style="font-size:11px;color:#64748b;margin-bottom:6px;">各维度得分</div>
              ${Object.entries(d.dimensions).map(([key, dim]) => {
                const c = this.getScoreColor(dim.score);
                return `
                  <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
                    <span style="font-size:11px;color:#94a3b8;width:70px;">${dim.name}</span>
                    <div style="flex:1;height:4px;border-radius:2px;background:rgba(255,255,255,0.05);overflow:hidden;">
                      <div style="width:${dim.score}%;height:100%;border-radius:2px;background:${c};"></div>
                    </div>
                    <span style="font-size:11px;font-weight:700;color:${c};width:28px;text-align:right;font-family:'JetBrains Mono',monospace;">${dim.score}</span>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        </div>
      </div>
    `;
  },

  renderDimensions(d) {
    let html = '';
    html += `
      <div class="section-header">
        <div class="section-title">📊 六维度评分明细</div>
        <div class="section-desc">0分=极致底部信号 · 50分=中性 · 100分=极致顶部信号</div>
      </div>
    `;
    html += '<div class="timing-dimensions-grid">';

    const dimIcons = {
      valuation: '💰',
      liquidity: '🌊',
      equity_bond: '⚖️',
      capital_flow: '💸',
      sentiment: '😰',
      micro_structure: '🔬'
    };

    Object.entries(d.dimensions).forEach(([key, dim]) => {
      const scoreClass = this.getScoreClass(dim.score);
      const icon = dimIcons[key] || '📊';

      html += `
        <div class="timing-dim-card score-${scoreClass}">
          <div class="timing-dim-header">
            <div class="timing-dim-name">${icon} ${dim.name}</div>
            <div class="timing-dim-score score-${scoreClass}">${dim.score}</div>
          </div>
          <div class="timing-dim-meta">
            <span class="timing-dim-signal score-${scoreClass}">${dim.signal}</span>
            <span class="timing-dim-weight">权重 ${dim.weight}%</span>
          </div>
          <div class="timing-dim-indicators">
            ${Object.entries(dim.indicators).map(([iKey, ind]) => {
              const indScoreClass = this.getScoreClass(ind.score);
              const hasHistory = ind.history && ind.history.length > 0;
              return `
                <div class="timing-dim-indicator ${hasHistory ? 'has-chart' : ''}">
                  <div class="timing-dim-indicator-row">
                    <span class="timing-dim-indicator-name">${ind.name}</span>
                    <span class="timing-dim-indicator-value">${ind.value}${ind.unit ? ind.unit : ''}</span>
                    <span class="timing-dim-indicator-score score-${indScoreClass}" style="color:${this.getScoreColor(ind.score)}">${ind.score}</span>
                  </div>
                  ${hasHistory ? `<div class="timing-mini-chart" id="mini-chart-${key}-${iKey}" data-history='${JSON.stringify(ind.history)}' data-score="${ind.score}"></div>` : ''}
                  ${ind.description ? `<div class="timing-dim-indicator-desc">${ind.description}</div>` : ''}
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    });

    html += '</div>';
    return html;
  },

  renderStyleRotation(d) {
    if (!d.style_rotation) return '';
    const sr = d.style_rotation;

    let html = `
      <div class="timing-style-section">
        <div class="section-header">
          <div class="section-title">🔄 风格轮动辅助判断</div>
          <div class="section-desc">大小盘、红利/成长风格定位与轮动信号</div>
        </div>
        <div class="timing-style-grid">
    `;

    // Large/Small
    const ls = sr.large_small;
    if (ls) {
      html += `
        <div class="timing-style-card">
          <div class="timing-style-indicator">${ls.indicator}</div>
          <div class="timing-style-status">${ls.status}</div>
          <div class="timing-style-signal">📌 ${ls.signal}</div>
          <div class="timing-style-detail">${ls.detail}</div>
          <div class="timing-style-metrics">
            ${ls.current_value ? `<span class="timing-style-metric"><span class="metric-label">%B</span><span class="metric-value">${ls.current_value.replace('%B=', '')}</span></span>` : ''}
            ${ls.ma242_40d ? `<span class="timing-style-metric"><span class="metric-label">40日收益差</span><span class="metric-value">${ls.ma242_40d}</span></span>` : ''}
            ${ls.rsi14_ma242 ? `<span class="timing-style-metric"><span class="metric-label">RSI14</span><span class="metric-value">${ls.rsi14_ma242}</span></span>` : ''}
          </div>
        </div>
      `;
    }

    // Value/Growth
    const vg = sr.value_growth;
    if (vg) {
      html += `
        <div class="timing-style-card">
          <div class="timing-style-indicator">${vg.indicator}</div>
          <div class="timing-style-status">${vg.status}</div>
          <div class="timing-style-signal">📌 ${vg.signal}</div>
          <div class="timing-style-detail">${vg.detail}</div>
          <div class="timing-style-metrics">
            ${vg.current_value ? `<span class="timing-style-metric"><span class="metric-label">%B</span><span class="metric-value">${vg.current_value.replace('%B=', '')}</span></span>` : ''}
            ${vg.ma242_40d ? `<span class="timing-style-metric"><span class="metric-label">40日收益差</span><span class="metric-value">${vg.ma242_40d}</span></span>` : ''}
          </div>
        </div>
      `;
    }

    // Dividend Premium
    const dp = sr.dividend_premium;
    if (dp) {
      html += `
        <div class="timing-style-card">
          <div class="timing-style-indicator">${dp.indicator}</div>
          <div class="timing-style-status">${dp.status}</div>
          <div class="timing-style-signal">📌 ${dp.signal}</div>
        </div>
      `;
    }

    html += '</div></div>';
    return html;
  },

  renderPositionAdvice(d) {
    if (!d.position_advice) return '';
    const pa = d.position_advice;
    const colors = ['#10b981', '#3b82f6', '#8b5cf6', '#f59e0b', '#06b6d4'];

    let html = `
      <div class="timing-position-section">
        <div class="section-header">
          <div class="section-title">🎯 仓位配置建议</div>
          <div class="section-desc">综合得分 ${d.composite_score}分 → 权益仓位 ${pa.equity_range}</div>
        </div>
        <div class="timing-position-grid">
    `;

    Object.entries(pa.breakdown).forEach(([key, item], idx) => {
      const color = colors[idx % colors.length];
      html += `
        <div class="timing-position-item">
          <div class="timing-position-item-name">${key.replace(/_/g, '/')}</div>
          <div class="timing-position-item-range" style="color:${color};">${item.range}</div>
          <div class="timing-position-item-note">${item.note}</div>
        </div>
      `;
    });

    html += '</div></div>';
    return html;
  },

  renderSignals(d) {
    if (!d.signals) return '';

    let html = `
      <div class="timing-signals-section">
        <div class="section-header">
          <div class="section-title">🚦 底部/顶部信号清单</div>
          <div class="section-desc">绿色=当前已触发 · 灰色=未触发 · 顶部确认需≥4维度同时亮灯</div>
        </div>
        <div class="timing-signals-grid">
    `;

    // Bottom signals
    html += `
      <div class="timing-signals-panel">
        <div class="timing-signals-title bottom">🟢 底部信号 (${d.signals.bottom.filter(s => s.active).length}/${d.signals.bottom.length} 触发)</div>
    `;
    d.signals.bottom.forEach(s => {
      html += `
        <div class="timing-signal-item ${s.active ? 'active' : ''}">
          <div class="timing-signal-check ${s.active ? 'active' : 'inactive'}">
            ${s.active ? '✓' : '○'}
          </div>
          <div class="timing-signal-content">
            <div class="timing-signal-dim">${s.dimension}</div>
            <div class="timing-signal-text">${s.signal}</div>
            <div class="timing-signal-stars">${'★'.repeat(s.stars)}${'☆'.repeat(5 - s.stars)}</div>
          </div>
        </div>
      `;
    });
    html += '</div>';

    // Top signals
    html += `
      <div class="timing-signals-panel">
        <div class="timing-signals-title top">🔴 顶部信号 (${d.signals.top.filter(s => s.active).length}/${d.signals.top.length} 触发)</div>
    `;
    d.signals.top.forEach(s => {
      html += `
        <div class="timing-signal-item ${s.active ? 'active' : ''}">
          <div class="timing-signal-check ${s.active ? 'active' : 'inactive'}">
            ${s.active ? '✓' : '○'}
          </div>
          <div class="timing-signal-content">
            <div class="timing-signal-dim">${s.dimension}</div>
            <div class="timing-signal-text">${s.signal}</div>
            <div class="timing-signal-stars">${'★'.repeat(s.stars)}${'☆'.repeat(5 - s.stars)}</div>
          </div>
        </div>
      `;
    });
    html += '</div></div></div>';

    return html;
  },

  renderTriggers(d) {
    if (!d.triggers) return '';

    let html = `
      <div class="timing-triggers-section">
        <div class="section-header">
          <div class="section-title">⚡ 边际变化触发条件</div>
          <div class="section-desc">满足2项以上可调整仓位（向上加仓至60%-70% / 向下减仓至20%-30%）</div>
        </div>
        <div class="timing-triggers-grid">
    `;

    // Add position triggers
    html += `
      <div class="timing-triggers-panel">
        <div class="timing-triggers-title add">📈 向上加仓触发 (${d.triggers.add_position.filter(t => t.met).length}/${d.triggers.add_position.length})</div>
    `;
    d.triggers.add_position.forEach(t => {
      html += `
        <div class="timing-trigger-item">
          <div class="timing-trigger-status ${t.met ? 'met' : 'unmet'}"></div>
          <div class="timing-trigger-text ${t.met ? 'met' : ''}">${t.condition}</div>
        </div>
      `;
    });
    html += '</div>';

    // Reduce position triggers
    html += `
      <div class="timing-triggers-panel">
        <div class="timing-triggers-title reduce">📉 向下减仓触发 (${d.triggers.reduce_position.filter(t => t.met).length}/${d.triggers.reduce_position.length})</div>
    `;
    d.triggers.reduce_position.forEach(t => {
      html += `
        <div class="timing-trigger-item">
          <div class="timing-trigger-status ${t.met ? 'met' : 'unmet'}"></div>
          <div class="timing-trigger-text ${t.met ? 'met' : ''}">${t.condition}</div>
        </div>
      `;
    });
    html += '</div>';

    html += '</div></div>';
    return html;
  },

  // ========== Mini Sparkline Charts ==========

  renderMiniCharts() {
    // Dispose previous mini charts
    this.miniCharts.forEach(c => c.dispose());
    this.miniCharts = [];
    if (this.miniChartObserver) {
      this.miniChartObserver.disconnect();
      this.miniChartObserver = null;
    }

    const containers = document.querySelectorAll('.timing-mini-chart');
    if (!containers.length) return;

    this.miniChartObserver = new ResizeObserver(() => {
      this.miniCharts.forEach(c => c.resize());
    });

    containers.forEach(el => {
      let history = [];
      try {
        history = JSON.parse(el.getAttribute('data-history') || '[]');
      } catch(e) { return; }
      if (!history.length) return;

      const score = parseFloat(el.getAttribute('data-score') || '50');
      const color = this.getScoreColor(score);

      const chart = echarts.init(el);
      this.miniCharts.push(chart);
      this.miniChartObserver.observe(el);

      const dates = history.map(h => h.date);
      const values = history.map(h => h.value);

      const option = {
        animation: true,
        animationDuration: 600,
        grid: { top: 4, bottom: 4, left: 4, right: 4 },
        xAxis: {
          type: 'category',
          data: dates,
          show: false,
          boundaryGap: false,
        },
        yAxis: {
          type: 'value',
          show: false,
          scale: true,
        },
        series: [{
          type: 'line',
          data: values,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.5, color: color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: color.replace(')', ', 0.25)').replace('rgb', 'rgba') },
              { offset: 1, color: color.replace(')', ', 0.02)').replace('rgb', 'rgba') },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: color, width: 1, type: 'dashed', opacity: 0.5 },
            data: [{ xAxis: dates.length - 1 }],
            label: { show: false },
          },
        }],
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#1e293b',
          borderColor: '#334155',
          textStyle: { color: '#e2e8f0', fontSize: 10 },
          formatter: function(params) {
            if (!params || !params[0]) return '';
            return `${params[0].axisValue}<br/>${params[0].value}`;
          },
        },
      };

      // Fix area gradient for hex colors
      const hexToRgba = (hex, alpha) => {
        const r = parseInt(hex.slice(1,3), 16);
        const g = parseInt(hex.slice(3,5), 16);
        const b = parseInt(hex.slice(5,7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
      };
      option.series[0].areaStyle = {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: hexToRgba(color, 0.25) },
          { offset: 1, color: hexToRgba(color, 0.02) },
        ]),
      };

      chart.setOption(option);
    });
  },

  // ========== ECharts Rendering ==========

  renderRadarChart(d) {
    const container = document.getElementById('timing-radar-chart');
    if (!container || !d) return;

    if (this.radarChart) {
      this.radarChart.dispose();
    }
    this.radarChart = echarts.init(container);

    const dims = Object.entries(d.dimensions);
    const indicator = dims.map(([key, dim]) => ({
      name: dim.name,
      max: 100,
    }));
    const values = dims.map(([key, dim]) => dim.score);

    const option = {
      animation: true,
      animationDuration: 800,
      radar: {
        indicator: indicator,
        shape: 'polygon',
        radius: '65%',
        center: ['50%', '52%'],
        axisName: {
          color: '#94a3b8',
          fontSize: 11,
          fontWeight: 500,
        },
        splitArea: {
          areaStyle: {
            color: ['rgba(30, 41, 59, 0.3)', 'rgba(30, 41, 59, 0.15)'],
          },
        },
        splitLine: {
          lineStyle: { color: 'rgba(148, 163, 184, 0.1)' },
        },
        axisLine: {
          lineStyle: { color: 'rgba(148, 163, 184, 0.15)' },
        },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: values,
              name: '当前评分',
              symbol: 'circle',
              symbolSize: 6,
              lineStyle: {
                color: '#3b82f6',
                width: 2,
              },
              areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
                  { offset: 1, color: 'rgba(59, 130, 246, 0.05)' },
                ]),
              },
              itemStyle: {
                color: '#3b82f6',
                borderColor: '#1e293b',
                borderWidth: 2,
              },
            },
            {
              value: [50, 50, 50, 50, 50, 50],
              name: '中性线',
              symbol: 'none',
              lineStyle: {
                color: 'rgba(245, 158, 11, 0.4)',
                width: 1,
                type: 'dashed',
              },
              areaStyle: {
                color: 'rgba(245, 158, 11, 0.03)',
              },
            },
          ],
        },
      ],
      tooltip: {
        trigger: 'item',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: function(params) {
          if (!params.value) return '';
          let html = '<div style="font-weight:600;margin-bottom:4px;">六维评分</div>';
          dims.forEach(([key, dim], idx) => {
            const val = params.value[idx];
            const color = val < 40 ? '#10b981' : val < 60 ? '#f59e0b' : '#ef4444';
            html += `<div style="display:flex;justify-content:space-between;gap:12px;"><span style="color:#94a3b8;">${dim.name}</span><span style="color:${color};font-weight:600;">${val}分</span></div>`;
          });
          return html;
        },
      },
    };

    this.radarChart.setOption(option);

    const observer = new ResizeObserver(() => this.radarChart.resize());
    observer.observe(container);
  },

  renderScoreGauge(d) {
    const container = document.getElementById('timing-score-gauge');
    if (!container || !d) return;

    if (this.scoreGaugeChart) {
      this.scoreGaugeChart.dispose();
    }
    this.scoreGaugeChart = echarts.init(container);

    const scoreColor = this.getScoreColor(d.composite_score);

    const option = {
      animation: true,
      animationDuration: 1200,
      animationEasing: 'cubicOut',
      series: [
        {
          type: 'gauge',
          startAngle: 220,
          endAngle: -40,
          min: 0,
          max: 100,
          radius: '90%',
          center: ['50%', '55%'],
          progress: {
            show: true,
            width: 14,
            roundCap: true,
            itemStyle: {
              color: scoreColor,
            },
          },
          pointer: {
            show: false,
          },
          axisLine: {
            lineStyle: {
              width: 14,
              color: [
                [0.2, '#10b981'],
                [0.35, '#22c55e'],
                [0.65, '#f59e0b'],
                [0.8, '#f97316'],
                [1, '#ef4444'],
              ],
              opacity: 0.2,
            },
          },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          title: { show: false },
          detail: {
            valueAnimation: true,
            fontSize: 36,
            fontWeight: 800,
            color: scoreColor,
            offsetCenter: [0, '0%'],
            formatter: '{value}',
          },
          data: [
            {
              value: d.composite_score,
            },
          ],
        },
      ],
    };

    this.scoreGaugeChart.setOption(option);

    const observer = new ResizeObserver(() => this.scoreGaugeChart.resize());
    observer.observe(container);
  },

  // ========== Utility Methods ==========

  getScoreColor(score) {
    if (score < 35) return '#10b981';
    if (score < 65) return '#f59e0b';
    return '#ef4444';
  },

  getScoreClass(score) {
    if (score < 35) return 'low';
    if (score < 65) return 'mid';
    return 'high';
  },

  getStatusClass(score) {
    if (score < 35) return 'bullish';
    if (score < 65) return 'neutral';
    return 'bearish';
  },

  dispose() {
    if (this.radarChart) {
      this.radarChart.dispose();
      this.radarChart = null;
    }
    if (this.scoreGaugeChart) {
      this.scoreGaugeChart.dispose();
      this.scoreGaugeChart = null;
    }
    this.miniCharts.forEach(c => c.dispose());
    this.miniCharts = [];
    if (this.miniChartObserver) {
      this.miniChartObserver.disconnect();
      this.miniChartObserver = null;
    }
  },
};

// Hook into the main app's tab system
(function() {
  // Override the app.renderTab to include timing tab
  const originalRenderTab = app.renderTab;
  app.renderTab = function(tab) {
    if (tab === 'timing') {
      TimingTab.init();
    } else {
      originalRenderTab.call(app, tab);
    }
  };

  // Override switchTab to dispose timing charts when switching away
  const originalSwitchTab = app.switchTab;
  app.switchTab = function(tab) {
    if (app.currentTab === 'timing' && tab !== 'timing') {
      TimingTab.dispose();
    }
    originalSwitchTab.call(app, tab);
  };

  // Override app.loadAllData to not break (timing loads its own data)
  // No change needed here

  // Override app.refresh to refresh timing if it's the current tab
  const originalRefresh = app.refresh;
  app.refresh = async function() {
    if (app.currentTab === 'timing') {
      TimingTab.dispose();
      TimingTab.init();
    }
    // Also call original for other tabs
    const btn = document.getElementById('refresh-btn');
    btn.classList.add('refreshing');
    await app.loadAllData();
    if (app.currentTab !== 'timing') {
      app.renderTab(app.currentTab);
    }
    const updateTimes = [
      app.data.cnMacro?.update_time,
      app.data.usMacro?.update_time,
    ].filter(Boolean);
    if (updateTimes.length > 0) {
      document.getElementById('update-time').textContent = `更新于 ${updateTimes[0]}`;
    }
    setTimeout(() => btn.classList.remove('refreshing'), 1000);
  };
})();
