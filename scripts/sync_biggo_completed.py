#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only completed-result receiver (NO lease, NO provider, NO CAS writes).

The existing `biggo_stage_runner.run_stage` inactive path must remain zero
network.  This separate entrypoint exists for an explicitly invoked catch-up:
it GETs the shared coordinator state/bundle, verifies the local baseline and
stage order, and applies the completed bundle locally.  It never acquires a
lease, never calls BigGo, and never writes coordinator state.

Sequential catch-up only: a missing stage/history is a hard alert, not a skip.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import batch_utils  # noqa: E402
import biggo_apply  # noqa: E402
import biggo_coordinator as coord  # noqa: E402


class SyncError(RuntimeError):
    pass


def plan_sync(meta, state):
    """Pure baseline/order check; returns {'ok', 'reason', 'stage'}."""
    meta = meta if isinstance(meta, dict) else {}
    state = state if isinstance(state, dict) else {}
    if state.get('status') != 'completed':
        return {'ok': False, 'reason': 'remote-not-completed', 'stage': None}
    cycle = str(state.get('cycleId') or '')
    stage = state.get('stage')
    if not cycle or isinstance(stage, bool) or not isinstance(stage, int) or stage < 1:
        return {'ok': False, 'reason': 'remote-cycle-invalid', 'stage': stage}
    date = cycle.split(':', 1)[0]
    local_start = str(meta.get('price_batch_start') or '')
    local_idx = meta.get('price_batch_idx', 0)
    if meta.get('appliedBundleHash') and meta.get('last_applied_cycle') == cycle:
        return {'ok': False, 'reason': 'already-applied', 'stage': stage}
    if not local_start:
        return {'ok': False, 'reason': 'missing-local-history', 'stage': stage}
    if local_start != date:
        return {'ok': False, 'reason': 'cycle-mismatch', 'stage': stage}
    if isinstance(local_idx, bool) or not isinstance(local_idx, int):
        return {'ok': False, 'reason': 'local-stage-invalid', 'stage': stage}
    if stage > local_idx + 1:
        return {'ok': False, 'reason': 'missing-intermediate-stage', 'stage': stage,
                'localStage': local_idx}
    if stage <= local_idx and not meta.get('appliedBundleHash'):
        return {'ok': False, 'reason': 'stage-not-ahead', 'stage': stage,
                'localStage': local_idx}
    return {'ok': True, 'reason': 'ready', 'stage': stage}


def main(argv=None):
    ap = argparse.ArgumentParser(description='sync verified completed BigGo bundle (read-only GET)')
    ap.add_argument('--dry-run', action='store_true', default=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--repair', default=os.environ.get('AIRCON_BIGGO_REPAIR_RECEIPT'))
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)
    apply = bool(args.apply)
    cfg = coord.CoordinatorConfig.from_env()
    if cfg is None:
        print('❌ coordinator 未配置；零網絡、零寫入', file=sys.stderr)
        return 2
    contents = coord.GitHubContentsClient(cfg)
    try:
        state, _sha = contents.read_state()
    except coord.CoordinatorError as e:
        print(f'❌ coordinator state 讀取失敗（零寫入）：{type(e).__name__}', file=sys.stderr)
        return 2
    if state is None:
        print('❌ coordinator state 缺失（零寫入）', file=sys.stderr)
        return 2
    try:
        meta = batch_utils.load_meta(os.path.join(BASE, 'prices_meta.json'))
    except batch_utils.MetaError as e:
        print(f'❌ local prices_meta 唔合格：{e}', file=sys.stderr)
        return 1
    plan = plan_sync(meta, state)
    report = {'schemaVersion': 1, 'mode': 'sync-completed', 'plan': plan,
              'cycleId': state.get('cycleId'), 'stage': state.get('stage'),
              'applied': False}
    if not plan['ok']:
        print(f"sync skipped: {plan['reason']}（零 BigGo／零 lease／零 coordinator write）")
        if args.report:
            with open(args.report, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        return 1 if plan['reason'] in ('missing-local-history', 'missing-intermediate-stage',
                                       'cycle-mismatch', 'local-stage-invalid') else 0
    client = coord.CoordinatorClient(contents, owner='read-only-sync', now=lambda: 0)
    client.state = state
    try:
        bundle = client.import_bundle(state['cycleId'], state['stage'])
    except coord.CoordinatorError as e:
        print(f'❌ completed bundle 匯入失敗（零寫入）：{type(e).__name__}', file=sys.stderr)
        return 2
    repair = None
    if args.repair:
        try:
            with open(args.repair, encoding='utf-8') as f:
                repair = json.load(f)
        except (OSError, ValueError) as e:
            print(f'❌ repair receipt 讀取失敗：{e}', file=sys.stderr)
            return 2
    report['bundleHash'] = bundle['bundleHash']
    if not apply:
        print(f"sync dry-run ready: bundle={bundle['bundleHash']} stage={state['stage']}")
    else:
        try:
            result = biggo_apply.apply_bundle(bundle, repo_root=BASE, repair=repair)
        except biggo_apply.ApplyError as e:
            print(f'❌ local apply 失敗（零 coordinator write）：{e}', file=sys.stderr)
            return 2
        report['applied'] = True
        report['result'] = result
        print(f"sync applied: noop={result.get('noop')} bundle={bundle['bundleHash']}")
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
