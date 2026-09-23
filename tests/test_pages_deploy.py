# -*- coding: utf-8 -*-
"""D2-A：GitHub Pages Actions 統一部署安全契約（envelope／dispatch／輸出目錄安全）。"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SPEC = importlib.util.spec_from_file_location(
    'build_pages_artifact_mod', os.path.join(BASE, 'scripts', 'build_pages_artifact.py'))
bpa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bpa)

WORKFLOWS = os.path.join(BASE, '.github', 'workflows')
PAYLOAD = ('index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv')
CSV_BYTES = '品牌,型號\nA,M1\nB,M2\n'.encode('utf-8')


def _load(name):
    with open(os.path.join(WORKFLOWS, name), encoding='utf-8') as f:
        return yaml.safe_load(f)


def _text(name):
    return open(os.path.join(WORKFLOWS, name), encoding='utf-8').read()


def _sha256_bytes(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _make_repo(tmp_path, files=PAYLOAD, metadata_overrides=None, sidecars=None,
               optional=('emsd_receipt.json', 'emsd_raw_receipt.json', 'official_batch_status.json')):
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith('.csv'):
            p.write_bytes(CSV_BYTES)
        else:
            p.write_bytes(('content:' + rel).encode('utf-8'))
    meta = {
        'schemaVersion': '1.0.0',
        'version': '1.2.9',
        'build': 'B20260923.PR-FIXTURE',
        'commit': 'a' * 40,
        'deployTime': '2026-09-23T00:00:00Z',
        'workflowRunId': '12345',
        'deploymentType': 'release',
        'datasetDate': '2026-09-22',
        'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-09-22T23:00:00Z',
        'datasetSourceUrl': 'https://example.invalid/emsd',
        'datasetSnapshotId': 'emsd-2026-09-22',
        'datasetHash': _sha256_bytes(CSV_BYTES),
        'recordCount': 2,
        'rawRecordCount': 2,
        'registrationCount': 2,
        'modelCount': 2,
    }
    meta['releasePayloadHash'] = bpa._load_gen_metadata().hash_files(list(files), base=str(tmp_path))
    if metadata_overrides:
        meta.update(metadata_overrides)
    (tmp_path / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'deploy_payload.json').write_text(
        json.dumps({'schemaVersion': '1.0.0', 'files': list(files)}), encoding='utf-8')
    (tmp_path / 'deploy_envelope.json').write_text(json.dumps({
        'schemaVersion': '1.0.0',
        'requiredFiles': list(files) + ['metadata.json'],
        'optionalFiles': list(optional),
    }), encoding='utf-8')
    if sidecars:
        for name, payload in sidecars.items():
            (tmp_path / name).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return {
        'repo': str(tmp_path),
        'payload': str(tmp_path / 'deploy_payload.json'),
        'envelope': str(tmp_path / 'deploy_envelope.json'),
        'metadata': str(tmp_path / 'metadata.json'),
        'meta': meta,
    }


def _build(fixture, out, **kw):
    return bpa.build(fixture['repo'], fixture['payload'], fixture['envelope'],
                     fixture['metadata'], str(out), **kw)


def test_pages_artifact_contains_payload_and_metadata_exactly(tmp_path):
    fx = _make_repo(tmp_path)
    out = tmp_path / '_site'
    result = _build(fx, out)
    assert result['ok'] is True
    assert set(result['files']) == set(PAYLOAD) | {'metadata.json'}
    actual = {os.path.relpath(os.path.join(r, n), out)
              for r, _d, ns in os.walk(out) for n in ns}
    assert actual == set(PAYLOAD) | {'metadata.json'}
    for rel in actual:
        assert (out / rel).read_bytes() == (tmp_path / rel).read_bytes()


def test_pages_artifact_includes_consistent_sidecars(tmp_path):
    raw = {'schemaVersion': 1, 'success': True, 'datasetHash': _sha256_bytes(CSV_BYTES)}
    raw_text = json.dumps(raw, ensure_ascii=False)
    receipt = {'success': True, 'datasetHash': _sha256_bytes(CSV_BYTES),
               'rawReceiptHash': _sha256_bytes(raw_text.encode('utf-8'))}
    fx = _make_repo(tmp_path, sidecars={'emsd_raw_receipt.json': raw,
                                        'emsd_receipt.json': receipt,
                                        'official_batch_status.json': {
                                            'decision': 'pending', 'generatedAt': '2026-09-23T00:00:00Z'}})
    result = _build(fx, tmp_path / '_site')
    assert 'emsd_receipt.json' in result['files']
    assert 'emsd_raw_receipt.json' in result['files']
    assert 'official_batch_status.json' in result['files']
    assert (tmp_path / '_site' / 'emsd_raw_receipt.json').exists()


def test_pages_artifact_skips_stale_raw_receipt(tmp_path):
    raw = {'schemaVersion': 1, 'success': True, 'datasetHash': 'sha256:' + '0' * 64}
    fx = _make_repo(tmp_path, sidecars={'emsd_raw_receipt.json': raw,
                                        'emsd_receipt.json': {'success': True,
                                                              'datasetHash': _sha256_bytes(CSV_BYTES)}})
    result = _build(fx, tmp_path / '_site')
    assert 'emsd_raw_receipt.json' not in result['files']
    skipped = {s['file']: s['reason'] for s in result['optionalSkipped']}
    assert skipped['emsd_raw_receipt.json'] == 'stale-or-mismatch-datasetHash'
    assert not (tmp_path / '_site' / 'emsd_raw_receipt.json').exists()


def test_pages_artifact_skips_raw_receipt_without_csv_receipt_binding(tmp_path):
    raw = {'schemaVersion': 1, 'success': True, 'datasetHash': _sha256_bytes(CSV_BYTES)}
    fx = _make_repo(tmp_path, sidecars={'emsd_raw_receipt.json': raw,
                                        'emsd_receipt.json': {'success': True,
                                                              'datasetHash': _sha256_bytes(CSV_BYTES)}})
    result = _build(fx, tmp_path / '_site')
    skipped = {s['file']: s['reason'] for s in result['optionalSkipped']}
    assert 'emsd_raw_receipt.json' not in result['files']
    assert skipped['emsd_raw_receipt.json'] in ('csv-receipt-rawReceiptHash-mismatch',
                                                'stale-or-mismatch-datasetHash')


@pytest.mark.parametrize('overrides', [
    {'releasePayloadHash': 'sha256:' + '0' * 64},
    {'datasetHash': 'sha256:' + '0' * 64},
    {'version': '0.0.1'},
    {'rawRecordCount': 999},
])
def test_pages_artifact_rejects_metadata_mismatch(tmp_path, overrides):
    fx = _make_repo(tmp_path, metadata_overrides=overrides)
    with pytest.raises(ValueError):
        _build(fx, tmp_path / '_site')


def test_pages_artifact_rejects_envelope_missing_payload_or_metadata(tmp_path):
    fx = _make_repo(tmp_path)
    env = json.loads((tmp_path / 'deploy_envelope.json').read_text(encoding='utf-8'))
    env['requiredFiles'] = [f for f in env['requiredFiles'] if f != 'metadata.json']
    (tmp_path / 'deploy_envelope.json').write_text(json.dumps(env), encoding='utf-8')
    with pytest.raises(ValueError, match='metadata.json'):
        _build(fx, tmp_path / '_site')
    env['requiredFiles'] = [f for f in PAYLOAD if f != 'index.html'] + ['metadata.json']
    (tmp_path / 'deploy_envelope.json').write_text(json.dumps(env), encoding='utf-8')
    with pytest.raises(ValueError, match='payload'):
        _build(fx, tmp_path / '_site')


@pytest.mark.parametrize('bad', ['../escape.html', '/abs.html', 'a\\b.html', 'a/../b.html',
                                 'a//b.html', '   x.html'])
def test_pages_artifact_rejects_bad_paths(tmp_path, bad):
    manifest = tmp_path / 'deploy_payload.json'
    manifest.write_text(json.dumps({'files': [bad]}), encoding='utf-8')
    envelope = tmp_path / 'deploy_envelope.json'
    envelope.write_text(json.dumps({'requiredFiles': [bad, 'metadata.json']}), encoding='utf-8')
    with pytest.raises(ValueError):
        bpa.build(str(tmp_path), str(manifest), str(envelope),
                  str(tmp_path / 'metadata.json'), str(tmp_path / '_site'))


def test_pages_artifact_check_only_never_touches_out(tmp_path):
    fx = _make_repo(tmp_path)
    out = tmp_path / 'occupied'
    out.mkdir()
    (out / 'keep.txt').write_text('untouched', encoding='utf-8')
    result = _build(fx, out, check_only=True)
    assert result['checkOnly'] is True
    assert (out / 'keep.txt').read_text(encoding='utf-8') == 'untouched'
    assert sorted(os.listdir(out)) == ['keep.txt']


def test_pages_artifact_rejects_dangerous_out_dirs(tmp_path):
    fx = _make_repo(tmp_path)
    with pytest.raises(ValueError, match='repo 根'):
        _build(fx, tmp_path)
    with pytest.raises(ValueError, match='祖先'):
        _build(fx, tmp_path.parent)
    with pytest.raises(ValueError, match=r'\.git'):
        _build(fx, tmp_path / '.git' / 'site')
    with pytest.raises(ValueError, match='重疊'):
        _build(fx, tmp_path / 'index.html')


def test_pages_artifact_refuses_unsealed_existing_dir_and_preserves_it(tmp_path):
    fx = _make_repo(tmp_path)
    out = tmp_path / 'existing-data'
    out.mkdir()
    (out / 'private.txt').write_text('do not delete', encoding='utf-8')
    with pytest.raises(ValueError, match='已有內容'):
        _build(fx, out)
    assert (out / 'private.txt').read_text(encoding='utf-8') == 'do not delete'
    # 即使 --replace-sealed，只要內容唔係本 artifact 就拒絕，唔會刪任何嘢
    with pytest.raises(ValueError, match='唔似本 artifact'):
        _build(fx, out, replace_sealed=True)
    assert (out / 'private.txt').exists()


def test_pages_artifact_replace_sealed_swaps_atomically(tmp_path):
    fx = _make_repo(tmp_path)
    out = tmp_path / '_site'
    _build(fx, out)
    _build(fx, out, replace_sealed=True)  # 第二次替換必須成功
    assert (out / 'metadata.json').exists()
    assert not [p for p in os.listdir(tmp_path) if '.staging-' in p or '.old-' in p]


def test_pages_artifact_failure_keeps_existing_content(tmp_path):
    fx = _make_repo(tmp_path)
    out = tmp_path / '_site'
    _build(fx, out)
    before = {rel: (out / rel).read_bytes() for rel in os.listdir(out)}
    # 令下一次封包必敗（來源檔案被換成 mismatched bytes），確認舊 out 唔會被刪／改
    (tmp_path / 'index.html').write_bytes(b'tampered')
    with pytest.raises(ValueError):
        _build(fx, out, replace_sealed=True)
    after = {rel: (out / rel).read_bytes() for rel in os.listdir(out)}
    assert before == after


def _try_symlink(link, target, target_is_directory=False):
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
        return True
    except (OSError, NotImplementedError):
        return False


def _try_junction(link, target):
    if os.name != 'nt':
        return False
    proc = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                          capture_output=True, text=True)
    return proc.returncode == 0


def test_pages_artifact_rejects_symlink_source_without_skip(tmp_path, monkeypatch):
    link = tmp_path / 'link.html'
    (tmp_path / 'index.html').write_bytes(b'index')
    made = _try_symlink(link, tmp_path / 'index.html')
    if not made:
        made = _try_junction(link, tmp_path / 'index.html')
    if not made:
        # 平台完全唔准建立 link：用受控 monkeypatch 驗同一拒絕路徑（唔 skip）
        real_islink = bpa.os.path.islink
        monkeypatch.setattr(bpa.os.path, 'islink',
                            lambda p: True if os.path.basename(str(p)) == 'link.html' else real_islink(p))
        link.write_bytes(b'plain')
    with pytest.raises(ValueError, match='symlink|普通檔案'):
        bpa.validate_source_file(str(tmp_path), 'link.html')


def test_pages_artifact_rejects_symlink_out_parent(tmp_path, monkeypatch):
    fx = _make_repo(tmp_path)
    real_parent = tmp_path / 'real-out'
    real_parent.mkdir()
    link_parent = tmp_path / 'link-parent'
    made = _try_symlink(link_parent, real_parent, target_is_directory=True)
    if not made:
        made = _try_junction(link_parent, real_parent)
    if made:
        with pytest.raises(ValueError, match='symlink|junction'):
            _build(fx, link_parent / 'site')
    else:
        real_islink = bpa.os.path.islink
        monkeypatch.setattr(bpa.os.path, 'islink',
                            lambda p: True if 'link-parent' in str(p) else real_islink(p))
        with pytest.raises(ValueError, match='symlink|junction'):
            _build(fx, link_parent / 'site')


def test_build_pages_artifact_cli_writes_report(tmp_path):
    fx = _make_repo(tmp_path)
    report = tmp_path / 'report.json'
    out = tmp_path / '_site'
    rc = bpa.main(['--repo', fx['repo'], '--payload-manifest', fx['payload'],
                   '--envelope', fx['envelope'], '--metadata', fx['metadata'],
                   '--out', str(out), '--report', str(report)])
    assert rc == 0
    saved = json.loads(report.read_text(encoding='utf-8'))
    assert saved['ok'] is True and saved['releasePayloadHash'] == fx['meta']['releasePayloadHash']


# ---------------- workflow / dispatch 契約 ----------------

def test_pages_deploy_workflow_contract():
    wf = _load('pages-deploy.yml')
    on = wf[True] if True in wf else wf['on']
    assert on['push']['branches'] == ['master']
    assert 'pull_request' in on
    assert 'workflow_dispatch' in on
    assert on['repository_dispatch']['types'] == ['aircon-pages-deploy']
    group = wf['concurrency']['group']
    assert 'pull_request' in group and 'production' in group
    assert wf['concurrency']['cancel-in-progress'] is False
    assert wf['concurrency']['queue'] == 'max'
    build = wf['jobs']['build']
    deploy = wf['jobs']['deploy']
    assert deploy['needs'] == 'build'
    assert "needs.build.outputs.mode == 'production'" in deploy['if']
    assert "github.event_name != 'pull_request'" in deploy['if']
    assert deploy['environment']['name'] == 'github-pages'
    assert deploy['permissions'] == {'pages': 'write', 'id-token': 'write'}
    names = [s.get('name', '') for s in build['steps']]
    i_verify = next(i for i, n in enumerate(names) if '驗證部署請求' in n)
    i_gates = next(i for i, n in enumerate(names) if '完整本機 gates' in n)
    i_fixture = next(i for i, n in enumerate(names) if 'PR 隔離 fixture' in n)
    i_build = next(i for i, n in enumerate(names) if '建立 Pages artifact' in n)
    i_upload = next(i for i, n in enumerate(names) if '上載 Pages artifact' in n)
    assert i_verify < i_gates < i_fixture < i_build < i_upload
    verify_run = build['steps'][i_verify]['run']
    assert 'verify_deploy_request.py' in verify_run
    assert '--poll-timeout 300' in verify_run and '--poll-interval 10' in verify_run
    assert '--metadata metadata.json' in verify_run
    fixture_run = build['steps'][i_fixture]['run']
    assert 'make_fixture_release.py' in fixture_run and 'verify_candidate.py' in fixture_run
    build_run = build['steps'][i_build]['run']
    assert '--envelope deploy_envelope.json' in build_run
    assert '--metadata metadata.json' in build_run
    assert '--replace-sealed' in build_run
    assert build['steps'][i_upload]['with']['path'] == '_site'


def test_pages_deploy_actions_pinned_to_full_commit():
    import re
    text = _text('pages-deploy.yml')
    uses = re.findall(r'uses:\s*(\S+)', text)
    assert uses, 'workflow 應該有 actions'
    for use in uses:
        assert re.match(r'^[^@\s]+@[0-9a-f]{40}$', use), use
    assert any('actions/configure-pages@' in u for u in uses)
    assert any('actions/upload-pages-artifact@' in u for u in uses)
    assert any('actions/deploy-pages@' in u for u in uses)


def test_daily_workflow_dispatches_pushed_commit_after_push():
    wf = _load('daily-update.yml')
    group = wf['concurrency']['group']
    assert 'pull_request' in group and 'production' in group
    steps = wf['jobs']['update']['steps']
    push = next(s for s in steps if s.get('name', '').startswith('提交並推送'))
    assert 'pushed=true' in push['run'] and "pushed=false" in push['run']
    dispatch = next(s for s in steps if '觸發 Pages 部署' in s.get('name', ''))
    assert dispatch['if'] == "steps.commit_push.outputs.pushed == 'true'"
    assert 'dispatch_pages_deploy.py' in dispatch['run']
    assert 'git rev-parse HEAD' in dispatch['run']
    assert 'GITHUB_TOKEN' in dispatch['env'] or 'github.token' in dispatch['env'].values()
    # PR gate 亦要有 fixture 封包（唔可以用 production metadata 驗候選）
    pr_steps = wf['jobs']['pull-request-gates']['steps']
    assert any('make_fixture_release.py' in (s.get('run') or '') for s in pr_steps)
    # 舊嘅 cancel 群組唔可以再出現
    assert 'weekly-update' not in _text('daily-update.yml')


def test_postdeploy_accepts_repository_dispatch_and_ancestor_check():
    text = _text('postdeploy-verify.yml')
    assert "github.event.workflow_run.event == 'repository_dispatch'" in text
    assert "github.event_name == 'workflow_run'" in text
    assert 'merge-base --is-ancestor HEAD origin/master' in text
    assert 'fetch-depth: 0' in text
    assert 'pages build and deployment' not in text


def test_dispatch_script_rejects_bad_inputs_and_posts_exact_payload():
    spec = importlib.util.spec_from_file_location(
        'dispatch_mod', os.path.join(BASE, 'scripts', 'dispatch_pages_deploy.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def fake_api(path, body):
        calls.append((path, json.loads(body.decode('utf-8'))))
        return 204

    result = mod.dispatch('owner/repo', 'a' * 40, '123', '1', 'tok', api=fake_api)
    assert result['ok'] is True
    assert calls[0][0] == '/repos/owner/repo/dispatches'
    assert calls[0][1]['event_type'] == 'aircon-pages-deploy'
    assert calls[0][1]['client_payload'] == {'commit': 'a' * 40, 'sourceRunId': '123',
                                             'sourceRunAttempt': '1'}
    for bad_commit, bad_run, token in (('HEAD', '1', 'tok'), ('a' * 40, 'abc', 'tok'),
                                       ('a' * 40, '1', '')):
        with pytest.raises(mod.DispatchError):
            mod.dispatch('owner/repo', bad_commit, bad_run, '1', token, api=fake_api)


def _git(tmp_path, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    return subprocess.run(['git'] + list(args), cwd=tmp_path, capture_output=True,
                          text=True, env=env)


def _git_repo(tmp_path):
    _git(tmp_path, 'init', '-q', '-b', 'master')
    (tmp_path / 'f.txt').write_text('1', encoding='utf-8')
    _git(tmp_path, 'add', 'f.txt')
    _git(tmp_path, 'commit', '-q', '-m', 'first')
    first = _git(tmp_path, 'rev-parse', 'HEAD').stdout.strip()
    (tmp_path / 'f.txt').write_text('2', encoding='utf-8')
    _git(tmp_path, 'commit', '-qam', 'second')
    second = _git(tmp_path, 'rev-parse', 'HEAD').stdout.strip()
    _git(tmp_path, 'update-ref', 'refs/remotes/origin/master', second)
    return first, second


def test_verify_deploy_request_binds_master_ancestor_and_source_run(tmp_path):
    """push 路徑基本契約；repository_dispatch 詳細覆蓋喺 test_verify_deploy_request.py。"""
    spec = importlib.util.spec_from_file_location(
        'verify_mod', os.path.join(BASE, 'scripts', 'verify_deploy_request.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    first, second = _git_repo(tmp_path)
    assert mod.verify('push', second, 'owner/repo', cwd=str(tmp_path))['ok'] is True
    with pytest.raises(mod.VerifyError):
        mod.verify('push', 'b' * 40, 'owner/repo', cwd=str(tmp_path))
    # 非 master 祖先：origin/master 退後一步，second 唔再係祖先
    _git(tmp_path, 'update-ref', 'refs/remotes/origin/master', first)
    with pytest.raises(mod.VerifyError, match='祖先'):
        mod.verify('push', second, 'owner/repo', cwd=str(tmp_path))
    _git(tmp_path, 'update-ref', 'refs/remotes/origin/master', second)
