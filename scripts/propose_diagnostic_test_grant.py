#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only proposal builder for the fixed diagnostic test-grant ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import diagnostic_test_grant as grant  # noqa: E402


def build(auth_path):
    raw = open(auth_path, 'rb').read()
    try:
        auth_input = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as e:
        raise grant.GrantError('authorization JSON invalid') from e
    auth = grant.make_authorization(auth_input,
                                    'sha256:' + hashlib.sha256(raw).hexdigest())
    ledger = grant.empty_ledger(auth)
    state_path = os.environ.get('AIRCON_BIGGO_COORDINATOR_STATE_PATH') \
        or 'coordinator/biggo-state.json'
    ledger_path = grant.fixed_ledger_path(type('C', (), {'state_path': state_path})())
    return {'schemaVersion': 1,
            'kind': 'diagnostic-test-grant-proposal-v1',
            'grantId': auth['grantId'],
            'authorizationFileHash': auth['authorizationFileHash'],
            'ledgerPath': ledger_path,
            'hardMaximum': grant.HARD_MAXIMUM,
            'reserved': dict(ledger['reserved']),
            'actual': dict(ledger['actual']),
            'authorization': auth,
            'ledger': ledger,
            'mode': 'proposal-read-only',
            'applySupported': False}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--authorization', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        proposal = build(args.authorization)
    except (OSError, grant.GrantError) as e:
        print(f'❌ grant proposal: {e}', file=sys.stderr)
        return 2
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(proposal, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    print(json.dumps({k: proposal[k] for k in
                      ('grantId', 'authorizationFileHash', 'ledgerPath',
                       'hardMaximum', 'mode', 'applySupported')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
