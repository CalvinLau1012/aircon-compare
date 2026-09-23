#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 EMSD 機電署能源標籤網下載全部空調機型號數據並存為 CSV
來源：https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php
"""
import urllib.request
import urllib.error
import csv
import hashlib
import io
import json
import os
import random
import sys
import time
import shutil
import tempfile
from html.parser import HTMLParser

from crawl_utils import BOT_UA, norm_model

_SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import private_raw_sink  # noqa: E402  D7-A：可插拔私人 raw sink adapter

BASE = 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php?type=all&searchR=50&p='
MIN_EMSD_ROWS = 1700  # 安全閘門：攞唔齊最少行數就唔覆寫現有 CSV
HEADER_SIGNATURE = '型號'  # 每頁表頭 signature：第 2 欄係「型號」

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE_DIR, 'update_queue.json')
RECEIPT_PATH = os.path.join(BASE_DIR, 'emsd_receipt.json')
RAW_RECEIPT_PATH = os.path.join(BASE_DIR, 'emsd_raw_receipt.json')
_LAST_HTTP = {}


class TableParser(HTMLParser):
    """只提取表格內嘅 <tr>/<td> 數據"""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.cur_row = []
        self.rows = []

    def handle_starttag(self, tag, *args):
        del args  # HTMLParser 傳入 attrs，呢度唔用
        if tag == 'table':
            self.in_table = True
        elif tag == 'tr' and self.in_table:
            self.in_tr = True
            self.cur_row = []
        elif tag in ('td', 'th') and self.in_tr:
            self.cur_cell = []

    def handle_data(self, data):
        if self.in_tr and hasattr(self, 'cur_cell'):
            self.cur_cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' or tag == 'th':
            if self.in_tr:
                self.cur_row.append(' '.join(''.join(self.cur_cell).split()))
                self.cur_cell = []
        elif tag == 'tr':
            if self.in_tr:
                self.rows.append(self.cur_row)
                self.in_tr = False
        elif tag == 'table':
            self.in_table = False


def parse_page_rows(html):
    """解析一頁 HTML：回 15 欄數據行（表頭按 signature 排除，唔限 p==1）"""
    parser = TableParser()
    parser.feed(html)
    rows = [r for r in parser.rows if len(r) == 15]
    return [r for r in rows if len(r) < 2 or r[1].strip() != HEADER_SIGNATURE]


def page_header(html):
    """攞一頁嘅表頭行（15 欄 + 第 2 欄係 signature）；冇就回 None"""
    parser = TableParser()
    parser.feed(html)
    for r in parser.rows:
        if len(r) == 15 and len(r) > 1 and r[1].strip() == HEADER_SIGNATURE:
            return r
    return None


def fetch_outcome(pages_expected, pages_fetched, aborted, total_rows, error=None):
    """fetch 結果判定（純函數，供測試）：
    - 中途網絡錯誤（aborted=True）→ 一律失敗，即使累積行數超過下限
    - 完整收尾且行數 >= 下限 → 成功
    """
    success = (not aborted) and total_rows >= MIN_EMSD_ROWS and pages_fetched > 0
    return {
        'success': success,
        'pagesExpected': pages_expected,
        'pagesFetched': pages_fetched,
        'totalRows': total_rows,
        'aborted': aborted,
        'error': error,
    }


def raw_page_record(page, raw_bytes, headers=None):
    """單頁原始 HTTP bytes 證據（唔會 re-serialize）。"""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ValueError('raw_bytes 必須係 bytes')
    headers = headers or {}
    return {
        'page': int(page),
        'byteLength': len(raw_bytes),
        'sha256': 'sha256:' + hashlib.sha256(bytes(raw_bytes)).hexdigest(),
        'lastModified': headers.get('lastModified') or headers.get('last-modified'),
        'etag': headers.get('etag') or headers.get('ETag'),
        '_raw': bytes(raw_bytes),
    }


def archive_hash(records):
    """以頁序＋長度前綴 framing 計整批 raw bytes hash（可重現、無歧義）。"""
    h = hashlib.sha256()
    for rec in sorted(records, key=lambda r: r['page']):
        page = str(rec['page']).encode('ascii')
        data = rec['_raw']
        h.update(len(page).to_bytes(8, 'big'))
        h.update(page)
        h.update(len(data).to_bytes(8, 'big'))
        h.update(data)
    return 'sha256:' + h.hexdigest()


def build_raw_receipt(records, dataset_hash, retrieved_at, source_url, total_rows,
                      per_page_rows):
    """由完整頁面 raw bytes 建立公開 raw receipt（只含 hash／metadata，無 bytes）。

    缺頁、重複頁、0 頁一律 ValueError；唔准把 CSV hash 冒充 raw hash。
    """
    if not isinstance(records, list) or not records:
        raise ValueError('raw records 唔可以空')
    pages = sorted(int(r['page']) for r in records)
    if pages != list(range(1, len(pages) + 1)):
        raise ValueError(f'raw pages 必須由 1 連續至 N（got {pages}）')
    public_pages = []
    for rec in sorted(records, key=lambda r: r['page']):
        public_pages.append({k: v for k, v in rec.items() if k != '_raw'})
    return {
        'schemaVersion': 1,
        'retrievedAt': retrieved_at,
        'sourceUrl': source_url,
        'success': True,
        'pageCount': len(records),
        'totalRows': total_rows,
        'perPageRows': list(per_page_rows),
        'datasetHash': dataset_hash,
        'archiveHash': archive_hash(records),
        'pages': public_pages,
    }


def write_raw_receipt_atomic(receipt):
    tmp = RAW_RECEIPT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RAW_RECEIPT_PATH)


def cleanup_expired_raw_sink(sink_dir, now=None, max_age_days=90):
    """D7-A：保留舊名 API；實作喺 private_raw_sink（只刪安全 run 目錄）。"""
    return private_raw_sink.cleanup_expired(sink_dir, now=now, max_age_days=max_age_days)


def persist_raw_archive(records, sink_dir=None, require=False, now=None, remote_api=None):
    """D7-A：保留舊名 API；按環境選 local／remote adapter（remote 未經批准唔會配置）。

    回傳唔含敏感路徑／token；只有下載核驗成功才回 persisted=True。
    """
    explicit = sink_dir or os.environ.get('AIRCON_EMSD_RAW_SINK_DIR')
    if explicit:
        sink_abs = os.path.abspath(explicit)
        repo = os.path.abspath(BASE_DIR)
        try:
            inside = os.path.commonpath([sink_abs, repo]) == repo
        except ValueError:
            inside = False
        if inside:
            raise ValueError('private raw sink 唔可以喺公開 repo 工作樹內')
    return private_raw_sink.persist_raw_archive(
        records, sink_dir=sink_dir, require=require, now=now, remote_api=remote_api)


def write_receipt(outcome, per_page, csv_path=None, retrieved_at=None, raw_receipt_hash=None):
    """寫 emsd_receipt.json（成功／失敗都寫，保留本次抓取證據）

    - retrieved_at：實際抓取完成（或中途失敗）嘅 UTC 時間。成功路徑必須由
      fetch 迴圈收尾後即時傳入，唔可以用寫收據當刻時間冒充抓取完成時間。
    - csv_path：只可以在 CSV 已成功原子寫入之後傳入；會即場對已寫入檔案計
      datasetHash，令收據同 CSV bytes 綁定。
    """
    if retrieved_at is None:
        retrieved_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    receipt = {
        'retrievedAt': retrieved_at,
        'sourceUrl': BASE.rstrip('&p='),
        'success': outcome['success'],
        'pagesExpected': outcome['pagesExpected'],
        'pagesFetched': outcome['pagesFetched'],
        'totalRows': outcome['totalRows'],
        'aborted': outcome['aborted'],
        'error': outcome['error'],
        'perPageRows': per_page,
    }
    if csv_path is not None:
        with open(csv_path, 'rb') as source:
            receipt['datasetHash'] = 'sha256:' + hashlib.sha256(source.read()).hexdigest()
    if raw_receipt_hash:
        receipt['rawReceiptHash'] = raw_receipt_hash
    tmp = RECEIPT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RECEIPT_PATH)


def fetch_page(p):
    url = BASE + str(p)
    req = urllib.request.Request(url, headers={
        'User-Agent': BOT_UA,
        'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5'})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                _LAST_HTTP.clear()
                _LAST_HTTP.update({
                    'bytes': raw,
                    'lastModified': resp.headers.get('Last-Modified'),
                    'etag': resp.headers.get('ETag'),
                })
                return raw.decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429):
                raise SystemExit(f'EMSD 返回 {e.code}（被限流），中止更新以保護來源，唔寫檔案')
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class DatasetCommitError(RuntimeError):
    """資料提交失敗；rolled_back=False 代表回滾未完成，journal 仍然保留。"""

    def __init__(self, message, rolled_back):
        super().__init__(message)
        self.rolled_back = rolled_back


def _journal_path():
    return os.path.join(BASE_DIR, 'dataset_commit.journal')


def load_queue():
    """讀取分批更新隊列（共用 queue_utils 契約；損毀唔可以默默重置）。"""
    from queue_utils import load_queue as _load_queue
    return _load_queue(QUEUE_PATH)


def plan_new_models(all_rows):
    """純讀計畫：比較新舊型號，計出本次新增；**唔寫任何檔**。

    回傳 {'new_models': rec, 'queue': q, 'added': [model, ...]}。
    真正寫入由 commit_dataset() 以交易方式一次過完成，避免 CSV／
    new_models.json／update_queue.json 出現半更新狀態。
    """
    csv_path = os.path.join(BASE_DIR, 'emsd_空調能源標籤.csv')
    new_path = os.path.join(BASE_DIR, 'new_models.json')

    def nk(s):
        return norm_model(s)  # 共用 crawl_utils（各腳本一字不差）

    # 舊 CSV 型號集合
    old_keys = set()
    if os.path.exists(csv_path):
        with open(csv_path, encoding='utf-8-sig') as f:
            for r in list(csv.reader(f))[1:]:
                if len(r) >= 15:
                    old_keys.add(nk(r[1]))

    # 已有新機記錄：損毀／結構錯唔可以默默重置
    today = time.strftime('%Y-%m-%d')
    rec = {'updated': today, 'models': []}
    if os.path.exists(new_path):
        try:
            with open(new_path, encoding='utf-8') as f:
                rec = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(f'new_models.json 損毀，唔可以默默重置：{e}')
        if not isinstance(rec, dict) or not isinstance(rec.get('models'), list):
            raise ValueError('new_models.json 結構唔正確，唔可以默默重置')
    known_keys = {nk(m.get('model')) for m in rec.get('models', []) if isinstance(m, dict)}

    added = []
    for r in all_rows:
        model = r[1].strip()
        key = nk(model)
        if key in old_keys or key in known_keys:
            continue
        try:
            kw = float(r[6])
        except (ValueError, TypeError):
            kw = 0.0
        rec['models'].append({
            'brand': r[0].strip(),
            'model': model,
            'energy': (r[4].strip() + '級') if str(r[4]).strip().isdigit() else str(r[4]).strip(),
            'kw': r[6],
            'btu': f'{kw*3412:,.0f}' if kw else '',
            'cspf': r[7],
            'gas': r[8],
            'type': '變頻' if '是' in str(r[14]) else '定頻',
            'first_seen': today,
        })
        known_keys.add(key)
        added.append(model)
    rec['updated'] = today
    # 有新機 → 準備分批更新隊列（stage 1：官網核實第一批）
    q = load_queue()
    if added:
        if q['stage'] == 0:
            q['stage'] = 1
        for a in added:
            if a not in q['models']:
                q['models'].append(a)
    return {'new_models': rec, 'queue': q, 'added': added}


def _build_csv_bytes(header, rows):
    """CSV bytes（utf-8-sig + LF）；寫檔同交易提交共用同一 builder。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def _read_optional(path):
    """只 FileNotFoundError 當「原本唔存在」；其他讀取錯誤喺變更前阻斷。"""
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _atomic_write_bytes(path, data):
    path = os.fspath(path)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


