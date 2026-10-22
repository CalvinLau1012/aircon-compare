#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily new-model demand -> monthly price-cycle entry.

This is the only normal daily entry that may *start* a price batch.  It does
not fetch, smoke, acquire a lease or call a provider.  The controlled stage
runner remains the only provider path.

Ordering contract (also wired into the private runner / public workflow):

  receive completed result -> EMSD -> official batch -> demand/cycle entry
  -> controlled BigGo runner -> consistent publication

The official queue is never advanced or cleared here, and a pending official
coverage gap never blocks this entry.

Modes:
  * both flags unset / both 0 -> legacy local meta start, still demand-gated
  * both flags 1             -> shared monthly-cycle-v2 + coordinator CAS
  * mixed flags              -> exit 2, fail closed
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (BASE, os.path.join(BASE, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import batch_utils  # noqa: E402
import price_cycle  # noqa: E402
import price_demand  # noqa: E402


def _now(value):
    if value is None:
        return _dt.datetime.now(_dt.timezone.utc)
    return _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def _write_anchor_cache(repo, anchor_doc, transition_doc=None, now=None):
    """Persist a sanitised read-only proof of the last coordinator anchor read.

    This is not authority: the receipt binds its hash and the next daily entry
    re-reads the real coordinator.  It lets the local 15-minute monitor verify
    the shared identity without network access.  Missing anchor is recorded as
    null (never a stale green proof).
    """
    doc = {
        'schemaVersion': 1,
        'kind': 'aircon-price-anchor-cache',
        'readAt': price_cycle.hkt_now(now).isoformat(timespec='seconds'),
        'anchor': anchor_doc if isinstance(anchor_doc, dict) else None,
        'transition': transition_doc if isinstance(transition_doc, dict) else None,
    }
    path = os.path.join(repo, 'price_anchor.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def _client(env):
    import biggo_coordinator as coord
    status = coord.config_status(env)
    if not status['configured']:
        return None
    contents = coord.GitHubContentsClient(coord.CoordinatorConfig.from_env(env))
    owner = env.get('AIRCON_BIGGO_LEASE_OWNER') or 'monthly-entry'
    return coord.CoordinatorClient(contents, owner=owner)


def run(*, repo, meta_path, demand_path, apply=False, now=None, env=None,
        store=None, client=None):
    env = env if env is not None else os.environ
    now_dt = _now(now)
    try:
        meta = batch_utils.load_meta(meta_path)
    except batch_utils.MetaError as e:
        return {'ok': False, 'action': 'blocked', 'reason': 'meta-invalid',
                'detail': str(e)[:200]}
    try:
        demand_doc = price_demand.load(demand_path)
    except price_demand.DemandError as e:
        return {'ok': False, 'action': 'blocked', 'reason': 'demand-invalid',
                'detail': str(e)[:200]}
    try:
        mode = price_cycle.shared_mode(env)
    except price_cycle.AnchorError as e:
        return {'ok': False, 'action': 'blocked', 'reason': 'mode-inconsistent',
                'detail': str(e)[:200]}
    pending = price_demand.pending(demand_doc)
    base = {'mode': mode, 'pendingDemand': len(pending),
            'month': price_cycle.cycle_month(now_dt),
            'demandHash': __import__('biggo_canonical').sha256_id(
                __import__('biggo_canonical').canonical_json_bytes(demand_doc))}
    if not pending:
        base.update({'ok': True, 'action': 'no-demand', 'reason': 'no-new-model'})
        return base
    if mode != 'shared':
        allowed, reason = batch_utils._demand_allows_start(meta, demand_doc, now_dt)
        base.update({'planReason': reason})
        if not allowed:
            base.update({'ok': True, 'action': reason, 'reason': reason})
            return base
        if not apply:
            base.update({'ok': True, 'action': 'waiting-eligible',
                         'reason': reason})
            return base
        try:
            started = batch_utils.start_price_batch(
                now=now_dt.timestamp(), meta_path=meta_path, demand_path=demand_path)
        except Exception as e:  # noqa: BLE001
            base.update({'ok': False, 'action': 'blocked', 'reason': 'start-failed',
                         'detail': f'{type(e).__name__}:{e}'[:200]})
            return base
        meta2 = batch_utils.load_meta(meta_path)
        base.update({'ok': True,
                     'action': 'started' if started else 'active-or-completed',
                     'cycle': price_cycle.cycle_identity(meta2),
                     'stage': meta2.get('price_batch_idx', 0)})
        return base
    # Shared mode: one HKT-month identity, CAS anchor, no localtime fallback.
    if store is None:
        try:
            store = price_cycle.CoordinatorAnchorStore.from_env(env)
        except Exception as e:  # noqa: BLE001
            base.update({'ok': False, 'action': 'blocked',
                         'reason': 'shared-anchor-required',
                         'detail': type(e).__name__})
            return base
    if client is None:
        client = _client(env)
    try:
        anchor_doc, _sha, transition_doc = price_cycle._read_store(store)
        try:
            _write_anchor_cache(repo, anchor_doc, transition_doc, now=now_dt)
        except OSError:
            base['anchorCache'] = 'write-failed'
    except price_cycle.AnchorError as e:
        base.update({'ok': False, 'action': 'blocked', 'reason': 'blocked-anchor',
                     'detail': str(e)[:200]})
        return base
    state = price_cycle.plan(
        meta, anchor_doc, transition_doc, now_dt,
        actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'),
        writer_role=env.get('AIRCON_PRICE_WRITER') or None)
    base['planReason'] = state.get('reason')
    if state.get('reason') == 'monthly-due' and not price_demand.pending(demand_doc):
        base.update({'ok': True, 'action': 'no-demand'})
        return base
    if not apply:
        base.update({'ok': state.get('reason') in (
            'monthly-due', 'already-anchored', 'active-batch', 'month-completed'),
            'action': 'waiting-eligible' if state.get('reason') == 'monthly-due'
                      else state.get('reason')})
        return base
    result = price_cycle.ensure_monthly_cycle(
        meta_path, now_dt, allow_write=True, store=store, client=client,
        repo_root=repo, actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'),
        initial_base=env.get('AIRCON_PRICE_INITIAL_BASE_HASH'),
        writer_role=env.get('AIRCON_PRICE_WRITER') or None,
        demand_doc=demand_doc, demand_path=demand_path)
    base.update({'ok': bool(result.get('written')) or result.get('reason') in (
                    'noop', 'active-batch', 'month-completed', 'already-anchored',
                    'no-demand'),
                 'action': result.get('reason'), 'written': bool(result.get('written')),
                 'cycle': result.get('cycle'), 'stage': result.get('stage')})
    if result.get('detail'):
        base['detail'] = str(result['detail'])[:200]
    return base


def main(argv=None):
    ap = argparse.ArgumentParser(description='new-model demand / monthly cycle entry')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--meta', default=None)
    ap.add_argument('--demand', default=None)
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--now', default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    result = run(repo=repo,
                 meta_path=args.meta or os.path.join(repo, 'prices_meta.json'),
                 demand_path=args.demand or os.path.join(repo, 'price_demand.json'),
                 apply=bool(args.apply and not args.plan), now=args.now)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get('ok') else 2


if __name__ == '__main__':
    sys.exit(main())
