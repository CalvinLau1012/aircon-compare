#!/usr/bin/env bash
# =============================================================================
# aircon-compare 持久正式發佈入口（release 299c3e9）
#
# 使用（唯一需要記住嘅命令）：
#   bash /srv/aircon-compare/aircon-docker/release-299c3e9.sh
#   → 互動選單：Preflight → Build staging → Verify → Deploy → Rollback
#
# 亦支援直接子命令（等價選單選項）：
#   preflight | build | verify | serve | stop-serve | apply | rollback [TS] | status | help
#
# 安全不變式（本輪加固）：
#   S1 普通使用者只做確認；所有需要讀寫 volume / 備份 / docker 嘅嘢一律在 sudo 階段做。
#      `rollback latest` 由 root 階段解析，普通使用者毋須讀取 root-only 備份目錄。
#   S2 停服務前先重驗 staging：staged-data 清單+hash、metadata schema、payload hash、
#      release image ID、volume 資料漂移；任何一項唔符即阻斷，唔會停服務。
#   S3 停服務後分三個階段：stopped（備份未完成）→ backup_ready → applying。
#      stopped 階段失敗只會安全重啟原服務，絕不會用未完成備份覆蓋 volume。
#      備份必須 archive 可讀 + checksum + 檔案清單一致，先會進入 backup_ready。
#   S4 container 解析用 `docker ps -aq`（running 或 stopped），多個目標即拒絕。
#   S5 只寫入白名單；proxy／憑證／其他服務不受影響。
# =============================================================================
set -euo pipefail

RELEASE_ID="299c3e9"
WARNED_SANDBOX=0
PHASE=""
BK_DIR=""

# ---------- 路徑 / 可注入設定（sandbox 測試用 AIRCON_* 覆寫） ----------
BASE_DIR="${AIRCON_BASE_DIR:-/srv/aircon-compare}"
STAGE_ROOT="${AIRCON_STAGE_ROOT:-$BASE_DIR/release-299c3e9}"
BACKUP_ROOT="${AIRCON_BACKUP_ROOT:-$BASE_DIR/release-backups}"
TARBALL="${AIRCON_TARBALL:-$BASE_DIR/release-299c3e9-src.tar.gz}"
MANIFEST="${AIRCON_MANIFEST:-$BASE_DIR/release-299c3e9-manifest.json}"
COMPOSE_FILE="${AIRCON_COMPOSE_FILE:-$BASE_DIR/compose.yaml}"
DOCKER_BIN="${AIRCON_DOCKER_BIN:-docker}"
CURL_BIN="${AIRCON_CURL_BIN:-curl}"
SUDO_BIN="${AIRCON_SUDO_BIN:-sudo}"
IMAGE_REPO="${AIRCON_IMAGE_REPO:-aircon-compare}"
CONTAINER_NAME="${AIRCON_CONTAINER:-aircon}"
COMPOSE_PROJECT="${AIRCON_COMPOSE_PROJECT:-aircon-docker}"
COMPOSE_SERVICE="${AIRCON_COMPOSE_SERVICE:-aircon}"
HEALTH_URL="${AIRCON_HEALTH_URL:-http://127.0.0.1/index.html}"
STAGING_PORT="${AIRCON_STAGING_PORT:-8788}"
VOL_NAME_OVERRIDE="${AIRCON_VOL_NAME:-}"
VOL_DIR_OVERRIDE="${AIRCON_VOL_DIR:-}"
ALLOW_NONROOT="${AIRCON_ALLOW_NONROOT:-0}"
ALLOW_ANY_VOL="${AIRCON_ALLOW_ANY_VOL:-0}"
AUTO_CONFIRM="${AIRCON_SANDBOX_AUTO_CONFIRM:-0}"
OWNER_USER="${AIRCON_OWNER:-${SUDO_USER:-}}"

WEB_FILES=(index.html "空調對比報告.pdf" "emsd_空調能源標籤.csv" metadata.json)

SELF="$(readlink -f "${BASH_SOURCE[0]}")"

# ---------- 基本輸出 ----------
log()  { printf '%s  %s\n' "$(date '+%F %T %Z')" "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; }
die()  { printf '❌ %s\n' "$*" >&2; exit 1; }

in_sandbox() { [ "$ALLOW_NONROOT" = "1" ] && [ -n "$VOL_DIR_OVERRIDE" ]; }
require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    if [ "$ALLOW_NONROOT" = "1" ]; then
      if [ "$WARNED_SANDBOX" = 0 ]; then
        warn "SANDBOX 模式（非 root；AIRCON_ALLOW_NONROOT=1）— 切勿用於正式站"
        WARNED_SANDBOX=1
      fi
    else
      die "呢個階段需要 root；請經入口腳本執行（會用 sudo）"
    fi
  fi
}

confirm() { # $1=問題 $2=要輸入嘅字
  local ans=""
  if [ "$AUTO_CONFIRM" = "1" ] && in_sandbox; then
    return 0
  fi
  if [ -e /dev/tty ] && [ -t 0 ]; then
    read -r -p "$1（輸入 $2 確認；其他取消）: " ans </dev/tty || ans=""
  elif [ -e /dev/tty ]; then
    read -r -p "$1（輸入 $2 確認；其他取消）: " ans </dev/tty || ans=""
  else
    die "需要互動終端先可以確認"
  fi
  [ "$ans" = "$2" ] || die "已取消"
}

sandbox_hook() { # 測試專用 fault injection；只在 sandbox 模式執行
  local name="$1" var="AIRCON_SANDBOX_HOOK_$1" cmd=""
  cmd="${!var:-}"
  if [ -n "$cmd" ] && in_sandbox; then
    log "SANDBOX hook $name：$cmd"
    export BK_DIR PHASE STAGE_ROOT VOL_DIR
    bash -c "$cmd"
  fi
}

# ---------- release-info 讀取 ----------
ri_get() { # $1=field
  python3 - "$STAGE_ROOT/release-info.json" "$1" <<'PY'
import json, sys
try:
    v = json.load(open(sys.argv[1], encoding='utf-8'))[sys.argv[2]]
except Exception:
    v = ''
print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
PY
}

