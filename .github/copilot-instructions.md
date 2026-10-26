# GitHub Copilot 專案指令

修改前完整讀取 `docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源）。

1. 先識別任務模式（ANALYZE / DIAGNOSE / CHANGE / RELEASE / ROLLBACK），並檢查實際倉庫。
2. 區分 `REQUIREMENT` / `HISTORICAL_CLAIM` / `TARGET_STATE` / `OBSERVED` / `UNKNOWN`，不得用歷史聲明或目標狀態代替運行證據。
3. 不得自行禁用、降級或移除 `required` 功能；不得偽造 metadata、測試證據或放寬阻斷門禁。
4. 版本號只修改 `models_data.py` 的 `VERSION`；部署時間／資料日期由受信任流水線生成的 `metadata.json` 提供，不得手填。
5. 人類決策與技術限制導致的轉向，必須在 `docs/DECISIONS.md` 按模板記錄決策與原因。
6. 完成後報告：改動、實際驗證命令與結果、未執行項、功能影響（Registry ID）、數據影響、發布狀態、風險與回滾。
7. 規則衝突或缺少高風險授權時，停止相關動作並請求人類決定。

## 2026-10-26 現況補充（append-only）

- 現行文件層級見 `docs/README.md`；指定觀察時間見 `docs/STATUS.md` §32；本輪決策見 `docs/DECISIONS.md` D42。舊 STATUS／CHANGELOG／ADR／architecture 段落是歷史快照，不當今日狀態。
- 網站日誌唯一內容來源是 `空調對比報告.md`；HTML／PDF 只可由生成器重建。私人站只服務四檔及 runtime JSON，站內文件導覽必須用可公開解析的 GitHub URL，不可用同源 `.md`。
- 提交前跑 `python scripts/check_docs_links.py`、`python scripts/check_site_links.py --site-root <候選站根>` 及相關 pytest。內部 4xx／5xx、缺 fragment、缺本地資產阻斷；外部 URL 只另列。
- 時間敏感 run／build／測試數只作當次證據；不要把候選本機通過寫成正式上線，也不要用舊 run 冒充今日結果。
- 本輪零 BigGo／EMSD／品牌 provider 請求、零 coordinator 寫入、零 daily 補跑；正式部署由 Root／維護者執行。
