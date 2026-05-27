# Early Bottoming Pullback Scanner

台股上市 + 上櫃全市場 OHLCV 掃描 Dashboard，用來尋找「仍在 MA250 年線以下、但已出現初動放量，並且初動後第一次健康回測」的股票。

## 功能

- TWSE OpenAPI / TPEx OpenAPI 擷取最新日成交資料。
- DuckDB 儲存歷史 OHLCV、股票清單與掃描結果。
- yfinance 可作為第一版歷史 300 日回補來源。
- CSV 匯入可接自有資料源。
- Streamlit Dashboard 支援市場、產業、距離年線、初動漲幅、回檔幅度、分數排序。
- Dashboard 可手動觸發線上更新，並可設定頁面自動刷新。

## 資料模式

這版採用「線上抓資料，本機快取計算」：

- TWSE/TPEx OpenAPI 用來抓最新日資料。
- 300 日歷史 K 線回補後存進 `data/market.duckdb`。
- Dashboard 讀 DuckDB 的掃描結果，所以篩選與排序會很快。
- 不建議每次開頁面都即時線上重算全市場，因為約 1,900 檔 * 300 日資料量大，容易變慢或遇到資料源限流。

若要從 UI 觸發更新，打開左側「線上更新」，可選擇只更新最新日，或包含 300 日歷史回補。

## 安裝

```bash
python3 -m pip install -r requirements.txt
```

## 每日更新

抓 TWSE/TPEx 最新日資料，並用 yfinance 回補最近 300 個交易日：

```bash
python3 -m scanner.cli update --backfill --days 300
```

或使用已提供的更新腳本：

```bash
scripts/update_daily.sh
```

macOS/Linux 可用 crontab 設定交易日收盤後更新，例如每週一至五 20:30：

```cron
30 20 * * 1-5 /Users/koneonsa/Documents/Codex/2026-05-26/dashboard-ohlcv-300-twse-openapi-tpex/scripts/update_daily.sh
```

若不想讓本機一直開著，可以改用 GitHub Actions 每天台北時間 20:30 自動更新報告、部署到 GitHub Pages，並推送 Telegram 網址。設定步驟見：

```text
docs/github_deploy.md
```

若已有標準化 OHLCV CSV：

```bash
python3 -m scanner.cli import-csv path/to/ohlcv.csv
```

CSV 欄位至少需要：

```text
date,symbol,name,market,open,high,low,close,volume
```

可選欄位：

```text
industry,value,source
```

## 啟動 Dashboard

```bash
streamlit run app.py
```

## 輸出 HTML 報告

```bash
python3 -m scanner.cli report
```

輸出位置：

```text
reports/latest.html
```

## Telegram 通知

建立 `.env`，填入 Telegram bot 設定：

```bash
cp .env.example .env
```

```text
TELEGRAM_BOT_TOKEN=你的 bot token
TELEGRAM_CHAT_ID=你的 chat id
TELEGRAM_REPORT_URL=
```

每日更新腳本會在輸出 `reports/latest.html` 後，把 HTML 報告檔案送到 Telegram。若有公開網址，可填 `TELEGRAM_REPORT_URL`，訊息會一併附上連結。

手動發送：

```bash
set -a
. ./.env
set +a
python3 -m scanner.cli notify-telegram
```

群組內查詢個股：

```bash
set -a
. ./.env
set +a
python3 -m scanner.cli telegram-bot
```

啟動後可在 Telegram 群組輸入 `2353`、`宏碁`，或 `/stock 2353`，bot 會回覆最新掃描卡片重點。這個查詢 bot 需要程式持續執行；若電腦關機，就需要改放到雲端主機或其他常駐環境。

Telegram 需要手動做一次設定：

1. 在 Telegram 搜尋 `@BotFather`，建立 bot 後取得 `TELEGRAM_BOT_TOKEN`。
2. 對你的 bot 傳一則訊息，或把 bot 加進群組。
3. 用 `https://api.telegram.org/bot<你的TOKEN>/getUpdates` 查出 `chat.id`，填到 `TELEGRAM_CHAT_ID`。
4. 若 HTML 報告之後有放到公開網址，把網址填入 `TELEGRAM_REPORT_URL`；沒有也沒關係，系統會直接把 HTML 檔案傳到 Telegram。

## 策略條件

核心條件：

- 低位階：股價與 MA60 仍在 MA250 附近或以下
- 90 日區間振幅 `< 60%`
- 最近 60 日低點沒有持續破底
- MA60 斜率開始走平或上彎
- 最近 30 個交易日內，自區間低點上漲超過 15%
- close 不跌破 recent low 30d
- close 仍高於最近 60 日低點 10% 以上
- 止跌訊號至少 2 個
- 30 日均量至少 1000 張

第二類型：高檔回落觀察型

- 30 日均量至少 1000 張
- 今日收盤價相對最近 30 日最高價回落 10% 以上
- Fib 位置介於 0.5 到 0.786
- 用來觀察前一段已有波段上漲、但現在進入回落整理的股票

第三類型：突破回踩型

- 30 日均量至少 1000 張
- 先突破當時往前 300 日高點
- 突破後高點至少高於該基準價 5%
- 今日收盤價回踩到該突破基準價正負 5% 內
- 用來觀察「突破長期壓力後回測不破」的股票

第四類型：成交量暴增型

- 30 日均量至少 1000 張
- 今日成交量大於 30 日均量的 1.6 倍
- 用來觀察「量能突然放大」的股票，可和其他類型同時出現在同一張卡片

斐波那契位置：

- 以最近 180 日內「先出現的波段低點」作為 `0`
- 以該低點之後出現的波段高點作為 `1`
- `fib_position = (close - fib_wave_low) / (fib_wave_high - fib_wave_low)`
- 報告會顯示目前股價所在區間，例如 `0.618-0.786 強勢修復`、`0.950-1.050 突破附近`

若同一檔股票同時符合多種類型，會在同一張卡片上顯示多個標籤。

報告互動：

- HTML 報告上方可搜尋股票代號或名稱，按 Enter 會直接跳到個股卡片。
- 每張卡片會依主要策略顯示參考進場價、目標價、停損價與風報比。

分數：

```text
score =
  impulse_return_score * 0.30
+ base_tightness_score * 0.20
+ low_stage_score * 0.20
+ bottoming_signal_score * 0.20
+ volume_signal_score * 0.10
```

## 專案結構

```text
app.py                 Streamlit Dashboard
scanner/cli.py         更新、匯入、掃描 CLI
scanner/sources.py     TWSE/TPEx/yfinance/CSV 資料來源
scanner/strategy.py    Early Bottoming Pullback 策略
scanner/db.py          DuckDB schema 與 upsert
scanner/indicators.py  MA / RSI 指標
data/market.duckdb     本機資料庫
```
