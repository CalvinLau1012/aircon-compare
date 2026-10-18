#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only static architecture audit for the aircon candidate repos.

It uses AST, file hashes and workflow YAML.  It never imports or executes a
production module.  Paths in the output are repo-relative.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache', 'node_modules', 'venv', '.venv'}
ENTRY_RE = re.compile(r'(?:python(?:3(?:\.\d+)?)?|py)\s+(?:-m\s+)?([A-Za-z0-9_./-]+\.py)\b')
SCRIPT_RE = re.compile(r'(?<![\w/])((?:scripts/)?[A-Za-z0-9_./-]+\.py)\b')


class AuditError(ValueError):
    pass


def sha256_file(path):
    p = Path(path)
    if p.is_symlink():
        raise AuditError('symlink source rejected')
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def rel_python_files(repo):
    repo = Path(repo)
    for root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            p = Path(root) / name
            if p.is_symlink():
                raise AuditError('symlink source rejected')
            yield p.relative_to(repo).as_posix(), p


def workflow_files(repo):
    wfdir = Path(repo) / '.github' / 'workflows'
    if not wfdir.is_dir():
        return []
    out = []
    for p in sorted(wfdir.glob('*.yml')) + sorted(wfdir.glob('*.yaml')):
        if p.is_symlink():
            raise AuditError('symlink workflow rejected')
        out.append((p.relative_to(Path(repo)).as_posix(), p))
    return out


def group_for(rel):
    stem = Path(rel).stem
    parent = Path(rel).parts[0]
    if rel.startswith('tests/') or rel.startswith('tests\\'):
        return 'tests_and_fixtures'
    if parent in {'migration', 'docker', 'production', 'r24p'}:
        return 'legacy_deployment_unknown'
    if stem.startswith(('fetch_', 'crawl_', 'emsd_', 'compare_')) or stem in {'crawl_utils', 'import_gemini_csv'}:
        return 'acquisition_and_normalisation'
    if stem.startswith(('biggo_', 'price_', 'batch_', 'queue_', 'advance_queue', 'repair_biggo', 'sync_biggo', 'verify_biggo', 'accept_shared', 'plan_price', 'audit_price', 'diagnostic_test', 'propose_coordinator', 'propose_diagnostic')):
        return 'pricing_biggo_coordination'
    if stem in {'check_freshness', 'freshness_issue', 'check_health_summary', 'source_health_monitor', 'source_status', 'monitor_presentation', 'render_monitor_summary', 'check_price_freshness'}:
        return 'monitoring_presentation'
    if stem.startswith(('generate_', 'stage_artifacts', 'make_fixture', 'make_biggo', 'archive_', 'build_pages', 'dispatch_pages', 'postdeploy', 'verify_deploy', 'verify_snapshot', 'verify_candidate', 'run_acceptance', 'check_payload', 'check_generated', 'augment_sync', 'render_', 'prepare_runtime')):
        return 'publication_release'
    if stem.startswith(('validate_', 'feature-check', 'extract_governance', 'check_public', 'pytest_evidence')) or stem in {'verify_candidate_acceptance'}:
        return 'governance_validation'
    if stem in {'atomic_release', 'price_cycle', 'price_cycle_entry', 'ranking_contract', 'reconcile_official_queue', 'repair_biggo_applied_time', 'private_raw_sink', 'biggo_rollback_guard'}:
        return 'private_extensions'
    return 'runtime_support'


def canonical_module(rel):
    p = Path(rel)
    if p.name == '__init__.py':
        parts = p.parts[:-1]
    else:
        parts = p.parts[:-1] + (p.stem,)
    return '.'.join(parts)


def module_aliases(rel):
    canonical = canonical_module(rel)
    aliases = {canonical} if canonical else set()
    parts = Path(rel).parts
    if len(parts) > 1 and parts[0] in {'scripts', 'tests'}:
        aliases.add('.'.join(parts[1:-1] + (Path(rel).stem,)))
    return {a for a in aliases if a}


