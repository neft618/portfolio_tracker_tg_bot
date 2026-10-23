import os
import time
from pathlib import Path
from storage import (
    init_tracking_tables,
    save_nado_wallet,
    get_tracked_nado,
    take_equity_snapshot,
)
import requests
from dotenv import load_dotenv
import re
import sqlite3
import schedule
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from nado_positions import (
    get_subaccounts,
    get_total_equity,
    build_account_summary,
)


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
#Временная память диалога
setup = {}
DATA_ERRORS = (
    requests.RequestException,
    sqlite3.Error,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    ArithmeticError,
)

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
        "allowed_updates": ["message", "callback_query"],
    }

    if offset is not None:
        payload["offset"] = offset

    return telegram_request("getUpdates", payload)




def send_summary(chat_id, text):
    if chat_id != MY_CHAT_ID:
        raise ValueError("Отправка разрешена только владельцу бота.")

    # Длинный текст отправляем несколькими сообщениями.
    for start in range(0, len(text), 2000):
        if start > 0:
            time.sleep(1.1)

        part = text[start:start + 2000]

        telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": part,
            },
        )

def save_all_snapshots():
    try:
        accounts = get_tracked_nado()
        saved = 0
        failed = []

        for account in accounts:
            try:
                ok = take_equity_snapshot(
                    account["subaccount"],
                    get_total_equity,
                )
            except DATA_ERRORS as error:
                print("Ошибка снимка:", type(error).__name__)
                ok = False

            if ok:
                saved += 1
            else:
                failed.append(
                    f"{account['label']} / {account['name']}"
                )

        text = (
            f"Снимки equity: сохранено {saved}/{len(accounts)}."
        )

        if not accounts:
            text += "\nНет выбранных субаккаунтов."

        if failed:
            text += "\nНе сохранены:\n" + "\n".join(failed)

    except DATA_ERRORS as error:
        print("Ошибка задания снимков:", type(error).__name__)
        text = "Не удалось выполнить сохранение снимков equity."

    try:
        send_summary(MY_CHAT_ID, text)
    except RuntimeError:
        print("Не удалось отправить результат сохранения снимков.")

def show_menu():
    setup.clear()

    has_wallets = bool(get_tracked_nado())
    title = (
        "Что добавим или изменим?"
        if has_wallets
        else "Что настроим?"
    )

    telegram_request("sendMessage", {
        "chat_id": MY_CHAT_ID,
        "text": title,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Сводка", "callback_data": "summary"}],
                [{
                    "text": "Solana — скоро",
                    "callback_data": "solana",
                }],
                [{
                    "text": "Мои кошельки",
                    "callback_data": "wallets",
                }],
            ]
        },
    })


def show_wallets():
    rows = get_tracked_nado()

    if not rows:
        send_summary(
            MY_CHAT_ID,
            "Кошельков пока нет. Отправь /start.",
        )
        return

    lines = []
    previous_address = None

    for row in rows:
        if row["address"] != previous_address:
            lines.extend(["", row["label"], row["address"]])
            previous_address = row["address"]

        lines.append(f"  • {row['name']}")

    send_summary(MY_CHAT_ID, "\n".join(lines).strip())

def selection_keyboard():
    rows = []
    page = setup["page"]
    start = page * 8

    for index in range(
        start,
        min(start + 8, len(setup["accounts"])),
    ):
        account = setup["accounts"][index]

        mark = "✅" if index in setup["selected"] else "⬜"

        rows.append([{
            "text": f"{mark} {account['subaccount_name']}",
            "callback_data": f"pick:{index}",
        }])

    navigation = []

    if page > 0:
        navigation.append({
            "text": "←",
            "callback_data": "page:prev",
        })

    if start + 8 < len(setup["accounts"]):
        navigation.append({
            "text": "→",
            "callback_data": "page:next",
        })

    if navigation:
        rows.append(navigation)

    rows.append([{
        "text": f"Сохранить ({len(setup['selected'])})",
        "callback_data": "save",
    }])

    rows.append([{
        "text": "Отмена",
        "callback_data": "cancel",
    }])

    return {"inline_keyboard": rows}

