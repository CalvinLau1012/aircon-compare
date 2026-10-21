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
import biggo_acquisition  # noqa: E402
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
    if stage <= local_idx:
        # A completed bundle at or behind the local stage is not a new
        # transition.  The same-cycle already-applied case was handled above;
        # every other equal/older stage must fail closed, including a remote
        # stage3 after local stage4.
        return {'ok': False, 'reason': 'stage-not-ahead', 'stage': stage,
                'localStage': local_idx}
    return {'ok': True, 'reason': 'ready', 'stage': stage}


def _bundle_error_code(error):
    text = str(error).lower()
    if 'manifest' in text and ('冇' in text or 'missing' in text or '缺少' in text):
        return 'manifest-missing'
    if 'hash' in text:
        return 'hash-mismatch'
    if 'cycle' in text or 'stage' in text:
        return 'cycle-stage-mismatch'
    if 'state.' in text or 'state binding' in text or 'state.snapshot' in text:
        return 'state-binding-mismatch'
    if 'json' in text or 'canonical' in text:
        return 'invalid-json-or-canonical'
    if 'schema' in text:
        return 'schema-mismatch'
    if '缺少' in text or 'missing' in text:
        return 'missing-file'
    return 'bundle-import-error'


def _import_flags(client, cycle, stage):
    helper = getattr(client, '_import_paths', None)
    if not callable(helper):
        return None
    try:
        _paths, flags = helper(cycle, stage)
        return flags
    except Exception:
        return None


def _read_obj(path, label):
    try:
        with open(path, encoding='utf-8') as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise SyncError(f'{label} 讀取／解析失敗') from e
    if not isinstance(value, dict):
        raise SyncError(f'{label} 必須係 object')
    return value


def _local_post(root):
    price = _read_obj(os.path.join(root, 'biggo_prices.json'), 'biggo_prices.json')
    tracking = _read_obj(os.path.join(root, 'model_status.json'), 'model_status.json')
    wrapper = _read_obj(os.path.join(root, 'model_blacklist.json'), 'model_blacklist.json')
    models = wrapper.get('models')
    if not isinstance(models, dict):
        raise SyncError('model_blacklist.json 缺 models object')
    return {
        'biggo_prices.json': biggo_apply._canonical_hash(price),
        'model_status.json': biggo_apply._canonical_hash(tracking),
        'model_blacklist.json': biggo_apply._canonical_hash(models),
    }


def _acceptance_valid(baseline):
    if not isinstance(baseline, dict)             or baseline.get('kind') != 'accepted-shared-baseline-v1':
        return False
    source = baseline.get('source')
    if not isinstance(source, dict):
        return False
    aid = baseline.get('acceptanceId')
    if not isinstance(aid, str) or not aid.startswith('sha256:'):
        return False
    body = {k: v for k, v in baseline.items() if k != 'acceptanceId'}
    want = 'sha256:' + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    active = baseline.get('activePost')
    if not isinstance(active, dict):
        return False
    return (aid == want
            and isinstance(source.get('cycleId'), str) and bool(source.get('cycleId'))
            and isinstance(source.get('stage'), int) and not isinstance(source.get('stage'), bool)
            and isinstance(source.get('bundleHash'), str)
            and isinstance(source.get('newSnapshotHash'), str)
            and isinstance(source.get('acquiredAt'), str)
            and source.get('acquiredAt', '').endswith('Z')
            and active.get('biggo_prices.json') == source.get('newSnapshotHash')
            and isinstance(active.get('model_status.json'), str)
            and isinstance(active.get('model_blacklist.json'), str))


def _baseline_source(meta):
    """Return a currently-adopted baseline source identity, without assuming
    that an eventual stage4 is complete.  Used only for a read-only idle-state
    fallback; full validation happens against the imported stage3 bundle."""
    baseline = meta.get('baselineAcceptance')
    if not isinstance(baseline, dict) \
            or baseline.get('kind') != 'accepted-shared-baseline-v1':
        return None
    source = baseline.get('source')
    if not isinstance(source, dict):
        return None
    if (meta.get('appliedBundleHash') != source.get('bundleHash')
            or meta.get('last_applied_cycle') != source.get('cycleId')
            or meta.get('price_batch_idx') != source.get('stage')):
        return None
    if not str(meta.get('appliedSnapshotId') or '').startswith('biggo-baseline-'):
        return None
    return source


