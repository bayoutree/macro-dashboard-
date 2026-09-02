/**
 * app_v2_patch.js - V3 周期看板适配补丁
 * 
 * 覆盖原有 app.js 中的周期Tab渲染逻辑，
 * 改用 CycleV3Module 进行渲染。
 * 
 * 使用方式: 在 index.html 中，在 app.js + cycle_v3.js 之后引入本文件。
 */

(function() {
  'use strict';

  // 1. 在 app.data 中添加 cyclePositionV3 字段
  if (typeof app !== 'undefined' && app.data) {
    app.data.cyclePositionV3 = null;
  }

  // 2. 保存原始方法
  const _originalLoadAllData = app.loadAllData.bind(app);

  // 3. 覆盖 loadAllData - 额外加载 cycle_position_v3.json
  app.loadAllData = async function() {
    await _originalLoadAllData();

    try {
      const resp = await fetch(`${CONFIG.dataDir}/cycle_position_v3.json?_v=${Date.now()}`, { cache: 'no-store' });
      if (resp.ok) {
        this.data.cyclePositionV3 = await resp.json();
        console.log('[V3] cycle_position_v3.json loaded');
      } else {
        console.warn('[V3] Failed to load cycle_position_v3.json:', resp.status);
      }
    } catch (err) {
      console.warn('[V3] Error loading cycle_position_v3.json:', err);
    }
  };

  // 4. 覆盖 renderTab
  app.renderTab = function(tab) {
    switch (tab) {
      case 'china':
        TabRenderers.renderChina(this.data.cnMacro, this.data.dashboardSummary);
        break;
      case 'us':
        TabRenderers.renderUS(this.data.usMacro, this.data.dashboardSummary);
        break;
      case 'cycle':
        if (this.data.cyclePositionV3 && typeof CycleV3Module !== 'undefined') {
          CycleV3Module.render(this.data.cyclePositionV3);
        } else if (this.data.cyclePositionV2 && typeof CycleV2Module !== 'undefined') {
          console.warn('[V3] V3 data not available, falling back to V2');
          CycleV2Module.render(this.data.cyclePositionV2);
        } else {
          TabRenderers.renderCycle(this.data.cyclePosition);
        }
        break;
      case 'allocation':
        TabRenderers.renderAllocation(this.data.assetValuation, this.data.assetPrices, this.data.dashboardSummary);
        break;
      case 'timing':
        if (typeof TimingTab !== 'undefined') TimingTab.init();
        break;
      case 'stock':
        var iframe = document.getElementById('stock-iframe');
        if (iframe && iframe.src !== 'stock_dashboard.html') {
          iframe.src = 'stock_dashboard.html';
        }
        break;
    }
  };

  // 5. 覆盖 switchTab
  app.switchTab = function(tab) {
    if (tab === this.currentTab) return;

    // Dispose cycle module when leaving cycle tab
    if (this.currentTab === 'cycle') {
      if (typeof CycleV3Module !== 'undefined') {
        try { CycleV3Module.dispose(); } catch (e) {}
      }
      if (typeof CycleV2Module !== 'undefined') {
        try { CycleV2Module.dispose(); } catch (e) {}
      }
    }

    // Dispose timing charts
    if (this.currentTab === 'timing' && typeof TimingTab !== 'undefined') {
      try { TimingTab.dispose(); } catch (e) {}
    }

    // Reset stock iframe when leaving
    if (this.currentTab === 'stock') {
      var iframe = document.getElementById('stock-iframe');
      if (iframe) { iframe.src = 'about:blank'; }
    }

    this.currentTab = tab;

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    document.querySelectorAll('.tab-content').forEach(content => {
      content.classList.toggle('active', content.id === 'tab-' + tab);
      content.classList.toggle('hidden', content.id !== 'tab-' + tab);
    });

    ChartManager.disposeAll();
    this.renderTab(tab);
  };

  // 6. 覆盖 refresh
  app.refresh = async function() {
    const btn = document.getElementById('refresh-btn');
    if (btn) btn.classList.add('refreshing');

    if (this.currentTab === 'cycle') {
      if (typeof CycleV3Module !== 'undefined') { try { CycleV3Module.dispose(); } catch (e) {} }
      if (typeof CycleV2Module !== 'undefined') { try { CycleV2Module.dispose(); } catch (e) {} }
    }

    ChartManager.disposeAll();
    await this.loadAllData();
    this.renderTab(this.currentTab);

    const updateTimes = [
      this.data.cnMacro?.update_time,
      this.data.usMacro?.update_time,
      this.data.cyclePositionV3?._meta?.update_time,
      this.data.cyclePositionV2?.update_time,
    ].filter(Boolean);
    if (updateTimes.length > 0) {
      const timeEl = document.getElementById('update-time');
      if (timeEl) timeEl.textContent = '更新于 ' + updateTimes[0];
    }

    if (btn) setTimeout(function() { btn.classList.remove('refreshing'); }, 1000);
  };

  // 7. 页面卸载时清理
  window.addEventListener('beforeunload', function() {
    if (typeof CycleV3Module !== 'undefined') { try { CycleV3Module.dispose(); } catch(e) {} }
    if (typeof CycleV2Module !== 'undefined') { try { CycleV2Module.dispose(); } catch(e) {} }
  });

  console.log('[V3] app_v2_patch.js loaded - Cycle tab upgraded to V3');
})();
