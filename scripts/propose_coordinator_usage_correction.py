#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only proposal for the lost stage4 smoke accounting delta."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402


def _raw(path):
    with open(path, 'rb') as f:
        return f.read()


def build(*, state_path, stage_result_path, manifest_path, source_commit,
          receipt_id, actual_token, actual_search):
    state_raw = _raw(state_path)
    sr_raw = _raw(stage_result_path)
    man_raw = _raw(manifest_path)
    state = json.loads(state_raw.decode('utf-8'))
    sr = json.loads(sr_raw.decode('utf-8'))
    manifest = json.loads(man_raw.decode('utf-8'))
    if state.get('status') != 'completed' or state.get('cycleId') != '2026-10-10:4/7' \
            or state.get('stage') != 4:
        raise ValueError('state must be completed 2026-10-10:4/7 stage4')
    if state.get('leaseOwner') is not None or state.get('intentAt') is not None \
            or state.get('callsMayHaveStarted'):
        raise ValueError('state still has lease/intent')
    if sr.get('cycleId') != state.get('cycleId') or sr.get('stage') != state.get('stage'):
        raise ValueError('stage-result cycle/stage mismatch')
    if sha256_id(sr_raw) != manifest.get('stageResultHash'):
        raise ValueError('manifest/stage-result hash mismatch')
    if not isinstance(source_commit, str) or len(source_commit) != 40 \
            or any(c not in '0123456789abcdef' for c in source_commit.lower()):
        raise ValueError('source commit invalid')
    if not isinstance(receipt_id, str) or not receipt_id.startswith('sha256:'):
        raise ValueError('receipt id invalid')
    if not (isinstance(actual_token, int) and actual_token >= 0
            and isinstance(actual_search, int) and actual_search >= 0):
        raise ValueError('actual counts invalid')
    before = state.get('requestAttempts') or {}
    owner = {'token': max(int(before.get('token', 0) or 0),
                          int((sr.get('requestAttempts') or {}).get('token', 0) or 0),
                          actual_token),
             'search': max(int(before.get('search', 0) or 0),
                           int((sr.get('requestAttempts') or {}).get('search', 0) or 0),
                           actual_search)}
    if owner == {'token': int(before.get('token', 0) or 0),
                 'search': int(before.get('search', 0) or 0)}:
        raise ValueError('no monotonic correction needed')
    proposal = {
        'schemaVersion': 1,
        'kind': 'coordinator-usage-correction-proposal-v1',
        'mode': 'read-only-proposal',
        'statePathHash': sha256_id(state_raw),
        'stateRawHash': 'sha256:' + hashlib.sha256(state_raw).hexdigest(),
        'stageResultRawHash': 'sha256:' + hashlib.sha256(sr_raw).hexdigest(),
        'manifestRawHash': 'sha256:' + hashlib.sha256(man_raw).hexdigest(),
        'cycleId': state.get('cycleId'),
        'stage': state.get('stage'),
        'before': {'token': int(before.get('token', 0) or 0),
                   'search': int(before.get('search', 0) or 0)},
        'proposedAfter': owner,
        'delta': {'token': owner['token'] - int(before.get('token', 0) or 0),
                  'search': owner['search'] - int(before.get('search', 0) or 0)},
        'stageResultCounts': sr.get('requestAttempts') or {},
        'actualEvidenceCounts': {'token': actual_token, 'search': actual_search},
        'sourceCommit': source_commit.lower(),
        'sourceReceiptId': receipt_id,
        'reason': 'renew preserved remote counters but lost unpersisted stage4 smoke accounting; '
                  'monotonic correction only',
    }
    proposal['proposalId'] = sha256_id(canonical_json_bytes(proposal))
    return proposal


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', required=True)
    ap.add_argument('--stage-result', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--source-commit', required=True)
    ap.add_argument('--receipt-id', required=True)
    ap.add_argument('--actual-token', type=int, required=True)
    ap.add_argument('--actual-search', type=int, required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        proposal = build(state_path=args.state, stage_result_path=args.stage_result,
                         manifest_path=args.manifest, source_commit=args.source_commit,
                         receipt_id=args.receipt_id,
                         actual_token=args.actual_token, actual_search=args.actual_search)
    except (OSError, ValueError) as e:
        print(f'❌ usage correction proposal: {e}', file=sys.stderr)
        return 2
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(proposal, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    print(json.dumps({k: proposal[k] for k in
                      ('proposalId', 'stateRawHash', 'before', 'proposedAfter',
                       'delta', 'mode')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