def _baseline_current(meta, bundle):
    baseline = meta.get('baselineAcceptance')
    if not isinstance(baseline, dict)             or baseline.get('kind') != 'accepted-shared-baseline-v1':
        return None
    source = baseline.get('source') if isinstance(baseline.get('source'), dict) else {}
    manifest = bundle['manifest']
    current = (meta.get('appliedBundleHash') == source.get('bundleHash')
               and meta.get('last_applied_cycle') == source.get('cycleId')
               and meta.get('price_batch_idx') == source.get('stage'))
    if str(meta.get('appliedSnapshotId') or '').startswith('biggo-baseline-'):
        current = True
    if not current:
        return None
    if not _acceptance_valid(baseline):
        raise SyncError('baseline acceptance receipt/identity invalid')
    if (source.get('bundleHash') != bundle['bundleHash']
            or source.get('cycleId') != manifest.get('cycleId')
            or source.get('stage') != manifest.get('stage')
            or source.get('newSnapshotHash') != bundle['newSnapshotHash']):
        raise SyncError('baseline acceptance does not bind the current bundle')
    src_sr = source.get('stageResultHash')
    if src_sr is not None and src_sr != bundle['stageResultHash']:
        raise SyncError('baseline stageResultHash does not bind current bundle')
    if source.get('acquisitionProvenance') == 'recorded' \
            and src_sr != bundle['stageResultHash']:
        raise SyncError('recorded baseline missing exact stageResultHash')
    try:
        biggo_acquisition.baseline_acquisition(source, bundle['stageResult'])
    except biggo_acquisition.AcquisitionError as exc:
        raise SyncError(f'baseline acquisition witness invalid: {exc}') from exc
    expected_id = ('biggo-baseline-' + str(source['cycleId']) + '-stage'
                   + str(source['stage']) + '-' + str(source['newSnapshotHash'])[-12:])
    if meta.get('appliedSnapshotId') != expected_id:
        raise SyncError('baseline appliedSnapshotId invalid')
    return baseline


