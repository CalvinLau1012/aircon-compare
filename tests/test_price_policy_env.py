"""Versioned non-secret policy must preserve shared/source guards."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import price_policy_env

def doc():
    return {'schemaVersion': 1, 'queryMode': 'normal', 'cycleEnabled': True,
            'sharedCycle': True, 'sourceRevision': 'a' * 40, 'writer': 'github'}

def test_policy_shared_source_and_injection_rejection():
    x = price_policy_env.policy_environment(doc())
    assert x['AIRCON_PRICE_QUERY_MODE'] == 'normal'
    assert x['AIRCON_PRICE_CYCLE_ENABLED'] == x['AIRCON_PRICE_SHARED_CYCLE'] == '1'
    for field, value in [('sharedCycle', False), ('sourceRevision', ''),
                         ('sourceRevision', 'a'*40+'\nUNSAFE=1'),
                         ('writer', 'private'), ('cycleEnabled', 1)]:
        bad=doc();bad[field]=value
        with pytest.raises(ValueError): price_policy_env.policy_environment(bad)

def test_missing_policy_is_receive_only(tmp_path, capsys):
    assert price_policy_env.main(['--policy', str(tmp_path/'missing')]) == 0
    out=capsys.readouterr().out
    assert 'AIRCON_PRICE_QUERY_MODE=receive-only' in out
    assert 'AIRCON_PRICE_CYCLE_ENABLED=0' in out

def test_workflow_entry_and_provider_share_policy():
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/daily-update.yml').read_text()
    assert 'scripts/price_policy_env.py' in text
    for name in ['AIRCON_PRICE_QUERY_MODE','AIRCON_PRICE_CYCLE_ENABLED',
                 'AIRCON_PRICE_SHARED_CYCLE','AIRCON_PRICE_SOURCE_REVISION']:
        assert ('secrets.'+name+' || env.'+name) in text
