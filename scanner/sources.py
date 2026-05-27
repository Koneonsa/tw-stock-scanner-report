from __future__ import annotations

import time
from datetime import datetime
from typing import Iterable

import pandas as pd
import requests
import yfinance as yf


TWSE_DAILY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
OHLCV_COLUMNS = [
    "date",
    "symbol",
    "name",
    "market",
    "industry",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "source",
    "updated_at",
]


def _is_common_stock_code(code: str | None) -> bool:
    if not code:
        return False
    code = str(code).strip()
    return code.isdigit() and len(code) == 4 and not code.startswith("00")


def _to_number(value) -> float | None:
    if value in (None, "", "--", "----"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _ohlcv_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records, columns=OHLCV_COLUMNS)
    if frame.empty:
        return frame
    return frame.dropna(subset=["open", "high", "low", "close"])


def fetch_twse_daily_all() -> pd.DataFrame:
    rows = requests.get(TWSE_DAILY_ALL, timeout=30).json()
    today = pd.Timestamp.today().normalize()
    records = []
    for row in rows:
        code = row.get("Code") or row.get("證券代號")
        if not _is_common_stock_code(code):
            continue
        records.append(
            {
                "date": today,
                "symbol": str(code),
                "name": row.get("Name") or row.get("證券名稱") or "",
                "market": "上市",
                "industry": "未分類",
                "open": _to_number(row.get("OpeningPrice") or row.get("開盤價")),
                "high": _to_number(row.get("HighestPrice") or row.get("最高價")),
                "low": _to_number(row.get("LowestPrice") or row.get("最低價")),
                "close": _to_number(row.get("ClosingPrice") or row.get("收盤價")),
                "volume": _to_number(row.get("TradeVolume") or row.get("成交股數")),
                "value": _to_number(row.get("TradeValue") or row.get("成交金額")),
                "source": "TWSE OpenAPI",
                "updated_at": pd.Timestamp.now(),
            }
        )
    return _ohlcv_frame(records)


def fetch_tpex_daily_all() -> pd.DataFrame:
    rows = requests.get(TPEX_DAILY_ALL, timeout=30).json()
    today = pd.Timestamp.today().normalize()
    records = []
    for row in rows:
        code = row.get("SecuritiesCompanyCode") or row.get("Code") or row.get("代號")
        if not _is_common_stock_code(code):
            continue
        records.append(
            {
                "date": today,
                "symbol": str(code),
                "name": row.get("CompanyName") or row.get("Name") or row.get("名稱") or "",
                "market": "上櫃",
                "industry": "未分類",
                "open": _to_number(row.get("Open") or row.get("開盤")),
                "high": _to_number(row.get("High") or row.get("最高")),
                "low": _to_number(row.get("Low") or row.get("最低")),
                "close": _to_number(row.get("Close") or row.get("收盤")),
                "volume": _to_number(row.get("TradingShares") or row.get("成交股數")),
                "value": _to_number(row.get("TransactionAmount") or row.get("成交金額")),
                "source": "TPEx OpenAPI",
                "updated_at": pd.Timestamp.now(),
            }
        )
    return _ohlcv_frame(records)


def fetch_latest_openapi() -> pd.DataFrame:
    frames = []
    for fetcher in (fetch_twse_daily_all, fetch_tpex_daily_all):
        try:
            frames.append(fetcher())
        except Exception as exc:
            print(f"OpenAPI fetch failed: {exc}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def symbols_from_latest(latest: pd.DataFrame) -> pd.DataFrame:
    if latest.empty:
        return pd.DataFrame(columns=["symbol", "name", "market", "industry", "source", "updated_at"])
    return latest[["symbol", "name", "market", "industry", "source", "updated_at"]].drop_duplicates(
        ["symbol", "market"]
    )


def _yf_ticker(symbol: str, market: str) -> str:
    suffix = ".TW" if market == "上市" else ".TWO"
    return f"{symbol}{suffix}"


def fetch_yfinance_history(symbols: pd.DataFrame, days: int = 300, batch_size: int = 80) -> pd.DataFrame:
    records = []
    period = f"{max(days + 80, 380)}d"
    symbol_rows = symbols.to_dict("records")
    for start in range(0, len(symbol_rows), batch_size):
        batch = symbol_rows[start : start + batch_size]
        tickers = [_yf_ticker(row["symbol"], row["market"]) for row in batch]
        market_by_ticker = {_yf_ticker(row["symbol"], row["market"]): row for row in batch}
        data = yf.download(
            tickers,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
        )
        for ticker in tickers:
            row = market_by_ticker[ticker]
            try:
                hist = data[ticker].copy() if len(tickers) > 1 else data.copy()
            except Exception:
                continue
            if hist.empty:
                continue
            hist = hist.reset_index().tail(days)
            for _, item in hist.iterrows():
                records.append(
                    {
                        "date": pd.to_datetime(item["Date"]).normalize(),
                        "symbol": row["symbol"],
                        "name": row.get("name") or "",
                        "market": row["market"],
                        "industry": row.get("industry") or "未分類",
                        "open": _to_number(item.get("Open")),
                        "high": _to_number(item.get("High")),
                        "low": _to_number(item.get("Low")),
                        "close": _to_number(item.get("Close")),
                        "volume": _to_number(item.get("Volume")),
                        "value": None,
                        "source": "yfinance backfill",
                        "updated_at": pd.Timestamp.now(),
                    }
                )
        time.sleep(0.4)
    return _ohlcv_frame(records)


def read_ohlcv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"date", "symbol", "name", "market", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    if "industry" not in df.columns:
        df["industry"] = "未分類"
    if "value" not in df.columns:
        df["value"] = None
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["source"] = df.get("source", "csv")
    df["updated_at"] = pd.Timestamp.now()
    return df[
        [
            "date",
            "symbol",
            "name",
            "market",
            "industry",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "value",
            "source",
            "updated_at",
        ]
    ]
