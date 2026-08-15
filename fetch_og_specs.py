#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMSD 型號規格批量抓取（Price.com.hk 產品頁 og:description）
攞：室內機尺寸、雪種（交叉確認）、功能（判斷 WiFi）
輸出：specs_emsd.json {型號: {size, gas, func}}
"""
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36'


def get(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')


def extract_og(html):
    m = re.search(r'property="og:description" content="([^"]*)"', html)
    if not m:
        return {}
    d = {}
    for part in m.group(1).split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            d[k.strip()] = v.strip()
    return d


def norm_size(s):
    """尺寸字串 → H×W×D（排序：窗口機高最小、闊中間、深最大）"""
    nums = re.findall(r'[\d.]+', s)
    if len(nums) < 3:
        return s
    vals = sorted(float(x) for x in nums[:3])
    return f"{vals[0]:g}×{vals[1]:g}×{vals[2]:g}"


def fetch_one(pid):
    try:
        html = get('https://www.price.com.hk/product.php?p=' + pid)
        og = extract_og(html)
        out = {}
        if '室內機尺寸' in og:
            out['size'] = norm_size(og['室內機尺寸'])
        if '雪種' in og:
            out['gas'] = og['雪種']
        if '功能' in og:
            out['func'] = og['功能'][:120]
        if '類型' in og:
            out['mount'] = og['類型']  # 窗口式/分體式等
        if '淨冷/冷暖' in og:
            out['mode'] = og['淨冷/冷暖']  # 淨冷/冷暖
        return out or None
    except Exception:
        return None


def main():
    with open(os.path.join(BASE, 'prices.json'), encoding='utf-8') as f:
        prices = json.load(f)

    # 只做有 pid 嘅
    todo = [(m, info['pid']) for m, info in prices.items()
            if isinstance(info, dict) and info.get('pid')]
    print(f'有 {len(todo)} 個型號有產品 ID，開始抓規格...')

    results = {}
    if os.path.exists(os.path.join(BASE, 'specs_emsd.json')):
        with open(os.path.join(BASE, 'specs_emsd.json'), encoding='utf-8') as f:
            results = json.load(f)
        print(f'已有 {len(results)} 個結果')
    # 已有結果但缺新欄位（mount/mode）嘅要重抓
    need_refetch = [m for m, v in results.items() if 'mount' not in v]
    print(f'其中 {len(need_refetch)} 個要重抓（補 類型/淨冷）')
    todo = [(m, p) for m, p in todo if m not in results or m in set(need_refetch)]
    print(f'要抓 {len(todo)} 個')

    ok = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_one, p): m for m, p in todo}
        done = 0
        for fut in as_completed(futures):
            m = futures[fut]
            try:
                spec = fut.result()
            except Exception:
                spec = None
            if spec:
                results[m] = spec
                ok += 1
            done += 1
            if done % 100 == 0:
                el = time.time() - t0
                print(f'  進度 {done}/{len(todo)}（得規格 {ok}）· {el:.0f}s')
                with open(os.path.join(BASE, 'specs_emsd.json'), 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)

    out = os.path.join(BASE, 'specs_emsd.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    print(f'完成！得規格 {ok} 個 · 用咗 {time.time()-t0:.0f}s · 存於 {out}')


if __name__ == '__main__':
    main()
