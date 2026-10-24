# Bobby Watcher (Portfolio tracker)
<img width="488" height="684" alt="8499b077a3088db8b10a9a036c62bdc1" src="https://github.com/user-attachments/assets/ffb2886b-051b-4d6d-8584-d932bfacdc2f" />

Бот для проверки кошельков и позиций в Telegram. Идея — получать утром одну сводку, а не открывать каждый аккаунт вручную.

На Nado можно добавить несколько кошельков и выбрать субаккаунты для отслеживания. В сводке есть открытые фьючерсы, размер позиции, цена входа, текущая цена, изменение за 24 часа, mark price, P&L и funding. Также показывается equity и его изменение относительно последнего снимка.

Для Solana выводятся баланс SOL и остатки токенов с названиями и mint-адресами. Расчёта P&L и стоимости в USD для Solana пока нет.

Бот рассчитан на одного пользователя. Кошельки и время сводки настраиваются кнопками в личном чате. Приватные ключи от кошельков не нужны.

## Установка

Нужен Python 3.12 или новее. Проект проверялся на Python 3.13.3. Команды ниже — для macOS и Linux.

Скачайте проект и откройте его папку в терминале:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Настройка

Создайте бота через [@BotFather](https://t.me/BotFather) командой `/newbot` и скопируйте токен.

Рядом с `bot.py` создайте файл `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_CHAT_ID=id_вашего_личного_чата
```

Если ID чата неизвестен, отправьте своему боту `/start`. Затем выполните команду ниже из папки проекта с активированным окружением. Сам бот в этот момент должен быть остановлен.

```bash
python - <<'PYTHON'
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("Сначала запишите токен в .env.")
try:
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
except (requests.RequestException, ValueError) as error:
    raise SystemExit(f"Ошибка запроса: {type(error).__name__}") from None

chats = {}
for update in data.get("result", []):
    chat = update.get("message", {}).get("chat", {})
    if chat.get("type") == "private":
        chats[chat["id"]] = chat.get("first_name", "Личный чат")
for chat_id, name in chats.items():
    print(f"{name}: {chat_id}")
if not chats:
    print("Отправьте боту новое сообщение и повторите команду.")
PYTHON
```

Впишите число из строки своего чата в `TELEGRAM_CHAT_ID`. Сообщения из других чатов бот игнорирует.

По умолчанию используется публичный Solana RPC. При необходимости свой адрес можно добавить в `.env` отдельной строкой `SOLANA_RPC_URL=адрес_RPC`.

## Запуск

```bash
source .venv/bin/activate
python bot.py
```

Отправьте `/start` в Telegram. Через меню добавьте Nado или Solana, укажите адреса и названия кошельков. Для Nado отметьте нужные субаккаунты и сохраните выбор. Повторная настройка того же адреса заменяет прежний выбор субаккаунтов.

В пункте «Время ежедневной сводки» отправьте время, например `08:30`. Время указывается по Москве. Чтобы отключить отправку, в этом же пункте отправьте `выкл`.

Команды бота:

- `/start` — меню;
- `/test` — проверка связи;
- `/wallets` — сохранённые кошельки;
- `/summary` — сводка сейчас;
- `/snapshot` — сохранить снимок equity Nado;
- `/cancel` — отменить текущую настройку.

Остановка — `Ctrl+C` в терминале.

## Расписание и данные

Снимки equity Nado сохраняются каждый день в 03:00 МСК. Время отправки сводки задаётся отдельно. Изменение equity включает пополнения и выводы, поэтому оно не равно торговому P&L.

Для работы расписания бот должен оставаться запущенным, а компьютер — включённым и подключённым к интернету. Во время сна или остановки процесса сводки не отправляются. Пропущенные задания после запуска не восполняются. Запускайте только один экземпляр бота.

Кошельки, настройки и снимки хранятся в `portfolio.db`. База создаётся автоматически и сохраняется между запусками. `.env` и база исключены из Git; публиковать их не нужно.

## Код

- `bot.py` — команды, кнопки, настройка и расписание;
- `nado_positions.py` — данные Nado и расчёт P&L;
- `solana_positions.py` — балансы SOL и токенов, названия из DexScreener;
- `storage.py` — работа с SQLite.

Если у токена нет названия, в сводке остаётся его mint-адрес. При ошибках API часть данных может быть недоступна — бот отмечает это в сообщении.
