# -*- coding: utf-8 -*-
"""Stage2 advance must not mutate an already-active BigGo price cycle."""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]
import advance_queue
import batch_utils


def test_stage2_advance_active_cycle_preserves_prices_meta_and_only_clears_queue(
        tmp_path, monkeypatch):
    queue = tmp_path / 'update_queue.json'
    queue.write_text(json.dumps({'stage': 2, 'models': ['MC18VXH', 'GWT07ES']}),
                     encoding='utf-8')
    active = {
        'last_applied_cycle': '2026-10-10:5/7',
        'price_batch_idx': 5,
        'price_batch_start': '2026-10-10',
        'last_price_month': '2026-10',
        'appliedSnapshotHash': 'sha256:' + 'a' * 64,
        'appliedBundleHash': 'sha256:' + 'b' * 64,
        'appliedAt': '2026-10-20T09:00:20Z',
    }
    writes = []

    def fake_load():
        return dict(active)

    def fake_save(meta, path=None):
        writes.append((dict(meta), path))
        raise AssertionError('active cycle must not be written')

    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(queue))
    monkeypatch.setattr(batch_utils, 'load_meta', fake_load)
    monkeypatch.setattr(batch_utils, 'save_meta', fake_save)
    assert advance_queue.main() == 0
    assert writes == []
    assert json.loads(queue.read_text()) == {'stage': 0, 'models': []}
    # The cycle fields remain exactly the supplied active state.
    assert fake_load() == active
