# -*- coding: utf-8 -*-
"""D7-A private raw sink：remote adapter 契約（fake HTTP）、retention、逃逸防禦。"""
import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import private_raw_sink as prs  # noqa: E402

_FETCH_SPEC = importlib.util.spec_from_file_location('fetch_emsd_sink_mod',
                                                     os.path.join(BASE, 'fetch_emsd.py'))
fetch = importlib.util.module_from_spec(_FETCH_SPEC)
_FETCH_SPEC.loader.exec_module(fetch)

NOW = dt.datetime(2026, 9, 23, 12, 0, 0, tzinfo=dt.timezone.utc)
RECORDS = [fetch.raw_page_record(1, b'<table>page-1</table>'),
           fetch.raw_page_record(2, b'<table>page-2</table>')]
TOKEN = 'ghp_totally-secret-token'
REPO = 'owner/private-archive'


class FakeGitHub:
    def __init__(self, private=True, upload_status=201, download_mutator=None):
        self.private = private
        self.upload_status = upload_status
        self.download_mutator = download_mutator
        self.calls = []
        self.assets = []
        self.uploaded = {}
        self.release = None
        self.next_id = 1

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path))
        if method == 'GET' and path == f'/repos/{REPO}':
            return 200, json.dumps({'private': self.private, 'full_name': REPO}).encode()
        if method == 'GET' and '/releases/tags/' in path:
            if self.release:
                return 200, json.dumps(self.release).encode()
            return 404, b''
        if method == 'POST' and path.endswith('/releases'):
            self.release = {'id': 5, 'tag_name': 'emsd-raw-archive'}
            return 201, json.dumps(self.release).encode()
        if method == 'GET' and path.endswith('/releases/5/assets?per_page=100'):
            return 200, json.dumps(self.assets).encode()
        if method == 'POST' and path.startswith('https://uploads.github.com/'):
            name = parse_qs(urlparse(path).query)['name'][0]
            self.uploaded[name] = data or b''
            self.next_id = max([a['id'] for a in self.assets] + [0]) + 1
            asset = {'id': self.next_id, 'name': name,
                     'url': f'https://api.github.com/repos/{REPO}/releases/assets/{self.next_id}',
                     'created_at': '2026-09-23T00:00:00Z'}
            self.assets.append(asset)
            self.next_id += 1
            return self.upload_status, json.dumps(asset).encode()
        if method == 'GET' and '/releases/assets/' in path:
            aid = int(path.rsplit('/', 1)[1])
            name = next(a['name'] for a in self.assets if a['id'] == aid)
            blob = self.uploaded.get(name, b'')
            if self.download_mutator:
                blob = self.download_mutator(blob)
            return 200, blob
        if method == 'DELETE' and '/releases/assets/' in path:
            aid = path.rsplit('/', 1)[1]
            self.assets = [a for a in self.assets if str(a['id']) != aid]
            return 204, b''
        return 404, b''


def _env(**over):
    env = {'AIRCON_EMSD_RAW_REMOTE_REPO': REPO, 'AIRCON_EMSD_RAW_REMOTE_TOKEN': TOKEN}
    env.update(over)
    return env


def test_remote_persist_private_readback_download_verify_and_redaction():
    fake = FakeGitHub()
    result = prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)
    assert result['persisted'] is True and result['verified'] is True
    assert result['durableRemote'] is True
    assert result['adapter'] == 'github-release-asset'
    assert result['archiveHash'] == prs.records_archive_hash(RECORDS)
    assert len(fake.uploaded) == 1
    name, blob = next(iter(fake.uploaded.items()))
    assert name.startswith('raw-') and name.endswith('.zip')
    assert hashlib.sha256(blob).hexdigest() in result['objectHash']
    text = json.dumps(result, ensure_ascii=False)
    assert TOKEN not in text and '<table>' not in text
    methods = [m for m, _p in fake.calls]
    assert 'POST' in methods  # release + upload
    # provider 回讀一定喺上傳之前
    upload_idx = next(i for i, (m, p) in enumerate(fake.calls) if p.startswith('https://uploads'))
    repo_idx = next(i for i, (m, p) in enumerate(fake.calls) if p == f'/repos/{REPO}')
    assert repo_idx < upload_idx


def test_remote_requires_private_provider_before_upload():
    fake = FakeGitHub(private=False)
    with pytest.raises(prs.SinkError, match='PRIVATE'):
        prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)
    assert not any(p.startswith('https://uploads') for _m, p in fake.calls)


def test_remote_download_verification_failure_blocks_success_receipt():
    fake = FakeGitHub(download_mutator=lambda b: b + b'tampered')
    with pytest.raises(prs.SinkError, match='下載核驗'):
        prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)


def test_remote_upload_failure_blocks_success_receipt():
    fake = FakeGitHub(upload_status=500)
    with pytest.raises(prs.SinkError, match='上傳失敗'):
        prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)


