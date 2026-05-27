# GitHub 自動化部署

目標：每天台北時間 20:30 自動抓資料、產生 `getmoneytommrrow.html`、部署到 GitHub Pages，並把報告網址推送到 Telegram。

## 1. 建立 GitHub Repo

1. 到 GitHub 建立一個新的 repository。
2. 把本專案推上去。
3. 不要把 `.env` 推上去，Telegram token 只放在 GitHub Secrets。

## 2. 設定 GitHub Pages

到 repo 的 `Settings -> Pages`：

- Source 選 `GitHub Actions`

部署成功後，報告網址會長得像：

```text
https://你的帳號.github.io/你的repo/getmoneytommrrow.html
```

## 3. 設定 Telegram Secrets

到 repo 的 `Settings -> Secrets and variables -> Actions -> New repository secret`，新增：

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

值就填本機 `.env` 裡目前使用的 token 與 chat id。

## 4. 啟用 Workflow

workflow 檔案在：

```text
.github/workflows/daily-report.yml
```

它會在每天台北時間 20:30 執行，也可以到 `Actions -> Daily Taiwan Stock Report -> Run workflow` 手動測試。

## 注意

- GitHub Actions 適合每天產生靜態報告與推送 Telegram 網址。
- GitHub Actions 不適合常駐互動式 Telegram 查詢 bot，因為工作執行完就會停止。
- 如果之後要讓 `/stock 2353` 這種查詢 24 小時在線，需要另外放在 Render、Railway、Fly.io、VPS 或 webhook 服務上。
