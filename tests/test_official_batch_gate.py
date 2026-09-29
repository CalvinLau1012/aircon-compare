# -*- coding: utf-8 -*-
"""官網批次推進閘門回歸（strict receipt／queue 綁定／fail-closed）"""
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SPEC = __import__('importlib.util').util.spec_from_file_location(
    'run_official_batch_mod', os.path.join(BASE, 'scripts', 'run_official_batch.py'))
rob = __import__('importlib.util').util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rob)

import fetch_shew  # noqa: E402
import fetch_rasonic  # noqa: E402
import fetch_carrier  # noqa: E402
import fetch_general  # noqa: E402
import fetch_official  # noqa: E402
import fetch_specs  # noqa: E402


def test_validate_output_rules(tmp_path):
    missing = tmp_path / 'nope.json'
    ok, reason = rob.validate_output(str(missing))
    assert not ok and '唔存在' in reason
    for name, body in (('true.json', 'true'), ('one.json', '1'), ('str.json', '"ok"')):
        p = tmp_path / name
        p.write_text(body, encoding='utf-8')
        ok, reason = rob.validate_output(str(p))
        assert not ok and 'object／array' in reason, (name, reason)
    empty = tmp_path / 'empty.json'
    empty.write_text('{}', encoding='utf-8')
    ok, reason = rob.validate_output(str(empty))
    assert not ok and '空' in reason
    good = tmp_path / 'good.json'
    good.write_text('{"m": {"size": "100x200x300"}}', encoding='utf-8')
    ok, reason = rob.validate_output(str(good))
    assert ok and reason == ''


# ---------------------------------------------------------------- strict marker

def _marker(**over):
    m = {'schemaVersion': 1, 'script': 'fake.py', 'attempted': 1, 'succeeded': 1,
         'failed': 0, 'succeededModels': ['M1'], 'failedModels': [],
         'alreadyVerified': [], 'covers': ['M1']}
    m.update(over)
    return m


def test_validate_marker_strict_negative_cases():
    evidence = {rob.norm_model('M1'): True}
    assert rob.validate_marker(_marker(), 'fake.py', evidence) == []
    # phantom covers（輸出冇 evidence）
    assert rob.validate_marker(_marker(), 'fake.py', {})
    # wrong script
    assert rob.validate_marker(_marker(script='evil.py'), 'fake.py', evidence)
    # failed > 0 而 rc=0 都要阻斷
    assert rob.validate_marker(_marker(failed=1, failedModels=['M9'],
                                       attempted=2, succeeded=1), 'fake.py', evidence)
    # count/list mismatch
    assert rob.validate_marker(_marker(succeeded=2), 'fake.py', evidence)
    assert rob.validate_marker(_marker(covers=['X9']), 'fake.py', evidence)
    # duplicate canonical
    assert rob.validate_marker(_marker(succeededModels=['M1', 'm-1'], succeeded=2,
                                       attempted=2, covers=['M1', 'm-1']), 'fake.py',
                               {rob.norm_model('M1'): True, rob.norm_model('m-1'): True})
    # covers 唔等於 union
    assert rob.validate_marker(_marker(succeededModels=[], succeeded=0, attempted=0,
                                       alreadyVerified=['M1'], covers=[]), 'fake.py', evidence)
    # schemaVersion
    assert rob.validate_marker(_marker(schemaVersion=2), 'fake.py', evidence)


# ---------------------------------------------------------------- wrapper（真 subprocess fixture）

FAKE_SCRIPT = '''\
# -*- coding: utf-8 -*-
import json, os, sys
OUT = os.environ["FAKE_OUT"]
out = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
mode = os.environ.get("FAKE_MODE", "covered")
def emit(payload):
    print("AIRCON_FETCH_RECEIPT " + json.dumps(payload, ensure_ascii=False))
BASE = {"schemaVersion": 1, "script": "fake.py", "attempted": 0, "succeeded": 0, "failed": 0,
        "succeededModels": [], "failedModels": [], "alreadyVerified": [], "covers": []}
if mode == "covered":
    out["M1"] = {"size": "100x200x300"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, attempted=1, succeeded=1, succeededModels=["M1"], covers=["M1"]))
elif mode == "zero":
    emit(dict(BASE))
elif mode == "zero_verified":
    out.setdefault("M1", {"size": "100x200x300"})
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, alreadyVerified=["M1"], covers=["M1"]))
elif mode == "nocover":
    out["M1"] = {"size": "100x200x300"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, attempted=1, succeeded=1, succeededModels=["M1"], covers=["M1"]))
elif mode == "phantom":
    emit(dict(BASE, attempted=1, succeeded=1, succeededModels=["M9"], covers=["M9"]))
elif mode == "coverage_pending":
    out.setdefault("M1", {"size": "100x200x300"})
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"],
              failureReasons={"M1": "http-404"},
              coveragePendingModels=["M1"], coveragePendingReasons={"M1": "http-404"}))
elif mode == "hard_reason":
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"],
              failureReasons={"M1": "network-error"}))
elif mode == "missing_reasons":
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"]))
elif mode == "pending_not_failed":
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"],
              failureReasons={"M1": "http-404"},
              coveragePendingModels=["M9"], coveragePendingReasons={"M9": "http-404"}))
elif mode == "pending_bad_reason":
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"],
              failureReasons={"M1": "http-404"},
              coveragePendingModels=["M1"], coveragePendingReasons={"M1": "http-500"}))
elif mode == "pending_reason_mismatch":
    emit(dict(BASE, attempted=1, failed=1, failedModels=["M1"],
              failureReasons={"M1": "network-error"},
              coveragePendingModels=["M1"], coveragePendingReasons={"M1": "http-404"}))
elif mode == "skip_mismatch":
    emit(dict(BASE, skipped=5))
elif mode == "wrong_script":
    emit(dict(BASE, script="evil.py", attempted=1, succeeded=1, succeededModels=["M1"],
              covers=["M1"]))
elif mode == "failed_rc0":
    out["M1"] = {"size": "100x200x300"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, attempted=2, succeeded=1, failed=1, succeededModels=["M1"],
              failedModels=["M9"], covers=["M1"]))
elif mode == "count_mismatch":
    emit(dict(BASE, attempted=2, succeeded=2, succeededModels=["M1"], covers=["M1"]))
elif mode == "fail":
    print("boom", file=sys.stderr)
    sys.exit(3)
elif mode == "queuemutate":
    q = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_queue.json")
    open(q, "w", encoding="utf-8").write('{"stage": 1, "models": ["CHANGED"]}')
    out["M1"] = {"size": "100x200x300"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    emit(dict(BASE, attempted=1, succeeded=1, succeededModels=["M1"], covers=["M1"]))
elif mode == "nomarker":
    out["M1"] = {"size": "100x200x300"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    sys.exit(0)
else:
    raise SystemExit("unknown FAKE_MODE")
'''


