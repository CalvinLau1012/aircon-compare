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
import hashlib
import json
import os
import sys
import time

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


def _write_report(path, report):
    if not path:
        return None
    body = {k: v for k, v in report.items() if k != 'receiptId'}
    receipt_id = 'sha256:' + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    report['receiptId'] = receipt_id
    try:
        out = os.path.abspath(path)
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        tmp = out + f'.tmp.{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
        return None
    except OSError as e:
        return f'{type(e).__name__}: {e}'


def main(argv=None):
    ap = argparse.ArgumentParser(description='sync verified completed BigGo bundle (read-only GET)')
    ap.add_argument('--dry-run', action='store_true', default=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--repair', default=os.environ.get('AIRCON_BIGGO_REPAIR_RECEIPT'))
    ap.add_argument('--report', default=None)
    ap.add_argument('--degraded-exit-zero', action='store_true',
                    help='missing history/stage gap is reported as degraded but does not block publish')
    args = ap.parse_args(argv)
    apply = bool(args.apply)
    report = {'schemaVersion': 2, 'mode': 'sync-completed',
              'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'ok': False, 'applied': False, 'reason': 'started', 'plan': None,
              'cycleId': None, 'stage': None}

    def finish(code, reason, **extra):
        report.update(extra)
        report['reason'] = reason
        report['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        err = _write_report(args.report, report)
        if err:
            print(f'❌ completed sync report 寫入失敗：{err}', file=sys.stderr)
            return 2
        return code

    try:
        cfg = coord.CoordinatorConfig.from_env()
    except coord.CoordinatorConfigError:
        return finish(2, 'coordinator-config-invalid')
    if cfg is None:
        print('❌ coordinator 未配置；零網絡、零寫入', file=sys.stderr)
        return finish(2, 'coordinator-not-configured')
    contents = coord.GitHubContentsClient(cfg)
    try:
        state, _sha = contents.read_state()
    except coord.CoordinatorError as e:
        print(f'❌ coordinator state 讀取失敗（零寫入）：{type(e).__name__}', file=sys.stderr)
        return finish(2, 'coordinator-read-error')
    if state is None:
        print('❌ coordinator state 缺失（零寫入）', file=sys.stderr)
        return finish(2, 'remote-state-missing')
    report['cycleId'] = state.get('cycleId')
    report['stage'] = state.get('stage')
    try:
        meta = batch_utils.load_meta(os.path.join(BASE, 'prices_meta.json'))
    except batch_utils.MetaError as e:
        print(f'❌ local prices_meta 唔合格：{e}', file=sys.stderr)
        return finish(1, 'local-meta-invalid')
    plan = plan_sync(meta, state)
    report['plan'] = plan
    if not plan['ok']:
        reason = plan['reason']
        print(f"sync skipped: {reason}（零 BigGo／零 lease／零 coordinator write）")
        if reason in ('missing-local-history', 'missing-intermediate-stage',
                      'cycle-mismatch', 'local-stage-invalid', 'stage-not-ahead'):
            return finish(0 if args.degraded_exit_zero else 1, reason)
        if reason in ('already-applied', 'remote-not-completed'):
            return finish(0, reason)
        return finish(2, reason)
    client = coord.CoordinatorClient(contents, owner='read-only-sync', now=lambda: 0)
    client.state = state
    try:
        bundle = client.import_bundle(state['cycleId'], state['stage'])
    except coord.LegacyBundleError:
        return finish(2, 'legacy-bundle-manual')
    except coord.CoordinatorError as e:
        print(f'❌ completed bundle 匯入失敗（零寫入）：{type(e).__name__}', file=sys.stderr)
        return finish(2, 'bundle-import-error')
    repair = None
    if args.repair:
        try:
            with open(args.repair, encoding='utf-8') as f:
                repair = json.load(f)
        except (OSError, ValueError) as e:
            print(f'❌ repair receipt 讀取失敗：{e}', file=sys.stderr)
            return finish(2, 'repair-invalid')
    report['bundleHash'] = bundle['bundleHash']
    if not apply:
        print(f"sync dry-run ready: bundle={bundle['bundleHash']} stage={state['stage']}")
        return finish(0, 'dry-run-ready', ok=True)
    try:
        result = biggo_apply.apply_bundle(bundle, repo_root=BASE, repair=repair)
    except biggo_apply.ApplyError as e:
        print(f'❌ local apply 失敗（零 coordinator write）：{e}', file=sys.stderr)
        return finish(3, 'apply-error', error=type(e).__name__)
    report['applied'] = True
    report['result'] = result
    print(f"sync applied: noop={result.get('noop')} bundle={bundle['bundleHash']}")
    return finish(0, 'applied', ok=True)


if __name__ == '__main__':
    sys.exit(main())
