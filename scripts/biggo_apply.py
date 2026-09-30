#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo local apply（P0 stage bundle）。

只由 `scripts/biggo_stage_runner.py` 在 coordinator CAS status=completed 之後呼叫。

不變式：
  - 先做全檔 preflight：每個檔案 hash 必須明確等於 bundle 的 pre-image 或
    post-image，否則零寫入 fail-closed（交人手 reconciliation）；
  - preflight 全過之後才逐檔 pre→post，最後寫 `prices_meta.json`
    （記 appliedBundleHash／appliedSnapshotHash）；
  - 已應用同一 bundle 再呼叫係 no-op，唔會重複推進 stage；
  - 呢個係 recoverable apply，唔係四檔原子交易。
"""
from __future__ import annotations

import json
import os
import time

import batch_utils
from biggo_canonical import canonical_json_bytes, sha256_json

BIGGO_SNAPSHOT = 'biggo_prices.json'
TRACKING = 'model_status.json'
BLACKLIST = 'model_blacklist.json'
META = 'prices_meta.json'


class ApplyError(RuntimeError):
    pass


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ApplyError(f'{os.path.basename(path)} 讀取／解析失敗：{e}') from e


def _write_json(path, obj):
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(canonical_json_bytes(obj).decode('utf-8'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ApplyError(f'{os.path.basename(path)} 寫入失敗：{e}') from e


def _apply_tracking(tracking, effects):
    out = dict(tracking)
    for key in effects.get('trackingRemovals', []):
        out.pop(key, None)
    for key, rec in (effects.get('trackingUpserts') or {}).items():
        out[key] = rec
    return out


def _apply_blacklist(models, effects):
    out = dict(models)
    for key in effects.get('blacklistRemovals', []):
        out.pop(key, None)
    for key, rec in (effects.get('blacklistUpserts') or {}).items():
        out[key] = rec
    return out


def _plan_image(path, pre_hash, post_hash, transform, *, label):
    """單檔 preflight：確認 pre/post image，計算結果但唔寫檔。

    回傳 (status, new_obj)；status ∈ {'pre', 'post'}；'pre' 時 new_obj 係待寫內容。
    """
    current = _read_json(path, default=None)
    if current is None:
        raise ApplyError(f'{label} 檔案唔存在（唔可以聲稱 pre-image）')
    current_hash = sha256_json(current)
    if current_hash == post_hash:
        return 'post', None
    if current_hash != pre_hash:
        raise ApplyError(f'{label} pre/post image 唔 match（needs manual reconciliation）')
    new_obj = transform(current)
    if sha256_json(new_obj) != post_hash:
        raise ApplyError(f'{label} effects 重算結果同 post-state hash 唔一致')
    return 'pre', new_obj


def _plan_blacklist_file(path, pre_hash, post_hash, effects):
    wrapper = _read_json(path, default=None)
    if wrapper is None:
        raise ApplyError('model_blacklist.json 唔存在')
    models = wrapper.get('models') if isinstance(wrapper, dict) else None
    if not isinstance(models, dict):
        raise ApplyError('model_blacklist.json 冇 models object')
    current_hash = sha256_json(models)
    if current_hash == post_hash:
        return 'post', None
    if current_hash != pre_hash:
        raise ApplyError('model_blacklist.json pre/post image 唔 match'
                         '（needs manual reconciliation）')
    new_models = _apply_blacklist(models, effects)
    if sha256_json(new_models) != post_hash:
        raise ApplyError('blacklist effects 重算結果同 post-state hash 唔一致')
    new_wrapper = dict(wrapper)
    new_wrapper['models'] = new_models
    new_wrapper['updated'] = time.strftime('%Y-%m-%d')
    return 'pre', new_wrapper


def _target_meta(meta, sr, manifest, now):
    """由 bundle 計算新 meta（唔寫檔）；stage 只可前進，不可倒退。"""
    mode = manifest.get('mode')
    stage = manifest['stage']
    today = time.strftime('%Y-%m-%d', time.gmtime(now or time.time()))
    new_meta = dict(meta)
    if mode == 'price-batch':
        cycle = manifest.get('cycleId', '')
        date = cycle.split(':', 1)[0] if ':' in cycle else ''
        if meta.get('price_batch_start') != date:
            raise ApplyError('本地 price_batch_start 同 bundle cycle 唔 match')
        current = int(meta.get('price_batch_idx', 0) or 0)
        target = max(current, int(stage))
        new_meta['price_batch_idx'] = target
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
    return new_meta


def apply_bundle(bundle, *, repo_root, now=None, meta_saver=None):
    """套用已驗證 bundle 到 repo_root 的四個本地檔。

    先做全檔 preflight（任何一個 file 係唔明 image 就零寫入 fail-closed），
    之後才逐檔 pre→post，最後寫 meta。回傳報告 dict。
    """
    root = os.path.abspath(repo_root)
    manifest = bundle.get('manifest')
    sr = bundle.get('stageResult')
    new_snapshot = bundle.get('newSnapshot')
    if not isinstance(manifest, dict) or not isinstance(sr, dict) \
            or not isinstance(new_snapshot, dict):
        raise ApplyError('bundle 結構唔完整')
    if sha256_json(manifest) != bundle.get('bundleHash'):
        raise ApplyError('bundleHash 唔一致')
    if sha256_json(sr) != bundle.get('stageResultHash'):
        raise ApplyError('stageResultHash 唔一致')
    if sha256_json(sr.get('effects')) != sr.get('effectsHash'):
        raise ApplyError('effectsHash 唔一致')
    if sha256_json(new_snapshot) != bundle.get('newSnapshotHash'):
        raise ApplyError('newSnapshotHash 唔一致')

    snapshot_path = os.path.join(root, BIGGO_SNAPSHOT)
    tracking_path = os.path.join(root, TRACKING)
    blacklist_path = os.path.join(root, BLACKLIST)
    meta_path = os.path.join(root, META)

    pre_t = sr['preState']['trackingHash']
    post_t = sr['postState']['trackingHash']
    pre_b = sr['preState']['blacklistHash']
    post_b = sr['postState']['blacklistHash']

    # ---- preflight（零寫入）----
    current_snapshot = _read_json(snapshot_path, default={})
    current_hash = sha256_json(current_snapshot)
    if current_hash == bundle['newSnapshotHash']:
        snapshot_plan = ('post', None)
    elif current_hash == bundle['baseSnapshotHash']:
        snapshot_plan = ('pre', new_snapshot)
    else:
        raise ApplyError('biggo_prices.json pre/post image 唔 match'
                         '（needs manual reconciliation）')
    tracking_plan = _plan_image(tracking_path, pre_t, post_t,
                                lambda cur: _apply_tracking(cur, sr['effects']),
                                label='model_status.json')
    blacklist_plan = _plan_blacklist_file(blacklist_path, pre_b, post_b, sr['effects'])
    try:
        meta = batch_utils.load_meta(meta_path)
    except batch_utils.MetaError as e:
        raise ApplyError(f'prices_meta.json 讀取失敗：{e}') from e
    all_post = (snapshot_plan[0] == 'post' and tracking_plan[0] == 'post'
                and blacklist_plan[0] == 'post')
    if meta.get('appliedBundleHash') == bundle['bundleHash']:
        if not all_post:
            raise ApplyError('meta 已 applied 但本地檔案唔係 post image'
                             '（needs manual reconciliation）')
        return {'snapshot': 'post', 'tracking': 'post', 'blacklist': 'post',
                'meta': 'noop', 'noop': True, 'bundleHash': bundle['bundleHash']}

    # ---- 寫入（preflight 全過才開始）----
    if snapshot_plan[0] == 'pre':
        _write_json(snapshot_path, snapshot_plan[1])
    if tracking_plan[0] == 'pre':
        _write_json(tracking_path, tracking_plan[1])
    if blacklist_plan[0] == 'pre':
        _write_json(blacklist_path, blacklist_plan[1])

    # ---- meta 最後（recoverable commit point）----
    new_meta = _target_meta(meta, sr, manifest, now)
    new_meta['appliedBundleHash'] = bundle['bundleHash']
    new_meta['appliedSnapshotHash'] = bundle['newSnapshotHash']
    saver = meta_saver or batch_utils.save_meta
    try:
        saver(new_meta, meta_path)
    except (batch_utils.MetaError, OSError) as e:
        raise ApplyError(f'prices_meta.json 寫入失敗（其餘檔案已係 post image）：{e}') from e
    return {
        'snapshot': 'post' if snapshot_plan[0] == 'post' else 'applied',
        'tracking': 'post' if tracking_plan[0] == 'post' else 'applied',
        'blacklist': 'post' if blacklist_plan[0] == 'post' else 'applied',
        'meta': 'written', 'noop': False, 'bundleHash': bundle['bundleHash'],
    }
