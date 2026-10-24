import os
import time
from pathlib import Path
from storage import (
    init_tracking_tables,
    save_nado_wallet,
    get_tracked_nado,
    take_equity_snapshot,
    save_solana_wallet,
    get_solana_wallets,
    get_setting,
    set_setting,
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
from solana_positions import (
    get_sol_balance,
    build_solana_summary,
)
from bybit_positions import (
    BYBIT_ERRORS,
    bybit_enabled,
    build_bybit_summary,
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

def save_all_snapshots(notify=True):
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

    if notify:
        try:
            send_summary(MY_CHAT_ID, text)
        except RuntimeError:
            print("Не удалось отправить результат сохранения снимков.")
    else:
        print(text)

def show_menu():
    setup.clear()
    daily_time = get_setting("daily_summary_time") or "не задано"

    telegram_request("sendMessage", {
        "chat_id": MY_CHAT_ID,
        "text": (
            "Настройки портфеля. "
            f"Время сводки: {daily_time} (МСК)."
        ),
        "reply_markup": {
            "inline_keyboard": [
                [{
                    "text": "Сводка сейчас",
                    "callback_data": "summary",
                }],
                [{
                    "text": "Добавить / изменить Nado",
                    "callback_data": "nado",
                }],
                [{
                    "text": "Добавить Solana",
                    "callback_data": "solana",
                }],
                [{
                    "text": "Мои кошельки",
                    "callback_data": "wallets",
                }],
                [{
                    "text": "Время ежедневной сводки",
                    "callback_data": "summary_time",
                }],
            ]
        },
    })


def show_wallets():
    lines = []
    previous_address = None

    for row in get_tracked_nado():
        if row["address"] != previous_address:
            lines.extend([
                "",
                f"Nado — {row['label']}",
                row["address"],
            ])
            previous_address = row["address"]

        lines.append(f"  • {row['name']}")

    for wallet in get_solana_wallets():
        lines.extend([
            "",
            f"Solana — {wallet['label']}",
            wallet["address"],
        ])

    send_summary(
        MY_CHAT_ID,
        "\n".join(lines).strip()
        or "Кошельков пока нет. Отправь /start.",
    )

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

def send_all_summaries(announce=True):
    accounts = get_tracked_nado()
    sol_wallets = get_solana_wallets()
    use_bybit = bybit_enabled()

    if not accounts and not sol_wallets and not use_bybit:
        send_summary(
            MY_CHAT_ID,
            "Сначала добавь кошельки через /start "
            "или настрой Bybit в .env.",
        )
        return

    if announce:
        send_summary(
            MY_CHAT_ID,
            "Собираю сводку…",
        )

    started = datetime.now(ZoneInfo("Europe/Moscow"))
    blocks = []
    nado_total = Decimal("0")
    nado_ok = 0
    sol_ok = 0

    for account in accounts:
        try:
            text, equity = build_account_summary(account)
            blocks.append(text)
            nado_total += equity
            nado_ok += 1

        except DATA_ERRORS as error:
            print("Ошибка Nado:", type(error).__name__)
            blocks.append(
                f"Nado — {account['label']} / {account['name']}\n"
                "Данные недоступны; в итог не включён."
            )

    for wallet in sol_wallets:
        try:
            text, complete = build_solana_summary(wallet)
            blocks.append(text)

            if complete:
                sol_ok += 1

        except DATA_ERRORS as error:
            print("Ошибка Solana:", type(error).__name__)
            blocks.append(
                f"Solana — {wallet['label']}\n"
                "Данные недоступны."
            )
    if use_bybit:
        try:
            blocks.append(build_bybit_summary())

        except BYBIT_ERRORS as error:
            code = getattr(error, "status_code", None)

            print(
                "Ошибка Bybit:",
                type(error).__name__,
                "код:",
                code,
            )

            blocks.append(
                "Bybit\nДанные недоступны. "
                "Проверь подключение и настройки API."
            )
            
    header = [
        f"Сводка — запрос от {started:%d.%m.%Y %H:%M} МСК"
    ]

    if accounts:
        header.append(
            f"Nado: получено {nado_ok}/{len(accounts)} субаккаунтов."
        )

        if nado_ok:
            title = (
                "Equity Nado"
                if nado_ok == len(accounts)
                else "Неполный equity Nado"
            )
            header.append(f"{title}: {nado_total:.2f} USDT0")
        else:
            header.append("Equity Nado недоступен.")

    if sol_wallets:
        header.append(
            f"Solana: полные балансы "
            f"{sol_ok}/{len(sol_wallets)} кошельков."
        )
        header.append(
            "SOL и токены показаны отдельно, в единицах активов."
        )

    header.append(
        "Изменение equity учитывает пополнения и выводы."
    )

    send_summary(
        MY_CHAT_ID,
        "\n".join(header)
        + "\n\n"
        + "\n\n──────────\n\n".join(blocks),
    )

def scheduled_summary():
    try:
        send_all_summaries(announce=False)
    except (RuntimeError,) + DATA_ERRORS as error:
        print(
            "Ошибка ежедневной сводки:",
            type(error).__name__,
        )


def apply_summary_schedule():
    schedule.clear("daily-summary")

    daily_time = get_setting("daily_summary_time")

    if daily_time:
        schedule.every().day.at(
            daily_time,
            "Europe/Moscow",
        ).do(scheduled_summary).tag("daily-summary")

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

    if setup.get("step") == "summary_time":
        if text.lower() == "выкл":
            daily_time = ""

        elif re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            daily_time = text

        else:
            send_summary(
                MY_CHAT_ID,
                "Введи время ЧЧ:ММ, например 08:30, или выкл.",
            )
            return

        set_setting("daily_summary_time", daily_time)
        apply_summary_schedule()
        setup.clear()

        answer = (
            f"Ежедневная сводка включена: {daily_time} МСК."
            if daily_time
            else "Ежедневная сводка отключена."
        )

        send_summary(MY_CHAT_ID, answer)
        show_menu()
        return

    if setup.get("step") == "sol_address":
        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text):
            send_summary(
                MY_CHAT_ID,
                "Пришли корректный Solana-адрес или /cancel.",
            )
            return

        # RPC дополнительно проверит формат адреса.
        # Нулевой баланс допустим.
        get_sol_balance(text)

        setup.update({
            "step": "sol_label",
            "address": text,
        })

        send_summary(
            MY_CHAT_ID,
            "Как назовём кошелёк? До 60 символов.",
        )
        return

    if setup.get("step") == "sol_label":
        if len(text) > 60:
            send_summary(
                MY_CHAT_ID,
                "Сократи название до 60 символов.",
            )
            return

        save_solana_wallet(setup["address"], text)
        setup.clear()

        send_summary(MY_CHAT_ID, "Solana-кошелёк сохранён.")
        show_menu()
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
        setup.clear()
        setup["step"] = "sol_address"

        send_summary(
            MY_CHAT_ID,
            "Пришли Solana-адрес. Отмена: /cancel.",
        )
        return

    if action == "summary_time":
        setup.clear()
        setup["step"] = "summary_time"

        current = get_setting("daily_summary_time") or "не задано"

        send_summary(
            MY_CHAT_ID,
            f"Сейчас: {current}.\n"
            "Введи время ежедневной сводки по Москве: "
            "например 08:30.\n"
            "Для отключения отправь выкл.",
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
        "03:00",
        "Europe/Moscow",
    ).do(save_all_snapshots, notify=False)

    apply_summary_schedule()

    offset = None
    print("Бот запущен. Отправь /start в Telegram.")
    print("Снимки Nado: 03:00 МСК.")
    print(
        "Ежедневная сводка:",
        get_setting("daily_summary_time") or "отключена",
    )

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