# -*- coding: utf-8 -*-
"""R11: real demand writer + real stage CLI; transient lock never staged."""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_real_demand_ledger_stages_commits_and_survives_checkout(tmp_path):
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', BASE, str(repo)],
                   check=True, timeout=120)
    script = (
        'import sys, json; sys.path.insert(0, %r); import price_demand; '
        'price_demand.register_new_models(%r, {"models": [{"brand": "格力", '
        '"model": "STAGE-1", "first_seen": "2026-10-22", '
        '"first_seen_at": "2026-10-22T00:00:00Z"}]}, now=1792600000)'
        % (str(repo), str(repo / 'price_demand.json')))
    subprocess.run([sys.executable, '-c', script], check=True, timeout=60)
    assert (repo / '.locks').is_dir()
    assert not (repo / 'price_demand.json.lock').exists()
    run = subprocess.run([sys.executable, 'scripts/stage_artifacts.py',
                          '--repo', str(repo), '--dry-run'],
                         cwd=str(repo), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert 'price_demand.json.lock' not in run.stdout + run.stderr
    # Real stage + data-only child commit, then a checkout round-trip.
    subprocess.run([sys.executable, 'scripts/stage_artifacts.py', '--repo', str(repo)],
                   cwd=str(repo), check=True, timeout=60)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 't@e.invalid'],
                   check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 't'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'data child'], check=True)
    tracked = subprocess.run(['git', '-C', str(repo), 'ls-files', 'price_demand.json'],
                             capture_output=True, text=True, check=True).stdout
    assert tracked.strip() == 'price_demand.json'
    assert 'price_demand.json.lock' not in subprocess.run(
        ['git', '-C', str(repo), 'ls-files'], capture_output=True, text=True,
        check=True).stdout
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'HEAD~1'], check=True)
    assert not (repo / 'price_demand.json').exists()
    subprocess.run(['git', '-C', str(repo), 'checkout', '-q', head], check=True)
    doc = json.loads((repo / 'price_demand.json').read_text())
    assert doc['demands'][0]['key'] == 'GREE|STAGE1'
    assert doc['demands'][0]['status'] == 'pending'
