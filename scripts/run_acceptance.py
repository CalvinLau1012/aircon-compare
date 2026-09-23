#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本機／CI 驗收 runner：逐 gate 直接取 subprocess return code，寫 machine report + logs。

- 每 gate 記錄固定 ID、exact argv（array）、開始／結束 UTC、真實 rc、log relative
  path、log SHA-256、Python 版本；
- 唔經 pipe（直接 subprocess.run），所以 rc 唔會被 pipe 掩蓋；
- 任何 gate 非零 → 整體非零，但仍寫出 failure report；
- report 寫入失敗亦非零；
- **CLI 只准固定 DEFAULT_GATES**（無 --spec）；測試自訂命令只可以直接呼叫
  `run_gates()`（trusted-code primitive），CLI 唔會執行任意 argv，因此唔會
  變成 live fetch 入口；
- gate ID 必須安全同 unique（^[A-Z][A-Z0-9_]*$），防止 log 路徑逃逸／覆寫；
- report／log dir 必須喺 repo 外（除非測試顯式 --allow-repo-paths）。

用法：
  python scripts/run_acceptance.py --report <path> --log-dir <dir>
  python scripts/run_acceptance.py --report <path> --log-dir <dir> --only PYTEST VALIDATE_DATA
退出碼：0 = 全部 rc=0；1 = 有 gate 非零、未知 --only、不安全 ID 或寫入失敗。
"""
import argparse
import hashlib
import json
import re
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable or 'python'

DEFAULT_GATES = [
    ('GOVERNANCE_EXTRACT', [PYTHON, os.path.join('scripts', 'extract_governance.py')]),
    ('VALIDATE_DATA', [PYTHON, 'validate_data.py']),
    ('VALIDATE_METADATA', [PYTHON, os.path.join('scripts', 'validate_metadata.py')]),
    ('PRIVACY_WORKTREE', [PYTHON, os.path.join('scripts', 'check_public_privacy.py'),
                          '--mode', 'worktree']),
    ('PYTEST', [PYTHON, '-m', 'pytest', 'tests/', '-q']),
    ('FEATURE_CHECK', [PYTHON, os.path.join('scripts', 'feature-check.py'), '--run-tests']),
    ('DIFF_CHECK', ['git', 'diff', '--check']),
]


def normalize_argv(argv):
    """將 interpreter path 正規化為 'python'、路徑分隔統一 '/'（machine report 可跨環境比對）。"""
    out = []
    for a in argv:
        if os.path.abspath(a) == os.path.abspath(PYTHON):
            out.append('python')
        else:
            out.append(a.replace('\\', '/'))
    return out


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run_gates(gates, log_dir, report_path, timeout=3600):
    ids = [gid for gid, _argv in gates]
    if len(ids) != len(set(ids)):
        raise ValueError(f'gate ID 重複：{ids}')
    for gid in ids:
        if not re.match(r'^[A-Z][A-Z0-9_]*$', gid):
            raise ValueError(f'不安全 gate ID：{gid!r}（只准 ^[A-Z][A-Z0-9_]*$）')
    os.makedirs(log_dir, exist_ok=True)
    report = {
        'schemaVersion': 1,
        'runner': 'scripts/run_acceptance.py',
        'startedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'python': sys.version.split()[0],
        'gates': [],
        'ok': False,
    }
    try:
        commit = subprocess.run(['git', '-C', BASE, 'rev-parse', 'HEAD'],
                                capture_output=True, text=True).stdout.strip()
        if commit:
            report['commit'] = commit
    except OSError:
        pass
    all_ok = True
    base_dir = os.path.dirname(os.path.abspath(report_path))
    for gate_id, argv in gates:
        started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        log_name = f'{gate_id}.log'
        log_path = os.path.join(log_dir, log_name)
        # 記錄相對 report 目錄嘅路徑（archive validator 由 reports 根解析）
        log_rel = os.path.relpath(log_path, base_dir).replace('\\', '/')
        run_argv = list(argv)
        junit_rel = None
        if gate_id == 'PYTEST':
            # 自動加 JUnit 報告（同一次 pytest 執行；argv 記錄保持 canonical）
            junit_rel = os.path.join('logs', 'pytest-junit.xml')
            run_argv = run_argv + ['--junitxml', os.path.join(log_dir, 'pytest-junit.xml')]
        try:
            proc = subprocess.run(run_argv, cwd=BASE, capture_output=True, timeout=timeout)
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
        entry = {
            'id': gate_id,
            'argv': normalize_argv(argv),
            'startedAt': started,
            'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'returncode': rc,
            'log': log_rel,
            'logSha256': _sha256_file(log_path),
        }
        if junit_rel:
            junit_path = os.path.join(log_dir, 'pytest-junit.xml')
            if os.path.isfile(junit_path):
                entry['junit'] = os.path.relpath(junit_path, base_dir).replace('\\', '/')
                entry['junitSha256'] = _sha256_file(junit_path)
        report['gates'].append(entry)
        print(f"{'✅' if rc == 0 else '❌'} {gate_id}: rc={rc} log={log_path}")
        if rc != 0:
            all_ok = False
    report['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    report['ok'] = all_ok
    try:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        tmp = report_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, report_path)
    except OSError as e:
        print(f'❌ machine report 寫入失敗：{e}', file=sys.stderr)
        return 1
    return 0 if all_ok else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='本機／CI 驗收 runner')
    ap.add_argument('--report', required=True)
    ap.add_argument('--log-dir', required=True)
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--allow-repo-paths', action='store_true',
                    help='測試用：容許 report／log 喺 repo 內（CI 唔應該用）')
    args = ap.parse_args(argv)
    gates = list(DEFAULT_GATES)
    if args.only:
        known = {g[0] for g in gates}
        unknown = [g for g in args.only if g not in known]
        if unknown:
            print(f'❌ --only 有未知 gate ID：{unknown}（拒絕部分忽略）', file=sys.stderr)
            return 1
        wanted = set(args.only)
        gates = [g for g in gates if g[0] in wanted]
        if not gates:
            print('❌ --only 冇符合嘅 gate', file=sys.stderr)
            return 1
    if not args.allow_repo_paths:
        root = os.path.realpath(BASE)
        for label, p in (('report', args.report), ('log-dir', args.log_dir)):
            real = os.path.realpath(os.path.abspath(p))
            try:
                inside = os.path.commonpath([root, real]) == root
            except ValueError:
                inside = False
            if inside:
                print(f'❌ {label} 必須喺 repo 外（{p}）；CI 唔應該用 repo 內路徑',
                      file=sys.stderr)
                return 1
    try:
        return run_gates(gates, args.log_dir, args.report)
    except ValueError as e:
        print(f'❌ acceptance runner 參數無效：{e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