def _setup_wrapper(tmp_path, monkeypatch, mode, queue_models=('M1',), queue_stage=1,
                   old_output=True, advance_rc=0, advance_ran=None):
    repo = tmp_path
    (repo / 'fake.py').write_text(FAKE_SCRIPT, encoding='utf-8')
    out = repo / 'fake_official.json'
    if old_output:
        out.write_text('{"OLD": {"size": "old"}}', encoding='utf-8')
    (repo / 'update_queue.json').write_text(
        json.dumps({'stage': queue_stage, 'models': list(queue_models)}), encoding='utf-8')
    marker = f'open(r"{advance_ran}", "w").write("ran")\n' if advance_ran else ''
    (repo / 'advance_queue.py').write_text(
        'import sys, json, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_queue.json")\n'
        + marker +
        f'sys.exit({advance_rc})\n', encoding='utf-8')
    monkeypatch.setattr(rob, 'BASE', str(repo))
    monkeypatch.setattr(rob, 'STAGES',
                        {1: {'scripts': ['fake.py'], 'outputs': ['fake_official.json']}})
    monkeypatch.setenv('FAKE_OUT', str(out))
    monkeypatch.setenv('FAKE_MODE', mode)
    return repo, out


def _run_wrapper(repo):
    return rob.main(['--stage', '1', '--receipt', str(repo / 'receipt.json')])


def test_zero_attempt_with_old_snapshot_is_pending_coverage_and_does_not_advance(tmp_path, monkeypatch):
    # D1-B：所有腳本／receipt／輸出本身成功，但 queue model 無覆蓋 → pending coverage。
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'zero')
    assert _run_wrapper(repo) == 0
    q = json.load(open(repo / 'update_queue.json', encoding='utf-8'))
    assert q['stage'] == 1 and q['models'] == ['M1'], 'queue stage／models 必須原樣保留'
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'queue-kept-pending-coverage'
    assert receipt['coveragePending'] is True
    assert receipt['advanced'] is False


def test_zero_attempt_with_verified_evidence_advances(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'zero_verified',
                               advance_ran=tmp_path / 'advance-ran.txt')
    assert _run_wrapper(repo) == 0
    assert (tmp_path / 'advance-ran.txt').exists(), 'alreadyVerified + evidence 應該可以推進'
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'advanced' and receipt['advanced'] is True


def test_covered_queue_models_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'covered',
                               advance_ran=tmp_path / 'advance-ran.txt')
    assert _run_wrapper(repo) == 0
    assert (tmp_path / 'advance-ran.txt').exists()
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['queueHashBefore'] == receipt['queueHashAfter']


def test_uncovered_queue_model_pending_coverage_keeps_queue(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'nocover', queue_models=('M2',))
    assert _run_wrapper(repo) == 0, '純 coverage 缺口應走 pending，唔可以當硬失敗'
    q = json.load(open(repo / 'update_queue.json', encoding='utf-8'))
    assert q['stage'] == 1 and q['models'] == ['M2']
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'queue-kept-pending-coverage'
    assert receipt['missingModels'] == ['M2']
    assert receipt['missingCanonicalModels'] == ['M2']


def test_phantom_covers_do_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'phantom')
    assert _run_wrapper(repo) == 1
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert any('evidence' in f for f in receipt['failures']), receipt['failures']


def test_wrong_script_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'wrong_script')
    assert _run_wrapper(repo) == 1


def test_failed_count_with_rc0_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'failed_rc0')
    assert _run_wrapper(repo) == 1
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert any('failed=1' in f or 'failed' in f for f in receipt['failures'])


def test_count_mismatch_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'count_mismatch')
    assert _run_wrapper(repo) == 1


def test_queue_mutated_during_run_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'queuemutate')
    assert _run_wrapper(repo) == 1
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert any('被改動' in f for f in receipt['failures'])


