import sqlite3
import requests
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import time, sleep
from zoneinfo import ZoneInfo


DB_PATH = Path(__file__).resolve().parent / "portfolio.db"

def init_tracking_tables():
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL
                    CHECK (source IN ('nado', 'solana')),
                address TEXT NOT NULL,
                label TEXT NOT NULL,
                UNIQUE (source, address)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS tracked_subaccounts (
                id INTEGER PRIMARY KEY,
                wallet_id INTEGER NOT NULL,
                subaccount TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                FOREIGN KEY (wallet_id)
                    REFERENCES wallets(id)
                    ON DELETE CASCADE
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY,
                subaccount TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                equity TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        connection.commit()

def save_solana_wallet(address, label):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        with connection:
            connection.execute("""
                INSERT INTO wallets (source, address, label)
                VALUES ('solana', ?, ?)
                ON CONFLICT(source, address)
                DO UPDATE SET label = excluded.label
            """, (address, label))


def get_solana_wallets():
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute("""
            SELECT address, label
            FROM wallets
            WHERE source = 'solana'
            ORDER BY id
        """).fetchall()

    return [dict(row) for row in rows]


def get_setting(key, default=""):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()

    return row[0] if row else default


def set_setting(key, value):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        with connection:
            connection.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
            """, (key, value))

def get_latest_equity_snapshot(subaccount):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        return connection.execute("""
            SELECT created_at, equity
            FROM equity_snapshots
            WHERE subaccount = ?
            ORDER BY id DESC
            LIMIT 1
        """, (subaccount,)).fetchone()
    
def save_nado_wallet(address, label, accounts):
    if not accounts:
        raise ValueError("Выбери хотя бы один субаккаунт.")

    address = address.lower()

    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        with connection:
            connection.execute("BEGIN IMMEDIATE")

            connection.execute("""
                INSERT INTO wallets (source, address, label)
                VALUES ('nado', ?, ?)
                ON CONFLICT(source, address)
                DO UPDATE SET label = excluded.label
            """, (address, label))

            wallet_id = connection.execute("""
                SELECT id FROM wallets
                WHERE source = 'nado' AND address = ?
            """, (address,)).fetchone()[0]

            connection.execute("""
                DELETE FROM tracked_subaccounts
                WHERE wallet_id = ?
            """, (wallet_id,))

            connection.executemany("""
                INSERT INTO tracked_subaccounts (
                    wallet_id, subaccount, name
                )
                VALUES (?, ?, ?)
            """, [
                (
                    wallet_id,
                    account["subaccount"],
                    account["subaccount_name"],
                )
                for account in accounts
            ])


def get_tracked_nado():
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute("""
            SELECT w.label, w.address, s.subaccount, s.name
            FROM wallets AS w
            JOIN tracked_subaccounts AS s ON s.wallet_id = w.id
            WHERE w.source = 'nado'
            ORDER BY w.id, s.id
        """).fetchall()

    return [dict(row) for row in rows]


def save_equity_snapshot(subaccount, equity):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY,
                subaccount TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                equity TEXT NOT NULL
            )
        """)

        connection.execute("BEGIN IMMEDIATE")

        previous = connection.execute("""
            SELECT created_at, equity
            FROM equity_snapshots
            WHERE subaccount = ?
            ORDER BY id DESC
            LIMIT 1
        """, (subaccount,)).fetchone()

        connection.execute("""
            INSERT INTO equity_snapshots (
                subaccount, created_at, equity
            )
            VALUES (?, ?, ?)
        """, (subaccount, int(time()), str(equity)))

        connection.commit()

    return previous

# Функция, которую планировщик вызывает ежедневно.
def take_equity_snapshot(subaccount, get_equity):
    # При временной ошибке делаем до трёх попыток.
    for attempt in range(1, 4):
        try:
            total_equity = get_equity(subaccount)
            previous = save_equity_snapshot(subaccount, total_equity)
            break

        except (
            requests.RequestException,
            sqlite3.Error,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ) as error:
            print(f"\nОшибка снимка, попытка {attempt}/3: {error}")

            if attempt == 3:
                print("Снимок не удалось сохранить. Проверь ошибку.")
                return False

            sleep(30)

    saved_at = datetime.now(ZoneInfo("Europe/Moscow"))

    print(f"\nСнимок сохранён: {saved_at:%d.%m.%Y %H:%M:%S} МСК")
    print(f"Total equity: {total_equity:.2f} USDT0")

    if previous is None:
        print("Это первый снимок этого субаккаунта.")
        return True

    previous_timestamp, previous_equity_text = previous
    previous_equity = Decimal(previous_equity_text)

    previous_date = datetime.fromtimestamp(
        previous_timestamp,
        tz=ZoneInfo("Europe/Moscow"),
    )

    change = total_equity - previous_equity

    print(
        f"Предыдущий снимок: "
        f"{previous_date:%d.%m.%Y %H:%M:%S} МСК"
    )
    print(f"Изменение equity: {change:+.2f} USDT0")

    if previous_equity > 0:
        change_percent = change / previous_equity * Decimal("100")
        print(f"Изменение в процентах: {change_percent:+.2f}%")
    return True
