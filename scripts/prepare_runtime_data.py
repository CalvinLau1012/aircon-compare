#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime 資料準備守衛（持久發佈管線用）

問題：舊 volume 嘅 `model_blacklist.json` / `model_status.json` key 係原始型號字串
（例如 `RC-X7U`），新程式碼（D11 canonical key）預期 `BRAND|NORM`。
`scripts/migrate_blacklist_keys.py` 只可以跑一次：canonical key 再過一次 norm_model
會變成另一個 key（唔係 idempotent），所以必須先偵測、後遷移、再驗證。

行為：
  1. 讀 model_blacklist.json / model_status.json；
  2. 若全部 key 已含 '|' → no-op（exit 0）；
  3. 若有舊格式 key → 以 subprocess 執行已審閱嘅遷移腳本（會自行備份 + 出報告）；
  4. 遷移後驗證：舊格式 key = 0、key 總數不變（碰撞由遷移腳本阻斷）。

用法：
  python scripts/prepare_runtime_data.py            # 準備（必要時遷移）
  python scripts/prepare_runtime_data.py --check    # 只檢查（未遷移會 exit 10）

Exit codes：0 = ready；1 = 失敗／阻斷；10 = 需要遷移（--check 模式）
"""
import argparse
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLACKLIST = os.path.join(BASE, 'model_blacklist.json')
STATUS = os.path.join(BASE, 'model_status.json')
MIGRATOR = os.path.join(BASE, 'scripts', 'migrate_blacklist_keys.py')


def _models_of(path):
    """回傳 (kind, models)；kind 為 'blacklist' 或 'status'。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'{os.path.basename(path)} 頂層唔係 object')
    if 'models' in data:
        models = data['models']
        kind = 'blacklist'
    else:
        models = data
        kind = 'status'
    if not isinstance(models, dict):
        raise ValueError(f'{os.path.basename(path)} 型號資料唔係 object')
    return kind, models


def legacy_keys(path):
    """回傳未 canonical 化嘅 key 清單（唔存在檔案 = 空清單）。"""
    if not os.path.exists(path):
        return []
    _, models = _models_of(path)
    return [str(k) for k in models if '|' not in str(k)]


def _summary(path):
    if not os.path.exists(path):
        return None
    _, models = _models_of(path)
    return len(models)


def main(argv=None):
    ap = argparse.ArgumentParser(description='runtime 資料準備（canonical key 遷移守衛）')
    ap.add_argument('--check', action='store_true', help='只檢查，唔會修改（需要遷移時 exit 10）')
    args = ap.parse_args(argv)

    if not os.path.exists(BLACKLIST):
        print('❌ 缺少 model_blacklist.json，無法準備 runtime 資料')
        return 1

    bl_legacy = legacy_keys(BLACKLIST)
    st_legacy = legacy_keys(STATUS)
    print(f'黑名單 key：{_summary(BLACKLIST)}（舊格式 {len(bl_legacy)}）'
          f' · model_status key：{_summary(STATUS)}（舊格式 {len(st_legacy)}）')

    if not bl_legacy and not st_legacy:
        print('✅ runtime 資料已係 canonical 格式，無需遷移')
        return 0

    if args.check:
        print(f'需要遷移：黑名單 {len(bl_legacy)} 個、model_status {len(st_legacy)} 個舊 key')
        return 10

    before_bl = _summary(BLACKLIST)
    before_st = _summary(STATUS)
    print(f'執行一次性遷移：{MIGRATOR}')
    proc = subprocess.run([sys.executable, MIGRATOR], cwd=BASE,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    sys.stdout.write(proc.stdout or '')
    sys.stderr.write(proc.stderr or '')
    if proc.returncode != 0:
        print('❌ 遷移失敗（碰撞或錯誤），阻斷部署')
        return 1

    after_bl_legacy = legacy_keys(BLACKLIST)
    after_st_legacy = legacy_keys(STATUS)
    after_bl = _summary(BLACKLIST)
    after_st = _summary(STATUS)
    problems = []
    if after_bl_legacy or after_st_legacy:
        problems.append(f'遷移後仍有舊 key：blacklist={len(after_bl_legacy)} status={len(after_st_legacy)}')
    if after_bl != before_bl:
        problems.append(f'黑名單 key 數改變：{before_bl} → {after_bl}')
    if before_st is not None and after_st != before_st:
        problems.append(f'model_status key 數改變：{before_st} → {after_st}')
    if problems:
        for p in problems:
            print(f'❌ {p}')
        return 1

    print(f'✅ 遷移完成並驗證：黑名單 {after_bl} key（全部 canonical）'
          f' · model_status {after_st} key')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
