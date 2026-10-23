import requests
import schedule
import time

from decimal import Decimal
from storage import take_equity_snapshot

url = "https://api.prod.nado.xyz/archive/v1"
instruments_url = "https://api.prod.nado.xyz/gateway/v1/query"
tickers_url = "https://api.prod.nado.xyz/archive/v2/tickers"

wallet_address = input("Введите адрес кошелька: ").strip()
X18_FIELDS = [
    "amount",
    "max_amount",
    "total_open_amount",
    "total_close_amount",
    "average_entry_price",
    "average_exit_price",
    "liquidated_amount",
    "open_fee",
    "close_fee",
    "realized_pnl",
    "net_funding_payment",
    "net_interest_payment",
    "net_entry_unrealized",
]

# Получение субаккаунтов для указанного адреса кошелька
def get_subaccounts(wallet_address):
    payload = {
        "subaccounts": {
            "address": wallet_address
        }
    }
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    result = response.json()
    if "subaccounts" not in result:
        raise ValueError("Неожиданный ответ API: нет поля subaccounts.")
    subaccounts = result["subaccounts"]

    return subaccounts

# Выбор субаккаунта для получения открытых позиций
def chose_subaccount(wallet_address):
    chosen_subaccount_id = None
    subaccounts = get_subaccounts(wallet_address)
    if not subaccounts:
        print("Для этого кошелька субаккаунты не найдены.")
        return None, None
    
    for subaccount in subaccounts:
        print(f"Субаккаунт: {subaccount['subaccount_name']}")
    chosen_subaccount = input("Введите имя субаккаунта для получения открытых позиций: ").strip()
    for subaccount in subaccounts:
        if subaccount['subaccount_name'] == chosen_subaccount:
            chosen_subaccount_id = subaccount['subaccount']
            break
    return chosen_subaccount, chosen_subaccount_id

# Получение открытых позиций для выбранного субаккаунта
def open_positions(chosen_subaccount_id):
    positions_payload = {
    "positions": {
        "subaccount": chosen_subaccount_id,
        "open": True,
        "limit": 100
        }
    }
    response = requests.post(url, json=positions_payload, timeout=15)
    response.raise_for_status()
    result = response.json()
    if "positions" not in result:
        raise ValueError("Неожиданный ответ API: нет поля positions.")
    for position in result["positions"]:
        for field in X18_FIELDS:
            position[field] = from_x18(position[field])
    return result["positions"]

# Преобразование чисел из формата с 18 десятичными знаками в обычный формат
def from_x18(value): 
    return Decimal(value) / Decimal(10**18)

# Получение справочника инструментов с Nado
def get_instruments():
    params = {
        "type": "symbols"
    }
    response = requests.get(instruments_url, params=params, timeout=15)
    response.raise_for_status()
    result = response.json()
    # Проверка успешности запроса и наличия данных
    if result.get("status") != "success":
        raise ValueError(f"Nado вернул ошибку: {result.get('error')}")

    if "data" not in result or "symbols" not in result["data"]:
        raise ValueError("Неожиданный ответ API: нет справочника symbols.")
    # Получение справочника инструментов
    symbols = result["data"]["symbols"]
    instruments_by_id = {}
    # Создание словаря инструментов по product_id
    for instrument in symbols.values():
        product_id = instrument["product_id"]
        instruments_by_id[product_id] = instrument

    return instruments_by_id

def get_tickers():
    params = {"market": "perp"}

    response = requests.get(tickers_url, params=params, timeout=15)
    response.raise_for_status()
    result = response.json(parse_float=Decimal)

    tickers_by_id = {}

    for ticker in result.values():
        product_id = ticker["product_id"]
        tickers_by_id[product_id] = ticker

    return tickers_by_id

def get_mark_prices(product_ids):
    if not product_ids:
        return {}

    payload = {
        "perp_prices": {
            "product_ids": product_ids
        }
    }

    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    result = response.json()

    prices_by_id = {}

    for price_data in result.values():
        product_id = price_data["product_id"]
        price_data["mark_price"] = from_x18(price_data["mark_price_x18"])
        prices_by_id[product_id] = price_data

    return prices_by_id

def calculate_unrealized_pnl(position, mark_price):
    signed_amount = position["amount"]

    if not position["direction"]:
        signed_amount = -signed_amount

    return signed_amount * mark_price - position["net_entry_unrealized"]

