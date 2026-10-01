# -*- coding: utf-8 -*-
"""BigGo CI guard／alert（P0）離線回歸。"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import verify_biggo_stage_artifacts as guard  # noqa: E402


def _status(tmp_path, obj):
    path = tmp_path / 'status.json'
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    return str(path)


def test_guard_passes_when_no_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: [])
    assert guard.run_guard('.', None) == 0


def test_guard_blocks_diff_without_status(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['biggo_prices.json'])
    assert guard.run_guard('.', None) == 1


def test_guard_blocks_needs_review_status(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['prices_meta.json'])
    path = _status(tmp_path, {'status': 'needs-review', 'localApply': 'none'})
    assert guard.run_guard('.', path) == 1


def test_guard_blocks_apply_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['biggo_prices.json'])
    path = _status(tmp_path, {'status': 'completed-apply-failed', 'localApply': 'failed',
                              'published': True})
    assert guard.run_guard('.', path) == 1


def test_guard_passes_confirmed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['biggo_prices.json',
                                                               'prices_meta.json'])
    path = _status(tmp_path, {'status': 'completed', 'localApply': 'applied',
                              'published': True, 'imported': False})
    assert guard.run_guard('.', path) == 0


def test_guard_passes_idempotent_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['biggo_prices.json'])
    path = _status(tmp_path, {'status': 'completed-idempotent-applied',
                              'localApply': 'noop', 'imported': True})
    assert guard.run_guard('.', path) == 0


def test_expect_clean_blocks_any_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: ['model_status.json'])
    assert guard.run_guard('.', None, expect_clean=True) == 1


def test_alert_missing_status_is_red(tmp_path):
    assert guard.run_alert(None) == 1


def test_alert_needs_review_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'needs-review', 'alert': True,
                              'rerun': 'prohibited'})
    assert guard.run_alert(path) == 1


def test_alert_apply_failed_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'completed-apply-failed', 'alert': False})
    assert guard.run_alert(path) == 1


def test_alert_completed_is_green(tmp_path):
    path = _status(tmp_path, {'status': 'completed', 'alert': False})
    assert guard.run_alert(path) == 0


def test_alert_error_status_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'error', 'alert': True})
    assert guard.run_alert(path) == 1


def test_alert_empty_status_is_red(tmp_path):
    path = _status(tmp_path, {'schemaVersion': 1, 'status': None})
    assert guard.run_alert(path) == 1


def test_guard_real_git_worktree(tmp_path):
    """真 git 工作樹：有 diff 冇 status → 阻斷；confirmed status → pass。"""
    repo = str(tmp_path / 'repo')
    os.makedirs(repo)
    subprocess.run(['git', 'init', '-q', repo], check=True, capture_output=True)
    subprocess.run(['git', '-C', repo, 'config', 'user.email', 'a@b.c'], check=True)
    subprocess.run(['git', '-C', repo, 'config', 'user.name', 't'], check=True)
    for name in guard.PATHS:
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            f.write('{}')
    subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
    subprocess.run(['git', '-C', repo, 'commit', '-qm', 'init'], check=True)
    with open(os.path.join(repo, 'biggo_prices.json'), 'w', encoding='utf-8') as f:
        json.dump({'M1': {'price': '$1起'}}, f)
    assert guard.run_guard(repo, None) == 1
    good = _status(tmp_path, {'status': 'completed', 'localApply': 'applied',
                              'published': True})
    assert guard.run_guard(repo, good) == 0


def test_alert_blocked_incomplete_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'blocked-incomplete', 'alert': True,
                              'rerun': 'prohibited'})
    assert guard.run_alert(path) == 1


def test_alert_quota_window_unsupported_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'blocked-quota-window-unsupported',
                              'alert': True})
    assert guard.run_alert(path) == 1


def test_alert_blocked_stage_gap_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'blocked-stage-gap', 'alert': True,
                              'rerun': 'prohibited'})
    assert guard.run_alert(path) == 1


def test_alert_blocked_force_cycle_incomplete_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'blocked-force-cycle-incomplete',
                              'alert': True, 'rerun': 'prohibited'})
    assert guard.run_alert(path) == 1


def test_alert_blocked_stale_is_red(tmp_path):
    path = _status(tmp_path, {'status': 'blocked-stale', 'alert': True,
                              'rerun': 'prohibited'})
    assert guard.run_alert(path) == 1