def parse_imports(tree):
    """Return full (level, module) import specs; never collapse dotted names."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append({'level': 0, 'module': alias.name})
        elif isinstance(node, ast.ImportFrom):
            out.append({'level': int(node.level or 0), 'module': node.module or '',
                        'names': [a.name for a in node.names]})
    # Keep deterministic order and remove exact duplicates; names are retained.
    unique = {}
    for spec in out:
        unique[(spec['level'], spec['module'], tuple(spec.get('names', ())))] = spec
    return [unique[k] for k in sorted(unique)]


def _read_text(path):
    if Path(path).is_symlink():
        raise AuditError('symlink source rejected')
    return Path(path).read_text(encoding='utf-8')


def load_yaml(path):
    """Return (mapping, None) or (None, error_code); do not echo YAML."""
    try:
        data = yaml.safe_load(_read_text(path))
    except Exception as exc:
        return None, type(exc).__name__
    if not isinstance(data, dict):
        return None, 'not-a-mapping'
    return data, None


def workflow_facts(repo):
    rows = []
    for rel, p in workflow_files(repo):
        data, err = load_yaml(p)
        if err is not None:
            rows.append({'workflow': rel, 'parse': 'invalid', 'error': err,
                         'entrypoints': [], 'schedule': None})
            continue
        on = data['on'] if 'on' in data else data.get(True)
        schedule = on.get('schedule') if isinstance(on, dict) else None
        steps = []
        jobs = data.get('jobs') if isinstance(data.get('jobs'), dict) else {}
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            for idx, step in enumerate(job.get('steps') or []):
                if not isinstance(step, dict):
                    continue
                run = step.get('run')
                if not isinstance(run, str):
                    continue
                refs = [m.group(1) for m in ENTRY_RE.finditer(run)]
                refs += [m.group(1) for m in SCRIPT_RE.finditer(run)
                         if m.group(1).startswith('scripts/')]
                refs = sorted(set(refs))
                if refs:
                    steps.append({'job': job_name,
                                  'step': step.get('name') or f'step{idx}',
                                  'scripts': refs})
        rows.append({'workflow': rel, 'parse': 'ok', 'error': None,
                     'entrypoints': steps, 'schedule': schedule})
    return rows


def _resolve_spec(spec, importer, by_module):
    level = spec['level']
    module = spec['module']
    if level:
        base = list(Path(importer).parent.parts)
        up = level - 1
        if up > len(base):
            return None, f'.{"." * level}{module}'
        base = base[:len(base) - up] if up else base
        names = ['.'.join(base + ([module] if module else []))]
        if not module:
            # `from . import util` can resolve a submodule through callers;
            # the import itself does not name it without the alias.
            names = ['.'.join(base)]
        else:
            names = ['.'.join(base + [module])]
    else:
        names = [module] if module else []
    if level and not module:
        base_name = '.'.join(base)
        names = ['.'.join([base_name, n]) for n in spec.get('names', []) if n != '*'] or [base_name]
    if not names or not names[0]:
        return None, f'.{"." * level}{module}'
    for name in names:
        candidates = by_module.get(name, [])
        if candidates:
            # Never prefer a test module over production; deterministic first.
            candidates = sorted(candidates, key=lambda x: (x.startswith('tests/'), x))
            return candidates[0], name
    # `from pkg import util` may name a submodule rather than an attribute.
    if not level and module:
        for alias in spec.get('names', []):
            if alias == '*':
                continue
            name = module + '.' + alias
            candidates = by_module.get(name, [])
            if candidates:
                candidates = sorted(candidates, key=lambda x: (x.startswith('tests/'), x))
                return candidates[0], name
    return None, names[0]


def _read_workflow_entrypoints(rows):
    entry = {}
    for row in rows:
        for step in row.get('entrypoints', []):
            for script in step['scripts']:
                entry.setdefault(script, []).append(
                    {'workflow': row['workflow'], 'job': step['job'], 'step': step['step']})
    return entry


def audit(repo, peer_root=None):
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise FileNotFoundError('repo directory not found')
    py = {}
    by_module = {}
    for rel, path in rel_python_files(repo):
        try:
            text = _read_text(path)
            tree = ast.parse(text, filename=rel)
            imports = parse_imports(tree)
            size = len(text.encode('utf-8'))
            lines = len(text.splitlines())
            valid = True
        except AuditError:
            raise
        except Exception:
            imports, size, lines, valid = [], path.lstat().st_size, 0, False
        item = {'path': rel, 'sha256': sha256_file(path), 'bytes': size, 'lines': lines,
                'group': group_for(rel), 'imports': imports,
                'parse': 'ok' if valid else 'invalid'}
        py[rel] = item
        for name in module_aliases(rel):
            by_module.setdefault(name, []).append(rel)
    edges, unresolved, external = [], [], []
    repo_tops = {str(x).split('.', 1)[0] for x in by_module}
    for rel, item in py.items():
        if item['parse'] != 'ok':
            continue
        for spec in item['imports']:
            target, name = _resolve_spec(spec, rel, by_module)
            if target and target != rel:
                edges.append({'from': rel, 'to': target, 'kind': 'observed_static_import',
                              'name': name, 'level': spec['level']})
            elif not target:
                top = str(name).split('.', 1)[0]
                if top in repo_tops:
                    unresolved.append({'from': rel, 'kind': 'unresolved_static_import',
                                       'name': name, 'level': spec['level']})
                else:
                    external.append(name)
    edges.sort(key=lambda x: (x['from'], x['to'], x['name']))
    callers = {}
    for e in edges:
        bucket = callers.setdefault(e['to'], [])
        if e['from'] not in bucket:
            bucket.append(e['from'])
    production_callers = {
        k: sorted(v) for k, v in callers.items()
        if any(not x.startswith('tests/') for x in v)
    }
    test_callers = {
        k: sorted(x for x in v if x.startswith('tests/')) for k, v in callers.items()
        if any(x.startswith('tests/') for x in v)
    }
    for k, v in production_callers.items():
        production_callers[k] = [x for x in v if not x.startswith('tests/')]
    # Static test references are associations, never evidence that tests ran.
    tests_for = {}
    for rel in py:
        if py[rel]['group'] == 'tests_and_fixtures':
            continue
        stem = Path(rel).stem
        hits = []
        for t, item in py.items():
            if item['group'] != 'tests_and_fixtures':
                continue
            text = _read_text(repo / t)
            if rel in text or f"'{stem}'" in text or f'"{stem}"' in text:
                hits.append(t)
        if hits:
            tests_for[rel] = sorted(hits)
    wf = workflow_facts(repo)
    workflow_errors = [r['workflow'] for r in wf if r['parse'] != 'ok']
    entrypoints = _read_workflow_entrypoints(wf)
    unknown = []
    for rel, item in py.items():
        if item['group'] == 'tests_and_fixtures':
            continue
        if not callers.get(rel) and rel not in entrypoints and not rel.startswith('scripts/'):
            unknown.append(rel)
    result = {
        'schemaVersion': 2,
        'kind': 'aircon-architecture-audit',
        'repoName': repo.name,
        'head': git_head(repo),
        'pythonFiles': len(py),
        'modules': sorted(py.values(), key=lambda x: x['path']),
        'edges': edges,
        'unresolvedStaticImports': sorted(unresolved, key=lambda x: (x['from'], x['name'])),
        'externalImports': sorted(set(external)),
        'callers': {k: sorted(v) for k, v in callers.items()},
        'productionCallers': production_callers,
        'testCallers': test_callers,
        'testsFor': tests_for,
        'workflows': wf,
        'workflowEntrypoints': entrypoints,
        'workflowParseErrors': workflow_errors,
        'declaredDynamicOrUnknown': sorted(unknown),
        'parseErrors': sorted(rel for rel, item in py.items() if item['parse'] != 'ok'),
        'outboundPacingRisk': outbound_risk(repo),
    }
    if peer_root:
        peer = Path(peer_root).resolve()
        if not peer.is_dir():
            raise FileNotFoundError('peer root not found')
        presult = audit(peer)
        this = {m['path']: m for m in result['modules']}
        other = {m['path']: m for m in presult['modules']}
        allp = sorted(set(this) | set(other))
        differences = []
        for p in allp:
            a, b = this.get(p), other.get(p)
            if a and b and a['sha256'] == b['sha256']:
                continue
            differences.append({'path': p, 'thisSha': a['sha256'] if a else None,
                                'peerSha': b['sha256'] if b else None,
                                'thisOnly': not b, 'peerOnly': not a})
        result['peerComparison'] = {
            'peerName': peer.name,
            'peerHead': presult['head'],
            'thisOnly': sorted(p for p in allp if p in this and p not in other),
            'peerOnly': sorted(p for p in allp if p in other and p not in this),
            'samePathDifferentHash': differences,
            'samePathSameHashCount': len(allp) - len(differences),
            'samePathDifferentHashCount': len([d for d in differences
                                               if not d['thisOnly'] and not d['peerOnly']]),
        }
    return result


def git_head(repo):
    try:
        out = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def outbound_risk(repo):
    """Static, reviewed facts only; no network or live state is inferred."""
    paths = ['fetch_biggo.py', 'scripts/biggo_stage_runner.py',
             'scripts/biggo_smoke_diagnostic.py', 'scripts/biggo_test_probe.py']
    found = []
    for rel in paths:
        p = Path(repo) / rel
        if p.is_symlink():
            raise AuditError('symlink source rejected')
        if not p.is_file():
            continue
        text = _read_text(p)
        found.append({
            'path': rel,
            'sha256': sha256_file(p),
            'hasMinPace': 'BIGGO_MIN_PACE' in text,
            'hasUsePaceFalse': 'use_pace=False' in text,
            'authBeforeSearchPaceReview': ('_get_access_token' in text and '_wait_pace' in text),
        })
    return {
        'status': 'UNKNOWN/REVIEW_REQUIRED',
        'facts': found,
        'reviewItem': ('Actual-send pacing must cover token/auth, search, smoke and both workers; '
                       'smoke currently disables pace; auth may precede search pacing. Provider '
                       'quota is unknown. This is not evidence that a delay fixes HTTP 429.'),
        'notFixedInStage1': True,
    }


def _write_exclusive(path, text):
    # 'x' is atomic exclusive creation and refuses an existing dangling symlink.
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--peer-root')
    ap.add_argument('--output')
    ap.add_argument('--output-dir')
    args = ap.parse_args(argv)
    if args.output and args.output_dir:
        ap.error('--output and --output-dir are mutually exclusive')
    try:
        result = audit(args.repo, args.peer_root)
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        if result.get('parseErrors') or result.get('workflowParseErrors'):
            print('architecture audit failed: invalid source or workflow', file=sys.stderr)
            return 2
        if args.output_dir:
            outdir = Path(args.output_dir)
            try:
                outdir.mkdir(parents=False, exist_ok=False)
                _write_exclusive(outdir / 'architecture-audit.json', text)
            except OSError:
                print('architecture audit failed: output refused', file=sys.stderr)
                return 2
            print(str(outdir / 'architecture-audit.json'))
        elif args.output:
            try:
                _write_exclusive(Path(args.output), text)
            except OSError:
                print('architecture audit failed: output refused', file=sys.stderr)
                return 2
            print(str(args.output))
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, AuditError):
        print('architecture audit failed: source or repository invalid', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
