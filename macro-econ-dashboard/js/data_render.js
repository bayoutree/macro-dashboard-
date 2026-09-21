/**
 * data_render.js — 指标卡片渲染模块（五层结构版）
 * 
 * 五层结构：
 * 1. 宏观象限（增长×通胀 → 象限定位）
 * 2. 四大部门拆解（居民/企业/政府/对外 → 找驱动与漏洞）
 * 3. 双政策面（货币+财政 → 解释经济为何是这样）
 * 4. 交叉验证（领先/同步/滞后 → 验真假拐点）
 * 5. 宏观结论（定性+主要矛盾+情景+资产信号灯）
 */

// ==================== 工具函数 ====================

function safeVal(v) {
  return (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v))) ? null : v;
}

function fmtNum(v, decimals = 1) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Number(v).toFixed(decimals);
}

function changeClass(change) {
  if (change === null || change === undefined || isNaN(change)) return 'change-flat';
  if (change > 0) return 'change-up';
  if (change < 0) return 'change-down';
  return 'change-flat';
}

function changeArrow(change) {
  if (change === null || change === undefined || isNaN(change)) return '';
  if (change > 0) return '↑';
  if (change < 0) return '↓';
  return '→';
}

function pctClass(pct) {
  if (pct === null || pct === undefined || isNaN(pct)) return 'pct-na';
  if (pct < 33) return 'pct-low';
  if (pct < 67) return 'pct-mid';
  return 'pct-high';
}

function pctLabel(pct) {
  if (pct === null || pct === undefined || isNaN(pct)) return 'P—';
  return `P${Math.round(pct)}`;
}

// ==================== ECharts 实例管理 ====================
const chartInstances = [];

function renderMiniChart(domId, history, change) {
  const dom = document.getElementById(domId);
  if (!dom) return;
  if (!history || history.length < 2) {
    dom.innerHTML = '<div class="no-data text-center py-2 text-xs text-gray-600">无历史数据</div>';
    return;
  }
  // Fallback: ensure container has height (prevents Tailwind CDN timing issue)
  if (dom.offsetHeight < 10) { dom.style.height = '50px'; }
  // Force chart to use parent metric-card's width, not full page width
  const card = dom.closest('.metric-card');
  if (card) {
    const cw = card.offsetWidth || card.getBoundingClientRect().width;
    if (cw > 50 && cw < 2000) dom.style.width = cw + 'px';
  }
  if (dom.offsetHeight < 10) dom.style.height = '50px';
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  const values = history.map(h => h.value);
  const dates = history.map(h => h.date);
  const lineColor = change > 0 ? '#ef4444' : change < 0 ? '#10b981' : '#6b7280';

  chart.setOption({
    grid: { top: 5, bottom: 15, left: 5, right: 5 },
    xAxis: { type: 'category', data: dates, show: false },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line', data: values, smooth: true, symbol: 'none',
      lineStyle: { color: lineColor, width: 1.5 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: lineColor + '30' }, { offset: 1, color: lineColor + '05' }
      ])},
    }],
    tooltip: { trigger: 'axis', formatter: p => `${p[0].axisValue}<br/>${fmtNum(p[0].value)}`,
      backgroundColor: 'rgba(17,24,39,0.95)', borderColor: '#1e2d3d', textStyle: { color: '#e5e7eb', fontSize: 11 } },
  });
  chartInstances.push(chart);
}

function renderLargeChart(domId, series, opts = {}) {
  const dom = document.getElementById(domId);
  if (!dom) return;
  // Fallback: ensure container has dimensions
  if (dom.offsetHeight < 10) { dom.style.minHeight = '200px'; }
  // Force chart to use parent container's width
  const parent = dom.parentElement;
  if (parent) {
    const pw = parent.offsetWidth || parent.getBoundingClientRect().width;
    if (pw > 50 && pw < 2000) dom.style.width = pw + 'px';
  }
  if (dom.offsetHeight < 10) dom.style.minHeight = '200px';
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  chart.setOption({
    grid: { top: 30, bottom: 30, left: 60, right: 30 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(17,24,39,0.95)', borderColor: '#1e2d3d', textStyle: { color: '#e5e7eb', fontSize: 12 } },
    xAxis: { type: 'category', data: series.dates || [], axisLine: { lineStyle: { color: '#1e2d3d' } }, axisLabel: { color: '#6b7280', fontSize: 11 } },
    yAxis: { type: 'value', scale: true, axisLine: { lineStyle: { color: '#1e2d3d' } }, axisLabel: { color: '#6b7280', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e2d3d', type: 'dashed' } } },
    series: [{ name: series.name || '', type: 'line', data: series.values || [], smooth: true, symbol: 'circle', symbolSize: 4,
      lineStyle: { color: opts.color || '#3b82f6', width: 2 },
      areaStyle: opts.area ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: (opts.color || '#3b82f6') + '30' }, { offset: 1, color: (opts.color || '#3b82f6') + '05' }
      ])} : undefined,
    }],
  });
  chartInstances.push(chart);
}

// ==================== 第一层：宏观象限 ====================

