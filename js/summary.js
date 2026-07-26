/**
 * summary.js - L5 综合结论渲染
 * 各层结论卡片、配置建议、关键风险
 */

const SummaryModule = (() => {

  /** 信号标签样式 */
  function signalBadge(signal) {
    const label = CONFIG.signalLabel[signal] || signal || '待定';
    const color = CONFIG.signalColor[signal] || '#8b5cf6';
    return `<span class="inline-block px-2 py-1 rounded text-xs font-bold" style="background:${color}22;color:${color}">${label}</span>`;
  }

  /** 渲染各层结论卡片 */
  function renderLayerCards(layers) {
    const container = document.getElementById('layer-cards');
    if (!container || !layers) return;

    const layerMeta = {
      L1_cycle: { icon: '🔄', name: '经济周期' },
      L2_macro: { icon: '📊', name: '宏观指标' },
      L3_valuation: { icon: '💰', name: '资产估值' },
      L4_technical: { icon: '📈', name: '技术走势' },
    };

    container.innerHTML = Object.entries(layerMeta).map(([key, meta]) => {
      const data = layers[key];
      if (!data) {
        return `
          <div class="card-sub">
            <div class="flex items-center gap-2 mb-2">
              <span class="text-lg">${meta.icon}</span>
              <span class="text-sm font-bold text-white">${meta.name}</span>
            </div>
            <p class="placeholder-msg">数据待更新</p>
          </div>
        `;
      }

      return `
        <div class="card-sub">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2">
              <span class="text-lg">${meta.icon}</span>
              <span class="text-sm font-bold text-white">${meta.name}</span>
            </div>
            ${signalBadge(data.signal)}
          </div>
          <p class="text-sm text-gray-300 leading-relaxed">${data.conclusion}</p>
        </div>
      `;
    }).join('');
  }

  /** 渲染配置建议 */
  function renderAllocation(allocation) {
    const container = document.getElementById('allocation-cards');
    if (!container || !allocation) return;

    const sections = [
      { key: 'overweight', label: '超配', cls: 'overweight', color: '#22c55e', icon: '🟢' },
      { key: 'market_weight', label: '标配', cls: 'market-weight', color: '#9ca3af', icon: '🟡' },
      { key: 'underweight', label: '低配', cls: 'underweight', color: '#ef4444', icon: '🔴' },
    ];

    container.innerHTML = sections.map(s => {
      const items = allocation[s.key] || [];
      return `
        <div class="card-sub" style="border-color:${s.color}40">
          <h4 style="color:${s.color}">${s.icon} ${s.label}</h4>
          <div class="flex flex-wrap gap-2 mt-2">
            ${items.length ? items.map(item =>
              `<span class="allocation-tag ${s.cls}">${item}</span>`
            ).join('') : '<span class="text-gray-500 text-sm">-</span>'}
          </div>
        </div>
      `;
    }).join('');
  }

  /** 渲染关键风险 */
  function renderRisks(risks) {
    const container = document.getElementById('risk-list');
    if (!container) return;

    if (!risks || risks.length === 0) {
      container.innerHTML = '<li class="text-gray-500 text-sm">暂无关键风险信息</li>';
      return;
    }

    container.innerHTML = risks.map(risk => `
      <li class="flex items-start gap-3 p-3 rounded-lg bg-gray-800/40 border border-gray-700/50">
        <span class="text-bear text-lg flex-shrink-0">⚠</span>
        <span class="text-sm text-gray-300">${risk}</span>
      </li>
    `).join('');
  }

  /** 主入口 */
  function render(data) {
    if (!data) return;
    renderLayerCards(data.layers);
    renderAllocation(data.allocation);
    renderRisks(data.key_risks);
  }

  return { render };
})();
