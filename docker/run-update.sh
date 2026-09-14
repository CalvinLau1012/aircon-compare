#!/bin/bash
# =============================================================================
# aircon-compare 容器內資料更新 + 持久發佈管線
#
# 每日 03:30 HKT 由 /etc/cron.d/aircon 執行（無參數 = 完整流程）：
#   1. 抓 EMSD / 官網核實 / BigGo 價錢（與舊版相同）
#   2. 由 image 內嘅 /opt/aircon-src 同步程式碼落 volume /app（資料檔不會被覆蓋）
#   3. runtime 資料準備（黑名單 canonical 遷移，只做一次、有守衛）
#   4. 數據驗證 + 治理閘門 + 非瀏覽器測試
#   5. 生成 index.html / PDF / two-stage metadata / CSV
#   6. 原子部署 4 個檔案到 /app/web
#
# 亦支援：
#   --build-only   ：跳過所有網絡抓取，只做 2-6（由 release apply 用：資料已由 staging 帶入）
#   --release-build：只抓 EMSD（用新嘅安全 ingestion），再做 2-6（由 release staging build 用）
#
# 設計要點：
#   - 程式碼來源永遠係 image（/opt/aircon-src），volume 只保留資料；
#     每次 cron 都會同步，所以重新 build image 後新程式碼必定生效。
#   - 資料檔（*.json / *.csv / *-bak*、deploy_payload.json 除外）永不覆蓋。
#   - 遷移只在偵測到舊格式時執行一次，碰撞會阻斷；失敗唔會部署。
#   - release staging 會用新 PR-3 ingestion 重抓 EMSD，順便修復舊 volume CSV 嘅重複表頭。
# =============================================================================
set -euo pipefail

# 路徑可用環境變數覆寫：正式容器用預設值；sandbox 測試用 AIRCON_* 指向臨時目錄。
REPO="${AIRCON_REPO:-/app}"
WEB="${AIRCON_WEB_DIR:-$REPO/web}"
SRC="${AIRCON_SYNC_SRC:-/opt/aircon-src}"
DATA_MOUNT="${AIRCON_DATA_MOUNT:-/data}"
LOG="${AIRCON_LOG:-/var/log/aircon/update.log}"
LOCK="${AIRCON_LOCK:-/tmp/aircon-update.lock}"
MODE="full"

case "${1:-}" in
  --build-only)   MODE="build-only" ;;
  --release-build) MODE="release-build" ;;
  --sync-only)    MODE="sync-only" ;;
  ""|--full)      MODE="full" ;;
  *) echo "用法：$0 [--full|--build-only|--release-build|--sync-only]" >&2; exit 2 ;;
esac

log() { echo "[$(date '+%F %T %Z')] $*" | tee -a "$LOG"; }

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! mkdir "$LOCK" 2>/dev/null; then
  log "已有更新在執行，本次跳過"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# 讀取 Docker secrets / mounted .env（cron 唔會繼承 container 嘅 environment）
if [ -f /run/aircon-secrets/.env ]; then
  # shellcheck disable=SC1091
  set -a; . /run/aircon-secrets/.env; set +a
fi

cd "$REPO"
log "========== 容器內資料更新開始（mode=$MODE） =========="

# ---------------------------------------------------------------------------
# 0. 由 image 同步程式碼落 volume（--delete：image 係程式碼真源；資料永不刪）
#
# rsync --delete 只會刪除「sender 冇 + 冇被 exclude」嘅檔案；以下 exclude 同時
# 保護 runtime 資料／生成物／快取：*.json（deploy_payload.json 除外）、*.csv、
# *-bak*、logs、web/、快取。所以舊 image 移除嘅程式碼會被清走，但資料完整保留。
# ---------------------------------------------------------------------------
sync_code() {
  [ -d "$SRC" ] || { log "❌ 搵唔到 image 內程式碼來源 $SRC"; exit 1; }
  log "同步程式碼（--delete，排除 runtime 資料）：$SRC → $REPO"
  rsync -a --checksum --delete -i \
    --include='deploy_payload.json' \
    --exclude='*.json' \
    --exclude='*.csv' \
    --exclude='*-bak*' \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='*.log' \
    --exclude='index.html' \
    --exclude='空調對比報告.html' \
    --exclude='空調對比報告.pdf' \
    --exclude='metadata.core.json' \
    --exclude='docs/blacklist-migration-*.md' \
    --exclude='web/' \
    "$SRC"/ "$REPO"/
}

