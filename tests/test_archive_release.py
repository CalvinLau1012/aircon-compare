# -*- coding: utf-8 -*-
"""長期歸檔回歸（GATE-09）— 第二輪強化

覆蓋 Codex 審查重現：歸檔目錄被篡改仍 rc=0、tag 同 metadata.version 唔一致仍
rc=0；另加 zip/checksum/provenance/manifest/release 報告/私隱等 fail-closed。
"""
import hashlib
import importlib.util
import json
import os
import shutil
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'archive_mod', os.path.join(BASE, 'scripts', 'archive_release.py'))
archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(archive)

_GM_SPEC = importlib.util.spec_from_file_location(
    'gen_metadata_ar', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
gen_metadata = importlib.util.module_from_spec(_GM_SPEC)
_GM_SPEC.loader.exec_module(gen_metadata)

VERSION = '1.2.9'
PAYLOAD = ['index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv']


def make_site(tmp_path, version=VERSION, build='B20260921.TEST'):
    (tmp_path / 'index.html').write_text('<html>v1.2.9</html>', encoding='utf-8')
    (tmp_path / '空調對比報告.pdf').write_bytes(b'%PDF-1.4 fake pdf')
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('品牌,型號\n開利,CHK18\n', encoding='utf-8')
    manifest = {'files': PAYLOAD}
    (tmp_path / 'deploy_payload.json').write_text(json.dumps(manifest), encoding='utf-8')
    csv_bytes = (tmp_path / 'emsd_空調能源標籤.csv').read_bytes()
    meta = {
        'schemaVersion': '1.0.0', 'version': version, 'build': build,
        'commit': 'a' * 40, 'deployTime': '2026-09-21T03:00:00Z',
        'workflowRunId': '42', 'deploymentType': 'release',
        'releasePayloadHash': gen_metadata.hash_files(PAYLOAD, base=str(tmp_path)),
        'datasetDate': '2026-09-21', 'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-09-20T18:59:57Z',
        'datasetSourceUrl': 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php',
        'datasetSnapshotId': 'emsd-2026-09-21-abc',
        'datasetHash': 'sha256:' + hashlib.sha256(csv_bytes).hexdigest(),
        'recordCount': 1,
    }
    (tmp_path / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    reports = tmp_path / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'pytest.txt').write_text('1 passed', encoding='utf-8')
    return meta


def _valid_acceptance(reports_dir, gate_overrides=None, drop=None, duplicate=None, unknown=False):
    logs = reports_dir / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    gates = []
    overrides = gate_overrides or {}
    for gid in archive.REQUIRED_ACCEPTANCE_GATES:
        argv = archive.ACCEPTANCE_ARGV[gid]
        if gid == drop:
            continue
        log_rel = f'logs/{gid}.log'
        (reports_dir / log_rel).write_text(f'{gid} ok\n', encoding='utf-8')
        entry = {'id': gid, 'argv': argv, 'returncode': overrides.get(gid, 0),
                 'log': log_rel,
                 'logSha256': archive.sha256_file(str(reports_dir / log_rel))}
        if gid == 'PYTEST':
            junit_rel = 'logs/pytest-junit.xml'
            (reports_dir / junit_rel).write_text(JUNIT_OK, encoding='utf-8')
            entry['junit'] = junit_rel
            entry['junitSha256'] = archive.sha256_file(str(reports_dir / junit_rel))
        gates.append(entry)
        if duplicate == gid:
            gates.append(dict(entry))
    if unknown:
        gates.append({'id': 'EVIL_GATE', 'argv': ['rm', '-rf', '/'], 'returncode': 0,
                      'log': 'e.log', 'logSha256': '0' * 64})
    ok = all(g['returncode'] == 0 for g in gates) and not (drop or duplicate or unknown)
    return {'schemaVersion': 1, 'runner': 'scripts/run_acceptance.py',
            'commit': 'b' * 40,
            'startedAt': '2026-09-22T00:00:00Z', 'finishedAt': '2026-09-22T00:10:00Z',
            'ok': ok, 'gates': gates}


def _valid_feature():
    node = 'tests/test_demo.py::test_pass'
    return {'ok': True, 'collection': {'matchedNodes': [node], 'deselected': []},
            'bindings': {node: {'status': 'passed'}}, 'failures': [],
            'run': {'exitStatus': 0, 'tests': {node: {'result': 'passed'}}}}


def _valid_postdeploy():
    return {
        'ok': True, 'failures': [],
        'checks': [{'check': c, 'pass': True} for c in archive.REQUIRED_POSTDEPLOY_CHECKS],
        'browser': [{'check': c, 'pass': True} for c in archive.REQUIRED_POSTDEPLOY_BROWSER],
    }


JUNIT_OK = ('<?xml version="1.0"?><testsuites><testsuite tests="1">'
            '<testcase classname="t" name="ok"/></testsuite></testsuites>')


def make_release_reports(reports_dir, acceptance=None, postdeploy=None, feature=None):
    reports_dir.mkdir(parents=True, exist_ok=True)
    if acceptance is None:
        acceptance = _valid_acceptance(reports_dir)
    (reports_dir / 'acceptance.json').write_text(json.dumps(acceptance), encoding='utf-8')
    post = postdeploy if postdeploy is not None else _valid_postdeploy()
    (reports_dir / 'postdeploy.json').write_text(json.dumps(post), encoding='utf-8')
    feat = feature if feature is not None else _valid_feature()
    (reports_dir / 'feature-check.json').write_text(json.dumps(feat), encoding='utf-8')



def run_archive(tmp_path, tag=f'v{VERSION}', out=None, extra=None):
    return archive.main([
        '--tag', tag, '--artifacts-dir', str(tmp_path),
        '--manifest', str(tmp_path / 'deploy_payload.json'),
        '--reports-dir', str(tmp_path / 'reports'),
        '--archive-commit', 'b' * 40,
        '--deployment-commit', 'd' * 40,
        '--source-commit', 'a' * 40,
        '--run-id', '42', '--deploy-sha', 'deploy-xyz',
        '--out-dir', str(out or (tmp_path / 'out')),
    ] + (extra or []))


def first_archive(tmp_path):
    assert run_archive(tmp_path) == 0
    return tmp_path / 'out' / f'v{VERSION}', tmp_path / 'out' / f'archive-v{VERSION}.zip'


def test_archive_builds_with_checksums_provenance_and_reports(tmp_path):
    make_site(tmp_path)
    assert run_archive(tmp_path) == 0
    root = tmp_path / 'out' / f'v{VERSION}'
    assert (root / 'CHECKSUMS.sha256').is_file()
    prov = json.load(open(root / 'PROVENANCE.json', encoding='utf-8'))
    assert prov['archiveType'] == 'draft'
    assert prov['archiveCommit'] == 'b' * 40
    assert prov['sourceCommit'] == 'a' * 40
    assert prov['deploymentCommit'] == 'd' * 40
    assert prov['workflowRunId'] == '42'
    for line in (root / 'CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines():
        digest, rel = line.split('  ', 1)
        assert archive.sha256_file(str(root / rel)) == digest


def test_archive_reports_source_is_reports_dir_not_artifacts(tmp_path):
    site = tmp_path / 'site'
    site.mkdir()
    make_site(site)
    external = tmp_path / 'external-reports'
    external.mkdir()
    (external / 'report.json').write_text('{"ok":1}', encoding='utf-8')
    assert archive.main([
        '--tag', f'v{VERSION}', '--artifacts-dir', str(site),
        '--manifest', str(site / 'deploy_payload.json'),
        '--reports-dir', str(external),
        '--archive-commit', 'b' * 40, '--out-dir', str(tmp_path / 'out'),
    ]) == 0
    root = tmp_path / 'out' / f'v{VERSION}'
    assert (root / 'reports' / 'report.json').read_text(encoding='utf-8') == '{"ok":1}'
    assert not (root / 'reports' / 'pytest.txt').exists()


# ---------------------------------------------------------------- tag／version

def test_archive_tag_must_equal_metadata_version(tmp_path):
    make_site(tmp_path)
    assert run_archive(tmp_path, tag='v9.9.9') == 1, 'tag 唔等於 metadata.version 必須拒絕'
    assert run_archive(tmp_path, tag='v1.2.9-test') == 1, '1.2.9-test 唔可以當 1.2.9'
    assert run_archive(tmp_path, tag='1.2.9') == 0
    assert not (tmp_path / 'out' / 'v9.9.9').exists()


def test_archive_run_id_must_not_contradict_metadata(tmp_path):
    make_site(tmp_path)
    assert run_archive(tmp_path, extra=['--run-id', '999']) == 1
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


# ---------------------------------------------------------------- tamper 偵測

def test_archive_tampered_directory_refused(tmp_path):
    """Codex 重現：目錄 index.html 被篡改後重跑唔可以 rc=0"""
    make_site(tmp_path)
    root, _zip = first_archive(tmp_path)
    (root / 'index.html').write_text('<html>TAMPERED</html>', encoding='utf-8')
    assert run_archive(tmp_path) == 1, '篡改目錄實際 bytes 必須拒絕'
    assert 'TAMPERED' in (root / 'index.html').read_text(encoding='utf-8'), '唔可以修復性覆寫'


def test_archive_tampered_checksums_refused(tmp_path):
    make_site(tmp_path)
    root, _zip = first_archive(tmp_path)
    lines = (root / 'CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines()
    lines[0] = '0' * 64 + lines[0][64:]
    (root / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert run_archive(tmp_path) == 1


def test_archive_extra_or_missing_file_refused(tmp_path):
    make_site(tmp_path)
    root, _zip = first_archive(tmp_path)
    (root / 'extra.txt').write_text('x', encoding='utf-8')
    assert run_archive(tmp_path) == 1
    (root / 'extra.txt').unlink()
    (root / 'reports' / 'pytest.txt').unlink()
    assert run_archive(tmp_path) == 1


def test_archive_tampered_zip_refused(tmp_path):
    make_site(tmp_path)
    root, zip_path = first_archive(tmp_path)
    # 在 zip 內加多餘 entry
    with zipfile.ZipFile(zip_path, 'a') as z:
        z.writestr('evil.txt', b'evil')
    assert run_archive(tmp_path) == 1, 'zip 多餘 entry 必須拒絕'


def test_archive_duplicate_zip_entry_refused(tmp_path):
    make_site(tmp_path)
    root, zip_path = first_archive(tmp_path)
    with zipfile.ZipFile(zip_path, 'a') as z:
        z.writestr('index.html', b'dup')
    assert run_archive(tmp_path) == 1, 'zip 重複 entry 必須拒絕'


def test_archive_provenance_mismatch_refused(tmp_path):
    make_site(tmp_path)
    root, _zip = first_archive(tmp_path)
    prov = json.load(open(root / 'PROVENANCE.json', encoding='utf-8'))
    prov['archiveCommit'] = 'c' * 40
    (root / 'PROVENANCE.json').write_text(json.dumps(prov), encoding='utf-8')
    # CHECKSUMS 亦更新，令唯一差異係 provenance facts
    lines = []
    for line in (root / 'CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines():
        digest, rel = line.split('  ', 1)
        if rel == 'PROVENANCE.json':
            digest = archive.sha256_file(str(root / 'PROVENANCE.json'))
        lines.append(f'{digest}  {rel}')
    (root / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert run_archive(tmp_path) == 1, 'provenance commit 唔一致必須拒絕'


def test_archive_identical_rerun_idempotent(tmp_path):
    make_site(tmp_path)
    root, _zip = first_archive(tmp_path)
    before = (root / 'CHECKSUMS.sha256').read_bytes()
    assert run_archive(tmp_path) == 0
    assert (root / 'CHECKSUMS.sha256').read_bytes() == before


def test_archive_missing_zip_or_dir_refused(tmp_path):
    make_site(tmp_path)
    root, zip_path = first_archive(tmp_path)
    zip_path.unlink()
    assert run_archive(tmp_path) == 1, '只有目錄、缺 zip 必須拒絕'
    assert not zip_path.exists(), '唔可以靜默補回 zip'
    # 兩者都唔存在 → 可以建立全新歸檔（唔係半完成狀態）
    shutil.rmtree(root)
    assert run_archive(tmp_path) == 0
    assert (root / 'CHECKSUMS.sha256').is_file() and zip_path.is_file()


# ---------------------------------------------------------------- manifest

def test_archive_manifest_validation(tmp_path):
    make_site(tmp_path)
    cases = {
        'absolute': ['/etc/passwd'] + PAYLOAD,
        'traversal': ['../outside.txt'] + PAYLOAD,
        'duplicate': PAYLOAD + [PAYLOAD[0]],
        'non_string': PAYLOAD + [123],
        'missing_required_type': ['index.html'] + ['空調對比報告.pdf'],
    }
    for name, files in cases.items():
        with open(tmp_path / 'deploy_payload.json', 'w', encoding='utf-8') as f:
            json.dump({'files': files}, f)
        rc = run_archive(tmp_path)
        assert rc == 1, f'manifest {name} 應該被拒'
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


def test_archive_manifest_symlink_escape_refused(tmp_path):
    make_site(tmp_path)
    outside = tmp_path.parent / 'outside-secret.txt'
    outside.write_text('secret', encoding='utf-8')
    link = tmp_path / 'link.txt'
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return  # 平台唔支援 symlink，跳過（CI Linux 會跑到）
    with open(tmp_path / 'deploy_payload.json', 'w', encoding='utf-8') as f:
        json.dump({'files': PAYLOAD + ['link.txt']}, f)
    assert run_archive(tmp_path) == 1


# ---------------------------------------------------------------- release 等級

def test_release_archive_requires_valid_reports(tmp_path):
    make_site(tmp_path)
    assert run_archive(tmp_path, extra=['--release']) == 1, '缺 acceptance／報告唔可以 release'
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


def test_release_accepts_machine_reports(tmp_path):
    make_site(tmp_path)
    make_release_reports(tmp_path / 'reports')
    assert run_archive(tmp_path, extra=['--release']) == 0
    prov = json.load(open(tmp_path / 'out' / f'v{VERSION}' / 'PROVENANCE.json', encoding='utf-8'))
    assert prov['archiveType'] == 'release'


def test_release_rejects_text_ok_or_failed_reports(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    for body in ('ok', 'FAILED'):
        make_release_reports(reports)
        (reports / 'acceptance.json').write_text(body, encoding='utf-8')
        assert run_archive(tmp_path, extra=['--release']) == 1, body


def test_release_rejects_nonzero_missing_duplicate_unknown_gates(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    cases = [
        _valid_acceptance(reports, gate_overrides={'PYTEST': 1}),
        _valid_acceptance(reports, drop='VALIDATE_DATA'),
        _valid_acceptance(reports, duplicate='PYTEST'),
        _valid_acceptance(reports, unknown=True),
    ]
    for acc in cases:
        make_release_reports(reports, acceptance=acc)
        assert run_archive(tmp_path, extra=['--release']) == 1, acc.get('gates')


def test_release_rejects_tampered_log(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    acc = _valid_acceptance(reports)
    make_release_reports(reports, acceptance=acc)
    (reports / 'logs' / 'PYTEST.log').write_text('tampered', encoding='utf-8')
    assert run_archive(tmp_path, extra=['--release']) == 1


def test_release_rejects_weak_feature_report(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    weak = [
        {'ok': True},  # 只有 ok
        {'ok': True, 'collection': {'matchedNodes': []}, 'bindings': {}, 'failures': [],
         'run': {'exitStatus': 0, 'tests': {}}},
        {'ok': True, 'collection': {'matchedNodes': ['n'], 'deselected': []},
         'bindings': {'n': {'status': 'missing'}}, 'failures': [],
         'run': {'exitStatus': 0, 'tests': {'n': {'result': 'passed'}}}},
    ]
    for feat in weak:
        make_release_reports(reports, feature=feat)
        assert run_archive(tmp_path, extra=['--release']) == 1, feat


def test_release_rejects_weak_postdeploy_report(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    weak = [
        {'ok': True},
        {'ok': True, 'failures': [], 'checks': [{'check': 'metadata.http_200', 'pass': True}],
         'browser': [{'check': 'browser.compare_modal', 'pass': True}]},
    ]
    for post in weak:
        make_release_reports(reports, postdeploy=post)
        assert run_archive(tmp_path, extra=['--release']) == 1, post


def _rewrite_junit(reports, xml):
    """覆寫 acceptance PYTEST gate 指向嘅 junit 並同步 hash（只測結構）。"""
    path = reports / 'logs' / 'pytest-junit.xml'
    path.write_text(xml, encoding='utf-8')
    acc_path = reports / 'acceptance.json'
    data = json.load(open(acc_path, encoding='utf-8'))
    for g in data['gates']:
        if g['id'] == 'PYTEST':
            g['junitSha256'] = archive.sha256_file(str(path))
    acc_path.write_text(json.dumps(data), encoding='utf-8')


def test_release_rejects_bad_junit(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    make_release_reports(reports)
    _rewrite_junit(reports, '<?xml version="1.0"?><testsuites><testsuite tests="1">'
                            '<testcase classname="t" name="bad"><failure>boom</failure>'
                            '</testcase></testsuite></testsuites>')
    assert run_archive(tmp_path, extra=['--release']) == 1
    _rewrite_junit(reports, '<testsuites></testsuites>')
    assert run_archive(tmp_path, extra=['--release']) == 1, '冇 testcase 都要拒'


def test_archive_privacy_scan_blocks_private_paths(tmp_path):
    make_site(tmp_path)
    (tmp_path / 'reports' / 'leak.txt').write_text(
        r'path C:\Users\RealPerson\secret' + '\n', encoding='utf-8')
    assert run_archive(tmp_path) == 1
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()
    assert not list((tmp_path / 'out').glob('.staging-*')), 'staging 例外必須清理'


def test_archive_tag_validation():
    for good in ('v1.2.9', '1.2.9', 'v1.2.9-rc.1', 'v1.2.9+build.5'):
        assert archive.valid_tag(good), good
    for bad in ('v1.2', '1.2', 'v1.2.9\n', 'v1.2.9 ', '../evil', 'v1.2.9/../x',
                'v01.2.9', '', None, 'x' * 200):
        assert not archive.valid_tag(bad), bad


def test_archive_rejects_hash_mismatch_before_writing(tmp_path):
    make_site(tmp_path)
    meta = json.load(open(tmp_path / 'metadata.json', encoding='utf-8'))
    meta['releasePayloadHash'] = 'sha256:' + '0' * 64
    (tmp_path / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
    assert run_archive(tmp_path) == 1
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


# ---------------------------------------------------------------- R5 report path trust

def _make_valid_release(tmp_path):
    make_site(tmp_path)
    reports = tmp_path / 'reports'
    make_release_reports(reports)
    return reports


def _mutate_acceptance(reports, mutate):
    acc_path = reports / 'acceptance.json'
    data = json.load(open(acc_path, encoding='utf-8'))
    mutate(data)
    acc_path.write_text(json.dumps(data), encoding='utf-8')


def test_release_rejects_traversal_absolute_backslash_log(tmp_path):
    make_site(tmp_path)
    reports = _make_valid_release(tmp_path)
    for bad in ('../outside.log', 'C:/abs.log', 'logs\\PYTEST.log', 'logs/./PYTEST.log',
                '/absolute.log'):
        _mutate_acceptance(reports, lambda d, b=bad: [
            g for g in d['gates'] if g['id'] == 'PYTEST'
        ][0].update({'log': b}))
        assert run_archive(tmp_path, extra=['--release']) == 1, bad
        assert not (tmp_path / 'out' / f'v{VERSION}').exists(), bad


def test_release_rejects_symlink_log(tmp_path):
    make_site(tmp_path)
    reports = _make_valid_release(tmp_path)
    outside = tmp_path / 'outside.log'
    outside.write_text('outside', encoding='utf-8')
    link = reports / 'logs' / 'evil.log'
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return
    _mutate_acceptance(reports, lambda d: [
        g for g in d['gates'] if g['id'] == 'PYTEST'
    ][0].update({'log': 'logs/evil.log', 'logSha256': archive.sha256_file(str(outside))}))
    assert run_archive(tmp_path, extra=['--release']) == 1
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


def test_release_rejects_duplicate_reports(tmp_path):
    make_site(tmp_path)
    reports = _make_valid_release(tmp_path)
    # duplicate acceptance（巢狀冒充）
    nested = reports / 'nested'
    nested.mkdir()
    (nested / 'acceptance.json').write_text('{}', encoding='utf-8')
    assert run_archive(tmp_path, extra=['--release']) == 1
    (nested / 'acceptance.json').unlink()
    # duplicate feature-check
    (nested / 'feature-check.json').write_text('{}', encoding='utf-8')
    assert run_archive(tmp_path, extra=['--release']) == 1
    assert not (tmp_path / 'out' / f'v{VERSION}').exists()


def test_release_rejects_acceptance_schema_and_identity(tmp_path):
    make_site(tmp_path)
    reports = _make_valid_release(tmp_path)
    cases = [
        lambda d: d.update({'schemaVersion': 2}),
        lambda d: d.update({'runner': 'scripts/evil.py'}),
        lambda d: d.update({'ok': 'true'}),
        lambda d: d.update({'commit': 'c' * 40}),          # 唔等於 archiveCommit b*40
        lambda d: d.update({'commit': 'not-a-sha'}),
        lambda d: d.update({'startedAt': 'yesterday'}),
    ]
    for mutate in cases:
        _mutate_acceptance(reports, mutate)
        assert run_archive(tmp_path, extra=['--release']) == 1, mutate
        assert not (tmp_path / 'out' / f'v{VERSION}').exists()
        # 還原有效 acceptance 再測下一 case
        make_release_reports(reports)
