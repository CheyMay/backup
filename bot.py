import json
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime
from typing import Any
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
BROADCAST_QUEUE: queue.Queue[tuple[int, int]] = queue.Queue()


class TelegramApiError(Exception):
    def __init__(self, status: int, description: str) -> None:
        self.status = status
        self.description = description
        super().__init__(f"{status}: {description}")


def on_stop(signum: int, frame: Any) -> None:
    global STOP
    STOP = True


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribed_at TEXT NOT NULL
            )
            """
        )


def add_subscriber(chat_id: int, username: str | None, first_name: str | None) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, first_name, subscribed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (chat_id, username, first_name, datetime.now().isoformat(timespec="seconds")),
        )


def remove_subscriber(chat_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))


def list_subscribers() -> list[int]:
    with db() as conn:
        rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
    return [int(row["chat_id"]) for row in rows]


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
            [{"text": "Отключение через 5 минут", "callback_data": "broadcast:5"}],
            [{"text": "Отключение через 10 минут", "callback_data": "broadcast:10"}],
            [{"text": "Отключение через 15 минут", "callback_data": "broadcast:15"}],
            [{"text": "Сколько подписчиков", "callback_data": "stats"}],
        ]
    }


def start_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Отписаться", "callback_data": "unsubscribe"}],
        ]
    }


def broadcast(minutes: int) -> tuple[int, int]:
    text = (
        "❗️❗️❗️ Важная информация\n\n"
        f"Через {minutes} минут будет отключение электроэнергии в {LOCATION_NAME}.\n\n"
        "Пожалуйста, не пользуйтесь лифтом, чтобы не застрять в нем. "
        "Заранее завершите важные дела и сохраните данные."
    )
    sent = 0
    failed = 0

    for chat_id in list_subscribers():
        try:
            send_message(chat_id, text)
            sent += 1
            time.sleep(0.05)
        except TelegramApiError as error:
            failed += 1
            if error.status in (400, 403):
                remove_subscriber(chat_id)

    return sent, failed


def enqueue_broadcast(admin_chat_id: int, minutes: int) -> None:
    BROADCAST_QUEUE.put((admin_chat_id, minutes))


def broadcast_worker() -> None:
    while not STOP:
        try:
            admin_chat_id, minutes = BROADCAST_QUEUE.get(timeout=1)
        except queue.Empty:
            continue

        try:
            send_message(admin_chat_id, f"Рассылка на {minutes} минут запущена.")
            sent, failed = broadcast(minutes)
            send_message(
                admin_chat_id,
                f"Рассылка завершена. Доставлено: {sent}. Ошибок: {failed}.",
            )
        except Exception as error:
            try:
                send_message(admin_chat_id, f"Ошибка рассылки: {error}")
            except Exception:
                pass
        finally:
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
            "Вы подписаны на уведомления об отключении света.",
            start_keyboard(),
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
        send_message(chat_id, "Выберите рассылку:", admin_keyboard())
        return

    if is_admin(user_id) and text in {"5", "10", "15"}:
        enqueue_broadcast(chat_id, int(text))
        send_message(chat_id, f"Рассылка на {text} минут поставлена в очередь.")
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

    if not is_admin(user_id):
        answer_callback(callback_id, "Нет доступа")
        return

    if data == "stats" and chat_id:
        count = len(list_subscribers())
        answer_callback(callback_id)
        send_message(chat_id, f"Подписчиков: {count}.", admin_keyboard())
        return

    if data.startswith("broadcast:") and chat_id:
        minutes = int(data.split(":", 1)[1])
        enqueue_broadcast(chat_id, minutes)
        answer_callback(callback_id, "Рассылка поставлена в очередь")
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
