#!/usr/bin/env bash
# shellcheck disable=SC1090  # source 路徑係執行時計出嘗，ShellCheck 跟唔到
# =============================================================================
# release-299c3e9.sh sandbox 測試（唔需要 root / docker / 真 volume）
#
# 覆蓋（第二輪加固）：
#   s1  dry-run 唔會寫任何嘢
#   s2  path guard（非 whitelist / symlink 越界）
#   s3  tarball hash 唔符 → 阻斷；build 後 volume 資料漂移 → apply 阻斷
#   s4  部署成功（備份、web 更新、release-record）
#   s5  回滾：原有檔案還原 + 部署新增檔案（web/PDF、web/CSV、根 PDF）精確刪除
#   s6  失敗自動回滾（健康檢查失敗 / image 唔存在）
#   s7  重跑安全（apply 兩次 / rollback 兩次）
#   s8  rollback 入口權限：普通使用者唔讀 root-only 備份目錄、直達 sudo 邊界
#   s9  root 解析 latest；stopped container 解析；無／多個目標拒絕
#   s10 TOCTOU：staged-data 改／刪、metadata payload、release image ID 被換
#   s11 備份失敗安全：mkdir 失敗、停機後 hook 失敗、archive 截斷、manifest 消失、tar 讀取失敗
#   s12 sync_code --delete：stale code 刪除、runtime 資料／web 保留（有 rsync 先跑）
#
# 用法：bash release/sandbox/run_sandbox_tests.sh
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE="$(cd "$HERE/../.." && pwd)"
SCRIPT="$WORKTREE/release/release-299c3e9.sh"
PYTHON="${PYTHON:-python3}"
COMMIT="299c3e9e518077de7a9bd52e88eaa9d23e69f02f"
FAKE_DOCKER="$HERE/fake_docker.sh"
FAKE_CURL="$HERE/fake_curl.sh"
FAKE_SUDO="$HERE/fake_sudo.sh"
RELEASE_ID="299c3e9"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n' "$1"; }
assert_true() { if eval "$1"; then ok "$2"; else bad "$2"; fi; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3（got=$1 want=$2）"; fi; }
assert_contains() { if grep -q -- "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3（$1 冇 '$2'）"; fi; }
assert_not_contains() { if grep -q -- "$2" "$1" 2>/dev/null; then bad "$3（$1 竟然有 '$2'）"; else ok "$3"; fi; }

# ---------- fixture helpers ----------
build_fixture() { # $1=dir $2=build $3=deployTime [$4=append-pdf-marker]
  local d="$1" build="$2" dtime="$3" marker="${4:-}"
  mkdir -p "$d/web"
  cp "$WORKTREE/index.html" "$d/index.html"
  cp "$WORKTREE/空調對比報告.pdf" "$d/空調對比報告.pdf"
  cp "$WORKTREE/emsd_空調能源標籤.csv" "$d/emsd_空調能源標籤.csv"
  cp "$WORKTREE/deploy_payload.json" "$d/"
  cp -a "$WORKTREE/scripts" "$d/scripts"
  cp -a "$WORKTREE/docs" "$d/docs"
  [ -n "$marker" ] && printf '%s' "$marker" >>"$d/空調對比報告.pdf"
  local dhash
  dhash="sha256:$(sha256sum "$d/emsd_空調能源標籤.csv" | cut -d' ' -f1)"
  "$PYTHON" "$d/scripts/gen-metadata.py" --stage core --out "$d/meta.core.json" --force \
    --version 1.2.8 --build "$build" --commit "$COMMIT" --workflow-run-id "sandbox-$build" \
    --dataset-date 2026-09-13 --dataset-date-basis retrieval-date-fallback \
    --dataset-retrieved-at "$dtime" \
    --dataset-source-url "https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php" \
    --dataset-snapshot-id emsd-2026-09-13 --dataset-hash "$dhash" \
    --record-count 1814 --raw-record-count 1863 --registration-count 1863 --model-count 1814 >/dev/null
  "$PYTHON" "$d/scripts/gen-metadata.py" --stage finalize --core "$d/meta.core.json" \
    --payload-manifest "$d/deploy_payload.json" --out "$d/metadata.json" --force >/dev/null
  rm -f "$d/meta.core.json"
  cp "$d/index.html" "$d/空調對比報告.pdf" "$d/emsd_空調能源標籤.csv" "$d/metadata.json" "$d/web/"
}

setup() {
  TMP="$(mktemp -d /tmp/aircon-sandbox.XXXXXX)"
  VOL="$TMP/vol"
  STAGE="$TMP/stage"
  BACKUP="$TMP/backups"
  mkdir -p "$VOL" "$BACKUP" "$TMP/src/docker"
  printf 'FROM scratch\n' >"$TMP/src/docker/Dockerfile"
  printf '%s\n' "$COMMIT" >"$TMP/src/.release-commit"
  tar -czf "$TMP/src.tar.gz" -C "$TMP/src" .
  local sha
  sha="$(sha256sum "$TMP/src.tar.gz" | cut -d' ' -f1)"
  printf '{"releaseId":"299c3e9","commit":"%s","tarballSha256":"%s"}\n' "$COMMIT" "$sha" >"$TMP/manifest.json"

  # 部署前 volume 狀態：web/ 只有 index.html + metadata.json（同真實站一致：PDF/CSV 係 404）
  printf '<html>OLD-INDEX</html>\n' >"$VOL/index.html"
  cp "$WORKTREE/emsd_空調能源標籤.csv" "$VOL/emsd_空調能源標籤.csv"
  printf '{"version":1,"updated":"2026-08-26","models":{"RC-X7U":{}}}\n' >"$VOL/model_blacklist.json"
  cat >"$VOL/metadata.json" <<'EOF'
{"schemaVersion":"1.0.0","version":"1.2.8","build":"B-old","commit":"299c3e9e518077de7a9bd52e88eaa9d23e69f02f",
 "deployTime":"2026-09-13T00:00:00Z","workflowRunId":"old","deploymentType":"release",
 "releasePayloadHash":"sha256:old","datasetDate":"2026-09-13","datasetDateBasis":"retrieval-date-fallback",
 "datasetRetrievedAt":"2026-09-13T00:00:00Z","datasetSourceUrl":"https://example.com","datasetSnapshotId":"x",
 "datasetHash":"sha256:x","recordCount":1}
EOF
  mkdir -p "$VOL/web"
  printf '<html>OLD-WEB-INDEX</html>\n' >"$VOL/web/index.html"
  cp "$VOL/metadata.json" "$VOL/web/metadata.json"

  build_fixture "$TMP/fixture-staging" "B-stage" "2026-09-13T10:00:00Z"
  build_fixture "$TMP/fixture-apply" "B-apply" "2026-09-13T10:30:00Z" "APPLY-PDF"
}

env_for() {
  unset AIRCON_ENFORCE_PATH_GUARD FAKE_FAIL_CURL FAKE_FAIL_IMAGE FAKE_FAIL_BUILD \
        FAKE_FAIL_COMPOSE FAKE_FAIL_RUN FAKE_APP_FIXTURE FAKE_PS_NONE FAKE_PS_AMBIGUOUS \
        FAKE_FAIL_IMAGE_INSPECT_LATEST FAKE_FAIL_IMAGE_INSPECT_PRE FAKE_FAIL_TAG \
        FAKE_TAG_ID_MISMATCH FAKE_IMAGE_ID AIRCON_SANDBOX_HOOK_AFTER_STOP \
        AIRCON_SANDBOX_HOOK_VERIFY_BACKUP 2>/dev/null || true
  export AIRCON_BASE_DIR="$TMP"
  export AIRCON_STAGE_ROOT="$STAGE"
  export AIRCON_BACKUP_ROOT="$BACKUP"
  export AIRCON_TARBALL="$TMP/src.tar.gz"
  export AIRCON_MANIFEST="$TMP/manifest.json"
  export AIRCON_DOCKER_BIN="$FAKE_DOCKER"
  export AIRCON_CURL_BIN="$FAKE_CURL"
  export AIRCON_SUDO_BIN="$FAKE_SUDO"
  export AIRCON_ALLOW_NONROOT=1
  export AIRCON_ALLOW_ANY_VOL=1
  export AIRCON_SANDBOX_AUTO_CONFIRM=1
  export AIRCON_VOL_DIR="$VOL"
  export AIRCON_VOL_NAME="$VOL"
  export AIRCON_HEALTH_URL="http://127.0.0.1/index.html"
  export AIRCON_IMAGE_REPO="aircon-compare"
  export AIRCON_COMPOSE_FILE="$TMP/compose.yaml"
  export FAKE_DOCKER_LOG="$TMP/docker.log"
  export FAKE_CURL_LOG="$TMP/curl.log"
  export FAKE_SUDO_LOG="$TMP/sudo.log"
  export FAKE_CURL_DIR="$VOL/web"
  FAKE_IMAGE_ID_OLD="sha256:$(printf 'a%.0s' {1..64})"
  FAKE_IMAGE_ID_RELEASE="sha256:$(printf 'c%.0s' {1..64})"
  export FAKE_IMAGE_ID_OLD FAKE_IMAGE_ID_RELEASE
  export FAKE_DOCKER_STATE="$TMP/docker-state"
  : >"$FAKE_DOCKER_STATE"
  touch "$FAKE_DOCKER_LOG" "$FAKE_CURL_LOG" "$FAKE_SUDO_LOG"
  : >"$FAKE_DOCKER_LOG"; : >"$FAKE_CURL_LOG"; : >"$FAKE_SUDO_LOG"
}

vol_state() { ( cd "$VOL" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ); }

build_staging() {
  export FAKE_APP_FIXTURE="$TMP/fixture-staging"
  ( source "$SCRIPT"; root_build ) >"$TMP/build.log" 2>&1
}

apply_staging() {
  export FAKE_APP_FIXTURE="$TMP/fixture-apply"
  ( source "$SCRIPT"; root_apply ) >"$TMP/apply.log" 2>&1
}

fresh_deploy() { # setup+env+build+apply；成功 return 0
  setup; env_for
  build_staging || { bad "root_build 應成功：$(tail -2 "$TMP/build.log")"; return 1; }
  apply_staging || { bad "root_apply 應成功：$(tail -2 "$TMP/apply.log")"; return 1; }
  return 0
}

# ============================ scenarios ============================
s1_dry_run_no_write() {
  setup; env_for
  local before after
  before="$(vol_state)"
  ( source "$SCRIPT"; cmd_preflight ) >"$TMP/out.log" 2>&1
  local rc=$?
  after="$(vol_state)"
  assert_eq "$rc" 0 "preflight 成功（rc=0）"
  assert_eq "$before" "$after" "dry-run 冇改動 volume"
  assert_not_contains "$FAKE_DOCKER_LOG" "stop" "dry-run 冇停容器"
}

s2_path_guard() {
  setup; env_for
  export AIRCON_ENFORCE_PATH_GUARD=1
  export AIRCON_VOL_DIR="$TMP/fake-outside-dir"
  mkdir -p "$AIRCON_VOL_DIR/web"
  ( source "$SCRIPT"; resolve_targets ) >"$TMP/out.log" 2>&1
  assert_true "[ $? -ne 0 ]" "非 whitelist 路徑被拒"
  export AIRCON_VOL_DIR="$TMP/link-vol"
  ln -sfn "$VOL" "$TMP/link-vol"
  ( source "$SCRIPT"; resolve_targets ) >"$TMP/out2.log" 2>&1
  assert_true "[ $? -ne 0 ]" "symlink volume 被拒"
}

s3_guard_refusals() {
  setup; env_for
  printf '{"releaseId":"299c3e9","commit":"%s","tarballSha256":"deadbeef"}\n' "$COMMIT" >"$TMP/manifest.json"
  ( source "$SCRIPT"; cmd_preflight ) >"$TMP/out.log" 2>&1
  assert_true "[ $? -ne 0 ]" "tarball sha 唔符 → 阻斷"
  printf '{"releaseId":"299c3e9","commit":"%s","tarballSha256":"%s"}\n' "$COMMIT" "$(sha256sum "$TMP/src.tar.gz" | cut -d' ' -f1)" >"$TMP/manifest.json"
  build_staging || { bad "root_build 應成功"; return; }
  printf 'DRIFT\n' >>"$VOL/emsd_空調能源標籤.csv"
  apply_staging
  assert_true "[ $? -ne 0 ]" "資料漂移 → apply 阻斷"
  assert_not_contains "$FAKE_DOCKER_LOG" "stop" "阻斷發生在停容器之前"
}

s4_deploy_success() {
  fresh_deploy || return
  assert_true "[ -f '$STAGE/app/web/index.html' ]" "staging 產出 web/index.html"
  assert_true "[ -f '$STAGE/release-info.json' ]" "staging 產出 release-info.json"
  assert_true "[ -f '$STAGE/release-image.id' ]" "staging 記錄 release image id"
  assert_true "[ -f '$VOL/web/index.html' ]" "部署後 volume web 有 index.html"
  assert_true "[ -f '$VOL/web/空調對比報告.pdf' ] && [ -f '$VOL/web/emsd_空調能源標籤.csv' ]" "部署後 web 新增 PDF/CSV"
  assert_eq "$("$PYTHON" -c "import json;print(json.load(open('$VOL/metadata.json'))['build'])")" "B-apply" "live metadata build = B-apply"
  local ts bk
  ts="$(ls -1 "$BACKUP" | tail -1)"
  bk="$BACKUP/$ts"
  assert_true "[ -f '$bk/volume.tar.gz' ] && [ -f '$bk/volume.tar.gz.sha256' ] && [ -f '$bk/volume-file-list.txt' ]" "備份有 archive + checksum + 檔案清單"
  assert_true "[ -f '$bk/image.pre-id.txt' ] && [ -f '$bk/image.pre-tag.txt' ]" "備份有完整 image pre 記錄（ID + tag 名）"
  assert_true "[ -f '$bk/RELEASE-RECORD.json' ]" "成功部署寫出 RELEASE-RECORD.json"
  assert_contains "$FAKE_DOCKER_LOG" "tag aircon-compare:latest aircon-compare:pre-" "舊 image 已 tag 為 pre-*"
  assert_contains "$FAKE_DOCKER_LOG" "compose -f $AIRCON_COMPOSE_FILE up -d --force-recreate" "只 recreate aircon 服務"
  assert_not_contains "$FAKE_DOCKER_LOG" "proxy" "冇碰 proxy"
  echo "$bk" >"$TMP/last_bk.txt"
}

s5_rollback_absent_files() {
  s4_deploy_success
  [ -f "$TMP/last_bk.txt" ] || { bad "s4 前置失敗"; return; }
  local bk ts
  bk="$(cat "$TMP/last_bk.txt")"; ts="$(basename "$bk")"
  ( source "$SCRIPT"; root_rollback "$ts" ) >"$TMP/rollback.log" 2>&1 || { bad "rollback 應成功：$(tail -3 "$TMP/rollback.log")"; return; }
  assert_true "[ ! -e '$VOL/web/空調對比報告.pdf' ]" "回滾精確刪除 web/PDF"
  assert_true "[ ! -e '$VOL/web/emsd_空調能源標籤.csv' ]" "回滾精確刪除 web/CSV"
  assert_true "[ ! -e '$VOL/空調對比報告.pdf' ]" "回滾刪除根目錄 PDF"
  assert_eq "$(cat "$VOL/web/index.html")" "<html>OLD-WEB-INDEX</html>" "web/index.html 還原"
  assert_eq "$("$PYTHON" -c "import json;print(json.load(open('$VOL/metadata.json'))['build'])")" "B-old" "metadata 還原 B-old"
  assert_true "[ -f '$VOL/model_blacklist.json' ]" "原有資料檔保留"
  assert_contains "$FAKE_DOCKER_LOG" "tag aircon-compare:pre-$ts aircon-compare:latest" "回滾還原舊 image tag"
}

s6_auto_rollback() {
  setup; env_for
  build_staging || { bad "root_build 應成功"; return; }
  export FAKE_APP_FIXTURE="$TMP/fixture-apply"
  export FAKE_FAIL_CURL=1
  apply_staging
  local rc=$?
  unset FAKE_FAIL_CURL
  assert_true "[ $rc -ne 0 ]" "健康檢查失敗 → apply 非零退出"
  assert_eq "$(cat "$VOL/web/index.html")" "<html>OLD-WEB-INDEX</html>" "自動回滾還原 web/index.html"
  assert_true "[ ! -e '$VOL/web/空調對比報告.pdf' ]" "自動回滾刪除新增 web/PDF"
  export FAKE_FAIL_IMAGE=1
  apply_staging
  assert_true "[ $? -ne 0 ]" "release image 唔存在 → 阻斷"
  unset FAKE_FAIL_IMAGE
}

s7_rerun_safety() {
  setup; env_for
  build_staging || { bad "root_build 應成功"; return; }
  apply_staging || { bad "第一次 apply 應成功"; return; }
  apply_staging || { bad "第二次 apply（重跑）應成功：$(tail -3 "$TMP/apply.log")"; return; }
  assert_eq "$("$PYTHON" -c "import json;print(json.load(open('$VOL/metadata.json'))['build'])")" "B-apply" "重跑後狀態一致"
  assert_eq "$(ls -1 "$BACKUP" | wc -l)" "2" "兩次部署各自備份"
  local ts1; ts1="$(ls -1 "$BACKUP" | head -1)"
  ( source "$SCRIPT"; root_rollback "$ts1" ) >"$TMP/rb1.log" 2>&1 || bad "第一次 rollback 應成功"
  ( source "$SCRIPT"; root_rollback "$ts1" ) >"$TMP/rb2.log" 2>&1 || bad "重跑 rollback 應成功"
  assert_eq "$(cat "$VOL/web/index.html")" "<html>OLD-WEB-INDEX</html>" "rollback 重跑後仍然乾淨"
}

s8_rollback_wrapper_permission() {
  setup; env_for
  mkdir -p "$BACKUP/20260913T000000Z"
  chmod 700 "$BACKUP" "$BACKUP/20260913T000000Z"
  # 模擬 root-only：普通使用者完全讀唔到備份目錄
  chmod 000 "$BACKUP"
  ( source "$SCRIPT"; cmd_rollback latest ) >"$TMP/out.log" 2>&1
  local rc=$?
  chmod 700 "$BACKUP"
  assert_eq "$rc" 99 "普通使用者 rollback latest 直達 sudo 邊界（fake sudo exit 99）"
  assert_contains "$FAKE_SUDO_LOG" "bash $SCRIPT --root-rollback latest" "sudo 呼叫精確（--root-rollback latest）"
  assert_not_contains "$TMP/out.log" "Permission denied" "冇依賴 root-only 目錄讀取（無權限錯誤）"
  # 明確 TS 也要傳俾 root；格式唔正確要在 sudo 前阻斷
  : >"$FAKE_SUDO_LOG"
  ( source "$SCRIPT"; cmd_rollback '../evil' ) >"$TMP/out2.log" 2>&1
  assert_true "[ $? -ne 0 ]" "非法 TS 格式被拒"
  assert_eq "$(wc -l <"$FAKE_SUDO_LOG")" "0" "非法 TS 唔會呼叫 sudo"
}

s9_root_latest_and_stopped() {
  setup; env_for
  make_backup() {
    local d="$1"
    mkdir -p "$d"
    tar -C "$VOL" -czf "$d/volume.tar.gz" . 2>/dev/null
    ( cd "$d" && sha256sum volume.tar.gz >volume.tar.gz.sha256 ) 2>/dev/null || sha256sum "$d/volume.tar.gz" >"$d/volume.tar.gz.sha256"
    ( cd "$VOL" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) >"$d/volume-file-list.txt"
    cp -a "$VOL/metadata.json" "$d/metadata.pre.json"
    printf '%s\n' "$FAKE_IMAGE_ID_OLD" >"$d/image.pre-id.txt"
    printf '%s\n' "aircon-compare:pre-$(basename "$d")" >"$d/image.pre-tag.txt"
    printf '{"releaseId":"299c3e9"}\n' >"$d/release-info.json"
  }
  VOL="$VOL"
  make_backup "$BACKUP/20260101T000000Z"
  printf '<html>STATE-A</html>\n' >"$VOL/index.html"
  make_backup "$BACKUP/20260202T000000Z"
  printf '<html>STATE-B</html>\n' >"$VOL/index.html"
  ( source "$SCRIPT"; root_rollback latest ) >"$TMP/rb.log" 2>&1
  assert_eq "$?" 0 "root_rollback latest 成功"
  assert_eq "$(cat "$VOL/index.html")" "<html>STATE-A</html>" "root 正確解析並還原最新備份（B 對應 STATE-A）"
  # stopped container 解析：唔用 VOL override，用 fake docker ps -aq
  export FAKE_INSPECT_SRC="$VOL"
  ( unset AIRCON_VOL_DIR; source "$SCRIPT"; resolve_targets 0 ) >"$TMP/res.log" 2>&1
  assert_eq "$?" 0 "stopped container 仍可解析 volume"
  assert_contains "$TMP/res.log" "web=$VOL/web" "解析到正確 web root"
  export FAKE_PS_AMBIGUOUS=1
  ( unset AIRCON_VOL_DIR; source "$SCRIPT"; resolve_targets 0 ) >"$TMP/res2.log" 2>&1
  assert_true "[ $? -ne 0 ]" "多個目標 container → 拒絕"
  unset FAKE_PS_AMBIGUOUS
  export FAKE_PS_NONE=1
  ( unset AIRCON_VOL_DIR; source "$SCRIPT"; resolve_targets 0 ) >"$TMP/res3.log" 2>&1
  assert_true "[ $? -ne 0 ]" "冇目標 container → 拒絕"
  unset FAKE_PS_NONE
}

s10_toctou_guards() {
  local case_name
  for case_name in tamper_file delete_file tamper_metadata swap_image; do
    setup; env_for
    if ! build_staging; then bad "root_build 應成功（$case_name）"; continue; fi
    local before; before="$(vol_state)"
    case "$case_name" in
      tamper_file)   printf '\nTAMPER\n' >>"$STAGE/staged-data/model_blacklist.json" ;;
      delete_file)   rm -f "$STAGE/staged-data/model_blacklist.json" ;;
      tamper_metadata)
        "$PYTHON" - "$STAGE/app/metadata.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding='utf-8'))