def test_receipt_output_hash_race_blocks_before_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'covered',
                               advance_ran=tmp_path / 'advance-ran.txt')
    real = rob._sha256_file
    calls = {'n': 0}

    def racy(path):
        if str(path).endswith('fake_official.json'):
            calls['n'] += 1
            if calls['n'] >= 2:
                return '0' * 64
        return real(path)

    monkeypatch.setattr(rob, '_sha256_file', racy)
    assert rob.main(['--stage', '1', '--receipt', str(tmp_path / 'receipt.json')]) == 1
    assert not (tmp_path / 'advance-ran.txt').exists(), 'hash race 唔可以 advance'


def test_ready_receipt_write_failure_blocks_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'covered',
                               advance_ran=tmp_path / 'advance-ran.txt')
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    rc = rob.main(['--stage', '1', '--receipt', str(blocker / 'receipt.json')])
    assert rc == 1
    assert not (tmp_path / 'advance-ran.txt').exists(), 'receipt 寫唔到絕不可 advance'


def test_missing_marker_blocks(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'nomarker')
    assert _run_wrapper(repo) == 1
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert any('machine receipt' in f for f in receipt['failures'])


def test_script_failure_blocks_even_with_valid_old_snapshot(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'fail')
    old = out.read_bytes()
    assert _run_wrapper(repo) == 1
    assert out.read_bytes() == old


def test_stage_mismatch_blocks(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'covered', queue_stage=2)
    assert _run_wrapper(repo) == 1


def test_advance_failure_keeps_ready_receipt(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'covered', advance_rc=1)
    assert _run_wrapper(repo) == 1
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['advanced'] is False
    assert receipt['decision'] == 'ready-to-advance'
    assert any('advance_queue.py' in f for f in receipt['failures'])


# ---------------------------------------------------------------- 各品牌 fixture

def test_fetch_rasonic_blank_page_blocks_and_keeps_snapshot(tmp_path, monkeypatch):
    (tmp_path / 'rasonic_urls.json').write_text(json.dumps(['https://example.com/p/1']),
                                                encoding='utf-8')
    out = tmp_path / 'rasonic_official.json'
    old = b'{"OLD":1}\n'
    out.write_bytes(old)
    monkeypatch.setattr(fetch_rasonic, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_rasonic.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_rasonic, 'get', lambda url: '<html></html>')
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old


def test_fetch_carrier_blank_page_blocks_and_keeps_snapshot(tmp_path, monkeypatch):
    (tmp_path / 'carrier_urls.json').write_text(json.dumps(['https://example.com/p/1']),
                                                encoding='utf-8')
    out = tmp_path / 'carrier_official.json'
    old = b'{"OLD":1}\n'
    out.write_bytes(old)
    monkeypatch.setattr(fetch_carrier, '__file__', str(tmp_path / 'carrier.py'))
    monkeypatch.setattr(fetch_carrier.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_carrier, 'get', lambda url: '')
    with pytest.raises(SystemExit) as exc:
        fetch_carrier.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old


def test_fetch_general_blank_page_blocks_and_keeps_snapshot(tmp_path, monkeypatch):
    out = tmp_path / 'general_official.json'
    old = b'{"OLD":1}\n'
    out.write_bytes(old)
    monkeypatch.setattr(fetch_general, '__file__', str(tmp_path / 'general.py'))
    monkeypatch.setattr(fetch_general.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_general, 'get', lambda url: '<html><body>登入</body></html>')
    with pytest.raises(SystemExit) as exc:
        fetch_general.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old


def test_fetch_official_blank_pages_block_and_keep_snapshot(tmp_path, monkeypatch):
    out = tmp_path / 'official_specs.json'
    old = b'{"OLD":1}\n'
    out.write_bytes(old)
    monkeypatch.setattr(fetch_official, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_official.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_official, 'get', lambda url: '<html></html>')
    with pytest.raises(SystemExit) as exc:
        fetch_official.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old


def test_fetch_specs_all_failed_blocks_and_keeps_snapshot(tmp_path, monkeypatch):
    out = tmp_path / 'specs.json'
    old = b'{"OLD":1}\n'
    out.write_bytes(old)
    monkeypatch.setattr(fetch_specs, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_specs.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_specs, 'get', lambda url: '')
    monkeypatch.setattr(fetch_specs, 'fetch_fortress', lambda model: {'error': 'blank page'})
    with pytest.raises(SystemExit) as exc:
        fetch_specs.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old


def test_fetch_shew_one_of_three_targets_fails_blocks_and_keeps_snapshot(tmp_path, monkeypatch):
    (tmp_path / 'shew_urls.json').write_text(
        json.dumps(['https://example.com/a', 'https://example.com/b', 'https://example.com/c']),
        encoding='utf-8')
    out = tmp_path / 'shew_official.json'
    old_bytes = b'{"OLD":1}\n'
    out.write_bytes(old_bytes)
    monkeypatch.setattr(fetch_shew, '__file__', str(tmp_path / 'shew.py'))
    monkeypatch.setattr(fetch_shew.time, 'sleep', lambda _s: None)
    state = {'n': 0}

    def fake_get(url):
        state['n'] += 1
        if state['n'] == 2:
            raise RuntimeError('target b failed')
        return ('<p>體積 (高x闊x深): 100x200x300 毫米 淨重 9.5 公斤 '
                '3 年全機保修, 5 年壓縮機保修 冷暖 無線遙控 Wi-Fi</p>')

    monkeypatch.setattr(fetch_shew, 'get', fake_get)
    with pytest.raises(SystemExit) as exc:
        fetch_shew.main()
    assert exc.value.code == 1
    assert out.read_bytes() == old_bytes


