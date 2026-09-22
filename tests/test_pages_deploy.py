# -*- coding: utf-8 -*-
"""D2-A：GitHub Pages Actions 統一部署安全契約。"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SPEC = importlib.util.spec_from_file_location(
    'build_pages_artifact_mod', os.path.join(BASE, 'scripts', 'build_pages_artifact.py'))
bpa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bpa)

WORKFLOWS = os.path.join(BASE, '.github', 'workflows')


def _load(name):
    with open(os.path.join(WORKFLOWS, name), encoding='utf-8') as f:
        return yaml.safe_load(f)


def _text(name):
    return open(os.path.join(WORKFLOWS, name), encoding='utf-8').read()


def _make_repo(tmp_path, files=('index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv')):
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(('content:' + rel).encode('utf-8'))
    manifest = tmp_path / 'deploy_payload.json'
    manifest.write_text(json.dumps({'files': list(files)}), encoding='utf-8')
    return manifest


def test_pages_artifact_contains_exact_manifest_files(tmp_path):
    manifest = _make_repo(tmp_path)
    out = tmp_path / '_site'
    result = bpa.build(str(tmp_path), str(manifest), str(out))
    assert result['ok'] is True
    expected = {'index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv'}
    actual = {os.path.relpath(os.path.join(r, n), out)
              for r, _d, ns in os.walk(out) for n in ns}
    assert actual == expected
    for rel in expected:
        assert (out / rel).read_bytes() == (tmp_path / rel).read_bytes()


@pytest.mark.parametrize('bad', ['../escape.html', '/abs.html', 'a\\b.html', 'a/../b.html',
                                 'a//b.html', '   x.html'])
def test_pages_artifact_rejects_bad_paths(tmp_path, bad):
    manifest = tmp_path / 'deploy_payload.json'
    manifest.write_text(json.dumps({'files': [bad]}), encoding='utf-8')
    with pytest.raises(ValueError):
        bpa.build(str(tmp_path), str(manifest), str(tmp_path / '_site'))


def test_pages_artifact_rejects_symlink(tmp_path):
    _make_repo(tmp_path)
    try:
        (tmp_path / 'link.html').symlink_to(tmp_path / 'index.html')
    except OSError as e:
        pytest.skip(f'平台唔支援建立 symlink：{e}')
    manifest = tmp_path / 'deploy_payload.json'
    manifest.write_text(json.dumps({'files': ['link.html']}), encoding='utf-8')
    with pytest.raises(ValueError, match='symlink'):
        bpa.build(str(tmp_path), str(manifest), str(tmp_path / '_site'))


def test_pages_artifact_rejects_missing_and_directory(tmp_path):
    manifest = tmp_path / 'deploy_payload.json'
    manifest.write_text(json.dumps({'files': ['missing.html']}), encoding='utf-8')
    with pytest.raises(ValueError, match='唔存在'):
        bpa.build(str(tmp_path), str(manifest), str(tmp_path / '_site'))
    (tmp_path / 'dir').mkdir()
    manifest.write_text(json.dumps({'files': ['dir']}), encoding='utf-8')
    with pytest.raises(ValueError, match='普通檔案'):
        bpa.build(str(tmp_path), str(manifest), str(tmp_path / '_site'))


def test_pages_deploy_workflow_contract():
    wf = _load('pages-deploy.yml')
    assert wf['name'] == 'Pages 部署（Actions）'
    on = wf[True] if True in wf else wf['on']
    assert 'pull_request' in on
    assert on['push']['branches'] == ['master']
    build = wf['jobs']['build']
    deploy = wf['jobs']['deploy']
    assert deploy['if'] == "github.event_name == 'push' && github.ref == 'refs/heads/master'"
    assert deploy['needs'] == 'build'
    assert deploy['environment']['name'] == 'github-pages'
    assert deploy['permissions'] == {'pages': 'write', 'id-token': 'write'}
    # PR 永不 deploy：deploy job 條件只允許 master push
    assert "pull_request" not in deploy['if']
    # 完整 gates 先於 artifact 建立
    names = [s.get('name', '') for s in build['steps']]
    i_gates = next(i for i, n in enumerate(names) if '完整本機 gates' in n)
    i_build = next(i for i, n in enumerate(names) if '建立 Pages artifact' in n)
    i_upload = next(i for i, n in enumerate(names) if '上載 Pages artifact' in n)
    assert i_gates < i_build < i_upload
    steps = build['steps']
    assert 'run_acceptance.py' in steps[i_gates]['run']
    assert 'build_pages_artifact.py' in steps[i_build]['run']
    assert steps[i_upload]['with']['path'] == '_site'


def test_pages_deploy_actions_pinned_to_full_commit():
    import re
    text = _text('pages-deploy.yml')
    uses = re.findall(r'uses:\s*(\S+)', text)
    assert uses, 'workflow 應該有 actions'
    for use in uses:
        assert re.match(r'^[^@\s]+@[0-9a-f]{40}$', use), use
    assert any('actions/configure-pages@' in u for u in uses)
    assert any('actions/upload-pages-artifact@' in u for u in uses)
    assert any('actions/deploy-pages@' in u for u in uses)


def test_postdeploy_binds_actions_pages_exact_sha_not_dynamic():
    text = _text('postdeploy-verify.yml')
    assert 'Pages 部署（Actions）' in text
    assert "github.event.workflow_run.event == 'dynamic'" not in text
    assert 'pages build and deployment' not in text
    assert 'github.event.workflow_run.head_sha' in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert 'github.event.workflow_run.head_branch' in text and "'master'" in text
    assert 'persist-credentials: false' in text
    assert 'contents: read' in text
