#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precommit acceptance on an isolated, clean candidate snapshot.

Daily tests currently run before EMSD acquisition.  That order let a hardcoded
test count pass on yesterday's CSV, then fail in the Pages build after today's
CSV was pushed.  This tool copies the *current working candidate* (tracked files
plus the generated release allowlist) into a private temp directory, initialises
a clean git snapshot there, and runs the data/metadata/artifact acceptance suite
against exactly those bytes.  A non-zero result blocks commit/push.

It is intentionally not a deployment or network tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED = (
    'index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv', 'metadata.json',
    'official_batch_status.json',
)
DEFAULT_TESTS = (
    'tests/test_metadata_twostage.py',
    'tests/test_dynamic_counts.py',
    'tests/test_emsd_csv_lf.py',
    'tests/test_energy_distribution.py',
    'tests/test_payload_bytes_vs_index.py',
    'tests/test_pdf_metadata_guard.py',
    'tests/test_schema_validation.py',
    'tests/test_public_privacy.py',
    'tests/test_source_status_20261017.py',
)
IGNORE = shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', '.venv',
                                'node_modules', '.mypy_cache', '.ruff_cache', '*.pyc')


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _tracked_files(repo):
    proc = subprocess.run(['git', '-c', f'safe.directory={repo}', '-C', repo,
                           'ls-files', '-z', '--cached', '--others',
                           '--exclude-standard'], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError('git ls-files 失敗：' + proc.stderr.decode('utf-8', 'replace')[:300])
    return [p for p in proc.stdout.decode('utf-8', 'replace').split('\0') if p]


def _copy_candidate(repo, dest):
    copied = []
    for rel in _tracked_files(repo):
        src = os.path.join(repo, rel)
        if os.path.islink(src) or not os.path.isfile(src):
            continue
        if os.path.basename(rel) in ('.env', 'runtime.env') or rel.startswith('.git/'):
            continue
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    for rel in GENERATED:
        src = os.path.join(repo, rel)
        if os.path.isfile(src):
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            if rel not in copied:
                copied.append(rel)
    return copied


def _run(argv, cwd, log_path, timeout):
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
        rc = proc.returncode
        payload = (proc.stdout or b'') + (b'\n' if proc.stdout and proc.stderr else b'') \
            + (proc.stderr or b'')
    except subprocess.TimeoutExpired as e:
        rc = -2
        payload = (e.stdout or b'') + (e.stderr or b'') + b'\nTIMEOUT'
    except OSError as e:
        rc = -3
        payload = str(e).encode()
    with open(log_path, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    return {
        'argv': argv,
        'returncode': rc,
        'startedAt': started,
        'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'log': log_path,
        'logSha256': _sha256(log_path),
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='precommit isolated candidate acceptance')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--report', required=True)
    ap.add_argument('--log-dir', required=True)
    ap.add_argument('--suite', choices=['targeted', 'full'], default='full')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    report_path = os.path.abspath(args.report)
    log_dir = os.path.abspath(args.log_dir)
    os.makedirs(log_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='aircon-candidate-acceptance-')
    candidate = os.path.join(tmp, 'candidate')
    report = {
        'schemaVersion': 1,
        'tool': 'scripts/verify_candidate_acceptance.py',
        'sourceRepo': repo,
        'startedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'suite': args.suite,
        'commands': [],
        'ok': False,
    }
    try:
        # Clone local history, then overwrite with the exact working candidate.
        # Keeping the source commit as an ancestor is required by the rebuild
        # preflight tests (metadata.commit must be a real ancestor); a synthetic
        # single-commit init would silently fail those tests instead of testing
        # the real candidate.
        clone = subprocess.run(['git', 'clone', '--no-hardlinks', repo, candidate],
                               capture_output=True, text=True, timeout=120)
        if clone.returncode != 0:
            raise RuntimeError('local git clone 失敗：' + (clone.stderr or '')[:300])
        copied = _copy_candidate(repo, candidate)
        subprocess.run(['git', 'config', 'user.name', 'candidate-acceptance'], cwd=candidate,
                       check=True)
        subprocess.run(['git', 'config', 'user.email', 'candidate-acceptance@invalid'],
                       cwd=candidate, check=True)
        subprocess.run(['git', 'add', '-A'], cwd=candidate, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'isolated candidate snapshot'],
                       cwd=candidate, check=True, env={
                           **os.environ,
                           'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z',
                           'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z',
                       })
        try:
            with open(os.path.join(repo, 'metadata.json'), encoding='utf-8') as f:
                meta = json.load(f)
            report['datasetHash'] = meta.get('datasetHash')
            report['recordCount'] = meta.get('recordCount')
            report['registrationCount'] = meta.get('registrationCount')
            report['build'] = meta.get('build')
            report['commit'] = meta.get('commit')
        except (OSError, ValueError):
            pass
        report['copiedFiles'] = len(copied)
        report['candidateHead'] = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=candidate, capture_output=True,
            text=True).stdout.strip()
        all_ok = True
        if args.suite == 'full':
            # Complete candidate acceptance: governance, data, metadata, privacy,
            # full pytest (including browser smoke), feature registry run-tests,
            # and diff check, all on this isolated clean snapshot.
            inner_report = os.path.join(log_dir, 'acceptance.json')
            inner_logs = os.path.join(log_dir, 'acceptance-logs')
            entry = _run([sys.executable, 'scripts/run_acceptance.py',
                          '--report', inner_report, '--log-dir', inner_logs],
                         candidate, os.path.join(log_dir, 'FULL_ACCEPTANCE.log'),
                         args.timeout)
            entry['id'] = 'FULL_ACCEPTANCE'
            report['commands'].append(entry)
            print(f"{'✅' if entry['returncode'] == 0 else '❌'} FULL_ACCEPTANCE: rc={entry['returncode']}")
            if entry['returncode'] != 0:
                all_ok = False
            if os.path.isfile(inner_report):
                try:
                    with open(inner_report, encoding='utf-8') as f:
                        report['acceptance'] = json.load(f)
                except (OSError, ValueError):
                    report['acceptance'] = {'error': 'unreadable'}
        else:
            commands = [
                ('VALIDATE_METADATA', [sys.executable, 'scripts/validate_metadata.py', 'metadata.json']),
                ('VALIDATE_DATA', [sys.executable, 'validate_data.py']),
                ('PYTEST_NEW_DATA', [sys.executable, '-m', 'pytest', *DEFAULT_TESTS, '-q']),
            ]
            for name, cmd in commands:
                log_path = os.path.join(log_dir, f'{name}.log')
                entry = _run(cmd, candidate, log_path, args.timeout)
                entry['id'] = name
                report['commands'].append(entry)
                print(f"{'✅' if entry['returncode'] == 0 else '❌'} {name}: rc={entry['returncode']}")
                if entry['returncode'] != 0:
                    all_ok = False
        report['ok'] = all_ok
        return 0 if all_ok else 1
    except Exception as e:  # noqa: BLE001 - any setup failure must block commit
        report['error'] = f'{type(e).__name__}: {e}'
        return 1
    finally:
        report['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        try:
            os.makedirs(os.path.dirname(report_path) or '.', exist_ok=True)
            tmp_report = report_path + '.tmp'
            with open(tmp_report, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_report, report_path)
        except OSError as e:
            print(f'❌ candidate acceptance report 寫入失敗：{e}', file=sys.stderr)
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
