# BilioniD HA Bot

Приватный Telegram-бот для Beszel, Uptime Kuma и Zabbix. Один узел принимает команды. Второй может отдельно проверять первый и присылать сообщения о его доступности.

## Запуск

Нужны Linux, Docker Compose v2 и доступ к Telegram Bot API. Добавьте бота в чат уведомлений и настройте `.env`.

~~~bash
bash deploy.sh --init
# Заполните TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID и TELEGRAM_ADMIN_USER_ID.
bash deploy.sh --check
bash deploy.sh
bash deploy.sh --status
~~~

Для подключения с другого сервера привяжите `HA_BIND_IP` к адресу приватной сети. В обычном контейнере используйте `HA_HOST=0.0.0.0`. `TELEGRAM_COMMAND_CHAT_ID` дополнительно ограничивает чат команд, `ALERT_TIMEZONE` задаёт часовой пояс сообщений.

| Команда | Что показывает |
|---|---|
| `/status`, `/servers` | Серверы, состояние Kuma и очередь уведомлений |
| `/server main`, `/server backup` | Доступные метрики конкретного узла |
| `/alerts` | Открытые и закрытые инциденты |
| `/beszel`, `/uptime`, `/problems` | Состояние источников и проблемы Zabbix |
| `/ha` | Готовность Telegram polling и связь со вторым узлом |
| `/containers` | Контейнеры при подключённом Docker API |

## Интеграции

**Beszel.** Включите `BESZEL_ENABLED` и укажите `BESZEL_MAIN_URL`, а при наличии второго хаба — `BESZEL_BACKUP_URL`. У бота должна быть собственная учётная запись с чтением нужных систем. Для Backup используются отдельные `BESZEL_BACKUP_USERNAME`, `BESZEL_BACKUP_PASSWORD` или `BESZEL_BACKUP_AUTH_TOKEN`. Бот проверяет состояния хабов и хостов. Пороги ресурсов задаются в Beszel.

**Kuma.** Укажите `UPTIME_KUMA_ENABLED=true`, URL и ключ Prometheus в `UPTIME_KUMA_API_KEY`. По желанию задайте `UPTIME_KUMA_STATUS_PAGE_SLUG`. Для событий с временем источника настройте JSON POST на `/events/kuma` и передайте `UPTIME_KUMA_WEBHOOK_SECRET` в заголовке `X-Monitor-Secret`. Без webhook переходы обнаруживаются опросом. [Ключи Kuma](https://github.com/louislam/uptime-kuma/wiki/Prometheus-API-Keys).

**Zabbix.** Для `/problems` задайте `ZABBIX_URL` и API-токен пользователя с доступом только к чтению нужных хостов. Для уведомлений задайте `ZABBIX_WEBHOOK_SECRET` и создайте media type Webhook со скриптом `scripts/zabbix-webhook.js`. Укажите отправку в операциях проблемы и восстановления. Пример проверки — Zabbix trapper `backup.exit`, куда задача передаёт код завершения.

| Параметр media type | Значение |
|---|---|
| `endpoint` | Приватный адрес бота с путём `/events/zabbix` |
| `secret` | Значение `ZABBIX_WEBHOOK_SECRET` |
| `event_id` | `{EVENT.ID}` |
| `recovery_id` | `{EVENT.RECOVERY.ID}` |
| `name` | `{EVENT.NAME}` |
| `severity` | `{EVENT.SEVERITY}` |
| `detail` | `{ALERT.MESSAGE}` |
| `proxy` | Опциональный адрес прокси, по умолчанию прямое подключение |

Для контейнеров выставьте `DOCKER_MONITOR_ENABLED=true` и укажите `DOCKER_PROXY_URL` ограниченного Docker API в приватной сети.

## Два узла

1. Настройте `HA_PEER_ENABLED=true`, одинаковый случайный `HA_SECRET_KEY`, приватный `HA_BIND_IP` и параметры второго узла на каждом VPS.
2. Main запускается с `NODE_ROLE=PRIMARY` и `TELEGRAM_POLLING_ENABLED=true`. Backup использует `NODE_ROLE=STANDBY`, `TELEGRAM_POLLING_ENABLED=false` и `HA_WATCH_PRIMARY=true`. Токен и чат одинаковые. Разместите проверки доступности Main вне Main, чтобы они работали при его отключении.
3. Перед передачей polling остановите прежний экземпляр через `docker compose stop bot` либо подтвердите выключение хоста через консоль провайдера. Запишите ему `TELEGRAM_POLLING_ENABLED=false`. Затем включите polling на Backup и запустите его. Проверьте `/ready` и `/ha`. При использовании webhook перенаправьте его на активный узел.
4. Для возврата повторите процедуру в обратном порядке. На каждом узле отдельная SQLite. При плановой передаче истории и очереди используйте согласованный снимок базы.

Если состояние старого узла неизвестно, отложите переключение до его проверки. Независимый арбитр и общее хранилище нужны для автоматического переключения с общей историей.

## Тесты, бэкап и откат

~~~bash
python -m pip install -r requirements-dev.txt
python -m pytest
bash deploy.sh --backup-config
~~~

Доступность обеих нод и один активный polling проверяются командой `python scripts/simulate_ha_test.py MAIN_URL BACKUP_URL` с переменной `HA_SECRET_KEY` в окружении. Бэкап включает `.env` и согласованный снимок SQLite. Перед восстановлением снимка остановите узел и проверьте `PRAGMA integrity_check`. Для отката образа используйте сохранённый `IMAGE_TAG` и `docker compose up -d --no-build`.
