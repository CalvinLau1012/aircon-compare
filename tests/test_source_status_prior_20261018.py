# -*- coding: utf-8 -*-
import copy, datetime as dt, json, os, subprocess, sys
import pytest
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import source_status as ss
SNAP = 'sha256:' + 'a' * 64
SID = 'biggo-2026-10-10:4/7-stage4-aaaaaaaaaaaa'
APPLIED = '2026-10-18T06:12:23Z'
ACQ = '2026-10-17T16:38:58Z'
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc).timestamp()

def prior(**over):
    b = {'snapshotHash': SNAP, 'snapshotId': SID, 'lastAppliedAt': APPLIED,
         'lastAcquiredAt': ACQ}
    b.update(over.pop('biggo', {}))
    return {'sourceStatus': {'schemaVersion': 1, 'biggo': b}}

def prices(**over):
    d = {'last_run': '2026-10-18', 'price_batch_start': '2026-10-10',
         'price_batch_idx': 4, 'appliedAt': APPLIED,
         'appliedSnapshotHash': SNAP, 'appliedSnapshotId': SID}
    d.update(over); return d

def build(prior_meta, pmeta=None, actual=SNAP, now=NOW, history=None):
    return ss.build_status(
        prices_meta=pmeta or prices(), prior_metadata=prior_meta,
        history_repo=history, biggo_snapshot_hash=actual,
        biggo_status={'status': 'skip-not-active', 'cycle': '2026-10-10:5/7', 'stage': 5},
        completed_sync={'ok': False, 'reason': 'remote-not-completed'}, now=now)

def test_real_stage4_prior_time_survives_pending_stage5():
    s = build(prior())
    assert s['biggo']['status'] == 'pending'
    assert s['biggo']['safeReason'] == 'completed-sync-remote-not-completed'
    assert s['biggo']['lastAcquiredAt'] == ACQ
    assert s['biggo']['lastAppliedAt'] == APPLIED
    assert s['biggo']['snapshotHash'] == SNAP and s['biggo']['snapshotId'] == SID

def test_prior_time_is_rejected_for_wrong_identity_time_or_shape():
    cases = [prior(biggo={'snapshotHash': 'sha256:' + 'b' * 64}),
             prior(biggo={'snapshotId': 'other'}),
             prior(biggo={'lastAppliedAt': '2026-10-17T06:12:23Z'}),
             prior(biggo={'lastAcquiredAt': '2026-10-19T00:00:00Z'}),
             prior(biggo={'lastAcquiredAt': '2026-10-18T07:12:23Z'}),
             {'sourceStatus': {'schemaVersion': 999, 'biggo': prior()['sourceStatus']['biggo']}},
             {}]
    for p in cases:
        s = build(p)
        assert s['biggo']['lastAcquiredAt'] is None, p

def test_new_verified_snapshot_does_not_inherit_old_acquisition():
    new_snap = 'sha256:' + 'c' * 64
    p = prior()
    s = build(p, prices(appliedSnapshotHash=new_snap, appliedSnapshotId='new'), actual=new_snap)
    assert s['biggo']['lastAcquiredAt'] is None

def test_inputs_are_not_mutated():
    p = prior(); q = prices(); before = copy.deepcopy((p, q))
    build(p, q)
    assert (p, q) == before

def test_git_history_recovers_only_exact_committed_metadata(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir(); subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 't@e.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 't'], check=True)
    (repo / 'metadata.json').write_text(json.dumps({'sourceStatus': {'schemaVersion': 1, 'biggo': {'snapshotHash': SNAP, 'snapshotId': SID, 'lastAppliedAt': APPLIED, 'lastAcquiredAt': ACQ}}}))
    subprocess.run(['git', '-C', str(repo), 'add', 'metadata.json'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'old'], check=True)
    s = build(None, history=str(repo))
    assert s['biggo']['lastAcquiredAt'] == ACQ
    assert ss._history_acquisition(str(repo), prices(), SNAP, APPLIED,
                                   dt.datetime.fromtimestamp(NOW, dt.timezone.utc), 32) == ACQ
    assert ss._history_acquisition(str(repo), prices(), 'sha256:' + 'd' * 64, APPLIED,
                                   dt.datetime.fromtimestamp(NOW, dt.timezone.utc), 32) is None
    assert ss._history_acquisition(str(repo), prices(), SNAP, APPLIED,
                                   dt.datetime.fromtimestamp(NOW, dt.timezone.utc), 33) is None
    assert ss._history_acquisition(str(tmp_path / 'missing'), prices(), SNAP, APPLIED,
                                   dt.datetime.fromtimestamp(NOW, dt.timezone.utc), 32) is None


def test_prior_biggo_does_not_pollute_emsd_fresh_facts():
    old = prior()
    old['datasetDate'] = '2026-01-01'
    old['datasetHash'] = 'sha256:' + 'e' * 64
    receipt = {'success': True, 'aborted': False, 'error': None,
               'retrievedAt': '2026-10-18T06:00:00Z',
               'datasetHash': 'sha256:' + 'f' * 64}
    s = ss.build_status(prices_meta=prices(), prior_metadata=old,
                        biggo_snapshot_hash=SNAP,
                        biggo_status={'status': 'skip-not-active', 'cycle': '2026-10-10:5/7', 'stage': 5},
                        completed_sync={'ok': False, 'reason': 'remote-not-completed'},
                        emsd_receipt=receipt, now=NOW)
    assert s['emsd']['lastAcquiredAt'] == '2026-10-18T06:00:00Z'
    assert s['emsd']['snapshotHash'] == 'sha256:' + 'f' * 64
    assert s['emsd']['lastAppliedDate'] is None
    assert s['biggo']['lastAcquiredAt'] == ACQ
