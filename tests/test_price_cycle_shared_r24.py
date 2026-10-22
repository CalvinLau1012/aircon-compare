# -*- coding: utf-8 -*-
"""r24 F01 v4：每日交易鏈（真 entry＋真 runner 七批；late catch-up；故障注入）。"""
import datetime
import hashlib
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'tests'))
sys.path.insert(0, str(BASE / 'scripts'))

import batch_utils  # noqa: E402
import biggo_apply  # noqa: E402
import biggo_canonical  # noqa: E402
import biggo_coordinator as coord  # noqa: E402
import price_cycle as pc  # noqa: E402
import test_biggo_stage_runner as T  # noqa: E402
runner = T.runner

SOURCE = 'c' * 40
ENV_EXTRA = {'AIRCON_PRICE_SOURCE_REVISION': SOURCE}
FILES = list(pc.REPO_FILES)


def _now(day, hour=2):
    return datetime.datetime(2026, 10, day, hour, 0, tzinfo=datetime.timezone.utc)


def _seed(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'biggo_prices.json').write_text(json.dumps({'OLD-1': T._entry('$1,000起')}))
    (root / 'model_status.json').write_text('{}')
    (root / 'model_blacklist.json').write_text(json.dumps({'models': {}}))
    (root / 'prices_meta.json').write_text(json.dumps(
        {'price_batch_idx': 7, 'last_price_month': '2026-09',
         'last_run': '2026-09-15', 'last_full': '2026-09-15'}))
    # New policy: a monthly start needs a real pending new-model demand.
    (root / 'price_demand.json').write_text(json.dumps({
        'schemaVersion': 1, 'kind': 'aircon-price-demand',
        'updatedAt': '2026-09-30T00:00:00Z',
        'demands': [{'key': 'BRAND|M1', 'brand': 'BRAND', 'model': 'M1',
                     'firstSeen': '2026-09-30', 'status': 'pending',
                     'evidence': []}]}))



def _hashes(root):
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in FILES}


def _meta(root):
    return json.loads((root / 'prices_meta.json').read_text())


def _base_hash(root):
    return biggo_canonical.sha256_json(
        json.loads((root / 'biggo_prices.json').read_text()))


def _contents(api):
    return coord.GitHubContentsClient(T.CONFIG, api=api)


def _client(api):
    return coord.CoordinatorClient(_contents(api), owner='writer')


def _store(api):
    return pc.CoordinatorAnchorStore(_contents(api))


def _seed_ack(api, root):
    ack = pc.transition_ack_bytes(SOURCE, SOURCE, _base_hash(root), _base_hash(root),
                                  at='2026-10-04')
    api.files[pc.TRANSITION_PATH] = (ack, 'ack-sha')


def _patch(monkeypatch, api):
    real = coord.GitHubContentsClient
    client = real(T.CONFIG, api=api)

    def factory(config, api=None):
        return real(config, api=api) if api is not None else client
    monkeypatch.setattr(coord, 'GitHubContentsClient', factory)


class RepoBatch:
    MetaError = batch_utils.MetaError

    def __init__(self, root):
        self.root = root

    def load_meta(self):
        return batch_utils.load_meta(str(self.root / 'prices_meta.json'))

    def get_batch_todo(self, models, meta):
        return (['M1', 'M2'], 0, 2) if meta.get('price_batch_start') else None


class StageFetch(T.FakeFetch):
    def __init__(self, tmp, root):
        super().__init__(tmp)
        self.root = root

    def stage_workload(self, meta=None):
        idx = int((meta or {}).get('price_batch_idx', 0) or 0)
        return {'batch': ['M1', 'M2'], 'review': [], 'idx': idx, 'total': 7,
                'queuedModels': 2}

    def run_price_batch(self, should_abort=None):
        self.batch_calls += 1
        self.REQUEST_STATS['search'] += 2
        base = json.loads((self.root / 'biggo_prices.json').read_text())
        new = dict(base)
        idx = int(batch_utils.load_meta(str(self.root / 'prices_meta.json'))
                  .get('price_batch_idx', 0))
        new[f'M{idx + 1}'] = T._entry()
        tracking = json.loads((self.root / 'model_status.json').read_text())
        blacklist = json.loads((self.root / 'model_blacklist.json').read_text())
        pre = {'trackingHash': T._h(tracking),
               'blacklistHash': T._h(blacklist.get('models') or {})}
        return {
            'status': 'completed', 'baseSnapshot': base, 'snapshot': new,
            'outcomes': [{'model': f'M{idx + 1}', 'canonicalKey': f'BRAND|M{idx + 1}',
                          'outcome': 'priced', 'price': new[f'M{idx + 1}']}],
            'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
            'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
            'preState': pre, 'postState': pre,
            'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                        'blacklistUpserts': {}, 'blacklistRemovals': []},
        }


