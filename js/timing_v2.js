/**
 * A股研究 Tab v3.0 - Renderer
 * Left: Six-dimensional scoring radar (v2.0)
 * Right: Trend confirmation panel (signal lights + scores + signal list)
 * Bottom: Integrated conclusion card
 */

const TimingTab = {
  data: null,
  rightData: null,
  radarChart: null,
  scoreGaugeChart: null,
  bullBarChart: null,
  bearBarChart: null,
  historyChart: null,
  miniCharts: [],
  miniChartObserver: null,

  async init() {
    try {
      const [leftResp, rightResp] = await Promise.all([
        fetch('data/timing_scores.json'),
        fetch('data/timing_right_scores.json')
      ]);
      if (!leftResp.ok) throw new Error('Failed to load timing data');
      this.data = await leftResp.json();
      this.rightData = rightResp.ok ? await rightResp.json() : null;
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
    const rd = this.rightData;
    let html = '';

    // Update info
    html += `
      <div class="timing-update-info">
        <span>📅</span>
        <span>数据基准日: ${d.update_date}</span>
        <span style="margin-left:8px;">·</span>
        <span style="margin-left:8px;">框架版本: ${d.version || 'v2.0'}</span>
        ${rd ? `<span style="margin-left:8px;">·</span><span style="margin-left:8px;">右侧更新: ${rd.update_date || d.update_date}</span>` : ''}
      </div>
    `;

    // ===== Two-column layout =====
    html += '<div class="timing-two-col-layout">';

    // LEFT COLUMN: existing six-dimensional scoring
    html += '<div class="timing-left-col">';
    html += this.renderHero(d);
    html += this.renderDimensions(d);
    html += '</div>'; // end left col

    // RIGHT COLUMN: trend confirmation panel
    if (rd) {
      html += '<div class="timing-right-col">';
      html += this.renderRightPanel(rd);
      html += '</div>'; // end right col
    }

    html += '</div>'; // end two-col layout

    // ===== Bottom: Integrated Conclusion =====
    if (rd) {
      html += this.renderConclusion(d, rd);
    } else {
      // Fallback: original sections when right data unavailable
      html += this.renderStyleRotation(d);
      html += this.renderPositionAdvice(d);
      html += this.renderSignals(d);
      html += this.renderTriggers(d);
    }

    document.getElementById('timing-content').innerHTML = html;

    // Render charts after DOM is ready
    requestAnimationFrame(() => {
      this.renderRadarChart(d);
      this.renderScoreGauge(d);
      this.renderMiniCharts();
      if (rd) {
        this.renderBullBar(rd);
        this.renderBearBar(rd);
        this.renderHistoryChart(rd);
      }
    });
  },

  // ================================================================
  // RIGHT PANEL
  // ================================================================
  renderRightPanel(rd) {
    let html = '<div class="timing-right-panel">';

    // 1. Signal Lights
    html += this.renderSignalLights(rd);

    // 2. Score Progress Bars
    html += this.renderScoreBars(rd);

    // 3. Signal Groups (collapsible)
    html += this.renderSignalGroups(rd);

    // 4. Style Rotation (right side)
    html += this.renderStyleRotationRight(rd);

    html += '</div>';
    return html;
  },

  renderSignalLights(rd) {
    const light = rd.signal_light || 'none';
    const dir = rd.signal_direction;

    const lights = [
      { id: 'yellow', emoji: '🟡', label: '关注', desc: '早期信号触发' },
      { id: 'orange', emoji: '🟠', label: '准备行动', desc: '确认信号触发' },
      { id: 'green', emoji: '🟢', label: '牛市确认', desc: '最强牛市信号', extra: true },
      { id: 'red', emoji: '🔴', label: '熊市确认', desc: '最强熊市信号', extra: true },
    ];

    let html = `
      <div class="trp-signal-lights">
        <div class="trp-section-title">🚦 信号灯</div>
        <div class="trp-lights-row">
    `;

    lights.forEach(l => {
      const isActive = light === l.id;
      html += `
        <div class="trp-light-item ${isActive ? 'active' : 'inactive'} ${l.extra ? 'extra' : ''}">
          <div class="trp-light-icon ${isActive ? 'glow-' + l.id : ''}">${l.emoji}</div>
          <div class="trp-light-label">${l.label}</div>
          <div class="trp-light-desc">${l.desc}</div>
        </div>
      `;
    });

    // Show "no light" state
    if (light === 'none') {
      html += `
        <div class="trp-light-item active">
          <div class="trp-light-icon glow-none">⚪</div>
          <div class="trp-light-label">无信号</div>
          <div class="trp-light-desc">维持当前状态</div>
        </div>
      `;
    }

    html += '</div>';

    // Trend bias label
    const biasLabels = {
      warm: '🌤 偏暖 · 牛市信号聚集',
      cool: '🌧 偏冷 · 熊市信号聚集',
      chaos: '🌪 混沌 · 多空交织',
      vacuum: '⚪ 真空 · 无明确趋势',
      neutral: '➖ 中性'
    };
    const biasText = biasLabels[rd.trend_bias] || '—';
    html += `<div class="trp-bias-label">${biasText}</div>`;

    html += '</div>';
    return html;
  },

  renderScoreBars(rd) {
    const bullPct = Math.min(100, (rd.bull_score / 120) * 100);
    const bearPct = Math.min(100, (rd.bear_score / 120) * 100);

    let html = `
      <div class="trp-score-bars">
        <div class="trp-section-title">📊 累计分数</div>
        <div class="trp-bar-group">
          <div class="trp-bar-label">
            <span>🐂 牛分</span>
            <span class="trp-bar-value bull">${rd.bull_score}<span class="trp-bar-max">/120</span></span>
          </div>
          <div class="trp-bar-track">
            <div class="trp-bar-thresholds">
              <div class="trp-threshold" style="left:25%" title="30分"><span>30</span></div>
              <div class="trp-threshold" style="left:41.7%" title="50分"><span>50</span></div>
              <div class="trp-threshold" style="left:66.7%" title="80分"><span>80</span></div>
            </div>
            <div id="trp-bull-bar" class="trp-bar-fill bull" style="width:${bullPct}%"></div>
          </div>
        </div>
        <div class="trp-bar-group">
          <div class="trp-bar-label">
            <span>🐻 熊分</span>
            <span class="trp-bar-value bear">${rd.bear_score}<span class="trp-bar-max">/120</span></span>
          </div>
          <div class="trp-bar-track">
            <div class="trp-bar-thresholds">
              <div class="trp-threshold" style="left:25%" title="30分"><span>30</span></div>
              <div class="trp-threshold" style="left:41.7%" title="50分"><span>50</span></div>
              <div class="trp-threshold" style="left:66.7%" title="80分"><span>80</span></div>
            </div>
            <div id="trp-bear-bar" class="trp-bar-fill bear" style="width:${bearPct}%"></div>
          </div>
        </div>
        <div class="trp-bar-legend">
          <span>&lt;30 趋势未变</span>
          <span>30-49 早期信号</span>
          <span>50-79 趋势形成</span>
          <span>≥80 强烈确认</span>
        </div>
      </div>
    `;

    // History trend chart container (only if history data exists)
    if (rd.history && rd.history.length >= 2) {
      html += `
        <div class="trp-history-section">
          <div class="trp-section-title">📈 分数走势 (近${rd.history.length}日)</div>
          <div id="trp-history-chart" class="trp-history-chart"></div>
        </div>
      `;
    }

    return html;
  },

  renderHistoryChart(rd) {
    if (!rd.history || rd.history.length < 2) return;

    // Dispose previous chart if any
    if (this.historyChart) {
      try { this.historyChart.dispose(); } catch(e) {}
      this.historyChart = null;
    }

    const container = document.getElementById('trp-history-chart');
    if (!container) return;

    const chart = echarts.init(container, null, { renderer: 'canvas' });
    this.historyChart = chart;

    const dates = rd.history.map(h => h.date);
    const bullScores = rd.history.map(h => h.bull_score);
    const bearScores = rd.history.map(h => h.bear_score);

    const option = {
      animation: true,
      animationDuration: 800,
      grid: {
        top: 28,
        right: 16,
        bottom: 28,
        left: 16,
        containLabel: true,
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        borderWidth: 1,
        textStyle: {
          color: '#e2e8f0',
          fontSize: 12,
          fontFamily: 'JetBrains Mono',
        },
        padding: [8, 12],
        formatter: function(params) {
          let tip = `<div style="font-weight:600;margin-bottom:4px">${params[0].axisValue}</div>`;
          params.forEach(p => {
            tip += `<div style="display:flex;align-items:center;gap:6px">
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color}"></span>
              <span>${p.seriesName}: <b>${p.value}</b>分</span>
            </div>`;
          });
          return tip;
        },
      },
      legend: {
        show: true,
        top: 2,
        right: 0,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        data: ['牛分', '熊分'],
      },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#1e293b' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#64748b',
          fontSize: 10,
          fontFamily: 'JetBrains Mono',
          formatter: function(val) {
            // Show MM-DD format, only every few labels
            return val.substring(5);
          },
          interval: function(index, total) {
            if (total <= 15) return 0;
            if (total <= 30) return Math.floor(total / 6) - 1;
            if (total <= 60) return Math.floor(total / 6) - 1;
            return Math.floor(total / 5) - 1;
          },
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 120,
        splitNumber: 4,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#64748b',
          fontSize: 10,
          fontFamily: 'JetBrains Mono',
        },
        splitLine: {
          lineStyle: { color: 'rgba(148, 163, 184, 0.08)', type: 'dashed' },
        },
      },
      series: [
        {
          name: '牛分',
          type: 'line',
          data: bullScores,
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          showSymbol: rd.history.length <= 30,
          lineStyle: { width: 2, color: '#10b981' },
          itemStyle: { color: '#10b981' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(16, 185, 129, 0.15)' },
              { offset: 1, color: 'rgba(16, 185, 129, 0.01)' },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { type: 'dashed', width: 1 },
            label: {
              fontSize: 9,
              fontFamily: 'JetBrains Mono',
              position: 'insideEndTop',
            },
            data: [
              { yAxis: 30, lineStyle: { color: 'rgba(148, 163, 184, 0.2)' }, label: { formatter: '30', color: '#475569' } },
              { yAxis: 50, lineStyle: { color: 'rgba(148, 163, 184, 0.25)' }, label: { formatter: '50', color: '#64748b' } },
              { yAxis: 80, lineStyle: { color: 'rgba(148, 163, 184, 0.3)' }, label: { formatter: '80', color: '#94a3b8' } },
            ],
          },
        },
        {
          name: '熊分',
          type: 'line',
          data: bearScores,
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          showSymbol: rd.history.length <= 30,
          lineStyle: { width: 2, color: '#ef4444' },
          itemStyle: { color: '#ef4444' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(239, 68, 68, 0.12)' },
              { offset: 1, color: 'rgba(239, 68, 68, 0.01)' },
            ]),
          },
        },
      ],
    };

    chart.setOption(option);

    // Handle resize
    if (!this._historyResizeObserver) {
      this._historyResizeObserver = new ResizeObserver(() => {
        if (this.historyChart) {
          try { this.historyChart.resize(); } catch(e) {}
        }
      });
    }
    this._historyResizeObserver.observe(container);
  },

  renderSignalGroups(rd) {
    const groups = [
      { key: 'early_bull', label: '🟡 早期牛市', icon: '🟡', color: '#f59e0b' },
      { key: 'confirm_bull', label: '🟠 确认牛市', icon: '🟠', color: '#f97316' },
      { key: 'strong_confirm_bull', label: '🟢 最强牛市', icon: '🟢', color: '#10b981' },
      { key: 'early_bear', label: '🔴 早期熊市', icon: '🟡', color: '#fbbf24' },
      { key: 'confirm_bear', label: '🟠 确认熊市', icon: '🟠', color: '#f97316' },
      { key: 'strong_confirm_bear', label: '🔴 最强熊市', icon: '🔴', color: '#ef4444' },
    ];

    let html = `
      <div class="trp-signal-groups">
        <div class="trp-section-title">📋 信号清单</div>
    `;

    groups.forEach(g => {
      const signals = rd.signals[g.key] || [];
      const triggeredCount = signals.filter(s => s.triggered).length;
      const hasAnyTriggered = triggeredCount > 0;

      html += `
        <details class="trp-group" ${hasAnyTriggered ? 'open' : ''}>
          <summary class="trp-group-header" style="--group-color:${g.color}">
            <span class="trp-group-name">${g.label}</span>
            <span class="trp-group-count">${triggeredCount}/${signals.length}</span>
          </summary>
          <div class="trp-group-body">
      `;

      signals.forEach(s => {
        html += `
          <div class="trp-signal-row ${s.triggered ? 'triggered' : ''}">
            <span class="trp-signal-status">${s.triggered ? '✅' : '❌'}</span>
            <div class="trp-signal-info">
              <div class="trp-signal-name">${s.name} <span class="trp-signal-score">(${s.score}分)</span></div>
              <div class="trp-signal-meta">
                <span class="trp-signal-value">${s.current_value || '--'}</span>
                <span class="trp-signal-source">📌 ${s.data_source}</span>
                <span class="trp-signal-time">🕐 ${s.update_time || '--'}</span>
              </div>
            </div>
          </div>
        `;
      });

      html += '</div></details>';
    });

    html += '</div>';
    return html;
  },

  renderStyleRotationRight(rd) {
    const sr = rd.style_rotation;
    if (!sr) return '';

    let html = `
      <div class="trp-style-rotation">
        <div class="trp-section-title">🔄 风格轮动</div>
        <div class="trp-style-grid">
    `;

    if (sr.large_small) {
      const ls = sr.large_small;
      html += `
        <div class="trp-style-item">
          <div class="trp-style-label">大盘/小盘比值</div>
          <div class="trp-style-val">${ls.ratio != null ? ls.ratio.toFixed(3) : '--'}</div>
          <div class="trp-style-signal">${ls.trend || '--'}</div>
        </div>
      `;
    }

    if (sr.dividend_premium) {
      const dp = sr.dividend_premium;
      html += `
        <div class="trp-style-item">
          <div class="trp-style-label">红利溢价</div>
          <div class="trp-style-val">${dp.value != null ? dp.value : '--'}</div>
          <div class="trp-style-signal">${dp.signal || '--'}</div>
        </div>
      `;
    }

    html += '</div></div>';
    return html;
  },

  // ================================================================
  // BOTTOM CONCLUSION
  // ================================================================
  renderConclusion(d, rd) {
    // Determine market position
    const compositeScore = d.composite_score || 50;
    const signalLight = rd.signal_light || 'none';
    const bullScore = rd.bull_score || 0;
    const bearScore = rd.bear_score || 0;
    const trendBias = rd.trend_bias || 'neutral';

    // Market position (based on left side score)
    let marketPos, posIcon, posColor;
    if (compositeScore < 35) {
      marketPos = '底部区域';
      posIcon = '🟢';
      posColor = 'bull';
    } else if (compositeScore < 65) {
      marketPos = '中性区域';
      posIcon = '🟡';
      posColor = 'neutral';
    } else {
      marketPos = '顶部区域';
      posIcon = '🔴';
      posColor = 'bear';
    }

    // Trend direction (based on signal light)
    let trendDir, trendIcon;
    if (signalLight === 'green') {
      trendDir = '多头确认';
      trendIcon = '📈';
    } else if (signalLight === 'red') {
      trendDir = '空头确认';
      trendIcon = '📉';
    } else if (signalLight === 'yellow' || signalLight === 'orange') {
      if (rd.signal_direction === 'bull') {
        trendDir = '多头形成中';
        trendIcon = '↗️';
      } else if (rd.signal_direction === 'bear') {
        trendDir = '空头形成中';
        trendIcon = '↘️';
      } else {
        trendDir = '趋势模糊';
        trendIcon = '↔️';
      }
    } else {
      // No light — use bias
      if (trendBias === 'warm') {
        trendDir = '偏暖震荡';
        trendIcon = '↗️';
      } else if (trendBias === 'cool') {
        trendDir = '偏冷震荡';
        trendIcon = '↘️';
      } else {
        trendDir = '震荡/真空';
        trendIcon = '➡️';
      }
    }

    // Internal structure (from left side signals)
    let structure, structIcon;
    const bottomActive = (d.signals?.bottom || []).filter(s => s.active).length;
    const topActive = (d.signals?.top || []).filter(s => s.active).length;
    if (bottomActive >= 4) {
      structure = '普涨格局';
      structIcon = '🟢';
    } else if (topActive >= 4) {
      structure = '普跌格局';
      structIcon = '🔴';
    } else {
      structure = '结构分化';
      structIcon = '🟡';
    }

    // Position advice (combining left + right)
    let posAdvice, adviceDetail;
    const leftScore = compositeScore;
    const rightSignal = signalLight;

    if (leftScore < 35 && (rightSignal === 'none' || rightSignal === 'yellow' && rd.signal_direction === 'bull')) {
      posAdvice = '建立底仓 10%-30%';
      adviceDetail = '左侧估值进入底部，右侧信号初步转暖';
    } else if (leftScore < 35 && rightSignal === 'orange' && rd.signal_direction === 'bull') {
      posAdvice = '加仓至 30%-50%';
      adviceDetail = '左侧低估 + 右侧确认信号触发';
    } else if (leftScore < 40 && rightSignal === 'orange') {
      posAdvice = '加仓至 60%-80%';
      adviceDetail = '估值偏低 + 多重确认信号';
    } else if (rightSignal === 'green') {
      posAdvice = '持有为主';
      adviceDetail = '牛市已确认，不追加，享受趋势';
    } else if (leftScore > 65 && rightSignal === 'none') {
      posAdvice = '开始减仓';
      adviceDetail = '估值偏高，等待右侧信号';
    } else if (rightSignal === 'yellow' && rd.signal_direction === 'bear') {
      posAdvice = '减仓至 30%-40%';
      adviceDetail = '右侧早期熊市信号触发';
    } else if (rightSignal === 'orange' && rd.signal_direction === 'bear') {
      posAdvice = '大幅减仓至 10%-20%';
      adviceDetail = '熊市确认信号触发';
    } else if (rightSignal === 'red') {
      posAdvice = '清仓';
      adviceDetail = '最强熊市确认';
    } else {
      posAdvice = '维持当前仓位';
      adviceDetail = '左右侧信号不明确，不主动调整';
    }

    // Key risks
    let risks = [];
    // Check for conflicting signals
    if (bullScore > 30 && bearScore > 30) {
      risks.push('多空信号交织，谨慎操作');
    }
    // Check if key signals are close to triggering
    const earlyBullClose = (rd.signals?.early_bull || []).filter(s => !s.triggered && s.current_value && s.current_value !== '数据待获取').length;
    if (earlyBullClose >= 3 && signalLight === 'none') {
      risks.push(`多个早期牛市信号接近触发(${earlyBullClose}个)`);
    }
    const earlyBearClose = (rd.signals?.early_bear || []).filter(s => !s.triggered && s.current_value && s.current_value !== '数据待获取').length;
    if (earlyBearClose >= 3 && signalLight === 'none') {
      risks.push(`多个早期熊市信号接近触发(${earlyBearClose}个)`);
    }
    if (leftScore > 70) {
      risks.push('左侧估值已进入高位预警区');
    }
    if (leftScore < 25) {
      risks.push('左侧估值极低，注意系统性风险');
    }
    if (risks.length === 0) {
      risks.push('暂无特别风险提示');
    }

    // Card background
    let cardBg;
    if (posColor === 'bull') cardBg = 'conclusion-bull';
    else if (posColor === 'bear') cardBg = 'conclusion-bear';
    else cardBg = 'conclusion-neutral';

    let html = `
      <div class="timing-conclusion-section">
        <div class="section-header">
          <div class="section-title">🎯 当前结论</div>
          <div class="section-desc">综合左侧估值 + 右侧趋势确认 → 操作建议</div>
        </div>
        <div class="timing-conclusion-card ${cardBg}">
          <div class="conclusion-grid">
            <div class="conclusion-item">
              <div class="conclusion-label">市场位置</div>
              <div class="conclusion-value">${posIcon} ${marketPos}</div>
              <div class="conclusion-sub">左侧评分 ${compositeScore}分</div>
            </div>
            <div class="conclusion-item">
              <div class="conclusion-label">趋势方向</div>
              <div class="conclusion-value">${trendIcon} ${trendDir}</div>
              <div class="conclusion-sub">${signalLight === 'none' ? '信号灯: ⚪无' : '信号灯: ' + signalLight}</div>
            </div>
            <div class="conclusion-item">
              <div class="conclusion-label">内部结构</div>
              <div class="conclusion-value">${structIcon} ${structure}</div>
              <div class="conclusion-sub">底${bottomActive}项 / 顶${topActive}项</div>
            </div>
            <div class="conclusion-item conclusion-highlight">
              <div class="conclusion-label">💡 仓位建议</div>
              <div class="conclusion-value">${posAdvice}</div>
              <div class="conclusion-sub">${adviceDetail}</div>
            </div>
          </div>
          <div class="conclusion-risks">
            <div class="conclusion-risks-title">⚠️ 关键风险</div>
            <ul>
              ${risks.map(r => `<li>${r}</li>`).join('')}
            </ul>
          </div>
          <div class="conclusion-footer">
            <span>数据更新: ${d.update_date}</span>
            <span>·</span>
            <span>左侧来源: 交易所/央行/统计局</span>
            <span>·</span>
            <span>右侧来源: 交易所行情/两融/沪深港通</span>
          </div>
        </div>
      </div>
    `;

    // Also render original sections below conclusion
    html += this.renderStyleRotation(d);
    html += this.renderPositionAdvice(d);
    html += this.renderSignals(d);
    html += this.renderTriggers(d);

    return html;
  },

  // ================================================================
  // LEFT PANEL (existing code, unchanged)
  // ================================================================

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

  // ================================================================
  // Charts
  // ================================================================

  renderMiniCharts() {
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

      const hexToRgba = (hex, alpha) => {
        const r = parseInt(hex.slice(1,3), 16);
        const g = parseInt(hex.slice(3,5), 16);
        const b = parseInt(hex.slice(5,7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
      };

      const option = {
        animation: true,
        animationDuration: 600,
        grid: { top: 4, bottom: 4, left: 4, right: 4 },
        xAxis: { type: 'category', data: dates, show: false, boundaryGap: false },
        yAxis: { type: 'value', show: false, scale: true },
        series: [{
          type: 'line', data: values, smooth: true, symbol: 'none',
          lineStyle: { width: 1.5, color: color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: hexToRgba(color, 0.25) },
              { offset: 1, color: hexToRgba(color, 0.02) },
            ]),
          },
        }],
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#1e293b',
          borderColor: '#334155',
          textStyle: { color: '#e2e8f0', fontSize: 10 },
        },
      };

      chart.setOption(option);
    });
  },

  renderRadarChart(d) {
    const container = document.getElementById('timing-radar-chart');
    if (!container || !d) return;

    if (this.radarChart) this.radarChart.dispose();
    this.radarChart = echarts.init(container);

    const dims = Object.entries(d.dimensions);
    const indicator = dims.map(([key, dim]) => ({ name: dim.name, max: 100 }));
    const values = dims.map(([key, dim]) => dim.score);

    const option = {
      animation: true,
      animationDuration: 800,
      radar: {
        indicator, shape: 'polygon', radius: '65%', center: ['50%', '52%'],
        axisName: { color: '#94a3b8', fontSize: 11, fontWeight: 500 },
        splitArea: { areaStyle: { color: ['rgba(30, 41, 59, 0.3)', 'rgba(30, 41, 59, 0.15)'] } },
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.1)' } },
        axisLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.15)' } },
      },
      series: [{
        type: 'radar',
        data: [
          {
            value: values, name: '当前评分', symbol: 'circle', symbolSize: 6,
            lineStyle: { color: '#3b82f6', width: 2 },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
                { offset: 1, color: 'rgba(59, 130, 246, 0.05)' },
              ]),
            },
            itemStyle: { color: '#3b82f6', borderColor: '#1e293b', borderWidth: 2 },
          },
          {
            value: [50, 50, 50, 50, 50, 50], name: '中性线', symbol: 'none',
            lineStyle: { color: 'rgba(245, 158, 11, 0.4)', width: 1, type: 'dashed' },
            areaStyle: { color: 'rgba(245, 158, 11, 0.03)' },
          },
        ],
      }],
      tooltip: {
        trigger: 'item', backgroundColor: '#1e293b', borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
      },
    };

    this.radarChart.setOption(option);
    const observer = new ResizeObserver(() => this.radarChart.resize());
    observer.observe(container);
  },

  renderScoreGauge(d) {
    const container = document.getElementById('timing-score-gauge');
    if (!container || !d) return;

    if (this.scoreGaugeChart) this.scoreGaugeChart.dispose();
    this.scoreGaugeChart = echarts.init(container);

    const scoreColor = this.getScoreColor(d.composite_score);

    const option = {
      animation: true, animationDuration: 1200, animationEasing: 'cubicOut',
      series: [{
        type: 'gauge', startAngle: 220, endAngle: -40, min: 0, max: 100, radius: '90%', center: ['50%', '55%'],
        progress: { show: true, width: 14, roundCap: true, itemStyle: { color: scoreColor } },
        pointer: { show: false },
        axisLine: {
          lineStyle: { width: 14, color: [[0.2,'#10b981'],[0.35,'#22c55e'],[0.65,'#f59e0b'],[0.8,'#f97316'],[1,'#ef4444']], opacity: 0.2 },
        },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, title: { show: false },
        detail: { valueAnimation: true, fontSize: 36, fontWeight: 800, color: scoreColor, offsetCenter: [0, '0%'], formatter: '{value}' },
        data: [{ value: d.composite_score }],
      }],
    };

    this.scoreGaugeChart.setOption(option);
    const observer = new ResizeObserver(() => this.scoreGaugeChart.resize());
    observer.observe(container);
  },

  renderBullBar(rd) {
    // Simple CSS-based bar (already rendered in HTML)
  },

  renderBearBar(rd) {
    // Simple CSS-based bar (already rendered in HTML)
  },

  // ================================================================
  // Utility
  // ================================================================

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
    if (this.radarChart) { this.radarChart.dispose(); this.radarChart = null; }
    if (this.scoreGaugeChart) { this.scoreGaugeChart.dispose(); this.scoreGaugeChart = null; }
    if (this.historyChart) { this.historyChart.dispose(); this.historyChart = null; }
    if (this._historyResizeObserver) { this._historyResizeObserver.disconnect(); this._historyResizeObserver = null; }
    this.miniCharts.forEach(c => c.dispose());
    this.miniCharts = [];
    if (this.miniChartObserver) { this.miniChartObserver.disconnect(); this.miniChartObserver = null; }
  },
};

// Hook into the main app's tab system
(function() {
  // The v2 patch handles tab routing. This IIFE is kept for standalone usage.
  if (typeof app !== 'undefined' && typeof app.switchTab === 'function') {
    // Already patched by app_v2_patch.js
    return;
  }

  // Fallback for standalone usage (without app_v2_patch.js)
  const originalRenderTab = typeof app !== 'undefined' ? app.renderTab : null;
  const originalSwitchTab = typeof app !== 'undefined' ? app.switchTab : null;

  if (typeof app !== 'undefined') {
    app.renderTab = function(tab) {
      if (tab === 'timing') {
        TimingTab.init();
      } else if (originalRenderTab) {
        originalRenderTab.call(app, tab);
      }
    };

    app.switchTab = function(tab) {
      if (app.currentTab === 'timing' && tab !== 'timing') {
        TimingTab.dispose();
      }
      if (originalSwitchTab) {
        originalSwitchTab.call(app, tab);
      }
    };
  }
})();
