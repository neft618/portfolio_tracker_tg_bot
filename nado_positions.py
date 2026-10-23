import requests
from decimal import Decimal

url = "https://api.prod.nado.xyz/archive/v1"
wallet_address = input("Введите адрес кошелька: ").strip()

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
        print(f"Субаккаунт: {subaccount['subaccount_name']}", f"Идентификатор: {subaccount['subaccount']}")
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

    return result["positions"]








# Блок выполнения 
chosen_subaccount, chosen_subaccount_id = chose_subaccount(wallet_address)

if chosen_subaccount_id is None:
    print("Субаккаунт не выбран. Запрос позиций не выполнен.")
else:
    positions = open_positions(chosen_subaccount_id)

    if not positions:
        print("Открытых позиций нет.")
    else:
        for position in positions:
            print(position)