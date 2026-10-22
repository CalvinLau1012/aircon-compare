# -*- coding: utf-8 -*-
"""r24 F01 v4：每日價格交易鏈（candidate；ready-but-not-enabled）。

- 同 start 自然進度：anchor 係權威，meta idx=anchor.stage 時每日 entry 回 noop／允許
  runner 跑下一 stage；只有異 root、本地超前、needs-review、corrupt 先阻斷。
- 進度只由已驗 bundle 證明：reconcile 先讀完整 staged chain（base=前 post、effects 串聯、
  terminal state／anchor.lastBundleHash 綁定），喺 scratch 準備，成功先以 journal 提交
  四個 live 檔；失敗零 live 改動／可恢復。
- transition ack 要對實際 sourceRevision 同初始 baseline；冇實際事實唔會被接受。
- 月度 history immutable：完成 7/7 寫 HISTORY_DIR/<month>.json；rollover 要求上月
  completed 真實證明。
"""
import datetime
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import batch_utils  # noqa: E402

PRICE_BATCH_DAYS = 7
SCHEMA_VERSION = 2
PROTOCOL = 'monthly-cycle-v2'
HKT = datetime.timezone(datetime.timedelta(hours=8))
ANCHOR_PATH = 'coordinator/monthly-cycle.json'
HISTORY_PATH = 'coordinator/monthly-history/{month}.json'
TRANSITION_PATH = 'coordinator/monthly-transition.json'
SHA256_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
SOURCE_REV_RE = re.compile(r'^[0-9a-f]{40}$')
_STATUS = ('active', 'completed', 'needs-review')
REPO_FILES = ('biggo_prices.json', 'model_status.json', 'model_blacklist.json',
              'prices_meta.json')
JOURNAL_NAME = '.price-sync-journal.json'


class AnchorError(RuntimeError):
    pass


class AnchorConflict(RuntimeError):
    pass


def shared_mode(env=None):
    env = env if env is not None else os.environ
    ce = env.get('AIRCON_PRICE_CYCLE_ENABLED') == '1'
    sc = env.get('AIRCON_PRICE_SHARED_CYCLE') == '1'
    if ce != sc:
        raise AnchorError('price-mode-inconsistent:enabled=%s:shared=%s' % (ce, sc))
    return 'shared' if sc else 'off'


def _strict_json(text):
    def _reject(value):
        raise ValueError(f'非標準 JSON 常數：{value}')
    return json.loads(text, parse_constant=_reject)


def hkt_now(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, (int, float)):
        now = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(HKT)


def cycle_month(now=None):
    return hkt_now(now).strftime('%Y-%m')


