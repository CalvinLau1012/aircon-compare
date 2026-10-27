# 公開決策紀錄

> 本檔只保留影響公開網站、公開資料及 GitHub Pages 的現行決策。完整內部運作歷史不屬公開倉庫。

## D-PUB-1 · 公開／非公開資料隔離

- 公開 repo 只保存公開網站所需的程式、資料、文件及公開驗收證據。
- 非公開環境的資料、設定、識別、hash、收據、路徑及運作證據不得加入公開 repo、網站、PDF 或 Actions artifact。
- 公開私隱 gate 必須在提交及發布前通過。

## D-PUB-2 · 動態資料與固定契約

- dataset date、登記／型號數、build、commit、workflow run、取得／套用時間、stage／status 及最新日誌日期，必須由 metadata、收據或報告內容動態取得。
- 隔離單元 fixture 可使用固定 sentinel，但不得拿來判斷 live candidate。
- `00:30 HKT` 排程、72 小時 freshness、介面尺寸上限、hash／收據 fail-closed 及每個公開 artifact 的 source pin 屬契約，可固定；source pin 必須隨新 release 精確更新。

## D-PUB-3 · 原子發布與 fail-closed

- HTML、PDF、CSV 與 metadata 必須視為同一發布單位，hash 及來源互相綁定。
- 候選在 commit 前須於隔離 clean snapshot 跑完整 gates。
- push、來源綁定、artifact 建置、Pages deploy 或部署後驗證任何一步失敗，均不得把部分產物視為成功。
- 程式或報告來源改變時必須重建 HTML／PDF／metadata，不沿用不相容舊產物。

## D-PUB-4 · Chrome 驗收

- GitHub Daily、Pages 及 GATE-08 的正式瀏覽器契約使用 Chromium。
- 缺字型或 browser library 屬驗收環境問題，應修復環境；不得改用另一引擎冒充 Chrome 通過。
- 搜尋、篩選、排序、比較、Escape、focus、手機／桌面、深淺色、console 及 failed request 都屬發布後檢查範圍。

## D-PUB-5 · 來源時間與待補資料

- EMSD、品牌官網、價格快照及發布時間分開記錄，不互相補造。
- 官網確實沒有可驗證資料時保持正常待補；抓取、TLS、parser、收據、hash 或部署錯誤仍須 fail closed。
- 價格原始取得時間沒有可信證據時保持 unknown。

## D-PUB-6 · 2026-10-27 發布閉環

- PR #34 merge `dcc602c`。
- 自動重建 `40420036460` 成功，artifact commit `b1fcb68`。
- Pages `40421702984` 的 build、deploy、GATE-08 成功。
- Freshness `40488083837` 判定 fresh。
- 公開文件後續更新繼續遵守 D-PUB-1 至 D-PUB-5。
