#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 PDF 報告（治理文檔 report.pdf-export · required 功能）

- 輸入：空調對比報告.md（同 Web 同一發布輸入）+ metadata（同 Web 同一 metadata）
- 輸出：空調對比報告.pdf（`build_pdf(output_path=...)` 可供測試寫入暫存目錄）
- 版本/資料日期/部署時間：用 generate_html.format_status（同 Web 同一套規則，唔會有第二套來源）
- 技術：reportlab（純 Python）+ 內置 STSong-Light CID 中文字體（唔使外置字型檔）

用法：
  python generate_pdf.py                 # 輸出 repo 根目錄 空調對比報告.pdf（讀 repo metadata.json）
  python generate_pdf.py --metadata metadata.core.json   # CI 兩階段：用同 run 嘅 metadata core
  build_pdf(output_path, metadata_path)  # 測試／建置可指定輸出路徑及 metadata 來源

可重現性：同輸入連續兩次 build 必須 byte-for-byte 相同（GATE-02／SC-014）；
reportlab 寫入嘅 CreationDate/ModDate 會被固定化。
"""
import json
import os
import re
import sys

import markdown
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.join(BASE, '空調對比報告.md')
OUT_PATH = os.path.join(BASE, '空調對比報告.pdf')
METADATA_PATH = os.path.join(BASE, 'metadata.json')

sys.path.insert(0, BASE)
from generate_html import format_status, VERSION, expand_dynamic_sections  # noqa: E402


class BlockExtractor(HTMLParser):
    """markdown → HTML → block 列表：h1/h2/h3/p/table/blockquote"""

    def __init__(self):
        super().__init__()
        self.blocks = []
        self.buf = []
        self.in_table = False
        self.table_rows = []
        self.cur_row = []
        self.cur_cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
            self.table_rows = []
        elif tag == 'tr' and self.in_table:
            self.cur_row = []
        elif tag in ('td', 'th') and self.in_table:
            self.cur_cell = []

    def handle_data(self, data):
        if self.in_table and self.cur_cell is not None:
            self.cur_cell.append(data)
        elif not self.in_table:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_table:
            self.cur_row.append(''.join(self.cur_cell).strip())
            self.cur_cell = None
        elif tag == 'tr' and self.in_table:
            if self.cur_row:
                self.table_rows.append(self.cur_row)
        elif tag == 'table':
            self.in_table = False
            self.blocks.append(('table', self.table_rows))
        elif tag in ('h1', 'h2', 'h3', 'p', 'blockquote', 'li'):
            text = ''.join(self.buf).strip()
            if text:
                self.blocks.append((tag, text))
            self.buf = []


def _safe_text(text):
    """控制台編碼唔支援 emoji／個別字元時用 replacement，唔可以令建置失敗"""
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    return text.encode(enc, 'replace').decode(enc)


def load_metadata(path=None):
    """讀取 metadata；path 預設 repo 根目錄 metadata.json。

    讀取／解析失敗唔可以靜默回 {}（否則會出一個狀態空白的 production PDF）。
    """
    p = path or METADATA_PATH
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f'無法讀取有效 metadata（{p}）：{e}')
    if not isinstance(data, dict) or not data:
        raise ValueError(f'metadata 必須係非空 object（{p}）')
    return data


def validate_metadata_for_pdf(meta):
    """PDF 用 metadata 必須過治理 Schema（core 可用 placeholder hash 驗核事實）。"""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scripts'))
    from validate_metadata import validate, validate_core
    from extract_governance import extract_blocks, GOV_FILE
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    if 'releasePayloadHash' in meta:
        return validate(meta, schema)
    return validate_core(meta, schema)


_FIXED_PDF_DATE = "D:20200101000000+00'00'"


def _normalize_pdf_bytes(data):
    """固定 reportlab 寫入嘅 CreationDate/ModDate/ID，令同輸入兩次 build byte-for-byte 相同"""
    fixed = _FIXED_PDF_DATE.encode('ascii')
    data = re.sub(rb'/CreationDate \(D:[^)]*\)', b'/CreationDate (' + fixed + b')', data)
    data = re.sub(rb'/ModDate \(D:[^)]*\)', b'/ModDate (' + fixed + b')', data)
    data = re.sub(rb'/ID \s*\[<[0-9a-f]+><[0-9a-f]+>\]',
                  b'/ID [<00000000000000000000000000000000><00000000000000000000000000000000>]',
                  data)
    return data


def build_pdf(output_path=None, metadata_path=None):
    """生成 PDF；output_path 預設 repo 根目錄（CI 用），測試應傳 tmp_path 避免覆寫使用者 PDF。

    metadata_path：本次部署嘅 metadata 來源；預設讀 repo 根目錄 metadata.json。
    CI 兩階段封裝會傳入同 run 嘅 metadata core（見 docs/DECISIONS.md D14），
    確保 PDF 嘅 version／datasetDate／deployTime 同最終 metadata 完全一致。
    """
    out_path = output_path or OUT_PATH
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    CJK = 'STSong-Light'

    st_h1 = ParagraphStyle('h1', fontName=CJK, fontSize=20, leading=26,
                           spaceAfter=10, textColor=colors.HexColor('#1d2539'))
    st_h2 = ParagraphStyle('h2', fontName=CJK, fontSize=15, leading=20,
                           spaceBefore=12, spaceAfter=6,
                           textColor=colors.HexColor('#4a5fa8'))
    st_h3 = ParagraphStyle('h3', fontName=CJK, fontSize=12, leading=16,
                           spaceBefore=8, spaceAfter=4,
                           textColor=colors.HexColor('#647ebf'))
    st_p = ParagraphStyle('p', fontName=CJK, fontSize=10, leading=15,
                          spaceAfter=4, textColor=colors.HexColor('#1d2539'))
    st_q = ParagraphStyle('q', fontName=CJK, fontSize=9, leading=13,
                          spaceAfter=4, leftIndent=8,
                          textColor=colors.HexColor('#5b6989'))
    st_cell = ParagraphStyle('cell', fontName=CJK, fontSize=8, leading=11)
    st_cellh = ParagraphStyle('cellh', fontName=CJK, fontSize=8, leading=11,
                              textColor=colors.white)

    meta = load_metadata(metadata_path)
    meta_errors = validate_metadata_for_pdf(meta)
    if meta_errors:
        raise ValueError('PDF metadata 唔過治理 Schema，拒絕生成：' + '；'.join(meta_errors[:5]))
    line1, line2 = format_status(meta, VERSION)

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title='香港空調對比報告')
    story = [Paragraph('香港空調對比報告', st_h1),
             Paragraph(line1, st_p),
             Paragraph(line2, st_q),
             Spacer(1, 6)]

    with open(MD_PATH, encoding='utf-8') as f:
        md_text = expand_dynamic_sections(f.read())
    html = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'sane_lists'])
    ex = BlockExtractor()
    ex.feed(html)

    for kind, payload in ex.blocks:
        if kind == 'h1':
            story.append(Paragraph(payload.replace('# ', ''), st_h1))
        elif kind == 'h2':
            story.append(Paragraph(payload, st_h2))
        elif kind == 'h3':
            story.append(Paragraph(payload, st_h3))
        elif kind == 'blockquote':
            story.append(Paragraph(payload, st_q))
        elif kind == 'table':
            if not payload:
                continue
            ncols = max(len(r) for r in payload)
            data = []
            for i, row in enumerate(payload):
                cells = [Paragraph((cell[:120] + ('…' if len(cell) > 120 else '')),
                                   st_cellh if i == 0 else st_cell)
                         for cell in row[:ncols]]
                while len(cells) < ncols:
                    cells.append(Paragraph('', st_cell))
                data.append(cells)
            if data:
                t = Table(data, repeatRows=1)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5fa8')),
                    ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#c7ccda')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                     [colors.white, colors.HexColor('#eef1fb')]),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ]))
                story.append(t)
                story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(payload, st_p))

    story.append(Spacer(1, 10))
    story.append(Paragraph('── 免責聲明 ──', st_h3))
    story.append(Paragraph('本報告僅供選購參考，不構成購買建議；價格及供應隨時變動，'
                           '請以商戶實時報價為準。', st_q))
    doc.build(story)
    with open(out_path, 'rb') as f:
        raw = f.read()
    normalized = _normalize_pdf_bytes(raw)
    if normalized != raw:
        with open(out_path, 'wb') as f:
            f.write(normalized)
    print(_safe_text(f'✅ PDF 已生成：{out_path}（{os.path.getsize(out_path) / 1024:.0f} KB）· v{VERSION}'))
    return out_path


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import argparse
    ap = argparse.ArgumentParser(description='生成空調對比報告 PDF')
    ap.add_argument('--metadata', default=None, help='metadata 來源（預設 repo metadata.json）')
    ap.add_argument('--out', default=None, help='輸出 PDF 路徑（預設 repo 根目錄）')
    args = ap.parse_args()
    try:
        build_pdf(output_path=args.out, metadata_path=args.metadata)
    except ValueError as e:
        print(f'❌ PDF 生成失敗（唔會出 invalid production PDF）：{e}', file=sys.stderr)
        sys.exit(1)
