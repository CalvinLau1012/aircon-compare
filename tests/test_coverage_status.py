# -*- coding: utf-8 -*-
"""D1-B：coverage pending 公開狀態投影與 UI 待核提示。"""
import importlib.util
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub = _load('publish_official_status_mod', 'scripts/publish_official_status.py')


def _receipt(tmp_path, decision, missing=('M9',)):
    r = {
        'schemaVersion': 1, 'stage': 1, 'decision': decision,
        'missingModels': list(missing), 'missingCanonicalModels': [m.upper() for m in missing],
        'startedAt': '2026-09-22T00:00:00Z', 'finishedAt': '2026-09-22T00:01:00Z',
    }
    p = tmp_path / 'receipt.json'
    p.write_text(json.dumps(r), encoding='utf-8')
    return p


def test_publish_pending_status_projection(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'queue-kept-pending-coverage')),
                     '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['pendingCoverage'] is True
    assert st['decision'] == 'queue-kept-pending-coverage'
    assert st['missingModels'] == ['M9']
    assert str(tmp_path) not in json.dumps(st), '公開狀態檔不可有本機絕對路徑'


def _advanced_receipt(tmp_path):
    import hashlib
    names = ('official_specs.json', 'shew_official.json', 'rasonic_official.json')
    files = {}
    for i, name in enumerate(names):
        body = ('%d' % i).encode()
        files[name] = {'sha256': 'sha256:' + hashlib.sha256(body).hexdigest(),
                       'size': len(body)}
    manifest = {'schemaVersion': 1, 'stage': 1, 'files': files}
    manifest['manifestHash'] = pub.sha256_id(pub.canonical_json_bytes(manifest))
    outputs = [{'output': name, 'ok': True, 'promoted': True,
                'hash': files[name]['sha256']} for name in names]
    r = {'schemaVersion': 1, 'stage': 1, 'decision': 'advanced',
         'advanced': True, 'failureClass': 'none', 'safeToContinue': True,
         'failures': [], 'finishedAt': '2026-09-22T00:01:00Z',
         'outputs': outputs, 'outputManifest': manifest,
         'staging': {'used': True, 'promoted': True}}
    p = tmp_path / 'receipt.json'; p.write_text(json.dumps(r), encoding='utf-8')
    return p


def test_publish_advanced_without_manifest_rejected(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'advanced', ())),
                     '--out', str(out)]) == 1
    assert not out.exists()


def test_publish_advanced_with_manifest_is_accepted_and_bound(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_advanced_receipt(tmp_path)),
                     '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['pendingCoverage'] is False
    assert st['snapshotHash'] == json.load(open(
        _advanced_receipt(tmp_path), encoding='utf-8'))['outputManifest']['manifestHash']


def test_publish_rejects_unknown_decision(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'weird')), '--out', str(out)]) == 1
    assert not out.exists()


def test_generate_html_pending_hint_and_placeholder(tmp_path, monkeypatch):
    gen = _load('generate_html_mod_cov', 'generate_html.py')
    monkeypatch.setattr(gen, 'BASE', str(tmp_path))
    assert gen.official_pending_hint() == ''
    (tmp_path / 'official_batch_status.json').write_text(json.dumps({
        'schemaVersion': 1, 'decision': 'queue-kept-pending-coverage',
        'pendingCoverage': True, 'missingModels': ['M1', 'M2'],
    }), encoding='utf-8')
    hint = gen.official_pending_hint()
    assert '官網規格待補' in hint and 'M1' in hint and 'M2' in hint
    assert '⚠️' not in hint and '未上市' not in hint and '不推論供應情況' in hint
    assert '__PENDING_HINT__' in gen.HTML_TEMPLATE


def test_daily_workflow_wires_pending_status_projection():
    text = open(os.path.join(BASE, '.github', 'workflows', 'daily-update.yml'),
                encoding='utf-8').read()
    assert 'scripts/publish_official_status.py' in text
    assert 'official_batch_status.json' in text


