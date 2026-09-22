#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署後核對（GATE-08）：線上 metadata／payload hash／核心瀏覽器行為

以指定發佈事實作 expected，檢查線上：
  1. metadata.json 可取得，version／build／commit／workflowRunId／releasePayloadHash／
     datasetHash／datasetDate／deployTime 同 expected 完全一致（錯版本最終必須失敗，
     唔會回退任意最新版假通過）；
  2. index.html、PDF、CSV 可取得（cache bust + 有界重試，處理 CDN 舊快取）；
  3. CSV bytes SHA-256 同 metadata.datasetHash 一致；
  4. 由 manifest 檔案 bytes 重算 releasePayloadHash（同 scripts/gen-metadata.py 同一
     framing），同 expected metadata.releasePayloadHash 一致；
  5. 核心瀏覽器行為 + runtime Version／Last Update／Last Deploy（HKT）顯示核對。

安全：
  - 預設只接受本 repo 官方 Pages host 及 localhost（fixture 測試）；其他 host 要
    顯式 --allow-host。URL 有 userinfo／非 http(s) 一律拒絕；
  - GET 只帶 no-cache 標頭，永不帶 Secrets／Authorization。

用法：
  python scripts/postdeploy_check.py --expected-metadata metadata.json \
      [--manifest deploy_payload.json] [--base-url https://calvinlau1012.github.io/aircon-compare/] \
      [--report <path>] [--retries 3] [--timeout 60] [--no-browser] [--payload-dir <dir>]
退出碼：0 = 全部通過；1 = 任一失敗
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
DEFAULT_BASE_URL = 'https://calvinlau1012.github.io/aircon-compare/'
OFFICIAL_HOSTS = {'calvinlau1012.github.io'}
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}
REQUIRED_COMPARE_FIELDS = (
    'version', 'build', 'commit', 'workflowRunId', 'releasePayloadHash',
    'datasetHash', 'datasetDate', 'deployTime',
)


def metadata_schema_errors(meta):
    """完整 Draft 2020-12 + format 驗證（同治理內嵌 METADATA_SCHEMA_V1）。"""
    from validate_metadata import validate
    from extract_governance import extract_blocks, GOV_FILE
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    return validate(meta, schema)


def metadata_object_diff(expected, online):
    """回傳 expected 同 online 所有唔同嘅 key（用於完整 object 等值檢查）。"""
    keys = set(expected) | set(online)
    return sorted(k for k in keys if expected.get(k) != online.get(k))


def ensure_trusted_base(base_url, extra_hosts=()):
    """只准官方 Pages 或 localhost（或顯式 allowlist）；拒絕任意 host / userinfo。"""
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f'base URL 必須係 http(s)：{base_url!r}')
    if parsed.username or parsed.password:
        raise ValueError('base URL 唔可以有 userinfo（避免憑證洩漏）')
    host = parsed.hostname
    if not host:
        raise ValueError(f'base URL 冇 host：{base_url!r}')
    allowed = OFFICIAL_HOSTS | LOCAL_HOSTS | {h.lower() for h in extra_hosts}
    if host.lower() not in allowed:
        raise ValueError(f'不受信任 host：{host!r}（只准官方 Pages／localhost／--allow-host）')
    if host.lower() in OFFICIAL_HOSTS and parsed.scheme != 'https':
        raise ValueError('官方 Pages host 必須用 https')
    return base_url.rstrip('/') + '/'


def _load_gen_metadata():
    spec = importlib.util.spec_from_file_location(
        'aircon_gen_metadata', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hash_blobs(rel_paths, blobs):
    """同 gen-metadata.hash_files 完全一樣嘅 framing，但對記憶體 bytes 計。"""
    gen = _load_gen_metadata()
    h = hashlib.sha256()
    for rel in sorted(rel_paths):
        gen._frame(h, rel, blobs[rel])
    return 'sha256:' + h.hexdigest()


def hkt_display(utc_z):
    """UTC Z → 頁面顯示格式（+8h、分鐘精度）；格式唔啱回 None。"""
    if not isinstance(utc_z, str) or not utc_z.endswith('Z'):
        return None
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$', utc_z):
        return None
    from calendar import timegm
    tup = time.strptime(utc_z[:19], '%Y-%m-%dT%H:%M:%S')
    epoch = timegm(tup) + 8 * 3600
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime(epoch))


