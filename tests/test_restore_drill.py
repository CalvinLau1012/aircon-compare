# -*- coding: utf-8 -*-
"""本地應用＋數據恢復演練（公開、fixture-only、維持 SC-009）

完全喺 pytest tmp_path 內進行，唔需要 sudo／docker／自建部署線：
- R1 歸檔 CHECKSUMS 逐檔驗證 + 快照 tar checksum
- R2 兼容性：metadata schemaVersion 相同、產品版本 MAJOR 相同
- R3 成功恢復：先寫 staging、逐檔驗證，最後原子換入 live
- R4 篡改歸檔：checksum 驗證攔截，live good package 完好
- R5 數據恢復：truncated 失敗唔碰 live；完整歸檔先驗 staging 才寫 live
"""
import hashlib
import json
import os
import shutil
import tarfile


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_checksums(root, files):
    lines = [f'{sha256_file(os.path.join(root, f))}  {f}' for f in files]
    (root / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def check_checksums(root):
    """逐檔驗證 CHECKSUMS；任何缺檔／不符回 False。"""
    for line in (root / 'CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines():
        digest, rel = line.split('  ', 1)
        if not os.path.isfile(os.path.join(root, rel)):
            return False
        if sha256_file(os.path.join(root, rel)) != digest:
            return False
    return True


def restore_package(archive, live, staging):
    """先驗 checksum + 兼容版本，再寫 staging、逐檔驗證，最後才換入 live。"""
    if not check_checksums(archive):
        return 'checksum'
    try:
        cur_schema = json.loads((live / 'metadata.json').read_text(encoding='utf-8'))['schemaVersion']
        new_schema = json.loads((archive / 'metadata.json').read_text(encoding='utf-8'))['schemaVersion']
    except (OSError, ValueError, KeyError):
        return 'metadata'
    if cur_schema != new_schema:
        return 'schema'
    try:
        cur_major = json.loads((live / 'metadata.json').read_text(encoding='utf-8'))['version'].split('.')[0]
        new_major = json.loads((archive / 'metadata.json').read_text(encoding='utf-8'))['version'].split('.')[0]
    except (OSError, ValueError, KeyError):
        return 'metadata'
    if cur_major != new_major:
        return 'major'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for f in ('index.html', 'emsd_空調能源標籤.csv', 'metadata.json', 'model_blacklist.json'):
        shutil.copy2(archive / f, staging / f)
    if not check_checksums(archive) or any(
            sha256_file(staging / f) != sha256_file(archive / f)
            for f in ('index.html', 'emsd_空調能源標籤.csv', 'metadata.json', 'model_blacklist.json')):
        return 'staging'
    old = live.with_name(live.name + '.old')
    if old.exists():
        shutil.rmtree(old)
    os.replace(live, old)
    try:
        os.replace(staging, live)
    except OSError:
        os.replace(old, live)
        return 'swap'
    shutil.rmtree(old)
    return ''


def live_state(live):
    out = {}
    for root, _dirs, names in os.walk(live):
        for n in names:
            p = os.path.join(root, n)
            out[os.path.relpath(p, live)] = sha256_file(p)
    return out


def make_fixture(tmp_path):
    archive = tmp_path / 'archive'
    live = tmp_path / 'live'
    archive.mkdir()
    live.mkdir()
    (archive / 'index.html').write_text('<html>ARCHIVED v1.2.9</html>', encoding='utf-8')
    (archive / 'emsd_空調能源標籤.csv').write_text('品牌,型號\n開利,CHK18\n', encoding='utf-8')
    (archive / 'metadata.json').write_text(
        json.dumps({'schemaVersion': '1.0.0', 'version': '1.2.9', 'datasetDate': '2026-09-21'}),
        encoding='utf-8')
    (archive / 'model_blacklist.json').write_text('{"version":1,"models":{}}', encoding='utf-8')
    files = ['index.html', 'emsd_空調能源標籤.csv', 'metadata.json', 'model_blacklist.json']
    write_checksums(archive, files)
    with tarfile.open(archive / 'data-snapshot.tar.gz', 'w:gz') as tar:
        for f in ('emsd_空調能源標籤.csv', 'model_blacklist.json', 'metadata.json'):
            tar.add(archive / f, arcname=f)
    (archive / 'data-snapshot.tar.gz.sha256').write_text(
        f'{sha256_file(archive / "data-snapshot.tar.gz")}  data-snapshot.tar.gz\n', encoding='utf-8')
    (live / 'index.html').write_text('<html>LIVE-OLD</html>', encoding='utf-8')
    (live / 'metadata.json').write_text(
        json.dumps({'schemaVersion': '1.0.0', 'version': '1.2.8', 'datasetDate': '2026-09-20'}),
        encoding='utf-8')
    (live / 'emsd_空調能源標籤.csv').write_text('品牌,型號\n開利,OLD\n', encoding='utf-8')
    (live / 'model_blacklist.json').write_text('{"version":1,"models":{"OLD":{}}}', encoding='utf-8')
    return archive, live


def test_r1_archive_checksums(tmp_path):
    archive, _live = make_fixture(tmp_path)
    assert check_checksums(archive)
    digest, rel = (archive / 'data-snapshot.tar.gz.sha256').read_text(encoding='utf-8').split()
    assert rel == 'data-snapshot.tar.gz'
    assert sha256_file(archive / 'data-snapshot.tar.gz') == digest


def test_r3_successful_restore_is_atomic(tmp_path):
    archive, live = make_fixture(tmp_path)
    assert restore_package(archive, live, tmp_path / 'staging.good') == ''
    assert (live / 'index.html').read_text(encoding='utf-8') == '<html>ARCHIVED v1.2.9</html>'
    for f in ('index.html', 'emsd_空調能源標籤.csv', 'metadata.json', 'model_blacklist.json'):
        assert sha256_file(live / f) == sha256_file(archive / f)
    assert not (tmp_path / 'staging.good').exists()


def test_r4_tampered_archive_refused_live_untouched(tmp_path):
    archive, live = make_fixture(tmp_path)
    before = live_state(live)
    (archive / 'index.html').write_text('<html>TAMPERED</html>', encoding='utf-8')
    assert restore_package(archive, live, tmp_path / 'staging.bad') == 'checksum'
    assert live_state(live) == before, '失敗時 live 必須完好'
    assert not (tmp_path / 'staging.bad').exists(), 'checksum 失敗前唔應該建立 staging'


def test_r2_incompatible_schema_or_major_refused(tmp_path):
    archive, live = make_fixture(tmp_path)
    (live / 'metadata.json').write_text(
        json.dumps({'schemaVersion': '2.0.0', 'version': '2.0.0'}), encoding='utf-8')
    assert restore_package(archive, live, tmp_path / 'staging.x') == 'schema'
    (live / 'metadata.json').write_text(
        json.dumps({'schemaVersion': '1.0.0', 'version': '9.0.0'}), encoding='utf-8')
    assert restore_package(archive, live, tmp_path / 'staging.x') == 'major'


def test_r5_truncated_data_restore_does_not_touch_live(tmp_path):
    archive, live = make_fixture(tmp_path)
    before = live_state(live)
    truncated = tmp_path / 'truncated.tar.gz'
    truncated.write_bytes((archive / 'data-snapshot.tar.gz').read_bytes()[:20])
    stage = tmp_path / 'data-stage'
    stage.mkdir()
    ok = True
    try:
        with tarfile.open(truncated) as tar:
            tar.extractall(stage, filter="data")
    except Exception:  # noqa: BLE001 - truncated 必然失敗
        ok = False
    assert not ok, 'truncated 歸檔唔應該成功解壓'
    assert live_state(live) == before, '失敗嘅數據恢復唔可以碰 live'
    # 完整歸檔：先解 staging、驗 checksum，才寫 live
    stage2 = tmp_path / 'data-stage2'
    stage2.mkdir()
    with tarfile.open(archive / 'data-snapshot.tar.gz') as tar:
        tar.extractall(stage2, filter="data")
    checksums = dict(line.split('  ', 1)[::-1] for line in
                     (archive / 'CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines())
    for f in ('emsd_空調能源標籤.csv', 'model_blacklist.json', 'metadata.json'):
        assert sha256_file(stage2 / f) == checksums[f]
        shutil.copy2(stage2 / f, live / f)
    assert sha256_file(live / 'emsd_空調能源標籤.csv') == sha256_file(archive / 'emsd_空調能源標籤.csv')
