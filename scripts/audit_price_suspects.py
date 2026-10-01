#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""價錢疑點唯讀審計（離線；只讀取 repo 內現有 JSON 快照）。

用途：把**現有顯示用快照**（BigGo 現役快照、PricesAPI／Gemini 後備、以及
Price.com.hk 舊快照）入面值得人手覆核嘅項目列成結構化清單。

重要界線：
  - Price.com.hk 抓取已因 Cloudflare anti-bot 封鎖而**放棄**；`fetch_prices.py` 唔會
    重啟，亦唔會新增 selector／retry／繞過方案。本工具對 `prices.json` 只係
    **legacy 顯示資料嘅唯讀覆核**，唔係重啟抓取嘅路徑。
  - 金額低、PID 共用、單一商戶、缺身份證據都只係**覆核理由**，唔係錯價結論；
    本工具唔會修改任何快照，亦唔會把任何型號標為 invalid／停售／隔離。
  - 全程唔用網絡、唔讀 Secrets／私人識別；輸出只含公開型號、價錢、商戶索引。

用法：
  python scripts/audit_price_suspects.py [--out PATH] [--threshold 500] [--repo DIR]
輸出：預設 stdout（deterministic JSON）；`--out` 只可以寫 repo 外路徑
（除非顯式 `--allow-repo-path`）。退出碼：0 成功；2 輸入／路徑錯誤。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 1
DEFAULT_THRESHOLD = 500
SOURCES = (
    ('price_legacy', 'prices.json'),
    ('biggo', 'biggo_prices.json'),
    ('gemini', 'gemini_prices.json'),
)
NOTES = [
    'price_legacy = historical prices.json snapshot (Price.com.hk scraping was abandoned '
    'earlier due to Cloudflare anti-bot; read-only review only, not a path to restart scraping)',
    'site display chain currently biggo > gemini > legacy price snapshot; pricesapi_prices.json '
    'is not present/loaded in this checkout',
    'reasons are review flags, not proof of wrong price; no snapshot is modified or invalidated',
]


def _first_number(text):
    m = re.search(r'([0-9][0-9,]*(?:\.[0-9]+)?)', str(text or ''))
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', ''))
    except ValueError:
        return None


def _entry_price(entry):
    if isinstance(entry, dict):
        return entry.get('price')
    return entry