d['releasePayloadHash'] = 'sha256:' + 'f' * 64
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
PY
        ;;
      swap_image)    FAKE_IMAGE_ID_RELEASE="sha256:$(printf 'd%.0s' {1..64})"; export FAKE_IMAGE_ID_RELEASE ;;
    esac
    apply_staging
    local rc=$?
    assert_true "[ $rc -ne 0 ]" "TOCTOU（$case_name）→ apply 阻斷"
    assert_not_contains "$FAKE_DOCKER_LOG" "docker stop" "TOCTOU（$case_name）阻斷在停服務之前"
    assert_eq "$(vol_state)" "$before" "TOCTOU（$case_name）volume 未改"
  done
}

s11_backup_failure_safety() {
  local case_name
  for case_name in mkdir_fail hook_after_stop truncate_archive missing_manifest tar_unreadable; do
    setup; env_for
    if ! build_staging; then bad "root_build 應成功（$case_name）"; continue; fi
    local before; before="$(vol_state)"
    case "$case_name" in
      mkdir_fail)
        : >"$TMP/blocker"
        export AIRCON_BACKUP_ROOT="$TMP/blocker/backups"
        ;;
      hook_after_stop)
        export AIRCON_SANDBOX_HOOK_AFTER_STOP='exit 7'
        ;;
      truncate_archive)
        export AIRCON_SANDBOX_HOOK_VERIFY_BACKUP='printf x > "$BK_DIR/volume.tar.gz"'
        ;;
      missing_manifest)
        export AIRCON_SANDBOX_HOOK_VERIFY_BACKUP='rm -f "$BK_DIR/volume-file-list.txt"'
        ;;
      tar_unreadable)
        chmod 000 "$VOL/index.html"
        ;;
    esac
    apply_staging
    local rc=$?
    [ "$case_name" = "tar_unreadable" ] && chmod 644 "$VOL/index.html"
    assert_true "[ $rc -ne 0 ]" "備份失敗（$case_name）→ apply 非零退出"
    if [ "$case_name" = "mkdir_fail" ]; then
      assert_not_contains "$FAKE_DOCKER_LOG" "docker stop" "mkdir 失敗發生在停服務之前"
    else
      assert_contains "$FAKE_DOCKER_LOG" "compose -f $AIRCON_COMPOSE_FILE up -d --force-recreate" "停止後失敗 → 已安全重啟原服務"
      assert_not_contains "$FAKE_DOCKER_LOG" "tag aircon-compare:release-$RELEASE_ID aircon-compare:latest" "停止後失敗 → 冇切換 release image"
    fi
    assert_not_contains "$FAKE_DOCKER_LOG" "proxy" "備份失敗（$case_name）冇碰 proxy"
    assert_true "[ ! -e '$VOL/web/空調對比報告.pdf' ]" "備份失敗（$case_name）冇安裝 staging 資料"
  done
}

