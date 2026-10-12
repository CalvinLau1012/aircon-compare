# -*- coding: utf-8 -*-
"""Hero 精簡版式行為回歸（D32 隔離 fixture；唔讀 production index.html／PDF／metadata）。

先執行 `generate_html.py` 重生候選 HTML，再用 Chromium 驗證行為契約（唔係文案鏡像）：
- 4 個 hero 統計保留：3 個 JS id 計數有動態值、第 4 個靜態計數存在；
- hero 來源摘要係短句，唯一入口係鍵盤可用嘅 `#source` 連結，Enter 後真正跳到來源區；
- 375／1280、light／dark 無水平溢出、hero 連結對比 >= 4.5:1、hero 高度維持精簡上限；
- 舊 hero 嘅來源／價格快照限制資訊仍存在於頁面（footer／來源區），沒有靜默遺失。
"""
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGE = '空調對比報告.html'

# 精簡上限：實作量得 375x667 = 466px、1280x900 = 386px（改動前 732／494）。
MAX_HERO_HEIGHT = {375: 600, 1280: 430}

_CONTRAST_JS = '''(payload) => {
  const parse = c => { const m = (c || '').match(/[\\d.]+/g) || []; const n = m.map(Number);
    return [n[0] || 0, n[1] || 0, n[2] || 0, n.length > 3 ? n[3] : 1]; };
  const lum = c => { const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
  const el = document.querySelector(payload.sel);
  if (!el) return null;
  return +ratio(parse(getComputedStyle(el).color), payload.bg).toFixed(2);
}'''


@pytest.fixture(scope='module')
def candidate_path():
    subprocess.run([sys.executable, 'generate_html.py'], cwd=ROOT, check=True,
                   capture_output=True)
    path = ROOT / PAGE
    assert path.exists(), 'generate_html.py 應該重生候選 HTML'
    return path


@pytest.fixture(scope='module')
def http_base(candidate_path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_hero_stats_summary_and_source_entry(browser, http_base):
    ctx = browser.new_context(viewport={'width': 375, 'height': 667})
    pg = ctx.new_page()
    pg.goto(f'{http_base}/{PAGE}')
    pg.wait_for_timeout(500)
    stats = pg.evaluate(
        "() => [...document.querySelectorAll('.hero .stats > div')].map(d => ({"
        "id: d.querySelector('[id]') ? d.querySelector('[id]').id : null,"
        "n: d.querySelector('.n').textContent.trim(),"
        "l: d.querySelector('.l').textContent.trim()}))")
    assert len(stats) == 4, stats
    assert {s['id'] for s in stats if s['id']} == {'statTotal', 'statPrice', 'statSize'}, stats
    assert all(s['l'] for s in stats), stats
    assert all(s['n'].replace(',', '').isdigit() for s in stats), stats
    assert all(int(s['n'].replace(',', '')) > 0 for s in stats), stats

    src = pg.text_content('.hero .src').strip()
    assert 0 < len(src) <= 140, f'hero 來源摘要應該係短句：{len(src)} 字'
    links = pg.evaluate(
        "() => [...document.querySelectorAll('.hero a')].map(a => a.getAttribute('href'))")
    assert links == ['#source'], f'hero 只應該保留一個來源入口：{links}'
    assert pg.evaluate("() => !!document.getElementById('source')"), '來源區要有 #source 錨點'
    assert '資料日期' in pg.text_content('#deployInfo'), 'deployInfo 仍要由 metadata runtime 更新'
    ctx.close()


def test_hero_source_link_keyboard_and_anchor(browser, http_base):
    ctx = browser.new_context(viewport={'width': 375, 'height': 667})
    pg = ctx.new_page()
    pg.goto(f'{http_base}/{PAGE}')
    pg.wait_for_timeout(400)
    pg.evaluate('() => window.scrollTo(0, 0)')
    link = pg.locator('.hero .src a[href="#source"]')
    assert link.count() == 1
    link.focus()
    assert pg.evaluate(
        "() => document.activeElement === document.querySelector('.hero .src a')"), '連結要可以用鍵盤聚焦'
    pg.keyboard.press('Enter')
    pg.wait_for_timeout(500)
    assert pg.evaluate('() => location.hash') == '#source'
    top = pg.evaluate(
        "() => document.getElementById('source').getBoundingClientRect().top")
    assert 0 <= top <= 220, f'來源錨點應該跳到視窗內：top={top}'
    ctx.close()


def test_hero_fit_and_link_contrast(browser, http_base):
    for scheme in ('light', 'dark'):
        for width, height in ((375, 667), (1280, 900)):
            ctx = browser.new_context(viewport={'width': width, 'height': height},
                                      color_scheme=scheme)
            pg = ctx.new_page()
            pg.goto(f'{http_base}/{PAGE}')
            pg.wait_for_timeout(400)
            overflow = pg.evaluate(
                '() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
            assert not overflow, f'{scheme} {width}px 有水平溢出'
            hero_h = pg.evaluate(
                "() => document.querySelector('.hero').getBoundingClientRect().height")
            assert hero_h <= MAX_HERO_HEIGHT[width], \
                f'{scheme} {width}px hero 高度超出精簡上限：{hero_h}'
            ratio = pg.evaluate(_CONTRAST_JS, {'sel': '.hero .src a', 'bg': [74, 95, 168]})
            assert ratio is not None and ratio >= 4.5, \
                f'{scheme} {width}px hero 來源連結對比不足：{ratio}'
            ctx.close()


def test_source_and_price_snapshot_caveats_retained(candidate_path):
    html = candidate_path.read_text(encoding='utf-8')
    for token in ('DATA.GOV.HK', 'hk-emsd-emsd1-meels-listed-models', '每週更新',
                  'BigGo', 'PricesAPI', 'Price.com.hk', 'LIHKG', '缺價標「待查」'):
        assert token in html, f'來源／價格快照限制資訊唔可以遺失：{token}'
