import requests

url = "https://api.prod.nado.xyz/archive/v1"
wallet_address = input("Введите адрес кошелька: ").strip()

payload = {
    "subaccounts": {
        "address": wallet_address
    }
}

print("Получение данных о позициях для кошелька:", wallet_address)

response = requests.post(url, json=payload, timeout=15)
result = response.json()
print("Ответ от API:", result, "Статус:", response.raise_for_status())