# -*- coding: utf-8 -*-
"""BigGo 警報來源 run 核實（offline；workflow_run payload fixtures）。"""
import copy
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import verify_biggo_alert_source as src  # noqa: E402
import verify_biggo_stage_artifacts as guard  # noqa: E402

REPO = 'CalvinLau1012/aircon-compare'


def _payload():
    return {
        'repository': {'full_name': REPO},
        'workflow_run': {
            'id': 36605423028,
            'name': '每日偵測 · 新機分批更新',
            'path': '.github/workflows/daily-update.yml',
            'event': 'schedule',
            'conclusion': 'success',
            'head_branch': 'master',
            'head_sha': 'a' * 40,
            'head_repository': {'full_name': REPO},
            'pull_requests': [],
        },
    }


def test_trusted_daily_success_accepted():
    assert src.verify(_payload(), expected_run_id=36605423028) == []


def test_workflow_dispatch_master_accepted():
    p = _payload()
    p['workflow_run']['event'] = 'workflow_dispatch'
    assert src.verify(p) == []


def test_pr_event_rejected():
    p = _payload()
    p['workflow_run']['event'] = 'pull_request'
    assert any('event' in e for e in src.verify(p))


def test_fork_repo_rejected():
    p = _payload()
    p['workflow_run']['head_repository']['full_name'] = 'attacker/aircon-compare'
    assert any('head_repository' in e for e in src.verify(p))


def test_cancelled_conclusion_rejected():
    p = _payload()
    p['workflow_run']['conclusion'] = 'cancelled'
    assert any('conclusion' in e for e in src.verify(p))


def test_failure_conclusion_accepted_for_alert_only():
    """daily 因 guard 阻斷而 failure：alert workflow 仍要跑（只讀，不 deploy）。"""
    p = _payload()
    p['workflow_run']['conclusion'] = 'failure'
    assert src.verify(p) == []


def test_non_master_branch_rejected():
    p = _payload()
    p['workflow_run']['head_branch'] = 'codex/evil'
    assert any('head_branch' in e for e in src.verify(p))


def test_bad_sha_rejected():
    p = _payload()
    p['workflow_run']['head_sha'] = 'abc'
    assert any('head_sha' in e for e in src.verify(p))


def test_pull_requests_field_rejected():
    p = _payload()
    p['workflow_run']['pull_requests'] = [{'number': 1}]
    assert any('pull_requests' in e for e in src.verify(p))


def test_wrong_workflow_rejected():
    p = _payload()
    p['workflow_run']['path'] = '.github/workflows/other.yml'
    p['workflow_run']['name'] = 'Other'
    errors = src.verify(p)
    assert any('name' in e for e in errors) and any('path' in e for e in errors)


def test_run_id_mismatch_rejected():
    assert any('run id' in e for e in src.verify(_payload(), expected_run_id=1))


def test_main_reads_event_file_and_exit_codes(tmp_path):
    good = tmp_path / 'good.json'
    good.write_text(json.dumps(_payload()), encoding='utf-8')
    assert src.main(['--event', str(good), '--run-id', '36605423028']) == 0
    bad = copy.deepcopy(_payload())
    bad['workflow_run']['conclusion'] = 'cancelled'
    bad_path = tmp_path / 'bad.json'
    bad_path.write_text(json.dumps(bad), encoding='utf-8')
    assert src.main(['--event', str(bad_path)]) == 1
    assert src.main(['--event', str(tmp_path / 'missing.json')]) == 2


def test_alert_red_while_pages_source_stays_success(tmp_path):
    """離線證明：受信任 daily（success）→ BigGo needs_review 時 alert 紅；
    同一 source 對 Pages 仍然係 success（verify_deploy_request 的 success-only 閘門
    由既有 test_non_success_conclusion_rejected 覆蓋，未放寬）。"""
    payload = _payload()
    assert src.verify(payload) == []
    assert payload['workflow_run']['conclusion'] == 'success'
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({'status': 'needs-review', 'alert': True}),
                      encoding='utf-8')
    assert guard.run_alert(str(status)) == 1, 'BigGo 警報必須紅'


def test_alert_red_for_apply_failed_and_daily_failure_source(tmp_path):
    p = _payload()
    p['workflow_run']['conclusion'] = 'failure'
    assert src.verify(p) == [], 'apply-failed 導致的 guard 阻斷仍要可由 alert 覆核'
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({'status': 'completed-apply-failed', 'alert': True}),
                      encoding='utf-8')
    assert guard.run_alert(str(status)) == 1
