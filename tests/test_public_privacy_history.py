# -*- coding: utf-8 -*-
"""D4-A：全 Git 歷史秘密／私人線審計回歸。"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'history_mod', os.path.join(BASE, 'scripts', 'check_public_history.py'))
history = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(history)


def _git(repo, *args):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    assert r.returncode == 0, r.stderr
    return r


def _repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'test@example.invalid')
    _git(repo, 'config', 'user.name', 'Test')
    return repo


def _commit(repo, name, body):
    (repo / name).write_text(body, encoding='utf-8')
    _git(repo, 'add', name)
    _git(repo, 'commit', '-q', '-m', name)


def test_history_audit_detects_credential_without_leaking_value(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, 'clean.txt', 'hello')
    token = 'ghp_' + 'A' * 36
    _commit(repo, 'secret.txt', 'token=' + token)
    report = history.audit(str(repo))
    assert report['credentialFindingCount'] == 1
    assert report['ok'] is False
    finding = report['credentialFindings'][0]
    assert finding['type'] == 'GITHUB_TOKEN' and finding['path'] == 'secret.txt'
    assert finding['commits'], '要報出含 secret 嘅 commit'
    assert token not in json.dumps(report), '報告不可包含秘密原文'


def test_history_audit_detects_private_key_fragment(tmp_path):
    repo = _repo(tmp_path)
    marker = '-----BEGIN ' + 'PRIVATE KEY-----'
    _commit(repo, 'key.txt', marker + '\nabc\n')
    report = history.audit(str(repo))
    assert any(f['type'] == 'PRIVATE_KEY' for f in report['credentialFindings'])


def test_history_audit_selfhost_is_residual_not_credential(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, 'notes.txt', '/srv/' + 'aircon-compare/fake\n')
    report = history.audit(str(repo))
    assert report['credentialFindingCount'] == 0
    assert report['selfHostFindingCount'] >= 1
    assert report['ok'] is True
    assert any(f['type'] == 'SELFHOST_PATH' for f in report['selfHostFindings'])


def test_history_audit_git_error_fail_closed(tmp_path):
    bogus = tmp_path / 'not-a-repo'
    bogus.mkdir()
    with pytest.raises(RuntimeError):
        history.audit(str(bogus))


def test_history_audit_main_writes_report_outside_and_exit_codes(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, 'clean.txt', 'hello')
    report_path = tmp_path / 'report.json'
    assert history.main(['--repo', str(repo), '--all-refs', '--report', str(report_path)]) == 0
    saved = json.load(open(report_path, encoding='utf-8'))
    assert saved['ok'] is True and saved['credentialFindingCount'] == 0
    bogus = tmp_path / 'not-a-repo2'
    bogus.mkdir()
    assert history.main(['--repo', str(bogus), '--report', str(tmp_path / 'bad.json')]) == 1
