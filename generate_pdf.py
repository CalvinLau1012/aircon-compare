#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 PDF 報告（治理文檔 report.pdf-export · required 功能）

- 輸入：空調對比報告.md（同 Web 同一發布輸入）+ metadata.json（同 Web 同一 metadata）
- 輸出：空調對比報告.pdf
- 版本/資料日期/部署時間：用 generate_html.format_status（同 Web 同一套規則，唔會有第二套來源）
- 技術：reportlab（純 Python）+ 內置 STSong-Light CID 中文字體（唔使外置字型檔）

用法：
  python generate_pdf.py
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
from generate_html import format_status, VERSION  # noqa: E402


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


def load_metadata():
    try:
        with open(METADATA_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def build_pdf():
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

    meta = load_metadata()
    line1, line2 = format_status(meta, VERSION)

    doc = SimpleDocTemplate(OUT_PATH, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title='香港空調對比報告')
    story = [Paragraph('香港空調對比報告', st_h1),
             Paragraph(line1, st_p),
             Paragraph(line2, st_q),
             Spacer(1, 6)]

    with open(MD_PATH, encoding='utf-8') as f:
        html = markdown.markdown(f.read(), extensions=['tables', 'fenced_code', 'sane_lists'])
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
                cells = [Paragraph(cell[:120], st_cellh if i == 0 else st_cell)
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
    print(f'✅ PDF 已生成：{OUT_PATH}（{os.path.getsize(OUT_PATH) / 1024:.0f} KB）· v{VERSION}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    build_pdf()
