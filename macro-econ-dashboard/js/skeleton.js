/**
 * skeleton.js — 经济骨架 DAG 数据与渲染
 * 包含节点、边、详情信息，以及 vis-network 初始化
 */

// ==================== 骨架图数据 ====================

// 节点详情信息
const nodeDetails = {
  // —— 政策层 ——
  'cn_central_bank': {
    name: '央行/货币政策',
    desc: '中国人民银行通过利率、存款准备金率、公开市场操作等工具调节货币供应和信用扩张',
    upstream: ['通胀CPI/PPI', '就业', '经济增长GDP'],
    downstream: ['利率体系', '信用与流动性', '汇率'],
    lag: '反应时滞：1-3个月（从数据发布到政策响应）',
    indicators: { '7天逆回购利率': '政策利率方向', 'MLF利率': '中期流动性定价', 'RRR': '银行放贷能力', 'M2增速': '货币供应' },
    watchPoints: ['央行季度货币政策执行报告', 'MLF/逆回购操作量与利率', '社融增速是否匹配名义GDP', 'DR007与政策利率偏离度'],
  },
  'cn_government': {
    name: '政府/财政政策',
    desc: '中央与地方政府通过预算安排、专项债发行、减税降费等手段调控经济',
    upstream: ['经济增长GDP'],
    downstream: ['政府部门(执行)', '经济增长'],
    lag: '传导时滞：当季生效，数季见效',
    indicators: { '一般公共预算收支': '财政力度', '专项债发行进度': '基建发力节奏', '财政赤字率': '积极程度', '城投债净融资': '隐性财政' },
    watchPoints: ['两会预算目标与赤字率', '专项债提前批额度与发行节奏', '土地出让收入变化', '减税降费规模'],
  },
  'us_central_bank': {
    name: 'Federal Reserve',
    desc: '美联储通过联邦基金利率、量化宽松/紧缩、前瞻指引等工具调控美元流动性和信用环境',
    upstream: ['通胀CPI/PPI', '就业', '经济增长GDP'],
    downstream: ['利率体系', '信用与流动性', '汇率'],
    lag: '反应时滞：6-8周（FOMC会议周期）',
    indicators: { '联邦基金利率': '基准利率', 'Fed Balance Sheet': '量化操作', 'Dot Plot': '利率路径预期', 'IOER/ON RRP': '利率走廊' },
    watchPoints: ['FOMC声明与点阵图', 'Powell记者会措辞变化', 'TGA账户余额', 'RRP使用量反映流动性过剩/紧缺'],
  },
  'us_government': {
    name: 'Federal Government',
    desc: '联邦政府通过联邦预算、国债发行、税收政策、产业法案（如IRA/CHIPS Act）调控经济',
    upstream: ['经济增长GDP'],
    downstream: ['政府部门(执行)', '经济增长'],
    lag: '传导时滞：当季生效，数季~数年见效',
    indicators: { '联邦赤字/GDP': '财政力度', '国债净发行': '抽水效应', 'Discretionary Spending': '支出方向', 'Tax Receipts': '经济活力' },
    watchPoints: ['联邦预算决议与债务上限', '国债发行计划（拍卖规模）', 'IRA/CHIPS Act落地进度与支出', '个税退税进度'],
  },

  // —— 金融条件层 ——
  'interest_rate': {
    name: '利率体系',
    desc: '从政策利率到市场利率的完整传导链条，含短端流动性利率和长端国债收益率',
    upstream: ['央行/货币政策'],
    downstream: ['居民部门', '企业部门', '对外部门'],
    lag: '传导时滞：数月~1年',
    indicators: {
      '中国': { 'DR007': '短端流动性', '10Y国债收益率': '长端定价', 'LPR': '信贷基准', 'AAA城投债收益率': '信用定价' },
      '美国': { 'SOFR/Effective FF Rate': '短端', '10Y Treasury': '长端', '30Y Mortgage Rate': '房贷', 'BBB企业债收益率': '信用' },
    },
    watchPoints: ['收益率曲线形态（倒挂信号）', '实际利率vs名义利率', '信用利差（TED/BAFF）', '利率传导效率'],
  },
  'credit_liquidity': {
    name: '信用与流动性',
    desc: '银行体系信用扩张能力、非银流动性状况、社会融资规模与结构',
    upstream: ['央行/货币政策'],
    downstream: ['居民部门', '企业部门'],
    lag: '传导时滞：数周~数月',
    indicators: {
      '中国': { '社融存量增速': '信用总量', '新增人民币贷款': '信贷投放', 'M1/M2剪刀差': '资金活化', '非银存款': '脱实向虚' },
      '美国': { 'Bank Credit': '银行信贷', 'M2增速': '货币供应', 'H.8 Commercial Loans': '工商业贷款', 'Money Market AUM': '流动性' },
    },
    watchPoints: ['社融结构（政府债vs企业债vs居民贷款）', '票据融资占比（冲量vs实体需求）', '银行不良率与拨备', 'BTFP/TGA/RRP三角形'],
  },
  'exchange_rate': {
    name: '汇率',
    desc: '本币对外币的比价，受利差、贸易顺差/逆差、资本流动影响，反作用于进出口和资本流动',
    upstream: ['央行/货币政策'],
    downstream: ['对外部门', '企业部门'],
    lag: '传导时滞：数月',
    indicators: {
      '中国': { 'USDCNY中间价': '官方引导', 'CFETS指数': '一篮子汇率', '外汇储备': '干预能力', '北向资金流向': '资本流动' },
      '美国': { 'DXY美元指数': '全球定价锚', 'Trade-Weighted Dollar': '贸易加权', 'CIP Basis': '美元流动性溢价' },
    },
    watchPoints: ['逆周期因子使用迹象', '资本管制力度变化', '中美利差驱动方向', '美元周期与新兴市场压力'],
  },

  // —— 实体经济部门 ——
  'household': {
    name: '居民部门',
    desc: '消费者信心、收入增长、消费支出、住房购买、储蓄行为',
    upstream: ['利率体系', '信用与流动性'],
    downstream: ['经济增长GDP', '通胀CPI/PPI'],
    lag: '传导时滞：数月~1年',
    indicators: {
      '中国': { '社零总额增速': '消费', '居民可支配收入': '收入', '70城房价': '资产', '消费者信心指数': '预期' },
      '美国': { 'Real Disposable Income': '收入', 'PCE/Core PCE': '消费', 'Consumer Confidence': '信心', 'Mortgage Applications': '住房' },
    },
    watchPoints: ['储蓄率变化（超额储蓄消耗）', '消费信贷增速与违约率', '房价预期与销售', '就业收入预期'],
  },
  'corporate': {
    name: '企业部门',
    desc: '企业盈利、投资扩张、库存周期、融资需求',
    upstream: ['利率体系', '信用与流动性', '汇率'],
    downstream: ['经济增长GDP', '通胀CPI/PPI', '就业'],
    lag: '传导时滞：数月~1年',
    indicators: {
      '中国': { '工业企业利润增速': '盈利', '制造业投资增速': '投资', '产成品库存': '库存周期', 'PMI生产经营预期': '信心' },
      '美国': { 'EPS Growth (S&P 500)': '盈利', 'Core Capex Orders': '投资', 'ISM Inventory Index': '库存', 'NFIB Small Biz Optimism': '信心' },
    },
    watchPoints: ['库存周期位置（主动补/被动补/主动去/被动去）', '产能利用率', '企业债违约率与利差', '资本开支计划'],
  },
  'government_sector': {
    name: '政府部门(执行)',
    desc: '政府支出执行进度、基建投资、公共品供给、转移支付',
    upstream: ['政府/财政政策'],
    downstream: ['经济增长GDP'],
    lag: '传导时滞：当季~数季',
    indicators: {
      '中国': { '基建投资增速': '发力方向', '财政支出进度': '执行', '专项债资金使用': '落地', 'PSL投放': '准财政' },
      '美国': { 'Federal Outlays': '支出', 'Infrastructure Spending': '基建', 'Transfer Payments': '转移支付', 'Defense Spending': '国防' },
    },
    watchPoints: ['基建项目开工率与实物工作量', '专项债资金拨付进度', '政府消费与投资对GDP拉动', '财政支出乘数效应'],
  },
  'external_sector': {
    name: '对外部门',
    desc: '进出口贸易、跨境资本流动、国际收支平衡',
    upstream: ['利率体系', '汇率'],
    downstream: ['经济增长GDP', '通胀CPI/PPI'],
    lag: '传导时滞：数月',
    indicators: {
      '中国': { '出口增速': '外需', '进口增速': '内需', '贸易顺差': '收支', 'FDI/ODI': '资本流动', '外汇占款': '基础货币' },
      '美国': { 'Trade Balance': '贸易逆差', 'Goods vs Services Exports': '结构', 'BEA Net Foreign Investment': '资本流动' },
    },
    watchPoints: ['出口结构升级（机电/新能源占比）', '贸易伙伴多元化进度', '资本外流压力', '全球供应链重构影响'],
  },

  // —— 结果变量 ——
  'gdp': {
    name: '经济增长GDP',
    desc: '国内生产总值增速，衡量经济整体产出增长',
    upstream: ['居民部门', '企业部门', '政府部门(执行)', '对外部门'],
    downstream: ['就业', '通胀CPI/PPI'],
    lag: '公布频率：季度（中国月度有累计同比）',
    indicators: {
      '中国': { 'GDP当季同比': '总量', '三产业增速': '结构', '三驾马车贡献': '驱动', '季调环比折年': '动能' },
      '美国': { 'GDP QoQ SAAR': '总量', 'PCE Contribution': '消费', 'Gross Private Investment': '投资', 'Net Exports': '贸易' },
    },
    watchPoints: ['实际GDP vs 名义GDP（隐含平减指数）', '环比折年动能趋势', '产出缺口方向', '潜在增速估计'],
  },
  'inflation': {
    name: '通胀CPI/PPI',
    desc: '消费者价格指数和生产者价格指数，衡量通胀压力和价格传导',
    upstream: ['居民部门', '企业部门', '对外部门', '经济增长GDP'],
    downstream: ['央行/货币政策'],
    lag: '传导时滞：数月',
    indicators: {
      '中国': { 'CPI同比': '消费通胀', 'PPI同比': '生产通胀', '核心CPI': '剔除食品能源', 'PPI-CPI剪刀差': '传导' },
      '美国': { 'CPI YoY': '标题通胀', 'Core CPI': '核心', 'PCE/Core PCE': '美联储目标', 'PPI': '上游' },
    },
    watchPoints: ['核心通胀趋势（3m/6m年化）', '通胀预期（TIPS/密歇根调查）', '工资-通胀螺旋迹象', '商品vs服务通胀分化'],
  },
  'employment': {
    name: '就业',
    desc: '就业人数、失业率、工资增速，衡量劳动力市场松紧',
    upstream: ['企业部门', '经济增长GDP'],
    downstream: ['央行/货币政策'],
    lag: '传导时滞：数月',
    indicators: {
      '中国': { '城镇调查失业率': '总体', '31大城市失业率': '核心城市', '青年失业率': '结构性', '新增就业': '增量' },
      '美国': { 'Nonfarm Payrolls': '新增就业', 'Unemployment Rate': '失业率', 'Labor Force Participation': '参与率', 'AHE工资增速': '工资' },
    },
    watchPoints: ['薪资增速vs通胀（实际工资）', '劳动参与率变化', '职位空缺/求职者比值', '临时裁员vs永久失业'],
  },
};

