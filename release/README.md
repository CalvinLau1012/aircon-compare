# Release 299c3e9 — 持久發佈工具（自建／self-host）

> 呢個工具係通用自建部署流程：所有環境專屬設定（base dir、compose file、host、憑證）都由
> 環境變數／你本地嘅 private notes 提供，repo 內冇任何個人環境默認值。

## 一、呢個 release 解決咩問題

靜態替換 web 四檔唔持久：容器內 cron 每日會用 volume 內舊程式碼重新生成
`index.html` + `metadata.json`，而且可能把舊資料快照覆蓋較新資料。

本 release 採用**程式碼持久化**方案：

1. **image 重建**：`docker build` 出帶新程式碼嘅 image，並在 image 內 `/opt/aircon-src`
   保存 pristine 程式碼。
2. **volume 程式碼同步**：`docker/run-update.sh` 每次執行（包括每日 cron）都會由
   `/opt/aircon-src` 以 `rsync --checksum --delete` 同步程式碼落 volume `/app`；
   **資料檔（`*.json`／`*.csv`／`*-bak*`）同 `web/` 永遠唔會被覆蓋或刪除**
   （`deploy_payload.json` manifest 除外）。
3. **兩階段 metadata + 4 檔部署**：同一條管線跑 `validate_data` → 治理 GATE-01/03 →
   非瀏覽器 pytest → `generate_html` → core metadata → PDF → finalize
   （`deploy_payload.json` manifest hash）→ 原子部署 `index.html`／PDF／CSV／`metadata.json`。
4. 因此 **cron 之後新程式碼仍然生效**；資料保持 volume 內最新版本。

## 二、環境變數（部署前必須提供）

| 變數 | 用途 | 備註 |
| --- | --- | --- |
| `AIRCON_BASE_DIR` | 部署封裝根目錄（預設 `/srv/aircon-compare`） | 內含 compose file、staging、backups |
| `AIRCON_COMPOSE_FILE` | docker compose 路徑（預設 `$AIRCON_BASE_DIR/compose.yaml`） | 必須存在 |
| `AIRCON_IMAGE_REPO` | image repo 名（預設 `aircon-compare`） | |
| `AIRCON_CONTAINER` / `AIRCON_COMPOSE_SERVICE` | 服務名（預設 `aircon`） | |
| `AIRCON_HEALTH_URL` | 部署後健康檢查 URL | |
| `AIRCON_STAGING_PORT` | staging HTTP server port（預設 `8788`） | |

腳本唔會提供任何 host／user／domain／憑證默認值；缺失或路徑唔存在時會 fail closed 並提示。

## 三、使用方式

```bash
# 先 export 上面所需環境變數
bash release/release-299c3e9.sh            # 互動選單
bash release/release-299c3e9.sh preflight
bash release/release-299c3e9.sh build|verify|serve|apply|rollback
```

流程：**preflight → build staging → verify →（可選 serve 外部瀏覽器驗收）→ apply**；
`sudo` 只會由你嘅終端直接讀密碼（腳本唔會讀取／保存／回顯密碼）。

## 四、部署安全設計

- container／volume／web root 由 `docker inspect` 動態解析（`docker ps -aq`，running 或 stopped 都支援；
  多個目標即拒絕）；只寫入 allowlist：volume 程式碼 + web 四檔。
- **停服務前 TOCTOU 重驗**：staged-data 清單+hash、metadata schema、payload hash、
  release image ID、volume 資料漂移；任何一項唔符即阻斷。
- **停機分階段**：`stopped`（備份未完成）失敗只安全重啟原服務；`backup_ready`／`applying`
  失敗才自動回滾。備份必須 archive 可讀 + checksum + 檔案清單一致。
- **舊 image 備份**：停服務前必須成功建立 pre tag 並驗證 ID，否則阻斷。
- **rollback latest**：由 root 從新到舊挑第一個完整備份（缺件跳過並 warning），
  回滾前先驗 archive + image tag 可還原。
- `docker/run-update.sh` 每次以 `rsync --checksum --delete` 同步程式碼（image 為真源），
  runtime 資料與 `web/` 永久排除。

## 五、驗收 / 測試

- `release/sandbox/run_sandbox_tests.sh`：116 斷言／14 情境（dry-run、path guard、TOCTOU、
  備份失敗安全、stopped container、rollback 權限、sync --delete）。
- `bash release/sandbox/test_sync_code.sh`：真 rsync 語義測試（有 rsync 先跑）。
- `release/runtime_smoke.py`：Playwright 瀏覽器驗收（`--base <URL>`）。
- 全部 shell 腳本通過 `bash -n` + ShellCheck。
- `scripts/check_public_privacy.py`：公開 repo 私隱 gate（HEAD 掃描；只列規則 ID／檔名）。

## 六、注意

- staging build 用 volume read-only 讀資料；apply 前會核對資料無漂移。
- apply 時 `metadata.deployTime`／`build` 會重新封裝（PDF 內嵌 deployTime），
  語義欄位（version／commit／datasetDate／datasetHash／counts）必須同 staging 一致。
- 私人部署設定（真實 host／domain／憑證）只應放本機 gitignored notes，唔好加入 commit。