# ---------------------------------------------------------------------------
# 0b. staging 隔離模式：由 /data（read-only volume）帶入最新 runtime 資料
# ---------------------------------------------------------------------------
sync_data_from_mount() {
  [ -d "$DATA_MOUNT" ] || return 0
  log "由 $DATA_MOUNT 帶入最新 runtime 資料（staging 模式）"
  find "$DATA_MOUNT" -maxdepth 1 -type f \
    \( -name '*.json' -o -name '*.csv' -o -name '*-bak*' \) \
    ! -name 'deploy_payload.json' \
    -exec cp -a {} "$REPO"/ \;
}

sync_code
sync_data_from_mount

if [ "$MODE" = "sync-only" ]; then
  log "sync-only：程式碼/資料同步完成（測試模式），唔進行抓取或部署"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1-3. 網絡抓取（--build-only 完全跳過；--release-build 只抓 EMSD）
# ---------------------------------------------------------------------------
if [ "$MODE" = "full" ] || [ "$MODE" = "release-build" ]; then
  if python fetch_emsd.py >>"$LOG" 2>&1; then
    log "✅ EMSD 抓取完成"
  else
    log "⚠️ EMSD 抓取失敗，保留現有數據，繼續生成"
  fi
fi

if [ "$MODE" = "full" ]; then
  STAGE=$(python -c 'import json;print(json.load(open("update_queue.json",encoding="utf-8")).get("stage",0))' 2>/dev/null || echo 0)
  log "更新階段 stage=$STAGE"

  if [ "$STAGE" = "1" ]; then
    log "官網核實第一批"
    python fetch_official.py >>"$LOG" 2>&1 || true
    python fetch_shew.py >>"$LOG" 2>&1 || true
    python fetch_rasonic.py >>"$LOG" 2>&1 || true
    python advance_queue.py >>"$LOG" 2>&1 || true
  elif [ "$STAGE" = "2" ]; then
    log "官網核實第二批"
    python fetch_carrier.py >>"$LOG" 2>&1 || true
    python fetch_general.py >>"$LOG" 2>&1 || true
    python fetch_specs.py >>"$LOG" 2>&1 || true
    python advance_queue.py >>"$LOG" 2>&1 || true
  fi

  if [ -n "${BIGGO_CLIENT_ID:-}" ] && [ -n "${BIGGO_CLIENT_SECRET:-}" ]; then
    log "偵測到 BigGo credentials，先做 smoke 測試"
    if python fetch_biggo.py --smoke >>"$LOG" 2>&1; then
      if python -c 'from batch_utils import price_batch_active; import sys; sys.exit(0 if price_batch_active() else 1)' >>"$LOG" 2>&1; then
        log "開始 BigGo 價錢批次"
        python fetch_biggo.py --price-batch >>"$LOG" 2>&1 || log "BigGo 批次部分失敗（保留現有價錢）"
      else
        log "價錢批次未啟動，跳過"
      fi
    else
      log "BigGo smoke 失敗，跳過價錢批次"
    fi
  else
    log "無 BigGo credentials，跳過價錢批次（使用現有快照）"
  fi
else
  log "$MODE：跳過網絡抓取，使用 volume 現有資料"
fi

# ---------------------------------------------------------------------------
# 4. Runtime 資料準備 + 驗證 + 治理閘門（全部阻斷性）；pytest 移到 generate_html 之後
# ---------------------------------------------------------------------------
log "runtime 資料準備（黑名單 canonical 遷移守衛）"
python scripts/prepare_runtime_data.py || { log "❌ runtime 資料準備失敗，中止部署"; exit 1; }

log "數據驗證"
python validate_data.py || { log "❌ 數據驗證失敗，中止部署"; exit 1; }

log "治理區塊驗證（GATE-01）"
python scripts/extract_governance.py || { log "❌ GATE-01 失敗，中止部署"; exit 1; }