s12_sync_delete() {
  if ! command -v rsync >/dev/null 2>&1; then
    echo "  ⏭  skip s12（本機冇 rsync；會在伺服器用真 rsync 跑同一測試）"
    return 0
  fi
  bash "$HERE/test_sync_code.sh" && ok "sync_code --delete 測試通過" || bad "sync_code --delete 測試失敗"
}

s13_image_backup_guards() {
  local case_name
  for case_name in inspect_latest_fail tag_fail tag_id_mismatch; do
    setup; env_for
    if ! build_staging; then bad "root_build 應成功（$case_name）"; continue; fi
    local before; before="$(vol_state)"
    case "$case_name" in
      inspect_latest_fail) export FAKE_FAIL_IMAGE_INSPECT_LATEST=1 ;;
      tag_fail)            export FAKE_FAIL_TAG=1 ;;
      tag_id_mismatch)     export FAKE_TAG_ID_MISMATCH=1 ;;
    esac
    apply_staging
    local rc=$?
    assert_true "[ $rc -ne 0 ]" "image 備份失敗（$case_name）→ apply 阻斷"
    assert_not_contains "$FAKE_DOCKER_LOG" "docker stop" "image 備份失敗（$case_name）阻斷在停服務之前"
    assert_eq "$(vol_state)" "$before" "image 備份失敗（$case_name）volume 未改"
    assert_contains "$TMP/apply.log" "拒絕部署" "image 備份失敗（$case_name）錯誤訊息明確"
    assert_not_contains "$FAKE_DOCKER_LOG" "proxy" "image 備份失敗（$case_name）冇碰 proxy"
  done
}