def send_all_summaries():
    accounts = get_tracked_nado()

    if not accounts:
        send_summary(
            MY_CHAT_ID,
            "Сначала добавь кошельки через /start.",
        )
        return

    send_summary(
        MY_CHAT_ID,
        "Собираю сводку по выбранным субаккаунтам…",
    )

    started = datetime.now(ZoneInfo("Europe/Moscow"))
    blocks = []
    total = Decimal("0")
    successful = 0

    for account in accounts:
        try:
            text, equity = build_account_summary(account)

        except DATA_ERRORS as error:
            print("Ошибка сводки:", type(error).__name__)

            blocks.append(
                f"{account['label']} / {account['name']}\n"
                "Не удалось получить данные. Не включён в итог."
            )
            continue

        blocks.append(text)
        total += equity
        successful += 1

    header = [
        f"Nado — запрос от {started:%d.%m.%Y %H:%M} МСК",
        f"Получено субаккаунтов: {successful}/{len(accounts)}",
    ]

    if successful == len(accounts):
        header.append(
            "Общий equity отслеживаемых субаккаунтов: "
            f"{total:.2f} USDT0"
        )
    elif successful:
        header.append(
            f"Неполный итог equity: {total:.2f} USDT0"
        )
    else:
        header.append("Общий equity недоступен.")

    header.append(
        "Изменение equity включает пополнения и выводы."
    )

    text = (
        "\n".join(header)
        + "\n\n"
        + "\n\n──────────\n\n".join(blocks)
    )

    send_summary(MY_CHAT_ID, text)

def handle_message(message):
    chat = message["chat"]

    if chat["type"] != "private" or chat["id"] != MY_CHAT_ID:
        return

    text = message.get("text", "").strip()

    if not text:
        return

    command = text.split()[0].split("@")[0]

    if command in ("/start", "/cancel"):
        show_menu()
        return

    if command == "/wallets":
        show_wallets()
        return

    if command == "/test":
        send_summary(MY_CHAT_ID, "Бот работает.")
        return
    if command == "/summary":
        send_all_summaries()
        return

    if command == "/snapshot":
        save_all_snapshots()
        return
    
    if text.startswith("/"):
        send_summary(
            MY_CHAT_ID,
            "Команды: /start, /wallets, /summary, /snapshot, /cancel, /test.",
        )
        return

    # Шаг 1: ждём адрес.
    if setup.get("step") == "address":
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", text):
            send_summary(
                MY_CHAT_ID,
                "Нужен адрес: 0x и 40 символов 0–9, a–f.",
            )
            return

        address = text.lower()
        received = get_subaccounts(address)

        # Оставляем обычные субаккаунты и убираем повторы.
        accounts_by_id = {}

        for account in received:
            if not account["isolated"]:
                accounts_by_id[account["subaccount"]] = account

        accounts = list(accounts_by_id.values())

        if not accounts:
            send_summary(
                MY_CHAT_ID,
                "Обычные субаккаунты не найдены. "
                "Проверь адрес или /cancel.",
            )
            return

        # Если адрес уже сохранён, восстановим его выбор.
        tracked = get_tracked_nado()

        selected_ids = {
            row["subaccount"]
            for row in tracked
            if row["address"] == address
        }

        setup.update({
            "step": "label",
            "address": address,
            "accounts": accounts,
            "selected": {
                index
                for index, account in enumerate(accounts)
                if account["subaccount"] in selected_ids
            },
            "page": 0,
        })

        send_summary(
            MY_CHAT_ID,
            "Как назовём кошелёк? "
            "Отправь название до 60 символов.",
        )
        return

    # Шаг 2: ждём название.
    if setup.get("step") == "label":
        if len(text) > 60:
            send_summary(
                MY_CHAT_ID,
                "Сократи название до 60 символов.",
            )
            return

        setup["label"] = text

        result = telegram_request("sendMessage", {
            "chat_id": MY_CHAT_ID,
            "text": (
                f"Кошелёк: {text}\n"
                "Выбери субаккаунты. "
                "Повторное нажатие снимает галочку.\n"
                "Сохранение заменит прежний выбор для этого адреса."
            ),
            "reply_markup": selection_keyboard(),
        })

        setup["step"] = "selection"
        setup["message_id"] = result["message_id"]
        return

    send_summary(
        MY_CHAT_ID,
        "Используй кнопки или /start.",
    )