def quote_path(path):
    return urllib.parse.quote(path, safe='/')


def http_get(base, path, timeout=60, retries=3, cache_key='', opener=None):
    """GET（no-cache）＋有界重試；只回最後一次結果，唔會 fallback 去其他版本路徑。"""
    url = base.rstrip('/') + '/' + quote_path(path.lstrip('/'))
    sep = '&' if '?' in url else '?'
    cb = f'cb={urllib.parse.quote(cache_key or str(int(time.time())))}-{int(time.time() * 1000) % 100000}'
    last_error = None
    for attempt in range(1, max(1, retries) + 1):
        req = urllib.request.Request(
            f'{url}{sep}{cb}&attempt={attempt}',
            headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
                     'User-Agent': 'aircon-compare-postdeploy/1.0'})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            last_error = f'HTTP {e.code} {url}'
        except Exception as e:  # noqa: BLE001 - 網絡層統一轉成可讀錯誤
            last_error = f'{type(e).__name__}: {e}'
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f'GET 失敗（{retries} 次）：{last_error}')


def check(name, cond, detail=None):
    entry = {'check': name, 'pass': bool(cond)}
    if detail is not None:
        entry['detail'] = detail if isinstance(detail, (str, int, float)) else str(detail)
    print(('PASS ' if cond else 'FAIL ') + name + (f' :: {detail}' if detail is not None else ''))
    return entry


