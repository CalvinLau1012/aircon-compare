# Release 299c3e9 — 持久正式發佈工具

## 一、呢個 release 解決咩問題

上一輪嘅「靜態替換 web 四檔」方案唔持久：容器內 cron（每日 03:30 HKT）會用 volume 內舊程式碼
重新生成 `index.html` + `metadata.json`，而且會把 9 月 2 日快照覆蓋線上較新資料。

本 release 採用**程式碼持久化**方案：

1. **image 重建**：`docker build` 出帶新程式碼嘅 `aircon-compare:release-299c3e9`，
   並在 image 內 `/opt/aircon-src` 保存 pristine 程式碼。
2. **volume 程式碼同步**：`docker/run-update.sh` 每次執行（包括每日 cron）都會由
   `/opt/aircon-src` rsync 程式碼落 volume `/app`；**資料檔（`*.json`/`*.csv`/`*-bak*`）
   永遠唔會被覆蓋**（除 `deploy_payload.json` 係 manifest）。
3. **兩階段 metadata + 4 檔部署**：每日 cron 同 release 都用同一條管線：
   `fetch` → `validate_data` → 治理 GATE-01/03 → `generate_html` → 非瀏覽器 pytest →
   core metadata → PDF → finalize（`deploy_payload.json` manifest hash）→
   原子部署 `index.html` / PDF / CSV / `metadata.json` 到 `/app/web`。
   - 測試喺 `generate_html` 之後跑：驗證嘅係「即將部署嘅 artifact」同目前資料一致。
   - `generate_html.py` 會將報告內文「全量資料庫 N 型號」同步為建置時實際數字
     （治理 F-11：當前狀態數字唔可以硬編）。
4. 因此 **cron 之後新程式碼仍然生效**；資料保持 volume 內最新版本，唔會倒退。

## 一點五、EMSD 舊資料修復（release 一次性）

線上 volume 嘅 `emsd_空調能源標籤.csv` 係舊 ingestion 產物（每頁表頭重複，1912 行＝1875 真實登記
＋37 個重複表頭），新 `validate_data` 會阻斷。Release staging build 用 **PR-3 新版 `fetch_emsd.py`**
重新抓 EMSD（38/38 頁，出 `emsd_receipt.json` 證據），得到乾淨 CSV（1875 登記）；apply 會將
已驗證嘅 staging 資料安裝到 volume，之後每日 cron 繼續用新 ingestion。若抓取失敗／行數不足，
PR-3 安全閘門保留舊檔 → `validate_data` 阻斷 → 唔會部署。

## 二、一次性 runtime 資料遷移

舊 volume（2026-08-26 或之前）嘅 `model_blacklist.json` / `model_status.json` key 係原始型號
（例如 `RC-X7U`），新程式碼（D11）預期 canonical `BRAND|NORM`。遷移**唔係 idempotent**，
所以 `scripts/prepare_runtime_data.py` 會：

- 先偵測舊格式 key；
- 只喺需要時執行已審閱嘅 `scripts/migrate_blacklist_keys.py`（會自行備份 `-bak-canonical-migration` + 出報告）；
- 遷移後驗證（舊 key = 0、key 數目不變；碰撞由遷移腳本阻斷）。

## 三、使用方式（唯一入口）

```bash
ssh calvin@192.168.8.16
bash /home/calvin/aircon-docker/release-299c3e9.sh
```

互動選單：

```
1) Preflight / dry-run     —— 唯讀：tarball hash、現況、流程
2) Build staging           ——（sudo）隔離 staging + 全套閘門；唔會改線上
3) Verify staging          —— metadata schema / payload hash /（有 playwright 就跑 smoke）
4) Deploy 正式站           ——（sudo）停 aircon → 完整備份 → 部署 → 重啟 → 驗證；失敗自動回滾
5) Rollback                ——（sudo）還原 volume + 舊 image tag
6) Serve staging           —— 127.0.0.1:8788，俾外部 Playwright 驗收
7) Stop serving
8) Status
```

亦可以：`bash release-299c3e9.sh preflight|build|verify|serve|apply|rollback [TS]|status`。

流程：**1 → 2 → 6（外部 Playwright 驗收）→ 4**；每一步都有明確確認，
`sudo` 只會由你嘅終端直接讀密碼（腳本唔會讀取／保存／回顯密碼）。

