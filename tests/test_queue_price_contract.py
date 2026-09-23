# -*- coding: utf-8 -*-
"""queue 契約、advance_queue 冪等、prices_meta fail-closed 回歸（無網絡）"""
import importlib.util
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import advance_queue  # noqa: E402
import batch_utils  # noqa: E402
import queue_utils  # noqa: E402
import fetch_emsd  # noqa: E402
_SPEC = importlib.util.spec_from_file_location(
    'rob_for_queue', os.path.join(BASE, 'scripts', 'run_official_batch.py'))
rob = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rob)
_SPEC2 = importlib.util.spec_from_file_location(
    'price_state_mod', os.path.join(BASE, 'scripts', 'price_batch_state.py'))
price_state = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(price_state)


# ---------------------------------------------------------------- 2.1 queue 契約

def test_missing_queue_is_default(tmp_path):
    assert queue_utils.load_queue(str(tmp_path / 'missing.json')) == {'stage': 0, 'models': []}


@pytest.mark.parametrize('body, needle', [
    ('{broken', '解析'),
    ('null', 'object'),
    ('{"models": []}', 'stage'),
    ('{"stage": true, "models": []}', 'stage'),
    ('{"stage": -1, "models": []}', 'stage'),
    ('{"stage": 3, "models": []}', 'stage'),
    ('{"stage": "1", "models": ["A"]}', 'stage'),
    ('{"stage": 0, "models": ["A"]}', 'stage 0'),
    ('{"stage": 1, "models": []}', '非空'),
    ('{"stage": 1, "models": [1]}', '字串'),
    ('{"stage": 1, "models": ["  "]}', '空白'),
    ('{"stage": 1, "models": ["RC-X7U", "rcx7u"]}', '重複'),
    ('{"stage": 1, "models": ["A"], "extra": NaN}', '常數'),
])
def test_queue_contract_rejects_bad(tmp_path, body, needle):
    p = tmp_path / 'q.json'
    p.write_text(body, encoding='utf-8')
    with pytest.raises(queue_utils.QueueError) as exc:
        queue_utils.load_queue(str(p))
    assert needle in str(exc.value)


def test_queue_contract_accepts_valid_and_keeps_extra(tmp_path):
    p = tmp_path / 'q.json'
    p.write_text('{"stage": 1, "models": ["A", "B"], "note": "x"}', encoding='utf-8')
    q = queue_utils.load_queue(str(p))
    assert q['stage'] == 1 and q['models'] == ['A', 'B'] and q['note'] == 'x'
    queue_utils.save_queue(q, str(p))
    assert json.loads(p.read_text(encoding='utf-8'))['note'] == 'x'
    assert not (tmp_path / 'q.json.tmp').exists()


def test_queue_save_failure_keeps_old_bytes(tmp_path, monkeypatch):
    p = tmp_path / 'q.json'
    p.write_text('{"stage": 0, "models": []}', encoding='utf-8')
    before = p.read_bytes()
    monkeypatch.setattr(queue_utils.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    with pytest.raises(queue_utils.QueueError):
        queue_utils.save_queue({'stage': 1, 'models': ['A']}, str(p))
    assert p.read_bytes() == before
    assert not (tmp_path / 'q.json.tmp').exists()


def test_all_entrypoints_share_contract(tmp_path, monkeypatch):
    bad = tmp_path / 'q.json'
    bad.write_text('{"stage": true, "models": []}', encoding='utf-8')
    real = tmp_path / 'update_queue.json'
    real.write_text('{"stage": true, "models": []}', encoding='utf-8')
    monkeypatch.setattr(fetch_emsd, 'QUEUE_PATH', str(bad))
    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(bad))
    monkeypatch.setattr(rob, 'BASE', str(tmp_path))
    with pytest.raises(queue_utils.QueueError):
        fetch_emsd.load_queue()
    with pytest.raises(queue_utils.QueueError):
        advance_queue.load()
    with pytest.raises(queue_utils.QueueError):
        rob.queue_stage()


# ---------------------------------------------------------------- 2.2 advance_queue

def _queue(tmp_path, stage, models):
    p = tmp_path / 'update_queue.json'
    p.write_text(json.dumps({'stage': stage, 'models': models}), encoding='utf-8')
    return p


def test_advance_stage1_to_2(tmp_path, monkeypatch):
    p = _queue(tmp_path, 1, ['A'])
    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(p))
    assert advance_queue.main() == 0
    assert json.loads(p.read_text(encoding='utf-8'))['stage'] == 2


def test_stage2_price_start_failure_keeps_queue(tmp_path, monkeypatch):
    p = _queue(tmp_path, 2, ['A', 'B'])
    before = p.read_bytes()
    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(p))

    def boom():
        raise batch_utils.MetaError('corrupt meta')

    monkeypatch.setattr(advance_queue, '_start_price_batch_idempotent', boom)
    assert advance_queue.main() == 1
    assert p.read_bytes() == before, 'price 啟動失敗唔可以清 queue'


