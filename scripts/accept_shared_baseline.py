#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accept a verified shared BigGo stage snapshot as an explicit local baseline.

The shared stage3 bundle + immutable repair receipt are the source of truth.
This tool rebuilds the post images from the actual server pre/post bytes and
lifecycle effects, verifies every source/target hash/schema, and either writes
a deterministic accepted-baseline receipt (default, read-only) or atomically
adopts the four active files under the same lock/journal as normal BigGo apply.

It is not a stage replay: stage1/2 history is never fabricated.  The active
prices_meta records the explicit accepted baseline, cycle/start/index from the
verified stage3, local adoption UTC time and source identities.  Old GitHub
files are archived byte-for-byte before any write; public-only keys remain in
that immutable archive and are listed in the receipt.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import batch_utils  # noqa: E402
import biggo_apply
import biggo_acquisition  # noqa: E402
import repair_biggo_apply as repair_mod  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_json  # noqa: E402

FILES = ('biggo_prices.json', 'model_status.json', 'model_blacklist.json',
         'prices_meta.json')
DATA = FILES[:3]
KIND = 'accepted-shared-baseline-v1'
JOURNAL = '.github-baseline-acceptance-journal.json'
ARCHIVE_PREFIX = 'github-baseline-archive-'
SOURCE_NAMES = ('manifest.json', 'base-biggo_prices.json', 'biggo_prices.json',
                'stage-result.json')


class BaselineError(RuntimeError):
    pass


def _load(path, label):
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        return json.loads(raw.decode('utf-8')), raw
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise BaselineError(f'{label} 讀取失敗：{type(e).__name__}') from e


def _black(obj):
    return obj.get('models') if isinstance(obj, dict) and 'models' in obj else obj


def _canon_file(path, name):
    obj, _ = _load(path, name)
    return sha256_json(_black(obj)) if name == 'model_blacklist.json' else sha256_json(obj)


def _raw_hash(path):
    return 'sha256:' + hashlib.sha256(open(path, 'rb').read()).hexdigest()


def _utc(value, label):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise BaselineError(f'{label} 必須係完整 UTC timestamp')
    try:
        dt = _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as e:
        raise BaselineError(f'{label} 時間無效') from e
    if dt.tzinfo is None or dt.utcoffset() != _dt.timedelta(0):
        raise BaselineError(f'{label} 必須係 UTC')
    return dt.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _hkt_date(generated_at):
    try:
        dt = _dt.datetime.fromisoformat(str(generated_at).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone(_dt.timedelta(hours=8))).strftime('%Y-%m-%d')
    except ValueError as e:
        raise BaselineError('stage-result generatedAt 無效') from e


def _file_hashes(root):
    out = {}
    for n in DATA:
        p = os.path.join(root, n)
        if not os.path.isfile(p):
            raise BaselineError(f'source 缺少 {n}')
        out[n] = _canon_file(p, n)
    return out


def _read_three(root):
    return {n: _load(os.path.join(root, n), n)[0] for n in DATA}


