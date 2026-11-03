import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import receive_completed_chain as receiver


def state(root='coordinator/snapshots'):
    return {'cycleId': '2026-11-03:2/7',
            'snapshotPath': root + '/2026-11-03-2-7/biggo_prices.json',
            'stageResultPath': root + '/2026-11-03-2-7/stage-result.json'}


def test_published_root_dynamic():
    assert receiver.published_root(state()) == 'coordinator/snapshots'
    assert receiver.published_root(state('new/archive')) == 'new/archive'


@pytest.mark.parametrize('path', ['/absolute', '../escape', 'bad/../escape',
                                 'https://host/file', 'other/2026-11-03-1-7/biggo_prices.json'])
def test_rejects_unsafe_or_wrong_stage(path):
    d = state(); d['snapshotPath'] = path
    with pytest.raises(ValueError):
        receiver.published_root(d)


def test_receiver_cannot_write_coordinator():
    with pytest.raises(RuntimeError):
        receiver.ReadOnlyContents.put_file(None, 'anything', b'anything')
