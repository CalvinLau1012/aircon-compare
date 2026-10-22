#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r24 F01 真候選入口（ready-but-not-enabled）：月度價格 cycle 啟動／catch-up。

--plan                          只讀 rc0
--apply --enable                需要一致 mode：CYCLE_ENABLED＝SHARED_CYCLE＝1；否則 rc3
--coordinator                   真 CoordinatorAnchorStore＋CoordinatorClient reconcile
--anchor-file <path>            offline fixture（需同時 --repo-root 才能 reconcile）
--repo-root <dir>               reconcile apply 的本地 repo（prices_meta.json 等）
exit：0 started／adopted／noop；3 未啟用；2 blocked（meta／anchor／transition／reconcile）。
唔呼叫 provider；apply 只做 anchor CAS＋真 bundle import/apply＋本地 meta。
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import price_cycle  # noqa: E402
import price_runtime  # noqa: E402
import verify_price_source_pin  # noqa: E402

_OK_REASONS = {'started', 'adopted', 'adopted-stage0', 'anchor-synced', 'noop',
               'active-batch', 'month-completed', 'already-anchored'}


def _coordinator_client(env):
    import biggo_coordinator as coord
    status = coord.config_status(env)
    if not status['configured']:
        return None
    contents = coord.GitHubContentsClient(coord.CoordinatorConfig.from_env(env))
    owner = env.get('AIRCON_BIGGO_LEASE_OWNER') or 'monthly-entry'
    return coord.CoordinatorClient(contents, owner=owner)


