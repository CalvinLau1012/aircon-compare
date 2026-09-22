# -*- coding: utf-8 -*-
"""D8-A：72h 新鮮度、告警去重、恢復與邊界。"""
import datetime as dt
import importlib.util
import json
import os
import sys

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'freshness_mod', os.path.join(BASE, 'scripts', 'check_freshness.py'))
freshness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(freshness)


def _now():
    return dt.datetime(2026, 9, 22, 12, 0, 0, tzinfo=dt.timezone.utc)


def _meta(now, seconds):
    ts = (now - dt.timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'datasetRetrievedAt': ts}


def test_71h59m59s_passes():
    r = freshness.evaluate(_meta(_now(), 71 * 3600 + 59 * 60 + 59), _now())
    assert r['ok'] is True and r['stale'] is False and r['ageSeconds'] == 71 * 3600 + 59 * 60 + 59


def test_72h_boundary_passes():
    r = freshness.evaluate(_meta(_now(), 72 * 3600), _now())
    assert r['ok'] is True and r['stale'] is False
    r2 = freshness.evaluate(_meta(_now(), 72 * 3600 + 1), _now())
    assert r2['ok'] is True and r2['stale'] is True


def test_missing_invalid_and_future_timestamp_hard_fail():
    for meta in ({}, {'datasetRetrievedAt': 'not-a-time'},
                 {'datasetRetrievedAt': '2026-09-22T12:00:01Z'}):
        r = freshness.evaluate(meta, _now())
        assert r['ok'] is False and r['stale'] is False


def test_issues_are_deduplicated_on_same_state():
    stale = freshness.evaluate(_meta(_now(), 73 * 3600), _now())
    plan = freshness.plan_issue(None, stale)
    assert plan['action'] == 'create'
    existing = {'body': plan['body']}
    assert freshness.plan_issue(existing, stale)['action'] == 'noop', '同狀態唔可以重複更新'
    changed = dict(stale, reason='another_reason')
    assert freshness.plan_issue(existing, changed)['action'] == 'update', '狀態改變才更新'
    fresh = freshness.evaluate(_meta(_now(), 1), _now())
    assert freshness.plan_issue(existing, fresh)['action'] == 'close', '恢復要關閉 issue'


def test_fresh_no_issue_is_noop():
    fresh = freshness.evaluate(_meta(_now(), 1), _now())
    assert freshness.plan_issue(None, fresh)['action'] == 'noop'


def test_cli_writes_report_and_exit_codes(tmp_path):
    meta = tmp_path / 'metadata.json'
    meta.write_text(json.dumps(_meta(_now(), 73 * 3600)), encoding='utf-8')
    report = tmp_path / 'report.json'
    rc = freshness.main(['--metadata-file', str(meta), '--report', str(report)])
    assert rc == 1, 'stale 必須非零'
    saved = json.load(open(report, encoding='utf-8'))
    assert saved['stale'] is True and saved['thresholdSeconds'] == 72 * 3600
    meta.write_text(json.dumps(_meta(_now(), 1)), encoding='utf-8')
    assert freshness.main(['--metadata-file', str(meta), '--report', str(report)]) == 0


def test_monitor_workflow_contract():
    p = os.path.join(BASE, '.github', 'workflows', 'freshness-monitor.yml')
    text = open(p, encoding='utf-8').read()
    wf = yaml.safe_load(text)
    assert "cron: '0 */6 * * *'" in text
    assert 'workflow_dispatch' in wf[True] if True in wf else wf['on']
    assert wf['permissions']['issues'] == 'write'
    assert 'scripts/postdeploy_check.py' in text
    assert 'scripts/freshness_issue.py' in text
    assert 'if: always()' in text
    assert 'mismatch' in text or '--postdeploy-report' in text