# ---------- 目標解析（動態 + 路徑守衛；running 或 stopped） ----------
resolve_targets() { # $1=require_web（預設 1）
  require_root
  local require_web="${1:-1}"
  if [ -n "$VOL_DIR_OVERRIDE" ]; then
    VOL_DIR="$VOL_DIR_OVERRIDE"
    VOL_NAME="${VOL_NAME_OVERRIDE:-sandbox-vol}"
  else
    local cids count
    cids="$("$DOCKER_BIN" ps -aq \
      --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
      --filter "label=com.docker.compose.service=$COMPOSE_SERVICE" || true)"
    if [ -z "$cids" ]; then
      cids="$("$DOCKER_BIN" ps -aq --filter "name=^/${CONTAINER_NAME}\$" || true)"
    fi
    count="$(printf '%s\n' "$cids" | grep -c '[0-9a-fA-F]' || true)"
    [ "$count" -ge 1 ] || die "搵唔到 $CONTAINER_NAME container（running 或 stopped）"
    [ "$count" -eq 1 ] || die "目標 container 唔唯一（搵到 $count 個），拒絕操作"
    local cid
    cid="$(printf '%s\n' "$cids" | grep '[0-9a-fA-F]' | head -n1)"
    VOL_NAME="$("$DOCKER_BIN" inspect -f '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Name}}{{end}}{{end}}' "$cid")"
    local src
    src="$("$DOCKER_BIN" inspect -f '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Source}}{{end}}{{end}}' "$cid")"
    [ -n "$src" ] || die "container 冇 /app mount，拒絕操作"
    VOL_DIR="$src"
  fi

  local guard=1
  if [ "$ALLOW_NONROOT" = "1" ] && [ "$ALLOW_ANY_VOL" = "1" ]; then guard=0; fi
  if [ "${AIRCON_ENFORCE_PATH_GUARD:-0}" = "1" ]; then guard=1; fi
  if [ "$guard" = "1" ]; then
    case "$VOL_DIR" in
      /var/lib/docker/volumes/*/_data) ;;
      *) die "volume host 路徑唔在 whitelist（/var/lib/docker/volumes/<name>/_data）：$VOL_DIR" ;;
    esac
  fi
  [ -L "$VOL_DIR" ] && die "volume 路徑係 symlink，拒絕操作：$VOL_DIR"
  [ -d "$VOL_DIR" ] || die "volume 路徑唔存在：$VOL_DIR"
  WEB_DIR="$VOL_DIR/web"
  if [ "$require_web" = "1" ] && [ ! -d "$WEB_DIR" ]; then
    die "搵唔到 web root：$WEB_DIR"
  fi
  log "目標：container=$CONTAINER_NAME volume=${VOL_NAME:-?} web=$WEB_DIR（state=$([ -d "$WEB_DIR" ] && echo ok || echo missing)）"
}

# ---------- 資料檔清單 / hash ----------
DATA_FIND=(find . -maxdepth 1 -type f \( -name '*.json' -o -name '*.csv' -o -name '*-bak*' \) ! -name 'deploy_payload.json' ! -name 'metadata.json')

data_hashes() { # 由 VOL_DIR 產生 "sha256  <relpath>"（排序）
  ( cd "$VOL_DIR" && "${DATA_FIND[@]}" -exec sha256sum {} + | LC_ALL=C sort -k2 )
}

# ---------- tarball / manifest 驗證 ----------
verify_manifest() {
  [ -f "$MANIFEST" ] || die "缺少 release manifest：$MANIFEST"
  [ -f "$TARBALL" ] || die "缺少 release source tarball：$TARBALL"
  read -r M_COMMIT M_SHA < <(python3 - "$MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
print(m['commit'], m['tarballSha256'])
PY
)
  local got
  got="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
  [ "$got" = "$M_SHA" ] || die "tarball sha256 唔符（got $got / want $M_SHA）"
  ok "tarball sha256 已驗證：$got（commit ${M_COMMIT:0:12}）"
}

# ---------- 非 root：preflight / status ----------
cmd_preflight() {
  echo "===== aircon-compare 持久發佈 preflight（release $RELEASE_ID） ====="
  echo "base dir      : $BASE_DIR"
  echo "stage root    : $STAGE_ROOT"
  echo "backup root   : $BACKUP_ROOT"
  echo "manifest      : $MANIFEST"
  echo "tarball       : $TARBALL"
  echo "compose       : $COMPOSE_FILE"
  echo "docker CLI    : $DOCKER_BIN"
  echo
  echo "----- release manifest / tarball -----"
  verify_manifest || die "release 來源驗證失敗"
  local listing
  listing="$(mktemp /tmp/aircon-tarball-list.XXXXXX)"
  if ! tar -tzf "$TARBALL" >"$listing" 2>/dev/null; then
    rm -f "$listing"
    die "tarball 無法讀取"
  fi
  if grep -qx './docker/Dockerfile' "$listing" || grep -qx 'docker/Dockerfile' "$listing"; then
    rm -f "$listing"
    ok "tarball 包含 docker/Dockerfile"
  else
    rm -f "$listing"
    die "tarball 結構唔啱（冇 docker/Dockerfile）"
  fi
  echo
  echo "----- 現時公開站（OBSERVED） -----"
  if "$CURL_BIN" -fsS --max-time 10 http://127.0.0.1/metadata.json 2>/dev/null | python3 -c '
import json, sys
m = json.load(sys.stdin)
print("version=%s build=%s commit=%s datasetDate=%s recordCount=%s"
      % (m["version"], m["build"], m["commit"][:12], m["datasetDate"], m.get("recordCount")))'; then
    :
  else
    warn "讀唔到公開站 metadata（可能未啟動；部署前請留意）"
  fi
  echo
  echo "----- staging 狀態 -----"
  if [ -f "$STAGE_ROOT/release-info.json" ]; then
    python3 - "$STAGE_ROOT/release-info.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding='utf-8'))
print("staging build :", r.get('build'), "· commit", r.get('commit', '')[:12],
      "· datasetDate", r.get('datasetDate'), "· counts", r.get('counts'))
print("built at      :", r.get('builtAt'), "· imageId", str(r.get('releaseImageId'))[:20])
PY
  else
    echo "未有 staging build（請先執行 build）"
  fi
  echo
  echo "----- 磁碟空間 -----"
  df -h "$BASE_DIR" 2>/dev/null | tail -1
  echo
  echo "----- 流程 -----"
  cat <<'EOT'
  1) build   ：sudo 讀取 volume 最新資料 → 隔離 staging 重建 + 全套閘門
  2) verify  ：核對 staging metadata / payload hash /（可選）Playwright
  3) apply   ：停 aircon → 完整備份（驗證後先改 volume）→ 部署 → 重啟 → 驗證
               任何失敗：備份未完成 = 安全重啟原服務；備份完成 = 自動回滾
  4) rollback：由 root 階段解析最新備份並還原 volume + 舊 image tag + 重啟
EOT
}

cmd_status() {
  echo "=== 公開站 metadata ==="
  "$CURL_BIN" -fsS --max-time 10 http://127.0.0.1/metadata.json 2>/dev/null || warn "讀唔到公開站"
  echo
  echo "=== staging ==="
  [ -f "$STAGE_ROOT/release-info.json" ] && cat "$STAGE_ROOT/release-info.json" || echo "（未有）"
  echo
  echo "=== 備份 ==="
  if [ -d "$BACKUP_ROOT" ] && [ -x "$BACKUP_ROOT" ] && [ -r "$BACKUP_ROOT" ]; then
    ls -1 "$BACKUP_ROOT" | tail -5
  else
    echo "（備份目錄需要 root 讀取；rollback latest 會由 root 階段解析最新備份）"
  fi
}

# ---------- 非 root：verify ----------
cmd_verify() {
  [ -f "$STAGE_ROOT/app/metadata.json" ] || die "未見 staging；請先執行 build"
  echo "=== 1. metadata Schema ==="
  ( cd "$STAGE_ROOT/app" && python3 scripts/validate_metadata.py metadata.json )
  echo "=== 2. payload hash（releasePayloadHash 重算） ==="
  python3 - "$STAGE_ROOT/app" <<'PY'
import importlib.util, json, os, sys
base = sys.argv[1]
spec = importlib.util.spec_from_file_location('gm', os.path.join(base, 'scripts', 'gen-metadata.py'))
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)
manifest = json.load(open(os.path.join(base, 'deploy_payload.json'), encoding='utf-8'))
meta = json.load(open(os.path.join(base, 'metadata.json'), encoding='utf-8'))
h = gm.hash_files(manifest['files'], base=base)
if h != meta['releasePayloadHash']:
    print('❌ payload hash 唔一致：', h, '!=', meta['releasePayloadHash']); sys.exit(1)
print('✅ payload hash 一致：', h)
PY
  echo "=== 3. 部署檔齊全 ==="
  for f in "${WEB_FILES[@]}"; do
    [ -s "$STAGE_ROOT/app/web/$f" ] || die "staging web 缺少 $f"
  done
  ok "index.html / PDF / CSV / metadata.json 齊全"
  echo "=== 4. staging 摘要 ==="
  python3 - "$STAGE_ROOT/app/metadata.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
print("version=%s build=%s commit=%s" % (m['version'], m['build'], m['commit']))
print("datasetDate=%s datasetHash=%s" % (m['datasetDate'], m['datasetHash'][:24]))
print("counts: model=%s registration=%s raw=%s" %
      (m.get('modelCount'), m.get('registrationCount'), m.get('rawRecordCount')))
PY
  echo "=== 5. staging HTTP（如已 serve） ==="
  if "$CURL_BIN" -fsS --max-time 5 "http://127.0.0.1:$STAGING_PORT/metadata.json" >/dev/null 2>&1; then
    ok "staging server 已就緒：http://127.0.0.1:$STAGING_PORT/（Playwright 可喺外面跑）"
  else
    echo "（未 serve；可執行：bash $SELF serve）"
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import playwright' 2>/dev/null; then
    echo "=== 6. Playwright smoke（本機有 playwright） ==="
    python3 "$STAGE_ROOT/src/release/runtime_smoke.py" --base "http://127.0.0.1:$STAGING_PORT" || die "Playwright runtime smoke 失敗"
  else
    warn "本機未有 playwright；瀏覽器驗收交由外部（Codex/DeepSeek）用 staging URL 進行"
  fi
  ok "verify 完成"
}

# ---------- 非 root：staging HTTP server ----------
cmd_serve() {
  [ -d "$STAGE_ROOT/app/web" ] || die "未有 staging web"
  if [ -f "$STAGE_ROOT/server.pid" ] && kill -0 "$(cat "$STAGE_ROOT/server.pid")" 2>/dev/null; then
    ok "staging server 已在跑（PID $(cat "$STAGE_ROOT/server.pid")）"
    return 0
  fi
  nohup python3 -m http.server "$STAGING_PORT" --bind 127.0.0.1 \
    --directory "$STAGE_ROOT/app/web" >"$STAGE_ROOT/server.log" 2>&1 &
  echo $! >"$STAGE_ROOT/server.pid"
  sleep 1
  ok "staging server：http://127.0.0.1:$STAGING_PORT/（PID $(cat "$STAGE_ROOT/server.pid")）"
}

cmd_stop_serve() {
  if [ -f "$STAGE_ROOT/server.pid" ]; then
    kill "$(cat "$STAGE_ROOT/server.pid")" 2>/dev/null || true
    rm -f "$STAGE_ROOT/server.pid"
    ok "staging server 已停止"
  else
    echo "（未在跑）"
  fi
}

# ---------- 非 root：build ----------
cmd_build() {
  cmd_preflight || die "preflight 失敗"
  echo
  echo "build 階段會："
  echo "  - 由 image 建立 staging（Docker build，唔碰現有容器）"
  echo "  - 以 read-only 方式讀取 volume 最新資料（唔會改線上）"
  echo "  - 在臨時容器內跑 validate_data + 治理閘門 + pytest（非瀏覽器）"
  echo "  - 產出 staging index/PDF/CSV/two-stage metadata"
  confirm "繼續 build staging？" BUILD
  "$SUDO_BIN" bash "$SELF" --root-build
  cmd_verify
  echo
  ok "staging build 完成。下一步：bash $SELF serve（外部 Playwright 驗收）或 bash $SELF apply"
}

# ---------- root：build staging ----------
root_build() {
  require_root
  resolve_targets
  verify_manifest

  log "準備 staging 目錄：$STAGE_ROOT"
  mkdir -p "$STAGE_ROOT"
  rm -rf "${STAGE_ROOT:?}/src" "${STAGE_ROOT:?}/app"
  mkdir -p "$STAGE_ROOT/src"
  tar -xzf "$TARBALL" -C "$STAGE_ROOT/src"
  [ -f "$STAGE_ROOT/src/docker/Dockerfile" ] || die "tarball 解壓後冇 docker/Dockerfile"
  [ -f "$STAGE_ROOT/src/.release-commit" ] || die "tarball 缺少 .release-commit"
  SRC_COMMIT="$(tr -d '[:space:]' <"$STAGE_ROOT/src/.release-commit")"
  [ "$SRC_COMMIT" = "$M_COMMIT" ] || die "commit 唔一致：tarball=$SRC_COMMIT manifest=$M_COMMIT"

  log "Docker build release image：$IMAGE_REPO:release-$RELEASE_ID"
  "$DOCKER_BIN" build -f "$STAGE_ROOT/src/docker/Dockerfile" \
    --build-arg "AIRCON_COMMIT=$SRC_COMMIT" \
    -t "$IMAGE_REPO:release-$RELEASE_ID" "$STAGE_ROOT/src"

  log "記錄 release image ID（apply 前會再核對，防止 TOCTOU）"
  RELEASE_IMAGE_ID="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:release-$RELEASE_ID")"
  [ -n "$RELEASE_IMAGE_ID" ] || die "讀唔到 release image ID"
  echo "$RELEASE_IMAGE_ID" >"$STAGE_ROOT/release-image.id"
  ok "release image ID：$RELEASE_IMAGE_ID"

  log "複製 source 到 staging app（資料稍後由 volume 帶入）"
  mkdir -p "$STAGE_ROOT/app"
  cp -a "$STAGE_ROOT/src/." "$STAGE_ROOT/app/"

  log "記錄 volume 部署前資料 hash（部署時會再核對，確保冇資料漂移）"
  data_hashes >"$STAGE_ROOT/data-hashes.pre.txt"
  wc -l <"$STAGE_ROOT/data-hashes.pre.txt" | { read -r n; log "資料檔數量：$n"; }

  log "臨時容器內執行 staging build（volume read-only；--release-build 會用新 PR-3 ingestion 重抓 EMSD，修復舊 CSV 重複表頭）"
  # shellcheck disable=SC2024
  "$DOCKER_BIN" run --rm \
    -v "$VOL_NAME:/data:ro" \
    -v "$STAGE_ROOT/app:/app" \
    --entrypoint /usr/local/bin/run-update.sh \
    "$IMAGE_REPO:release-$RELEASE_ID" --release-build | tee "$STAGE_ROOT/build.log"

  log "收錄 staging 最新資料（apply 時會安裝到 volume；刷新後嘅 EMSD CSV/receipt 亦包括在內）"
  rm -rf "${STAGE_ROOT:?}/staged-data"
  mkdir -p "$STAGE_ROOT/staged-data"
  ( cd "$STAGE_ROOT/app" && "${DATA_FIND[@]}" -exec cp -a {} "$STAGE_ROOT/staged-data/" \; )
  ( cd "$STAGE_ROOT/staged-data" && find . -maxdepth 1 -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) \
    >"$STAGE_ROOT/data-hashes.staged.txt"
  local staged_manifest_hash staged_count
  staged_manifest_hash="$(sha256sum "$STAGE_ROOT/data-hashes.staged.txt" | cut -d' ' -f1)"
  staged_count="$(wc -l <"$STAGE_ROOT/data-hashes.staged.txt")"
  ok "staged-data manifest：${staged_manifest_hash:0:16}…（$staged_count 檔）"

  if [ "$(id -u)" -eq 0 ] && [ -n "$OWNER_USER" ] && id "$OWNER_USER" >/dev/null 2>&1; then
    chown -R "$OWNER_USER:$OWNER_USER" "$STAGE_ROOT"
  fi

  # staging metadata → release-info.json
  python3 - "$STAGE_ROOT" "$SRC_COMMIT" "$TARBALL" "$RELEASE_IMAGE_ID" \
          "$staged_manifest_hash" "$staged_count" <<'PY'
import hashlib, json, os, sys, time
stage, commit, tarball, image_id, staged_hash, staged_count = sys.argv[1:7]
base = os.path.join(stage, 'app')
m = json.load(open(os.path.join(base, 'metadata.json'), encoding='utf-8'))
h = hashlib.sha256(open(tarball, 'rb').read()).hexdigest()

def file_hash(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest() if os.path.exists(path) else None

info = {
    'releaseId': '299c3e9',
    'commit': commit,
    'tarballSha256': h,
    'builtAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'build': m['build'],
    'workflowRunId': m['workflowRunId'],
    'version': m['version'],
    'datasetDate': m['datasetDate'],
    'datasetHash': m['datasetHash'],
    'deployTime': m['deployTime'],
    'releasePayloadHash': m['releasePayloadHash'],
    'counts': {k: m.get(k) for k in ('recordCount', 'modelCount', 'registrationCount', 'rawRecordCount')},
    'dataHashesPre': file_hash(os.path.join(stage, 'data-hashes.pre.txt')),
    'dataHashesStaged': file_hash(os.path.join(stage, 'data-hashes.staged.txt')),
    'stagedDataManifestHash': staged_hash,
    'stagedDataCount': int(staged_count),
    'releaseImageId': image_id,
    'emsdReceipt': json.load(open(os.path.join(base, 'emsd_receipt.json'), encoding='utf-8'))
    if os.path.exists(os.path.join(base, 'emsd_receipt.json')) else None,
}
with open(os.path.join(stage, 'release-info.json'), 'w', encoding='utf-8') as f:
    json.dump(info, f, ensure_ascii=False, indent=2)
print(json.dumps(info, ensure_ascii=False))
PY
  ok "staging build 完成：build=$(ri_get build)"
}

# ---------- root：apply 前置完整性（停服務前） ----------
verify_staging_integrity() {
  [ -f "$STAGE_ROOT/release-info.json" ] || die "未有 staging build（release-info.json）"
  [ -f "$STAGE_ROOT/app/metadata.json" ] || die "staging 缺少 metadata.json"
  [ -f "$STAGE_ROOT/data-hashes.pre.txt" ] || die "staging 缺少 data-hashes.pre.txt"
  [ -f "$STAGE_ROOT/data-hashes.staged.txt" ] || die "staging 缺少 data-hashes.staged.txt"
  [ -d "$STAGE_ROOT/staged-data" ] || die "staging 缺少 staged-data"

  [ "$(ri_get commit)" = "$M_COMMIT" ] || die "staging commit 同 release manifest 唔一致"
  [ "$(ri_get tarballSha256)" = "$M_SHA" ] || die "staging 對應嘅 tarball 唔係目前 release package（請重新 build）"

  log "S2a：重驗 staged-data 清單 + hash"
  local recomputed
  recomputed="$( cd "$STAGE_ROOT/staged-data" && find . -maxdepth 1 -type f -exec sha256sum {} + | LC_ALL=C sort -k2 )"
  if ! diff -q <(printf '%s\n' "$recomputed") "$STAGE_ROOT/data-hashes.staged.txt" >/dev/null; then
    die "staged-data 檔案清單／hash 同 build 時記錄唔一致（被改過？）"
  fi
  local manifest_now count_now
  manifest_now="$(sha256sum "$STAGE_ROOT/data-hashes.staged.txt" | cut -d' ' -f1)"
  [ "$manifest_now" = "$(ri_get stagedDataManifestHash)" ] || die "staged-data manifest hash 唔一致"
  count_now="$(find "$STAGE_ROOT/staged-data" -maxdepth 1 -type f | wc -l)"
  [ "$count_now" = "$(ri_get stagedDataCount)" ] || die "staged-data 檔案數唔一致（$count_now）"
  ok "staged-data 完整（$count_now 檔；manifest ${manifest_now:0:16}…）"

  log "S2b：重驗 staging metadata schema + payload hash"
  ( cd "$STAGE_ROOT/app" && python3 scripts/validate_metadata.py metadata.json >/dev/null ) \
    || die "staging metadata 唔過 Schema"
  local payload_now meta_payload
  payload_now="$(python3 - "$STAGE_ROOT/app" <<'PY'
import importlib.util, json, os, sys
base = sys.argv[1]
spec = importlib.util.spec_from_file_location('gm', os.path.join(base, 'scripts', 'gen-metadata.py'))
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)
manifest = json.load(open(os.path.join(base, 'deploy_payload.json'), encoding='utf-8'))
print(gm.hash_files(manifest['files'], base=base))
PY
)"
  meta_payload="$(python3 -c "import json;print(json.load(open('$STAGE_ROOT/app/metadata.json'))['releasePayloadHash'])")"
  [ "$payload_now" = "$meta_payload" ] || die "staging payload hash 唔自洽（$payload_now vs $meta_payload）"
  [ "$payload_now" = "$(ri_get releasePayloadHash)" ] || die "staging payload hash 同 release-info 唔一致"
  ok "staging metadata + payload hash 一致（${payload_now:0:16}…）"

  log "S2c：重驗 release image ID"
  local image_now
  image_now="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:release-$RELEASE_ID" 2>/dev/null || true)"
  [ -n "$image_now" ] || die "搵唔到 release image，請重新 build"
  [ "$image_now" = "$(ri_get releaseImageId)" ] || die "release image ID 同 build 時唔一致（image 被換過？）"
  ok "release image 一致（$image_now）"

  log "S2d：staging web 部署檔齊全"
  for f in "${WEB_FILES[@]}"; do
    [ -s "$STAGE_ROOT/app/web/$f" ] || die "staging web 缺少 $f"
  done
  ok "web 四檔齊全"
}

# ---------- root：apply ----------
auto_recover_trap() {
  local rc=$?
  trap - EXIT
  case "$PHASE" in
    stopped)
      warn "部署中止（rc=$rc）而備份未完成：安全重啟原服務（唔會改 volume）"
      restart_original || warn "重啟原服務失敗，請人手檢查：docker start $CONTAINER_NAME"
      ;;
    backup_ready|applying)
      warn "部署未完成（rc=$rc），自動回滾..."
      rollback_impl "$BK_DIR" || warn "自動回滾有問題，請人手執行：bash $SELF rollback $(basename "$BK_DIR")"
      ;;
    *)
      : # 未停服務／已完成：唔需要處理
      ;;
  esac
  exit "$rc"
}

restart_original() {
  log "重啟原服務（同一 image tag，未改 volume）"
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d --force-recreate "$COMPOSE_SERVICE" || return 1
  wait_http "$HEALTH_URL" 30 || return 1
  if [ -f "$BK_DIR/metadata.pre.json" ] && [ -f "$VOL_DIR/metadata.json" ]; then
    python3 - "$BK_DIR/metadata.pre.json" "$VOL_DIR/metadata.json" <<'PY'
import json, sys
a = json.load(open(sys.argv[1], encoding='utf-8'))
b = json.load(open(sys.argv[2], encoding='utf-8'))
assert a['build'] == b['build'], (a['build'], b['build'])
print('✅ 已重啟並確認原 build：', b['build'])
PY
  fi
  ok "原服務已安全重啟"
}

verify_backup() {
  local bk="$1"
  [ -s "$bk/volume.tar.gz" ] || die "備份 archive 唔存在／係空"
  [ -s "$bk/volume.tar.gz.sha256" ] || die "備份 checksum 檔唔存在"
  ( cd "$bk" && sha256sum -c volume.tar.gz.sha256 >/dev/null ) || die "備份 archive checksum 唔符"
  local listing
  listing="$(mktemp /tmp/aircon-backup-list.XXXXXX)"
  if ! tar -tzf "$bk/volume.tar.gz" >"$listing" 2>/dev/null; then
    rm -f "$listing"
    die "備份 archive 讀唔到（可能截斷）"
  fi
  if [ -d "$VOL_DIR/web" ] && ! grep -qx './web/index.html' "$listing"; then
    rm -f "$listing"
    die "備份 archive 缺少 web/index.html"
  fi
  rm -f "$listing"
  [ -s "$bk/volume-file-list.txt" ] || die "備份檔案清單唔存在／係空"
  [ -s "$bk/image.pre-id.txt" ] || die "備份缺少舊 image ID 記錄"
  [ -s "$bk/image.pre-tag.txt" ] || die "備份缺少舊 image tag 記錄"
  local list_now
  list_now="$(mktemp /tmp/aircon-backup-files.XXXXXX)"
  ( cd "$VOL_DIR" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) >"$list_now"
  if ! diff -q "$bk/volume-file-list.txt" "$list_now" >/dev/null; then
    rm -f "$list_now"
    die "備份檔案清單同 volume 現況唔一致"
  fi
  rm -f "$list_now"
  ok "備份已驗證（archive 可讀 + checksum + 檔案清單 + image 記錄）"
}

verify_backup_archive() { # 回滾前：只驗 archive 本身（唔比對現時 volume）
  local bk="$1"
  [ -s "$bk/volume.tar.gz" ] || die "備份 archive 唔存在／係空"
  [ -s "$bk/volume.tar.gz.sha256" ] || die "備份缺少 checksum 檔"
  ( cd "$bk" && sha256sum -c volume.tar.gz.sha256 >/dev/null ) || die "備份 checksum 唔符，拒絕回滾"
  tar -tzf "$bk/volume.tar.gz" >/dev/null 2>&1 || die "備份 archive 讀唔到，拒絕回滾"
  [ -s "$bk/volume-file-list.txt" ] || die "備份缺少檔案清單，拒絕回滾"
  [ -s "$bk/image.pre-id.txt" ] || die "備份缺少舊 image ID 記錄，拒絕回滾"
  [ -s "$bk/image.pre-tag.txt" ] || die "備份缺少舊 image tag 記錄，拒絕回滾"
}

root_apply() {
  require_root
  resolve_targets
  verify_manifest
  verify_staging_integrity

  log "S2e：核對 volume 資料同 build 時一致"
  local cur_hash staged_hash
  cur_hash="$(data_hashes | sha256sum | cut -d' ' -f1)"
  staged_hash="$(sha256sum "$STAGE_ROOT/data-hashes.pre.txt" | cut -d' ' -f1)"
  if ! diff -q <(data_hashes) "$STAGE_ROOT/data-hashes.pre.txt" >/dev/null; then
    die "volume 資料自 build 後已改變（可能 cron 跑過）；請重新 build staging"
  fi
  ok "資料快照一致（sha256 ${cur_hash:0:16}；staged ${staged_hash:0:16}）"

  # 備份目錄／舊 image 資訊：停服務前準備好（失敗即中止，服務不受影響）
  local ts bk bts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  bk="$BACKUP_ROOT/$ts"
  local n=1
  while [ -e "$bk" ]; do
    bk="$BACKUP_ROOT/${ts}_$n"
    n=$((n + 1))
  done
  BK_DIR="$bk"
  bts="$(basename "$bk")"
  mkdir -p "$bk" || die "建立備份目錄失敗：$bk"
  chmod 700 "$BACKUP_ROOT" "$bk"
  cp -a "$STAGE_ROOT/release-info.json" "$bk/release-info.json"
  [ -f "$VOL_DIR/metadata.json" ] && cp -a "$VOL_DIR/metadata.json" "$bk/metadata.pre.json" || true

  # S3a：停服務前必須成功取得舊 latest image ID
  log "S3a：讀取舊 latest image ID（讀唔到即阻斷，唔會停服務）"
  OLD_IMAGE_ID="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:latest" 2>/dev/null || true)"
  case "$OLD_IMAGE_ID" in
    ""|unknown) die "讀唔到舊 latest image ID（$IMAGE_REPO:latest）；拒絕部署" ;;
  esac
  printf '%s' "$OLD_IMAGE_ID" | grep -Eq '^(sha256:)?[0-9a-f]{12,64}$' \
    || die "舊 latest image ID 格式異常（$OLD_IMAGE_ID）；拒絕部署"
  printf '%s\n' "$OLD_IMAGE_ID" >"$bk/image.pre-id.txt"
  printf '%s\n' "$IMAGE_REPO:pre-$bts" >"$bk/image.pre-tag.txt"

  # S3b：必須成功建立 pre tag，並驗證 tag ID 等於舊 latest ID
  log "S3b：建立舊 image 備份 tag 並驗證（失敗即阻斷）"
  "$DOCKER_BIN" tag "$IMAGE_REPO:latest" "$IMAGE_REPO:pre-$bts" \
    || die "建立舊 image tag 失敗（$IMAGE_REPO:pre-$bts）；拒絕部署"
  local pre_tag_id
  pre_tag_id="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:pre-$bts" 2>/dev/null || true)"
  [ -n "$pre_tag_id" ] || die "讀唔到備份 tag ID（$IMAGE_REPO:pre-$bts）；拒絕部署"
  [ "$pre_tag_id" = "$OLD_IMAGE_ID" ] \
    || die "備份 tag ID 唔等於舊 latest ID（tag=$pre_tag_id latest=$OLD_IMAGE_ID）；拒絕部署"
  ok "舊 image 已備份：$OLD_IMAGE_ID（tag $IMAGE_REPO:pre-$bts）"
  ok "備份目錄已建立：$bk"

  log "停止 aircon 容器（cron 同時停止；proxy / 其他服務不受影響）"
  PHASE="stopped"
  trap auto_recover_trap EXIT
  "$DOCKER_BIN" stop "$CONTAINER_NAME" >/dev/null
  sandbox_hook AFTER_STOP

  log "建立完整備份：$bk"
  tar -C "$VOL_DIR" -czf "$bk/volume.tar.gz.tmp" . || die "tar 備份失敗"
  mv -f "$bk/volume.tar.gz.tmp" "$bk/volume.tar.gz"
  sha256sum "$bk/volume.tar.gz" >"$bk/volume.tar.gz.sha256"
  ( cd "$VOL_DIR" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) >"$bk/volume-file-list.txt"
  cp -a "$WEB_DIR" "$bk/web"
  sandbox_hook VERIFY_BACKUP

  log "驗證備份（未完成前唔會改 volume）"
  verify_backup "$bk"
  PHASE="backup_ready"
  ok "備份完成並驗證（$(du -h "$bk/volume.tar.gz" | cut -f1)）"

  log "安裝 staging 已驗證資料（包括新 PR-3 ingestion 修復嘅 EMSD CSV + receipt）"
  cp -a "$STAGE_ROOT/staged-data/." "$VOL_DIR/"
  if ! diff -q <(data_hashes) "$STAGE_ROOT/data-hashes.staged.txt" >/dev/null; then
    die "安裝後 volume 資料同 staging manifest 唔一致"
  fi
  ok "資料已安裝並核對（$(find "$STAGE_ROOT/staged-data" -maxdepth 1 -type f | wc -l) 檔）"

  PHASE="applying"
  log "以新程式碼在 volume 內執行 release build（in-place，--build-only：唔再抓網，用已驗證資料）"
  "$DOCKER_BIN" run --rm \
    -v "$VOL_NAME:/app" \
    --entrypoint /usr/local/bin/run-update.sh \
    "$IMAGE_REPO:release-$RELEASE_ID" --build-only | tee "$bk/apply-build.log"

  log "切換 image tag 並重啟 aircon 服務"
  "$DOCKER_BIN" tag "$IMAGE_REPO:release-$RELEASE_ID" "$IMAGE_REPO:latest"
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d --force-recreate "$COMPOSE_SERVICE"

  log "部署後驗證"
  wait_http "$HEALTH_URL" 30 || die "HTTP 健康檢查失敗：$HEALTH_URL"
  post_apply_verify "$bk"

  PHASE="done"
  trap - EXIT

  python3 - "$bk" "$STAGE_ROOT/app/metadata.json" "$VOL_DIR/metadata.json" <<'PY'
import json, os, sys, time
bk, staged_path, live_path = sys.argv[1], sys.argv[2], sys.argv[3]
staged = json.load(open(staged_path, encoding='utf-8'))
live = json.load(open(live_path, encoding='utf-8'))
record = {
    'releaseId': '299c3e9',
    'commit': live['commit'],
    'deployedBuild': live['build'],
    'stagingBuild': staged['build'],
    'deployTime': live['deployTime'],
    'datasetDate': live['datasetDate'],
    'datasetHash': live['datasetHash'],
    'releasePayloadHash': live['releasePayloadHash'],
    'counts': {k: live.get(k) for k in ('recordCount', 'modelCount', 'registrationCount', 'rawRecordCount')},
    'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
json.dump(record, open(os.path.join(bk, 'RELEASE-RECORD.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
print(json.dumps(record, ensure_ascii=False, indent=2))
PY
  ok "正式部署完成：build=$(python3 -c "import json;print(json.load(open('$VOL_DIR/metadata.json'))['build'])")"
  echo "備份 / 回滾：bash $SELF rollback $(basename "$bk")"
  echo "公開驗證：bash $SELF status"
}

wait_http() {
  local url="$1" tries="${2:-30}" i
  for ((i = 1; i <= tries; i++)); do
    if "$CURL_BIN" -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

post_apply_verify() {
  local bk="$1"
  log "核對 web 檔案 hash（index / CSV 必須同 staging 一致）"
  for f in index.html "emsd_空調能源標籤.csv"; do
    local a b
    a="$(sha256sum "$WEB_DIR/$f" | cut -d' ' -f1)"
    b="$(sha256sum "$STAGE_ROOT/app/web/$f" | cut -d' ' -f1)"
    [ "$a" = "$b" ] || { echo "staged=$b live=$a" >>"$bk/verify-failure.txt"; die "$f hash 唔一致"; }
    ok "$f hash 一致（${a:0:16}）"
  done
  for f in "空調對比報告.pdf" metadata.json; do
    [ -s "$WEB_DIR/$f" ] || die "web 缺少 $f"
  done
  python3 - "$STAGE_ROOT/app/metadata.json" "$VOL_DIR/metadata.json" <<'PY'
import json, sys
staged = json.load(open(sys.argv[1], encoding='utf-8'))
live = json.load(open(sys.argv[2], encoding='utf-8'))
# 部署時會用新 deployTime 重新封裝，所以 build/workflowRunId/deployTime/releasePayloadHash
# 可以唔同；其餘語義欄位（版本／commit／資料快照／計數）必須完全一致。
for k in ('version', 'commit', 'datasetDate', 'datasetHash', 'datasetDateBasis',
          'recordCount', 'modelCount', 'registrationCount', 'rawRecordCount'):
    if staged.get(k) != live.get(k):
        print(f'❌ metadata {k} 唔一致：staged={staged.get(k)} live={live.get(k)}')
        sys.exit(1)
print('✅ metadata 語義欄位一致（build/deployTime 於部署時刻重新封裝）')
PY
  log "核對 live payload hash 自洽（live metadata.releasePayloadHash 對 live 檔案）"
  python3 - "$VOL_DIR" <<'PY'
import importlib.util, json, os, sys
base = sys.argv[1]
spec = importlib.util.spec_from_file_location('gm', os.path.join(base, 'scripts', 'gen-metadata.py'))
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)
manifest = json.load(open(os.path.join(base, 'deploy_payload.json'), encoding='utf-8'))
meta = json.load(open(os.path.join(base, 'metadata.json'), encoding='utf-8'))
h = gm.hash_files(manifest['files'], base=base)
if h != meta['releasePayloadHash']:
    print('❌ live payload hash 唔自洽：', h, '!=', meta['releasePayloadHash'])
    sys.exit(1)
print('✅ live payload hash 自洽：', h)
PY
  for p in "/index.html" "/metadata.json" "/%E7%A9%BA%E8%AA%BF%E5%B0%8D%E6%AF%94%E5%A0%B1%E5%91%8A.pdf" "/emsd_%E7%A9%BA%E8%AA%BF%E8%83%BD%E6%BA%90%E6%A8%99%E7%B1%A4.csv"; do
    "$CURL_BIN" -fsS --max-time 10 -o /dev/null "http://127.0.0.1$p" || die "HTTP $p 失敗"
    ok "HTTP 200 $p"
  done
  ok "部署後驗證通過"
}

# ---------- rollback ----------
latest_backup_ts() { # root-only：由新到舊挑第一個「完整候選」；不完整目錄跳過並 warning
  [ -d "$BACKUP_ROOT" ] || die "備份目錄唔存在：$BACKUP_ROOT"
  local d ts
  while IFS= read -r ts; do
    [ -n "$ts" ] || continue
    d="$BACKUP_ROOT/$ts"
    if backup_is_complete "$d"; then
      printf '%s\n' "$ts"
      return 0
    fi
    warn "跳過不完整備份：$ts（唔會自動刪除，留作診斷）"
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | LC_ALL=C sort -r)
  die "冇任何完整備份（至少需要 volume.tar.gz + checksum + 檔案清單 + image pre 記錄 + release-info）"
}

backup_is_complete() { # $1=backup dir：唔做貴重驗證（checksum/tar 由 rollback_impl 正式做）
  local d="$1"
  [ -s "$d/volume.tar.gz" ] || return 1
  [ -s "$d/volume.tar.gz.sha256" ] || return 1
  [ -s "$d/volume-file-list.txt" ] || return 1
  [ -s "$d/image.pre-id.txt" ] || return 1
  [ -s "$d/image.pre-tag.txt" ] || return 1
  [ -s "$d/release-info.json" ] || return 1
  return 0
}

valid_backup_ts() {
  case "$1" in
    (*[!0-9A-Za-z_-]*|'') return 1 ;;
    *) return 0 ;;
  esac
}

rollback_impl() { # $1=backup dir（root-only）
  local bk="$1"
  [ -d "$bk" ] || die "備份目錄唔存在：$bk"
  local ts
  ts="$(basename "$bk")"

  # 1. archive 完整驗證（唔碰 volume／服務）
  verify_backup_archive "$bk"

  # 2. image 還原能力驗證（唔碰 volume／服務）
  local expected_id want_tag tag_id latest_id
  expected_id="$(tr -d '[:space:]' <"$bk/image.pre-id.txt")"
  want_tag="$(tr -d '[:space:]' <"$bk/image.pre-tag.txt")"
  [ "$want_tag" = "$IMAGE_REPO:pre-$ts" ] || die "image pre tag 記錄同備份名唔一致（$want_tag）"
  tag_id="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$want_tag" 2>/dev/null || true)"
  latest_id="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:latest" 2>/dev/null || true)"
  if [ "$tag_id" = "$expected_id" ]; then
    log "舊 image tag 可用：$want_tag（$tag_id）"
  elif [ "$latest_id" = "$expected_id" ]; then
    tag_id="$latest_id"
    log "latest 已經係舊 image ID，毋須 retag"
  else
    warn "無法還原舊 image：$want_tag ID=${tag_id:-<missing>}｜latest ID=${latest_id:-<missing>}｜expected=$expected_id"
    warn "volume 未改動；嘗試重啟現有服務到可診斷狀態"
    restart_original || warn "重啟失敗，請人手檢查：docker compose up -d aircon"
    die "回滾中止：舊 image 無法還原（唔會用 volume-only 冒充完整成功）"
  fi

  # 3. 停止服務 → 還原 volume → retag → 重啟
  log "回滾：停止 $CONTAINER_NAME"
  "$DOCKER_BIN" stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  log "還原整個 volume（原有檔案覆蓋，本次新增檔案會被刪除）"
  find "$VOL_DIR" -mindepth 1 -delete
  tar -xzf "$bk/volume.tar.gz" -C "$VOL_DIR"
  if [ "$tag_id" = "$expected_id" ] && [ "$latest_id" != "$expected_id" ]; then
    log "還原舊 image tag：$want_tag → $IMAGE_REPO:latest"
    "$DOCKER_BIN" tag "$want_tag" "$IMAGE_REPO:latest" || die "還原 image tag 失敗"
  fi
  local live_id
  live_id="$("$DOCKER_BIN" image inspect -f '{{.Id}}' "$IMAGE_REPO:latest" 2>/dev/null || true)"
  [ "$live_id" = "$expected_id" ] || die "回滾後 latest image ID 唔等於舊 ID（$live_id vs $expected_id）"
  log "重啟 $CONTAINER_NAME"
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d --force-recreate "$COMPOSE_SERVICE"
  wait_http "$HEALTH_URL" 30 || die "回滾後 HTTP 健康檢查失敗"
  local list
  list="$(mktemp /tmp/aircon-rollback-list.XXXXXX)"
  ( cd "$VOL_DIR" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 ) >"$list"
  if ! diff -q "$bk/volume-file-list.txt" "$list" >/dev/null; then
    rm -f "$list"
    die "回滾後 volume 檔案清單同備份記錄唔一致"
  fi
  rm -f "$list"
  ok "volume 已精確還原（檔案清單 + hash 與備份完全一致）"
  python3 - "$bk" "$VOL_DIR/metadata.json" <<'PY'
import json, os, sys
bk, live_path = sys.argv[1], sys.argv[2]
live = json.load(open(live_path, encoding='utf-8'))
expected = None
p = os.path.join(bk, 'metadata.pre.json')
if os.path.exists(p):
    expected = json.load(open(p, encoding='utf-8'))
if expected:
    assert expected['build'] == live['build'], (expected['build'], live['build'])
    print('✅ 已還原原 build：', live['build'])
else:
    print('✅ volume 已還原（無舊 metadata 可比對）')
PY
  ok "回滾完成"
}

cmd_rollback() {
  local ts="${1:-latest}"
  if [ "$ts" != "latest" ] && ! valid_backup_ts "$ts"; then
    die "備份時間戳格式唔正確：$ts"
  fi
  if [ "$ts" = "latest" ]; then
    confirm "回滾到最新備份（由 root 階段解析；會停 aircon、還原 volume、重啟）？" ROLLBACK
  else
    confirm "回滾到備份 $ts（會停 aircon、還原 volume、重啟）？" ROLLBACK
  fi
  # S1：普通使用者唔需要（亦唔應該）讀取 root-only 備份目錄；由 root 階段解析 latest
  "$SUDO_BIN" bash "$SELF" --root-rollback "$ts"
}

root_rollback() {
  require_root
  resolve_targets 0
  local ts="${1:-}"
  [ -n "$ts" ] || die "需要備份時間戳或 latest"
  if [ "$ts" = "latest" ]; then
    ts="$(latest_backup_ts)"
    log "解析最新備份：$ts"
  fi
  valid_backup_ts "$ts" || die "備份時間戳格式唔正確：$ts"
  rollback_impl "$BACKUP_ROOT/$ts"
}

# ---------- 選單 ----------
cmd_menu() {
  while true; do
    cat <<EOT

===== aircon-compare 持久發佈（release $RELEASE_ID） =====
  1) Preflight / dry-run（唯讀檢查）
  2) Build staging（需要 sudo：讀 volume 最新資料 + 全套閘門）
  3) Verify staging（metadata/hash/可選 Playwright）
  4) Deploy 正式站（需要 sudo：停服務→備份→部署→驗證，失敗自動處理）
  5) Rollback（還原最新備份；sudo 階段解析）
  6) 開始 staging HTTP server（俾外部 Playwright 驗收）
  7) 停止 staging HTTP server
  8) Status
  0) 離開
EOT
    local choice=""
    read -r -p "選擇: " choice </dev/tty || break
    case "$choice" in
      1) cmd_preflight ;;
      2) cmd_build ;;
      3) cmd_verify ;;
      4) cmd_apply ;;
      5) cmd_rollback latest ;;
      6) cmd_serve ;;
      7) cmd_stop_serve ;;
      8) cmd_status ;;
      0|q|Q) break ;;
      *) echo "唔明白：$choice" ;;
    esac
  done
}

cmd_apply() {
  [ -f "$STAGE_ROOT/release-info.json" ] || die "未有 staging build；請先 build"
  echo "即將部署以下 staging 到正式站："
  cat "$STAGE_ROOT/release-info.json"
  echo
  echo "風險／行為："
  echo "  - 停服務前會重驗 staging（data/metadata/payload/image）+ volume 資料漂移"
  echo "  - 完整備份並驗證之後先會改 volume；備份未完成即失敗只會安全重啟原服務"
  echo "  - 停服務後任何失敗都會自動回滾（完整 volume 還原）"
  echo "  - 只 recreate aircon 服務；proxy / 憑證 / 其他服務不受影響"
  confirm "確認部署？" DEPLOY
  "$SUDO_BIN" bash "$SELF" --root-apply
}

usage() {
  cat <<EOT
用法：bash $0 [命令]
  （無參數）   互動選單
  preflight   唯讀檢查（tarball/hash/現況/流程）
  build       建立 staging（內部用 sudo 讀 volume + 跑閘門）
  verify      驗證 staging（schema/payload hash/可選 Playwright）
  serve       啟動 staging HTTP server（127.0.0.1:$STAGING_PORT）
  stop-serve  停止 staging HTTP server
  apply       部署 staging 到正式站（sudo、自動備份、失敗自動處理）
  rollback [TS]  回滾（預設最新；sudo 階段解析備份）
  status      顯示公開站 / staging / 備份狀態
  help        顯示本說明
EOT
}

main() {
  case "${1:-menu}" in
    ""|menu) cmd_menu ;;
    preflight|--dry-run|plan) cmd_preflight ;;
    build|--build) cmd_build ;;
    verify|--verify) cmd_verify ;;
    serve|--serve) cmd_serve ;;
    stop-serve|--stop-serve) cmd_stop_serve ;;
    apply|--apply) cmd_apply ;;
    rollback|--rollback) shift; cmd_rollback "${1:-latest}" ;;
    status|--status) cmd_status ;;
    help|-h|--help) usage ;;
    --root-build) root_build ;;
    --root-apply) root_apply ;;
    --root-rollback) shift; root_rollback "${1:-}" ;;
    *) usage; exit 2 ;;
  esac
}

# 被 source（sandbox 測試）時只定義函式，唔執行
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
