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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def rel_files(repo):
    repo = Path(repo).resolve()
    for p in sorted(repo.rglob('*.py')):
        if any(part in SKIP_DIRS for part in p.relative_to(repo).parts):
            continue
        yield p.relative_to(repo).as_posix(), p


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


def module_candidates(rel):
    p = Path(rel)
    names = {p.stem, rel[:-3].replace('/', '.')}
    parts = p.parts
    if len(parts) > 1:
        names.add('.'.join(parts[:-1]) + '.' + p.stem)
        # scripts/ and tests/ are routinely on sys.path.
        names.add(p.stem)
    return names


def parse_imports(tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.append(node.module.split('.')[0])
    return sorted(set(found))


def load_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        return {'__error__': type(exc).__name__}


def workflow_facts(repo, by_name):
    rows = []
    wfdir = Path(repo) / '.github' / 'workflows'
    if not wfdir.is_dir():
        return rows
    for p in sorted(wfdir.glob('*.yml')) + sorted(wfdir.glob('*.yaml')):
        data = load_yaml(p)
        rel = p.relative_to(Path(repo)).as_posix()
        if '__error__' in data:
            rows.append({'workflow': rel, 'parse': 'invalid', 'entrypoints': []})
            continue
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
                refs = []
                for m in ENTRY_RE.finditer(run):
                    refs.append(m.group(1))
                # static script references in reviewed shell blocks (not import discovery)
                refs += [m.group(1) for m in SCRIPT_RE.finditer(run) if m.group(1).startswith('scripts/')]
                refs = sorted(set(refs))
                if refs:
                    steps.append({'job': job_name, 'step': step.get('name') or f'step{idx}', 'scripts': refs})
        rows.append({'workflow': rel, 'parse': 'ok', 'entrypoints': steps,
                     'schedule': data.get('on', {}).get('schedule') if isinstance(data.get('on'), dict) else None})
    return rows


def audit(repo, peer_root=None):
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f'repo directory not found: {repo.name}')
    py = {}
    by_candidate = {}
    for rel, path in rel_files(repo):
        try:
            text = path.read_text(encoding='utf-8')
            tree = ast.parse(text, filename=rel)
            imports = parse_imports(tree)
            size = len(text.encode('utf-8'))
            lines = len(text.splitlines())
            valid = True
        except Exception:
            imports, size, lines, valid = [], path.stat().st_size, 0, False
        item = {'path': rel, 'sha256': sha256_file(path), 'bytes': size, 'lines': lines,
                'group': group_for(rel), 'imports': imports, 'parse': 'ok' if valid else 'invalid'}
        py[rel] = item
        for name in module_candidates(rel):
            by_candidate.setdefault(name, []).append(rel)
    edges = []
    for rel, item in py.items():
        for name in item['imports']:
            candidates = by_candidate.get(name, [])
            # Prefer a same-directory module, then root/scripts, never a test.
            target = None
            here = Path(rel).parent.as_posix()
            for c in candidates:
                if Path(c).parent.as_posix() == here:
                    target = c
                    break
            if target is None:
                for c in candidates:
                    if c == f'{name}.py' or c == f'scripts/{name}.py':
                        target = c
                        break
            if target is None and candidates:
                target = candidates[0]
            if target and target != rel:
                edges.append({'from': rel, 'to': target, 'kind': 'observed_static_import', 'name': name})
    edges.sort(key=lambda x: (x['from'], x['to'], x['name']))
    callers = {}
    for e in edges:
        callers.setdefault(e['to'], []).append(e['from'])
    test_text = {}
    for rel, item in py.items():
        if item['group'] == 'tests_and_fixtures':
            test_text[rel] = (repo / rel).read_text(encoding='utf-8', errors='replace')
    tests_for = {}
    for rel in py:
        if py[rel]['group'] == 'tests_and_fixtures':
            continue
        needle = rel
        stem = Path(rel).stem
        hits = []
        for t, text in test_text.items():
            if needle in text or f"'{stem}'" in text or f'"{stem}"' in text or f'/{stem}.py' in text:
                hits.append(t)
        if hits:
            tests_for[rel] = hits
    wf = workflow_facts(repo, by_candidate)
    entrypoints = {}
    for row in wf:
        for step in row.get('entrypoints', []):
            for script in step['scripts']:
                entrypoints.setdefault(script, []).append({'workflow': row['workflow'], 'job': step['job'], 'step': step['step']})
    unknown = []
    for rel, item in py.items():
        if item['group'] == 'tests_and_fixtures':
            continue
        if not callers.get(rel) and rel not in entrypoints and not item['path'].startswith('scripts/'):
            unknown.append(rel)
    result = {
        'schemaVersion': 1,
        'kind': 'aircon-architecture-audit',
        'repoName': repo.name,
        'head': git_head(repo),
        'pythonFiles': len(py),
        'modules': sorted(py.values(), key=lambda x: x['path']),
        'edges': edges,
        'callers': callers,
        'testsFor': tests_for,
        'workflows': wf,
        'workflowEntrypoints': entrypoints,
        'declaredDynamicOrUnknown': sorted(unknown),
        'parseErrors': sorted(rel for rel, item in py.items() if item['parse'] != 'ok'),
        'outboundPacingRisk': outbound_risk(repo),
    }
    if peer_root:
        peer = Path(peer_root).resolve()
        if not peer.is_dir():
            raise FileNotFoundError(f'peer root not found: {peer.name}')
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
            'samePathDifferentHashCount': len([d for d in differences if not d['thisOnly'] and not d['peerOnly']]),
        }
    return result


def git_head(repo):
    try:
        out = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def outbound_risk(repo):
    """Static, reviewed facts only; no network or live state is inferred."""
    paths = ['fetch_biggo.py', 'scripts/biggo_stage_runner.py', 'scripts/biggo_smoke_diagnostic.py', 'scripts/biggo_test_probe.py']
    found = []
    for rel in paths:
        p = Path(repo) / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
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
        'reviewItem': 'Actual-send pacing must cover token/auth, search, smoke and both workers; smoke currently disables pace; auth may precede search pacing. Provider quota is unknown. This is not evidence that a delay fixes HTTP 429.',
        'notFixedInStage1': True,
    }


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
    except (OSError, ValueError) as exc:
        print(f'architecture audit failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if result.get('parseErrors'):
        print('architecture audit failed: invalid Python source: ' + ', '.join(result['parseErrors']), file=sys.stderr)
        return 2
    if args.output_dir:
        outdir = Path(args.output_dir)
        if outdir.exists():
            ap.error('--output-dir must not exist (no-clobber)')
        outdir.mkdir(parents=True)
        target = outdir / 'architecture-audit.json'
        target.write_text(text, encoding='utf-8')
        print(str(target))
    elif args.output:
        target = Path(args.output)
        if target.exists():
            ap.error('--output must not exist (no-clobber)')
        if not target.parent.exists():
            ap.error('--output parent must exist')
        target.write_text(text, encoding='utf-8')
        print(str(target))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
