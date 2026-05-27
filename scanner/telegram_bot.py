from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
from requests import RequestException

from .config import DATA_DIR
from .db import connect


OFFSET_PATH = DATA_DIR / "telegram_bot_offset.txt"


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _read_offset() -> int | None:
    try:
        return int(OFFSET_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_offset(offset: int) -> None:
    OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(str(offset), encoding="utf-8")


def _clean_query(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^/stock(@\w+)?\s*", "", value, flags=re.I)
    value = re.sub(r"^/查詢(@\w+)?\s*", "", value, flags=re.I)
    value = value.replace(".TW", "").replace(".TWO", "")
    return value.strip()


def _trade_plan(row: pd.Series) -> str:
    try:
        return json.loads(row.get("details") or "{}").get("trade_plan") or "-"
    except Exception:
        return "-"


def _fmt_pct(value: float) -> str:
    return "-" if pd.isna(value) else f"{value:+.1f}%"


def _fmt_price(value: float) -> str:
    return "-" if pd.isna(value) else f"{value:.2f}"


def _ticker_suffix(market: str) -> str:
    return ".TW" if market == "上市" else ".TWO"


def _tv_exchange(market: str) -> str:
    return "TWSE" if market == "上市" else "TPEX"


def _tradingview_url(symbol: str, market: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={_tv_exchange(market)}%3A{symbol}"


def _yahoo_url(symbol: str, market: str) -> str:
    return f"https://tw.stock.yahoo.com/quote/{symbol}{_ticker_suffix(market)}"


def _lookup_stock(query: str) -> pd.DataFrame:
    con = connect()
    like = f"%{query}%"
    return con.execute(
        """
        SELECT *
        FROM scan_results
        WHERE as_of = (SELECT MAX(as_of) FROM scan_results)
          AND (symbol = ? OR name LIKE ?)
        ORDER BY
          CASE WHEN symbol = ? THEN 0 ELSE 1 END,
          score DESC NULLS LAST
        LIMIT 5
        """,
        [query, like, query],
    ).df()


def _format_stock(row: pd.Series) -> str:
    lines = [
        f"{row['symbol']} {row['name']}（{row['market']}）",
        f"資料日：{row['as_of']}",
        f"分類：{row['strategy_tag']}",
        f"分數：{row['score']:.0f}",
        f"收盤：{_fmt_price(row['close'])}（{_fmt_pct(row.get('day_change_pct'))}）",
        f"Fib：{row.get('fib_position', float('nan')):.3f} / {row.get('fib_zone') or '-'}",
        f"Fib 0：{_fmt_price(row.get('fib_low_price'))}",
        f"Fib 1：{_fmt_price(row.get('fib_high_price'))}",
        f"突破基準：{_fmt_price(row.get('breakout_base_price'))}（距離 {_fmt_pct(row.get('distance_to_breakout_base_pct'))}）",
        f"30日均量：{row.get('avg_volume_30_lots', 0):,.0f} 張",
        f"今日量能：{row.get('today_volume_lots', 0):,.0f} 張 / {row.get('volume_surge_multiple', 0):.2f}x",
        f"建議價位：進場 {_fmt_price(row.get('entry_price'))} / 目標 {_fmt_price(row.get('target_price'))} / 停損 {_fmt_price(row.get('stop_loss_price'))}",
        f"風報比：{_fmt_price(row.get('risk_reward_ratio'))}",
        f"TradingView：{_tradingview_url(str(row['symbol']), str(row['market']))}",
        f"Yahoo股市：{_yahoo_url(str(row['symbol']), str(row['market']))}",
        "",
        _trade_plan(row),
    ]
    return "\n".join(lines)


def answer_query(text: str) -> str | None:
    query = _clean_query(text)
    if query.lower().startswith("/ping"):
        return "查詢 bot 在線。請輸入 /stock 2353 或 /stock 宏碁。"
    if not query:
        return "請輸入股票代號或名稱，例如：2353、宏碁，或 /stock 2353"
    if query.startswith("/"):
        return None
    if len(query) < 2 and not query.isdigit():
        return None

    matches = _lookup_stock(query)
    if matches.empty:
        return f"找不到「{query}」的最新掃描資料。可以試試股票代號或完整名稱。"
    if len(matches) == 1:
        return _format_stock(matches.iloc[0])

    summary = [f"找到 {len(matches)} 筆，請輸入更完整的代號或名稱："]
    for row in matches.itertuples():
        summary.append(f"{row.symbol} {row.name}｜{row.strategy_tag}｜分數 {row.score:.0f}")
    return "\n".join(summary)


def _send_message(token: str, chat_id: int | str, text: str) -> None:
    response = requests.post(
        _api_url(token, "sendMessage"),
        data={
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()


def _log(message: str) -> None:
    print(message, flush=True)


def poll_forever(interval: float = 1.0, once: bool = False) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed_chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")

    offset = _read_offset()
    _log("Telegram query bot is running. Press Ctrl+C to stop.")
    while True:
        try:
            response = requests.get(
                _api_url(token, "getUpdates"),
                params={"timeout": 25, "offset": offset},
                timeout=35,
            )
            response.raise_for_status()
            updates = response.json().get("result", [])
            for update in updates:
                offset = int(update["update_id"]) + 1
                _write_offset(offset)
                message = update.get("message") or update.get("channel_post") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = message.get("text") or ""
                _log(f"update={update.get('update_id')} chat={chat_id} text={text!r}")
                if allowed_chat and str(chat_id) != str(allowed_chat):
                    _log(f"ignored chat={chat_id}; allowed_chat={allowed_chat}")
                    continue
                answer = answer_query(text)
                if answer:
                    _send_message(token, chat_id, answer)
                    _log(f"replied chat={chat_id} query={text!r}")
            if once:
                return
        except RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            body = getattr(exc.response, "text", "")
            _log(f"Telegram polling error: {exc.__class__.__name__} status={status} body={body[:500]}")
            time.sleep(5)
        time.sleep(interval)
