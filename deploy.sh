#!/usr/bin/env bash
# ==============================================================================
# BilioniD HA Sentinel — Интерактивный установщик и скрипт управления
# ==============================================================================
# Поддерживаемые флаги:
#   bash deploy.sh                — Интерактивная установка / обновление
#   bash deploy.sh --check        — Только preflight-проверки системы
#   bash deploy.sh --status       — Статус контейнера, healthcheck и HA
#   bash deploy.sh --logs         — Просмотр и отслеживание логов
#   bash deploy.sh --backup-config — Создание резервной копии .env и данных
# ==============================================================================

set -euo pipefail

# Цвета для терминала
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
DATA_DIR="data"

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERROR]${NC} $1"; }
log_header() {
    echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}"
}

# ------------------------------------------------------------------------------
# Флаги командной строки
# ------------------------------------------------------------------------------
show_status() {
    log_header "Статус BilioniD HA Sentinel"
    if ! command -v docker &>/dev/null; then
        log_err "Docker не установлен."
        exit 1
    fi

    if docker compose ps --all | grep -q "bilionid-ha-sentinel"; then
        docker compose ps
        echo ""
        log_info "Проверка Healthcheck endpoint..."
        HA_PORT_CFG=$(grep -E "^HA_PORT=" .env 2>/dev/null | cut -d '=' -f2 || echo "29010")
        if curl -s -m 3 "http://127.0.0.1:${HA_PORT_CFG}/health" 2>/dev/null | grep -q '"status"'; then
            log_ok "HA Endpoint (http://127.0.0.1:${HA_PORT_CFG}/health) отвечает:"
            curl -s -m 3 "http://127.0.0.1:${HA_PORT_CFG}/health" | python3 -m json.tool 2>/dev/null || curl -s "http://127.0.0.1:${HA_PORT_CFG}/health"
        else
            log_warn "HA Endpoint не отвечает или контейнер запускается."
        fi
    else
        log_warn "Контейнеры bilionid-ha-sentinel не запущены."
    fi
}

show_logs() {
    log_header "Логи BilioniD HA Sentinel"
    docker compose logs -f --tail=100
}

backup_config() {
    log_header "Резервное копирование конфигурации"
    BACKUP_TS=$(date +%Y%m%d_%H%M%S)
    BACKUP_NAME="bilionid-config-backup-${BACKUP_TS}.tar.gz"

    if [[ ! -f "$ENV_FILE" ]]; then
        log_warn "Файл $ENV_FILE не найден. Резервная копия не создана."
        return
    fi

    tar -czf "$BACKUP_NAME" "$ENV_FILE" "$DATA_DIR" 2>/dev/null || tar -czf "$BACKUP_NAME" "$ENV_FILE"
    chmod 600 "$BACKUP_NAME"
    log_ok "Резервная копия сохранена в файл: $BACKUP_NAME"
}

# Обработка аргументов
if [[ "${1:-}" == "--status" ]]; then
    show_status
    exit 0
elif [[ "${1:-}" == "--logs" ]]; then
    show_logs
    exit 0
elif [[ "${1:-}" == "--backup-config" ]]; then
    backup_config
    exit 0
fi

