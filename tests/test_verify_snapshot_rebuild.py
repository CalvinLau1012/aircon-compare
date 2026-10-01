# -*- coding: utf-8 -*-
"""Verified snapshot rebuild preflight／guard 離線回歸（零 provider、唯讀）。

用真 repo 做 git 祖先／乾淨檢查，同時用 temp files-root 做 payload／receipt／CSV
tamper 測試；唔會改動 repo 任何生產檔。
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import verify_snapshot_rebuild as vsr  # noqa: E402

PAYLOAD = ('index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv')
COPY_FILES = PAYLOAD + ('metadata.json', 'deploy_payload.json', 'emsd_receipt.json',
                        'emsd_raw_receipt.json', 'prices_meta.json')


def _git(*args):
    return subprocess.run(['git', '-C', BASE, *args], capture_output=True, timeout=60)


def _head():
    return _git('rev-parse', 'HEAD').stdout.decode().strip()


def _receipt_retrieved():
    with open(os.path.join(BASE, 'emsd_receipt.json'), encoding='utf-8') as f:
        return json.load(f)['retrievedAt']


def _now_within():
    dt = datetime.fromisoformat(_receipt_retrieved().replace('Z', '+00:00'))
    return (dt + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _now_stale():
    dt = datetime.fromisoformat(_receipt_retrieved().replace('Z', '+00:00'))
    return (dt + timedelta(hours=73)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _fixture(tmp_path):
    root = tmp_path / 'files'
    root.mkdir(parents=True, exist_ok=True)
    for rel in COPY_FILES:
        shutil.copy2(os.path.join(BASE, rel), root / rel)
    return root


def _run_preflight(tmp_path, files_root, *, now=None, expected_head=None,
                   force='false', receipt=None, raw=None, csv=None, price_meta=None,
                   report=None):
    out = tmp_path / 'out'
    out.mkdir(exist_ok=True)
    argv = ['preflight', '--repo', BASE, '--files-root', str(files_root),
            '--now', now or _now_within(),
            '--expected-head', expected_head or _head(),
            '--force-price-batch', force,
            '--baseline-out', str(out / 'baseline.json'),
            '--report', report or str(out / 'preflight.json')]
    if receipt:
        argv += ['--receipt', str(receipt)]
    if raw:
        argv += ['--raw-receipt', str(raw)]
    if csv:
        argv += ['--csv', str(csv)]
    if price_meta:
        argv += ['--price-meta', str(price_meta)]
    rc = vsr.main(argv)
    report_path = report or str(out / 'preflight.json')
    data = json.load(open(report_path, encoding='utf-8')) if os.path.exists(report_path) else None
    return rc, data


def _failed(data):
    return {c['check'] for c in data['checks'] if not c['pass']}


def _tamper(path, mutate):
    with open(path, 'rb') as f:
        raw = f.read()
    with open(path, 'wb') as f:
        f.write(mutate(raw))


def test_preflight_accepts_valid_snapshot(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 0 and report['ok'] is True, report
    baseline = json.load(open(tmp_path / 'out' / 'baseline.json', encoding='utf-8'))
    assert baseline['head'] == _head()
    assert baseline['metadataCommit'] == '53b33ee944ea2cb5f5bb0d93eb72211de6da4bac'
    assert set(baseline['files']) >= {'emsd_空調能源標籤.csv', 'emsd_receipt.json',
                                      'prices_meta.json'}
    for value in baseline['files'].values():
        assert value is None or vsr.SHA256_RE.match(value)


def test_preflight_rejects_metadata_hash_tamper(tmp_path):
    root = _fixture(tmp_path)
    meta_path = root / 'metadata.json'
    meta = json.load(open(meta_path, encoding='utf-8'))
    meta['datasetHash'] = 'sha256:' + '0' * 64
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and not report['ok']
    assert 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_csv_tamper(tmp_path):
    root = _fixture(tmp_path)
    _tamper(root / 'emsd_空調能源標籤.csv', lambda b: b + b'\n')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and not report['ok']
    assert 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_release_payload_hash_tamper(tmp_path):
    root = _fixture(tmp_path)
    meta_path = root / 'metadata.json'
    meta = json.load(open(meta_path, encoding='utf-8'))
    meta['releasePayloadHash'] = 'sha256:' + '1' * 64
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'payload-and-metadata-contract' in _failed(report)


def test_preflight_rejects_bad_receipt_and_raw_binding(tmp_path):
    root = _fixture(tmp_path)
    receipt = json.load(open(root / 'emsd_receipt.json', encoding='utf-8'))
    receipt['success'] = False
    (root / 'emsd_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False),
                                            encoding='utf-8')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'receipt-raw-binding' in _failed(report)

    root2 = _fixture(tmp_path / 'raw')
    _tamper(root2 / 'emsd_raw_receipt.json', lambda b: b + b' ')
    rc2, report2 = _run_preflight(tmp_path / 'raw', root2)
    assert rc2 == 1 and 'receipt-raw-binding' in _failed(report2)


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
    meta_path = root / 'metadata.json'
    meta = json.load(open(meta_path, encoding='utf-8'))
    meta['commit'] = 'b' * 40
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 1 and 'metadata-commit-ancestry' in _failed(report)


def test_preflight_rejects_active_price_stage_and_force(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root, force='true')
    assert rc == 1 and 'price-stage-inactive-force-false' in _failed(report)

    root2 = _fixture(tmp_path / 'active')
    meta = json.load(open(root2 / 'prices_meta.json', encoding='utf-8'))
    meta['price_batch_start'] = '2026-10-01'
    meta['price_batch_idx'] = 0
    (root2 / 'prices_meta.json').write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding='utf-8')
    rc2, report2 = _run_preflight(tmp_path / 'active', root2)
    assert rc2 == 1 and 'price-stage-inactive-force-false' in _failed(report2)


def test_preflight_rejects_dirty_checkout(tmp_path):
    repo = tmp_path / 'dirty'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 't@e.invalid'],
                   check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 't'], check=True)
    (repo / 'f.txt').write_text('x', encoding='utf-8')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'c'], check=True)
    (repo / 'f.txt').write_text('dirty', encoding='utf-8')
    root = _fixture(tmp_path)
    out = tmp_path / 'out2'
    out.mkdir()
    rc = vsr.main(['preflight', '--repo', str(repo), '--files-root', str(root),
                   '--now', _now_within(), '--force-price-batch', 'false',
                   '--baseline-out', str(out / 'baseline.json'),
                   '--report', str(out / 'preflight.json')])
    report = json.load(open(out / 'preflight.json', encoding='utf-8'))
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


def test_preflight_no_network(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError('preflight 唔可以發網絡請求')

    monkeypatch.setattr(socket, 'socket', boom)
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 0 and report['ok'] is True


def test_guard_allows_generated_only_and_blocks_source_change(tmp_path):
    root = _fixture(tmp_path)
    rc, report = _run_preflight(tmp_path, root)
    assert rc == 0 and report['ok']
    baseline = tmp_path / 'out' / 'baseline.json'
    (root / 'metadata.json').write_text('{}', encoding='utf-8')  # generated artifact ok
    guard_report = tmp_path / 'out' / 'guard.json'
    rc2 = vsr.main(['guard', '--repo', BASE, '--files-root', str(root),
                    '--baseline', str(baseline), '--report', str(guard_report)])
    data = json.load(open(guard_report, encoding='utf-8'))
    assert rc2 == 0 and data['ok'] is True, data
    (root / 'emsd_receipt.json').write_text('{}', encoding='utf-8')  # source changed
    guard_report2 = tmp_path / 'out' / 'guard2.json'
    rc3 = vsr.main(['guard', '--repo', BASE, '--files-root', str(root),
                    '--baseline', str(baseline), '--report', str(guard_report2)])
    data2 = json.load(open(guard_report2, encoding='utf-8'))
    assert rc3 == 1 and 'preserved-sources-unchanged' in _failed(data2)


def test_report_path_inside_repo_rejected(tmp_path):
    root = _fixture(tmp_path)
    inside = os.path.join(BASE, 'rebuild-report-should-not-exist.json')
    assert not os.path.exists(inside)
    with pytest.raises(SystemExit) as exc:
        vsr.main(['preflight', '--repo', BASE, '--files-root', str(root),
                  '--now', _now_within(), '--force-price-batch', 'false',
                  '--baseline-out', str(tmp_path / 'out' / 'baseline.json'),
                  '--report', inside])
    assert exc.value.code == 2
    assert not os.path.exists(inside)