def handle_callback(query):
    message = query.get("message", {})
    chat = message.get("chat", {})

    if (
        query["from"]["id"] != MY_CHAT_ID
        or chat.get("id") != MY_CHAT_ID
        or chat.get("type") != "private"
    ):
        return

    telegram_request("answerCallbackQuery", {
        "callback_query_id": query["id"],
    })

    action = query.get("data", "")

    if action == "summary":
        send_all_summaries()
        return

    if action == "nado":
        setup.clear()
        setup["step"] = "address"

        send_summary(
            MY_CHAT_ID,
            "Пришли адрес Nado-кошелька. Отмена: /cancel.",
        )
        return

    if action == "solana":
        send_summary(
            MY_CHAT_ID,
            "Настройку Solana подключим следующим этапом.",
        )
        return

    if action == "wallets":
        show_wallets()
        return

    # Кнопки выбора действуют только для текущей настройки.
    if (
        setup.get("step") != "selection"
        or setup.get("message_id") != message.get("message_id")
    ):
        send_summary(
            MY_CHAT_ID,
            "Это старые кнопки. "
            "Используй последнее меню или /start.",
        )
        return

    if action == "cancel":
        show_menu()
        return

    if action == "save":
        selected = [
            account
            for index, account in enumerate(setup["accounts"])
            if index in setup["selected"]
        ]

        if not selected:
            send_summary(
                MY_CHAT_ID,
                "Выбери хотя бы один субаккаунт.",
            )
            return

        save_nado_wallet(
            setup["address"],
            setup["label"],
            selected,
        )

        setup.clear()

        send_summary(
            MY_CHAT_ID,
            f"Сохранено субаккаунтов: {len(selected)}.",
        )
        show_menu()
        return

    if action.startswith("pick:"):
        index = int(action.split(":")[1])

        if not 0 <= index < len(setup["accounts"]):
            return

        if index in setup["selected"]:
            setup["selected"].remove(index)
        else:
            setup["selected"].add(index)

    elif action in ("page:prev", "page:next"):
        delta = 1 if action == "page:next" else -1
        last_page = (len(setup["accounts"]) - 1) // 8

        setup["page"] = max(
            0,
            min(last_page, setup["page"] + delta),
        )

    else:
        return

    # Обновляем галочки в существующем сообщении.
    telegram_request("editMessageReplyMarkup", {
        "chat_id": MY_CHAT_ID,
        "message_id": setup["message_id"],
        "reply_markup": selection_keyboard(),
    })

def main():
    init_tracking_tables()

    schedule.every().day.at(
        "03:00", "Europe/Moscow"
    ).do(save_all_snapshots)

    offset = None
    print("Бот запущен. Отправь /start в Telegram.")
    print("Снимки equity запланированы ежедневно на 03:00 МСК.")

    while True:
        schedule.run_pending()

        try:
            updates = get_updates(offset)
        except RuntimeError as error:
            print(error)
            time.sleep(3)
            continue

        for update in updates:
            offset = update["update_id"] + 1

            try:
                if "callback_query" in update:
                    handle_callback(update["callback_query"])

                elif "message" in update:
                    handle_message(update["message"])

            except (
                RuntimeError,
                requests.RequestException,
                sqlite3.Error,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
            ) as error:
                print(
                    "Ошибка обработки события:",
                    type(error).__name__,
                )

                try:
                    send_summary(
                        MY_CHAT_ID,
                        "Не удалось выполнить действие. "
                        "Повтори его или отправь /cancel.",
                    )
                except RuntimeError:
                    print("Не удалось отправить сообщение об ошибке.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен.")