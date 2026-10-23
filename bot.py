import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise ValueError("В .env не найден TELEGRAM_BOT_TOKEN.")

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

chat_id_text = os.getenv("TELEGRAM_CHAT_ID")

if not chat_id_text:
    raise ValueError("В .env не найден TELEGRAM_CHAT_ID.")

MY_CHAT_ID = int(chat_id_text)



def telegram_request(method, payload):
    try:
        response = requests.post(
            f"{BASE_URL}/{method}",
            json=payload,
            timeout=35,
        )
    except requests.RequestException:
        raise RuntimeError("Не удалось соединиться с Telegram.") from None

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Telegram вернул ответ не в формате JSON.") from None

    if not response.ok or not result.get("ok"):
        code = result.get("error_code", response.status_code)
        description = result.get("description", "Неизвестная ошибка")
        raise RuntimeError(f"Ошибка Telegram {code}: {description}")

    return result["result"]



def get_updates(offset):
    payload = {
        "timeout": 25,
        "allowed_updates": ["message"],
    }

    if offset is not None:
        payload["offset"] = offset

    return telegram_request("getUpdates", payload)




def send_summary(chat_id, text):
    if chat_id != MY_CHAT_ID:
        raise ValueError("Отправка разрешена только владельцу бота.")

    # Длинный текст отправляем несколькими сообщениями.
    for start in range(0, len(text), 2000):
        part = text[start:start + 2000]

        telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": part,
            },
        )

def handle_message(message):
    if message["chat"]["type"] != "private":
        return

    chat_id = message["chat"]["id"]

    if chat_id != MY_CHAT_ID:
        return

    text = message.get("text", "").strip()

    if not text:
        return

    command = text.split()[0].split("@")[0]

    if command == "/start":
        send_summary(
            chat_id,
            "Привет! Я бот для отслеживания портфеля Nado.\n"
            f"ID этого чата: {chat_id}\n\n"
            "Отправь /test, чтобы проверить отправку сообщений.\n"
            "Настройку кошелька подключим следующим шагом.",
        )

    elif command == "/test":
        send_summary(
            chat_id,
            "Тест отправки сводки.\n\n"
            "Связь с Telegram работает.\n"
            "Данные Nado пока не подключены к этому сообщению.",
        )

    else:
        send_summary(chat_id, "Доступные команды: /start и /test.")


def main():
    offset = None
    print("Бот запущен. Напиши ему /start в Telegram.")

    while True:
        try:
            updates = get_updates(offset)
        except RuntimeError as error:
            print(error)
            time.sleep(3)
            continue

        for update in updates:
            offset = update["update_id"] + 1

            message = update.get("message")
            if message is None:
                continue

            try:
                handle_message(message)
            except RuntimeError as error:
                print(f"Не удалось отправить ответ: {error}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен.")