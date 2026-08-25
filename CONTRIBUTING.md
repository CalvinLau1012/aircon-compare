# 貢獻指引

多謝你想參與呢個香港空調對比項目。

## 改動前

1. 睇 `AGENTS.md`
2. 跑測試：

```bash
python -m pytest tests/ -q
python validate_data.py
```

3. 數據類改動要同時更新 `README.md`、`需求摘要.md`、`空調對比報告.md`
4. UI／CSS 改動（尤其深色模式）要喺 `generate_html.py` 改，重新生成 `index.html`，並同步上述文件嘅更新日誌

## 提交建議

- 一個 PR 只做一件事
- 唔好直接改 `index.html`（佢係 `generate_html.py` 生成）
- 唔好提交 API key、token、secret

## 生成網頁

```bash
python generate_html.py
```