def browser_checks(base, expected, report, timeout):
    """核心瀏覽器行為＋runtime 顯示核對（Playwright；行為證據）。"""
    from playwright.sync_api import sync_playwright
    checks = []
    console_errors, page_errors, failed, bad = [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = ctx.new_page()
        page.on('console', lambda m: console_errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
        page.on('pageerror', lambda exc: page_errors.append(str(exc)))
        page.on('requestfailed', lambda r: failed.append(f'{r.method} {r.url} {r.failure}'))
        page.on('response', lambda r: bad.append(f'{r.status} {r.url}') if r.status >= 400 else None)
        resp = page.goto(base + 'index.html?cb=postdeploy', wait_until='networkidle', timeout=timeout * 1000)
        checks.append(check('browser.index_200', bool(resp) and resp.status == 200,
                            resp.status if resp else None))
        page.wait_for_timeout(500)
        version = str(expected.get('version', ''))
        ver_text = page.text_content('#verInfo')
        checks.append(check('browser.version_from_metadata', ver_text == f'v{version}',
                            {'shown': ver_text, 'expected': f'v{version}'}))
        info = page.text_content('#deployInfo') or ''
        hkt = hkt_display(expected.get('deployTime'))
        checks.append(check('browser.last_deploy_hkt', bool(hkt) and hkt in info,
                            {'shown': info, 'expected_hkt': hkt}))
        checks.append(check('browser.dataset_date', str(expected.get('datasetDate')) in info,
                            expected.get('datasetDate')))
        # 核心路徑：搜尋
        page.fill('#q', '日立')
        page.evaluate('resetShown();renderList()')
        names = page.evaluate(
            "() => [...document.querySelectorAll('.mitem .info .name')].map(e => e.textContent.trim())")
        checks.append(check('browser.search_non_empty', bool(names), len(names)))
        checks.append(check('browser.search_brand_match',
                            bool(names) and all(('日立' in n or 'HITACHI' in n) for n in names),
                            names[:3]))
        page.fill('#q', '')
        # 核心路徑：篩選 + 排序
        page.select_option('#fBrand', 'Gree 格力')
        page.evaluate('resetShown();renderList()')
        gree = page.evaluate(
            "() => [...document.querySelectorAll('.mitem .info .name')].map(e => e.textContent.trim())")
        checks.append(check('browser.filter_brand', bool(gree) and all('Gree' in n for n in gree), gree[:3]))
        page.select_option('#fBrand', '')
        page.select_option('#sortBy', 'price')
        page.evaluate('resetShown();renderList()')
        prices = page.evaluate(
            "() => [...document.querySelectorAll('.mitem .plink')].map(e => {"
            "const m = e.textContent.match(/\\$([\\d,]+)/);"
            "return m ? parseInt(m[1].replace(/,/g,''), 10) : 999999; })")
        checks.append(check('browser.sort_price', prices == sorted(prices), prices[:5]))
        # 未知價型號唔可以當成任何價位（回歸 slice(0,-4) 脆弱提取）
        price_bad = page.evaluate('''() => {
          const bad = [];
          const ranges = {'2以下':[0,2000],'2-3':[2000,3000],'3-4':[3000,4000],'4-5':[4000,5000],'5以上':[5000,Infinity]};
          for (const v of Object.keys(ranges)) {
            document.getElementById('fPrice').value = v;
            for (const m of ALL) {
              if (!matches(m)) continue;
              const mm = String(m.price || '').match(/\\$([\\d,]+)/);
              if (!mm) { bad.push([v, m.brand, m.model, 'no-price']); continue; }
              const p = parseInt(mm[1].replace(/,/g,''));
              const [lo, hi] = ranges[v];
              if (p < lo || p >= hi) bad.push([v, m.brand, m.model, p]);
            }
          }
          document.getElementById('fPrice').value = '';
          return bad;
        }''')
        checks.append(check('browser.filter_price_unknown_excluded', price_bad == [], price_bad[:3]))
        page.select_option('#sortBy', '')
        page.evaluate('resetShown();renderList()')
        # 核心路徑：比較面板
        page.evaluate('''() => { clearAll(); resetShown(); renderList();
          const ins=[...document.querySelectorAll('.mitem input')]; ins[0].click(); ins[1].click();
          openCompare(); }''')
        page.wait_for_timeout(250)
        opened = page.evaluate("() => document.querySelector('.panel').classList.contains('open')")
        rows = page.evaluate("() => document.querySelectorAll('.panel table tr').length")
        checks.append(check('browser.compare_modal', opened and rows >= 3, {'open': opened, 'rows': rows}))
        # 鍵盤：Escape 關閉面板
        page.keyboard.press('Escape')
        page.wait_for_timeout(150)
        closed = not page.evaluate("() => document.querySelector('.panel').classList.contains('open')")
        checks.append(check('browser.compare_escape_keyboard', closed))
        # 響應式：多斷點無水平溢出
        overflow = []
        for w, h in ((320, 568), (375, 667), (768, 1024), (1280, 900)):
            page.set_viewport_size({'width': w, 'height': h})
            page.wait_for_timeout(80)
            if page.evaluate('() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1'):
                overflow.append(w)
        page.set_viewport_size({'width': 1280, 'height': 900})
        checks.append(check('browser.responsive_no_overflow', not overflow, overflow))
        checks.append(check('browser.no_console_errors', not console_errors and not page_errors, console_errors[:3] + page_errors[:3]))
        ignored = ('/favicon.ico',)
        real_bad = [b for b in bad if not any(i in b for i in ignored)]
        real_failed = [f for f in failed if not any(i in f for i in ignored)]
        checks.append(check('browser.no_failed_requests', not real_failed and not real_bad,
                            (real_failed[:3] + real_bad[:3])))
        ctx.close()
        browser.close()
    report['browser'] = checks
    return checks


def run(args):
    failures = []
    report = {'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'baseUrl': args.base_url, 'checks': [], 'failures': [], 'browser': []}

    def rec(entry):
        report['checks'].append(entry)
        if not entry.get('pass'):
            failures.append(entry)

    def finish(status):
        report['failures'] = failures
        report['ok'] = not failures
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
            with open(args.report, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f'❌ 報告寫入失敗：{e}', file=sys.stderr)
            return 1
        print(f"\n{'✅ 部署後核對全部通過' if not failures else '❌ 部署後核對有 %d 項失敗' % len(failures)}"
              f"（報告：{args.report}）")
        return status

    # 0) early validation：expected／manifest／trust 無效即停，唔會再發任何 request
    if not os.path.exists(args.expected_metadata):
        rec(check('metadata.expected_present', False, args.expected_metadata))
        print(f'❌ 搵唔到 expected metadata：{args.expected_metadata}', file=sys.stderr)
        return finish(1)
    try:
        with open(args.expected_metadata, encoding='utf-8') as f:
            expected = json.load(f)
    except (OSError, ValueError) as e:
        rec(check('metadata.expected_parse', False, str(e)))
        return finish(1)
    if not isinstance(expected, dict):
        rec(check('metadata.expected_object', False, 'expected metadata 必須係 JSON object'))
        return finish(1)
    expected_errors = metadata_schema_errors(expected)
    rec(check('metadata.expected_schema_valid', not expected_errors, expected_errors[:5]))
    if expected_errors:
        print('❌ expected metadata 唔過完整 Schema，停止後續 request', file=sys.stderr)
        return finish(1)
    try:
        with open(args.manifest, encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        rec(check('manifest.parse', False, str(e)))
        return finish(1)
    files = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        rec(check('manifest.files', False, 'manifest 必須有非空 files 陣列'))
        return finish(1)
    gen = _load_gen_metadata()
    try:
        files = gen.normalize_manifest_paths(files)
    except ValueError as e:
        rec(check('manifest.paths', False, str(e)))
        return finish(1)
    rec(check('manifest.paths', True, f'{len(files)} 個相對路徑'))
    try:
        base = ensure_trusted_base(args.base_url, args.allow_host)
    except ValueError as e:
        rec(check('trusted_base', False, str(e)))
        return finish(1)
    cache_key = expected.get('commit', '')[:12]

    # 1) 線上 metadata：完整 object + schema 一致才結束重試；否則有界重試後失敗
    online = None
    last_error = None
    attempts = max(1, args.retries)
    matched = False
    for attempt in range(attempts):
        try:
            status, body, _ = http_get(base, 'metadata.json', timeout=args.timeout,
                                       retries=1, cache_key=f'{cache_key}-v{attempt}')
        except Exception as e:  # noqa: BLE001
            last_error = e
            online = None
        else:
            try:
                online = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                online = None
        if isinstance(online, dict) and online == expected \
                and not metadata_schema_errors(online):
            rec(check('metadata.http_200', status == 200, status))
            rec(check('metadata.full_object_equal', True))
            matched = True
            break
        if attempt + 1 < attempts:
            time.sleep(min(2 ** (attempt + 1), 8))
    if online is None:
        rec(check('metadata.fetch', False, last_error or 'no valid metadata'))
    elif not isinstance(online, dict):
        rec(check('metadata.json_parse', False, '線上 metadata 唔係 object'))
        online = None
    else:
        if not matched:
            rec(check('metadata.http_200', True, 'retried'))
        online_errors = metadata_schema_errors(online)
        rec(check('metadata.online_schema_valid', not online_errors, online_errors[:5]))
        for field in REQUIRED_COMPARE_FIELDS:
            rec(check(f'metadata.{field}', online.get(field) == expected.get(field),
                      {'online': online.get(field), 'expected': expected.get(field)}))
        if not matched:
            rec(check('metadata.full_object_equal', online == expected,
                      {'diffKeys': metadata_object_diff(expected, online)[:10]}))

    # 2) payload 檔案 + CSV hash + 完整 payload hash + PDF 同 metadata 同一事實
    blobs = {}
    for rel in files:
        try:
            status, blob, ctype = http_get(base, rel, timeout=args.timeout,
                                           retries=args.retries, cache_key=cache_key)
            rec(check(f'payload.http.{rel}', status == 200, status))
            blobs[rel] = blob
        except Exception as e:  # noqa: BLE001
            rec(check(f'payload.http.{rel}', False, e))
    if blobs:
        try:
            payload_hash = hash_blobs(list(blobs), blobs)
            rec(check('payload.releasePayloadHash', payload_hash == expected.get('releasePayloadHash'),
                      {'computed': payload_hash, 'expected': expected.get('releasePayloadHash')}))
        except Exception as e:  # noqa: BLE001
            rec(check('payload.releasePayloadHash', False, e))
    csv_rel = next((f for f in files if f.endswith('.csv')), None)
    if csv_rel and csv_rel in blobs:
        csv_hash = 'sha256:' + hashlib.sha256(blobs[csv_rel]).hexdigest()
        rec(check('payload.csv_datasetHash', csv_hash == expected.get('datasetHash'),
                  {'csv': csv_hash, 'expected': expected.get('datasetHash')}))
        if isinstance(online, dict):
            rec(check('payload.csv_online_metadata_hash',
                      csv_hash == online.get('datasetHash'), online.get('datasetHash')))
    pdf_rel = next((f for f in files if f.endswith('.pdf')), None)
    if pdf_rel and pdf_rel in blobs:
        rec(check('payload.pdf_magic', blobs[pdf_rel].startswith(b'%PDF'),
                  blobs[pdf_rel][:8].decode('latin1')))
        if not args.no_pdf_repro:
            try:
                sys.path.insert(0, BASE)
                import generate_pdf  # noqa: PLC0415
                with tempfile.TemporaryDirectory() as td:
                    out = os.path.join(td, 'expected.pdf')
                    generate_pdf.build_pdf(out, metadata_path=args.expected_metadata)
                    expected_hash = hashlib.sha256(open(out, 'rb').read()).hexdigest()
                got_hash = hashlib.sha256(blobs[pdf_rel]).hexdigest()
                rec(check('payload.pdf_matches_metadata', got_hash == expected_hash,
                          {'online': got_hash[:16], 'rebuilt': expected_hash[:16]}))
            except Exception as e:  # noqa: BLE001
                rec(check('payload.pdf_matches_metadata', False, f'{type(e).__name__}: {e}'))
    if args.payload_dir:
        for rel, blob in blobs.items():
            local = os.path.join(args.payload_dir, rel)
            try:
                with open(local, 'rb') as f:
                    same = f.read() == blob
                rec(check(f'payload.bytes.{rel}', same, local))
            except OSError as e:
                rec(check(f'payload.bytes.{rel}', False, e))

    # 3) 瀏覽器行為
    if not args.no_browser:
        try:
            for entry in browser_checks(base, expected, report, args.timeout):
                if not entry.get('pass'):
                    failures.append(entry)
        except Exception as e:  # noqa: BLE001
            rec(check('browser.launch', False, f'{type(e).__name__}: {e}'))
    return finish(0 if not failures else 1)


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='部署後核對（GATE-08）')
    ap.add_argument('--expected-metadata', default=os.path.join(BASE, 'metadata.json'),
                    help='expected 部署 metadata（指定發佈 commit 嘅檔案）')
    ap.add_argument('--manifest', default=os.path.join(BASE, 'deploy_payload.json'),
                    help='payload manifest（releasePayloadHash 範圍）')
    ap.add_argument('--base-url', default=DEFAULT_BASE_URL,
                    help=f'線上 base URL（預設官方 Pages：{DEFAULT_BASE_URL}）')
    ap.add_argument('--allow-host', action='append', default=[],
                    help='額外允許 host（可重複；預設只准官方 Pages／localhost）')
    ap.add_argument('--payload-dir', default=None, help='同時逐 bytes 比對本地檔案')
    ap.add_argument('--report', default=os.path.join(tempfile.gettempdir(), 'aircon-postdeploy.json'))
    ap.add_argument('--retries', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=60)
    ap.add_argument('--no-browser', action='store_true',
                    help='跳過瀏覽器（只建議喺無 Playwright 環境做 HTTP 部分；唔係完整 GATE-08）')
    ap.add_argument('--no-pdf-repro', action='store_true',
                    help='跳過「線上 PDF bytes 必須等於用 expected metadata 重建」檢查（預設開啟）')
    args = ap.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
