"""Repo documentation link/fragment contract (offline)."""
from pathlib import Path

from scripts.check_docs_links import _github_target, check


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


def test_checker_validates_this_repo_master_blob_links(tmp_path):
    (tmp_path / 'README.md').write_text(
        '# Home\n\n'
        '[present](https://github.com/CalvinLau1012/aircon-compare/blob/master/docs/STATUS.md)\n'
        '[missing](https://github.com/CalvinLau1012/aircon-compare/blob/master/docs/MISSING.md)\n',
        encoding='utf-8')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'STATUS.md').write_text('# Status\n', encoding='utf-8')

    result = check(tmp_path)
    assert not result['ok']
    assert any(item['target'].endswith('/docs/MISSING.md')
               and item['reason'] == 'missing target'
               for item in result['broken'])
    assert not any(item['target'].endswith('/docs/STATUS.md')
                   for item in result['broken'])


def test_only_current_master_urls_are_mapped_to_candidate_tree():
    assert _github_target(
        '/CalvinLau1012/aircon-compare/blob/master/README.md') == ('README.md', '')
    assert _github_target(
        '/CalvinLau1012/aircon-compare/tree/master/docs') == ('docs', '')
    assert _github_target(
        '/CalvinLau1012/aircon-compare/blob/old-release/README.md') is None
    assert _github_target('/someone/else/blob/master/README.md') is None