// 单国节点定义（中国/美国共用结构，通过country前缀区分）
function buildNodes(country) {
  const prefix = country === 'china' ? 'cn' : 'us';
  const colorMap = {
    policy: { bg: '#ef4444', border: '#dc2626', font: '#fff' },      // 红
    finance: { bg: '#f59e0b', border: '#d97706', font: '#fff' },      // 黄
    real: { bg: '#10b981', border: '#059669', font: '#fff' },         // 绿
    result: { bg: '#3b82f6', border: '#2563eb', font: '#fff' },       // 蓝
  };

  const nodes = [
    // 政策层
    { id: `${prefix}_central_bank`, label: `${country === 'china' ? '央行' : 'Fed'}\n货币政策`, group: 'policy', level: 0 },
    { id: `${prefix}_government`, label: `${country === 'china' ? '政府' : 'Federal Gov'}\n财政政策`, group: 'policy', level: 0 },
    // 金融条件层
    { id: `${prefix}_interest_rate`, label: '利率体系', group: 'finance', level: 1 },
    { id: `${prefix}_credit_liquidity`, label: '信用与流动性', group: 'finance', level: 1 },
    { id: `${prefix}_exchange_rate`, label: '汇率', group: 'finance', level: 1 },
    // 实体经济
    { id: `${prefix}_household`, label: '居民部门', group: 'real', level: 2 },
    { id: `${prefix}_corporate`, label: '企业部门', group: 'real', level: 2 },
    { id: `${prefix}_government_sector`, label: '政府(执行)', group: 'real', level: 2 },
    { id: `${prefix}_external_sector`, label: '对外部门', group: 'real', level: 2 },
    // 结果变量
    { id: `${prefix}_gdp`, label: 'GDP\n经济增长', group: 'result', level: 3 },
    { id: `${prefix}_inflation`, label: 'CPI/PPI\n通胀', group: 'result', level: 3 },
    { id: `${prefix}_employment`, label: '就业', group: 'result', level: 3 },
  ];

  // 为节点添加颜色
  return nodes.map(n => {
    const c = colorMap[n.group];
    // 使用不带前缀的key查详情
    const detailKey = n.id.replace(`${prefix}_`, '');
    return {
      id: n.id,
      label: n.label,
      detailKey: detailKey,
      color: { background: c.bg, border: c.border, font: { color: c.font } },
      shape: 'box',
      font: { size: 14, face: 'Inter', multi: true },
      margin: 12,
      widthConstraint: { minimum: 100, maximum: 160 },
    };
  });
}

