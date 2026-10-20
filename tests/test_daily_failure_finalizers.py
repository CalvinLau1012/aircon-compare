# -*- coding: utf-8 -*-
"""Failure-path finalizers for the daily workflow (offline, no network)."""
import copy
import json
import os
from pathlib import Path
import sys

import yaml

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
import augment_sync_report as sync
import make_biggo_failure_status as biggo
import verify_biggo_stage_artifacts as guard

RUN = '123456789'
HEAD = 'a' * 40
EVENT = 'schedule'
AT = '2026-10-18T20:30:00Z'


def _args(report, *extra):
    return ['--report', str(report), '--run-id', RUN, '--head', HEAD,
            '--event', EVENT, *extra]


def test_sync_valid_report_is_preserved_and_identity_is_bound(tmp_path):
    path = tmp_path / 'sync.json'
    original = {'schemaVersion': 2, 'mode': 'sync-completed', 'ok': True,
                'applied': True, 'reason': 'applied', 'postStateVerified': True,
                'generatedAt': '2026-10-18T19:00:00Z', 'cycleId': 'c', 'stage': 3,
                'requests': {'token': 7, 'search': 9}}
    path.write_text(json.dumps(original), encoding='utf-8')
    assert sync.main(_args(path, '--upstream-outcome', 'success',
                           '--job-status', 'success', '--generated-at', AT)) == 0
    got = json.loads(path.read_text())
    assert got['ok'] is True and got['requests'] == {'token': 7, 'search': 9}
    assert got['sourceRunId'] == int(RUN) and got['headSha'] == HEAD
    assert got['sourceEvent'] == EVENT


def test_sync_skipped_after_upstream_failure_gets_honest_receipt(tmp_path):
    path = tmp_path / 'sync.json'
    assert sync.main(_args(path, '--upstream-outcome', 'skipped',
                           '--job-status', 'failure',
                           '--allow-upstream-fallback', '--generated-at', AT)) == 0
    got = json.loads(path.read_text())
    assert got['schemaVersion'] == 2 and got['mode'] == 'sync-completed'
    assert got['ok'] is False and got['applied'] is False
    assert got['postStateVerified'] is False and got['notAttempted'] is True
    assert got['reason'] == 'upstream-blocked'
    assert got['generatedAt'] == AT and got['sourceRunId'] == int(RUN)
    assert got['headSha'] == HEAD and got['sourceEvent'] == EVENT
    assert 'postFileHashes' not in got
    # Repeat is idempotent and does not invent a later success time.
    assert sync.main(_args(path, '--upstream-outcome', 'skipped',
                           '--job-status', 'failure',
                           '--allow-upstream-fallback',
                           '--generated-at', '2026-10-18T21:00:00Z')) == 0
    assert json.loads(path.read_text())['generatedAt'] == AT


def test_sync_missing_after_real_attempt_or_success_is_fail_closed(tmp_path):
    path = tmp_path / 'sync.json'
    assert sync.main(_args(path, '--upstream-outcome', 'success',
                           '--job-status', 'success', '--allow-upstream-fallback',
                           '--generated-at', AT)) == 2
    assert not path.exists()
    assert sync.main(_args(path, '--upstream-outcome', 'failure',
                           '--job-status', 'failure', '--allow-upstream-fallback',
                           '--generated-at', AT)) == 2
    assert not path.exists()


def test_sync_corrupt_or_wrong_identity_is_fail_closed(tmp_path):
    path = tmp_path / 'sync.json'
    path.write_text('{broken', encoding='utf-8')
    assert sync.main(_args(path, '--upstream-outcome', 'skipped',
                           '--job-status', 'failure',
                           '--allow-upstream-fallback')) == 2
    path.unlink()
    assert sync.main(['--report', str(path), '--run-id', 'bad', '--head', HEAD,
                      '--event', EVENT, '--upstream-outcome', 'skipped',
                      '--job-status', 'failure', '--allow-upstream-fallback']) == 2


