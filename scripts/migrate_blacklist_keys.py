#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黑名單 canonical key 一次性遷移（治理改善方案 M1 PR-2）

- 讀 model_blacklist.json（舊：raw 型號字串 key）
- 用 crawl_utils.load_brand_lookup() 解品牌，逐 key 轉 canonical（BRAND|NORM）
- orphan（搵唔到品牌）→ UNKNOWN|NORM，記入報告（唔會靜默當成功）
- 碰撞（兩個舊 key 指向同一 canonical）→ 阻斷退出 1
- 備份原檔 + 輸出 docs/blacklist-migration-2026-09.md 報告
- model_status.json 嘅 tracking key 一併遷移

用法：python scripts/migrate_blacklist_keys.py
"""
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from crawl_utils import canonical_model_key, load_brand_lookup, norm_model  # noqa: E402

BLACKLIST = os.path.join(BASE, 'model_blacklist.json')
STATUS = os.path.join(BASE, 'model_status.json')
BACKUP_SUFFIX = '-bak-canonical-migration'
REPORT = os.path.join(BASE, 'docs', 'blacklist-migration-2026-09.md')


def migrate_dict(models, brand_lookup):
    """逐 key 遷移；回傳 (new_models, matched, orphan_list, collision_list)"""
    new_models = {}
    matched = 0
    orphans = []
    collisions = []
    for raw, info in models.items():
        raw = str(raw)
        norm = norm_model(raw)
        brand = brand_lookup.get(norm)
        key = canonical_model_key(brand, raw) if brand else f'UNKNOWN|{norm}'
        if brand:
            matched += 1
        else:
            orphans.append(raw)
        if key in new_models:
            collisions.append((raw, key))
            continue
        new_models[key] = info
    return new_models, matched, orphans, collisions


def main():
    with open(BLACKLIST, encoding='utf-8') as f:
        data = json.load(f)
    models = data.get('models') if isinstance(data, dict) else data
    if not isinstance(models, dict):
        print('❌ model_blacklist.json 結構唔啱（預期 {models:{...}}）')
        sys.exit(1)

    brand_lookup = load_brand_lookup()
    new_models, matched, orphans, collisions = migrate_dict(models, brand_lookup)
    if collisions:
        print(f'❌ 碰撞 {len(collisions)} 個，唔可以遷移：')
        for raw, key in collisions:
            print(f'   {raw!r} → {key}')
        sys.exit(1)

    # 備份 + 寫新黑名單
    backup = BLACKLIST + BACKUP_SUFFIX
    shutil.copy2(BLACKLIST, backup)
    data['models'] = new_models
    data['version'] = data.get('version', 1)
    data['updated'] = time.strftime('%Y-%m-%d')
    with open(BLACKLIST, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # model_status.json tracking key 一併遷移
    status_migrated = status_orphan = 0
    if os.path.exists(STATUS):
        with open(STATUS, encoding='utf-8') as f:
            st = json.load(f)
        if isinstance(st, dict):
            new_st, _, st_orphan, st_coll = migrate_dict(st, brand_lookup)
            if st_coll:
                print(f'❌ model_status.json 碰撞 {len(st_coll)} 個，唔可以遷移')
                sys.exit(1)
            status_migrated = len(st) - len(st_orphan)
            status_orphan = len(st_orphan)
            with open(STATUS, 'w', encoding='utf-8') as f:
                json.dump(new_st, f, ensure_ascii=False, indent=2)

    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('# 黑名單 canonical key 遷移報告\n\n')
        f.write(f'- **日期**：{time.strftime("%Y-%m-%d")}\n')
        f.write(f'- **遷移前 key 數**：{len(models)}\n')
        f.write(f'- **遷移後 key 數**：{len(new_models)}\n')
        f.write(f'- **matched（品牌已解析）**：{matched}\n')
        f.write(f'- **orphan（品牌未解析 → UNKNOWN|NORM）**：{len(orphans)}\n')
        f.write(f'- **碰撞**：{len(collisions)}（非零會阻斷）\n')
        f.write(f'- **備份**：`{os.path.basename(backup)}`\n')
        f.write(f'- **model_status.json tracking key**：遷移 {status_migrated} 個，orphan {status_orphan} 個\n\n')
        if orphans:
            f.write('## orphan 清單\n\n')
            for o in orphans:
                f.write(f'- `{o}` → `UNKNOWN|{norm_model(o)}`\n')
            f.write('\n> orphan 型號唔喺現行 EMSD CSV / 官方 JSON / 核心 29 入面，'
                    '頁面唔會顯示，因此唔會影響停售標示；保留喺黑名單以備復核。\n')
    print(f'✅ 黑名單遷移完成：{len(models)} → {len(new_models)}（matched {matched} · orphan {len(orphans)} · 碰撞 {len(collisions)}）')
    print(f'  備份：{backup}')
    print(f'  報告：{REPORT}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
