/**
 * app.js - 主入口
 * 加载所有JSON数据并分发到各模块渲染
 */

// 全局数据缓存
const AppData = {};

/**
 * 安全加载JSON文件
 * @param {string} filename - 文件名
 * @returns {Promise<object|null>}
 */
async function loadJSON(filename) {
  try {
    const url = `${CONFIG.dataPath}/${filename}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (e) {
    console.warn(`[加载数据失败] ${filename}:`, e.message);
    return null;
  }
}

/**
 * 更新顶部显示
 */
function updateHeader() {
  // 从已加载数据中提取最近的更新时间
  const times = Object.values(AppData)
    .filter(d => d && d.update_time)
    .map(d => d.update_time)
    .sort()
    .reverse();

  const timeEl = document.getElementById('update-time');
  if (timeEl) {
    timeEl.textContent = times.length ? `最后更新：${times[0]}` : '数据加载中...';
  }
}

/**
 * 刷新所有数据
 */
async function refreshAll() {
  const btn = document.getElementById('btn-refresh');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '加载中...';
  }

  try {
    await loadAndRender();
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '刷新数据';
    }
  }
}

/**
 * 加载所有数据并渲染
 */
async function loadAndRender() {
  const { dataFiles } = CONFIG;

  // 并行加载所有JSON
  const [cycleData, usMacroData, cnMacroData, valuationData, pricesData, summaryData] = await Promise.all([
    loadJSON(dataFiles.cycle),
    loadJSON(dataFiles.usMacro),
    loadJSON(dataFiles.cnMacro),
    loadJSON(dataFiles.valuation),
    loadJSON(dataFiles.prices),
    loadJSON(dataFiles.summary),
  ]);

  // 缓存数据
  AppData.cycle = cycleData;
  AppData.usMacro = usMacroData;
  AppData.cnMacro = cnMacroData;
  AppData.valuation = valuationData;
  AppData.prices = pricesData;
  AppData.summary = summaryData;

  // 更新时间显示
  updateHeader();

  // 分发到各模块
  if (cycleData) CycleModule.render(cycleData);
  if (usMacroData || cnMacroData) MacroModule.render(usMacroData, cnMacroData);
  if (valuationData) ValuationModule.render(valuationData);
  if (pricesData) TechnicalModule.render(pricesData);
  if (summaryData) SummaryModule.render(summaryData);

  // 显示未加载数据的层级占位符
  showPlaceholders();
}

/**
 * 对未加载数据的部分显示占位符
 */
function showPlaceholders() {
  const placeholders = [
    { key: 'cycle', ids: ['kongbo-phase'] },
    { key: 'usMacro', ids: ['us-leading'] },
    { key: 'cnMacro', ids: ['cn-leading'] },
    { key: 'valuation', ids: ['chart-shiller-pe'] },
    { key: 'prices', ids: ['chart-normalized'] },
    { key: 'summary', ids: ['layer-cards'] },
  ];

  placeholders.forEach(({ key, ids }) => {
    if (!AppData[key]) {
      ids.forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.textContent.trim()) {
          el.innerHTML = '<p class="placeholder-msg">数据待更新</p>';
        }
      });
    }
  });
}

/**
 * 导航高亮（滚动时标记当前区域）
 */
function setupNavHighlight() {
  const sections = document.querySelectorAll('section[id]');
  const navLinks = document.querySelectorAll('.nav-pill');

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        navLinks.forEach(link => link.classList.remove('active'));
        const activeLink = document.querySelector(`.nav-pill[href="#${entry.target.id}"]`);
        if (activeLink) activeLink.classList.add('active');
      }
    });
  }, { rootMargin: '-20% 0px -70% 0px' });

  sections.forEach(section => observer.observe(section));
}

/**
 * 全局resize处理
 */
window.addEventListener('resize', () => {
  // ECharts图表自动resize由各自模块的resize listener处理
});

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
  setupNavHighlight();
  loadAndRender();
});