def test_biggo_upstream_and_unknown_facts_are_distinct(tmp_path):
    up = tmp_path / 'up.json'
    assert biggo.main(['--out', str(up), '--kind', 'upstream-skipped',
                       '--run-id', RUN, '--head', HEAD, '--event', EVENT,
                       '--generated-at', AT]) == 0
    d = json.loads(up.read_text())
    assert d['status'] == 'blocked-upstream' and d['alert'] is True
    assert d['providerPhaseEntered'] is False and d['leaseAcquired'] is False
    assert d['requests'] == {'token': 0, 'search': 0}
    assert d['failureScope'] == 'upstream-workflow'
    unknown = tmp_path / 'unknown.json'
    assert biggo.main(['--out', str(unknown), '--kind', 'provider-phase-unknown',
                       '--run-id', RUN, '--head', HEAD, '--event', EVENT,
                       '--generated-at', AT]) == 0
    u = json.loads(unknown.read_text())
    assert u['status'] == 'blocked-provider-unknown' and u['alert'] is True
    assert u['providerPhaseEntered'] is None and u['leaseAcquired'] is None
    assert u['requests'] is None


def test_biggo_existing_valid_status_is_not_overwritten(tmp_path):
    path = tmp_path / 'status.json'
    original = {'schemaVersion': 1, 'status': 'needs-review', 'alert': True,
                'requests': {'token': 1, 'search': 0},
                'providerPhaseEntered': True, 'leaseAcquired': True}
    path.write_text(json.dumps(original), encoding='utf-8')
    assert biggo.main(['--out', str(path), '--kind', 'provider-phase-unknown',
                       '--run-id', RUN, '--head', HEAD, '--event', EVENT]) == 0
    assert json.loads(path.read_text()) == original


def test_alert_status_messages_are_about_upstream_or_unknown(tmp_path, capsys):
    up = tmp_path / 'up.json'
    biggo.main(['--out', str(up), '--kind', 'upstream-skipped',
                '--run-id', RUN, '--head', HEAD, '--event', EVENT, '--generated-at', AT])
    assert guard.run_alert(str(up), RUN, HEAD) == 1
    assert 'upstream workflow step failed' in capsys.readouterr().out
    unk = tmp_path / 'unknown.json'
    biggo.main(['--out', str(unk), '--kind', 'provider-phase-unknown',
                '--run-id', RUN, '--head', HEAD, '--event', EVENT, '--generated-at', AT])
    assert guard.run_alert(str(unk), RUN, HEAD) == 1
    assert 'UNKNOWN' in capsys.readouterr().out


def test_daily_workflow_wires_both_failure_finalizers(tmp_path):
    text = (BASE / '.github/workflows/daily-update.yml').read_text(encoding='utf-8')
    assert 'SYNC_OUTCOME: ${{ steps.completed_sync.outcome }}' in text
    assert '--allow-upstream-fallback' in text
    assert 'id: biggo_failure_receipt' in text
    assert 'scripts/make_biggo_failure_status.py' in text
    assert 'steps.biggo_failure_receipt.outcome' in text
    assert 'if-no-files-found: error' in text
    # A legal provider-skip receipt must not be created after an upstream failure.
    upstream = text.split('id: biggo_failure_receipt', 1)[1].split(
        '建立 provider-skip status', 1)[0]
    assert 'failure()' in upstream
    legal_skip = text.split('建立 provider-skip status', 1)[1].split(
        '上載 BigGo stage status', 1)[0]
    assert 'always()' not in legal_skip


def test_atomic_write_failure_is_nonzero(tmp_path):
    sync_dir = tmp_path / 'sync-dir'
    sync_dir.mkdir()
    assert sync.main(_args(sync_dir, '--upstream-outcome', 'skipped',
                           '--job-status', 'failure',
                           '--allow-upstream-fallback')) == 2
    big_dir = tmp_path / 'big-dir'
    big_dir.mkdir()
    assert biggo.main(['--out', str(big_dir), '--kind', 'upstream-skipped',
                       '--run-id', RUN, '--head', HEAD, '--event', EVENT]) == 2
