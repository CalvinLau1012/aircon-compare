# -*- coding: utf-8 -*-
"""回歸：EMSD CSV 必須原生 LF（GitHub Pages hash 鏈修正）

背景：`csv.writer` 預設 lineterminator='\\r\\n'，而 `.gitattributes` 係
`* text=auto eol=lf`；git add 時 index／發佈 bytes 變 LF，但 pipeline 對工作樹 CRLF
計 datasetHash／releasePayloadHash → 線上 hash 鏈斷。本測試實際走 `fetch_emsd.write_csv`
寫檔路徑，斷言輸出無 CRLF 且現有 loader 讀得到，並檢查已入庫 CSV 亦係 LF。
"""
import hashlib
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SPEC = importlib.util.spec_from_file_location('fetch_emsd', os.path.join(BASE, 'fetch_emsd.py'))
fetch_emsd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch_emsd)

HEADER = ['品牌', '型號', '參考編號', '類別', '能源級別',
          '每年耗電量', '額定功率', 'CSPF', '製冷量', '能源標籤',
          '登記日期', '有效日期', '備註', '來源', '其他']
ROWS = [
    ['日立', 'RA-10RF', 'REF-1', '窗口式', '1級', '500', '0.5', '4.5', '9000', 'X',
     '2020-01-01', '2030-01-01', '', 'EMSD', ''],
    ['樂信', 'RC-XG9', 'REF-2', '窗口式', '4級', '700', '0.7', '3.0', '8803', 'X',
     '2020-01-01', '2030-01-01', '', 'EMSD', ''],
]


def test_write_csv_is_lf_only_with_bom(tmp_path):
    out = str(tmp_path / 'emsd.csv')
    fetch_emsd.write_csv(HEADER, ROWS, out)
    raw = open(out, 'rb').read()
    assert raw.startswith(b'\xef\xbb\xbf'), 'utf-8-sig BOM 應該保留'
    assert b'\r\n' not in raw, 'CSV 唔可以有 CRLF（會同 git index LF 正規化唔一致）'
    assert raw.count(b'\n') == len(ROWS) + 1, '每個 record 剛好一個 LF'
    assert b'\r' not in raw, '唔可以有裸 CR'


def test_written_csv_readable_by_existing_loaders(tmp_path):
    from crawl_utils import load_models, load_registrations, load_energy_distributions
    out = str(tmp_path / 'emsd.csv')
    fetch_emsd.write_csv(HEADER, ROWS, out)
    regs = load_registrations(out)
    models = load_models(out)
    reg_dist, canon_dist = load_energy_distributions(out)
    assert len(regs) == 2 and len(models) == 2, (regs, models)
    assert reg_dist.get('1級') == 1 and reg_dist.get('4級') == 1, reg_dist
    assert canon_dist.get('1級') == 1 and canon_dist.get('4級') == 1, canon_dist


def test_write_csv_atomic_replace_drops_tmp(tmp_path):
    out = str(tmp_path / 'emsd.csv')
    fetch_emsd.write_csv(HEADER, ROWS, out)
    assert os.path.exists(out)
    assert not os.path.exists(out + '.tmp'), '原子替換後唔應該留 .tmp'


def test_committed_csv_is_lf_only():
    """已入庫 CSV（會直接上 GitHub Pages）必須係 LF bytes。"""
    path = os.path.join(BASE, 'emsd_空調能源標籤.csv')
    if not os.path.exists(path):
        return
    raw = open(path, 'rb').read()
    assert b'\r\n' not in raw, '已入庫 EMSD CSV 係 CRLF；git 正規化會令線上 hash 鏈斷'
    # 同 metadata.datasetHash 自洽（若 metadata 存在）
    meta_path = os.path.join(BASE, 'metadata.json')
    if os.path.exists(meta_path):
        import json
        meta = json.load(open(meta_path, encoding='utf-8'))
        sha = 'sha256:' + hashlib.sha256(raw).hexdigest()
        assert meta.get('datasetHash') == sha, \
            f'metadata.datasetHash 同已入庫 CSV 唔一致：{meta.get("datasetHash")} vs {sha}'
