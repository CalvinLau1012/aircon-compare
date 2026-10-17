#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo local apply（P0 stage bundle）。

只由 `scripts/biggo_stage_runner.py` 在 coordinator CAS status=completed 之後呼叫。

安全契約（2026-10-17 修復）：
  - 先做全檔 preflight：每個檔案 hash 必須明確等於 bundle 的 pre-image 或
    post-image，否則零寫入 fail-closed（交人手 reconciliation）；
  - 所有目標內容（包括 prices_meta 目標）在任何寫入之前計算及驗證；
  - 整個 local operation 持有同一個 exclusive lock；並行 writer 只會 fail-closed；
  - durable journal（bundleHash/pre-post hash/target hashes）喺第一個寫入前建立，
    每次寫入後 checkpoint；partial write／crash 後同一個 bundle 可安全 replay；
  - 四個檔案以 snapshot→tracking→blacklist→meta 次序寫入，metadata last；
  - 已應用同一 bundle 再呼叫係 no-op；journal 或 meta 衝突一律 fail-closed；
  - optional immutable repair receipt 只可加入「既有 record 的 tracking upserts」，
    仍須重算出原本 postState hash；唔可以改價錢 bytes、cycle 或 acquisition 日期。
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import errno
import fcntl
import hashlib
import json
import os
import time

import batch_utils
from biggo_canonical import canonical_json_bytes, sha256_json

BIGGO_SNAPSHOT = 'biggo_prices.json'
TRACKING = 'model_status.json'
BLACKLIST = 'model_blacklist.json'
META = 'prices_meta.json'
LOCK_NAME = '.aircon-biggo-apply.lock'
JOURNAL_NAME = '.aircon-biggo-apply-journal.json'
REPAIR_SCHEMA_VERSION = 1
REPAIR_TRANSIENT_FIELDS = frozenset({'repairId', 'createdAt', 'tool', 'immutable'})


class ApplyError(RuntimeError):
    pass


def repair_id_for(receipt):
    """Stable repair identity; excludes audit timestamp/tool metadata."""
    body = {k: v for k, v in receipt.items() if k not in REPAIR_TRANSIENT_FIELDS}
    return 'sha256:' + hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ApplyError(f'{os.path.basename(path)} 讀取／解析失敗：{e}') from e


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_bytes(path, data, mode=0o644):
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    tmp = f'{path}.tmp.{os.getpid()}.{time.time_ns()}'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp, path)
        _fsync_dir(directory)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ApplyError(f'{os.path.basename(path)} 寫入失敗：{e}') from e


def _write_json(path, obj):
    _atomic_write_bytes(path, canonical_json_bytes(obj))


def _canonical_hash(obj):
    return sha256_json(obj)


