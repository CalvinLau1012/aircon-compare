# -*- coding: utf-8 -*-
"""瀏覽器核心路徑 smoke 測試（E2 行為證據，Registry required 功能綁定）

用 Playwright 打開生成嘅 index.html，驗證：
- core.search / core.filter / core.sort（比較器列表操作）
- core.compare / ui.comparison-modal（揀機 → 對比面板，含 Escape/焦點）
- ui.responsive（手機／平板斷點無水平溢出）
- operations.version-display / last-deploy / dataset-update（metadata.json 讀取）
- 基本對比度（深色模式 + 既有淺色元件）

本地：pip install playwright && playwright install chromium
CI：workflow 已有 smoke 步驟
"""
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, 'index.html')
if not os.path.exists(INDEX):
    INDEX = os.path.join(ROOT, '空調對比報告.html')

playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import sync_playwright  # noqa: E402

URL = 'file:///' + INDEX.replace('\\', '/')


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope='module')
def page(browser):
    pg = browser.new_page(viewport={'width': 1280, 'height': 900})
    pg.goto(URL)
    yield pg
    pg.close()


@pytest.fixture(scope='module')
def http_base():
    """本地 HTTP 服務：file:// 唔可以 fetch metadata.json，metadata 測試要用 HTTP"""
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=ROOT, **kwargs)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