def test_publish_script_pending_without_missing_queue_coverage_is_pending(tmp_path):
    """D1-B 返修：script 確認 pending 但 M1 已被另一 script 覆蓋（missing 空）都要 pending。"""
    r = _receipt(tmp_path, 'queue-kept-pending-coverage', missing=())
    data = json.load(open(r, encoding='utf-8'))
    data.update({'missingQueueCoverage': False,
                 'scriptCoveragePending': ['M1'],
                 'coveragePendingFromScripts': ['M1']})
    r.write_text(json.dumps(data), encoding='utf-8')
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(r), '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['pendingCoverage'] is True
    assert st['missingQueueCoverage'] is False
    assert st['scriptCoveragePending'] == ['M1']
    assert st['missingModels'] == []


def test_generate_html_script_pending_hint_lists_script_models(tmp_path, monkeypatch):
    gen = _load('generate_html_mod_cov2', 'generate_html.py')
    monkeypatch.setattr(gen, 'BASE', str(tmp_path))
    (tmp_path / 'official_batch_status.json').write_text(json.dumps({
        'schemaVersion': 1, 'decision': 'queue-kept-pending-coverage',
        'pendingCoverage': True, 'missingQueueCoverage': False,
        'missingModels': [], 'scriptCoveragePending': ['FR-KS18'],
    }), encoding='utf-8')
    hint = gen.official_pending_hint()
    assert '官網規格待補' in hint and 'FR-KS18' in hint
    assert '⚠️' not in hint


def test_real_runner_receipt_projects_trusted_manifest_chain(tmp_path, monkeypatch):
    """Real run_official_batch receipt -> publisher -> source_status identity."""
    rob = _load('rob_chain', 'scripts/run_official_batch.py')
    ss = _load('ss_chain', 'scripts/source_status.py')
    names = ('official_specs.json', 'shew_official.json', 'rasonic_official.json')
    repo = tmp_path
    (repo / 'update_queue.json').write_text(
        json.dumps({'stage': 1, 'models': ['M1']}), encoding='utf-8')
    (repo / 'advance_queue.py').write_text('import sys; sys.exit(0)\n', encoding='utf-8')
    scripts = []
    for i, name in enumerate(names):
        script = f'fake_{i}.py'
        (repo / script).write_text(
            'import json, os, sys\n'
            f'p=os.path.join(os.path.dirname(os.path.abspath(__file__)), {name!r})\n'
            'json.dump({"M1":{"size":"100x200x300"}}, open(p,"w"))\n'
            'print("AIRCON_FETCH_RECEIPT " + json.dumps({"schemaVersion":1,'
            f'"script":{script!r},"attempted":1,"succeeded":1,"failed":0,'
            '"succeededModels":["M1"],"failedModels":[],"alreadyVerified":[],'
            '"covers":["M1"]}))\n', encoding='utf-8')
        scripts.append(script)
    monkeypatch.setattr(rob, 'BASE', str(repo))
    monkeypatch.setattr(rob, 'STAGES', {1: {'scripts': scripts, 'outputs': list(names)}})
    receipt_path = repo / 'official_receipt.json'
    assert rob.main(['--stage', '1', '--receipt', str(receipt_path)]) == 0
    receipt = json.load(open(receipt_path, encoding='utf-8'))
    assert receipt['decision'] == 'advanced'
    assert receipt['outputManifest']['manifestHash'].startswith('sha256:')
    out = repo / 'official_batch_status.json'
    assert pub.main(['--receipt', str(receipt_path), '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['snapshotHash'] == receipt['outputManifest']['manifestHash']
    s = ss.build_status(metadata={'datasetHash': 'sha256:' + 'a' * 64,
                                  'datasetSnapshotId': 'emsd-test'},
                        official_status=st)
    assert s['official']['status'] == 'ok'
    assert s['official']['snapshotHash'] == st['snapshotHash']
    assert s['official']['lastAcquiredAt'] == receipt['finishedAt']


def test_publisher_cli_runs_fresh_env_and_real_receipt(tmp_path):
    import subprocess
    script = os.path.join(BASE, 'scripts', 'publish_official_status.py')
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    p = subprocess.run([sys.executable, script, '--help'], cwd=BASE, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0 and '--receipt' in p.stdout
    out = tmp_path / 'status.json'
    p = subprocess.run([sys.executable, script, '--receipt',
                        str(_advanced_receipt(tmp_path)), '--out', str(out)],
                       cwd=BASE, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.load(open(out))['snapshotHash'].startswith('sha256:')
