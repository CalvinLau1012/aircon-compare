# -*- coding: utf-8 -*-
"""Release runtime smoke（server staging / 部署後 HTTP 驗收；E4 證據）

用法：
  python release/runtime_smoke.py --base http://127.0.0.1:8080 [--artifacts-dir <dir>]

- 唔硬編記錄數：以伺服器 metadata.json 為事實源，驗證頁面顯示同能源表一致。
- 能源表用語義定位（h2 → 後續兄弟節點內所有 table），唔用脆弱 XPath。
- --artifacts-dir：同時比對 index/PDF/CSV 由 HTTP 讀到嘅 bytes 同 staging 檔案一致。
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ENERGY_TABLES_JS = """() => {
  const h = [...document.querySelectorAll('h2')].find(e => e.textContent.includes('能源標籤分析'));
  if (!h) return {error: 'h2 能源標籤分析 not found'};
  const tables = [];
  const siblings = [];
  let n = h.nextElementSibling;
  while (n && n.tagName !== 'H2') {
    siblings.push(n.tagName + (n.className ? '.' + String(n.className).split(' ')[0] : ''));
    if (n.tagName === 'TABLE') tables.push(n);
    else tables.push(...n.querySelectorAll('table'));
    n = n.nextElementSibling;
  }
  return {
    count: tables.length,
    siblingTags: siblings,
    tables: tables.map(t => [...t.querySelectorAll('tr')].map(r =>
      [...r.querySelectorAll('th,td')].map(c => c.textContent.trim()))),
  };
}"""

CONTRAST_JS = '''(selectors) => {
  const parse = c => { const m = (c || '').match(/[\\d.]+/g) || []; const n = m.map(Number);
    return [n[0] || 0, n[1] || 0, n[2] || 0, n.length > 3 ? n[3] : 1]; };
  const blend = (fg, bg) => { const a = fg[3];
    return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1]; };
  const lum = c => { const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
  const bgOf = e => { const chain = []; let n = e;
    while (n) { const c = parse(getComputedStyle(n).backgroundColor); if (c[3] > 0) chain.push(c); n = n.parentElement; }
    let bg = [255, 255, 255, 1]; for (const c of chain.reverse()) bg = blend(c, bg); return bg; };
  const out = {};
  for (const [name, sel] of Object.entries(selectors)) {
    const e = document.querySelector(sel);
    if (!e) { out[name] = null; continue; }
    out[name] = +ratio(parse(getComputedStyle(e).color), bgOf(e)).toFixed(2);
  }
  return out;
}'''

results = {'checks': [], 'failures': []}


def check(name, cond, detail=None):
    results['checks'].append({'check': name, 'pass': bool(cond), 'detail': detail})
    if not cond:
        results['failures'].append({'check': name, 'detail': detail})
    print(('PASS ' if cond else 'FAIL ') + name + (f' :: {detail}' if detail is not None else ''))
    return bool(cond)


def http_get(base, path, timeout=30):
    req = urllib.request.Request(base + path, headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), time.time()


def num(s):
    return int(str(s).replace(',', ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--artifacts-dir', default=None)
    args = ap.parse_args()
    base = args.base.rstrip('/')

    status, body, _ = http_get(base, '/metadata.json')
    meta = json.loads(body.decode('utf-8'))
    check('metadata.http_200', status == 200)
    print('   metadata:', {k: meta.get(k) for k in ('version', 'build', 'commit', 'datasetDate',
                                                    'recordCount', 'registrationCount', 'modelCount')})
    for path, name in (('/index.html', 'index.html'),
                       ('/%E7%A9%BA%E8%AA%BF%E5%B0%8D%E6%AF%94%E5%A0%B1%E5%91%8A.pdf', '空調對比報告.pdf'),
                       ('/emsd_%E7%A9%BA%E8%AA%BF%E8%83%BD%E6%BA%90%E6%A8%99%E7%B1%A4.csv', 'emsd_空調能源標籤.csv')):
        try:
            st, blob, _ = http_get(base, path)
            check(f'http.{name}', st == 200)
            if args.artifacts_dir:
                local = open(f'{args.artifacts_dir}/{name}', 'rb').read()
                check(f'bytes.{name}', blob == local,
                      {'server': hashlib.sha256(blob).hexdigest()[:16],
                       'staged': hashlib.sha256(local).hexdigest()[:16]})
        except urllib.error.HTTPError as e:
            check(f'http.{name}', False, e.code)

    console_errors, page_errors, failed, bad = [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme='light')
        page = ctx.new_page()
        page.on('console', lambda m: console_errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
        page.on('pageerror', lambda exc: page_errors.append(str(exc)))
        page.on('requestfailed', lambda r: failed.append(f'{r.method} {r.url} {r.failure}'))
        page.on('response', lambda r: bad.append(f'{r.status} {r.url}') if r.status >= 400 else None)
        resp = page.goto(base + '/index.html', wait_until='networkidle', timeout=60000)
        check('browser.index_200', bool(resp) and resp.status == 200, resp.status if resp else None)
        page.wait_for_timeout(600)

        ver = page.locator('#verInfo').inner_text().strip()
        deploy = page.locator('#deployInfo').inner_text().strip()
        foot = page.locator('#footStatus').inner_text().strip()
        check('ui.version', ver == 'v' + meta['version'], ver)
        check('ui.last_update', meta['datasetDate'] in foot, foot)
        check('ui.last_deploy', '最後部署' in deploy and 'HKT' in deploy, deploy)

        energy = page.evaluate(ENERGY_TABLES_JS)
        check('energy.two_tables', energy.get('count') == 2, energy.get('siblingTags'))
        if energy.get('count') == 2:
            core_rows, full_rows = {}, {}
            for row in energy['tables'][0][1:]:
                core_rows[row[0].replace('級', '').replace(' ', '')] = num(row[1])
            for row in energy['tables'][1][1:]:
                full_rows[row[0].replace('級', '').replace(' ', '')] = [num(c) for c in row[1:]]
            check('energy.core.levels_1_5', set(core_rows) == {'1', '2', '3', '4', '5'}, core_rows)
            check('energy.core.total29', sum(core_rows.values()) == 29, sum(core_rows.values()))
            check('energy.full.total_model', full_rows.get('合計', [None, None])[1] == meta.get('modelCount'),
                  {'table': full_rows.get('合計'), 'metadata': meta.get('modelCount')})
            check('energy.full.total_registration',
                  full_rows.get('合計', [None, None, None])[2] == meta.get('registrationCount'),
                  {'table': full_rows.get('合計'), 'metadata': meta.get('registrationCount')})

        page.fill('#q', '日立')
        page.evaluate('resetShown();renderList()')
        names = page.evaluate("() => [...document.querySelectorAll('.mitem .info .name')].map(e=>e.textContent.trim())")
        check('search.hitachi', bool(names) and all('日立' in n or 'HITACHI' in n for n in names), names[:3])
        page.fill('#q', '')
        page.select_option('#fBrand', 'Gree 格力')
        page.evaluate('resetShown();renderList()')
        names = page.evaluate("() => [...document.querySelectorAll('.mitem .info .name')].map(e=>e.textContent.trim())")
        check('filter.gree', bool(names) and all('Gree' in n for n in names), names[:3])
        page.select_option('#fBrand', '')
        page.select_option('#sortBy', 'price')
        page.evaluate('resetShown();renderList()')
        prices = page.evaluate("() => [...document.querySelectorAll('.mitem .plink')].map(e => {"
                               "const s=e.textContent.split('🔍')[0].replace(/[^0-9]/g,'');"
                               "return s?parseInt(s.slice(0,-4)||s.slice(0,4),10)||999999:999999;})")
        check('sort.price', prices == sorted(prices), prices[:5])
        page.select_option('#sortBy', '')
        page.evaluate('resetShown();renderList()')

        page.evaluate('clearAll()')
        inputs = page.locator('.mitem input')
        inputs.nth(0).check()
        inputs.nth(1).check()
        page.click('#btnCompare')
        page.wait_for_timeout(250)
        check('compare.open', page.evaluate("document.getElementById('panel').classList.contains('open')"))
        check('compare.rows', page.locator('#panel table tr').count() >= 3)
        check('compare.focus', page.evaluate('document.getElementById("panel").contains(document.activeElement)'))
        page.keyboard.press('Escape')
        page.wait_for_timeout(200)
        check('compare.escape', not page.evaluate("document.getElementById('panel').classList.contains('open')"))
        check('compare.focus_return', page.evaluate('document.activeElement.id') == 'btnCompare')
        page.evaluate('clearAll()')

        for w, h in ((320, 568), (375, 667), (768, 1024), (1024, 768)):
            page.set_viewport_size({'width': w, 'height': h})
            page.wait_for_timeout(80)
            of = page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
            check(f'responsive.{w}', not of)
        page.set_viewport_size({'width': 1280, 'height': 900})

        selectors = {'footer_text': 'footer', 'footer_blk_p': 'footer .blk p', 'footer_blk_a': 'footer .blk a',
                     'footer_blk_h3': 'footer .blk h3', 'plink': '.plink', 'h3': '.md-content h3',
                     'gocompare': '.compare-tools .gocompare'}
        for scheme in ('light', 'dark'):
            c2 = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme=scheme)
            pg2 = c2.new_page()
            pg2.goto(base + '/index.html', wait_until='networkidle')
            pg2.wait_for_timeout(400)
            for name, r in pg2.evaluate(CONTRAST_JS, selectors).items():
                check(f'contrast.{scheme}.{name}', r is not None and r >= 4.5, r)
            c2.close()

        ctx.close()
        browser.close()

    check('browser.no_console_error', not console_errors, console_errors[:3])
    check('browser.no_page_error', not page_errors, page_errors[:3])
    check('browser.no_failed_request', not failed, failed[:3])
    check('browser.no_http_error', not bad, bad[:3])

    passed = not results['failures']
    print('\n=== RESULT:', 'PASS' if passed else 'FAIL',
          f'({sum(c["pass"] for c in results["checks"])}/{len(results["checks"])} checks) ===')
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