def test_fetch_shew_all_three_success_writes_snapshot(tmp_path, monkeypatch):
    (tmp_path / 'shew_urls.json').write_text(
        json.dumps(['https://example.com/a', 'https://example.com/b', 'https://example.com/c']),
        encoding='utf-8')
    out = tmp_path / 'shew_official.json'
    out.write_bytes(b'{"OLD":1}\n')
    monkeypatch.setattr(fetch_shew, '__file__', str(tmp_path / 'shew.py'))
    monkeypatch.setattr(fetch_shew.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_shew, 'get', lambda url: (
        '<p>體積 (高x闊x深): 100x200x300 毫米 淨重 9.5 公斤 '
        '3 年全機保修, 5 年壓縮機保修 冷暖 無線遙控 Wi-Fi</p>'))
    fetch_shew.main()
    data = json.loads(out.read_text(encoding='utf-8'))
    assert len(data) == 3
    assert not (tmp_path / 'shew_official.json.tmp').exists()


# ---------------------------------------------------------------- D1-B coverage pending（2026-09-29 repair）

def _wrapper_receipt(repo):
    return json.load(open(repo / 'receipt.json', encoding='utf-8'))


def test_confirmed_404_coverage_pending_keeps_queue_and_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'coverage_pending',
                               advance_ran=tmp_path / 'advance-ran.txt')
    assert _run_wrapper(repo) == 0, '確認 404 嘅純 coverage gap 應可發布（exit 0）'
    assert not (tmp_path / 'advance-ran.txt').exists(), 'coverage pending 唔可以 advance queue'
    q = json.load(open(repo / 'update_queue.json', encoding='utf-8'))
    assert q == {'stage': 1, 'models': ['M1']}, 'queue stage／models 必須原樣保留'
    after = json.loads(out.read_text(encoding='utf-8'))
    assert 'OLD' in after, '舊規格必須保留（唔可以被部分結果覆寫）'
    receipt = _wrapper_receipt(repo)
    assert receipt['decision'] == 'queue-kept-pending-coverage'
    assert receipt['coveragePending'] is True
    assert receipt['coveragePendingFromScripts'] == ['M1']
    assert receipt['coveragePendingReasons'] == {'M1': 'http-404'}
    assert receipt['failures'] == [], 'coverage pending 唔應該當硬失敗'


def test_hard_failure_reason_does_not_advance(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'hard_reason')
    assert _run_wrapper(repo) == 1
    receipt = _wrapper_receipt(repo)
    assert receipt['decision'] == 'queue-kept-fail-closed'
    assert any('coverage pending' in f or 'failed=1' in f for f in receipt['failures']), receipt['failures']


def test_failed_without_failure_reasons_fails_closed(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'missing_reasons')
    assert _run_wrapper(repo) == 1
    receipt = _wrapper_receipt(repo)
    assert any('failureReasons' in f for f in receipt['failures']), receipt['failures']


def test_pending_not_subset_of_failed_fails_closed(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'pending_not_failed')
    assert _run_wrapper(repo) == 1
    receipt = _wrapper_receipt(repo)
    assert any('subset' in f for f in receipt['failures']), receipt['failures']


def test_pending_reason_not_allowlisted_fails_closed(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'pending_bad_reason')
    assert _run_wrapper(repo) == 1
    receipt = _wrapper_receipt(repo)
    assert any('未知 reason' in f for f in receipt['failures']), receipt['failures']


def test_pending_reason_mismatch_with_failure_reasons_fails_closed(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'pending_reason_mismatch')
    assert _run_wrapper(repo) == 1
    receipt = _wrapper_receipt(repo)
    assert any('\u5514\u4e00\u81f4' in f for f in receipt['failures']), receipt['failures']


def test_receipt_skipped_mismatch_fails_closed(tmp_path, monkeypatch):
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'skip_mismatch')
    assert _run_wrapper(repo) == 1, 'skipped != len(alreadyVerified) 要阻斷'


def test_coverage_pending_does_not_touch_blacklist_or_biggo(tmp_path, monkeypatch):
    """D1-B：coverage pending 唔會自動淘汰／黑名單，亦唔會呼叫任何 BigGo 入口。"""
    import crawl_utils
    calls = []
    monkeypatch.setattr(crawl_utils, 'fetch', lambda *a, **k: calls.append(a))
    repo, out = _setup_wrapper(tmp_path, monkeypatch, 'coverage_pending')
    bl = tmp_path / 'model_blacklist.json'
    bl.write_text('{"version": 1, "models": {}}', encoding='utf-8')
    before = bl.read_bytes()
    assert _run_wrapper(repo) == 0
    assert bl.read_bytes() == before
    assert calls == []


# ---------------------------------------------------------------- Frostar FR-KS（2026-09-29 repair）

import urllib.error  # noqa: E402

FR_KS_SLUG = ('https://www.rasonicshop.hk/products/frostar-fr-ks{n}-r32-inverter-'
              'cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control-34hp')


