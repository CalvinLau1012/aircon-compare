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
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from crawl_utils import canonical_model_key, norm_model  # noqa: E402

CSV_URL = 'https://www.emsd.gov.hk/energylabel/files/meels_rac.csv'
PAGINATED_URL = ('https://www.emsd.gov.hk/energylabel/tc/households/rac/'
                 'select_ac_result.php?type=all&searchR=50')

# DATA.GOV.HK CKAN 目錄（官方 API；dataset ID 公開且穩定；唔用 resource UUID 做選擇）
CATALOG_API_URL = ('https://data.gov.hk/en-data/api/3/action/package_show'
                   '?id=hk-emsd-emsd1-meels-listed-models')
CATALOG_DATASET_ID = 'hk-emsd-emsd1-meels-listed-models'
CATALOG_DATASET_PAGE = ('https://data.gov.hk/en-data/dataset/'
                        'hk-emsd-emsd1-meels-listed-models')
CATALOG_API_HOST = 'data.gov.hk'
CATALOG_RESOURCE_NAME = 'room air conditioners'
CATALOG_MAX_BYTES = 2 * 1024 * 1024
CSV_HOST_ALLOWLIST = frozenset({'www.emsd.gov.hk'})
CSV_PATH_RE = re.compile(r'^/energylabel/files/[A-Za-z0-9._-]+\.csv$')
HTML_CONTENT_TYPES = ('text/html', 'application/xhtml+xml')

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

# 真實 DATA.GOV.HK open-data CSV 觀測契約（2026-09-29 首次受信任 CI live；relay 覆核）：
# 29 欄、英文 header，並以「Product being Supplied by Information Provider」標示供應狀態。
# paginated 來源只列目前供應型號，所以只有 'Yes' 行屬現行受治理登記範圍。
CSV_SUPPLIED_KEY = 'productbeingsuppliedbyinformationprovider'
CSV_SUPPLIED_ALLOWED = ('Yes', 'No', 'No Information')
CSV_SUPPLIED_PRIMARY = 'Yes'

# CSV primary 值 → 受治理生產表示（同歷史 paginated 表示等價；未見過 token 即 fail-closed）
INVERTER_TRUE_TOKENS = frozenset({'Y', 'Yes', 'YES', 'yes', '是', 'True', 'true', '1'})
INVERTER_FALSE_TOKENS = frozenset({'N', 'No', 'NO', 'no', '否', 'False', 'false', '0'})
HEATING_MISSING_DEFAULT = '不適用'  # heatingGrade：冇供暖資料
HEATING_SENTINEL = '—'              # 供暖數值欄：官方 sentinel（validate_data 契約）
HEATING_NUMERIC_FIELDS = ('heatingAnnualKwh', 'heatingCapacityKw', 'hspf')

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


class CatalogUnavailable(DualSourceError):
    """CKAN 目錄暫時不可用（網絡／5xx／429）：允許 fallback 到上次批准嘅直接 URL。"""

    def __init__(self, message, diff=None):
        super().__init__(message, kind='catalog_unavailable', diff=diff)


class CatalogInvalid(DualSourceError):
    """CKAN 回應／資源身份／URL 唔符合契約：一律 fail-closed，冇 silent fallback。"""

    def __init__(self, message, kind='catalog_invalid', diff=None):
        super().__init__(message, kind=kind, diff=diff)


class CatalogAmbiguous(DualSourceError):
    """目錄冇唯一 active Room Air Conditioners CSV resource：fail-closed。"""

    def __init__(self, message, diff=None):
        super().__init__(message, kind='catalog_ambiguous', diff=diff)


@dataclasses.dataclass
class RawResponse:
    """一次 HTTP GET 嘅實際證據（body 唔可以寫入公開 receipt）。"""

    url: str
    status: int
    body: bytes
    headers: dict
    fetchedAt: str
    notModified: bool = False
    finalUrl: str = None


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
        'brand': {'品牌', 'brand', 'brandtraditionalchinese'},
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
                 'coolingseasonalperformancefactor',
                 'coolingseasonalperformancefactorcspf'},
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
                 'heatingseasonalperformancefactor',
                 'heatingseasonalperformancefactorhspf'},
        'provider': {'資料提供者', 'dataprovider', 'supplier', 'provider',
                     'informationprovidertraditionalchinese'},
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


