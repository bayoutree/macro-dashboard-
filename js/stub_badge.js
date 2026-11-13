/* [2026-11-12] stub 指标「估算值」视觉标识
 *
 * 背景：v4_stub_flags.json 在数据层标记了哪些指标是桩值/未核实，
 * 但前端渲染与真实观测无差别，读者会把估算值当成官方值。
 * 本脚本读该文件，有命中时在页面顶部挂一条黄色提示条，列出被标指标。
 * 当前 stub_count=0，属防御性实现：未来再出现桩值会自动亮出来。
 */
(function () {
  'use strict';
  var LABEL = {
    stub: '桩值（静态占位，非官方观测）',
    // [2026-11-13] 补三类非官方观测的中文名：原先直接吐英文 key
    // （manual_compiled / rolling_single / non_numeric），读者看不懂。
    manual_compiled: '手工汇编估算（有公开来源、无历史序列）',
    rolling_single: '滚动单值（区间计算值，非当期观测）',
    non_numeric: '文本派生（定性判断，非数值观测）',
    unverified: '未核实（来源待定级）',
    meta_error: '元数据待修（值有效、时间戳有误）',
    lag_normal: '正常滞后'
  };

  function render(flags) {
    if (!flags || !flags.length) return;
    var bar = document.createElement('div');
    bar.id = 'stub-notice';
    bar.setAttribute('role', 'alert');
    bar.style.cssText = [
      'position:relative', 'margin:0 0 16px 0', 'padding:10px 14px',
      'background:#78350f', 'border:1px solid #b45309', 'border-radius:8px',
      'color:#fde68a', 'font-size:13px', 'line-height:1.6'
    ].join(';');

    var items = flags.map(function (f) {
      var nm = (f.name || (f.path || '').split('/').pop());
      var st = LABEL[f.status] || f.status || '未标注';
      var cur = (f.current !== undefined && f.current !== null) ? ('，当前显示 ' + f.current + (f.unit || '')) : '';
      return '<li style="margin:2px 0"><b>' + nm + '</b>：' + st + cur + '</li>';
    }).join('');

    bar.innerHTML =
      '<div style="font-weight:600;margin-bottom:4px">⚠ 本看板有 ' + flags.length +
      ' 项指标不是官方观测值，请勿按真实数据解读</div>' +
      '<ul style="margin:0;padding-left:18px">' + items + '</ul>';

    var main = document.querySelector('main') || document.body;
    main.insertBefore(bar, main.firstChild);
  }

  function boot() {
    fetch('data/v4_stub_flags.json?v=' + Date.now())
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        render(d.flags || []);
      })
      .catch(function () { /* 静默：标识失败不能影响主看板 */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
