import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import receive_completed_chain as receiver


def state(root='coordinator/snapshots'):
    return {'cycleId': '2026-11-03:2/7',
            'snapshotPath': root + '/2026-11-03-2-7/biggo_prices.json',
            'stageResultPath': root + '/2026-11-03-2-7/stage-result.json'}


def test_published_root_dynamic():
    assert receiver.published_root(state()) == 'coordinator/snapshots'
    assert receiver.published_root(state('new/archive')) == 'new/archive'


@pytest.mark.parametrize('path', ['/absolute', '../escape', 'bad/../escape',
                                 'https://host/file', 'other/2026-11-03-1-7/biggo_prices.json'])
def test_rejects_unsafe_or_wrong_stage(path):
    d = state(); d['snapshotPath'] = path
    with pytest.raises(ValueError):
        receiver.published_root(d)


def test_receiver_cannot_write_coordinator():
    with pytest.raises(RuntimeError):
        receiver.ReadOnlyContents.put_file(None, 'anything', b'anything')


def test_cooldown_does_not_block_completed_receiving(tmp_path, monkeypatch):
    import json
    doc = state()
    doc.update(status='completed', stage=2, bundleHash='sha256:new', cooldownUntil=9999999999)
    (tmp_path / 'price-policy.json').write_text(json.dumps({'sourceRevision': 'a' * 40}))
    monkeypatch.setattr(receiver, 'ROOT', tmp_path)
    cfg = type('Config', (), {'snapshot_dir': 'old-path'})()
    monkeypatch.setattr(receiver.coord.CoordinatorConfig, 'from_env', lambda: cfg)
    contents = type('Contents', (), {'read_state': lambda self: (doc, 'sha')})()
    monkeypatch.setattr(receiver, 'ReadOnlyContents', lambda config: contents)
    monkeypatch.setattr(receiver.price_cycle, 'CoordinatorAnchorStore', lambda c: object())
    monkeypatch.setattr(receiver.price_cycle, '_read_store', lambda store: (
        {'cycle': doc['cycleId'], 'stage': 2}, 'sha', {}))
    monkeypatch.setattr(receiver.batch_utils, 'load_meta', lambda path: {'price_batch_idx': 0})
    monkeypatch.setattr(receiver.verify_price_source_pin, 'verify_git', lambda *a: None)
    monkeypatch.setattr(receiver.coord, 'CoordinatorClient', type('NoProviderClient', (), {
        '__init__': lambda self, *a, **kw: None,
        '_safe_published_path': lambda self, v: v}))
    received = []
    def reconcile(*args, **kwargs):
        received.append(cfg.snapshot_dir)
        return {'cycle': doc['cycleId'], 'applied': [1, 2]}
    monkeypatch.setattr(receiver.price_cycle, 'reconcile_shared', reconcile)
    receiver.main()
    assert received == ['coordinator/snapshots']
    assert doc['cooldownUntil'] == 9999999999
