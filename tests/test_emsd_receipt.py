# -*- coding: utf-8 -*-
"""EMSD 收據／抓取證據回歸（P0 時間與資料真實性）

- 成功收據只可以在 CSV 成功寫入之後寫，datasetHash 對已寫入 bytes 計算
- retrievedAt 係抓取完成時間（由呼叫方傳入），唔係寫收據當刻隨機時間
- 頁數剛好 50 倍數（下一頁回空確認完結）唔可以多報一頁
- 0 頁、壞表頭、detect_new_models 失敗、CSV 寫入失敗都寫失敗收據，唔改舊 CSV
- 403/429 寫失敗收據後以非零狀態中止
"""
import hashlib
import importlib.util
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SPEC = importlib.util.spec_from_file_location('fetch_emsd_mod', os.path.join(BASE, 'fetch_emsd.py'))
fetch_emsd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch_emsd)


HEADER = ['品牌', '型號'] + [f'c{i}' for i in range(13)]


def _html(rows, header=False):
    trs = []
    if header:
        trs.append('<tr>' + ''.join(f'<th>{c}</th>' for c in HEADER) + '</tr>')
    for r in rows:
        trs.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
    return '<table>' + ''.join(trs) + '</table>'


def _rows(n, start='M'):
    return [[f'品牌{i}', f'{start}{i}'] + [str(i)] * 13 for i in range(n)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_emsd, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch_emsd, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    monkeypatch.setattr(fetch_emsd, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch_emsd, 'MIN_EMSD_ROWS', 100)
    monkeypatch.setattr(fetch_emsd.random, 'uniform', lambda a, b: 0)
    return tmp_path


def _run_main(pages):
    calls = {'i': 0}

    def fake_fetch(p):
        calls['i'] += 1
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch_emsd.fetch_page = fake_fetch
    try:
        fetch_emsd.main()
        return 0
    except SystemExit as e:
        return e.code


def _receipt(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def test_exact_multiple_of_50_pages_counts_data_pages_only(env):
    """2 頁各 50 行 + 第 3 頁空 = fetched 2／expected 2，唔可以報 3 頁"""
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 0
    r = _receipt(env / 'emsd_receipt.json')
    assert r['pagesFetched'] == 2 and r['pagesExpected'] == 2
    assert r['totalRows'] == 100 and r['aborted'] is False and r['error'] is None
    assert r['success'] is True
    csv_path = env / 'emsd_空調能源標籤.csv'
    assert r['datasetHash'] == 'sha256:' + hashlib.sha256(csv_path.read_bytes()).hexdigest()


def test_success_receipt_time_is_capture_completion_not_write_time(env, monkeypatch):
    """retrievedAt 必須由 main 抓取收尾時間傳入（唔係 write_receipt 內 now）"""
    captured = {}

    def spy(outcome, per_page, csv_path=None, retrieved_at=None, **kwargs):
        captured['retrieved_at'] = retrieved_at
        captured['csv_path'] = csv_path
        return original(outcome, per_page, csv_path, retrieved_at,
                        raw_receipt_hash=kwargs.get('raw_receipt_hash'))

    original = fetch_emsd.write_receipt
    monkeypatch.setattr(fetch_emsd, 'write_receipt', spy)
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 0
    assert captured['retrieved_at'] and captured['retrieved_at'].endswith('Z')
    assert captured['csv_path'] is not None, '成功收據必須綁定已寫入 CSV'


def test_zero_pages_failure_receipt_and_no_csv_overwrite(env):
    old = env / 'emsd_空調能源標籤.csv'
    old.write_text('OLD-CSV', encoding='utf-8')
    assert _run_main(['']) == 1
    assert old.read_text(encoding='utf-8') == 'OLD-CSV'
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and r['pagesFetched'] == 0
    assert 'datasetHash' not in r, '失敗收據唔可以綁 hash 扮成功'


def test_bad_header_failure_receipt(env, monkeypatch):
    monkeypatch.setattr(fetch_emsd, 'MIN_EMSD_ROWS', 50)
    old = env / 'emsd_空調能源標籤.csv'
    old.write_text('OLD-CSV', encoding='utf-8')
    assert _run_main([_html(_rows(50), header=False)]) == 1
    assert old.read_text(encoding='utf-8') == 'OLD-CSV'
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and r['error'] == 'invalid header'


def test_detect_new_models_failure_blocks_csv_update(env, monkeypatch):
    def boom(rows):
        raise RuntimeError('planning failed')

    monkeypatch.setattr(fetch_emsd, 'plan_new_models', boom)
    old = env / 'emsd_空調能源標籤.csv'
    old.write_text('OLD-CSV', encoding='utf-8')
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    assert old.read_text(encoding='utf-8') == 'OLD-CSV'
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and 'new-model detection failed' in r['error']


def test_dataset_commit_failure_blocks_success_receipt_and_rolls_back(env, monkeypatch):
    old = env / 'emsd_空調能源標籤.csv'
    old.write_text('OLD-CSV', encoding='utf-8')
    (env / 'new_models.json').write_text('{"updated":"2026-01-01","models":[]}', encoding='utf-8')
    (env / 'update_queue.json').write_text('{"stage":0,"models":[]}', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}

    def boom(header, rows, path, plan):
        raise OSError('disk full')

    monkeypatch.setattr(fetch_emsd, 'commit_dataset', boom)
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and 'dataset commit failed' in r['error']
    assert 'datasetHash' not in r
    after = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}
    for name, data in before.items():
        assert after[name] == data, f'{name} 唔應該被改動'


def test_success_commits_csv_and_sidecars_together(env):
    """成功：CSV + new_models + queue 一齊提交，收據 hash 綁定新 CSV"""
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 0
    new_models = json.loads((env / 'new_models.json').read_text(encoding='utf-8'))
    queue = json.loads((env / 'update_queue.json').read_text(encoding='utf-8'))
    assert len(new_models['models']) == 100
    assert queue['stage'] == 1 and len(queue['models']) == 100
    r = _receipt(env / 'emsd_receipt.json')
    csv_path = env / 'emsd_空調能源標籤.csv'
    assert r['datasetHash'] == 'sha256:' + hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert r['success'] is True


@pytest.mark.parametrize('fail_name', ['emsd_空調能源標籤.csv', 'new_models.json', 'update_queue.json'])
def test_commit_replaces_failure_rolls_back_all_three(env, monkeypatch, fail_name):
    """故障注入：三個 replace 任一失敗 → CSV／new_models／queue bytes 全部保持原狀"""
    old_csv = env / 'emsd_空調能源標籤.csv'
    old_csv.write_text('OLD-CSV', encoding='utf-8')
    (env / 'new_models.json').write_text('{"updated":"2026-01-01","models":[]}', encoding='utf-8')
    (env / 'update_queue.json').write_text('{"stage":0,"models":[]}', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}
    real_replace = os.replace
    state = {'armed': True}

    def fake_replace(src, dst):
        if state['armed'] and os.path.basename(dst) == fail_name and str(src).endswith('.tmp'):
            state['armed'] = False
            raise OSError(f'inject failure on {fail_name}')
        return real_replace(src, dst)

    monkeypatch.setattr(fetch_emsd.os, 'replace', fake_replace)
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    with pytest.raises(SystemExit) as exc:
        fetch_emsd.main()
    assert exc.value.code == 1
    after = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}
    for name, data in before.items():
        assert after[name] == data, f'{name} 唔應該被改動'
    for tmp in list(env.glob('*.tmp')):
        assert not tmp.exists(), f'唔應該留低 tmp：{tmp.name}'
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and 'dataset commit failed' in r['error']