// 单国边定义
function buildEdges(country) {
  const prefix = country === 'china' ? 'cn' : 'us';
  const edges = [
    // 央行 → 金融
    { from: `${prefix}_central_bank`, to: `${prefix}_interest_rate`, label: '即时~数周' },
    { from: `${prefix}_central_bank`, to: `${prefix}_credit_liquidity`, label: '数周~月' },
    { from: `${prefix}_central_bank`, to: `${prefix}_exchange_rate`, label: '即时~月' },
    // 政府 → 实体
    { from: `${prefix}_government`, to: `${prefix}_government_sector`, label: '当季' },
    { from: `${prefix}_government`, to: `${prefix}_gdp`, label: '数季~年' },
    // 金融 → 实体
    { from: `${prefix}_interest_rate`, to: `${prefix}_household`, label: '数月~年' },
    { from: `${prefix}_interest_rate`, to: `${prefix}_corporate`, label: '数月~年' },
    { from: `${prefix}_interest_rate`, to: `${prefix}_external_sector`, label: '数月' },
    { from: `${prefix}_credit_liquidity`, to: `${prefix}_household`, label: '数月' },
    { from: `${prefix}_credit_liquidity`, to: `${prefix}_corporate`, label: '数月~年' },
    { from: `${prefix}_exchange_rate`, to: `${prefix}_external_sector`, label: '数月' },
    { from: `${prefix}_exchange_rate`, to: `${prefix}_corporate`, label: '数月' },
    // 实体 → 结果
    { from: `${prefix}_household`, to: `${prefix}_gdp`, label: '当季' },
    { from: `${prefix}_corporate`, to: `${prefix}_gdp`, label: '当季' },
    { from: `${prefix}_government_sector`, to: `${prefix}_gdp`, label: '当季~季' },
    { from: `${prefix}_external_sector`, to: `${prefix}_gdp`, label: '当季' },
    { from: `${prefix}_household`, to: `${prefix}_inflation`, label: '数月' },
    { from: `${prefix}_corporate`, to: `${prefix}_inflation`, label: '数月~年' },
    { from: `${prefix}_external_sector`, to: `${prefix}_inflation`, label: '数月' },
    { from: `${prefix}_corporate`, to: `${prefix}_employment`, label: '数月' },
    { from: `${prefix}_gdp`, to: `${prefix}_employment`, label: '数月' },
    { from: `${prefix}_gdp`, to: `${prefix}_inflation`, label: '数月' },
  ];

  return edges.map(e => ({
    ...e,
    arrows: 'to',
    color: { color: '#4b5563', highlight: '#3b82f6' },
    font: { size: 10, color: '#6b7280', face: 'JetBrains Mono' },
    label: e.label,
    smooth: { type: 'curvedCW', roundness: 0.15 },
  }));
}