# ==============================================================================
# Preflight проверки
# ==============================================================================
run_preflight_checks() {
    log_header "1. Preflight проверки окружения"

    # 1. ОС
    if [[ -f /etc/os-release ]]; then
        OS_NAME=$(grep -E "^PRETTY_NAME=" /etc/os-release | cut -d '=' -f2 | tr -d '"')
        log_ok "ОС: $OS_NAME"
    else
        log_warn "Не удалось точно определить версию ОС (файл /etc/os-release отсутствует)."
    fi

    # 2. Docker
    if command -v docker &>/dev/null; then
        DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
        log_ok "Docker установлен: v$DOCKER_VER"
    else
        log_err "Docker не найден! Установите Docker перед развертыванием."
        exit 1
    fi

    # 3. Docker Compose v2
    if docker compose version &>/dev/null; then
        COMPOSE_VER=$(docker compose version --short)
        log_ok "Docker Compose v2 установлен: v$COMPOSE_VER"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_VER=$(docker-compose --version | awk '{print $3}' | tr -d ',')
        log_ok "Docker Compose установлен: v$COMPOSE_VER"
    else
        log_err "Docker Compose не найден! Установите Docker Compose (plugin)."
        exit 1
    fi

    # 4. Tailscale
    LOCAL_TS_IP=""
    if command -v tailscale &>/dev/null; then
        LOCAL_TS_IP=$(tailscale ip -4 2>/dev/null || true)
        if [[ -n "$LOCAL_TS_IP" ]]; then
            log_ok "Tailscale активен. Локальный IP: $LOCAL_TS_IP"
        else
            log_warn "Tailscale установлен, но IP не получен (возможно, не залогинен)."
        fi
    else
        # Проверим сетевые интерфейсы на наличие 100.x.x.x
        LOCAL_TS_IP=$(ip -4 addr show 2>/dev/null | grep -oP '(?<=inet\s)100\.\d+\.\d+\.\d+' | head -n1 || true)
        if [[ -n "$LOCAL_TS_IP" ]]; then
            log_ok "Обнаружен Tailscale IP интерфейса: $LOCAL_TS_IP"
        else
            log_warn "Tailscale CLI не найден. Убедитесь, что Tailscale настроен."
        fi
    fi

    # 5. Ресурсы: ОЗУ и Диск
    FREE_RAM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $7}' || echo "unknown")
    TOTAL_RAM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "unknown")
    FREE_DISK_GB=$(df -BG . 2>/dev/null | awk 'NR==2 {print $4}' | tr -d 'G' || echo "unknown")

    log_ok "Оперативная память: свободно ${FREE_RAM_MB} MB из ${TOTAL_RAM_MB} MB"
    log_ok "Свободное место на диске: ${FREE_DISK_GB} GB"

    if [[ "$FREE_RAM_MB" != "unknown" && "$FREE_RAM_MB" -lt 250 ]]; then
        log_warn "Мало свободной оперативной памяти (<250 MB). Рекомендуется освободить ресурсы."
    fi

    if [[ "$FREE_DISK_GB" != "unknown" && "$FREE_DISK_GB" -lt 2 ]]; then
        log_warn "Мало места на диске (<2 GB). Сборка Docker образа может потребовать больше места."
    fi

    # 6. Hostname
    CURRENT_HOSTNAME=$(hostname -f 2>/dev/null || hostname)
    log_ok "Hostname узла: $CURRENT_HOSTNAME"
}

if [[ "${1:-}" == "--check" ]]; then
    run_preflight_checks
    log_ok "Preflight-проверки завершены успешно."
    exit 0
fi

# ==============================================================================
# Интерактивный мастер конфигурации
# ==============================================================================
clear || true
echo -e "${BOLD}${CYAN}"
echo "======================================================================"
echo "          🤖 BilioniD HA Sentinel — Интерактивный установщик          "
echo "        Отказоустойчивый мониторинг инфраструктуры Main & Backup       "
echo "======================================================================"
echo -e "${NC}"

run_preflight_checks

log_header "2. Конфигурация роли узла"