def test_403_writes_failure_receipt_then_exits(env, monkeypatch):
    def forbidden(p):
        raise SystemExit('EMSD 返回 403（被限流）')

    monkeypatch.setattr(fetch_emsd, 'fetch_page', forbidden)
    with pytest.raises(SystemExit):
        fetch_emsd.main()
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and r['aborted'] is True
    assert '403' in r['error'] and r['pagesFetched'] == 0


def test_partial_fetch_keeps_old_csv_and_failure_receipt(env):
    calls = {'n': 0}

    def fake_fetch(p):
        calls['n'] += 1
        if p == 1:
            return _html(_rows(50, 'A'), header=True)
        raise RuntimeError('page 2 network down')

    fetch_emsd.fetch_page = fake_fetch
    old = env / 'emsd_空調能源標籤.csv'
    old.write_text('OLD-CSV', encoding='utf-8')
    with pytest.raises(SystemExit) as exc:
        fetch_emsd.main()
    assert exc.value.code == 1
    assert old.read_text(encoding='utf-8') == 'OLD-CSV'
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and r['aborted'] is True
    assert r['pagesFetched'] == 1 and r['pagesExpected'] == 2


def test_help_does_not_fetch_or_touch_data(env, monkeypatch, capsys):
    """--help 只印用法，絶不連網、不改 CSV／收據（防誤觸真抓）"""
    def boom(*args, **kwargs):
        raise AssertionError('--help 唔應該觸發任何網絡抓取')

    monkeypatch.setattr(fetch_emsd, 'fetch_page', boom)
    monkeypatch.setattr(fetch_emsd.sys, 'argv', ['fetch_emsd.py', '--help'])
    fetch_emsd.main()
    out = capsys.readouterr().out
    assert '用法' in out
    assert not (env / 'emsd_receipt.json').exists()
    assert not (env / 'emsd_空調能源標籤.csv').exists()


