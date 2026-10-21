# -*- coding: utf-8 -*-
"""Normal BigGo acquisition witness: collector -> immutable bundle -> receiver
-> sourceStatus -> metadata CLI -> HTML/PDF wording.

All provider traffic is stubbed.  The tests use the real stage-result builder,
coordinator validator, completed receiver and metadata CLI.
"""
import csv
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import biggo_acquisition
import biggo_apply
import biggo_canonical
import biggo_coordinator as coord
import fetch_biggo
import report_common
import source_status
import sync_biggo_completed as sync


def H(obj):
    return biggo_canonical.sha256_json(obj)


def entry(price='$2,500起', day='2026-10-20'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': day}


def stage_result(*, model='RA-10RF', acquired='2026-10-20T16:37:40Z',
                 generated='2026-10-20T16:37:43Z', cycle='2026-10-20:6/7',
                 stage=6, include_block=True):
    base = {'OLD': entry('$1,000起', '2026-10-01')}
    new = dict(base, **{model: entry()})
    outcomes = [{'model': model, 'canonicalKey': 'HITACHI|RA-10RF',
                 'outcome': 'priced', 'price': new[model],
                 'acquiredAt': acquired,
                 'resultHash': biggo_acquisition.result_hash(new[model])}]
    sr = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': cycle, 'stage': stage, 'mode': 'price-batch',
        'generatedAt': generated,
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'requestAttempts': {'token': 0, 'search': 1},
        'responseStatusCounts': {'200': 1},
        'outcomes': outcomes,
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                    'blacklistUpserts': {}, 'blacklistRemovals': []},
        'preState': {'trackingHash': H({}), 'blacklistHash': H({})},
        'postState': {'trackingHash': H({}), 'blacklistHash': H({})},
    }
    sr['effectsHash'] = H(sr['effects'])
    if include_block:
        sr['acquisition'] = biggo_acquisition.build_block(
            cycle_id=cycle, stage=stage, base_snapshot=base, new_snapshot=new,
            outcomes=outcomes, batch_started_at='2026-10-20T16:37:30Z',
            generated_at=generated)
    return sr, base, new


def bundle_for(sr, base, new, *, cycle='2026-10-20:6/7', stage=6,
               published='2026-10-20T16:37:43Z'):
    manifest = {
        'schemaVersion': 2, 'cycleId': cycle, 'stage': stage,
        'mode': 'price-batch', 'publishedAt': published,
        'baseSnapshotHash': H(base), 'newSnapshotHash': H(new),
        'snapshotHash': H(new), 'stageResultHash': H(sr),
    }
    return {'manifest': manifest, 'stageResult': sr,
            'baseSnapshot': base, 'newSnapshot': new,
            'baseSnapshotHash': H(base), 'newSnapshotHash': H(new),
            'stageResultHash': H(sr), 'bundleHash': H(manifest)}