def test_stage2_queue_save_failure_then_retry_completes(tmp_path, monkeypatch):
    p = _queue(tmp_path, 2, ['A'])
    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(p))
    state = {'started': False}

    def start():
        first = not state['started']
        state['started'] = True
        return first

    monkeypatch.setattr(advance_queue, '_start_price_batch_idempotent', start)
    real_save = advance_queue.save

    def failing_save(q):
        raise queue_utils.QueueError('disk full')

    monkeypatch.setattr(advance_queue, 'save', failing_save)
    assert advance_queue.main() == 1
    assert json.loads(p.read_text(encoding='utf-8'))['stage'] == 2, 'queue 保持 stage 2'
    # 重跑：start 已啟動回 False（唔係錯誤），save 恢復正常 → 完成
    monkeypatch.setattr(advance_queue, 'save', real_save)
    assert advance_queue.main() == 0
    assert json.loads(p.read_text(encoding='utf-8')) == {'stage': 0, 'models': []}


def test_advance_broken_queue_does_not_reset(tmp_path, monkeypatch):
    p = tmp_path / 'update_queue.json'
    p.write_text('{broken', encoding='utf-8')
    before = p.read_bytes()
    monkeypatch.setattr(advance_queue, 'QUEUE_PATH', str(p))
    assert advance_queue.main() == 1
    assert p.read_bytes() == before


# ---------------------------------------------------------------- 2.3 prices_meta

def test_meta_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_utils, 'META_PATH', str(tmp_path / 'missing.json'))
    assert batch_utils.load_meta() == {}


@pytest.mark.parametrize('body', [
    '{broken', 'null', '[]', '{"blocked_until": true}',
    '{"price_batch_idx": -1}', '{"price_batch_idx": 8}',
    '{"price_batch_start": "2026/09/01"}', '{"last_price_month": "2026-9"}',
    '{"last_deploy": 123}', '{"blocked_until": NaN}',
])
def test_meta_corrupt_fail_closed(tmp_path, monkeypatch, body):
    p = tmp_path / 'prices_meta.json'
    p.write_text(body, encoding='utf-8')
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    with pytest.raises(batch_utils.MetaError):
        batch_utils.load_meta()
    with pytest.raises(batch_utils.MetaError):
        batch_utils.price_batch_active()


def test_meta_save_failure_preserves_old_bytes(tmp_path, monkeypatch):
    p = tmp_path / 'prices_meta.json'
    p.write_text('{"last_run": "2026-09-01"}', encoding='utf-8')
    before = p.read_bytes()
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    monkeypatch.setattr(batch_utils.os, 'replace',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    with pytest.raises(batch_utils.MetaError):
        batch_utils.save_meta({'last_run': '2026-09-02'})
    assert p.read_bytes() == before
    assert not (tmp_path / 'prices_meta.json.tmp').exists()


def test_start_price_batch_does_not_swallow_bad_meta(tmp_path, monkeypatch):
    p = tmp_path / 'prices_meta.json'
    p.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    with pytest.raises(batch_utils.MetaError):
        batch_utils.start_price_batch()
    assert p.read_bytes() == b'{broken'


def test_price_batch_state_exit_codes(tmp_path, monkeypatch):
    p = tmp_path / 'prices_meta.json'
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    p.write_text('{"price_batch_start": "2026-09-01", "price_batch_idx": 0}', encoding='utf-8')
    assert price_state.main() == 0
    p.write_text('{}', encoding='utf-8')
    assert price_state.main() == 1
    p.write_text('{broken', encoding='utf-8')
    assert price_state.main() == 2


# ---------------------------------------------------------------- R5：真實日曆日期

@pytest.mark.parametrize('body', [
    '{"last_full": "2026-02-30"}',
    '{"last_full": "2026-13-01"}',
    '{"last_full": "2026-9-1"}',
    '{"last_price_month": "2026-13"}',
    '{"last_price_month": "2026-9"}',
    '{"last_deploy": "not-a-time"}',
    '{"last_deploy": "2026-02-30 25:00:00"}',
    '{"last_force_batch": "2026-99-99 00:00:00"}',
    '{"last_check": "2026-00-01"}',
])
def test_meta_rejects_fake_calendar_values(tmp_path, monkeypatch, body):
    p = tmp_path / 'prices_meta.json'
    p.write_text(body, encoding='utf-8')
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    with pytest.raises(batch_utils.MetaError):
        batch_utils.load_meta()


def test_detect_mode_corrupt_meta_raises_not_full(tmp_path, monkeypatch):
    p = tmp_path / 'prices_meta.json'
    p.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(batch_utils, 'META_PATH', str(p))
    with pytest.raises(batch_utils.MetaError):
        batch_utils.detect_mode()
