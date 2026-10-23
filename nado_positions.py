import requests
from decimal import Decimal

url = "https://api.prod.nado.xyz/archive/v1"
instruments_url = "https://api.prod.nado.xyz/gateway/v1/query"

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
        for position in positions:
            direction = "LONG" if position["direction"] else "SHORT"
            margin = "ISOLATED" if position["isolated"] else "CROSS"
            instrument = instruments_by_id.get(position["product_id"])

            if instrument is None:
                print(f"Инструмент с product_id {position['product_id']} не найден.")
                continue
            if instrument["type"] != "perp":
                continue  # Пропуск инструментов, которые не являются perpetual

            print(
                f"Инструмент: {instrument["symbol"]} | {direction} | {margin}\n"
                f"Размер: {position['amount']:f}\n"
                f"Средняя цена входа: {position['average_entry_price']:.8f}\n"
                f"Реализованный P&L без комиссий: {position['realized_pnl']:+.4f} USDT0\n"
                f"Funding по позиции: {position['net_funding_payment']:+.4f} USDT0\n"
            )