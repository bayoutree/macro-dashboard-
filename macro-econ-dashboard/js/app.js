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
    const response = await fetch('data/indicators.json?v=20260924e');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    macroData = await response.json();
    window.macroData = macroData;

    if (macroData.update_time) {
      updateTimeEl.textContent = `📅 更新于 ${macroData.update_time}`;
    }
    statusEl.innerHTML = '<span class="text-green-400">✓ 数据已加载</span>';

    if (macroData.china) renderCountry(macroData.china, 'china');
    if (macroData.us) renderCountry(macroData.us, 'us');

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