def get_total_equity(subaccount):
    gateway_url = "https://api.prod.nado.xyz/gateway/v1/query"
    total_equity = Decimal("0")

    for query_type in ("subaccount_info", "isolated_positions"):
        payload = {
            "type": query_type,
            "subaccount": subaccount
        }

        response = requests.post(
            gateway_url, json=payload, timeout=15
        )
        response.raise_for_status()
        result = response.json()

        if result.get("status") != "success":
            raise ValueError(
                f"Ошибка получения equity: {result.get('error')}"
            )

        data = result["data"]

        if query_type == "subaccount_info":
            if not data["exists"]:
                raise ValueError("Субаккаунт не существует.")

            total_equity += from_x18(data["healths"][2]["health"])

        else:
            for isolated_position in data["isolated_positions"]:
                total_equity += from_x18(
                    isolated_position["healths"][2]["health"]
                )

    return total_equity

















# Блок выполнения
chosen_subaccount, chosen_subaccount_id = chose_subaccount(wallet_address)

if chosen_subaccount_id is None:
    print("Субаккаунт не выбран. Запрос позиций не выполнен.")
else:
    positions = open_positions(chosen_subaccount_id)

    if not positions:
        print("Открытых позиций нет.")
    else:
        instruments_by_id = get_instruments()

        # Отбираем фьючерсные позиции и собираем ID для запроса цен.
        perp_positions = []
        product_ids = []

        for position in positions:
            product_id = position["product_id"]
            instrument = instruments_by_id.get(product_id)

            if instrument is None:
                print(
                    f"Инструмент с ID {product_id} не найден. "
                    "Позиция пропущена."
                )
                continue

            if instrument["type"] != "perp":
                continue

            perp_positions.append(position)

            if product_id not in product_ids:
                product_ids.append(product_id)

        if not perp_positions:
            print("Фьючерсных позиций среди распознанных инструментов нет.")
        else:
            # Получаем данные один раз для всех позиций.
            tickers_by_id = get_tickers()
            prices_by_id = get_mark_prices(product_ids)

            print(f"\nСубаккаунт: {chosen_subaccount}")
            print(f"Открытых фьючерсных позиций: {len(perp_positions)}")

            for position in perp_positions:
                product_id = position["product_id"]
                instrument = instruments_by_id[product_id]

                direction = "LONG" if position["direction"] else "SHORT"
                margin = "ISOLATED" if position["isolated"] else "CROSS"

                print()
                print("-" * 50)
                print(
                    f"Инструмент: {instrument['symbol']} | "
                    f"{direction} | {margin}"
                )
                print(f"Размер: {position['amount']:f}")
                print(
                    f"Средняя цена входа: "
                    f"{position['average_entry_price']:.8f} USDT0"
                )

                # Последняя цена и изменение за 24 часа.
                ticker = tickers_by_id.get(product_id)

                if ticker is None:
                    print("Последняя цена и изменение за 24ч: нет данных")
                else:
                    last_price = ticker["last_price"]
                    change_24h = ticker["price_change_percent_24h"]

                    print(f"Последняя цена: {last_price:.8f} USDT0")
                    print(f"Изменение за 24ч: {change_24h:+.2f}%")

                # Mark price и оценка нереализованного P&L.
                price_data = prices_by_id.get(product_id)

                if price_data is None:
                    print("Mark price и нереализованный P&L: нет данных")
                else:
                    mark_price = price_data["mark_price"]
                    unrealized_pnl = calculate_unrealized_pnl(
                        position, mark_price
                    )

                    print(f"Mark price: {mark_price:.8f} USDT0")
                    print(
                        f"Нереализованный P&L по mark price: "
                        f"{unrealized_pnl:+.4f} USDT0"
                    )

                print(
                    f"Реализованный P&L по позиции без комиссий: "
                    f"{position['realized_pnl']:+.4f} USDT0"
                )
                print(
                    f"Funding по позиции: "
                    f"{position['net_funding_payment']:+.4f} USDT0"
                )
if chosen_subaccount_id is not None:
    schedule.every().day.at(
         "03:00", "Europe/Moscow"
    ).do(
        take_equity_snapshot,
        chosen_subaccount_id,
        get_total_equity,
    )

    print("\nСнимки equity запланированы ежедневно на 03:00 МСК.")
    print("Для остановки нажми Ctrl+C.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nПрограмма остановлена.")