function renderEconomicCycle(countryData, country) {
  // 新版第一层：经济周期定位
  // 中国 → 货币-信用模型
  // 美国 → 经典美林 + 流动性第三轴
  const ec = countryData.economic_cycle;
  if (!ec) {
    // 兼容旧数据：没有 economic_cycle 时退回象限展示
    return renderQuadrantLegacy(countryData);
  }

  const phaseColorMap = {
    '🟢🟢': '#10b981',
    '🟢': '#10b981',
    '🟡': '#f59e0b',
    '🔴': '#ef4444',
    '🔴🔴': '#dc2626',
    '⚪': '#6b7280',
  };
  const phaseColor = phaseColorMap[ec.icon] || '#3b82f6';

  // 资产基准 → 8 类资产信号灯（理论驱动）
  const assetLights = deriveAssetSignals(ec.asset_base, country);

  let dimsHtml = '';
  if (country === 'china') {
    // 中国：货币 + 信用 两维
    dimsHtml = renderCnCycleDims(ec);
  } else {
    // 美国：美林象限 + 流动性 第三轴
    dimsHtml = renderUsCycleDims(ec, countryData);
  }

  return `
    <div class="bg-gradient-to-br from-dash-card to-blue-900/10 rounded-xl p-6 border border-dash-border mb-6">
      <div class="flex items-start justify-between mb-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-semibold text-white">🎯 第一层：经济周期定位</h2>
          <span class="text-xs text-gray-600 px-2 py-0.5 bg-dash-bg/50 rounded">${ec.method}</span>
        </div>
        <span class="quadrant-badge text-base" style="background:${phaseColor}20; color:${phaseColor}; border:1px solid ${phaseColor}50;">
          ${ec.icon} ${ec.phase}
        </span>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div class="p-3 bg-dash-bg/40 rounded-lg border border-dash-border/50">
          <div class="text-xs text-gray-500 mb-1">资产基准（理论映射）</div>
          <div class="text-sm font-mono text-gray-200">${ec.asset_base}</div>
        </div>
        <div class="p-3 bg-dash-bg/40 rounded-lg border border-dash-border/50">
          <div class="text-xs text-gray-500 mb-1">经济逻辑</div>
          <div class="text-xs text-gray-300 leading-relaxed">${ec.asset_desc}</div>
        </div>
      </div>

      ${dimsHtml}

      <div class="mt-4 p-3 bg-dash-bg/30 rounded-lg border border-dash-border/30">
        <div class="text-xs text-gray-500 mb-1">📌 资产基准（理论内核 → 第五层做完整映射）</div>
        <div class="text-sm font-mono text-gray-200 leading-relaxed">${ec.asset_base}</div>
      </div>
    </div>
  `;
}

// 兼容老数据的象限展示（无 economic_cycle 时用）
function renderQuadrantLegacy(data) {
  const gdp = data.quadrant?.gdp;
  const cpi = data.quadrant?.cpi;
  const ppi = data.quadrant?.ppi;
  if (!gdp && !cpi) return '<div class="bg-dash-card rounded-xl p-6 border border-dash-border"><p class="text-gray-500 text-sm">经济周期数据加载中...</p></div>';
  const gdpVal = gdp?.value;
  const cpiVal = cpi?.value;
  const gdpPct = gdp?.pct_5y;
  const cpiPct = cpi?.pct_5y;
  let quadrant, quadrantDesc, quadrantColor;
  const gdpHigh = gdpPct !== null && gdpPct !== undefined && gdpPct > 45;
  const cpiHigh = cpiPct !== null && cpiPct !== undefined && cpiPct > 45;
  if (gdpHigh && cpiHigh) { quadrant = '过热'; quadrantColor = '#ef4444'; }
  else if (!gdpHigh && cpiHigh) { quadrant = '滞胀'; quadrantColor = '#f59e0b'; }
  else if (!gdpHigh && !cpiHigh) { quadrant = '衰退'; quadrantColor = '#6b7280'; }
  else { quadrant = '复苏'; quadrantColor = '#10b981'; }
  const metrics = [gdp, cpi, ppi].filter(Boolean).map((m, i) => {
    const names = ['GDP增长', 'CPI通胀', 'PPI通胀'];
    return renderMiniMetricCard(names[i], m, `q-${i}`);
  }).join('');
  return `
    <div class="bg-dash-card rounded-xl p-6 border border-dash-border mb-6">
      <div class="flex items-center gap-3 mb-4">
        <h2 class="text-lg font-semibold text-white">🎯 第一层：宏观象限定位</h2>
        <span class="quadrant-badge" style="background:${quadrantColor}20; color:${quadrantColor}; border:1px solid ${quadrantColor}50;">${quadrant}</span>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">${metrics}</div>
    </div>
  `;
}

function renderCnCycleDims(ec) {
  const m = ec.monetary, c = ec.credit;
  const dr007 = ec.monetary_indicators?.dr007;
  const m2 = ec.monetary_indicators?.m2;
  const m1 = ec.credit_indicators?.m1;
  const shrzgm = ec.credit_indicators?.shrzgm;
  return `
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="p-4 bg-dash-bg/30 rounded-lg border border-dash-border/50">
        <div class="flex items-center justify-between mb-3">
          <div class="text-xs text-gray-500 uppercase tracking-wide">货币维度 (DR007 / M2)</div>
          <span class="text-base">${m.icon} ${m.state}</span>
        </div>
        <div class="text-xs text-gray-400 leading-relaxed mb-3">${m.desc}</div>
        <div class="grid grid-cols-2 gap-2">
          ${dr007 ? renderMiniMetricCard('DR007', dr007, 'cn-m-d') : ''}
          ${m2 ? renderMiniMetricCard('M2同比', m2, 'cn-m-m2') : ''}
        </div>
      </div>
      <div class="p-4 bg-dash-bg/30 rounded-lg border border-dash-border/50">
        <div class="flex items-center justify-between mb-3">
          <div class="text-xs text-gray-500 uppercase tracking-wide">信用维度 (M1 / 社融)</div>
          <span class="text-base">${c.icon} ${c.state}</span>
        </div>
        <div class="text-xs text-gray-400 leading-relaxed mb-3">${c.desc}</div>
        <div class="grid grid-cols-2 gap-2">
          ${m1 ? renderMiniMetricCard('M1同比', m1, 'cn-c-m1') : ''}
          ${shrzgm && shrzgm.value !== undefined ? renderMiniMetricCard('社融存量', shrzgm, 'cn-c-sf') : ''}
        </div>
      </div>
    </div>
  `;
}

