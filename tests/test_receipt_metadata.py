# -*- coding: utf-8 -*-
"""部署 metadata 收據鏈回歸（P0）

- receipt_facts：成功／失敗／頁數／來源／UTC／未來時間／hash／CSV 行數全部 fail-closed
- datasetDate 由實際 retrievedAt 轉香港時區（跨月／跨年都要正確）
- 重建模式用舊完整 hash-bound 收據 → 保留舊日期，唔會用生成時間改寫
- 舊無 hash 收據 → 明確失敗（唔可以補假 hash／假時間）
- workflow 由 emsd_receipt.json 出 dataset 事實、EMSD 抓取失敗即阻斷
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(BASE, '.github', 'workflows', 'daily-update.yml')
_SPEC = importlib.util.spec_from_file_location(
    'gen_metadata_receipt', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
gen_metadata = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_metadata)

SOURCE = ('https://www.emsd.gov.hk/energylabel/tc/households/rac/'
          'select_ac_result.php?type=all&searchR=50')


def make_csv(path, rows=60):
    header = ['品牌', '型號'] + [f'c{i}' for i in range(13)]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(header)
        for i in range(rows):
            w.writerow([f'品牌{i}', f'M{i}'] + [str(i)] * 13)
    with open(path, 'rb') as f:
        return 'sha256:' + hashlib.sha256(f.read()).hexdigest()


def make_receipt(path, csv_path, **over):
    digest = 'sha256:' + hashlib.sha256(open(csv_path, 'rb').read()).hexdigest()
    receipt = {
        'retrievedAt': '2026-09-20T18:59:57Z',
        'sourceUrl': SOURCE,
        'success': True,
        'pagesExpected': 2,
        'pagesFetched': 2,
        'totalRows': 60,
        'aborted': False,
        'error': None,
        'perPageRows': [50, 10],
        'datasetHash': digest,
    }
    receipt.update(over)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(receipt, f, ensure_ascii=False)
    return receipt


def test_receipt_facts_hkt_cross_month_and_year(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    import datetime as _dt
    r1 = tmp_path / 'r1.json'
    make_receipt(r1, csv_path, retrievedAt='2026-01-31T18:00:00Z')
    facts = gen_metadata.receipt_facts(str(r1), str(csv_path),
                                       now=_dt.datetime(2026, 2, 1, tzinfo=_dt.timezone.utc))
    assert facts['datasetDate'] == '2026-02-01', 'UTC 1/31 18:00 → HKT 2/1'
    r2 = tmp_path / 'r2.json'
    make_receipt(r2, csv_path, retrievedAt='2025-12-31T16:30:00Z')
    facts = gen_metadata.receipt_facts(str(r2), str(csv_path),
                                       now=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc))
    assert facts['datasetDate'] == '2026-01-01', 'UTC 12/31 16:30 → HKT 1/1'
    assert facts['datasetRetrievedAt'] == '2025-12-31T16:30:00Z', '保留收據原文時間'
    assert facts['rawRecordCount'] == 60 and facts['registrationCount'] == 60


def test_receipt_facts_rebuild_keeps_old_date(tmp_path):
    """重建模式：同一 CSV + 舊收據 → 日期由收據 retrievedAt 得出，唔係今日"""
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    receipt = tmp_path / 'r.json'
    make_receipt(receipt, csv_path, retrievedAt='2026-09-01T10:00:00Z')
    facts = gen_metadata.receipt_facts(str(receipt), str(csv_path))
    assert facts['datasetDate'] == '2026-09-01'
    assert facts['datasetRetrievedAt'] == '2026-09-01T10:00:00Z'


@pytest.mark.parametrize('over', [
    {'success': False},
    {'aborted': True},
    {'error': 'page 3 down'},
    {'totalRows': 59},
    {'pagesFetched': 1},
    {'pagesExpected': 3},
    {'perPageRows': [50, 0, 10]},
    {'retrievedAt': '2026-09-20T18:59:57+00:00'},
])
def test_receipt_facts_rejects_bad_receipts(tmp_path, over):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    receipt = tmp_path / 'r.json'
    make_receipt(receipt, csv_path, **over)
    with pytest.raises(ValueError):
        gen_metadata.receipt_facts(str(receipt), str(csv_path))


def test_receipt_facts_rejects_unapproved_source_and_future(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    r1 = tmp_path / 'r1.json'
    make_receipt(r1, csv_path, sourceUrl='https://evil.example.com/x.php')
    with pytest.raises(ValueError, match='Unapproved'):
        gen_metadata.receipt_facts(str(r1), str(csv_path))
    r2 = tmp_path / 'r2.json'
    make_receipt(r2, csv_path, retrievedAt='2099-01-01T00:00:00Z')
    with pytest.raises(ValueError, match='future'):
        gen_metadata.receipt_facts(str(r2), str(csv_path))


def test_receipt_facts_requires_hash_bound_csv(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    r1 = tmp_path / 'r1.json'
    make_receipt(r1, csv_path)
    data = json.load(open(r1, encoding='utf-8'))
    del data['datasetHash']
    json.dump(data, open(r1, 'w', encoding='utf-8'))
    with pytest.raises(ValueError, match='legacy receipt'):
        gen_metadata.receipt_facts(str(r1), str(csv_path))
    r2 = tmp_path / 'r2.json'
    make_receipt(r2, csv_path, datasetHash='sha256:' + 'a' * 64)
    with pytest.raises(ValueError, match='hash missing or not matching'):
        gen_metadata.receipt_facts(str(r2), str(csv_path))


def test_receipt_facts_rejects_csv_row_mismatch(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    receipt = tmp_path / 'r.json'
    digest = 'sha256:' + hashlib.sha256(open(csv_path, 'rb').read()).hexdigest()
    make_receipt(receipt, csv_path, totalRows=10, pagesExpected=1, pagesFetched=1,
                 perPageRows=[10], datasetHash=digest)
    with pytest.raises(ValueError, match='row count'):
        gen_metadata.receipt_facts(str(receipt), str(csv_path))


def _core_args(receipt, csv_path, extra=None):
    return [
        '--stage', 'core', '--out', str(csv_path.parent / 'core.json'), '--force',
        '--version', '1.2.9', '--build', 'B20260921.TEST', '--commit', 'a' * 40,
        '--workflow-run-id', '42', '--emsd-receipt', str(receipt),
        '--dataset-csv', str(csv_path), '--record-count', '60',
    ] + (extra or [])


def _run(*args):
    return subprocess.run([sys.executable, os.path.join(BASE, 'scripts', 'gen-metadata.py'), *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def test_core_receipt_cli_overrides_wrong_date_and_hash(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    digest = make_csv(csv_path)
    receipt = tmp_path / 'r.json'
    make_receipt(receipt, csv_path, retrievedAt='2026-09-20T18:59:57Z')
    args = _core_args(receipt, csv_path, ['--dataset-hash', digest,
                                          '--dataset-date', '1999-01-01',
                                          '--dataset-date-basis', 'official-published-date'])
    r = _run(*args)
    assert r.returncode == 0, r.stderr
    core = json.load(open(tmp_path / 'core.json', encoding='utf-8'))
    assert core['datasetDate'] == '2026-09-21', '收據 retrievedAt（HKT）優先，唔可以用手填日期'
    assert core['datasetDateBasis'] == 'retrieval-date-fallback'
    assert core['datasetRetrievedAt'] == '2026-09-20T18:59:57Z'
    assert 'releasePayloadHash' not in core
    # core 驗證（placeholder hash）通過；正式 Schema 仍然拒絕
    r2 = _run('--stage', 'core', '--out', str(tmp_path / 'core2.json'), '--force',
              '--version', '1.2.9', '--build', 'B1', '--commit', 'a' * 40,
              '--workflow-run-id', '42', '--emsd-receipt', str(receipt),
              '--dataset-csv', str(csv_path), '--dataset-hash', 'sha256:' + 'f' * 64,
              '--record-count', '60')
    assert r2.returncode != 0 and 'hash' in (r2.stderr or '').lower()
    rc = _run('--stage', 'core', '--out', str(tmp_path / 'core3.json'), '--force',
              '--version', '1.2.9', '--build', 'B1', '--commit', 'a' * 40,
              '--workflow-run-id', '42', '--emsd-receipt', str(receipt),
              '--dataset-csv', str(csv_path), '--dataset-hash', 'sha256:' + digest.removeprefix('sha256:'),
              '--record-count', '60')
    assert rc.returncode == 0, rc.stderr


def test_core_trust_gate_rejects_pr_events(tmp_path):
    """冇 --force 時：非 GHA 或 PR 事件唔可以生成部署 metadata（治理 §9.3）"""
    script = os.path.join(BASE, 'scripts', 'gen-metadata.py')
    base_args = ['--stage', 'core', '--out', str(tmp_path / 'core.json'),
                 '--version', '1.2.9', '--build', 'B1', '--commit', 'a' * 40,
                 '--workflow-run-id', '1', '--dataset-date', '2026-09-21',
                 '--dataset-date-basis', 'retrieval-date-fallback',
                 '--dataset-source-url', 'https://example.com', '--dataset-snapshot-id', 's',
                 '--dataset-hash', 'sha256:' + 'b' * 64, '--record-count', '1']
    env = dict(os.environ, GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='pull_request')
    r = subprocess.run([sys.executable, script, *base_args], env=env,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode != 0 and 'PR' in r.stderr, r.stderr
    env2 = dict(os.environ, GITHUB_ACTIONS='false', GITHUB_EVENT_NAME='schedule')
    r2 = subprocess.run([sys.executable, script, *base_args], env=env2,
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r2.returncode != 0 and '受信任' in r2.stderr, r2.stderr


def test_validate_core_mode(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    digest = make_csv(csv_path)
    receipt = tmp_path / 'r.json'
    make_receipt(receipt, csv_path)
    core = tmp_path / 'core.json'
    r = _run(*_core_args(receipt, csv_path, ['--dataset-hash', digest]))
    assert r.returncode == 0, r.stderr
    vm = os.path.join(BASE, 'scripts', 'validate_metadata.py')
    ok = subprocess.run([sys.executable, vm, '--core', str(core)],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert ok.returncode == 0, ok.stderr
    plain = subprocess.run([sys.executable, vm, str(core)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert plain.returncode != 0, '未 finalize core 唔可以過正式 Schema'


def test_workflow_uses_receipt_and_fails_closed():
    import yaml
    wf = yaml.safe_load(open(WORKFLOW, encoding='utf-8'))
    steps = wf['jobs']['update']['steps']
    by_name = {s.get('name', ''): s for s in steps}
    emsd = by_name['抓取 EMSD + 新機偵測']
    assert not emsd.get('continue-on-error'), 'EMSD 每日抓取失敗必須阻斷'
    core_run = next(s['run'] for s in steps if '階段 1' in s.get('name', ''))
    assert '--emsd-receipt emsd_receipt.json' in core_run
    assert '--dataset-csv' in core_run
    assert '--dataset-date "$(date +%F)"' not in core_run, '唔可以再用 runner 日期冒充資料日期'
    assert '--dataset-source-url' not in core_run
    feature = by_name['功能註冊表檢查（GATE-03）']
    assert '--run-tests' in feature['run'], 'CI 要有實際受測證據'
    batch1 = by_name['官網核實第一批（新機後第 1 日）']
    assert 'run_official_batch.py --stage 1' in batch1['run']
    biggo = next(s['run'] for s in steps if '價錢快照' in s.get('name', ''))
    for line in biggo.splitlines():
        if 'fetch_biggo.py' in line:
            assert '|| true' not in line, f'BigGo 命令唔可以吞失敗：{line.strip()}'
    assert 'GITHUB_STEP_SUMMARY' in biggo, 'skip／失敗要有可審計結果'
    assert '--force-batch' in biggo and '--smoke' in biggo, 'force-batch 亦要 smoke 保護'
    assert 'exit "$rc"' in biggo or 'exit $rc' in biggo, '真失敗要非零退出，唔可以當成功'


def test_receipt_facts_rejects_bool_counts_and_bad_source(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    make_csv(csv_path)
    r1 = tmp_path / 'r1.json'
    make_receipt(r1, csv_path, perPageRows=[True, 59])
    with pytest.raises(ValueError):
        gen_metadata.receipt_facts(str(r1), str(csv_path))
    r2 = tmp_path / 'r2.json'
    make_receipt(r2, csv_path,
                 sourceUrl='https://user:pass@www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php')
    with pytest.raises(ValueError, match='userinfo'):
        gen_metadata.receipt_facts(str(r2), str(csv_path))
    r3 = tmp_path / 'r3.json'
    make_receipt(r3, csv_path,
                 sourceUrl='https://www.emsd.gov.hk:8443/energylabel/tc/households/rac/select_ac_result.php')
    with pytest.raises(ValueError, match='port'):
        gen_metadata.receipt_facts(str(r3), str(csv_path))


def test_no_receipt_requires_explicit_retrieved_at(tmp_path):
    csv_path = tmp_path / 'emsd.csv'
    digest = make_csv(csv_path)
    r = _run('--stage', 'core', '--out', str(tmp_path / 'core.json'), '--force',
             '--version', '1.2.9', '--build', 'B1', '--commit', 'a' * 40,
             '--workflow-run-id', '1', '--dataset-date', '2026-09-21',
             '--dataset-date-basis', 'retrieval-date-fallback',
             '--dataset-source-url', 'https://example.com', '--dataset-snapshot-id', 's',
             '--dataset-hash', digest, '--record-count', '60')
    assert r.returncode != 0
    assert 'dataset-retrieved-at' in r.stderr


def test_production_requires_receipt(tmp_path):
    """GHA 非 PR 事件、冇 --force、冇 receipt：唔可以用手填日期出新 metadata"""
    script = os.path.join(BASE, 'scripts', 'gen-metadata.py')
    env = dict(os.environ, GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='schedule')
    r = subprocess.run(
        [sys.executable, script, '--stage', 'core', '--out', str(tmp_path / 'core.json'),
         '--version', '1.2.9', '--build', 'B1', '--commit', 'a' * 40,
         '--workflow-run-id', '1', '--dataset-date', '2026-09-21',
         '--dataset-date-basis', 'retrieval-date-fallback',
         '--dataset-source-url', 'https://example.com', '--dataset-snapshot-id', 's',
         '--dataset-hash', 'sha256:' + 'b' * 64, '--record-count', '1'],
        env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode != 0
    assert 'emsd-receipt' in r.stderr


def test_local_force_cannot_overwrite_repo_metadata(tmp_path):
    script = os.path.join(BASE, 'scripts', 'gen-metadata.py')
    # 直接用 repo 生產 metadata 路徑測守衛（唔應該寫入）
    env = dict(os.environ)
    env.pop('GITHUB_ACTIONS', None)
    r = subprocess.run(
        [sys.executable, script, '--stage', 'finalize', '--core', str(tmp_path / 'core.json'),
         '--payload-manifest', os.path.join(BASE, 'deploy_payload.json'),
         '--out', os.path.join(BASE, 'metadata.json'), '--force'],
        env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode != 0
    assert '本地 --force' in r.stderr or '讀唔到 core' in r.stderr


def _core_obj(tmp_path, **over):
    csv_path = tmp_path / 'emsd.csv'
    digest = make_csv(csv_path)
    r = tmp_path / 'r.json'
    make_receipt(r, csv_path)
    core = None
    args = _core_args(r, csv_path, ['--dataset-hash', digest])
    run = _run(*args)
    assert run.returncode == 0, run.stderr
    core = json.load(open(tmp_path / 'core.json', encoding='utf-8'))
    core.update(over)
    out = tmp_path / 'core-over.json'
    out.write_text(json.dumps(core), encoding='utf-8')
    return out


def test_finalize_rejects_csv_hash_mismatch(tmp_path):
    core = _core_obj(tmp_path)
    core_data = json.load(open(core, encoding='utf-8'))
    core_data['datasetHash'] = 'sha256:' + 'f' * 64
    core.write_text(json.dumps(core_data), encoding='utf-8')
    r = _run('--stage', 'finalize', '--core', str(core),
             '--payload-manifest', os.path.join(BASE, 'deploy_payload.json'),
             '--out', str(tmp_path / 'metadata.json'), '--force',
             '--dataset-csv', str(tmp_path / 'emsd.csv'))
    assert r.returncode != 0
    assert 'CSV' in r.stderr


def test_finalize_rejects_rawcount_mismatch(tmp_path):
    core = _core_obj(tmp_path, rawRecordCount=5)
    r = _run('--stage', 'finalize', '--core', str(core),
             '--payload-manifest', os.path.join(BASE, 'deploy_payload.json'),
             '--out', str(tmp_path / 'metadata.json'), '--force',
             '--dataset-csv', str(tmp_path / 'emsd.csv'))
    assert r.returncode != 0
    assert 'rawRecordCount' in r.stderr
