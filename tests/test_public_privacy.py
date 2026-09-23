# -*- coding: utf-8 -*-
"""回歸：公開 repo 私隱 gate（模式語義、fail-closed、禁止市場樣本）

- tracked/head = HEAD tree blobs；index = staged index blobs；worktree = tracked worktree
  + untracked non-ignored；tree = 指定目錄 bytes。
- cat-file／ls-files／ls-tree 失敗、unmerged、header 錯、invalid UTF-8 一律 fail-closed。
- 合成樣本以 runtime 組成（避免測試檔自身被 gate 命中）。
"""
import importlib.util
import io
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
_SPEC = importlib.util.spec_from_file_location(
    'check_public_privacy', os.path.join(BASE, 'scripts', 'check_public_privacy.py'))
cpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cpp)

PRIVATE_IP = '.'.join(['192', '168', '55', '77'])
DDNS = 'server1' + '.' + 'ddns' + '.net'
USER = 'someuser'


def _git(repo, *args):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _init_repo(tmp_path, commit=False):
    repo = str(tmp_path)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'core.autocrlf', 'false')
    _git(repo, 'config', 'user.email', 't@example.com')
    _git(repo, 'config', 'user.name', 't')
    return repo


def _write(repo, rel, text, stage=True):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', encoding='utf-8', newline='\n').write(text)
    if stage:
        _git(repo, 'add', '-f', rel)


def _rules(repo, mode):
    return sorted({rule for rule, _, _ in cpp.scan_mode(repo, mode)})


def test_forbidden_samples_are_detected_index_mode(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'a.md', f'host={PRIVATE_IP}\nuser={USER}@{PRIVATE_IP}\n')
    _write(repo, 'b.md', f'domain={DDNS}\nhome=' + '/' + 'home/' + USER + '/app\n')
    _write(repo, 'c.md', 'path=' + 'C:' + '\\' + 'Users' + '\\' + 'somebody' + '\\work\n'
                         'agents=' + '.agents' + '/private-notes/x\n')
    _write(repo, 'd.md', 'vol=' + '/var/lib/docker/' + 'volumes/realvolume/_data\n'
                         'compose=' + 'proj' + '_aircon-' + 'data\n')
    _write(repo, 'e.md', 'build=' + 'B' + '20260101' + '.docker-' + 'abcdef\n'
                         'port=' + '127.0.0.1:' + '87' + '88\n'
                         'ts=' + '20260101' + 'T010203Z\n')
    _write(repo, 'f.md', 'token=' + 'gho_' + 'A' * 20 + '\n')
    hits = _rules(repo, 'index')
    for rule in ('LAN_IPV4', 'SSH_AT_IP', 'DDNS_DOMAIN', 'HOME_PATH', 'WIN_USER_PATH',
                 'AGENTS_PATH', 'DOCKER_VOLUME_PATH', 'COMPOSE_VOLUME_NAME',
                 'PRIVATE_BUILD_ID', 'LOCAL_PORT', 'BACKUP_TIMESTAMP', 'TOKEN_PATTERN'):
        assert rule in hits, f'{rule} 應該命中：{hits}'


def test_allowed_examples_pass(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'ok.md', 'host=' + '192.0.2.10\n' + 'user=deploy-user@192.0.2.10\n'
                          'domain=aircon.example.invalid\nhome=/home/deploy-user/app\n'
                          'vol=' + '/var/lib/docker/volumes/example-volume/_data\n'
                          'port=127.0.0.1:8080\n')
    _write(repo, 'tests/fixture.md', 'ts=' + '20260101' + 'T010203Z\n'
                                     'port=' + '127.0.0.1:' + '87' + '88\n')
    assert cpp.scan_mode(repo, 'index') == []


def test_worktree_candidate_is_clean():
    assert cpp.scan_mode(BASE, 'worktree') == [], '公開候選 worktree 唔應該有私隱 pattern'