class _OperationLock:
    """Process-wide exclusive lock; held for the complete local operation."""

    def __init__(self, root):
        self.path = os.path.join(root, LOCK_NAME)
        self.fd = None

    def __enter__(self):
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        try:
            self.fd = os.open(self.path, flags, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise ApplyError('另一個 BigGo local apply 正在進行（lock busy；零寫入）') from e
            raise ApplyError(f'BigGo apply lock 失敗：{e}') from e
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


def _journal_path(root):
    return os.path.join(root, JOURNAL_NAME)


def _read_journal(root):
    path = _journal_path(root)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ApplyError(f'apply journal 損毀：{e}') from e
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 \
            or not isinstance(data.get('bundleHash'), str):
        raise ApplyError('apply journal schema 唔合格')
    return data


def _write_journal(root, journal):
    _write_json(_journal_path(root), journal)


def _remove_journal(root):
    path = _journal_path(root)
    try:
        os.remove(path)
        _fsync_dir(root)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise ApplyError(f'apply journal 清理失敗（已完成，但需人手清理）：{e}') from e


def _hash_file_json(path):
    obj = _read_json(path, default=None)
    if obj is None:
        return None, None
    return obj, _canonical_hash(obj)


def _apply_tracking(tracking, effects):
    out = dict(tracking)
    for key in effects.get('trackingRemovals', []):
        out.pop(key, None)
    for key, rec in (effects.get('trackingUpserts') or {}).items():
        out[key] = copy.deepcopy(rec)
    return out


def _apply_blacklist(models, effects):
    out = dict(models)
    for key in effects.get('blacklistRemovals', []):
        out.pop(key, None)
    for key, rec in (effects.get('blacklistUpserts') or {}).items():
        out[key] = copy.deepcopy(rec)
    return out


def _plan_image(path, pre_hash, post_hash, transform, *, label):
    """單檔 preflight：確認 pre/post image，計算結果但唔寫檔。"""
    current, current_hash = _hash_file_json(path)
    if current is None:
        raise ApplyError(f'{label} 檔案唔存在（唔可以聲稱 pre-image）')
    if current_hash == post_hash:
        return 'post', None, current_hash
    if current_hash != pre_hash:
        raise ApplyError(f'{label} pre/post image 唔 match（needs manual reconciliation）')
    new_obj = transform(current)
    if _canonical_hash(new_obj) != post_hash:
        raise ApplyError(f'{label} effects 重算結果同 post-state hash 唔一致')
    return 'pre', new_obj, current_hash


def _plan_blacklist_file(path, pre_hash, post_hash, effects):
    wrapper, _ = _hash_file_json(path)
    if wrapper is None:
        raise ApplyError('model_blacklist.json 唔存在')
    models = wrapper.get('models') if isinstance(wrapper, dict) else None
    if not isinstance(models, dict):
        raise ApplyError('model_blacklist.json 冇 models object')
    current_hash = _canonical_hash(models)
    if current_hash == post_hash:
        return 'post', None, current_hash
    if current_hash != pre_hash:
        raise ApplyError('model_blacklist.json pre/post image 唔 match'
                         '（needs manual reconciliation）')
    new_models = _apply_blacklist(models, effects)
    if _canonical_hash(new_models) != post_hash:
        raise ApplyError('blacklist effects 重算結果同 post-state hash 唔一致')
    new_wrapper = dict(wrapper)
    new_wrapper['models'] = new_models
    # Preserve the original `updated` value when the target wrapper hash is not
    # part of the bundle contract; callers that need a date can pass it via a
    # repair receipt.  Existing behaviour used today's date, which made a
    # replay change bytes even after the models were already post-image.
    new_wrapper.setdefault('updated', time.strftime('%Y-%m-%d'))
    return 'pre', new_wrapper, current_hash


def _run_date_from_stage(sr, manifest, now):
    """Stage actual completion date (UTC generatedAt → HKT); fallback cycle date."""
    gen = str(sr.get('generatedAt') or '')
    try:
        dt_utc = _dt.datetime.strptime(gen, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=_dt.timezone.utc)
        return dt_utc.astimezone(_dt.timezone(_dt.timedelta(hours=8))).strftime('%Y-%m-%d')
    except ValueError:
        cycle = str(manifest.get('cycleId') or '')
        return cycle.split(':', 1)[0] if ':' in cycle else time.strftime(
            '%Y-%m-%d', time.gmtime(now or time.time()))


def _utc_timestamp(value):
    """Normalise a successful apply instant to a full UTC Z timestamp.

    Date-only values are deliberately not accepted for a new write: the old
    producer stored such a value, which source_status cannot use as a time.
    Existing date-only values remain readable for legacy/no-op preservation.
    """
    if value is None:
        when = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(value, _dt.datetime):
        when = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        when = _dt.datetime.fromtimestamp(value, _dt.timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if len(text) == 10:
            raise ApplyError('appliedAt 新寫入必須係完整 UTC timestamp，不可用 date-only')
        try:
            when = _dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError as e:
            raise ApplyError(f'appliedAt 時間格式唔正確：{value!r}') from e
    else:
        raise ApplyError(f'appliedAt 類型唔支援：{type(value).__name__}')
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _applied_date(applied_at):
    """Legacy display date helper; never used for the new appliedAt field."""
    ts = applied_at.timestamp() if hasattr(applied_at, 'timestamp') else applied_at
    return _dt.datetime.fromtimestamp(ts or time.time(), _dt.timezone.utc).astimezone(
        _dt.timezone(_dt.timedelta(hours=8))).strftime('%Y-%m-%d')


def _snapshot_identity(manifest, snapshot_hash):
    cycle = str(manifest.get('cycleId') or 'unknown-cycle')
    stage = manifest.get('stage')
    tail = str(snapshot_hash or '')[-12:]
    return f'biggo-{cycle}-stage{stage}-{tail}'


def _target_meta(meta, sr, manifest, now, applied_at=None):
    """由 bundle 計算新 meta（唔寫檔）；stage 只可前進，不可倒退。"""
    mode = manifest.get('mode')
    stage = manifest['stage']
    run_date = _run_date_from_stage(sr, manifest, now)
    today = run_date
    new_meta = dict(meta)
    if mode == 'price-batch':
        cycle = manifest.get('cycleId', '')
        date = cycle.split(':', 1)[0] if ':' in cycle else ''
        if meta.get('price_batch_start') != date:
            raise ApplyError('本地 price_batch_start 同 bundle cycle 唔 match')
        current = int(meta.get('price_batch_idx', 0) or 0)
        target = max(current, int(stage))
        new_meta['price_batch_idx'] = target
        new_meta['last_price_month'] = date[:7]
        if target >= batch_utils.PRICE_BATCH_DAYS:
            new_meta.pop('price_batch_start', None)
            new_meta['last_full'] = today
        new_meta['last_run'] = today
        new_meta['last_batch_status'] = 'completed'
        new_meta['last_batch_idx'] = int(stage) - 1
        new_meta['last_batch_net_errors'] = 0
    elif mode == 'force':
        stamp = (sr.get('metaFields') or {}).get('last_force_batch')
        new_meta['last_force_batch'] = stamp or time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(now or time.time()))
    else:
        raise ApplyError(f'bundle mode 唔支援：{mode!r}')
    new_meta['last_applied_cycle'] = manifest.get('cycleId')
    new_meta['appliedSnapshotId'] = _snapshot_identity(
        manifest, manifest.get('snapshotHash') or manifest.get('newSnapshotHash'))
    if applied_at is not None:
        new_meta['appliedAt'] = _utc_timestamp(applied_at)
    return new_meta


def _validate_bundle(bundle):
    manifest = bundle.get('manifest')
    sr = bundle.get('stageResult')
    new_snapshot = bundle.get('newSnapshot')
    if not isinstance(manifest, dict) or not isinstance(sr, dict) \
            or not isinstance(new_snapshot, dict):
        raise ApplyError('bundle 結構唔完整')
    if _canonical_hash(manifest) != bundle.get('bundleHash'):
        raise ApplyError('bundleHash 唔一致')
    if _canonical_hash(sr) != bundle.get('stageResultHash'):
        raise ApplyError('stageResultHash 唔一致')
    if _canonical_hash(sr.get('effects')) != sr.get('effectsHash'):
        raise ApplyError('effectsHash 唔一致')
    if _canonical_hash(new_snapshot) != bundle.get('newSnapshotHash'):
        raise ApplyError('newSnapshotHash 唔一致')
    for key in ('trackingHash', 'blacklistHash'):
        value = (sr.get('preState') or {}).get(key)
        if not isinstance(value, str) or not value.startswith('sha256:'):
            raise ApplyError(f'stageResult.preState.{key} 唔合法')
        value = (sr.get('postState') or {}).get(key)
        if not isinstance(value, str) or not value.startswith('sha256:'):
            raise ApplyError(f'stageResult.postState.{key} 唔合法')


def merge_repair_effects(bundle, repair):
    """Validate an immutable repair receipt and return a copied stage-result.

    Only tracking upserts for records already present in the pre-image are
    accepted.  Price bytes, snapshot hashes, cycle and acquisition facts are
    never touched.  The repaired effects must still reproduce the original
    postState hash exactly.
    """
    if repair is None:
        return bundle['stageResult'], None
    if not isinstance(repair, dict):
        raise ApplyError('repair receipt 必須係 object')
    if repair.get('schemaVersion') != REPAIR_SCHEMA_VERSION:
        raise ApplyError('repair receipt schemaVersion 唔支援')
    if repair.get('kind') != 'tracking-existing-record-upserts-v1':
        raise ApplyError('repair receipt kind 唔支援')
    if repair.get('originalBundleHash') != bundle.get('bundleHash'):
        raise ApplyError('repair receipt 同 bundleHash 唔 match')
    if repair.get('originalStageResultHash') != bundle.get('stageResultHash'):
        raise ApplyError('repair receipt 同 stageResultHash 唔 match')
    claimed = repair.get('repairId')
    if claimed != repair_id_for(repair):
        raise ApplyError('repair receipt repairId 唔一致')
    if repair.get('publishedPreState') != bundle['stageResult'].get('preState'):
        raise ApplyError('repair receipt publishedPreState 同 bundle 唔 match')
    if repair.get('postState') != bundle['stageResult'].get('postState'):
        raise ApplyError('repair receipt postState 同 bundle 唔 match')
    actual_pre = (repair.get('preState') or {}).get('trackingHash')
    if not isinstance(actual_pre, str) or not actual_pre.startswith('sha256:'):
        raise ApplyError('repair receipt preState.trackingHash 唔合法')
    upserts = repair.get('trackingUpserts')
    if not isinstance(upserts, dict) or not upserts:
        raise ApplyError('repair receipt trackingUpserts 必須係非空 object')
    sr = copy.deepcopy(bundle['stageResult'])
    effects = sr['effects']
    existing = {k: copy.deepcopy(v) for k, v in
                (effects.get('trackingUpserts') or {}).items()}
    for key, rec in upserts.items():
        if not isinstance(key, str) or not isinstance(rec, dict):
            raise ApplyError('repair trackingUpsert 格式唔正確')
        if key in existing and existing[key] != rec:
            raise ApplyError(f'repair 同原有 trackingUpsert 衝突：{key}')
        existing[key] = copy.deepcopy(rec)
    effects['trackingUpserts'] = existing
    sr['effectsHash'] = _canonical_hash(effects)
    # The original bundle/stage-result identity stays immutable; `sr` is an
    # in-memory repaired view validated against the original postState by the
    # caller.  Never write this view back to the coordinator.
    return sr, repair


def derive_tracking_repair(bundle, current_tracking):
    """Read-only derivation of missing existing-record tracking updates.

    The 2026-10-10 stage planner shallow-copied tracking, so existing records
    updated by a clean miss were mutated in memory before `compute_effects`.
    Those records were absent from `effects.trackingUpserts`; the published
    post hash is still authoritative.  This function discovers exactly those
    missing updates from stage outcomes and proves the repaired effects
    reproduce the original postState hash.  It performs no writes.
    """
    sr = bundle['stageResult']
    effects = sr['effects']
    published_pre_hash = sr['preState']['trackingHash']
    post_hash = sr['postState']['trackingHash']
    current_hash = _canonical_hash(current_tracking)
    if current_hash == post_hash:
        return None
    base_effects = copy.deepcopy(effects)
    candidate = _apply_tracking(current_tracking, base_effects)
    candidate_hash = _canonical_hash(candidate)
    if candidate_hash == post_hash:
        return None
    missing = {}
    cycle = sr.get('cycleId')
    generated = str(sr.get('generatedAt') or '')
    date = ''
    try:
        gen_dt = _dt.datetime.fromisoformat(generated.replace('Z', '+00:00'))
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=_dt.timezone.utc)
        date = gen_dt.astimezone(_dt.timezone(_dt.timedelta(hours=8))).date().isoformat()
    except ValueError:
        date = ''
    if not date or not cycle:
        raise ApplyError('stage-result 缺 generatedAt／cycleId，無法界定 repair 日期')
    # Only clean_miss outcomes can update an existing tracking record.  A
    # net_error must not be counted; priced outcomes remove tracking.
    for item in sr.get('outcomes') or []:
        if not isinstance(item, dict) or item.get('outcome') != 'clean_miss':
            continue
        key = item.get('canonicalKey')
        if not isinstance(key, str) or key not in current_tracking:
            continue
        if key in (effects.get('trackingUpserts') or {}):
            continue
        old = current_tracking.get(key)
        if not isinstance(old, dict):
            continue
        rec = copy.deepcopy(old)
        rec['last_checked'] = date
        rec['misses'] = int(rec.get('misses', 0)) + 1
        rec['first_missed'] = rec.get('first_missed') or date
        rec['batch_id'] = cycle
        missing[key] = rec
    if not missing:
        raise ApplyError('current tracking 同 postState 唔一致，但無可解釋的 clean_miss repair')
    # Two legitimate cases:
    #  a) current == published preState (the planner omitted effects but did not
    #     publish a mutated pre-image; e.g. small fixtures);
    #  b) current != published preState: the published pre-image was computed
    #     AFTER the buggy in-memory mutation, so current + missing must equal it.
    if current_hash != published_pre_hash:
        published_trial = _apply_tracking(current_tracking, {'trackingUpserts': missing})
        if _canonical_hash(published_trial) != published_pre_hash:
            raise ApplyError('current tracking 加回 missing effects 後唔等於原 published preState（drift）')
    trial_effects = copy.deepcopy(base_effects)
    trial_effects['trackingUpserts'] = {
        **(trial_effects.get('trackingUpserts') or {}), **missing}
    trial = _apply_tracking(current_tracking, trial_effects)
    if _canonical_hash(trial) != post_hash:
        raise ApplyError('推導的 repair 仍未能重算出原 postState hash（拒絕猜測）')
    body = {
        'schemaVersion': REPAIR_SCHEMA_VERSION,
        'kind': 'tracking-existing-record-upserts-v1',
        'originalBundleHash': bundle['bundleHash'],
        'originalStageResultHash': bundle['stageResultHash'],
        # Actual pre-image on disk (before original effects, without the
        # published in-memory mutation).
        'preState': {'trackingHash': current_hash,
                     'blacklistHash': sr['preState']['blacklistHash']},
        # Immutable published identity that the buggy planner used.
        'publishedPreState': copy.deepcopy(sr['preState']),
        'postState': copy.deepcopy(sr['postState']),
        'trackingUpserts': missing,
        'evidence': {
            'cycleId': cycle,
            'generatedAt': generated,
            'outcomes': [
                {'canonicalKey': k, 'outcome': 'clean_miss'}
                for k in sorted(missing)
            ],
        },
    }
    body['repairId'] = repair_id_for(body)
    return body