def _fr_html(model, price, sku='sku-1'):
    payload = {
        '@type': 'Product',
        'name': f'Frostar 冰雪牌 {model} R32變頻淨冷窗口式冷氣機 (1匹)',
        'description': '附獨立抽濕模式及無線遙控',
        'sku': sku,
        'offers': {'price': price, 'availability': 'http://schema.org/InStock'},
    }
    return ('<html><head><script type="application/ld+json">'
            + json.dumps(payload, ensure_ascii=False) + '</script></head><body></body></html>')


def _rasonic_env(tmp_path, monkeypatch, urls, emsd_models=('FR-KS7', 'FR-KS9', 'FR-KS12',
                                                            'FR-KS18')):
    (tmp_path / 'rasonic_urls.json').write_text(json.dumps(urls), encoding='utf-8')
    monkeypatch.setattr(fetch_rasonic, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_rasonic.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_rasonic, 'load_models', lambda: list(emsd_models))
    return tmp_path / 'rasonic_official.json'


def _stdout_receipt(capsys):
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.startswith('AIRCON_FETCH_RECEIPT ')]
    assert len(lines) == 1, f'receipt lines={len(lines)}'
    return json.loads(lines[0][len('AIRCON_FETCH_RECEIPT '):])


def test_fetch_rasonic_parses_fr_ks_with_identity_evidence(tmp_path, monkeypatch, capsys):
    urls = [FR_KS_SLUG.format(n=n) for n in (7, 9, 12, 18)]
    out_path = _rasonic_env(tmp_path, monkeypatch, urls)
    prices = {7: 2900, 9: 3700, 12: 4700, 18: 5900}
    monkeypatch.setattr(fetch_rasonic, 'get',
                        lambda url: _fr_html('FR-KS' + url.split('-ks')[1].split('-')[0],
                                             prices[int(url.split('-ks')[1].split('-')[0])]))
    fetch_rasonic.main()
    data = json.loads(out_path.read_text(encoding='utf-8'))
    assert set(data) == {'FR-KS7', 'FR-KS9', 'FR-KS12', 'FR-KS18'}
    assert data['FR-KS7']['price'] == 'HK$2900'
    assert data['FR-KS18']['price'] == 'HK$5900'
    assert data['FR-KS7']['evidence']['source'] == 'json-ld-product'
    assert data['FR-KS7']['evidence']['modelInName'] is True
    assert data['FR-KS7']['evidence']['modelInConfiguredUrl'] is True
    assert data['FR-KS7']['evidence']['urlSlugToken'] == 'FR-KS7'
    assert data['FR-KS7']['evidence']['identityBasis'] == \
        'configured-url-slug+product-json-ld-name'
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 4 and receipt['succeeded'] == 4
    assert receipt['failed'] == 0 and receipt['skipped'] == 0
    assert set(receipt['succeededModels']) == set(data)
    assert receipt['covers'] == sorted({rob.norm_model(m) for m in data})


def test_fetch_rasonic_confirmed_404_is_coverage_pending_and_keeps_old(tmp_path, monkeypatch,
                                                                       capsys):
    url = FR_KS_SLUG.format(n=18)
    out_path = _rasonic_env(tmp_path, monkeypatch, [url])
    old = {'FR-KS18': {'name': 'old', 'size': '1x1x1'}}
    out_path.write_text(json.dumps(old), encoding='utf-8')
    before = out_path.read_bytes()

    def not_found(_url):
        raise urllib.error.HTTPError(_url, 404, 'Not Found', {}, None)

    monkeypatch.setattr(fetch_rasonic, 'get', not_found)
    fetch_rasonic.main()
    assert out_path.read_bytes() == before, 'coverage pending 要保留舊規格 bytes'
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 1 and receipt['succeeded'] == 0 and receipt['failed'] == 1
    assert receipt['failedModels'] == ['FR-KS18']
    assert receipt['failureReasons'] == {'FR-KS18': 'http-404'}
    assert receipt['coveragePendingModels'] == ['FR-KS18']
    assert receipt['coveragePendingReasons'] == {'FR-KS18': 'http-404'}


def test_fetch_rasonic_transport_error_is_hard_failure(tmp_path, monkeypatch):
    url = FR_KS_SLUG.format(n=7)
    out_path = _rasonic_env(tmp_path, monkeypatch, [url])
    out_path.write_text(json.dumps({'FR-KS7': {'size': 'old'}}), encoding='utf-8')
    monkeypatch.setattr(fetch_rasonic, 'get',
                        lambda _url: (_ for _ in ()).throw(RuntimeError('network down')))
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1


def test_fetch_rasonic_200_without_product_jsonld_is_hard_failure(tmp_path, monkeypatch):
    url = FR_KS_SLUG.format(n=7)
    _rasonic_env(tmp_path, monkeypatch, [url])
    monkeypatch.setattr(fetch_rasonic, 'get', lambda _url: '<html><body>登入</body></html>')
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1


def test_fetch_rasonic_model_url_mismatch_is_hard_failure(tmp_path, monkeypatch):
    url = FR_KS_SLUG.format(n=7)
    _rasonic_env(tmp_path, monkeypatch, [url])
    monkeypatch.setattr(fetch_rasonic, 'get', lambda _url: _fr_html('FR-KS9', 3700))
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1, '型號同 URL slug 唔一致唔可以當成功'


