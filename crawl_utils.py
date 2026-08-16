#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禮貌爬蟲共用工具（各 fetch_*.py / generate_html.py 共用，避免重複實現）
- 誠實 Bot UA（列明專案來源，方便網站管理員聯絡）
- 退避重試（遵從 Retry-After，唔硬碰限流）
- 隨機抖動延遲（避免突發流量）
- 型號規範化 norm_model（各腳本一字不差嘅去空格/大寫化）
- EMSD CSV 型號載入 load_models（去重）
"""
import csv
import json
import os
import random
import re
import ssl
import time
import urllib.request
import urllib.error

BOT_UA = ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
          '+https://github.com/CalvinLau1012/aircon-compare) '
          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36')

# 專案根目錄（所有腳本同層，__file__ 即係根目錄）
BASE = os.path.dirname(os.path.abspath(__file__))
EMSD_CSV = os.path.join(BASE, 'emsd_空調能源標籤.csv')


def norm_model(s):
    """型號規範化：去除非字母數字、轉大寫（各腳本統一比對用）"""
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def jitter_sleep(lo=0.3, hi=0.9):
    """隨機抖動延遲，避免機械式固定間隔"""
    time.sleep(random.uniform(lo, hi))


def fetch(url, timeout=15, retries=3, extra_headers=None):
    """帶退避重試嘅 GET（返回解碼文字）；連續 403/429 會拋出 HTTPError（叫用方應停止而非硬碰）"""
    last = None
    for attempt in range(retries):
        headers = {
            'User-Agent': BOT_UA,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5',
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429) and attempt < retries - 1:
                wait = int(e.headers.get('Retry-After') or 0) or 10 * (attempt + 1)
                time.sleep(wait)
            elif e.code in (403, 429):
                raise  # 已退避多次仍被拒 → 唔好再硬碰
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


def load_json(path, default=None):
    """讀 JSON；檔案唔存在或壞檔回 default，唔會整死批次"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data, indent=None):
    """寫 JSON（ensure_ascii=False），自動建 parent directory"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def html_to_text(html, joiner=' ', keep_lines=False):
    """HTML 轉純文字：移除 script/style/標籤，壓縮多餘空白"""
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html or '', flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', '\n' if keep_lines else joiner, t)
    t = re.sub(r'[ \t\r]+', ' ', t)
    if keep_lines:
        lines = [ln.strip() for ln in t.split('\n') if ln.strip()]
        return '\n'.join(lines)
    return re.sub(r'\s+', joiner, t).strip()


def no_verify_ssl_context():
    """部分香港官網 TLS 憑證鏈唔完整，先提供一個唔驗證憑證嘅 context（同舊行為一致）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_models(csv_path=None):
    """載入 EMSD CSV 型號清單（去重，按 norm_model 比對）"""
    path = csv_path or EMSD_CSV
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']
    models = []
    seen = set()
    for r in rows:
        m = r[1].strip()
        k = norm_model(m)
        if not k or k in seen:
            continue
        seen.add(k)
        models.append(m)
    return models