function renderUsCycleDims(ec, countryData) {
  const q = ec.quadrant, l = ec.liquidity;
  const gdp = ec.quadrant_indicators?.gdp;
  const cpi = ec.quadrant_indicators?.cpi;
  const ppi = ec.quadrant_indicators?.ppi;
  const fed = ec.liquidity_indicators?.fed_funds;
  const ust10 = ec.liquidity_indicators?.ust_10y;
  const tips = ec.liquidity_indicators?.tips_10y;
  const hy = ec.liquidity_indicators?.hy_spread;
  return `
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="p-4 bg-dash-bg/30 rounded-lg border border-dash-border/50">
        <div class="flex items-center justify-between mb-3">
          <div class="text-xs text-gray-500 uppercase tracking-wide">美林象限 (GDP × CPI)</div>
          <span class="text-base">${q.icon} ${q.state}</span>
        </div>
        <div class="text-xs text-gray-400 leading-relaxed mb-3">
          GDP P${fmtNum(gdp?.pct_5y)} ${gdp?.pct_5y > 50 ? '高于中位' : '低于中位'} ·
          CPI P${fmtNum(cpi?.pct_5y)} ${cpi?.pct_5y > 50 ? '高于中位' : '低于中位'}
        </div>
        <div class="grid grid-cols-3 gap-2">
          ${gdp ? renderMiniMetricCard('GDP', gdp, 'us-q-g') : ''}
          ${cpi ? renderMiniMetricCard('CPI', cpi, 'us-q-c') : ''}
          ${ppi ? renderMiniMetricCard('PPI', ppi, 'us-q-p') : ''}
        </div>
      </div>
      <div class="p-4 bg-dash-bg/30 rounded-lg border border-dash-border/50">
        <div class="flex items-center justify-between mb-3">
          <div class="text-xs text-gray-500 uppercase tracking-wide">流动性第三轴</div>
          <span class="text-base">${l.icon} ${l.state}</span>
        </div>
        <div class="text-xs text-gray-400 leading-relaxed mb-3">${l.desc}</div>
        <div class="grid grid-cols-2 gap-2">
          ${fed ? renderMiniMetricCard('Fed Funds', fed, 'us-l-ff') : ''}
          ${tips ? renderMiniMetricCard('10Y实际利率', tips, 'us-l-tips') : ''}
          ${ust10 ? renderMiniMetricCard('10Y美债', ust10, 'us-l-u10') : ''}
          ${hy ? renderMiniMetricCard('HY信用利差', hy, 'us-l-hy') : ''}
        </div>
      </div>
    </div>
  `;
}

// 由经济周期的资产基准字符串 → 8 类资产信号灯（理论驱动）
function deriveAssetSignals(assetBase, country) {
  // 8 类资产顺序固定
  const ASSETS = [
    { key: 'cn_stock', label: 'A股', icon: '🇨🇳📈' },
    { key: 'cn_bond', label: '中债', icon: '🇨🇳🏦' },
    { key: 'cn_realestate', label: '中房产', icon: '🇨🇳🏠' },
    { key: 'us_stock', label: '美股', icon: '🇺🇸📈' },
    { key: 'us_bond', label: '美债', icon: '🇺🇸🏦' },
    { key: 'usd', label: '美元', icon: '💵' },
    { key: 'gold', label: '黄金', icon: '🥇' },
    { key: 'commodity', label: '商品', icon: '🛢️' },
  ];
  // 解析 assetBase 字符串，找每类资产的位次
  // 例："现金>黄金>商品≈债券>股票" → 现金=1, 黄金=2, 商品≈债券=3.5, 股票=5
  const ranking = parseAssetRanking(assetBase, country);
  return ASSETS.map(a => {
    const rank = ranking[a.key] ?? 5;  // 未提及=中性
    let sig, color;
    if (rank <= 2) { sig = '偏多'; color = '#10b981'; }
    else if (rank >= 4) { sig = '偏空'; color = '#ef4444'; }
    else { sig = '中性'; color = '#f59e0b'; }
    // 中国专属：美股/美债/美元 在中国分析框架里不太相关，置灰
    const dimmed = country === 'china' && ['us_stock', 'us_bond', 'usd', 'gold', 'commodity'].includes(a.key);
    const opacity = dimmed ? 'opacity-50' : '';
    return `
      <div class="flex flex-col items-center gap-1 px-2 py-2 bg-dash-bg/50 rounded border border-dash-border min-w-[80px] ${opacity}">
        <span class="text-base">${a.icon}</span>
        <span class="text-[10px] text-gray-500">${a.label}</span>
        <span class="signal-light text-[10px]" style="background:${color}20; color:${color}; border:1px solid ${color}50;">
          <span class="dot" style="background:${color}"></span>${sig}
        </span>
      </div>
    `;
  }).join('');
}

function parseAssetRanking(s, country) {
  // 解析 "A>B>C≈D>E" → {code: rank}
  // country 决定"股票/债券/现金"映射到本国还是美国资产
  const STOCK = country === 'us' ? 'us_stock' : 'cn_stock';
  const BOND = country === 'us' ? 'us_bond' : 'cn_bond';
  const CASH = country === 'us' ? 'usd' : 'cash';
  const ALIAS = {
    '股票': STOCK, '中国股市': STOCK, 'A股': STOCK, '美股': STOCK, '标普': STOCK, '成长股': STOCK, '成长': STOCK, '价值股': STOCK, '价值': STOCK,
    '债券': BOND, '中债': BOND, '美债': BOND, '长久期国债': BOND, '利率债': BOND, '信用债': BOND, '短久期债券': BOND, '短债': BOND, '长久期债券': BOND,
    '房地产': 'cn_realestate', '房产': 'cn_realestate',
    '美元': 'usd',
    '黄金': 'gold',
    '商品': 'commodity', '大宗商品': 'commodity',
    '现金': CASH,
  };
  const out = {};
  // 按 > / ≈ 分组
  const groups = s.split('>').map(g => g.split('≈').map(x => x.trim()).filter(Boolean));
  groups.forEach((g, gi) => {
    g.forEach(item => {
      // 移除"（含xxx）"等修饰
      const base = item.replace(/（.*）/g, '').trim();
      const aliasKey = Object.keys(ALIAS).find(k => base.includes(k));
      if (aliasKey) {
        const code = ALIAS[aliasKey];
        if (!out[code]) {
          out[code] = gi + 1;  // group index from 0
        }
      }
    });
  });
  return out;
}