def test_fetch_rasonic_404_for_unknown_model_is_hard_failure(tmp_path, monkeypatch):
    url = FR_KS_SLUG.format(n=7)
    _rasonic_env(tmp_path, monkeypatch, [url], emsd_models=('RC-X7U',))

    def not_found(_url):
        raise urllib.error.HTTPError(_url, 404, 'Not Found', {}, None)

    monkeypatch.setattr(fetch_rasonic, 'get', not_found)
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1, 'URL 型號唔喺 EMSD 登記 → 唔可以當 coverage pending'


# ---------------------------------------------------------------- fetch_official accounting

def test_fetch_official_all_skipped_receipt_counts_and_lists_are_exact(tmp_path, monkeypatch,
                                                                       capsys):
    (tmp_path / 'official_specs.json').write_text(
        json.dumps({'RA-10RF': {'size': '1x1x1'}}), encoding='utf-8')
    monkeypatch.setattr(fetch_official, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_official.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_official, 'PANASONIC', [])
    monkeypatch.setattr(fetch_official, 'COMFEE_MODELS', [])
    monkeypatch.setattr(fetch_official, 'HITACHI_PAGES', ['https://example.com/list'])
    monkeypatch.setattr(fetch_official, 'get',
                        lambda _url: '<html><body>RA-10RF 遙控 淨冷</body></html>')
    before = (tmp_path / 'official_specs.json').read_bytes()
    assert fetch_official.main() is None
    assert (tmp_path / 'official_specs.json').read_bytes() == before
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 0 and receipt['succeeded'] == 0 and receipt['failed'] == 0
    assert receipt['skipped'] == len(receipt['alreadyVerified']) == 1
    assert receipt['alreadyVerified'] == ['RA-10RF']
    assert receipt['covers'] == ['RA10RF']


def test_fetch_official_listing_failure_is_hard_and_counted_consistently(tmp_path, monkeypatch,
                                                                        capsys):
    (tmp_path / 'official_specs.json').write_text('{"OLD": {"size": "old"}}', encoding='utf-8')
    monkeypatch.setattr(fetch_official, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_official.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_official, 'PANASONIC', [])
    monkeypatch.setattr(fetch_official, 'COMFEE_MODELS', [])
    monkeypatch.setattr(fetch_official, 'HITACHI_PAGES', ['https://example.com/list'])
    monkeypatch.setattr(fetch_official, 'get',
                        lambda _url: (_ for _ in ()).throw(RuntimeError('list down')))
    with pytest.raises(SystemExit) as exc:
        fetch_official.main()
    assert exc.value.code == 1
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == receipt['failed'] == 1
    assert receipt['failedModels'] == ['HITACHI-LISTING:https://example.com/list']
    assert receipt['failureReasons'] == {
        'HITACHI-LISTING:https://example.com/list': 'listing-hard-failure'}


def test_fetch_official_stale_comfee_hyphen_slug_removed_from_targets():
    assert 'cafa-09crn8-pc2' not in fetch_official.COMFEE_MODELS, 'stale 404 slug 唔可以再試'
    assert 'cafa-09crn8pc2' in fetch_official.COMFEE_MODELS, '正確官方 slug 必須保留'


# ---------------------------------------------------------------- D1-B：script pending 即使有另一覆蓋都要保留 queue（2026-09-29 返修）

FAKE_MULTI_SCRIPT = '''\
# -*- coding: utf-8 -*-
import json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(BASE, "fake_multi_config.json"), encoding="utf-8"))
spec = cfg[os.path.basename(__file__)]
out = spec["out"]
data = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
data.setdefault("M1", {"size": "100x200x300"})
json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
payload = {"schemaVersion": 1, "script": os.path.basename(__file__), "attempted": 0,
           "succeeded": 0, "failed": 0, "succeededModels": [], "failedModels": [],
           "alreadyVerified": [], "covers": []}
if spec["mode"] == "pending":
    payload.update(attempted=1, failed=1, failedModels=["M1"],
                   failureReasons={"M1": "http-404"},
                   coveragePendingModels=["M1"], coveragePendingReasons={"M1": "http-404"})
elif spec["mode"] == "covered":
    payload.update(attempted=1, succeeded=1, succeededModels=["M1"], covers=["M1"])
elif spec["mode"] == "hard":
    payload.update(attempted=1, failed=1, failedModels=["M9"],
                   failureReasons={"M9": "network-error"})
elif spec["mode"] == "pending_invalid":
    payload.update(attempted=1, failed=1, failedModels=["M1"],
                   failureReasons={"M1": "network-error"},
                   coveragePendingModels=["M1"], coveragePendingReasons={"M1": "http-404"})
else:
    raise SystemExit("unknown mode")
print("AIRCON_FETCH_RECEIPT " + json.dumps(payload, ensure_ascii=False))
'''


