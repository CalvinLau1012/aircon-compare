#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量驗證 Price.com.hk 產品鏈接（pid）是否真係指向對應型號
- 逐個抓取 product.php?p={pid} 頁面標題，與型號規範化比對
- 唔匹配 → 重新 search 攞新 pid 再驗證一次
- 仍唔匹配 → 清除 pid（網頁會退返 search 鏈接，唔會指錯產品）
- 完成後寫回 prices.json + 輸出報告 verify_report.json
"""
import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
PRICES_PATH = os.path.join(BASE, 'prices.json')
REPORT_PATH = os.path.join(BASE, 'verify_report.json')
PROGRESS_PATH = os.path.join(BASE, 'verify_progress.json')

UA = ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
      '+https://github.com/CalvinLau1012/aircon-compare) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36')


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def _get(url):
    """帶抖動+退避嘅 GET，返回 html 或 None"""
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.2, 0.5))
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            return urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                wait = int(e.headers.get('Retry-After') or 0) or 8 * (attempt + 1)
                time.sleep(wait)
            elif e.code in (403, 429):
                return None
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def page_title(html):
    """從產品頁抽取標題（og:title > <title>）"""
    if not html:
        return ''
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    return m.group(1).strip() if m else ''


def search_first_pid(html):
    m = re.search(r'product\.php\?p=(\d+)', html or '')
    return m.group(1) if m else None


def check_pid(model, pid):
    """檢查 pid 頁面標題係咪對應 model；返回 (ok, title, 頁面狀態)"""
    html = _get(f'https://www.price.com.hk/product.php?p={pid}')
    title = page_title(html)
    if not title:
        return False, '', 'no-title'
    nm, nt = norm_model(model), norm_model(title)
    if len(nm) >= 4 and (nm in nt or nt in nm):
        return True, title, 'ok'
    return False, title, 'mismatch'


def fix_by_search(model):
    """重新 search 攞第一個 pid 並再驗證；返回 (pid, title) 或 (None, '')"""
    html = _get('https://www.price.com.hk/search.php?g=A&q=' + urllib.parse.quote(model))
    pid = search_first_pid(html)
    if not pid:
        return None, ''
    ok, title, _ = check_pid(model, pid)
    return (pid, title) if ok else (None, title)


def load_progress():
    """讀取分批驗證進度（已成功核實過嘅型號）"""
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH, encoding='utf-8') as f:
                p = json.load(f)
            return {'verified': list(p.get('verified', [])), 'round_started': p.get('round_started', '')}
        except Exception:
            pass
    return {'verified': [], 'round_started': ''}


def save_progress(prog):
    with open(PROGRESS_PATH, 'w', encoding='utf-8') as f:
        json.dump(prog, f, ensure_ascii=False)


def main():
    # 分批模式：--batch N 按未驗證順序驗證 N 個（進度存 verify_progress.json，分多日完成全量）
    batch_n = None
    if '--batch' in sys.argv:
        try:
            batch_n = int(sys.argv[sys.argv.index('--batch') + 1])
        except (ValueError, IndexError):
            batch_n = 200
    # 抽樣模式：--sample N 只驗證 N 個隨機樣本
    sample_n = None
    if '--sample' in sys.argv:
        try:
            sample_n = int(sys.argv[sys.argv.index('--sample') + 1])
        except (ValueError, IndexError):
            sample_n = 50

    with open(PRICES_PATH, encoding='utf-8') as f:
        data = json.load(f)

    items = [(m, v.get('pid')) for m, v in data.items()
             if isinstance(v, dict) and v.get('pid')]
    prog = None
    if sample_n and sample_n < len(items):
        random.seed(int(time.strftime('%Y%m%d')))  # 固定每日種子，重跑結果一致
        items = random.sample(items, sample_n)
        print(f'抽樣模式：{len(items)} 個（每日快速驗證）')
    elif batch_n:
        prog = load_progress()
        vset = set(prog['verified'])
        pending = [it for it in items if it[0] not in vset]
        if not pending:
            # 一輪完成：重置進度，開新一輪
            prog = {'verified': [], 'round_started': time.strftime('%Y-%m-%d')}
            save_progress(prog)
            vset = set()
            pending = items
            print('🎉 上一輪全量核實完成，開始新一輪')
        batch = pending[:batch_n]
        print(f'分批模式：本批 {len(batch)} 個（本輪已核實 {len(vset)} 個）')
        items = batch
    else:
        print(f'要驗證 {len(items)} 個產品鏈接...')

    ok_n = 0
    fixed = 0
    cleared = 0
    kept = 0
    t0 = time.time()
    report = {'checked': len(items), 'ok': [], 'fixed': [], 'cleared': [], 'kept': []}

    def verify_one(item):
        model, pid = item
        ok, title, status = check_pid(model, pid)
        if ok:
            return ('ok', model, pid, title)
        if status == 'no-title':
            # 攞唔到頁面（網絡/限流）→ 唔可以判錯，保留原 pid 下次再驗
            return ('kept', model, pid, title)
        # 有標題但唔匹配：先重 search 一次
        new_pid, new_title = fix_by_search(model)
        if new_pid and new_pid != pid:
            return ('fixed', model, new_pid, new_title)
        if new_pid == pid:
            # search 第一個結果仍係原本嗰個 → 頁面或寫法差異，保留
            return ('kept', model, pid, new_title or title)
        return ('cleared', model, None, new_title or title)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(verify_one, it): it for it in items}
        done = 0
        for fut in as_completed(futures):
            st, model, pid, title = fut.result()
            if st == 'ok':
                ok_n += 1
                report['ok'].append({'model': model, 'pid': pid})
                if prog is not None:
                    prog['verified'].append(model)
            elif st == 'fixed':
                fixed += 1
                data[model]['pid'] = pid
                report['fixed'].append({'model': model, 'pid': pid, 'title': title})
                if prog is not None:
                    prog['verified'].append(model)
            elif st == 'kept':
                kept += 1
                report['kept'].append({'model': model, 'pid': pid})
                # 網絡/限流問題：唔算核實，留待下批再試
            else:
                cleared += 1
                data[model]['pid'] = None
                report['cleared'].append({'model': model, 'title': title})
                if prog is not None:
                    prog['verified'].append(model)
            done += 1
            if done % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done}/{len(items)}（正確 {ok_n} · 修正 {fixed} · 保留 {kept} · 清除 {cleared}）· {el:.0f}s', flush=True)
                with open(REPORT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(report, f, ensure_ascii=False, indent=1)
                if prog is not None:
                    save_progress(prog)
                # 限流熔斷：過半數攞唔到頁面 → 疑似被封，中止，唔改數據
                if done >= 50 and kept > done * 0.5:
                    print('⚠️ 疑似被官方限流（過半攞唔到頁面），中止驗證，保留現有鏈接', flush=True)
                    report['aborted'] = True
                    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
                        json.dump(report, f, ensure_ascii=False, indent=1)
                    sys.exit(1)

    with open(PRICES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    if prog is not None:
        save_progress(prog)

    el = time.time() - t0
    print(f'完成！共 {len(items)} 個 · 正確 {ok_n} · 重新修正 {fixed} · 保留 {kept} · 清除錯誤 {cleared} · 用咗 {el:.0f}s')
    print(f'報告：{REPORT_PATH}')
    if cleared:
        print('⚠️ 已清除錯誤鏈接嘅型號（網頁會改用 search 鏈接）：')
        for r in report['cleared']:
            print('   -', r['model'])


if __name__ == '__main__':
    main()