function renderMiniMetricCard(name, metric, prefix) {
  const v = safeVal(metric.value);
  const change = safeVal(metric.change);
  const pct5 = safeVal(metric.pct_5y);
  const pct10 = safeVal(metric.pct_10y);
  const unit = metric.unit || '';
  const date = metric.date || '—';
  const chartId = `mini-${prefix}`;

  return `
    <div class="metric-card">
      <div class="flex items-start justify-between mb-1">
        <div>
          <div class="text-sm font-medium text-gray-300">${name}</div>
          <div class="text-xs text-gray-600 font-mono">${date}</div>
        </div>
        <div class="flex gap-1">
          <span class="pct-tag ${pctClass(pct5)}" title="5年分位数">5Y ${pctLabel(pct5)}</span>
          <span class="pct-tag ${pctClass(pct10)}" title="10年分位数">10Y ${pctLabel(pct10)}</span>
        </div>
      </div>
      <div class="flex items-baseline gap-2 mb-2">
        <span class="text-2xl font-bold text-white font-mono">${fmtNum(v)}</span>
        ${unit ? `<span class="text-xs text-gray-500">${unit}</span>` : ''}
        ${change !== null ? `<span class="text-xs font-mono ${changeClass(change)}">${changeArrow(change)} ${fmtNum(Math.abs(change))}</span>` : ''}
      </div>
      <div id="${chartId}" class="w-full" style="height:50px"></div>
    </div>
  `;
}

// ==================== 第二层：四大部门拆解 ====================

function renderSectors(sectorsData) {
  if (!sectorsData) return '';
  
  const deptConfig = [
    { key: 'household', title: '居民部门', icon: '🏠', desc: '消费底座（C占GDP大头）', watchFields: ['retail', 'income', 'house_price', 'employment'] },
    { key: 'corporate', title: '企业部门', icon: '🏭', desc: '供给+投资需求', watchFields: ['industrial_profit', 'mfg_investment', 'pmi'] },
    { key: 'government', title: '政府部门', icon: '🏛️', desc: '财政托底力度', watchFields: ['fiscal_balance', 'special_bond'] },
    { key: 'external', title: '对外部门', icon: '🌍', desc: '外需+资本流', watchFields: ['export', 'import', 'trade_balance'] },
  ];

  const deptHtml = deptConfig.map(dept => {
    const data = sectorsData[dept.key];
    if (!data || typeof data !== 'object') {
      return `
        <div class="dept-card">
          <div class="dept-header"><span class="dept-icon">${dept.icon}</span> ${dept.title}</div>
          <p class="text-xs text-gray-600 mb-2">${dept.desc}</p>
          <p class="text-xs text-gray-700">数据加载中...</p>
        </div>
      `;
    }
    
    const cards = Object.entries(data).map(([key, metric]) => {
      return renderMetricCard(key, metric, `sect-${dept.key}-${key}`);
    }).join('');
    
    // 部门诊断
    let diagnosis = '';
    const allValues = Object.values(data).map(m => m?.value).filter(v => v !== null && v !== undefined);
    if (allValues.length > 0) {
      const avg = allValues.reduce((a, b) => a + b, 0) / allValues.length;
      if (dept.key === 'household') {
        if (avg < 2) diagnosis = '<span class="text-red-400">消费疲软</span>，需求不足';
        else if (avg > 6) diagnosis = '<span class="text-green-400">消费强劲</span>，需求旺盛';
        else diagnosis = '<span class="text-yellow-400">消费温和</span>，需求平稳';
      } else if (dept.key === 'corporate') {
        if (avg < 0) diagnosis = '<span class="text-red-400">企业收缩</span>，投资意愿低';
        else if (avg > 8) diagnosis = '<span class="text-green-400">企业扩张</span>，投资积极';
        else diagnosis = '<span class="text-yellow-400">企业平稳</span>，投资温和增长';
      } else if (dept.key === 'external') {
        const exports = data.export?.value;
        const imports = data.import?.value;
        if (exports !== null && imports !== null) {
          if (exports > imports) diagnosis = '<span class="text-green-400">顺差扩大</span>，外需支撑';
          else diagnosis = '<span class="text-red-400">顺差收窄</span>，外需走弱';
        }
      }
    }

    return `
      <div class="dept-card">
        <div class="dept-header">
          <span class="dept-icon">${dept.icon}</span> ${dept.title}
          <span class="text-xs text-gray-600 font-normal ml-2">${dept.desc}</span>
        </div>
        ${diagnosis ? `<div class="dept-diagnosis">诊断：${diagnosis}</div>` : ''}
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
          ${cards}
        </div>
      </div>
    `;
  }).join('');

  return `
    <div class="mb-6">
      <div class="flex items-center gap-2 mb-4">
        <h2 class="group-title">🔍 第二层：四大部门拆解</h2>
        <span class="text-xs text-gray-600">找驱动与漏洞 — 哪个部门在拉动/拖累经济？</span>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">${deptHtml}</div>
    </div>
  `;
}

// ==================== 第三层：双政策面 ====================

function renderPolicy(policyData) {
  if (!policyData) return '';

  const monetary = policyData.monetary || {};
  const fiscal = policyData.fiscal || {};

  const monetaryCards = Object.entries(monetary).map(([key, metric]) => {
    return renderMetricCard(key, metric, `pol-m-${key}`);
  }).join('');

  const fiscalCards = Object.entries(fiscal).map(([key, metric]) => {
    return renderMetricCard(key, metric, `pol-f-${key}`);
  }).join('');

  // 政策姿态判断
  let monetaryStance = '';
  const dr007 = monetary.dr007;
  if (dr007) {
    const p5 = dr007.pct_5y;
    if (p5 < 20) monetaryStance = '<span class="text-green-400">宽松</span>（DR007处历史低位）';
    else if (p5 > 80) monetaryStance = '<span class="text-red-400">收紧</span>（DR007处历史高位）';
    else monetaryStance = '<span class="text-yellow-400">中性</span>（DR007处正常区间）';
  }

  return `
    <div class="mb-6">
      <div class="flex items-center gap-2 mb-4">
        <h2 class="group-title">⚖️ 第三层：双政策面</h2>
        <span class="text-xs text-gray-600">货币+财政 → 解释经济为何是这样</span>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div class="dept-card">
          <div class="dept-header">🏦 货币政策 ${monetaryStance ? `<span class="text-xs font-normal ml-2">姿态：${monetaryStance}</span>` : ''}</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">${monetaryCards || '<p class="text-xs text-gray-600">数据加载中...</p>'}</div>
        </div>
        <div class="dept-card">
          <div class="dept-header">🏛️ 财政政策</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">${fiscalCards || '<p class="text-xs text-gray-600">数据加载中...</p>'}</div>
        </div>
      </div>
    </div>
  `;
}

