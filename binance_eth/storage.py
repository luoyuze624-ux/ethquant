import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATA_DIR, DB_PATH
from binance_eth.log import get_logger

log = get_logger(__name__)


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            close_time INTEGER,
            quote_volume REAL,
            trades INTEGER,
            taker_buy_base REAL,
            taker_buy_quote REAL,
            PRIMARY KEY (symbol, interval, open_time)
        )
        """
    )
    conn.commit()
    conn.close()
    log.info("Database initialized at %s", db_path)


def save_klines_to_db(
    klines: list[list[Any]],
    symbol: str,
    interval: str,
    db_path: Path = DB_PATH,
) -> int:
    if not klines:
        return 0
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    rows = [
        (
            symbol,
            interval,
            int(k[0]),
            float(k[1]),
            float(k[2]),
            float(k[3]),
            float(k[4]),
            float(k[5]),
            int(k[6]),
            float(k[7]),
            int(k[8]),
            float(k[9]),
            float(k[10]),
        )
        for k in klines
    ]
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR REPLACE INTO klines
        (symbol, interval, open_time, open, high, low, close, volume,
         close_time, quote_volume, trades, taker_buy_base, taker_buy_quote)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    inserted = cursor.rowcount
    conn.close()
    log.info("Saved %d klines to DB (%s %s)", inserted, symbol, interval)
    return inserted


def save_klines_to_csv(
    klines: list[list[Any]],
    symbol: str,
    interval: str,
    data_dir: Path = DATA_DIR,
) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        klines,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    df = df.drop(columns=["ignore"])
    csv_path = data_dir / f"{symbol}_{interval}.csv"
    df.to_csv(csv_path, index=False)
    log.info("Saved %d klines to CSV: %s", len(df), csv_path)
    return csv_path


def load_klines_from_db(
    symbol: str,
    interval: str,
    limit: int | None = None,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    query = """
        SELECT open_time, open, high, low, close, volume,
               close_time, quote_volume, trades, taker_buy_base, taker_buy_quote
        FROM klines
        WHERE symbol = ? AND interval = ?
        ORDER BY open_time DESC
    """
    if limit:
        query += f" LIMIT {limit}"
    df = pd.read_sql_query(query, conn, params=(symbol, interval))
    conn.close()
    if not df.empty:
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        df = df.sort_values("open_time").reset_index(drop=True)
    return df
