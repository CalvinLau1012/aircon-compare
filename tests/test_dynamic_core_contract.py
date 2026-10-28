"""Core catalogue size and missing specifications are data, not defaults."""
import copy
import pytest
import generate_html as g

@pytest.mark.parametrize('size', [0, 1, 28, 30, 40])
def test_core_size_changes_without_fixed_expectation(monkeypatch, size):
    rows = [dict(model=f'FIXTURE-{i}', energy='1級') for i in range(size)]
    monkeypatch.setattr(g, 'MODELS', rows)
    result = g.energy_distribution_markdown()
    assert f'| **合計** | {size}' in result
    assert '核心 29' not in result


def test_unknown_model_does_not_inherit_core_specifications(monkeypatch):
    rows = [dict(model='UNKNOWN-FIXTURE')]
    monkeypatch.setattr(g, 'MODELS', rows)
    g.apply_specs_override()
    assert rows[0]['mount'] == rows[0]['mode'] == rows[0]['remote'] == '待查'


def test_explicit_specs_are_preserved(monkeypatch):
    rows = [dict(model='UNKNOWN-FIXTURE', mount='分體式', mode='冷暖', remote='➖')]
    expected = copy.deepcopy(rows)
    monkeypatch.setattr(g, 'MODELS', rows)
    g.apply_specs_override()
    assert rows == expected


def test_shell_core_count_is_a_build_token():
    assert '__CORE_COUNT__' in g.HTML_TEMPLATE
    assert '<div class="n">29</div>' not in g.HTML_TEMPLATE
    assert '220 型號' not in g.HTML_TEMPLATE

@pytest.mark.parametrize('duplicate', [False, True])
def test_registration_equality_and_duplicates(tmp_path, duplicate):
    import csv
    from crawl_utils import load_energy_distributions, load_models, load_registrations
    path = tmp_path / 'emsd.csv'
    row = ['FIXTURE', 'MODEL1', '', '', '1'] + [''] * 10
    rows = [row, row] if duplicate else [row]
    with path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['brand', 'model'] + [''] * 13)
        writer.writerows(rows)
    reg, canon = load_energy_distributions(path)
    assert sum(reg.values()) == len(load_registrations(path)) == len(rows)
    assert sum(canon.values()) == len(load_models(path)) == 1

@pytest.mark.parametrize('delta', [-1, 1])
def test_rendered_hero_tracks_core_catalogue(monkeypatch, delta):
    rows = copy.deepcopy(g.MODELS)
    if delta < 0:
        rows.pop()
    else:
        extra = copy.deepcopy(rows[0])
        extra['model'] = 'NEW-FIXTURE-MODEL'
        rows.append(extra)
    monkeypatch.setattr(g, 'MODELS', rows)
    outputs = []
    monkeypatch.setattr(g, 'write_html_output', lambda path, html: outputs.append(html))
    g.build_html()
    assert f'<div class="n">{len(rows)}</div>' in outputs[0]
    assert '__CORE_COUNT__' not in outputs[0]