DEFAULT_ROLE="1"
RECOMMENDED_ROLE_TEXT="PRIMARY (Основной сервер / Main)"
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_ROLE=$(grep -E "^NODE_ROLE=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2 || true)
    if [[ "$EXISTING_ROLE" == "STANDBY" ]]; then
        DEFAULT_ROLE="2"
        RECOMMENDED_ROLE_TEXT="STANDBY (Резервный сервер / Backup)"
    fi
fi

echo -e "Выберите роль для данного сервера:"
echo -e "  ${BOLD}1)${NC} MAIN / PRIMARY   — Основной узел (активный бот по умолчанию)"
echo -e "  ${BOLD}2)${NC} BACKUP / STANDBY — Резервный узел (горячий standby)"
echo -e "Рекомендуется для этого сервера: ${GREEN}${RECOMMENDED_ROLE_TEXT}${NC}"

read -r -p "Введите номер роли [1/2] (по умолчанию $DEFAULT_ROLE): " ROLE_CHOICE
ROLE_CHOICE="${ROLE_CHOICE:-$DEFAULT_ROLE}"

if [[ "$ROLE_CHOICE" == "1" ]]; then
    NODE_ROLE="PRIMARY"
    DEFAULT_NODE_NAME="${CURRENT_HOSTNAME:-primary-node}"
    DEFAULT_PEER_NAME="backup-node"
else
    NODE_ROLE="STANDBY"
    DEFAULT_NODE_NAME="${CURRENT_HOSTNAME:-backup-node}"
    DEFAULT_PEER_NAME="primary-node"
fi

read -r -p "Имя текущего узла [$DEFAULT_NODE_NAME]: " INPUT_NODE_NAME
NODE_NAME="${INPUT_NODE_NAME:-$DEFAULT_NODE_NAME}"

read -r -p "Имя парного узла [$DEFAULT_PEER_NAME]: " INPUT_PEER_NAME
PEER_NODE_NAME="${INPUT_PEER_NAME:-$DEFAULT_PEER_NAME}"

DEFAULT_LOCAL_IP="${LOCAL_TS_IP:-100.64.0.1}"
read -r -p "Tailscale IP текущего узла [$DEFAULT_LOCAL_IP]: " INPUT_TS_IP
TAILSCALE_IP="${INPUT_TS_IP:-$DEFAULT_LOCAL_IP}"

DEFAULT_PEER_IP="100.64.0.2"
read -r -p "Tailscale IP парного сервера ($PEER_NODE_NAME) [$DEFAULT_PEER_IP]: " INPUT_PEER_IP
PEER_TAILSCALE_IP="${INPUT_PEER_IP:-$DEFAULT_PEER_IP}"

# ------------------------------------------------------------------------------
# Проверка портов для межсерверного HA
# ------------------------------------------------------------------------------
log_header "3. Настройка сетевого порта HA Supervisor"
echo -e "HA Supervisor использует отдельный легковесный HTTP-порт для обмена heartbeat,"
echo -e "проверок состояния и синхронизации между серверами по сети Tailscale."
echo -e "Существующие занятые порты в инфраструктуре:"
echo -e "  • Main: 20000, 28000, 28001, 29000, 29001, 29002"
echo -e "  • Backup: 29000"
echo -e "Рекомендуемый свободный порт: ${GREEN}29010${NC}"

DEFAULT_HA_PORT="29010"
read -r -p "Порт для HA Supervisor [$DEFAULT_HA_PORT]: " INPUT_HA_PORT
HA_PORT="${INPUT_HA_PORT:-$DEFAULT_HA_PORT}"

# Проверка занятости порта на хосте
is_port_busy() {
    local port=$1
    if command -v ss &>/dev/null; then
        ss -tuln | grep -q ":${port} " && return 0
    elif command -v netstat &>/dev/null; then
        netstat -tuln | grep -q ":${port} " && return 0
    elif command -v lsof &>/dev/null; then
        lsof -i ":${port}" &>/dev/null && return 0
    fi
    return 1
}

if is_port_busy "$HA_PORT"; then
    log_err "ВНИМАНИЕ: Порт $HA_PORT уже занят на данном сервере!"
    NEW_PORT=$((HA_PORT + 1))
    while is_port_busy "$NEW_PORT"; do
        NEW_PORT=$((NEW_PORT + 1))
    done
    echo -e "Предлагаемый свободный порт: ${GREEN}${NEW_PORT}${NC}"
    read -r -p "Использовать порт $NEW_PORT? [Y/n]: " PORT_CONFIRM
    if [[ "$PORT_CONFIRM" =~ ^[Nn] ]]; then
        read -r -p "Укажите свободный порт вручную: " HA_PORT
    else
        HA_PORT="$NEW_PORT"
    fi
fi
log_ok "Порт HA Supervisor: $HA_PORT"

# ------------------------------------------------------------------------------
# Секретный ключ HA
# ------------------------------------------------------------------------------
EXISTING_HA_SECRET=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_HA_SECRET=$(grep -E "^HA_SECRET_KEY=" "$ENV_FILE" | cut -d '=' -f2- || true)
fi

if [[ -n "$EXISTING_HA_SECRET" && "$EXISTING_HA_SECRET" != "change-this-to-a-very-secure-random-32-byte-hex-string" ]]; then
    HA_SECRET_KEY="$EXISTING_HA_SECRET"
    log_ok "Используется существующий HA_SECRET_KEY из $ENV_FILE"
else
    # Генерация надежного случайного ключа
    GEN_SECRET=$(openssl rand -hex 16 2>/dev/null || date +%s%N | md5sum | head -c 32)
    echo -e "Секретный ключ межсерверной авторизации HA должен быть ${BOLD}одинаковым${NC} на Main и Backup."
    read -r -p "Введите общий HA_SECRET_KEY или нажмите Enter для генерации [$GEN_SECRET]: " INPUT_HA_SECRET
    HA_SECRET_KEY="${INPUT_HA_SECRET:-$GEN_SECRET}"
fi

# ------------------------------------------------------------------------------
# Telegram Bot
# ------------------------------------------------------------------------------
log_header "4. Настройка Telegram Bot"

EXISTING_BOT_TOKEN=""
EXISTING_CHAT_ID=""
EXISTING_USER_ID=""

if [[ -f "$ENV_FILE" ]]; then
    EXISTING_BOT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$ENV_FILE" | cut -d '=' -f2- || true)
    EXISTING_CHAT_ID=$(grep -E "^TELEGRAM_CHAT_ID=" "$ENV_FILE" | cut -d '=' -f2- || true)
    EXISTING_USER_ID=$(grep -E "^TELEGRAM_ADMIN_USER_ID=" "$ENV_FILE" | cut -d '=' -f2- || true)
fi

# 1. Bot Token
while true; do
    if [[ -n "$EXISTING_BOT_TOKEN" ]]; then
        read -r -p "Telegram Bot Token [Сохранить текущий]: " INPUT_BOT_TOKEN
        BOT_TOKEN="${INPUT_BOT_TOKEN:-$EXISTING_BOT_TOKEN}"
    else
        read -r -p "Введите Telegram Bot Token (от @BotFather): " BOT_TOKEN
    fi

    BOT_TOKEN=$(echo "$BOT_TOKEN" | tr -d '[:space:]')
    if [[ -z "$BOT_TOKEN" ]]; then
        log_err "Токен не может быть пустым."
        continue
    fi

    log_info "Проверка токена через Telegram Bot API (getMe)..."
    GET_ME_RESP=$(curl -s -m 5 "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || echo "")
    if echo "$GET_ME_RESP" | grep -q '"ok":true'; then
        BOT_USERNAME=$(echo "$GET_ME_RESP" | grep -oP '(?<="username":")[^"]+' || echo "bot")
        log_ok "Токен проверен успешно! Имя бота: @${BOT_USERNAME}"

        # Проверка Webhook (getWebhookInfo)
        WEBHOOK_INFO=$(curl -s -m 5 "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" || echo "")
        WEBHOOK_URL=$(echo "$WEBHOOK_INFO" | grep -oP '(?<="url":")[^"]+' || echo "")
        if [[ -n "$WEBHOOK_URL" ]]; then
            log_warn "Установлен сторонний Webhook: $WEBHOOK_URL"
            log_warn "Согласно ТЗ, существующий Webhook не удаляется автоматически."
        fi
        break
    else
        log_err "Ошибка валидации токена: ответ Telegram API: $GET_ME_RESP"
        read -r -p "Повторить ввод токена? [Y/n]: " RETRY_TOKEN
        if [[ "$RETRY_TOKEN" =~ ^[Nn] ]]; then
            log_warn "Продолжаем с указанным токеном без верификации."
            break
        fi
    fi
done

# 2. Chat ID
while true; do
    PROMPT_CHAT="Telegram Chat ID для алертов"
    if [[ -n "$EXISTING_CHAT_ID" ]]; then
        PROMPT_CHAT="$PROMPT_CHAT [$EXISTING_CHAT_ID]"
    fi
    read -r -p "$PROMPT_CHAT: " INPUT_CHAT_ID
    CHAT_ID="${INPUT_CHAT_ID:-$EXISTING_CHAT_ID}"
    CHAT_ID=$(echo "$CHAT_ID" | tr -d '[:space:]')
    if [[ -n "$CHAT_ID" ]]; then
        log_ok "Telegram Chat ID: $CHAT_ID"
        break
    else
        log_err "Chat ID не может быть пустым."
    fi
done

# 3. User ID владельца (опционально)
PROMPT_USER="Telegram User ID (Опционально, нажмите Enter для доступа по Chat ID)"
if [[ -n "$EXISTING_USER_ID" ]]; then
    PROMPT_USER="Telegram User ID [$EXISTING_USER_ID]"
fi
read -r -p "$PROMPT_USER: " INPUT_USER_ID
ADMIN_USER_ID="${INPUT_USER_ID:-${EXISTING_USER_ID:-0}}"
ADMIN_USER_ID=$(echo "$ADMIN_USER_ID" | tr -d '[:space:]')
[[ -z "$ADMIN_USER_ID" ]] && ADMIN_USER_ID="0"
log_ok "Telegram Admin User ID: $ADMIN_USER_ID"

# ------------------------------------------------------------------------------
# Настройка Beszel Hub
# ------------------------------------------------------------------------------
log_header "5. Настройка интеграции Beszel"
if [[ "$NODE_ROLE" == "PRIMARY" ]]; then
    DEFAULT_BESZEL_MAIN="http://${TAILSCALE_IP}:29001"
    DEFAULT_BESZEL_BACKUP="http://${PEER_TAILSCALE_IP}:29000"
else
    DEFAULT_BESZEL_MAIN="http://${PEER_TAILSCALE_IP}:29001"
    DEFAULT_BESZEL_BACKUP="http://${TAILSCALE_IP}:29000"
fi

read -r -p "URL Main Beszel Hub [$DEFAULT_BESZEL_MAIN]: " INPUT_BESZEL_MAIN
BESZEL_MAIN_URL="${INPUT_BESZEL_MAIN:-$DEFAULT_BESZEL_MAIN}"

read -r -p "URL Backup Beszel Hub [$DEFAULT_BESZEL_BACKUP]: " INPUT_BESZEL_BACKUP
BESZEL_BACKUP_URL="${INPUT_BESZEL_BACKUP:-$DEFAULT_BESZEL_BACKUP}"

# Проверка доступности Beszel Hub
log_info "Проверка доступности Beszel Hub..."
if curl -s -m 3 "${BESZEL_MAIN_URL}/api/health" &>/dev/null; then
    log_ok "Main Beszel Hub доступен (${BESZEL_MAIN_URL})"
else
    log_warn "Main Beszel Hub не ответил на /api/health (возможно, агент запускается)."
fi

read -r -p "Требуется ли логин/пароль для Beszel PocketBase? [y/N]: " BESZEL_AUTH_NEEDED
BESZEL_USERNAME=""
BESZEL_PASSWORD=""
if [[ "$BESZEL_AUTH_NEEDED" =~ ^[Yy] ]]; then
    read -r -p "Beszel Username/Email: " BESZEL_USERNAME
    read -r -s -p "Beszel Password: " BESZEL_PASSWORD
    echo ""
fi

# ------------------------------------------------------------------------------
# Настройка Uptime Kuma
# ------------------------------------------------------------------------------
log_header "6. Настройка интеграции Uptime Kuma"
echo -e "Интеграция с Uptime Kuma опциональна (сбор метрик доступности)."
read -r -p "Включить интеграцию Uptime Kuma? [Y/n]: " KUMA_ENABLE_CHOICE
if [[ "$KUMA_ENABLE_CHOICE" =~ ^[Nn] ]]; then
    UPTIME_KUMA_ENABLED="false"
    UPTIME_KUMA_URL="http://${TAILSCALE_IP}:29000"
    UPTIME_KUMA_API_KEY=""
else
    UPTIME_KUMA_ENABLED="true"
    DEFAULT_KUMA_URL="http://${TAILSCALE_IP}:29000"
    if [[ "$NODE_ROLE" == "STANDBY" ]]; then
        DEFAULT_KUMA_URL="http://${PEER_TAILSCALE_IP}:29000"
    fi
    read -r -p "URL Uptime Kuma [$DEFAULT_KUMA_URL]: " INPUT_KUMA_URL
    UPTIME_KUMA_URL="${INPUT_KUMA_URL:-$DEFAULT_KUMA_URL}"

    read -r -p "API ключ / токен Uptime Kuma (оставьте пустым для базового /metrics): " UPTIME_KUMA_API_KEY
    UPTIME_KUMA_API_KEY="${UPTIME_KUMA_API_KEY:-}"
fi

# ------------------------------------------------------------------------------
# Генерация .env и сохранение бэкапа
# ------------------------------------------------------------------------------
log_header "7. Генерация конфигурации .env"

if [[ -f "$ENV_FILE" ]]; then
    BACKUP_ENV="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$ENV_FILE" "$BACKUP_ENV"
    log_ok "Создана резервная копия существующего $ENV_FILE -> $BACKUP_ENV"
fi

cat > "$ENV_FILE" <<EOF
# Автоматически сгенерировано deploy.sh ($(date -u +"%Y-%m-%dT%H:%M:%SZ"))
NODE_ROLE=${NODE_ROLE}
NODE_NAME=${NODE_NAME}
TAILSCALE_IP=${TAILSCALE_IP}
PEER_NODE_NAME=${PEER_NODE_NAME}
PEER_TAILSCALE_IP=${PEER_TAILSCALE_IP}

HA_PORT=${HA_PORT}
HA_HOST=0.0.0.0
HA_SECRET_KEY=${HA_SECRET_KEY}
HA_HEARTBEAT_INTERVAL_SEC=5.0
HA_FAILOVER_TIMEOUT_SEC=30.0
HA_RECOVERY_TIMEOUT_SEC=30.0
HA_LEASE_DURATION_SEC=15.0

TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_CHAT_ID=${CHAT_ID}
TELEGRAM_ADMIN_USER_ID=${ADMIN_USER_ID}
RATE_LIMIT_COMMANDS_PER_MIN=30

BESZEL_MAIN_URL=${BESZEL_MAIN_URL}
BESZEL_BACKUP_URL=${BESZEL_BACKUP_URL}
BESZEL_USERNAME=${BESZEL_USERNAME}
BESZEL_PASSWORD=${BESZEL_PASSWORD}
BESZEL_AUTH_TOKEN=

UPTIME_KUMA_ENABLED=${UPTIME_KUMA_ENABLED}
UPTIME_KUMA_URL=${UPTIME_KUMA_URL}
UPTIME_KUMA_API_KEY=${UPTIME_KUMA_API_KEY}

DOCKER_SOCKET_PATH=/var/run/docker.sock
DOCKER_MONITOR_ENABLED=true

DATA_DIR=/app/data
LOG_LEVEL=INFO
LOG_FORMAT_JSON=true
EOF

# Защита прав на .env и каталог данных
if [ -n "${SUDO_USER:-}" ]; then
    chown "$SUDO_USER:$SUDO_USER" "$ENV_FILE" 2>/dev/null || true
fi
chmod 600 "$ENV_FILE" 2>/dev/null || chmod 644 "$ENV_FILE" 2>/dev/null || true
mkdir -p "$DATA_DIR"
chown -R 1000:1000 "$DATA_DIR" 2>/dev/null || true
chmod 775 "$DATA_DIR" 2>/dev/null || chmod 777 "$DATA_DIR"
log_ok "Файл $ENV_FILE сформирован, права на $DATA_DIR настроены для контейнера."

# ------------------------------------------------------------------------------
# Сборка и развертывание Docker
# ------------------------------------------------------------------------------
log_header "8. Сборка и запуск Docker контейнеров"

echo -e "Будет собран и запущен проект ${BOLD}bilionid-ha-sentinel${NC} через Docker Compose."
docker compose build --pull || docker compose build
docker compose up -d

log_info "Ожидание инициализации и healthcheck (до 25 секунд)..."
HEALTH_OK=false
for i in {1..25}; do
    if curl -s -m 2 "http://127.0.0.1:${HA_PORT}/health" 2>/dev/null | grep -q '"status":"ok"'; then
        HEALTH_OK=true
        break
    fi
    sleep 1
done

echo ""
if [[ "$HEALTH_OK" == "true" ]]; then
    log_ok "Сервис успешно запущен и прошел проверку healthcheck!"
else
    log_warn "Сервис запустился, но healthcheck еще не вернул 'ok'. Проверьте логи командой: ./deploy.sh --logs"
fi

# ------------------------------------------------------------------------------
# Итоговый отчет
# ------------------------------------------------------------------------------
log_header "9. Развертывание завершено успешно!"
echo -e "${BOLD}${GREEN}"
echo "======================================================================"
echo "    🎉 BilioniD HA Sentinel успешно развернут на сервере $NODE_NAME!   "
echo "======================================================================"
echo -e "${NC}"
echo -e "Параметры текущего узла:"
echo -e "  • Роль:             ${BOLD}${NODE_ROLE}${NC}"
echo -e "  • Имя узла:         ${BOLD}${NODE_NAME}${NC}"
echo -e "  • Tailscale IP:     ${BOLD}${TAILSCALE_IP}${NC}"
echo -e "  • HA Port:          ${BOLD}${HA_PORT}${NC}"
echo -e "  • Парный узел:      ${BOLD}${PEER_NODE_NAME} (${PEER_TAILSCALE_IP})${NC}"
echo -e "  • Polling Telegram: ${BOLD}$([[ "$NODE_ROLE" == "PRIMARY" ]] && echo "АКТИВЕН (Лидер)" || echo "ОСТАНОВЛЕН (Резерв)")${NC}"
echo ""
echo -e "Полезные команды управления:"
echo -e "  • Проверить статус:  ${CYAN}./deploy.sh --status${NC}"
echo -e "  • Просмотр логов:    ${CYAN}./deploy.sh --logs${NC}"
echo -e "  • Создать бэкап:     ${CYAN}./deploy.sh --backup-config${NC}"
echo -e "  • Удалить сервис:    ${CYAN}./uninstall.sh${NC}"
echo ""
if [[ "$NODE_ROLE" == "PRIMARY" ]]; then
    echo -e "${YELLOW}Следующий шаг:${NC} Перейдите на резервный сервер (Backup),"
    echo -e "скопируйте туда папку ${BOLD}bilionid-ha-sentinel${NC} и запустите ${CYAN}bash deploy.sh${NC},"
    echo -e "выбрав роль ${BOLD}2) BACKUP / STANDBY${NC} и указав этот же токен бота и секретный ключ."
fi
echo ""
