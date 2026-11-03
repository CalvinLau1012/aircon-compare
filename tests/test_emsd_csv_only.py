"""Normal CSV-only entry, preserved failures, and source/receipt binding."""
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import emsd_dual_source as dual
import fetch_emsd as fetch
from test_emsd_dual_source import _live_bytes, _live_row


def response(rows):
    return dual.RawResponse(url=dual.CSV_URL, status=200, body=_live_bytes(rows),
                            headers={}, fetchedAt='2026-01-31T18:00:00Z')


def setup(tmp_path, monkeypatch, resp):
    for key, val in {'BASE_DIR': str(tmp_path), 'QUEUE_PATH': str(tmp_path/'update_queue.json'),
                     'RECEIPT_PATH': str(tmp_path/'emsd_receipt.json'),
                     'RAW_RECEIPT_PATH': str(tmp_path/'emsd_raw_receipt.json')}.items():
        monkeypatch.setattr(fetch, key, val)
    monkeypatch.setattr(fetch, 'resolve_csv_source', lambda **kw: dual.last_known_good_resolution(reason='test', now=1769882400))
    monkeypatch.setattr(fetch, 'fetch_csv_source', lambda **kw: resp)
    monkeypatch.setattr(fetch, 'fetch_page', lambda *a: pytest.fail('normal path fetched web page'))
    monkeypatch.setattr(fetch, 'persist_raw_archive', lambda *a, **kw: {'persisted': False})
    monkeypatch.setattr(fetch.sys, 'argv', ['fetch_emsd.py'])
    return tmp_path/'emsd_空調能源標籤.csv'


def test_normal_entry_never_calls_pagination_and_metadata_keeps_acquisition(tmp_path, monkeypatch):
    out=setup(tmp_path, monkeypatch, response([_live_row('ONE'), _live_row('TWO')]))
    fetch.main()
    doc=json.loads((tmp_path/'emsd_receipt.json').read_text())
    assert doc['sourceMode']=='csv-only' and 'dualSource' not in doc
    assert doc['totalRows']==2 and doc['transportUnits']=='csv-files'
    spec=importlib.util.spec_from_file_location('csv_metadata', Path(fetch.__file__).parent/'scripts/gen-metadata.py')
    gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)
    facts=gen.receipt_facts(str(tmp_path/'emsd_receipt.json'), str(out))
    assert facts['datasetDate']=='2026-02-01' and facts['registrationCount']==2
    doc['csvValidation']['source']['fetchedAt']='2026-02-01T00:00:00Z'
    (tmp_path/'emsd_receipt.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='binding'):
        gen.receipt_facts(str(tmp_path/'emsd_receipt.json'), str(out))


@pytest.mark.parametrize('bad', ['duplicate', 'missing', 'schema', 'withdrawal', '304', 'archive'])
def test_bad_candidate_preserves_csv_queue_and_demand(tmp_path, monkeypatch, bad):
    old=response([_live_row(str(i)) for i in range(10)])
    out=setup(tmp_path, monkeypatch, old)
    fetch.main()
    protected=[out,tmp_path/'update_queue.json',tmp_path/'price_demand.json']
    before={p:p.read_bytes() for p in protected if p.exists()}
    rows=[_live_row(str(i)) for i in range(10)]
    if bad=='duplicate':rows[1]=rows[0]
    if bad=='missing':rows[1][8]=''
    if bad=='withdrawal':rows=rows[:7]
    resp=response(rows)
    if bad=='schema':resp.body=resp.body.replace(b'Reference Number',b'Unknown Reference')
    if bad=='304':resp.status=304
    monkeypatch.setattr(fetch, 'fetch_csv_source', lambda **kw: resp)
    if bad=='archive':
        def fail(*a,**kw):raise OSError('archive unavailable')
        monkeypatch.setattr(fetch, 'persist_raw_archive', fail)
    with pytest.raises(SystemExit):fetch.main()
    assert {p:p.read_bytes() for p in before}==before
    assert json.loads((tmp_path/'emsd_receipt.json').read_text())['success'] is False


def test_relative_policy_accepts_shrink_growth_and_multiple_model_registrations(tmp_path):
    out=tmp_path/'old.csv'
    baseline=dual.verify_csv_only(response([_live_row(str(i)) for i in range(10)]))
    with out.open('w',newline='',encoding='utf-8-sig') as fh:
        w=csv.writer(fh);w.writerow(dual.CANONICAL_HEADER);w.writerows(baseline['rows'])
    for count in [8,10,12]:
        assert dual.verify_csv_only(response([_live_row(str(i)) for i in range(count)]),baseline_path=out)['rowCount']==count


def test_ragged_and_malformed_csv_rejected():
    resp=response([_live_row('ONE')]);resp.body=resp.body.rstrip()+b',extra\n'
    with pytest.raises(dual.SourceSchemaError):dual.verify_csv_only(resp)


def test_raw_receipt_csv_only_rebuild_and_tamper(tmp_path, monkeypatch):
    out=setup(tmp_path, monkeypatch, response([_live_row('ONE')]))
    def persisted(records, **kw):
        return {'persisted':True,'verified':True,'durableRemote':True,'adapter':'fixture',
                'retentionDays':90,'archiveHash':fetch.archive_hash(records)}
    monkeypatch.setattr(fetch,'persist_raw_archive',persisted)
    fetch.main()
    script=Path(fetch.__file__).parent/'scripts/verify_snapshot_rebuild.py'
    spec=importlib.util.spec_from_file_location('csv_rebuild',script)
    check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
    args=SimpleNamespace(receipt=str(tmp_path/'emsd_receipt.json'),raw_receipt=str(tmp_path/'emsd_raw_receipt.json'),
                         files_root=str(tmp_path),csv_rel=out.name)
    meta={'datasetHash':'sha256:'+hashlib.sha256(out.read_bytes()).hexdigest(),'datasetSourceUrl':dual.CSV_URL}
    assert check._check_receipts(args,meta)==[]
    raw=json.loads(Path(args.raw_receipt).read_text());raw['pages'][0]['sha256']='sha256:'+'0'*64
    Path(args.raw_receipt).write_text(json.dumps(raw))
    assert check._check_receipts(args,meta)