def test_head_mode_reads_head_not_worktree_or_index(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'a.md', 'clean\n')
    _git(repo, 'commit', '-q', '-m', 'init')
    # worktree 改私人內容（unstaged）；另外 stage 另一個私人檔
    open(os.path.join(repo, 'a.md'), 'w', encoding='utf-8').write('home=/' + 'home/' + USER + '/x\n')
    _write(repo, 'b.md', 'domain=' + DDNS + '\n')
    assert cpp.scan_mode(repo, 'tracked') == [], 'tracked 要讀 HEAD blob'
    assert 'LAN_IPV4' not in _rules(repo, 'tracked')
    assert 'HOME_PATH' in _rules(repo, 'worktree'), 'worktree 要讀工作樹'
    assert 'DDNS_DOMAIN' in _rules(repo, 'index'), 'index 要讀 staged blob'
    assert 'HOME_PATH' not in _rules(repo, 'index'), 'index 唔應該見到 unstaged worktree 改動'


def test_worktree_mode_covers_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    open(os.path.join(repo, 'untracked.md'), 'w', encoding='utf-8').write(
        'home=/' + 'home/' + USER + '/x\n')
    assert 'HOME_PATH' in _rules(repo, 'worktree')
    assert cpp.scan_mode(repo, 'index') == [], '未 stage 嘅 untracked 唔應該出現喺 index 模式'


def test_deleted_tracked_file_worktree_skips_without_error(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'gone.md', 'home=/' + 'home/' + USER + '/x\n')
    os.remove(os.path.join(repo, 'gone.md'))
    assert 'HOME_PATH' in _rules(repo, 'index'), 'index blob 仍然要掃'
    assert cpp.scan_mode(repo, 'worktree') == [], '刪除後 worktree 模式唔應該報讀取錯誤'


def test_git_failure_fails_closed(tmp_path):
    with pytest.raises(RuntimeError):
        cpp.scan_mode(str(tmp_path), 'index')


def test_unmerged_index_fails_closed(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, 'conflict.txt', 'clean\n')
    _git(repo, 'commit', '-q', '-m', 'init')
    sha = _git(repo, 'hash-object', '-w', 'conflict.txt').decode().strip()
    info = (f'100644 {sha} 1\tconflict.txt\n'
            f'100644 {sha} 2\tconflict.txt\n').encode()
    subprocess.run(['git', '-C', repo, 'update-index', '--index-info'],
                   input=info, check=True)
    with pytest.raises(RuntimeError, match='unmerged'):
        cpp.scan_mode(repo, 'index')


def test_cat_file_failure_fails_closed(tmp_path):
    repo = _init_repo(tmp_path)
    with pytest.raises(RuntimeError, match='cat-file|搵唔到'):
        cpp._cat_file_batch(repo, [('x', '0' * 40)])


def test_invalid_utf8_text_fails_closed(tmp_path):
    repo = _init_repo(tmp_path)
    p = os.path.join(repo, 'leak.txt')
    open(p, 'wb').write(b'\xff\xfe invalid utf8')
    _git(repo, 'add', '-f', 'leak.txt')
    with pytest.raises(RuntimeError, match='UTF-8'):
        cpp.scan_mode(repo, 'index')
    with pytest.raises(RuntimeError, match='UTF-8'):
        cpp.scan_mode(repo, 'worktree')


def test_pending_marker_detected(tmp_path):
    repo = _init_repo(tmp_path)
    marker = 'TODO(' + 'PENDING-PRIVACY-SELFHOST' + '): move this'
    _write(repo, 'todo.md', marker + '\n')
    assert 'PENDING_MARKER' in _rules(repo, 'index')


def test_tree_mode_scans_actual_bytes(tmp_path):
    root = tmp_path / 'archive'
    root.mkdir()
    (root / 'report.json').write_text('{"ok": true}', encoding='utf-8')
    assert cpp.scan_mode(str(root), 'tree') == []
    (root / 'leak.txt').write_text('path C:' + '\\Users\\' + 'RealPerson\\x\n', encoding='utf-8')
    assert 'WIN_USER_PATH' in {r for r, _, _ in cpp.scan_mode(str(root), 'tree')}


def test_scan_output_has_no_matched_text(tmp_path):
    repo = _init_repo(tmp_path)
    secret = DDNS
    open(os.path.join(repo, 'leak.md'), 'w', encoding='utf-8').write('domain=' + secret + '\n')
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cpp.scan_mode(repo, 'worktree')  # scan 本身唔打印
    finally:
        sys.stdout = old
    assert secret not in buf.getvalue()
    assert 'DDNS_DOMAIN' in _rules(repo, 'worktree')
