# -*- coding: utf-8 -*-
"""stage_artifacts 精確 allowlist 回歸（唔用 git add -A）"""
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE, 'scripts', 'stage_artifacts.py')


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def _git(repo, *args):
    r = _run(['git', *args], repo)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def _setup(tmp_path):
    repo = str(tmp_path)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 't@example.com')
    _git(repo, 'config', 'user.name', 't')
    (tmp_path / 'deploy_payload.json').write_text(
        json.dumps({'files': ['index.html']}), encoding='utf-8')
    (tmp_path / 'index.html').write_text('v1', encoding='utf-8')
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('a\n1\n', encoding='utf-8')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'init')
    return repo


def _stage(repo, *extra):
    return _run([sys.executable, SCRIPT, '--repo', repo, *extra], repo)


def _staged(repo):
    out = _run(['git', 'diff', '--cached', '--name-only', '-z'], repo).stdout
    return [p for p in out.split('\0') if p]


def test_stage_only_allowed_changes(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('a\n2\n', encoding='utf-8')
    r = _stage(repo)
    assert r.returncode == 0, r.stderr
    staged = _staged(repo)
    assert set(staged) == {'index.html', 'emsd_空調能源標籤.csv'}


def test_unexpected_file_blocks_everything(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    (tmp_path / 'private-secret.txt').write_text('secret', encoding='utf-8')
    r = _stage(repo)
    assert r.returncode == 1
    assert 'allowlist 以外' in r.stderr
    staged = _staged(repo)
    assert staged == [], '有 unexpected 檔時唔可以 stage 任何嘢'


def test_rename_blocks(tmp_path):
    repo = _setup(tmp_path)
    _git(repo, 'mv', 'index.html', 'index2.html')
    r = _stage(repo)
    assert r.returncode == 1
    assert 'rename' in r.stderr


def test_dry_run_does_not_stage(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    r = _stage(repo, '--dry-run')
    assert r.returncode == 0, r.stderr
    assert _staged(repo) == []


def test_no_changes_is_ok(tmp_path):
    repo = _setup(tmp_path)
    r = _stage(repo)
    assert r.returncode == 0, r.stderr
    assert '無需 stage' in r.stdout

def test_allowlisted_deletion_blocks(tmp_path):
    repo = _setup(tmp_path)
    os.remove(os.path.join(repo, 'index.html'))
    r = _stage(repo)
    assert r.returncode == 1
    assert 'deletion' in r.stderr
    assert _staged(repo) == [], '有 deletion 時唔可以 stage 其他檔'


def test_staged_deletion_blocks(tmp_path):
    repo = _setup(tmp_path)
    _git(repo, 'rm', 'index.html')
    r = _stage(repo)
    assert r.returncode == 1
    assert 'deletion' in r.stderr


def test_unexpected_deletion_blocks(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'other.txt').write_text('x', encoding='utf-8')
    _git(repo, 'add', 'other.txt')
    _git(repo, 'commit', '-q', '-m', 'other')
    os.remove(os.path.join(repo, 'other.txt'))
    r = _stage(repo)
    assert r.returncode == 1
    assert 'deletion' in r.stderr


def test_pre_staged_unexpected_blocks_even_without_worktree_change(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'secret.txt').write_text('x', encoding='utf-8')
    _git(repo, 'add', 'secret.txt')
    r = _stage(repo)
    assert r.returncode == 1
    assert 'staged' in r.stderr


def test_manifest_traversal_duplicate_self_reference_blocked(tmp_path):
    repo = _setup(tmp_path)
    cases = [
        ['../outside.txt'],
        ['/etc/passwd'],
        ['index.html', 'index.html'],
        ['metadata.json'],
        ['index.html', 123],
    ]
    for files in cases:
        (tmp_path / 'deploy_payload.json').write_text(json.dumps({'files': files}), encoding='utf-8')
        r = _stage(repo)
        assert r.returncode == 1, files
        assert _staged(repo) == [], files


def test_manifest_missing_or_symlink_escape_blocked(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'deploy_payload.json').write_text(json.dumps({'files': ['nope.csv']}),
                                                  encoding='utf-8')
    r = _stage(repo)
    assert r.returncode == 1
    outside = tmp_path.parent / 'outside-secret.txt'
    outside.write_text('secret', encoding='utf-8')
    link = tmp_path / 'link.txt'
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return
    (tmp_path / 'deploy_payload.json').write_text(json.dumps({'files': ['link.txt']}),
                                                  encoding='utf-8')
    assert _stage(repo).returncode == 1


def test_no_partial_stage_on_unexpected_and_dry_run_no_change(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    (tmp_path / 'secret.txt').write_text('x', encoding='utf-8')
    r = _stage(repo)
    assert r.returncode == 1
    assert _staged(repo) == [], '失敗時唔可以有部分 stage'
    os.remove(os.path.join(repo, 'secret.txt'))
    r2 = _stage(repo, '--dry-run')
    assert r2.returncode == 0
    assert _staged(repo) == [], 'dry-run 唔可以改 index'



# ---------------------------------------------------------------- R5 parser／index 精確測試

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    'stage_artifacts_mod', os.path.join(BASE, 'scripts', 'stage_artifacts.py'))
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)


def _index_bytes(repo):
    path = sa._index_path(repo)
    return open(path, 'rb').read()


def _index_state(repo):
    """index 內每個 path 嘅 blob OID（語義狀態；比 raw bytes 穩定）。"""
    out = _run(['git', 'ls-files', '-s', '-z'], repo).stdout
    state = {}
    for e in out.split('\0'):
        if not e:
            continue
        meta, path = e.split('\t', 1)
        _mode, oid, stage = meta.split()
        assert stage == '0'
        state[path] = oid
    return state


def test_pre_staged_allowed_succeeds_and_preserves_index(tmp_path):
    """Codex 重現：allowlisted pre-staged modify（worktree 已乾淨）必須成功且 index 不變"""
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    _git(repo, 'add', 'index.html')
    before_index = _index_state(repo)
    r = _stage(repo)
    assert r.returncode == 0, r.stderr
    assert _staged(repo) == ['index.html'], 'staged names 要精確'
    assert _index_state(repo) == before_index, 'index blob OID 必須不變'


def test_pre_staged_allowed_plus_new_change(tmp_path):
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    _git(repo, 'add', 'index.html')
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('a\n3\n', encoding='utf-8')
    before = _index_state(repo)
    r = _stage(repo)
    assert r.returncode == 0, r.stderr
    assert set(_staged(repo)) == {'index.html', 'emsd_空調能源標籤.csv'}
    assert _index_state(repo) != before, '有新改動時 index 應該更新'


def test_staged_status_parser_formats(tmp_path):
    # M
    (tmp_path / 'm').mkdir()
    repo = _setup(tmp_path / 'm')
    (tmp_path / 'm' / 'index.html').write_text('v2', encoding='utf-8')
    _git(repo, 'add', 'index.html')
    staged, deleted, renames = sa._staged_status(repo)
    assert staged == ['index.html'] and not deleted and not renames
    # A
    (tmp_path / 'a').mkdir()
    repo = _setup(tmp_path / 'a')
    (tmp_path / 'a' / 'new.csv').write_text('x\n1\n', encoding='utf-8')
    _git(repo, 'add', 'new.csv')
    staged, deleted, renames = sa._staged_status(repo)
    assert staged == ['new.csv']
    # D（先 commit 再 rm）
    (tmp_path / 'd').mkdir()
    repo = _setup(tmp_path / 'd')
    (tmp_path / 'd' / 'new.csv').write_text('x\n1\n', encoding='utf-8')
    _git(repo, 'add', 'new.csv')
    _git(repo, 'commit', '-q', '-m', 'new')
    _git(repo, 'rm', '-q', 'new.csv')
    staged, deleted, renames = sa._staged_status(repo)
    assert deleted == ['new.csv'] and 'new.csv' not in staged
    # R
    (tmp_path / 'r').mkdir()
    repo = _setup(tmp_path / 'r')
    (tmp_path / 'r' / 'r1.txt').write_text('line\n', encoding='utf-8')
    _git(repo, 'add', 'r1.txt')
    _git(repo, 'commit', '-q', '-m', 'r1')
    _git(repo, 'mv', 'r1.txt', 'r2.txt')
    staged, deleted, renames = sa._staged_status(repo)
    assert len(renames) == 1 and renames[0]['old'] == 'r1.txt' \
        and renames[0]['new'] == 'r2.txt', renames


def test_staged_status_parser_rejects_bad(tmp_path):
    repo = _setup(tmp_path)
    cases = [
        [b'M'],                                    # truncated（缺 path）
        [b'Z', b'x'],                              # 未知 status
        [b'M', b''],                               # 空 path
        [b'M', b'a', b'M', b'a'],                  # duplicate
        [b'M', b'\xff\xfe'],                       # invalid UTF-8
        [b'R100', b'old'],                         # rename truncated
        [b'R100', b'', b'new'],                    # rename 空 old
    ]
    for tokens in cases:
        with pytest.raises(ValueError):
            sa._parse_name_status_tokens(tokens)
    # 合法 cases
    assert sa._parse_name_status_tokens([b'M', b'a']) == (['a'], [], [])
    assert sa._parse_name_status_tokens([b'A', b'a']) == (['a'], [], [])
    assert sa._parse_name_status_tokens([b'D', b'a']) == ([], ['a'], [])
    st, de, rn = sa._parse_name_status_tokens([b'R100', b'old', b'new'])
    assert st == [] and rn[0]['old'] == 'old' and rn[0]['new'] == 'new'
    st, de, rn = sa._parse_name_status_tokens([b'C75', b'old', b'new'])
    assert rn[0]['status'] == 'C75'


def test_index_restore_on_invariant_failure(tmp_path, monkeypatch):
    """final invariant 失敗：index 恢復呼叫前狀態，唔留部分 stage"""
    repo = _setup(tmp_path)
    (tmp_path / 'index.html').write_text('v2', encoding='utf-8')
    before_index = _index_state(repo)
    calls = {'n': 0}
    real = sa._staged_names

    def broken(repo_):
        calls['n'] += 1
        return ['WRONG']  # 第一次呼叫就係 stage 後 invariant 檢查

    monkeypatch.setattr(sa, '_staged_names', broken)
    rc = sa.main(['--repo', repo])
    assert rc == 1
    assert _index_state(repo) == before_index, 'invariant 失敗必須恢復 index'
    monkeypatch.setattr(sa, '_staged_names', real)
    assert _staged(repo) == [], '恢復後 index 應該同呼叫前一樣（無 staged）'
