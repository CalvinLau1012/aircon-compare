# -*- coding: utf-8 -*-
"""Fix A 回歸：動態型號數字（Hero／OG／description）＋ registration/model 計數分離（D12）

治理要求：
- 當前狀態數字必須由建置時實際資料算出，唔可以硬編（1,927／1,854 只可以存在於
  明確標示歷史事件嘅文檔段落，唔可以出現喺生成物）；
- registrationCount（EMSD 登記記錄）同 modelCount（canonical product view）
  係兩個唔同概念，顯示層必須分開標示。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import generate_html  # noqa: E402
from crawl_utils import load_models, load_registrations  # noqa: E402

INDEX = os.path.join(BASE, 'index.html')
LEGACY_HARDCODED = ('1,927', '1,854')


def _index_html():
    with open(INDEX, encoding='utf-8') as f:
        return f.read()


def _build_time_counts():
    """同 generate_html.build_html() 一樣嘅計數來源（頁面實際顯示嘅數字）"""
    models = len(generate_html.MODELS) + len(generate_html.load_emsd_models())
    regs = len(load_registrations())
    return models, regs


def test_template_has_no_hardcoded_current_counts():
    tpl = generate_html.HTML_TEMPLATE
    for n in LEGACY_HARDCODED:
        assert n not in tpl, f'HTML 模板唔應該硬編舊計數 {n}'
    assert '__TOTAL_MODELS__' in tpl, '模板要有建置時型號數 placeholder'
    assert '__EMSD_REGISTRATIONS__' in tpl, '模板要有建置時登記數 placeholder'


def test_generated_index_counts_match_build_time_snapshot():
    models, regs = _build_time_counts()
    assert models > 0 and regs >= models
    html = _index_html()
    # Hero：登記數同型號數分開標示
    assert f'（{regs:,} 筆登記 · {models:,} 型號）' in html, 'Hero 要用建置時實際登記／型號數'
    # Open Graph／meta description：用型號數
    assert f'香港空調對比報告 · {models:,} 型號 · EMSD + 官網核實' in html
    assert f'香港空調對比報告：{models:,} 型號' in html
    # 報告內文（嵌入嘅 空調對比報告.md）亦已同步
    assert f'全量資料庫 {models:,} 型號' in html
    for n in LEGACY_HARDCODED:
        assert n not in html, f'index.html 仍然有舊硬編數字 {n}（歷史數字只可留喺標明歷史嘅文檔）'


def test_registration_count_and_model_count_are_distinct_concepts():
    """EMSD CSV 有重複登記：registrationCount > modelCount；兩者唔可以混用"""
    regs = len(load_registrations())
    models = len(load_models())
    assert regs > models, f'預期登記數 {regs} 大於型號數 {models}（D12）'
    html = _index_html()
    assert f'{regs:,} 筆登記' in html, '必須用「筆登記」字眼標示 registrationCount'
    assert '全量資料庫 1,927' not in html and '全量 1,854' not in html