def valid_date(v):
    if not isinstance(v, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        return False
    try:
        datetime.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def valid_applied_at(v):
    """Accept legacy date-only historical values and new full UTC timestamps."""
    if valid_date(v):
        return True
    if not isinstance(v, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', v):
        return False
    try:
        datetime.datetime.fromisoformat(v.replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def valid_month(v):
    if not isinstance(v, str) or not re.fullmatch(r'\d{4}-\d{2}', v):
        return False
    try:
        return 1 <= int(v[5:7]) <= 12
    except ValueError:
        return False


def cycle_identity(meta):
    start = str(meta.get('price_batch_start') or '')
    idx = meta.get('price_batch_idx', 0)
    if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx <= PRICE_BATCH_DAYS):
        return None
    if not valid_date(start) or idx >= PRICE_BATCH_DAYS:
        return None
    return f'{start}:{idx + 1}/{PRICE_BATCH_DAYS}'


def _anchor_cycle(started_at, stage):
    return f'{started_at}:{max(int(stage), 1)}/{PRICE_BATCH_DAYS}'


def validate_meta(meta):
    if not isinstance(meta, dict):
        return ['meta-not-object']
    try:
        batch_utils._validate_meta(meta, 'prices_meta')
    except batch_utils.MetaError as e:
        return [f'batch-utils:{e}']
    return []


def validate_anchor(doc):
    if not isinstance(doc, dict):
        return ['anchor-not-object']
    errs = []
    if doc.get('formatVersion') != SCHEMA_VERSION:
        errs.append('formatVersion-invalid')
    if doc.get('protocol') != PROTOCOL:
        errs.append('protocol-invalid')
    if not valid_month(doc.get('month')):
        errs.append('month-invalid')
    started = doc.get('startedAt')
    if not valid_date(started):
        errs.append('startedAt-invalid')
    stage = doc.get('stage')
    if isinstance(stage, bool) or not isinstance(stage, int) or not (0 <= stage <= PRICE_BATCH_DAYS):
        errs.append('stage-invalid')
    status = doc.get('status')
    if status not in _STATUS:
        errs.append('status-invalid')
    if isinstance(stage, int) and not isinstance(stage, bool) and status in _STATUS:
        if status == 'completed' and stage != PRICE_BATCH_DAYS:
            errs.append('completed-stage-not-7')
        if status == 'active' and stage >= PRICE_BATCH_DAYS:
            errs.append('active-stage-7')
    if valid_date(started) and isinstance(stage, int) and not isinstance(stage, bool):
        if doc.get('cycle') != _anchor_cycle(started, stage):
            errs.append('cycle-inconsistent')
    if valid_date(started) and valid_month(doc.get('month')):
        if started[:7] != doc['month']:
            errs.append('month-startedAt-mismatch')
    if 'lastBundleHash' in doc and not (isinstance(doc['lastBundleHash'], str)
                                        and SHA256_RE.fullmatch(doc['lastBundleHash'])):
        errs.append('lastBundleHash-invalid')
    return errs


def validate_transition(doc, *, actual_source=None, initial_base=None, writer_role=None):
    """形狀＋（可選）實際 sourceRevision／初始 baseline 綁定。"""
    if not isinstance(doc, dict):
        return ['transition-not-object']
    errs = []
    if doc.get('formatVersion') != SCHEMA_VERSION:
        errs.append('transition-formatVersion')
    if doc.get('protocol') != PROTOCOL:
        errs.append('transition-protocol')
    if doc.get('oldEntryStopped') is not True:
        errs.append('old-entry-not-acknowledged')
    writers = doc.get('writers')
    if not isinstance(writers, dict):
        errs.append('writers-invalid')
    else:
        for name in ('github', 'private'):
            w = writers.get(name)
            if not isinstance(w, dict):
                errs.append(f'writer-{name}-missing')
                continue
            if w.get('protocol') != PROTOCOL:
                errs.append(f'writer-{name}-protocol')
            rev = w.get('sourceRevision')
            if not isinstance(rev, str) or not SOURCE_REV_RE.fullmatch(rev):
                errs.append(f'writer-{name}-sourceRevision')
            elif actual_source and (writer_role is None or name == writer_role) \
                    and rev != actual_source:
                errs.append(f'writer-{name}-source-mismatch')
    initial = doc.get('initial')
    if not isinstance(initial, dict):
        errs.append('initial-invalid')
    else:
        for key in ('snapshotHash', 'baseHash'):
            if not isinstance(initial.get(key), str) or not SHA256_RE.fullmatch(initial[key]):
                errs.append(f'initial-{key}')
        if initial_base and isinstance(initial.get('snapshotHash'), str) \
                and initial['snapshotHash'] != initial_base:
            errs.append('initial-baseline-mismatch')
        if initial_base and isinstance(initial.get('baseHash'), str) \
                and initial['baseHash'] != initial_base:
            errs.append('initial-base-mismatch')
    if not valid_date(doc.get('at')):
        errs.append('transition-at-invalid')
    return errs


def transition_ack_bytes(github_rev, private_rev, snapshot_hash, base_hash, at=None):
    doc = {
        'formatVersion': SCHEMA_VERSION, 'protocol': PROTOCOL, 'oldEntryStopped': True,
        'writers': {
            'github': {'protocol': PROTOCOL, 'sourceRevision': github_rev},
            'private': {'protocol': PROTOCOL, 'sourceRevision': private_rev},
        },
        'initial': {'snapshotHash': snapshot_hash, 'baseHash': base_hash},
        'at': at or hkt_now().strftime('%Y-%m-%d'),
    }
    errs = validate_transition(doc)
    if errs:
        raise AnchorError('ack-invalid:' + ','.join(errs))
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode('utf-8')


class FileAnchorStore:
    """Offline fixture：expected-hash CAS；history 亦 immutable。"""

    def __init__(self, path, transition_path=None):
        self.path = path
        self.transition_path = transition_path or (path + '.transition')

    def _read(self, path):
        if not os.path.exists(path):
            return None, None
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            raise AnchorError(f'anchor-unreadable:{type(e).__name__}')
        return data, 'sha256:' + hashlib.sha256(data).hexdigest()

    def read_raw(self):
        return self._read(self.path)

    def read_transition_raw(self):
        return self._read(self.transition_path)

    def claim_raw(self, data, sha):
        lock = self.path + '.lock'
        for _ in range(50):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.02)
        else:
            raise AnchorError('anchor-lock-busy')
        try:
            current, current_sha = self._read(self.path)
            if current is not None and sha is not None and current_sha != sha:
                raise AnchorConflict('anchor-cas-mismatch')
            if current is None and sha is not None:
                raise AnchorConflict('anchor-missing-but-sha-given')
            if current is not None and sha is None:
                raise AnchorConflict('anchor-exists')
            tmp = self.path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return 'sha256:' + hashlib.sha256(data).hexdigest()
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass

    def _history_path(self, month):
        return self.path + f'.history-{month}.json'

    def read_history(self, month):
        return self._read(self._history_path(month))[0]

    def write_history(self, month, data):
        p = self._history_path(month)
        existing, _sha = self._read(p)
        if existing is not None:
            if _history_conflict(existing, data):
                raise AnchorError('history-immutable-conflict')
            return
        with open(p, 'wb') as f:
            f.write(data)

    def read_journal(self, repo_root):
        return _read_journal(repo_root)

    def write_journal(self, repo_root, entries):
        _write_journal(repo_root, entries)

    def clear_journal(self, repo_root):
        _clear_journal(repo_root)


class CoordinatorAnchorStore:
    def __init__(self, contents, path=ANCHOR_PATH, transition_path=TRANSITION_PATH):
        self.contents = contents
        self.path = path
        self.transition_path = transition_path

    def read_raw(self):
        try:
            return self.contents.get_file(self.path)
        except Exception as e:  # noqa: BLE001
            raise AnchorError(f'anchor-read-failed:{type(e).__name__}')

    def read_transition_raw(self):
        try:
            return self.contents.get_file(self.transition_path)
        except Exception as e:  # noqa: BLE001
            raise AnchorError(f'transition-read-failed:{type(e).__name__}')

    def claim_raw(self, data, sha):
        import biggo_coordinator as coord
        try:
            return self.contents.put_file(self.path, data,
                                          'coordinator: monthly cycle anchor', sha=sha)
        except coord.CASConflict as e:
            raise AnchorConflict(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise AnchorError(f'anchor-write-failed:{type(e).__name__}')

    def read_history(self, month):
        data, _sha = self.contents.get_file(HISTORY_PATH.format(month=month))
        return data

    def write_history(self, month, data):
        path = HISTORY_PATH.format(month=month)
        existing, _sha = self.contents.get_file(path)
        if existing is not None:
            if _history_conflict(existing, data):
                raise AnchorError('history-immutable-conflict')
            return
        self.contents.put_file(path, data, 'coordinator: monthly cycle history')

    def read_journal(self, repo_root):
        return _read_journal(repo_root)

    def write_journal(self, repo_root, entries):
        _write_journal(repo_root, entries)

    def clear_journal(self, repo_root):
        _clear_journal(repo_root)

    @classmethod
    def from_env(cls, env=None, contents=None):
        import biggo_coordinator as coord
        env = env if env is not None else os.environ
        if contents is None:
            status = coord.config_status(env)
            if not status['configured']:
                raise AnchorError(f'coordinator-not-configured:{status["reason"]}')
            contents = coord.GitHubContentsClient(coord.CoordinatorConfig.from_env(env))
        return cls(contents)


def _canonical_bytes(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode('utf-8')


def _history_conflict(existing, data):
    """同月 history 只可寫一次；same-facts（updatedAt 等 volatile 欄除外）視為同一歷史。"""
    if existing == data:
        return False
    try:
        old = _strict_json(existing.decode('utf-8'))
        new = _strict_json(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return True
    if isinstance(old, dict) and isinstance(new, dict):
        old = {k: v for k, v in old.items() if k != 'updatedAt'}
        new = {k: v for k, v in new.items() if k != 'updatedAt'}
        return old != new
    return True


def _read_json_raw(raw, label):
    if raw is None:
        return None
    try:
        return _strict_json(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as e:
        raise AnchorError(f'{label}-corrupt:{type(e).__name__}')


def _read_store(store):
    if store is None:
        return None, None, None
    anchor_raw, anchor_sha = store.read_raw()
    transition_raw, _t_sha = store.read_transition_raw()
    return (_read_json_raw(anchor_raw, 'anchor'), anchor_sha,
            _read_json_raw(transition_raw, 'transition'))


def _load_meta_strict(meta_path):
    try:
        with open(meta_path, encoding='utf-8') as f:
            raw = f.read()
    except OSError as e:
        raise AnchorError(f'meta-unreadable:{type(e).__name__}')
    meta = _strict_json(raw)
    errs = validate_meta(meta)
    if errs:
        raise AnchorError('meta-invalid:' + ','.join(errs))
    return meta


def _file_bytes_hash(path):
    try:
        with open(path, 'rb') as f:
            return 'sha256:' + hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        raise AnchorError(f'file-unreadable:{type(e).__name__}')


def _repo_hashes(root):
    return {name: _file_bytes_hash(os.path.join(root, name)) for name in REPO_FILES}


def _read_journal(root):
    p = os.path.join(root, JOURNAL_NAME)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return _strict_json(f.read())


def _write_journal(root, entries):
    p = os.path.join(root, JOURNAL_NAME)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(entries, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _clear_journal(root):
    try:
        os.remove(os.path.join(root, JOURNAL_NAME))
    except OSError:
        pass


def _canonical_file_hash(path, name):
    import biggo_canonical as _canon
    with open(path, encoding='utf-8') as f:
        obj = json.load(f)
    if name == 'model_blacklist.json':
        obj = (obj or {}).get('models') or {}
    return _canon.sha256_json(obj)


def _bundle_posts(bundle):
    return {
        'biggo_prices.json': bundle['newSnapshotHash'],
        'model_status.json': bundle['stageResult']['postState']['trackingHash'],
        'model_blacklist.json': bundle['stageResult']['postState']['blacklistHash'],
    }


def _bundle_pres(bundle):
    return {
        'biggo_prices.json': bundle['baseSnapshotHash'],
        'model_status.json': bundle['stageResult']['preState']['trackingHash'],
        'model_blacklist.json': bundle['stageResult']['preState']['blacklistHash'],
    }


def _bundle_run_date(bundle):
    """由已驗 bundle 推導實際 run 日期（UTC generatedAt→HKT；否則 cycle 日期）。"""
    sr = bundle.get('stageResult') or {}
    manifest = bundle.get('manifest') or {}
    gen = str(sr.get('generatedAt') or '')
    try:
        dt = datetime.datetime.strptime(gen, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=datetime.timezone.utc)
        return dt.astimezone(HKT).strftime('%Y-%m-%d')
    except ValueError:
        cycle = str(manifest.get('cycleId') or '')
        date = cycle.split(':', 1)[0] if ':' in cycle else ''
        return date if valid_date(date) else None


def inspect_journal(repo_root, expect=None):
    """只讀檢查 journal：全預檢（含第四檔 staged metadata 真事實），無任何 live 寫。

    expect 必須來自已驗 authority context：cycle／bundleHash／sourceRevision／posts
    以及 target bundle（bundle）、cycle start（start）、stage（stage）。缺 context
    （UNKNOWN）一律 fail-closed，唔可以略過綁定。
    """
    try:
        journal = _read_journal(repo_root)
    except ValueError:
        return {'ok': False, 'reason': 'journal-corrupt'}
    if not journal:
        return {'ok': True, 'reason': 'no-journal', 'files': []}
    root = os.path.abspath(repo_root)
    if journal.get('version') != 3:
        return {'ok': False, 'reason': 'journal-unsupported-version'}
    cyc = re.fullmatch(r'(\d{4}-\d{2}-\d{2}):(\d)/7', str(journal.get('cycle') or ''))
    if not cyc or not valid_date(cyc.group(1)) or not (1 <= int(cyc.group(2)) <= 7):
        return {'ok': False, 'reason': 'journal-cycle-invalid'}
    if int(journal.get('stage') or 0) != int(cyc.group(2)):
        return {'ok': False, 'reason': 'journal-stage-cycle-mismatch'}
    if not isinstance(journal.get('bundleHash'), str) or not SHA256_RE.fullmatch(journal['bundleHash']):
        return {'ok': False, 'reason': 'journal-bundlehash-invalid'}
    if not isinstance(journal.get('sourceRevision'), str) \
            or not SOURCE_REV_RE.fullmatch(journal['sourceRevision']):
        return {'ok': False, 'reason': 'journal-sourcerevision-invalid'}
    if not expect:
        return {'ok': False, 'reason': 'journal-authority-required'}
    start = expect.get('start')
    jidx = expect.get('stage')
    bundle = expect.get('bundle')
    approved_source = expect.get('sourceRevision')
    if not valid_date(start) or isinstance(jidx, bool) or not isinstance(jidx, int) \
            or jidx != int(cyc.group(2)):
        return {'ok': False, 'reason': 'journal-authority-required'}
    if not isinstance(approved_source, str) or not SOURCE_REV_RE.fullmatch(approved_source):
        return {'ok': False, 'reason': 'journal-authority-required'}
    if not isinstance(bundle, dict) or not isinstance(bundle.get('bundleHash'), str) \
            or not SHA256_RE.fullmatch(bundle['bundleHash']) \
            or bundle.get('bundleHash') != journal.get('bundleHash'):
        return {'ok': False, 'reason': 'journal-bundle-binding-mismatch'}
    if expect.get('cycle') is not None and journal['cycle'] != expect.get('cycle'):
        return {'ok': False, 'reason': 'journal-cycle-binding-mismatch'}
    if journal['cycle'] != _anchor_cycle(start, jidx):
        return {'ok': False, 'reason': 'journal-cycle-binding-mismatch'}
    if journal['bundleHash'] != expect.get('bundleHash'):
        return {'ok': False, 'reason': 'journal-bundle-binding-mismatch'}
    if journal['sourceRevision'] != approved_source:
        return {'ok': False, 'reason': 'journal-source-binding-mismatch'}
    mt = journal.get('metaTarget')
    mt_idx = mt.get('price_batch_idx') if isinstance(mt, dict) else None
    if not isinstance(mt, dict) or mt.get('appliedBundleHash') != journal['bundleHash'] \
            or isinstance(mt_idx, bool) or not isinstance(mt_idx, int) or mt_idx != jidx:
        return {'ok': False, 'reason': 'journal-metatarget-mismatch'}
    scratch_root = journal.get('scratchRoot')
    parent = os.path.realpath(os.path.dirname(root))
    if not isinstance(scratch_root, str) or not os.path.isabs(scratch_root):
        return {'ok': False, 'reason': 'journal-scratch-root-invalid'}
    real_scratch = os.path.realpath(scratch_root)
    if os.path.dirname(real_scratch) != parent \
            or not os.path.basename(real_scratch).startswith('price-reconcile-'):
        return {'ok': False, 'reason': 'journal-scratch-root-outside'}
    files = journal.get('files')
    if not isinstance(files, list) or len(files) != len(REPO_FILES):
        return {'ok': False, 'reason': 'journal-file-set-invalid'}
    by_name = {}
    for entry in files:
        if not isinstance(entry, dict) or entry.get('file') not in REPO_FILES:
            return {'ok': False, 'reason': 'journal-file-invalid'}
        if entry['file'] in by_name:
            return {'ok': False, 'reason': 'journal-file-duplicate'}
        by_name[entry['file']] = entry
    if set(by_name) != set(REPO_FILES):
        return {'ok': False, 'reason': 'journal-file-set-invalid'}
    for name, entry in by_name.items():
        src, dest = entry.get('staged'), entry.get('dest')
        if not isinstance(src, str) or not isinstance(dest, str):
            return {'ok': False, 'reason': 'journal-path-invalid'}
        if os.path.abspath(dest) != os.path.join(root, name):
            return {'ok': False, 'reason': 'journal-dest-outside-root'}
        if os.path.islink(dest):
            return {'ok': False, 'reason': 'journal-dest-symlink'}
        if os.path.realpath(src) != os.path.join(real_scratch, name):
            return {'ok': False, 'reason': 'journal-staged-outside-scratch'}
        if entry.get('sha256') != entry.get('postHash') or \
                not isinstance(entry.get('postHash'), str) or not SHA256_RE.fullmatch(entry['postHash']):
            return {'ok': False, 'reason': 'journal-post-hash-mismatch'}
        current = _file_bytes_hash(dest) if os.path.exists(dest) else None
        if current not in (entry.get('preHash'), entry.get('postHash')):
            return {'ok': False, 'reason': 'journal-dest-image-mismatch'}
        if current != entry.get('postHash'):
            if not os.path.exists(src) or _file_bytes_hash(src) != entry['sha256']:
                return {'ok': False, 'reason': 'journal-staged-invalid'}
        posts = expect.get('posts') or {}
        if name in posts:
            probe = dest if (os.path.exists(dest)
                             and _file_bytes_hash(dest) == entry.get('postHash')) else src
            try:
                if _canonical_file_hash(probe, name) != posts[name]:
                    return {'ok': False, 'reason': 'journal-post-material-mismatch'}
            except (OSError, ValueError):
                return {'ok': False, 'reason': 'journal-post-material-invalid'}
    # ---- 第四檔（metadata）真預檢：schema＋bundle／cycle／start／batch 真事實 ----
    meta_entry = by_name['prices_meta.json']
    meta_probe = None
    if os.path.exists(meta_entry['dest']) \
            and _file_bytes_hash(meta_entry['dest']) == meta_entry.get('postHash'):
        meta_probe = meta_entry['dest']
    elif isinstance(meta_entry.get('staged'), str) and os.path.exists(meta_entry['staged']):
        meta_probe = meta_entry['staged']
    if meta_probe is None:
        return {'ok': False, 'reason': 'journal-staged-meta-unreadable'}
    try:
        with open(meta_probe, 'rb') as f:
            meta_raw = f.read()
    except OSError:
        return {'ok': False, 'reason': 'journal-staged-meta-unreadable'}
    try:
        staged_meta = _strict_json(meta_raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {'ok': False, 'reason': 'journal-staged-meta-invalid'}
    try:
        batch_utils._validate_meta(staged_meta, 'prices_meta')
    except batch_utils.MetaError:
        return {'ok': False, 'reason': 'journal-staged-meta-schema'}
    run_date = _bundle_run_date(bundle)
    if run_date is None:
        return {'ok': False, 'reason': 'journal-staged-meta-unverifiable'}
    if staged_meta.get('price_batch_idx') != jidx:
        return {'ok': False, 'reason': 'journal-staged-meta-index-mismatch'}
    if jidx == PRICE_BATCH_DAYS:
        if 'price_batch_start' in staged_meta:
            return {'ok': False, 'reason': 'journal-staged-meta-start-present'}
        if staged_meta.get('last_full') != run_date:
            return {'ok': False, 'reason': 'journal-staged-meta-lastfull-mismatch'}
    else:
        if str(staged_meta.get('price_batch_start') or '') != start:
            return {'ok': False, 'reason': 'journal-staged-meta-start-mismatch'}
    if staged_meta.get('last_applied_cycle') != _anchor_cycle(start, jidx):
        return {'ok': False, 'reason': 'journal-staged-meta-cycle-mismatch'}
    if staged_meta.get('appliedBundleHash') != bundle['bundleHash']:
        return {'ok': False, 'reason': 'journal-staged-meta-bundle-mismatch'}
    if staged_meta.get('appliedSnapshotHash') != bundle.get('newSnapshotHash'):
        return {'ok': False, 'reason': 'journal-staged-meta-snapshot-mismatch'}
    if str(staged_meta.get('last_price_month') or '') != start[:7]:
        return {'ok': False, 'reason': 'journal-staged-meta-month-mismatch'}
    if staged_meta.get('last_run') != run_date:
        return {'ok': False, 'reason': 'journal-staged-meta-lastrun-mismatch'}
    if staged_meta.get('last_batch_status') != 'completed' \
            or staged_meta.get('last_batch_idx') != jidx - 1 \
            or staged_meta.get('last_batch_net_errors') != 0:
        return {'ok': False, 'reason': 'journal-staged-meta-batch-mismatch'}
    if staged_meta.get('appliedAt') is not None and not valid_applied_at(staged_meta.get('appliedAt')):
        return {'ok': False, 'reason': 'journal-staged-meta-appliedat-invalid'}
    if mt.get('appliedBundleHash') != staged_meta.get('appliedBundleHash') \
            or mt_idx != staged_meta.get('price_batch_idx'):
        return {'ok': False, 'reason': 'journal-metatarget-mismatch'}
    return {'ok': True, 'reason': 'valid', 'files': by_name}


def _journal_expect(journal, bundles, start, actual_source):
    """由已驗 chain 建 journal authority expect；任何 UNKNOWN 都 raise。"""
    if not actual_source or not SOURCE_REV_RE.fullmatch(str(actual_source)):
        raise AnchorError('journal-invalid:journal-authority-required')
    jstage = journal.get('stage')
    if isinstance(jstage, bool) or not isinstance(jstage, int) \
            or jstage < 1 or jstage > len(bundles):
        raise AnchorError('journal-invalid:journal-target-outside-chain')
    jbundle = bundles[jstage - 1]
    expect = {'cycle': _anchor_cycle(start, jstage),
              'bundleHash': jbundle['bundleHash'],
              'sourceRevision': actual_source,
              'posts': _bundle_posts(jbundle),
              'bundle': jbundle, 'start': start, 'stage': jstage}
    return jstage, jbundle, expect


def inspect_pending_journal(anchor_doc, transition_doc, store, client, repo_root,
                            writer_role=None, actual_source=None):
    """Shared 入口只讀 pending journal 檢查（零寫、零 provider）。

    回 {'pending': bool, ...}；journal 存在但任何權威／事實不明確 → raise AnchorError。
    """
    if repo_root is None:
        return {'pending': False}
    try:
        journal = _read_journal(repo_root)
    except ValueError:
        raise AnchorError('journal-invalid:journal-corrupt')
    if journal is None:
        return {'pending': False}
    if not actual_source or not SOURCE_REV_RE.fullmatch(str(actual_source)):
        raise AnchorError('journal-invalid:journal-authority-required')
    if anchor_doc is None:
        raise AnchorError('journal-invalid:journal-anchor-required')
    a_errs = validate_anchor(anchor_doc)
    if a_errs:
        raise AnchorError('journal-invalid:journal-anchor-invalid')
    if client is None:
        raise AnchorError('journal-invalid:journal-remote-required')
    state = client.read_remote_state()
    if state is None:
        raise AnchorError('journal-invalid:journal-remote-missing')
    if state.get('status') != 'completed':
        raise AnchorError(
            f'journal-invalid:journal-remote-not-completed:{state.get("status")}')
    start = anchor_doc['startedAt']
    if not str(state.get('cycleId') or '').startswith(start + ':'):
        raise AnchorError('journal-invalid:journal-remote-root-mismatch')
    target_stage = int(state.get('stage') or 0)
    bundles = _stage_chain(client, start, target_stage, transition_doc, anchor_doc, state,
                           base_override=anchor_doc.get('cycleBaseHash'))
    jstage, jbundle, expect = _journal_expect(journal, bundles, start, actual_source)
    plan = inspect_journal(repo_root, expect=expect)
    if not plan.get('ok'):
        raise AnchorError('journal-invalid:' + str(plan.get('reason')))
    all_post = True
    for name, entry in plan['files'].items():
        dest = os.path.join(repo_root, name)
        if not os.path.exists(dest) or _file_bytes_hash(dest) != entry.get('postHash'):
            all_post = False
            break
    return {'pending': True, 'stage': jstage, 'targetStage': target_stage,
            'cycle': _anchor_cycle(start, jstage), 'bundleHash': jbundle['bundleHash'],
            'allPost': all_post}


def recover_journal(repo_root, expect=None):
    """先 inspect（零寫）；通過後才 replace，metadata 最後。"""
    plan = inspect_journal(repo_root, expect=expect)
    if not plan.get('ok'):
        raise AnchorError(f"journal-invalid:{plan.get('reason')}")
    if plan.get('reason') == 'no-journal':
        return {'recovered': False}
    by_name = plan['files']
    ordered = [n for n in REPO_FILES if n != 'prices_meta.json'] + ['prices_meta.json']
    try:
        for name in ordered:
            entry = by_name[name]
            if os.path.exists(entry['dest']) and _file_bytes_hash(entry['dest']) == entry['postHash']:
                continue
            os.replace(entry['staged'], entry['dest'])
            if _file_bytes_hash(entry['dest']) != entry['postHash']:
                raise AnchorError('journal-post-hash-mismatch')
    except OSError as e:
        raise AnchorError(f'journal-io-failure:{type(e).__name__}') from e
    _clear_journal(repo_root)
    return {'recovered': True}


def _archive_month(store, anchor_doc, snapshot_hash=None):
    if store is None:
        return
    doc = dict(anchor_doc)
    doc['historyVersion'] = 1
    if snapshot_hash:
        doc['finalSnapshotHash'] = snapshot_hash
    store.write_history(anchor_doc['month'], _canonical_bytes(doc))


def plan(meta, anchor_doc=None, transition_doc=None, now=None, *,
         actual_source=None, initial_base=None, writer_role=None):
    """Authority-first；同 start 自然進度唔會被當新 cycle 衝突。"""
    month = cycle_month(now)
    if writer_role not in (None, 'github', 'private'):
        return {'eligible': False, 'reason': 'writer-role-invalid', 'month': month}
    if not actual_source or not SOURCE_REV_RE.fullmatch(str(actual_source)):
        return {'eligible': False, 'reason': 'source-unavailable', 'month': month}
    errs = validate_meta(meta)
    if errs:
        return {'eligible': False, 'reason': 'meta-invalid', 'errors': errs, 'month': month}
    # anchor 存在時，initial baseline 以 anchor.initialBaseHash 為權威（進度後本地價已變）
    if anchor_doc is not None and anchor_doc.get('initialBaseHash'):
        initial_base = anchor_doc['initialBaseHash']
    t_errs = validate_transition(transition_doc, actual_source=actual_source,
                                 initial_base=initial_base, writer_role=writer_role)
    if t_errs:
        return {'eligible': False, 'reason': 'transition-not-acknowledged',
                'errors': t_errs, 'month': month}
    if anchor_doc is not None:
        a_errs = validate_anchor(anchor_doc)
        if a_errs:
            return {'eligible': False, 'reason': 'anchor-corrupt', 'errors': a_errs,
                    'month': month}
        am = anchor_doc['month']
        local_start = str(meta.get('price_batch_start') or '')
        local_idx = meta.get('price_batch_idx', 0)
        if am == month:
            if anchor_doc['status'] == 'needs-review':
                return {'eligible': False, 'reason': 'anchor-needs-review',
                        'cycle': anchor_doc['cycle'], 'month': month}
            if local_start and local_start != anchor_doc['startedAt']:
                return {'eligible': False, 'reason': 'local-authority-conflict',
                        'localStart': local_start, 'anchorStart': anchor_doc['startedAt'],
                        'month': month}
            if local_start and local_idx > anchor_doc['stage']:
                return {'eligible': False, 'reason': 'local-ahead-of-anchor',
                        'localIdx': local_idx, 'anchorStage': anchor_doc['stage'],
                        'month': month}
            if anchor_doc['status'] == 'completed' and anchor_doc['stage'] == PRICE_BATCH_DAYS \
                    and local_start == anchor_doc['startedAt'] and local_idx >= PRICE_BATCH_DAYS:
                return {'eligible': False, 'reason': 'month-completed',
                        'cycle': anchor_doc['cycle'], 'month': month}
            return {'eligible': False, 'reason': 'already-anchored',
                    'cycle': anchor_doc['cycle'], 'stage': anchor_doc['stage'],
                    'startedAt': anchor_doc['startedAt'], 'month': month}
        if am > month:
            return {'eligible': False, 'reason': 'anchor-ahead', 'month': month}
        # anchor 舊月：當月 local 若有 start 而 root 不同 → 衝突
        if local_start and local_start[:7] == month and local_start != anchor_doc['startedAt']:
            return {'eligible': False, 'reason': 'local-authority-conflict',
                    'localStart': local_start, 'anchorStart': anchor_doc['startedAt'],
                    'month': month}
        if anchor_doc['status'] != 'completed' or anchor_doc['stage'] < PRICE_BATCH_DAYS:
            return {'eligible': False, 'reason': 'previous-cycle-incomplete',
                    'cycle': anchor_doc['cycle'], 'month': month}
        return {'eligible': True, 'reason': 'monthly-due', 'month': month,
                'cycle': _anchor_cycle(hkt_now(now).strftime('%Y-%m-%d'), 0)}
    start = str(meta.get('price_batch_start') or '')
    idx = meta.get('price_batch_idx', 0)
    if start and idx < PRICE_BATCH_DAYS:
        if start[:7] != month:
            return {'eligible': False, 'reason': 'legacy-active-pending',
                    'cycle': cycle_identity(meta), 'month': month}
        return {'eligible': False, 'reason': 'active-batch',
                'cycle': cycle_identity(meta), 'stage': idx, 'month': month}
    if str(meta.get('last_price_month') or '') == month:
        return {'eligible': False, 'reason': 'month-completed', 'month': month}
    return {'eligible': True, 'reason': 'monthly-due', 'month': month,
            'cycle': _anchor_cycle(hkt_now(now).strftime('%Y-%m-%d'), 0)}


def _stage_chain(client, start, target_stage, transition_doc, anchor_doc, state,
                 base_override=None):
    """讀取並驗證 1..target_stage 完整連續 chain；terminal 與 state/anchor 綁定。"""
    if not valid_date(start):
        raise AnchorError('start-invalid')
    initial_base = base_override or anchor_doc.get('cycleBaseHash') or \
        (transition_doc or {}).get('initial', {}).get('baseHash')
    bundles = []
    for stage in range(1, target_stage + 1):
        cycle = _anchor_cycle(start, stage)
        bundle = client.import_historic_bundle(cycle, stage)
        if stage == 1:
            if initial_base and bundle['baseSnapshotHash'] != initial_base:
                raise AnchorError('chain-initial-base-mismatch')
        else:
            prev = bundles[-1]
            if bundle['baseSnapshotHash'] != prev['newSnapshotHash']:
                raise AnchorError(f'chain-base-mismatch-stage{stage}')
            prev_sr = prev['stageResult']
            sr = bundle['stageResult']
            if sr['preState']['trackingHash'] != prev_sr['postState']['trackingHash']:
                raise AnchorError(f'chain-tracking-mismatch-stage{stage}')
            if sr['preState']['blacklistHash'] != prev_sr['postState']['blacklistHash']:
                raise AnchorError(f'chain-blacklist-mismatch-stage{stage}')
        bundles.append(bundle)
    final = bundles[-1] if bundles else None
    if final is not None:
        if state.get('snapshotHash') and final['newSnapshotHash'] != state['snapshotHash']:
            raise AnchorError('terminal-state-snapshot-mismatch')
        if state.get('bundleHash') and final['bundleHash'] != state['bundleHash']:
            raise AnchorError('terminal-state-bundle-mismatch')
        if state.get('snapshotPath') and final.get('snapshotPath') \
                and state['snapshotPath'] != final['snapshotPath']:
            raise AnchorError('terminal-state-path-mismatch')
        if state.get('stageResultHash') and final.get('stageResultHash') \
                and state['stageResultHash'] != final['stageResultHash']:
            raise AnchorError('terminal-state-stageresult-mismatch')
        if anchor_doc['stage'] == target_stage and anchor_doc.get('lastBundleHash') \
                and anchor_doc['lastBundleHash'] != final['bundleHash']:
            raise AnchorError('anchor-lastBundleHash-mismatch')
    return bundles


def _repo_snapshot_hash(root):
    try:
        with open(os.path.join(root, 'biggo_prices.json'), encoding='utf-8') as f:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import biggo_canonical as _canon
            return _canon.sha256_json(json.load(f))
    except (OSError, ValueError) as e:
        raise AnchorError(f'repo-snapshot-unreadable:{type(e).__name__}')


def _verify_local_material(meta, anchor_doc, client, repo_root, transition_doc=None):
    """本地四檔與已驗 last-completed bundle／baseline 一致嗎（provider 前 guard）。"""
    def reason(msg):
        return msg
    try:
        if anchor_doc['stage'] == 0:
            base = anchor_doc.get('cycleBaseHash') or \
                ((transition_doc or {}).get('initial') or {}).get('baseHash')
            if not base:
                return 'baseline-unknown'
            if _repo_snapshot_hash(repo_root) != base:
                return 'baseline-price-mismatch'
            local_start = str(meta.get('price_batch_start') or '')
            if local_start and local_start != anchor_doc['startedAt']:
                return 'baseline-start-mismatch'
            return None
        if client is None:
            return 'material-check-unavailable'
        bundle = client.import_historic_bundle(anchor_doc['cycle'], anchor_doc['stage'])
        if bundle['bundleHash'] != anchor_doc.get('lastBundleHash'):
            return 'lastbundle-mismatch'
        if _repo_snapshot_hash(repo_root) != bundle['newSnapshotHash']:
            return 'price-mismatch'
        import biggo_canonical as _canon
        with open(os.path.join(repo_root, 'model_status.json'), encoding='utf-8') as f:
            tracking = json.load(f)
        if _canon.sha256_json(tracking) != bundle['stageResult']['postState']['trackingHash']:
            return 'tracking-mismatch'
        with open(os.path.join(repo_root, 'model_blacklist.json'), encoding='utf-8') as f:
            black = (json.load(f) or {}).get('models') or {}
        if _canon.sha256_json(black) != bundle['stageResult']['postState']['blacklistHash']:
            return 'blacklist-mismatch'
        if meta.get('appliedBundleHash') != bundle['bundleHash']:
            return 'meta-bundle-mismatch'
        if int(meta.get('price_batch_idx', 0) or 0) != anchor_doc['stage']:
            return 'meta-idx-mismatch'
        return None
    except Exception as e:  # noqa: BLE001
        return f'material-error:{type(e).__name__}'


def _hash_pairs(bundle):
    """每檔 (pre, post) hash；snapshot/tracking/blacklist 各自獨立恢復窗口。"""
    return {
        'biggo_prices.json': (bundle['baseSnapshotHash'], bundle['newSnapshotHash']),
        'model_status.json': (bundle['stageResult']['preState']['trackingHash'],
                              bundle['stageResult']['postState']['trackingHash']),
        'model_blacklist.json': (bundle['stageResult']['preState']['blacklistHash'],
                                 bundle['stageResult']['postState']['blacklistHash']),
    }


def _local_stage(bundles, meta, repo_root):
    """回 (start_index, reason)；start_index 0-based 下一個要套用嘅 bundle。"""
    idx = int(meta.get('price_batch_idx', 0) or 0)
    if idx and 1 <= idx <= len(bundles) and \
            meta.get('appliedBundleHash') == bundles[idx - 1]['bundleHash']:
        return idx, None
    import biggo_canonical as _canon

    def _file_hash(name):
        if name == 'biggo_prices.json':
            with open(os.path.join(repo_root, name), encoding='utf-8') as f:
                return _canon.sha256_json(json.load(f))
        if name == 'model_status.json':
            with open(os.path.join(repo_root, name), encoding='utf-8') as f:
                return _canon.sha256_json(json.load(f))
        with open(os.path.join(repo_root, name), encoding='utf-8') as f:
            return _canon.sha256_json((json.load(f) or {}).get('models') or {})

    current = {n: _file_hash(n) for n in ('biggo_prices.json', 'model_status.json',
                                          'model_blacklist.json')}
    for i, bundle in enumerate(bundles, start=1):
        pairs = _hash_pairs(bundle)
        if all(current[n] in pairs[n] for n in current):
            return i - 1, None
    if all(current[n] == _hash_pairs(bundles[-1])[n][1] for n in current):
        return len(bundles), None
    return None, 'local-files-not-in-chain'


def _sync_anchor(store, anchor_doc, bundles):
    """anchor 逐步追上已驗 chain（每個 stage 綁 bundleHash）。"""
    for idx, bundle in enumerate(bundles, start=1):
        if idx <= anchor_doc['stage']:
            continue
        status = 'completed' if idx == PRICE_BATCH_DAYS else 'active'
        advance_stage(store, anchor_doc['month'], idx, bundle['bundleHash'], status=status,
                      snapshot_hash=bundle['newSnapshotHash'])


def reconcile_shared(meta, anchor_doc, transition_doc, store, client, repo_root,
                     apply_fn=None, now=None, meta_path=None, dry_run=False,
                     writer_role=None, actual_source=None):
    """先驗完整 chain＋scratch 準備，成功先 journal 提交四個 live 檔。"""
    import biggo_coordinator as coord  # noqa: F401
    if apply_fn is None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import biggo_apply as _apply
        apply_fn = _apply.apply_bundle
    if writer_role not in (None, 'github', 'private'):
        raise AnchorError('writer-role-invalid')
    if not actual_source or not SOURCE_REV_RE.fullmatch(str(actual_source)):
        raise AnchorError('source-unavailable')
    a_errs = validate_anchor(anchor_doc)
    if a_errs:
        raise AnchorError('anchor-invalid:' + ','.join(a_errs))
    state = client.read_remote_state()
    if state is None:
        raise AnchorError('remote-state-missing')
    if state.get('status') != 'completed':
        raise AnchorError(f'remote-not-completed:{state.get("status")}')
    start = anchor_doc['startedAt']
    if not str(state.get('cycleId') or '').startswith(start + ':'):
        raise AnchorError(f'remote-root-mismatch:{state.get("cycleId")}')
    if int(state.get('stage') or 0) < anchor_doc['stage']:
        raise AnchorError('remote-stage-behind-anchor')
    target_stage = int(state.get('stage') or 0)
    bundles = _stage_chain(client, start, target_stage, transition_doc, anchor_doc, state,
                           base_override=anchor_doc.get('cycleBaseHash'))
    try:
        journal = _read_journal(repo_root)
    except ValueError:
        raise AnchorError('journal-invalid:journal-corrupt')
    recovered = False
    if journal is not None:
        jstage, jbundle, jexpect = _journal_expect(journal, bundles, start, actual_source)
        jplan = inspect_journal(repo_root, expect=jexpect)
        if not jplan.get('ok'):
            raise AnchorError(f"journal-invalid:{jplan.get('reason')}")
        if dry_run:
            return {'state': state, 'applied': [], 'dryRun': True, 'journalPlanned': True,
                    'journalStage': jstage,
                    'cycle': _anchor_cycle(start, target_stage), 'plannedFrom': None}
        recover_journal(repo_root, expect=jexpect)
        recovered = True
        # 恢復後必須用真正落盤嘅 metadata／materials 重新定位，唔可以再用呼叫時舊 meta。
        meta = _load_meta_strict(
            meta_path or os.path.join(repo_root, 'prices_meta.json'))
    # 依本地 material hash＋已驗 chain 定位已套用進度（唔靠 meta 計數）
    start_idx, local_reason = _local_stage(bundles, meta, repo_root)
    if start_idx is None:
        raise AnchorError(local_reason or 'local-files-not-in-chain')
    if recovered and start_idx == len(bundles):
        # 已證實完成此 root／bundle：recovery 已落全部 post image；
        # 唔可以當新 root 重置，唔重寫已完成四檔，只同步落後 anchor。
        _sync_anchor(store, anchor_doc, bundles)
        return {'state': state, 'applied': [],
                'cycle': _anchor_cycle(start, target_stage),
                'meta': meta, 'recovered': True}
    local_idx = start_idx
    to_apply = bundles[start_idx:]
    local_hash = _repo_snapshot_hash(repo_root)
    def _tracking_hash():
        with open(os.path.join(repo_root, 'model_status.json'), encoding='utf-8') as f:
            import biggo_canonical as _c
            return _c.sha256_json(json.load(f))

    def _blacklist_hash():
        with open(os.path.join(repo_root, 'model_blacklist.json'), encoding='utf-8') as f:
            import biggo_canonical as _c
            return _c.sha256_json((json.load(f) or {}).get('models') or {})

    def _material_matches(bundle):
        return (bundle['newSnapshotHash'] == local_hash
                and bundle['stageResult']['postState']['trackingHash'] == _tracking_hash()
                and bundle['stageResult']['postState']['blacklistHash'] == _blacklist_hash())



    # ---- scratch 準備（live 零改動）----
    import shutil
    import tempfile
    scratch = tempfile.mkdtemp(prefix='price-reconcile-', dir=os.path.dirname(repo_root))
    try:
        for name in REPO_FILES:
            src = os.path.join(repo_root, name)
            if not os.path.exists(src):
                raise AnchorError(f'missing-live-file:{name}')
            shutil.copy2(src, os.path.join(scratch, name))
        scratch_meta_path = os.path.join(scratch, 'prices_meta.json')
        scratch_meta = _load_meta_strict(scratch_meta_path)
        chain_hashes = {b['bundleHash'] for b in bundles}
        same_cycle_meta = (str(scratch_meta.get('last_price_month') or '') == start[:7]
                           and scratch_meta.get('appliedBundleHash') in chain_hashes)
        if str(scratch_meta.get('price_batch_start') or '') != start \
                and not same_cycle_meta:
            scratch_meta['price_batch_start'] = start
            scratch_meta['price_batch_idx'] = 0  # 新 root：上月 idx 唔係本 cycle 進度
            with open(scratch_meta_path, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(scratch_meta, f, ensure_ascii=False)
        applied = []
        apply_now = now.timestamp() if hasattr(now, 'timestamp') else (now or time.time())
        if not to_apply and bundles:
            # local 已經係 target post image：只補 meta facts（apply noop 路徑）
            apply_fn(bundles[-1], repo_root=scratch, now=apply_now, applied_at=now)
        for offset, bundle in enumerate(to_apply, start=local_idx + 1):
            report = apply_fn(bundle, repo_root=scratch, now=apply_now,
                              applied_at=now)
            applied.append({'stage': offset, 'bundleHash': bundle['bundleHash'],
                            'snapshotHash': bundle['newSnapshotHash'],
                            'noop': bool(report.get('noop'))})
        scratch_meta = _load_meta_strict(scratch_meta_path)
        if bundles and scratch_meta.get('appliedBundleHash') != bundles[-1]['bundleHash']:
            raise AnchorError('scratch-meta-bundle-mismatch')
        if bundles and scratch_meta.get('appliedSnapshotHash') != bundles[-1]['newSnapshotHash']:
            raise AnchorError('scratch-meta-snapshot-mismatch')
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise

    if dry_run:
        shutil.rmtree(scratch, ignore_errors=True)
        return {'state': state, 'applied': [], 'dryRun': True,
                'cycle': _anchor_cycle(start, target_stage),
                'plannedFrom': start_idx}
    # ---- journal commit（可恢復；失敗保留 journal）----
    entries = []
    final_bundle = bundles[-1] if bundles else None
    for name in REPO_FILES:
        staged = os.path.join(scratch, name)
        dest = os.path.join(repo_root, name)
        post = _file_bytes_hash(staged)
        entries.append({'file': name, 'staged': staged, 'dest': dest,
                        'sha256': post, 'postHash': post,
                        'preHash': _file_bytes_hash(dest)})
    store.write_journal(repo_root, {
        'version': 3, 'cycle': _anchor_cycle(start, target_stage),
        'bundleHash': final_bundle['bundleHash'] if final_bundle else None,
        'sourceRevision': actual_source,
        'stage': target_stage, 'scratchRoot': scratch,
        'metaTarget': {'appliedBundleHash': final_bundle['bundleHash'] if final_bundle else None,
                       'price_batch_idx': target_stage},
        'files': entries})
    # metadata 最後；non-meta 先
    ordered = [e for e in entries if e['file'] != 'prices_meta.json'] \
        + [e for e in entries if e['file'] == 'prices_meta.json']
    try:
        for entry in ordered:
            os.replace(entry['staged'], entry['dest'])
            if _file_bytes_hash(entry['dest']) != entry['postHash']:
                raise AnchorError('commit-hash-mismatch')
    except OSError as e:
        raise AnchorError(f'commit-io-failure:{type(e).__name__}') from e
    store.clear_journal(repo_root)
    try:
        shutil.rmtree(scratch, ignore_errors=True)
    except OSError:
        pass

    # ---- anchor 逐步追上 remote（每個 stage 綁 bundleHash）----
    _sync_anchor(store, anchor_doc, bundles)
    return {'state': state, 'applied': applied,
            'cycle': _anchor_cycle(start, target_stage),
            'meta': scratch_meta}


def ensure_monthly_cycle(meta_path, now=None, allow_write=False, store=None,
                         client=None, repo_root=None, apply_fn=None, dry_run=False,
                         actual_source=None, initial_base=None, writer_role=None,
                         demand_doc=None, demand_path=None):
    month = cycle_month(now)
    try:
        meta = _load_meta_strict(meta_path)
    except AnchorError as e:
        return {'written': False, 'reason': 'blocked-meta', 'detail': str(e), 'month': month}
    if store is None and allow_write:
        return {'written': False, 'reason': 'shared-anchor-required', 'month': month}
    try:
        anchor_doc, anchor_sha, transition_doc = _read_store(store)
    except AnchorError as e:
        return {'written': False, 'reason': 'blocked-anchor', 'detail': str(e), 'month': month}
    state = plan(meta, anchor_doc, transition_doc, now,
                 actual_source=actual_source, initial_base=initial_base,
                 writer_role=writer_role)

    # New-model demand is a prerequisite for opening a *new* monthly cycle.
    # Existing active/completed identities and reconciliations are never hidden
    # by this gate: only the eligible->start transition is gated.
    if demand_doc is not None and state.get('reason') == 'monthly-due':
        import price_demand
        if not price_demand.pending(demand_doc):
            out = dict(state)
            out.update({'written': False, 'reason': 'no-demand',
                        'demandPath': demand_path, 'month': month})
            return out

    def _journal_noop(out):
        """任何 noop／month-completed 之前必先驗 pending journal（零寫）；不得略過。"""
        if anchor_doc is None:
            return out
        try:
            pending = inspect_pending_journal(
                anchor_doc, transition_doc, store, client, repo_root,
                writer_role=writer_role, actual_source=actual_source)
        except Exception as e:  # noqa: BLE001
            out.update({'reason': 'reconciliation-blocked',
                        'detail': f'{type(e).__name__}:{e}'[:200]})
            return out
        if not pending.get('pending'):
            return out
        out['journalPending'] = True
        if not allow_write or dry_run:
            return out
        if pending.get('allPost'):
            try:
                if store is not None:
                    store.clear_journal(repo_root)
                else:
                    _clear_journal(repo_root)
            except OSError as e:  # noqa: BLE001
                out.update({'reason': 'reconciliation-blocked',
                            'detail': f'journal-clear-failed:{type(e).__name__}'})
                return out
            out['journalCleared'] = True
            return out
        try:
            rec = reconcile_shared(meta, anchor_doc, transition_doc, store, client,
                                   repo_root, apply_fn=apply_fn, now=now,
                                   meta_path=meta_path, writer_role=writer_role,
                                   actual_source=actual_source)
        except Exception as e:  # noqa: BLE001
            out.update({'reason': 'reconciliation-blocked',
                        'detail': f'{type(e).__name__}:{e}'[:200]})
            return out
        on_disk = _load_meta_strict(meta_path)
        return {'written': True, 'reason': 'adopted', 'cycle': rec['cycle'],
                'stage': on_disk.get('price_batch_idx', 0),
                'applied': rec['applied'], 'month': anchor_doc['month'],
                'journalRecovered': bool(rec.get('recovered'))}

    if not state['eligible']:
        out = dict(state)
        out['written'] = False
        if state['reason'] == 'month-completed' and allow_write and anchor_doc:
            out['reason'] = 'noop'
            return _journal_noop(out)
        if state['reason'] in ('already-anchored', 'local-ahead-of-anchor') \
                and allow_write and anchor_doc:
            stage = anchor_doc['stage']
            local_start = str(meta.get('price_batch_start') or '')
            local_idx = meta.get('price_batch_idx', 0)
            if local_start == anchor_doc['startedAt'] and local_idx == stage:
                verify = _verify_local_material(meta, anchor_doc, client, repo_root,
                                                transition_doc) if repo_root else 'no-repo'
                if verify is None:
                    out['reason'] = 'noop'
                    return _journal_noop(out)
                if client is not None and repo_root:
                    try:
                        remote0 = client.read_remote_state()
                    except Exception:  # noqa: BLE001
                        remote0 = 'error'
                    if isinstance(remote0, dict) and remote0.get('status') == 'completed':
                        try:
                            rec = reconcile_shared(meta, anchor_doc, transition_doc, store,
                                                   client, repo_root, apply_fn=apply_fn,
                                                   now=now, meta_path=meta_path,
                                                   writer_role=writer_role,
                                                   actual_source=actual_source)
                        except Exception as e:  # noqa: BLE001
                            out.update({'reason': 'reconciliation-blocked',
                                        'detail': f'{type(e).__name__}:{e}'[:200]})
                            return out
                        on_disk = _load_meta_strict(meta_path)
                        return {'written': True, 'reason': 'adopted', 'cycle': rec['cycle'],
                                'stage': on_disk.get('price_batch_idx', 0),
                                'applied': rec['applied'], 'month': anchor_doc['month']}
                    if remote0 is None and anchor_doc['stage'] == 0:
                        base = anchor_doc.get('cycleBaseHash') or anchor_doc.get('initialBaseHash')
                        if base and _repo_snapshot_hash(repo_root) == base:
                            out['reason'] = 'noop'
                            return _journal_noop(out)
                out.update({'reason': 'material-blocked', 'detail': verify})
                return out
            if anchor_doc['stage'] == 0 and (local_idx == 0 or not local_start) \
                    and client is not None and repo_root is not None:
                # 第二 writer：第一 writer 只 claim 咗 root、尚未有 transaction。
                try:
                    remote0 = client.read_remote_state()
                except Exception:  # noqa: BLE001
                    remote0 = 'error'
                if remote0 is None:
                    try:
                        pend = inspect_pending_journal(
                            anchor_doc, transition_doc, store, client, repo_root,
                            writer_role=writer_role, actual_source=actual_source)
                    except Exception as e:  # noqa: BLE001
                        out.update({'reason': 'reconciliation-blocked',
                                    'detail': f'{type(e).__name__}:{e}'[:200]})
                        return out
                    if pend.get('pending'):
                        out.update({'reason': 'reconciliation-blocked',
                                    'detail': 'journal-pending-stage0'})
                        return out
                    base = anchor_doc.get('cycleBaseHash') or anchor_doc.get('initialBaseHash')
                    if base and _repo_snapshot_hash(repo_root) == base:
                        meta2 = _load_meta_strict(meta_path)
                        meta2['price_batch_start'] = anchor_doc['startedAt']
                        meta2['price_batch_idx'] = 0
                        meta2['last_price_month'] = anchor_doc['month']
                        tmp = meta_path + '.tmp'
                        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                            json.dump(meta2, f, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp, meta_path)
                        return {'written': True, 'reason': 'adopted-stage0',
                                'cycle': anchor_doc['cycle'], 'stage': 0,
                                'month': anchor_doc['month']}
                    out.update({'reason': 'material-blocked',
                                'detail': 'stage0-baseline-mismatch'})
                    return out
                if remote0 != 'error' and isinstance(remote0, dict):
                    if remote0.get('status') in ('active', 'needs_review'):
                        out.update({'reason': 'remote-quarantine',
                                    'detail': remote0.get('status')})
                        return out
            if anchor_doc['month'] == month and anchor_doc['status'] == 'completed' \
                    and anchor_doc['stage'] == PRICE_BATCH_DAYS \
                    and local_idx >= PRICE_BATCH_DAYS and client is not None and repo_root:
                try:
                    st = client.read_remote_state()
                    verify = _verify_local_material(meta, anchor_doc, client, repo_root,
                                                    transition_doc)
                    if st and st.get('snapshotHash') == _repo_snapshot_hash(repo_root) \
                            and verify is None:
                        out['reason'] = 'noop'
                        return _journal_noop(out)
                except Exception:  # noqa: BLE001
                    pass
            if local_start == anchor_doc['startedAt'] and local_idx > stage:
                if client is None or repo_root is None:
                    out['reason'] = 'reconciliation-required'
                    return out
                try:
                    rec = reconcile_shared(meta, anchor_doc, transition_doc, store, client,
                                           repo_root, apply_fn=apply_fn, now=now,
                                           meta_path=meta_path,
                                           writer_role=writer_role,
                                           actual_source=actual_source)
                except Exception as e:  # noqa: BLE001
                    out.update({'reason': 'reconciliation-blocked',
                                'detail': f'{type(e).__name__}:{e}'[:200]})
                    return out
                return {'written': False, 'reason': 'anchor-synced',
                        'cycle': rec['cycle'], 'stage': rec['state'].get('stage'),
                        'month': anchor_doc['month']}
            if client is None or repo_root is None:
                out['reason'] = 'reconciliation-required'
                return out
            try:
                rec = reconcile_shared(meta, anchor_doc, transition_doc, store, client,
                                       repo_root, apply_fn=apply_fn, now=now,
                                       meta_path=meta_path, writer_role=writer_role,
                                       actual_source=actual_source)
            except Exception as e:  # noqa: BLE001
                out.update({'reason': 'reconciliation-blocked',
                            'detail': f'{type(e).__name__}:{e}'[:200]})
                return out
            on_disk = _load_meta_strict(meta_path)
            return {'written': True, 'reason': 'adopted', 'cycle': rec['cycle'],
                    'stage': on_disk.get('price_batch_idx', stage),
                    'applied': rec['applied'], 'month': anchor_doc['month'],
                    'metaFacts': {k: on_disk.get(k) for k in
                                  ('appliedBundleHash', 'appliedSnapshotHash',
                                   'last_run', 'last_full', 'appliedAt')}}
        if state['reason'] == 'month-completed' and allow_write and anchor_doc:
            return _journal_noop(out)
        return out
    if not allow_write or dry_run:
        out = dict(state)
        out.update({'written': False, 'reason': 'not-enabled' if not allow_write else 'dry-run',
                    'planReason': state['reason']})
        return out
    started = hkt_now(now).strftime('%Y-%m-%d')
    lineage = ((transition_doc or {}).get('initial') or {}).get('snapshotHash')
    if not lineage:
        return {'written': False, 'reason': 'transition-baseline-missing', 'month': month}
    cycle_base = initial_base
    if anchor_doc is not None:  # rollover 舊月：base 由上月 history 真 proof 導出
        history_raw = store.read_history(anchor_doc['month']) if store else None
        history = _read_json_raw(history_raw, 'history') if history_raw else None
        if not (isinstance(history, dict)
                and history.get('status') == 'completed'
                and history.get('stage') == PRICE_BATCH_DAYS
                and (history.get('finalBundleHash') or history.get('lastBundleHash'))
                == anchor_doc.get('lastBundleHash')
                and isinstance(history.get('finalSnapshotHash'), str)
                and SHA256_RE.fullmatch(history['finalSnapshotHash'])):
            return {'written': False, 'reason': 'previous-history-missing', 'month': month}
        cycle_base = history['finalSnapshotHash']
    new_doc = {'formatVersion': SCHEMA_VERSION, 'protocol': PROTOCOL, 'month': month,
               'cycle': _anchor_cycle(started, 0), 'startedAt': started, 'stage': 0,
               'status': 'active', 'initialBaseHash': lineage, 'cycleBaseHash': cycle_base,
               'updatedAt': hkt_now(now).isoformat(timespec='seconds')}
    try:
        store.claim_raw(_canonical_bytes(new_doc), anchor_sha)
    except AnchorConflict:
        anchor2, _sha2, transition2 = _read_store(store)
        state2 = plan(meta, anchor2, transition2, now,
                      actual_source=actual_source, initial_base=initial_base)
        return {'written': False, 'reason': 'anchor-race-blocked',
                'planReason': state2.get('reason'), 'month': month,
                'cycle': (anchor2 or {}).get('cycle')}
    except AnchorError as e:
        return {'written': False, 'reason': 'blocked-anchor', 'detail': str(e), 'month': month}
    meta['price_batch_start'] = started
    meta['price_batch_idx'] = 0
    meta['last_price_month'] = month
    tmp = meta_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(meta, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, meta_path)
    return {'written': True, 'reason': 'started', 'cycle': new_doc['cycle'],
            'stage': 0, 'month': month}


def advance_stage(store, month, stage, bundle_hash, status=None, now=None,
                  snapshot_hash=None):
    anchor_doc, anchor_sha, _transition = _read_store(store)
    a_errs = validate_anchor(anchor_doc) if anchor_doc else ['anchor-missing']
    if a_errs:
        raise AnchorError('anchor-invalid:' + ','.join(a_errs))
    if anchor_doc['month'] != month:
        raise AnchorError('anchor-month-mismatch')
    if not isinstance(bundle_hash, str) or not SHA256_RE.fullmatch(bundle_hash):
        raise AnchorError('bundle-hash-required')
    if isinstance(stage, bool) or not isinstance(stage, int) or not (0 <= stage <= PRICE_BATCH_DAYS):
        raise AnchorError('stage-invalid')
    if stage == anchor_doc['stage']:
        if anchor_doc['status'] == 'completed' or stage < PRICE_BATCH_DAYS:
            return {'stage': stage, 'noop': True}
        raise AnchorError('stage-regression-or-duplicate')
    if stage < anchor_doc['stage']:
        raise AnchorError('stage-regression-or-duplicate')
    if stage != anchor_doc['stage'] + 1:
        raise AnchorError('stage-jump-refused')
    want_status = status or ('completed' if stage == PRICE_BATCH_DAYS else 'active')
    if stage == PRICE_BATCH_DAYS and want_status != 'completed':
        raise AnchorError('stage7-must-complete')
    if stage < PRICE_BATCH_DAYS and want_status != 'active':
        raise AnchorError('incomplete-stage-must-active')
    new_doc = dict(anchor_doc)
    new_doc.update({'stage': stage, 'status': want_status,
                    'cycle': _anchor_cycle(anchor_doc['startedAt'], stage),
                    'lastBundleHash': bundle_hash,
                    'updatedAt': hkt_now(now).isoformat(timespec='seconds')})
    errs = validate_anchor(new_doc)
    if errs:
        raise AnchorError('anchor-invalid:' + ','.join(errs))
    try:
        store.claim_raw(_canonical_bytes(new_doc), anchor_sha)
    except AnchorConflict:
        raise AnchorError('anchor-stage-conflict')
    if stage == PRICE_BATCH_DAYS:
        if not snapshot_hash or not SHA256_RE.fullmatch(str(snapshot_hash)):
            raise AnchorError('snapshot-hash-required-for-history')
        _archive_month(store, new_doc, snapshot_hash)
    return {'stage': stage, 'status': want_status}