def _setup_multi_wrapper(tmp_path, monkeypatch, a_mode, b_mode):
    repo = tmp_path
    (repo / 'fake_a.py').write_text(FAKE_MULTI_SCRIPT, encoding='utf-8')
    (repo / 'fake_b.py').write_text(FAKE_MULTI_SCRIPT, encoding='utf-8')
    (repo / 'fake_a.json').write_text('{"OLD": {"size": "old"}}', encoding='utf-8')
    (repo / 'fake_b.json').write_text('{"OLD": {"size": "old"}}', encoding='utf-8')
    (repo / 'fake_multi_config.json').write_text(json.dumps({
        'fake_a.py': {'out': str(repo / 'fake_a.json'), 'mode': a_mode},
        'fake_b.py': {'out': str(repo / 'fake_b.json'), 'mode': b_mode},
    }), encoding='utf-8')
    (repo / 'update_queue.json').write_text(
        json.dumps({'stage': 1, 'models': ['M1']}), encoding='utf-8')
    (repo / 'advance_queue.py').write_text(
        'import os\n'
        'open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '
        '"advance-ran.txt"), "w").write("ran")\n', encoding='utf-8')
    monkeypatch.setattr(rob, 'BASE', str(repo))
    monkeypatch.setattr(rob, 'STAGES', {
        1: {'scripts': ['fake_a.py', 'fake_b.py'],
            'outputs': ['fake_a.json', 'fake_b.json']}})
    return repo


def test_script_pending_covered_by_another_script_still_keeps_queue(tmp_path, monkeypatch):
    """Script 確認 404 pending，即使 M1 已被另一 script covers 覆蓋，都唔可以 advance。"""
    repo = _setup_multi_wrapper(tmp_path, monkeypatch, 'pending', 'covered')
    rc = rob.main(['--stage', '1', '--receipt', str(repo / 'receipt.json')])
    assert rc == 0, '純 coverage gap（script pending）應可繼續發布'
    assert not (repo / 'advance-ran.txt').exists(), \
        'script coverage pending 必須保留 queue，唔可以因為 missing_coverage 空而 advance'
    q = json.load(open(repo / 'update_queue.json', encoding='utf-8'))
    assert q == {'stage': 1, 'models': ['M1']}, 'queue stage／models 必須原樣保留'
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'queue-kept-pending-coverage'
    assert receipt['advanced'] is False
    assert receipt['missingQueueCoverage'] is False, 'M1 已被另一 script 覆蓋'
    assert receipt['scriptCoveragePending'] is True
    assert receipt['coveragePending'] is True
    assert receipt['coveragePendingFromScripts'] == ['M1']
    assert receipt['failures'] == []


def test_script_pending_plus_hard_failure_still_exits_1(tmp_path, monkeypatch):
    repo = _setup_multi_wrapper(tmp_path, monkeypatch, 'pending', 'hard')
    rc = rob.main(['--stage', '1', '--receipt', str(repo / 'receipt.json')])
    assert rc == 1
    assert not (repo / 'advance-ran.txt').exists()
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'queue-kept-fail-closed'


def test_invalid_marker_cannot_contribute_script_pending(tmp_path, monkeypatch):
    """coveragePendingReasons 同 failureReasons 唔一致 → hard fail，唔可以當 pending。"""
    repo = _setup_multi_wrapper(tmp_path, monkeypatch, 'pending_invalid', 'covered')
    rc = rob.main(['--stage', '1', '--receipt', str(repo / 'receipt.json')])
    assert rc == 1
    assert not (repo / 'advance-ran.txt').exists()
    receipt = json.load(open(repo / 'receipt.json', encoding='utf-8'))
    assert receipt['decision'] == 'queue-kept-fail-closed'
    assert any('唔一致' in f for f in receipt['failures']), receipt['failures']


def test_fetch_rasonic_fr_ks7_does_not_match_longer_fr_ks70_slug(tmp_path, monkeypatch):
    """精確 token：Product FR-KS7 + URL FR-KS70 唔可以因為 substring 被當成功。"""
    url = FR_KS_SLUG.format(n=70)
    _rasonic_env(tmp_path, monkeypatch, [url])
    monkeypatch.setattr(fetch_rasonic, 'get', lambda _url: _fr_html('FR-KS7', 2900))
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1


def test_fetch_rasonic_rc_xg7_does_not_match_longer_rc_xg70_slug(tmp_path, monkeypatch):
    url = 'https://www.rasonicshop.hk/products/rasonic-rc-xg70-window-type-air-conditioner'
    _rasonic_env(tmp_path, monkeypatch, [url], emsd_models=('RC-XG7',))
    monkeypatch.setattr(fetch_rasonic, 'get', lambda _url: _fr_html('RC-XG7', 3900))
    with pytest.raises(SystemExit) as exc:
        fetch_rasonic.main()
    assert exc.value.code == 1


def test_fetch_rasonic_valid_exact_fr_ks_tokens_still_pass(tmp_path, monkeypatch, capsys):
    """四個真實 Frostar slug（精確 token）保持成功，唔可以被精確比對誤殺。"""
    urls = [FR_KS_SLUG.format(n=n) for n in (7, 9, 12, 18)]
    _rasonic_env(tmp_path, monkeypatch, urls)
    answers = {
        'FR-KS7': _fr_html('FR-KS7', 2900),
        'FR-KS9': _fr_html('FR-KS9', 3700),
        'FR-KS12': _fr_html('FR-KS12', 4700),
        'FR-KS18': _fr_html('FR-KS18', 5900),
    }
    monkeypatch.setattr(fetch_rasonic, 'get',
                        lambda url: answers['FR-KS' + url.split('-ks')[1].split('-')[0]])
    fetch_rasonic.main()
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 4 and receipt['succeeded'] == 4 and receipt['failed'] == 0
    assert set(receipt['succeededModels']) == set(answers)


# ---------------------------------------------------------------- pending eligibility（2026-09-29 第二次返修）

