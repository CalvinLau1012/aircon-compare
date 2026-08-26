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

