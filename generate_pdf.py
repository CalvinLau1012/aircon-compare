#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
香港窗口式空調對比報告 → 精美 PDF 轉換器（全新排版 v3）
- 封面頁（深藍色塊 + 大標題 + 統計數字）
- 自動目錄頁
- 章節色條標題（h1 全寬色塊 / h2 金色左條）
- 彩色注意框（引用 > 轉換）
- 美化表格（表頭深藍 / 斑馬紋 / 金色點綴）
- 手機版卡片美化
- 頁碼頁眉頁腳
"""
import re
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, ListFlowable, ListItem,
                                KeepTogether, HRFlowable)

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
FONT = 'STSong-Light'

# ============ 調色板 ============
C_PRIMARY  = colors.HexColor('#0F3D5C')   # 深海軍藍
C_PRIMARY2 = colors.HexColor('#1B5E8A')   # 較淺海軍藍
C_ACCENT   = colors.HexColor('#C9A227')   # 金色
C_BG       = colors.HexColor('#F5F8FB')   # 頁面淺藍灰
C_TEXT     = colors.HexColor('#22303C')   # 正文深灰藍
C_MUTED    = colors.HexColor('#6E7E8E')   # 次要文字
C_LINE     = colors.HexColor('#D8E1EB')   # 網格線
C_ALT      = colors.HexColor('#EEF4FA')   # 斑馬紋
C_NOTE_BG  = colors.HexColor('#FFF7E3')   # 注意框
C_NOTE_BAR = colors.HexColor('#E0A11E')
C_WARN_BG  = colors.HexColor('#FDEDED')
C_WARN_BAR = colors.HexColor('#C0392B')
C_OK_BG    = colors.HexColor('#E9F7EF')
C_OK_BAR   = colors.HexColor('#1E8E5A')

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def st(name, **kw):
    base = dict(fontName=FONT, leading=15, fontSize=10, textColor=C_TEXT)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    'cover_title': st('cover_title', fontSize=28, leading=36, alignment=TA_CENTER,
                      textColor=colors.white, spaceAfter=4),
    'cover_sub':   st('cover_sub', fontSize=14, leading=20, alignment=TA_CENTER,
                      textColor=C_ACCENT, spaceAfter=18),
    'cover_meta':  st('cover_meta', fontSize=10, leading=16, alignment=TA_CENTER,
                      textColor=colors.white),
    'cover_stat_n': st('cover_stat_n', fontSize=20, leading=24, alignment=TA_CENTER,
                       textColor=C_ACCENT),
    'cover_stat_l': st('cover_stat_l', fontSize=9, leading=13, alignment=TA_CENTER,
                       textColor=colors.white),
    'h1':  st('h1', fontSize=14.5, leading=20, spaceBefore=4, spaceAfter=6,
              textColor=colors.white),
    'h2':  st('h2', fontSize=12.5, leading=17, spaceBefore=10, spaceAfter=4,
              textColor=C_PRIMARY),
    'h3':  st('h3', fontSize=11, leading=15, spaceBefore=7, spaceAfter=3,
              textColor=C_PRIMARY2),
    'body': st('body', spaceAfter=5, leading=15),
    'bullet': st('bullet', leftIndent=12, spaceAfter=3, leading=14.5),
    'note': st('note', fontSize=9.5, leading=14, textColor=C_TEXT),
    'toc': st('toc', fontSize=10.5, leading=19),
    'toc1': st('toc1', fontSize=10.5, leading=19, textColor=C_TEXT),
    'toc2': st('toc2', fontSize=9.5, leading=17, leftIndent=14, textColor=C_MUTED),
    'cell_th': st('cell_th', fontSize=7.8, leading=10.5, textColor=colors.white,
                  alignment=TA_CENTER),
    'cell_td': st('cell_td', fontSize=7.8, leading=10.8, textColor=C_TEXT),
    'card_title': st('card_title', fontSize=9.5, leading=13, textColor=colors.white,
                     alignment=TA_LEFT),
    'card_label': st('card_label', fontSize=8, leading=11.5, textColor=C_PRIMARY2),
    'card_value': st('card_value', fontSize=8, leading=11.5, textColor=C_TEXT),
    'code': st('code', fontSize=9, leading=13, textColor=C_PRIMARY,
               backColor=C_BG, borderColor=C_LINE, borderWidth=0.6,
               borderPadding=7, spaceBefore=4, spaceAfter=8),
}

# ============ Markdown 解析 ============

def clean_inline(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = text.replace('\\', '')
    text = re.sub(r'[\U0001F000-\U0001FAFF]', '', text)
    text = re.sub(r'[\u2705\u274C\u26A0\u2B50\u2728\u26A1\u2B55]', '', text)
    text = re.sub(r'[\u2600-\u26FF]', '', text)
    return text


def parse_table(lines):
    rows = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith('|'):
            continue
        cells = [c.strip() for c in ln.strip('|').split('|')]
        if all(re.fullmatch(r':?-{2,}:?', c.replace(' ', '')) for c in cells):
            continue
        rows.append(cells)
    return rows or None


# ============ 版面元件 ============

def h1_block(text):
    """章節標題：全寬深藍色塊 + 白色文字 + 金色左條"""
    p = Paragraph(text, S['h1'])
    t = Table([['', p]], colWidths=[3.2 * mm, CONTENT_W - 3.2 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), C_ACCENT),
        ('BACKGROUND', (1, 0), (1, 0), C_PRIMARY),
        ('LEFTPADDING', (1, 0), (1, 0), 9),
        ('RIGHTPADDING', (1, 0), (1, 0), 6),
        ('TOPPADDING', (1, 0), (1, 0), 6),
        ('BOTTOMPADDING', (1, 0), (1, 0), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return [Spacer(1, 10), t, Spacer(1, 4)]


def h2_row(text):
    """子標題：金色左條 + 深藍字"""
    p = Paragraph(text, S['h2'])
    t = Table([['', p]], colWidths=[2.4 * mm, CONTENT_W - 2.4 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), C_ACCENT),
        ('LEFTPADDING', (1, 0), (1, 0), 7),
        ('RIGHTPADDING', (1, 0), (1, 0), 4),
        ('TOPPADDING', (1, 0), (1, 0), 2),
        ('BOTTOMPADDING', (1, 0), (1, 0), 2),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


def note_box(text, kind='note'):
    """彩色注意框：> 引用轉換"""
    if kind == 'warn':
        bar, bg = C_WARN_BAR, C_WARN_BG
    elif kind == 'ok':
        bar, bg = C_OK_BAR, C_OK_BG
    else:
        bar, bg = C_NOTE_BAR, C_NOTE_BG
    p = Paragraph(text, S['note'])
    t = Table([['', p]], colWidths=[2.6 * mm, CONTENT_W - 2.6 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), bar),
        ('BACKGROUND', (1, 0), (1, 0), bg),
        ('LEFTPADDING', (1, 0), (1, 0), 8),
        ('RIGHTPADDING', (1, 0), (1, 0), 8),
        ('TOPPADDING', (1, 0), (1, 0), 5),
        ('BOTTOMPADDING', (1, 0), (1, 0), 5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return [Spacer(1, 2), t, Spacer(1, 4)]


def _char_units(s):
    u = 0
    for ch in s:
        u += 2 if ord(ch) > 0x2E7F else 1
    return u


def make_grid_table(norm, compact=False):
    """打印版傳統表格：深藍表頭 + 斑馬紋 + 金色分隔"""
    ncols = len(norm[0])
    col_units = [0] * ncols
    for c in range(ncols):
        units = []
        for ridx, row in enumerate(norm):
            w = _char_units(clean_inline(row[c]))
            units.append(w * 1.5 if ridx == 0 else w)
        col_units[c] = max(units) + 2
    total_units = sum(col_units) or 1
    col_widths = [max(11.0, CONTENT_W * u / total_units) for u in col_units]
    tw = sum(col_widths)
    if tw > CONTENT_W:
        col_widths = [w * CONTENT_W / tw for w in col_widths]

    th_fs, th_ld = (7.4, 10.2) if compact else (7.8, 10.8)
    td_fs, td_ld = (7.4, 10.4) if compact else (7.8, 11)
    pad = 2.2 if compact else 2.8

    para_rows = []
    for ridx, row in enumerate(norm):
        para_row = []
        for cell in row:
            c = clean_inline(cell)
            if ridx == 0:
                para_row.append(Paragraph(c, st('th', fontSize=th_fs, leading=th_ld,
                                                textColor=colors.white, alignment=TA_CENTER)))
            else:
                para_row.append(Paragraph(c, st('td', fontSize=td_fs, leading=td_ld,
                                                textColor=C_TEXT)))
        para_rows.append(para_row)

    t = Table(para_rows, colWidths=col_widths, repeatRows=1)
    cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, C_LINE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, C_ACCENT),
        ('TOPPADDING', (0, 0), (-1, -1), pad),
        ('BOTTOMPADDING', (0, 0), (-1, -1), pad),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]
    for r in range(1, len(para_rows)):
        if r % 2 == 0:
            cmds.append(('BACKGROUND', (0, r), (-1, r), C_ALT))
    t.setStyle(TableStyle(cmds))
    return t


def make_card_table(norm):
    """手機版卡片：一型號一卡，表頭深藍 + 資料行分隔"""
    header = norm[0]
    data_rows = norm[1:]
    cards = []

    for row in data_rows:
        lines = []
        title_parts = [clean_inline(row[0])]
        if len(row) > 1 and row[1] and clean_inline(row[1]):
            title_parts.append(clean_inline(row[1]))
        title = ' '.join(title_parts).strip()
        for c in range(2, len(header)):
            label = clean_inline(header[c])
            val = clean_inline(row[c]) if c < len(row) else ''
            if label and val:
                lines.append([label, val])
        if not lines and len(row) > 1:
            lines = [[clean_inline(header[c]), clean_inline(row[c])]
                     for c in range(1, len(row)) if clean_inline(row[c])]
            title = clean_inline(row[0])

        card_rows = []
        # 卡頭：金色左條 + 深藍底型號名
        head = Table([['', Paragraph(title, S['card_title'])]],
                     colWidths=[3 * mm, 181 * mm])
        head.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), C_ACCENT),
            ('BACKGROUND', (1, 0), (1, 0), C_PRIMARY),
            ('LEFTPADDING', (1, 0), (1, 0), 7),
            ('RIGHTPADDING', (1, 0), (1, 0), 4),
            ('TOPPADDING', (1, 0), (1, 0), 4.5),
            ('BOTTOMPADDING', (1, 0), (1, 0), 4.5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        card_rows.append([head])

        for label, val in lines:
            lbl = Paragraph(label, S['card_label'])
            v = Paragraph(val, S['card_value'])
            inner = Table([[lbl, v]], colWidths=[52 * mm, 129 * mm])
            inner.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LINEBELOW', (0, 0), (-1, -2), 0.3, C_LINE),
            ]))
            card_rows.append([inner])

        # 每張卡：頭 + 內容 + 邊框
        card = Table(card_rows, colWidths=[184 * mm])
        cmds = [
            ('BOX', (0, 0), (-1, -1), 0.6, C_LINE),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]
        card.setStyle(TableStyle(cmds))
        cards.append(Spacer(1, 3))
        cards.append(card)
    return cards


# ============ 封面 + 目錄 ============

def cover_page(canvas, doc):
    """封面頁：用 canvas 直接繪製全頁深藍設計（無頁碼頁眉）"""
    canvas.saveState()
    # 全頁深藍背景
    canvas.setFillColor(C_PRIMARY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    # 頂部裝飾條
    canvas.setFillColor(C_PRIMARY2)
    canvas.rect(0, PAGE_H - 46, PAGE_W, 46, stroke=0, fill=1)
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 50, PAGE_W, 4, stroke=0, fill=1)
    # 主標題
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT, 29)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.74, '香港窗口式淨冷型遙控空調')
    canvas.setFont(FONT, 15)
    canvas.setFillColor(C_ACCENT)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.685, '統合對比報告 · 29 型號全面剖析')
    # 金色分隔線
    canvas.setStrokeColor(C_ACCENT)
    canvas.setLineWidth(1.6)
    canvas.line(PAGE_W * 0.28, PAGE_H * 0.64, PAGE_W * 0.72, PAGE_H * 0.64)
    # 統計數字
    stats = [('29', '型號收錄'), ('16', '定頻機型'), ('13', '變頻機型'), ('1,927', 'EMSD 官方核實')]
    x0 = PAGE_W / 2 - 168
    bw = 84
    canvas.setFont(FONT, 26)
    for k, (n, l) in enumerate(stats):
        cx = x0 + k * bw + bw / 2
        canvas.setFillColor(C_ACCENT)
        canvas.drawCentredString(cx, PAGE_H * 0.545, n)
        canvas.setFont(FONT, 10)
        canvas.setFillColor(colors.white)
        canvas.drawCentredString(cx, PAGE_H * 0.505, l)
        canvas.setFont(FONT, 26)
    # 底部資訊
    canvas.setFont(FONT, 10)
    canvas.setFillColor(colors.HexColor('#BFD0DE'))
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.30,
                             '資料來源：機電署 EMSD 能源標籤資料庫（1,927 型號全量核實）')
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.265,
                             'Price.com.hk · 豐澤 · 電器幫 · 百老匯 · Gemini 交叉驗證')
    canvas.setFont(FONT, 12)
    canvas.setFillColor(C_ACCENT)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.20, '2026 年 8 月 12 日 更新版')
    canvas.restoreState()


def build_toc(story, toc_items):
    """目錄頁"""
    story.append(Spacer(1, 4 * mm))
    story.append(h2_row('目錄'))
    story.append(Spacer(1, 2 * mm))
    for level, num, text in toc_items:
        if level == 1:
            story.append(Paragraph(f'{num}　{text}', S['toc1']))
        else:
            story.append(Paragraph(text, S['toc2']))
    story.append(PageBreak())


def _footer(canvas, doc, mode):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(C_MUTED)
    # 頁眉線
    canvas.setStrokeColor(C_LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 12 * mm, PAGE_W - MARGIN, PAGE_H - 12 * mm)
    canvas.drawString(MARGIN, PAGE_H - 10.5 * mm, '香港窗口式空調對比報告')
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 10.5 * mm, '2026-08-12')
    # 頁腳
    canvas.line(MARGIN, 11 * mm, PAGE_W - MARGIN, 11 * mm)
    canvas.drawCentredString(PAGE_W / 2, 7 * mm, f'— {doc.page} —')
    canvas.restoreState()


# ============ 主建構 ============

def build_pdf(md_path, pdf_path, mode='print'):
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=17 * mm, bottomMargin=17 * mm,
        title='香港窗口式空調對比報告', author='AI 整理',
    )
    story = []
    toc_items = []
    tno = 0

    lines = content.split('\n')
    i, total = 0, len(lines)

    # 第一頁留空 → canvas 畫封面
    story.append(PageBreak())

    while i < total:
        ln = lines[i].rstrip()

        m = re.match(r'^(#{1,3})\s+(.*)$', ln)
        if m:
            level = len(m.group(1))
            text = clean_inline(m.group(2))
            if level == 1:
                tno += 1
                toc_items.append((1, tno, text))
                story.extend(h1_block(text))
            elif level == 2:
                toc_items.append((2, '', text))
                story.append(h2_row(text))
            else:
                story.append(Paragraph(text, S['h3']))
            i += 1
            continue

        if re.match(r'^-{3,}$', ln) or re.match(r'^\*{3,}$', ln):
            story.append(Spacer(1, 3))
            i += 1
            continue

        if ln.startswith('|'):
            tbl_lines = []
            while i < total and lines[i].strip().startswith('|'):
                tbl_lines.append(lines[i])
                i += 1
            rows = parse_table(tbl_lines)
            if rows:
                ncols = max(len(r) for r in rows)
                norm = [r + [''] * (ncols - len(r)) for r in rows]
                if mode == 'mobile' and ncols >= 6:
                    made = make_card_table(norm)
                    story.extend(made)
                else:
                    story.append(make_grid_table(norm, compact=(mode == 'print')))
            continue

        if re.match(r'^\s*[-*+]\s+', ln):
            items = [clean_inline(re.sub(r'^\s*[-*+]\s+', '', ln))]
            i += 1
            while i < total:
                nxt = lines[i].strip()
                if re.match(r'^[-*+]\s+', nxt):
                    items.append(clean_inline(re.sub(r'^[-*+]\s+', '', nxt)))
                    i += 1
                elif nxt == '' and i + 1 < total and re.match(r'^\s*[-*+]\s+', lines[i + 1]):
                    i += 1
                    continue
                else:
                    break
            fl = ListFlowable(
                [ListItem(Paragraph(t, S['bullet']), leftIndent=6) for t in items],
                bulletType='bullet', start='•', leftIndent=12,
                bulletFontName=FONT, bulletFontSize=10)
            story.append(fl)
            continue

        if ln.startswith('>'):
            q = clean_inline(ln.lstrip('>').strip())
            kind = 'note'
            if '⚠' in ln or '警告' in ln or '注意' in ln:
                kind = 'warn'
            elif '✅' in ln or '完成' in ln or '確認' in ln:
                kind = 'ok'
            story.extend(note_box(q, kind))
            i += 1
            continue

        if ln.strip().startswith('```'):
            code_lines = []
            i += 1
            while i < total and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1
            if code_lines:
                story.append(Paragraph('<br/>'.join(clean_inline(c) for c in code_lines), S['code']))
            continue

        if not ln.strip():
            i += 1
            continue

        story.append(Paragraph(clean_inline(ln), S['body']))
        i += 1

    # 目錄插入喺第二頁（封面之後）
    toc_flow = []
    build_toc(toc_flow, toc_items)
    story = story[:1] + toc_flow + story[1:]

    def footer_print(canvas, doc_):
        _footer(canvas, doc_, 'print')

    doc.build(story, onFirstPage=cover_page, onLaterPages=footer_print)
    return pdf_path


if __name__ == '__main__':
    base = r'd:\香港窗口式空調查找'
    md = os.path.join(base, '空調對比報告.md')
    build_pdf(md, os.path.join(base, '空調對比報告-打印版.pdf'), mode='print')
    print('已生成（打印版 v3）')
    build_pdf(md, os.path.join(base, '空調對比報告-手機版.pdf'), mode='mobile')
    print('已生成（手機版 v3）')
