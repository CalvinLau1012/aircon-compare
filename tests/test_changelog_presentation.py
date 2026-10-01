"""Browser verification of changelog history, wrapping and both colour schemes (offline)."""
import re
import subprocess
import sys
from html import unescape
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def candidate_html():
    subprocess.run([sys.executable, 'generate_html.py'], cwd=ROOT, check=True,
                   capture_output=True)
    return (ROOT / '空調對比報告.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('width', [375, 768, 1280])
@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_changelog_preserves_history_and_fits_viewport(candidate_html, width, scheme):
    import generate_html
    source = (ROOT / '空調對比報告.md').read_text(encoding='utf-8')
    journal = source.split('## 📅 更新日誌', 1)[1].split('\n## ', 1)[0]
    original = generate_html.md_to_html(journal)
    expected = [unescape(re.sub('<[^>]+>', '', h))
                for h in re.findall(r'<h3>(.*?)</h3>', original, re.S)]
    assert len(expected) > 10  # This must exercise historical entries too.
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 900},
                                color_scheme=scheme)
        # This test must never contact sources, prices, or external fonts.
        page.route('**/*', lambda route: route.abort())
        page.set_content(candidate_html, wait_until='domcontentloaded')
        entries = page.locator('.changelog-entry > h3').all_text_contents()
        assert entries == expected
        assert page.locator('.changelog table').count() > 5
        bad = page.locator('.changelog .table-scroll').evaluate_all(
            '(els)=>els.filter(e=>e.scrollWidth>e.clientWidth+1).length')
        assert bad == 0, 'Changelog tables should wrap without horizontal scrolling'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert page.locator('.md-content table').evaluate_all(
            '(els)=>els.every(e=>e.parentElement.classList.contains("table-scroll"))')
        browser.close()
