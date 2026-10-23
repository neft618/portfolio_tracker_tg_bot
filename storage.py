import sqlite3
import requests

from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import time, sleep
from zoneinfo import ZoneInfo


DB_PATH = Path(__file__).resolve().parent / "portfolio.db"
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
                return

            sleep(30)

    saved_at = datetime.now(ZoneInfo("Europe/Moscow"))

    print(f"\nСнимок сохранён: {saved_at:%d.%m.%Y %H:%M:%S} МСК")
    print(f"Total equity: {total_equity:.2f} USDT0")

    if previous is None:
        print("Это первый снимок этого субаккаунта.")
        return

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
