# -*- coding: utf-8 -*-
"""Canonical revival and safe review-evidence retention regressions."""
import copy
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]
import fetch_biggo as fetch
import biggo_stage_runner as runner


def _price(model='CHK18FBVX'):
    return {'price': '$4,499起', 'merchants': 1,
            'url': f'https://biggo.hk/s/?q={model}', 'updated': '2026-10-20'}


def test_canonical_revival_is_priced_with_exact_raw_mapping():
    p = _price()
    staged = fetch._completed_payload(
        idx=4, base={}, results={'CHK18FBVX': p}, got=[], clean_miss=[], net_err=[],
        revived=['CARRIER|CHK18FBVX'],
        review_evidence=[{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
                          'result': 'revived', 'price': p}],
        pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
        brand_lookup={'CHK18FBVX': 'CARRIER'})
    assert staged['outcomes'] == [{
        'model': 'CHK18FBVX', 'canonicalKey': 'CARRIER|CHK18FBVX',
        'outcome': 'priced', 'price': p}]
    assert staged['counters'] == {'got': 1, 'cleanMiss': 0, 'netErrors': 0}


@pytest.mark.parametrize('mutator', [
    lambda ev: [],
    lambda ev: [dict(ev[0], price={'price': '$1起', 'merchants': 1,
                                    'url': 'https://biggo.hk/s/?q=X', 'updated': '2026-10-20'})],
    lambda ev: [dict(ev[0], model='OTHER')],
])
def test_revival_missing_or_conflicting_evidence_is_rejected(mutator):
    p = _price()
    ev = [{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
           'result': 'revived', 'price': p}]
    with pytest.raises(ValueError):
        fetch._completed_payload(
            idx=4, base={}, results={'CHK18FBVX': p}, got=[], clean_miss=[], net_err=[],
            revived=['CARRIER|CHK18FBVX'], review_evidence=mutator(ev),
            pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
            brand_lookup={'CHK18FBVX': 'CARRIER'})


def test_revived_cannot_collide_with_a_priced_model():
    p = _price()
    with pytest.raises(ValueError):
        fetch._completed_payload(
            idx=4, base={}, results={'CHK18FBVX': p}, got=['CHK18FBVX'],
            clean_miss=[], net_err=[], revived=['CARRIER|CHK18FBVX'],
            review_evidence=[{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
                              'result': 'revived', 'price': p}],
            pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
            brand_lookup={'CHK18FBVX': 'CARRIER'})


def test_review_evidence_retains_full_safe_fields(tmp_path):
    staged = {'status': 'completed', 'idx': 4, 'baseSnapshot': {},
              'snapshot': {'M': {'price': '$100起'}}, 'outcomes': [],
              'counters': {'got': 0, 'cleanMiss': 0, 'netErrors': 0},
              'blacklistReview': {'quotaIndex': 4, 'reviewed': []},
              'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                          'blacklistUpserts': {}, 'blacklistRemovals': []},
              'preState': {'trackingHash': 'sha256:' + 'a' * 64,
                           'blacklistHash': 'sha256:' + 'b' * 64},
              'postState': {'trackingHash': 'sha256:' + 'c' * 64,
                            'blacklistHash': 'sha256:' + 'd' * 64},
              'metaFields': {'price_batch_idx': 5},
              'BIGGO_CLIENT_SECRET': 'MUST-NOT-BE-RETAINED'}
    before = copy.deepcopy(staged)
    runner._write_review_evidence(str(tmp_path / 'status.json'), staged,
                                  stage_result={'safe': True})
    got = json.loads((tmp_path / 'biggo-review/stage-snapshot.json').read_text())
    for key in ('idx', 'baseSnapshot', 'blacklistReview', 'effects',
                'preState', 'postState', 'metaFields', 'stageResult'):
        assert got.get(key) == staged.get(key, {'safe': True})
    assert 'BIGGO_CLIENT_SECRET' not in json.dumps(got)
    assert staged == before


