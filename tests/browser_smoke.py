# -*- coding: utf-8 -*-
"""瀏覽器核心路徑 smoke 測試（E2 行為證據，Registry required 功能綁定）

用 Playwright 打開生成嘅 index.html，驗證：
- core.search / core.filter / core.sort（比較器列表操作）
- core.compare / ui.comparison-modal（揀機 → 對比面板）
- ui.responsive（手機斷點無水平溢出）

本地：pip install playwright && playwright install chromium
CI：workflow 已有 smoke 步驟
"""
import os
import sys

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
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={'width': 1280, 'height': 900})
        pg.goto(URL)
        yield pg
        browser.close()


def _shown_names(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.mitem .info .name')].map(e => e.textContent.trim())")


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


def test_compare_modal(page):
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


def test_responsive_no_overflow(page):
    page.set_viewport_size({'width': 375, 'height': 667})
    overflow = page.evaluate(
        '() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
    assert not overflow, '375px 手機唔應該有水平溢出'
    mascot = page.evaluate(
        "() => getComputedStyle(document.querySelector('.hero .mascot')).display")
    assert mascot == 'none', '手機斷點吉祥物應該隱藏'
    page.set_viewport_size({'width': 1280, 'height': 900})
