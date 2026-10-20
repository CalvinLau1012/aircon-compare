#!/usr/bin/env python3
"""Create a run-bound BigGo failure receipt without claiming provider success.

- upstream-skipped: the provider step was skipped because an earlier workflow
  step failed.  providerPhaseEntered/lease are false and requests are zero.
- provider-phase-unknown: the provider step failed but no valid status exists;
  provider/lease/request facts are unknown (null), never zero/success.
An existing valid status is preserved unchanged.
"""
import argparse
import json
import os
import re
import sys
import time


def _valid_identity(run_id, head, event):
    return (isinstance(run_id, str) and re.fullmatch(r'[0-9]+', run_id)
            and isinstance(head, str) and re.fullmatch(r'[0-9a-f]{40}', head)
            and event in ('schedule', 'workflow_dispatch'))


def build(kind, run_id, head, event, generated_at):
    if not _valid_identity(run_id, head, event):
        raise ValueError('invalid run identity')
    if kind == 'upstream-skipped':
        return {'schemaVersion': 1, 'status': 'blocked-upstream', 'alert': True,
                'mode': 'upstream-workflow-failure', 'failureScope': 'upstream-workflow',
                'reason': 'upstream-workflow-failed', 'sourceRunId': int(run_id),
                'sourceEvent': event, 'headSha': head,
                'providerPhaseEntered': False, 'leaseAcquired': False,
                'published': False, 'imported': False, 'localApply': None,
                'requests': {'token': 0, 'search': 0}, 'generatedAt': generated_at}
    if kind == 'provider-phase-unknown':
        return {'schemaVersion': 1, 'status': 'blocked-provider-unknown', 'alert': True,
                'mode': 'provider-phase-failure', 'failureScope': 'provider-phase',
                'reason': 'provider-status-missing-or-invalid',
                'sourceRunId': int(run_id), 'sourceEvent': event, 'headSha': head,
                'providerPhaseEntered': None, 'leaseAcquired': None,
                'published': None, 'imported': None, 'localApply': None,
                'requests': None, 'generatedAt': generated_at}
    raise ValueError('invalid failure kind')


def _atomic_write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--kind', choices=('upstream-skipped', 'provider-phase-unknown'),
                    required=True)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--head', required=True)
    ap.add_argument('--event', required=True)
    ap.add_argument('--generated-at', default=None)
    a = ap.parse_args(argv)
    if not _valid_identity(a.run_id, a.head, a.event):
        print('❌ invalid run identity', file=sys.stderr)
        return 2
    if os.path.lexists(a.out):
        try:
            existing = json.load(open(a.out, encoding='utf-8'))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            return 0
        if a.kind == 'upstream-skipped':
            print('❌ existing status corrupt; refusing upstream fallback', file=sys.stderr)
            return 2
    generated = a.generated_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    try:
        _atomic_write(a.out, build(a.kind, a.run_id, a.head, a.event, generated))
    except (OSError, ValueError) as e:
        print(f'❌ failure status write failed: {type(e).__name__}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
