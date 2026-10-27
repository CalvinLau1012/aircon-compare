# 更新紀錄

> 本檔只記錄公開網站、公開資料與 GitHub Pages 的發布結果。非公開環境資料、設定及運作證據不收錄於公開倉庫。

## 2026-10-27 — 文件、連結及 Chrome 驗收完成

- PR #34 已合併；自動重建 run `40420036460` 產生 artifact commit `b1fcb68d4fb9e8df20767f466f7d64e2859bd3c3`。
- Pages run `40421702984` 的 build、deploy 及 GATE-08 全部成功；freshness run `40488083837` 判定公開網站資料新鮮。
- 公開 live 為 v1.2.9／build `B20261026.197.1`／datasetDate `2026-10-27`／1,782 筆登記／1,721 個型號；四個正式檔案與指定 artifact 逐 byte 相同。
- Root 以 Chromium 驗證搜尋、篩選、排序、比較視窗、Escape、響應式、console 及 request error，全部通過。
- 與 live candidate 連動的日期、筆數、build、commit、run、來源時間及狀態，改由 metadata 或收據動態取得；隔離 fixture 及安全／介面契約才可固定。
- 公開文件發布沒有重新取得 EMSD、品牌官網或價格資料，也沒有修改公開排程或憑證設定。
- 第一輪重建 run `40418450703` 在最終 push 遇到 GitHub HTTP 500；工作流正確 fail closed，沒有發布半成品。第二輪從最新 master 重新建包後成功。

## 2026-10-26 — 文件與網站更新日誌同步

- 更新 README、STATUS、DECISIONS、需求摘要、網站更新日誌及交付報告。
- 新增 Markdown、站內 fragment、下載檔及 PDF URL 檢查。
- 公開頁面及 PDF 由生成器重建，不直接修改封裝成品。

## 2026-10-24 — 自動更新接受閘門修正

- 候選接受測試與正式業務設定隔離，避免測試 fixture 被執行環境污染。
- 公開正常流程、Pages、GATE-08 及 freshness 均取得正式成功證據。

## 2026-09-24 — v1.2.9 發布

- v1.2.9 正式發布並啟用 GitHub Pages 自動部署。
- metadata、四檔 hash、部署後瀏覽器檢查及 freshness 閘門納入公開發布流程。

較早的內部運作紀錄不屬公開文件；公開版本與正式產物狀態以 [STATUS](docs/STATUS.md) 為準。
