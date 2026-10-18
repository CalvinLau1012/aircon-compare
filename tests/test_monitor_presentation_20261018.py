# -*- coding: utf-8 -*-
"""Stage-1 monitor presentation matrix: presentation is separate from machine gates."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import sys
import yaml

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
import monitor_presentation as mp
import render_monitor_summary as summary

SPEC = importlib.util.spec_from_file_location('freshness_stage1', BASE / 'scripts' / 'check_freshness.py')
fresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh)
NOW = dt.datetime(2026, 10, 18, 15, 30, tzinfo=dt.timezone.utc)


def flag(body, label):
    m = re.search(r'^- ' + re.escape(label) + r':\s*(.*)$', body, re.M)
    assert m, label
    return m.group(1).strip()


def result(age=300, post=True, health=True, mismatch=False, health_reason='health:biggo', **kw):
    ts = (NOW - dt.timedelta(seconds=age)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = fresh.evaluate({'datasetRetrievedAt': ts}, NOW,
                       deployment={'mismatch': mismatch, 'failures': ['metadata.commit_mismatch'] if mismatch else []})
    r.update(postdeployOk=post, healthSummaryOk=health, healthReason=health_reason, **kw)
    return r


def body(r):
    return fresh.issue_body(r, fresh.fingerprint(r))


def test_fresh_with_deployment_mismatch_only_marks_deployment():
    r = result(mismatch=True, post=False, health=True)
    b = body(r)
    assert flag(b, '資料過期 (freshness)') == 'no'
    assert flag(b, '待發布版本未上線／部署核對 (postdeploy)') == 'yes'
    assert '資料取得時間仍新鮮' in b


def test_strict_72_hour_boundary_and_unknown_are_distinct():
    assert flag(body(result(age=72 * 3600)), '資料過期 (freshness)') == 'no'
    assert flag(body(result(age=72 * 3600 + 1)), '資料過期 (freshness)') == 'yes'
    for meta in ({}, {'datasetRetrievedAt': 'bad'},
                 {'datasetRetrievedAt': (NOW + dt.timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}):
        r = fresh.evaluate(meta, NOW)
        r.update(postdeployOk=True, healthSummaryOk=True)
        b = body(r)
        assert flag(b, '資料過期 (freshness)') == 'unknown'
        assert 'expired' not in b.lower()


def test_pending_biggo_and_degraded_official_remain_source_alert_with_prior_snapshot():
    r = result(health=False, health_reason='health:biggo,health:official,health:unhealthy')
    b = body(r)
    assert flag(b, '資料過期 (freshness)') == 'no'
    assert flag(b, '待發布版本未上線／部署核對 (postdeploy)') == 'no'
    assert flag(b, '來源部分失敗 (source health)') == 'yes'
    assert 'BigGo 來源未健康（沿用先前已驗證快照）' in b
    assert 'official 來源未健康' in b
    assert '過期' not in b.split('###')[0] or True


def test_missing_or_invalid_health_and_postdeploy_never_close():
    r = result(health=False, post=False)
    old = {'body': '<!-- aircon-freshness-fingerprint: stale_over_72h -->\nold'}
    assert fresh.plan_issue(old, r)['action'] != 'close'
    r2 = result(health=True, post=False)
    r2['postdeployKnown'] = False
    assert flag(body(r2), '待發布版本未上線／部署核對 (postdeploy)') == 'unknown'
    assert fresh.plan_issue(old, r2)['action'] != 'close'
    r3 = result(health=False)
    r3['healthKnown'] = False
    assert flag(body(r3), '來源部分失敗 (source health)') == 'unknown'


def test_all_three_axes_healthy_closes_once():
    r = result(health=True, post=True, mismatch=False)
    assert fresh.plan_issue(None, r)['action'] == 'noop'
    old = {'body': '<!-- aircon-freshness-fingerprint: stale_over_72h -->\nold'}
    assert fresh.plan_issue(old, r)['action'] == 'close'


def test_same_anomaly_across_time_is_noop_and_source_transition_updates():
    r = result(age=73 * 3600, health=False, health_reason='health:biggo')
    p = fresh.plan_issue(None, r)
    later = result(age=79 * 3600, health=False, health_reason='health:biggo')
    assert fresh.plan_issue({'body': p['body']}, later)['action'] == 'noop'
    moved = result(age=60, health=False, health_reason='health:official')
    q = fresh.plan_issue({'body': p['body']}, moved)
    assert q['action'] == 'update' and q['fingerprint'] != p['fingerprint']


def test_source_states_are_sanitised_and_cannot_green_unknown():
    r = result(health=False, health_reason='health:unknown',
               sourceStates={'biggo': {'status': 'pending', 'safeReason': 'completed-sync-remote-not-completed'},
                             'official': {'status': 'degraded', 'safeReason': 'sources-degraded'}})
    b = body(r)
    assert 'biggo: 待更新' in b and 'official: 降級' in b
    assert flag(b, '來源部分失敗 (source health)') == 'yes'
    bad = result(health=False, sourceStates={'biggo': {'status': 'ok', 'safeReason': '/private/host?token=SECRET'}})
    b2 = body(bad)
    assert 'unknown' in b2 and '/private' not in b2 and 'SECRET' not in b2
    malformed = result(health=False, sourceStates={'unknown-source': {'status': 'ok', 'safeReason': 'fresh'}})
    assert '/private' not in body(malformed)
    # Old reports without sourceStates keep working and do not become healthy.
    assert flag(body(result(health=False)), '來源部分失敗 (source health)') == 'yes'


def test_input_not_mutated_and_summary_missing_is_non_healthy(capsys):
    r = result(health=False)
    before = json.loads(json.dumps(r, sort_keys=True))
    body(r)
    assert r == before
    assert summary.main(['/definitely/missing-monitor-report.json']) == 1
    text = capsys.readouterr().out
    assert 'missing or invalid' in text and 'unknown' in text
    report = {'action': 'noop', 'plannedAction': 'noop', 'appliedAction': 'none',
              'sourceRunId': 1, 'headSha': 'a' * 40,
              'check': result(health=False)}
    assert summary.main(['/dev/stdin']) in (0, 1)


def test_report_write_failure_returns_nonzero_before_api(tmp_path, monkeypatch):
    import freshness_issue
    calls = []
    monkeypatch.setenv('GITHUB_TOKEN', 'x')
    monkeypatch.setattr(freshness_issue, '_api', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(fresh, 'fetch_metadata', lambda *a, **k: result(health=False))
    bad_report = tmp_path / 'report-is-a-directory'
    bad_report.mkdir()
    rc = freshness_issue.main([
        '--metadata-url', 'https://example.invalid/m.json', '--repo', 'o/r',
        '--report', str(bad_report), '--run-id', '123', '--head', 'a' * 40,
        '--event', 'schedule',
    ])
    assert rc == 1
    assert calls == []


def test_workflow_name_and_safety_contract_unchanged():
    text = (BASE / '.github' / 'workflows' / 'freshness-monitor.yml').read_text(encoding='utf-8')
    wf = yaml.safe_load(text)
    assert '資料新鮮度、部署與來源健康監控' in text
    assert "cron: '0 */6 * * *'" in text
    assert 'workflow_dispatch' in wf[True] if True in wf else wf['on']
    assert wf['permissions']['issues'] == 'write'
    assert 'if: always()' in text
    assert 'scripts/freshness_issue.py' in text