// ==================== 第四层：交叉验证 ====================

function renderValidation(validationData) {
  if (!validationData) return '';

  const groups = [
    { key: 'leading', title: '领先指标', icon: '🚀', desc: '提前反映经济拐点 — 验证当前趋势是否将转向' },
    { key: 'coincident', title: '同步指标', icon: '📊', desc: '与经济同步 — 验证当前趋势的真实性' },
    { key: 'lagging', title: '滞后指标', icon: '📉', desc: '确认经济趋势 — 验证拐点是否已确认' },
  ];

  const groupHtml = groups.map(g => {
    const data = validationData[g.key];
    if (!data || typeof data !== 'object') return '';
    const cards = Object.entries(data).map(([key, metric]) => {
      return renderMetricCard(key, metric, `val-${g.key}-${key}`);
    }).join('');
    if (!cards) return '';
    return `
      <div class="dept-card">
        <div class="dept-header">${g.icon} ${g.title} <span class="text-xs font-normal text-gray-600 ml-2">${g.desc}</span></div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">${cards}</div>
      </div>
    `;
  }).join('');

  // 拐点判断
  let crossCheck = '';
  const leading = validationData.leading || {};
  const coincident = validationData.coincident || {};
  
  const leadingChanges = Object.values(leading).map(m => m?.change).filter(v => v !== null && v !== undefined);
  const coincidentChanges = Object.values(coincident).map(m => m?.change).filter(v => v !== null && v !== undefined);
  
  if (leadingChanges.length > 0 && coincidentChanges.length > 0) {
    const leadingAvg = leadingChanges.reduce((a, b) => a + b, 0) / leadingChanges.length;
    const coincidentAvg = coincidentChanges.reduce((a, b) => a + b, 0) / coincidentChanges.length;
    
    if (leadingAvg > 0 && coincidentAvg < 0) {
      crossCheck = '<span class="text-green-400">领先指标回升但同步指标仍在下行 → 可能临近拐点（复苏信号）</span>';
    } else if (leadingAvg < 0 && coincidentAvg > 0) {
      crossCheck = '<span class="text-red-400">领先指标回落但同步指标仍在上行 → 拐点可能临近（走弱信号）</span>';
    } else if (leadingAvg > 0 && coincidentAvg > 0) {
      crossCheck = '<span class="text-green-400">领先+同步同向上行 → 趋势确认，短期无忧</span>';
    } else if (leadingAvg < 0 && coincidentAvg < 0) {
      crossCheck = '<span class="text-red-400">领先+同步同向下行 → 下行趋势确认</span>';
    } else {
      crossCheck = '<span class="text-gray-400">指标方向不一 → 信号模糊，需更多确认</span>';
    }
  }

  return `
    <div class="mb-6">
      <div class="flex items-center gap-2 mb-4">
        <h2 class="group-title">🔬 第四层：交叉验证</h2>
        <span class="text-xs text-gray-600">领先/同步/滞后 → 验真假拐点</span>
      </div>
      ${crossCheck ? `<div class="mb-4 p-3 bg-dash-bg/30 rounded-lg text-sm text-gray-300">拐点判断：${crossCheck}</div>` : ''}
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">${groupHtml}</div>
    </div>
  `;
}

// ==================== 第五层：宏观结论 + 资产映射 ====================