// 联动视图：两国叠加 + 跨境连接
function buildCrossNodes() {
  const cnNodes = buildNodes('china').map(n => ({ ...n, id: n.id, label: `🇨🇳 ${n.label}` }));
  const usNodes = buildNodes('us').map(n => ({ ...n, id: n.id, label: `🇺🇸 ${n.label}` }));
  return [...cnNodes, ...usNodes];
}

function buildCrossEdges() {
  const cnEdges = buildEdges('china');
  const usEdges = buildEdges('us');
  // 跨境连接
  const crossEdges = [
    { from: 'cn_exchange_rate', to: 'us_exchange_rate', label: '利差驱动', color: { color: '#f59e0b', highlight: '#fbbf24' } },
    { from: 'cn_external_sector', to: 'us_external_sector', label: '贸易链路', color: { color: '#f59e0b', highlight: '#fbbf24' } },
    { from: 'cn_interest_rate', to: 'us_interest_rate', label: '中美利差', color: { color: '#f59e0b', highlight: '#fbbf24' } },
    { from: 'cn_corporate', to: 'us_corporate', label: '供应链', color: { color: '#f59e0b', highlight: '#fbbf24' } },
  ];
  return [...cnEdges, ...usEdges, ...crossEdges.map(e => ({ ...e, arrows: 'to', dashes: true, font: { size: 10, color: '#f59e0b', face: 'JetBrains Mono' }, smooth: { type: 'curvedCW', roundness: 0.2 } }))];
}

