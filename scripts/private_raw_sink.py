#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D7-A：private raw archive sink（可插拔 adapter）。

現有實作：
  - `local-dir`：本機／mount 目錄（非 durable remote；只在未配置遠端時使用，
    公開 receipt 會如實標示 `durableRemote: false`）；
  - `github-release-asset`：Private GitHub Release asset（供應端 PRIVATE 回讀、
    上傳後下載 hash 核驗、90 日 retention、同名唔覆蓋）。呢個 adapter 今次只提供
    實作同 fake HTTP 測試；未經人類確認遠端方案前，正式 CI 唔會配置、亦唔會建立
    任何 Release／上傳任何資產。

安全契約：
  - raw bytes 只會經過記憶體／repo 外 temp；錯誤訊息、report 一律唔會包含 raw
    bytes、token 或私人 URL；
  - remote 必須 provider 回讀 `private: true`；上傳後強制下載核對 sha256 + size；
  - `require` 模式下未配置／配置不完整即 raise（唔會靜默降級）；
  - local cleanup 只刪 sink 內、`run-` 前綴、非 symlink／junction 而且無逃逸嘅
    目錄；驗證失敗唔會刪任何既有內容。
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_RETENTION_DAYS = 90
ASSET_PREFIX = 'raw-'


class SinkError(RuntimeError):
    pass


