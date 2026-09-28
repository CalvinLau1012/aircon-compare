#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EMSD 雙來源治理（approved design B）。

主來源：
  - 官方 open-data CSV：https://www.emsd.gov.hk/energylabel/files/meels_rac.csv
    經確定性 header 映射／欄位正規化之後，才可成為寫入生產 CSV 嘅 primary 資料。

獨立核對來源：
  - 現有逐頁 energy-label 結果（fetch_emsd.py 嘅分頁抓取）；
    兩者必須註冊編號集合、品牌／canonical 型號、受治理欄位、登記／型號計數全部一致。

Fail-closed 契約：
  - 任一來源缺失／不完整／Schema 唔明 → DualSourceError；
  - 受治理資料唔一致 → DualSourceError(kind='mismatch')，附可供審計嘅脫敏 diff；
  - 呼叫方（fetch_emsd.py）只會喺兩個來源都完整且一致時，才提交生產 CSV／收據。

安全契約：
  - 只用明確 User-Agent、有限 retry／timeout；403／429 即失敗，唔硬碰；
  - diff report 只含計數、欄位名、有限樣本註冊編號，唔含 raw bytes、token 或私人 URL；
  - 本模組唔會自行寫任何生產檔案。
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from crawl_utils import canonical_model_key, norm_model  # noqa: E402

CSV_URL = 'https://www.emsd.gov.hk/energylabel/files/meels_rac.csv'
PAGINATED_URL = ('https://www.emsd.gov.hk/energylabel/tc/households/rac/'
                 'select_ac_result.php?type=all&searchR=50')

SOURCE_KIND_CSV = 'emsd-open-data-csv'
SOURCE_KIND_PAGINATED = 'emsd-energy-label-paginated'

# 標準 15 欄（與受治理生產 CSV 一致；順序係 canonical order）
CANONICAL_FIELDS = (
    'brand', 'model', 'registrationNo', 'year', 'coolingGrade',
    'coolingAnnualKwh', 'coolingCapacityKw', 'cspf', 'refrigerant',
    'heatingGrade', 'heatingAnnualKwh', 'heatingCapacityKw', 'hspf',
    'provider', 'inverter',
)
CANONICAL_HEADER = (
    '品牌', '型號', '參考編號', '年份 (*)', '能源效益級別(製冷)(1 至 5)',
    '每年耗電量(製冷)(千瓦小時)', '製冷量(千瓦)',
    '製冷季節性表現系數 (CSPF)', '製冷劑',
    '能源效益級別(供暖)(1 至 5)', '每年耗電量(供暖)(千瓦小時)', '供暖量(千瓦)',
    '供暖季節性表現系數 (HSPF)', '資料提供者', '變頻',
)

# 受治理欄位 = 全部 15 欄（包括 key 欄；key 集合另外單獨比對）
GOVERNED_FIELDS = CANONICAL_FIELDS

# 數值欄位：兩來源嘅寫法（例如 6 / 6.0 / 6.00）唔應該造成假 mismatch
NUMERIC_FIELDS = frozenset({
    'year', 'coolingGrade', 'coolingAnnualKwh', 'coolingCapacityKw', 'cspf',
    'heatingGrade', 'heatingAnnualKwh', 'heatingCapacityKw', 'hspf',
})

# 空值等價標記（兩來源對「冇資料」嘅寫法可能唔同）
MISSING_MARKERS = frozenset({'', '-', '--', 'n/a', 'na', 'nil', 'none', '不適用', '待查'})

DEFAULT_USER_AGENT = ('aircon-compare-emsd-dual-source/1.0 '
                      '(project: aircon-compare; official EMSD open data comparison)')
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 3


class DualSourceError(RuntimeError):
    """雙來源缺失／Schema 唔明／受治理資料唔一致（一律 fail-closed）。"""

    def __init__(self, message, *, kind='unknown', diff=None):
        super().__init__(message)
        self.kind = kind
        self.diff = diff or {}


class SourceSchemaError(DualSourceError):
    def __init__(self, message, diff=None):
        super().__init__(message, kind='schema', diff=diff)