def test_weaker_failure_callback_preserves_exact_stage_result_and_manifest_fields(tmp_path):
    staged = {'status': 'completed', 'idx': 4, 'baseSnapshot': {},
              'snapshot': {}, 'outcomes': [],
              'counters': {'got': 0, 'cleanMiss': 0, 'netErrors': 0},
              'blacklistReview': {'quotaIndex': 4, 'reviewed': []},
              'effects': {}, 'preState': {}, 'postState': {}, 'metaFields': {}}
    sr = {'cycleId': '2026-10-10:5/7', 'stage': 5, 'generatedAt': '2026-10-20T07:19:53Z'}
    mf = {'cycleId': '2026-10-10:5/7', 'stage': 5, 'mode': 'price-batch',
          'commit': 'a' * 40, 'publishedAt': '2026-10-20T07:19:53Z'}
    runner._write_review_evidence(str(tmp_path / 'status.json'), staged,
                                  stage_result=sr, manifest_fields=mf)
    runner._write_review_evidence(str(tmp_path / 'status.json'), staged)
    saved = json.loads((tmp_path / 'biggo-review/stage-snapshot.json').read_text())
    assert saved['stageResult'] == sr
    assert saved['manifestFields'] == mf


@pytest.mark.parametrize('failure_phase', ['publish', 'commit'])
def test_publish_or_commit_failure_retains_exact_publish_context(tmp_path, failure_phase):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        'repo_runner_fixtures', Path(runner.__file__).resolve().parent.parent
        / 'tests/test_biggo_stage_runner.py')
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    captured = {}

    def hook(client):
        real_publish = client.publish_bundle
        real_commit = client.commit

        def publish(base, new, stage_result, manifest_fields):
            captured['stageResult'] = copy.deepcopy(stage_result)
            captured['manifestFields'] = copy.deepcopy(manifest_fields)
            if failure_phase == 'publish':
                raise runner.coord_mod.CoordinatorError('simulated publish failure')
            return real_publish(base, new, stage_result, manifest_fields)

        def commit(**kwargs):
            if failure_phase == 'commit' and kwargs.get('status') == 'completed':
                raise runner.coord_mod.CoordinatorError('simulated commit failure')
            return real_commit(**kwargs)

        client.publish_bundle = publish
        client.commit = commit

    fixtures._run(tmp_path, client_hook=hook, status_out=str(tmp_path / 'status.json'),
                  env={**fixtures.CONFIGURED_ENV, 'GITHUB_ACTIONS': 'true',
                       'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '1234'})
    saved = json.loads((tmp_path / 'biggo-review/stage-snapshot.json').read_text())
    assert saved['stageResult'] == captured['stageResult']
    assert saved['manifestFields'] == captured['manifestFields']


def test_publication_finalize_changes_only_explicit_publication_identity():
    import biggo_recovery as recovery
    packet = {
        'manifest': {'cycleId': '2026-10-10:5/7', 'stage': 5,
                     'publishedAt': None, 'publicationStatus': 'pending'},
        'newSnapshot': {'A': {'price': '$1起'}},
        'pricesMetaTarget': {'_recovery': {}},
        'publication': {'status': 'pending', 'publishedAt': None,
                        'sourceCompletionBound': '2026-10-20T07:19:53Z'},
    }
    with_bad = copy.deepcopy(packet)
    with pytest.raises(ValueError):
        recovery._finalize_publication(with_bad, '2026-10-20T07:19:53Z')
    first = recovery._finalize_publication(packet, '2026-10-20T08:00:00Z')
    second = recovery._finalize_publication(packet, '2026-10-20T08:00:00Z')
    assert first == second
    assert first['manifest']['publishedAt'] == '2026-10-20T08:00:00Z'
    assert first['publication']['status'] == 'finalized'
    assert packet['manifest']['publishedAt'] is None
