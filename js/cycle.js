/**
 * cycle.js - L1 经济周期定位渲染
 * 处理康波、朱格拉、基钦周期和共振总结
 */

const CycleModule = (() => {
  // 阶段颜色映射
  const phaseColors = {
    expansion: '#22c55e',
    early_expansion: '#4ade80',
    peak: '#f59e0b',
    contraction: '#ef4444',
    late_contraction: '#f97316',
    trough: '#dc2626',
    mid_restocking: '#22d3ee',
    early_restocking_divergent: '#06b6d4',
    late_destocking: '#a78bfa',
    default: '#6b7280',
  };

  function getPhaseColor(code) {
    return phaseColors[code] || phaseColors.default;
  }

  /** 渲染康波周期 */
  function renderKongbo(data) {
    if (!data) return;

    // 当前阶段
    const phaseEl = document.getElementById('kongbo-phase');
    if (phaseEl) {
      phaseEl.textContent = data.current_phase || '数据待更新';
      phaseEl.style.color = '#22c55e'; // 康波回升→绿色
    }

    // 置信度
    const confEl = document.getElementById('kongbo-confidence');
    if (confEl && data.confidence) {
      const confMap = { high: '高', medium: '中', low: '低' };
      confEl.textContent = `置信度：${confMap[data.confidence] || data.confidence}`;
    }

    // 证据列表
    const evEl = document.getElementById('kongbo-evidence');
    if (evEl && data.key_evidence) {
      evEl.innerHTML = data.key_evidence.map(e => `<li>${e}</li>`).join('');
    }

    // 历史时间线
    const tlEl = document.getElementById('kongbo-timeline');
    if (tlEl && data.history) {
      tlEl.innerHTML = data.history.map(h => {
        const isCurrent = !h.end;
        const cls = isCurrent ? 'border-bull text-bull' : 'border-gray-700 text-gray-400';
        const endStr = h.end || '至今';
        return `<span class="timeline-tag ${cls}">
          第${h.round}轮 ${h.name || h.phase}（${h.start}-${endStr}）
        </span>`;
      }).join('');
    }
  }

  /** 渲染朱格拉周期卡片 */
  function renderJuglar(data) {
    if (!data) return;
    const regions = { us: '🇺🇸 美国', cn: '🇨🇳 中国', eu: '🇪🇺 欧洲' };

    Object.entries(regions).forEach(([key, label]) => {
      const el = document.getElementById(`juglar-${key}`);
      const d = data[key];
      if (!el) return;

      if (!d) {
        el.innerHTML = `<h4>${label}</h4><p class="detail-text">数据待更新</p>`;
        return;
      }

      const color = getPhaseColor(d.phase_code);
      let html = `
        <h4>${label}</h4>
        <p class="phase-label" style="color:${color}">${d.phase || '未知'}</p>
        <p class="detail-text">
          ${d.start ? `起始：${d.start}` : ''}
          ${d.expected_end ? ` · 预计结束：${d.expected_end}` : ''}
          ${d.expected_bottom ? ` · 预计触底：${d.expected_bottom}` : ''}
        </p>
        ${d.key_driver ? `<p class="detail-text mt-1">驱动力：${d.key_driver}</p>` : ''}
        ${d.key_issue ? `<p class="detail-text mt-1">关键问题：${d.key_issue}</p>` : ''}
      `;
      if (d.evidence && d.evidence.length) {
        html += `<ul class="evidence-list mt-2">${d.evidence.map(e => `<li>${e}</li>`).join('')}</ul>`;
      }
      if (d.risk) {
        html += `<p class="detail-text mt-2 text-bear">⚠ ${d.risk}</p>`;
      }
      el.innerHTML = html;
    });
  }

  /** 渲染库存周期卡片 */
  function renderKitchin(data) {
    if (!data) return;
    const regions = { us: '🇺🇸 美国', cn: '🇨🇳 中国', eu: '🇪🇺 欧洲' };

    Object.entries(regions).forEach(([key, label]) => {
      const el = document.getElementById(`kitchin-${key}`);
      const d = data[key];
      if (!el) return;

      if (!d) {
        el.innerHTML = `<h4>${label}</h4><p class="detail-text">数据待更新</p>`;
        return;
      }

      const color = getPhaseColor(d.phase_code);
      let html = `
        <h4>${label}</h4>
        <p class="phase-label" style="color:${color}">${d.phase || '未知'}</p>
        ${d.detail ? `<p class="detail-text">${d.detail}</p>` : ''}
      `;
      if (d.evidence && d.evidence.length) {
        html += `<ul class="evidence-list mt-2">${d.evidence.map(e => `<li>${e}</li>`).join('')}</ul>`;
      }
      if (d.risk) {
        html += `<p class="detail-text mt-2 text-bear">⚠ ${d.risk}</p>`;
      }
      el.innerHTML = html;
    });
  }

  /** 渲染三周期共振 */
  function renderResonance(data) {
    if (!data) return;
    const container = document.getElementById('resonance-content');
    if (!container) return;

    const matrix = data.matrix;
    if (!matrix) return;

    const regions = { us: '🇺🇸 美国', cn: '🇨🇳 中国', eu: '🇪🇺 欧洲' };
    let html = '';

    Object.entries(regions).forEach(([key, label]) => {
      const d = matrix[key];
      if (!d) return;
      html += `
        <div class="card-sub">
          <h4>${label}</h4>
          <div class="grid grid-cols-3 gap-1 text-xs my-2">
            <div><span class="text-gray-500">康波：</span>${d.kongbo || '-'}</div>
            <div><span class="text-gray-500">朱格拉：</span>${d.juglar || '-'}</div>
            <div><span class="text-gray-500">基钦：</span>${d.kitchin || '-'}</div>
          </div>
          <p class="detail-text text-bull">${d.combined || ''}</p>
        </div>
      `;
    });

    // 整体判断
    html += `
      <div class="col-span-full mt-4 p-4 rounded-lg bg-gray-800/60 border border-gray-700">
        <p class="text-sm text-white font-semibold mb-2">整体格局：${data.overall_regime || '-'}</p>
        <p class="text-sm text-bull mb-3">🔑 ${data.key_window || ''}</p>
        ${data.bull_signals ? `
          <p class="text-xs text-gray-400 mb-1">看多信号：</p>
          <ul class="evidence-list text-xs">${data.bull_signals.map(s => `<li>${s}</li>`).join('')}</ul>
        ` : ''}
        ${data.bear_signals ? `
          <p class="text-xs text-gray-400 mb-1 mt-2">看空信号：</p>
          <ul class="evidence-list text-xs">${data.bear_signals.map(s => `<li>${s}</li>`).join('')}</ul>
        ` : ''}
      </div>
    `;

    container.innerHTML = html;
  }

  /** 主渲染入口 */
  function render(data) {
    if (!data) {
      console.warn('[L1] 无周期数据');
      return;
    }
    renderKongbo(data.kongbo);
    renderJuglar(data.juglar);
    renderKitchin(data.kitchin);
    renderResonance(data.resonance);
  }

  return { render };
})();
