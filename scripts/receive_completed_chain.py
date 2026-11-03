#!/usr/bin/env python3
"""Catch up verified completed stages using GET-only coordinator access."""
import json
import os
import posixpath
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import biggo_coordinator as coord
import price_cycle
import batch_utils
import verify_price_source_pin


def published_root(state):
    snap = coord.CoordinatorClient._safe_published_path(None, state.get('snapshotPath'))
    stage = coord.CoordinatorClient._safe_published_path(None, state.get('stageResultPath'))
    if not snap or not stage:
        raise ValueError('missing published bundle references')
    parent = posixpath.dirname(snap)
    if (posixpath.basename(snap) != 'biggo_prices.json'
            or posixpath.basename(stage) != 'stage-result.json'
            or posixpath.dirname(stage) != parent
            or posixpath.basename(parent) != coord.cycle_slug(state['cycleId'])):
        raise ValueError('inconsistent published bundle references')
    root = posixpath.dirname(parent)
    if not root:
        raise ValueError('missing published bundle root')
    return root


class ReadOnlyContents(coord.GitHubContentsClient):
    def put_file(self, *args, **kwargs):
        raise RuntimeError('receiver must not write coordinator')


def main():
    cfg = coord.CoordinatorConfig.from_env()
    if cfg is None:
        raise ValueError('coordinator not configured')
    contents = ReadOnlyContents(cfg)
    state, _ = contents.read_state()
    if not state or state['status'] != 'completed':
        print('chain receiver: remote not completed; no changes')
        return
    store = price_cycle.CoordinatorAnchorStore(contents)
    anchor, _, transition = price_cycle._read_store(store)
    if anchor['cycle'] != state['cycleId'] or anchor['stage'] != state['stage']:
        raise ValueError('terminal state and monthly anchor disagree')
    meta = batch_utils.load_meta(str(ROOT / 'prices_meta.json'))
    if meta.get('appliedBundleHash') == state.get('bundleHash'):
        print('chain receiver: already at terminal bundle; normal receiver verifies files')
        return
    # The source policy and full immutable chain remain authoritative.
    policy = json.loads((ROOT / 'price-policy.json').read_text())
    revision = policy['sourceRevision']
    verify_price_source_pin.verify_git(str(ROOT), revision)
    cfg.snapshot_dir = published_root(state)
    client = coord.CoordinatorClient(contents, owner='read-only-chain-receiver')
    result = price_cycle.reconcile_shared(
        meta, anchor, transition, store, client, str(ROOT),
        meta_path=str(ROOT / 'prices_meta.json'), writer_role='github',
        actual_source=revision)
    print(json.dumps({'receivedStages': result.get('applied'), 'cycle': result['cycle']}))


if __name__ == '__main__':
    main()