def _entry(root, api, when):
    return pc.ensure_monthly_cycle(str(root / 'prices_meta.json'), when, allow_write=True,
                                   store=_store(api), client=_client(api),
                                   repo_root=str(root), apply_fn=biggo_apply.apply_bundle,
                                   actual_source=SOURCE, initial_base=_base_hash(root))


def _stage(root, api, when):
    contents = _contents(api)

    def factory(config, owner, now_fn):
        return coord.CoordinatorClient(contents, owner=owner, now=now_fn)
    fetch = StageFetch(root, root)
    env = dict(T.CONFIGURED_ENV, AIRCON_PRICE_CYCLE_ENABLED='1',
               AIRCON_PRICE_SHARED_CYCLE='1', **ENV_EXTRA,
               AIRCON_PRICE_INITIAL_BASE_HASH=_base_hash(root))
    out = []
    code = runner.run_stage(env=env, batch_mod=RepoBatch(root), fetch_mod=fetch,
                            client_factory=factory, now=lambda: when.timestamp(),
                            print_fn=out.append, apply_fn=biggo_apply.apply_bundle,
                            repo_root=str(root))
    return {'code': code, 'status': T._status(out), 'smoke': fetch.smoke_calls,
            'batch': fetch.batch_calls, 'out': out}


def _anchor(api):
    return json.loads(api.files[pc.ANCHOR_PATH][0].decode('utf-8'))


