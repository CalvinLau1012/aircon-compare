#!/usr/bin/env python3
"""Emit reviewed non-secret GitHub price policy to GITHUB_ENV.

Policy is data, separate from the fixed code revision acknowledged by both
writers. It cannot replace source-closure or coordinator transition checks.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

FIELDS = {'schemaVersion', 'queryMode', 'cycleEnabled', 'sharedCycle',
          'sourceRevision', 'writer'}

def policy_environment(doc):
    if not isinstance(doc, dict) or set(doc) != FIELDS or doc['schemaVersion'] != 1:
        raise ValueError('invalid-price-policy-schema')
    mode = doc['queryMode']
    ce, sc = doc['cycleEnabled'], doc['sharedCycle']
    if mode not in ('normal', 'receive-only') or type(ce) is not bool \
            or type(sc) is not bool or ce != sc or doc['writer'] != 'github':
        raise ValueError('invalid-price-policy-mode')
    rev = doc['sourceRevision']
    if not isinstance(rev, str) or (rev and not re.fullmatch('[0-9a-f]{40}', rev)):
        raise ValueError('invalid-price-policy-revision')
    if mode == 'normal' and (not ce or not rev):
        raise ValueError('normal-price-policy-requires-shared-source')
    return {'AIRCON_PRICE_QUERY_MODE': mode,
            'AIRCON_PRICE_CYCLE_ENABLED': '1' if ce else '0',
            'AIRCON_PRICE_SHARED_CYCLE': '1' if sc else '0',
            'AIRCON_PRICE_SOURCE_REVISION': rev,
            'AIRCON_PRICE_WRITER': 'github'}

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--policy', default='price-policy.json')
    args = ap.parse_args(argv)
    try:
        path = Path(args.policy)
        doc = json.loads(path.read_text()) if path.exists() else {
            'schemaVersion': 1, 'queryMode': 'receive-only',
            'cycleEnabled': False, 'sharedCycle': False,
            'sourceRevision': '', 'writer': 'github'}
        env = policy_environment(doc)
    except (OSError, ValueError, KeyError) as exc:
        print('price policy failed: ' + str(exc), file=sys.stderr)
        return 2
    for key, value in env.items():
        print(key + '=' + value)
    return 0

if __name__ == '__main__':
    sys.exit(main())
