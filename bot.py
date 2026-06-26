import json
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "").strip()
ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "bot.db")
LOCATION_NAME = os.getenv("LOCATION_NAME", "вашем доме")
HOUSES = ("1", "4", "5", "6", "9", "13")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPENER = (
    urlopen
    if not TELEGRAM_PROXY_URL
    else build_opener(
        ProxyHandler(
            {
                "http": TELEGRAM_PROXY_URL,
                "https": TELEGRAM_PROXY_URL,
            }
        )
    ).open
)
STOP = False
BROADCAST_QUEUE: queue.Queue[tuple[int, str]] = queue.Queue()
ACTIVE_BROADCAST_HOUSES: set[str] = set()
ACTIVE_BROADCAST_LOCK = threading.Lock()


class TelegramApiError(Exception):
    def __init__(self, status: int, description: str) -> None:
        self.status = status
        self.description = description
        super().__init__(f"{status}: {description}")


def on_stop(signum: int, frame: Any) -> None:
    global STOP
    STOP = True


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                house TEXT,
                subscribed_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(subscribers)").fetchall()
        }
        if "house" not in columns:
            conn.execute("ALTER TABLE subscribers ADD COLUMN house TEXT")


def add_subscriber(chat_id: int, username: str | None, first_name: str | None) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, first_name, house, subscribed_at)
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (chat_id, username, first_name, datetime.now().isoformat(timespec="seconds")),
        )


def set_subscriber_house(chat_id: int, house: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE subscribers SET house = ? WHERE chat_id = ?",
            (house, chat_id),
        )


def get_subscriber_house(chat_id: int) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT house FROM subscribers WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    if not row:
        return None
    return row["house"]


def remove_subscriber(chat_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))


def list_subscribers_by_house(house: str) -> list[int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM subscribers WHERE house = ?",
            (house,),
        ).fetchall()
    return [int(row["chat_id"]) for row in rows]


