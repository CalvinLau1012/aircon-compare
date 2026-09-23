# -*- coding: utf-8 -*-
"""R7：部署請求信任邊界（run attempt／workflow path／direct parent／metadata binding／polling）。"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_SPEC = importlib.util.spec_from_file_location(
    'verify_deploy_mod', os.path.join(BASE, 'scripts', 'verify_deploy_request.py'))
vdr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vdr)

REPO = 'owner/repo'
SRC = 'a' * 40  # placeholder overwritten by real tmp repo SHAs
DAILY_PATH = '.github/workflows/daily-update.yml@refs/heads/master'


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    return subprocess.run(['git'] + list(args), cwd=cwd, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', env=env)


def _head(cwd):
    return _git(cwd, 'rev-parse', 'HEAD').stdout.strip()


def _commit_file(cwd, name, content, message):
    with open(os.path.join(cwd, name), 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list))
                else content)
    _git(cwd, 'add', name)
    _git(cwd, 'commit', '-q', '-m', message)
    return _head(cwd)


def _chain(tmp_path, metadata_override=None):
    """base(src) → push product(commit=src+metadata)，origin/master=product。"""
    tmp = str(tmp_path)
    _git(tmp, 'init', '-q', '-b', 'master')
    _commit_file(tmp, 'f.txt', 'base', 'base')
    src = _head(tmp)
    meta = {'workflowRunId': '12345', 'commit': src, 'extra': 'ok'}
    if metadata_override is not None:
        meta = metadata_override(meta)
    push = _commit_file(tmp, 'metadata.json', meta, 'push product')
    _git(tmp, 'update-ref', 'refs/remotes/origin/master', push)
    return tmp, src, push


def _run_obj(src, **over):
    obj = {
        'id': 12345, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
        'event': 'schedule', 'head_branch': 'master', 'head_sha': src,
        'path': DAILY_PATH, 'repository': {'full_name': REPO},
    }
    obj.update(over)
    return obj


class FakeAPI:
    def __init__(self, responses, secret=None):
        self.responses = list(responses)
        self.calls = []
        self.secret = secret

    def __call__(self, path, token):
        self.calls.append((path, token))
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _verify(tmp, src, push, api, sleep=None, **over):
    params = dict(repo=REPO, source_run_id='12345', source_run_attempt='1',
                  api=api, token='TOKEN-MUST-NOT-LEAK', cwd=tmp,
                  sleep=sleep or FakeSleep(), poll_timeout=30, poll_interval=5,
                  metadata_path=os.path.join(tmp, 'metadata.json'))
    params.update(over)
    return vdr.verify('repository_dispatch', push, **params)


def test_valid_chain_passes_and_binds_run(tmp_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src))])
    result = _verify(tmp, src, push, api)
    assert result['ok'] is True
    assert result['sourceRunId'] == '12345' and result['runAttempt'] == 1
    assert result['workflowPath'] == vdr.DAILY_WORKFLOW_PATH
    assert result['sourceRunHeadSha'] == src and result['parent'] == src
    assert len(api.calls) == 1
    assert 'TOKEN-MUST-NOT-LEAK' not in json.dumps(result)


def test_in_progress_then_completed_passes_with_bounded_sleep(tmp_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, status='in_progress')),
                   (200, _run_obj(src, status='queued')),
                   (200, _run_obj(src))])
    sleep = FakeSleep()
    assert _verify(tmp, src, push, api, sleep=sleep)['ok'] is True
    assert len(api.calls) == 3
    assert sleep.calls == [5, 5]


def test_timeout_when_never_completed(tmp_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, status='in_progress'))])
    sleep = FakeSleep()
    with pytest.raises(vdr.VerifyError, match='timeout'):
        _verify(tmp, src, push, api, sleep=sleep, poll_timeout=10, poll_interval=5)
    assert len(sleep.calls) == 2  # max_polls = 2，之後即 timeout


def test_run_attempt_mismatch_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, run_attempt=2))])
    with pytest.raises(vdr.VerifyError, match='attempt'):
        _verify(tmp, src, push, api)


def test_run_id_mismatch_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, id=999))])
    with pytest.raises(vdr.VerifyError, match='run id'):
        _verify(tmp, src, push, api)


@pytest.mark.parametrize('bad_path', [
    '.github/workflows/release.yml@refs/heads/master',
    '.github/workflows/daily-update.yml@refs/heads/other',
    '.github/workflows/../daily-update.yml',
    '', None, 123,
])
def test_workflow_path_mismatch_rejected(tmp_path, bad_path):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, path=bad_path))])
    with pytest.raises(vdr.VerifyError, match='workflow path'):
        _verify(tmp, src, push, api)


def test_workflow_path_at_ref_and_dot_prefix_accepted(tmp_path):
    tmp, src, push = _chain(tmp_path)
    for good in (DAILY_PATH, './.github/workflows/daily-update.yml@refs/heads/master',
                 '.github/workflows/daily-update.yml'):
        api = FakeAPI([(200, _run_obj(src, path=good))])
        assert _verify(tmp, src, push, api)['ok'] is True


def test_ancestor_but_not_direct_parent_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    later = _commit_file(tmp, 'later.txt', 'later', 'later')
    _git(tmp, 'update-ref', 'refs/remotes/origin/master', later)
    api = FakeAPI([(200, _run_obj(src))])
    # src 係 later 嘅祖父，但 later 嘅 direct parent 係 push（唔等於 src）
    with pytest.raises(vdr.VerifyError, match='parent'):
        _verify(tmp, src, later, api)
    assert len(api.calls) == 1, 'API 只應該查詢一次 run'


def test_merge_commit_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    _git(tmp, 'checkout', '-q', '-b', 'side', src)
    _commit_file(tmp, 'side.txt', 'side', 'side')
    _git(tmp, 'checkout', '-q', 'master')
    _commit_file(tmp, 'main.txt', 'main', 'main')
    _git(tmp, 'merge', '--no-ff', '-q', '-m', 'merge side', 'side')
    merge = _head(tmp)
    _git(tmp, 'update-ref', 'refs/remotes/origin/master', merge)
    api = FakeAPI([(200, _run_obj(src))])
    with pytest.raises(vdr.VerifyError, match='一個 parent'):
        _verify(tmp, src, merge, api)


@pytest.mark.parametrize('override', [
    lambda m: {**m, 'workflowRunId': '99999'},
    lambda m: {**m, 'commit': 'b' * 40},
    lambda m: {'workflowRunId': 12345, 'commit': m['commit']},
    lambda m: ['not', 'object'],
])
def test_metadata_binding_mismatch_rejected(tmp_path, override):
    tmp, src, push = _chain(tmp_path, metadata_override=override)
    api = FakeAPI([(200, _run_obj(src))])
    with pytest.raises(vdr.VerifyError, match='metadata'):
        _verify(tmp, src, push, api)


def test_metadata_missing_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    os.remove(os.path.join(tmp, 'metadata.json'))
    api = FakeAPI([(200, _run_obj(src))])
    with pytest.raises(vdr.VerifyError, match='metadata'):
        _verify(tmp, src, push, api)


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out', 'neutral', None])
def test_non_success_conclusion_rejected(tmp_path, conclusion):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, conclusion=conclusion))])
    with pytest.raises(vdr.VerifyError, match='conclusion'):
        _verify(tmp, src, push, api)


@pytest.mark.parametrize('over', [
    {'repository': {'full_name': 'other/repo'}},
    {'event': 'pull_request'},
    {'head_branch': 'dev'},
    {'head_sha': 'not-a-sha'},
    {'status': 'completed', 'conclusion': 'success', 'head_sha': 'not-a-sha'},
])
def test_repo_event_branch_sha_mismatch_rejected(tmp_path, over):
    tmp, src, push = _chain(tmp_path)
    api = FakeAPI([(200, _run_obj(src, **over))])
    with pytest.raises(vdr.VerifyError):
        _verify(tmp, src, push, api)


def test_api_http_error_and_non_object_rejected(tmp_path):
    tmp, src, push = _chain(tmp_path)
    with pytest.raises(vdr.VerifyError, match='HTTP 500'):
        _verify(tmp, src, push, FakeAPI([(500, None)]))
    with pytest.raises(vdr.VerifyError, match='HTTP 404'):
        _verify(tmp, src, push, FakeAPI([(404, {'message': 'not found'})]))
    with pytest.raises(vdr.VerifyError):
        _verify(tmp, src, push, FakeAPI([(200, ['not', 'object'])]))


def test_error_and_report_do_not_leak_api_body_or_token(tmp_path):
    tmp, src, push = _chain(tmp_path)
    secret_body = {'secretGarbage': 'LEAK-ME-NOT', 'status': 'completed',
                   'conclusion': 'failure'}
    api = FakeAPI([(200, _run_obj(src, **secret_body))])
    try:
        _verify(tmp, src, push, api)
        raise AssertionError('should fail')
    except vdr.VerifyError as e:
        assert 'LEAK-ME-NOT' not in str(e)
        assert 'TOKEN-MUST-NOT-LEAK' not in str(e)


def test_push_event_only_requires_head_and_ancestor(tmp_path):
    tmp, src, push = _chain(tmp_path)
    assert vdr.verify('push', push, REPO, cwd=tmp)['ok'] is True
    # origin/master 退到 src：push 唔再係祖先
    _git(tmp, 'update-ref', 'refs/remotes/origin/master', src)
    with pytest.raises(vdr.VerifyError, match='祖先'):
        vdr.verify('push', push, REPO, cwd=tmp)
    with pytest.raises(vdr.VerifyError, match='HEAD'):
        vdr.verify('push', 'b' * 40, REPO, cwd=tmp)


def test_cli_valid_and_invalid(tmp_path, monkeypatch):
    tmp, src, push = _chain(tmp_path)
    report = tmp_path / 'report.json'
    rc = vdr.main(['--event', 'push', '--commit', push, '--repo', REPO,
                   '--cwd', tmp, '--report', str(report)])
    assert rc == 0 and json.loads(report.read_text(encoding='utf-8'))['ok'] is True
    rc = vdr.main(['--event', 'push', '--commit', 'b' * 40, '--repo', REPO, '--cwd', tmp])
    assert rc == 1
