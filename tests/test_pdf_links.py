"""PDF link contract: absolute URLs clickable; relative/fragment targets never emitted."""
import re
from pathlib import Path

import generate_pdf


def test_inline_links_are_absolute_and_clickable():
    html = (
        '<p><a href="https://example.com/a">官方頁</a> '
        '<a href="README.md">相對文件</a> '
        '<a href="#price">目錄</a> '
        '另見 https://example.com/b。以及 https://example.com/c).</p>'
    )
    ex = generate_pdf.BlockExtractor()
    ex.feed(html)
    kind, payload = ex.blocks[0]
    assert kind == 'p'
    out = generate_pdf._pdf_inline(payload, ex.links)
    assert '<a href="https://example.com/a">官方頁</a>' in out
    assert '相對文件' in out and 'README.md' not in out
    assert '目錄' in out and 'href="#price"' not in out
    assert '<a href="https://example.com/b">https://example.com/b</a>' in out
    assert '<a href="https://example.com/c">https://example.com/c</a>' in out
    assert '。' not in re.findall(r'<a href="([^"]+)"', out)[-2]


def test_generated_pdf_contains_clickable_absolute_links(tmp_path):
    out = tmp_path / 'aircon-report.pdf'
    generate_pdf.build_pdf(output_path=str(out), metadata_path='metadata.json')
    raw = out.read_bytes()
    assert b'/URI' in raw
    # The report's known absolute sources must be present as annotation targets.
    assert b'https://github.com/CalvinLau1012/aircon-compare/blob/master/docs/STATUS.md' in raw
    assert b'https://lihkg.com/thread/4121620/page/1' in raw
    # Relative repo paths and fragment-only links must not become wrong PDF URLs.
    assert b'/URI (README.md)' not in raw
    assert b'/URI (docs/STATUS.md)' not in raw
    assert b'/URI (#' not in raw
