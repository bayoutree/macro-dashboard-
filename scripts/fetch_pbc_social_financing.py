#!/usr/bin/env python3
"""央行官方社会融资规模采集（存量+增量，逐月）。

数据源: 中国人民银行 调查统计司 -> 统计数据 -> 社会融资规模
入口: https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html

产出: {'stock': [{date,value}...], 'flow': [{date,value}...]} 升序月序列
单位: 存量=万亿元（央行原表单位）; 增量=亿元（央行原表单位）
"""
import re, io, ssl, urllib.request
import openpyxl
import xlrd  # P1-10b: 2016-2019 存量表为 .xls 旧格式

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
BASE = "https://www.pbc.gov.cn"
STAT_INDEX = BASE + "/diaochatongjisi/116219/116319/index.html"

_A_HREF = re.compile(r"""<a\s+href=['"]([^'"]+)['"][^>]*>\s*([^<]{0,40}?)\s*</a>""")
_MONTH_RE = re.compile(r'^(\d{4})\.(\d{1,2})$')


def _http(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def discover_sf_pages(max_years=4):  # max_years=None 全量发现（回填模式）
    h = _http(STAT_INDEX).decode('utf-8', 'ignore')
    year_pages = {}
    for m in _A_HREF.finditer(h):
        u, t = m.group(1), m.group(2)
        mm = re.match(r'^(\d{4})年统计数据$', t)
        if mm:
            year_pages[int(mm.group(1))] = BASE + u if u.startswith('/') else u
    cf = {}
    _years = sorted(year_pages) if max_years is None else sorted(year_pages)[-max_years:]
    for y in _years:
        try:
            hh = _http(year_pages[y]).decode('utf-8', 'ignore')
        except Exception:
            continue
        for m in _A_HREF.finditer(hh):
            u, t = m.group(1), m.group(2)
            if t.startswith('社会融资规模'):
                cf[y] = BASE + u if u.startswith('/') else u
                break
    return cf


def discover_xlsx(sf_page_url):
    h = _http(sf_page_url).decode('utf-8', 'ignore')
    out = []
    for m in re.finditer(r"""href=['"]([^'"]+\.xlsx?)['"]""", h, re.I):  # P1-10b: 2016-2019 为 .xls
        u = m.group(1)
        out.append(BASE + u if u.startswith('/') else u)
    return out


def _month_cols(row):
    cols = {}
    for j, v in enumerate(row):
        if v is None:
            continue
        mm = _MONTH_RE.match(str(v).strip())
        if mm:
            cols[j] = f"{mm.group(1)}-{int(mm.group(2)):02d}"
    return cols


def parse_xlsx(raw):
    """返回 (kind, values, growth)。growth=存量表自带官方增速列（P1-10b，口径官方衔接）。"""
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    wb.close()
    if not rows:
        return None, {}, {}
    title = str(rows[0][0] or '')
    has_stock_row = any(str((r[0] if r else '') or '').strip() == '社会融资规模存量' for r in rows)
    if '存量统计表' in title or has_stock_row:
        kind = 'stock'
    elif '增量统计表' in title:
        kind = 'flow'
    else:
        kind = 'flow'

    out, growth = {}, {}
    month_row, cols = None, {}
    for i, r in enumerate(rows[:12]):
        c = _month_cols(r)
        if len(c) >= 4:
            month_row, cols = i, c
            break
    if cols:
        target = '社会融资规模存量' if kind == 'stock' else '社会融资规模增量'
        for r in rows:
            if str((r[0] if r else '') or '').strip() == target:
                for j, d in cols.items():
                    if j < len(r) and isinstance(r[j], (int, float)):
                        out[d] = float(r[j])
                    if kind == 'stock' and j + 1 < len(r) and isinstance(r[j + 1], (int, float)):
                        growth[d] = float(r[j + 1])
                break
    if not out:
        for r in rows:
            if not r or r[0] is None:
                continue
            mm = _MONTH_RE.match(str(r[0]).strip())
            if mm:
                val = r[1] if len(r) > 1 else None
                if isinstance(val, (int, float)):
                    out[f"{mm.group(1)}-{int(mm.group(2)):02d}"] = float(val)
    return kind, out, growth


def parse_xls(raw):
    """xlrd 解析 .xls 旧格式（2016-2019 存量/增量表），行列扫描逻辑与 parse_xlsx 一致。
    实测 2016 存量表：行4 月份表头（2016.1@列1，每月占2列：存量+官方增速），行8 目标行。"""
    wb = xlrd.open_workbook(file_contents=raw)
    ws = wb.sheet_by_index(0)
    rows = [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]
    if not rows:
        return None, {}, {}
    title = str(rows[0][0] or '')
    has_stock_row = any(str((r[0] if r else '') or '').strip() == '社会融资规模存量' for r in rows)
    if '存量统计表' in title or has_stock_row:
        kind = 'stock'
    elif '增量统计表' in title:
        kind = 'flow'
    else:
        kind = 'flow'
    out, growth = {}, {}
    month_row, cols = None, {}
    for i, r in enumerate(rows[:12]):
        c = _xls_month_cols(r)
        if len(c) >= 4:
            month_row, cols = i, c
            break
    if cols:
        target = '社会融资规模存量' if kind == 'stock' else '社会融资规模增量'
        for r in rows:
            if str((r[0] if r else '') or '').strip() == target:
                for j, d in cols.items():
                    if j < len(r):
                        nv = _xls_num(r[j])
                        if nv is not None:
                            out[d] = nv
                    if kind == 'stock' and j + 1 < len(r):
                        gv = _xls_num(r[j + 1])
                        if gv is not None:
                            growth[d] = gv
                break
    if not out:
        for r in rows:
            if not r or r[0] is None:
                continue
            mm = _MONTH_RE.match(str(r[0]).strip())
            if mm:
                nv = _xls_num(r[1]) if len(r) > 1 else None
                if nv is not None:
                    out[f"{mm.group(1)}-{int(mm.group(2)):02d}"] = nv
    return kind, out, growth


def _xls_num(v):
    """.xls 值规范化：2018 表存量为文本格式数字（'185.77'），2016 表为 float。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(',', '').replace(chr(160), '').strip())
        except ValueError:
            return None
    return None


def _xls_month_cols(row):
    """.xls 月份表头解析（撞键安全）。
    数字格式下 2016.1 与 2016.10 同为 float 2016.1（1月/10月不可区分）——
    年度矩阵表月份列按列号单调递增，据此递推重建 1..N 月，并用非歧义项
    （文本型或 2-9/11/12 月）交叉验证位置一致性，验证失败返回空。"""
    hits = []  # (col, year, month|None)
    for j, v in enumerate(row):
        if isinstance(v, str):
            mm = _MONTH_RE.match(v.strip())
            if mm:
                hits.append((j, int(mm.group(1)), int(mm.group(2))))
        elif isinstance(v, float) and 1990 < v < 2100:
            frac2 = round((v - int(v)) * 100 + 1e-6)
            if frac2 in (11, 12):
                hits.append((j, int(v), frac2))
            elif 10 <= frac2 <= 90 and frac2 % 10 == 0:
                hits.append((j, int(v), frac2 // 10 if frac2 > 10 else None))  # 10=歧义(1或10)
    if len(hits) < 4 or len({y for _, y, _ in hits}) != 1:
        return {}
    hits.sort()
    y = hits[0][1]
    for k, (_, _, m) in enumerate(hits):
        if m is not None and m != k + 1:
            return {}  # 非歧义项与列序递推冲突，结构不符年度矩阵
    return {j: f"{y}-{k + 1:02d}" for k, (j, _, _) in enumerate(hits)}


def parse_any(raw, url):
    """按文件后缀分发解析器（.xls 旧格式走 xlrd，.xlsx 走 openpyxl）。"""
    return parse_xls(raw) if url.lower().endswith('.xls') else parse_xlsx(raw)


def fetch_social_financing(years=None):
    """抓取并合并多年社融数据（默认最近 4 年，足够覆盖 13+ 月同比）。

    健壮性：目录发现失败返回空结构而非抛异常；单年/单文件失败仅跳过该部分，
    已成功抓取的数据照常返回（部分成功优于整体失败）。
    """
    try:
        pages = discover_sf_pages(max_years=None if years else 4)
    except Exception:
        return {'stock': [], 'flow': []}
    if years is None:
        years = sorted(pages.keys())
    stock, flow, stock_yoy = {}, {}, {}
    for y in years:
        if y not in pages:
            continue
        try:
            xs = discover_xlsx(pages[y])
        except Exception:
            continue
        for x in xs:
            try:
                kind, data, growth = parse_any(_http(x), x)
            except Exception:
                continue
            if kind == 'stock':
                stock.update(data)
                stock_yoy.update(growth)
            elif kind == 'flow':
                flow.update(data)
    to_list = lambda d: [{'date': k, 'value': v} for k, v in sorted(d.items())]
    return {'stock': to_list(stock), 'flow': to_list(flow),
            'stock_yoy_official': to_list(stock_yoy)}


if __name__ == '__main__':
    r = fetch_social_financing()
    print('stock n=', len(r['stock']), r['stock'][:1], r['stock'][-1:])
    print('flow  n=', len(r['flow']), r['flow'][:1], r['flow'][-1:])
    for d in r['stock'][-4:]:
        print('S', d)
    for d in r['flow'][-4:]:
        print('F', d)