function renderConclusion(countryData) {
  const conclusionData = countryData.conclusion || {};
  const ec = countryData.economic_cycle;
  if (!conclusionData && !ec) {
    return `
      <div class="bg-gradient-to-r from-dash-card to-blue-900/20 rounded-xl p-6 border border-dash-border mb-6">
        <h2 class="text-lg font-semibold text-white mb-3">💡 第五层：宏观结论 + 资产映射</h2>
        <p class="text-gray-500 text-sm">结论生成中...</p>
      </div>
    `;
  }

  const { 
    positioning = '', 
    main_contradiction = '', 
    scenarios = [] 
  } = conclusionData;

  const ASSET_CONFIG = [
    { key: 'cn_stock', label: 'A股', icon: '🇨🇳📈', dim_on: 'us' },
    { key: 'cn_bond', label: '中债', icon: '🇨🇳🏦', dim_on: 'us' },
    { key: 'cn_realestate', label: '中房', icon: '🇨🇳🏠', dim_on: 'us' },
    { key: 'us_stock', label: '美股', icon: '🇺🇸📈', dim_on: 'china' },
    { key: 'us_bond', label: '美债', icon: '🇺🇸🏦', dim_on: 'china' },
    { key: 'usd', label: '美元', icon: '💵', dim_on: 'china' },
    { key: 'gold', label: '黄金', icon: '🥇', dim_on: 'china' },
    { key: 'commodity', label: '商品', icon: '🛢️', dim_on: 'china' },
  ];

  const country = countryData === window.macroData?.china ? 'china' : 'us';
  const valuation = window.macroData?.asset_valuation || {};

  // 1) 经济周期基准（来自第一层 economic_cycle.asset_base）
  const baseRanking = ec ? parseAssetRanking(ec.asset_base, country) : {};
  function baseScore(key) {
    const r = baseRanking[key];
    if (r === undefined) return 0;
    // 连续权重（扣子建议）：排序第1→+1.5，第2→+0.5，第3→-0.5，第4→-1.5
    // ranking 为 1-based 组号，线性映射 w = 2.5 - r
    const w = 2.5 - r;
    return Math.max(-1.5, Math.min(1.5, w));
  }

  // 2) 估值修正（基于资产当前分位数）
  // 做多型资产（股票/商品/房产）：分位低=便宜=+1，高=贵=-1
  // 债券（收益率型）：分位已表示收益率分位，高=便宜=+1，低=贵=-1
  // 黄金/美元：分位高=贵=-1，低=便宜=+1
  const LONG_TYPE = ['cn_stock', 'us_stock', 'commodity', 'cn_realestate'];
  const BOND_TYPE = ['cn_bond', 'us_bond'];
  const SAFE_TYPE = ['gold', 'usd'];

  function valScore(key) {
    const v = valuation[key];
    if (!v) return 0;
    const p = v.pct_5y;
    if (p === null || p === undefined) return 0;
    // 阈值放宽：35 分位以下算便宜，65 以上算偏贵（30/70 边界太严）
    if (LONG_TYPE.includes(key)) {
      if (p < 35) return 1; if (p > 65) return -1; return 0;
    } else if (BOND_TYPE.includes(key)) {
      if (p > 65) return 1; if (p < 35) return -1; return 0;
    } else { // gold / usd
      if (p > 65) return -1; if (p < 35) return 1; return 0;
    }
  }

  // 3) 综合信号灯 = 基准 + 估值（clamp -2..2）
  function compositeSignal(key) {
    const total = baseScore(key) + valScore(key);
    if (total >= 1) return { text: '偏多', color: '#10b981', score: total };
    if (total <= -1) return { text: '偏空', color: '#ef4444', score: total };
    return { text: '中性', color: '#f59e0b', score: total };
  }

  const signalHtml = ASSET_CONFIG.map(a => {
    const base = baseScore(a.key);
    const val = valScore(a.key);
    const sig = compositeSignal(a.key);
    const valInfo = valuation[a.key];
    const dimmed = a.dim_on === country;
    const opacity = dimmed ? 'opacity-40' : '';
    const valPct = valInfo ? valInfo.pct_5y : null;

    // 基准方向小图标
    const baseIcon = base > 0 ? '▲' : base < 0 ? '▼' : '—';
    const baseColor = base > 0 ? '#10b981' : base < 0 ? '#ef4444' : '#6b7280';
    const valIcon = val > 0 ? '↑便宜' : val < 0 ? '↓偏贵' : '→中性';
    const valColor = val > 0 ? '#10b981' : val < 0 ? '#ef4444' : '#6b7280';

    return `
      <div class="flex flex-col items-center gap-1 px-2 py-3 bg-dash-bg/50 rounded-lg border border-dash-border min-w-[120px] ${opacity}">
        <span class="text-lg">${a.icon}</span>
        <span class="text-[11px] text-gray-400">${a.label}</span>
        <span class="text-[9px] font-mono" style="color:${baseColor}">周期${baseIcon}</span>
        <span class="text-[9px] font-mono" style="color:${valColor}">估值${valIcon}${valPct !== null ? ' P'+valPct : ''}</span>
        <span class="signal-light text-[11px] mt-1" style="background:${sig.color}20; color:${sig.color}; border:1px solid ${sig.color}50;">
          <span class="dot" style="background:${sig.color}"></span>${sig.text}
        </span>
      </div>
    `;
  }).join('');

  // 情景演化路径（来自 economic_cycle.evolution_paths）
  const paths = ec?.evolution_paths || [];
  const pathHtml = paths.map((p, i) => {
    const probColor = p.prob > 50 ? '#10b981' : p.prob > 30 ? '#f59e0b' : '#ef4444';
    const conds = (p.conditions || []).map(c => `
      <div class="flex items-center gap-1.5 text-[11px] ${c.met ? 'text-green-400' : 'text-gray-500'}">
        <span>${c.met ? '✓' : '○'}</span><span>${c.desc}</span>
      </div>`).join('');
    const metCount = (p.conditions || []).filter(c => c.met).length;
    const totalCount = (p.conditions || []).length;
    return `
      <div class="scenario-card">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-xs font-mono text-gray-500">路径${i+1}</span>
          <span class="text-sm font-medium text-gray-300">${p.from} → ${p.to}</span>
          <span class="scenario-prob" style="color:${probColor}">${p.prob}%</span>
        </div>
        ${conds ? `<div class="mb-1.5 space-y-0.5">${conds}</div>` : (p.trigger ? `<p class="text-xs text-gray-500 mb-1.5">触发：${p.trigger}</p>` : '')}
        ${totalCount ? `<div class="text-[10px] text-gray-600 mb-0.5">条件满足 ${metCount}/${totalCount}</div>` : ''}
        ${p.fail ? `<div class="text-[10px] text-red-400/70 mb-0.5">失效：${p.fail}</div>` : ''}
        ${p.confidence ? `<div class="text-[10px] text-gray-600">置信度：${p.confidence}</div>` : ''}
      </div>`;
  }).join('');

  const scenarioHtml = (scenarios || []).map((s, i) => {
    const probColor = s.probability > 50 ? '#10b981' : s.probability > 30 ? '#f59e0b' : '#ef4444';
    return `
      <div class="scenario-card">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-xs font-mono text-gray-500">情景${i+1}</span>
          <span class="text-sm font-medium text-gray-300">${s.name || ''}</span>
          <span class="scenario-prob" style="color:${probColor}">${s.probability || '?'}%</span>
        </div>
        <p class="text-xs text-gray-500 leading-relaxed">${s.description || ''}</p>
        ${s.trigger ? `<p class="text-xs text-gray-600 mt-1">触发条件：${s.trigger}</p>` : ''}
      </div>
    `;
  }).join('');

  return `
    <div class="bg-gradient-to-r from-dash-card to-blue-900/20 rounded-xl p-6 border border-dash-border mb-6">
      <h2 class="text-lg font-semibold text-white mb-4">💡 第五层：宏观结论 + 资产映射</h2>
      
      ${ec ? `
        <div class="mb-4">
          <div class="text-xs text-gray-500 uppercase tracking-wide mb-1">宏观定性（来自第一层经济周期定位）</div>
          <div class="text-sm text-gray-200 leading-relaxed p-3 bg-dash-bg/30 rounded-lg">
            <span class="text-base font-semibold" style="color:${ec.icon === '🔴' ? '#ef4444' : ec.icon === '🟢' || ec.icon === '🟢🟢' ? '#10b981' : '#f59e0b'}">${ec.icon} ${ec.phase}</span>
            <span class="text-gray-500 text-xs ml-2">（${ec.method}）</span><br>
            ${ec.asset_desc}
          </div>
        </div>
      ` : ''}
      
      ${positioning ? `
        <div class="mb-4">
          <div class="text-xs text-gray-500 uppercase tracking-wide mb-1">补充说明</div>
          <div class="text-sm text-gray-300 leading-relaxed p-3 bg-dash-bg/20 rounded-lg text-xs">${positioning}</div>
        </div>
      ` : ''}
      
      ${main_contradiction ? `
        <div class="mb-4">
          <div class="text-xs text-gray-500 uppercase tracking-wide mb-1">主要矛盾</div>
          <div class="text-sm text-gray-200 leading-relaxed p-3 bg-dash-bg/30 rounded-lg">${main_contradiction}</div>
        </div>
      ` : ''}
      
      ${scenarioHtml ? `
        <div class="mb-4">
          <div class="text-xs text-gray-500 uppercase tracking-wide mb-2">未来情景</div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">${scenarioHtml}</div>
        </div>
      ` : ''}
      
      <div class="mb-3">
        <div class="text-xs text-gray-500 uppercase tracking-wide mb-2">大类资产映射（经济周期基准 × 估值修正）</div>
        <div class="flex flex-wrap gap-2">${signalHtml}</div>
        <div class="text-[10px] text-gray-600 mt-2 leading-relaxed">
          📌 每个资产显示：<span style="color:#6b7280">周期▲/▼</span> = 经济周期定位的基准方向；
          <span style="color:#6b7280">估值↑便宜/↓偏贵</span> = 当前分位数修正；
          综合信号灯 = 基准 + 估值。灰色=非本国视角资产（供参考）。
        </div>
      </div>
      
      ${pathHtml ? `
        <div>
          <div class="text-xs text-gray-500 uppercase tracking-wide mb-2">情景演化路径（拐点监测）</div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">${pathHtml}</div>
        </div>
      ` : ''}
    </div>
  `;
}

