#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only-first migration planner for the shared monthly price identity.

The current production state predates the shared protocol:

  * the 2026-10 October cycle is completed (7/7) in both local stores;
  * both ends serve the same immutable snapshot;
  * ``price_batch_start`` is absent because the last stage cleaned it;
  * the coordinator has ``biggo-state.json`` but no ``monthly-cycle.json``,
    ``monthly-transition.json`` or ``monthly-history/2026-10.json``.

This tool derives the exact transition/anchor/history facts from the real EMSD
new-model record, the immutable stage-7 manifest/result, and both local
baselines.  Default mode performs **zero writes**.  ``--apply`` only writes to
an explicitly supplied file-based fixture anchor store; it can never write the
real coordinator.  Root performs the reviewed coordinator CAS afterwards.

Insufficient evidence is reported as ``blocked``.  Nothing is guessed from
prices, the official queue or a generic "today" clock.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (BASE, os.path.join(BASE, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import price_cycle  # noqa: E402
import price_demand  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402

SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
SOURCE_RE = re.compile(r'^[0-9a-f]{40}$')


class MigrationError(RuntimeError):
    pass


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _snapshot_hash(path):
    return sha256_id(canonical_json_bytes(_load(path)))


def _meta_facts(meta, label, snapshot_hash, month, bundle_hash=None):
    if not isinstance(meta, dict):
        return [f'{label}:meta-missing']
    errors = []
    if meta.get('appliedSnapshotHash') != snapshot_hash:
        errors.append(f'{label}:applied-snapshot-mismatch')
    if bundle_hash and meta.get('appliedBundleHash') != bundle_hash:
        errors.append(f'{label}:applied-bundle-mismatch')
    if meta.get('last_applied_cycle') != month.get('cycle'):
        errors.append(f'{label}:applied-cycle-mismatch')
    if meta.get('last_price_month') != month.get('month'):
        errors.append(f'{label}:last-month-mismatch')
    if meta.get('price_batch_idx') != 7:
        errors.append(f'{label}:stage-not-7')
    if meta.get('price_batch_start'):
        # A completed cycle should have cleaned this key.  If it is present the
        # caller may be looking at an active/ambiguous state, not this plan.
        errors.append(f'{label}:unexpected-price-batch-start')
    return errors


def _validate_stage_result(result, manifest, snapshot):
    """Structural/hash checks that do not require the stage-6 base bytes."""
    errors = []
    if not isinstance(result, dict):
        return ['stage-result-missing']
    for field in ('schemaVersion', 'cycleId', 'stage', 'mode', 'generatedAt',
                  'counters', 'requestAttempts', 'responseStatusCounts',
                  'outcomes', 'blacklistReview', 'effects', 'preState',
                  'postState', 'effectsHash'):
        if field not in result:
            errors.append(f'stage-result-missing:{field}')
    if errors:
        return errors
    if result.get('schemaVersion') != 1:
        errors.append('stage-result-schema')
    if result.get('cycleId') != manifest.get('cycleId') \
            or result.get('stage') != manifest.get('stage'):
        errors.append('stage-result-cycle/stage')
    if result.get('mode') != manifest.get('mode'):
        errors.append('stage-result-mode')
    if sha256_id(canonical_json_bytes(result)) != manifest.get('stageResultHash'):
        errors.append('stage-result-hash-mismatch')
    if sha256_id(canonical_json_bytes(result.get('effects'))) != result.get('effectsHash'):
        errors.append('effects-hash-mismatch')
    counters = result.get('counters')
    if not isinstance(counters, dict) or set(counters) != {'got', 'cleanMiss', 'netErrors'}:
        errors.append('counters-invalid')
    outcomes = result.get('outcomes')
    if not isinstance(outcomes, list):
        errors.append('outcomes-invalid')
        return errors
    got = clean = net = 0
    seen = set()
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            errors.append('outcome-invalid')
            continue
        model = outcome.get('model')
        kind = outcome.get('outcome')
        if not isinstance(model, str) or not model or model in seen:
            errors.append('outcome-model-invalid')
            continue
        seen.add(model)
        if kind == 'priced':
            got += 1
            if outcome.get('price') != snapshot.get(model):
                errors.append(f'priced-not-in-snapshot:{model}')
        elif kind == 'clean_miss':
            clean += 1
        elif kind == 'net_error':
            net += 1
        else:
            errors.append('outcome-kind-invalid')
    if isinstance(counters, dict) and (
            counters.get('got') != got or counters.get('cleanMiss') != clean
            or counters.get('netErrors') != net):
        errors.append('counters-mismatch')
    return errors


def plan(*, repo, prices_meta, snapshot, new_models, stage_manifest,
         stage_result, public_meta, private_meta, source_public,
         source_private, at=None, anchor_file=None, history_file=None,
         transition_file=None):
    if isinstance(at, str):
        at = _dt.datetime.fromisoformat(at.replace('Z', '+00:00'))
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=_dt.timezone.utc)
    now = at or _dt.datetime.now(_dt.timezone.utc)
    hkt = now.astimezone(_dt.timezone(_dt.timedelta(hours=8)))
    errors = []
    missing_refs = []
    try:
        meta = _load(prices_meta)
        snap = _load(snapshot)
        manifest = _load(stage_manifest)
        result = _load(stage_result)
        new_models_doc = _load(new_models)
    except (OSError, ValueError) as e:
        return {'ok': False, 'mode': 'blocked', 'blockedReasons': [
            f'input-unreadable:{type(e).__name__}'], 'missingRefs': []}
    snapshot_hash = sha256_id(canonical_json_bytes(snap))
    if manifest.get('newSnapshotHash') != snapshot_hash \
            or manifest.get('snapshotHash') != snapshot_hash:
        errors.append('manifest-snapshot-mismatch')
    if manifest.get('schemaVersion') != 2:
        errors.append('manifest-schema')
    cycle = manifest.get('cycleId')
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2}):7/7', str(cycle or ''))
    if not m:
        errors.append('manifest-cycle-not-completed')
        cycle_month = None
        started = None
    else:
        started = m.group(1)
        cycle_month = started[:7]
    if manifest.get('stage') != 7:
        errors.append('manifest-stage-not-7')
    if manifest.get('recordCount') != len(snap):
        errors.append('manifest-record-count-mismatch')
    if not (isinstance(manifest.get('baseSnapshotHash'), str)
            and SHA_RE.match(manifest['baseSnapshotHash'])):
        errors.append('manifest-base-hash-invalid')
    bundle_hash = sha256_id(canonical_json_bytes(manifest))
    errors.extend(_validate_stage_result(result, manifest, snap))
    errors.extend(_meta_facts(_load(prices_meta) if prices_meta else {}, 'local',
                              snapshot_hash,
                              {'cycle': cycle, 'month': cycle_month},
                              bundle_hash=bundle_hash))
    for label, path in (('public', public_meta), ('private', private_meta)):
        if not path:
            errors.append(f'{label}:meta-not-supplied')
            continue
        try:
            other = _load(path)
        except (OSError, ValueError):
            errors.append(f'{label}:meta-unreadable')
            continue
        errors.extend(_meta_facts(other, label, snapshot_hash,
                                  {'cycle': cycle, 'month': cycle_month},
                                  bundle_hash=bundle_hash))
    if not SOURCE_RE.match(str(source_public or '')):
        errors.append('public:source-revision-invalid')
    if not SOURCE_RE.match(str(source_private or '')):
        errors.append('private:source-revision-invalid')
    if not cycle_month or started is None:
        errors.append('cycle-month-unavailable')
        transition_bytes = anchor_bytes = history_bytes = None
        transition = anchor = history = None
    elif not SOURCE_RE.match(str(source_public or '')) \
            or not SOURCE_RE.match(str(source_private or '')):
        # Do not call the protocol helper with invalid revisions; the errors
        # above stay authoritative and the plan is blocked.
        transition_bytes = anchor_bytes = history_bytes = None
        transition = anchor = history = None
    else:
        transition = json.loads(price_cycle.transition_ack_bytes(
            source_public, source_private, snapshot_hash, snapshot_hash,
            at=hkt.strftime('%Y-%m-%d')).decode('utf-8'))
        anchor = {
            'formatVersion': price_cycle.SCHEMA_VERSION,
            'protocol': price_cycle.PROTOCOL,
            'month': cycle_month,
            'cycle': cycle,
            'startedAt': started,
            'stage': 7,
            'status': 'completed',
            'lastBundleHash': bundle_hash,
            'initialBaseHash': snapshot_hash,
            'updatedAt': hkt.isoformat(timespec='seconds'),
        }
        a_errs = price_cycle.validate_anchor(anchor)
        if a_errs:
            errors.extend('anchor:' + e for e in a_errs)
        history = dict(anchor)
        history['historyVersion'] = 1
        history['finalSnapshotHash'] = snapshot_hash
        transition_bytes = canonical_json_bytes(transition)
        anchor_bytes = canonical_json_bytes(anchor)
        history_bytes = canonical_json_bytes(history)
    # Demand evidence: derive only from the immutable stage-7 outcomes and the
    # real EMSD new-model file.  Earlier stages are not guessed.
    demand_doc = None
    demand_text = None
    if not errors:
        try:
            demand_doc = price_demand.merge_new_models(
                price_demand.empty(now), new_models_doc, now=now)
            demand_doc = price_demand.record_outcomes(
                demand_doc, result, manifest, now=now)
            demand_text = json.dumps(demand_doc, ensure_ascii=False,
                                     sort_keys=True, indent=2) + '\n'
        except (price_demand.DemandError, ValueError) as e:
            errors.append('demand-invalid:' + type(e).__name__)
    for stage in range(1, 7):
        missing_refs.append(f'snapshots/2026-10-10-{stage}-7/stage-result.json')
    missing_refs.append('snapshots/2026-10-10-7-7/base-biggo_prices.json')
    missing_refs = [r for r in missing_refs if not _local_ref_exists(repo, r)]
    receipt = {
        'schemaVersion': 1,
        'kind': 'aircon-price-cycle-migration-receipt',
        'mode': 'apply' if anchor_file else 'preview',
        'ok': not errors,
        'ready': not errors,
        'blockedReasons': errors,
        'generatedAt': hkt.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'cycle': cycle,
        'month': cycle_month,
        'startedAt': started,
        'stage': 7,
        'bundleHash': bundle_hash,
        'baseSnapshotHash': manifest.get('baseSnapshotHash'),
        'newSnapshotHash': snapshot_hash,
        'initialBaseline': {'snapshotHash': snapshot_hash, 'baseHash': snapshot_hash},
        'sourceRevision': {'public': source_public, 'private': source_private},
        'anchor': anchor,
        'anchorHash': sha256_id(anchor_bytes) if anchor_bytes else None,
        'transition': transition,
        'transitionHash': sha256_id(transition_bytes) if transition_bytes else None,
        'history': history,
        'historyHash': sha256_id(history_bytes) if history_bytes else None,
        'demand': demand_doc,
        'demandHash': sha256_id(demand_text.encode('utf-8')) if demand_text else None,
        'missingRefs': missing_refs,
        'notes': [
            'Completed-cycle anchor uses the current served snapshot as the '
            'proven transition lineage; cycleBaseHash is not invented.',
            'Stage-7 base bytes and earlier stage results are optional for the '
            'completed anchor but listed when absent for audit completeness.',
            'No provider, coordinator or production write is performed here.',
        ],
    }
    if errors:
        receipt['ok'] = False
        receipt['ready'] = False
        return receipt

    target_anchor = anchor_file or os.path.join(repo, 'fixture-monthly-cycle.json')
    target_transition = transition_file or (target_anchor + '.transition')
    target_history = history_file or (target_anchor + f'.history-{cycle_month}.json')
    if anchor_file and _semantic_noop(target_anchor, target_transition,
                                      target_history, anchor, transition, history):
        pre = _prestate(target_anchor, target_transition, target_history,
                        _read_bytes(target_anchor), _read_bytes(target_transition),
                        _read_bytes(target_history))
        receipt['prestate'] = pre
        receipt['expectedAnchorSha'] = pre['anchorSha']
        receipt['expectedTransitionSha'] = pre['transitionSha']
        receipt['mode'] = 'no-op'
        receipt['written'] = False
        receipt['ok'] = True
        receipt['ready'] = True
        return receipt
    pre = _prestate(target_anchor, target_transition, target_history,
                    anchor_bytes, transition_bytes, history_bytes)
    receipt['prestate'] = pre
    if pre['conflict']:
        receipt['ok'] = False
        receipt['ready'] = False
        receipt['blockedReasons'] = pre['conflict']
        return receipt
    receipt['expectedAnchorSha'] = pre['anchorSha']
    receipt['expectedTransitionSha'] = pre['transitionSha']
    if not anchor_file:
        receipt['applyPlan'] = {
            'targets': {'anchor': 'coordinator/monthly-cycle.json',
                        'transition': 'coordinator/monthly-transition.json',
                        'history': f'coordinator/monthly-history/{cycle_month}.json'},
            'expectedCoordinates': {
                'anchorSha': pre['anchorSha'],
                'transitionSha': pre['transitionSha'],
                'historyAbsent': pre['historySha'] is None,
            },
            'rootAction': 'supply final source revisions, review hashes, then CAS '
                          'transition -> anchor -> history at the coordinator',
        }
        return receipt
    if not pre['allAbsentOrSame']:
        receipt['ok'] = False
        receipt['ready'] = False
        receipt['blockedReasons'] = ['prestate-conflict']
        return receipt
    if all(pre[k] is not None for k in
           ('anchorSha', 'transitionSha', 'historySha')):
        # Every target already carries the exact planned bytes: a repeat apply
        # is a true no-op (no timestamp refresh, no rewrite).
        receipt['mode'] = 'no-op'
        receipt['written'] = False
        receipt['ok'] = True
        receipt['ready'] = True
        return receipt
    demand_path = os.path.join(repo, 'price_demand.json')
    existing_demand = None
    if os.path.exists(demand_path):
        try:
            existing_demand = price_demand.load(demand_path)
        except price_demand.DemandError:
            receipt['ok'] = False
            receipt['ready'] = False
            receipt['blockedReasons'] = ['demand-invalid-prestate']
            return receipt
        # Strong prestate: never overwrite a demand file that contains facts
        # different from this derived migration (including newer processed
        # evidence).  Same facts may keep their original updatedAt.
        if existing_demand.get('demands') != demand_doc.get('demands'):
            receipt['ok'] = False
            receipt['ready'] = False
            receipt['blockedReasons'] = ['demand:prestate-conflict']
            return receipt
    try:
        # Transition first: the claim is only meaningful when both writers have
        # acknowledged the old entry has stopped.
        if pre['transitionSha'] is None:
            with open(target_transition, 'wb') as f:
                f.write(transition_bytes)
                f.flush()
                os.fsync(f.fileno())
        store = price_cycle.FileAnchorStore(target_anchor, target_transition)
        if pre['anchorSha'] is None:
            claimed = store.claim_raw(anchor_bytes, None)
        else:
            claimed = pre['anchorSha']
        if history_bytes is not None and pre['historySha'] is None:
            # Explicit fixture path is authoritative; FileAnchorStore's helper
            # derives its own path, which is not what Root will CAS.
            with open(target_history, 'wb') as f:
                f.write(history_bytes)
                f.flush()
                os.fsync(f.fileno())
        if demand_text is not None and existing_demand is None:
            price_demand.update(demand_path, lambda doc: demand_doc, now)
        receipt['written'] = {
            'anchor': claimed,
            'transition': pre['transitionSha'] or sha256_id(transition_bytes),
            'history': sha256_id(history_bytes),
            'demand': receipt['demandHash'],
        }
        receipt['mode'] = 'applied'
    except (OSError, price_cycle.AnchorError) as e:
        receipt['ok'] = False
        receipt['ready'] = False
        receipt['blockedReasons'] = [f'apply-failed:{type(e).__name__}:{e}'[:300]]
    return receipt