def write_local(root, bundle):
    price = bundle['newSnapshot']
    tracking = {}
    blacklist = {}
    for name, data in (('biggo_prices.json', price),
                       ('model_status.json', tracking),
                       ('model_blacklist.json', {'models': blacklist})):
        (root / name).write_text(json.dumps(data), encoding='utf-8')
    cycle = bundle['manifest']['cycleId']
    stage = bundle['manifest']['stage']
    meta = {'last_run': '2026-10-20', 'price_batch_start': '2026-10-20',
            'price_batch_idx': stage, 'appliedAt': '2026-10-20T16:37:43Z',
            'appliedBundleHash': bundle['bundleHash'],
            'appliedSnapshotHash': bundle['newSnapshotHash'],
            'last_applied_cycle': cycle,
            'appliedSnapshotId': ('biggo-' + cycle + '-stage' + str(stage)
                                  + '-' + bundle['newSnapshotHash'][-12:])}
    (root / 'prices_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return meta


def test_collector_records_only_validated_provider_results(monkeypatch):
    payload = {'list': [{'title': 'HITACHI RA-10RF window air conditioner',
                         'price': '2500', 'nindex': 'hk_shop'}]}
    monkeypatch.setattr(fetch_biggo, '_api_search',
                        lambda model, **kw: (payload, True))
    fetch_biggo.reset_acquisitions()
    model, result, ok = fetch_biggo._search_tri_state('RA-10RF')
    assert ok and result and model == 'RA-10RF'
    got = fetch_biggo._acquisition_of('RA-10RF', result)
    assert got['acquiredAt'].endswith('Z')
    assert got['resultHash'] == biggo_acquisition.result_hash(result)
    merged = fetch_biggo._merge_outcomes(['RA-10RF'], [], [], [], {'RA-10RF': result},
                                         {'ra-10rf': 'HITACHI'})
    assert merged[0]['acquiredAt'] == got['acquiredAt']
    assert merged[0]['resultHash'] == got['resultHash']
    # A failed/transport result never creates a witness.
    fetch_biggo.reset_acquisitions()
    monkeypatch.setattr(fetch_biggo, '_api_search', lambda model, **kw: (None, False))
    assert fetch_biggo._search_tri_state('RA-10RF') == ('RA-10RF', None, False)
    assert fetch_biggo._ACQUISITIONS == {}


def test_witness_binds_cycle_stage_snapshots_and_results():
    sr, base, new = stage_result()
    block = sr['acquisition']
    biggo_acquisition.validate_block(
        block, outcomes=sr['outcomes'], cycle_id=sr['cycleId'], stage=sr['stage'],
        base_snapshot=base, new_snapshot=new, generated_at=sr['generatedAt'],
        now=1_800_000_000)
    # Coordinator publish path accepts the same block.
    coord.validate_stage_result(sr, base, new, cycle_id=sr['cycleId'],
                                stage=sr['stage'], mode='price-batch')
    # Wrong cycle/stage, snapshots, result hash, future/malformed all fail.
    for mutate in (
            lambda b: b.update(cycleId='2026-10-20:7/7'),
            lambda b: b.update(stage=7),
            lambda b: b.update(newSnapshotHash='sha256:' + '1' * 64),
            lambda b: b['results'][0].update(resultHash='sha256:' + '2' * 64),
            lambda b: b['results'][0].update(acquiredAt='2099-01-01T00:00:00Z'),
            lambda b: b.update(basis='report-clock'),
            lambda b: b.update(results=[])):
        bad = json.loads(json.dumps(sr))
        mutate(bad['acquisition'])
        with pytest.raises((biggo_acquisition.AcquisitionError, coord.CoordinatorStateError)):
            coord.validate_stage_result(bad, base, new, cycle_id=sr['cycleId'],
                                        stage=sr['stage'], mode='price-batch')
    # A legacy stage result has no block and remains importable/unknown.
    legacy, l_base, l_new = stage_result(include_block=False)
    coord.validate_stage_result(legacy, l_base, l_new, cycle_id=legacy['cycleId'],
                                stage=legacy['stage'], mode='price-batch')
    assert biggo_acquisition.from_stage_result(legacy)['provenance'] == 'unknown'


def test_receiver_uses_witness_not_publication_and_rejects_tamper(tmp_path):
    sr, base, new = stage_result()
    bundle = bundle_for(sr, base, new)
    meta = write_local(tmp_path, bundle)
    verified = sync.verify_local_post(bundle, meta, tmp_path)
    assert verified['acquiredAt'] == '2026-10-20T16:37:40Z'
    assert verified['acquiredAtProvenance'] == 'recorded'
    assert verified['acquisitionHash'] == H(sr['acquisition'])
    assert verified['acquiredAt'] != bundle['manifest']['publishedAt']
    # Legacy bundle: manifest.publishedAt is never mapped to acquiredAt.
    legacy_sr, l_base, l_new = stage_result(include_block=False)
    legacy = bundle_for(legacy_sr, l_base, l_new)
    lmeta = write_local(tmp_path, legacy)
    out = sync.verify_local_post(legacy, lmeta, tmp_path)
    assert out['acquiredAt'] is None
    assert out['acquiredAtProvenance'] == 'unknown'
    assert out['acquiredAt'] != legacy['manifest']['publishedAt']
    # Tampered block fails the receiver.
    bad_sr, b_base, b_new = stage_result()
    bad_sr['acquisition']['results'][0]['acquiredAt'] = '2026-10-20T16:00:00Z'
    bad = bundle_for(bad_sr, b_base, b_new)
    bmeta = write_local(tmp_path, bad)
    with pytest.raises(sync.SyncError):
        sync.verify_local_post(bad, bmeta, tmp_path)


def test_source_status_and_report_keep_acquire_and_apply_separate(tmp_path):
    sr, base, new = stage_result()
    bundle = bundle_for(sr, base, new)
    meta = write_local(tmp_path, bundle)
    verified = sync.verify_local_post(bundle, meta, tmp_path)
    receiver = {'ok': True, 'postStateVerified': True, 'reason': 'already-applied',
                'cycleId': meta['last_applied_cycle'], 'stage': meta['price_batch_idx'],
                'bundleHash': bundle['bundleHash'], 'verifiedPost': verified,
                'acquiredAt': verified['acquiredAt'],
                'acquiredAtProvenance': verified['acquiredAtProvenance'],
                'acquisitionHash': verified['acquisitionHash']}
    status = source_status.build_status(
        metadata={'datasetDate': '2026-10-20', 'datasetHash': 'sha256:' + 'a' * 64,
                  'deployTime': '2026-10-20T16:40:00Z'},
        emsd_receipt={'success': True, 'aborted': False, 'error': None,
                      'retrievedAt': '2026-10-20T16:30:00Z',
                      'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta=meta,
        biggo_status={'status': 'completed', 'cycle': meta['last_applied_cycle'],
                      'stage': meta['price_batch_idx'], 'at': '2026-10-20T16:37:43Z'},
        biggo_snapshot_hash=bundle['newSnapshotHash'],
        completed_sync=receiver,
        now=1_793_000_000)
    assert status['biggo']['status'] == 'ok'
    assert status['biggo']['lastAcquiredAt'] == '2026-10-20T16:37:40Z'
    assert status['biggo']['lastAppliedAt'] == '2026-10-20T16:37:43Z'
    assert source_status.validate_status(status) == []
    text = '\n'.join(report_common.format_status(
        {'version': '1.2.9', 'datasetDate': '2026-10-20',
         'deployTime': '2026-10-20T16:40:00Z', 'sourceStatus': status}, '1.2.9'))
    assert 'BigGo 最近成功取得 2026-10-21 00:37 HKT' in text
    assert '最近成功套用 2026-10-21 00:37 HKT' in text
    # Same snapshot, no witness: only apply is shown, explicitly unknown origin.
    legacy_sr, l_base, l_new = stage_result(include_block=False)
    legacy = bundle_for(legacy_sr, l_base, l_new)
    lmeta = write_local(tmp_path, legacy)
    lv = sync.verify_local_post(legacy, lmeta, tmp_path)
    lstatus = source_status.build_status(
        metadata={'datasetDate': '2026-10-20', 'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta=lmeta,
        biggo_status={'status': 'completed', 'cycle': lmeta['last_applied_cycle'],
                      'stage': lmeta['price_batch_idx']},
        biggo_snapshot_hash=legacy['newSnapshotHash'],
        completed_sync={'ok': True, 'postStateVerified': True, 'reason': 'already-applied',
                        'cycleId': lmeta['last_applied_cycle'],
                        'stage': lmeta['price_batch_idx'],
                        'bundleHash': legacy['bundleHash'], 'verifiedPost': lv,
                        'acquiredAt': lv['acquiredAt'],
                        'acquiredAtProvenance': lv['acquiredAtProvenance']},
        now=1_793_000_000)
    assert lstatus['biggo']['lastAcquiredAt'] is None
    ltext = '\n'.join(report_common.format_status(
        {'version': '1.2.9', 'datasetDate': '2026-10-20',
         'deployTime': '2026-10-20T16:40:00Z', 'sourceStatus': lstatus}, '1.2.9'))
    assert '原始取得時間未記錄' in ltext and '最近成功取得' not in ltext


def _write_emsd(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['品牌', '型號'] + [f'c{i}' for i in range(13)])
        w.writerow(['HITACHI', 'RA-10RF'] + ['1'] * 13)
    digest = 'sha256:' + hashlib.sha256(csv_path.read_bytes()).hexdigest()
    receipt = tmp_path / 'emsd-receipt.json'
    receipt.write_text(json.dumps({
        'retrievedAt': '2026-10-20T16:30:00Z',
        'sourceUrl': 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php?x=1',
        'success': True, 'pagesExpected': 1, 'pagesFetched': 1,
        'totalRows': 1, 'aborted': False, 'error': None,
        'perPageRows': [1], 'datasetHash': digest}), encoding='utf-8')
    return csv_path, receipt, digest


def test_metadata_cli_preserves_recorded_acquisition_not_publication(tmp_path):
    sr, base, new = stage_result()
    bundle = bundle_for(sr, base, new)
    meta = write_local(tmp_path, bundle)
    verified = sync.verify_local_post(bundle, meta, tmp_path)
    status = source_status.build_status(
        metadata={'datasetDate': '2026-10-20', 'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta=meta,
        biggo_status={'status': 'completed', 'cycle': meta['last_applied_cycle'],
                      'stage': meta['price_batch_idx']},
        biggo_snapshot_hash=bundle['newSnapshotHash'],
        completed_sync={'ok': True, 'postStateVerified': True, 'reason': 'already-applied',
                        'cycleId': meta['last_applied_cycle'],
                        'stage': meta['price_batch_idx'],
                        'bundleHash': bundle['bundleHash'], 'verifiedPost': verified,
                        'acquiredAt': verified['acquiredAt'],
                        'acquiredAtProvenance': verified['acquiredAtProvenance'],
                        'acquisitionHash': verified['acquisitionHash']},
        now=1_793_000_000)
    status_path = tmp_path / 'source_status.json'
    status_path.write_text(json.dumps(status), encoding='utf-8')
    csv_path, receipt, digest = _write_emsd(tmp_path)
    core = tmp_path / 'core.json'
    env = dict(os.environ, GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='schedule')
    r = subprocess.run([sys.executable, os.path.join(BASE, 'scripts', 'gen-metadata.py'),
                        '--stage', 'core', '--out', str(core), '--force',
                        '--version', '1.2.9', '--build', 'B20261020.TEST',
                        '--commit', 'a' * 40, '--workflow-run-id', '42',
                        '--emsd-receipt', str(receipt), '--dataset-csv', str(csv_path),
                        '--record-count', '1', '--dataset-hash', digest,
                        '--source-status', str(status_path)],
                       cwd=BASE, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    manifest = tmp_path / 'payload.json'
    manifest.write_text(json.dumps({'files': ['README.md']}), encoding='utf-8')
    out = tmp_path / 'metadata.json'
    r2 = subprocess.run([sys.executable, os.path.join(BASE, 'scripts', 'gen-metadata.py'),
                         '--stage', 'finalize', '--core', str(core),
                         '--payload-manifest', str(manifest), '--dataset-csv', str(csv_path),
                         '--out', str(out)],
                        cwd=BASE, env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    final = json.loads(out.read_text())
    assert final['sourceStatus']['biggo']['lastAcquiredAt'] == '2026-10-20T16:37:40Z'
    assert final['sourceStatus']['biggo']['lastAcquiredAt'] != final['deployTime']
    from extract_governance import metadata_schema
    from validate_metadata import validate
    assert validate(final, metadata_schema()) == []


def test_winner_local_receiver_survives_pre_provider_not_completed(tmp_path):
    sr, base, new = stage_result()
    bundle = bundle_for(sr, base, new)
    meta = write_local(tmp_path, bundle)
    verified = sync.verify_local_post(bundle, meta, tmp_path)
    local = {'ok': True, 'postStateVerified': True, 'reason': 'applied',
             'cycleId': meta['last_applied_cycle'], 'stage': meta['price_batch_idx'],
             'bundleHash': bundle['bundleHash'], 'verifiedPost': verified,
             'acquiredAt': verified['acquiredAt'],
             'acquiredAtProvenance': verified['acquiredAtProvenance'],
             'acquisitionHash': verified['acquisitionHash']}
    status = source_status.build_status(
        metadata={'datasetDate': '2026-10-20', 'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta=meta,
        biggo_status={'status': 'completed', 'cycle': meta['last_applied_cycle'],
                      'stage': meta['price_batch_idx'], 'localReceiver': local},
        biggo_snapshot_hash=bundle['newSnapshotHash'],
        completed_sync={'ok': False, 'reason': 'remote-not-completed'},
        now=1_793_000_000)
    assert status['biggo']['status'] == 'ok'
    assert status['biggo']['lastAcquiredAt'] == '2026-10-20T16:37:40Z'
    assert status['biggo']['lastAppliedAt'] == '2026-10-20T16:37:43Z'
