#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only per-key reconciliation receipt for two deployment data sets.

This is deliberately NOT an auto-merge tool.  It records every key and the safe
action: the active writer keeps its own lifecycle/price entries; the other side
is never overwritten; any cross-side transition must go through a verified
completed bundle (+ repair receipt) and the normal atomic apply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
from biggo_canonical import sha256_json  # noqa: E402

FILES = ('biggo_prices.json', 'model_status.json', 'model_blacklist.json')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _blacklist(obj):
    return obj.get('models') if isinstance(obj, dict) and 'models' in obj else obj


def build(active_dir, other_dir, *, active_role='server'):
    receipt = {
        'schemaVersion': 1,
        'activeRole': active_role,
        'safeToAutoMerge': False,
        'forbidden': ['whole-server overwrite', 'copy other queue', 'guess baseline'],
        'requiredPath': 'verified completed bundle + immutable repair receipt + normal atomic apply',
        'files': {},
    }
    for name in FILES:
        left = _load(os.path.join(active_dir, name))
        right = _load(os.path.join(other_dir, name))
        if name == 'model_blacklist.json':
            left, right = _blacklist(left), _blacklist(right)
        keys = sorted(set(left) | set(right))
        entries = {}
        counts = {'same': 0, 'activeOnly': 0, 'otherOnly': 0, 'valueChanged': 0}
        for key in keys:
            if key in left and key in right:
                if left[key] == right[key]:
                    cls, action = 'same', 'none'
                else:
                    cls, action = 'valueChanged', 'keep-active; require verified bundle for any change'
                counts[cls] += 1
            elif key in left:
                cls, action = 'activeOnly', 'keep-active; never delete by other side'
                counts['activeOnly'] += 1
            else:
                cls, action = 'otherOnly', 'review-only; do not auto-add; use verified bundle'
                counts['otherOnly'] += 1
            entries[key] = {'class': cls, 'action': action,
                            'activeHash': hashlib.sha256(
                                json.dumps(left.get(key), sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
                            if key in left else None,
                            'otherHash': hashlib.sha256(
                                json.dumps(right.get(key), sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
                            if key in right else None}
        receipt['files'][name] = {
            'counts': counts,
            'activeHash': sha256_json(left),
            'otherHash': sha256_json(right),
            'keys': entries,
        }
    receipt['receiptId'] = 'sha256:' + hashlib.sha256(
        json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return receipt


def main(argv=None):
    ap = argparse.ArgumentParser(description='read-only per-key reconciliation plan')
    ap.add_argument('--active', required=True)
    ap.add_argument('--other', required=True)
    ap.add_argument('--active-role', default='server')
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        receipt = build(args.active, args.other, active_role=args.active_role)
    except (OSError, ValueError) as e:
        print(f'❌ reconciliation 預演失敗：{e}', file=sys.stderr)
        return 1
    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(receipt, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps({name: data['counts'] for name, data in receipt['files'].items()},
                     ensure_ascii=False))
    print('receiptId=' + receipt['receiptId'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