def count_subscribers_by_house() -> dict[str, int]:
    counts = {house: 0 for house in HOUSES}
    with db() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(house, '') AS house, COUNT(*) AS count
            FROM subscribers
            GROUP BY COALESCE(house, '')
            """
        ).fetchall()

    without_house = 0
    for row in rows:
        house = str(row["house"])
        count = int(row["count"])
        if house in counts:
            counts[house] = count
        else:
            without_house += count

    if without_house:
        counts["Без дома"] = without_house
    return counts


def api(method: str, payload: dict[str, Any], timeout: int = 20) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_BASE}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with OPENER(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = json.loads(error.read().decode("utf-8"))
        raise TelegramApiError(error.code, body.get("description", "Telegram API error"))
    except URLError as error:
        raise TelegramApiError(0, str(error.reason))

    if not body.get("ok"):
        raise TelegramApiError(0, body.get("description", "Telegram API error"))
    return body.get("result")


def send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    api("sendMessage", payload, timeout=15)


def answer_callback(callback_id: str, text: str = "") -> None:
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    api("answerCallbackQuery", payload, timeout=8)


def admin_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Оповестить об отключении", "callback_data": "admin_outage"}],
            [{"text": "Сколько подписчиков", "callback_data": "stats"}],
        ]
    }


def house_keyboard(prefix: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": f"Дом {HOUSES[index]}", "callback_data": f"{prefix}:{HOUSES[index]}"},
                {"text": f"Дом {HOUSES[index + 1]}", "callback_data": f"{prefix}:{HOUSES[index + 1]}"},
            ]
            for index in range(0, len(HOUSES), 2)
        ]
    }


def admin_house_keyboard() -> dict[str, Any]:
    keyboard = house_keyboard("broadcast_house")
    keyboard["inline_keyboard"].append([{"text": "Назад", "callback_data": "admin_back"}])
    return keyboard


def confirm_broadcast_keyboard(house: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Да, отправить", "callback_data": f"confirm_broadcast:{house}"}],
            [{"text": "Отмена", "callback_data": "cancel_broadcast"}],
        ]
    }


def start_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Изменить дом", "callback_data": "choose_house"}],
            [{"text": "Отписаться", "callback_data": "unsubscribe"}],
        ]
    }


def broadcast(house: str) -> tuple[int, int]:
    text = (
        "❗️❗️❗️ Важная информация\n\n"
        f"В доме {house} будет отключение электроэнергии.\n\n"
        "Пожалуйста, не пользуйтесь лифтом, чтобы не застрять в нем. "
        "Заранее завершите важные дела и сохраните данные."
    )
    sent = 0
    failed = 0

    for chat_id in list_subscribers_by_house(house):
        try:
            send_message(chat_id, text)
            sent += 1
            time.sleep(0.05)
        except TelegramApiError as error:
            failed += 1
            if error.status in (400, 403):
                remove_subscriber(chat_id)

    return sent, failed


def enqueue_broadcast(admin_chat_id: int, house: str) -> bool:
    with ACTIVE_BROADCAST_LOCK:
        if house in ACTIVE_BROADCAST_HOUSES:
            return False
        ACTIVE_BROADCAST_HOUSES.add(house)
    BROADCAST_QUEUE.put((admin_chat_id, house))
    return True


def broadcast_worker() -> None:
    while not STOP:
        try:
            admin_chat_id, house = BROADCAST_QUEUE.get(timeout=1)
        except queue.Empty:
            continue

        try:
            send_message(admin_chat_id, f"Рассылка по дому {house} запущена.")
            sent, failed = broadcast(house)
            send_message(
                admin_chat_id,
                f"Дом {house}: рассылка завершена. Доставлено: {sent}. Ошибок: {failed}.",
            )
        except Exception as error:
            try:
                send_message(admin_chat_id, f"Ошибка рассылки: {error}")
            except Exception:
                pass
        finally:
            with ACTIVE_BROADCAST_LOCK:
                ACTIVE_BROADCAST_HOUSES.discard(house)
            BROADCAST_QUEUE.task_done()


def is_admin(user_id: int | None) -> bool:
    return user_id in ADMIN_IDS


def handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return

    if text.startswith("/start"):
        add_subscriber(chat_id, user.get("username"), user.get("first_name"))
        send_message(
            chat_id,
            "Выберите ваш дом, чтобы получать уведомления только по нему:",
            house_keyboard("house"),
        )
        return

    if text.startswith("/stop") or text.lower() in {"отписаться", "стоп"}:
        remove_subscriber(chat_id)
        send_message(chat_id, "Вы отписались от уведомлений.")
        return

    if text.startswith("/admin"):
        if not is_admin(user_id):
            send_message(chat_id, "Нет доступа.")
            return
        send_message(chat_id, "Панель администратора:", admin_keyboard())
        return

    send_message(chat_id, "Команды: /start - подписаться, /stop - отписаться.")


def handle_callback(callback: dict[str, Any]) -> None:
    callback_id = callback["id"]
    data = callback.get("data", "")
    user = callback.get("from", {})
    user_id = user.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if data == "unsubscribe" and chat_id:
        remove_subscriber(chat_id)
        answer_callback(callback_id, "Готово")
        send_message(chat_id, "Вы отписались от уведомлений.")
        return

    if data == "choose_house" and chat_id:
        add_subscriber(chat_id, user.get("username"), user.get("first_name"))
        answer_callback(callback_id)
        send_message(chat_id, "Выберите ваш дом:", house_keyboard("house"))
        return

    if data.startswith("house:") and chat_id:
        house = data.split(":", 1)[1]
        if house not in HOUSES:
            answer_callback(callback_id, "Неизвестный дом")
            return
        add_subscriber(chat_id, user.get("username"), user.get("first_name"))
        current_house = get_subscriber_house(chat_id)
        if current_house == house:
            answer_callback(callback_id, f"Дом {house} уже выбран")
            return
        set_subscriber_house(chat_id, house)
        answer_callback(callback_id, f"Дом {house} сохранен")
        send_message(
            chat_id,
            f"Дом {house} сохранен. Вы подписаны на уведомления.",
            start_keyboard(),
        )
        return

    if not is_admin(user_id):
        answer_callback(callback_id, "Нет доступа")
        return

    if data == "stats" and chat_id:
        counts = count_subscribers_by_house()
        total = sum(counts.values())
        lines = [f"Подписчиков всего: {total}.", ""]
        lines.extend(f"Дом {house}: {counts.get(house, 0)}" for house in HOUSES)
        if counts.get("Без дома", 0):
            lines.append(f"Без дома: {counts['Без дома']}")
        answer_callback(callback_id)
        send_message(chat_id, "\n".join(lines), admin_keyboard())
        return

    if data == "admin_outage" and chat_id:
        answer_callback(callback_id)
        send_message(chat_id, "Выберите дом для оповещения:", admin_house_keyboard())
        return

    if data == "admin_back" and chat_id:
        answer_callback(callback_id)
        send_message(chat_id, "Панель администратора:", admin_keyboard())
        return

    if data.startswith("broadcast_house:") and chat_id:
        house = data.split(":", 1)[1]
        if house not in HOUSES:
            answer_callback(callback_id, "Неизвестный дом")
            return
        answer_callback(callback_id)
        send_message(
            chat_id,
            f"Оповестить дом {house} об отключении электроэнергии?",
            confirm_broadcast_keyboard(house),
        )
        return

    if data.startswith("confirm_broadcast:") and chat_id:
        house = data.split(":", 1)[1]
        if house not in HOUSES:
            answer_callback(callback_id, "Неизвестный дом")
            return
        queued = enqueue_broadcast(chat_id, house)
        if not queued:
            answer_callback(callback_id, f"Дом {house}: рассылка уже идет")
            return
        answer_callback(callback_id, f"Дом {house}: рассылка поставлена")
        send_message(chat_id, f"Дом {house}: рассылка поставлена в очередь.")
        return

    if data == "cancel_broadcast" and chat_id:
        answer_callback(callback_id, "Отменено")
        send_message(chat_id, "Рассылка отменена.", admin_keyboard())
        return

    answer_callback(callback_id)


def poll_updates() -> None:
    offset = None

    while not STOP:
        try:
            payload: dict[str, Any] = {
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset

            updates = api("getUpdates", payload)

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        handle_message(update["message"])
                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])
                except Exception as error:
                    print(f"Update handling error: {error}", file=sys.stderr)
        except TelegramApiError as error:
            print(f"Telegram API error: {error}", file=sys.stderr)
            time.sleep(5)


def main() -> None:
    if not BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
        sys.exit(1)
    if not ADMIN_IDS:
        print("Set ADMIN_IDS first, for example: 123456789,987654321", file=sys.stderr)
        sys.exit(1)

    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGTERM, on_stop)

    init_db()
    api("deleteWebhook", {"drop_pending_updates": False})
    threading.Thread(target=broadcast_worker, daemon=True).start()
    print("Bot is running. Press Ctrl+C to stop.")
    poll_updates()
    print("Bot stopped.")


if __name__ == "__main__":
    main()
