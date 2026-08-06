/**
 * app_v2_patch.js - V2 周期看板适配补丁
 * 
 * 覆盖原有 app.js 中的周期Tab渲染逻辑，
 * 改用 CycleV2Module 进行渲染。
 * 
 * 使用方式: 在 index_v2.html 中，在 app.js 之后引入本文件。
 */

(function() {
  'use strict';

  // 1. 在 app.data 中添加 cyclePositionV2 字段
  if (typeof app !== 'undefined' && app.data) {
    app.data.cyclePositionV2 = null;
  }

  // 2. 保存原始的 loadAllData 和 renderTab 方法
  const _originalLoadAllData = app.loadAllData.bind(app);
  const _originalRenderTab = app.renderTab.bind(app);
  const _originalSwitchTab = app.switchTab.bind(app);
  const _originalRefresh = app.refresh.bind(app);

  // 3. 覆盖 loadAllData - 额外加载 cycle_position_v2.json
  app.loadAllData = async function() {
    // 先执行原始加载
    await _originalLoadAllData();

    // 额外加载 V2 周期数据
    try {
      const resp = await fetch(`${CONFIG.dataDir}/cycle_position_v2.json`);
      if (resp.ok) {
        const rawData = await resp.json();
        this.data.cyclePositionV2 = normalizeCycleData(rawData);
        console.log('[V2] cycle_position_v2.json loaded and normalized');
      } else {
        console.warn('[V2] Failed to load cycle_position_v2.json:', resp.status);
      }
    } catch (err) {
      console.warn('[V2] Error loading cycle_position_v2.json:', err);
    }
  };

  // 4. 覆盖 renderTab - cycle tab 使用 V2 模块
  app.renderTab = function(tab) {
    switch (tab) {
      case 'china':
        TabRenderers.renderChina(this.data.cnMacro, this.data.dashboardSummary);
        break;
      case 'us':
        TabRenderers.renderUS(this.data.usMacro, this.data.dashboardSummary);
        break;
      case 'cycle':
        // 使用 V2 渲染模块
        if (this.data.cyclePositionV2) {
          CycleV2Module.render(this.data.cyclePositionV2);
        } else {
          // 回退到 V1
          console.warn('[V2] cyclePositionV2 not available, falling back to V1');
          TabRenderers.renderCycle(this.data.cyclePosition);
        }
        break;
      case 'allocation':
        TabRenderers.renderAllocation(this.data.assetValuation, this.data.assetPrices, this.data.dashboardSummary);
        break;
    }
  };

  // 5. 覆盖 switchTab - 切换时 dispose V2 模块
  app.switchTab = function(tab) {
    if (tab === this.currentTab) return;

    // 如果当前是 cycle tab，先 dispose V2 模块
    if (this.currentTab === 'cycle' && typeof CycleV2Module !== 'undefined') {
      try {
        CycleV2Module.dispose();
      } catch (e) {
        console.warn('[V2] Error disposing CycleV2Module:', e);
      }
    }

    this.currentTab = tab;

    // 更新 tab 按钮状态
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // 更新 tab 内容显示
    document.querySelectorAll('.tab-content').forEach(content => {
      content.classList.toggle('active', content.id === `tab-${tab}`);
      content.classList.toggle('hidden', content.id !== `tab-${tab}`);
    });

    // Dispose 原有图表并渲染新 tab
    ChartManager.disposeAll();
    this.renderTab(tab);
  };

  // 6. 覆盖 refresh - 确保 V2 数据也刷新
  app.refresh = async function() {
    const btn = document.getElementById('refresh-btn');
    if (btn) btn.classList.add('refreshing');

    // Dispose V2 charts if on cycle tab
    if (this.currentTab === 'cycle' && typeof CycleV2Module !== 'undefined') {
      try {
        CycleV2Module.dispose();
      } catch (e) {}
    }

    ChartManager.disposeAll();
    await this.loadAllData();
    this.renderTab(this.currentTab);

    // 更新时间显示
    const updateTimes = [
      this.data.cnMacro?.update_time,
      this.data.usMacro?.update_time,
      this.data.cyclePositionV2?.update_time,
    ].filter(Boolean);
    if (updateTimes.length > 0) {
      const timeEl = document.getElementById('update-time');
      if (timeEl) timeEl.textContent = `更新于 ${updateTimes[0]}`;
    }

    if (btn) setTimeout(() => btn.classList.remove('refreshing'), 1000);
  };

  // 7. 页面卸载时清理
  window.addEventListener('beforeunload', () => {
    if (typeof CycleV2Module !== 'undefined') {
      try { CycleV2Module.dispose(); } catch(e) {}
    }
  });

  console.log('[V2] app_v2_patch.js loaded - Cycle tab upgraded to V2');
})();
