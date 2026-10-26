"""Internal site link/asset/download/fragment checks for generated static trees."""
from pathlib import Path

from scripts.check_site_links import check


def _write_good_site(root: Path):
    (root / 'index.html').write_text(
        '<!doctype html><html><body>'
        '<a href="#section">section</a> '
        '<a href="doc.html#detail">doc</a> '
        '<link rel="stylesheet" href="style.css">'
        '<h2 id="section">Section</h2>'
        '</body></html>', encoding='utf-8')
    (root / 'doc.html').write_text(
        '<!doctype html><html><body><h2 id="detail">Detail</h2></body></html>',
        encoding='utf-8')
    (root / 'style.css').write_text('body{color:#000}', encoding='utf-8')
    (root / '空調對比報告.pdf').write_bytes(b'%PDF-1.4\n%%EOF\n')
    (root / 'emsd_空調能源標籤.csv').write_text('a,b\n1,2\n', encoding='utf-8')
    (root / 'metadata.json').write_text('{"version":"1.2.9"}', encoding='utf-8')


def test_all_internal_links_assets_downloads_and_fragments_pass(tmp_path):
    _write_good_site(tmp_path)
    result = check(tmp_path)
    assert result['ok'], result['broken']
    assert result['pagesCrawled'] >= 1
    assert all(r['ok'] for r in result['requiredDownloads'])
    assert result['externalCount'] == 0


def test_missing_doc_link_is_reported_as_broken(tmp_path):
    _write_good_site(tmp_path)
    with (tmp_path / 'index.html').open('a', encoding='utf-8') as f:
        f.write('<a href="README.md">repo docs</a>')
    result = check(tmp_path)
    assert not result['ok']
    assert any(item.get('raw') == 'README.md' and item.get('status') == 404
               for item in result['broken'])


def test_missing_fragment_is_reported_as_broken(tmp_path):
    _write_good_site(tmp_path)
    with (tmp_path / 'index.html').open('a', encoding='utf-8') as f:
        f.write('<a href="#does-not-exist">bad fragment</a>')
    result = check(tmp_path)
    assert not result['ok']
    assert any(item.get('fragment') == 'does-not-exist' and not item.get('fragmentOk')
               for item in result['broken'])
