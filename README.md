# bot_svet

Telegram-бот для рассылки об отключении электроэнергии.

Бот: `@otkl_svet_bot`.

## Как работает

- пользователь пишет `/start` и подписывается на уведомления;
- админ пишет `/admin`;
- админ нажимает кнопку `Отключение через 5/10/15 минут`;
- всем подписчикам приходит уведомление.

Текст рассылки:

```text
❗️❗️❗️ Важная информация

Через 5 минут будет отключение электроэнергии в вашем доме.

Пожалуйста, не пользуйтесь лифтом, чтобы не застрять в нем. Заранее завершите важные дела и сохраните данные.
```

## Настройка на сервере

В папке бота создайте `config.env` из примера:

```bash
cp config.env.example config.env
```

В `config.env` нужно указать токен бота:

```text
TELEGRAM_BOT_TOKEN=токен от BotFather
ADMIN_IDS=1392555265
LOCATION_NAME=вашем доме
TELEGRAM_PROXY_URL=
```

Если сервер не открывает Telegram API напрямую, укажите HTTP/HTTPS-прокси:

```text
TELEGRAM_PROXY_URL=http://login:password@ip:port
```

## Ручной запуск

```bash
cd ~/bot_svet
sudo apt update
sudo apt install -y python3
sed -i 's/\r$//' start.sh
chmod +x start.sh
./start.sh
```

## Автозапуск

```bash
cd ~/bot_svet
sudo cp bot_svet.service.example /etc/systemd/system/bot_svet.service
sudo sed -i "s|/root/bot_svet|$(pwd)|g" /etc/systemd/system/bot_svet.service
sudo systemctl daemon-reload
sudo systemctl enable --now bot_svet
sudo systemctl status bot_svet --no-pager
```

Логи:

```bash
sudo journalctl -u bot_svet -f
```