def main(argv=None, store_factory=None, client_factory=None):
    ap = argparse.ArgumentParser(description='月度價格 cycle 候選入口')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--demand-file', default=None)
    ap.add_argument('--repo-root', default=None)
    ap.add_argument('--anchor-file', default=None)
    ap.add_argument('--coordinator', action='store_true')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--guard', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--enable', action='store_true')
    args = ap.parse_args(argv)
    env = os.environ
    try:
        mode = price_cycle.shared_mode(env)
        qmode = price_runtime.query_mode(env)
    except (price_cycle.AnchorError, price_runtime.PriceRuntimeError) as e:
        print(json.dumps({'mode': 'apply' if args.apply else 'plan', 'written': False,
                          'reason': 'mode-inconsistent', 'detail': str(e)}, ensure_ascii=False))
        return 2
    if qmode == 'receive-only':
        print(json.dumps({'mode': 'apply' if args.apply else 'plan',
                          'written': False, 'reason': 'skip-receive-only',
                          'ok': True}, ensure_ascii=False))
        return 0
    if qmode == 'normal' and mode != 'shared':
        print(json.dumps({'mode': 'apply' if args.apply else 'plan',
                          'written': False,
                          'reason': 'normal-requires-paired-shared-flags'},
                         ensure_ascii=False))
        return 2
    if qmode == 'fixture-local' and mode == 'shared':
        print(json.dumps({'mode': 'apply' if args.apply else 'plan',
                          'written': False,
                          'reason': 'fixture-local-cannot-use-shared'},
                         ensure_ascii=False))
        return 2
    if qmode == 'normal':
        repo_for_pin = args.repo_root or os.path.dirname(os.path.abspath(args.meta))
        try:
            verify_price_source_pin.verify_configured(repo_for_pin, env)
        except (ValueError, OSError) as e:
            print(json.dumps({'mode': 'apply' if args.apply else 'plan',
                              'written': False, 'reason': 'source-pin-failed',
                              'detail': str(e)[:200]}, ensure_ascii=False))
            return 2
    demand_path = args.demand_file or os.path.join(
        os.path.dirname(os.path.abspath(args.meta)), 'price_demand.json')
    demand_doc = None
    import price_demand
    try:
        demand_doc = (price_demand.load_required(demand_path)
                      if qmode in ('normal', 'receive-only')
                      else price_demand.load(demand_path))
    except price_demand.DemandError as e:
        print(json.dumps({'mode': 'apply' if args.apply else 'plan',
                          'written': False, 'reason': 'demand-missing-or-invalid',
                          'detail': str(e)[:200]}, ensure_ascii=False))
        return 2
    if args.anchor_file:
        store = price_cycle.FileAnchorStore(args.anchor_file)
    elif args.coordinator:
        try:
            store = (store_factory or price_cycle.CoordinatorAnchorStore.from_env)(env)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({'mode': 'apply' if args.apply else 'plan', 'written': False,
                              'reason': 'coordinator-store-unavailable',
                              'detail': type(e).__name__}, ensure_ascii=False))
            return 2
    else:
        store = None
    if args.guard:
        if mode != 'shared':
            print(json.dumps({'mode': 'guard', 'ok': True,
                              'detail': 'legacy-mode-no-monthly-guard'}, ensure_ascii=False))
            return 0
        if store is None:
            print(json.dumps({'mode': 'guard', 'ok': False,
                              'reason': 'shared-anchor-required'}, ensure_ascii=False))
            return 2
        try:
            anchor_doc, _sha, transition_doc = price_cycle._read_store(store)
        except price_cycle.AnchorError as e:
            print(json.dumps({'mode': 'guard', 'ok': False, 'reason': 'blocked-anchor',
                              'detail': str(e)}, ensure_ascii=False))
            return 2
        meta = json.load(open(args.meta, encoding='utf-8'))
        plan_state = price_cycle.plan(
            meta, anchor_doc, transition_doc,
            actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'),
            writer_role=env.get('AIRCON_PRICE_WRITER') or None)
        if demand_doc is not None and plan_state.get('reason') == 'monthly-due' \
                and not __import__('price_demand').pending(demand_doc):
            print(json.dumps({'mode': 'guard', 'ok': False,
                              'reason': 'no-demand'}, ensure_ascii=False))
            return 0
        allowed = {'monthly-due', 'already-anchored', 'active-batch', 'month-completed'}
        if plan_state.get('reason') not in allowed:
            print(json.dumps({'mode': 'guard', 'ok': False, 'plan': plan_state},
                             ensure_ascii=False, sort_keys=True))
            return 2
        repo_root = args.repo_root or os.path.dirname(os.path.abspath(args.meta))
        if not args.coordinator:
            print(json.dumps({'mode': 'guard', 'ok': False,
                              'reason': 'shared-coordinator-required'}, ensure_ascii=False))
            return 2
        client = (client_factory or _coordinator_client)(env)
        if client is None:
            print(json.dumps({'mode': 'guard', 'ok': False,
                              'reason': 'coordinator-not-configured'}, ensure_ascii=False))
            return 2
        try:
            remote = client.read_remote_state()
        except Exception as e:  # noqa: BLE001
            print(json.dumps({'mode': 'guard', 'ok': False, 'reason': 'remote-read-failed',
                              'detail': type(e).__name__}, ensure_ascii=False))
            return 2
        if anchor_doc is not None:
            verify = price_cycle._verify_local_material(
                meta, anchor_doc, client, repo_root, transition_doc)
        else:
            verify = None  # 首次 claim：transition 已綁 baseline，apply 先寫
        if remote is not None:
            status = remote.get('status')
            if status == 'needs_review':
                print(json.dumps({'mode': 'guard', 'ok': False, 'reason': 'remote-needs-review'},
                                 ensure_ascii=False))
                return 2
            if status == 'active' and (remote.get('callsMayHaveStarted')
                                       or remote.get('intentAt')):
                print(json.dumps({'mode': 'guard', 'ok': False, 'reason': 'remote-quarantine'},
                                 ensure_ascii=False))
                return 2
        if verify is not None:
            if remote and remote.get('status') == 'completed':
                try:
                    price_cycle.reconcile_shared(
                        meta, anchor_doc, transition_doc, store, client, repo_root,
                        apply_fn=None, now=None, meta_path=args.meta, dry_run=True,
                        writer_role=env.get('AIRCON_PRICE_WRITER') or None,
                        actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'))
                except Exception as e:  # noqa: BLE001
                    print(json.dumps({'mode': 'guard', 'ok': False,
                                      'reason': 'material-blocked',
                                      'detail': f'{verify}+dryrun:{e}'[:200]},
                                     ensure_ascii=False))
                    return 2
            else:
                print(json.dumps({'mode': 'guard', 'ok': False, 'reason': 'material-blocked',
                                  'detail': verify}, ensure_ascii=False))
                return 2
        elif anchor_doc is not None:
            # material 表面 valid／month-completed：仍必須先驗 pending journal（零寫）。
            try:
                price_cycle.inspect_pending_journal(
                    anchor_doc, transition_doc, store, client, repo_root,
                    writer_role=env.get('AIRCON_PRICE_WRITER') or None,
                    actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'))
            except Exception as e:  # noqa: BLE001
                print(json.dumps({'mode': 'guard', 'ok': False,
                                  'reason': 'material-blocked',
                                  'detail': f'pending-journal:{e}'[:200]},
                                 ensure_ascii=False))
                return 2
        print(json.dumps({'mode': 'guard', 'ok': True, 'plan': plan_state},
                         ensure_ascii=False, sort_keys=True))
        return 0
    if not args.apply:
        if store is None:
            return 2
        try:
            anchor_doc, _sha, transition_doc = price_cycle._read_store(store)
        except price_cycle.AnchorError as e:
            print(json.dumps({'mode': 'plan', 'blocked': str(e)}, ensure_ascii=False))
            return 2
        meta = json.load(open(args.meta, encoding='utf-8'))
        plan_state = price_cycle.plan(
            meta, anchor_doc, transition_doc,
            actual_source=env.get('AIRCON_PRICE_SOURCE_REVISION'),
            writer_role=env.get('AIRCON_PRICE_WRITER') or None)
        if demand_doc is not None and plan_state.get('reason') == 'monthly-due':
            import price_demand
            if not price_demand.pending(demand_doc):
                plan_state['demandGate'] = 'no-demand'
        print(json.dumps({'mode': 'plan', 'plan': plan_state},
                         ensure_ascii=False, sort_keys=True))
        return 0
    enabled = args.enable and mode == 'shared'
    if not enabled:
        print(json.dumps({'mode': 'apply', 'written': False,
                          'reason': 'ready-but-not-enabled'}, ensure_ascii=False))
        return 3
    client = None
    if args.coordinator:
        client = (client_factory or _coordinator_client)(env)
        if client is None:
            print(json.dumps({'mode': 'apply', 'written': False,
                              'reason': 'coordinator-not-configured'}, ensure_ascii=False))
            return 2
    repo_root = args.repo_root or os.path.dirname(os.path.abspath(args.meta))
    source_rev = env.get('AIRCON_PRICE_SOURCE_REVISION') or None
    initial_base = env.get('AIRCON_PRICE_INITIAL_BASE_HASH') or None
    if initial_base is None:
        try:
            import biggo_canonical
            snap = json.load(open(os.path.join(repo_root, 'biggo_prices.json'),
                                  encoding='utf-8'))
            initial_base = biggo_canonical.sha256_json(snap)
        except (OSError, ValueError):
            initial_base = None
    result = price_cycle.ensure_monthly_cycle(
        args.meta, allow_write=True, store=store, client=client, repo_root=repo_root,
        actual_source=source_rev, initial_base=initial_base,
        writer_role=env.get('AIRCON_PRICE_WRITER') or None,
        demand_doc=demand_doc, demand_path=demand_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    if result.get('written') or result.get('reason') in _OK_REASONS:
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
