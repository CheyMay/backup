# Telegram-бот для рассылки об отключении света

Простой бот без внешних Python-библиотек. Пользователь пишет `/start`, выбирает свой дом и попадает в список подписчиков. Админ пишет `/admin`, выбирает дом, и бот отправляет уведомление только подписчикам этого дома.

Бот: `@otkl_svet_bot`.

## Что нужно

- Python 3.10 или новее.
- Токен бота от `@BotFather`.
- Telegram ID человека, которому разрешена рассылка. Его можно узнать через `@userinfobot`.

## Быстрый запуск на Windows PowerShell

Откройте файл `config.env` и внесите:

```text
TELEGRAM_BOT_TOKEN=токен от BotFather
ADMIN_IDS=ваш Telegram ID
LOCATION_NAME=название района или дома
TELEGRAM_PROXY_URL=
```

В текущей папке токен бота и тестовый ID админа уже внесены.

Если сервер не открывает Telegram API напрямую, укажите HTTP/HTTPS-прокси:

```text
TELEGRAM_PROXY_URL=http://login:password@ip:port
```

или без логина и пароля:

```text
TELEGRAM_PROXY_URL=http://ip:port
```

После этого запустите:

```powershell
cd "C:\Users\user\Documents\Codex\2026-06-26\new-chat-2\outputs\svet-telegram-bot"
.\start.ps1
```

Если PowerShell не разрешит запуск файла, используйте:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

## Запуск на VPS Linux

Загрузите в папку `/root/bot_svet` файлы `bot.py`, `config.env`, `start.sh` и `bot_svet.service.example`.

```bash
cd /root/bot_svet
chmod +x start.sh
./start.sh
```

Для автозапуска через systemd:

```bash
sudo cp bot_svet.service.example /etc/systemd/system/bot_svet.service
sudo systemctl daemon-reload
sudo systemctl enable --now bot_svet
sudo systemctl status bot_svet
```

## Запуск без файла config.env

Откройте папку с ботом:

```powershell
cd "C:\Users\user\Documents\Codex\2026-06-26\new-chat-2\outputs\svet-telegram-bot"
```

Укажите токен и ID админа:

```powershell
$env:TELEGRAM_BOT_TOKEN="123456:ABCDEF"
$env:ADMIN_IDS="123456789"
$env:LOCATION_NAME="вашем доме"
python bot.py
```

Если админов несколько:

```powershell
$env:ADMIN_IDS="123456789,987654321"
```

## Как пользоваться

1. Подписчик открывает бота и отправляет `/start`.
2. Подписчик выбирает свой дом.
3. Админ открывает бота и отправляет `/admin`.
4. Админ нажимает `Оповестить об отключении` и выбирает нужный дом.
5. Подписчики выбранного дома получают уведомление.

Дома для выбора: `1`, `4`, `5`, `6`, `9`, `13`.

Если отключают несколько домов, админ нажимает несколько нужных домов подряд.

Текст рассылки:

```text
❗️❗️❗️ Важная информация

В доме 5 будет отключение электроэнергии.

Пожалуйста, не пользуйтесь лифтом, чтобы не застрять в нем. Заранее завершите важные дела и сохраните данные.
```

## Где хранятся подписчики

Подписчики сохраняются в файл `bot.db` рядом с `bot.py`. Если удалить этот файл, список подписчиков очистится.

## Важно

Бот должен быть постоянно запущен, иначе рассылки не будут отправляться. Для постоянной работы его обычно запускают на небольшом VPS, домашнем мини-ПК или любом сервере, который не выключается.
