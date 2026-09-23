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
