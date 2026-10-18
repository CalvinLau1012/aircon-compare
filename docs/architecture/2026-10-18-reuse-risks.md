# 2026-10-18 重用風險與未確認清單（Stage 0）

## BigGo 實際發送節流（本輪不修，列後續設計）

已核對的靜態事實：

- `fetch_biggo.py` 有 `BIGGO_MIN_PACE`，預設 **2.5 秒**；`_wait_pace()` 由兩個 worker 共用；批次路徑在 `_api_search` 內先 `_wait_pace()`，之後才 `_get_access_token()`，所以 auth／token 與 search 沒有一個涵蓋兩者的統一 actual-send pacing。
- smoke 明確傳 `use_pace=False`，不等待 pace。
- `fetch_biggo.py` 的 `_api_search` 是批次與 smoke 的共同低層入口；不能只因 batch 有 pace 就推論 auth 或 smoke 已受同一節流保護。
- 2026-10-18 HKT 14:04 的真實 probe：**認證請求返回 HTTP 429，search 0 次即停**。不可說「香港商品搜尋配額已耗盡」或「search endpoint 已限流」；當時沒有產品 search 請求，`site=biggo.hk/region=hk` header 亦不在 auth 請求中。
- 同日 20:38／20:46 HKT 兩端 normal full 均 skip-cooldown，provider **0 token／0 search**；之後未再呼叫。
- BigGo HK 後台當日請求數顯示 **0**；dashboard 是否計入失敗認證、帳戶 metric 口徑仍 **UNKNOWN**，不可用此數字反推 provider quota 已恢復或已耗盡。
- provider quota 仍然 **UNKNOWN**；不能從 429 推導具體剩餘配額，也不能聲稱加搜尋 delay 就必定解除 429。

### 需要納入後續共用節流模塊的 outbound 入口

| 入口 | 目前路徑／事實 | 風險 |
|---|---|---|
| Daily smoke | `scripts/biggo_stage_runner.py` → `fetch_biggo.run_smoke` | `use_pace=False`；token／auth 後才 search |
| Daily price batch | `scripts/biggo_stage_runner.py` → `fetch_biggo` batch | batch `_wait_pace` 在 token 前；auth 後 search 可相連 |
| CLI smoke／batch | `fetch_biggo.py --smoke`／`--price-batch` | 直接入口，與 runner 共用低層 pacing 狀態但無統一 actual-send gate |
| Coordinator diagnostic | `scripts/biggo_smoke_diagnostic.py --execute` → `run_smoke` | 有 lease／budget／cap，仍走 smoke 無 pace |
| Test probe | `scripts/biggo_test_probe.py` → `run_smoke` | 受限授權測試，非 daily；仍需實際發送邊界 |
| 其他 BigGo 相關 workflow | `biggo-alert.yml`（只核實 artifact）、`biggo-diagnostic.yml`（preview／read-only／execute） | alert 不應被誤列為 provider；execute 才可能 provider |

後續 Root 設計（尚未實作）：同一 pacing 覆蓋 token／auth、search、smoke、兩個 workers；等待可中止；等待後重驗 lease／budget／cooldown；429 即停並保留 `Retry-After` 及 48h fallback；冷卻中不試價；fake clock／network stub 驗證；預設 2.5 秒不擅自改 5 秒或縮短；未知 quota 不編造。**本輪沒有修改 fetch_biggo／biggo_limiter／stage_runner／diag，也沒有 provider／coordinator 操作。**

## 重用與接線風險

- 同名 `production.py` 不能當作已重用：歷史 81 個同名檔中 62 個 byte 相同、19 個有差異；本 Stage 0 掃描範圍更廣，數字不可互換或宣稱固定百分比。
- 私人 legacy deployment tree 的部分模塊可能有不同接線；本 repo 靜態掃描不能證明它們是 daily 已使用；詳細名稱與狀態留在私人 repo／交付，不在公開文件公開。
- 私人功能（收藏、組合、備註、import／export、provenance、動態 EMSD 報告）不可因 public 沒有對應而刪除。
- 靜態 import 不等於 runtime call；`source_health_monitor.py` 在本 repo 無 workflow 入口，私人 deployment 實際接線仍 UNKNOWN。
- 公開文件／JSON 不得包含私人 root、domain、repo、token、個人 user；私人詳細差異留在私人 repo 或 repo 外交付。

## 本輪未做

- 沒有報告共用／安全 JSON／前端拆分（按計畫等 Root 接受本輪）。
- 沒有改門檻、health gate、fingerprint、issue 去重、恢復或 coordinator 狀態。
