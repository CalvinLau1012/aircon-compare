# -*- coding: utf-8 -*-
"""r24 F01 v3 單元：strict meta/transition/anchor、identity、plan authority-first、FileStore CAS。"""
import datetime
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

import price_cycle as pc  # noqa: E402
import price_cycle_entry as entry  # noqa: E402

NOW = datetime.datetime(2026, 10, 5, 3, 0, tzinfo=datetime.timezone.utc)
ACK = pc.transition_ack_bytes('a' * 40, 'b' * 40, 'sha256:' + '1' * 64,
                              'sha256:' + '2' * 64)
SOURCE = 'a' * 40


def _store(tmp, name='anchor.json', ack=True):
    p = tmp / name
    t = tmp / (name + '.transition')
    if ack:
        t.write_bytes(ACK)
    return pc.FileAnchorStore(str(p), str(t))


def _meta(tmp, name, **over):
    m = {'last_price_month': '2026-09'}
    m.update(over)
    p = tmp / name
    p.write_text(json.dumps(m), encoding='utf-8')
    return p


def test_meta_strict_uses_batch_utils():
    assert pc.validate_meta({'price_batch_idx': True})
    assert pc.validate_meta({'price_batch_start': '2026-02-30'})
    assert pc.validate_meta({'blocked_until': 'invalid'})
    assert pc.validate_meta({'last_price_month': '2026-13'})
    assert pc.validate_meta({'price_batch_start': '2026-10-05', 'price_batch_idx': 0,
                             'last_price_month': '2026-10'}) == []


def test_cycle_identity_matches_runner_stage():
    meta = {'price_batch_start': '2026-10-04', 'price_batch_idx': 3}
    assert pc.cycle_identity(meta) == '2026-10-04:4/7'
    done = {'price_batch_start': '2026-10-04', 'price_batch_idx': 7}
    assert pc.cycle_identity(done) is None


def test_transition_requires_two_named_writers_and_source():
    weak = {'formatVersion': 2, 'protocol': pc.PROTOCOL, 'oldEntryStopped': True,
            'writers': {'github': {'protocol': pc.PROTOCOL, 'sourceRevision': 'x'}},
            'initial': {'snapshotHash': 'sha256:' + '1' * 64,
                        'baseHash': 'sha256:' + '2' * 64}, 'at': '2026-10-04'}
    errs = pc.validate_transition(weak)
    assert 'writer-private-missing' in errs and 'writer-github-sourceRevision' in errs
    assert pc.validate_transition(json.loads(ACK)) == []
    legacy = json.loads(ACK)
    legacy['oldEntryStopped'] = False
    assert 'old-entry-not-acknowledged' in pc.validate_transition(legacy)


def test_anchor_validation_no_crash_and_status_rules():
    base = {'formatVersion': 2, 'protocol': pc.PROTOCOL, 'month': '2026-10',
            'cycle': '2026-10-05:1/7', 'startedAt': '2026-10-05', 'stage': 0,
            'status': 'active'}
    assert pc.validate_anchor(base) == []
    null_stage = dict(base, stage=None)
    assert 'stage-invalid' in pc.validate_anchor(null_stage)
    assert 'active-stage-7' in pc.validate_anchor(dict(base, stage=7, cycle='2026-10-05:7/7'))
    assert 'completed-stage-not-7' in pc.validate_anchor(
        dict(base, stage=3, status='completed', cycle='2026-10-05:3/7'))


def test_plan_authority_first_needs_review_beats_local_active():
    anchor = {'formatVersion': 2, 'protocol': pc.PROTOCOL, 'month': '2026-10',
              'cycle': '2026-10-04:1/7', 'startedAt': '2026-10-04', 'stage': 0,
              'status': 'needs-review'}
    meta = {'price_batch_start': '2026-10-05', 'price_batch_idx': 0,
            'last_price_month': '2026-10'}
    got = pc.plan(meta, anchor, json.loads(ACK), NOW, actual_source=SOURCE,
                  writer_role='github')
    assert got['reason'] == 'anchor-needs-review', got
    conflict = pc.plan({'price_batch_start': '2026-10-05', 'price_batch_idx': 0,
                        'last_price_month': '2026-09'},
                       dict(anchor, status='active'), json.loads(ACK), NOW,
                       actual_source=SOURCE, writer_role='github')
    assert conflict['reason'] == 'local-authority-conflict', conflict


def test_files_store_cas_and_corrupt_block(tmp_path):
    store = _store(tmp_path, ack=False)
    doc = {'formatVersion': 2, 'protocol': pc.PROTOCOL, 'month': '2026-10', 'cycle': '2026-10-05:1/7',
           'startedAt': '2026-10-05', 'stage': 0, 'status': 'active'}
    store.path = str(tmp_path / 'missing.json')
    (tmp_path / 'missing.json').write_bytes(b'{broken')
    meta = _meta(tmp_path, 'm.json')
    got = pc.ensure_monthly_cycle(str(meta), NOW, allow_write=True, store=store)
    assert got['written'] is False and got['reason'] in ('blocked-anchor', 'transition-not-acknowledged')


def test_advance_requires_evidence_and_sequence(tmp_path):
    store = _store(tmp_path)
    anchor = {'formatVersion': 2, 'protocol': pc.PROTOCOL, 'month': '2026-10',
              'cycle': '2026-10-05:1/7', 'startedAt': '2026-10-05', 'stage': 0,
              'status': 'active'}
    (tmp_path / 'anchor.json').write_bytes(pc._canonical_bytes(anchor))
    try:
        pc.advance_stage(store, '2026-10', 7, 'sha256:' + 'a' * 64, status='completed')
    except pc.AnchorError as e:
        assert 'stage-jump-refused' in str(e)
    else:
        raise AssertionError('0->7 必須拒')
    try:
        pc.advance_stage(store, '2026-10', 7, 'sha256:' + 'a' * 64)
    except pc.AnchorError as e:
        assert 'stage-jump' in str(e)
    pc.advance_stage(store, '2026-10', 1, 'sha256:' + 'b' * 64)
    assert json.loads((tmp_path / 'anchor.json').read_text())['stage'] == 1
    assert pc.advance_stage(store, '2026-10', 1, 'sha256:' + 'b' * 64)['noop'] is True


def test_cli_plan_and_not_enabled(tmp_path, monkeypatch):
    store = _store(tmp_path)
    meta = _meta(tmp_path, 'm.json')
    # Default is receive-only: a safe skip, never an implicit local start.
    assert entry.main(['--meta', str(meta), '--anchor-file', str(store.path)]) == 0
    assert entry.main(['--meta', str(meta), '--anchor-file', str(store.path),
                       '--apply']) == 0
    monkeypatch.setenv('AIRCON_PRICE_QUERY_MODE', 'fixture-local')
    monkeypatch.setenv('AIRCON_PRICE_FIXTURE', '1')
    assert entry.main(['--meta', str(meta), '--anchor-file', str(store.path)]) == 0
    assert entry.main(['--meta', str(meta), '--anchor-file', str(store.path),
                       '--apply']) == 3
