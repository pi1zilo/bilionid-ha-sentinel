# BilioniD HA Bot

Telegram-бот для мониторинга двух VPS через Beszel, Uptime Kuma и Zabbix. Бот показывает инциденты и системные журналы в настроенной группе Telegram. Один узел принимает команды, а резервный проверяет доступность основного и может отправлять уведомления. При аварии оператор вручную передаёт приём команд резервному узлу.

## Возможности

- `/uptime` показывает состояние, время отклика и теги мониторов Uptime Kuma. Имена и теги можно получить из настроенной страницы статуса Kuma.
- Команда `/logs` читает `journald` на обоих VPS. `/alerts` показывает сбои и восстановления, а `/note` добавляет действия операторов в журнал инцидентов.
- События Beszel, Kuma и Zabbix сохраняются в SQLite. Очередь повторяет доставку уведомлений при временных ошибках Telegram.
- `deploy.sh --update` обновляет установку из GitHub. Можно настроить ежедневный запуск через таймер systemd. При сбое сборки или проверки готовности скрипт возвращает предыдущий коммит и образ.

Ветка `main` содержит BilioniD HA Bot. [BilioniD HA Unified с автоматическим переключением](https://github.com/pi1zilo/bilionid-ha-sentinel/tree/unified) и [исходный BilioniD HA Sentinel](https://github.com/pi1zilo/bilionid-ha-sentinel/tree/sentinel-v1) доступны со своими инструкциями.

## Запуск

На VPS нужны Linux, запущенный Docker Engine, `docker compose` с поддержкой `up --wait`, `flock` и исходящий доступ к Telegram Bot API. Для установки из GitHub нужен Git.

~~~bash
git clone --branch main --single-branch https://github.com/pi1zilo/bilionid-ha-sentinel.git bilionid-ha-bot
cd bilionid-ha-bot
bash deploy.sh --setup
~~~

Если вы устанавливаете бота из существующего архива, перенесите его через SCP или SFTP и распакуйте на VPS.

~~~bash
tar -xzf bilionid-ha-bot-vps.tar.gz
cd bilionid-ha-bot
bash deploy.sh --setup
~~~

Мастер спрашивает роль узла, токен Telegram и ID группы со знаком минус, предлагает настроить второй узел и интеграции. Он сохраняет `.env` и запускает контейнер. Если `.env` уже существует, его прежняя версия сохраняется в `.env.backup.*`. Для запуска с текущими настройками используйте `bash deploy.sh`.

Добавьте бота в указанную группу и отправьте `/start` после запуска основного узла. Команды и кнопки доступны всем участникам этой группы. Бот игнорирует личные сообщения и остальные чаты. При включённом режиме приватности Telegram адресуйте команды боту через `@`, например `/logs@имя_бота`. [Какие сообщения получает бот](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).

Состояние контейнера доступно через `bash deploy.sh --status`, журнал — через `bash deploy.sh --logs`.

SQLite сохраняется в `data/`. Скрипт запускает контейнер с UID и GID владельца этой папки. При первом запуске из-под root папка создаётся с UID и GID `1000`.

Для подключения с другого сервера привяжите `HA_BIND_IP` к адресу приватной сети. В обычном контейнере используйте `HA_HOST=0.0.0.0`. По умолчанию бот принимает команды в группе `TELEGRAM_CHAT_ID`. Если указан `TELEGRAM_COMMAND_CHAT_ID`, команды принимаются в этой группе. `ALERT_TIMEZONE` задаёт часовой пояс сообщений.

| Команда | Что показывает |
|---|---|
| `/status`, `/servers` | Серверы, состояние Kuma и очередь уведомлений |
| `/server main`, `/server backup` | Доступные метрики конкретного узла |
| `/alerts` | Последние сбои, восстановления и действия операторов на обоих доступных узлах |
| `/note suslik Перезагрузил сервер вручную` | Записать действие в журнал с автором и временем сообщения |
| `/logs`, `/logs main 50`, `/logs backup 100` | Системный журнал выбранного VPS |
| `/beszel`, `/uptime`, `/problems` | Состояние источников и проблемы Zabbix |
| `/ha` | Готовность Telegram polling и связь со вторым узлом |
| `/containers` | Контейнеры при подключённом Docker API |

## Обновление существующей установки

Обновите сначала Backup, затем Main. Если развёртываете из архива `bilionid-ha-bot-update.tar.gz`, скопируйте его через `scp` в домашний каталог SSH-пользователя на VPS. Из существующего каталога бота выполните:

~~~bash
bash deploy.sh --backup-config &&
env -u TAR_OPTIONS tar --no-same-owner --no-overwrite-dir -xzf "$HOME/bilionid-ha-bot-update.tar.gz" &&
bash deploy.sh &&
bash deploy.sh --status
~~~

Пакет обновления содержит исходники и файлы сборки. Сохраняйте серверные `.env` и `data/`. Если каталог проекта принадлежит root, выполняйте эти команды с `sudo`. После обновления обоих узлов проверьте `/ha` в группе Telegram.

### Автообновление из GitHub

Скрипт берёт новые коммиты из `origin` для текущей ветки. Для этой версии на каждом VPS выберите `main`. Для приватной копии репозитория настройте SSH deploy key с доступом на чтение для пользователя, который запускает бота. [Deploy keys в GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys#deploy-keys).

Если бот установлен из архива и вы знаете коммит именно этой установки, выполните в её каталоге `git init -b main`, `git remote add origin https://github.com/pi1zilo/bilionid-ha-sentinel.git` и `git fetch origin main`. Команда `git reset --mixed SHA_УСТАНОВЛЕННОЙ_ВЕРСИИ` заполнит индекс и сохранит файлы на диске. Проверьте `git status --short` и сохраните нужные локальные правки отдельно. Если в установленной версии ещё нет скрипта, установите обновление один раз командой `git merge --ff-only --no-overwrite-ignore origin/main && bash deploy.sh`. Если коммит неизвестен, сделайте резервную копию `.env` и `data/` и используйте свежий клон `main` для переноса этой установки.

На Backup, затем на Main, вручную проверьте обновление. Запускайте команды под пользователем, у которого есть доступ к этому каталогу, Docker и GitHub.

~~~bash
bash deploy.sh --update
sudo loginctl enable-linger "$(id -un)"
bash deploy.sh --install-update-timer 04:00  # Backup, ежедневно по Москве
~~~

На Main укажите `04:30`, чтобы разнести перезапуски. Если ветка уже актуальна или бот остановлен, контейнеры не перезапускаются. При новом коммите скрипт собирает образ, применяет Compose и проверяет `/ready`. При сбое он восстанавливает предыдущий коммит и образ. `.env` и `data/` сохраняются. Таймер использует `systemd --user`, а скрипт требует `git` и `flock`.

~~~bash
systemctl --user list-timers bilionid-ha-update.timer
journalctl --user -u bilionid-ha-update.service -n 50
systemctl --user disable --now bilionid-ha-update.timer  # Остановить расписание
~~~

## Интеграции

**Beszel.** Включите `BESZEL_ENABLED` и укажите `BESZEL_MAIN_URL`, а при наличии второго хаба — `BESZEL_BACKUP_URL`. У бота должна быть собственная учётная запись с чтением нужных систем. Для Backup используются отдельные `BESZEL_BACKUP_USERNAME`, `BESZEL_BACKUP_PASSWORD` или `BESZEL_BACKUP_AUTH_TOKEN`. Бот проверяет состояния хабов и хостов. Пороги ресурсов задаются в Beszel.

Чтобы сообщения о порогах проходили через бота и попадали в `/alerts`, задайте в `.env` активного узла случайный `BESZEL_WEBHOOK_SECRET` длиной от 32 символов и перезапустите бота. В **Settings → Notifications** Beszel добавьте Generic webhook с приватным адресом активного узла. При подключении по HTTP внутри Tailscale используйте такой URL.

```text
generic://BOT_TAILSCALE_IP:29010/events/beszel?template=json&disabletls=yes&@X-Monitor-Secret=SECRET&$hub=main
```

Для HTTPS опустите `disabletls=yes`. Для второго хаба используйте `$hub=backup`. Для порогов одного хоста настройте отправку с одного хаба, чтобы они приходили одним потоком. После успешного **Test URL** замените прежний прямой Telegram URL этим webhook. Бот использует время получения сообщения для порогов. [Формат Generic webhook](https://beszel.dev/guide/notifications/generic).

В журнале видно, какая проверка обнаружила сбой. Это может быть проверка HA API, приёма обновлений Telegram, API Beszel или связи хаба с агентом. При изменении оценочного времени загрузки по двум свежим замерам одного хаба появляется запись о признаках перезагрузки. Для причины и автора плановых действий используйте `/note`. Для обнаружения подозрительного процесса нужны данные о процессах. События таких проверок можно отправить существующим webhook Zabbix.

**Kuma.** Укажите `UPTIME_KUMA_ENABLED=true`, URL и ключ Prometheus в `UPTIME_KUMA_API_KEY`. Если `/metrics` недоступен, бот пробует страницу статуса `default` или страницу, заданную через `UPTIME_KUMA_STATUS_PAGE_SLUG`. Для событий с временем источника настройте JSON POST на `/events/kuma` и передайте `UPTIME_KUMA_WEBHOOK_SECRET` в заголовке `X-Monitor-Secret`. Без webhook переходы обнаруживаются опросом. [Ключи Kuma](https://github.com/louislam/uptime-kuma/wiki/Prometheus-API-Keys).

`/uptime` показывает теги рядом с мониторами. Бот читает их из `/metrics`, если Kuma экспортирует теги, и уточняет исходные имена и значения по странице статуса. Если в метриках тегов нет, добавьте нужные мониторы обоих VPS на страницу статуса Kuma и включите **Show Tags** («Показать тэги»). Для собственного slug укажите `UPTIME_KUMA_STATUS_PAGE_SLUG`. Используйте страницу, доступную только в вашей сети, если имена мониторов и теги предназначены для внутреннего использования.

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

Для списка локальных контейнеров выполните `bash deploy.sh --enable-docker-monitoring` на нужном VPS. Скрипт сохраняет настройки в `.env`, запускает внутренний Docker-прокси и проверяет, что бот получил список. Для списка резервного VPS включите мониторинг также там и настройте связь между узлами. Если используется свой прокси, задайте `DOCKER_MONITOR_ENABLED=true` и его `DOCKER_PROXY_URL` в `.env`.

## Системный журнал VPS

`/logs` показывает кнопки **Main** и **Backup**. Нажмите одну из них, чтобы получить последние 50 строк журнала. Команда `/logs backup 100` сразу запрашивает 100 строк у Backup. Можно указать от 1 до 200 строк. Длинный вывод приходит файлом `.txt`. Времена записей указаны в UTC. Команда показывает записи Linux `journald`. Контейнеры с драйвером Docker `json-file` видны через `bash deploy.sh --logs` на VPS.

На каждом VPS, чей журнал нужен, определите каталог журнала текущей машины и числовой GID группы `systemd-journal`.

~~~bash
machine_id=$(cat /etc/machine-id)
journal_dir="/var/log/journal/$machine_id"
if [ ! -d "$journal_dir" ]; then journal_dir="/run/log/journal/$machine_id"; fi
printf 'SERVER_JOURNAL_DIR=%s\nSERVER_JOURNAL_GID=%s\n' "$journal_dir" "$(getent group systemd-journal | cut -d: -f3)"
~~~

Добавьте полученные значения в `.env` этого VPS. Укажите `COMPOSE_FILE=docker-compose.yml:docker-compose.server-logs.yml`. Если `COMPOSE_FILE` уже задан, добавьте `:docker-compose.server-logs.yml` в конец его значения. Дополнительный файл Compose включает установку `journalctl`, добавляет контейнеру GID группы `systemd-journal` и монтирует каталог в режиме чтения. [Руководство journalctl](https://www.freedesktop.org/software/systemd/man/latest/journalctl).

Пересоберите бота и проверьте чтение журнала из работающего контейнера на каждом VPS:

~~~bash
bash deploy.sh &&
docker compose --env-file .env exec -T bot \
  journalctl --directory=/host-journal --system --no-pager -n 1
~~~

Если `journalctl` отсутствует внутри контейнера, проверьте, что Compose использует дополнительный файл и сборка получила `INSTALL_JOURNALCTL=true`. Если журнал читается на обоих VPS, а `/logs backup 50` сообщает об ошибке, проверьте связь узлов через `/ha`.

Отправляйте `/logs` в настроенной группе Telegram. Для запроса журнала второго VPS нужна настроенная связь HA.

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

Подробности о сборе событий и переключении узлов описаны в [ARCHITECTURE.md](ARCHITECTURE.md). Проект распространяется по [лицензии MIT](LICENSE).
