import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts import verify_snapshot_rebuild as guard


def witness(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    report = dict(schemaVersion=2, mode='sync-completed', ok=True,
                  postStateVerified=True, reason='applied', cycleId='2026-10-10:4/7',
                  stage=4, bundleHash='sha256:'+'a'*64, newSnapshotHash='sha256:'+'b'*64)
    meta = dict(appliedBundleHash=report['bundleHash'],last_applied_cycle=report['cycleId'],
                price_batch_idx=4, appliedSnapshotHash=report['newSnapshotHash'])
    hashes = {}
    for name in guard.SYNC_FILES:
        raw=json.dumps(meta if name=='prices_meta.json' else {'value':1}).encode()
        (tmp_path/name).write_bytes(raw)
        hashes[name]='sha256:'+hashlib.sha256(raw).hexdigest()
    report['postFileHashes']=hashes
    report['receiptId']='sha256:'+hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    report.update(sourceRunId=123,sourceEvent='workflow_dispatch',headSha='c'*40)
    path=tmp_path/'report.json';path.write_text(json.dumps(report))
    return SimpleNamespace(completed_sync_report=str(path),files_root=str(tmp_path)), {'head':'c'*40}, report


def test_exact_same_run_four_file_witness(tmp_path, monkeypatch):
    args,base,_=witness(tmp_path,monkeypatch)
    assert set(guard._verified_sync_files(args,base))==guard.SYNC_FILES


@pytest.mark.parametrize('mutation',['run','head','receipt','metadata','file','symlink'])
def test_reject_stale_or_modified_receiver_witness(tmp_path,monkeypatch,mutation):
    args,base,r=witness(tmp_path,monkeypatch)
    if mutation=='run':r['sourceRunId']=124
    elif mutation=='head':r['headSha']='d'*40
    elif mutation=='receipt':r['receiptId']='sha256:'+'0'*64
    elif mutation=='metadata':(tmp_path/'prices_meta.json').write_text('{}')
    elif mutation=='file':(tmp_path/'model_status.json').write_text('{}')
    elif mutation=='symlink':
        f=tmp_path/'biggo_prices.json';data=f.read_bytes();f.unlink();(tmp_path/'target').write_bytes(data);f.symlink_to(tmp_path/'target')
    (tmp_path/'report.json').write_text(json.dumps(r))
    with pytest.raises(guard.CheckError):guard._verified_sync_files(args,base)


def test_unverified_report_does_not_allow_source_changes(tmp_path,monkeypatch):
    args,base,r=witness(tmp_path,monkeypatch);r['postStateVerified']=False
    (tmp_path/'report.json').write_text(json.dumps(r))
    assert guard._verified_sync_files(args,base)=={}