@dataclasses.dataclass
class RawResponse:
    """一次 HTTP GET 嘅實際證據（body 唔可以寫入公開 receipt）。"""

    url: str
    status: int
    body: bytes
    headers: dict
    fetchedAt: str
    notModified: bool = False


def utc_now_iso(now=None):
    ts = now if now is not None else datetime.now(timezone.utc)
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, timezone.utc)
    return ts.strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def normalize_header_key(name):
    """Header → 穩定比對 key：NFKC、細寫、去空白／括號／標點。"""
    text = unicodedata.normalize('NFKC', str(name or ''))
    text = re.sub(r'[\s()（）\[\]【】{}<>《》.*:：;；,，、/\\_\-—–]+', '', text)
    return text.lower()


def normalize_value(field, value):
    """單一欄位確定性正規化：NFKC、收斂空白、空值標記、數值 canonical form。"""
    text = unicodedata.normalize('NFKC', str('' if value is None else value))
    text = re.sub(r'\s+', ' ', text).strip()
    if text.lower() in MISSING_MARKERS or text in MISSING_MARKERS:
        return ''
    if field in NUMERIC_FIELDS:
        try:
            num = float(text)
        except (TypeError, ValueError):
            return text
        if num != num or num in (float('inf'), float('-inf')):  # NaN／inf
            return text
        if num == int(num):
            return str(int(num))
        return format(num, '.12g')
    return text


def normalize_row(row):
    """Canonical 15 欄 row → 比對用 normalized tuple。"""
    values = list(row) + [''] * (len(CANONICAL_FIELDS) - len(row))
    return tuple(normalize_value(f, v) for f, v in zip(CANONICAL_FIELDS, values[:15]))


def model_key_of(row):
    return canonical_model_key(row[0], row[1])


def _csv_field_aliases():
    """Canonical field → 可接受 header key 集合（中文 tracked header + 英文別名）。"""
    return {
        'brand': {'品牌', 'brand'},
        'model': {'型號', 'model', 'modelno', 'modelnumber'},
        'registrationNo': {'參考編號', '參考號碼', 'registrationno', 'registrationnumber',
                           'referenceno', 'referencenumber', 'refno', 'refnumber'},
        'year': {'年份', 'year', 'yearofregistration', 'registrationyear'},
        'coolingGrade': {'能源效益級別製冷1至5', '能源效益級別製冷',
                         'energyefficiencygradecooling', 'energyefficiencygradecooling1to5',
                         'coolingenergyefficiencygrade', 'coolinggrade'},
        'coolingAnnualKwh': {'每年耗電量製冷千瓦小時', '每年耗電量製冷',
                             'annualenergyconsumptioncoolingkwh',
                             'annualenergyconsumptioncooling',
                             'coolingannualenergyconsumptionkwh',
                             'coolingannualenergyconsumption'},
        'coolingCapacityKw': {'製冷量千瓦', 'coolingcapacitykw', 'coolingcapacity'},
        'cspf': {'製冷季節性表現系數cspf', '製冷季節性表現系數', 'cspf',
                 'coolingseasonalperformancefactor'},
        'refrigerant': {'製冷劑', 'refrigerant'},
        'heatingGrade': {'能源效益級別供暖1至5', '能源效益級別供暖',
                         'energyefficiencygradeheating', 'energyefficiencygradeheating1to5',
                         'heatingenergyefficiencygrade', 'heatinggrade'},
        'heatingAnnualKwh': {'每年耗電量供暖千瓦小時', '每年耗電量供暖',
                             'annualenergyconsumptionheatingkwh',
                             'annualenergyconsumptionheating',
                             'heatingannualenergyconsumptionkwh',
                             'heatingannualenergyconsumption'},
        'heatingCapacityKw': {'供暖量千瓦', 'heatingcapacitykw', 'heatingcapacity'},
        'hspf': {'供暖季節性表現系數hspf', '供暖季節性表現系數', 'hspf',
                 'heatingseasonalperformancefactor'},
        'provider': {'資料提供者', 'dataprovider', 'supplier', 'provider'},
        'inverter': {'變頻', 'inverter', 'invertertype'},
    }


