# -*- coding: utf-8 -*-
"""
P0 回歸測試（治理改善方案 M1：先寫測試後修正）

PR-1 引入時呢啲測試會 fail（紅燈證據）；由 PR-2（canonical 型號鍵／生命週期）同
PR-3（EMSD ingestion）嘅修正轉綠。全部轉綠前唔可以合併去 master，
亦唔可以 mark skip／xfail。
"""
import csv
import hashlib
import inspect
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ================================================================ PR-1：PDF

def test_pdf_build_is_reproducible(tmp_path):
    """同一輸入連續兩次 build 必須 byte-for-byte 相同（GATE-02／SC-014）"""
    from generate_pdf import build_pdf
    p1 = tmp_path / 'a.pdf'
    p2 = tmp_path / 'b.pdf'
    build_pdf(str(p1))
    build_pdf(str(p2))
    assert p1.read_bytes() == p2.read_bytes()


def test_build_pdf_to_tmp_does_not_modify_tracked_pdf(tmp_path):
    """build_pdf 指定輸出路徑時，唔可以改動 repo 受追蹤 PDF"""
    from generate_pdf import build_pdf
    tracked = os.path.join(BASE, '空調對比報告.pdf')

    def _hash():
        with open(tracked, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()

    before = _hash()
    build_pdf(str(tmp_path / 'x.pdf'))
    assert _hash() == before


# ================================================================ PR-2：canonical 型號鍵

def test_canonical_brand_normalizes_platform_variants():
    """品牌名跨平台矯正：中文／英文／顯示名 → 統一 canonical ID"""
    from crawl_utils import canonical_brand
    assert canonical_brand('日立牌') == 'HITACHI'
    assert canonical_brand('HITACHI 日立') == 'HITACHI'
    assert canonical_brand('開利') == 'CARRIER'
    assert canonical_brand('Carrier 開利') == 'CARRIER'
    assert canonical_brand('樂信牌') == 'RASONIC'
    assert canonical_brand('Rasonic 樂 信') == 'RASONIC'
    assert canonical_brand('Panasonic 樂聲') == 'PANASONIC'
    assert canonical_brand('General 珍寶') == 'GENERAL'
    assert canonical_brand('Gree 格力') == 'GREE'


def test_canonical_model_key_joins_brand_and_norm():
    from crawl_utils import canonical_model_key
    assert canonical_model_key('HITACHI', 'RA-10RF') == 'HITACHI|RA10RF'
    assert canonical_model_key('Carrier 開利', 'CHK 18EAVX') == 'CARRIER|CHK18EAVX'


def test_blacklist_entries_are_canonical():
    """黑名單所有 key 必須係 canonical（BRAND|NORM）格式"""
    from model_lifecycle import load_blacklist
    black = load_blacklist()
    assert black, '黑名單唔應該係空'
    for k in black:
        assert re.fullmatch(r'[A-Z0-9\u4e00-\u9fff]+\|[A-Z0-9]+', k), f'非 canonical key：{k!r}'


def test_discontinued_models_marked_in_page():
    """黑名單型號（含 -／／空格）喺頁面必須標示為停售（改善方案 F-05）"""
    from crawl_utils import norm_model
    from generate_html import assign_status, load_blacklist
    with open(os.path.join(BASE, 'model_blacklist.json'), encoding='utf-8') as f:
        data = json.load(f)
    keys = list((data.get('models') or {}) if isinstance(data, dict) else data)
    norm_keys = {norm_model(k.split('|')[-1]) for k in keys}
    black = load_blacklist()
    found = None
    with open(os.path.join(BASE, 'emsd_空調能源標籤.csv'), encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    for r in rows:
        if len(r) < 15 or r[1].strip() == '型號':
            continue
        model = r[1].strip()
        if norm_model(model) in norm_keys and re.search(r'[^A-Za-z0-9]', model):
            found = (r[0].strip(), model)
            break
    assert found, '搵唔到黑名單內含符號而 CSV 存在嘅型號（資料可能已改變）'
    item = {'brand': found[0], 'model': found[1]}
    assign_status(item, black)
    assert item.get('status') == '停售', f'{found[1]} 應該標示停售，實際係 {item.get("status")!r}'


def test_protected_models_return_canonical_keys():
    """fetch_biggo.protected_models() 必須回傳 canonical key（同 record_results 一致）"""
    from fetch_biggo import protected_models
    protected = protected_models()
    assert 'HITACHI|RA10RF' in protected, f'protected 冇 canonical key，樣本：{list(protected)[:5]}'


def test_batch_protection_keys_align_with_core_models():
    """核心 29 嘅 canonical key 必須全部喺 protected set（保護唔可以失效）"""
    from crawl_utils import canonical_model_key
    from fetch_biggo import protected_models
    from models_data import MODELS
    protected = protected_models()
    for m in MODELS:
        key = canonical_model_key(m['brand'], m['model'])
        assert key in protected, f'核心型號 {m["model"]}（{key}）唔喺 protected set'


def test_record_results_respects_protected_canonical(tmp_path, monkeypatch):
    """受保護型號（canonical key）連續 miss 唔可以淘汰；非受保護照常淘汰"""
    import model_lifecycle as ml
    monkeypatch.setattr(ml, 'BLACKLIST_PATH', str(tmp_path / 'black.json'))
    monkeypatch.setattr(ml, 'TRACKING_PATH', str(tmp_path / 'status.json'))
    protected = {'HITACHI|RA10RF'}
    for _ in range(3):
        ml.record_results([('HITACHI|RA10RF', False)], protected=protected)
    assert ml.load_blacklist() == {}, '受保護型號唔應該被淘汰'
    for _ in range(3):
        ml.record_results([('CARRIER|CHK18EAVX', False)], protected=protected)
    assert 'CARRIER|CHK18EAVX' in ml.load_blacklist(), '非受保護型號應該照常淘汰'


def test_price_batch_workers_follow_d3():
    """D3 決策：並發數係 2；run_price_batch 唔可以再用 3 workers"""
    import fetch_biggo
    src = inspect.getsource(fetch_biggo.run_price_batch)
    assert 'max_workers=3' not in src, 'run_price_batch 違反 D3（並發數應該係 2）'


# ================================================================ PR-3：EMSD ingestion

def test_csv_has_no_repeated_header_rows():
    """CSV 唔可以有重複「型號」表頭（改善方案 F-04）"""
    path = os.path.join(BASE, 'emsd_空調能源標籤.csv')
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    dup = [i for i, r in enumerate(rows) if i > 0 and len(r) >= 2 and r[1].strip() == '型號']
    assert dup == [], f'CSV 有重複表頭：行號 {dup[:10]}'


def test_emsd_page_parser_drops_header_every_page():
    """每一頁嘅表頭都要按 signature 排除，唔可以只靠 p == 1"""
    from fetch_emsd import parse_page_rows
    header = '<tr><th>品牌</th><th>型號</th>' + ''.join(
        f'<th>c{i}</th>' for i in range(13)) + '</tr>'
    body = '<tr><td>開利</td><td>CHK-18EAVX</td>' + ''.join(
        f'<td>{i}</td>' for i in range(13)) + '</tr>'
    html = '<table>' + header + body + '</table>'
    rows = parse_page_rows(html)
    assert rows == [['開利', 'CHK-18EAVX'] + [str(i) for i in range(13)]]


def test_partial_fetch_not_treated_as_success():
    """中途網絡錯誤即使累積超過 1700 行，都唔可以當成功覆寫舊 CSV"""
    from fetch_emsd import fetch_outcome
    o = fetch_outcome(pages_expected=40, pages_fetched=10, aborted=True, total_rows=2100)
    assert o['success'] is False, 'partial fetch 唔應該視為成功'
    o2 = fetch_outcome(pages_expected=40, pages_fetched=40, aborted=False, total_rows=1863)
    assert o2['success'] is True


def test_registration_and_model_counts_separable():
    """登記記錄（registration）同型號（canonical product view）要分得開"""
    from crawl_utils import load_models, load_registrations
    regs = load_registrations()
    models = load_models()
    assert len(models) > 0
    assert len(regs) >= len(models), f'登記 {len(regs)} 唔應該少過型號 {len(models)}'
