# -*- coding: utf-8 -*-
"""Verified snapshot rebuild preflight／guard 離線回歸（零 provider、唯讀）。

設計原則（Codex 2026-10-01 review 返修）：
  - 唔 hardcode 歷史 commit／日期／counts：預期值一律由真 repo fixture 動態讀出；
  - preflight 測試全部用 `--now` 注入時間，冇 wall-clock 依賴；
  - guard 測試用真 repo（clean）或者自建 temp git repo，唔改動 source repo；
  - 負向測試覆蓋 baseline schema、git 狀態、三個 generated output 以外改動、
    raw receipt cardinality／provenance、required input 缺失、失敗 preflight 失效化。
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import verify_snapshot_rebuild as vsr  # noqa: E402

GENERATED = vsr.ALLOWED_GENERATED
COPY_FILES = tuple(vsr.REQUIRED_PRESERVED) + ('metadata.json',) + GENERATED


# ---------------------------------------------------------------- helpers


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          timeout=120)


def _git_head(repo):
    return _git(repo, 'rev-parse', 'HEAD').stdout.decode().strip()


def _head():
    return _git_head(BASE)


def _sha256_rel(root, rel):
    h = hashlib.sha256()
    with open(os.path.join(str(root), *rel.split('/')), 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _load(root, rel):
    with open(os.path.join(str(root), *rel.split('/')), encoding='utf-8') as f:
        return json.load(f)


def _dump(root, rel, data):
    path = os.path.join(str(root), *rel.split('/'))
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fixture_meta(root):
    return _load(root, 'metadata.json')


def _receipt_retrieved(root=None):
    return _load(root or BASE, 'emsd_receipt.json')['retrievedAt']


def _now_within():
    dt = datetime.fromisoformat(_receipt_retrieved().replace('Z', '+00:00'))
    return (dt + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _now_stale():
    dt = datetime.fromisoformat(_receipt_retrieved().replace('Z', '+00:00'))
    return (dt + timedelta(hours=73)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _fixture(tmp_path, name='files'):
    root = tmp_path / name
    for rel in COPY_FILES:
        dst = root.joinpath(*rel.split('/'))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(BASE, *rel.split('/')), dst)
    return root


def _run_preflight(tmp_path, files_root, *, now=None, expected_head=None,
                   force='false', receipt=None, raw=None, csv=None, price_meta=None,
                   report=None, baseline_out=None, repo=BASE):
    out = tmp_path / 'out'
    out.mkdir(parents=True, exist_ok=True)
    argv = ['preflight', '--repo', str(repo), '--files-root', str(files_root),
            '--now', now or _now_within(),
            '--expected-head', expected_head or _head(),
            '--force-price-batch', force,
            '--baseline-out', str(baseline_out or (out / 'baseline.json')),
            '--report', str(report or (out / 'preflight.json'))]
    if receipt:
        argv += ['--receipt', str(receipt)]
    if raw:
        argv += ['--raw-receipt', str(raw)]
    if csv:
        argv += ['--csv', str(csv)]
    if price_meta:
        argv += ['--price-meta', str(price_meta)]
    rc = vsr.main(argv)
    report_path = str(report or (out / 'preflight.json'))
    data = json.load(open(report_path, encoding='utf-8')) if os.path.exists(report_path) else None
    return rc, data


def _failed(data):
    return {c['check'] for c in data['checks'] if not c['pass']}


def _tamper(path, mutate):
    with open(path, 'rb') as f:
        raw = f.read()
    with open(path, 'wb') as f:
        f.write(mutate(raw))


def _mutate_json(root, rel, mutate):
    data = _load(root, rel)
    mutate(data)
    _dump(root, rel, data)


def _valid_baseline(root, repo=None, *, head=None, metadata_commit=None,
                    facts=None, files=None, schema_version=vsr.SCHEMA_VERSION,
                    created_at=None):
    meta = _fixture_meta(root)
    return {
        'schemaVersion': schema_version,
        'createdAt': created_at or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'head': head or _head(),
        'metadataCommit': metadata_commit or meta['commit'],
        'facts': facts if facts is not None else vsr._collect_facts(meta),
        'files': files if files is not None else
            {rel: _sha256_rel(root, rel) for rel in vsr.REQUIRED_PRESERVED},
    }


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _guard(tmp_path, root, baseline, *, repo=BASE, name='guard.json'):
    report = tmp_path / name
    rc = vsr.main(['guard', '--repo', str(repo), '--files-root', str(root),
                   '--baseline', str(baseline), '--report', str(report)])
    return rc, json.load(open(report, encoding='utf-8'))


def _regen_metadata(root, **overrides):
    """由 fixture metadata 派生一份完整 Schema-valid 嘅「重建後」metadata。"""
    meta = _fixture_meta(root)
    meta = json.loads(json.dumps(meta))
    meta['commit'] = overrides.pop('commit', 'a' * 40)
    meta['build'] = overrides.pop('build', 'B20990101.1.1')
    meta['workflowRunId'] = overrides.pop('workflowRunId', '1')
    meta['deployTime'] = overrides.pop(
        'deployTime', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    meta['releasePayloadHash'] = overrides.pop('releasePayloadHash', 'sha256:' + '2' * 64)
    meta.update(overrides)
    return meta


def _make_temp_repo(tmp_path, name='temp-repo'):
    repo = tmp_path / name
    for rel in COPY_FILES:
        dst = repo.joinpath(*rel.split('/'))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(BASE, *rel.split('/')), dst)
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 't@e.invalid'],
                   check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 't'], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'fixture'], check=True)
    return repo


# ---------------------------------------------------------------- preflight positive


def test_preflight_accepts_valid_snapshot(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 0 and report['ok'] is True, report
    assert not _failed(report)
    baseline = _load(tmp_path / 'out', 'baseline.json')
    meta = _fixture_meta(root)
    assert baseline['schemaVersion'] == vsr.SCHEMA_VERSION
    assert baseline['head'] == _head()
    assert baseline['metadataCommit'] == meta['commit']
    assert set(baseline['files']) == set(vsr.REQUIRED_PRESERVED)
    for value in baseline['files'].values():
        assert vsr.SHA256_RE.match(value)
    # acquisition facts 同 fixture metadata 一致（唔 hardcode 日期／counts）。
    for field in vsr.REQUIRED_FACT_FIELDS:
        assert baseline['facts'][field] == meta[field]
    assert report['baseline']['written'] is True


def test_preflight_no_network(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError('preflight 唔可以發網絡請求')

    monkeypatch.setattr(socket, 'socket', boom)
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 0 and report['ok'] is True


# ---------------------------------------------------------------- preflight negatives


def test_preflight_rejects_metadata_hash_tamper(tmp_path):
    root = _fixture(tmp_path)
    _mutate_json(root, 'metadata.json',
                 lambda m: m.update(datasetHash='sha256:' + '0' * 64))
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_release_payload_hash_tamper(tmp_path):
    root = _fixture(tmp_path)
    _mutate_json(root, 'metadata.json',
                 lambda m: m.update(releasePayloadHash='sha256:' + '1' * 64))
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_csv_tamper(tmp_path):
    root = _fixture(tmp_path)
    _tamper(root / 'emsd_空調能源標籤.csv', lambda b: b + b'\n')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_missing_brand_input(tmp_path):
    root = _fixture(tmp_path)
    (root / 'pana_official.json').unlink()
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'required-inputs-present' in _failed(report)


def test_preflight_rejects_source_timestamp_mutation(tmp_path):
    for field, value in (('datasetRetrievedAt', '2026-09-30T19:00:00Z'),
                         ('datasetDate', '1999-01-01')):
        root = _fixture(tmp_path, name=f'mut-{field}')
        _mutate_json(root, 'metadata.json', lambda m, f=field, v=value: m.update(**{f: v}))
        rc, report = _run_preflight(tmp_path / f'mut-{field}', root)
        assert rc == 1 and 'receipt-facts' in _failed(report), field


def test_preflight_rejects_receipt_failure_and_raw_binding(tmp_path):
    root = _fixture(tmp_path)
    _mutate_json(root, 'emsd_receipt.json', lambda r: r.update(success=False))
    rc, report = _run_preflight(tmp_path / 'a', root)
    assert rc == 1 and 'receipt-raw-binding' in _failed(report)

    root2 = _fixture(tmp_path / 'b')
    _tamper(root2 / 'emsd_raw_receipt.json', lambda b: b + b' ')
    rc2, report2 = _run_preflight(tmp_path / 'b', root2)
    assert rc2 == 1 and 'receipt-raw-binding' in _failed(report2)


def test_preflight_rejects_raw_page_cardinality_and_shape(tmp_path):
    cases = {
        'perpage_short': lambda raw: raw.update(perPageRows=raw['perPageRows'][:-1]),
        'page_duplicate': lambda raw: raw['pages'][1].update(page=raw['pages'][0]['page']),
        'page_bool_length': lambda raw: raw['pages'][0].update(byteLength=True),
        'page_bad_hash': lambda raw: raw['pages'][0].update(sha256='sha256:zz'),
        'pagecount_mismatch': lambda raw: raw.update(pageCount=raw['pageCount'] + 1),
    }
    for name, mutate in cases.items():
        root = _fixture(tmp_path, name=f'raw-{name}')
        _mutate_json(root, 'emsd_raw_receipt.json', mutate)
        rc, report = _run_preflight(tmp_path / f'raw-{name}', root)
        assert rc == 1 and 'receipt-raw-binding' in _failed(report), name


def test_preflight_rejects_raw_provenance_inconsistency(tmp_path):
    cases = {
        'missing_sources': lambda raw: raw.pop('sources'),
        'not_durable': lambda raw: raw['sources'][1].update(durableRemote=False),
        'archive_mismatch': lambda raw: raw['sources'][0].update(
            archiveHash='sha256:' + '3' * 64),
        'private_missing': lambda raw: raw.pop('privateArchive'),
        'receipt_dual_missing': None,  # 特例：改 receipt
        'receipt_perpage_mismatch': None,
    }
    for name, mutate in cases.items():
        root = _fixture(tmp_path, name=f'prov-{name}')
        if name == 'receipt_dual_missing':
            _mutate_json(root, 'emsd_receipt.json', lambda r: r.pop('dualSource'))
        elif name == 'receipt_perpage_mismatch':
            _mutate_json(root, 'emsd_receipt.json',
                         lambda r: r.update(perPageRows=r['perPageRows'][:-1]))
        else:
            _mutate_json(root, 'emsd_raw_receipt.json', mutate)
        rc, report = _run_preflight(tmp_path / f'prov-{name}', root)
        assert rc == 1 and 'receipt-raw-binding' in _failed(report), name


def test_preflight_rejects_stale_and_future(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root, now=_now_stale())
    assert rc == 1 and 'age-within-72h' in _failed(report)
    dt = datetime.fromisoformat(_receipt_retrieved().replace('Z', '+00:00'))
    future = (dt - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    rc2, report2 = _run_preflight(tmp_path, root, now=future)
    # receipt_facts 自己亦會拒絕未來時間（提早 fail-closed），兩種檢查邊個先行都算正確。
    assert rc2 == 1 and not report2['ok']
    assert _failed(report2) & {'age-within-72h', 'receipt-facts'}


def test_preflight_rejects_untrusted_commit(tmp_path):
    root = _fixture(tmp_path)
    _mutate_json(root, 'metadata.json', lambda m: m.update(commit='b' * 40))
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'metadata-commit-ancestry' in _failed(report)


def test_preflight_rejects_shallow_checkout(tmp_path):
    root = _fixture(tmp_path)
    shallow = tmp_path / 'shallow'
    subprocess.run(['git', 'clone', '--depth', '1', '--no-local',
                    Path(BASE).as_uri(), str(shallow)], capture_output=True, timeout=600)
    if not shallow.exists() or not (shallow / '.git').exists():
        pytest.skip('環境唔支援建立 shallow clone（可能係 Windows file:// 限制）')
    rc, report = _run_preflight(tmp_path / 'shallow-out', root, repo=shallow)
    assert rc == 1 and 'metadata-commit-ancestry' in _failed(report)
    assert any('shallow' in e for c in report['checks']
               if c['check'] == 'metadata-commit-ancestry' for e in c['errors'])


def test_preflight_rejects_active_price_stage_and_force(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root, force='true')
    assert rc == 1 and 'price-stage-inactive-force-false' in _failed(report)

    root2 = _fixture(tmp_path / 'active')
    _mutate_json(root2, 'prices_meta.json',
                 lambda m: m.update(price_batch_start='2026-10-01', price_batch_idx=0))
    rc2, report2 = _run_preflight(tmp_path / 'active', root2)
    assert rc2 == 1 and 'price-stage-inactive-force-false' in _failed(report2)


def test_preflight_rejects_dirty_checkout(tmp_path):
    repo = _make_temp_repo(tmp_path, 'dirty-repo')
    (repo / 'biggo_prices.json').write_text('{changed', encoding='utf-8')
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path / 'dirty-out', root, repo=repo)
    assert rc == 1 and 'git-clean-and-head' in _failed(report)


def test_preflight_rejects_symlink_payload(tmp_path):
    root = _fixture(tmp_path)
    target = tmp_path / 'outside.html'
    target.write_text('<!doctype html>', encoding='utf-8')
    link = root / 'index.html'
    link.unlink()
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip('環境唔支援 symlink')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'payload-and-metadata-contract' in _failed(report)


def test_failed_preflight_invalidates_previous_baseline(tmp_path):
    root = _fixture(tmp_path)
    baseline_path = tmp_path / 'out' / 'baseline.json'
    rc, report = _run_preflight(tmp_path, root, baseline_out=baseline_path)
    assert rc == 0 and report['ok']
    assert _load(baseline_path.parent, 'baseline.json')['schemaVersion'] == vsr.SCHEMA_VERSION
    # 同一路徑再跑一次失敗 preflight：舊有效 baseline 必須失效化。
    rc2, report2 = _run_preflight(tmp_path, root, now=_now_stale(),
                                  baseline_out=baseline_path)
    assert rc2 == 1 and not report2['ok']
    marker = _load(baseline_path.parent, 'baseline.json')
    assert marker.get('invalidated') is True
    rc3, data = _guard(tmp_path, root, baseline_path, name='guard-invalidated.json')
    assert rc3 == 1 and 'baseline-schema' in _failed(data)


def test_report_path_inside_repo_rejected(tmp_path):
    root = _fixture(tmp_path)
    inside = os.path.join(BASE, 'rebuild-report-should-not-exist.json')
    assert not os.path.exists(inside)
    with pytest.raises(SystemExit) as exc:
        vsr.main(['preflight', '--repo', str(BASE), '--files-root', str(root),
                  '--now', _now_within(), '--force-price-batch', 'false',
                  '--baseline-out', str(tmp_path / 'out' / 'baseline.json'),
                  '--report', inside])
    assert exc.value.code == 2
    assert not os.path.exists(inside)


# ---------------------------------------------------------------- guard: baseline schema


def test_guard_rejects_invalid_baselines(tmp_path):
    root = _fixture(tmp_path)
    valid = _valid_baseline(root)
    cases = {
        'wrong-schema': {**valid, 'schemaVersion': 1},
        'bad-head': {**valid, 'head': 'not-a-sha'},
        'bad-commit': {**valid, 'metadataCommit': 'x'},
        'missing-files': {k: v for k, v in valid.items() if k != 'files'},
        'empty-files': {**valid, 'files': {}},
        'missing-entry': {**valid, 'files': {k: v for k, v in valid['files'].items()
                                             if k != 'pana_official.json'}},
        'extra-entry': {**valid, 'files': {**valid['files'], 'evil.json': 'sha256:' + '0' * 64}},
        'traversal-entry': {**valid, 'files': {**valid['files'],
                                               '../evil.json': 'sha256:' + '0' * 64}},
        'null-hash': {**valid, 'files': {**valid['files'], 'prices.json': None}},
        'bad-hash': {**valid, 'files': {**valid['files'], 'prices.json': 'deadbeef'}},
        'missing-facts': {**valid, 'facts': {k: v for k, v in valid['facts'].items()
                                             if k != 'datasetHash'}},
        'bad-record-count': {**valid, 'facts': {**valid['facts'], 'recordCount': True}},
        'invalidated-marker': {'schemaVersion': vsr.SCHEMA_VERSION, 'invalidated': True,
                               'reason': 'preflight-failed'},
    }
    for name, baseline in cases.items():
        path = _write_json(tmp_path / f'bad-{name}.json', baseline)
        rc, report = _guard(tmp_path, root, path, name=f'guard-{name}.json')
        assert rc == 1 and not report['ok'], name
        assert 'baseline-schema' in _failed(report) or 'baseline-load' in _failed(report), name


def test_guard_fails_when_git_command_fails(tmp_path):
    root = _fixture(tmp_path)
    baseline = _write_json(tmp_path / 'baseline.json',
                           _valid_baseline(root, head='a' * 40))
    norepo = tmp_path / 'not-a-repo'
    norepo.mkdir()
    rc, report = _guard(tmp_path, root, baseline, repo=norepo, name='guard-norepo.json')
    assert rc == 1
    assert 'git-changes-limited-to-generated' in _failed(report)


# ---------------------------------------------------------------- guard: changes


def test_guard_allows_only_generated_output_changes(tmp_path):
    repo = _make_temp_repo(tmp_path)
    baseline_path = _write_json(tmp_path / 'baseline.json',
                                _valid_baseline(repo, head=_git_head(repo)))
    # 合法：重新生成嘅 metadata（facts 不變、commit/build/deploy/hash 可變）＋改動 index。
    _dump(repo, 'metadata.json', _regen_metadata(repo))
    (repo / 'index.html').write_text('<!doctype html><html>rebuilt</html>',
                                     encoding='utf-8')
    rc, report = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-ok.json')
    assert rc == 0 and report['ok'] is True, report

    # tracked 非 output 改動
    (repo / 'biggo_prices.json').write_text('{}', encoding='utf-8')
    rc2, report2 = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-tracked.json')
    assert rc2 == 1 and 'preserved-sources-unchanged' in _failed(report2)
    assert 'git-changes-limited-to-generated' in _failed(report2)


def test_guard_rejects_untracked_and_staged_non_output_changes(tmp_path):
    repo = _make_temp_repo(tmp_path)
    baseline_path = _write_json(tmp_path / 'baseline.json',
                                _valid_baseline(repo, head=_git_head(repo)))
    (repo / 'scratch-notes.md').write_text('x', encoding='utf-8')  # untracked
    rc, report = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-untracked.json')
    assert rc == 1 and 'git-changes-limited-to-generated' in _failed(report)
    (repo / 'scratch-notes.md').unlink()
    (repo / 'specs.json').write_text('{}', encoding='utf-8')
    subprocess.run(['git', '-C', str(repo), 'add', 'specs.json'], check=True)
    rc2, report2 = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-staged.json')
    assert rc2 == 1 and 'git-changes-limited-to-generated' in _failed(report2)


def test_guard_rejects_deletion_and_rename(tmp_path):
    repo = _make_temp_repo(tmp_path)
    baseline_path = _write_json(tmp_path / 'baseline.json',
                                _valid_baseline(repo, head=_git_head(repo)))
    (repo / 'prices.json').unlink()
    rc, report = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-del.json')
    assert rc == 1 and 'preserved-sources-unchanged' in _failed(report)

    repo2 = _make_temp_repo(tmp_path, 'rename-repo')
    baseline_path2 = _write_json(tmp_path / 'baseline2.json',
                                 _valid_baseline(repo2, head=_git_head(repo2)))
    subprocess.run(['git', '-C', str(repo2), 'mv', 'biggo_prices.json',
                    'biggo_prices_renamed.json'], check=True)
    rc2, report2 = _guard(tmp_path, repo2, baseline_path2, repo=repo2,
                          name='guard-rename.json')
    assert rc2 == 1 and 'git-changes-limited-to-generated' in _failed(report2)


def test_guard_rejects_metadata_fact_or_schema_change(tmp_path):
    repo = _make_temp_repo(tmp_path)
    baseline_path = _write_json(tmp_path / 'baseline.json',
                                _valid_baseline(repo, head=_git_head(repo)))
    # 完整 schema 但改咗 acquisition fact（datasetDate）→ fail。
    _dump(repo, 'metadata.json', _regen_metadata(repo, datasetDate='1999-01-01'))
    rc, report = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-fact.json')
    assert rc == 1 and 'generated-metadata-contract' in _failed(report)

    # 唔完整／無 schema 嘅 metadata → fail（唔可以靠空 object 通過）。
    _dump(repo, 'metadata.json', {})
    rc2, report2 = _guard(tmp_path, repo, baseline_path, repo=repo, name='guard-empty.json')
    assert rc2 == 1 and 'generated-metadata-contract' in _failed(report2)


def test_guard_requires_all_generated_outputs_present(tmp_path):
    root = _fixture(tmp_path)
    baseline_path = _write_json(tmp_path / 'baseline.json', _valid_baseline(root))
    (root / 'metadata.json').unlink()
    rc, report = _guard(tmp_path, root, baseline_path, name='guard-missing-out.json')
    assert rc == 1 and 'generated-present' in _failed(report)