## 四、部署安全設計（第二輪加固）

- `container` / `volume` / `web root` 由 `docker inspect` 動態解析（用 `docker ps -aq`，running 或 stopped 都得；多個目標即拒絕）；
  非 `/var/lib/docker/volumes/*/_data` 或 symlink 一律拒絕。
- 只寫入 allowlist：`/app` 程式碼 + `/app/web` 四個檔案；proxy／憑證／其他服務不受影響。
- **S1 rollback 權限**：備份目錄 root-only（700）；`rollback latest` 由 root 階段解析，普通使用者毋須讀取。
- **S2 停服務前 TOCTOU 重驗**：staged-data 清單+hash（manifest hash + 檔案數）、metadata schema、payload hash、
  release image ID、volume 資料漂移；任何一項唔符即阻斷，服務不受影響。
- **S3 停機分階段**：`stopped`（備份未完成）失敗 → 只安全重啟原服務；`backup_ready`／`applying` 失敗 → 自動回滾。
  停機前另有 **S3a/S3b**：舊 `latest` image ID 讀取失敗、pre tag 建立失敗、或 tag ID 唔等於舊 ID，
  三者一律在停服務前阻斷（保證回滾一定有可用舊 image）；
  備份必須 archive 可讀 + checksum + 檔案清單 + image pre 記錄一致才會進入下一階段；
  回滾還原整個 volume，所以部署新增嘅 PDF/CSV/receipt 會被精確刪除。
- **S4 備份選擇**：`rollback latest` 由 root 從新到舊挑第一個完整候選（volume.tar.gz／checksum／
  檔案清單／image pre ID+tag／release-info），不完整目錄跳過並 warning（唔會自動刪除）；
  只有不完整備份時明確拒絕，且唔會停服務／改 volume。
- **S5 程式碼同步**：`rsync -a --checksum --delete` 以 image `/opt/aircon-src` 為真源（內容比對，stale 程式碼會刪；
  同 size/mtime 嘅舊內容都會更新），同時永久排除 `*.json`／`*.csv`／`*-bak*`／`web/`／logs／快取；`deploy_payload.json` 照同步。
- 部署後：HTTP 健康檢查、`index.html`/CSV hash 對 staging、metadata 語義欄位一致、
  live payload hash 自洽、四個 URL 200。
- 手動回滾：`bash release-299c3e9.sh rollback`（預設最新備份）。

## 五、驗收 / 測試

- `release/sandbox/run_sandbox_tests.sh`：**116 斷言 / 14 情境**，覆蓋 dry-run 不寫、path guard、
  tarball/資料漂移阻斷、部署成功、回滾（含刪除新增檔）、失敗自動回滾、重跑安全、
  rollback 權限（root-only 目錄）、root 解析 latest、stopped/歧義 container、
  TOCTOU（staged-data 改／刪、metadata、image ID）、備份失敗安全（mkdir／停機後 hook／archive 截斷／
  manifest 消失／tar 讀取失敗）、image 備份 tag 失敗（inspect／tag／ID 唔符）、latest 完整備份選擇、
  sync --delete（stale code 刪除、runtime 資料保留）。
- `docker/run-update.sh --sync-only` 可用環境變數指向臨時目錄，用真 rsync 驗證同步語義。
- `docker/run-update.sh` 內每次都會跑：`validate_data` + GATE-01 + GATE-03 +
  `pytest tests/ -q --ignore=tests/browser_smoke.py`。
- `release/runtime_smoke.py`：Playwright 瀏覽器驗收（搜尋／篩選／排序／比較／
  Escape 焦點／響應式／對比度／能源表 vs metadata counts）。
- 全部 shell 腳本通過 `bash -n` + ShellCheck（0.10.0, `-S warning` 無問題）。

## 六、注意

- staging build 用 volume **read-only** 讀資料；apply 先停容器，所以 build 之後
  資料唔會漂移（腳本會用 `data-hashes.txt` 再核對，唔一致會叫你先重新 build）。
- 部署時 `metadata.deployTime` / `build` / `releasePayloadHash` 會用部署時刻重新封裝
  （PDF 內嵌 deployTime），語義欄位（version/commit/datasetDate/datasetHash/counts）
  必須同 staging 一致。
- 舊嘅 `deploy-candidate-299c3e9.sh`（上一輪靜態方案）已停用，唔好再用。
