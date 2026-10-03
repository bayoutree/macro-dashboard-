#!/usr/bin/env python3
"""央行官方社会融资规模采集（存量+增量，逐月）。

数据源: 中国人民银行 调查统计司 -> 统计数据 -> 社会融资规模
入口: https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html

产出: {'stock': [{date,value}...], 'flow': [{date,value}...]} 升序月序列
单位: 存量=万亿元（央行原表单位）; 增量=亿元（央行原表单位）
"""
import re, io, ssl, urllib.request
import openpyxl

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


def discover_sf_pages():
    h = _http(STAT_INDEX).decode('utf-8', 'ignore')
    year_pages = {}
    for m in _A_HREF.finditer(h):
        u, t = m.group(1), m.group(2)
        mm = re.match(r'^(\d{4})年统计数据$', t)
        if mm:
            year_pages[int(mm.group(1))] = BASE + u if u.startswith('/') else u
    cf = {}
    for y in sorted(year_pages)[-4:]:
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
    for m in re.finditer(r"""href=['"]([^'"]+\.xlsx)['"]""", h, re.I):
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
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    wb.close()
    if not rows:
        return None, {}
    title = str(rows[0][0] or '')
    has_stock_row = any(str((r[0] if r else '') or '').strip() == '社会融资规模存量' for r in rows)
    if '存量统计表' in title or has_stock_row:
        kind = 'stock'
    elif '增量统计表' in title:
        kind = 'flow'
    else:
        kind = 'flow'

    out = {}
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
    return kind, out


def fetch_social_financing(years=None):
    """抓取并合并多年社融数据（默认最近 4 年，足够覆盖 13+ 月同比）。

    健壮性：目录发现失败返回空结构而非抛异常；单年/单文件失败仅跳过该部分，
    已成功抓取的数据照常返回（部分成功优于整体失败）。
    """
    try:
        pages = discover_sf_pages()
    except Exception:
        return {'stock': [], 'flow': []}
    if years is None:
        years = sorted(pages.keys())
    stock, flow = {}, {}
    for y in years:
        if y not in pages:
            continue
        try:
            xs = discover_xlsx(pages[y])
        except Exception:
            continue
        for x in xs:
            try:
                kind, data = parse_xlsx(_http(x))
            except Exception:
                continue
            if kind == 'stock':
                stock.update(data)
            elif kind == 'flow':
                flow.update(data)
    to_list = lambda d: [{'date': k, 'value': v} for k, v in sorted(d.items())]
    return {'stock': to_list(stock), 'flow': to_list(flow)}


if __name__ == '__main__':
    r = fetch_social_financing()
    print('stock n=', len(r['stock']), r['stock'][:1], r['stock'][-1:])
    print('flow  n=', len(r['flow']), r['flow'][:1], r['flow'][-1:])
    for d in r['stock'][-4:]:
        print('S', d)
    for d in r['flow'][-4:]:
        print('F', d)