def test_remote_refuses_clobber_of_existing_asset():
    fake = FakeGitHub()
    fake.release = {'id': 5, 'tag_name': 'emsd-raw-archive'}
    archive_hash = prs.records_archive_hash(RECORDS)
    blob = prs.build_zip(RECORDS, archive_hash, '2026-09-23T12:00:00Z')
    object_hash = 'sha256:' + hashlib.sha256(blob).hexdigest()
    name = f'raw-20260923T120000Z-{object_hash[7:19]}.zip'
    fake.assets = [{'id': 9, 'name': name, 'created_at': '2026-09-23T00:00:00Z',
                    'url': f'https://api.github.com/repos/{REPO}/releases/assets/9'}]
    with pytest.raises(prs.SinkError, match='同名'):
        prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)
    assert not any(p.startswith('https://uploads') for _m, p in fake.calls)


def test_remote_retention_deletes_only_expired_matching_assets():
    fake = FakeGitHub()
    fake.release = {'id': 5, 'tag_name': 'emsd-raw-archive'}
    fake.assets = [
        {'id': 1, 'name': 'raw-20260623T120000Z-old.zip', 'created_at': '2026-06-23T12:00:00Z',
         'url': f'https://api.github.com/repos/{REPO}/releases/assets/1'},
        {'id': 2, 'name': 'raw-20260922T120000Z-fresh.zip', 'created_at': '2026-09-22T12:00:00Z',
         'url': f'https://api.github.com/repos/{REPO}/releases/assets/2'},
        {'id': 3, 'name': 'unrelated.zip', 'created_at': '2020-01-01T00:00:00Z',
         'url': f'https://api.github.com/repos/{REPO}/releases/assets/3'},
    ]
    result = prs.persist_raw_archive(RECORDS, env=_env(), now=NOW, remote_api=fake)
    assert result['expiredRemoved'] == 1
    names = [a['name'] for a in fake.assets]
    assert 'raw-20260623T120000Z-old.zip' not in names
    assert 'raw-20260922T120000Z-fresh.zip' in names
    assert 'unrelated.zip' in names
    # 剛剛上傳嘅對象唔會被自己清理
    assert any(n.startswith('raw-20260923') for n in names)


@pytest.mark.parametrize('env', [
    {'AIRCON_EMSD_RAW_REMOTE_REPO': REPO},
    {'AIRCON_EMSD_RAW_REMOTE_TOKEN': TOKEN},
    {'AIRCON_EMSD_RAW_REMOTE_REPO': 'bad repo', 'AIRCON_EMSD_RAW_REMOTE_TOKEN': TOKEN},
])
def test_partial_or_bad_remote_config_never_silently_degrades(env):
    with pytest.raises(prs.SinkError):
        prs.persist_raw_archive(RECORDS, env=env, now=NOW)


def test_require_without_any_sink_blocks():
    with pytest.raises(prs.SinkError, match='未配置'):
        prs.persist_raw_archive(RECORDS, env={}, now=NOW, require=True)
    out = prs.persist_raw_archive(RECORDS, env={}, now=NOW, require=False)
    assert out['persisted'] is False and out['adapter'] is None


def _try_symlink(link, target, target_is_directory=False):
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
        return True
    except (OSError, NotImplementedError):
        return False


def _try_junction(link, target):
    if os.name != 'nt':
        return False
    proc = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                          capture_output=True, text=True)
    return proc.returncode == 0


def test_local_sink_rejects_symlink_or_junction_sink(tmp_path, monkeypatch):
    real = tmp_path / 'real-sink'
    real.mkdir()
    link = tmp_path / 'link-sink'
    made = _try_symlink(link, real, target_is_directory=True) or _try_junction(link, real)
    if made:
        with pytest.raises(prs.SinkError, match='symlink|逃出'):
            prs.persist_local(RECORDS, str(link), now=NOW)
    else:
        real_islink = prs.os.path.islink
        monkeypatch.setattr(prs.os.path, 'islink',
                            lambda p: True if str(p).endswith('link-sink') else real_islink(p))
        with pytest.raises(prs.SinkError, match='symlink'):
            prs.persist_local(RECORDS, str(link), now=NOW)


def test_local_cleanup_skips_symlink_escape_and_outside_content(tmp_path, monkeypatch):
    sink = tmp_path / 'sink'
    sink.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'manifest.json').write_text(json.dumps({'createdAt': '2020-01-01T00:00:00Z'}),
                                           encoding='utf-8')
    (outside / 'important.txt').write_text('keep', encoding='utf-8')
    link = sink / 'run-20200101T000000Z'
    made = _try_symlink(link, outside, target_is_directory=True) or _try_junction(link, outside)
    if made:
        assert prs.cleanup_expired(str(sink), now=NOW) == []
        assert (outside / 'important.txt').read_text(encoding='utf-8') == 'keep'
    else:
        real_islink = prs.os.path.islink
        monkeypatch.setattr(prs.os.path, 'islink',
                            lambda p: True if str(p).endswith('run-20200101T000000Z') else real_islink(p))
        assert prs.cleanup_expired(str(sink), now=NOW) == []
        assert (outside / 'important.txt').read_text(encoding='utf-8') == 'keep'


