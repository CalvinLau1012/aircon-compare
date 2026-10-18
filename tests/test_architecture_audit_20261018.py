import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'architecture_audit.py'
SPEC = importlib.util.spec_from_file_location('architecture_audit_20261018', SCRIPT)
AA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AA)


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    return p


def repo(tmp_path):
    write(tmp_path, 'lib.py', 'VALUE = 1\n')
    write(tmp_path, 'main.py', 'import lib\n')
    write(tmp_path, 'tests/test_main.py', 'import main\n')
    return tmp_path


def test_known_static_importer_and_callers(tmp_path):
    r = repo(tmp_path)
    out = AA.audit(r)
    assert any(e['from'] == 'main.py' and e['to'] == 'lib.py' for e in out['edges'])
    assert 'main.py' in out['callers']['lib.py']
    assert out['parseErrors'] == []


def test_does_not_import_or_execute_production_module(tmp_path):
    r = repo(tmp_path)
    write(r, 'boom.py', 'raise RuntimeError("must not execute")\n')
    write(r, 'uses_boom.py', 'import boom\n')
    out = AA.audit(r)
    assert any(e['to'] == 'boom.py' for e in out['edges'])
    assert not any('must not execute' in str(x) for x in out.values())


def test_source_change_changes_hash_and_peer_diff(tmp_path):
    r = repo(tmp_path)
    a = AA.audit(r)
    write(r, 'lib.py', 'VALUE = 2\n')
    b = AA.audit(r)
    assert {m['path']: m['sha256'] for m in a['modules']}['lib.py'] != {m['path']: m['sha256'] for m in b['modules']}['lib.py']
    peer = repo(tmp_path / 'peer')
    c = AA.audit(r, peer)
    assert 'lib.py' in [x['path'] for x in c['peerComparison']['samePathDifferentHash']]


def test_invalid_python_exits_nonzero_without_fake_audit(tmp_path):
    r = repo(tmp_path)
    write(r, 'broken.py', 'def nope(:\n')
    out = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(r)], text=True, capture_output=True)
    assert out.returncode == 2
    assert 'invalid Python source' in out.stderr


def test_missing_repo_is_nonzero(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(tmp_path / 'absent')], text=True, capture_output=True)
    assert out.returncode == 2
    assert 'failed' in out.stderr


def test_output_no_clobber_and_relative_privacy(tmp_path):
    r = repo(tmp_path / 'repo')
    target = tmp_path / 'audit.json'
    out = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(r), '--output', str(target)], text=True, capture_output=True)
    assert out.returncode == 0, out.stderr
    text = target.read_text()
    assert str(r) not in text and '/tmp/' not in text and '/home/' not in text
    assert json.loads(text)['repoName'] == 'repo'
    again = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(r), '--output', str(target)], text=True, capture_output=True)
    assert again.returncode != 0 and 'no-clobber' in again.stderr


def test_output_dir_requires_new_dir(tmp_path):
    r = repo(tmp_path / 'repo')
    d = tmp_path / 'newdir'
    assert subprocess.run([sys.executable, str(SCRIPT), '--repo', str(r), '--output-dir', str(d)], capture_output=True).returncode == 0
    assert (d / 'architecture-audit.json').is_file()
    assert subprocess.run([sys.executable, str(SCRIPT), '--repo', str(r), '--output-dir', str(d)], capture_output=True).returncode != 0