def test_write_receipt_atomic_tmp_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_emsd, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    outcome = {'success': False, 'pagesExpected': 1, 'pagesFetched': 0, 'totalRows': 0,
               'aborted': True, 'error': 'x'}
    fetch_emsd.write_receipt(outcome, [], csv_path=None, retrieved_at='2026-09-20T18:59:57Z')
    assert not os.path.exists(fetch_emsd.RECEIPT_PATH + '.tmp')
    r = _receipt(fetch_emsd.RECEIPT_PATH)
    assert r['retrievedAt'] == '2026-09-20T18:59:57Z'


# ---------------------------------------------------------------- journal／fail-closed



# ---------------------------------------------------------------- journal／fail-closed

def test_journal_recovery_restores_all_targets(tmp_path, monkeypatch):
    """crash 後啟動：journal 存在 → 三份資料還原至提交前 bytes，journal 移除"""
    monkeypatch.setattr(fetch_emsd, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch_emsd, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    csv = tmp_path / 'emsd_空調能源標籤.csv'
    new_models = tmp_path / 'new_models.json'
    queue = tmp_path / 'update_queue.json'
    old = {csv: b'OLD-CSV', new_models: b'{"updated":"old","models":[]}', queue: b'{"stage":0,"models":[]}'}
    # 模擬 crash 後狀態：三份檔已換成新 bytes，journal 仍在
    csv.write_bytes(b'NEW-CSV')
    new_models.write_bytes(b'{"updated":"new","models":[1]}')
    queue.write_bytes(b'{"stage":1,"models":["X"]}')
    import base64
    journal = {'version': 1, 'targets': {
        str(p): (None if d is None else base64.b64encode(d).decode()) for p, d in old.items()}}
    (tmp_path / 'dataset_commit.journal').write_text(json.dumps(journal), encoding='utf-8')
    assert fetch_emsd.recover_commit_journal() is True
    assert csv.read_bytes() == b'OLD-CSV'
    assert new_models.read_bytes() == old[new_models]
    assert queue.read_bytes() == old[queue]
    assert not (tmp_path / 'dataset_commit.journal').exists()


def test_rollback_incomplete_precise_state_then_journal_recovery(tmp_path, monkeypatch):
    """回滾失敗：精確部分狀態 + journal 保留；解除故障後 recovery 完整還原。

    target 次序 = [csv, new_models, queue]；注入：csv replace 成功、new_models replace
    失敗、之後所有 rollback replace 失敗。因此精確狀態係：
      csv = NEW（已提交）、new_models = OLD、queue = OLD、journal 存在。
    """
    import base64
    monkeypatch.setattr(fetch_emsd, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch_emsd, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    csv = tmp_path / 'emsd_空調能源標籤.csv'
    new_models = tmp_path / 'new_models.json'
    queue = tmp_path / 'update_queue.json'
    old = {csv: b'OLD-CSV',
           new_models: b'{"updated":"old","models":[]}',
           queue: b'{"stage":0,"models":[]}'}
    for p, data in old.items():
        p.write_bytes(data)
    plan = {'new_models': {'updated': 'new', 'models': []}, 'queue': {'stage': 0, 'models': []},
            'added': []}
    real_replace = os.replace
    state = {'failed': False}

    def fake_replace(src, dst):
        s, d = str(src), str(dst)
        if not state['failed'] and d.endswith('new_models.json') and s.endswith('.tmp'):
            state['failed'] = True
            raise OSError('inject')
        if state['failed'] and (d.endswith('emsd_空調能源標籤.csv') or d.endswith('new_models.json')):
            raise OSError('rollback fails')
        return real_replace(src, dst)

    monkeypatch.setattr(fetch_emsd.os, 'replace', fake_replace)
    with pytest.raises(fetch_emsd.DatasetCommitError) as exc:
        fetch_emsd.commit_dataset(['品牌', '型號'], [['a', 'b']], str(csv), plan)
    assert exc.value.rolled_back is False

    journal = tmp_path / 'dataset_commit.journal'
    assert journal.exists(), '回滾失敗必須保留 journal'
    data = json.loads(journal.read_text(encoding='utf-8'))
    assert set(data['targets'].keys()) == {str(p) for p in old}
    for p, old_bytes in old.items():
        assert base64.b64decode(data['targets'][str(p)]) == old_bytes
    # 精確部分狀態：csv 已換新、new_models／queue 未換
    assert csv.read_bytes() != old[csv], 'csv 應該已提交（部分狀態）'
    assert new_models.read_bytes() == old[new_models]
    assert queue.read_bytes() == old[queue]

    # 解除故障 → recovery 完整還原 + journal 移除
    monkeypatch.setattr(fetch_emsd.os, 'replace', real_replace)
    assert fetch_emsd.recover_commit_journal() is True
    for p, old_bytes in old.items():
        assert p.read_bytes() == old_bytes, f'{p.name} 應該還原至提交前 bytes'
    assert not journal.exists()


def test_journal_recovery_failure_keeps_journal(tmp_path, monkeypatch):
    """recovery 本身失敗：raise、journal 保留，唔可以報 restored。"""
    import base64
    monkeypatch.setattr(fetch_emsd, 'BASE_DIR', str(tmp_path))
    csv = tmp_path / 'emsd_空調能源標籤.csv'
    csv.write_bytes(b'CURRENT')
    journal = tmp_path / 'dataset_commit.journal'
    journal.write_text(json.dumps({'version': 1, 'targets': {
        str(csv): base64.b64encode(b'OLD-CSV').decode()}}), encoding='utf-8')

    def boom(src, dst):
        raise OSError('disk error')

    monkeypatch.setattr(fetch_emsd.os, 'replace', boom)
    with pytest.raises(RuntimeError, match='恢復失敗'):
        fetch_emsd.recover_commit_journal()
    assert journal.exists(), 'recovery 失敗必須保留 journal'
    assert csv.read_bytes() == b'CURRENT'


def test_bad_json_sidecars_not_silently_reset(env):
    (env / 'new_models.json').write_text('{broken', encoding='utf-8')
    (env / 'update_queue.json').write_text('{"stage": 0, "models": []}', encoding='utf-8')
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    assert not (env / 'emsd_空調能源標籤.csv').exists()
    r = _receipt(env / 'emsd_receipt.json')
    assert r['success'] is False and 'new-model detection failed' in r['error']


def test_bad_queue_json_not_silently_reset(env):
    (env / 'update_queue.json').write_text('{broken', encoding='utf-8')
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    assert not (env / 'emsd_空調能源標籤.csv').exists()


def test_success_receipt_write_failure_blocks(env, monkeypatch):
    """成功收據寫唔到：exit 1，唔可以當成功（下游 hash 綁定會阻斷）"""
    real = fetch_emsd.write_receipt

    def flaky(outcome, per_page, csv_path=None, retrieved_at=None):
        if csv_path is not None:
            raise OSError('receipt disk full')
        return real(outcome, per_page, csv_path, retrieved_at)

    monkeypatch.setattr(fetch_emsd, 'write_receipt', flaky)
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    assert (env / 'emsd_空調能源標籤.csv').exists()  # 資料已提交
    assert not (env / 'emsd_receipt.json').exists()  # 但無成功收據