def _write_journal(saved):
    """寫 crash-recovery journal（舊 bytes base64）。"""
    import base64
    payload = {'version': 1, 'targets': {
        path: (None if data is None else base64.b64encode(data).decode('ascii'))
        for path, data in saved.items()}}
    _atomic_write_bytes(_journal_path(), json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def recover_commit_journal():
    """啟動時：若上次提交中途 crash，依 journal 還原；回傳是否有恢復。"""
    import base64
    jp = _journal_path()
    if not os.path.exists(jp):
        return False
    try:
        with open(jp, encoding='utf-8') as f:
            data = json.load(f)
        targets = data['targets']
        if not isinstance(targets, dict):
            raise ValueError('targets 唔係 object')
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f'dataset commit journal 損毀，需人手處理：{e}')
    for path, b64 in targets.items():
        try:
            if b64 is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                _atomic_write_bytes(path, base64.b64decode(b64))
        except (OSError, ValueError) as e:
            raise RuntimeError(f'journal 恢復失敗（{path}）：{e}（保留 journal 作恢復證據）')
    os.remove(jp)
    print('⚠️ 偵測到未完成嘅資料提交，已依 journal 恢復至提交前狀態')
    return True


def commit_dataset(header, rows, csv_path, plan):
    """交易式提交 CSV + new_models.json + update_queue.json。

    先讀舊 bytes（唯一 FileNotFound 可當 missing）→ 寫 crash journal →
    寫齊三個 .tmp → 逐個 os.replace。任何一步失敗即回滾已換入嘅檔；
    回滾完整才清 journal，回滾失敗會保留 journal 並以 rolled_back=False 拋出
    （呼叫方唔可以聲稱資料原狀）。

    界限：三個 replace 唔係 power-loss atomic；journal 用嚟喺下次啟動恢復。
    """
    targets = [
        (csv_path, _build_csv_bytes(header, rows)),
        (os.path.join(BASE_DIR, 'new_models.json'), _json_bytes(plan['new_models'])),
        (QUEUE_PATH, _json_bytes(plan['queue'])),
    ]
    saved = {path: _read_optional(path) for path, _ in targets}
    _write_journal(saved)
    tmps = []
    replaced = []
    try:
        for path, data in targets:
            tmp = path + '.tmp'
            tmps.append((path, tmp))
            with open(tmp, 'wb') as f:
                f.write(data)
        for path, tmp in tmps:
            os.replace(tmp, path)
            replaced.append(path)
    except OSError as e:
        rollback_errors = []
        for path in reversed(replaced):
            old = saved[path]
            try:
                if old is None:
                    os.remove(path)
                else:
                    _atomic_write_bytes(path, old)
            except OSError as re:
                rollback_errors.append(f'{path}: {re}')
        if rollback_errors:
            raise DatasetCommitError(
                f'提交失敗（{e}）且回滾未完成：' + '；'.join(rollback_errors), rolled_back=False)
        try:
            os.remove(_journal_path())
        except OSError:
            pass
        raise DatasetCommitError(f'提交失敗（{e}），已回滾至原狀', rolled_back=True)
    finally:
        for _path, tmp in tmps:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    try:
        os.remove(_journal_path())
    except OSError:
        pass


