/**
 * app.js — 主逻辑（五层结构版）
 * Tab 切换、数据加载、页面初始化
 */

let macroData = null;
let currentTab = new URLSearchParams(location.search).get('tab') || 'china';

// ==================== Tab 切换 ====================

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.remove('active');
  });
  const target = document.getElementById(`tab-${tab}`);
  if (target) target.classList.add('active');

  // 骨架图 Tab
  if (tab === 'skeleton') {
    setTimeout(() => {
      if (!skeletonNetwork) {
        initSkeletonNetwork('china');
      } else {
        skeletonNetwork.redraw();
      }
    }, 50);
  }

  // resize 图表
  setTimeout(() => {
    chartInstances.forEach(c => { if (c && !c.isDisposed()) c.resize(); });
  }, 50);
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

document.querySelectorAll('.skel-view-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.skel-view-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    initSkeletonNetwork(btn.dataset.view);
  });
});

// ==================== 数据加载 ====================

async function loadData() {
  const statusEl = document.getElementById('data-status');
  const updateTimeEl = document.getElementById('update-time');
  statusEl.innerHTML = '<span class="loading-spinner inline-block"></span> 数据加载中...';

  try {
    const response = await fetch('data/indicators.json?v=20260924f');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    macroData = await response.json();
    window.macroData = macroData;

    if (macroData.update_time) {
      updateTimeEl.textContent = `📅 更新于 ${macroData.update_time}`;
    }
    statusEl.innerHTML = '<span class="text-green-400">✓ 数据已加载</span>';

    // [V22-2 2026-11-07] 断更判定：update_time 超 2 个交易日未刷新 → 红条+状态灯转黄（CIO P1验收横切H1）
    try {
      const _ut = macroData.update_time;
      const _banner = document.getElementById('staleness-banner');
      if (_ut && _banner) {
        const _lag = Math.max(0, Math.round((Date.now() - new Date(_ut + 'T16:00:00+08:00').getTime()) / 86400000 * 5 / 7));
        if (_lag > 2) {
          _banner.textContent = `⚠️ 数据断更：indicators.json 停在 ${_ut}（约 ${_lag} 个交易日未更新），页面读数非最新，链路修复中。`;
          _banner.classList.remove('hidden');
          statusEl.innerHTML = `<span class="text-yellow-400">⚠ 数据滞后 ${_lag} 个交易日</span>`;
        } else {
          _banner.classList.add('hidden');
        }
      }
    } catch (e) { console.warn('V22-2 断更检查失败(不阻断):', e); }

    // P0-2（2026-11-01 CIO）：资产价格序列超 10 个交易日未更新 → 信号灯强制置灰。
    // 读取仓库根 data/asset_prices_monthly.json 的 series_meta（由
    // scripts/build_asset_prices_monthly.py 日频聚合生成），交易日按 自然日×5/7
    // 近似（与 freshness_gate.py 同口径），前端本地复算为准、文件标记兜底。
    try {
      const mr = await fetch('../data/asset_prices_monthly.json?v=' + Date.now());
      if (mr.ok) {
        const md = await mr.json();
        const meta = (md.prices && md.prices.series_meta) || {};
        const bySeries = {};
        Object.keys(meta).forEach(function(k) {
          const m = meta[k] || {};
          const eff = m.effective_last_date || m.last_src_date;
          if (!eff) return;
          const td = Math.max(0, Math.round((Date.now() - new Date(eff).getTime()) / 86400000 * 5 / 7));
          bySeries[k] = { stale: td > 10, tradingDays: td, lastDate: eff,
                          seamMismatch: !!m.seam_mismatch };
        });
        const assetMap = { cn_stock: 'hs300', cn_bond: 'cn_10y_yield', us_stock: 'sp500',
                           us_bond: 'us_10y_yield', usd: 'usd_index', gold: 'gold', commodity: 'copper' };
        window.__assetSeriesStale = {};
        Object.keys(assetMap).forEach(function(ak) {
          if (bySeries[assetMap[ak]]) window.__assetSeriesStale[ak] = bySeries[assetMap[ak]];
        });
      }
    } catch (e) { console.warn('P0-2 序列断更检查加载失败(不阻断):', e); }

    if (macroData.china) renderCountry(macroData.china, 'china');
    if (macroData.us) renderCountry(macroData.us, 'us');

    // P1-11（2026-11-02）：中美利差上主屏 —— 常驻条渲染到所有 Tab 顶部容器
    const spreadStrip = document.getElementById('spread-mainscreen');
    if (spreadStrip && macroData.cross && macroData.cross.cn_us_10y_spread) {
      spreadStrip.innerHTML = renderSpreadMainscreen(macroData.cross);
      const _sp = macroData.cross.cn_us_10y_spread;
      renderMiniChart('mini-spread-main', _sp.history, _sp.change, spreadStrip);
    }

    if (!macroData.china && !macroData.us) showNoData();

  } catch (err) {
    console.warn('数据加载失败:', err);
    statusEl.innerHTML = '<span class="text-yellow-400">⚠ 数据文件未找到</span>';
    updateTimeEl.textContent = '等待数据生成';
    showLoadingPlaceholder();
  }
}

function showNoData() {
  ['china', 'us'].forEach(c => {
    const el = document.getElementById(`${c}-groups`);
    if (el) el.innerHTML = `<div class="bg-dash-card rounded-xl p-8 border border-dash-border text-center"><p class="text-gray-500">暂无数据</p></div>`;
  });
}

function showLoadingPlaceholder() {
  const placeholder = `
    <div class="bg-dash-card rounded-xl p-8 border border-dash-border text-center">
      <div class="loading-spinner mx-auto mb-3"></div>
      <p class="text-gray-400 text-sm mb-1">数据加载中...</p>
      <p class="text-gray-600 text-xs">后端数据生成脚本运行后，数据将自动显示在此处</p>
      <p class="text-gray-700 text-xs mt-2 font-mono">预期路径: data/indicators.json</p>
    </div>
  `;
  ['china', 'us'].forEach(c => {
    const el = document.getElementById(`${c}-groups`);
    if (el) el.innerHTML = placeholder;
  });
}

// ==================== 刷新 ====================

document.getElementById('refresh-btn').addEventListener('click', function() {
  const svg = this.querySelector('svg');
  if (svg) {
    svg.style.transition = 'transform 0.6s';
    svg.style.transform = 'rotate(360deg)';
    setTimeout(() => { svg.style.transition = ''; svg.style.transform = ''; }, 600);
  }
  loadData();
});

// ==================== 启动 ====================

document.addEventListener('DOMContentLoaded', loadData);
  setTimeout(() => { const tab = new URLSearchParams(location.search).get('tab'); if (tab) switchTab(tab); }, 500);

// ==================== iframe auto-resize ====================
function notifyParentResize() {
  if (window.parent !== window) {
    var h = document.documentElement.scrollHeight || document.body.scrollHeight;
    window.parent.postMessage({ type: 'macro-resize', height: h }, '*');
  }
}

// Notify on load
window.addEventListener('load', function() { setTimeout(notifyParentResize, 500); });
window.addEventListener('resize', notifyParentResize);

// Observe content changes
if (typeof MutationObserver !== 'undefined') {
  var _mo = new MutationObserver(function() { notifyParentResize(); });
  _mo.observe(document.body, { childList: true, subtree: true, attributes: true });
}

// Notify after tab switch (hook into existing switchTab)
if (typeof switchTab === 'function') {
  var _origSwitch = switchTab;
  switchTab = function(tab) {
    _origSwitch(tab);
    setTimeout(notifyParentResize, 300);
  };
}
