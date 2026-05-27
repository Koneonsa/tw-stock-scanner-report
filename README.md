# 台股每日線型掃描報告

這是一套台股上市 + 上櫃 OHLCV 掃描工具，用來每天盤後尋找「底部初動、突破回踩、高檔健康回落、量增止跌、成交量暴增」等線型。現在主要輸出是一份可互動的靜態 HTML daily report，並透過 Telegram bot 推送報告網址。

目前公開報告網址：

```text
https://koneonsa.github.io/tw-stock-scanner-report/getmoneytommrrow.html
```

## 目前自動化流程

GitHub Actions 會在每天台北時間 20:30 自動執行：

1. 抓 TWSE / TPEx OpenAPI 最新日資料。
2. 用 yfinance 回補最近 300 個交易日 OHLCV。
3. 存入 DuckDB 並重新掃描全市場。
4. 產生 `reports/getmoneytommrrow.html`。
5. 部署到 GitHub Pages。
6. 排程執行時把 `getmoneytommrrow.html` 網址推送到 Telegram。

手動 push 到 `main` 也會重新部署報告，但不會推 Telegram。手動執行 workflow 時可選擇是否推 Telegram。

workflow 檔案：

```text
.github/workflows/daily-report.yml
```

## 報告功能

- 上方統計各類型有多少檔，點擊可跳到該類型。
- 搜尋股票代號或名稱後可跳到個股卡片。
- Top 5 進場觀察可點擊股票，直接跳到該個股卡片。
- 個股名稱點擊會開 TradingView 完整圖表。
- 卡片顯示今日收盤價、漲跌幅、型態契合度、Fib 位置、突破基準、量能、進場價、目標價、停損價與風報比。

## 策略分類

### Top 5 進場觀察

這不是單一策略，而是從所有策略裡挑出最接近可操作的前 5 檔。純成交量暴增、純日內突破回踩不會進 Top 5。

### 底部起漲

- 低位階，股價與 MA60 仍在 MA250 附近或以下。
- 90 日區間振幅小於 60%。
- 最近 60 日低點沒有持續破底。
- MA60 斜率走平或上彎。
- 最近 30 日內自波段低點起漲至少 15%。
- 30 日均量至少 1000 張。
- 止跌/轉強訊號至少 2 個。

### 突破回踩

- 原本盤整或下跌後，突破前一個波段高點。
- 突破與回踩必須發生在不同交易日。
- 收盤價回到突破基準正負 5% 內。
- 突破尺度會看 60 / 90 / 120 / 180 / 300 日，越長期突破分數越高。

### 日內突破回踩

- 今日盤中突破前波高點，但同日收盤壓回突破基準附近。
- 時間尺度較短，與正式突破回踩分開呈現。

### 高檔回落觀察

- 30 日均量至少 1000 張。
- 今日收盤價相對最近 30 日高點回落 10% 以上。
- Fib 位置在 0.5 到 0.786。

### 量增止跌 / 量增止跌觀察

- 昨日大量紅 K 先止跌。
- 若今日站上昨日高點，列為量增止跌。
- 若尚未站上昨日高點，但型態接近，列為量增止跌觀察。

### 成交量暴增

- 30 日均量至少 1000 張。
- 今日成交量大於 30 日均量 1.6 倍。
- 這偏向警訊，通常需要搭配其他策略一起看。

## Fib 計算

Fib 不是用固定時間範圍的絕對高低點，而是用波段：

- Fib 0：最近 180 日內先出現的波段低點。
- Fib 1：該低點之後出現的波段高點。
- `fib_position = (close - fib_low) / (fib_high - fib_low)`

報告會顯示目前股價所在區間，例如：

```text
0.618-0.786 強勢修復
0.950-1.050 突破附近
```

## Telegram

需要在 GitHub repository secrets 設定：

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

GitHub Actions 排程執行時會把報告網址推送到 Telegram。若是手動 push 或手動測試，預設不推 Telegram。

本機也可以手動推送：

```bash
set -a
. ./.env
set +a
python3 -m scanner.cli notify-telegram
```

## Telegram 個股查詢 bot

本機支援互動查詢：

```bash
set -a
. ./.env
set +a
python3 -m scanner.cli telegram-bot
```

在 Telegram 群組輸入：

```text
/stock 2353
/stock 宏碁
```

bot 會回覆個股掃描重點、TradingView 連結、Yahoo 股市連結、Fib 0/1 價位與進出場參考。

注意：GitHub Actions 不適合常駐互動 bot，因為每次 job 跑完就會停止。如果要讓 `/stock` 查詢 24 小時在線，需要另外放到 Render、Railway、Fly.io、VPS 或其他常駐服務。

## 本機使用

安裝套件：

```bash
python3 -m pip install -r requirements.txt
```

更新資料並掃描：

```bash
python3 -m scanner.cli update --backfill --days 300
```

輸出 HTML：

```bash
python3 -m scanner.cli report
```

啟動 Streamlit Dashboard：

```bash
streamlit run app.py
```

## 專案結構

```text
app.py                         Streamlit Dashboard
.github/workflows/daily-report.yml
                               GitHub Actions 每日更新、部署與 Telegram 推送
scanner/cli.py                 CLI 入口
scanner/sources.py             TWSE / TPEx / yfinance / CSV 資料來源
scanner/strategy.py            掃描策略與進出場價計算
scanner/report.py              靜態 HTML 報告
scanner/telegram_notify.py     Telegram daily report 推送
scanner/telegram_bot.py        Telegram 個股查詢 bot
scanner/db.py                  DuckDB schema 與 upsert
scanner/indicators.py          MA / RSI 指標
data/market.duckdb             本機資料庫，不會推上 GitHub
reports/getmoneytommrrow.html            本機輸出報告，不會推上 GitHub
```

## 部署文件

GitHub 自動化設定細節：

```text
docs/github_deploy.md
```