// ==================== vis-network 渲染 ====================

let skeletonNetwork = null;

// 层级布局：按 level 分层排列
function getHierarchicalLayout() {
  return {
    hierarchical: {
      direction: 'LR',  // 从左到右
      sortMethod: 'directed',
      levelSeparation: 280,
      nodeSpacing: 100,
      treeSpacing: 160,
      blockShifting: true,
      edgeMinimization: true,
      parentCentralization: false,
    },
  };
}

function initSkeletonNetwork(view = 'china') {
  const container = document.getElementById('skeleton-network');
  if (!container) return;

  let nodes, edges;
  if (view === 'china') {
    nodes = buildNodes('china');
    edges = buildEdges('china');
  } else if (view === 'us') {
    nodes = buildNodes('us');
    edges = buildEdges('us');
  } else {
    nodes = buildCrossNodes();
    edges = buildCrossEdges();
  }

  const data = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  };

  const options = {
    layout: getHierarchicalLayout(),
    physics: {
      enabled: view === 'cross', // 联动视图用物理引擎
      barnesHut: {
        gravitationalConstant: -8000,
        centralGravity: 0.3,
        springLength: 150,
        springConstant: 0.04,
        damping: 0.4,
      },
    },
    nodes: {
      shape: 'box',
      margin: 12,
      widthConstraint: { minimum: 100, maximum: 160 },
      font: { size: 14, face: 'Inter', multi: true },
      borderWidth: 2,
      shadow: { enabled: true, color: 'rgba(0,0,0,0.3)', size: 8, x: 2, y: 2 },
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
      smooth: { type: 'curvedCW', roundness: 0.15 },
      width: 1.5,
      color: { color: '#4b5563', highlight: '#3b82f6', hover: '#6b7280' },
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
      zoomView: true,
      dragView: true,
    },
  };

  if (skeletonNetwork) {
    skeletonNetwork.destroy();
  }
  skeletonNetwork = new vis.Network(container, data, options);

  // 点击节点显示详情
  skeletonNetwork.on('click', function(params) {
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0];
      const node = nodes.find(n => n.id === nodeId);
      if (node && node.detailKey) {
        renderNodeDetail(node.detailKey);
      }
    }
  });
}