def apply_repair(bundle, repair, *, repo_root):
    """Apply ONLY the immutable tracking repair, idempotently and under lock.

    This does not write price, blacklist or meta.  A later normal
    `apply_bundle(..., repair=repair)` sees the tracking post-image and can
    complete the original four-file transaction without changing its identity.
    """
    if not isinstance(repair, dict):
        raise ApplyError('repair receipt 必須係 object')
    root = os.path.abspath(repo_root)
    tracking_path = os.path.join(root, TRACKING)
    with _OperationLock(root):
        current, current_hash = _hash_file_json(tracking_path)
        if current is None:
            raise ApplyError('model_status.json 唔存在')
        post_hash = (repair.get('postState') or {}).get('trackingHash')
        pre_hash = (repair.get('preState') or {}).get('trackingHash')
        if current_hash == post_hash:
            return {'tracking': 'post', 'noop': True, 'repairId': repair.get('repairId')}
        if current_hash != pre_hash:
            raise ApplyError('current tracking 唔係 repair pre/post image（fail closed）')
        validated, _ = merge_repair_effects(bundle, repair)
        expected = _apply_tracking(current, validated['effects'])
        if _canonical_hash(expected) != post_hash:
            raise ApplyError('repair 後 tracking hash 唔等於原 postState')
        _write_json(tracking_path, expected)
        if _canonical_hash(_read_json(tracking_path)) != post_hash:
            raise ApplyError('repair 寫入後回讀 hash 唔一致')
        return {'tracking': 'applied', 'noop': False, 'repairId': repair.get('repairId')}