def _local_ref_exists(repo, rel):
    if not repo:
        return False
    return os.path.isfile(os.path.join(repo, *rel.split('/')))


def _read_bytes(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _semantic_noop(anchor_path, transition_path, history_path,
                    anchor, transition, history):
    """True when all three existing docs are the same migration facts.

    Only audit/observation time fields are ignored (``updatedAt`` and the
    transition's ``at`` date); all cycle/stage/hash/source facts must match.
    """
    if anchor is None or transition is None or history is None:
        return False
    paths = (anchor_path, transition_path, history_path)
    if not all(os.path.isfile(p) for p in paths):
        return False
    try:
        old_anchor = _load(anchor_path)
        old_transition = _load(transition_path)
        old_history = _load(history_path)
    except (OSError, ValueError):
        return False

    def strip(doc, *fields):
        if not isinstance(doc, dict):
            return None
        return {k: v for k, v in doc.items() if k not in fields}
    return (strip(old_anchor, 'updatedAt') == strip(anchor, 'updatedAt')
            and strip(old_transition, 'at') == strip(transition, 'at')
            and strip(old_history, 'updatedAt') == strip(history, 'updatedAt'))


def _sha(data):
    return None if data is None else 'sha256:' + hashlib.sha256(data).hexdigest()


def _prestate(anchor_path, transition_path, history_path, anchor_bytes,
              transition_bytes, history_bytes):
    current = {}
    conflict = []
    for label, path, target in (
            ('anchor', anchor_path, anchor_bytes),
            ('transition', transition_path, transition_bytes),
            ('history', history_path, history_bytes)):
        data = _read_bytes(path)
        current[label + 'Sha'] = _sha(data)
        if data is not None and data != target:
            conflict.append(f'{label}:prestate-conflict')
    current['conflict'] = conflict
    current['allAbsentOrSame'] = not conflict
    return current


def main(argv=None):
    ap = argparse.ArgumentParser(description='plan/apply shared monthly cycle migration')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--prices-meta', default=None)
    ap.add_argument('--snapshot', default=None)
    ap.add_argument('--new-models', default=None)
    ap.add_argument('--stage-manifest', required=True)
    ap.add_argument('--stage-result', required=True)
    ap.add_argument('--public-meta', required=True)
    ap.add_argument('--private-meta', required=True)
    ap.add_argument('--source-public', required=True)
    ap.add_argument('--source-private', required=True)
    ap.add_argument('--at', default=None)
    ap.add_argument('--anchor-file', default=None)
    ap.add_argument('--transition-file', default=None)
    ap.add_argument('--history-file', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    at = None
    if args.at:
        try:
            at = _dt.datetime.fromisoformat(args.at.replace('Z', '+00:00'))
        except ValueError:
            print('❌ invalid --at', file=sys.stderr)
            return 2
    if args.apply and not args.anchor_file:
        print('❌ --apply requires --anchor-file (fixture only; no coordinator write)',
              file=sys.stderr)
        return 2
    receipt = plan(
        repo=repo,
        prices_meta=args.prices_meta or os.path.join(repo, 'prices_meta.json'),
        snapshot=args.snapshot or os.path.join(repo, 'biggo_prices.json'),
        new_models=args.new_models or os.path.join(repo, 'new_models.json'),
        stage_manifest=args.stage_manifest,
        stage_result=args.stage_result,
        public_meta=args.public_meta,
        private_meta=args.private_meta,
        source_public=args.source_public,
        source_private=args.source_private,
        at=at, anchor_file=args.anchor_file if args.apply else None,
        transition_file=args.transition_file,
        history_file=args.history_file)
    text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
        tmp = args.out + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.out)
    print(text, end='')
    return 0 if receipt.get('ok') else 2


if __name__ == '__main__':
    sys.exit(main())