s14_latest_complete_selection() {
  setup; env_for
  make_complete_backup() {
    local d="$1"
    mkdir -p "$d"
    tar -C "$VOL" -czf "$d/volume.tar.gz" .
    ( cd "$d" && sha256sum volume.tar.gz >volume.tar.gz.sha256 )
    ( cd "$VOL" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) >"$d/volume-file-list.txt"
    cp -a "$VOL/metadata.json" "$d/metadata.pre.json"
    printf '%s\n' "$FAKE_IMAGE_ID_OLD" >"$d/image.pre-id.txt"
    printf '%s\n' "aircon-compare:pre-$(basename "$d")" >"$d/image.pre-tag.txt"
    printf '{"releaseId":"299c3e9"}\n' >"$d/release-info.json"
  }
  make_complete_backup "$BACKUP/20260101T000000Z"
  printf '<html>STATE-A</html>\n' >"$VOL/index.html"
  make_complete_backup "$BACKUP/20260202T000000Z"
  printf '<html>STATE-B</html>\n' >"$VOL/index.html"
  # 較新但唔完整（缺 checksum）
  mkdir -p "$BACKUP/20260303T000000Z"
  tar -C "$VOL" -czf "$BACKUP/20260303T000000Z/volume.tar.gz" .
  ( source "$SCRIPT"; root_rollback latest ) >"$TMP/rb.log" 2>&1
  assert_eq "$?" 0 "較新 incomplete＋較舊 valid → latest 仍成功"
  assert_contains "$TMP/rb.log" "跳過不完整備份" "唔完整目錄有 warning 跳過"
  assert_eq "$(cat "$VOL/index.html")" "<html>STATE-A</html>" "latest 揀到最新完整備份（20260202 → STATE-A）"
  assert_true "[ -d '$BACKUP/20260303T000000Z' ]" "唔完整備份冇被自動刪除"
  # 只有 incomplete → 拒絕且不改 volume／不停服務
  rm -rf "$TMP"
  setup; env_for
  mkdir -p "$BACKUP/20260404T000000Z"
  tar -C "$VOL" -czf "$BACKUP/20260404T000000Z/volume.tar.gz" .
  local before; before="$(vol_state)"
  ( source "$SCRIPT"; root_rollback latest ) >"$TMP/rb2.log" 2>&1
  assert_true "[ $? -ne 0 ]" "只有 incomplete → latest 阻斷"
  assert_contains "$TMP/rb2.log" "冇任何完整備份" "只有 incomplete 時錯誤訊息明確"
  assert_eq "$(vol_state)" "$before" "只有 incomplete 時 volume 未改"
  assert_not_contains "$FAKE_DOCKER_LOG" "docker stop" "只有 incomplete 時冇停服務"
}

main() {
  echo "== sandbox tests: $SCRIPT =="
  for s in s1_dry_run_no_write s2_path_guard s3_guard_refusals s4_deploy_success \
           s5_rollback_absent_files s6_auto_rollback s7_rerun_safety \
           s8_rollback_wrapper_permission s9_root_latest_and_stopped \
           s10_toctou_guards s11_backup_failure_safety s12_sync_delete \
           s13_image_backup_guards s14_latest_complete_selection; do
    echo "-- $s"
    TMP=""
    "$s"
    if [ -n "${KEEP_TMP:-}" ]; then
      [ -n "$TMP" ] && echo "   （tmp 保留：$TMP）"
    else
      [ -n "$TMP" ] && rm -rf "$TMP"
    fi
  done
  echo
  echo "== 結果：PASS=$PASS FAIL=$FAIL =="
  [ "$FAIL" -eq 0 ]
}

TMP=""
main "$@"
