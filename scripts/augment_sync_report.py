#!/usr/bin/env python3
"""Bind a completed-sync machine report to this source run/head.

A missing report is not success.  The only fallback is a run-bound,
explicitly-marked upstream-blocked receipt when the receive step was skipped
because an earlier step failed.  A report that exists but is corrupt, or a
receive that actually ran without a valid report, remains fail-closed.
"""
import argparse
import json
import os
import re
import sys
import time


def _identity_ok(run_id, head, event):
    return (isinstance(run_id, str) and re.fullmatch(r'[0-9]+', run_id)
            and isinstance(head, str) and re.fullmatch(r'[0-9a-f]{40}', head)
            and event in ('schedule', 'workflow_dispatch'))


def _upstream_report(run_id, head, event, generated_at):
    if not _identity_ok(run_id, head, event):
        raise ValueError('invalid source identity')
    return {'schemaVersion': 2, 'mode': 'sync-completed',
            'generatedAt': generated_at, 'finishedAt': generated_at,
            'ok': False, 'applied': False, 'postStateVerified': False,
            'notAttempted': True, 'reason': 'upstream-blocked',
            'upstreamStatus': 'failure', 'plan': None, 'cycleId': None,
            'stage': None, 'sourceRunId': int(run_id), 'headSha': head,
            'sourceEvent': event}


def _atomic_write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', required=True)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--head', required=True)
    ap.add_argument('--event', required=True)
    ap.add_argument('--upstream-outcome', default=None)
    ap.add_argument('--job-status', default=None)
    ap.add_argument('--allow-upstream-fallback', action='store_true')
    ap.add_argument('--generated-at', default=None)
    args = ap.parse_args(argv)
    if not _identity_ok(args.run_id, args.head, args.event):
        print('❌ invalid source identity', file=sys.stderr)
        return 2
    generated_at = args.generated_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    exists = os.path.lexists(args.report)
    data = None
    if exists:
        try:
            with open(args.report, encoding='utf-8') as f:
                candidate = json.load(f)
            if (isinstance(candidate, dict)
                    and candidate.get('schemaVersion') == 2
                    and candidate.get('mode') == 'sync-completed'):
                data = candidate
        except (OSError, ValueError):
            data = None
    if data is not None:
        data.update({'sourceRunId': int(args.run_id), 'sourceEvent': args.event,
                     'headSha': args.head})
        try:
            _atomic_write(args.report, data)
        except OSError as e:
            print(f'❌ report write failed: {type(e).__name__}', file=sys.stderr)
            return 2
        return 0
    if exists:
        print('❌ sync report exists but is invalid; refusing fallback', file=sys.stderr)
        return 2
    if (args.allow_upstream_fallback and args.upstream_outcome == 'skipped'
            and args.job_status == 'failure'):
        try:
            _atomic_write(args.report, _upstream_report(
                args.run_id, args.head, args.event, generated_at))
        except OSError as e:
            print(f'❌ report write failed: {type(e).__name__}', file=sys.stderr)
            return 2
        return 0
    print('❌ sync report missing/invalid and no trusted upstream fallback', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