def write_csv(header, rows, path):
    """寫 EMSD CSV（原子替換）。

    固定用 LF（`lineterminator='\n'`）：`.gitattributes` 係 `* text=auto eol=lf`，
    git add 時會把 CRLF 正規化為 LF；若工作樹係 CRLF，pipeline 對工作樹計算嘅
    datasetHash／releasePayloadHash 就會同已發佈 bytes 唔一致（GitHub Pages hash 鏈斷）。
    所以由源頭寫 LF，確保 worktree bytes == git index bytes == 發佈 bytes。
    """
    _atomic_write_bytes(path, _build_csv_bytes(header, rows))


def main():
    # Windows 控制台編碼保護（cp950 無法輸出部分字元）
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')

    # 防誤觸：--help／-h 只印用法，绝不連網（本腳本無 argparse，曾經因此意外真抓）
    if any(arg in ('-h', '--help') for arg in sys.argv[1:]):
        print('用法：python fetch_emsd.py\n'
              '  （無參數）抓取 EMSD 全量並原子更新 emsd_空調能源標籤.csv + emsd_receipt.json；\n'
              '  失敗會寫失敗收據且唔覆寫舊 CSV。')
        return

    try:
        recover_commit_journal()
    except RuntimeError as e:
        print(f'❌ {e}', file=sys.stderr)
        sys.exit(1)

    _LAST_HTTP.clear()
    all_rows = []
    raw_records = []
    header = []
    per_page = []
    aborted = False
    error_msg = None
    p = 1
    while True:
        try:
            html = fetch_page(p)
        except SystemExit as exc:
            write_receipt(fetch_outcome(p, len(per_page), True, len(all_rows), str(exc)), per_page)
            raise  # 403/429：保護來源，記錄失敗後中止
        except Exception as e:
            aborted = True
            error_msg = f'第 {p} 頁：{e}'
            print('頁', p, '錯誤:', e)
            break
        rows = parse_page_rows(html)
        if not rows:
            print('頁', p, '無數據，結束')
            break
        if p == 1:
            header = page_header(html) or []
            print('表頭:', header)
        # D7-A：保存本頁實際接收 bytes（失敗／空確認頁唔當有數據頁）；測試 fallback
        # 會由 monkeypatched fetch_page 回傳字串 encode，確保 contract 仍可驗。
        raw = _LAST_HTTP.get('bytes')
        if raw is None:
            raw = html.encode('utf-8')
        raw_records.append(raw_page_record(
            p, raw, {'lastModified': _LAST_HTTP.get('lastModified'),
                     'etag': _LAST_HTTP.get('etag')}))
        all_rows.extend(rows)
        per_page.append(len(rows))
        print('頁', p, '攞到', len(rows), '行，累計', len(all_rows))
        if len(rows) < 50:
            break
        p += 1
        time.sleep(random.uniform(1.0, 2.5))  # 分頁隨機抖動，唔畀官方機械式節奏

    # 抓取實際完成時間：由迴圈收尾即時捕捉（唔可以用寫收據當刻時間冒充）
    retrieved_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    # pagesFetched 只計有數據嘅頁。最後一頁剛好 50 行時，下一頁會回空確認到尾，
    # 該空頁唔算 fetched；唔可以再攞 p 當頁數（會多報一頁）。
    pages_fetched = len(per_page)
    pages_expected = pages_fetched + 1 if aborted else pages_fetched
    outcome = fetch_outcome(pages_expected=pages_expected,
                            pages_fetched=pages_fetched,
                            aborted=aborted,
                            total_rows=len(all_rows),
                            error=error_msg)
    if not outcome['success']:
        write_receipt(outcome, per_page, retrieved_at=retrieved_at)
        reason = (f'中途網絡錯誤（{error_msg}）' if aborted
                  else f'只攞到 {len(all_rows)} 行，少於安全下限 {MIN_EMSD_ROWS}')
        print(f'⚠️ EMSD 抓取未完整（{reason}），唔覆寫現有 CSV', file=sys.stderr)
        sys.exit(1)

    if not header or len(header) != 15:
        outcome.update(success=False, aborted=True, error='invalid header')
        write_receipt(outcome, per_page, retrieved_at=retrieved_at)
        print('⚠️ 攞唔到有效表頭（15 欄），唔覆寫現有 CSV', file=sys.stderr)
        sys.exit(1)

    out = os.path.join(BASE_DIR, 'emsd_空調能源標籤.csv')
    # 先純讀計畫（讀舊 CSV／現有 sidecar），失敗即寫失敗收據，唔改任何資料檔。
    try:
        plan = plan_new_models(all_rows)
    except Exception as e:
        outcome.update(success=False, aborted=True, error=f'new-model detection failed: {e}')
        write_receipt(outcome, per_page, retrieved_at=retrieved_at)
        print(f'❌ 新機偵測失敗（{e}），唔覆寫現有 CSV', file=sys.stderr)
        sys.exit(1)
    # 交易式提交 CSV + new_models.json + update_queue.json：
    # 任何一步失敗即回滾已換入嘅檔，唔會留下半更新狀態。
    try:
        commit_dataset(header, all_rows, out, plan)
    except DatasetCommitError as e:
        outcome.update(success=False, aborted=True, error=f'dataset commit failed: {e}')
        try:
            write_receipt(outcome, per_page, retrieved_at=retrieved_at)
        except Exception as re:
            print(f'⚠️ 失敗收據亦寫唔到：{re}', file=sys.stderr)
        if e.rolled_back:
            print(f'❌ 資料提交失敗（{e}）；已回滾，三份資料保持原狀', file=sys.stderr)
        else:
            print(f'❌ 資料提交失敗而且回滾未完成（{e}）；journal 保留作恢復證據，'
                  '下游必須阻斷', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        outcome.update(success=False, aborted=True, error=f'dataset commit failed: {e}')
        write_receipt(outcome, per_page, retrieved_at=retrieved_at)
        print(f'❌ 資料提交失敗（{e}）', file=sys.stderr)
        sys.exit(1)
    added = plan['added']
    if added:
        print('🆕 新機偵測：本次新增', len(added), '個', ' · '.join(added[:12]))
        print('📋 已加入分批更新隊列（stage', plan['queue']['stage'],
              '，共', len(plan['queue']['models']), '個新機待核實）')
    else:
        print('🆕 新機偵測：本次新增 0 個')
    # D7-A：先寫 private raw archive（repo 外）。persist 失敗／require 而缺配置即阻斷，
    # 確保唔會出現「public receipt 聲稱已保存但 private object 唔存在」。
    require_raw = os.environ.get('AIRCON_EMSD_REQUIRE_RAW_SINK') == '1'
    try:
        sink_result = persist_raw_archive(raw_records, require=require_raw)
    except Exception as e:
        print(f'❌ private raw archive 保存失敗，阻斷發布（{e}）', file=sys.stderr)
        sys.exit(1)
    raw_receipt_hash = None
    if sink_result.get('persisted'):
        try:
            dataset_hash = 'sha256:' + hashlib.sha256(open(out, 'rb').read()).hexdigest()
            raw_receipt = build_raw_receipt(
                raw_records, dataset_hash=dataset_hash, retrieved_at=retrieved_at,
                source_url=BASE.rstrip('&p='), total_rows=len(all_rows),
                per_page_rows=per_page)
            raw_receipt['privateArchive'] = {
                'persisted': True,
                'adapter': sink_result.get('adapter'),
                'objectId': sink_result.get('objectId'),
                'archiveHash': sink_result.get('archiveHash'),
                'verified': sink_result.get('verified') is True,
                'durableRemote': sink_result.get('durableRemote') is True,
                'retentionDays': sink_result.get('retentionDays'),
            }
            write_raw_receipt_atomic(raw_receipt)
            raw_receipt_hash = 'sha256:' + hashlib.sha256(
                open(RAW_RECEIPT_PATH, 'rb').read()).hexdigest()
        except Exception as e:
            print(f'❌ raw receipt 原子寫入失敗，阻斷發布（{e}）', file=sys.stderr)
            sys.exit(1)
    else:
        print('⚠️ private raw sink 未配置（非 require 模式）：今次唔寫 raw receipt；'
              '正式 CI 必須設定 remote adapter（AIRCON_EMSD_RAW_REMOTE_REPO／TOKEN）'
              '或者過渡用 AIRCON_EMSD_RAW_SINK_DIR＋require。', file=sys.stderr)
    # 成功收據必須喺整組資料提交之後、對已寫入 CSV bytes 計 hash；寫唔到收據即阻斷
    # （唔可以用舊收據配新 CSV 誤導下游 metadata 生成）。
    try:
        write_receipt(outcome, per_page, out, retrieved_at=retrieved_at,
                      raw_receipt_hash=raw_receipt_hash)
    except Exception as e:
        print(f'❌ 收據寫入失敗（{e}）；CSV 已更新但無有效收據，下游必須阻斷', file=sys.stderr)
        sys.exit(1)
    print('完成！共', len(all_rows), '個型號，存於', out)
    print(f'📦 抓取證據：{RECEIPT_PATH}（頁 {outcome["pagesFetched"]}/{outcome["pagesExpected"]}，retrievedAt={retrieved_at}）')


if __name__ == '__main__':
    main()
