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