def map_csv_headers(header):
    """Header row → {field: index}；缺欄／重複 mapping 即 SourceSchemaError。"""
    aliases = _csv_field_aliases()
    normalized = [normalize_header_key(h) for h in header]
    mapping = {}
    duplicate = {}
    for field, names in aliases.items():
        matches = [i for i, key in enumerate(normalized) if key in names]
        if not matches:
            continue
        if len(matches) > 1:
            duplicate[field] = matches
        mapping[field] = matches[0]
    missing = [f for f in CANONICAL_FIELDS if f not in mapping]
    if missing or duplicate:
        raise SourceSchemaError(
            'CSV header 唔符合受治理欄位契約：missing=%s duplicate=%s'
            % (missing, sorted(duplicate)),
            diff={'missing': missing, 'duplicate': sorted(duplicate)})
    return mapping


def _decode_csv(raw):
    for encoding in ('utf-8-sig', 'big5', 'cp950'):
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise SourceSchemaError('CSV 唔可以用 utf-8-sig／big5／cp950 解碼')


def parse_open_data_csv(raw):
    """官方 open-data CSV bytes → canonical 15 欄 rows（未 normalize，保留原字串）。

    回傳 dict：{'rows', 'header', 'encoding', 'rowCount'}。
    """
    if not isinstance(raw, (bytes, bytearray)) or not bytes(raw).strip():
        raise SourceSchemaError('CSV 係空 bytes', diff={'byteLength': len(raw or b'')})
    text, encoding = _decode_csv(bytes(raw))
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise SourceSchemaError('CSV 冇 header row')
    if not any(str(h).strip() for h in header):
        raise SourceSchemaError('CSV header 係空')
    mapping = map_csv_headers(header)
    rows = []
    for line_no, row in enumerate(reader, start=2):
        if not any(str(c).strip() for c in row):
            continue
        if len(row) < len(header):
            raise SourceSchemaError(
                f'CSV 第 {line_no} 行少過 header 欄數（{len(row)} < {len(header)}）',
                diff={'line': line_no, 'cells': len(row), 'headerCells': len(header)})
        rows.append(tuple(str(row[mapping[f]]).strip() for f in CANONICAL_FIELDS))
    return {'rows': rows, 'header': list(header), 'encoding': encoding,
            'rowCount': len(rows)}


def _index_rows(rows):
    """canonical rows → {registration key: [normalized row, ...]}（keys 以小寫原文為準）。"""
    index = {}
    for row in rows:
        key = normalize_value('registrationNo', row[2])
        index.setdefault(key, []).append(normalize_row(row))
    for key in index:
        index[key].sort()
    return index


def compare_sources(csv_rows, paginated_rows, *, sample_limit=20):
    """確定性比較兩個來源；回傳同構結果（唔 raise）。

    - registration key 多重集合相等；
    - 每個 key 嘅受治理欄位 tuple 完全相等（數值已 canonical 化）；
    - 登記數、unique key 數、canonical 型號數相等。
    """
    csv_index = _index_rows(csv_rows)
    pag_index = _index_rows(paginated_rows)
    csv_keys = set(csv_index)
    pag_keys = set(pag_index)
    missing_in_csv = sorted(pag_keys - csv_keys)
    missing_in_paginated = sorted(csv_keys - pag_keys)
    mismatched = []
    field_counts = {f: 0 for f in GOVERNED_FIELDS}
    for key in sorted(csv_keys & pag_keys):
        for left, right in zip(csv_index[key], pag_index[key]):
            if left != right:
                mismatched.append(key)
                for i, field in enumerate(GOVERNED_FIELDS):
                    if left[i] != right[i]:
                        field_counts[field] += 1
                break
    def model_count(rows):
        return len({model_key_of(r) for r in rows if norm_model(r[1])})
    counts = {
        'csv': {'registrationCount': len(csv_rows), 'uniqueRegistrationKeys': len(csv_keys),
                'modelCount': model_count(csv_rows)},
        'paginated': {'registrationCount': len(paginated_rows),
                      'uniqueRegistrationKeys': len(pag_keys),
                      'modelCount': model_count(paginated_rows)},
    }
    equal = (not missing_in_csv and not missing_in_paginated and not mismatched
             and counts['csv'] == counts['paginated'])
    return {
        'equal': equal,
        'counts': counts,
        'missingInCsv': {'count': len(missing_in_csv), 'samples': missing_in_csv[:sample_limit]},
        'missingInPaginated': {'count': len(missing_in_paginated),
                               'samples': missing_in_paginated[:sample_limit]},
        'mismatchedKeys': {'count': len(mismatched), 'samples': mismatched[:sample_limit]},
        'fieldMismatchCounts': {f: n for f, n in field_counts.items() if n},
    }