// 渲染节点详情面板
function renderNodeDetail(key) {
  const detail = nodeDetails[key];
  const panel = document.getElementById('skeleton-detail');
  if (!detail) {
    panel.innerHTML = '<p class="text-gray-500 text-sm">暂无详情信息</p>';
    return;
  }

  // 构建指标表格
  let indicatorsHtml = '';
  const inds = detail.indicators;
  if (inds['中国'] || inds['美国']) {
    // 双国指标
    indicatorsHtml = `
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="text-xs text-red-400 font-semibold mb-1">🇨🇳 中国</div>
          ${inds['中国'] ? Object.entries(inds['中国']).map(([k, v]) => `<div class="text-xs"><span class="text-gray-400">${k}</span>: <span class="text-gray-300">${v}</span></div>`).join('') : '<div class="text-xs text-gray-600">—</div>'}
        </div>
        <div>
          <div class="text-xs text-blue-400 font-semibold mb-1">🇺🇸 美国</div>
          ${inds['美国'] ? Object.entries(inds['美国']).map(([k, v]) => `<div class="text-xs"><span class="text-gray-400">${k}</span>: <span class="text-gray-300">${v}</span></div>`).join('') : '<div class="text-xs text-gray-600">—</div>'}
        </div>
      </div>
    `;
  } else {
    indicatorsHtml = Object.entries(inds).map(([k, v]) => 
      `<div class="text-xs"><span class="text-gray-400">${k}</span>: <span class="text-gray-300">${v}</span></div>`
    ).join('');
  }

  panel.innerHTML = `
    <h3 class="text-base font-semibold text-white mb-2">${detail.name}</h3>
    <p class="text-xs text-gray-400 leading-relaxed mb-4">${detail.desc}</p>
    
    <div class="mb-3">
      <div class="text-xs font-semibold text-gray-500 mb-1">⬆ 上游</div>
      <div class="flex flex-wrap gap-1">
        ${detail.upstream.map(u => `<span class="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">${u}</span>`).join('')}
      </div>
    </div>
    
    <div class="mb-3">
      <div class="text-xs font-semibold text-gray-500 mb-1">⬇ 下游</div>
      <div class="flex flex-wrap gap-1">
        ${detail.downstream.map(d => `<span class="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-400">${d}</span>`).join('')}
      </div>
    </div>
    
    <div class="mb-3">
      <div class="text-xs font-semibold text-gray-500 mb-1">⏱ 时滞</div>
      <div class="text-xs text-gray-300">${detail.lag}</div>
    </div>
    
    <div class="mb-3">
      <div class="text-xs font-semibold text-gray-500 mb-2">📈 关键指标</div>
      ${indicatorsHtml}
    </div>
    
    <div>
      <div class="text-xs font-semibold text-gray-500 mb-1">👀 观察要点</div>
      <ul class="text-xs text-gray-300 space-y-1 list-disc list-inside">
        ${detail.watchPoints.map(w => `<li>${w}</li>`).join('')}
      </ul>
    </div>
  `;
}
