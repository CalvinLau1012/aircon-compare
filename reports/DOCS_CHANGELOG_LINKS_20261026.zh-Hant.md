# 公開文件、更新日誌與連結修復報告

## 1. 範圍

本報告只涵蓋公開 GitHub 文件、公開網站來源、GitHub Pages、公開四檔及公開驗收。非公開環境的資料、設定、識別與運作證據不收錄。

## 2. 文件更新

- README、CHANGELOG、公開 STATUS、公開 DECISIONS、文件索引、需求摘要及網站更新日誌已同步。
- 公開文件改為精簡純公開版本；完整舊內容保留於本機權威元文件，沒有改寫 Git 歷史。
- dataset date、筆數、build、commit、run、來源時間及狀態均按 metadata／收據／內容動態驗證。

## 3. 連結修復

- `scripts/check_docs_links.py` 驗 Markdown 目標與 fragment。
- `scripts/check_site_links.py` 驗站內導覽、資產、四個下載檔及 fragment。
- `tests/test_pdf_links.py` 驗 PDF 只產生正確絕對 HTTP(S) annotations。
- 外部網站暫時錯誤不冒充站內壞鏈；站內缺檔、4xx／5xx 或缺 fragment 會阻斷。

## 4. Chrome UI

正式本機 UI 證據使用 Playwright Chromium。已覆蓋手機／桌面、深淺色、hero、更新日誌、搜尋、篩選、排序、比較及鍵盤操作。字型或 browser library 缺失屬測試環境故障，不改用另一引擎冒充通過。

## 5. 公開發布證據

- PR #34 merge `dcc602c03138afe98f0fc5d221821de36251826c`。
- 自動重建 run `40420036460` success；artifact `b1fcb68d4fb9e8df20767f466f7d64e2859bd3c3`。
- Pages run `40421702984` 的 build／deploy／GATE-08 success。
- Freshness run `40488083837`：configured、ok、fresh，failures 空。
- Live v1.2.9／build `B20261026.197.1`／datasetDate 2026-10-27／1,782 registrations／1,721 models。
- 四檔與指定 artifact 逐 byte 相同；Root Chromium post-deploy 全部通過。

## 6. Fail-closed 記錄

第一輪重建 run `40418450703` 在最後 push 遇 GitHub HTTP 500，沒有 dispatch Pages 或發布半成品。第二輪由最新 master 重新建包後成功；沒有將未推送 commit 當成已上線。

## 7. 最新候選測試

| Gate | 結果 |
| --- | --- |
| 完整 acceptance | 7/7 rc=0 |
| Pytest | 1,419 tests；0 failure／error／skip |
| Chromium targeted | 19/19 |
| Markdown links | 25 files／76 links／19 fragments／0 broken |
| Public privacy | 0 命中 |

## 8. 邊界

- 文件發布沒有重新取得 EMSD、品牌官網或價格資料。
- 沒有修改公開自動化設定或排程。
- 非公開環境資料、設定、識別、hash、收據、路徑及運作證據不進入公開 repo、網站或 PDF。
- 舊價格快照缺可信取得時間時保持 unknown，不以套用或部署時間補造。
