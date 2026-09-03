/**
 * cycle_v3.js - 全球周期看板 V3 渲染模块
 * 
 * 覆盖 cycle_v2.js，实现 spec v3 全部 25 个改进点
 * 
 * 数据结构: cycle_position_v3.json
 * 入口: CycleV3Module.render(data)
 */

const CycleV3Module = (() => {
  let chartInstances = [];
  const LAYER_MAP = {
    0: 'layer_0_debt_cycle', 1: 'layer_1_kondratieff', 2: 'layer_2_perez',
    3: 'layer_3_rate_regime', 4: 'layer_4_juglar', 5: 'layer_5_kitchner', 6: 'layer_6_merrill'
  };
  const COLORS = {
    bullish: '#10b981', cautiousBullish: '#84cc16', neutral: '#f59e0b',
    cautiousBearish: '#f97316', bearish: '#ef4444', blue: '#3b82f6',
    purple: '#8b5cf6', cyan: '#06b6d4', red: '#ef4444',
    textPrimary: '#e2e8f0', textSecondary: '#94a3b8', textMuted: '#64748b',
    bgCard: '#111827', bgCardHover: '#1a2332', borderSubtle: '#1e293b',
    tooltipBg: '#1e293b', tooltipBorder: '#334155',
    // asset signal colors
    signalBg: { bullish:'#dcfce7', neutral_bullish:'#fef3c7', neutral:'#fef9c3', neutral_bearish:'#f3f4f6', bearish:'#fee2e2' },
    signalText: { bullish:'#166534', neutral_bullish:'#92400e', neutral:'#854d0e', neutral_bearish:'#4b5563', bearish:'#991b1b' }
  };
  const SIGNAL_WEIGHT_MAP = {
    'trough_to_recovery':2,'early_recovery':2,'expansion':2,'active_restocking':2,'recovery':2,
    'deployment':2,'early_expansion':1,'k_shaped_restocking':1,'passive_destocking':1,
    'recovery_to_overheat':1,'moderate_expansion':1,'mild_recovery':1,
    'neutral':0,'neutral_tight':0,'structural_deleveraging_late':0,'overheat':0,
    'frenzy_late':-1,'high_rate_transition':-1,
    'late_deleveraging_to_smooth':-2,'turning_point_anticipation':-2,'contraction':-2,
    'active_destocking':-2,'stagflation':-2,'recession':-2
  };
  const PHASE_COLORS = {
    bullish:'#10b981', cautious:'#f59e0b', bearish:'#ef4444',
    caution_bullish:'#84cc16', neutral:'#6b7280', constraint:'#f59e0b'
  };

  // ========== Utilities ==========
  function tooltipConfig() {
    return { backgroundColor:COLORS.tooltipBg, borderColor:COLORS.tooltipBorder, borderWidth:1,
      textStyle:{ color:COLORS.textPrimary, fontSize:12, fontFamily:'JetBrains Mono' }, padding:[8,12] };
  }
  function gridConfig(extra={}) {
    return Object.assign({top:36,right:16,bottom:28,left:16,containLabel:true}, extra);
  }
  function createChart(id, option) {
    const el = document.getElementById(id);
    if (!el) return null;
    const chart = echarts.init(el, null, {renderer:'canvas'});
    chart.setOption(option);
    chartInstances.push(chart);
    return chart;
  }
  function fmtNum(v, decimals=1) { return v == null ? '--' : Number(v).toFixed(decimals); }
  function fmtPct(v, decimals=1) { return v == null ? '--' : Number(v).toFixed(decimals) + '%'; }
  function escapeHtml(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // Frequency-aware thresholds
  const FREQ_THRESHOLDS = {
    daily:    { fresh: 7, stale: 30, label: '日频' },
    weekly:   { fresh: 14, stale: 60, label: '周频' },
    monthly:  { fresh: 45, stale: 120, label: '月频' },
    quarterly:{ fresh: 120, stale: 270, label: '季频' },
    annual:   { fresh: 400, stale: 730, label: '年频' },
    event_driven: { fresh: 365, stale: 730, label: '事件驱动' }
  };

  function getFreshness(lastUpdated, frequency) {
    if (!lastUpdated) return { cls:'expired', label:'数据待更新', days:999 };
    const days = Math.floor((Date.now() - new Date(lastUpdated)) / 86400000);
    const thresh = FREQ_THRESHOLDS[frequency] || FREQ_THRESHOLDS.monthly;
    const freqLabel = thresh.label;
    if (days <= thresh.fresh) return { cls:'fresh', label:\`${days}天前\`, days };
    if (days <= thresh.stale) return { cls:'stale', label:\`⚠️ \${days}天未更新\`, days };
    return { cls:'expired', label:\`\${days}天未更新\`, days };
  }

  function freshnessBadge(lastUpdated, frequency) {
    const f = getFreshness(lastUpdated, frequency);
    const freqTag = frequency && frequency !== 'daily' && frequency !== 'weekly'
      ? \` <span style="font-size:9px;opacity:0.7">\${FREQ_THRESHOLDS[frequency]?.label||''}</span>\` : '';
    return \`<span class="freshness-badge \${f.cls}" title="\${f.label}">\${f.cls==='fresh'?freqTag:f.label}\${freqTag}</span>\`;
  }

  function signalColor(signal) {
    const map = { bullish:COLORS.bullish, neutral_bullish:'#f59e0b', neutral:'#9ca3af',
      neutral_bearish:'#9ca3af', bearish:COLORS.bearish, cautious:'#f59e0b' };
    return map[signal] || COLORS.neutral;
  }

  function signalBg(signal) { return COLORS.signalBg[signal] || '#f3f4f6'; }
  function signalFg(signal) { return COLORS.signalText[signal] || '#4b5563'; }

  // ========== Section Renderers ==========

  /** _meta header bar */
  function renderMeta(meta) {
    if (!meta) return '';
    const score = meta.data_freshness_score || 0;
    const scoreColor = score >= 80 ? '#10b981' : score >= 60 ? '#f59e0b' : '#ef4444';
    const scoreLabel = score >= 80 ? '新鲜' : score >= 60 ? '一般' : '老化';
    return `
    <div class="meta-header">
      <div class="meta-left">
        <span class="meta-date">📅 评估日期: ${meta.assessment_date || '--'}</span>
        <span class="meta-version">版本 v${meta.version || '3.0'}</span>
        <span class="meta-next">下次评估: ${meta.next_assessment_date || '--'}</span>
      </div>
      <div class="meta-right">
        <span class="freshness-score" style="color:${scoreColor}">
          数据新鲜度: <strong>${score}</strong>/100 (${scoreLabel})
        </span>
      </div>
    </div>`;
  }

  /** 改进点#1: 大债务周期 Layer 0 */
  function renderDebtCycle(layer) {
    if (!layer) return '';
    const renderRegion = (r, key) => {
      if (!r) return '';
      const sw = r.signal_weight || 0;
      const swColor = sw > 0 ? COLORS.bullish : sw < 0 ? COLORS.bearish : COLORS.neutral;
      const indicators = r.indicators || {};
      const indCards = Object.entries(indicators).map(([k, ind]) => {
        const f = freshnessBadge(ind.last_updated, ind.frequency);
        let threshHtml = '';
        if (ind.threshold) {
          const w = ind.threshold.warning || ind.threshold.critical || '';
          const c = ind.threshold.critical || '';
          threshHtml = `<div class="ind-threshold">阈值: 警告=${w} 临界=${c}</div>`;
        }
        let historyMini = '';
        if (ind.history && ind.history.length > 0) {
          const pts = ind.history.map(h => `${h.date}:${h.value}`).join(',');
          historyMini = `<div class="ind-history" data-points="${escapeHtml(pts)}"></div>`;
        }
        return `
        <div class="indicator-card ${getFreshness(ind.last_updated, ind.frequency).cls}">
          <div class="ind-header"><span class="ind-name">${escapeHtml(ind.name)}</span>${f}</div>
          <div class="ind-value">${fmtNum(ind.current)} <span class="ind-unit">${escapeHtml(ind.unit||'')}</span></div>
          ${threshHtml}
          <div class="ind-source" title="${escapeHtml(ind.source||'')}">来源: ${escapeHtml((ind.source||'').split('(')[0])}</div>
          ${historyMini}
        </div>`;
      }).join('');
      return `
      <div class="region-card debt-region">
        <div class="region-header">
          <span class="region-flag">${key==='us'?'🇸':'🇨🇳'}</span>
          <span class="region-name">${key==='us'?'美国':'中国'}</span>
          <span class="region-phase">${escapeHtml(r.phase||'')}</span>
          <span class="signal-weight-badge" style="color:${swColor}">${sw>0?'+':''}${sw}</span>
        </div>
        <p class="region-desc">${escapeHtml(r.description||'')}</p>
        <p class="region-implication">💡 ${escapeHtml(r.implication||'')}</p>
        <div class="indicators-grid">${indCards}</div>
      </div>`;
    };
    return `
    <section class="v3-section" id="section-debt-cycle">
      <h2 class="section-title"> 达里奥·大债务周期 <span class="section-subtitle">第0层 · 约束层</span></h2>
      <p class="section-desc">${escapeHtml(layer.description||'')}</p>
      <div class="regions-row">
        ${renderRegion(layer.us, 'us')}
        ${renderRegion(layer.cn, 'cn')}
      </div>
      ${renderStructuralReform(layer.cn?.structural_reform)}
    </section>`;
  }

  // G-11: Structural reform rendering (方案D)
  function renderStructuralReform(sr) {
    if (!sr) return '';
    
    const proxies = (sr.proxy_indicators || []).map(p => {
      if (p.quantifiable) {
        const f = freshnessBadge(p.last_updated, p.frequency);
        return \`<div class="reform-card">
          <div class="reform-name">\${escapeHtml(p.name)} \${f}</div>
          <div class="reform-value">\${p.current}\${escapeHtml(p.unit||'')}</div>
          <div class="reform-trend">\${escapeHtml(p.trend||'')}</div>
          <div class="reform-desc">\${escapeHtml(p.description||'')}</div>
        </div>\`;
      } else {
        return \`<div class="reform-card reform-qualitative">
          <div class="reform-name">\${escapeHtml(p.name)}</div>
          <div class="reform-assessment">\${escapeHtml(p.assessment||'')}</div>
          <div class="reform-desc">\${escapeHtml(p.note||'')}</div>
        </div>\`;
      }
    }).join('');
    
    return \`
    <div class="reform-section">
      <h3>🏗️ 结构性改革进度（方案D：代理指标+定性标注）</h3>
      <p class="reform-note">\${escapeHtml(sr.qualitative_assessment||'')}</p>
      <div class="reform-grid">\${proxies}</div>
    </div>\`;
  }


  /** 康波 × 大宗商品判断框架（周金涛经典预判） */
  function renderCommodityFramework(layer) {
    const cf = layer?.commodity_framework;
    if (!cf) return '';
    const assetCards = (cf.asset_mapping||[]).map(a => {
      const color = a.signal==='bullish' ? '#10b981' : a.signal==='bearish' ? '#ef4444' : '#f59e0b';
      return `<div class="commodity-card" style="border-left:3px solid ${color}">
        <div class="comm-asset" style="color:${color}">${escapeHtml(a.asset)}</div>
        <div class="comm-reasoning">${escapeHtml(a.reasoning)}</div>
      </div>`;
    }).join('');
    const cases = (cf.historical_cases||[]).map(c =>
      `<div class="case-row"><span class="case-year">${escapeHtml(c.year)}</span><span class="case-wave">${escapeHtml(c.wave)}</span><span class="case-comm">${escapeHtml(c.commodity)}</span></div>`
    ).join('');
    return `
    <div class="commodity-framework">
      <h3>🛢️ ${escapeHtml(cf.title||'')}</h3>
      <p class="comm-desc">${escapeHtml((cf.description||'').replace(/\n/g,'<br>'))}</p>
      <div class="comm-current">
        <h4>当前含义</h4>
        <p>${escapeHtml((cf.current_phase_implication||'').replace(/\n/g,'<br>'))}</p>
      </div>
      <div class="comm-assets">${assetCards}</div>
      <div class="comm-cases">
        <h4>历史验证记录</h4>
        <div class="cases-table">${cases}</div>
      </div>
    </div>`;
  }

  /** 改进点#10 + #18: 康波 with TFP chart + percentile bands */
  function renderKondratieff(layer) {
    if (!layer) return '';
    const cnTfp = layer.indicators?.tfp_growth?.cn;
    const usTfp = layer.indicators?.tfp_growth?.us;
    let tfpChartHtml = '';
    if (cnTfp?.history?.length) {
      tfpChartHtml = `<div id="chart-kondratieff-tfp" class="chart-container" style="width:100%;height:320px;"></div>`;
    }
    const evidenceHtml = (layer.key_evidence||[]).map(e => `<li>${escapeHtml(e)}</li>`).join('');
    const adviceHtml = (layer.investment_advice||[]).map(a => `<li>${escapeHtml(a)}</li>`).join('');
    const historyWaves = (layer.history||[]).map(w =>
      `<span class="wave-tag">${escapeHtml(w.name)} (${escapeHtml(w.start)}-${escapeHtml(w.end)})</span>`
    ).join('');
    return `
    <section class="v3-section" id="section-kondratieff">
      <h2 class="section-title">🌊 康波（康德拉季耶夫长波）<span class="section-subtitle">第1层</span>
        <span class="freshness-badge">${freshnessBadge(layer.last_updated)}</span>
      </h2>
      <div class="phase-banner" style="border-left:4px solid ${COLORS.bullish}">
        <span class="phase-text">${escapeHtml(layer.current_phase||'')}</span>
        <span class="signal-weight-badge" style="color:${COLORS.bullish}">${layer.signal_weight>0?'+':''}${layer.signal_weight}</span>
        <span class="confidence-badge">${layer.confidence==='high'?'高置信度':layer.confidence==='medium'?'中置信度':'低置信度'}</span>
      </div>
      <div class="usage-box"><strong>投资含义:</strong> ${escapeHtml(layer.investment_usage||'')}</div>
      ${tfpChartHtml}
      <div class="evidence-box">
        <h4>关键证据</h4>
        <ul>${evidenceHtml}</ul>
      </div>
      <div class="advice-box">
        <h4>配置建议</h4>
        <ul>${adviceHtml}</ul>
      </div>
      <div class="waves-timeline">${historyWaves}</div>
      ${renderCommodityFramework(layer)}
    </section>`;
  }

  /** 改进点#8 + #19 + #22: 佩雷斯 with Turning Point + threshold_params */
  function renderPerez(layer) {
    if (!layer) return '';
    const tpSignals = (layer.turning_point_signals||[]).map(s => {
      const color = s.status==='green'?'#10b981':s.status==='yellow'?'#f59e0b':'#ef4444';
      return `
      <div class="tp-signal-card" style="border-left:3px solid ${color}">
        <div class="tp-name">${escapeHtml(s.indicator)}</div>
        <div class="tp-value">当前: <strong>${s.current_value}</strong>${s.metric?' ('+escapeHtml(s.metric)+')':''}</div>
        <div class="tp-threshold">阈值: ${s.threshold||s.threshold_trigger||'--'}</div>
        <div class="tp-desc">${escapeHtml(s.description||'')}</div>
        <div class="tp-status" style="color:${color}">${s.status==='green'?'✅ 安全':s.status==='yellow'?'⚠️ 观察':' 预警'}</div>
      </div>`;
    }).join('');

    const kr = layer.key_ratio;
    let krHtml = '';
    if (kr) {
      const tp = kr.threshold_params || {};
      const isFrenzy = kr.current_value >= (tp.frenzy_threshold || 2.7);
      krHtml = `
      <div class="key-ratio-card">
        <h4>📊 ${escapeHtml(kr.name)}</h4>
        <div class="kr-value">当前: <strong>${fmtNum(kr.current_value, 2)}</strong></div>
        <div class="kr-threshold">阈值: ${escapeHtml(kr.threshold||'')}</div>
        ${tp.mean!=null?`<div class="kr-params">均值=${tp.mean} | σ=${tp.std_dev} | Frenzy阈值=${tp.frenzy_threshold} <span class="kr-note">${escapeHtml(tp.note||'')}</span></div>`:''}
        ${isFrenzy?'<div class="kr-warning" style="color:#ef4444">️ 当前值已超过Frenzy阈值!</div>':''}
        <div id="chart-perez-ratio" class="chart-container" style="width:100%;height:200px;margin-top:8px;"></div>
      </div>`;
    }
    return `
    <section class="v3-section" id="section-perez">
      <h2 class="section-title">🔬 佩雷斯（技术革命周期）<span class="section-subtitle">第2层</span>
        ${freshnessBadge(layer.last_updated)}
      </h2>
      <div class="phase-banner" style="border-left:4px solid ${COLORS.cautious}">
        <span class="phase-text">${escapeHtml(layer.current_phase||'')}</span>
        <span class="signal-weight-badge" style="color:${COLORS.cautious}">${layer.signal_weight}</span>
      </div>
      <div class="tp-section">
        <h3 class="tp-title">🚨 Turning Point 预警子模块</h3>
        <div class="tp-grid">${tpSignals}</div>
      </div>
      ${krHtml}
    </section>`;
  }

  /** 改进点#2: 高利率时代跟踪 */
  function renderRateRegime(layer, highRateTracker) {
    if (!layer && !highRateTracker) return '';
    const allIndicators = [];
    ['structural','forward_looking','market_based'].forEach(group => {
      const sub = layer?.[group];
      if (!sub) return;
      // Handle both old format (direct indicators) and new format ({name, indicators})
      const inds = sub.indicators || {};
      Object.entries(inds).forEach(([k, ind]) => {
        const item = {...ind, layer_group: group, ind_key: k};
        // Handle dual-region indicators (us/cn sub-fields)
        if (ind.us || ind.cn) {
          ['us','cn'].forEach(rk => {
            const rData = ind[rk];
            if (rData && typeof rData === 'object') {
              allIndicators.push({
                ...rData, name: ind.name + (${JSON.stringify(rk==='us'?' 🇺🇸':' 🇨🇳')}${rk==='us'?'':' '}),
                source: ind.source, layer_group: group, ind_key: k+'_'+rk,
                last_updated: rData.last_updated || ind.last_updated,
                frequency: rData.frequency || ind.frequency,
                description: ind.description,
                history: rData.history || ind.history
              });
            }
          });
        } else {
          allIndicators.push(item);
        }
      });
    });
    // Also add from high_rate_tracker if present
    if (highRateTracker?.indicators) {
      highRateTracker.indicators.forEach(ind => { allIndicators.push({...ind, layer_group: ind.layer}); });
    }

    const groupLabels = { structural:'🏗️ 结构性因素', forward_looking:'🔭 前瞻指标', market_based:'📊 市场指标' };
    const groups = {};
    allIndicators.forEach(ind => {
      const g = ind.layer_group || 'other';
      if (!groups[g]) groups[g] = [];
      groups[g].push(ind);
    });

    let gridHtml = '';
    Object.entries(groups).forEach(([g, items]) => {
      const cards = items.map(ind => {
        const color = ind.status==='green'?'#10b981':ind.status==='yellow'?'#f59e0b':ind.status==='red'?'#ef4444':'#6b7280';
        const f = freshnessBadge(ind.last_updated, ind.frequency);
        const val = ind.current_value ?? ind.current ?? '--';
        const descHtml = ind.description ? `<div class="ri-desc">${escapeHtml(ind.description)}</div>` : '';
        const chartId = 'chart-rate-'+group+'-'+(ind.ind_key||'');
        return `
        <div class="rate-indicator-card" style="border-left:3px solid ${color}">
          <div class="ri-header">
            <span class="ri-name">${escapeHtml(ind.name)}</span>
            <span class="ri-status" style="color:${color}">${escapeHtml(ind.status_label||ind.status||'')}</span>
          </div>
          <div class="ri-value">${fmtNum(val)} <span class="ri-unit">${escapeHtml(ind.unit||'')}</span></div>
          <div class="ri-threshold">${escapeHtml(ind.threshold||'')}</div>
          ${descHtml}
          <div id="${chartId}" class="chart-container" style="width:100%;height:100px;margin-top:4px;"></div>
          ${f}
        </div>`;
      }).join('');
      gridHtml += `<div class="rate-group"><h4>${groupLabels[g]||g}</h4><div class="indicators-grid">${cards}</div></div>`;
    });

    // Scenario table from high_rate_tracker
    let scenarioHtml = '';
    if (highRateTracker?.scenario_table) {
      scenarioHtml = `<div class="scenario-table">
        <h4>利率情景分析</h4>
        <table><thead><tr><th>情景</th><th>概率</th><th>触发条件</th><th>配置含义</th></tr></thead><tbody>
        ${highRateTracker.scenario_table.map(s =>
          `<tr><td><strong>${escapeHtml(s.scenario)}</strong></td><td>${escapeHtml(s.probability||'--')}</td><td>${escapeHtml(s.trigger||'')}</td><td>${escapeHtml(s.implication||'')}</td></tr>`
        ).join('')}
        </tbody></table></div>`;
    }

    const assessment = highRateTracker ? `
    <div class="assessment-box">
      <span class="assessment-label">${escapeHtml(highRateTracker.overall_assessment||'')}</span>
      <p>${escapeHtml(highRateTracker.assessment_detail||'')}</p>
      <p class="portfolio-implication">💡 ${escapeHtml(highRateTracker.implication_for_portfolio||'')}</p>
    </div>` : '';

    return `
    <section class="v3-section" id="section-rate-regime">
      <h2 class="section-title"> 高利率时代跟踪<span class="section-subtitle">第3层</span></h2>
      ${assessment}
      ${gridHtml}
      ${scenarioHtml}
    </section>`;
  }

  /** 朱格拉 / 基钦 / 美林 通用渲染 */
  function renderJuglar(layer) {
    if (!layer) return '';
    const renderRegion = (r, key) => {
      if (!r) return '';
      const swColor = signalColor(r.signal_weight > 0 ? 'bullish' : r.signal_weight < 0 ? 'bearish' : 'neutral');
      const indicators = r.indicators || {};
      const indHtml = Object.entries(indicators).map(([k, ind]) => {
        const pct = ind.percentile;
        let pctBadge = '';
        if (pct?.current_rank) {
          pctBadge = `<span class="percentile-badge" title="p25=${pct.p25} p50=${pct.p50} p75=${pct.p75}">${escapeHtml(pct.current_rank)}</span>`;
        }
        const f = freshnessBadge(ind.last_updated, ind.frequency);
        return `
        <div class="indicator-card ${getFreshness(ind.last_updated, ind.frequency).cls}">
          <div class="ind-header"><span class="ind-name">${escapeHtml(ind.name)}</span>${f} ${pctBadge}</div>
          <div class="ind-value">${fmtNum(ind.current)} <span class="ind-unit">${escapeHtml(ind.unit||'')}</span></div>
          <div id="chart-juglar-${key}-${k}" class="chart-container" style="width:100%;height:120px;margin-top:8px;"></div>
        </div>`;
      }).join('');
      return `
      <div class="region-card">
        <div class="region-header">
          <span class="region-flag">${key==='us'?'🇺🇸':'🇨'}</span>
          <span class="region-name">${key==='us'?'美国':'中国'}</span>
          <span class="region-phase">${escapeHtml(r.current_phase||r.phase||'')}</span>
          <span class="signal-weight-badge" style="color:${swColor}">${r.signal_weight>0?'+':''}${r.signal_weight}</span>
        </div>
        <p class="region-desc">${escapeHtml(r.description||r.investment_usage||'')}</p>
        <div class="indicators-grid">${indHtml}</div>
        ${r.key_driver?`<div class="driver-box">🔑 ${escapeHtml(r.key_driver)}</div>`:''}
        ${r.evidence?`<div class="evidence-box"><h4>证据</h4><ul>${r.evidence.map(e=>`<li>${escapeHtml(e)}</li>`).join('')}</ul></div>`:''}
        ${r.investment_advice?`<div class="advice-box"><h4>配置建议</h4><ul>${r.investment_advice.map(a=>`<li>${escapeHtml(a)}</li>`).join('')}</ul></div>`:''}
      </div>`;
    };
    return renderRegion(layer.us, 'us') + renderRegion(layer.cn, 'cn');
  }

  /** 美林时钟 with quadrant_trajectory */
  function renderMerrill(layer) {
    if (!layer) return '';
    const renderRegion = (r, key) => {
      if (!r) return '';
      const swColor = signalColor(r.signal_weight > 0 ? 'bullish' : r.signal_weight < 0 ? 'bearish' : 'neutral');
      const trajectory = r.quadrant_trajectory || [];
      let trajHtml = '';
      if (trajectory.length > 0) {
        trajHtml = `<div class="trajectory-box"><h4>美林时钟轨迹</h4><div class="trajectory-chain">${
          trajectory.map(t => `<span class="traj-node"><span class="traj-label">${escapeHtml(t.label||'')}</span>${escapeHtml(t.quadrant)} <span class="traj-period">${escapeHtml(t.start||'')}→${escapeHtml(t.end||'至今')}</span></span>`).join(' → ')
        }</div></div>`;
      }
      const indicators = r.indicators || {};
      const indHtml = Object.entries(indicators).map(([k, ind]) => {
        const f = freshnessBadge(ind.last_updated, ind.frequency);
        return `
        <div class="indicator-card ${getFreshness(ind.last_updated, ind.frequency).cls}">
          <div class="ind-header"><span class="ind-name">${escapeHtml(ind.name)}</span>${f}</div>
          <div class="ind-value">${fmtNum(ind.current)} <span class="ind-unit">${escapeHtml(ind.unit||'')}</span></div>
          <div id="chart-merrill-${key}-${k}" class="chart-container" style="width:100%;height:120px;margin-top:8px;"></div>
        </div>`;
      }).join('');
      return `
      <div class="region-card">
        <div class="region-header">
          <span class="region-flag">${key==='us'?'🇺🇸':'🇨🇳'}</span>
          <span class="region-name">${key==='us'?'美国':'中国'}</span>
          <span class="region-phase">${escapeHtml(r.current_phase||'')}</span>
          <span class="signal-weight-badge" style="color:${swColor}">${r.signal_weight>0?'+':''}${r.signal_weight}</span>
        </div>
        ${trajHtml}
        <div class="indicators-grid">${indHtml}</div>
      </div>`;
    };
    return renderRegion(layer.us, 'us') + renderRegion(layer.cn, 'cn');
  }


  /** 改进点#3: 周期共识度评分卡 - 置顶版本（结论先行） */
  function renderConsensusSummary(consensus) {
    if (!consensus) return '';
    const score = consensus.overall_score || 0;
    const scoreColor = score >= 70 ? '#10b981' : score >= 50 ? '#f59e0b' : score >= 30 ? '#f97316' : '#ef4444';
    const signal = consensus.overall_signal || 'neutral';
    const signalColor = signal === 'bullish' ? '#10b981' : signal === 'bearish' ? '#ef4444' : '#f59e0b';
    
    const allocation = consensus.asset_allocation_summary || {};
    const allocCards = Object.entries(allocation).map(([key, item]) => {
      const emoji = key === 'equity' ? '📈' : key === 'bonds' ? '📊' : key === 'commodities' ? '🛢️' : '💵';
      return `
        <div class="consensus-alloc-card">
          <div class="alloc-emoji">${emoji}</div>
          <div class="alloc-name">${key === 'equity' ? '股票' : key === 'bonds' ? '债券' : key === 'commodities' ? '商品' : '现金'}</div>
          <div class="alloc-weight" style="color:${signalColor}">${item.weight || '--'}</div>
          <div class="alloc-rec">${item.recommendation || ''}</div>
          <div class="alloc-reason">${item.rationale || ''}</div>
        </div>`;
    }).join('');
    
    const nesting = consensus.cycle_nesting || {};
    
    return `
    <section class="v3-section consensus-summary-section" id="section-consensus-summary">
      <div class="consensus-summary-header">
        <div class="consensus-score-block">
          <div class="consensus-score" style="color:${scoreColor}">${score}</div>
          <div class="consensus-label">综合周期评分</div>
        </div>
        <div class="consensus-assessment">
          <div class="assessment-text">${escapeHtml(consensus.overall_assessment || '')}</div>
          <div class="assessment-signal" style="color:${signalColor}">信号: ${signal === 'bullish' ? '偏多' : signal === 'bearish' ? '偏空' : '中性'}</div>
        </div>
        <div class="consensus-date">📅 ${consensus.last_updated || '--'}</div>
      </div>
      
      <!-- 资产配置建议卡片 -->
      <div class="consensus-alloc-grid">
        ${allocCards}
      </div>
      
      <!-- 周期嵌套解读 -->
      <div class="consensus-nesting-box">
        <h4>🔄 周期嵌套结构</h4>
        <p>${escapeHtml(nesting.description || '')}</p>
        <p class="nesting-interpretation">💡 ${escapeHtml(nesting.interpretation || '')}</p>
      </div>
      
      <!-- 风险提示 -->
      ${consensus.key_risks?.length ? `
      <div class="consensus-risks">
        <h4>⚠️ 关键风险</h4>
        <ul>${consensus.key_risks.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
      </div>` : ''}
      
      <button class="expand-detail-btn" onclick="document.getElementById('section-consensus-detail')?.scrollIntoView({behavior:'smooth'})">
        📊 查看各周期详细分析 ▾
      </button>
    </section>`;
  }

  /** 改进点#3: 共识度评分卡 - 详细版本 */
  function renderConsensus(consensus) {
    if (!consensus) return '';
    const score = consensus.score || 0;
    const scoreColor = score >= 70 ? '#10b981' : score >= 50 ? '#f59e0b' : score >= 30 ? '#f97316' : '#ef4444';
    const scenario = consensus.scenario || '--';
    const dims = consensus.dimension_scores || [];
    const dimRows = dims.map(d => {
      const sw = d.score ?? d.us_score ?? 0;
      const swColor = sw > 0 ? '#10b981' : sw < 0 ? '#ef4444' : '#6b7280';
      return `<tr>
        <td>${d.layer!=null?'L'+d.layer+' · ':''} ${escapeHtml(d.dimension)}</td>
        <td style="color:${swColor};font-weight:600">${sw>0?'+':''}${sw}</td>
        <td>×${d.weight||1}</td>
        <td style="font-weight:600">${d.weighted_score>0?'+':''}${fmtNum(d.weighted_score)}</td>
      </tr>`;
    }).join('');
    return `
    <section class="v3-section" id="section-consensus">
      <h2 class="section-title">🎯 周期共识度评分卡<span class="section-subtitle">分数+情景双轨制</span></h2>
      <div class="consensus-header">
        <div class="consensus-score" style="color:${scoreColor}">${score}</div>
        <div class="consensus-info">
          <div class="consensus-score-label">${getScoreLabel(score)}</div>
          <div class="consensus-scenario">情景: <strong>${escapeHtml(scenario)}</strong> - ${escapeHtml(consensus.scenario_description||'')}</div>
          <div class="consensus-date">${freshnessBadge(consensus.last_updated)} ${consensus.last_updated||''}</div>
        </div>
      </div>
      <table class="dimension-table">
        <thead><tr><th>维度</th><th>信号</th><th>权重</th><th>加权</th></tr></thead>
        <tbody>${dimRows}</tbody>
      </table>
    </section>`;
  }
  function getScoreLabel(s) {
    if (s>=80) return '强烈看多（黄金时代）';
    if (s>=60) return '温和看多（结构性机会）';
    if (s>=40) return '中性偏谨慎（上行空间受限）';
    if (s>=20) return '偏空（防御为主）';
    return '强烈看空（全面防御）';
  }

  /** 改进点#4: 中美周期错位矩阵 */
  function renderUsChinaMatrix(matrix) {
    if (!matrix) return '';
    const dims = matrix.dimensions || [];
    const relColors = { '共振':'#10b981', '错位':'#f59e0b', '反向':'#9ca3af' };
    const rows = dims.map(d => {
      const rc = relColors[d.relationship] || '#6b7280';
      const usColor = signalColor(d.us_signal);
      const cnColor = signalColor(d.cn_signal);
      return `<tr>
        <td><strong>${escapeHtml(d.name)}</strong></td>
        <td style="color:${usColor}">${escapeHtml(d.us_direction)}</td>
        <td><span class="signal-dot" style="background:${usColor}"></span>${escapeHtml(d.us_signal)}</td>
        <td style="color:${cnColor}">${escapeHtml(d.cn_direction)}</td>
        <td><span class="signal-dot" style="background:${cnColor}"></span>${escapeHtml(d.cn_signal)}</td>
        <td><span class="rel-badge" style="background:${rc}20;color:${rc};border:1px solid ${rc}">${escapeHtml(d.relationship)}</span></td>
      </tr>`;
    }).join('');
    return `
    <section class="v3-section" id="section-us-china-matrix">
      <h2 class="section-title"> 中美周期错位矩阵</h2>
      <div class="matrix-stats">
        <span class="stat共振" style="color:#10b981">共振: ${matrix.sync_count||0}</span>
        <span class="stat错位" style="color:#f59e0b">错位: ${matrix.divergence_count||0}</span>
        <span class="stat反向" style="color:#9ca3af">反向: ${matrix.opposite_count||0}</span>
      </div>
      <table class="matrix-table">
        <thead><tr><th>维度</th><th>🇸 美国方向</th><th>信号</th><th>🇨🇳 中国方向</th><th>信号</th><th>关系</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="matrix-implication">💡 ${escapeHtml(matrix.implication_for_a_stock||'')}</div>
    </section>`;
  }

  /** 改进点#5/#11/#14: 综合结论区 (资产配置 + 风险收益比) */
  function renderAssetAllocation(allocation) {
    if (!allocation) return '';
    const current = allocation.current || [];
    const cards = current.map(a => {
      const bg = signalBg(a.signal);
      const fg = signalFg(a.signal);
      const f = freshnessBadge(a.last_updated, a.frequency);
      const rrScore = a.risk_reward_score || 0;
      const rrColor = rrScore >= 7 ? '#10b981' : rrScore >= 5 ? '#f59e0b' : '#ef4444';
      const confBorder = a.confidence_level==='high'?'#10b981':a.confidence_level==='medium'?'#f59e0b':'#9ca3af';
      const drivers = (a.key_drivers||[]).map(d=>`<span class="driver-tag">${escapeHtml(d)}</span>`).join('');
      const triggers = (a.trigger_conditions||[]).map(t=>`<li>${escapeHtml(t)}</li>`).join('');
      return `
      <div class="asset-card" style="border-left:3px solid ${a.color||bg}">
        <div class="asset-header">
          <span class="asset-name">${escapeHtml(a.asset)}</span>
          <span class="asset-view" style="background:${bg};color:${fg}">${escapeHtml(a.view)}</span>
          <div class="rr-gauge" title="风险收益比">
            <svg viewBox="0 0 100 55" width="50" height="28">
              <path d="M10,50 A40,40 0 0,1 90,50" fill="none" stroke="#334155" stroke-width="6" stroke-linecap="round"/>
              <path d="M10,50 A40,40 0 0,1 ${10+80*rrScore/100},${50-40*Math.sin(Math.PI*rrScore/10)}" fill="none" stroke="${rrColor}" stroke-width="6" stroke-linecap="round"/>
              <text x="50" y="46" text-anchor="middle" fill="${rrColor}" font-size="14" font-weight="bold">${rrScore.toFixed(1)}</text>
            </svg>
          </div>
        </div>
        <div class="asset-drivers">${drivers}</div>
        <div class="asset-signal-detail">
          <span class="signal-count support">✅ ${a.supporting_signals||0} 支撑</span>
          <span class="signal-count oppose">❌ ${a.opposing_signals||0} 反对</span>
        </div>
        <div class="asset-reasoning" style="color:${fg};border-left:2px solid ${confBorder};padding-left:8px;font-size:12px">${escapeHtml(a.reasoning||'')}</div>
        ${a.signal_detail?.supporting?.length?`<div class="signal-expand"><strong>支撑:</strong> ${a.signal_detail.supporting.map(s=>escapeHtml(s)).join(', ')}</div>`:''}
        ${a.signal_detail?.opposing?.length?`<div class="signal-expand"><strong>反对:</strong> ${a.signal_detail.opposing.map(s=>escapeHtml(s)).join(', ')}</div>`:''}
        ${triggers?`<div class="trigger-box"><strong>触发调仓:</strong><ul>${triggers}</ul></div>`:''}
        <div class="asset-footer">${f} · 置信度: ${a.confidence_level||'--'}</div>
      </div>`;
    }).join('');

    // Scenario table - 折叠卡片式
    let scenarioHtml = '';
    if (allocation.scenario_table) {
      const scenarios = allocation.scenario_table;
      const currentScenario = scenarios.find(s => s.is_current) || scenarios[0];
      const scenarioCards = scenarios.map((s, idx) => {
        const isCurrent = s === currentScenario;
        const allocEntries = Object.entries(s.allocation||{}).map(([k,v]) =>
          `<span class="scenario-alloc"><span class="alloc-key">${escapeHtml(k)}</span><span class="alloc-val">${escapeHtml(v)}</span></span>`
        ).join('');
        return `
        <div class="scenario-card ${isCurrent ? 'scenario-current' : ''}" onclick="this.classList.toggle('expanded')">
          <div class="scenario-header">
            <span class="scenario-name">${escapeHtml(s.scenario)}</span>
            <span class="scenario-prob">${escapeHtml(s.probability||'--')}</span>
            ${isCurrent ? '<span class="scenario-badge">当前</span>' : ''}
          </div>
          <div class="scenario-label-text">${escapeHtml(s.label||'')}</div>
          <div class="scenario-detail">
            <div class="scenario-trigger">触发: ${escapeHtml(s.trigger||'暂无')}</div>
            <div class="scenario-alloc-grid">${allocEntries}</div>
          </div>
        </div>`;
      }).join('');
      scenarioHtml = `<div class="scenario-section"><h3>📋 情景配置</h3>
        <div class="scenario-cards-grid">${scenarioCards}</div>
      </div>`;
    }

    // Cycle calendar
    let calendarHtml = '';
    if (allocation.cycle_calendar) {
      const now = new Date();
      const futureEvents = allocation.cycle_calendar.filter(e => new Date(e.date) >= now).sort((a,b) => new Date(a.date)-new Date(b.date));
      const calNodes = futureEvents.map(e => {
        const dotColor = e.importance==='high'?'#ef4444':'#f59e0b';
        return `<div class="calendar-node">
          <div class="cal-dot" style="background:${dotColor}"></div>
          <div class="cal-date">${escapeHtml(e.date)}</div>
          <div class="cal-event"><strong>${escapeHtml(e.event)}</strong></div>
          <div class="cal-impact">影响: ${escapeHtml(e.impact_asset||'')}</div>
          <div class="cal-direction">预期: ${escapeHtml(e.expected_direction||'')}</div>
        </div>`;
      }).join('');
      calendarHtml = `<div class="calendar-section"><h3>📅 周期日历（未来3个月）</h3><div class="calendar-timeline">${calNodes}</div></div>`;
    }

    return `
    <section class="v3-section" id="section-asset-allocation">
      <h2 class="section-title"> 资产配置建议</h2>
      <div class="asset-grid">${cards}</div>
      ${scenarioHtml}
      ${calendarHtml}
    </section>`;
  }

  /** 改进点#6/#7: 周期金字塔 + SVG同心圆 */
  function renderPortfolioGuide(guide) {
    if (!guide) return '';
    const pyramid = guide.pyramid;
    let pyramidHtml = '';
    if (pyramid?.layers) {
      const layers = pyramid.layers;
      // Text pyramid: widest at bottom (layer 6), narrowest at top (layer 0)
      const reversed = [...layers].reverse();
      const pyramidRows = reversed.map((l, i) => {
        const widthPct = 40 + (i / (reversed.length-1)) * 55;
        return `<div class="pyramid-row" style="width:${widthPct}%;margin:0 auto">
          <div class="pyramid-cell" style="border-left:3px solid ${l.color||'#6b7280'}">
            <span class="pyramid-layer">L${l.layer}</span>
            <span class="pyramid-name">${escapeHtml(l.name)}</span>
            <span class="pyramid-position">${escapeHtml(l.position||'')}</span>
            <span class="pyramid-signal" style="color:${l.color||'#6b7280'}">${escapeHtml(l.signal||'')}</span>
          </div>
        </div>`;
      }).join('');
      pyramidHtml = `<div class="pyramid-container"><h3>📐 周期嵌套金字塔（文字版）</h3>${pyramidRows}</div>`;
    }
    return `
    <section class="v3-section" id="section-portfolio-guide">
      <h2 class="section-title">️ 周期嵌套结构</h2>
      ${pyramidHtml}
      <div class="svg-note">📌 SVG同心圆版本将在文字版验证后升级</div>
    </section>`;
  }

  /** 信贷脉冲 + 二阶导数 (#9) */
  function renderCreditImpulse(ci) {
    if (!ci) return '';
    const renderRegion = (r, key) => {
      if (!r) return '';
      const sd = r.second_derivative;
      let sdHtml = '';
      if (sd) {
        const sdColor = sd.signal==='positive_acceleration'?'#10b981':'#ef4444';
        sdHtml = `<div class="second-derivative" style="color:${sdColor}">
          二阶导数: <strong>${fmtNum(sd.value,2)}</strong> - ${escapeHtml(sd.description||'')}
        </div>`;
      }
      return `
      <div class="region-card credit-region">
        <div class="region-header">
          <span class="region-flag">${key==='global'?'🌐':key==='us'?'🇺🇸':'🇨'}</span>
          <span class="region-name">${key==='global'?'全球':key==='us'?'美国':'中国'}</span>
          <span class="region-phase">${escapeHtml(r.phase||'')}</span>
        </div>
        <div class="ind-value">${fmtNum(r.current_value,1)} <span class="ind-unit">${escapeHtml(r.unit||'')}</span></div>
        ${sdHtml}
        <div id="chart-credit-${key}" class="chart-container" style="width:100%;height:160px;margin-top:8px;"></div>
      </div>`;
    };
    return `
    <section class="v3-section" id="section-credit-impulse">
      <h2 class="section-title">💉 信贷脉冲</h2>
      <p class="section-desc">${escapeHtml(ci.importance_note||'')}</p>
      <div class="regions-row">
        ${renderRegion(ci.global,'global')}
        ${renderRegion(ci.cn,'cn')}
        ${renderRegion(ci.us,'us')}
      </div>
    </section>`;
  }

  /** 综合结论 synthesis */
  function renderSynthesis(synthesis) {
    if (!synthesis) return '';
    const positions = (synthesis.cycle_positions_summary||[]).map(p =>
      `<div class="position-item" style="border-left:3px solid ${p.color||'#6b7280'}">
        <span class="pos-layer">L${p.layer}</span>
        <span class="pos-cycle">${escapeHtml(p.cycle)}</span>
        <span class="pos-position">${escapeHtml(p.position||'')}</span>
        <span class="pos-signal" style="color:${p.color}">${escapeHtml(p.signal||'')}</span>
        <span class="pos-weight">${p.signal_weight>0?'+':''}${p.signal_weight}</span>
      </div>`
    ).join('');
    const risks = (synthesis.key_risks||[]).map(r=>`<li style="color:#ef4444">⚠️ ${escapeHtml(r)}</li>`).join('');
    const watches = (synthesis.key_watchpoints||[]).map(w=>`<li style="color:#f59e0b">👁️ ${escapeHtml(w)}</li>`).join('');
    return `
    <section class="v3-section" id="section-synthesis">
      <h2 class="section-title"> ${escapeHtml(synthesis.title||'周期轮动综合研判')}</h2>
      <div class="synthesis-assessment">${escapeHtml(synthesis.overall_assessment||'')}</div>
      <div class="positions-grid">${positions}</div>
      <div class="risk-watch-grid">
        <div class="risk-box"><h4>关键风险</h4><ul>${risks}</ul></div>
        <div class="watch-box"><h4>关注要点</h4><ul>${watches}</ul></div>
      </div>
    </section>`;
  }

  // ========== Chart Rendering ==========
  function renderCharts(data) {
    // TFP chart (Kondratieff)
    const cnTfp = data.cycle_layers?.layer_1_kondratieff?.indicators?.tfp_growth?.cn;
    const usTfp = data.cycle_layers?.layer_1_kondratieff?.indicators?.tfp_growth?.us;
    if (cnTfp?.history?.length) {
      const series = [{ name:'中国TFP', data:cnTfp.history.map(h=>[h.date, h.value]), type:'line', smooth:true,
        lineStyle:{width:2}, itemStyle:{color:'#3b82f6'},
        areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(59,130,246,0.3)'},{offset:1,color:'rgba(59,130,246,0.02)'}]}}
      }];
      if (usTfp?.history?.length) {
        series.push({ name:'美国TFP', data:usTfp.history.map(h=>[h.date, h.value]), type:'line', smooth:true,
          lineStyle:{width:2}, itemStyle:{color:'#8b5cf6'} });
      }
      // Percentile bands
      let markLines = [];
      if (cnTfp.percentile) {
        const p = cnTfp.percentile;
        if (p.p25!=null) markLines.push({lineStyle:{type:'dashed',color:'#d1d5db'},label:{formatter:'p25'},data:[{yAxis:p.p25}]});
        if (p.p50!=null) markLines.push({lineStyle:{type:'dashed',color:'#9ca3af'},label:{formatter:'p50'},data:[{yAxis:p.p50}]});
        if (p.p75!=null) markLines.push({lineStyle:{type:'dashed',color:'#6b7280'},label:{formatter:'p75'},data:[{yAxis:p.p75}]});
      }
      createChart('chart-kondratieff-tfp', {
        tooltip: {...tooltipConfig(), trigger:'axis'},
        legend:{textStyle:{color:COLORS.textSecondary}, top:0},
        grid: gridConfig({top:30}),
        xAxis:{type:'category', data:cnTfp.history.map(h=>h.date), axisLine:{lineStyle:{color:COLORS.borderSubtle}},
          axisLabel:{color:COLORS.textMuted, fontSize:10, rotate:cnTfp.history.length>8?30:0}},
        yAxis:{type:'value', name:'%', nameTextStyle:{color:COLORS.textMuted},
          axisLine:{lineStyle:{color:COLORS.borderSubtle}}, axisLabel:{color:COLORS.textMuted},
          splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
        series
      });
    }

    // Perez key_ratio chart
    const kr = data.cycle_layers?.layer_2_perez?.key_ratio;
    if (kr?.history?.length) {
      const tp = kr.threshold_params || {};
      const frenzyLine = tp.frenzy_threshold ? [{lineStyle:{type:'solid',color:'#ef4444'},label:{formatter:'Frenzy阈值'},data:[{yAxis:tp.frenzy_threshold}]}] : [];
      const meanLine = tp.mean!=null ? [{lineStyle:{type:'dashed',color:'#6b7280'},label:{formatter:'均值'},data:[{yAxis:tp.mean}]}] : [];
      createChart('chart-perez-ratio', {
        tooltip: {...tooltipConfig(), trigger:'axis'},
        grid: gridConfig({top:20}),
        xAxis:{type:'category', data:kr.history.map(h=>h.date), axisLine:{lineStyle:{color:COLORS.borderSubtle}},
          axisLabel:{color:COLORS.textMuted, fontSize:10}},
        yAxis:{type:'value', axisLine:{lineStyle:{color:COLORS.borderSubtle}}, axisLabel:{color:COLORS.textMuted},
          splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
        series:[{type:'line',data:kr.history.map(h=>[h.date,h.value]),smooth:true,
          lineStyle:{width:2,color:'#8b5cf6'},itemStyle:{color:'#8b5cf6'},
          markLine:{data:[...frenzyLine,...meanLine],symbol:'none'}}]
      });
    }

    // Credit impulse charts
    ['global','cn','us'].forEach(key => {
      const r = data.credit_impulse?.[key];
      const chartId = `chart-credit-${key}`;
      if (r?.history?.length) {
        const zeroLine = [{lineStyle:{type:'solid',color:'#6b7280'},label:{formatter:'零轴'},data:[{yAxis:0}]}];
        createChart(chartId, {
          tooltip:{...tooltipConfig(),trigger:'axis'},
          grid:gridConfig({top:10,bottom:20}),
          xAxis:{type:'category',data:r.history.map(h=>h.date),axisLine:{lineStyle:{color:COLORS.borderSubtle}},
            axisLabel:{color:COLORS.textMuted,fontSize:9,rotate:30}},
          yAxis:{type:'value',axisLine:{lineStyle:{color:COLORS.borderSubtle}},axisLabel:{color:COLORS.textMuted},
            splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
          series:[{type:'bar',data:r.history.map(h=>({value:h.value,itemStyle:{color:h.value>=0?'#10b981':'#ef4444'}})),
            markLine:{data:zeroLine,symbol:'none'}}]
        });
      }
    });

    // Juglar/Kitchin/Merrill indicator charts
    ['layer_4_juglar','layer_5_kitchner','layer_6_merrill'].forEach(layerKey => {
      const layer = data.cycle_layers?.[layerKey];
      if (!layer) return;
      ['us','cn'].forEach(regionKey => {
        const r = layer[regionKey];
        if (!r?.indicators) return;
        Object.entries(r.indicators).forEach(([indKey, ind]) => {
          const chartId = `chart-juglar-${regionKey}-${indKey}`;
          const el = document.getElementById(chartId);
          if (!el || !ind.history?.length) return;
          const pct = ind.percentile;
          let markLines = [];
          if (pct) {
            if (pct.p25!=null) markLines.push({lineStyle:{type:'dashed',color:'#d1d5db'},label:{formatter:'p25'},data:[{yAxis:pct.p25}]});
            if (pct.p50!=null) markLines.push({lineStyle:{type:'dashed',color:'#9ca3af'},label:{formatter:'p50'},data:[{yAxis:pct.p50}]});
            if (pct.p75!=null) markLines.push({lineStyle:{type:'dashed',color:'#6b7280'},label:{formatter:'p75'},data:[{yAxis:pct.p75}]});
          }
          createChart(chartId, {
            tooltip:{...tooltipConfig(),trigger:'axis'},
            grid:gridConfig({top:10,bottom:15,left:40}),
            xAxis:{type:'category',data:ind.history.map(h=>h.date),axisLine:{lineStyle:{color:COLORS.borderSubtle}},
              axisLabel:{color:COLORS.textMuted,fontSize:9,rotate:ind.history.length>6?30:0}},
            yAxis:{type:'value',axisLine:{lineStyle:{color:COLORS.borderSubtle}},axisLabel:{color:COLORS.textMuted},
              splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
            series:[{type:'line',data:ind.history.map(h=>[h.date,h.value]),smooth:true,
              lineStyle:{width:2,color:'#06b6d4'},itemStyle:{color:'#06b6d4'},
              markLine: markLines.length ? {data:markLines,symbol:'none'} : undefined}]
          });
        });
      });
    });
  }

  // ========== Main Render Entry ==========
  function render(data) {
    if (!data) return;
    const container = document.getElementById('cycle-content');
    if (!container) return;

    const layers = data.cycle_layers || {};
    const cross = data.cross_analysis || {};
    const allocation = data.asset_allocation || null;
    const synthesis = data.synthesis || null;
    const creditImpulse = data.credit_impulse || null;
    const consensus = data.cycle_consensus || null;

    let html = '';
    // Meta header
    html += renderMeta(data._meta);
    // ★ 评分卡+配置建议置顶（结论先行）
    if (consensus) {
      html += renderConsensusSummary(consensus);
    }
    // Layer 0: Debt Cycle
    html += renderDebtCycle(layers.layer_0_debt_cycle);
    // Layer 1: Kondratieff
    html += renderKondratieff(layers.layer_1_kondratieff);
    // Layer 2: Perez
    html += renderPerez(layers.layer_2_perez);
    // ★ 中美周期错位表（移到评分卡下方）
    html += renderUsChinaMatrix(cross.us_china_matrix);
    // Layer 3: Rate Regime + High Rate Tracker
    html += renderRateRegime(layers.layer_3_rate_regime, cross.high_rate_tracker);
    // Layer 4: Juglar
    html += `
    <section class="v3-section" id="section-juglar">
      <h2 class="section-title">⚙️ 朱格拉周期<span class="section-subtitle">第4层</span></h2>
      <div class="regions-row">${renderJuglar(layers.layer_4_juglar)}</div>
    </section>`;
    // Layer 5: Kitchner
    html += `
    <section class="v3-section" id="section-kitchner">
      <h2 class="section-title">📦 基钦周期<span class="section-subtitle">第5层</span></h2>
      <div class="regions-row">${renderJuglar(layers.layer_5_kitchner)}</div>
    </section>`;
    // Layer 6: Merrill
    html += `
    <section class="v3-section" id="section-merrill">
      <h2 class="section-title">🕐 美林时钟<span class="section-subtitle">第6层</span></h2>
      <div class="regions-row">${renderMerrill(layers.layer_6_merrill)}</div>
    </section>`;
    // Credit Impulse (#9)
    html += renderCreditImpulse(creditImpulse);
    // Portfolio Guide (Pyramid #6/#7)
    html += renderPortfolioGuide(cross.portfolio_guide);
    // Asset Allocation (#5/#11/#14)
    html += renderAssetAllocation(allocation);
    // Synthesis
    html += renderSynthesis(synthesis);

    container.innerHTML = html;

    // Render ECharts after DOM update
    requestAnimationFrame(() => { renderCharts(data); });
  }

  function dispose() {
    chartInstances.forEach(c => { try { c.dispose(); } catch(e){} });
    chartInstances = [];
  }

  return { render, dispose };
})();
