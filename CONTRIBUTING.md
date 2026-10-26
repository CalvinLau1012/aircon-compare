# 貢獻指引

感謝你參與這個香港空調對比項目。

## 修改前

1. 閱讀 `AGENTS.md`
2. 執行測試：

```bash
python -m pytest tests/ -q
python validate_data.py
```

3. 數據類修改須同時更新 `README.md`、`需求摘要.md`、`空調對比報告.md`
4. UI／CSS 修改（尤其是深色模式）須在 `generate_html.py` 修改，重新生成 `index.html`，並同步上述文件的更新日誌

## 提交建議

- 一個 PR 只做一件事
- 不要直接修改 `index.html`（它由 `generate_html.py` 生成）
- 不要提交 API key、token、secret

## 生成網頁

```bash
python generate_html.py
```

## 2026-10-01 文件／網頁日誌維護補充（追加；D32）

- 現行版本與部署索引見 README／CHANGELOG 最新入口；候選記錄的「未部署」屬歷史，最新事實見 docs/STATUS §29 及後續追加。
- 報告 md／HTML_TEMPLATE 修改先在隔離目錄生成明示 fixture HTML／PDF／metadata，做手機及深色瀏覽器驗證；不要手改 production metadata 或沿用舊 payload hash。
- 完成可信 PR CI 後，經 D31 `rebuild_verified_snapshot=true`、`force_price_batch=false` 在 master 重建，再核對 exact source/artifact 的 Pages 與 GATE-08。無須資料來源或 BigGo 呼叫；72h acquisition 年齡不能因重建而延長。
- 正常 daily 不變：00:30 HKT；價格 stage inactive 應零 token／search。Price.com 抓取仍放棄，私人 server 工作仍暫停。
- pytest 的 snapshot preflight 正向測試需乾淨 checkout；有使用者未提交修改時，用隔離 worktree，不覆蓋／stage／stash 該修改。新部署不能用上一輪測試數字冒充驗收。

## 2026-10-12 現況補充（追加；與 AGENTS 規則一致）

- 現行版本／部署索引見 README 頂部 2026-10-12 追加與 `docs/STATUS.md` §31；2026-10-01 節「私人 server 工作仍暫停」已過期，私人部署已恢復（獨立排程 00:30 HKT 運行中）。
- GitHub Actions schedule 係預定觸發；官方明示高負載可延遲甚至丟棄，00:30 HKT 唔係準時保證；本項目未有延遲量測，唔會自動補跑或重複查價。
- daily 只可經 `scripts/biggo_stage_runner.py` 受控入口；inactive 零 provider；合法 `no-price` 唔等於網絡故障。手動診斷 `--check-coordinator`（唯讀）同 `--execute`（provider 測試）互斥。
- 報告 md／HTML_TEMPLATE 文案改動先做隔離 fixture 驗證（D32），再由 D31 `rebuild_verified_snapshot=true`、`force_price_batch=false` 全量重生 `index.html`＋PDF＋metadata；未提交生成物之前唔可以宣稱已發布。

## 2026-10-26 文件／連結回歸補充（追加）

- 網站更新日誌只改 `空調對比報告.md` 內容來源；HTML／PDF 由生成器重建，不手改 `index.html` 或 `/app/web` 產物。私人站文件導覽用可公開解析的 GitHub URL，不用同源 `.md` 相對連結。
- 提交前至少跑：

```bash
python scripts/check_docs_links.py
python scripts/check_site_links.py --site-root <候選站根>
python -m pytest tests/test_docs_links.py tests/test_site_links.py tests/test_pdf_links.py -q
```

- 內部 4xx／5xx、缺 fragment、缺本地資產是阻斷；外部網站反爬／暫時錯誤只另列，不當內部壞鏈。時間敏感 run／build／測試數標明「當次證據」，不宣稱今日仍相同。
- 現行文件層級與逐份 action 見 `docs/README.md`、`docs/STATUS.md` §32、`docs/DOCS_SYNC_INVENTORY_20261026.json`。