def build_diff_report(*, kind, generated_at, csv_summary, paginated_summary, comparison,
                      error=None, sample_limit=20):
    """脫敏 diff report：只有計數、欄位名、有限樣本 key（公開 EMSD 資料）。"""
    report = {
        'schemaVersion': 1,
        'kind': kind,
        'generatedAt': generated_at,
        'sources': {'csv': csv_summary, 'paginated': paginated_summary},
        'comparison': comparison,
    }
    if error:
        report['error'] = str(error)[:500]
    return report


def write_diff_report(path, report):
    """原子寫 diff report（呼叫方負責路徑喺 repo 外）。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def default_diff_report_path(now=None):
    stamp = utc_now_iso(now).replace(':', '').replace('-', '')
    base = os.environ.get('AIRCON_EMSD_DIFF_DIR') or tempfile.gettempdir()
    return os.path.join(base, f'aircon-emsd-diff-{stamp}.json')


def http_get(url, *, headers=None, timeout=DEFAULT_TIMEOUT, now=None):
    """真實 HTTP GET（唯一網絡入口；測試用 transport 注入，不會在 import 時呼叫）。"""
    req = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, 'status', 200) or 200
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        raise DualSourceError(f'HTTP {e.code}（來源拒絕，停止硬碰）', kind='http_error',
                              diff={'status': e.code, 'url': url}) from e
    except Exception as e:  # noqa: BLE001 - 網絡層一律 fail-closed
        raise DualSourceError(f'網絡錯誤：{type(e).__name__}', kind='network_error',
                              diff={'url': url}) from e
    return RawResponse(url=url, status=status, body=body, headers=resp_headers,
                       fetchedAt=utc_now_iso(now))


def fetch_csv_raw(*, transport=None, now=None, conditional=None,
                  max_attempts=DEFAULT_MAX_ATTEMPTS, timeout=DEFAULT_TIMEOUT):
    """抓官方 open-data CSV：明確 UA、有限 retry（只重試網絡／5xx）、403／429 即失敗。"""
    transport = transport or http_get
    headers = {'User-Agent': DEFAULT_USER_AGENT, 'Accept': 'text/csv,*/*;q=0.8',
               'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5'}
    if conditional:
        if conditional.get('etag'):
            headers['If-None-Match'] = str(conditional['etag'])
        if conditional.get('lastModified'):
            headers['If-Modified-Since'] = str(conditional['lastModified'])
    last_error = None
    for attempt in range(max(1, max_attempts)):
        try:
            resp = transport(CSV_URL, headers=headers, timeout=timeout, now=now)
        except DualSourceError as e:
            last_error = e
            if e.kind in ('http_error',) or attempt + 1 >= max_attempts:
                raise
            time.sleep(min(2 ** attempt, 8))
            continue
        if resp.status == 304:
            return dataclasses.replace(resp, notModified=True, body=b'')
        if resp.status == 200:
            if not resp.body or not resp.body.strip():
                raise DualSourceError('CSV 回應係空 bytes', kind='csv_incomplete',
                                      diff={'status': resp.status, 'byteLength': len(resp.body)})
            return resp
        if resp.status in (403, 429):
            raise DualSourceError(f'CSV 返回 HTTP {resp.status}（被限流），停止硬碰',
                                  kind='http_error', diff={'status': resp.status})
        last_error = DualSourceError(f'CSV 返回 HTTP {resp.status}', kind='http_error',
                                     diff={'status': resp.status})
        if attempt + 1 >= max_attempts:
            raise last_error
        time.sleep(min(2 ** attempt, 8))
    raise last_error or DualSourceError('CSV 抓取失敗', kind='network_error')


def csv_summary(resp, parsed, *, min_rows=None):
    """CSV 來源嘅脫敏摘要（唔含 body）。"""
    summary = {
        'sourceKind': SOURCE_KIND_CSV,
        'sourceUrl': CSV_URL,
        'status': resp.status,
        'byteLength': len(resp.body),
        'sha256': 'sha256:' + sha256_hex(resp.body),
        'fetchedAt': resp.fetchedAt,
        'lastModified': resp.headers.get('Last-Modified'),
        'etag': resp.headers.get('ETag'),
        'encoding': parsed.get('encoding'),
        'rowCount': parsed.get('rowCount'),
    }
    if min_rows is not None:
        summary['minRowsGate'] = min_rows
    return summary


def build_receipt_dual_source(*, comparison, csv_summary_, paginated_summary_,
                              csv_archive=None, paginated_archive=None,
                              retrieved_at=None):
    """主要收據用嘅 dualSource block（計數 + 兩個來源摘要 + raw archive namespace）。"""
    return {
        'schemaVersion': 1,
        'primary': SOURCE_KIND_CSV,
        'crossCheck': SOURCE_KIND_PAGINATED,
        'equal': comparison['equal'],
        'retrievedAt': retrieved_at,
        'sources': {
            SOURCE_KIND_CSV: dict(csv_summary_, archive=csv_archive or {}),
            SOURCE_KIND_PAGINATED: dict(paginated_summary_, archive=paginated_archive or {}),
        },
        'counts': comparison['counts'],
    }


def verify_dual_source(*, paginated_rows, paginated_raw_records, retrieved_at,
                       transport=None, now=None, min_rows=1700,
                       paginated_archive_hash=None, csv_transport=None,
                       conditional=None, raw_response=None):
    """主入口：抓 CSV、parse、min-row gate、比較；成功回 primary rows + 證據。

    `raw_response` 供呼叫方／測試注入（fetch_emsd 用可 monkeypatch 嘅 wrapper
    事先抓好）；唔傳就經 `fetch_csv_raw` 抓。

    任何失敗 raise DualSourceError（kind: network_error／http_error／schema／
    csv_incomplete／paginated_incomplete／mismatch），diff 可供呼叫方寫脫敏 report。
    """
    if not paginated_rows:
        raise DualSourceError('paginated 來源冇資料，唔可以單獨放行 CSV',
                              kind='paginated_incomplete', diff={'totalRows': 0})
    if not paginated_raw_records:
        raise DualSourceError('paginated 來源冇 raw page 證據', kind='paginated_incomplete')
    resp = raw_response or fetch_csv_raw(transport=csv_transport or transport, now=now,
                                         conditional=conditional)
    if not resp.body or not resp.body.strip():
        raise DualSourceError('CSV 回應係空 bytes', kind='csv_incomplete',
                              diff={'status': resp.status, 'byteLength': len(resp.body)})
    parsed = parse_open_data_csv(resp.body)
    if parsed['rowCount'] < min_rows:
        raise DualSourceError(
            f"CSV 只攞到 {parsed['rowCount']} 行，少於安全下限 {min_rows}",
            kind='csv_incomplete',
            diff={'rowCount': parsed['rowCount'], 'minRows': min_rows})
    comparison = compare_sources(parsed['rows'], paginated_rows)
    csv_sum = csv_summary(resp, parsed, min_rows=min_rows)
    pag_sum = {
        'sourceKind': SOURCE_KIND_PAGINATED,
        'sourceUrl': PAGINATED_URL,
        'pageCount': len(paginated_raw_records),
        'totalRows': len(paginated_rows),
        'archiveHash': paginated_archive_hash,
        'fetchedAt': retrieved_at,
    }
    if not comparison['equal']:
        raise DualSourceError('EMSD 雙來源受治理資料唔一致', kind='mismatch',
                              diff={'comparison': comparison, 'csv': csv_sum,
                                    'paginated': pag_sum})
    return {'rows': parsed['rows'], 'csv': csv_sum, 'paginated': pag_sum,
            'comparison': comparison, 'rawResponse': resp, 'parsed': parsed}
