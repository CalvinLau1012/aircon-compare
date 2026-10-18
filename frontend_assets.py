#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic assembler for the controlled inline frontend sources.

Only files explicitly listed in ASSET_PATHS are read.  The result is a single
template string; runtime JSON is injected later by generate_html.build_html.
"""
from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).resolve().parent

ASSET_PATHS = (
    'frontend/public/shell.html',
    'frontend/public/style.css',
    'frontend/public/state.js',
    'frontend/public/list.js',
    'frontend/public/compare.js',
    'frontend/public/bootstrap.js',
    'frontend/public/status.js',
)


def _read(rel):
    if not isinstance(rel, str) or rel not in ASSET_PATHS:
        raise RuntimeError('frontend asset is not in the controlled manifest')
    path = BASE / rel
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f'frontend asset missing or symlink: {rel}')
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def load_template():
    values = {rel: _read(rel) for rel in ASSET_PATHS}
    shell = values['frontend/public/shell.html']
    if shell.count('__CSS__') != 1 or shell.count('__SCRIPTS__') != 1:
        raise RuntimeError('frontend shell must contain exactly one CSS and one script token')
    css = values['frontend/public/style.css']
    scripts = ''.join(values[rel] for rel in ASSET_PATHS
                      if rel.startswith('frontend/public/') and rel.endswith('.js'))
    template = shell.replace('__CSS__', css, 1).replace('__SCRIPTS__', scripts, 1)
    if '__CSS__' in template or '__SCRIPTS__' in template:
        raise RuntimeError('unassembled frontend token remains')
    return template
