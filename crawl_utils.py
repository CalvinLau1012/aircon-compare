#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禮貌爬蟲共用工具
- 誠實 Bot UA（列明專案來源，方便網站管理員聯絡）
- 退避重試（遵從 Retry-After，唔硬碰限流）
- 隨機抖動延遲（避免突發流量）
"""
import random
import time
import urllib.request
import urllib.error

BOT_UA = ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
          '+https://github.com/CalvinLau1012/aircon-compare) '
          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36')


def jitter_sleep(lo=0.3, hi=0.9):
    """隨機抖動延遲，避免機械式固定間隔"""
    time.sleep(random.uniform(lo, hi))


def fetch(url, timeout=15, retries=3):
    """帶退避重試嘅 GET；連續 403/429 會拋出 HTTPError（叫用方應停止而非硬碰）"""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            'User-Agent': BOT_UA,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5',
        })
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
