# -*- coding: utf-8 -*-
"""Fix B 回歸：兩階段 metadata 封裝（DECISIONS.md D14）

- 階段 1（core）：核心事實（deployTime 由腳本生成）；未完成件過唔到正式 Schema
- 階段 2（finalize）：只新增 releasePayloadHash，除 hash 外核心事實逐欄不變
- hash 範圍：明確 manifest、可重現、篡改會變、唔包含 metadata.json（非自引用）
- PDF 使用同 run core metadata；唔可以讀上一 run 嘅 metadata.json
- 未 finalize 唔可以進入部署步驟（workflow 次序 + Schema 閘門）
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

MANIFEST = os.path.join(BASE, 'deploy_payload.json')
WORKFLOW = os.path.join(BASE, '.github', 'workflows', 'daily-update.yml')

_SPEC = importlib.util.spec_from_file_location(
    'gen_metadata', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
gen_metadata = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_metadata)


def _run(script, *args):
    return subprocess.run(
        [sys.executable, os.path.join(BASE, 'scripts', script), *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=BASE)


def _csv_facts():
    import csv
    import hashlib
    path = os.path.join(BASE, 'emsd_空調能源標籤.csv')
    with open(path, 'rb') as f:
        digest = 'sha256:' + hashlib.sha256(f.read()).hexdigest()
    with open(path, encoding='utf-8-sig') as f:
        rows = sum(1 for _ in csv.reader(f)) - 1
    return digest, rows


def _core_args(out):
    digest, n_rows = _csv_facts()
    return [
        '--stage', 'core', '--out', str(out), '--force',
        '--version', '1.2.8',
        '--build', 'B20260913.TEST',
        '--commit', 'a' * 40,
        '--workflow-run-id', '123456789',
        '--dataset-date', '2026-09-03',
        '--dataset-date-basis', 'retrieval-date-fallback',
        '--dataset-retrieved-at', '2026-09-03T00:00:00Z',
        '--dataset-source-url',
        'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php',
        '--dataset-snapshot-id', 'emsd-2026-09-03',
        '--dataset-hash', digest,
        '--record-count', '1814',
        '--raw-record-count', str(n_rows),
        '--registration-count', str(n_rows),
        '--model-count', '1814',
    ]


def _gen_core(out):
    r = _run('gen-metadata.py', *_core_args(out))
    assert r.returncode == 0, r.stderr
    with open(out, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- 階段 1（core）

def test_core_stage_has_no_hash_and_fails_formal_schema(tmp_path):
    core_path = tmp_path / 'metadata.core.json'
    core = _gen_core(core_path)
    assert 'releasePayloadHash' not in core, 'core 未 finalize，唔應該有 payload hash'
    r = _run('validate_metadata.py', str(core_path))
    assert r.returncode != 0, '未 finalize core 唔可以通過正式 Schema（唔可以部署）'
    assert 'releasePayloadHash' in r.stderr


def test_core_stage_refuses_metadata_json_name(tmp_path):
    r = _run('gen-metadata.py', '--stage', 'core', '--out', str(tmp_path / 'metadata.json'),
             '--force', '--version', '1.2.8', '--build', 'B1', '--commit', 'a' * 40,
             '--workflow-run-id', '1', '--dataset-date', '2026-09-03',
             '--dataset-date-basis', 'retrieval-date-fallback',
             '--dataset-source-url', 'https://example.com',
             '--dataset-snapshot-id', 's1', '--dataset-hash', 'sha256:' + 'b' * 64,
             '--record-count', '1')
    assert r.returncode != 0, 'core 階段唔可以寫 metadata.json（未完成件唔可以冒充部署 metadata）'


def test_core_stage_rejects_payload_hash_args(tmp_path):
    args = _core_args(tmp_path / 'metadata.core.json') + ['--payload-dir', '.']
    r = _run('gen-metadata.py', *args)
    assert r.returncode != 0, 'core 階段唔應該接受 payload hash 參數'


# ---------------------------------------------------------------- 階段 2（finalize）

def _finalize_args(core, out):
    return ['--stage', 'finalize', '--core', str(core),
            '--payload-manifest', MANIFEST, '--out', str(out), '--force']


def test_finalize_adds_only_hash_and_preserves_all_core_fields(tmp_path):
    core = _gen_core(tmp_path / 'metadata.core.json')
    out = tmp_path / 'metadata.json'
    r = _run('gen-metadata.py', *_finalize_args(tmp_path / 'metadata.core.json', out))
    assert r.returncode == 0, r.stderr
    with open(out, encoding='utf-8') as f:
        final = json.load(f)
    assert final['releasePayloadHash'].startswith('sha256:')
    assert {k: v for k, v in final.items() if k != 'releasePayloadHash'} == core, (
        'finalize 只可以新增 releasePayloadHash，核心事實逐欄不變')
    # 關鍵事實同 core 一致
    for field in ('version', 'datasetDate', 'deployTime', 'build', 'commit', 'workflowRunId'):
        assert final[field] == core[field]
    r2 = _run('validate_metadata.py', str(out))
    assert r2.returncode == 0, r2.stderr


def test_finalize_refuses_already_finalized_core(tmp_path):
    core_path = tmp_path / 'metadata.core.json'
    core = _gen_core(core_path)
    core['releasePayloadHash'] = 'sha256:' + 'd' * 64
    core_path.write_text(json.dumps(core, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'metadata.json'
    r = _run('gen-metadata.py', *_finalize_args(core_path, out))
    assert r.returncode != 0, '已經有 hash 嘅 core 唔可以再 finalize'
    assert not out.exists(), '失敗時唔應該寫出 metadata.json'


def test_finalize_refuses_core_named_metadata_json(tmp_path):
    fake = tmp_path / 'metadata.json'
    fake.write_text('{}', encoding='utf-8')
    r = _run('gen-metadata.py', '--stage', 'finalize', '--core', str(fake),
             '--payload-manifest', MANIFEST, '--out', str(tmp_path / 'out-metadata.json'), '--force')
    assert r.returncode != 0


def test_finalize_output_must_be_named_metadata_json(tmp_path):
    _gen_core(tmp_path / 'metadata.core.json')
    r = _run('gen-metadata.py', '--stage', 'finalize', '--core', str(tmp_path / 'metadata.core.json'),
             '--payload-manifest', MANIFEST, '--out', str(tmp_path / 'final.json'), '--force')
    assert r.returncode != 0, 'finalize 只可以寫正式 metadata.json'


def test_finalize_requires_source_of_hash(tmp_path):
    _gen_core(tmp_path / 'metadata.core.json')
    r = _run('gen-metadata.py', '--stage', 'finalize', '--core', str(tmp_path / 'metadata.core.json'),
             '--out', str(tmp_path / 'metadata.json'), '--force')
    assert r.returncode != 0


# ---------------------------------------------------------------- hash 行為

def test_hash_is_order_independent_and_tamper_sensitive(tmp_path):
    (tmp_path / 'a.bin').write_bytes(b'one')
    (tmp_path / 'b.bin').write_bytes(b'two')
    h1 = gen_metadata.hash_files(['a.bin', 'b.bin'], base=str(tmp_path))
    assert h1 == gen_metadata.hash_files(['b.bin', 'a.bin'], base=str(tmp_path)), (
        'manifest 次序唔應該影響 hash（排序後計算）')
    (tmp_path / 'a.bin').write_bytes(b'ONE')
    h2 = gen_metadata.hash_files(['a.bin', 'b.bin'], base=str(tmp_path))
    assert h2 != h1, '篡改 payload 內容必須改變 hash'


def test_hash_is_reproducible_for_same_content(tmp_path):
    (tmp_path / 'x.txt').write_bytes(b'same')
    assert (gen_metadata.hash_files(['x.txt'], base=str(tmp_path))
            == gen_metadata.hash_files(['x.txt'], base=str(tmp_path)))


def test_hash_rejects_self_reference_and_bad_paths(tmp_path):
    (tmp_path / 'metadata.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'x.txt').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError):
        gen_metadata.hash_files(['metadata.json'], base=str(tmp_path))
    with pytest.raises(ValueError):
        gen_metadata.hash_files(['../x.txt'], base=str(tmp_path))
    with pytest.raises(ValueError):
        gen_metadata.hash_files(['/etc/hosts'], base=str(tmp_path))


def test_metadata_json_not_part_of_hash(tmp_path):
    """metadata 自引用檢查：改動 metadata.json 唔會改 payload hash"""
    (tmp_path / 'x.txt').write_bytes(b'x')
    (tmp_path / 'metadata.json').write_text('{"v":1}', encoding='utf-8')
    h1 = gen_metadata.hash_files(['x.txt'], base=str(tmp_path))
    assert gen_metadata.hash_payload(str(tmp_path)) == h1, '--payload-dir 舊接口亦排除 metadata.json'
    (tmp_path / 'metadata.json').write_text('{"v":2}', encoding='utf-8')
    assert gen_metadata.hash_files(['x.txt'], base=str(tmp_path)) == h1


# ---------------------------------------------------------------- PDF 用同 run core

def test_pdf_consumes_core_metadata_not_repo_metadata(tmp_path, monkeypatch):
    import generate_pdf
    core_path = tmp_path / 'metadata.core.json'
    core = _gen_core(core_path)
    seen = {}
    real_load = generate_pdf.load_metadata

    def spy(path=None):
        seen['path'] = path
        return real_load(path)

    monkeypatch.setattr(generate_pdf, 'load_metadata', spy)
    out = tmp_path / 'r.pdf'
    generate_pdf.build_pdf(str(out), metadata_path=str(core_path))
    assert seen['path'] == str(core_path), 'PDF 必須讀同 run core metadata，唔係上一 run metadata.json'
    assert out.exists() and out.stat().st_size > 10000
    # core 嘅 version／datasetDate 真係用嚟砌 PDF 狀態行（同 Web 同一套 format_status）
    line1, _ = generate_pdf.format_status(core, generate_pdf.VERSION)
    assert core['version'] in line1 and core['datasetDate'] in line1

    # 行為證明：改 core version → PDF 內容改變（唔係忽略參數）
    core2_path = tmp_path / 'metadata.core2.json'
    altered = dict(core)
    altered['version'] = '0.0.1-test'
    core2_path.write_text(json.dumps(altered, ensure_ascii=False), encoding='utf-8')
    out2 = tmp_path / 'r2.pdf'
    generate_pdf.build_pdf(str(out2), metadata_path=str(core2_path))
    assert out2.read_bytes() != out.read_bytes(), 'PDF 內容必須反映 core metadata 嘅 version'


def test_two_stage_local_simulation(tmp_path):
    """本地模擬 CI 次序：core → schema 拒 → PDF 用 core → finalize → schema 過"""
    core_path = tmp_path / 'metadata.core.json'
    core = _gen_core(core_path)
    assert _run('validate_metadata.py', str(core_path)).returncode != 0

    import generate_pdf
    pdf = tmp_path / '空調對比報告.pdf'
    generate_pdf.build_pdf(str(pdf), metadata_path=str(core_path))
    assert pdf.exists()

    final_path = tmp_path / 'metadata.json'
    r = _run('gen-metadata.py', *_finalize_args(core_path, final_path))
    assert r.returncode == 0, r.stderr
    assert _run('validate_metadata.py', str(final_path)).returncode == 0
    with open(final_path, encoding='utf-8') as f:
        final = json.load(f)
    assert final['version'] == core['version']
    assert final['datasetDate'] == core['datasetDate']
    assert final['deployTime'] == core['deployTime']
    # 同 manifest 重算一致（可重現）
    assert final['releasePayloadHash'] == gen_metadata.hash_manifest(MANIFEST)


# ---------------------------------------------------------------- workflow 次序

def test_workflow_yaml_valid_and_two_stage_order():
    import yaml
    with open(WORKFLOW, encoding='utf-8') as f:
        wf = yaml.safe_load(f)
    steps = wf['jobs']['update']['steps']
    names = [s.get('name', '') for s in steps]
    runs = {s.get('name', ''): s.get('run', '') for s in steps}

    def idx(sub):
        return next(i for i, n in enumerate(names) if sub in n)

    i_core = idx('階段 1')
    i_pdf = idx('report.pdf-export')
    i_final = idx('階段 2')
    i_val = idx('驗證 metadata.json')
    i_commit = idx('提交並推送')
    assert i_core < i_pdf < i_final < i_val < i_commit, (
        f'兩階段次序錯：core={i_core} pdf={i_pdf} finalize={i_final} validate={i_val} commit={i_commit}')

    core_run = next(r for n, r in runs.items() if '階段 1' in n)
    assert '--stage core' in core_run
    assert 'RUNNER_TEMP' in core_run, 'core 必須寫 repo 外暫存，唔可以被 git add／部署'
    assert '--record-count "$MODELS"' in core_run, 'recordCount 要用唯一型號數（D12）'
    assert '--registration-count "$REGISTRATIONS"' in core_run
    assert '--model-count "$MODELS"' in core_run

    pdf_run = next(r for n, r in runs.items() if 'report.pdf-export' in n)
    assert '--metadata "$RUNNER_TEMP/metadata.core.json"' in pdf_run, (
        'PDF 要用同 run core metadata，唔可以讀上一 run metadata.json')

    fin_run = next(r for n, r in runs.items() if '階段 2' in n)
    assert '--stage finalize' in fin_run
    assert '--payload-manifest deploy_payload.json' in fin_run

    # commit 步驟唔可以部署 core
    commit_run = next(r for n, r in runs.items() if '提交並推送' in n)
    assert 'metadata.core.json' not in commit_run


def test_deploy_payload_manifest_scope():
    with open(MANIFEST, encoding='utf-8') as f:
        spec = json.load(f)
    files = spec['files']
    assert files, 'manifest 唔可以空'
    assert 'index.html' in files, 'Web 應用必須入 payload'
    assert '空調對比報告.pdf' in files, 'PDF 必須入 payload'
    assert 'emsd_空調能源標籤.csv' in files, '資料快照必須入 payload'
    assert 'metadata.json' not in files, '最終 metadata 唔可以入 payload（自引用）'
    assert 'metadata.core.json' not in files
    for banned in ('.git', '.venv', '.agents', 'tests/', 'docs/', '__pycache__'):
        assert not any(f.startswith(banned) for f in files), f'payload 唔應該包含 {banned}'