def _public_only(github_pre, target):
    out = {}
    for n in DATA:
        old = github_pre[n]
        new = target[n]
        old = _black(old) if n == 'model_blacklist.json' else old
        new = _black(new) if n == 'model_blacklist.json' else new
        keys = sorted(set(old) - set(new))
        out[n] = [{'key': k, 'oldHash': 'sha256:' + hashlib.sha256(
            json.dumps(old[k], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            'reason': 'archived-public-only; not adopted into shared baseline'}
            for k in keys]
    return out


def _date_analysis(github_price, target_price):
    rows = []
    counts = {'onlyPriceMerchantSame': 0, 'amountOrMerchantChanged': 0,
              'olderDate': 0, 'newerDate': 0, 'sameDate': 0}
    for key in sorted(set(github_price) & set(target_price)):
        old = github_price[key]; new = target_price[key]
        if old == new:
            continue
        old_date = old.get('updated') if isinstance(old, dict) else None
        new_date = new.get('updated') if isinstance(new, dict) else None
        core_old = ({k: old.get(k) for k in ('price', 'merchants')}
                    if isinstance(old, dict) else {})
        core_new = ({k: new.get(k) for k in ('price', 'merchants')}
                    if isinstance(new, dict) else {})
        cls = 'only-price-merchant-same' if core_old == core_new else 'amount-or-merchant-changed'
        counts['onlyPriceMerchantSame' if cls.startswith('only') else 'amountOrMerchantChanged'] += 1
        relation = 'unknown'
        if old_date and new_date:
            if new_date < old_date:
                relation = 'older'; counts['olderDate'] += 1
            elif new_date > old_date:
                relation = 'newer'; counts['newerDate'] += 1
            else:
                relation = 'same'; counts['sameDate'] += 1
        rows.append({'key': key, 'class': cls, 'dateRelation': relation,
                     'githubUpdated': old_date, 'sharedUpdated': new_date,
                     'githubValue': old, 'sharedValue': new})
    return {'counts': counts, 'rows': rows}


def _server_only(github_pre, target):
    out = {}
    for n in DATA:
        old = github_pre[n]
        new = target[n]
        old = _black(old) if n == 'model_blacklist.json' else old
        new = _black(new) if n == 'model_blacklist.json' else new
        out[n] = [{'key': k, 'newValue': new[k],
                   'newHash': 'sha256:' + hashlib.sha256(
                       json.dumps(new[k], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                   'reason': 'adopted from verified shared post-state'}
                  for k in sorted(set(new) - set(old))]
    return out


def _expected(bundle_dir, repair_path, source_pre, source_post, github_pre,
              adopted_at):
    bundle = repair_mod.load_bundle(os.path.abspath(bundle_dir))
    repair, repair_raw = _load(repair_path, 'repair receipt')
    if repair.get('repairId') != biggo_apply.repair_id_for(repair):
        raise BaselineError('repair receipt identity invalid')
    if repair.get('originalBundleHash') != bundle['bundleHash'] \
            or repair.get('originalStageResultHash') != bundle['stageResultHash']:
        raise BaselineError('repair does not bind bundle')
    sr, _ = biggo_apply.merge_repair_effects(bundle, repair)
    pre = _read_three(source_pre)
    post = _read_three(source_post)
    gh = _read_three(github_pre)
    gh_meta, _ = _load(os.path.join(github_pre, 'prices_meta.json'), 'github pre meta')
    pre_meta, _ = _load(os.path.join(source_pre, 'prices_meta.json'), 'source pre meta')
    post_meta, _ = _load(os.path.join(source_post, 'prices_meta.json'), 'source post meta')
    if sha256_json(pre['biggo_prices.json']) != bundle['baseSnapshotHash']:
        raise BaselineError('source pre price != bundle base')
    if sha256_json(pre['model_status.json']) != repair.get('preState', {}).get('trackingHash'):
        raise BaselineError('source pre tracking != repair pre')
    if sha256_json(_black(pre['model_blacklist.json'])) != repair.get('preState', {}).get('blacklistHash'):
        raise BaselineError('source pre blacklist != repair pre')
    rebuilt_tracking = biggo_apply._apply_tracking(pre['model_status.json'], sr['effects'])
    rebuilt_black = biggo_apply._apply_blacklist(_black(pre['model_blacklist.json']), sr['effects'])
    expected = {
        'biggo_prices.json': bundle['newSnapshot'],
        'model_status.json': rebuilt_tracking,
        # the verified contract is the models object; keep source wrapper facts
        'model_blacklist.json': {**post['model_blacklist.json'], 'models': rebuilt_black},
    }
    expected_hash = {
        'biggo_prices.json': sha256_json(expected['biggo_prices.json']),
        'model_status.json': sha256_json(expected['model_status.json']),
        'model_blacklist.json': sha256_json(rebuilt_black),
    }
    if expected_hash['model_status.json'] != sr['postState']['trackingHash']:
        raise BaselineError('rebuilt tracking != verified post')
    if expected_hash['model_blacklist.json'] != sr['postState']['blacklistHash']:
        raise BaselineError('rebuilt blacklist != verified post')
    # actual server post must be the same verified post, not merely self-claimed.
    if _file_hashes(source_post) != expected_hash:
        raise BaselineError('server post hashes != rebuilt verified post')
    if _file_hashes(source_pre) != {
            'biggo_prices.json': bundle['baseSnapshotHash'],
            'model_status.json': repair.get('preState', {}).get('trackingHash'),
            'model_blacklist.json': repair.get('preState', {}).get('blacklistHash')}:
        raise BaselineError('source pre hashes mismatch')
    github_hash = _file_hashes(github_pre)
    github_raw = {n: _raw_hash(os.path.join(github_pre, n)) for n in FILES}
    source_evidence = {}
    for n in SOURCE_NAMES:
        p = os.path.join(bundle_dir, n)
        if not os.path.isfile(p):
            raise BaselineError(f'bundle source missing {n}')
        source_evidence[n] = _raw_hash(p)
    source_evidence['repair.json'] = _raw_hash(repair_path)
    cycle = bundle['manifest']['cycleId']
    date = cycle.split(':', 1)[0]
    stage = bundle['manifest']['stage']
    # Publication time is not an acquisition witness.  Only the immutable
    # stage-result witness (if any) may supply a time; legacy bundles stay
    # unknown and are recorded as such.
    try:
        witness = biggo_acquisition.from_stage_result(bundle['stageResult'])
    except biggo_acquisition.AcquisitionError as exc:
        raise BaselineError(f'acquisition witness invalid: {exc}') from exc
    acquired_at = witness['acquiredAt']
    meta = copy.deepcopy(post_meta)
    # Replace any historical apply fact with an explicit local adoption fact.
    meta.update({
        'price_batch_start': date,
        'price_batch_idx': int(stage),
        'last_price_month': date[:7],
        'last_run': _hkt_date(sr.get('generatedAt')),
        'last_batch_status': 'completed',
        'last_batch_idx': int(stage) - 1,
        'last_batch_net_errors': 0,
        'last_applied_cycle': cycle,
        'appliedBundleHash': bundle['bundleHash'],
        'appliedSnapshotHash': bundle['newSnapshotHash'],
        'appliedAt': adopted_at,
        'appliedSnapshotId': 'biggo-baseline-' + cycle + '-stage' + str(stage) + '-' + bundle['newSnapshotHash'][-12:],
    })
    # stage3 is not a full cycle; preserve the source meta's last_full value,
    # but do not invent a new one.
    if stage < 7 and 'last_full' in post_meta:
        meta['last_full'] = post_meta['last_full']
    acceptance = {
        'schemaVersion': 1,
        'kind': KIND,
        'acceptanceId': None,
        'adoptedAt': adopted_at,
        'source': {
            'bundleHash': bundle['bundleHash'],
            'repairId': repair.get('repairId'),
            'cycleId': cycle, 'stage': stage,
            'baseSnapshotHash': bundle['baseSnapshotHash'],
            'newSnapshotHash': bundle['newSnapshotHash'],
            'stageResultHash': bundle['stageResultHash'],
            'acquiredAt': acquired_at,
            'acquisitionProvenance': witness['provenance'],
            'acquisitionHash': witness['blockHash'],
            'sourceEvidence': source_evidence,
            'sourcePre': _file_hashes(source_pre),
            'sourcePost': expected_hash,
            'githubPre': github_hash,
            'githubPreMeta': sha256_json(gh_meta),
            'githubPreRaw': github_raw,
        },
        'archive': {'prefix': ARCHIVE_PREFIX, 'oldFiles': github_hash},
        'publicOnly': _public_only(gh, expected),
        'serverOnly': _server_only(gh, expected),
        'activePost': expected_hash,
        'distinction': 'local accepted shared baseline; not a replay of GitHub stage1/2/3',
    }
    # acceptanceId is independent of receiptId and is embedded in meta.
    aid_body = {k: v for k, v in acceptance.items() if k != 'acceptanceId'}
    acceptance['acceptanceId'] = 'sha256:' + hashlib.sha256(
        json.dumps(aid_body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    meta['baselineAcceptance'] = acceptance
    target = {**expected, 'prices_meta.json': meta}
    target_hash = {n: (sha256_json(target[n]) if n != 'model_blacklist.json'
                       else sha256_json(_black(target[n]))) for n in FILES}
    target_hash['prices_meta.json'] = sha256_json(target['prices_meta.json'])
    # Validate target schema before any write/receipt.
    try:
        batch_utils._validate_meta(meta, 'prices_meta')
    except batch_utils.MetaError as e:
        raise BaselineError(f'target meta schema invalid: {e}') from e
    receipt = {
        'schemaVersion': 1, 'kind': KIND,
        'createdAt': adopted_at,
        'adoptedAt': adopted_at,
        'receiptId': None,
        'acceptanceId': acceptance['acceptanceId'],
        'source': acceptance['source'],
        'targetFiles': target_hash,
        'target': target,
        'publicOnly': acceptance['publicOnly'],
        'serverOnly': acceptance['serverOnly'],
        'dateAnalysis': _date_analysis(gh['biggo_prices.json'], expected['biggo_prices.json']),
        'archive': acceptance['archive'],
        'distinction': acceptance['distinction'],
    }
    receipt['receiptId'] = 'sha256:' + hashlib.sha256(
        json.dumps({k: v for k, v in receipt.items() if k != 'receiptId'},
                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return receipt


def _validate_receipt(receipt):
    if receipt.get('schemaVersion') != 1 or receipt.get('kind') != KIND:
        raise BaselineError('receipt kind/schema invalid')
    expected = receipt.get('receiptId')
    body = {k: v for k, v in receipt.items() if k != 'receiptId'}
    want = 'sha256:' + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if expected != want:
        raise BaselineError('receiptId invalid')
    if receipt.get('acceptanceId') != (receipt.get('target', {}).get('prices_meta.json', {})
                                       .get('baselineAcceptance', {}).get('acceptanceId')):
        raise BaselineError('acceptanceId/meta mismatch')
    return want


def _archive(receipt, root, archive_root):
    short = receipt['receiptId'].split(':', 1)[1][:16]
    dest = os.path.join(os.path.abspath(archive_root), ARCHIVE_PREFIX + short)
    if os.path.isdir(dest):
        for n in FILES:
            p = os.path.join(dest, n)
            expected = receipt['source']['githubPreRaw'][n]
            if not os.path.isfile(p) or _raw_hash(p) != expected:
                raise BaselineError(f'archive conflict: {n}')
        return dest
    os.makedirs(dest, mode=0o700)
    for n in FILES:
        src = os.path.join(root, n)
        expected = receipt['source']['githubPreRaw'][n]
        if _raw_hash(src) != expected:
            raise BaselineError(f'github pre changed before archive: {n}')
        data = open(src, 'rb').read()
        dst = os.path.join(dest, n)
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        if _raw_hash(dst) != _raw_hash(src):
            raise BaselineError(f'archive hash mismatch: {n}')
    fd = os.open(dest, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)
    return dest


def _journal(path, receipt, archive):
    return {'schemaVersion': 1, 'kind': 'accepted-shared-baseline-journal-v1',
            'receiptId': receipt['receiptId'], 'acceptanceId': receipt['acceptanceId'],
            'adoptedAt': receipt['adoptedAt'], 'archive': archive,
            'pre': {**receipt['source']['githubPre'],
                    'prices_meta.json': receipt['source']['githubPreMeta']},
            'post': receipt['targetFiles'],
            'state': 'pending'}


def _current(root):
    return _file_hashes(root) | {'prices_meta.json': _canon_file(os.path.join(root, 'prices_meta.json'), 'prices_meta.json')}


def build(args):
    adopted = _utc(args.adopted_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'adoptedAt')
    receipt = _expected(args.bundle_dir, args.repair, args.source_pre, args.source_post,
                        args.github_pre, adopted)
    if os.path.exists(args.out):
        old, _ = _load(args.out, 'existing receipt')
        if old.get('receiptId') != receipt['receiptId']:
            raise BaselineError('immutable receipt already exists with another identity')
    else:
        with open(args.out, 'wb') as f:
            f.write(canonical_json_bytes(receipt)); f.flush(); os.fsync(f.fileno())
    print(f"✅ accepted shared baseline receipt: {receipt['receiptId']}")
    print(f"   acceptance={receipt['acceptanceId']} adoptedAt={adopted}")
    print(f"   target={receipt['targetFiles']}")
    return 0


def plan(args):
    receipt, _ = _load(args.receipt, 'receipt')
    _validate_receipt(receipt)
    expected = _expected(args.bundle_dir, args.repair, args.source_pre, args.source_post,
                         args.github_pre, receipt['adoptedAt'])
    if expected['receiptId'] != receipt['receiptId']:
        raise BaselineError('receipt does not match current verified sources')
    current = _current(args.repo)
    if current == receipt['targetFiles']:
        return {'ok': True, 'verdict': 'noop', 'receiptId': receipt['receiptId']}
    pre = {**receipt['source']['githubPre'],
           'prices_meta.json': receipt['source']['githubPreMeta']}
    if current != pre:
        raise BaselineError('current GitHub state is neither pre nor accepted post')
    return {'ok': True, 'verdict': 'ready', 'receiptId': receipt['receiptId'],
            'archivePrefix': ARCHIVE_PREFIX + receipt['receiptId'].split(':', 1)[1][:16],
            'target': receipt['targetFiles']}


def apply(args):
    receipt, _ = _load(args.receipt, 'receipt')
    receipt_id = _validate_receipt(receipt)
    if receipt_id != args.expect_receipt_id:
        raise BaselineError('receipt does not match --expect-receipt-id')
    expected = _expected(args.bundle_dir, args.repair, args.source_pre, args.source_post,
                         args.github_pre, receipt['adoptedAt'])
    if expected['receiptId'] != receipt_id:
        raise BaselineError('receipt does not match current verified sources')
    root = os.path.abspath(args.repo)
    with biggo_apply._OperationLock(root):
        current = _current(root)
        if current == receipt['targetFiles']:
            return {'ok': True, 'verdict': 'noop', 'receiptId': receipt_id}
        jp = os.path.join(root, JOURNAL)
        journal = None
        if os.path.exists(jp):
            journal, _raw = _load(jp, 'acceptance journal')
            if journal.get('receiptId') != receipt_id \
                    or journal.get('adoptedAt') != receipt['adoptedAt']:
                raise BaselineError('existing journal conflicts with receipt')
            pre_all = {**receipt['source']['githubPre'],
                       'prices_meta.json': receipt['source']['githubPreMeta']}
            for n in FILES:
                if current[n] not in (pre_all[n], receipt['targetFiles'][n]):
                    raise BaselineError(f'journal recovery found unknown image: {n}')
            archive = journal.get('archive')
            if not archive or not os.path.isdir(archive):
                raise BaselineError('journal archive missing')
        else:
            pre = {**receipt['source']['githubPre'],
                   'prices_meta.json': receipt['source']['githubPreMeta']}
            if current != pre:
                raise BaselineError('prestate changed; refusing')
            archive = _archive(receipt, root, args.archive_root)
            journal = _journal(jp, receipt, archive)
            biggo_apply._write_json(jp, journal)
        # Mixed pre/post is accepted only in the same-receipt journal path.
        if any(current[n] == receipt['targetFiles'][n] for n in DATA) \
                and current != receipt['targetFiles'] and journal is None:
            raise BaselineError('partial data without journal; refusing')
        assert journal is not None
        try:
            for n in DATA:
                data = canonical_json_bytes(receipt['target'][n])
                biggo_apply._atomic_write_bytes(os.path.join(root, n), data)
                if _canon_file(os.path.join(root, n), n) != receipt['targetFiles'][n]:
                    raise BaselineError(f'post hash mismatch after {n}')
                journal['state'] = n
                biggo_apply._write_json(os.path.join(root, JOURNAL), journal)
            batch_utils.save_meta(receipt['target']['prices_meta.json'],
                                  os.path.join(root, 'prices_meta.json'))
            if _canon_file(os.path.join(root, 'prices_meta.json'), 'prices_meta.json') \
                    != receipt['targetFiles']['prices_meta.json']:
                raise BaselineError('prices_meta post hash mismatch')
            journal['state'] = 'committed'; biggo_apply._write_json(os.path.join(root, JOURNAL), journal)
        except Exception:
            raise
        return {'ok': True, 'verdict': 'applied', 'receiptId': receipt_id,
                'archive': archive, 'journal': JOURNAL}


def rollback(args):
    receipt, _ = _load(args.receipt, 'receipt')
    receipt_id = _validate_receipt(receipt)
    if receipt_id != args.expect_receipt_id:
        raise BaselineError('receipt does not match --expect-receipt-id')
    root = os.path.abspath(args.repo)
    journal, _ = _load(os.path.join(root, JOURNAL), 'journal')
    if journal.get('receiptId') != receipt_id:
        raise BaselineError('journal receipt mismatch')
    archive = journal.get('archive')
    # Verify every backup before the first write.
    for n in FILES:
        p = os.path.join(archive, n)
        if not os.path.isfile(p) or _raw_hash(p) != receipt['source']['githubPreRaw'][n]:
            raise BaselineError(f'rollback backup invalid: {n}')
    with biggo_apply._OperationLock(root):
        if _current(root) != receipt['targetFiles']:
            raise BaselineError('current is not accepted baseline post (later stage or conflict)')
        for n in DATA:
            data = open(os.path.join(archive, n), 'rb').read()
            biggo_apply._atomic_write_bytes(os.path.join(root, n), data)
            if _canon_file(os.path.join(root, n), n) != receipt['source']['githubPre'][n]:
                raise BaselineError(f'rollback hash mismatch: {n}')
        data = open(os.path.join(archive, 'prices_meta.json'), 'rb').read()
        biggo_apply._atomic_write_bytes(os.path.join(root, 'prices_meta.json'), data)
        if _current(root) != {**receipt['source']['githubPre'],
                              'prices_meta.json': receipt['source']['githubPreMeta']}:
            raise BaselineError('rollback meta/data mismatch')
        journal['state'] = 'rolled-back'; biggo_apply._write_json(os.path.join(root, JOURNAL), journal)
    return {'ok': True, 'verdict': 'rolled-back', 'receiptId': receipt_id}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('build', 'plan', 'apply'):
        x = sub.add_parser(name)
        x.add_argument('--bundle-dir', required=True)
        x.add_argument('--repair', required=True)
        x.add_argument('--source-pre', required=True)
        x.add_argument('--source-post', required=True)
        x.add_argument('--github-pre', required=True)
        if name == 'build':
            x.add_argument('--out', required=True)
            x.add_argument('--adopted-at', default=None)
        else:
            x.add_argument('--receipt', required=True)
            x.add_argument('--repo', required=True)
        if name == 'apply':
            x.add_argument('--archive-root', required=True)
            x.add_argument('--expect-receipt-id', required=True)
    x = sub.add_parser('rollback')
    x.add_argument('--receipt', required=True); x.add_argument('--repo', required=True)
    x.add_argument('--expect-receipt-id', required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'build': return build(args)
        if args.cmd == 'plan':
            print(json.dumps(plan(args), ensure_ascii=False)); return 0
        if args.cmd == 'apply':
            print(json.dumps(apply(args), ensure_ascii=False)); return 0
        if args.cmd == 'rollback':
            print(json.dumps(rollback(args), ensure_ascii=False)); return 0
    except (BaselineError, biggo_apply.ApplyError, batch_utils.MetaError, OSError) as e:
        print(f'❌ baseline: {e}', file=sys.stderr); return 1


if __name__ == '__main__':
    sys.exit(main())
