# -*- coding: utf-8 -*-
"""stage_run_evidence 回歸（2026-09-29 第二次返修：baseline provenance）

- timestamp 單獨唔可以證明 provenance：pre-step baseline hash／absence 必須一齊用；
- run start 之前嘅 timestamp 一律排除（冇 skew 容忍，即使只差 1 秒）；
- baseline 話 present 而 hash 冇變（即使 timestamp 人為移入 run window）→ 排除；
- 內容真係變咗＋timestamp 喺 run window → stage；
- 有 run-specific id 而唔 match → 排除；
- baseline 缺失／損毀 → fail closed（rc 2），唔會亂 stage；
- 只讀來源，status 唔含絕對路徑，baseline 檔唔會入 staging dir。
"""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'stage_run_evidence_mod', os.path.join(BASE, 'scripts', 'stage_run_evidence.py'))
sre = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sre)


def _utc(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return path


def _baseline(tmp_path, files, name='baseline.json'):
    path = tmp_path / name
    assert sre.main(['--write-baseline', str(path), *[str(f) for f in files]]) == 0
    return path


def _run(out_dir, files, baseline, rc=0, started_at=None, run_id='123', phase='emsd'):
    args = ['--out-dir', str(out_dir), '--phase', phase, '--run-id', run_id,
            '--commit', 'a' * 40, '--source-rc', str(rc),
            '--run-started-at', started_at or _utc(minutes=-1)]
    if baseline is not None:
        args += ['--baseline', str(baseline)]
    args += [str(f) for f in files]
    return sre.main(args)


def _status(out_dir):
    return json.loads((out_dir / 'run-status.json').read_text(encoding='utf-8'))


def test_write_baseline_records_presence_and_sha256(tmp_path):
    present = _write(tmp_path / 'emsd_receipt.json',
                     {'success': True, 'retrievedAt': '2026-09-27T19:53:03Z'})
    baseline_path = _baseline(tmp_path, [present, tmp_path / 'emsd_raw_receipt.json'])
    payload = json.loads(baseline_path.read_text(encoding='utf-8'))
    assert payload['schemaVersion'] == 1
    assert payload['files']['emsd_receipt.json']['present'] is True
    assert len(payload['files']['emsd_receipt.json']['sha256']) == 64
    assert payload['files']['emsd_raw_receipt.json'] == {'present': False, 'sha256': None}
    assert str(tmp_path) not in baseline_path.read_text(encoding='utf-8')


def test_timestamp_one_second_before_run_start_is_excluded(tmp_path):
    """前一次 run 嘅 timestamp 只差 1 秒，都唔可以入 artifact。"""
    old = _write(tmp_path / 'emsd_receipt.json',
                 {'success': True, 'retrievedAt': '2026-09-27T19:53:03Z'})
    baseline = _baseline(tmp_path, [old])
    # 換成新 content 但 timestamp 只差 run start 1 秒（仍然 pre-run）
    _write(old, {'success': True, 'retrievedAt': _utc(seconds=-61)})
    started = _utc(seconds=-60)
    out = tmp_path / 'out'
    assert _run(out, [old], baseline, rc=1, started_at=started) == 0
    assert not (out / 'emsd_receipt.json').exists()
    status = _status(out)
    rec = status['files'][0]
    assert rec['staged'] is False and rec['reason'] == 'before-run-start'
    assert status['error'] == 'source-rc-1-and-no-current-run-evidence'


def test_matching_hash_with_in_window_timestamp_is_excluded(tmp_path):
    """舊 content（hash 同 baseline 相同）即使 timestamp 改到 run window 都要排除。"""
    old = _write(tmp_path / 'official_batch_status.json',
                 {'schemaVersion': 1, 'generatedAt': _utc(seconds=-1),
                  'pendingCoverage': False})
    # baseline 喺 source step 之前捕捉；candidate 內容完全一樣但 timestamp 看似在 window
    baseline = _baseline(tmp_path, [old])
    out = tmp_path / 'out'
    assert _run(out, [old], baseline, rc=0, phase='official') == 0
    assert not (out / 'official_batch_status.json').exists(), \
        'hash 同 baseline 相同＝舊 content，唔可以因為 timestamp 在 window 就 stage'
    rec = _status(out)['files'][0]
    assert rec['staged'] is False and rec['reason'] == 'baseline-unchanged'
    assert rec['baselineSha256'] == rec['sha256']


def test_fresh_changed_content_is_staged(tmp_path):
    old = _write(tmp_path / 'emsd_receipt.json',
                 {'success': True, 'retrievedAt': '2026-09-27T19:53:03Z'})
    baseline = _baseline(tmp_path, [old])
    fresh = _write(old, {'success': False, 'retrievedAt': _utc(seconds=-5),
                         'error': 'network'})
    out = tmp_path / 'out'
    assert _run(out, [fresh], baseline, rc=1) == 0
    staged = json.loads((out / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert staged['success'] is False and staged['error'] == 'network'
    rec = _status(out)['files'][0]
    assert rec['staged'] is True and rec['reason'] == 'current-run'
    assert rec['sha256'] != rec['baselineSha256']
    assert _status(out)['error'] is None


def test_absent_before_and_fresh_file_is_staged(tmp_path):
    target = tmp_path / 'emsd_raw_receipt.json'
    baseline = _baseline(tmp_path, [target])
    fresh = _write(target, {'success': True, 'retrievedAt': _utc(seconds=-5)})
    out = tmp_path / 'out'
    assert _run(out, [fresh], baseline, rc=0) == 0
    assert (out / 'emsd_raw_receipt.json').exists()
    assert _status(out)['files'][0]['reason'] == 'current-run'


def test_missing_file_and_run_id_mismatch_are_excluded(tmp_path):
    baseline = _baseline(tmp_path, [tmp_path / 'emsd_receipt.json'])
    out = tmp_path / 'out'
    assert _run(out, [tmp_path / 'emsd_receipt.json'], baseline, rc=1) == 0
    status = _status(out)
    assert status['files'][0]['reason'] == 'missing'
    assert status['error'] == 'source-rc-1-and-no-current-run-evidence'

    target = _write(tmp_path / 'other.json',
                    {'retrievedAt': _utc(seconds=-5), 'runId': '999'})
    baseline2 = _baseline(tmp_path, [target], name='baseline2.json')
    _write(target, {'retrievedAt': _utc(seconds=-5), 'runId': '999'})  # content 已變
    out2 = tmp_path / 'out2'
    assert _run(out2, [target], baseline2, rc=0, run_id='123') == 0
    assert not (out2 / 'other.json').exists()
    assert _status(out2)['files'][0]['reason'] == 'run-id-mismatch'


def test_official_stale_status_excluded_but_fresh_receipt_staged(tmp_path):
    stale_status = _write(tmp_path / 'official_batch_status.json',
                          {'schemaVersion': 1, 'generatedAt': '2026-09-27T19:53:03Z'})
    receipt = tmp_path / 'aircon-official-receipt.json'
    baseline = _baseline(tmp_path, [receipt, stale_status], name='official-baseline.json')
    fresh_receipt = _write(receipt, {'startedAt': _utc(seconds=-5),
                                     'decision': 'ready-to-advance'})
    out = tmp_path / 'out'
    assert _run(out, [fresh_receipt, stale_status], baseline, rc=0, phase='official') == 0
    assert (out / 'aircon-official-receipt.json').exists()
    assert not (out / 'official_batch_status.json').exists()
    by_name = {r['name']: r for r in _status(out)['files']}
    assert by_name['aircon-official-receipt.json']['staged'] is True
    assert by_name['official_batch_status.json']['reason'] == 'before-run-start'


def test_missing_or_invalid_baseline_fails_closed(tmp_path):
    target = _write(tmp_path / 'emsd_receipt.json', {'retrievedAt': _utc(seconds=-5)})
    out = tmp_path / 'out'
    assert _run(out, [target], baseline=None, rc=0) == 2
    # 損毀 baseline：寫 status 記 baseline-invalid 後 rc 2
    bad = tmp_path / 'bad-baseline.json'
    bad.write_text('not-json', encoding='utf-8')
    assert _run(out, [target], bad, rc=0) == 2
    status = _status(out)
    assert status['baselineUsed'] is False
    assert status['error'] == 'baseline-invalid'
    assert status['stagedCount'] == 0
    assert not (out / 'emsd_receipt.json').exists()


def test_status_baseline_files_not_in_staging_and_no_absolute_paths(tmp_path):
    old = _write(tmp_path / 'emsd_receipt.json',
                 {'success': True, 'retrievedAt': '2026-09-27T19:53:03Z'})
    baseline = _baseline(tmp_path, [old], name='baseline-emsd.json')
    fresh = _write(old, {'success': False, 'retrievedAt': _utc(seconds=-5)})
    out = tmp_path / 'out'
    assert _run(out, [fresh], baseline, rc=0) == 0
    assert (staged_files := sorted(p.name for p in out.iterdir()))
    assert staged_files == ['emsd_receipt.json', 'run-status.json'], staged_files
    raw = (out / 'run-status.json').read_text(encoding='utf-8')
    assert str(tmp_path) not in raw, 'run-status 唔可以有本機絕對路徑'
    assert 'baseline-emsd.json' not in raw, 'baseline 檔名唔應該入 staging／status'
    assert sre.main(['--out-dir', str(out), '--baseline', str(baseline),
                     '--run-started-at', 'bad-date', str(fresh)]) == 2
