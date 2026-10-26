"""Repo documentation link/fragment contract (offline)."""
from pathlib import Path

from scripts.check_docs_links import check


def test_repo_docs_have_no_broken_internal_links():
    result = check(Path('.').resolve())
    assert result['ok'], result['broken']
    assert result['markdownFiles'] >= 20
    assert result['linksChecked'] > 0


def test_checker_detects_missing_file_and_fragment(tmp_path):
    (tmp_path / 'a.md').write_text(
        '# Title\n\n[ok](#title)\n[missing](b.md)\n[bad](#nope)\n',
        encoding='utf-8')
    result = check(tmp_path)
    assert not result['ok']
    reasons = {item['reason'] for item in result['broken']}
    assert 'missing target' in reasons
    assert 'missing fragment' in reasons