def verify_local_post(bundle, meta, root, repair=None):
    """Verify a local no-op/post state against the immutable shared bundle.

    This is deliberately a hash comparison against the already-verified
    bundle.postState/snapshot plus meta identity.  It does not replay the old
    buggy stage3 effects and never writes local or coordinator state.
    """
    try:
        biggo_apply._validate_bundle(bundle)
        stage_result, _repair_meta = biggo_apply.merge_repair_effects(bundle, repair)
    except (biggo_apply.ApplyError, ValueError) as e:
        raise SyncError(f'bundle/repair invalid: {e}') from e
    manifest = bundle['manifest']
    actual = _local_post(root)
    expected = {
        'biggo_prices.json': bundle['newSnapshotHash'],
        'model_status.json': stage_result['postState']['trackingHash'],
        'model_blacklist.json': stage_result['postState']['blacklistHash'],
    }
    if actual != expected:
        raise SyncError('local post-image does not match verified bundle postState')
    identity = {
        'appliedBundleHash': bundle['bundleHash'],
        'last_applied_cycle': manifest.get('cycleId'),
        'price_batch_idx': manifest.get('stage'),
        'appliedSnapshotHash': bundle['newSnapshotHash'],
        'appliedSnapshotId': ('biggo-' + str(manifest.get('cycleId')) + '-stage'
                              + str(manifest.get('stage')) + '-'
                              + str(bundle['newSnapshotHash'])[-12:]),
    }
    for key, want in identity.items():
        if key == 'appliedSnapshotId':
            # Baseline deliberately uses its own explicit id; normal stages do
            # not.  The baseline branch below validates its own id.
            continue
        if meta.get(key) != want:
            raise SyncError(f'prices_meta {key} does not match verified bundle')
    baseline = _baseline_current(meta, bundle)
    if baseline is not None:
        source = baseline['source']
        active = baseline['activePost']
        if (meta.get('appliedSnapshotHash') != source.get('newSnapshotHash')
                or meta.get('last_applied_cycle') != source.get('cycleId')
                or meta.get('price_batch_idx') != source.get('stage')):
            raise SyncError('baseline meta identity mismatch')
        for name in ('model_status.json', 'model_blacklist.json'):
            if active.get(name) != actual[name]:
                raise SyncError(f'baseline activePost mismatch: {name}')
        if active.get('biggo_prices.json') != actual['biggo_prices.json']:
            raise SyncError('baseline activePost mismatch: biggo_prices.json')
        baseline_id = baseline.get('acceptanceId')
    else:
        if meta.get('appliedSnapshotId') != identity['appliedSnapshotId']:
            raise SyncError('prices_meta appliedSnapshotId does not match verified bundle')
        baseline_id = None
    marker = biggo_apply.recovery_time_evidence(bundle)
    # A verified witness lives in the immutable stage-result.  Publication time
    # is never an acquisition time; a legacy bundle without a witness stays
    # unknown, and a baseline without explicit recorded provenance stays
    # unknown too (its historical manifest publication is not evidence).
    if baseline is not None:
        try:
            baseline_acq = biggo_acquisition.baseline_acquisition(
                baseline['source'], bundle['stageResult'])
        except biggo_acquisition.AcquisitionError as exc:
            raise SyncError(f'baseline acquisition witness invalid: {exc}') from exc
        acquired_at = baseline_acq['acquiredAt']
        provenance = baseline_acq['provenance']
        acquisition_hash = baseline_acq['blockHash']
    else:
        try:
            witness = biggo_acquisition.from_stage_result(
                bundle['stageResult'], now=time.time())
        except biggo_acquisition.AcquisitionError as exc:
            raise SyncError(f'acquisition witness invalid: {exc}') from exc
        acquired_at = witness['acquiredAt']
        provenance = witness['provenance']
        acquisition_hash = witness['blockHash']
    result = {
        'biggo_prices.json': actual['biggo_prices.json'],
        'model_status.json': actual['model_status.json'],
        'model_blacklist.json': actual['model_blacklist.json'],
        'bundleHash': bundle['bundleHash'],
        'stageResultHash': bundle['stageResultHash'],
        'cycleId': manifest.get('cycleId'),
        'stage': manifest.get('stage'),
        'snapshotHash': bundle['newSnapshotHash'],
        'acquiredAt': acquired_at,
        'acquiredAtProvenance': provenance,
        'acquisitionHash': acquisition_hash,
        'baselineAcceptanceId': baseline_id,
        'repairId': (repair or {}).get('repairId'),
    }
    if marker is not None:
        result.update({
            'acquiredAt': None,
            'acquiredAtProvenance': 'unknown',
            'acquisitionHash': None,
            'recoveryTimeEvidenceHash': biggo_apply._canonical_hash(marker),
            'sourceCompletionBound': marker['sourceCompletionBound'],
            'logWindowStart': marker['logWindowStart'],
            'sourceRunId': marker['sourceRunId'],
            'sourceHead': marker['sourceHead'],
            'artifactSha256': marker['artifactSha256'],
            'recoveryTimeEvidence': dict(marker),
        })
    return result


