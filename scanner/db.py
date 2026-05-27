from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .config import DB_PATH


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT,
            name TEXT,
            market TEXT,
            industry TEXT,
            source TEXT,
            updated_at TIMESTAMP,
            PRIMARY KEY (symbol, market)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            date DATE,
            symbol TEXT,
            name TEXT,
            market TEXT,
            industry TEXT,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            value DOUBLE,
            source TEXT,
            updated_at TIMESTAMP,
            PRIMARY KEY (date, symbol, market)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_results (
            as_of DATE,
            symbol TEXT,
            name TEXT,
            market TEXT,
            industry TEXT,
            close DOUBLE,
            day_change_pct DOUBLE,
            distance_to_ma250_pct DOUBLE,
            impulse_return_pct DOUBLE,
            pullback_pct DOUBLE,
            range_90d_pct DOUBLE,
            ma60_slope_pct DOUBLE,
            impulse_volume_multiple DOUBLE,
            pullback_volume_ratio DOUBLE,
            stop_signal_count INTEGER,
            avg_volume_20_lots DOUBLE,
            avg_volume_30_lots DOUBLE,
            today_volume_lots DOUBLE,
            volume_surge_multiple DOUBLE,
            bottom_rise_pct DOUBLE,
            fib_position DOUBLE,
            fib_zone TEXT,
            fib_low_price DOUBLE,
            fib_high_price DOUBLE,
            breakout_base_price DOUBLE,
            breakout_lookback_days INTEGER,
            distance_to_breakout_base_pct DOUBLE,
            entry_price DOUBLE,
            target_price DOUBLE,
            stop_loss_price DOUBLE,
            risk_reward_ratio DOUBLE,
            score DOUBLE,
            strategy_tag TEXT,
            signal_type TEXT,
            signal_types TEXT,
            passed BOOLEAN,
            details TEXT,
            updated_at TIMESTAMP,
            PRIMARY KEY (as_of, symbol, market)
        )
        """
    )
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS avg_volume_20_lots DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS day_change_pct DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS avg_volume_30_lots DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS today_volume_lots DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS volume_surge_multiple DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS bottom_rise_pct DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS fib_position DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS fib_zone TEXT")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS fib_low_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS fib_high_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS breakout_base_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS breakout_lookback_days INTEGER")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS distance_to_breakout_base_pct DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS entry_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS target_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS stop_loss_price DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS risk_reward_ratio DOUBLE")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS signal_type TEXT")
    con.execute("ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS signal_types TEXT")


def upsert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    temp = f"tmp_{table}"
    con.register(temp, df)
    columns = list(df.columns)
    col_sql = ", ".join(columns)
    excluded_sql = ", ".join([f"{c}=EXCLUDED.{c}" for c in columns])
    con.execute(
        f"""
        INSERT INTO {table} ({col_sql})
        SELECT {col_sql} FROM {temp}
        ON CONFLICT DO UPDATE SET {excluded_sql}
        """
    )
    con.unregister(temp)
    return len(df)


def load_ohlcv(con: duckdb.DuckDBPyConnection, min_rows: int = 260) -> pd.DataFrame:
    return con.execute(
        """
        SELECT *
        FROM ohlcv
        WHERE symbol IN (
            SELECT symbol
            FROM ohlcv
            GROUP BY symbol, market
            HAVING COUNT(*) >= ?
        )
        ORDER BY market, symbol, date
        """,
        [min_rows],
    ).df()


def prune_non_common_stocks(con: duckdb.DuckDBPyConnection) -> None:
    predicate = "length(symbol) != 4 OR starts_with(symbol, '00')"
    con.execute(f"DELETE FROM symbols WHERE {predicate}")
    con.execute(f"DELETE FROM ohlcv WHERE {predicate}")
    con.execute(f"DELETE FROM scan_results WHERE {predicate}")


def latest_scan(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """
        SELECT *
        FROM scan_results
        WHERE as_of = (SELECT MAX(as_of) FROM scan_results)
        ORDER BY score DESC NULLS LAST
        """
    ).df()