def canonicalize_csv_values(values):
    """CSV primary row（canonical field dict）→ 受治理生產表示。

    - inverter：官方 CSV 用 Y／N；生產受治理表示係 是／否（`validate_data.py` 契約）。
      任何未見過嘅 token 即 `SourceSchemaError`（fail-closed；唔可以當定頻）。
    - 供暖欄：官方 CSV 用空字串表示冇供暖資料；生產表示沿用歷史 paginated 官方
      sentinel（`heatingGrade='不適用'`、數值欄='—'），兩者等價。
    """
    out = dict(values)
    inverter = out.get('inverter', '')
    if inverter in INVERTER_TRUE_TOKENS:
        out['inverter'] = '是'
    elif inverter in INVERTER_FALSE_TOKENS:
        out['inverter'] = '否'
    else:
        raise SourceSchemaError('CSV inverter 值唔在受治理集合',
                                diff={'field': 'inverter'})
    if out.get('heatingGrade', '') == '':
        out['heatingGrade'] = HEATING_MISSING_DEFAULT
    for field in HEATING_NUMERIC_FIELDS:
        if out.get(field, '') == '':
            out[field] = HEATING_SENTINEL
    return out


def parse_open_data_csv(raw):
    """官方 open-data CSV bytes → canonical 15 欄 rows（已轉受治理生產表示）。

    真實 CSV（2026-09-29 首次受信任 CI live 觀測）有 29 欄、英文 header，並以
    「Product being Supplied by Information Provider」標示供應狀態；只有 `Yes` 行屬
    現行可比對登記（與 paginated 來源範圍一致）。供應狀態欄缺失／重複、值未知、
    inverter token 未知一律 fail-closed。

    回傳 dict：{'rows', 'header', 'encoding', 'rowCount', 'excludedRowCount'}。
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
    normalized = [normalize_header_key(h) for h in header]
    supplied_matches = [i for i, key in enumerate(normalized) if key == CSV_SUPPLIED_KEY]
    if len(supplied_matches) != 1:
        raise SourceSchemaError(
            'CSV 供應狀態欄唔符合契約（必須唯一）',
            diff={'missing': ['csvSupplied'] if not supplied_matches else [],
                  'duplicate': ['csvSupplied'] if len(supplied_matches) > 1 else []})
    supplied_idx = supplied_matches[0]
    rows = []
    excluded = 0
    for line_no, row in enumerate(reader, start=2):
        if not any(str(c).strip() for c in row):
            continue
        if len(row) < len(header):
            raise SourceSchemaError(
                f'CSV 第 {line_no} 行少過 header 欄數（{len(row)} < {len(header)}）',
                diff={'line': line_no, 'cells': len(row), 'headerCells': len(header)})
        supplied = str(row[supplied_idx]).strip()
        if supplied not in CSV_SUPPLIED_ALLOWED:
            raise SourceSchemaError('CSV 供應狀態值唔在受治理集合',
                                    diff={'field': 'csvSupplied', 'line': line_no})
        if supplied != CSV_SUPPLIED_PRIMARY:
            excluded += 1
            continue
        values = {f: str(row[mapping[f]]).strip() for f in CANONICAL_FIELDS}
        values = canonicalize_csv_values(values)
        rows.append(tuple(values[f] for f in CANONICAL_FIELDS))
    return {'rows': rows, 'header': list(header), 'encoding': encoding,
            'rowCount': len(rows), 'excludedRowCount': excluded}


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


def http_get(url, *, headers=None, timeout=DEFAULT_TIMEOUT, now=None, validator=None):
    """真實 HTTP GET（唯一網絡入口；測試用 transport 注入，不會在 import 時呼叫）。

    `validator`（可選）：redirect policy。每個 redirect target 會喺**跟之前**先交
    俾 validator；唔合格即 raise DualSourceError，絕對唔會向未批准 host 發請求。
    同一 origin（或通過同一 allowlist 契約）嘅官方 redirect 仍然可以跟。
    """
    req = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        if validator is not None:
            opener = urllib.request.build_opener(_ValidatingRedirectHandler(validator))
            resp_ctx = opener.open(req, timeout=timeout)
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout)
        with resp_ctx as resp:
            body = resp.read()
            status = getattr(resp, 'status', 200) or 200
            resp_headers = dict(resp.headers.items())
            final_url = getattr(resp, 'geturl', lambda: url)() or url
    except DualSourceError:
        raise  # redirect validator／URL 契約錯誤：保留 fail-closed kind
    except urllib.error.HTTPError as e:
        raise DualSourceError(f'HTTP {e.code}（來源拒絕，停止硬碰）', kind='http_error',
                              diff={'status': e.code, 'url': url}) from e
    except Exception as e:  # noqa: BLE001 - 網絡層一律 fail-closed
        raise DualSourceError(f'網絡錯誤：{type(e).__name__}', kind='network_error',
                              diff={'url': url}) from e
    return RawResponse(url=url, status=status, body=body, headers=resp_headers,
                       fetchedAt=utc_now_iso(now), finalUrl=str(final_url))


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """redirect 前先驗證 target：唔合格就 raise，唔會跟過去（zero request）。"""

    def __init__(self, validator):
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_official_csv_url(url):
    """嚴格官方 CSV URL 契約：HTTPS、allowlist host、/energylabel/files/*.csv、
    冇 userinfo／非標準 port／query／fragment。違反即 CatalogInvalid（fail-closed）。"""
    try:
        parsed = urlsplit(str(url or ''))
    except ValueError:
        raise CatalogInvalid('CSV URL 唔可以解析', diff={'field': 'url'})
    if parsed.scheme != 'https':
        raise CatalogInvalid('CSV URL 必須係 https', diff={'scheme': parsed.scheme})
    if parsed.hostname not in CSV_HOST_ALLOWLIST:
        raise CatalogInvalid('CSV URL host 唔喺 allowlist', diff={'host': parsed.hostname})
    if parsed.username or parsed.password:
        raise CatalogInvalid('CSV URL 唔可以有 userinfo')
    if parsed.port is not None:
        raise CatalogInvalid('CSV URL 唔可以有非標準 port', diff={'port': parsed.port})
    if parsed.query or parsed.fragment:
        raise CatalogInvalid('CSV URL 唔可以有 query／fragment', diff={'query': bool(parsed.query)})
    if not CSV_PATH_RE.match(parsed.path or ''):
        raise CatalogInvalid('CSV URL path 唔喺批准／energylabel/files 範圍', diff={'path': parsed.path})
    return parsed


def validate_catalog_api_url(url):
    """CKAN package_show URL 完整身份：https、data.gov.hk、固定 path＋dataset query、
    無 userinfo／port／fragment。redirect 同 final URL 都要過呢個契約。"""
    try:
        parsed = urlsplit(str(url or ''))
    except ValueError:
        raise CatalogInvalid('CKAN URL 唔可以解析', diff={'field': 'url'})
    if parsed.scheme != 'https' or parsed.hostname != CATALOG_API_HOST:
        raise CatalogInvalid('CKAN URL scheme／host 唔符',
                             diff={'scheme': parsed.scheme, 'host': parsed.hostname})
    if parsed.username or parsed.password:
        raise CatalogInvalid('CKAN URL 唔可以有 userinfo')
    if parsed.port is not None:
        raise CatalogInvalid('CKAN URL 唔可以有非標準 port', diff={'port': parsed.port})
    if parsed.fragment:
        raise CatalogInvalid('CKAN URL 唔可以有 fragment')
    if parsed.path != '/en-data/api/3/action/package_show':
        raise CatalogInvalid('CKAN URL path 唔符', diff={'path': parsed.path})
    if parsed.query != 'id=' + CATALOG_DATASET_ID:
        raise CatalogInvalid('CKAN URL dataset query 唔符', diff={'query': parsed.query[:120]})
    return parsed


def _catalog_unavailable(e):
    """可 fallback（網絡／5xx／408／425／429）；其他 kind（schema／invalid／ambiguous）硬失敗。"""
    if e.kind not in ('network_error', 'http_error', 'catalog_unavailable'):
        return None
    status = (e.diff or {}).get('status')
    if isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
        return None
    return CatalogUnavailable('CKAN 目錄暫時不可用（%s）' % e.kind,
                              diff={'status': status})


def resolve_csv_resource(*, transport=None, now=None, max_attempts=DEFAULT_MAX_ATTEMPTS,
                         timeout=DEFAULT_TIMEOUT):
    """用 DATA.GOV.HK CKAN package_show 解析唯一 active Room Air Conditioners CSV resource。

    - dataset ID 固定 `hk-emsd-emsd1-meels-listed-models`，唔靠 resource UUID；
    - 必須 exactly 1 個 name／description = Room Air Conditioners、format CSV、state active、
      URL 通過官方 allowlist 嘅 resource；0／多過 1 即 CatalogAmbiguous；
    - 目錄暫時不可用 raise CatalogUnavailable（呼叫方才可 fallback 上次批准 URL）；
      目錄有回應但內容／身份／URL 唔合 → CatalogInvalid（冇 fallback）。
    """
    transport = transport or http_get
    validate_catalog_api_url(CATALOG_API_URL)
    headers = {'User-Agent': DEFAULT_USER_AGENT,
               'Accept': 'application/json,*/*;q=0.5'}
    last_error = None
    for attempt in range(max(1, max_attempts)):
        try:
            resp = transport(CATALOG_API_URL, headers=headers, timeout=timeout, now=now,
                             validator=validate_catalog_api_url)
        except DualSourceError as e:
            unavailable = _catalog_unavailable(e)
            if unavailable is None:
                raise CatalogInvalid(
                    f"CKAN 目錄硬拒絕（{e.kind}）", diff={'status': (e.diff or {}).get('status')})
            last_error = unavailable
            if attempt + 1 >= max_attempts:
                raise last_error
            time.sleep(min(2 ** attempt, 8))
            continue
        final = validate_catalog_api_url(str(resp.finalUrl or resp.url))
        if resp.status == 304:
            raise CatalogInvalid('CKAN 回 304（冇可用目錄 bytes，唔准用 cache）',
                                 diff={'status': 304})
        if resp.status != 200:
            probe = DualSourceError(f'CKAN 回 HTTP {resp.status}', kind='http_error',
                                    diff={'status': resp.status})
            unavailable = _catalog_unavailable(probe)
            if unavailable is None:
                raise CatalogInvalid(f'CKAN 目錄硬拒絕（HTTP {resp.status}）',
                                     diff={'status': resp.status})
            last_error = unavailable
            if attempt + 1 >= max_attempts:
                raise last_error
            time.sleep(min(2 ** attempt, 8))
            continue
        if len(resp.body) > CATALOG_MAX_BYTES:
            raise CatalogInvalid('CKAN 目錄回應過大', diff={'byteLength': len(resp.body)})
        content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        head = bytes(resp.body[:64]).lstrip().lower()
        if content_type in HTML_CONTENT_TYPES or head.startswith(b'<html') \
                or head.startswith(b'<!doctype'):
            raise CatalogInvalid('CKAN 回應係 HTML（唔可以當 JSON 目錄）',
                                 diff={'contentType': content_type})
        try:
            payload = json.loads(resp.body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as e:
            raise CatalogInvalid('CKAN 回應唔係有效 UTF-8 JSON',
                                 diff={'error': type(e).__name__})
        if not isinstance(payload, dict) or payload.get('success') is not True \
                or not isinstance(payload.get('result'), dict):
            raise CatalogInvalid('CKAN package_show success／result 契約唔符')
        result = payload['result']
        if result.get('name') != CATALOG_DATASET_ID:
            raise CatalogInvalid('CKAN dataset 身份唔符（name != 指定 dataset ID）',
                                 diff={'datasetName': str(result.get('name'))[:120]})
        # 2026-09-29 返修：provider 身份必須係 EMSD（實際 package_show shape 有
        # organization.name='hk-emsd'；缺失或唔同即 fail closed，唔准猜）。
        organization = result.get('organization')
        provider = organization.get('name') if isinstance(organization, dict) else None
        if provider != 'hk-emsd':
            raise CatalogInvalid('CKAN dataset provider 唔係 EMSD（hk-emsd）',
                                 diff={'provider': str(provider)[:80] if provider else None})
        resources = result.get('resources')
        if not isinstance(resources, list):
            raise CatalogInvalid('CKAN dataset 冇 resources array')
        matches = []
        for res in resources:
            if not isinstance(res, dict):
                continue
            name = ' '.join(str(res.get('name') or res.get('description') or '').split()).lower()
            if name != CATALOG_RESOURCE_NAME:
                continue
            if str(res.get('format') or '').strip().upper() != 'CSV':
                continue
            if str(res.get('state') or '').strip().lower() != 'active':
                continue
            validate_official_csv_url(res.get('url'))
            matches.append(res)
        if len(matches) != 1:
            raise CatalogAmbiguous(
                f'active Room Air Conditioners CSV resource 數目 = {len(matches)}（必須 exactly 1）',
                diff={'count': len(matches)})
        resource = matches[0]
        return {
            'schemaVersion': 1,
            'mode': 'catalog',
            'datasetId': CATALOG_DATASET_ID,
            'resourceId': str(resource.get('id')),
            'resourceName': ' '.join(str(resource.get('name') or '').split()),
            'resourceFormat': 'CSV',
            'catalogApiUrl': CATALOG_API_URL,
            'datasetPageUrl': CATALOG_DATASET_PAGE,
            'resolvedCsvUrl': str(resource.get('url')),
            'resolvedAt': utc_now_iso(now),
            'catalogFetchedAt': resp.fetchedAt,
        }
    raise last_error or CatalogUnavailable('CKAN 目錄抓取失敗')


def last_known_good_resolution(*, reason, now=None):
    """CKAN 暫時不可用時嘅上次批准直接 URL（仍然會重新抓新鮮 bytes，唔用 cache）。"""
    return {
        'schemaVersion': 1,
        'mode': 'last-known-good-fallback',
        'datasetId': CATALOG_DATASET_ID,
        'resourceId': None,
        'resourceName': None,
        'resourceFormat': None,
        'catalogApiUrl': CATALOG_API_URL,
        'datasetPageUrl': CATALOG_DATASET_PAGE,
        'resolvedCsvUrl': CSV_URL,
        'resolvedAt': utc_now_iso(now),
        'fallbackReason': str(reason),
    }


def fetch_csv_raw(*, url=None, transport=None, now=None, conditional=None,
                  max_attempts=DEFAULT_MAX_ATTEMPTS, timeout=DEFAULT_TIMEOUT):
    """抓官方 open-data CSV：明確 UA、有限 retry（只重試網絡／5xx）、403／429 即失敗。

    `url` 預設係上次批准嘅直接 URL；CKAN resolver 解析到新 URL 時傳入。無論
    request URL 定（redirect 後）final URL 都要通過官方 allowlist 契約。
    """
    transport = transport or http_get
    fetch_url = str(url or CSV_URL)
    validate_official_csv_url(fetch_url)
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
            resp = transport(fetch_url, headers=headers, timeout=timeout, now=now,
                             validator=validate_official_csv_url)
        except DualSourceError as e:
            last_error = e
            if e.kind in ('http_error',) or attempt + 1 >= max_attempts:
                raise
            time.sleep(min(2 ** attempt, 8))
            continue
        effective = str(resp.finalUrl or resp.url)
        validate_official_csv_url(effective)
        if resp.status == 304:
            return dataclasses.replace(resp, notModified=True, body=b'')
        if resp.status == 200:
            content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            head = bytes(resp.body[:64]).lstrip().lower()
            if content_type in HTML_CONTENT_TYPES or head.startswith(b'<html') \
                    or head.startswith(b'<!doctype'):
                raise DualSourceError('CSV 回應係 HTML（唔可以冒充資料）',
                                      kind='content_type', diff={'contentType': content_type})
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


def csv_summary(resp, parsed, *, min_rows=None, resolution=None):
    """CSV 來源嘅脫敏摘要（唔含 body）；包括 catalog／resolved URL 證據。"""
    resolved_url = (resolution or {}).get('resolvedCsvUrl') or resp.finalUrl or resp.url
    summary = {
        'sourceKind': SOURCE_KIND_CSV,
        'sourceUrl': resolved_url,
        'resolvedUrl': resolved_url,
        'status': resp.status,
        'notModified': bool(resp.notModified),
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
    if resolution:
        summary['catalog'] = {
            'mode': resolution.get('mode'),
            'datasetId': resolution.get('datasetId'),
            'resourceId': resolution.get('resourceId'),
            'resourceName': resolution.get('resourceName'),
            'catalogApiUrl': resolution.get('catalogApiUrl'),
            'datasetPageUrl': resolution.get('datasetPageUrl'),
            'resolvedCsvUrl': resolved_url,
            'resolvedAt': resolution.get('resolvedAt'),
            'fallbackReason': resolution.get('fallbackReason'),
        }
    return summary


def build_receipt_dual_source(*, comparison, csv_summary_, paginated_summary_,
                              csv_archive=None, paginated_archive=None,
                              retrieved_at=None, resolution=None):
    """主要收據用嘅 dualSource block（計數 + 兩個來源摘要 + raw archive namespace）。"""
    block = {
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
    if resolution:
        block['catalog'] = {
            'mode': resolution.get('mode'),
            'datasetId': resolution.get('datasetId'),
            'resourceId': resolution.get('resourceId'),
            'resourceName': resolution.get('resourceName'),
            'catalogApiUrl': resolution.get('catalogApiUrl'),
            'datasetPageUrl': resolution.get('datasetPageUrl'),
            'resolvedCsvUrl': resolution.get('resolvedCsvUrl'),
            'resolvedAt': resolution.get('resolvedAt'),
            'fallbackReason': resolution.get('fallbackReason'),
        }
    return block


def verify_dual_source(*, paginated_rows, paginated_raw_records, retrieved_at,
                       transport=None, now=None, min_rows=1700,
                       paginated_archive_hash=None, csv_transport=None,
                       conditional=None, raw_response=None, url=None,
                       resolution=None):
    """主入口：抓 CSV、parse、min-row gate、比較；成功回 primary rows + 證據。

    `raw_response` 供呼叫方／測試注入（fetch_emsd 用可 monkeypatch 嘅 wrapper
    事先抓好）；唔傳就經 `fetch_csv_raw` 抓 `url`（預設 CSV_URL）。

    任何失敗 raise DualSourceError（kind: network_error／http_error／catalog_*／
    content_type／schema／csv_incomplete／csv_not_modified／paginated_incomplete／
    mismatch），diff 可供呼叫方寫脫敏 report。
    """
    if not paginated_rows:
        raise DualSourceError('paginated 來源冇資料，唔可以單獨放行 CSV',
                              kind='paginated_incomplete', diff={'totalRows': 0})
    if not paginated_raw_records:
        raise DualSourceError('paginated 來源冇 raw page 證據', kind='paginated_incomplete')
    fetch_url = (resolution or {}).get('resolvedCsvUrl') or url or CSV_URL
    resp = raw_response or fetch_csv_raw(url=fetch_url, transport=csv_transport or transport,
                                         now=now, conditional=conditional)
    if resp.notModified:
        raise DualSourceError('CSV 只回 304（cached bytes），唔准用舊 cache 當新鮮資料',
                              kind='csv_not_modified')
    if not resp.body or not resp.body.strip():
        raise DualSourceError('CSV 回應係空 bytes', kind='csv_incomplete',
                              diff={'status': resp.status, 'byteLength': len(resp.body)})
    # 注入／redirect 後嘅實際 URL 都要通過官方 allowlist（unknown host／redirect fail-closed）
    validate_official_csv_url(str(resp.finalUrl or resp.url))
    parsed = parse_open_data_csv(resp.body)
    if parsed['rowCount'] < min_rows:
        raise DualSourceError(
            f"CSV 只攞到 {parsed['rowCount']} 行，少於安全下限 {min_rows}",
            kind='csv_incomplete',
            diff={'rowCount': parsed['rowCount'], 'minRows': min_rows})
    comparison = compare_sources(parsed['rows'], paginated_rows)
    csv_sum = csv_summary(resp, parsed, min_rows=min_rows, resolution=resolution)
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
            'comparison': comparison, 'rawResponse': resp, 'parsed': parsed,
            'resolution': resolution or {}}
