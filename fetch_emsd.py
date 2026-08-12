#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 EMSD 機電署能源標籤網下載全部空調機型號數據並存為 CSV
來源：https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php
"""
import urllib.request
import re
import csv
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
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
    return html


def main():
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
        time.sleep(0.5)

    out = r'd:\香港窗口式空調查找\emsd_空調能源標籤.csv'
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)
    print('完成！共', len(all_rows), '個型號，存於', out)


if __name__ == '__main__':
    main()
