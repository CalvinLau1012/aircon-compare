#!/usr/bin/env bash
# =============================================================================
# sync_code（run-update.sh --sync-only）fixture 測試
#
# 驗證 rsync --delete 語義：
#   - image source 係程式碼真源：stale 程式碼（檔案／目錄）會被刪除；
#   - runtime *.json / *.csv / *-bak*、web/、logs、快取、migration 報告完全保留；
#   - deploy_payload.json（code manifest）仍然同步；
#   - 生成物（index.html / 報告 PDF）不被 image 舊版覆蓋。
#
# 需要 rsync（本機或伺服器）；冇 rsync 時 exit 77（skip）。
# 用法：bash release/sandbox/test_sync_code.sh
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE="$(cd "$HERE/../.." && pwd)"
RUN_UPDATE="$WORKTREE/docker/run-update.sh"

if ! command -v rsync >/dev/null 2>&1; then
  echo "SKIP: 沒有 rsync"
  exit 77
fi

TMP="$(mktemp -d /tmp/aircon-sync-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
SRC="$TMP/src"
DST="$TMP/dst"
mkdir -p "$SRC/scripts" "$SRC/docs" "$DST/scripts" "$DST/web" "$DST/__pycache__" \
         "$DST/stale_dir" "$DST/docs" "$DST/.pytest_cache"

# ---- image source（程式碼真源）----
printf 'NEW-HELLO\n' >"$SRC/hello.py"
printf 'NEW-SCRIPT\n' >"$SRC/scripts/new.py"
printf '{"files":["index.html"]}\n' >"$SRC/deploy_payload.json"
printf 'NEW-DOCS\n' >"$SRC/docs/guide.md"
printf 'FROM scratch\n' >"$SRC/Dockerfile"
printf 'SRC-CSV-SHOULD-NOT-OVERWRITE\n' >"$SRC/emsd_空調能源標籤.csv"
printf '{"src":true}\n' >"$SRC/model_blacklist.json"
printf 'NEW-INDEX-ARTIFACT\n' >"$SRC/index.html"

# ---- volume（runtime 狀態）----
printf 'OLD-HELLO\n' >"$DST/hello.py"
printf 'STALE-MODULE\n' >"$DST/legacy_removed.py"
printf 'STALE-SUBMODULE\n' >"$DST/stale_dir/old.py"
printf 'OLD-SCRIPT\n' >"$DST/scripts/new.py"
printf '{"files":["old"]}\n' >"$DST/deploy_payload.json"
printf 'OLD-DOCS\n' >"$DST/docs/guide.md"
printf 'RUNTIME-JSON\n' >"$DST/runtime.json"
printf 'RUNTIME-CSV\n' >"$DST/emsd_空調能源標籤.csv"
printf '{"bak":true}\n' >"$DST/model_blacklist.json-bak-canonical-migration"
printf '{"status":{}}\n' >"$DST/model_status.json"
printf 'WEB-INDEX\n' >"$DST/web/index.html"
printf 'WEB-OLD-PDF\n' >"$DST/web/空調對比報告.pdf"
printf 'MIGRATION-REPORT\n' >"$DST/docs/blacklist-migration-2026-09.md"
printf 'PYC\n' >"$DST/__pycache__/x.pyc"
printf 'LOG\n' >"$DST/update.log"
printf 'LIVE-INDEX-ARTIFACT\n' >"$DST/index.html"
printf '{"build":"live"}\n' >"$DST/metadata.json"
printf 'CACHE\n' >"$DST/.pytest_cache/CACHEDIR.TAG"

# ---- 執行 sync-only ----
AIRCON_REPO="$DST" \
AIRCON_WEB_DIR="$DST/web" \
AIRCON_SYNC_SRC="$SRC" \
AIRCON_DATA_MOUNT="$TMP/no-such-data" \
AIRCON_LOG="$TMP/update.log" \
AIRCON_LOCK="$TMP/update.lock" \
  bash "$RUN_UPDATE" --sync-only >"$TMP/out.log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || { echo "❌ sync-only 失敗（rc=$rc）"; cat "$TMP/out.log"; exit 1; }

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
expect_eq() { if [ "$(cat "$2" 2>/dev/null)" = "$3" ]; then ok "$1"; else bad "$1（got=$(cat "$2" 2>/dev/null | head -c 40)）"; fi; }
expect_absent() { if [ ! -e "$1" ]; then ok "$2"; else bad "$2（仍然存在）"; fi; }

expect_eq "程式碼更新：hello.py = src" "$DST/hello.py" "NEW-HELLO"
expect_eq "程式碼更新：scripts/new.py" "$DST/scripts/new.py" "NEW-SCRIPT"
expect_eq "deploy_payload.json 有同步" "$DST/deploy_payload.json" '{"files":["index.html"]}'
expect_eq "文件更新：docs/guide.md" "$DST/docs/guide.md" "NEW-DOCS"
expect_absent "$DST/legacy_removed.py" "stale 頂層程式碼已刪除"
expect_absent "$DST/stale_dir" "stale 子目錄已刪除"
expect_eq "runtime JSON 保留" "$DST/runtime.json" "RUNTIME-JSON"
expect_eq "runtime CSV 保留（唔被 src 覆蓋）" "$DST/emsd_空調能源標籤.csv" "RUNTIME-CSV"
expect_eq "blacklist 遷移備份保留" "$DST/model_blacklist.json-bak-canonical-migration" '{"bak":true}'
expect_eq "model_status.json 保留" "$DST/model_status.json" '{"status":{}}'
expect_eq "web/ 保留" "$DST/web/index.html" "WEB-INDEX"
expect_eq "web/PDF 保留" "$DST/web/空調對比報告.pdf" "WEB-OLD-PDF"
expect_eq "migration 報告保留" "$DST/docs/blacklist-migration-2026-09.md" "MIGRATION-REPORT"
expect_eq "__pycache__ 保留" "$DST/__pycache__/x.pyc" "PYC"
expect_eq "update.log 保留" "$DST/update.log" "LOG"
expect_eq "生成物 index.html 唔被舊 image 覆蓋" "$DST/index.html" "LIVE-INDEX-ARTIFACT"
expect_eq "metadata.json 保留" "$DST/metadata.json" '{"build":"live"}'
expect_eq ".pytest_cache 保留" "$DST/.pytest_cache/CACHEDIR.TAG" "CACHE"

echo "== sync_code 測試：PASS=$PASS FAIL=$FAIL =="
[ "$FAIL" -eq 0 ]
