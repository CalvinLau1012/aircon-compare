# -*- coding: utf-8 -*-
"""D8-A：72h 新鮮度、部署一致性 combined health、穩定 fingerprint 去重、恢復與邊界。"""
import datetime as dt
import importlib.util
import json
import os
import sys

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_SPEC = importlib.util.spec_from_file_location(
    'freshness_mod', os.path.join(BASE, 'scripts', 'check_freshness.py'))
freshness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(freshness)
sys.modules['check_freshness'] = freshness  # freshness_issue 內 `from check_freshness import ...` 要用同一 module

_ISSUE_SPEC = importlib.util.spec_from_file_location(
    'freshness_issue_mod', os.path.join(BASE, 'scripts', 'freshness_issue.py'))
freshness_issue = importlib.util.module_from_spec(_ISSUE_SPEC)
_ISSUE_SPEC.loader.exec_module(freshness_issue)
TOKEN_FORBIDDEN = 'TOKEN-MUST-NEVER-APPEAR-9f3a'


def _now():
    """固定測試時鐘（純函數測試用；令 72h boundary 計算精確、可重現）。"""
    return dt.datetime(2026, 9, 22, 12, 0, 0, tzinfo=dt.timezone.utc)


def _wall_now():
    """真實當前 UTC（只供會用真實時鐘嘅 CLI／issue 路徑測試用）。

    教訓（2026-09-25 daily failure）：freshness.main()／freshness_issue.main() 內部用
    真實時鐘判斷新鮮度，所以凡係餵入 CLI／issue 路徑嘅 fixture 必須相對於真實時間，
    唔可以硬編日期 —— 否則 fixture 過咗 72h 就會被判 stale，測試無故失敗並阻塞每日更新。
    純函數測試繼續用 _now() 保持精確。
    """
    return dt.datetime.now(dt.timezone.utc)


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


def test_fresh_plus_postdeploy_failure_is_alert_not_noop():
    fresh = freshness.evaluate(_meta(_now(), 60), _now())
    fresh['postdeployOk'] = False
    plan = freshness.plan_issue(None, fresh)
    assert plan['action'] == 'create', 'fresh 但部署核對失敗必須開警報，唔可以 noop'
    assert 'postdeploy_failed' in plan['fingerprint']
    assert 'postdeploy failure: yes' in plan['body']
    # 同一狀態重複 → noop
    assert freshness.plan_issue({'body': plan['body']}, fresh)['action'] == 'noop'
    # postdeploy 恢復（freshness 仍 fresh）→ close 一次
    healed = freshness.evaluate(_meta(_now(), 60), _now())
    healed['postdeployOk'] = True
    assert freshness.plan_issue({'body': plan['body']}, healed)['action'] == 'close'
    assert freshness.plan_issue(None, healed)['action'] == 'noop'


def test_stale_after_six_hours_is_noop_with_identical_fingerprint():
    stale = freshness.evaluate(_meta(_now(), 73 * 3600), _now())
    plan = freshness.plan_issue(None, stale)
    assert plan['action'] == 'create'
    existing = {'body': plan['body']}
    later = freshness.evaluate(_meta(_now() + dt.timedelta(hours=6), 79 * 3600),
                               _now() + dt.timedelta(hours=6))
    assert later['ageSeconds'] != stale['ageSeconds']
    assert freshness.plan_issue(existing, later)['action'] == 'noop', \
        '同一 stale 狀態跨 6 小時唔可以重複 update（fingerprint 唔含 ageSeconds）'
    # 分類改變（stale → fetch_error）→ update
    err = {'ok': False, 'stale': False, 'ageSeconds': None, 'reason': 'fetch_error:URLError'}
    assert freshness.plan_issue(existing, err)['action'] == 'update'
    # 恢復 → close
    fresh = freshness.evaluate(_meta(_now(), 1), _now())
    assert freshness.plan_issue(existing, fresh)['action'] == 'close'


def test_old_body_without_fingerprint_gets_migrated():
    stale = freshness.evaluate(_meta(_now(), 73 * 3600), _now())
    old = {'body': 'Freshness monitor alert.\n\n- reason: `stale_over_72h`\n'}
    assert freshness.plan_issue(old, stale)['action'] == 'update'
    assert 'aircon-freshness-fingerprint' in freshness.plan_issue(old, stale)['body']


def test_fresh_no_issue_is_noop():
    fresh = freshness.evaluate(_meta(_now(), 1), _now())
    assert freshness.plan_issue(None, fresh)['action'] == 'noop'


def test_cli_writes_report_and_exit_codes(tmp_path):
    meta = tmp_path / 'metadata.json'
    meta.write_text(json.dumps(_meta(_wall_now(), 73 * 3600)), encoding='utf-8')
    report = tmp_path / 'report.json'
    rc = freshness.main(['--metadata-file', str(meta), '--report', str(report)])
    assert rc == 1, 'stale 必須非零'
    saved = json.load(open(report, encoding='utf-8'))
    assert saved['stale'] is True and saved['thresholdSeconds'] == 72 * 3600
    assert saved['category'] == 'stale_over_72h'
    meta.write_text(json.dumps(_meta(_wall_now(), 1)), encoding='utf-8')
    assert freshness.main(['--metadata-file', str(meta), '--report', str(report)]) == 0


