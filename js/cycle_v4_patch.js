/**
 * cycle_v4_patch.js - Phase 4-A 前端7项改造
 * 
 * 改造清单:
 * 1. 信息流倒置 (Hero → Evidence → Details)
 * 2. 8资产排序 (asset_ranking)
 * 3. 证伪清单 (falsification)
 * 4. 本期vs上期 (localStorage tracking)
 * 5. 数据新鲜度 (timestamps)
 * 6. 矛盾标注 (contradiction_status)
 * 7. 数据质量等级 (data_quality)
 * 
 * 数据源: cycle_position_v4.json (commit a173b56)
 */

(function() {
  'use strict';

  var ASSET_LABELS = {
    'china_equity': '🇨🇳 A股',
    'china_bond': '🇨🇳 中国债券',
    'china_realestate': '🇨🇳 中国地产',
    'us_equity': '🇺🇸 美股',
    'us_bond': '🇺🇸 美债',
    'commodities': '🛢️ 大宗商品',
    'gold': '🥇 黄金',
    'usd': '💵 美元'
  };

  var QUALITY_COLORS = { 'HIGH': '#10b981', 'MEDIUM': '#f59e0b', 'LOW': '#ef4444' };
  var QUALITY_BG = { 'HIGH': 'rgba(16,185,129,0.12)', 'MEDIUM': 'rgba(245,158,11,0.12)', 'LOW': 'rgba(239,68,68,0.12)' };
  var QUALITY_LABELS = { 'HIGH': '高', 'MEDIUM': '中', 'LOW': '低' };

  var DIRECTION_ARROWS = { 'up': '↑', 'neutral': '→', 'down': '↓' };
  var DIRECTION_COLORS = { 'up': '#10b981', 'neutral': '#f59e0b', 'down': '#ef4444' };

  var SIGNAL_BADGE_STYLES = {
    '超配': { bg: '#dcfce7', color: '#166534', border: '#86efac' },
    '标配': { bg: '#fef9c3', color: '#854d0e', border: '#fde047' },
    '低配': { bg: '#fee2e2', color: '#991b1b', border: '#fca5a5' }
  };

  var _v4Data = null;

  function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ========== 1. Hero Section ==========
  function renderHeroSection(data) {
    var cc = data.cycle_consensus;
    if (!cc) return '';
    var us = cc.united_states || {};
    var cn = cc.china || {};

    function scoreColor(s) {
      if (s >= 80) return '#10b981';
      if (s >= 60) return '#84cc16';
      if (s >= 40) return '#f59e0b';
      if (s >= 20) return '#f97316';
      return '#ef4444';
    }
    function signalText(s) {
      if (s >= 80) return '强烈看多';
      if (s >= 60) return '温和看多';
      if (s >= 40) return '中性偏谨慎';
      if (s >= 20) return '偏空';
      return '强烈看空';
    }

    function renderRegionBlock(label, flag, d) {
      var sc = scoreColor(d.consensus_score);
      var sig = signalText(d.consensus_score);
      return '<div class="v4-hero-region">' +
        '<div class="v4-hero-region-header">' +
          '<span class="v4-hero-flag">' + flag + '</span>' +
          '<span class="v4-hero-region-name">' + label + '</span>' +
        '</div>' +
        '<div class="v4-hero-score" style="color:' + sc + '">' + d.consensus_score.toFixed(1) + '</div>' +
        '<div class="v4-hero-signal" style="color:' + sc + '">' + sig + '</div>' +
        '<div class="v4-hero-layers">' +
          '<div class="v4-hero-layer"><span class="v4-hero-p-label">P1 朱格拉</span><span class="v4-hero-p-val" style="color:' + (d.p1_score > 0 ? '#10b981' : d.p1_score < 0 ? '#ef4444' : '#f59e0b') + '">' + d.p1_score + '</span></div>' +
          '<div class="v4-hero-layer"><span class="v4-hero-p-label">P2 基钦</span><span class="v4-hero-p-val" style="color:' + (d.p2_score > 0 ? '#10b981' : d.p2_score < 0 ? '#ef4444' : '#f59e0b') + '">' + d.p2_score + '</span></div>' +
          '<div class="v4-hero-layer"><span class="v4-hero-p-label">P3 美林</span><span class="v4-hero-p-val" style="color:' + (d.p3_score > 0 ? '#10b981' : d.p3_score < 0 ? '#ef4444' : '#f59e0b') + '">' + d.p3_score + '</span></div>' +
        '</div>' +
        '<div class="v4-hero-labels">' +
          '<div class="v4-hero-label">' + esc(d.p1_label || '') + '</div>' +
          '<div class="v4-hero-label">' + esc(d.p2_label || '') + '</div>' +
          '<div class="v4-hero-label">' + esc(d.p3_label || '') + '</div>' +
        '</div>' +
      '</div>';
    }

    // Comparison delta
    var prev = {};
    try { prev = JSON.parse(localStorage.getItem('v4_comparison') || '{}'); } catch(e) {}
    var usDelta = prev.us_score ? (us.consensus_score - prev.us_score) : null;
    var cnDelta = prev.cn_score ? (cn.consensus_score - prev.cn_score) : null;

    function renderDelta(delta) {
      if (delta === null || delta === undefined) return '';
      var abs = Math.abs(delta).toFixed(1);
      if (Math.abs(delta) < 0.05) return '<span class="v4-delta v4-delta-flat">— 持平</span>';
      if (delta > 0) return '<span class="v4-delta v4-delta-up">↑+' + abs + '</span>';
      return '<span class="v4-delta v4-delta-down">↓-' + abs + '</span>';
    }

    var formula = cc.formula || 'raw_score = P1×0.3 + P2×0.4 + P3×0.3';

    return '<section class="v4-section v4-hero-section" id="v4-hero">' +
      '<div class="v4-hero-header">' +
        '<h2 class="v4-hero-title">📊 周期定位总览</h2>' +
        '<span class="v4-hero-date">📅 ' + esc(cc.last_updated || '') + '</span>' +
      '</div>' +
      '<div class="v4-hero-formula">' + esc(formula) + '</div>' +
      '<div class="v4-hero-grid">' +
        renderRegionBlock('美国', '🇺🇸', us) +
        renderRegionBlock('中国', '🇨🇳', cn) +
      '</div>' +
      '<div class="v4-hero-deltas">' +
        '<span class="v4-delta-label">vs 上期:</span>' +
        '<span>🇺🇸 ' + renderDelta(usDelta) + '</span>' +
        '<span>🇨🇳 ' + renderDelta(cnDelta) + '</span>' +
      '</div>' +
    '</section>';
  }

  // ========== 2. Asset Ranking Section ==========
  function renderAssetRankingSection(data) {
    var ar = data.asset_ranking;
    if (!ar || !ar.ranking) return '';

    var rows = ar.ranking.map(function(r) {
      var label = ASSET_LABELS[r.asset] || r.asset;
      var dirArrow = DIRECTION_ARROWS[r.adjusted_direction] || '→';
      var dirColor = DIRECTION_COLORS[r.adjusted_direction] || '#f59e0b';
      var baseArrow = DIRECTION_ARROWS[r.base_direction] || '→';
      var badge = SIGNAL_BADGE_STYLES[r.signal] || SIGNAL_BADGE_STYLES['标配'];
      var adjHtml = '';
      if (r.adjustment && r.adjustment !== 0) {
        var adjColor = r.adjustment < 0 ? '#ef4444' : '#10b981';
        adjHtml = '<span class="v4-rank-adj" style="color:' + adjColor + '" title="基线方向: ' + baseArrow + ' → 降级后: ' + dirArrow + '">' + (r.adjustment > 0 ? '+' : '') + r.adjustment + '</span>';
      } else {
        adjHtml = '<span class="v4-rank-adj v4-rank-adj-none">—</span>';
      }

      var barWidth = Math.max(10, (9 - r.rank + 1) / 8 * 100);
      var barColor = r.signal === '超配' ? '#10b981' : r.signal === '低配' ? '#ef4444' : '#f59e0b';

      return '<tr class="v4-rank-row">' +
        '<td class="v4-rank-num">#' + r.rank + '</td>' +
        '<td class="v4-rank-name">' + label + '</td>' +
        '<td class="v4-rank-signal"><span class="v4-signal-badge" style="background:' + badge.bg + ';color:' + badge.color + ';border:1px solid ' + badge.border + '">' + r.signal + '</span></td>' +
        '<td class="v4-rank-dir"><span style="color:' + dirColor + ';font-weight:600">' + dirArrow + '</span> ' + esc(r.adjusted_direction) + '</td>' +
        '<td class="v4-rank-adj-cell">' + adjHtml + '</td>' +
        '<td class="v4-rank-bar-cell"><div class="v4-rank-bar-bg"><div class="v4-rank-bar" style="width:' + barWidth + '%;background:' + barColor + '"></div></div></td>' +
      '</tr>';
    }).join('');

    var constraintHtml = '';
    if (ar.constraint_active) {
      constraintHtml = '<div class="v4-constraint-notice">' +
        '⚠️ 约束层生效: C1(大债务) + C2(高利率) 已触发降级' +
        '<span class="v4-constraint-note">' + esc(ar.note || '') + '</span>' +
      '</div>';
    }

    return '<section class="v4-section v4-ranking-section" id="v4-asset-ranking">' +
      '<h2 class="v4-section-title">🏆 8资产吸引力排序</h2>' +
      '<div class="v4-ranking-subtitle">基于 transmission_table(P1=' + (((data.cycle_consensus || {}).united_states || {}).p1_score || ((data.cycle_consensus || {}).china || {}).p1_score || '?') + ',P2=' + (((data.cycle_consensus || {}).united_states || {}).p2_score || ((data.cycle_consensus || {}).china || {}).p2_score || '?') + ') + constraint_degradation</div>' +
      '<table class="v4-ranking-table">' +
        '<thead><tr>' +
          '<th>排名</th><th>资产</th><th>信号</th><th>方向</th><th>降级</th><th>强度</th>' +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
      constraintHtml +
    '</section>';
  }

  // ========== 3. Falsification Checklist ==========
  function renderFalsificationSection(data) {
    var cc = data.cycle_consensus || {};
    var us = cc.united_states || {};
    var cn = cc.china || {};
    var cd = data.constraint_degradation || {};

    var items = [];

    // P1 falsification: Capacity utilization
    var juglar = (data.cycle_layers || {}).cycle_juglar || {};
    var juglarUs = juglar.us || {};
    var juglarCn = juglar.cn || {};
    items.push({
      icon: '⚙️',
      label: '朱格拉扩张',
      condition: '产能利用率 > 77%',
      us_status: us.p1_score > 0 ? '✅ 当前P1=' + us.p1_score + '（' + (us.p1_label || '') + '）' : '❌ P1=' + us.p1_score,
      cn_status: cn.p1_score > 0 ? '✅ 当前P1=' + cn.p1_score + '（' + (cn.p1_label || '') + '）' : '❌ P1=' + cn.p1_score,
      falsify: '若产能利用率跌破77%且持续2月 → P1转负，扩张结束'
    });

    // P2 falsification: Inventory/Sales
    items.push({
      icon: '📦',
      label: '基钦补库',
      condition: '库存/销售比稳定或下降',
      us_status: us.p2_score > 0 ? '✅ 当前P2=' + us.p2_score + '（' + (us.p2_label || '') + '）' : '❌ P2=' + us.p2_score,
      cn_status: cn.p2_score > 0 ? '✅ 当前P2=' + cn.p2_score + '（' + (cn.p2_label || '') + '）' : '❌ P2=' + cn.p2_score,
      falsify: '若库存/销售比连续3月上升 → 从补库转入去库'
    });

    // P3 falsification: Merrill clock
    items.push({
      icon: '🕐',
      label: '美林复苏',
      condition: '增长↑ 通胀↓ 信贷↑',
      us_status: us.p3_score > 0 ? '✅ 当前P3=' + us.p3_score + '（' + (us.p3_label || '') + '）' : '❌ P3=' + us.p3_score,
      cn_status: cn.p3_score > 0 ? '✅ 当前P3=' + cn.p3_score + '（' + (cn.p3_label || '') + '）' : '❌ P3=' + cn.p3_score,
      falsify: '若通胀转为上行或信贷收缩 → 离开复苏象限'
    });

    // C1 constraint
    var c1 = cd.C1_debt_cycle || {};
    if (c1.currently_triggered) {
      var c1v = c1.current_values || {};
      items.push({
        icon: '🔴',
        label: 'C1 债务约束',
        condition: '债务/GDP>130% 且 利息/财政收入>20%',
        us_status: '⚠️ 已触发: 债务/GDP=' + (c1v.debt_to_gdp || '?') + '%, 利息/收入=' + (c1v.interest_to_revenue || '?') + '%',
        cn_status: 'ℹ️ 中国不适用（财政结构不同）',
        falsify: '若债务/GDP回落至130%以下或利息/财政收入<20% → 约束解除'
      });
    }

    // C2 constraint
    var c2 = cd.C2_rate_regime || {};
    if (c2.currently_triggered) {
      var c2v = c2.current_values || {};
      items.push({
        icon: '🔴',
        label: 'C2 利率约束',
        condition: '联邦基金利率>3% 且 实际利率>1%',
        us_status: '⚠️ 已触发: 基金利率=' + (c2v.fed_funds_rate || '?') + '%, 实际利率=' + (c2v.real_rate || '?') + '%',
        cn_status: '⚠️ 联动触发',
        falsify: '若联邦基金利率降至3%以下或实际利率<1% → 约束解除'
      });
    }

    var rows = items.map(function(item) {
      return '<tr>' +
        '<td class="v4-fals-icon">' + item.icon + '</td>' +
        '<td class="v4-fals-label"><strong>' + esc(item.label) + '</strong><br><span class="v4-fals-cond">' + esc(item.condition) + '</span></td>' +
        '<td class="v4-fals-us">' + esc(item.us_status) + '</td>' +
        '<td class="v4-fals-cn">' + esc(item.cn_status) + '</td>' +
        '<td class="v4-fals-criteria">' + esc(item.falsify) + '</td>' +
      '</tr>';
    }).join('');

    return '<section class="v4-section v4-falsification-section" id="v4-falsification">' +
      '<h2 class="v4-section-title">🔬 证伪条件清单</h2>' +
      '<div class="v4-falsification-desc">当前周期定位的成立条件及证伪标准。任一条件被证伪时，对应P值需重新评估。</div>' +
      '<table class="v4-falsification-table">' +
        '<thead><tr>' +
          '<th></th><th>周期/约束</th><th>🇺🇸 美国</th><th>🇨🇳 中国</th><th>证伪标准</th>' +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
    '</section>';
  }

  // ========== 4. Data Freshness Section ==========
  function renderFreshnessSection(data) {
    var meta = data._meta || {};
    var sources = meta.data_sources || [];
    var dq = data.data_quality || {};
    var cq = dq.current_quality || {};

    var sourceCards = sources.map(function(s) {
      var isStale = s.status === 'stale';
      var statusColor = isStale ? '#ef4444' : '#10b981';
      var statusIcon = isStale ? '🔴' : '🟢';
      var statusText = isStale ? '过期' : '正常';
      return '<div class="v4-source-card' + (isStale ? ' v4-source-stale' : '') + '">' +
        '<div class="v4-source-name">' + esc(s.name) + '</div>' +
        '<div class="v4-source-status"><span style="color:' + statusColor + '">' + statusIcon + ' ' + statusText + '</span></div>' +
        '<div class="v4-source-date">最后更新: ' + esc(s.last_fetch || '--') + '</div>' +
      '</div>';
    }).join('');

    // Quality badges for US and CN
    function renderQualityBlock(region, label, flag) {
      var q = cq[region] || {};
      var overall = q.overall || 'N/A';
      var details = q.details || {};
      var oColor = QUALITY_COLORS[overall] || '#6b7280';
      var oBg = QUALITY_BG[overall] || 'rgba(107,114,128,0.12)';

      var detailBadges = Object.entries(details).map(function(entry) {
        var key = entry[0], level = entry[1];
        var c = QUALITY_COLORS[level] || '#6b7280';
        var bg = QUALITY_BG[level] || 'rgba(107,114,128,0.12)';
        var l = QUALITY_LABELS[level] || '?';
        return '<span class="v4-quality-badge" style="background:' + bg + ';color:' + c + '" title="' + esc(key) + '">' + esc(key) + ': ' + l + '</span>';
      }).join('');

      return '<div class="v4-quality-block">' +
        '<div class="v4-quality-header">' +
          '<span>' + flag + ' ' + label + '</span>' +
          '<span class="v4-quality-overall" style="background:' + oBg + ';color:' + oColor + '">总评: ' + overall + '</span>' +
        '</div>' +
        '<div class="v4-quality-details">' + detailBadges + '</div>' +
      '</div>';
    }

    return '<section class="v4-section v4-freshness-section" id="v4-data-quality">' +
      '<h2 class="v4-section-title">📡 数据质量与来源</h2>' +
      '<div class="v4-freshness-meta">' +
        '<span>评估日期: <strong>' + esc(meta.assessment_date || '--') + '</strong></span>' +
        '<span>新鲜度评分: <strong style="color:' + ((meta.data_freshness_score||0) >= 80 ? '#10b981' : (meta.data_freshness_score||0) >= 60 ? '#f59e0b' : '#ef4444') + '">' + (meta.data_freshness_score || '--') + '/100</strong></span>' +
        '<span>下次评估: ' + esc(meta.next_assessment_date || '--') + '</span>' +
      '</div>' +
      '<div class="v4-quality-grid">' +
        renderQualityBlock('united_states', '美国', '🇺🇸') +
        renderQualityBlock('china', '中国', '🇨🇳') +
      '</div>' +
      '<div class="v4-sources-grid">' + sourceCards + '</div>' +
    '</section>';
  }

  // ========== 5. Contradiction Warning ==========
  function renderContradictionWarning(data) {
    var cs = data.contradiction_status || {};
    var status = cs.current_status || {};
    var warnings = [];

    ['united_states', 'china'].forEach(function(region) {
      var r = status[region] || {};
      if (r.has_contradiction) {
        var flag = region === 'united_states' ? '🇺🇸' : '🇨🇳';
        warnings.push(flag + ' ' + esc(r.details || '存在矛盾信号'));
      }
    });

    // Also check transmission_table for current combination
    var tt = data.transmission_table || {};
    var cc = data.cycle_consensus || {};
    var us = cc.united_states || {};
    var cn = cc.china || {};
    var contradCombos = tt.contradictory_combinations || [];

    // Check if current US or CN combo is in contradictory list
    var entries = tt.entries || [];
    [us, cn].forEach(function(region, idx) {
      var p1 = region.p1_score;
      var p2 = region.p2_score;
      if (p1 === undefined || p2 === undefined) return;
      entries.forEach(function(e, i) {
        if (e.p1_score === p1 && e.p2_score === p2 && e.contradictory) {
          var flag = idx === 0 ? '🇺🇸' : '🇨🇳';
          var comboNum = i + 1;
          warnings.push(flag + ' 当前P1=' + p1 + '×P2=' + p2 + '为矛盾组合#' + comboNum + '（' + esc(e.scenario || '') + '）');
        }
      });
    });

    if (warnings.length === 0) return '';

    var warningList = warnings.map(function(w) {
      return '<div class="v4-contradiction-item">⚠️ ' + w + '</div>';
    }).join('');

    var displayRules = cs.display_rules || {};
    var message = displayRules.warning_message || '周期信号矛盾，建议防御优先';

    return '<div class="v4-contradiction-warning" id="v4-contradiction-warning">' +
      '<div class="v4-contradiction-header">' +
        '<span class="v4-contradiction-icon">⚠️</span>' +
        '<span class="v4-contradiction-title">周期信号矛盾</span>' +
      '</div>' +
      warningList +
      '<div class="v4-contradiction-action">默认行为: ' + esc(((cs.rules || {}).default_behavior) || '防御优先，不超配风险资产') + '</div>' +
    '</div>';
  }

  // ========== 6. Comparison Tracking ==========
  function updateComparison(data) {
    var cc = data.cycle_consensus || {};
    var us = cc.united_states || {};
    var cn = cc.china || {};

    var current = {
      us_score: us.consensus_score || 0,
      cn_score: cn.consensus_score || 0,
      date: cc.last_updated || new Date().toISOString().slice(0, 10),
      ranking: {}
    };

    var ar = data.asset_ranking || {};
    (ar.ranking || []).forEach(function(r) {
      current.ranking[r.asset] = r.rank;
    });

    localStorage.setItem('v4_comparison', JSON.stringify(current));
  }

  // ========== 7. DOM Enhancement: Quality badges on layer sections ==========
  function addQualityBadges(data) {
    var dq = data.data_quality || {};
    var cq = dq.current_quality || {};
    var usDetails = (cq.united_states || {}).details || {};
    var cnDetails = (cq.china || {}).details || {};

    // Map section IDs to quality keys
    var sectionMap = {
      'section-debt-cycle': { key: 'C1_debt_cycle' },
      'section-juglar': { key: 'P1_juglar' },
      'section-kitchin': { key: 'P2_kitchin' },
      'section-merrill': { key: 'P3_merrill' }
    };

    Object.entries(sectionMap).forEach(function(entry) {
      var sectionId = entry[0];
      var info = entry[1];
      var section = document.getElementById(sectionId);
      if (!section) return;

      var title = section.querySelector('.section-title, .v3-section > h2');
      if (!title) return;

      var usQ = usDetails[info.key] || '';
      var cnQ = cnDetails[info.key] || '';

      if (!usQ && !cnQ) return;

      var badgeHtml = '<span class="v4-layer-quality">';
      if (usQ) {
        var c = QUALITY_COLORS[usQ] || '#6b7280';
        badgeHtml += '<span class="v4-layer-qbadge" style="color:' + c + '" title="美国数据质量: ' + usQ + '">🇺🇸' + (QUALITY_LABELS[usQ] || usQ) + '</span>';
      }
      if (cnQ) {
        var c2 = QUALITY_COLORS[cnQ] || '#6b7280';
        badgeHtml += '<span class="v4-layer-qbadge" style="color:' + c2 + '" title="中国数据质量: ' + cnQ + '">🇨🇳' + (QUALITY_LABELS[cnQ] || cnQ) + '</span>';
      }
      badgeHtml += '</span>';

      title.insertAdjacentHTML('beforeend', badgeHtml);
    });
  }

  // ========== 8. DOM Enhancement: Freshness timestamps ==========
  function addFreshnessTimestamps(data) {
    var meta = data._meta || {};
    var sources = meta.data_sources || [];
    // Find most recent data source date
    var latestDate = meta.assessment_date || '';
    sources.forEach(function(s) {
      if (s.last_fetch > latestDate) latestDate = s.last_fetch;
    });

    // Add data freshness to meta header if exists
    var metaHeader = document.querySelector('.meta-header');
    if (metaHeader) {
      var freshnessDiv = document.createElement('div');
      freshnessDiv.className = 'v4-freshness-inline';
      freshnessDiv.innerHTML = '<span class="v4-freshness-dot" style="background:' + ((meta.data_freshness_score||0) >= 80 ? '#10b981' : (meta.data_freshness_score||0) >= 60 ? '#f59e0b' : '#ef4444') + '"></span>' +
        '数据截至 ' + esc(latestDate) + ' · 新鲜度 ' + (meta.data_freshness_score || '--') + '/100';
      metaHeader.appendChild(freshnessDiv);
    }
  }

  // ========== DOM Reorganization ==========
  function reorganizeDOM(data) {
    var container = document.getElementById('cycle-content');
    if (!container) return;

    // 1. Insert contradiction warning at very top (if any)
    var contraHtml = renderContradictionWarning(data);
    if (contraHtml) {
      container.insertAdjacentHTML('afterbegin', contraHtml);
    }

    // 2. Insert Hero section after meta-header (before old consensus)
    var heroHtml = renderHeroSection(data);
    var metaHeader = container.querySelector('.meta-header');
    if (metaHeader) {
      metaHeader.insertAdjacentHTML('afterend', heroHtml);
    } else {
      container.insertAdjacentHTML('afterbegin', heroHtml);
    }

    // 3. Insert Asset Ranking after hero
    var rankingHtml = renderAssetRankingSection(data);
    var heroSection = document.getElementById('v4-hero');
    if (heroSection && rankingHtml) {
      heroSection.insertAdjacentHTML('afterend', rankingHtml);
    }

    // 4. Insert Falsification after asset ranking (or hero if no ranking)
    var falsHtml = renderFalsificationSection(data);
    var rankingSection = document.getElementById('v4-asset-ranking');
    if (rankingSection && falsHtml) {
      rankingSection.insertAdjacentHTML('afterend', falsHtml);
    } else if (heroSection && falsHtml) {
      heroSection.insertAdjacentHTML('afterend', falsHtml);
    }

    // 5. Insert Data Quality at bottom
    var freshHtml = renderFreshnessSection(data);
    if (freshHtml) {
      container.insertAdjacentHTML('beforeend', freshHtml);
    }

    // 6. Add quality badges to existing layer sections
    addQualityBadges(data);

    // 7. Add freshness timestamps
    addFreshnessTimestamps(data);

    // 8. Update comparison tracking
    updateComparison(data);
  }

  // ========== Main Install ==========
  function install() {
    if (!window.CycleV3Module) {
      console.error('[V4] CycleV3Module not found, cannot install patch');
      return;
    }

    var _origRender = window.CycleV3Module.render.bind(window.CycleV3Module);

    window.CycleV3Module.render = function(data) {
      // Call original v3 render (builds all existing DOM)
      _origRender(data);

      // Store data for external access
      window._v4Data = data;

      // After v3 has populated DOM, add v4 enhancements
      requestAnimationFrame(function() {
        try {
          reorganizeDOM(data);
        } catch(e) {
          console.error('[V4] DOM enhancement error:', e);
        }
      });
    };

    console.log('[V4] Phase 4-A patch installed (7 enhancements)');
  }

  // Auto-install when loaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  } else {
    install();
  }
})();
