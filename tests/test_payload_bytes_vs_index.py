# -*- coding: utf-8 -*-
"""回歸：payload worktree bytes vs git index bytes 防線（防再現 Pages hash 鏈斷）

用臨時 git repo + `.gitattributes` `* text=auto eol=lf` 重現真實正規化環境：
- LF 檔案 → gate 通過；
- CRLF 檔案 → git add 後 index 變 LF → gate 必須阻斷（呢個就係原本嘅 bug class）；
- 未 stage 嘅 payload 檔案 → gate 必須阻斷。
"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE, 'scripts', 'check_payload_bytes_vs_index.py')


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def _git(repo, *args):
    r = _run(['git', *args], repo)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def _setup_repo(tmp_path, files):
    repo = str(tmp_path)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'core.autocrlf', 'false')
    open(os.path.join(repo, '.gitattributes'), 'w', encoding='utf-8').write('* text=auto eol=lf\n')
    for rel, data in files.items():
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(data)
    open(os.path.join(repo, 'deploy_payload.json'), 'w', encoding='utf-8').write(
        json.dumps({'files': list(files)}, ensure_ascii=False))
    return repo


def _gate(repo):
    return _run([sys.executable, SCRIPT, '--repo', repo], repo)


def test_lf_files_pass(tmp_path):
    repo = _setup_repo(tmp_path, {'a.csv': b'x,y\n1,2\n', 'b.pdf': b'%PDF-1.4\n'})
    _git(repo, 'add', '-A')
    r = _gate(repo)
    assert r.returncode == 0, r.stderr + r.stdout
    assert 'worktree bytes == git index bytes' in r.stdout


def test_crlf_file_is_blocked(tmp_path):
    repo = _setup_repo(tmp_path, {'a.csv': b'x,y\r\n1,2\r\n'})
    _git(repo, 'add', '-A')
    # 先確認環境真係會正規化（index blob 係 LF）
    idx = _run(['git', 'cat-file', 'blob', ':a.csv'], repo).stdout.encode('utf-8', errors='replace')
    assert b'\r\n' not in idx, '測試前提：index blob 應該被正規化為 LF'
    wt = open(os.path.join(repo, 'a.csv'), 'rb').read()
    assert b'\r\n' in wt, '測試前提：worktree 應該係 CRLF'
    r = _gate(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert '唔一致' in r.stderr, r.stderr


def test_unstaged_payload_is_blocked(tmp_path):
    repo = _setup_repo(tmp_path, {'a.csv': b'x,y\n1,2\n'})
    _git(repo, 'add', '-A')
    open(os.path.join(repo, 'a.csv'), 'ab').write(b'3,4\n')  # 改咗但未 stage
    r = _gate(repo)
    assert r.returncode == 1, r.stdout + r.stderr


def test_missing_payload_is_blocked(tmp_path):
    repo = _setup_repo(tmp_path, {'a.csv': b'x,y\n1,2\n'})
    _git(repo, 'add', '-A')
    os.remove(os.path.join(repo, 'a.csv'))
    r = _gate(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert '缺少 payload 檔案' in r.stderr, r.stderr
