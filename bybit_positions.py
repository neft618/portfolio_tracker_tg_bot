import os
from decimal import Decimal
from pathlib import Path

import requests
from dotenv import load_dotenv
from pybit.exceptions import FailedRequestError, InvalidRequestError
from pybit.unified_trading import HTTP


BYBIT_ERRORS = (
    requests.RequestException,
    FailedRequestError,
    InvalidRequestError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    ArithmeticError,
)


def bybit_enabled():
    # Даже один заполненный ключ считаем попыткой подключения:
    # при отсутствии второго покажем ошибку.
    return bool(
        os.getenv("BYBIT_API_KEY", "").strip()
        or os.getenv("BYBIT_API_SECRET", "").strip()
    )


def get_session():
    api_key = os.getenv("BYBIT_API_KEY", "").strip()
    api_secret = os.getenv("BYBIT_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise ValueError("Нужны BYBIT_API_KEY и BYBIT_API_SECRET.")

    return HTTP(
        testnet=False,
        api_key=api_key,
        api_secret=api_secret,
        timeout=15,
        max_retries=2,
    )


def get_open_positions(session):
    positions = []
    cursor = None
    seen_cursors = set()

    while True:
        params = {
            "category": "linear",
            "settleCoin": "USDT",
            "limit": 200,
        }

        if cursor:
            params["cursor"] = cursor

        result = session.get_positions(**params)["result"]

        for position in result["list"]:
            if Decimal(position["size"]) > 0:
                positions.append(position)

        cursor = result.get("nextPageCursor")

        if not cursor:
            return positions

        if cursor in seen_cursors:
            raise ValueError("Bybit повторил страницу позиций.")

        seen_cursors.add(cursor)


def format_number(value, signed=False):
    if value is None or value == "":
        return "нет данных"

    number = Decimal(str(value))

    if not number.is_finite():
        raise ValueError("Некорректное число в ответе Bybit.")

    if signed:
        return f"{number:+.4f}"

    return f"{number:f}"


def format_stop(value):
    if value is None or value == "":
        return "не указан в ответе"

    if Decimal(str(value)) == 0:
        return "не указан в ответе"

    return format_number(value)


def build_bybit_summary():
    session = get_session()

    accounts = session.get_wallet_balance(
        accountType="UNIFIED",
    )["result"]["list"]

    if not accounts:
        raise ValueError("Bybit не вернул Unified-аккаунт.")

    account = accounts[0]
    positions = get_open_positions(session)

    lines = [
        "Bybit — Unified",
        f"Equity аккаунта: {format_number(account.get('totalEquity'))} USD",
        "Открытые контракты с расчётами в USDT:",
    ]

    if not positions:
        lines.append("Открытых USDT-позиций нет.")
        return "\n".join(lines)

    tickers = {}

    try:
        result = session.get_tickers(category="linear")

        tickers = {
            item["symbol"]: item
            for item in result["result"]["list"]
        }
    except BYBIT_ERRORS:
        lines.append("Рыночные цены и изменение за 24ч недоступны.")

    for position in positions:
        symbol = position["symbol"]

        if position["side"] == "Buy":
            direction = "LONG"
        elif position["side"] == "Sell":
            direction = "SHORT"
        else:
            raise ValueError("Неизвестное направление позиции.")

        lines.extend([
            "",
            f"{symbol} | {direction}",
            f"Размер: {format_number(position['size'])}",
            f"Вход: {format_number(position.get('avgPrice'))} USDT",
            f"Mark price: {format_number(position.get('markPrice'))} USDT",
            "Нереализованный P&L: "
            f"{format_number(position.get('unrealisedPnl'), signed=True)} USDT",
            "Реализованный P&L текущей позиции: "
            f"{format_number(position.get('curRealisedPnl'), signed=True)} USDT",
            f"TP: {format_stop(position.get('takeProfit'))}",
            f"SL: {format_stop(position.get('stopLoss'))}",
        ])

        ticker = tickers.get(symbol)

        if ticker:
            lines.append(
                "Последняя цена: "
                f"{format_number(ticker.get('lastPrice'))} USDT"
            )

            change = ticker.get("price24hPcnt")

            if change not in (None, ""):
                percent = Decimal(change) * 100
                lines.append(f"Изменение за 24ч: {percent:+.2f}%")

    return "\n".join(lines)

if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().with_name(".env"))

    try:
        print(build_bybit_summary())

    except BYBIT_ERRORS as error:
        code = getattr(error, "status_code", None)

        print(f"Ошибка Bybit: {type(error).__name__}")

        if code is not None:
            print(f"Код ошибки: {code}")