log "功能註冊表檢查（GATE-03）"
python scripts/feature-check.py || { log "❌ GATE-03 失敗，中止部署"; exit 1; }

# ---------------------------------------------------------------------------
# 5. 生成網頁 / PDF / two-stage metadata
# ---------------------------------------------------------------------------
log "生成網頁"
python generate_html.py || { log "❌ 生成 HTML 失敗"; exit 1; }
cp "空調對比報告.html" index.html

# 測試要在 generate_html 之後：test_dynamic_counts / test_energy_distribution 會用
# index.html 對比目前資料。volume/container 內舊 index.html 唔一定同新抓資料一致，
# 所以先重建 artifact 再跑測試，先係對「將要部署嘅產物」做有效閘門。
log "測試閘門（非瀏覽器；對已重建 artifact）"
python -m pytest tests/ -q --ignore=tests/browser_smoke.py || { log "❌ 測試失敗，中止部署"; exit 1; }

DATASET_HASH="sha256:$(sha256sum 'emsd_空調能源標籤.csv' | cut -d' ' -f1)"
RAW_RECORDS=$(python -c "import csv;print(sum(1 for _ in csv.reader(open('emsd_空調能源標籤.csv',encoding='utf-8-sig'))) - 1)")
REGISTRATIONS=$(python -c "from crawl_utils import load_registrations;print(len(load_registrations()))")
MODELS=$(python -c "from crawl_utils import load_models;print(len(load_models()))")
VERSION=$(python -c 'import models_data;print(models_data.VERSION)')
COMMIT=$(cat "$SRC/.release-commit" 2>/dev/null || echo unknown)
RUN_ID="docker-$(date -u +%Y%m%d%H%M%S)"
BUILD="B$(date -u +%Y%m%d).$RUN_ID"
CORE="$(mktemp /tmp/metadata.core.XXXXXX.json)"

log "核心事實：version=$VERSION commit=$COMMIT modelCount=$MODELS registrationCount=$REGISTRATIONS rawRecordCount=$RAW_RECORDS"

python scripts/gen-metadata.py --stage core \
  --out "$CORE" --force \
  --version "$VERSION" \
  --build "$BUILD" \
  --commit "$COMMIT" \
  --workflow-run-id "$RUN_ID" \
  --dataset-date "$(date +%F)" \
  --dataset-date-basis retrieval-date-fallback \
  --dataset-source-url "https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php" \
  --dataset-snapshot-id "emsd-$(date +%F)" \
  --dataset-hash "$DATASET_HASH" \
  --record-count "$MODELS" \
  --raw-record-count "$RAW_RECORDS" \
  --registration-count "$REGISTRATIONS" \
  --model-count "$MODELS" || { log "❌ core metadata 失敗"; rm -f "$CORE"; exit 1; }

log "生成 PDF（用同一 run core metadata）"
python generate_pdf.py --metadata "$CORE" || { log "❌ PDF 失敗"; rm -f "$CORE"; exit 1; }

log "finalize metadata（payload manifest hash）"
python scripts/gen-metadata.py --stage finalize \
  --core "$CORE" \
  --payload-manifest deploy_payload.json \
  --out metadata.json --force || { log "❌ finalize metadata 失敗"; rm -f "$CORE"; exit 1; }
rm -f "$CORE"
test ! -e metadata.core.json || { log "❌ 未 finalize 中間檔唔應該存在"; exit 1; }

python scripts/validate_metadata.py || { log "❌ metadata Schema 驗證失敗"; exit 1; }

# ---------------------------------------------------------------------------
# 6. 原子部署 4 個檔案到 /app/web
# ---------------------------------------------------------------------------
mkdir -p "$WEB"
for f in index.html "空調對比報告.pdf" "emsd_空調能源標籤.csv" metadata.json; do
  [ -f "$REPO/$f" ] || { log "❌ 缺少部署檔 $f"; exit 1; }
  cp "$REPO/$f" "$WEB/$f.new"
  mv -f "$WEB/$f.new" "$WEB/$f"
done

log "✅ 部署完成：build=$BUILD · datasetDate=$(date +%F) · modelCount=$MODELS · registrationCount=$REGISTRATIONS"
log "========== 完成 =========="
