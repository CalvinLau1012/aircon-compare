#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 EMSD 機電署能源標籤網下載全部空調機型號數據並存為 CSV
來源：https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php
"""
import urllib.request
import urllib.error
import csv
import json
import os
import random
import sys
import time
from html.parser import HTMLParser

from crawl_utils import BOT_UA, norm_model

BASE = 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php?type=all&searchR=50&p='
MIN_EMSD_ROWS = 1700  # 安全閘門：攞唔齊最少行數就唔覆寫現有 CSV
HEADER_SIGNATURE = '型號'  # 每頁表頭 signature：第 2 欄係「型號」

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE_DIR, 'update_queue.json')
RECEIPT_PATH = os.path.join(BASE_DIR, 'emsd_receipt.json')


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


def write_receipt(outcome, per_page):
    """寫 emsd_receipt.json（成功／失敗都寫，保留本次抓取證據）"""
    receipt = {
        'retrievedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sourceUrl': BASE.rstrip('&p='),
        'success': outcome['success'],
        'pagesExpected': outcome['pagesExpected'],
        'pagesFetched': outcome['pagesFetched'],
        'totalRows': outcome['totalRows'],
        'aborted': outcome['aborted'],
        'error': outcome['error'],
        'perPageRows': per_page,
    }
    with open(RECEIPT_PATH, 'w', encoding='utf-8') as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)


def fetch_page(p):
    url = BASE + str(p)
    req = urllib.request.Request(url, headers={
        'User-Agent': BOT_UA,
        'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5'})
    last = None
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
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
QUEUE_PATH = os.path.join(BASE_DIR, 'update_queue.json')


def load_queue():
    """讀取分批更新隊列"""
    try:
        with open(QUEUE_PATH, encoding='utf-8') as f:
            q = json.load(f)
            if isinstance(q, dict) and 'stage' in q:
                return q
    except Exception:
        pass
    return {'stage': 0, 'models': []}


def save_queue(q):
    with open(QUEUE_PATH, 'w', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False)


def detect_new_models(all_rows):
    """比較新舊型號，記錄新上市型號（new_models.json 累積，保留首次發現日期）"""
    base = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base, 'emsd_空調能源標籤.csv')
    new_path = os.path.join(base, 'new_models.json')

    def nk(s):
        return norm_model(s)  # 共用 crawl_utils（各腳本一字不差）

    # 舊 CSV 型號集合
    old_keys = set()
    if os.path.exists(csv_path):
        with open(csv_path, encoding='utf-8-sig') as f:
            for r in list(csv.reader(f))[1:]:
                if len(r) >= 15:
                    old_keys.add(nk(r[1]))

    # 已有新機記錄
    rec = {'updated': time.strftime('%Y-%m-%d'), 'models': []}
    if os.path.exists(new_path):
        try:
            with open(new_path, encoding='utf-8') as f:
                rec = json.load(f)
        except Exception:
            rec = {'updated': time.strftime('%Y-%m-%d'), 'models': []}
    known_keys = {nk(m.get('model')) for m in rec.get('models', [])}

    today = time.strftime('%Y-%m-%d')
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
    with open(new_path, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False)
    print('🆕 新機偵測：本次新增', len(added), '個', (' · ' + ', '.join(added[:12])) if added else '')
    # 有新機 → 寫入分批更新隊列（stage 1：官網核實第一批）
    if added:
        q = load_queue()
        if q['stage'] == 0:
            q['stage'] = 1
        for a in added:
            if a not in q['models']:
                q['models'].append(a)
        save_queue(q)
        print('📋 已加入分批更新隊列（stage', q['stage'], '，共', len(q['models']), '個新機待核實）')
    return added


def main():
    # Windows 控制台編碼保護（cp950 無法輸出部分字元）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    all_rows = []
    header = []
    per_page = []
    aborted = False
    error_msg = None
    p = 1
    while True:
        try:
            html = fetch_page(p)
        except SystemExit:
            raise  # 403/429：保護來源，直接中止
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
        all_rows.extend(rows)
        per_page.append(len(rows))
        print('頁', p, '攞到', len(rows), '行，累計', len(all_rows))
        if len(rows) < 50:
            break
        p += 1
        time.sleep(random.uniform(1.0, 2.5))  # 分頁隨機抖動，唔畀官方機械式節奏

    pages_fetched = p - 1 if aborted else p
    outcome = fetch_outcome(pages_expected=pages_fetched + (1 if aborted else 0),
                            pages_fetched=pages_fetched,
                            aborted=aborted,
                            total_rows=len(all_rows),
                            error=error_msg)
    write_receipt(outcome, per_page)

    if not outcome['success']:
        reason = (f'中途網絡錯誤（{error_msg}）' if aborted
                  else f'只攞到 {len(all_rows)} 行，少於安全下限 {MIN_EMSD_ROWS}')
        print(f'⚠️ EMSD 抓取未完整（{reason}），唔覆寫現有 CSV', file=sys.stderr)
        sys.exit(1)

    if not header or len(header) != 15:
        print('⚠️ 攞唔到有效表頭（15 欄），唔覆寫現有 CSV', file=sys.stderr)
        sys.exit(1)

    out = os.path.join(BASE_DIR, 'emsd_空調能源標籤.csv')
    detect_new_models(all_rows)  # 新機偵測（比較新舊 CSV）
    tmp = out + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)
    os.replace(tmp, out)  # 原子替換：寫好先換名，唔會整壞現有 CSV
    print('完成！共', len(all_rows), '個型號，存於', out)
    print(f'📦 抓取證據：{RECEIPT_PATH}（頁 {outcome["pagesFetched"]}/{outcome["pagesExpected"]}）')


if __name__ == '__main__':
    main()