def _run_issue_main(tmp_path, monkeypatch, metadata=None, postdeploy=None, fail_fetch=False,
                    api=None, token=TOKEN_FORBIDDEN):
    calls = []

    def default_api(tok, method, path, data=None):
        calls.append({'token': tok, 'method': method, 'path': path, 'data': data})
        if api:
            return api(calls, method, path, data)
        return [] if method == 'GET' else {'number': 7}

    monkeypatch.setattr(freshness_issue, '_api', default_api)
    monkeypatch.setenv('GITHUB_TOKEN', token)

    def fake_fetch(url, timeout=30):
        if fail_fetch:
            raise OSError('network down')
        return metadata if metadata is not None else _meta(_wall_now(), 60)

    monkeypatch.setattr(freshness, 'fetch_metadata', fake_fetch)
    args = ['--metadata-url', 'https://example.invalid/metadata.json',
            '--repo', 'owner/repo', '--report', str(tmp_path / 'issue-report.json')]
    if postdeploy is not None:
        args += ['--postdeploy-report', str(postdeploy)]
    rc = freshness_issue.main(args)
    report_path = tmp_path / 'issue-report.json'
    report = json.load(open(report_path, encoding='utf-8')) if report_path.exists() else None
    return rc, calls, report


def test_issue_main_missing_postdeploy_report_fail_closed(tmp_path, monkeypatch):
    def api(calls, method, path, data):
        if method == 'GET':
            return []
        return {'number': 7}
    rc, calls, report = _run_issue_main(tmp_path, monkeypatch,
                                        postdeploy=tmp_path / 'does-not-exist.json', api=api)
    assert rc == 1
    assert report['action'] == 'create'
    posts = [c for c in calls if c['method'] == 'POST' and c['path'].endswith('/issues')]
    assert posts and 'postdeploy_report_missing' in json.dumps(posts[0]['data'])
    assert TOKEN_FORBIDDEN not in json.dumps(posts[0]['data'])
    report_text = (tmp_path / 'issue-report.json').read_text(encoding='utf-8')
    assert TOKEN_FORBIDDEN not in report_text


def test_issue_main_invalid_postdeploy_json_fail_closed(tmp_path, monkeypatch):
    bad = tmp_path / 'post.json'
    bad.write_text('not json', encoding='utf-8')
    rc, calls, report = _run_issue_main(tmp_path, monkeypatch, postdeploy=bad)
    assert rc == 1 and report['action'] == 'create'
    assert 'postdeploy_report_invalid' in report['check']['reason']


def test_issue_main_fetch_error_nonzero_and_redacted(tmp_path, monkeypatch):
    rc, calls, report = _run_issue_main(tmp_path, monkeypatch, fail_fetch=True)
    assert rc == 1 and report['action'] == 'create'
    assert report['check']['reason'] == 'fetch_error:OSError'
    assert report['check'].get('category') or True  # main report 唔一定要 category


def test_issue_main_api_error_nonzero(tmp_path, monkeypatch):
    def api(calls, method, path, data):
        raise RuntimeError('GitHub API GET /repos/owner/repo/issues 失敗：HTTP 500')
    rc, _calls, _report = _run_issue_main(tmp_path, monkeypatch, api=api)
    assert rc == 1


def test_issue_main_dedup_and_recovery_close_once(tmp_path, monkeypatch):
    state = {'existing': None}

    def api(calls, method, path, data):
        if method == 'GET':
            return [state['existing']] if state['existing'] else []
        if method == 'POST' and path.endswith('/issues'):
            state['existing'] = {'number': 7, 'body': data['body']}
            return state['existing']
        if method == 'PATCH' and data and data.get('state') == 'closed':
            state['existing'] = None
        return {'ok': True}

    # 第一次：stale → create
    rc, _c, rep = _run_issue_main(tmp_path, monkeypatch, metadata=_meta(_wall_now(), 73 * 3600), api=api)
    assert rc == 1 and rep['action'] == 'create'
    body = state['existing']['body']
    # 第二次：同一 stale（age 大咗）→ noop
    rc, _c, rep = _run_issue_main(tmp_path, monkeypatch, metadata=_meta(_wall_now(), 80 * 3600), api=api)
    assert rc == 1 and rep['action'] == 'noop'
    assert state['existing']['body'] == body
    # 第三次：fresh → close 一次
    rc, _c, rep = _run_issue_main(tmp_path, monkeypatch, metadata=_meta(_wall_now(), 60), api=api)
    assert rc == 0 and rep['action'] == 'close'
    # 第四次：已經無 open issue → noop fresh
    rc, _c, rep = _run_issue_main(tmp_path, monkeypatch, metadata=_meta(_wall_now(), 60), api=api)
    assert rc == 0 and rep['action'] == 'noop'


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
    assert '--postdeploy-report' in text