def _attach_receiver_facts(report, verified):
    facts = biggo_acquisition.canonical_receiver_facts(
        {'verifiedPost': verified,
         'acquiredAt': verified.get('acquiredAt'),
         'acquiredAtProvenance': verified.get('acquiredAtProvenance'),
         'acquisitionHash': verified.get('acquisitionHash')})
    report.update(facts)
    return report


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
        if report.get('ok') is True and report.get('postStateVerified') is True:
            report['postFileHashes'] = {}
            for name in ('biggo_prices.json', 'model_status.json',
                         'model_blacklist.json', 'prices_meta.json'):
                with open(os.path.join(BASE, name), 'rb') as stream:
                    report['postFileHashes'][name] = 'sha256:' + hashlib.sha256(stream.read()).hexdigest()
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
    noop_candidate = plan.get('reason') == 'already-applied'
    if not plan['ok'] and not noop_candidate:
        reason = plan['reason']
        # A completed baseline remains the last verified local post even if a
        # later stage is merely idle (for example a repair-release run that
        # skipped the provider step).  Verify the actual accepted stage3 bundle
        # read-only; never claim the idle stage4 was completed.
        baseline_source = _baseline_source(meta)
        if reason == 'remote-not-completed' and state.get('status') == 'idle' \
                and baseline_source is not None:
            try:
                client = coord.CoordinatorClient(contents, owner='read-only-sync',
                                                 now=lambda: 0)
                client.state = None
                bundle = client.import_bundle(baseline_source['cycleId'],
                                              baseline_source['stage'])
            except coord.LegacyBundleError:
                return finish(2, 'legacy-bundle-manual')
            except coord.CoordinatorError as e:
                print(f'❌ baseline idle fallback bundle 匯入失敗（零寫入）：{type(e).__name__}',
                      file=sys.stderr)
                return finish(2, 'bundle-import-error')
            repair_fallback = None
            if args.repair:
                try:
                    with open(args.repair, encoding='utf-8') as f:
                        candidate_repair = json.load(f)
                except (OSError, ValueError):
                    return finish(2, 'repair-invalid')
                if candidate_repair.get('originalBundleHash') == bundle['bundleHash'] \
                        and candidate_repair.get('originalStageResultHash') == bundle['stageResultHash']:
                    repair_fallback = candidate_repair
                else:
                    return finish(2, 'repair-invalid')
            try:
                verified = verify_local_post(bundle, meta, BASE, repair=repair_fallback)
            except (SyncError, biggo_apply.ApplyError) as e:
                print(f'❌ baseline idle fallback integrity failure（零寫入）：{e}',
                      file=sys.stderr)
                return finish(3, 'local-integrity-error', ok=False,
                              postStateVerified=False, error=type(e).__name__)
            report['remoteState'] = {'status': state.get('status'),
                                     'cycleId': state.get('cycleId'),
                                     'stage': state.get('stage')}
            report['baselineFallback'] = 'idle-remote; verified accepted stage3'
            report['cycleId'] = baseline_source['cycleId']
            report['stage'] = baseline_source['stage']
            report['bundleHash'] = bundle['bundleHash']
            report['stageResultHash'] = bundle['stageResultHash']
            report['newSnapshotHash'] = bundle['newSnapshotHash']
            report.update({'postStateVerified': True, 'verifiedPost': verified,
                           'applied': False, 'noop': True})
            _attach_receiver_facts(report, verified)
            print(f"sync baseline idle fallback verified: bundle={bundle['bundleHash']}")
            return finish(0, 'already-applied', ok=True)
        print(f"sync skipped: {reason}（零 BigGo／零 lease／零 coordinator write）")
        # A local meta that claims an applied bundle while the repository no
        # longer coherently matches that stage is corruption, not a benign
        # missing-history degradation.  It must block daily even with
        # --degraded-exit-zero.  A truly fresh/inactive repo keeps the legacy
        # degraded behaviour below.
        if meta.get('appliedBundleHash') and reason in (
                'stage-not-ahead', 'cycle-mismatch', 'local-stage-invalid'):
            return finish(3, 'local-integrity-error', ok=False,
                          postStateVerified=False, error=reason)
        if reason in ('missing-local-history', 'missing-intermediate-stage',
                      'cycle-mismatch', 'local-stage-invalid', 'stage-not-ahead'):
            return finish(0 if args.degraded_exit_zero else 1, reason)
        if reason in ('remote-not-completed',):
            return finish(0, reason)
        return finish(2, reason)
    client = coord.CoordinatorClient(contents, owner='read-only-sync', now=lambda: 0)
    client.state = state
    report['bundleImport'] = _import_flags(client, state['cycleId'], state['stage'])
    try:
        bundle = client.import_bundle(state['cycleId'], state['stage'])
    except coord.LegacyBundleError:
        return finish(2, 'legacy-bundle-manual')
    except coord.CoordinatorError as e:
        print(f'❌ completed bundle 匯入失敗（零寫入）：{type(e).__name__}', file=sys.stderr)
        return finish(2, 'bundle-import-error',
                      bundleErrorCode=_bundle_error_code(e))
    repair = None
    if args.repair:
        try:
            with open(args.repair, encoding='utf-8') as f:
                repair = json.load(f)
        except (OSError, ValueError) as e:
            print(f'❌ repair receipt 讀取失敗：{e}', file=sys.stderr)
            return finish(2, 'repair-invalid')
    report['bundleHash'] = bundle['bundleHash']
    report['bundleImport'] = bundle.get('importPaths') or report.get('bundleImport')
    report['stageResultHash'] = bundle['stageResultHash']
    report['newSnapshotHash'] = bundle['newSnapshotHash']
    # The repair secret is intentionally retained across stages.  It is only
    # applicable to the exact bundle it names; a stale repair must not block a
    # later normal stage.
    repair_binding = None
    if repair is not None:
        if repair.get('originalBundleHash') == bundle['bundleHash'] \
                and repair.get('originalStageResultHash') == bundle['stageResultHash']:
            repair_binding = repair
        else:
            baseline = report.get('baselineAcceptanceId')
            meta_baseline = meta.get('baselineAcceptance') or {}
            source = meta_baseline.get('source') if isinstance(meta_baseline, dict) else {}
            baseline_current = (isinstance(source, dict)
                                and source.get('bundleHash') == bundle['bundleHash']
                                and meta.get('appliedBundleHash') == bundle['bundleHash'])
            if baseline_current:
                print('❌ baseline repair does not bind current bundle', file=sys.stderr)
                return finish(2, 'repair-invalid')
            report['repairIgnored'] = 'does-not-bind-this-bundle'
    if not apply:
        print(f"sync dry-run ready: bundle={bundle['bundleHash']} stage={state['stage']}")
        return finish(0, 'dry-run-ready', ok=True)
    if noop_candidate:
        try:
            verified = verify_local_post(bundle, meta, BASE, repair=repair_binding)
        except (SyncError, biggo_apply.ApplyError) as e:
            print(f'❌ local post-state integrity failure（零資料寫入）：{e}', file=sys.stderr)
            return finish(3, 'local-integrity-error', ok=False,
                          postStateVerified=False, error=type(e).__name__)
        report.update({'postStateVerified': True, 'verifiedPost': verified,
                       'applied': False, 'noop': True})
        _attach_receiver_facts(report, verified)
        print(f"sync already-applied verified: bundle={bundle['bundleHash']}")
        return finish(0, 'already-applied', ok=True)
    try:
        result = biggo_apply.apply_bundle(bundle, repo_root=BASE, repair=repair_binding)
    except biggo_apply.ApplyError as e:
        print(f'❌ local apply 失敗（零 coordinator write）：{e}', file=sys.stderr)
        return finish(3, 'apply-error', error=type(e).__name__)
    try:
        meta_after = batch_utils.load_meta(os.path.join(BASE, 'prices_meta.json'))
        verified = verify_local_post(bundle, meta_after, BASE, repair=repair_binding)
    except (SyncError, biggo_apply.ApplyError, batch_utils.MetaError) as e:
        print(f'❌ post-apply local integrity failure：{e}', file=sys.stderr)
        return finish(3, 'local-integrity-error', ok=False,
                      postStateVerified=False, error=type(e).__name__)
    report['applied'] = True
    report['postStateVerified'] = True
    report['verifiedPost'] = verified
    report['result'] = result
    _attach_receiver_facts(report, verified)
    print(f"sync applied: noop={result.get('noop')} bundle={bundle['bundleHash']}")
    return finish(0, 'applied', ok=True)


if __name__ == '__main__':
    sys.exit(main())
