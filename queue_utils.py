#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_queue.json 單一契約（fetch_emsd／advance_queue／run_official_batch／workflow 共用）

契約（fail-closed；所有入口一致）：
- 檔案唔存在 → `{'stage': 0, 'models': []}`（清楚初始值）；
- 存在但讀取／JSON／結構錯誤 → raise `QueueError`，**唔可以默默 reset**；
- `stage` 必須 exact int（`bool` 唔算 int）而且只准 `0`／`1`／`2`；
- `models` 必須 array；每項 trim 後非空字串；用 canonical `norm_model` 檢查重複；
- `stage == 0` → `models` 必須空；`stage in (1, 2)` → `models` 必須非空；
- 未知額外欄位允許，writer 原樣保留（唔會靜默丟失）。

原子寫入：同目錄 tmp + flush + fsync + `os.replace`；失敗清理 tmp，保留舊 bytes。
"""
import json
import os

from crawl_utils import norm_model

BASE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE, 'update_queue.json')


class QueueError(ValueError):
    """update_queue.json 契約錯誤（讀取／解析／結構／內容）。"""


def validate_queue(q, label='update_queue.json'):
    """驗證 in-memory queue；任何違反契約即 raise QueueError。"""
    if not isinstance(q, dict):
        raise QueueError(f'{label} 頂層必須係 object（got {type(q).__name__}）')
    if 'stage' not in q:
        raise QueueError(f'{label} 缺少 stage')
    stage = q['stage']
    if not isinstance(stage, int) or isinstance(stage, bool):
        raise QueueError(f'{label} stage 必須係整數（唔接受 bool／{type(stage).__name__}）')
    if stage not in (0, 1, 2):
        raise QueueError(f'{label} stage 只准 0／1／2（got {stage}）')
    if 'models' not in q or not isinstance(q['models'], list):
        raise QueueError(f'{label} 缺少 models array')
    seen = set()
    for i, m in enumerate(q['models']):
        if not isinstance(m, str):
            raise QueueError(f'{label} models[{i}] 必須係字串（got {type(m).__name__}）')
        s = m.strip()
        if not s:
            raise QueueError(f'{label} models[{i}] 唔可以空白')
        key = norm_model(s)
        if key in seen:
            raise QueueError(f'{label} models 有 canonical 重複：{s!r}')
        seen.add(key)
    if stage == 0 and q['models']:
        raise QueueError(f'{label} stage 0 時 models 必須為空')
    if stage in (1, 2) and not q['models']:
        raise QueueError(f'{label} stage {stage} 時 models 必須非空')
    return q


def load_queue(path=None):
    """讀取並驗證 queue；missing 回預設，其餘錯誤 raise（唔會 reset）。"""
    path = path or QUEUE_PATH
    if not os.path.exists(path):
        return {'stage': 0, 'models': []}
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        raise QueueError(f'update_queue.json 讀取失敗：{e}')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        raise QueueError(f'update_queue.json 唔係有效 UTF-8：{e}')
    try:
        q = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(
            QueueError(f'update_queue.json 唔接受非標準常數：{c}')))
    except json.JSONDecodeError as e:
        raise QueueError(f'update_queue.json JSON 解析失敗：{e}')
    return validate_queue(q)


def save_queue(q, path=None):
    """驗證後原子寫入；失敗清理 tmp 並 raise，唔會破壞舊 bytes。"""
    path = path or QUEUE_PATH
    validate_queue(q)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(q, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise QueueError(f'update_queue.json 寫入失敗：{e}')