def _load_source(repo, name):
    path = os.path.join(repo, name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def _pid_map(price_data):
    by_pid = {}
    for model, entry in (price_data or {}).items():
        if isinstance(entry, dict):
            pid = entry.get('pid')
            if pid not in (None, ''):
                by_pid.setdefault(str(pid), []).append(model)
    return by_pid


def collect(repo, threshold=DEFAULT_THRESHOLD):
    """回傳 (report_dict)；只讀檔，無副作用。"""
    loaded = {}
    counts = {}
    for source, filename in SOURCES:
        data = _load_source(repo, filename)
        loaded[source] = data
        counts[source] = len(data) if isinstance(data, dict) else 0
    pid_map = _pid_map(loaded.get('price_legacy'))

    candidates = []
    by_reason = {}
    by_source = {}

    def add(model, source, raw_price, reasons, *, pid=None, url=None,
            merchants=None, matched_title=None):
        reasons = sorted(set(reasons))
        if not reasons:
            return
        by_source[source] = by_source.get(source, 0) + 1
        for reason in reasons:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        candidates.append({
            'model': str(model),
            'source': source,
            'rawPrice': raw_price,
            'pid': None if pid in (None, '') else str(pid),
            'url': None if url in (None, '') else str(url),
            'merchants': merchants if isinstance(merchants, int) else None,
            'matchedTitle': None if matched_title in (None, '') else str(matched_title),
            'reasons': reasons,
        })

    # ---- price_legacy（歷史 Price.com.hk 快照；唯讀，永不重抓） ----
    for model, entry in sorted((loaded.get('price_legacy') or {}).items()):
        raw = _entry_price(entry)
        reasons = []
        value = _first_number(raw)
        if value is not None and value < threshold:
            reasons.append('low_price')
        pid = entry.get('pid') if isinstance(entry, dict) else None
        if pid in (None, ''):
            reasons.append('missing_identity_evidence')
        elif len(pid_map.get(str(pid), [])) > 1:
            reasons.append('shared_pid')
        add(model, 'price_legacy', raw, reasons, pid=pid)

    # ---- biggo（現役主力；新 entry 有 matchedTitle／nindex 證據） ----
    for model, entry in sorted((loaded.get('biggo') or {}).items()):
        if not isinstance(entry, dict):
            continue
        raw = entry.get('price')
        reasons = []
        value = _first_number(raw)
        low = value is not None and value < threshold
        if low:
            reasons.append('low_price')
        merchants = entry.get('merchants')
        if isinstance(merchants, int) and not isinstance(merchants, bool) \
                and merchants == 1:
            reasons.append('single_merchant')
        if not entry.get('matchedTitle') and (low or merchants == 1):
            reasons.append('missing_identity_evidence')
        add(model, 'biggo', raw, reasons, url=entry.get('url'),
            merchants=merchants if isinstance(merchants, int) else None,
            matched_title=entry.get('matchedTitle'))

    # ---- gemini（AI 搜索後備；冇產品身份證據） ----
    for model, entry in sorted((loaded.get('gemini') or {}).items()):
        if not isinstance(entry, dict):
            continue
        raw = entry.get('price')
        reasons = ['missing_identity_evidence']
        value = _first_number(raw)
        if value is not None and value < threshold:
            reasons.append('low_price')
        add(model, 'gemini', raw, reasons, url=entry.get('url'))

    candidates.sort(key=lambda c: (c['source'], c['model']))
    return {
        'schemaVersion': SCHEMA_VERSION,
        'kind': 'price-suspect-audit',
        'mode': 'read-only-offline',
        'threshold': threshold,
        'notes': NOTES,
        'sources': {
            source: {'file': filename, 'present': loaded.get(source) is not None,
                     'entries': counts.get(source, 0)}
            for source, filename in SOURCES
        },
        'summary': {
            'candidateCount': len(candidates),
            'byReason': dict(sorted(by_reason.items())),
            'bySource': dict(sorted(by_source.items())),
        },
        'candidates': candidates,
    }


def _resolve_out(out_path, repo, allow_repo_path):
    """`--out` 必須喺 repo 外（除非顯式 allow）；回 (path, error)。"""
    repo_real = os.path.realpath(os.path.abspath(repo))
    out_real = os.path.realpath(os.path.abspath(out_path))
    inside = out_real == repo_real or out_real.startswith(repo_real + os.sep)
    if inside and not allow_repo_path:
        return None, ('--out 唔可以喺 repo 內（避免污染工作樹）；'
                      '請用 repo 外路徑或省略 --out 用 stdout')
    return out_real, None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='價錢疑點唯讀審計（離線）')
    ap.add_argument('--repo', default=BASE, help='repo 根目錄（預設：本 script 所在 repo）')
    ap.add_argument('--out', default=None,
                    help='輸出 JSON 路徑（預設 stdout；只可寫 repo 外）')
    ap.add_argument('--allow-repo-path', action='store_true',
                    help='顯式允許 --out 寫入 repo 內（預設拒絕）')
    ap.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD,
                    help=f'低價覆核門檻（預設 {DEFAULT_THRESHOLD}；只係審計篩選，唔係錯價判準）')
    args = ap.parse_args(argv)
    if args.threshold < 0:
        print('❌ --threshold 必須係非負整數', file=sys.stderr)
        return 2
    try:
        report = collect(args.repo, threshold=args.threshold)
    except (OSError, ValueError) as e:
        print(f'❌ 讀取快照失敗：{type(e).__name__}', file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if not args.out:
        sys.stdout.write(text)
        return 0
    out_path, err = _resolve_out(args.out, args.repo, args.allow_repo_path)
    if err:
        print(f'❌ {err}', file=sys.stderr)
        return 2
    try:
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    except OSError as e:
        print(f'❌ 報告寫入失敗：{type(e).__name__}', file=sys.stderr)
        return 2
    print(f'✅ 疑點審計完成：{report["summary"]["candidateCount"]} 個覆核候選 → {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
