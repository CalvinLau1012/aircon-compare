# -*- coding: utf-8 -*-
"""能源標籤顯示回歸：1–5 固定次序、核心 29 與全量分佈來源／總和正確（動態生成）

- 核心 29 靜態表：次序 1→2→3→4→5、數字同 models_data.MODELS 一致（防漂移）
- 全量動態表：canonical model（同 load_models 去重規則）／registration（逐筆登記）分開
- 生成 HTML／PDF 必須已展開 marker，且 1–5 全部存在（0 都顯示）
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import generate_html  # noqa: E402
from crawl_utils import (ENERGY_LEVELS, load_energy_distributions,  # noqa: E402
                         load_models, load_registrations)

INDEX = os.path.join(BASE, 'index.html')
MD = os.path.join(BASE, '空調對比報告.md')


def _table_rows(text):
    """解析 markdown 表格資料列 → [[cell, ...], ...]（略過表頭同分隔線）"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if not cells or all(set(c) <= set('-: ') for c in cells):
            continue
        rows.append(cells)
    return rows


def _as_int(cell):
    return int(re.sub(r'[^0-9]', '', cell) or '0')


# ---------------------------------------------------------------- 資料來源

def test_energy_distribution_sources_and_sums():
    reg, canon = load_energy_distributions()
    assert set(reg) <= set(ENERGY_LEVELS), f'registration 有非 1–5 級別：{set(reg) - set(ENERGY_LEVELS)}'
    assert set(canon) <= set(ENERGY_LEVELS)
    assert sum(reg.values()) == len(load_registrations()), 'registration 總和要等於登記數'
    assert sum(canon.values()) == len(load_models()), 'canonical 總和要等於唯一型號數'
    assert sum(canon.values()) < sum(reg.values()), 'canonical model 應少過 registration（有重複登記）'


# ---------------------------------------------------------------- 核心 29 靜態表

def test_md_core29_table_fixed_order_and_matches_models_data():
    text = open(MD, encoding='utf-8').read()
    assert '## ⚡ 能源標籤分析' in text
    section = text.split('## ⚡ 能源標籤分析', 1)[1].split('<!--', 1)[0]
    rows = _table_rows(section)
    core_rows = [r for r in rows if re.fullmatch(r'\**([1-5]) 級\**', r[0])]
    assert len(core_rows) == 5, f'核心 29 表要有齊 1–5 級：{core_rows}'
    assert [re.sub(r'[^1-5]', '', r[0]) for r in core_rows] == ['1', '2', '3', '4', '5']
    expected = generate_html.core_energy_counts()
    for row in core_rows:
        lv = re.sub(r'[^1-5]', '', row[0]) + '級'
        assert _as_int(row[1]) == expected[lv], f'{lv} 靜態表同 models_data 唔一致'
    assert sum(_as_int(r[1]) for r in core_rows) == len(generate_html.MODELS) == 29


# ---------------------------------------------------------------- 動態全量表

def test_dynamic_table_has_all_levels_in_order_and_correct_numbers():
    md = generate_html.energy_distribution_markdown()
    rows = _table_rows(md)
    data = [r for r in rows if r[0] != '能源級別']
    assert [re.sub(r'[^1-5]', '', r[0]) for r in data[:5]] == ['1', '2', '3', '4', '5'], (
        f'動態表次序錯：{data}')
    assert data[-1][0] == '**合計**'
    reg, canon = load_energy_distributions()
    core = generate_html.core_energy_counts()
    for lv, row in zip(ENERGY_LEVELS, data[:5]):
        assert (_as_int(row[1]), _as_int(row[2]), _as_int(row[3])) == (
            core[lv], canon[lv], reg[lv]), f'{lv} 數字來源唔一致'
    assert _as_int(data[-1][1]) == len(generate_html.MODELS)
    assert _as_int(data[-1][2]) == len(load_models())
    assert _as_int(data[-1][3]) == len(load_registrations())


def test_dynamic_table_shows_zero_levels(monkeypatch):
    """即使某級為 0，1–5 五列都要存在（唔可以隱藏）"""
    zeros = ({lv: 0 for lv in ENERGY_LEVELS}, {lv: 0 for lv in ENERGY_LEVELS})
    monkeypatch.setattr(generate_html, 'load_energy_distributions', lambda: zeros)
    rows = _table_rows(generate_html.energy_distribution_markdown())
    labels = [r[0] for r in rows if r[0] in ENERGY_LEVELS]
    assert labels == list(ENERGY_LEVELS)


def test_generated_html_contains_expanded_dynamic_energy_table():
    html = open(INDEX, encoding='utf-8').read()
    assert generate_html.ENERGY_DIST_MARKER not in html, '生成物唔應該再有動態 marker'
    rendered = generate_html.md_to_html(generate_html.ENERGY_DIST_MARKER)
    table = rendered[rendered.index('<table>'):rendered.index('</table>') + len('</table>')]
    assert table in html, 'index.html 嘅動態能源表同即時生成結果唔一致'
    labels = re.findall(r'<td>([1-5]級)</td>', table)
    assert labels[:5] == list(ENERGY_LEVELS), f'HTML 能源表次序錯：{labels[:5]}'
    rows = re.findall(r'<tr>(.*?)</tr>', table, re.S)
    assert any('<strong>合計</strong>' in r for r in rows), '動態表要有合計列'


def test_md_marker_present_for_build_expansion():
    text = open(MD, encoding='utf-8').read()
    assert generate_html.ENERGY_DIST_MARKER in text, 'md 要保留 marker 畀 build 展開'


# ---------------------------------------------------------------- 文字語境

def test_core29_energy_claim_is_scoped():
    text = open(MD, encoding='utf-8').read()
    assert '「定頻最高只有 3 級」只適用於上述核心 29 型號' in text
    assert '只能選變頻（1級）。定頻最高只有 3 級，長期開冷氣電費差距' not in text, (
        '唔可以保留未限定語境嘅市場級結論')
    assert '全量' in text and '3 級' in text


# ---------------------------------------------------------------- PDF 亦要展開

def test_pdf_uses_expanded_dynamic_sections(tmp_path, monkeypatch):
    import generate_pdf
    seen = {}
    real = generate_pdf.expand_dynamic_sections

    def spy(md_text):
        seen['marker_in'] = generate_html.ENERGY_DIST_MARKER in md_text
        out = real(md_text)
        seen['marker_out'] = generate_html.ENERGY_DIST_MARKER not in out
        return out

    monkeypatch.setattr(generate_pdf, 'expand_dynamic_sections', spy)
    generate_pdf.build_pdf(str(tmp_path / 'energy.pdf'))
    assert seen.get('marker_in') is True, 'PDF 應該收到含 marker 嘅 md 原文'
    assert seen.get('marker_out') is True, 'PDF 應該用展開後版本'
