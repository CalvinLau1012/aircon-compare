"""Clock-boundary regressions with no provider or filesystem writes."""
from datetime import datetime, timezone
import time

import pytest

import biggo_acquisition as acquisition
import fetch_biggo
from scripts import biggo_stage_runner as runner


@pytest.mark.parametrize('start', [
    '2026-10-20T00:00:00.999000+00:00',
    '2026-10-20T23:59:59.999000+00:00',
    '2026-10-31T23:59:59.999000+00:00',
    '2026-12-31T23:59:59.999000+00:00',
])
def test_response_window_uses_explicit_clock_across_boundaries(monkeypatch, start):
    real_gmtime = time.gmtime
    clock = [datetime.fromisoformat(start).timestamp()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])

    def lagging_implicit_clock(sample=None):
        # Deterministically model the observed lag of gmtime() around a tick.
        return real_gmtime(clock[0] - 1 if sample is None else sample)

    monkeypatch.setattr(time, 'gmtime', lagging_implicit_clock)
    fetch_biggo.reset_acquisitions()
    started = acquisition.utc_stamp()
    clock[0] += .002
    price = {'price': '$2,500起', 'merchants': 1,
             'url': 'https://example.invalid/fixture', 'updated': 'fixture'}
    fetch_biggo._record_acquisition('FIXTURE', price)
    recorded = fetch_biggo._acquisition_of('FIXTURE', price)
    outcome = {'model': 'FIXTURE', 'outcome': 'priced', 'price': price, **recorded}
    finished = acquisition.utc_stamp()
    staged = {
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'outcomes': [outcome], 'effects': {}, 'preState': {}, 'postState': {},
        'baseSnapshot': {}, 'snapshot': {'FIXTURE': price},
        'acquisitionWindow': {'startedAt': started, 'finishedAt': finished},
    }
    clock[0] += .002

    class Client:
        state = {'requestAttempts': {'token': 0, 'search': 0},
                 'responseStatusCounts': {}}

    result = runner._build_stage_result(
        cycle_id='fixture:1/7', stage_no=1, mode='price-batch',
        client=Client(), now_fn=lambda: clock[0], staged=staged)
    expected = datetime.fromtimestamp(clock[0], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert result['acquisition']['batchStartedAt'] == started
    assert result['acquisition']['lastValidResultAt'] == expected
    assert started < recorded['acquiredAt'] == finished == result['generatedAt']
    assert recorded['resultHash'] == acquisition.result_hash(price)
    fetch_biggo.reset_acquisitions()


@pytest.mark.parametrize('offset', [-1, 301])
def test_actual_out_of_window_result_is_still_rejected(offset):
    base_time = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    outcome = {'model': 'FIXTURE', 'outcome': 'clean_miss',
               'acquiredAt': acquisition.utc_stamp(base_time + offset),
               'resultHash': acquisition.result_hash(None)}
    with pytest.raises(acquisition.AcquisitionError, match='outside batch window'):
        acquisition.build_block(
            cycle_id='fixture:1/7', stage=1, base_snapshot={}, new_snapshot={},
            outcomes=[outcome], batch_started_at=acquisition.utc_stamp(base_time),
            generated_at=acquisition.utc_stamp(base_time), require_complete=True)
