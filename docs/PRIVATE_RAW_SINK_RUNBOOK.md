# 私人 raw sink runbook（D7-A）

> 狀態：remote adapter 代碼已有 fake HTTP 測試證據，但 **未經人類選定 provider、未建
> Release、未上傳任何資產、未取得 live 證據**。未完成以下步驟前，唔可以講「remote
> 保存完成」，公開收據亦只會如實標示 `durableRemote` 狀態。

## 1. 啟用前置（全部要人類／平台完成）

1. 選定私人儲存方案（候選：Private GitHub Release asset）。若用其他 provider，
   需先實作對應 adapter 同 fake HTTP 測試，唔可以繞過 adapter 介面。
2. 準備最小權限憑證：
   - GitHub Release asset 候選需要可以建立 release／上傳／讀取／刪除 asset 嘅
     token（只限該私人 repo），唔好共用公開 repo token。
3. 喺 GitHub repo 設定 Secret（值唔會寫入 log；**私人 repo 識別只可以放 Secrets，
   唔准放 repo 檔／Variables／workflow log**）：
   - `AIRCON_EMSD_RAW_REMOTE_REPO`：`owner/name`（私人 repo）。
   - `AIRCON_EMSD_RAW_REMOTE_TOKEN`：私人 repo 最小權限 token。
   - 可選：`AIRCON_EMSD_RAW_REMOTE_TAG`（預設 `emsd-raw-archive`）、
     `AIRCON_EMSD_RAW_RETENTION_DAYS`（預設 90）。
4. 設定 `AIRCON_EMSD_REQUIRE_RAW_SINK=1`（現行 daily workflow 由
   `secrets.AIRCON_EMSD_REQUIRE_RAW_SINK` 讀取；repo 目前未有 Variables）。設定後任何
   raw persist 失敗（包括未配置／配置不完整）都會令 daily 阻斷，唔會寫成功 CSV 收據。
5. daily workflow 已經接入以上 env（`.github/workflows/daily-update.yml` 的
   `抓取 EMSD + 新機偵測` step）：`REQUIRE`、`REMOTE_REPO`、`REMOTE_TOKEN`、
   `REMOTE_TAG`、`RETENTION_DAYS`、`SINK_DIR` 全部精確對應 `secrets.*`。
   **只設定 Secret 而未選定 provider 之前，require 模式會照樣阻斷**；local-dir 只係
   過渡（公開收據會標示非 durable），唔可以當 long-term 保存。

## 2. 首次 live 驗收（人手，platform）

1. 手動 dispatch `每日偵測 · 新機分批更新`（master）。
2. 確認 run 成功，而且：
   - `emsd_receipt.json` 有 `rawReceiptHash`；
   - `emsd_raw_receipt.json` 的 `privateArchive.adapter` 係實際 adapter，
     `verified: true`、`durableRemote: true`、`retentionDays: 90`；
   - provider 後台見到新 asset，名以 `raw-` 開頭。
3. 由 provider 下載同一 asset，重算 sha256 同 `objectHash` 一致；
   確認 asset 唔可以被覆蓋（同名再上傳會失敗）。
4. 確認公開 repo worktree／Pages artifact／workflow log 都冇 raw HTML bytes、
   冇 token、冇私人 URL。
5. 確認舊 asset 清理只刪超過 90 日、同名前綴、唔係本次上傳嘅資產。

## 3. 未配置時嘅行為（fail-closed 契約）

- `AIRCON_EMSD_REQUIRE_RAW_SINK=1` 而完全無 sink 配置：`fetch_emsd.py` 非零退出，
  唔寫成功 CSV 收據（已有測試）。
- 有 remote repo 但無 token（或相反）：`private_raw_sink` 即 raise，唔會退回 local。
- remote 上傳後下載 hash 唔符、上傳 HTTP 非 201、provider 唔係 PRIVATE：一律
  raise，唔會寫公開 raw receipt，亦唔會寫成功 CSV 收據。
- 未設 `AIRCON_EMSD_REQUIRE_RAW_SINK`：今日仍係非 require 模式（唔寫 raw receipt）。
  呢個係平台 Secret 未配置嘅缺口（STATUS §14 `UNKNOWN`），唔可以當 D7-A 已完成；
  workflow 接線已存在，平台設定完成後即會生效。

## 4. 回滾

- 將 `AIRCON_EMSD_REQUIRE_RAW_SINK` 移除或設回非 `1`，daily 會停止要求 raw sink。
- 已上傳私人資產按 provider 政策保留或手動刪除；公開 repo 唔會因此改動。
- 唔需要亦唔准改 production `metadata.json`（版本／hash 由可信流程生成）。