// ==================== 中美联动（嵌在底部） ====================

function renderCrossSection(crossData) {
  if (!crossData) return '';

  const spread = crossData.cn_us_10y_spread;
  const usdcny = crossData.usdcny;

  // 利差状态判断
  let spreadStatus = '';
  if (spread && spread.value !== null) {
    const v = spread.value;
    if (v < 0) spreadStatus = '中美利差倒挂，人民币承压，中国宽松空间受限';
    else if (v < 50) spreadStatus = '中美利差处于低位，关注资本流动变化';
    else spreadStatus = '中美利差正常，汇率压力可控';
  }

  const chains = [
    { from: '美联储政策', to: '中美利差', desc: '美联储利率决定美元利率，形成中美利差', lag: '即时' },
    { from: '中美利差', to: '人民币汇率', desc: '利差收窄/倒挂→资本流出→贬值压力', lag: '数周~月' },
    { from: '人民币汇率', to: '中国政策空间', desc: '贬值压力约束中国央行宽松空间', lag: '持续' },
    { from: '美国需求', to: '中国出口', desc: '美国消费拉动中国出口和制造业', lag: '数月' },
    { from: '中国出口', to: '美国通胀', desc: '中国出口价格传导至美国进口通胀', lag: '数月~季' },
    { from: '风险情绪', to: '资本流动', desc: '避险情绪驱动资本回流美国', lag: '即时' },
  ];

  const chainsHtml = chains.map((c, i) => `
    <div class="chain-item">
      <div class="text-sm font-mono text-gray-500">${String(i+1).padStart(2,'0')}</div>
      <div class="flex-1">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-sm font-medium text-gray-300">${c.from}</span>
          <span class="chain-arrow">→</span>
          <span class="text-sm font-medium text-gray-300">${c.to}</span>
          <span class="text-xs text-gray-600 ml-2">⏱ ${c.lag}</span>
        </div>
        <div class="text-xs text-gray-500">${c.desc}</div>
      </div>
    </div>
  `).join('');

  return `
    <div class="cross-highlight mb-6">
      <div class="flex items-center gap-2 mb-4">
        <h2 class="group-title">🌐 中美联动</h2>
        <span class="text-xs text-gray-600">利差/汇率/资本流 — 双国看板的核心增量</span>
      </div>
      
      ${spreadStatus ? `<div class="mb-4 p-3 bg-dash-bg/30 rounded-lg text-sm text-gray-300">${spreadStatus}</div>` : ''}
      
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <div class="bg-dash-card rounded-xl p-5 border border-dash-border">
          <h3 class="text-sm font-semibold text-gray-300 mb-3">中美10Y国债利差</h3>
          <div id="chart-spread" class="w-full" style="height:300px"></div>
        </div>
        <div class="bg-dash-card rounded-xl p-5 border border-dash-border">
          <h3 class="text-sm font-semibold text-gray-300 mb-3">美元/人民币汇率</h3>
          <div id="chart-usdcny" class="w-full" style="height:300px"></div>
        </div>
      </div>
      
      <div class="bg-dash-card rounded-xl p-5 border border-dash-border">
        <h3 class="text-sm font-semibold text-gray-300 mb-3">跨境传导链路</h3>
        <div class="space-y-2">${chainsHtml}</div>
      </div>
    </div>
  `;
}

// ==================== 通用指标卡片 ====================

