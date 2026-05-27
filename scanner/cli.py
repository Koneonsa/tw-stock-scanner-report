from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import DEFAULT_HISTORY_DAYS
from .db import connect, load_ohlcv, prune_non_common_stocks, upsert_df
from .sources import fetch_latest_openapi, fetch_yfinance_history, read_ohlcv_csv, symbols_from_latest
from .strategy import scan_market
from .report import export_latest_report
from .telegram_notify import send_report
from .telegram_bot import poll_forever


def update(args: argparse.Namespace) -> None:
    con = connect()
    latest = fetch_latest_openapi()
    if not latest.empty:
        upsert_df(con, "ohlcv", latest)
        symbols = symbols_from_latest(latest)
        upsert_df(con, "symbols", symbols)
        prune_non_common_stocks(con)
        print(f"OpenAPI latest rows: {len(latest):,}")
    else:
        symbols = con.execute("SELECT * FROM symbols").df()
        print("OpenAPI latest rows: 0")

    if args.backfill and not symbols.empty:
        hist = fetch_yfinance_history(symbols, days=args.days)
        upsert_df(con, "ohlcv", hist)
        print(f"Historical backfill rows: {len(hist):,}")

    ohlcv = load_ohlcv(con)
    results = scan_market(ohlcv)
    upsert_df(con, "scan_results", results)
    print(f"Scanned symbols: {results['symbol'].nunique() if not results.empty else 0:,}")
    print(f"Passed symbols: {int(results['passed'].sum()) if not results.empty else 0:,}")


def import_csv(args: argparse.Namespace) -> None:
    con = connect()
    df = read_ohlcv_csv(args.path)
    upsert_df(con, "ohlcv", df)
    symbols = (
        df.sort_values("date")
        .groupby(["symbol", "market"], as_index=False)
        .tail(1)[["symbol", "name", "market", "industry", "source", "updated_at"]]
    )
    upsert_df(con, "symbols", symbols)
    results = scan_market(load_ohlcv(con))
    upsert_df(con, "scan_results", results)
    print(f"Imported OHLCV rows: {len(df):,}")
    print(f"Passed symbols: {int(results['passed'].sum()) if not results.empty else 0:,}")


def scan(args: argparse.Namespace) -> None:
    con = connect()
    results = scan_market(load_ohlcv(con))
    upsert_df(con, "scan_results", results)
    cols = ["symbol", "name", "market", "close", "score", "strategy_tag"]
    print(results.loc[results["passed"], cols].head(args.limit).to_string(index=False))


def report(args: argparse.Namespace) -> None:
    output = export_latest_report(args.output)
    print(f"Report exported: {output}")


def notify_telegram(args: argparse.Namespace) -> None:
    send_report(args.report)


def telegram_bot(args: argparse.Namespace) -> None:
    poll_forever(interval=args.interval, once=args.once)


def main() -> None:
    parser = argparse.ArgumentParser(description="Early Bottoming Pullback Scanner")
    sub = parser.add_subparsers(required=True)

    p_update = sub.add_parser("update", help="Fetch latest OpenAPI data and optionally backfill history.")
    p_update.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS)
    p_update.add_argument("--backfill", action="store_true", help="Backfill 300d history with yfinance.")
    p_update.set_defaults(func=update)

    p_import = sub.add_parser("import-csv", help="Import standardized OHLCV CSV.")
    p_import.add_argument("path")
    p_import.set_defaults(func=import_csv)

    p_scan = sub.add_parser("scan", help="Run scanner from stored OHLCV.")
    p_scan.add_argument("--limit", type=int, default=50)
    p_scan.set_defaults(func=scan)

    p_report = sub.add_parser("report", help="Export latest scan as a static HTML report.")
    p_report.add_argument("--output", type=Path, default=None)
    p_report.set_defaults(func=report)

    p_notify = sub.add_parser("notify-telegram", help="Send latest HTML report via Telegram bot.")
    p_notify.add_argument("--report", type=Path, default=None)
    p_notify.set_defaults(func=notify_telegram)

    p_bot = sub.add_parser("telegram-bot", help="Run Telegram query bot for stock lookups.")
    p_bot.add_argument("--interval", type=float, default=1.0)
    p_bot.add_argument("--once", action="store_true", help="Poll once and exit, useful for testing.")
    p_bot.set_defaults(func=telegram_bot)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