def _utc_stamp(now):
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _run_stamp(now):
    return (now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')


def _manifest(records, archive_hash, created_at):
    return {
        'schemaVersion': 1,
        'createdAt': created_at,
        'pageCount': len(records),
        'archiveHash': archive_hash,
        'pages': [{k: v for k, v in rec.items() if k != '_raw'}
                  for rec in sorted(records, key=lambda r: r['page'])],
    }


def _write_pages(dst_dir, records):
    for rec in sorted(records, key=lambda r: r['page']):
        name = f'p{rec["page"]:02d}.html'
        with open(os.path.join(dst_dir, name), 'wb') as f:
            f.write(rec['_raw'])
            f.flush()
            os.fsync(f.fileno())


def _is_link_like(path):
    """symlink 或 Windows junction 都當 link-like（junction islink() 係 False）。"""
    if os.path.islink(path):
        return True
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return getattr(st, 'st_reparse_tag', 0) != 0


def _assert_inside(base, path, label):
    real_base = os.path.realpath(base)
    real_path = os.path.realpath(path)
    try:
        inside = os.path.commonpath([real_base, real_path]) == real_base
    except ValueError:
        inside = False
    if not inside:
        raise SinkError(f'{label} 逃出 sink（realpath 不符）')


def cleanup_expired(sink_dir, now=None, max_age_days=DEFAULT_RETENTION_DAYS):
    """刪除 sink 內超齡 run 目錄；只處理安全目錄，回傳被刪名 list。"""
    if not sink_dir or _is_link_like(sink_dir) or not os.path.isdir(sink_dir):
        return []
    now = now or datetime.now(timezone.utc)
    removed = []
    for name in sorted(os.listdir(sink_dir)):
        path = os.path.join(sink_dir, name)
        if not name.startswith('run-') or _is_link_like(path) or not os.path.isdir(path):
            continue
        try:
            _assert_inside(sink_dir, path, 'cleanup 目標')
        except SinkError:
            continue
        created = None
        try:
            with open(os.path.join(path, 'manifest.json'), encoding='utf-8') as f:
                created_raw = json.load(f).get('createdAt')
            created = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
        except Exception:  # noqa: BLE001
            created = None
        if created is None:
            created = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        if now - created > timedelta(days=max_age_days):
            shutil.rmtree(path)
            removed.append(name)
    return removed


def persist_local(records, sink_dir, now=None, retention_days=DEFAULT_RETENTION_DAYS):
    sink = os.path.abspath(sink_dir)
    if _is_link_like(sink):
        raise SinkError('private sink 係 symlink／junction，拒絕寫入')
    os.makedirs(sink, exist_ok=True)
    if _is_link_like(sink):
        raise SinkError('private sink 建立後變成 symlink／junction，拒絕寫入')
    final_dir = os.path.join(sink, 'run-' + _run_stamp(now))
    if os.path.lexists(final_dir):
        final_dir = final_dir + '-' + str(os.getpid())
    tmp = tempfile.mkdtemp(prefix='.tmp-run-', dir=sink)
    try:
        _write_pages(tmp, records)
        manifest = _manifest(records, records_archive_hash(records), _utc_stamp(now))
        with open(os.path.join(tmp, 'manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if _is_link_like(final_dir):
            raise SinkError('目標 run 目錄係 symlink／junction，拒絕覆蓋')
        os.replace(tmp, final_dir)
        _assert_inside(sink, final_dir, 'run 目錄')
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    removed = cleanup_expired(sink, now=now, max_age_days=retention_days)
    return {
        'adapter': 'local-dir',
        'persisted': True,
        'verified': True,
        'durableRemote': False,
        'objectId': os.path.basename(final_dir),
        'archiveHash': manifest['archiveHash'],
        'retentionDays': retention_days,
        'expiredRemoved': len(removed),
    }


def records_archive_hash(records):
    """頁序 + 長度前綴 framing；同 fetch_emsd.archive_hash 單一契約（不可各自漂移）。"""
    h = hashlib.sha256()
    for rec in sorted(records, key=lambda r: r['page']):
        page = str(rec['page']).encode('ascii')
        data = rec['_raw']
        h.update(len(page).to_bytes(8, 'big'))
        h.update(page)
        h.update(len(data).to_bytes(8, 'big'))
        h.update(data)
    return 'sha256:' + h.hexdigest()


def build_zip(records, archive_hash, created_at):
    """確定性 zip bytes（固定 timestamp，內容 = raw pages + manifest.json）。"""
    buf = io.BytesIO()
    manifest = _manifest(records, archive_hash, created_at)
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rec in sorted(records, key=lambda r: r['page']):
            info = zipfile.ZipInfo(f'p{rec["page"]:02d}.html', date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, rec['_raw'])
        info = zipfile.ZipInfo('manifest.json', date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    return buf.getvalue()


class GitHubReleaseAssetSink:
    """Private GitHub Release asset adapter（fake HTTP 可測；未經批准唔會實際使用）。"""

    def __init__(self, repo, token, release_tag='emsd-raw-archive',
                 retention_days=DEFAULT_RETENTION_DAYS, api=None):
        self.repo = repo
        self.token = token
        self.release_tag = release_tag
        self.retention_days = retention_days
        self._api = api or self._default_api

    # ---- HTTP 層：path 相對 https://api.github.com；upload path 用完整 host ----
    def _default_api(self, method, path, data=None, headers=None):
        url = path if path.startswith('http') else 'https://api.github.com' + path
        hdrs = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'aircon-raw-sink',
            **({'Authorization': f'Bearer {self.token}'} if self.token else {}),
        }
        if headers:
            hdrs.update(headers)
        req = urlrequest.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urlrequest.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urlerror.HTTPError as e:
            return e.code, b''

    def _json(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'} if body else None
        status, raw = self._api(method, path, body, headers)
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw.decode('utf-8'))
            except ValueError:
                parsed = None
        return status, parsed

    def provider_private(self):
        status, repo = self._json('GET', f'/repos/{self.repo}')
        if status != 200 or not isinstance(repo, dict):
            raise SinkError(f'provider 回讀失敗（HTTP {status}）')
        if repo.get('private') is not True:
            raise SinkError('provider repo 唔係 PRIVATE，拒絕放原始 bytes')
        return True

    def _release(self):
        status, rel = self._json('GET', f'/repos/{self.repo}/releases/tags/{self.release_tag}')
        if status == 200 and isinstance(rel, dict):
            return rel
        if status != 404:
            raise SinkError(f'release 回讀失敗（HTTP {status}）')
        status, rel = self._json('POST', f'/repos/{self.repo}/releases', {
            'tag_name': self.release_tag,
            'name': self.release_tag,
            'draft': False,
            'prerelease': True,
            'body': 'EMSD raw archive retention store（機器管理，唔好手動改）',
        })
        if status != 201 or not isinstance(rel, dict):
            raise SinkError(f'release 建立失敗（HTTP {status}）')
        return rel

    def _assets(self, release_id):
        status, assets = self._json('GET',
                                    f'/repos/{self.repo}/releases/{release_id}/assets?per_page=100')
        if status != 200 or not isinstance(assets, list):
            raise SinkError(f'asset 列舉失敗（HTTP {status}）')
        return assets

    def persist(self, records, archive_hash, now=None, created_at=None):
        created_at = created_at or _utc_stamp(now)
        blob = build_zip(records, archive_hash, created_at)
        object_hash = 'sha256:' + hashlib.sha256(blob).hexdigest()
        asset_name = f'{ASSET_PREFIX}{_run_stamp(now)}-{object_hash[7:19]}.zip'
        self.provider_private()
        release = self._release()
        assets = self._assets(release['id'])
        if any(a.get('name') == asset_name for a in assets):
            raise SinkError(f'asset 同名已存在，拒絕覆蓋：{asset_name}')
        status, raw = self._api(
            'POST',
            f'https://uploads.github.com/repos/{self.repo}/releases/{release["id"]}'
            f'/assets?name={asset_name}',
            blob,
            {'Content-Type': 'application/zip'})
        if status != 201 or not raw:
            raise SinkError(f'asset 上傳失敗（HTTP {status}）')
        try:
            asset = json.loads(raw.decode('utf-8'))
        except ValueError:
            raise SinkError('asset 上傳回應唔係 JSON')
        # 上傳後下載核驗（provider → 本機 bytes）
        status, got = self._api('GET', asset['url'],
                                headers={'Accept': 'application/octet-stream'})
        if status != 200 or hashlib.sha256(got).hexdigest() != object_hash[7:]:
            raise SinkError('上傳後下載核驗失敗（hash／HTTP 唔符）')
        removed = self._cleanup(release['id'], now=now, keep=asset_name)
        return {
            'adapter': 'github-release-asset',
            'persisted': True,
            'verified': True,
            'durableRemote': True,
            'objectId': asset_name,
            'archiveHash': archive_hash,
            'objectHash': object_hash,
            'retentionDays': self.retention_days,
            'expiredRemoved': len(removed),
        }

    def _cleanup(self, release_id, now=None, keep=None):
        now = now or datetime.now(timezone.utc)
        removed = []
        for asset in self._assets(release_id):
            name = asset.get('name') or ''
            if name == keep or not name.startswith(ASSET_PREFIX):
                continue
            created = None
            try:
                created = datetime.fromisoformat(
                    (asset.get('created_at') or '').replace('Z', '+00:00'))
            except ValueError:
                created = None
            if created is None or now - created <= timedelta(days=self.retention_days):
                continue
            status, _ = self._api('DELETE', asset['url'])
            if status in (204, 200):
                removed.append(name)
        return removed


_REMOTE_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def adapter_from_env(env=None, api=None):
    """回傳 (kind, config)；部分配置（remote repo 無 token 等）即 raise，唔會靜默降級。"""
    env = env if env is not None else os.environ
    repo = (env.get('AIRCON_EMSD_RAW_REMOTE_REPO') or '').strip()
    token = env.get('AIRCON_EMSD_RAW_REMOTE_TOKEN') or ''
    local = (env.get('AIRCON_EMSD_RAW_SINK_DIR') or '').strip()
    if repo or token:
        if not repo or not token:
            raise SinkError('remote sink 配置不完整（repo／token 必須同時設定），拒絕降級')
        if not _REMOTE_RE.match(repo):
            raise SinkError('remote sink repo 格式錯誤')
        return 'remote', {
            'repo': repo,
            'token': token,
            'release_tag': env.get('AIRCON_EMSD_RAW_REMOTE_TAG') or 'emsd-raw-archive',
            'retention_days': int(env.get('AIRCON_EMSD_RAW_RETENTION_DAYS')
                                  or DEFAULT_RETENTION_DAYS),
            'api': api,
        }
    if local:
        return 'local', {'sink_dir': local}
    return None, {}


def persist_raw_archive(records, sink_dir=None, require=False, now=None, env=None, remote_api=None):
    """單一入口：按環境選 adapter；remote 失敗／require 未配置即 raise。"""
    archive_hash = records_archive_hash(records)
    kind, cfg = adapter_from_env(env)
    if sink_dir and kind != 'remote':
        kind, cfg = 'local', {'sink_dir': sink_dir}
    elif sink_dir and kind == 'remote':
        raise SinkError('已有 explicit sink_dir，但環境同時配置 remote；拒絕混用')
    if kind is None:
        if require:
            raise SinkError('private raw sink 未配置（require 模式唔可以靜默通過）')
        return {'adapter': None, 'persisted': False, 'verified': False,
                'durableRemote': False, 'objectId': None, 'archiveHash': archive_hash,
                'retentionDays': None, 'reason': 'not_configured'}
    if kind == 'local':
        result = persist_local(records, cfg['sink_dir'], now=now)
    else:
        sink = GitHubReleaseAssetSink(cfg['repo'], cfg['token'],
                                      release_tag=cfg['release_tag'],
                                      retention_days=cfg['retention_days'],
                                      api=cfg['api'] or remote_api)
        result = sink.persist(records, archive_hash, now=now)
    result['archiveHash'] = archive_hash
    if result.get('persisted') and not result.get('verified'):
        raise SinkError('adapter 未有下載核驗證據，拒絕發成功 receipt')
    return result


def main(argv=None):
    """CLI 只提供 adapter 自檢（唔會抓 EMSD）；實際抓取唔准由呢個腳本觸發。"""
    if argv is None:
        argv = sys.argv[1:]
    if '--help' in argv or not argv:
        print('private_raw_sink：由 fetch_emsd.py import 使用；'
              '本 CLI 唔會抓取或上傳（測試一律用 fake HTTP fixture）。')
        return 0
    print('❌ 唔支援直接執行；請用 fetch_emsd.py（有安全閘門）。', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