class _CannedRemoteSink:
    calls = []

    def __init__(self, repo, token, release_tag='emsd-raw-archive', retention_days=90, api=None):
        self.repo, self.token = repo, token
        self.api = api
        _CannedRemoteSink.calls.append({'repo': repo, 'token': token, 'api': api})

    def persist(self, records, archive_hash, now=None, created_at=None):
        return {'adapter': 'github-release-asset', 'persisted': True, 'verified': True,
                'durableRemote': True, 'objectId': 'raw-fake.zip',
                'archiveHash': archive_hash, 'retentionDays': 90, 'expiredRemoved': 0}


def test_fetch_emsd_remote_adapter_wiring_and_token_never_in_public_files(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    monkeypatch.setattr(fetch, 'RAW_RECEIPT_PATH', str(tmp_path / 'emsd_raw_receipt.json'))
    monkeypatch.setattr(fetch, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch, 'MIN_EMSD_ROWS', 100)
    monkeypatch.setenv('AIRCON_EMSD_RAW_REMOTE_REPO', REPO)
    monkeypatch.setenv('AIRCON_EMSD_RAW_REMOTE_TOKEN', TOKEN)
    monkeypatch.setattr(fetch.private_raw_sink, 'GitHubReleaseAssetSink', _CannedRemoteSink)
    _CannedRemoteSink.calls.clear()
    pages = ['<table><tr><th>品牌</th><th>型號</th><th>c0</th><th>c1</th><th>c2</th><th>c3</th>'
             '<th>c4</th><th>c5</th><th>c6</th><th>c7</th><th>c8</th><th>c9</th><th>c10</th>'
             '<th>c11</th><th>c12</th></tr>' +
             ''.join(f'<tr><td>A{i}</td><td>X{i}</td>' + '<td>1</td>' * 13 + '</tr>'
                     for i in range(50)) + '</table>',
             '<table>' + ''.join(f'<tr><td>B{i}</td><td>Y{i}</td>' + '<td>1</td>' * 13 + '</tr>'
                                 for i in range(50)) + '</table>',
             '']

    def fake_fetch(p):
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch
    assert fetch.main() is None
    raw = json.loads((tmp_path / 'emsd_raw_receipt.json').read_text(encoding='utf-8'))
    success = json.loads((tmp_path / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert raw['privateArchive']['adapter'] == 'github-release-asset'
    assert raw['privateArchive']['durableRemote'] is True
    assert raw['privateArchive']['verified'] is True
    assert 'rawReceiptHash' in success
    assert _CannedRemoteSink.calls and _CannedRemoteSink.calls[0]['repo'] == REPO
    for p in tmp_path.iterdir():
        if p.is_file():
            text = p.read_text(encoding='utf-8', errors='replace')
            assert TOKEN not in text, f'token 唔可以寫入公開檔：{p.name}'
            assert '<table>' not in text, f'raw bytes 唔可以寫入公開檔：{p.name}'


def test_fetch_emsd_remote_failure_writes_no_success_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    monkeypatch.setattr(fetch, 'RAW_RECEIPT_PATH', str(tmp_path / 'emsd_raw_receipt.json'))
    monkeypatch.setattr(fetch, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch, 'MIN_EMSD_ROWS', 100)
    monkeypatch.setenv('AIRCON_EMSD_RAW_REMOTE_REPO', REPO)
    monkeypatch.setenv('AIRCON_EMSD_RAW_REMOTE_TOKEN', TOKEN)

    class Boom(_CannedRemoteSink):
        def persist(self, records, archive_hash, now=None, created_at=None):
            raise prs.SinkError('上傳後下載核驗失敗')

    monkeypatch.setattr(fetch.private_raw_sink, 'GitHubReleaseAssetSink', Boom)
    pages = ['<table><tr><th>品牌</th><th>型號</th><th>c0</th><th>c1</th><th>c2</th><th>c3</th>'
             '<th>c4</th><th>c5</th><th>c6</th><th>c7</th><th>c8</th><th>c9</th><th>c10</th>'
             '<th>c11</th><th>c12</th></tr>' +
             ''.join(f'<tr><td>A{i}</td><td>X{i}</td>' + '<td>1</td>' * 13 + '</tr>'
                     for i in range(50)) + '</table>',
             '<table>' + ''.join(f'<tr><td>B{i}</td><td>Y{i}</td>' + '<td>1</td>' * 13 + '</tr>'
                                 for i in range(50)) + '</table>',
             '']

    def fake_fetch(p):
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch
    with pytest.raises(SystemExit) as e:
        fetch.main()
    assert e.value.code == 1
    assert not (tmp_path / 'emsd_receipt.json').exists()
    assert not (tmp_path / 'emsd_raw_receipt.json').exists()
