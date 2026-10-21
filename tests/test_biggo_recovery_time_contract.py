# -*- coding: utf-8 -*-
"""Recovery source-time provenance end-to-end contract.

The contract is deliberately offline.  It uses the real immutable recovery
evidence when E is mounted (Root uses /evidence); otherwise the test is skipped
rather than synthesising a different acquisition history.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE / 'scripts')]

import biggo_apply as apply_mod
import biggo_recovery as recovery
import report_common
import source_status
import sync_biggo_completed

E = Path(os.environ.get('AIRCON_RECOVERY_EVIDENCE', '/evidence'))
NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc).timestamp()
OLD_STAGE4 = '2026-10-18T06:12:23Z'


def _evidence_available():
    return (E / 'original-inputs-manifest.json').is_file() and \
        (E / 'run-39384334727/artifact-12247731259.zip').is_file()


@pytest.fixture(scope='module', autouse=True)
def _require_evidence():
    if not _evidence_available():
        pytest.skip('real recovery evidence is not mounted')


def _packet(publication):
    return recovery.build_recovery(E, publication_time=publication)


def _bundle(packet):
    manifest = packet['manifest']
    return {
        'manifest': manifest,
        'stageResult': packet['stageResult'],
        'baseSnapshot': packet['baseSnapshot'],
        'newSnapshot': packet['newSnapshot'],
        'baseSnapshotHash': manifest['baseSnapshotHash'],
        'newSnapshotHash': manifest['newSnapshotHash'],
        'stageResultHash': manifest['stageResultHash'],
        'bundleHash': packet['bundleHash'],
    }


def _root(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir(parents=True)
    for name in ('biggo_prices.json', 'model_status.json',
                 'model_blacklist.json', 'prices_meta.json'):
        shutil.copy2(E / 'original-inputs' / name, root / name)
    return root


def _apply(packet, root, applied_at):
    bundle = _bundle(packet)
    result = apply_mod.apply_bundle(bundle, repo_root=str(root),
                                    applied_at=applied_at)
    meta = json.loads((root / 'prices_meta.json').read_text())
    verified = sync_biggo_completed.verify_local_post(bundle, meta, str(root))
    return bundle, meta, verified, result


def _status(meta, verified, *, prior_metadata=None):
    bundle_hash = verified['bundleHash']
    report = {
        'ok': True, 'postStateVerified': True,
        'cycleId': verified['cycleId'], 'stage': verified['stage'],
        'bundleHash': bundle_hash, 'verifiedPost': verified,
    }
    biggo = {
        'status': 'completed', 'cycle': verified['cycleId'], 'stage': 5,
        'snapshotHash': verified['snapshotHash'], 'acquiredAt': OLD_STAGE4,
    }
    return source_status.build_status(
        prices_meta=meta, biggo_status=biggo,
        biggo_snapshot_hash=verified['snapshotHash'],
        completed_sync=report, prior_metadata=prior_metadata, now=NOW)


def _old_prior(meta):
    # A deliberately valid prior receipt for the *same* new snapshot, carrying
    # the old stage4 acquisition time.  It must not leak into the recovery
    # stage5 unknown-time result.
    biggo = {
        'status': 'completed', 'cycle': meta['last_applied_cycle'], 'stage': 5,
        'snapshotHash': meta['appliedSnapshotHash'], 'acquiredAt': OLD_STAGE4,
    }
    return {'sourceStatus': source_status.build_status(
        prices_meta=meta, biggo_status=biggo,
        biggo_snapshot_hash=meta['appliedSnapshotHash'], now=NOW)}


def _rehash(bundle):
    bundle['bundleHash'] = apply_mod._canonical_hash(bundle['manifest'])
    return bundle


@pytest.mark.parametrize('publication,applied_at', [
    ('2026-10-20T07:50:00Z', '2026-10-20T08:00:00Z'),
    ('2026-10-20T07:55:00Z', '2026-10-20T08:05:00Z'),
])
def test_two_publication_intents_preserve_unknown_acquisition(tmp_path, publication, applied_at):
    packet = _packet(publication)
    root = _root(tmp_path)
    bundle, meta, verified, result = _apply(packet, root, applied_at)
    assert result['noop'] is False
    assert verified['acquiredAt'] is None
    assert verified['acquiredAtProvenance'] == 'unknown'
    assert verified['sourceCompletionBound'] == '2026-10-20T07:19:53Z'
    assert verified['recoveryTimeEvidenceHash']
    assert verified['snapshotHash'] == packet['manifest']['newSnapshotHash']
    assert packet['stageResult']['generatedAt'] == '2026-10-20T07:19:53Z'
    assert meta['appliedAt'] == applied_at
    status = _status(meta, verified, prior_metadata=_old_prior(meta))
    b = status['biggo']
    assert b['status'] == 'ok'
    assert b['lastAcquiredAt'] is None
    assert 'timeProvenance' not in b
    assert b['lastAppliedAt'] == applied_at
    assert b['lastAcquiredAt'] != publication
    assert b['lastAcquiredAt'] != applied_at
    assert b['lastAcquiredAt'] != dt.datetime.fromtimestamp(
        NOW, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    text = '\n'.join(report_common.format_status({'sourceStatus': status}, 'vtest'))
    assert '原始取得時間未記錄' in text and '最近成功取得' not in text
    assert OLD_STAGE4[:16].replace('T', ' ') not in text


def test_second_intent_keeps_price_effects_and_source_bound_identical(tmp_path):
    first = _packet('2026-10-20T07:50:00Z')
    second = _packet('2026-10-20T07:55:00Z')
    assert first['newSnapshot'] == second['newSnapshot']
    assert first['stageResult'] == second['stageResult']
    assert first['manifest']['recoveryTimeEvidence'] == second['manifest']['recoveryTimeEvidence']
    assert first['bundleHash'] != second['bundleHash']
    r1 = _root(tmp_path / 'one')
    r2 = _root(tmp_path / 'two')
    _, m1, v1, _ = _apply(first, r1, '2026-10-20T08:00:00Z')
    _, m2, v2, _ = _apply(second, r2, '2026-10-20T08:05:00Z')
    assert v1['acquiredAt'] is None and v2['acquiredAt'] is None
    assert m1['appliedSnapshotHash'] == m2['appliedSnapshotHash']
    assert m1['appliedBundleHash'] != m2['appliedBundleHash']


def test_repeat_receive_is_noop_and_unknown_time_stays_unknown(tmp_path):
    packet = _packet('2026-10-20T07:50:00Z')
    root = _root(tmp_path)
    bundle, meta, verified, result = _apply(packet, root, '2026-10-20T08:00:00Z')
    assert result['noop'] is False
    again = apply_mod.apply_bundle(bundle, repo_root=str(root),
                                   applied_at='2026-10-20T08:05:00Z')
    assert again['noop'] is True
    meta2 = json.loads((root / 'prices_meta.json').read_text())
    assert meta2 == meta
    verified2 = sync_biggo_completed.verify_local_post(bundle, meta2, str(root))
    assert verified2['acquiredAt'] is None
    status = _status(meta2, verified2, prior_metadata=_old_prior(meta2))
    assert status['biggo']['lastAcquiredAt'] is None
    assert status['biggo']['lastAppliedAt'] == '2026-10-20T08:00:00Z'
    assert 'timeProvenance' not in status['biggo']


def test_pending_and_tampered_recovery_evidence_is_rejected(tmp_path):
    pending = recovery.build_recovery(E)
    with pytest.raises((apply_mod.ApplyError, sync_biggo_completed.SyncError)):
        sync_biggo_completed.verify_local_post(_bundle(pending), {}, str(tmp_path))

    final = _packet('2026-10-20T07:50:00Z')

    missing = copy.deepcopy(_bundle(final))
    del missing['manifest']['recoveryTimeEvidence']
    _rehash(missing)
    with pytest.raises((apply_mod.ApplyError, sync_biggo_completed.SyncError)):
        sync_biggo_completed.verify_local_post(missing, {}, str(tmp_path))

    incomplete = copy.deepcopy(_bundle(final))
    incomplete['manifest']['publishedAt'] = None
    _rehash(incomplete)
    with pytest.raises((apply_mod.ApplyError, sync_biggo_completed.SyncError)):
        sync_biggo_completed.verify_local_post(incomplete, {}, str(tmp_path))

    cases = [
        ('acquiredAt', '2026-10-20T08:00:00Z'),
        ('timeProvenance', 'recorded'),
        ('sourceCompletionBound', '2026-10-20T07:20:00Z'),
        ('sourceHead', '0' * 40),
        ('artifactSha256', 'sha256:' + '0' * 64),
        ('stageResultHash', 'sha256:' + '1' * 64),
    ]
    for key, value in cases:
        broken = copy.deepcopy(_bundle(final))
        broken['manifest']['recoveryTimeEvidence'][key] = value
        broken['manifest']['recovery']['timeEvidenceHash'] = apply_mod._canonical_hash(
            broken['manifest']['recoveryTimeEvidence'])
        _rehash(broken)
        with pytest.raises((apply_mod.ApplyError, sync_biggo_completed.SyncError)):
            sync_biggo_completed.verify_local_post(broken, {}, str(tmp_path))

def test_unverified_unknown_marker_does_not_clear_trusted_prior_time(tmp_path):
    packet = _packet('2026-10-20T07:50:00Z')
    root = _root(tmp_path)
    bundle, meta, verified, _ = _apply(packet, root, '2026-10-20T08:00:00Z')
    loose = dict(verified)
    loose.update({'acquiredAt': None, 'acquiredAtProvenance': 'unknown',
                  'recoveryTimeEvidenceHash': None})
    report = {'ok': True, 'postStateVerified': True,
              'cycleId': verified['cycleId'], 'stage': verified['stage'],
              'bundleHash': verified['bundleHash'], 'verifiedPost': loose}
    biggo = {'status': 'completed', 'cycle': verified['cycleId'], 'stage': 5,
             'snapshotHash': verified['snapshotHash'], 'acquiredAt': OLD_STAGE4}
    status = source_status.build_status(
        prices_meta=meta, biggo_status=biggo,
        biggo_snapshot_hash=verified['snapshotHash'], completed_sync=report,
        prior_metadata=_old_prior(meta), now=NOW)
    # The old stage4 time is not a witness for this stage5 snapshot; the
    # unverified/unknown marker must stay unknown rather than borrow it.
    assert status['biggo']['lastAcquiredAt'] is None
    assert status['biggo'].get('timeProvenance') != 'unknown'


def test_real_recovery_status_passes_formal_metadata_cli(tmp_path):
    """Exercise the actual daily core CLI, not only Python status/UI helpers."""
    import subprocess
    packet = _packet('2026-10-20T07:50:00Z')
    root = _root(tmp_path)
    _, meta, verified, _ = _apply(packet, root, '2026-10-20T08:00:00Z')
    status = _status(meta, verified, prior_metadata=_old_prior(meta))
    status_file = tmp_path / 'source-status.json'
    status_file.write_text(json.dumps(status))
    existing = json.loads((BASE / 'metadata.json').read_text())
    output = tmp_path / 'metadata.core.json'
    args = [sys.executable, str(BASE / 'scripts/gen-metadata.py'),
            '--stage', 'core', '--force', '--out', str(output),
            '--version', existing['version'], '--build', 'Brecovery-schema-contract',
            '--commit', existing['commit'], '--workflow-run-id', 'recovery-schema-contract',
            '--emsd-receipt', str(BASE / 'emsd_receipt.json'),
            '--dataset-csv', str(BASE / 'emsd_空調能源標籤.csv'),
            '--dataset-hash', existing['datasetHash'],
            '--record-count', str(existing['recordCount']),
            '--source-status', str(status_file)]
    for key, flag in [('modelCount', '--model-count'),
                      ('registrationCount', '--registration-count'),
                      ('rawRecordCount', '--raw-record-count')]:
        if key in existing:
            args.extend([flag, str(existing[key])])
    result = subprocess.run(args, capture_output=True, text=True, cwd=BASE)
    assert result.returncode == 0, result.stdout + result.stderr
    emitted = json.loads(output.read_text())['sourceStatus']['biggo']
    assert emitted['lastAcquiredAt'] is None
    assert verified['acquiredAtProvenance'] == 'unknown'
    assert verified['recoveryTimeEvidenceHash']
    assert 'timeProvenance' not in emitted
