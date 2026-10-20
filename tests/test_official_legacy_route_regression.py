"""Known official models must stay reachable through the daily adapter plan."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_official_batch import STAGES, target_scoped_plan

@pytest.mark.parametrize(('model','script'), [
 ('RC-HU70A','fetch_shew.py'), ('RC-H7HR','fetch_shew.py'),
 ('RC-S7HR','fetch_shew.py'), ('RC-V7AC','fetch_shew.py'),
 ('CF-09VAGF-H','fetch_official.py'),
])
def test_known_official_models_remain_reachable(model,script):
 assert script in target_scoped_plan(STAGES[1],[model])['scripts']