def _official_env(tmp_path, monkeypatch, registered, panasonic=(), comfee=(), hitachi=()):
    (tmp_path / 'official_specs.json').write_text('{"OLD": {"size": "old"}}',
                                                  encoding='utf-8')
    monkeypatch.setattr(fetch_official, 'BASE', str(tmp_path))
    monkeypatch.setattr(fetch_official.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(fetch_official, 'PANASONIC', list(panasonic))
    monkeypatch.setattr(fetch_official, 'COMFEE_MODELS', list(comfee))
    monkeypatch.setattr(fetch_official, 'HITACHI_PAGES', list(hitachi))
    monkeypatch.setattr(fetch_official, 'load_models', lambda: list(registered))
    return tmp_path


def _not_found(_url):
    raise urllib.error.HTTPError(_url, 404, 'Not Found', {}, None)


def test_fetch_official_panasonic_registered_404_is_coverage_pending(tmp_path, monkeypatch,
                                                                     capsys):
    url = 'https://www.panasonic.hk/zh-cht/item/9592--cw-sul70ba'
    _official_env(tmp_path, monkeypatch, ['CW-SUL70BA'], panasonic=[('CW-SUL70BA', url)])
    monkeypatch.setattr(fetch_official, 'get', _not_found)
    assert fetch_official.main() is None
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 1 and receipt['failed'] == 1
    assert receipt['failureReasons'] == {'CW-SUL70BA': 'http-404'}
    assert receipt['coveragePendingModels'] == ['CW-SUL70BA']
    assert receipt['coveragePendingReasons'] == {'CW-SUL70BA': 'http-404'}


def test_fetch_official_panasonic_url_mismatch_404_is_hard_failure(tmp_path, monkeypatch,
                                                                   capsys):
    url = 'https://www.panasonic.hk/zh-cht/item/9592--cw-sul70baa'
    _official_env(tmp_path, monkeypatch, ['CW-SUL70BA'], panasonic=[('CW-SUL70BA', url)])
    monkeypatch.setattr(fetch_official, 'get', _not_found)
    with pytest.raises(SystemExit) as exc:
        fetch_official.main()
    assert exc.value.code == 1, 'URL token 同 model 唔一致唔可以 pending'
    receipt = _stdout_receipt(capsys)
    assert receipt['failureReasons'] == {'CW-SUL70BA': 'product-hard-failure'}
    assert 'coveragePendingModels' not in receipt


def test_fetch_official_unregistered_model_404_is_hard_failure(tmp_path, monkeypatch, capsys):
    url = 'https://www.panasonic.hk/zh-cht/item/9592--cw-sul70ba'
    _official_env(tmp_path, monkeypatch, ['OTHERMODEL'], panasonic=[('CW-SUL70BA', url)])
    monkeypatch.setattr(fetch_official, 'get', _not_found)
    with pytest.raises(SystemExit) as exc:
        fetch_official.main()
    assert exc.value.code == 1, '唔喺 EMSD 登記型號唔可以 pending'
    receipt = _stdout_receipt(capsys)
    assert receipt['failureReasons'] == {'CW-SUL70BA': 'product-hard-failure'}


def test_fetch_official_comfee_registered_404_is_coverage_pending(tmp_path, monkeypatch,
                                                                  capsys):
    _official_env(tmp_path, monkeypatch, ['CAFA-09CRN8PC2'], comfee=['cafa-09crn8pc2'])
    monkeypatch.setattr(fetch_official, 'get', _not_found)
    assert fetch_official.main() is None
    receipt = _stdout_receipt(capsys)
    assert receipt['coveragePendingModels'] == ['CAFA-09CRN8PC2']
    assert receipt['failureReasons'] == {'CAFA-09CRN8PC2': 'http-404'}


def test_fetch_official_hitachi_registered_404_is_coverage_pending(tmp_path, monkeypatch,
                                                                   capsys):
    _official_env(tmp_path, monkeypatch, ['RA-10RF'],
                  hitachi=['https://example.com/list'])
    state = {'calls': 0}

    def fake_get(_url):
        state['calls'] += 1
        if state['calls'] == 1:
            return '<html><body>RA-10RF 窗口式冷氣機</body></html>'
        raise urllib.error.HTTPError(_url, 404, 'Not Found', {}, None)

    monkeypatch.setattr(fetch_official, 'get', fake_get)
    assert fetch_official.main() is None
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 1 and receipt['failed'] == 1
    assert receipt['coveragePendingModels'] == ['RA-10RF']
    assert receipt['failureReasons'] == {'RA-10RF': 'http-404'}


def test_fetch_official_non404_success_not_blocked_by_registration(tmp_path, monkeypatch,
                                                                   capsys):
    """非 404 成功唔受登記檢查影響（即使型號唔喺 EMSD set）。"""
    url = 'https://www.panasonic.hk/zh-cht/item/1--cw-notreg'
    _official_env(tmp_path, monkeypatch, ['OTHERMODEL'], panasonic=[('CW-NOTREG', url)])
    monkeypatch.setattr(fetch_official, 'get',
                        lambda _url: '<html><title>CW-NOTREG 淨冷</title></html>')
    assert fetch_official.main() is None
    receipt = _stdout_receipt(capsys)
    assert receipt['attempted'] == 1 and receipt['succeeded'] == 1
    assert receipt['succeededModels'] == ['CW-NOTREG']
    assert receipt['failed'] == 0
