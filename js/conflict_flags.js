/* conflict_flags.js — P0-⑤ 三看板冲突自动标注·前端组件（CIO 展示规格 v1）
 * 数据源：data/conflict_flags.json（生成器 build_conflict_flags.py，只读契约）
 * 展示规范：
 *   🔴 hard      → 顶部通栏常驻不可关闭，文案含两板原文+各自 as_of，禁联合结论
 *   ⛔ critical  → 深红通栏 + 弹层一次/会话
 *   🟡 divergence→ 黄点注记（结论区右上角，找不到则视口右上角），悬浮显示对方板原文+as_of
 *   v4 pending_review → 灰蓝标「待复核（评估日 X，已逾期）」（周期看板标题区/synthesis 卡）
 *   v4 reviewing      → 橙标「复核中，临时以数据层为基准」
 * 铁律：禁止合成「联合结论/综合认为」；待复核板退出判定但原文保留；缺源静默不报错。
 */
(function () {
  'use strict';
  var BOARD_NAMES = { macro_cross: '宏观截面', cycle_v4: '全球周期v4', a_timing: 'A股择时' };
  var DIR_TXT = { '1': '偏多', '0': '中性', '-1': '偏空' };

  function fetchFlags(cb) {
    var paths = ['data/conflict_flags.json', '../data/conflict_flags.json'];
    var i = 0;
    (function tryNext() {
      if (i >= paths.length) return cb(null);
      fetch(paths[i] + '?v=' + Date.now())
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (j) { cb(j); })
        .catch(function () { i++; tryNext(); });
    })();
  }

  function el(tag, css, html) {
    var d = document.createElement(tag);
    if (css) d.style.cssText = css;
    if (html != null) d.innerHTML = html;
    return d;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function boardLine(cf, id) {
    var b = (cf.boards || []).filter(function (x) { return x.board === id; })[0] || {};
    return BOARD_NAMES[id] + '「' + esc(b.raw_value || '—') + '」（' + (DIR_TXT[String(b.direction)] || '—') +
      '，截至 ' + esc(b.as_of || '—') + '）';
  }

  function renderBanner(cf) {
    var lv = cf.conflict && cf.conflict.level;
    if (lv !== 'hard' && lv !== 'critical') return;
    var hardPairs = (cf.conflict.pairs || []).filter(function (p) { return p.type === 'hard' && p.both_effective; });
    var bar;
    if (lv === 'critical') {
      bar = el('div',
        'position:relative;z-index:9999;background:#7f1d1d;color:#fecaca;padding:10px 16px;' +
        'font-size:13px;line-height:1.6;border-bottom:2px solid #ef4444;',
        '⛔ <b>高冲突</b>：三板方向互不印证，强制人工复核中。' +
        hardPairs.map(function (p) { return esc(p.note); }).join('；') +
        '　<span style="opacity:.75">并列展示，不做联合结论。</span>');
    } else {
      bar = el('div',
        'position:relative;z-index:9999;background:#991b1b;color:#fee2e2;padding:10px 16px;' +
        'font-size:13px;line-height:1.6;border-bottom:2px solid #f87171;',
        '🔴 <b>看板冲突</b>：' + hardPairs.map(function (p) {
          return boardLine(cf, p.a) + ' vs ' + boardLine(cf, p.b);
        }).join('；') +
        '。并列展示，不做联合结论。48h 内人工复核。');
    }
    document.body.insertBefore(bar, document.body.firstChild);
    if (lv === 'critical' && !sessionStorage.getItem('cf_critical_modal')) {
      sessionStorage.setItem('cf_critical_modal', '1');
      var mask = el('div',
        'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.55);display:flex;' +
        'align-items:center;justify-content:center;');
      var box = el('div',
        'max-width:520px;margin:0 16px;background:#1f2937;color:#e5e7eb;border:1px solid #ef4444;' +
        'border-radius:10px;padding:20px 22px;font-size:13px;line-height:1.8;',
        '<div style="font-size:15px;font-weight:600;color:#fca5a5;margin-bottom:8px">⛔ 三看板高冲突</div>' +
        '<div>三板方向互不印证，强制人工复核中。请各位读者以各看板原文为准，本平台不做联合结论。</div>' +
        '<div style="margin-top:8px;color:#9ca3af">' + hardPairs.map(function (p) { return esc(p.note); }).join('<br>') + '</div>' +
        '<div style="text-align:right;margin-top:14px"><button id="cf-modal-ok" style="background:#ef4444;' +
        'color:#fff;border:none;border-radius:6px;padding:6px 18px;cursor:pointer">已知悉</button></div>');
      mask.appendChild(box);
      mask.addEventListener('click', function (e) { if (e.target === mask) mask.remove(); });
      box.querySelector('#cf-modal-ok').addEventListener('click', function () { mask.remove(); });
      document.body.appendChild(mask);
    }
  }

  function renderDivergence(cf) {
    if (!cf.conflict || cf.conflict.level !== 'divergence') return;
    var pairs = (cf.conflict.pairs || []).filter(function (p) { return p.both_effective && p.type === 'divergence'; });
    if (!pairs.length) return;
    var tip = pairs.map(function (p) {
      return boardLine(cf, p.a) + '<br>vs ' + boardLine(cf, p.b);
    }).join('<br><br>');
    var dot = el('div',
      'width:14px;height:14px;border-radius:50%;background:#f59e0b;cursor:help;' +
      'box-shadow:0 0 0 3px rgba(245,158,11,.25);', '');
    dot.title = '看板分歧（不亮灯，仅注记）';
    var tipBox = el('div',
      'position:absolute;right:0;top:22px;width:300px;background:#111827;color:#d1d5db;' +
      'border:1px solid #f59e0b;border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.7;' +
      'display:none;z-index:10001;text-align:left;',
      '<div style="color:#fbbf24;font-weight:600;margin-bottom:4px">🟡 看板分歧注记</div>' + tip +
      '<div style="margin-top:6px;color:#6b7280">并列展示，不做联合结论。</div>');
    var wrap = el('div', 'position:fixed;right:14px;top:64px;z-index:10000;', '');
    wrap.appendChild(dot); wrap.appendChild(tipBox);
    wrap.addEventListener('mouseenter', function () { tipBox.style.display = 'block'; });
    wrap.addEventListener('mouseleave', function () { tipBox.style.display = 'none'; });
    document.body.appendChild(wrap);
  }

  function renderV4Clock(cf) {
    var ck = cf.v4_clock || {};
    if (ck.state !== 'pending_review' && ck.state !== 'reviewing') return;
    var pending = ck.state === 'pending_review';
    var txt = pending
      ? '待复核（评估日 ' + esc(ck.assessment_date || '—') + '，已逾期）'
      : '复核中，临时以数据层为基准';
    var css = pending
      ? 'display:inline-block;background:#334155;color:#93c5fd;border:1px solid #475569;' +
        'border-radius:6px;padding:3px 10px;font-size:12px;margin:6px 0;'
      : 'display:inline-block;background:#7c2d12;color:#fdba74;border:1px solid #c2410c;' +
        'border-radius:6px;padding:3px 10px;font-size:12px;margin:6px 0;';
    // 锚点：周期看板标题区 / synthesis 卡（找不到则不挂载，缺锚不报错）
    var anchors = [];
    var tc = document.getElementById('tab-cycle');
    if (tc) anchors.push(tc);
    Array.prototype.forEach.call(document.querySelectorAll('[id*="synthesis"],[class*="synthesis"]'), function (n) {
      anchors.push(n);
    });
    anchors.forEach(function (a) {
      var badge = el('div', css, (pending ? '🩶 ' : '🟠 ') + '全球周期v4 ' + txt);
      a.insertBefore(badge, a.firstChild);
    });
  }

  function init() {
    fetchFlags(function (cf) {
      if (!cf || !cf.conflict) return; // 缺源静默降级
      renderBanner(cf);
      renderDivergence(cf);
      renderV4Clock(cf);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
