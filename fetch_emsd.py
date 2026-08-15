#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 EMSD 機電署能源標籤網下載全部空調機型號數據並存為 CSV
來源：https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php
"""
import urllib.request
import urllib.error
import re
import csv
import json
import os
import random
import sys
import time
from html.parser import HTMLParser

BASE = 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php?type=all&searchR=50&p='


class TableParser(HTMLParser):
    """只提取表格內嘅 <tr>/<td> 數據"""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.cur_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
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


def fetch_page(p):
    url = BASE + str(p)
    req = urllib.request.Request(url, headers={
        'User-Agent': ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
                       '+https://github.com/CalvinLau1012/aircon-compare)'),
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
        return re.sub(r'[^A-Z0-9]', '', str(s).upper())

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
    p = 1
    while True:
        try:
            html = fetch_page(p)
        except Exception as e:
            print('頁', p, '錯誤:', e)
            break
        parser = TableParser()
        parser.feed(html)
        # 過濾：只留 15 欄嘅數據行（表頭 15 欄）
        rows = [r for r in parser.rows if len(r) == 15]
        if not rows:
            print('頁', p, '無數據，結束')
            break
        # 第一頁第一行係表頭
        if p == 1:
            header = rows[0]
            data_rows = rows[1:]
            print('表頭:', header)
        else:
            data_rows = rows
        all_rows.extend(data_rows)
        print('頁', p, '攞到', len(data_rows), '行，累計', len(all_rows))
        if len(data_rows) < 50:
            break
        p += 1
        time.sleep(random.uniform(1.0, 2.5))  # 分頁隨機抖動，唔畀官方機械式節奏

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'emsd_空調能源標籤.csv')
    detect_new_models(all_rows)  # 新機偵測（比較新舊 CSV）
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)
    print('完成！共', len(all_rows), '個型號，存於', out)


if __name__ == '__main__':
    main()
