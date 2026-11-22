#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")/.."

declare -A settings=()

read_setting() {
    local key=$1 prompt=$2 pattern=$3 mode=${4:-} default=${5:-} value
    local -a flags=(-r)
    if [[ $mode == secret ]]; then
        flags+=(-s)
    fi
    while true; do
        printf '%s: ' "$prompt" >&2
        if ! IFS= read "${flags[@]}" value; then
            printf '\nНастройка прервана.\n' >&2
            exit 1
        fi
        if [[ $mode == secret ]]; then
            printf '\n' >&2
        fi
        value=${value:-$default}
        if [[ $value =~ $pattern ]]; then
            settings[$key]=$value
            return
        fi
        printf 'Проверьте значение и попробуйте ещё раз.\n' >&2
    done
}

ask_yes() {
    read_setting choice "$1 [y/N]" '^[YyNn]$' '' n
    [[ ${settings[choice]} == [Yy] ]]
}

read_private_ip() {
    local key=$1 label=$2
    local octet='(0|[1-9][0-9]?|1[0-9][0-9]|2[0-4][0-9]|25[0-5])'
    local subnet='(172\.(1[6-9]|2[0-9]|3[01])|192\.168|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7]))'
    read_setting "$key" "$label" "^($subnet\.$octet\.$octet|10\.$octet\.$octet\.$octet)$"
}

read_beszel_auth() {
    local label=$1 prefix=$2
    if ask_yes "Нужна авторизация для $label?"; then
        read_setting "${prefix}_AUTH_TOKEN" 'Токен (Enter для входа по логину)' '^.*$' secret
        if [[ -z ${settings[${prefix}_AUTH_TOKEN]} ]]; then
            read_setting "${prefix}_USERNAME" 'Логин' '^.+$'
            read_setting "${prefix}_PASSWORD" 'Пароль' '^.+$' secret
        fi
    fi
}

printf 'Настройка BilioniD HA Bot\n'
if [[ -e .env ]]; then
    printf 'Текущий .env будет сохранён в резервной копии.\n'
fi

read_setting role 'Узел: 1 — основной, 2 — резервный [1]' '^[12]$' '' 1
settings[NODE_ROLE]=PRIMARY
if [[ ${settings[role]} == 2 ]]; then
    settings[NODE_ROLE]=STANDBY
    settings[NODE_NAME]=backup
    settings[PEER_NODE_NAME]=main
    settings[HA_WATCH_PRIMARY]=true
fi

read_setting TELEGRAM_BOT_TOKEN 'Токен Telegram от BotFather (ввод скрыт)' '^[0-9]+:[A-Za-z0-9_-]+$' secret
read_setting TELEGRAM_CHAT_ID 'ID группы Telegram (со знаком минус)' '^-[1-9][0-9]*$'

if [[ ${settings[NODE_ROLE]} == STANDBY ]] || ask_yes 'Подключить резервный узел?'; then
    settings[HA_PEER_ENABLED]=true
    read_private_ip TAILSCALE_IP 'Приватный IPv4 этого сервера (например, Tailscale)'
    while true; do
        read_private_ip PEER_TAILSCALE_IP 'Приватный IPv4 второго сервера'
        [[ ${settings[TAILSCALE_IP]} != "${settings[PEER_TAILSCALE_IP]}" ]] && break
        printf 'Укажите адрес второго сервера.\n' >&2
    done
    settings[HA_BIND_IP]=${settings[TAILSCALE_IP]}
    if [[ ${settings[NODE_ROLE]} == PRIMARY ]]; then
        read_setting HA_SECRET_KEY 'Общий секрет HA от 32 символов (Enter для генерации)' '^(.{32,})?$' secret
        if [[ -z ${settings[HA_SECRET_KEY]} ]]; then
            settings[HA_SECRET_KEY]=$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')
            printf 'Общий секрет будет сохранён в .env. Он понадобится при настройке второго узла.\n'
        fi
    else
        read_setting HA_SECRET_KEY 'Общий секрет HA из настроек основного сервера' '^.{32,}$' secret
    fi
fi

optional_url='^(https?://[^[:space:]]+)?$'
if ask_yes 'Настроить интеграции мониторинга?'; then
    read_setting BESZEL_MAIN_URL 'URL основного Beszel (Enter пропускает Beszel)' "$optional_url"
    if [[ -n ${settings[BESZEL_MAIN_URL]} ]]; then
        settings[BESZEL_ENABLED]=true
        read_beszel_auth 'основного Beszel' BESZEL
        read_setting BESZEL_BACKUP_URL 'URL резервного Beszel (Enter пропускает)' "$optional_url"
        if [[ -n ${settings[BESZEL_BACKUP_URL]} ]]; then
            read_beszel_auth 'резервного Beszel' BESZEL_BACKUP
        fi
    fi
    read_setting UPTIME_KUMA_URL 'URL Uptime Kuma (Enter пропускает)' "$optional_url"
    if [[ -n ${settings[UPTIME_KUMA_URL]} ]]; then
        settings[UPTIME_KUMA_ENABLED]=true
        read_setting UPTIME_KUMA_API_KEY 'Ключ Prometheus Uptime Kuma (Enter, если ключ не нужен)' '^.*$' secret
        read_setting UPTIME_KUMA_STATUS_PAGE_SLUG 'Имя страницы статуса Kuma [default]' '^.*$' '' default
        read_setting UPTIME_KUMA_WEBHOOK_SECRET 'Секрет webhook Kuma от 32 символов (Enter оставляет опрос)' '^(.{32,})?$' secret
    fi
    read_setting ZABBIX_URL 'URL Zabbix (Enter пропускает)' "$optional_url"
    if [[ -n ${settings[ZABBIX_URL]} ]]; then
        read_setting ZABBIX_API_TOKEN 'Токен API Zabbix' '^.+$' secret
        read_setting ZABBIX_WEBHOOK_SECRET 'Секрет webhook Zabbix от 32 символов (Enter пропускает)' '^(.{32,})?$' secret
    fi
    read_setting DOCKER_PROXY_URL 'HTTP(S)-адрес прокси Docker API (Enter пропускает)' "$optional_url"
    if [[ -n ${settings[DOCKER_PROXY_URL]} ]]; then
        settings[DOCKER_MONITOR_ENABLED]=true
    fi
fi

temp=$(mktemp ./.env.setup.XXXXXX)
trap 'rm -f -- "$temp"' EXIT
while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ ^([A-Z_][A-Z_0-9]*)= ]]; then
        key=${BASH_REMATCH[1]}
        if [[ -v settings[$key] ]]; then
            value=${settings[$key]}
            value=${value//\\/\\\\}
            value=${value//\"/\\\"}
            value=${value//\$/\$\$}
            printf '%s="%s"\n' "$key" "$value"
            continue
        fi
    fi
    printf '%s\n' "$line"
done < .env.example > "$temp"

if [[ -e .env ]]; then
    backup=$(mktemp ./.env.backup.XXXXXX)
    install -m 0600 -- .env "$backup"
    printf 'Предыдущие настройки сохранены в %s.\n' "$backup"
fi
mv -fT -- "$temp" .env
printf 'Настройки записаны в .env.\n'