def _shown_names(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.mitem .info .name')].map(e => e.textContent.trim())")


def _reset_controls(page):
    page.evaluate('''() => {
      document.getElementById('fSelOnly').checked = false;
      for (const id of ['q','fBrand','fMount','fHp','fType','fEnergy','fStatus','fPrice','sortBy']) {
        document.getElementById(id).value = '';
      }
      clearAll(); resetShown(); renderList();
    }''')


def test_search_models(page):
    page.fill('#q', '日立')
    page.evaluate('resetShown();renderList()')
    names = _shown_names(page)
    assert names, '搜索日立應該有結果'
    assert all('日立' in n or 'HITACHI' in n for n in names), f'搜索結果混入其他品牌：{names[:5]}'
    page.fill('#q', '')
    page.evaluate('resetShown();renderList()')


def test_filter_brand(page):
    page.select_option('#fBrand', 'Gree 格力')
    page.evaluate('resetShown();renderList()')
    names = _shown_names(page)
    assert names and all('Gree' in n for n in names), f'品牌過濾錯：{names[:5]}'
    page.select_option('#fBrand', '')
    page.evaluate('resetShown();renderList()')


def test_sort_price(page):
    page.select_option('#sortBy', 'price')
    page.evaluate('resetShown();renderList()')
    prices = page.evaluate(
        "() => [...document.querySelectorAll('.mitem .plink')].map(e => {"
        "const s = e.textContent.split('🔍')[0].replace(/[^0-9]/g,'');"
        "return s ? parseInt(s.slice(0, -4) || s.slice(0, 4), 10) || 999999 : 999999; })")
    assert prices == sorted(prices), f'價格排序錯：{prices[:6]}'
    page.select_option('#sortBy', '')
    page.evaluate('resetShown();renderList()')


def test_filter_price_excludes_unknown(page):
    """core.filter：未知價型號唔可以當成任何價位（尤其「5以上」）"""
    bad = page.evaluate('''() => {
      const bad = [];
      const ranges = {'2以下':[0,2000],'2-3':[2000,3000],'3-4':[3000,4000],'4-5':[4000,5000],'5以上':[5000,Infinity]};
      for (const v of Object.keys(ranges)) {
        document.getElementById('fPrice').value = v;
        for (const m of ALL) {
          if (!matches(m)) continue;
          const mm = String(m.price || '').match(/\\$([\\d,]+)/);
          if (!mm) { bad.push([v, m.brand, m.model, 'no-price']); continue; }
          const p = parseInt(mm[1].replace(/,/g, ''));
          const [lo, hi] = ranges[v];
          if (p < lo || p >= hi) bad.push([v, m.brand, m.model, p]);
        }
      }
      document.getElementById('fPrice').value = '';
      return bad;
    }''')
    assert bad == [], f'價位過濾產生唔符合條件嘅型號：{bad[:5]}'


def test_fsel_only_stays_in_sync(page):
    """core.filter：「只顯示已選」之下反選要即刻由列表移除"""
    _reset_controls(page)
    page.evaluate('''() => {
      document.getElementById('fSelOnly').checked = false; clearAll(); renderList();
      const ins = [...document.querySelectorAll('.mitem input')];
      ins[0].click(); ins[1].click();
      document.getElementById('fSelOnly').checked = true; renderList();
    }''')
    assert page.evaluate('document.querySelectorAll(".mitem").length') == 2
    page.evaluate('''() => { const ins = [...document.querySelectorAll('.mitem input')]; ins[0].click(); }''')
    assert page.evaluate('selected.size') == 1
    assert page.evaluate('document.querySelectorAll(".mitem").length') == 1
    _reset_controls(page)


def test_compare_modal(page):
    _reset_controls(page)
    inputs = page.locator('.mitem input')
    assert inputs.count() >= 2
    inputs.nth(0).check()
    inputs.nth(1).check()
    page.click('#btnCompare')
    page.wait_for_timeout(200)
    opened = page.evaluate("() => document.querySelector('.panel').classList.contains('open')")
    rows = page.evaluate("() => document.querySelectorAll('.panel table tr').length")
    assert opened, '對比面板應該打開'
    assert rows >= 3, '對比面板至少要有表頭 + 2 行'
    page.evaluate("() => { document.querySelector('.panel').classList.remove('open'); clearAll(); }")


def test_compare_modal_escape_keyboard(page):
    """ui.comparison-modal：Escape 關閉、焦點移入／返回、aria 狀態同步"""
    _reset_controls(page)
    page.evaluate('''() => { const ins=[...document.querySelectorAll('.mitem input')]; ins[0].click(); ins[1].click(); }''')
    page.click('#btnCompare')
    page.wait_for_timeout(150)
    assert page.evaluate('document.getElementById("panel").classList.contains("open")')
    assert page.evaluate('document.getElementById("panel").getAttribute("role")') == 'dialog'
    assert page.evaluate('document.getElementById("btnCompare").getAttribute("aria-expanded")') == 'true'
    assert page.evaluate('document.getElementById("panel").contains(document.activeElement)'), '打開後焦點應該移入面板'
    page.keyboard.press('Escape')
    page.wait_for_timeout(150)
    assert not page.evaluate('document.getElementById("panel").classList.contains("open")'), 'Escape 應該閂面板'
    assert page.evaluate('document.getElementById("btnCompare").getAttribute("aria-expanded")') == 'false'
    assert page.evaluate('document.activeElement.id') == 'btnCompare', 'Escape 關閉後焦點應該返去開始比較'
    _reset_controls(page)


def test_responsive_no_overflow(page):
    for w, h in [(320, 568), (375, 667), (414, 896), (768, 1024), (900, 900), (1024, 768)]:
        page.set_viewport_size({'width': w, 'height': h})
        page.wait_for_timeout(80)
        overflow = page.evaluate(
            '() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
        assert not overflow, f'{w}px 唔應該有水平溢出'
    page.set_viewport_size({'width': 375, 'height': 667})
    mascot = page.evaluate(
        "() => getComputedStyle(document.querySelector('.hero .mascot')).display")
    assert mascot == 'none', '手機斷點吉祥物應該隱藏'
    page.set_viewport_size({'width': 1280, 'height': 900})


def test_tooltip_no_horizontal_overflow(page):
    """導覽 tooltip 絕對定位唔可以撐出頁面水平滾動（721-999px 平板斷點）"""
    for w in (721, 768, 900, 999, 1000, 1280):
        page.set_viewport_size({'width': w, 'height': 900})
        page.wait_for_timeout(60)
        overflow = page.evaluate(
            '() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
        assert not overflow, f'{w}px tooltip 造成水平溢出'
    page.set_viewport_size({'width': 1280, 'height': 900})


_CONTRAST_JS = '''(selectors) => {
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


def test_contrast_key_elements(browser):
    """深色／淺色模式主要文字、按鈕、連結對比 >= 4.5:1（大字 >= 3:1）"""
    selectors = {
        'footer_text': 'footer',
        'footer_blk_p': 'footer .blk p',
        'footer_blk_a': 'footer .blk a',
        'footer_blk_h3': 'footer .blk h3',
        'compare_sel': '.compare .head .sel',
        'gocompare': '.compare-tools .gocompare',
        'plink': '.plink',
        'h3': '.md-content h3',
        'toolbar_btn': '#btnCompare + button',
    }
    for scheme in ('light', 'dark'):
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme=scheme)
        pg = ctx.new_page()
        pg.goto(URL)
        pg.wait_for_timeout(300)
        ratios = pg.evaluate(_CONTRAST_JS, selectors)
        for name, r in ratios.items():
            assert r is not None, f'{scheme} 模式搵唔到 {name}'
            assert r >= 4.5, f'{scheme} 模式 {name} 對比不足：{r}:1'
        # 對比面板內按鈕
        pg.evaluate('''() => { clearAll(); const ins=[...document.querySelectorAll('.mitem input')];
            ins[0].click(); ins[1].click(); openCompare(); }''')
        pg.wait_for_timeout(150)
        panel_ratios = pg.evaluate(_CONTRAST_JS, {
            'panel_light_btn': '.panel .phead button.light',
            'panel_close_btn': '#btnClosePanel',
        })
        for name, r in panel_ratios.items():
            assert r is not None, f'{scheme} 模式搵唔到 {name}'
            assert r >= 4.5, f'{scheme} 模式 {name} 對比不足：{r}:1'
        # hero 統計數字：大字粗體（2.1em/700），背景係 hero 漸變最淺色 #4a5fa8 → >= 3:1
        hero_ratio = pg.evaluate('''() => {
          const lum = c => { const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
            return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
          const m = getComputedStyle(document.querySelector('.hero .stats .n')).color.match(/[\\d.]+/g).map(Number);
          const l1 = lum(m), l2 = lum([74, 95, 168]);
          return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        }''')
        assert hero_ratio >= 3.0, f'{scheme} 模式 hero 統計數字對比不足：{hero_ratio}:1'
        ctx.close()


def test_metadata_display_and_fractional_time(browser, http_base):
    """operations.last-deploy / dataset-update：只讀 metadata.json，支援小數秒 UTC"""
    meta = {
        'schemaVersion': '1.0.0', 'version': '9.9.9', 'build': 'B-test',
        'commit': 'a' * 40, 'deployTime': '2026-09-02T19:28:14.500Z',
        'workflowRunId': '1', 'deploymentType': 'release',
        'releasePayloadHash': 'sha256:' + 'b' * 64,
        'datasetDate': '2026-09-02', 'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-09-02T19:28:14Z',
        'datasetSourceUrl': 'https://example.com', 'datasetSnapshotId': 's1',
        'datasetHash': 'sha256:' + 'c' * 64, 'recordCount': 1,
    }
    ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
    pg = ctx.new_page()
    pg.route('**/metadata.json', lambda route: route.fulfill(
        status=200, content_type='application/json', body=json.dumps(meta)))
    pg.goto(http_base + '/index.html')
    pg.wait_for_timeout(400)
    info = pg.text_content('#deployInfo')
    assert '2026-09-03 03:28' in info, f'小數秒 UTC→HKT 轉換錯：{info}'
    assert '9.9.9' in info, f'版本應嚟自 metadata：{info}'
    assert pg.text_content('#verInfo') == 'v9.9.9', '頁腳版本應嚟自 metadata'
    assert '2026-09-02' in pg.text_content('#footStatus')
    ctx.close()


def test_metadata_failure_no_hardcoded_values(browser, http_base):
    """operations.version-display/last-deploy：metadata 載入失敗唔可以顯示硬編舊值"""
    import models_data
    ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
    pg = ctx.new_page()
    pg.route('**/metadata.json', lambda route: route.abort())
    pg.goto(http_base + '/index.html')
    pg.wait_for_timeout(400)
    info = pg.text_content('#deployInfo')
    assert '暫不可用' in info, f'載入失敗應該顯示暫不可用：{info}'
    ver = pg.text_content('#verInfo')
    assert ver == 'v?', f'載入失敗版本應該係 v?：{ver}'
    assert models_data.VERSION not in ver, '唔可以回退硬編產品版本'
    ctx.close()
