/**
 * data_normalize.js - 数据适配层
 * 将 cycle_position_v2.json 的原始数据结构转换为 CycleV2Module 期望的格式
 */

function normalizeCycleData(raw) {
  if (!raw) return raw;
  const data = JSON.parse(JSON.stringify(raw)); // deep clone

  // === 1. Synthesis: cycle_positions_summary ===
  if (data.synthesis?.cycle_positions_summary) {
    data.synthesis.cycle_positions_summary = data.synthesis.cycle_positions_summary.map(item => {
      // Derive signal code from color or text
      let signal = 'neutral';
      const color = (item.color || '').toLowerCase();
      if (color === '#10b981' || color.includes('green')) signal = 'bullish';
      else if (color === '#ef4444' || color.includes('red')) signal = 'bearish';
      else if (color === '#f59e0b' || color.includes('amber')) signal = 'neutral';
      else if (color === '#22d3ee' || color.includes('cyan')) signal = 'bullish';
      return { ...item, name: item.cycle || item.name, signal };
    });
  }

  // === 2. Synthesis: asset_views ===
  if (data.synthesis?.asset_views) {
    data.synthesis.asset_views = data.synthesis.asset_views.map(item => ({
      ...item,
      name: item.asset || item.name,
      allocation: item.view || item.allocation,
      reason: item.reasoning || item.reason || item.rationale || '',
    }));
  }

  // === 3. Synthesis: key_observations ===
  if (data.synthesis?.key_watchpoints && !data.synthesis?.key_observations) {
    data.synthesis.key_observations = data.synthesis.key_watchpoints;
  }

  // === 4. Perez ===
  if (data.perez) {
    const pz = data.perez;
    // Map current_phase to current_stage
    if (pz.current_phase && !pz.current_stage) {
      pz.current_stage = pz.current_phase;
    }
    // Move stage_description signals to top level
    if (pz.stage_description) {
      if (pz.stage_description.financial_signals && !pz.financial_signals) {
        pz.financial_signals = pz.stage_description.financial_signals;
      }
      if (pz.stage_description.real_signals && !pz.real_signals) {
        pz.real_signals = pz.stage_description.real_signals;
      }
    }
    // Move key_ratio to indicators as finance_real_ratio
    if (pz.key_ratio && !pz.indicators) pz.indicators = {};
    if (pz.key_ratio) {
      pz.indicators.finance_real_ratio = {
        name: pz.key_ratio.name || '金融资本/生产资本比值',
        data: pz.key_ratio.history || [],
        thresholds: [
          { value: 2.0, label: 'Frenzy阈值', color: '#f59e0b' }
        ]
      };
    }
    // Normalize indicator format: {history: [{date, value}]} → {data: [{date, value}]}
    if (pz.indicators) {
      Object.keys(pz.indicators).forEach(key => {
        const ind = pz.indicators[key];
        if (ind && ind.history && !ind.data) {
          ind.data = ind.history;
        }
      });
    }
  }

  // === 5. Juglar: add signal and map phase_position ===
  ['us', 'cn'].forEach(region => {
    const d = data.juglar?.[region];
    if (!d) return;
    // Map phase_position → phase_progress
    if (d.phase_position !== undefined && d.phase_progress === undefined) {
      d.phase_progress = d.phase_position;
    }
    // Derive signal from phase_code
    const pc = d.phase_code || '';
    if (pc.includes('expansion') || pc.includes('early_expansion')) d.signal = 'bullish';
    else if (pc.includes('contraction') || pc.includes('late_contraction')) d.signal = 'bearish';
    else d.signal = 'neutral';
  });

  // === 6. Kitchin: add signal, current_phase_index, and normalize indicator names ===
  const kitchinPhaseMap = {
    'passive_destocking': 0, 'early_restocking': 0,
    'active_restocking': 1, 'mid_restocking': 1,
    'passive_restocking': 2, 'late_restocking': 2,
    'active_destocking': 3, 'late_destocking': 3,
    'divergent_restocking': 1, 'divergent_destocking': 3,
  };

  ['us', 'cn'].forEach(region => {
    const d = data.kitchin?.[region];
    if (!d) return;
    // Derive signal
    const pc = d.phase_code || '';
    if (pc.includes('restocking') && !pc.includes('divergent')) d.signal = 'bullish';
    else if (pc.includes('destocking')) d.signal = 'bearish';
    else if (pc.includes('divergent')) d.signal = 'neutral';
    else d.signal = 'neutral';

    // Map phase_code → current_phase_index
    d.current_phase_index = kitchinPhaseMap[d.phase_code] ?? 0;

    // Normalize indicator names
    if (d.indicators) {
      const ind = d.indicators;
      // inventory_to_sales → inv_sales_ratio
      if (ind.inventory_to_sales && !ind.inv_sales_ratio) ind.inv_sales_ratio = ind.inventory_to_sales.history || ind.inventory_to_sales;
      // ism_new_orders → pmi_new_orders
      if (ind.ism_new_orders && !ind.pmi_new_orders) ind.pmi_new_orders = ind.ism_new_orders.history || ind.ism_new_orders;
      // pmi_inventory_diff → keep (already matches)
      if (ind.pmi_inventory_diff && !ind.pmi_inventory_diff.history) {
        // already raw array
      } else if (ind.pmi_inventory_diff?.history) {
        ind.pmi_inventory_diff = ind.pmi_inventory_diff.history;
      }
    }
  });

  // === 7. Merrill Clock: add signal, normalize indicator names, fix quadrant_history ===
  ['us', 'cn'].forEach(region => {
    const d = data.merrill_clock?.[region];
    if (!d) return;
    // Derive signal from quadrant
    const q = d.current_quadrant || '';
    if (q.includes('复苏') || q.toLowerCase().includes('recovery')) d.signal = 'bullish';
    else if (q.includes('过热') || q.toLowerCase().includes('overheat')) d.signal = 'neutral';
    else if (q.includes('滞胀') || q.toLowerCase().includes('stagflation')) d.signal = 'bearish';
    else if (q.includes('衰退') || q.toLowerCase().includes('recession')) d.signal = 'bearish';
    else d.signal = 'neutral';

    // Normalize indicator names and extract history arrays
    if (d.indicators) {
      const ind = d.indicators;
      // ppi_yoy → ppi
      if (ind.ppi_yoy && !ind.ppi) ind.ppi = ind.ppi_yoy;
      // breakeven_inflation → breakeven
      if (ind.breakeven_inflation && !ind.breakeven) ind.breakeven = ind.breakeven_inflation;
      // Extract .history from each indicator
      Object.keys(ind).forEach(key => {
        if (ind[key]?.history && Array.isArray(ind[key].history)) {
          // Keep the full object but also provide direct array access
          ind[key] = ind[key].history;
        }
      });
    }

    // Fix quadrant_history format
    if (d.quadrant_history) {
      d.quadrant_history = d.quadrant_history.map(h => ({
        ...h,
        period: h.start ? `${h.start}-${h.end || '至今'}` : h.period,
      }));
    }
  });

  // === 8. Credit Impulse: normalize indicator access ===
  const ci = data.credit_impulse;
  if (ci) {
    // Global
    if (ci.global?.indicators?.global_liquidity_index) {
      const gli = ci.global.indicators.global_liquidity_index;
      ci.global.liquidity_index = gli.history || gli;
    }

    // China
    if (ci.cn?.indicators) {
      const cnInd = ci.cn.indicators;
      // tsf_growth → tsrf_growth
      if (cnInd.tsf_growth && !cnInd.tsrf_growth) {
        cnInd.tsrf_growth = cnInd.tsf_growth.history || cnInd.tsf_growth;
      }
      // credit_impulse
      if (cnInd.credit_impulse) {
        cnInd.credit_impulse = cnInd.credit_impulse.history || cnInd.credit_impulse;
      }
      // m2_vs_gdp → split into m2_growth and nominal_gdp_growth
      if (cnInd.m2_vs_gdp) {
        const m2g = cnInd.m2_vs_gdp;
        if (m2g.m2_history) cnInd.m2_growth = m2g.m2_history;
        if (m2g.nominal_gdp_history) cnInd.nominal_gdp_growth = m2g.nominal_gdp_history;
      }
      // loan_structure → lt_loan_ratio
      if (cnInd.loan_structure && !cnInd.lt_loan_ratio) {
        cnInd.lt_loan_ratio = cnInd.loan_structure.history || cnInd.loan_structure;
      }
    }

    // US
    if (ci.us?.indicators) {
      const usInd = ci.us.indicators;
      if (usInd.hy_spread) usInd.hy_spread = usInd.hy_spread.history || usInd.hy_spread;
      if (usInd.fed_balance_sheet) {
        usInd.fed_assets = usInd.fed_balance_sheet.history || usInd.fed_balance_sheet;
      }
      if (usInd.bank_credit) usInd.bank_credit = usInd.bank_credit.history || usInd.bank_credit;
    }

    // Investment advice
    if (ci.global?.investment_advice && !ci.advice) {
      ci.advice = ci.global.investment_advice;
    }
    if (ci.global?.investment_usage && !ci.investment_usage) {
      ci.investment_usage = ci.global.investment_usage;
    }
  }

  return data;
}