def apply_bundle(bundle, *, repo_root, now=None, meta_saver=None, applied_at=None, repair=None, lock_held=False):
    """套用已驗證 bundle 到 repo_root 的四個本地檔。

    先做全檔 preflight（任何一個 file 係唔明 image 就零寫入 fail-closed），
    之後才逐檔 pre→post，最後寫 meta。回傳報告 dict。
    """
    root = os.path.abspath(repo_root)
    _validate_bundle(bundle)
    manifest = bundle['manifest']
    sr, repair_meta = merge_repair_effects(bundle, repair)
    new_snapshot = bundle['newSnapshot']

    snapshot_path = os.path.join(root, BIGGO_SNAPSHOT)
    tracking_path = os.path.join(root, TRACKING)
    blacklist_path = os.path.join(root, BLACKLIST)
    meta_path = os.path.join(root, META)

    lock_ctx = contextlib.nullcontext() if lock_held else _OperationLock(root)
    with lock_ctx:
        journal = _read_journal(root)
        if journal is not None and journal.get('bundleHash') != bundle['bundleHash']:
            raise ApplyError('存在另一個未完成 bundle 的 apply journal（衝突；零寫入）')
        # Capture the apply instant once, before the durable journal.  If a
        # journal already exists, its original instant is authoritative; a
        # retry/recovery may never move the successful time forward.
        if journal is not None:
            recorded = journal.get('appliedAt')
            if not isinstance(recorded, str):
                raise ApplyError('舊 journal 缺 appliedAt；拒絕以重試時間補造（需人手遷移）')
            apply_timestamp = _utc_timestamp(recorded)
        else:
            apply_timestamp = _utc_timestamp(applied_at if applied_at is not None else now)

        # ---- preflight（零寫入），全部目標內容先計算 ----
        current_snapshot, snapshot_hash = _hash_file_json(snapshot_path)
        if current_snapshot is None:
            current_snapshot = {}
            snapshot_hash = _canonical_hash(current_snapshot)
        if snapshot_hash == bundle['newSnapshotHash']:
            snapshot_plan = ('post', None)
        elif snapshot_hash == bundle['baseSnapshotHash']:
            snapshot_plan = ('pre', new_snapshot)
        else:
            raise ApplyError('biggo_prices.json pre/post image 唔 match'
                             '（needs manual reconciliation）')
        tracking_pre_hash = ((repair_meta or {}).get('preState') or {}).get(
            'trackingHash') or sr['preState']['trackingHash']
        tracking_plan = _plan_image(tracking_path, tracking_pre_hash,
                                    sr['postState']['trackingHash'],
                                    lambda cur: _apply_tracking(cur, sr['effects']),
                                    label=TRACKING)
        blacklist_plan = _plan_blacklist_file(blacklist_path,
                                              sr['preState']['blacklistHash'],
                                              sr['postState']['blacklistHash'],
                                              sr['effects'])
        try:
            meta = batch_utils.load_meta(meta_path)
        except batch_utils.MetaError as e:
            raise ApplyError(f'prices_meta.json 讀取失敗：{e}') from e
        staged_meta = _target_meta(meta, sr, manifest, now, applied_at=apply_timestamp)
        staged_meta['appliedBundleHash'] = bundle['bundleHash']
        staged_meta['appliedSnapshotHash'] = bundle['newSnapshotHash']
        staged_meta['appliedSnapshotId'] = _snapshot_identity(
            manifest, bundle['newSnapshotHash'])
        try:
            batch_utils._validate_meta(staged_meta, 'prices_meta')
        except batch_utils.MetaError as e:
            raise ApplyError(f'prices_meta 目標 schema 唔合格：{e}') from e
        staged_meta_hash = _canonical_hash(staged_meta)

        all_post = (snapshot_plan[0] == 'post' and tracking_plan[0] == 'post'
                    and blacklist_plan[0] == 'post')
        meta_applied = meta.get('appliedBundleHash') == bundle['bundleHash']
        if meta_applied:
            if not all_post:
                raise ApplyError('meta 已 applied 但本地檔案唔係 post image'
                                 '（needs manual reconciliation）')
            if journal is not None:
                _remove_journal(root)
            return {'snapshot': 'post', 'tracking': 'post', 'blacklist': 'post',
                    'meta': 'noop', 'noop': True, 'bundleHash': bundle['bundleHash'],
                    'repairId': (repair_meta or {}).get('repairId')}

        # Journal is written before any data write.  It is the recovery source
        # for a crash between files or between the last file and meta.
        if journal is None:
            journal = {
                'schemaVersion': 1,
                'bundleHash': bundle['bundleHash'],
                'stageResultHash': bundle['stageResultHash'],
                'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'appliedAt': apply_timestamp,
                'appliedSnapshotId': _snapshot_identity(manifest, bundle['newSnapshotHash']),
                'files': {
                    BIGGO_SNAPSHOT: {
                        'preHash': snapshot_hash, 'postHash': bundle['newSnapshotHash'],
                        'state': snapshot_plan[0]},
                    TRACKING: {
                        'preHash': tracking_pre_hash,
                        'postHash': sr['postState']['trackingHash'],
                        'state': tracking_plan[0]},
                    BLACKLIST: {
                        'preHash': sr['preState']['blacklistHash'],
                        'postHash': sr['postState']['blacklistHash'],
                        'state': blacklist_plan[0]},
                },
                'meta': {'postHash': staged_meta_hash, 'state': 'pending'},
                'repairId': (repair_meta or {}).get('repairId'),
            }
            _write_journal(root, journal)

        def _mark(name, state):
            journal['files'][name]['state'] = state
            _write_journal(root, journal)

        # ---- 寫入（preflight 全過才開始）----
        if snapshot_plan[0] == 'pre':
            _write_json(snapshot_path, snapshot_plan[1])
            _mark(BIGGO_SNAPSHOT, 'post')
        if tracking_plan[0] == 'pre':
            _write_json(tracking_path, tracking_plan[1])
            _mark(TRACKING, 'post')
        if blacklist_plan[0] == 'pre':
            _write_json(blacklist_path, blacklist_plan[1])
            _mark(BLACKLIST, 'post')

        # ---- meta 最後（recoverable commit point）----
        saver = meta_saver or batch_utils.save_meta
        try:
            saver(staged_meta, meta_path)
        except (batch_utils.MetaError, OSError) as e:
            raise ApplyError(f'prices_meta.json 寫入失敗（其餘檔案已係 post image）：{e}') from e
        # 回讀驗證：自訂 saver 亦不可靜默寫錯 schema／hash。
        try:
            written_meta = batch_utils.load_meta(meta_path)
        except batch_utils.MetaError as e:
            raise ApplyError(f'prices_meta.json 寫入後回讀失敗：{e}') from e
        if _canonical_hash(written_meta) != staged_meta_hash:
            raise ApplyError('prices_meta.json 寫入後 hash 唔等於已驗證目標')
        journal['meta']['state'] = 'written'
        _write_journal(root, journal)
        _remove_journal(root)
        return {
            'snapshot': 'post' if snapshot_plan[0] == 'post' else 'applied',
            'tracking': 'post' if tracking_plan[0] == 'post' else 'applied',
            'blacklist': 'post' if blacklist_plan[0] == 'post' else 'applied',
            'meta': 'written', 'noop': False, 'bundleHash': bundle['bundleHash'],
            'repairId': (repair_meta or {}).get('repairId'),
        }
