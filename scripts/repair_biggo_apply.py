#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only/guarded repair for the 2026-10-10 BigGo tracking mismatch.

Default (no --apply) is read-only: it reads an immutable coordinator bundle from
a local directory, re-reads the exact live four-file state, derives ONLY the
missing existing-record tracking upserts, proves the original postState hash is
reproduced, and emits an immutable repair receipt.  It never changes prices,
acquisition dates, cycle, queue, coordinator state, or the original bundle.

The receipt is a separate identity (repairId) bound to the original bundleHash
and stageResultHash.  Normal `biggo_apply.apply_bundle(..., repair=receipt)`
validates it and completes the original four-file transaction.  `--apply`
performs only the tracking repair, under the apply lock; it still requires the
original immutable bundle and receipt.  Root alone runs this on production.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BASE, SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import biggo_apply as apply_mod  # noqa: E402
import biggo_coordinator as coord  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id, sha256_json  # noqa: E402


class RepairError(RuntimeError):
    pass


def _read_json(path, label):
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        return json.loads(raw.decode('utf-8')), raw
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise RepairError(f'{label} 讀取失敗：{type(e).__name__}') from e


def load_bundle(bundle_dir):
    """Load and strictly validate a v2 immutable bundle from local files."""
    manifest, _ = _read_json(os.path.join(bundle_dir, 'manifest.json'), 'manifest.json')
    base, _ = _read_json(os.path.join(bundle_dir, 'base-biggo_prices.json'), 'base snapshot')
    new, _ = _read_json(os.path.join(bundle_dir, 'biggo_prices.json'), 'new snapshot')
    sr, _ = _read_json(os.path.join(bundle_dir, 'stage-result.json'), 'stage-result')
    if not isinstance(manifest, dict) or not isinstance(sr, dict):
        raise RepairError('bundle manifest／stage-result 必須係 object')
    if manifest.get('schemaVersion') != coord.BUNDLE_MANIFEST_SCHEMA_VERSION:
        raise RepairError('manifest schemaVersion 唔支援')
    for key in ('baseSnapshotHash', 'newSnapshotHash', 'stageResultHash'):
        if not isinstance(manifest.get(key), str):
            raise RepairError(f'manifest.{key} 缺失')
    if sha256_json(base) != manifest['baseSnapshotHash']:
        raise RepairError('base snapshot hash 唔 match manifest')
    if sha256_json(new) != manifest['newSnapshotHash']:
        raise RepairError('new snapshot hash 唔 match manifest')
    if sha256_json(sr) != manifest['stageResultHash']:
        raise RepairError('stage-result hash 唔 match manifest')
    if manifest.get('recordCount') != len(new):
        raise RepairError('manifest recordCount 唔 match new snapshot')
    try:
        coord.validate_stage_result(
            sr, base, new, cycle_id=manifest.get('cycleId'),
            stage=manifest.get('stage'), mode=manifest.get('mode'))
    except coord.CoordinatorStateError as e:
        raise RepairError(f'stage-result 驗證失敗：{e}') from e
    bundle = {
        'manifest': manifest, 'baseSnapshot': base, 'newSnapshot': new,
        'stageResult': sr,
        'baseSnapshotHash': manifest['baseSnapshotHash'],
        'newSnapshotHash': manifest['newSnapshotHash'],
        'stageResultHash': manifest['stageResultHash'],
        'bundleHash': sha256_json(manifest),
        'snapshotPath': (manifest.get('files') or {}).get('snapshot'),
    }
    return bundle


def load_current_state(repo_root):
    state = {}
    for name in (apply_mod.BIGGO_SNAPSHOT, apply_mod.TRACKING,
                 apply_mod.BLACKLIST, apply_mod.META):
        path = os.path.join(repo_root, name)
        obj, _ = _read_json(path, name)
        state[name] = obj
    return state


def _write_immutable(path, receipt):
    data = canonical_json_bytes(receipt)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            existing = f.read()
        if existing != data:
            raise RepairError('repair receipt 已存在且內容不同（immutable；拒絕覆寫）')
        return 'unchanged'
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return 'created'


def build_receipt(bundle, current_tracking, *, now=None):
    receipt = apply_mod.derive_tracking_repair(bundle, current_tracking)
    if receipt is None:
        return None
    receipt = copy.deepcopy(receipt)
    receipt['createdAt'] = time.strftime(
        '%Y-%m-%dT%H:%M:%SZ', time.gmtime(now if now is not None else time.time()))
    receipt['tool'] = 'scripts/repair_biggo_apply.py'
    receipt['immutable'] = True
    receipt['repairId'] = apply_mod.repair_id_for(receipt)
    return receipt


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='BigGo 2026-10-10 tracking repair (dry-run by default)')
    ap.add_argument('--bundle-dir', required=True)
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--out', required=True, help='immutable repair receipt path')
    ap.add_argument('--expect-bundle-hash', default=None)
    ap.add_argument('--expect-repair-id', default=None)
    ap.add_argument('--apply', action='store_true',
                    help='guarded apply of the tracking repair only (Root-only later)')
    ap.add_argument('--force', action='store_true',
                    help='allow --apply without --expect-repair-id (still requires immutable bundle)')
    args = ap.parse_args(argv)
    try:
        bundle = load_bundle(os.path.abspath(args.bundle_dir))
        if args.expect_bundle_hash and bundle['bundleHash'] != args.expect_bundle_hash:
            raise RepairError('bundleHash 同 --expect-bundle-hash 唔 match')
        current = load_current_state(os.path.abspath(args.repo))
        receipt = build_receipt(bundle, current[apply_mod.TRACKING])
        if receipt is None:
            print('✅ 已經係原 postState，無需 repair（零寫入）')
            return 0
        if args.expect_repair_id and receipt['repairId'] != args.expect_repair_id:
            raise RepairError('repairId 同 --expect-repair-id 唔 match')
        if args.apply and not args.expect_repair_id and not args.force:
            raise RepairError('--apply 必須同時提供 --expect-repair-id（或明示 --force）')
        out_path = os.path.abspath(args.out)
        if os.path.exists(out_path):
            # Reuse the already-written immutable receipt (its createdAt may
            # differ from this invocation); core identity must still match.
            existing, _ = _read_json(out_path, 'repair receipt')
            if isinstance(existing, dict) and apply_mod.repair_id_for(existing) == receipt['repairId'] \
                    and existing.get('repairId') == receipt['repairId'] \
                    and existing.get('originalBundleHash') == receipt['originalBundleHash']:
                receipt = existing
        action = _write_immutable(out_path, receipt)
        print(f"✅ repair receipt {action}: {os.path.abspath(args.out)}")
        print(f"   repairId={receipt['repairId']}")
        print(f"   originalBundleHash={receipt['originalBundleHash']}")
        print(f"   pre={receipt['preState']['trackingHash']}")
        print(f"   post={receipt['postState']['trackingHash']}")
        print(f"   upserts={sorted(receipt['trackingUpserts'])}")
        if args.apply:
            report = apply_mod.apply_repair(bundle, receipt, repo_root=os.path.abspath(args.repo))
            print(f"   applied_tracking={report['tracking']} noop={report['noop']}")
        return 0
    except (RepairError, apply_mod.ApplyError) as e:
        print(f'❌ repair 失敗（零猜測／零部分寫入）：{e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