def test_daily_seven_days_entry_then_runner(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    root = tmp_path / 'winner'
    _seed(root)
    _seed_ack(api, root)
    _patch(monkeypatch, api)
    first = _entry(root, api, _now(4))
    assert first['reason'] == 'started' and first['cycle'] == '2026-10-04:1/7'
    for day in range(4, 11):
        got = _stage(root, api, _now(day))
        assert got['code'] == 0 and got['status'] == 'completed', (day, got['out'])
        assert _anchor(api)['stage'] == day - 3, _anchor(api)
        if day > 4:
            entry = _entry(root, api, _now(day))
            assert entry['reason'] in ('noop', 'adopted'), (day, entry)
    assert _anchor(api)['status'] == 'completed'
    meta = _meta(root)
    assert meta['price_batch_idx'] == 7 and meta['last_price_month'] == '2026-10'
    assert meta['last_run'] == '2026-10-10' and meta['last_full'] == '2026-10-10'
    assert meta.get('appliedBundleHash') and meta.get('appliedSnapshotHash')
    assert 'price_batch_start' not in meta
    assert api.files[pc.HISTORY_PATH.format(month='2026-10')][0]


def test_late_writer_catchup_and_reentry(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    winner = tmp_path / 'winner'
    _seed(winner)
    _seed_ack(api, winner)
    _patch(monkeypatch, api)
    _entry(winner, api, _now(4))
    for day in range(4, 11):
        assert _stage(winner, api, _now(day))['status'] == 'completed'
    late = tmp_path / 'late'
    _seed(late)
    before = _hashes(late)
    got = _entry(late, api, _now(20))
    assert got['reason'] == 'adopted' and got['stage'] == 7, got
    wh, lh = _hashes(winner), _hashes(late)
    assert lh['biggo_prices.json'] == wh['biggo_prices.json']
    assert lh['model_status.json'] == wh['model_status.json']
    assert lh['model_blacklist.json'] == wh['model_blacklist.json']
    wm, lm = _meta(winner), _meta(late)
    for key in ('price_batch_idx', 'last_run', 'last_full', 'appliedBundleHash',
                'appliedSnapshotHash', 'last_price_month'):
        assert lm.get(key) == wm.get(key), (key, lm.get(key), wm.get(key))
    again = _entry(late, api, _now(21))
    assert again['written'] is False and again['reason'] in ('noop', 'month-completed')
    assert _hashes(late) == lh


def test_missing_bundle_keeps_all_four_bytes(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    winner = tmp_path / 'winner'
    _seed(winner)
    _seed_ack(api, winner)
    _patch(monkeypatch, api)
    _entry(winner, api, _now(4))
    for day in range(4, 11):
        assert _stage(winner, api, _now(day))['status'] == 'completed'
    start = _anchor(api)['startedAt']
    del api.files[_client(api)._bundle_paths(f'{start}:4/7')['manifest']]
    late = tmp_path / 'late'
    _seed(late)
    before = _hashes(late)
    got = _entry(late, api, _now(20))
    assert got['written'] is False and got['reason'] == 'reconciliation-blocked', got
    assert _hashes(late) == before, (before, _hashes(late))


def test_wrong_source_ack_blocks_provider(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    root = tmp_path / 'repo'
    _seed(root)
    _seed_ack(api, root)
    _patch(monkeypatch, api)
    _entry(root, api, _now(4))
    assert _stage(root, api, _now(4))['status'] == 'completed'
    bad = pc.transition_ack_bytes('a' * 40, 'b' * 40, _base_hash(root), _base_hash(root))
    api.files[pc.TRANSITION_PATH] = (bad, 'bad-ack')
    got = _stage(root, api, _now(5))
    assert got['status'] == 'needs-review' and got['smoke'] == 0 and got['batch'] == 0


def test_anchor_crash_sync_without_reapply(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    root = tmp_path / 'repo'
    _seed(root)
    _seed_ack(api, root)
    _patch(monkeypatch, api)
    _entry(root, api, _now(4))
    for day in range(4, 7):
        assert _stage(root, api, _now(day))['status'] == 'completed'
    # 模擬 remote 已 commit stage3、anchor CAS 前 crash：anchor 退回 stage2
    anchor = _anchor(api)
    anchor.update({'stage': 2, 'cycle': f"{anchor['startedAt']}:2/7",
                   'status': 'active', 'lastBundleHash': None})
    anchor.pop('lastBundleHash', None)
    api.files[pc.ANCHOR_PATH] = (pc._canonical_bytes(anchor), 'anchor-old')
    before = _hashes(root)
    got = _entry(root, api, _now(7))
    assert got['written'] is False and got['reason'] in ('noop', 'adopted', 'anchor-synced'), got
    assert _anchor(api)['stage'] == 3
    assert _hashes(root) == before, 'crash sync 唔應該改本地已套用資料'


def test_io_failure_commit_is_recoverable(monkeypatch, tmp_path):
    api = T.FakeGitHubApi()
    winner = tmp_path / 'winner'
    _seed(winner)
    _seed_ack(api, winner)
    _patch(monkeypatch, api)
    _entry(winner, api, _now(4))
    for day in range(4, 7):
        assert _stage(winner, api, _now(day))['status'] == 'completed'
    late = tmp_path / 'late'
    _seed(late)
    real_replace = pc.os.replace
    calls = {'n': 0}

    def flaky(src, dst):
        if str(dst).endswith('/late/model_status.json'):
            raise OSError('disk-full-simulated')
        return real_replace(src, dst)
    monkeypatch.setattr(pc.os, 'replace', flaky)
    blocked = _entry(late, api, _now(20))
    assert blocked['reason'] == 'reconciliation-blocked'
    assert pc._read_journal(str(late)), 'journal 應該保留'
    monkeypatch.setattr(pc.os, 'replace', real_replace)
    recovered = _entry(late, api, _now(21))
    assert recovered['reason'] in ('adopted', 'noop'), recovered
    assert _hashes(late)['biggo_prices.json'] == _hashes(winner)['biggo_prices.json']
