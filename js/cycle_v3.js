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
  let resizeHandler = null;
  let resizeTimer = null;
  const LAYER_MAP = {
    0: 'constraint_debt_cycle', 1: 'narrative_kondratieff', 2: 'narrative_perez',
    3: 'constraint_rate_regime', 4: 'cycle_juglar', 5: 'cycle_kitchin', 6: 'cycle_merrill_3d'
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
  // Phase 2B: Merrill 3D 8-state definitions
  const MERRILL_3D_STATES = [
    {growth:'up', inflation:'down', credit:'up', name:'金发女孩复苏', best:'股票', score:2},
    {growth:'up', inflation:'down', credit:'down', name:'无信贷支撑复苏', best:'股票(选择性)', score:1},
    {growth:'up', inflation:'up', credit:'up', name:'过热', best:'商品', score:1},
    {growth:'up', inflation:'up', credit:'down', name:'滞胀前兆', best:'现金/短债', score:-1},
    {growth:'down', inflation:'up', credit:'up', name:'信贷驱动通胀', best:'商品/黄金', score:-1},
    {growth:'down', inflation:'up', credit:'down', name:'滞胀', best:'黄金/现金', score:-2},
    {growth:'down', inflation:'down', credit:'up', name:'信贷宽松实体弱', best:'债券(信用债)', score:0},
    {growth:'down', inflation:'down', credit:'down', name:'衰退', best:'利率债/现金', score:-1},
  ];
  const MERRILL_MATRIX_ROWS = [
    {growth:'up', inflation:'down', label:'增长↑ 通胀↓'},
    {growth:'up', inflation:'up', label:'增长↑ 通胀↑'},
    {growth:'down', inflation:'up', label:'增长↓ 通胀↑'},
    {growth:'down', inflation:'down', label:'增长↓ 通胀↓'},
  ];

  // Phase 3: Asset ranking per Merrill 3D state (based on Merrill Lynch Investment Clock paper)
  const STATE_ASSET_RANKING = {
    '金发女孩复苏':   {stocks:'★★★', bonds:'★★', commodities:'★', gold:'★', cash:'☆', re:'★★', credit:'★★★', fx_domestic:'★★'},
    '无信贷支撑复苏': {stocks:'★★', bonds:'★', commodities:'★★', gold:'★', cash:'☆', re:'★★', credit:'★★', fx_domestic:'★'},
    '过热':           {stocks:'★★', bonds:'★', commodities:'★★★', gold:'★★', cash:'☆', re:'★', credit:'★', fx_domestic:'☆'},
    '滞胀前兆':       {stocks:'☆', bonds:'★', commodities:'★★', gold:'★★', cash:'★★★', re:'☆', credit:'☆', fx_domestic:'★★'},
    '信贷驱动通胀':   {stocks:'★', bonds:'☆', commodities:'★★★', gold:'★★★', cash:'☆', re:'★', credit:'☆', fx_domestic:'☆'},
    '滞胀':           {stocks:'☆', bonds:'★', commodities:'★★', gold:'★★★', cash:'★★', re:'☆', credit:'☆', fx_domestic:'☆'},
    '信贷宽松实体弱': {stocks:'★★', bonds:'★★★', commodities:'☆', gold:'★', cash:'★', re:'☆', credit:'★★', fx_domestic:'★'},
    '衰退':           {stocks:'☆', bonds:'★★★', commodities:'☆', gold:'★★', cash:'★★★', re:'☆', credit:'★', fx_domestic:'★★'}
  };
  const ASSET_LABELS = {stocks:'股票', bonds:'债券', commodities:'商品', gold:'黄金', cash:'现金', re:'房地产', credit:'信用债', fx_domestic:'本币'};

  // Set during render() — used by getFreshness/freshnessBadge as reference date
  let _assessmentDate = null;


  // ========== Utilities ==========
  function tooltipConfig() {
    return { backgroundColor:COLORS.tooltipBg, borderColor:COLORS.tooltipBorder, borderWidth:1,
      textStyle:{ color:COLORS.textPrimary, fontSize:12, fontFamily:'JetBrains Mono' }, padding:[8,12] };
  }
  function gridConfig(extra={}) {
    return Object.assign({top:36,right:16,bottom:28,left:16,containLabel:true}, extra);
  }
  function createChart(id, option, skipSizeCheck) {
    const el = document.getElementById(id);
    if (!el) { console.warn('[Chart] Container not found:', id); return null; }
    // Dispose existing instance if re-rendering
    try { var _ei = echarts.getInstanceByDom(el); if (_ei) _ei.dispose(); } catch(e){}
    try {
      if (!el.offsetHeight) { el.style.height = el.style.height || '200px'; }
      void el.offsetHeight;
      if (!skipSizeCheck && (!el.offsetWidth || !el.offsetHeight)) {
        console.warn('[Chart] Container zero-size:', id, el.offsetWidth+'x'+el.offsetHeight);
        return null;
      }
      const chart = echarts.init(el, null, {renderer:'svg'});
      chart.setOption(option, true);
      chartInstances.push(chart);
      console.log('[Chart] ✓ Rendered', id, el.offsetWidth+'x'+el.offsetHeight, skipSizeCheck?'(forced)':'');
      return chart;
    } catch(e) {
      console.error('[Chart] Error rendering', id, ':', e.message, e.stack);
      return null;
    }
  }
  function fmtNum(v, decimals=1) { return v == null ? '--' : Number(v).toFixed(decimals); }
  function fmtPct(v, decimals=1) { return v == null ? '--' : Number(v).toFixed(decimals) + '%'; }
  function escapeHtml(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // Frequency-aware thresholds: cycleDays = normal publication lag + period length
  // e.g. monthly: data for month M published ~15-30 days after M ends, so cycleDays=60 is safe
  const FREQ_THRESHOLDS = {
    daily:    { cycleDays: 3, maxAge: 30, label: '日频' },
    weekly:   { cycleDays: 14, maxAge: 45, label: '周频' },
    monthly:  { cycleDays: 60, maxAge: 120, label: '月频' },
    quarterly:{ cycleDays: 150, maxAge: 300, label: '季频' },
    annual:   { cycleDays: 450, maxAge: 730, label: '年频' },
    event_driven: { cycleDays: 365, maxAge: 730, label: '事件驱动' }
  };

  function getFreshness(lastUpdated, frequency, assessmentDate) {
    // Use _assessmentDate (set during render) or explicit param, fallback to Date.now()
    const refDate = (assessmentDate || _assessmentDate) ? new Date(assessmentDate || _assessmentDate) : new Date();
    const thresh = FREQ_THRESHOLDS[frequency] || FREQ_THRESHOLDS.monthly;
    const freqLabel = thresh.label;
    // If no last_updated but we have a current value, data is available
    if (!lastUpdated) {
      return { cls:'no-date', label:`${freqLabel}`, days:0, freqLabel, date:'' };
    }
    const days = Math.floor((refDate - new Date(lastUpdated)) / 86400000);
    // Within normal publication cycle → this is the latest period
    if (days <= thresh.cycleDays) return { cls:'fresh', label:`${lastUpdated} | ${freqLabel} | ✅最新`, days, freqLabel, date:lastUpdated };
    // Beyond cycle but within maxAge → likely a gap
    if (days <= thresh.maxAge) return { cls:'stale', label:`${lastUpdated} | ${freqLabel} | ⏳等待更新`, days, freqLabel, date:lastUpdated };
    // Way beyond → something is wrong
    return { cls:'expired', label:`${lastUpdated} | ${freqLabel} | ⚠️数据断更`, days, freqLabel, date:lastUpdated };
  }

  function freshnessBadge(lastUpdated, frequency, assessmentDate) {
    const f = getFreshness(lastUpdated, frequency, assessmentDate);
    if (f.cls === 'no-date') {
      // No last_updated: show frequency tag only, no scary warning
      return `<span class="freshness-badge fresh" title="${f.freqLabel}"><span style="font-size:9px;opacity:0.7">${f.freqLabel}</span></span>`;
    }
    return `<span class="freshness-badge ${f.cls}" title="${f.label}">${f.label}</span>`;
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
  function renderConstraintDebt(layer) {
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
          ${ind.source_url ? `<a href="${escapeHtml(ind.source_url)}" target="_blank" class="source-link">📎 数据来源</a>` : ''}
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
      ${layer.constraint_triggered ? '<div class="constraint-warning" style="background:rgba(239,68,68,0.15);border:1px solid #ef4444;border-radius:8px;padding:10px 14px;margin-bottom:12px;color:#fca5a5;font-size:13px;">⚠️ 债务约束生效：所有量化层bullish信号已降级为cautious_bullish，共识度上限60分</div>' : ''}
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
        return `<div class="reform-card">
          <div class="reform-name">${escapeHtml(p.name)} ${f}</div>
          <div class="reform-value">${p.current}${escapeHtml(p.unit||'')}</div>
          <div class="reform-trend">${escapeHtml(p.trend||'')}</div>
          <div class="reform-desc">${escapeHtml(p.description||'')}</div>
        </div>`;
      } else {
        return `<div class="reform-card reform-qualitative">
          <div class="reform-name">${escapeHtml(p.name)}</div>
          <div class="reform-assessment">${escapeHtml(p.assessment||'')}</div>
          <div class="reform-desc">${escapeHtml(p.note||'')}</div>
        </div>`;
      }
    }).join('');
    
    return `
    <div class="reform-section">
      <h3>🏗️ 结构性改革进度（方案D：代理指标+定性标注）</h3>
      <p class="reform-note">${escapeHtml(sr.qualitative_assessment||'')}</p>
      <div class="reform-grid">${proxies}</div>
    </div>`;
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
  function renderNarrativeKondratieff(layer) {
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
      <h2 class="section-title">🌊 康波（康德拉季耶夫长波）<span class="section-subtitle">第1层</span></h2>
      <div class="phase-banner" style="border-left:4px solid ${COLORS.bullish}">
        <span class="phase-text">${escapeHtml(layer.current_phase||'')}</span>
        ${layer.signal_weight != null ? `<span class="signal-weight-badge" style="color:${COLORS.bullish}">${layer.signal_weight>0?'+':''}${layer.signal_weight}</span>` : ''}
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
      ${renderCommodityForecast(layer.commodity_forecast)}
    </section>`;
  }

  /** 改进点#8 + #19 + #22: 佩雷斯 with Turning Point + threshold_params */
  function renderNarrativePerez(layer) {
    if (!layer) return '';
    const tpSignals = (layer.turning_point_signals||[]).map(s => {
      const color = s.status==='green'?'#10b981':s.status==='yellow'?'#f59e0b':'#ef4444';
      return `
      <div class="tp-signal-card" style="border-left:3px solid ${color}">
        <div class="tp-name">${escapeHtml(s.indicator)}</div>
        <div class="tp-value">当前: <strong>${s.current_value ?? s.current}</strong>${s.metric?' ('+escapeHtml(s.metric)+')':''}</div>
        <div class="tp-threshold">阈值: ${s.threshold||s.threshold_trigger||'--'}</div>
        <div class="tp-desc">${escapeHtml(s.description||'')}</div>
        <div class="tp-status" style="color:${color}">${s.status==='green'?'✅ 安全':s.status==='yellow'?'⚠️ 观察':' 预警'}</div>
      </div>`;
    }).join('');

    const kr = layer.key_ratio;
    let krHtml = '';
    if (kr) {
      const tp = kr.threshold_params || {};
      const isFrenzy = (kr.current_value ?? kr.current) >= (tp.frenzy_threshold || 2.7);
      krHtml = `
      <div class="key-ratio-card">
        <h4>📊 ${escapeHtml(kr.name)}</h4>
        <div class="kr-value">当前: <strong>${fmtNum(kr.current_value ?? kr.current, 2)}</strong></div>
        <div class="kr-threshold">阈值: ${escapeHtml(kr.threshold||'')}</div>
        ${tp.mean!=null?`<div class="kr-params">均值=${tp.mean} | σ=${tp.std_dev} | Frenzy阈值=${tp.frenzy_threshold} <span class="kr-note">${escapeHtml(tp.note||'')}</span></div>`:''}
        ${isFrenzy?'<div class="kr-warning" style="color:#ef4444">️ 当前值已超过Frenzy阈值!</div>':''}
        <div id="chart-perez-ratio" class="chart-container" style="width:100%;height:200px;margin-top:8px;"></div>
      </div>`;
    }
    return `
    <section class="v3-section" id="section-perez">
      <h2 class="section-title">🔬 佩雷斯（技术革命周期）<span class="section-subtitle">第2层</span></h2>
      <div class="phase-banner" style="border-left:4px solid ${COLORS.cautious}">
        <span class="phase-text">${escapeHtml(layer.current_phase||'')}</span>
        ${layer.signal_weight != null ? `<span class="signal-weight-badge" style="color:${COLORS.cautious}">${layer.signal_weight}</span>` : ''}
      </div>
      <div class="tp-section">
        <h3 class="tp-title">🚨 Turning Point 预警子模块</h3>
        <div class="tp-grid">${tpSignals}</div>
      </div>
      ${krHtml}
    </section>`;
  }

  /** 改进点#2: 高利率时代跟踪 */
  function renderConstraintRate(layer, highRateTracker) {
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
                ...rData, name: ind.name + (rk==='us' ? ' 🇺🇸' : ' 🇨🇳'),
                source: ind.source, layer_group: group, ind_key: k+'_'+rk,
                last_updated: rData.last_updated || ind.last_updated,
                frequency: rData.frequency || ind.frequency,
                description: ind.description,
                history: rData.history
              });
            }
          });
        } else {
          allIndicators.push(item);
        }
      });
    });
    // Also add from high_rate_tracker if present (inherit parent last_updated as fallback)
    if (highRateTracker?.indicators) {
      const hrtDate = highRateTracker.last_updated || null;
      highRateTracker.indicators.forEach(ind => {
        allIndicators.push({...ind, layer_group: ind.layer, last_updated: ind.last_updated || hrtDate});
      });
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
        const chartId = 'chart-rate-'+g+'-'+(ind.ind_key||'');
        const sourceUrlHtml = ind.source_url ? `<a href="${escapeHtml(ind.source_url)}" target="_blank" class="source-link">📎 数据来源</a>` : '';
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
          ${sourceUrlHtml}
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
      ${layer?.regime_state ? renderRegimeStateBadge(layer.regime_state) : ''}
      ${assessment}
      ${gridHtml}
      ${scenarioHtml}
    </section>`;
  }

  /** 朱格拉 / 基钦 / 美林 通用渲染 */
  function renderJuglar(layer, prefix) {
    prefix = prefix || 'juglar';
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
        const juglarSourceUrl = ind.source_url ? `<a href="${escapeHtml(ind.source_url)}" target="_blank" class="source-link">📎 数据来源</a>` : '';
        return `
        <div class="indicator-card ${getFreshness(ind.last_updated, ind.frequency).cls}">
          <div class="ind-header"><span class="ind-name">${escapeHtml(ind.name)}</span>${f} ${pctBadge}</div>
          <div class="ind-value">${fmtNum(ind.current)} <span class="ind-unit">${escapeHtml(ind.unit||'')}</span></div>
          <div id="chart-${prefix}-${key}-${k}" class="chart-container" style="width:100%;height:120px;margin-top:8px;"></div>
          ${juglarSourceUrl}
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


  /** 改进点#3: 周期共识度评分卡 - 置顶版本（结论先行）
   *  兼容v4结构：cycle_consensus.united_states / .china 各含 p1/p2/p3 score+label */
  function renderConsensusSummary(consensus) {
    if (!consensus) return '';

    // v4 structure: has united_states / china sub-objects
    const us = consensus.united_states || {};
    const cn = consensus.china || {};
    const isV4 = us.consensus_score !== undefined;

    if (isV4) {
      // === v4 双区域共识卡 ===
      const scoreColor = (s) => s >= 80 ? '#10b981' : s >= 60 ? '#84cc16' : s >= 40 ? '#f59e0b' : s >= 20 ? '#f97316' : '#ef4444';

      function renderRegionCard(flag, label, d) {
        const sc = scoreColor(d.consensus_score);
        const pItems = [
          { name: 'P1 朱格拉', score: d.p1_score, label: d.p1_label },
          { name: 'P2 基钦', score: d.p2_score, label: d.p2_label },
          { name: 'P3 美林', score: d.p3_score, label: d.p3_label }
        ];
        const pHtml = pItems.map(p => {
          const pColor = p.score > 0 ? '#10b981' : p.score < 0 ? '#ef4444' : '#f59e0b';
          return `<div class="consensus-p-item">
            <span class="consensus-p-name">${p.name}</span>
            <span class="consensus-p-score" style="color:${pColor};font-weight:700">${p.score}</span>
            <span class="consensus-p-label">${escapeHtml(p.label || '')}</span>
          </div>`;
        }).join('');

        return `<div class="consensus-region-card">
          <div class="crc-header">
            <span class="crc-flag">${flag}</span>
            <span class="crc-name">${label}</span>
          </div>
          <div class="crc-score" style="color:${sc}">${d.consensus_score.toFixed(1)}</div>
          <div class="crc-signal" style="color:${sc}">${escapeHtml(d.signal || '')}</div>
          <div class="crc-formula">raw=${d.raw_score.toFixed(1)}</div>
          <div class="crc-p-grid">${pHtml}</div>
        </div>`;
      }

      return `
      <section class="v3-section consensus-summary-section" id="section-consensus-summary">
        <div class="consensus-v4-header">
          <h2 class="section-title">📊 周期共识评分</h2>
          <span class="consensus-date">📅 ${escapeHtml(consensus.last_updated || '--')}</span>
        </div>
        <div class="consensus-formula-bar">${escapeHtml(consensus.formula || '')}</div>
        <div class="consensus-v4-grid">
          ${renderRegionCard('🇺🇸', '美国', us)}
          ${renderRegionCard('🇨🇳', '中国', cn)}
        </div>
        <button class="expand-detail-btn" onclick="var el=document.getElementById('section-kondratieff');if(el)el.scrollIntoView({behavior:'smooth'})">
          📊 查看各周期详细分析 ▾
        </button>
      </section>`;
    }

    // === 旧版 fallback (cross_analysis.consensus) ===
    const score = consensus.overall_score || consensus.score || 0;
    const scoreColor = score >= 70 ? '#10b981' : score >= 50 ? '#f59e0b' : score >= 30 ? '#f97316' : '#ef4444';
    const signal = consensus.overall_signal || 'neutral';
    const signalColor = signal === 'bullish' ? '#10b981' : signal === 'bearish' ? '#ef4444' : '#f59e0b';
    const assessment = consensus.overall_assessment || consensus.scenario_description || '';

    return `
    <section class="v3-section consensus-summary-section" id="section-consensus-summary">
      <div class="consensus-summary-header">
        <div class="consensus-score-block">
          <div class="consensus-score" style="color:${scoreColor}">${typeof score === 'number' ? score.toFixed(1) : score}</div>
          <div class="consensus-label">综合周期评分</div>
        </div>
        <div class="consensus-assessment">
          <div class="assessment-text">${escapeHtml(assessment)}</div>
          <div class="assessment-signal" style="color:${signalColor}">信号: ${signal === 'bullish' ? '偏多' : signal === 'bearish' ? '偏空' : '中性'}</div>
        </div>
        <div class="consensus-date">📅 ${escapeHtml(consensus.last_updated || '--')}</div>
      </div>
      <button class="expand-detail-btn" onclick="var el=document.getElementById('section-kondratieff');if(el)el.scrollIntoView({behavior:'smooth'})">
        📊 查看各周期详细分析 ▾
      </button>
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
      // Phase 2B: Handle empty placeholder objects (e.g. 中国房地产)
      if (!a || (!a.asset && !a.signal && !a.view)) {
        return '<div class="asset-card asset-placeholder" style="border-left:3px solid #374151;opacity:0.6;">' +
          '<div class="asset-header"><span class="asset-name" style="color:#6b7280;">' + escapeHtml(a?.asset || '待获取') + '</span></div>' +
          '<div style="color:#6b7280;font-size:13px;padding:12px 0;">📊 数据待获取</div>' +
          '</div>';
      }
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

  /** 改进点#6/#7: 周期金字塔 + SVG同心圆 + SVG金字塔 */
  function renderPortfolioGuide(guide) {
    if (!guide) return '';
    const pyramid = guide.pyramid;
    let pyramidHtml = '';
    let svgCircleHtml = '';
    let svgPyramidHtml = '';
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

      // G-06: SVG Concentric Circles
      const cx = 150, cy = 150;
      const maxR = 140, minR = 20;
      const step = (maxR - minR) / (layers.length - 1);
      let circles = '';
      layers.forEach((l, i) => {
        const r = maxR - i * step;
        const isActive = l.signal && l.signal !== '约束' && l.signal !== '偏空' && l.signal !== '谨慎';
        const strokeW = isActive ? 3 : 1.5;
        const opacity = isActive ? 1 : 0.5;
        circles += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${l.color||'#6b7280'}" stroke-width="${strokeW}" opacity="${opacity}"/>`;
        // Labels on the right side of each ring
        const labelY = cy - r + 4;
        const labelX = cx + r + 4;
        if (labelX < 295) {
          circles += `<text x="${Math.min(labelX, 290)}" y="${Math.max(labelY, 12)}" fill="${l.color||'#94a3b8'}" font-size="8" font-weight="${isActive?'bold':'normal'}">L${l.layer} ${escapeHtml(l.name)}</text>`;
        }
      });
      // Center dot
      circles += `<circle cx="${cx}" cy="${cy}" r="4" fill="#10b981" opacity="0.8"/>`;
      circles += `<text x="${cx}" y="${cy + 16}" text-anchor="middle" fill="#94a3b8" font-size="7">当前</text>`;
      svgCircleHtml = `<div class="svg-viz-container"><h3>🎯 周期同心圆（SVG）</h3>
        <svg viewBox="0 0 300 300" width="300" height="300" class="svg-concentric">${circles}</svg>
        <div class="svg-legend">${layers.map(l => `<span class="legend-item" style="color:${l.color||'#94a3b8'}">● L${l.layer} ${escapeHtml(l.name)}</span>`).join('')}</div>
      </div>`;

      // G-15: SVG Pyramid
      const triW = 280, triH = 260;
      const triCx = triW / 2;
      const layerCount = layers.length;
      const layerH = triH / layerCount;
      let triParts = '';
      // Build triangle layers from top (L0) to bottom (L{max})
      layers.forEach((l, i) => {
        const topY = i * layerH;
        const botY = (i + 1) * layerH;
        // Triangle width at each Y level
        const topW = (i / layerCount) * (triW * 0.85);
        const botW = ((i + 1) / layerCount) * (triW * 0.85);
        const x1 = triCx - topW / 2, x2 = triCx + topW / 2;
        const x3 = triCx + botW / 2, x4 = triCx - botW / 2;
        const isActive = l.signal && l.signal !== '约束' && l.signal !== '偏空' && l.signal !== '谨慎';
        const fillOpacity = isActive ? 0.35 : 0.15;
        const strokeColor = l.color || '#6b7280';
        triParts += `<polygon points="${x1},${topY} ${x2},${topY} ${x3},${botY} ${x4},${botY}" fill="${strokeColor}" fill-opacity="${fillOpacity}" stroke="${strokeColor}" stroke-width="1.5"/>`;
        // Label
        const midY = (topY + botY) / 2 + 4;
        triParts += `<text x="${triCx}" y="${midY}" text-anchor="middle" fill="${strokeColor}" font-size="9" font-weight="${isActive?'bold':'normal'}">L${l.layer} ${escapeHtml(l.name)}</text>`;
        if (l.position) {
          triParts += `<text x="${triCx}" y="${midY + 12}" text-anchor="middle" fill="#94a3b8" font-size="7">${escapeHtml(l.position)}</text>`;
        }
      });
      svgPyramidHtml = `<div class="svg-viz-container"><h3>📐 周期金字塔（SVG）</h3>
        <svg viewBox="0 0 ${triW} ${triH}" width="${triW}" height="${triH}" class="svg-pyramid">${triParts}</svg>
      </div>`;
    }
    return `
    <section class="v3-section" id="section-portfolio-guide">
      <h2 class="section-title">️ 周期嵌套结构</h2>
      ${pyramidHtml}
      <div class="svg-viz-row">
        ${svgCircleHtml}
        ${svgPyramidHtml}
      </div>
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
        <div class="ind-value">${fmtNum(r.current_value ?? r.current,1)} <span class="ind-unit">${escapeHtml(r.unit||'')}</span></div>
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


  // ========== Phase 2B: New Helper Functions ==========

  /** Render commodity_forecast card (v4 addition to Kondratieff) */
  function renderCommodityForecast(cf) {
    if (!cf) return '';
    return `
    <div class="cycle-card commodity-forecast" style="border:1px solid #d97706;border-radius:10px;background:linear-gradient(135deg,rgba(217,119,6,0.08),rgba(245,158,11,0.04));padding:16px;margin-top:16px;">
      <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <h4 style="margin:0;color:#f59e0b;font-size:15px;">🛢️ 康波视角下的大宗商品</h4>
        <span class="disclaimer-tag" style="font-size:11px;color:#fbbf24;background:rgba(251,191,36,0.12);padding:2px 8px;border-radius:4px;border:1px solid rgba(251,191,36,0.3);">⚠️ 主观判断·仅供参考</span>
      </div>
      <div class="card-body">
        <p class="phase-label" style="font-size:14px;font-weight:600;color:#fbbf24;margin-bottom:8px;">当前阶段: ${escapeHtml(cf.current_phase||'')} → 商品${escapeHtml(cf.commodity_phase||'')}</p>
        <p style="color:#e2e8f0;margin-bottom:12px;">${escapeHtml(cf.outlook||'')}</p>
        <div class="forecast-section" style="margin-bottom:10px;">
          <h5 style="color:#d97706;font-size:13px;margin-bottom:4px;">中期展望（3-5年）</h5>
          <p style="color:#cbd5e1;font-size:13px;">${escapeHtml(cf.medium_term_3_5year||'')}</p>
        </div>
        <div class="forecast-section" style="margin-bottom:10px;">
          <h5 style="color:#d97706;font-size:13px;margin-bottom:4px;">交叉验证（vs P2基钦）</h5>
          <p style="color:#cbd5e1;font-size:13px;">${escapeHtml(cf.cross_validation||'')}</p>
        </div>
        <div class="forecast-section">
          <h5 style="color:#d97706;font-size:13px;margin-bottom:4px;">历史参考</h5>
          <p style="color:#cbd5e1;font-size:13px;">${escapeHtml(cf.historical_reference||'')}</p>
        </div>
      </div>
    </div>`;
  }

  /** Render regime_state badge for Rate Regime */
  function renderRegimeStateBadge(rs) {
    if (!rs) return '';
    const regime = rs.current_regime || 'normal';
    const regimeColors = { high_rate:'#ef4444', transition:'#f59e0b', normal:'#10b981' };
    const regimeLabels = { high_rate:'🔴 高利率', transition:'🟡 过渡期', normal:'🟢 正常' };
    const color = regimeColors[regime] || '#6b7280';
    const label = regimeLabels[regime] || regime;
    const constraintText = rs.constraint_active ? '约束生效中' : '无约束';
    const constraintColor = rs.constraint_active ? '#ef4444' : '#10b981';
    return `
    <div class="regime-badge ${regime}" style="display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:10px;background:rgba(${regime==='high_rate'?'239,68,68':regime==='transition'?'245,158,11':'16,185,129'},0.1);border:1px solid ${color};margin-bottom:12px;">
      <span class="regime-label" style="color:#94a3b8;font-size:12px;">利率环境</span>
      <span class="regime-value" style="color:${color};font-size:15px;font-weight:700;">${label}</span>
      <span class="constraint-indicator" style="margin-left:auto;color:${constraintColor};font-size:12px;font-weight:600;padding:3px 10px;border-radius:4px;background:rgba(${rs.constraint_active?'239,68,68,0.12':'16,185,129,0.12'});border:1px solid ${constraintColor}40;">${constraintText}</span>
    </div>`;
  }

  /** Phase 2B: Merrill 3D Matrix (4×2 grid) */
  function renderMerrill3D(layer) {
    if (!layer) return '';

    // Build 4×2 matrix HTML
    let matrixHtml = '';
    // Determine current positions
    const usG = layer.us?.growth_direction || layer.us?.growth;
    const usI = layer.us?.inflation_direction || layer.us?.inflation;
    const usC = layer.us?.credit_direction || layer.us?.credit;
    const cnG = layer.cn?.growth_direction || layer.cn?.growth;
    const cnI = layer.cn?.inflation_direction || layer.cn?.inflation;
    const cnC = layer.cn?.credit_direction || layer.cn?.credit;

    const matchState = (g, i, c) => {
      if (!g || !i || !c) return null;
      return MERRILL_3D_STATES.find(s =>
        s.growth === g && s.inflation === i && s.credit === c
      ) || null;
    };
    const usState = matchState(usG, usI, usC);
    const cnState = matchState(cnG, cnI, cnC);

    const scoreBg = (score) => {
      if (score >= 2) return 'rgba(16,185,129,0.25)';
      if (score >= 1) return 'rgba(16,185,129,0.12)';
      if (score === 0) return 'rgba(107,114,128,0.15)';
      if (score >= -1) return 'rgba(239,68,68,0.12)';
      return 'rgba(239,68,68,0.25)';
    };
    const scoreBorder = (score) => {
      if (score >= 1) return '#10b981';
      if (score === 0) return '#6b7280';
      return '#ef4444';
    };

    let rows = '';
    MERRILL_MATRIX_ROWS.forEach(row => {
      let cells = '';
      ['up','down'].forEach(credit => {
        const state = MERRILL_3D_STATES.find(s =>
          s.growth === row.growth && s.inflation === row.inflation && s.credit === credit
        );
        if (!state) { cells += '<div class="m3d-cell empty"></div>'; return; }
        const isUsCur = usState && state.name === usState.name;
        const isCnCur = cnState && state.name === cnState.name;
        const isCurrent = isUsCur || isCnCur;
        const bg = isCurrent ? 'rgba(6,182,212,0.2)' : scoreBg(state.score);
        const border = isCurrent ? '#06b6d4' : scoreBorder(state.score);
        const flag = isUsCur && isCnCur ? ' 🇺🇸🇨🇳' : isUsCur ? ' 🇺🇸' : isCnCur ? ' 🇨🇳' : '';
        const star = isCurrent ? ' ★' : '';
        // Phase 3: Tooltip + onclick + pulse
        const ranking = STATE_ASSET_RANKING[state.name] || {};
        const rankLines = Object.entries(ranking).map(function(e){ return (ASSET_LABELS[e[0]]||e[0]) + ':' + e[1]; }).join(' | ');
        const scoreMeaning = state.score > 0 ? '看多' : state.score < 0 ? '看空' : '中性';
        const tooltipText = escapeHtml(state.name) + '\n' +
          '增长:' + (state.growth==='up'?'↑':'↓') + ' 通胀:' + (state.inflation==='up'?'↑':'↓') + ' 信贷:' + (state.credit==='up'?'↑':'↓') + '\n' +
          'Score: ' + (state.score>0?'+':'') + state.score + ' (' + scoreMeaning + ')\n' +
          '最优: ' + escapeHtml(state.best) + '\n' +
          '资产排名: ' + escapeHtml(rankLines);
        const pulseClass = isCurrent ? ' m3d-pulse' : '';
        const currentGlow = isCurrent ? 'box-shadow:0 0 12px rgba(6,182,212,0.4);' : '';
        cells += '<div class="m3d-cell' + (isCurrent ? ' m3d-current' : '') + pulseClass + '"' +
          ' title="' + tooltipText + '"' +
          ' onclick="window._merrillDetail(\'' + escapeHtml(state.name).replace(/'/g, "\\'") + '\')"' +
          ' style="background:' + bg + ';border:2px solid ' + border + ';border-radius:8px;padding:10px;text-align:center;min-height:90px;display:flex;flex-direction:column;justify-content:center;cursor:pointer;transition:transform 0.2s;' + currentGlow + '"' +
          ' onmouseover="this.style.transform=\'scale(1.05)\'" onmouseout="this.style.transform=\'scale(1)\'">' +
          '<div class="m3d-name" style="font-size:12px;font-weight:700;color:#e2e8f0;margin-bottom:4px;">' + escapeHtml(state.name) + star + '</div>' +
          '<div class="m3d-score" style="font-size:11px;color:' + (state.score>=0?'#10b981':'#ef4444') + ';margin-bottom:2px;">Score: ' + (state.score>0?'+':'') + state.score + '</div>' +
          '<div class="m3d-best" style="font-size:11px;color:#94a3b8;">最优: ' + escapeHtml(state.best) + '</div>' +
          (flag ? '<div class="m3d-flag" style="font-size:13px;margin-top:4px;">' + flag + '</div>' : '') +
          '</div>';
      });
      rows += `
      <div class="m3d-row-label" style="display:flex;align-items:center;font-size:11px;color:#94a3b8;font-weight:600;padding-right:8px;white-space:nowrap;">${escapeHtml(row.label)}</div>
      ${cells}`;
    });

    matrixHtml = `
    <style>
      @keyframes m3d-pulse {
        0% { box-shadow: 0 0 12px rgba(6,182,212,0.4); transform: scale(1); }
        50% { box-shadow: 0 0 20px rgba(6,182,212,0.7); transform: scale(1.03); }
        100% { box-shadow: 0 0 12px rgba(6,182,212,0.4); transform: scale(1); }
      }
      .m3d-pulse { animation: m3d-pulse 2s infinite; }
      .m3d-detail-panel {
        margin-top: 16px; padding: 16px; border-radius: 10px;
        background: rgba(17,24,39,0.95); border: 1px solid #334155;
        display: none; transition: all 0.3s ease;
      }
      .m3d-detail-panel.active { display: block; }
      .m3d-detail-panel h3 { margin: 0 0 12px 0; color: #06b6d4; font-size: 16px; }
      .m3d-detail-panel table { width: 100%; border-collapse: collapse; font-size: 13px; }
      .m3d-detail-panel th, .m3d-detail-panel td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #1e293b; }
      .m3d-detail-panel th { color: #94a3b8; font-weight: 600; }
      .m3d-traj-chart { width: 100%; height: 280px; margin: 16px 0; }
    </style>
    <div class="merrill-3d-matrix" style="margin-bottom:20px;">
      <div class="m3d-header" style="display:grid;grid-template-columns:auto 1fr 1fr;gap:8px;margin-bottom:8px;font-size:12px;font-weight:700;color:#94a3b8;">
        <div></div>
        <div style="text-align:center;">信贷↑</div>
        <div style="text-align:center;">信贷↓</div>
      </div>
      <div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:6px;">
        ${rows}
      </div>
    </div>
    <div id="merrill-detail-panel" class="m3d-detail-panel"></div>`;

    // Render regional detail cards (indicators, trajectory)
    const renderRegionDetail = (r, key) => {
      if (!r) return '';
      const swColor = signalColor(r.signal_weight > 0 ? 'bullish' : r.signal_weight < 0 ? 'bearish' : 'neutral');
      const trajectory = r.quadrant_trajectory || [];
      let trajHtml = '';
      if (trajectory.length > 0) {
        trajHtml = '<div class="trajectory-box"><h4>美林时钟轨迹</h4>' +
          '<div id="m3d-traj-chart-' + key + '" class="m3d-traj-chart"></div></div>';
      }
      const indicators = r.indicators || {};
      const indHtml = Object.entries(indicators).map(([k, ind]) => {
        const f = freshnessBadge(ind.last_updated, ind.frequency);
        const merrillSourceUrl = ind.source_url ? '<a href="' + escapeHtml(ind.source_url) + '" target="_blank" class="source-link">📎 数据来源</a>' : '';
        return '<div class="indicator-card ' + getFreshness(ind.last_updated, ind.frequency).cls + '">' +
          '<div class="ind-header"><span class="ind-name">' + escapeHtml(ind.name) + '</span>' + f + '</div>' +
          '<div class="ind-value">' + fmtNum(ind.current) + ' <span class="ind-unit">' + escapeHtml(ind.unit||'') + '</span></div>' +
          '<div id="chart-merrill-' + key + '-' + k + '" class="chart-container" style="width:100%;height:120px;margin-top:8px;"></div>' +
          merrillSourceUrl +
          '</div>';
      }).join('');
      return '<div class="region-card">' +
        '<div class="region-header">' +
          '<span class="region-flag">' + (key==='us'?'🇺🇸':'🇨🇳') + '</span>' +
          '<span class="region-name">' + (key==='us'?'美国':'中国') + '</span>' +
          '<span class="region-phase">' + escapeHtml(r.current_phase||'') + '</span>' +
          '<span class="signal-weight-badge" style="color:' + swColor + '">' + (r.signal_weight>0?'+':'') + r.signal_weight + '</span>' +
        '</div>' +
        trajHtml +
        '<div class="indicators-grid">' + indHtml + '</div>' +
        '</div>';
    };

    var regionHtml = renderRegionDetail(layer.us, 'us') + renderRegionDetail(layer.cn, 'cn');
    
    // Phase 3: Schedule trajectory chart rendering after DOM update
    setTimeout(function() {
      if (layer.us && layer.us.quadrant_trajectory && layer.us.quadrant_trajectory.length) {
        renderMerrillTrajectoryChart('m3d-traj-chart-us', layer.us.quadrant_trajectory, 'us', usState);
      }
      if (layer.cn && layer.cn.quadrant_trajectory && layer.cn.quadrant_trajectory.length) {
        renderMerrillTrajectoryChart('m3d-traj-chart-cn', layer.cn.quadrant_trajectory, 'cn', cnState);
      }
    }, 100);
    
    return '<div class="merrill-full-layout">' +
      '<div class="merrill-matrix-wrap">' + matrixHtml + '</div>' +
      '<div class="merrill-regions-wrap">' + regionHtml + '</div>' +
    '</div>';
  }

  // Phase 3: ECharts trajectory ring chart
  function renderMerrillTrajectoryChart(containerId, trajectory, region, currentState) {
    var el = document.getElementById(containerId);
    if (!el || !trajectory || trajectory.length === 0) return;
    var chart = echarts.init(el, null, {renderer:'canvas'});
    chartInstances.push(chart);
    
    var regionLabel = region === 'us' ? '\ud83c\uddfa\ud83c\uddf8 美国' : '\ud83c\udde8\ud83c\uddf3 中国';
    var stateNames = MERRILL_3D_STATES.map(function(s){ return s.name; });
    var shortNames = {
      '金发女孩复苏':'金发复苏','无信贷支撑复苏':'无信贷复苏','过热':'过热',
      '滞胀前兆':'滞胀前兆','信贷驱动通胀':'信贷通胀','滞胀':'滞胀',
      '信贷宽松实体弱':'信贷宽松','衰退':'衰退'
    };
    
    // Build nodes (circular layout)
    var nodes = stateNames.map(function(name, i) {
      var angle = (i / stateNames.length) * 2 * Math.PI - Math.PI / 2;
      var x = 50 + 35 * Math.cos(angle);
      var y = 50 + 35 * Math.sin(angle);
      var isCur = currentState && currentState.name === name;
      var stateObj = MERRILL_3D_STATES.find(function(s){ return s.name === name; });
      return {
        name: shortNames[name] || name,
        x: x, y: y,
        symbolSize: isCur ? 28 : 16,
        itemStyle: {
          color: isCur ? '#06b6d4' : (stateObj && stateObj.score >= 0 ? '#10b981' : '#ef4444'),
          borderColor: isCur ? '#06b6d4' : '#334155',
          borderWidth: isCur ? 3 : 1,
          shadowBlur: isCur ? 15 : 0,
          shadowColor: isCur ? 'rgba(6,182,212,0.6)' : 'transparent'
        },
        label: {
          show: true,
          fontSize: isCur ? 12 : 10,
          color: isCur ? '#06b6d4' : '#94a3b8',
          fontWeight: isCur ? 'bold' : 'normal'
        },
        _fullName: name
      };
    });
    
    // Build edges from trajectory transitions
    var edges = [];
    for (var i = 0; i < trajectory.length - 1; i++) {
      var fromQ = trajectory[i].quadrant;
      var toQ = trajectory[i + 1].quadrant;
      var fromShort = shortNames[fromQ] || fromQ;
      var toShort = shortNames[toQ] || toQ;
      if (fromShort !== toShort) {
        edges.push({
          source: fromShort,
          target: toShort,
          lineStyle: {
            color: '#06b6d4',
            width: 2,
            curveness: 0.2,
            type: 'solid'
          },
          symbol: ['none', 'arrow'],
          symbolSize: 8
        });
      }
    }
    
    var option = {
      title: {
        text: regionLabel + ' 周期轨迹',
        left: 'center',
        top: 5,
        textStyle: { color: '#e2e8f0', fontSize: 13, fontWeight: '600' }
      },
      tooltip: Object.assign({}, tooltipConfig(), {
        formatter: function(params) {
          if (params.dataType === 'node') {
            var fullName = params.data._fullName || params.data.name;
            var st = MERRILL_3D_STATES.find(function(s){ return s.name === fullName; });
            if (st) {
              return '<b>' + escapeHtml(st.name) + '</b><br/>' +
                '增长:' + (st.growth==='up'?'↑':'↓') + ' 通胀:' + (st.inflation==='up'?'↑':'↓') + ' 信贷:' + (st.credit==='up'?'↑':'↓') + '<br/>' +
                'Score: ' + (st.score>0?'+':'') + st.score + '<br/>' +
                '最优: ' + escapeHtml(st.best);
            }
          }
          return params.data.source + ' → ' + params.data.target;
        }
      }),
      series: [{
        type: 'graph',
        layout: 'none',
        coordinateSystem: null,
        data: nodes,
        links: edges,
        roam: false,
        lineStyle: { opacity: 0.8 },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3 }
        }
      }]
    };
    chart.setOption(option);
  }

  // Phase 3: Global detail panel handler
  function initMerrillDetailPanel() {
    if (window._merrillDetail) return;
    window._merrillDetail = function(stateName) {
      var panel = document.getElementById('merrill-detail-panel');
      if (!panel) return;
      
      // Toggle if same state clicked
      if (panel.dataset.state === stateName && panel.classList.contains('active')) {
        panel.classList.remove('active');
        panel.dataset.state = '';
        return;
      }
      
      var state = MERRILL_3D_STATES.find(function(s){ return s.name === stateName; });
      if (!state) return;
      
      var ranking = STATE_ASSET_RANKING[stateName] || {};
      var scoreMeaning = state.score > 0 ? '看多' : state.score < 0 ? '看空' : '中性';
      var scoreColor = state.score > 0 ? '#10b981' : state.score < 0 ? '#ef4444' : '#94a3b8';
      
      // Asset ranking table
      var rankRows = Object.entries(ranking).map(function(e) {
        var label = ASSET_LABELS[e[0]] || e[0];
        var v = e[1];
        var color = v === '★★★' ? '#10b981' : v === '★★' ? '#84cc16' : v === '★' ? '#f59e0b' : '#6b7280';
        return '<tr><td>' + escapeHtml(label) + '</td><td style="color:' + color + ';font-weight:600;">' + escapeHtml(v) + '</td></tr>';
      }).join('');
      
      // Historical trajectory records
      var trajData = window._merrillData || {};
      var historyRows = '';
      ['us', 'cn'].forEach(function(region) {
        var traj = (trajData[region] && trajData[region].quadrant_trajectory) || [];
        var regionLabel = region === 'us' ? '\ud83c\uddfa\ud83c\uddf8 美国' : '\ud83c\udde8\ud83c\uddf3 中国';
        var filtered = traj.filter(function(t){ return t.quadrant === stateName; });
        filtered.forEach(function(t) {
          var duration = '--';
          if (t.start && t.end) {
            var dMs = new Date(t.end).getTime() - new Date(t.start).getTime();
            duration = Math.ceil(dMs / (1000*60*60*24)) + '天';
          } else if (!t.end) {
            duration = '进行中';
          }
          historyRows += '<tr><td>' + regionLabel + '</td><td>' + escapeHtml(t.start || '--') + ' → ' + escapeHtml(t.end || '至今') + '</td><td>' + escapeHtml(duration) + '</td></tr>';
        });
      });
      
      if (!historyRows) {
        historyRows = '<tr><td colspan="3" style="text-align:center;color:#64748b;">暂无历史记录</td></tr>';
      }
      
      var html = 
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
          '<h3 style="margin:0;">' + escapeHtml(stateName) + '</h3>' +
          '<button onclick="document.getElementById(\'merrill-detail-panel\').classList.remove(\'active\');document.getElementById(\'merrill-detail-panel\').dataset.state=\'\'" style="background:none;border:1px solid #475569;color:#94a3b8;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px;">✕ 关闭</button>' +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">' +
          '<div>' +
            '<h4 style="color:#94a3b8;font-size:13px;margin:0 0 8px 0;">📊 三维方向 & 评分</h4>' +
            '<div style="font-size:14px;margin-bottom:8px;">' +
              '<span style="margin-right:12px;">增长: <b style="color:' + (state.growth==='up'?'#10b981':'#ef4444') + '">' + (state.growth==='up'?'↑ 上行':'↓ 下行') + '</b></span>' +
              '<span style="margin-right:12px;">通胀: <b style="color:' + (state.inflation==='up'?'#ef4444':'#10b981') + '">' + (state.inflation==='up'?'↑ 上行':'↓ 下行') + '</b></span>' +
              '<span>信贷: <b style="color:' + (state.credit==='up'?'#10b981':'#ef4444') + '">' + (state.credit==='up'?'↑ 上行':'↓ 下行') + '</b></span>' +
            '</div>' +
            '<div style="font-size:14px;">Score: <b style="color:' + scoreColor + ';">' + (state.score>0?'+':'') + state.score + ' (' + scoreMeaning + ')</b></div>' +
            '<div style="font-size:13px;color:#94a3b8;margin-top:4px;">最优资产: ' + escapeHtml(state.best) + '</div>' +
          '</div>' +
          '<div>' +
            '<h4 style="color:#94a3b8;font-size:13px;margin:0 0 8px 0;">🏆 资产配置建议</h4>' +
            '<table><thead><tr><th>资产</th><th>评级</th></tr></thead><tbody>' + rankRows + '</tbody></table>' +
          '</div>' +
        '</div>' +
        '<div style="margin-top:16px;">' +
          '<h4 style="color:#94a3b8;font-size:13px;margin:0 0 8px 0;">📜 历史进入记录</h4>' +
          '<table><thead><tr><th>区域</th><th>时间段</th><th>持续时长</th></tr></thead><tbody>' + historyRows + '</tbody></table>' +
        '</div>';
      
      panel.innerHTML = html;
      panel.classList.add('active');
      panel.dataset.state = stateName;
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
  }

  /** Phase 2B: renderConsensus rewritten for v4 3-dimension scoring */
  function renderConsensus(consensus) {
    if (!consensus) return '';
    const scoreData = consensus.score || {};
    const usScore = scoreData.us ?? scoreData.us_raw ?? 0;
    const cnScore = scoreData.cn ?? scoreData.cn_raw ?? 0;
    const avgScore = typeof scoreData.us === 'number' && typeof scoreData.cn === 'number'
      ? ((scoreData.us + scoreData.cn) / 2) : (usScore + cnScore) / 2;
    const scoreColor = avgScore >= 70 ? '#10b981' : avgScore >= 50 ? '#f59e0b' : avgScore >= 30 ? '#f97316' : '#ef4444';

    const dims = consensus.dimension_scores || [];
    const dimRows = dims.map(d => {
      const usS = d.us_score ?? d.us_raw ?? 0;
      const cnS = d.cn_score ?? d.cn_raw ?? 0;
      const w = d.weight || 0;
      const weighted = d.weighted_score ?? ((usS * w + cnS * w) / 200).toFixed(2);
      return '<tr>' +
        '<td>' + escapeHtml(d.dimension) + '</td>' +
        '<td style="text-align:center;color:#60a5fa;">' + fmtNum(usS, 1) + '</td>' +
        '<td style="text-align:center;color:#f87171;">' + fmtNum(cnS, 1) + '</td>' +
        '<td style="text-align:center;">' + (w * 100) + '%</td>' +
        '<td style="text-align:right;font-weight:600;">' + fmtNum(weighted, 2) + '</td>' +
        '</tr>';
    }).join('');

    // constraint_override info
    const rules = consensus.scoring_rules || {};
    const co = rules.constraint_override || {};
    let constraintHtml = '';
    if (co.active || co.triggered) {
      const notes = (co.notes || co.description || []);
      const notesArr = Array.isArray(notes) ? notes : [notes];
      constraintHtml = '<div class="constraint-override-box" style="margin-top:16px;padding:12px;border-radius:8px;background:rgba(239,68,68,0.1);border:1px solid #ef4444;">' +
        '<h4 style="color:#fca5a5;margin-bottom:8px;">⚠️ 约束覆盖生效</h4>' +
        '<p style="color:#e2e8f0;font-size:13px;">' + escapeHtml(co.description || co.reason || 'C1/C2约束层生效，量化信号已降级') + '</p>' +
        (notesArr.length ? '<ul style="margin-top:6px;">' + notesArr.map(n => '<li style="color:#fca5a5;font-size:12px;">' + escapeHtml(n) + '</li>').join('') + '</ul>' : '') +
        '</div>';
    }

    // narrative_notes (N1/N2)
    const narrativeNotes = consensus.narrative_notes || {};
    let narrativeHtml = '';
    const noteKeys = Object.keys(narrativeNotes);
    if (noteKeys.length > 0) {
      const noteCards = noteKeys.map(k => {
        const note = narrativeNotes[k];
        const noteText = typeof note === 'string' ? note : (note.text || note.content || note.assessment || '');
        const noteTitle = k.replace(/_/g, ' ').replace(/^N\d+\s*/, '');
        return '<div class="narrative-note-card" style="padding:10px;border-radius:8px;background:rgba(139,92,246,0.08);border:1px solid #8b5cf640;margin-bottom:8px;">' +
          '<div style="font-size:12px;font-weight:700;color:#a78bfa;margin-bottom:4px;">' + escapeHtml(k) + '</div>' +
          '<p style="font-size:13px;color:#cbd5e1;margin:0;">' + escapeHtml(noteText) + '</p>' +
          '</div>';
      }).join('');
      narrativeHtml = '<div class="narrative-notes-section" style="margin-top:16px;">' +
        '<h4 style="color:#a78bfa;margin-bottom:10px;">📝 定性判断备注</h4>' +
        noteCards +
        '</div>';
    }

    return '<section class="v3-section" id="section-consensus">' +
      '<h2 class="section-title">🎯 周期共识度评分卡<span class="section-subtitle">3维量化评分</span></h2>' +
      '<div class="consensus-header" style="display:flex;align-items:center;gap:20px;margin-bottom:16px;">' +
        '<div class="consensus-score" style="font-size:36px;font-weight:800;color:' + scoreColor + ';">' + fmtNum(avgScore, 1) + '</div>' +
        '<div class="consensus-info">' +
          '<div style="display:flex;gap:16px;margin-bottom:4px;">' +
            '<span style="color:#60a5fa;font-size:14px;">🇺🇸 US: <strong>' + fmtNum(usScore, 1) + '</strong></span>' +
            '<span style="color:#f87171;font-size:14px;">🇨🇳 CN: <strong>' + fmtNum(cnScore, 1) + '</strong></span>' +
          '</div>' +
          '<div style="font-size:12px;color:#94a3b8;">' + freshnessBadge(consensus.last_updated) + ' ' + (consensus.last_updated || '') + '</div>' +
        '</div>' +
      '</div>' +
      '<table class="dimension-table" style="width:100%;border-collapse:collapse;font-size:13px;">' +
        '<thead><tr style="border-bottom:1px solid #334155;">' +
          '<th style="text-align:left;padding:8px;color:#94a3b8;">维度</th>' +
          '<th style="text-align:center;padding:8px;color:#60a5fa;">🇺🇸 US</th>' +
          '<th style="text-align:center;padding:8px;color:#f87171;">🇨🇳 CN</th>' +
          '<th style="text-align:center;padding:8px;color:#94a3b8;">权重</th>' +
          '<th style="text-align:right;padding:8px;color:#94a3b8;">加权得分</th>' +
        '</tr></thead>' +
        '<tbody>' + dimRows + '</tbody>' +
      '</table>' +
      constraintHtml +
      narrativeHtml +
    '</section>';
  }

  // ========== Chart Rendering ==========
  // Retry mechanism: if charts fail to render, retry after delay
  let _chartRetryCount = 0;
  const MAX_CHART_RETRIES = 3;

  function renderCharts(data, _skipSizeCheck, _failedCharts) {
    _skipSizeCheck = _skipSizeCheck || false;
    _failedCharts = _failedCharts || new Set();
    console.log('[Charts] renderCharts called. skipSizeCheck:', _skipSizeCheck, 'Instances:', chartInstances.length);
    const _echartsAvailable = typeof echarts !== 'undefined';
    console.log('[Charts] echarts available:', _echartsAvailable);

    // TFP chart (Kondratieff)
    const cnTfp = data.cycle_layers?.narrative_kondratieff?.indicators?.tfp_growth?.cn;
    const usTfp = data.cycle_layers?.narrative_kondratieff?.indicators?.tfp_growth?.us;
    console.log('[Charts] cnTfp history:', cnTfp?.history?.length, 'usTfp history:', usTfp?.history?.length);
    if (cnTfp?.history?.length) {
      const chartEl = document.getElementById('chart-kondratieff-tfp');
      console.log('[Charts] TFP container:', chartEl ? 'FOUND ('+chartEl.offsetWidth+'x'+chartEl.offsetHeight+')' : 'NOT FOUND');
      if (!chartEl) {
        console.error('[Charts] TFP container missing! Section HTML may not have been rendered.');
      }
      const toDateStr = (d) => /^\d{4}$/.test(String(d)) ? d + '-01-01' : d;
      const series = [{ name:'中国TFP', data:cnTfp.history.map(h=>[toDateStr(h.date), h.value]), type:'line', smooth:true,
        lineStyle:{width:2}, itemStyle:{color:'#3b82f6'},
        areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(59,130,246,0.3)'},{offset:1,color:'rgba(59,130,246,0.02)'}]}}
      }];
      if (usTfp?.history?.length) {
        series.push({ name:'美国TFP', data:usTfp.history.map(h=>[toDateStr(h.date), h.value]), type:'line', smooth:true,
          lineStyle:{width:2}, itemStyle:{color:'#8b5cf6'} });
      }
      let markLines = [];
      if (cnTfp.percentile) {
        const p = cnTfp.percentile;
        if (p.p25!=null) markLines.push({lineStyle:{type:'dashed',color:'#d1d5db'},label:{formatter:'p25'},data:[{yAxis:p.p25}]});
        if (p.p50!=null) markLines.push({lineStyle:{type:'dashed',color:'#9ca3af'},label:{formatter:'p50'},data:[{yAxis:p.p50}]});
        if (p.p75!=null) markLines.push({lineStyle:{type:'dashed',color:'#6b7280'},label:{formatter:'p75'},data:[{yAxis:p.p75}]});
      }
      const tfpResult = createChart('chart-kondratieff-tfp', {
        tooltip: {...tooltipConfig(), trigger:'axis'},
        legend:{textStyle:{color:COLORS.textSecondary}, top:0},
        grid: gridConfig({top:30}),
        xAxis:{type:'time', axisLine:{lineStyle:{color:COLORS.borderSubtle}},
          axisLabel:{color:COLORS.textMuted, fontSize:10, formatter:function(v){return new Date(v).getFullYear()}}},
        yAxis:{type:'value', name:'%', nameTextStyle:{color:COLORS.textMuted},
          axisLine:{lineStyle:{color:COLORS.borderSubtle}}, axisLabel:{color:COLORS.textMuted},
          splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
        series
      }, _skipSizeCheck);
      if (!tfpResult) _failedCharts.add('chart-kondratieff-tfp');
      else _failedCharts.delete('chart-kondratieff-tfp');
    } else {
      console.warn('[Charts] No TFP data to render');
    }

    // Perez key_ratio chart
    const kr = data.cycle_layers?.narrative_perez?.key_ratio;
    console.log('[Charts] Perez key_ratio history:', kr?.history?.length);
    if (kr?.history?.length) {
      const perezEl = document.getElementById('chart-perez-ratio');
      console.log('[Charts] Perez container:', perezEl ? 'FOUND ('+perezEl.offsetWidth+'x'+perezEl.offsetHeight+')' : 'NOT FOUND');
      if (!perezEl) {
        console.error('[Charts] Perez container missing!');
      }
      const tp = kr.threshold_params || {};
      const frenzyLine = tp.frenzy_threshold ? [{lineStyle:{type:'solid',color:'#ef4444'},label:{formatter:'Frenzy阈值'},data:[{yAxis:tp.frenzy_threshold}]}] : [];
      const meanLine = tp.mean!=null ? [{lineStyle:{type:'dashed',color:'#6b7280'},label:{formatter:'均值'},data:[{yAxis:tp.mean}]}] : [];
      const toDateStr2 = (d) => /^\d{4}$/.test(String(d)) ? d + '-01-01' : d;
      const perezResult = createChart('chart-perez-ratio', {
        tooltip: {...tooltipConfig(), trigger:'axis'},
        grid: gridConfig({top:20}),
        xAxis:{type:'time', axisLine:{lineStyle:{color:COLORS.borderSubtle}},
          axisLabel:{color:COLORS.textMuted, fontSize:10, formatter:function(v){return new Date(v).getFullYear()}}},
        yAxis:{type:'value', axisLine:{lineStyle:{color:COLORS.borderSubtle}}, axisLabel:{color:COLORS.textMuted},
          splitLine:{lineStyle:{color:COLORS.borderSubtle,type:'dashed'}}},
        series:[{type:'line',data:kr.history.map(h=>[toDateStr2(h.date),h.value]),smooth:false,
          lineStyle:{width:2,color:'#8b5cf6'},itemStyle:{color:'#8b5cf6'},symbol:'circle',symbolSize:6,
          markLine:{data:[...frenzyLine,...meanLine],symbol:'none'}}]
      }, _skipSizeCheck);
      if (!perezResult) _failedCharts.add('chart-perez-ratio');
      else _failedCharts.delete('chart-perez-ratio');
    } else {
      console.warn('[Charts] No Perez data to render');
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

    // G-07: Layer 3 Rate Regime sparkline charts
    const layer3 = data.cycle_layers?.constraint_rate_regime;
    if (layer3) {
      ['structural', 'forward_looking', 'market_based'].forEach(groupName => {
        const groupData = layer3[groupName];
        if (!groupData) return;
        const indicators = groupData.indicators || {};
        Object.entries(indicators).forEach(([indKey, ind]) => {
          if (ind.us || ind.cn) {
            ['us', 'cn'].forEach(rk => {
              const rData = ind[rk];
              const hist = rData?.history || ind.history;
              if (!hist?.length) return;
              const chartId = 'chart-rate-' + groupName + '-' + indKey + '_' + rk;
              const el = document.getElementById(chartId);
              if (!el) return;
              const rColor = rk === 'us' ? '#3b82f6' : '#ef4444';
              const rName = rk === 'us' ? '美国' : '中国';
              createChart(chartId, {
                tooltip: {...tooltipConfig(), trigger: 'axis'},
                grid: gridConfig({top: 8, bottom: 12, left: 30, right: 8}),
                xAxis: {type: 'category', data: hist.map(h => h.date), show: false},
                yAxis: {type: 'value', axisLabel: {color: COLORS.textMuted, fontSize: 8},
                  splitLine: {lineStyle: {color: COLORS.borderSubtle, type: 'dashed'}},
                  axisLine: {show: false}},
                series: [{name: rName, type: 'line', data: hist.map(h => [h.date, h.value]),
                  smooth: true, symbol: 'none', lineStyle: {width: 2, color: rColor},
                  itemStyle: {color: rColor},
                  areaStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{offset: 0, color: rColor.replace(')', ',0.25)').replace('rgb', 'rgba')},
                      {offset: 1, color: 'transparent'}]}}}]
              });
            });
          } else if (ind.history?.length) {
            const chartId = 'chart-rate-' + groupName + '-' + indKey;
            const el = document.getElementById(chartId);
            if (!el) return;
            createChart(chartId, {
              tooltip: {...tooltipConfig(), trigger: 'axis'},
              grid: gridConfig({top: 8, bottom: 12, left: 30, right: 8}),
              xAxis: {type: 'category', data: ind.history.map(h => h.date), show: false},
              yAxis: {type: 'value', axisLabel: {color: COLORS.textMuted, fontSize: 8},
                splitLine: {lineStyle: {color: COLORS.borderSubtle, type: 'dashed'}},
                axisLine: {show: false}},
              series: [{type: 'line', data: ind.history.map(h => [h.date, h.value]),
                smooth: true, symbol: 'none', lineStyle: {width: 2, color: '#8b5cf6'},
                itemStyle: {color: '#8b5cf6'},
                areaStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                  colorStops: [{offset: 0, color: 'rgba(139,92,246,0.25)'},
                    {offset: 1, color: 'transparent'}]}}}]
            });
          }
        });
      });
    }

    // Juglar/Kitchin/Merrill indicator charts
    const layerPrefixMap = {
      'cycle_juglar': 'juglar',
      'cycle_kitchin': 'kitchin',
      'cycle_merrill_3d': 'merrill'
    };
    ['cycle_juglar','cycle_kitchin','cycle_merrill_3d'].forEach(layerKey => {
      const layer = data.cycle_layers?.[layerKey];
      if (!layer) return;
      const prefix = layerPrefixMap[layerKey] || 'juglar';
      ['us','cn'].forEach(regionKey => {
        const r = layer[regionKey];
        if (!r?.indicators) return;
        Object.entries(r.indicators).forEach(([indKey, ind]) => {
          const chartId = `chart-${prefix}-${regionKey}-${indKey}`;
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
    // Set assessment date for freshness calculations
    _assessmentDate = data._meta?.assessment_date || null;

    const container = document.getElementById('cycle-content');
    if (!container) return;

    const layers = data.cycle_layers || {};
    const cross = data.cross_analysis || {};
    const allocation = data.asset_allocation || null;
    const synthesis = data.synthesis || null;
    const creditImpulse = data.credit_impulse || null;
    const consensus = data.cycle_consensus || data.cross_analysis?.consensus || null;

    let html = '';
    // Meta header
    html += renderMeta(data._meta);
    // ★ 评分卡+配置建议置顶（结论先行）
    if (consensus) {
      html += renderConsensusSummary(consensus);
    }
    // Layer 0: Debt Cycle
    html += renderConstraintDebt(layers.constraint_debt_cycle);
    // Layer 1: Kondratieff
    html += renderNarrativeKondratieff(layers.narrative_kondratieff);
    // Layer 2: Perez
    html += renderNarrativePerez(layers.narrative_perez);
    // Layer 3: Rate Regime + High Rate Tracker
    html += renderConstraintRate(layers.constraint_rate_regime, cross.high_rate_tracker);
    // Layer 4: Juglar
    html += `
    <section class="v3-section" id="section-juglar">
      <h2 class="section-title">⚙️ 朱格拉周期<span class="section-subtitle">第4层</span></h2>
      <div class="regions-row">${renderJuglar(layers.cycle_juglar)}</div>
    </section>`;
    // Layer 5: Kitchner
    html += `
    <section class="v3-section" id="section-kitchin">
      <h2 class="section-title">📦 基钦周期<span class="section-subtitle">第5层</span></h2>
      <div class="regions-row">${renderJuglar(layers.cycle_kitchin, 'kitchin')}</div>
    </section>`;
    // Layer 6: Merrill
    html += `
    <section class="v3-section" id="section-merrill">
      <h2 class="section-title">🕐 美林时钟 3D（增长×通胀×信贷）<span class="section-subtitle">第6层</span></h2>
      <div class="merrill-combined-label" style="font-size:12px;color:#94a3b8;margin-bottom:8px;">📊 中美综合矩阵（点击单元格查看详情）</div>
      <div class="regions-row">${renderMerrill3D(layers.cycle_merrill_3d)}</div>
    </section>`;
    // Credit Impulse (#9)
    html += renderCreditImpulse(creditImpulse);
    // Portfolio Guide (Pyramid #6/#7)
    html += renderPortfolioGuide(cross.portfolio_guide);
    // Asset Allocation (#5/#11/#14)
    html += renderAssetAllocation(allocation);
    // Synthesis
    html += renderSynthesis(synthesis);
    // ★ 中美周期错位矩阵移到最底部（总结性内容放页面末尾）
    html += renderUsChinaMatrix(cross.us_china_matrix);

    container.innerHTML = html;
    
    // Phase 3: Store Merrill data for detail panel access
    window._merrillData = {
      us: (data.cycle_layers && data.cycle_layers.cycle_merrill_3d && data.cycle_layers.cycle_merrill_3d.us) || null,
      cn: (data.cycle_layers && data.cycle_layers.cycle_merrill_3d && data.cycle_layers.cycle_merrill_3d.cn) || null
    };
    initMerrillDetailPanel();

    // Render ECharts after DOM update — per-chart retry tracking
    let _failedCharts = new Set();
    let _retryAttempt = 0;

    function tryRenderCharts(skipSizeCheck) {
      _failedCharts.clear();
      renderCharts(data, skipSizeCheck, _failedCharts);
    }

    requestAnimationFrame(() => {
      tryRenderCharts(false);
      if (_failedCharts.size > 0) {
        console.log('[Charts]', _failedCharts.size, 'chart(s) failed, retry 1 in 500ms:', [..._failedCharts]);
        setTimeout(() => {
          tryRenderCharts(false);
          if (_failedCharts.size > 0) {
            console.log('[Charts] Still failing, retry 2 in 1500ms (force):', [..._failedCharts]);
            setTimeout(() => tryRenderCharts(true), 1500);
          }
        }, 500);
      }
    });

    // Debounced resize handler for ECharts
    if (!resizeHandler) {
      resizeHandler = () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          chartInstances.forEach(c => { try { c.resize(); } catch(e){} });
        }, 200);
      };
      window.addEventListener('resize', resizeHandler);
    }
  }

  function dispose() {
    chartInstances.forEach(c => { try { c.dispose(); } catch(e){} });
    chartInstances = [];
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler);
      resizeHandler = null;
    }
  }

  return { render, dispose };
})();

// Expose to global scope
window.CycleV3Module = CycleV3Module;
