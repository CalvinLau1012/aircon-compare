"""Production Pages guard must reject every transitive generator source drift."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import postdeploy_check

SOURCES = (*postdeploy_check.REPRO_CODE_PATHS, 'requirements.txt', '空調對比報告.md')


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'source-closure-fixture')
    git(repo, 'config', 'user.email', 'source-closure@example.invalid')
    for rel in SOURCES:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture source: ' + rel + '\n', encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'fixture generator source')
    commit = git(repo, 'rev-parse', 'HEAD')
    (repo / 'metadata.json').write_text(json.dumps({'commit': commit}))
    return repo


def check(repo):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/check_generated_source_binding.py'),
                           '--repo', str(repo)], capture_output=True, text=True)


def test_same_generator_source_is_accepted(source_repo):
    result = check(source_repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('rel', SOURCES)
@pytest.mark.parametrize('mode', ['changed', 'missing'])
def test_each_generator_dependency_blocks_stale_pages(source_repo, rel, mode):
    path = source_repo / rel
    if mode == 'missing':
        path.unlink()
    else:
        path.write_text(path.read_text(encoding='utf-8') + 'changed bytes\n', encoding='utf-8')
    result = check(source_repo)
    assert result.returncode != 0, f'{rel} {mode} was wrongly accepted: {result.stdout}'
    assert rel in result.stdout + result.stderr
