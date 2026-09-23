#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 封包後精確 stage 發佈產物（唔再用 `git add -A`）。

- allowed = deploy_payload.json 列出嘅公開負載 + 明確 runtime 資料／狀態檔；
- manifest 用同 `gen-metadata` 一致嘅路徑契約（相對、無 ..、無重複、無
  metadata.json 自引用、檔案存在、無 symlink escape）；
- 任何 deletion（worktree 或已 staged）一律 fail-closed，唔會留低 unstaged deletion；
- rename／copy 拒絕；parse porcelain `-z` 正確處理雙路徑 entry；
- stage 之前先檢查 index 已有 staged paths；allowlist 外一律失敗；
- 任何錯誤之前唔會部分 stage；dry-run 唔改 index；
- 成功後 staged paths 精確等於 pre-staged ∪ 本次允許改動（兩者都必須在 allowlist）。

用法：
  python scripts/stage_artifacts.py [--repo .] [--manifest deploy_payload.json] [--dry-run]
退出碼：0 = 已 stage（或 dry-run 列出）；1 = 有未允許／deletion／git 錯誤。
"""
import argparse
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

RUNTIME_STATE = {
    'emsd_空調能源標籤.csv', 'emsd_receipt.json', 'emsd_raw_receipt.json',
    'metadata.json', 'new_models.json', 'update_queue.json',
    'official_specs.json', 'shew_official.json', 'rasonic_official.json',
    'carrier_official.json', 'general_official.json', 'specs.json',
    'official_batch_status.json',
    'biggo_prices.json', 'prices.json', 'prices_meta.json', 'gemini_prices.json',
    'model_blacklist.json', 'model_status.json',
}


def _git(repo, *args, check=True):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError('git %s 失敗：%s' % (' '.join(args),
                                                r.stderr.decode('utf-8', 'replace')[:300]))
    return r


def _decode_utf8(blob, label):
    try:
        return blob.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError(f'{label} 唔係有效 UTF-8（唔可以猜）：{e}')


def _parse_z(out):
    """worktree porcelain -z：XY<space>path；rename/copy 為 XY<space>new\0old。"""
    parts = out.split(b'\0')
    if parts and parts[-1] == b'':
        parts.pop()
    changed, renamed, deleted = [], [], []
    seen = set()
    i = 0
    while i < len(parts):
        e = _decode_utf8(parts[i], 'porcelain entry')
        i += 1
        if len(e) < 4 or e[2] != ' ':
            raise ValueError(f'porcelain entry 格式唔正確：{e!r}')
        status, path = e[:2], e[3:]
        if not path:
            raise ValueError('porcelain entry 空 path')
        if status[0] in 'RC' or status[1] in 'RC':
            renamed.append(path)
            if i < len(parts):
                i += 1  # skip old path token
            continue
        if path in seen:
            raise ValueError(f'worktree status 有重複 path：{path}')
        seen.add(path)
        if 'D' in status:
            deleted.append(path)
        else:
            changed.append(path)
    return changed, renamed, deleted


def _worktree_status(repo):
    out = _git(repo, 'status', '--porcelain', '-z').stdout
    return _parse_z(out)


def _parse_name_status_tokens(tokens):
    """純 parser：tokens 係 NUL 切開嘅 bytes（唔含尾隨空 token）。

    格式：M\0path\0、A\0path\0、D\0path\0、T\0path\0、
          Rnnn\0old\0new\0、Cnnn\0old\0new\0。
    拒絕 truncated／未知 status／空 path／duplicate／invalid UTF-8；唔准猜。
    回傳 (staged, deleted, renames)。
    """
    staged, deleted, renames = [], [], []
    seen = set()
    i = 0
    while i < len(tokens):
        status = _decode_utf8(tokens[i], 'staged status')
        i += 1
        if not re.match(r'^[MADRTUXBC]\d*$', status):
            raise ValueError(f'未知 staged status：{status!r}')
        code = status[0]
        if code in ('R', 'C'):
            if i + 1 >= len(tokens):
                raise ValueError('staged rename／copy entry truncated')
            old = _decode_utf8(tokens[i], 'rename old path')
            new = _decode_utf8(tokens[i + 1], 'rename new path')
            i += 2
            if not old or not new:
                raise ValueError('staged rename／copy 有空 path')
            for p in (old, new):
                if p in seen:
                    raise ValueError(f'staged 有重複 path：{p}')
                seen.add(p)
            renames.append({'status': status, 'old': old, 'new': new})
            continue
        if i >= len(tokens):
            raise ValueError('staged entry truncated（缺 path）')
        path = _decode_utf8(tokens[i], 'staged path')
        i += 1
        if not path:
            raise ValueError('staged 空 path')
        if path in seen:
            raise ValueError(f'staged 有重複 path：{path}')
        seen.add(path)
        if code == 'D':
            deleted.append(path)
        else:
            staged.append(path)
    return staged, deleted, renames


def _staged_status(repo):
    """`git diff --cached --name-status -z` → 嚴格 parser（見 _parse_name_status_tokens）。"""
    out = _git(repo, 'diff', '--cached', '--name-status', '-z').stdout
    tokens = out.split(b'\0')
    if tokens and tokens[-1] == b'':
        tokens.pop()
    return _parse_name_status_tokens(tokens)


def _staged_names(repo):
    out = _git(repo, 'diff', '--cached', '--name-only', '-z').stdout
    return sorted(p for p in _decode_utf8(out, 'staged names').split('\0') if p)


def _index_path(repo):
    out = _git(repo, 'rev-parse', '--git-path', 'index').stdout.decode('utf-8', 'replace').strip()
    return out if os.path.isabs(out) else os.path.join(repo, out)


def _snapshot_index(repo):
    path = _index_path(repo)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return path, f.read()
    return path, None


def _restore_index(path, data):
    if data is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + '.restore.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_gen_metadata():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'gen_metadata_for_stage', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='精確 stage 發佈產物')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    repo = args.repo
    manifest_path = args.manifest or os.path.join(repo, 'deploy_payload.json')

    try:
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        files = manifest.get('files')
        if not isinstance(files, list) or not files:
            raise ValueError('manifest files 必須係非空陣列')
        gen = _load_gen_metadata()
        # 先驗路徑語法；存在／symlink 驗證喺 deletion／rename 檢查之後（錯誤更精確）
        norm = gen.normalize_manifest_paths(files)
        allowed = set(norm) | RUNTIME_STATE
        changed, renamed, deleted = _worktree_status(repo)
        staged, staged_deleted, staged_renames = _staged_status(repo)
        if staged_renames:
            raise ValueError(f'已 staged 咗 rename／copy：{staged_renames[0]}；唔准')
        if staged is None:
            raise ValueError('已 staged 咗 rename；唔准 rename')
    except (OSError, ValueError, RuntimeError) as e:
        print(f'❌ stage_artifacts 失敗（fail-closed）：{e}', file=sys.stderr)
        return 1

    if renamed:
        print(f'❌ 有 rename：{renamed}；改名唔喺發佈 allowlist，拒絕', file=sys.stderr)
        return 1
    if deleted or staged_deleted:
        print(f'❌ 有 deletion（worktree={deleted}／staged={staged_deleted}）；'
              '發佈流程唔准刪檔，拒絕', file=sys.stderr)
        return 1
    unexpected_worktree = sorted(set(changed) - allowed)
    unexpected_staged = sorted(set(staged) - allowed)
    if unexpected_worktree or unexpected_staged:
        print('❌ 有 allowlist 以外嘅改動／staged 檔，唔會 stage 任何嘢：', file=sys.stderr)
        for p in unexpected_worktree:
            print(f'  - worktree: {p}', file=sys.stderr)
        for p in unexpected_staged:
            print(f'  - staged: {p}', file=sys.stderr)
        return 1
    # 最後才驗檔案存在／symlink escape（deletion／rename 已經喺上面阻斷）
    try:
        files = gen.validate_manifest_files(norm, base=repo)
    except (OSError, ValueError) as e:
        print(f'❌ manifest 檔案驗證失敗：{e}', file=sys.stderr)
        return 1
    to_add = sorted(p for p in changed if p in allowed and os.path.exists(os.path.join(repo, p)))
    if args.dry_run:
        print('（dry-run）會 stage：' + ('、'.join(to_add) or '（無改動）'))
        return 0

    expected = set(staged) | set(to_add)
    if to_add:
        # 執行前 snapshot index；invariant 失敗即原子恢復，唔可以留低部分 stage
        index_path, index_bytes = _snapshot_index(repo)
        try:
            _git(repo, 'add', '--', *to_add)
            final_staged = set(_staged_names(repo))
            if final_staged != expected:
                raise ValueError(
                    f'staged 結果唔符預期：actual={sorted(final_staged)} expected={sorted(expected)}')
        except (RuntimeError, ValueError) as e:
            try:
                _restore_index(index_path, index_bytes)
                restored = set(_staged_names(repo))
                if restored != set(staged):
                    print(f'⚠️ index 恢復後仍然唔符：{sorted(restored)}', file=sys.stderr)
            except OSError as re_err:
                print(f'⚠️ index 恢復失敗：{re_err}', file=sys.stderr)
            print(f'❌ stage 失敗，已恢復呼叫前 index：{e}', file=sys.stderr)
            return 1
    else:
        final_staged = set(_staged_names(repo))
        if final_staged != expected:
            print(f'❌ staged 結果唔符預期：actual={sorted(final_staged)} expected={sorted(expected)}',
                  file=sys.stderr)
            return 1
    if to_add:
        print(f'✅ 已精確 stage {len(to_add)} 個發佈產物：' + '、'.join(to_add))
    else:
        print('✅ 冇允許清單內嘅改動，無需 stage')
    return 0


if __name__ == '__main__':
    sys.exit(main())