function renderMetricCard(key, metric, prefix) {
  if (!metric || typeof metric !== 'object') return '';
  const v = safeVal(metric.value);
  const change = safeVal(metric.change);
  const pct5 = safeVal(metric.pct_5y);
  const pct10 = safeVal(metric.pct_10y);
  const unit = metric.unit || '';
  const date = metric.date || '—';
  const name = metric.name || key;
  const chartId = `chart-${prefix}`;

  return `
    <div class="metric-card">
      <div class="flex items-start justify-between mb-2">
        <div>
          <div class="text-sm font-medium text-gray-300">${name}</div>
          <div class="text-xs text-gray-600 font-mono">${date}</div>
        </div>
        <div class="flex gap-1">
          <span class="pct-tag ${pctClass(pct5)}" title="5年分位数">5Y ${pctLabel(pct5)}</span>
          <span class="pct-tag ${pctClass(pct10)}" title="10年分位数">10Y ${pctLabel(pct10)}</span>
        </div>
      </div>
      <div class="flex items-baseline gap-2 mb-2">
        <span class="text-2xl font-bold text-white font-mono">${fmtNum(v)}</span>
        ${unit ? `<span class="text-xs text-gray-500">${unit}</span>` : ''}
        ${change !== null ? `<span class="text-xs font-mono ${changeClass(change)}">${changeArrow(change)} ${fmtNum(Math.abs(change))}</span>` : ''}
      </div>
      <div id="${chartId}" class="w-full" style="height:50px"></div>
    </div>
  `;
}

// ==================== 主渲染入口 ====================

function renderCountry(countryData, country) {
  const container = document.getElementById(`${country}-groups`);
  if (!container) return;

  let html = '';
  
  // 第一层：经济周期定位（新：货币-信用 / 美林+流动性）
  html += renderEconomicCycle(countryData, country);
  
  // 第二层：四大部门
  if (countryData.sectors) {
    html += renderSectors(countryData.sectors);
  }
  
  // 第三层：双政策
  if (countryData.policy) {
    html += renderPolicy(countryData.policy);
  }
  
  // 第四层：交叉验证
  if (countryData.validation) {
    html += renderValidation(countryData.validation);
  }
  
  // 第五层：结论
  html += renderConclusion(countryData);
  
  // 中美联动（仅中国 Tab 底部）
  if (country === 'china' && window.macroData?.cross) {
    html += renderCrossSection(window.macroData.cross);
  }

  container.innerHTML = html;

  // 渲染所有迷你图
  // 第一层迷你图（中国：货币+信用；美国：象限+流动性）
  if (country === 'china' && countryData.economic_cycle) {
    const ec = countryData.economic_cycle;
    ['dr007', 'm2'].forEach(k => {
      const m = ec.monetary_indicators?.[k];
      if (m) renderMiniChart(`mini-cn-m-${k === 'dr007' ? 'd' : 'm2'}`, m.history, m.change);
    });
    ['m1'].forEach(k => {
      const m = ec.credit_indicators?.[k];
      if (m) renderMiniChart(`mini-cn-c-m1`, m.history, m.change);
    });
    const sf = ec.credit_indicators?.shrzgm;
    if (sf) renderMiniChart('mini-cn-c-sf', sf.history, sf.change);
  } else if (country === 'us' && countryData.economic_cycle) {
    const ec = countryData.economic_cycle;
    ['gdp', 'cpi', 'ppi'].forEach((k, i) => {
      const m = ec.quadrant_indicators?.[k];
      if (m) renderMiniChart(`mini-us-q-${k[0]}`, m.history, m.change);
    });
    [['fed_funds', 'ff'], ['ust_10y', 'u10'], ['tips_10y', 'tips'], ['hy_spread', 'hy']].forEach(([k, suf]) => {
      const m = ec.liquidity_indicators?.[k];
      if (m) renderMiniChart(`mini-us-l-${suf}`, m.history, m.change);
    });
  } else if (countryData.quadrant) {
    // 兼容旧数据
    ['gdp', 'cpi', 'ppi'].forEach((k, i) => {
      if (countryData.quadrant[k]) {
        renderMiniChart(`mini-q-${i}`, countryData.quadrant[k].history, countryData.quadrant[k].change);
      }
    });
  }
  
  // 部门层
  if (countryData.sectors) {
    Object.entries(countryData.sectors).forEach(([deptKey, deptData]) => {
      if (typeof deptData === 'object') {
        Object.entries(deptData).forEach(([key, metric]) => {
          renderMiniChart(`chart-sect-${deptKey}-${key}`, metric?.history, metric?.change);
        });
      }
    });
  }
  
  // 政策层
  if (countryData.policy) {
    ['monetary', 'fiscal'].forEach(pk => {
      if (countryData.policy[pk]) {
        Object.entries(countryData.policy[pk]).forEach(([key, metric]) => {
          renderMiniChart(`chart-pol-${pk}-${key}`, metric?.history, metric?.change);
        });
      }
    });
  }
  
  // 验证层
  if (countryData.validation) {
    ['leading', 'coincident', 'lagging'].forEach(vk => {
      if (countryData.validation[vk]) {
        Object.entries(countryData.validation[vk]).forEach(([key, metric]) => {
          renderMiniChart(`chart-val-${vk}-${key}`, metric?.history, metric?.change);
        });
      }
    });
  }
  
  // 联动图表（仅中国）
  if (country === 'china' && window.macroData?.cross) {
    const cross = window.macroData.cross;
    if (cross.cn_us_10y_spread?.history) {
      renderLargeChart('chart-spread', {
        dates: cross.cn_us_10y_spread.history.map(h => h.date),
        values: cross.cn_us_10y_spread.history.map(h => h.value),
        name: '中美10Y利差'
      }, { color: '#f59e0b', area: true });
    }
    if (cross.usdcny?.history) {
      renderLargeChart('chart-usdcny', {
        dates: cross.usdcny.history.map(h => h.date),
        values: cross.usdcny.history.map(h => h.value),
        name: 'USDCNY'
      }, { color: '#10b981', area: true });
    }
  }
}

// resize
window.addEventListener('resize', function() {
  chartInstances.forEach(c => { if (c && !c.isDisposed()) c.resize(); });
});
