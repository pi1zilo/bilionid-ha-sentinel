#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"

usage() {
    printf '%s\n' \
        'bash deploy.sh --init              Create .env from the sample' \
        'bash deploy.sh --setup             Configure interactively and start the bot' \
        'bash deploy.sh --check             Validate Docker and Compose configuration' \
        'bash deploy.sh                     Build and start (configure on first run)' \
        'bash deploy.sh --enable-docker-monitoring  Enable local container monitoring and start' \
        'bash deploy.sh --status            Show container status and readiness' \
        'bash deploy.sh --logs              Follow service logs' \
        'bash deploy.sh --backup-config     Back up .env and a consistent SQLite snapshot' \
        'bash deploy.sh --update            Deploy new commits from origin on the current branch' \
        'bash deploy.sh --install-update-timer [HH:MM]  Schedule daily updates (Moscow time)'
}

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

case "${1:-}" in
    ''|--init|--setup|--enable-docker-monitoring|--update)
        exec {deploy_lock}>>.deploy.lock
        flock -n "$deploy_lock" || fail 'Another deployment is already running.' ;;
esac

case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --init)
        if [[ -e .env ]]; then
            printf '.env already exists.\n'
        else
            install -m 0600 .env.example .env
            printf 'Created .env. Open it to configure the node before deployment.\n'
        fi
        exit 0 ;;
    --setup)
        bash scripts/setup-env.sh ;;
    '')
        if [[ ! -f .env && -t 0 ]]; then
            bash scripts/setup-env.sh
        fi ;;
    --check|--status|--logs|--backup-config|--enable-docker-monitoring|--update|--install-update-timer) ;;
    *) usage >&2; exit 2 ;;
esac

if [[ ! -f .env ]]; then
    printf 'Run bash deploy.sh --setup to configure the bot.\n' >&2
    exit 1
fi

compose=(docker compose --env-file .env)

prepare_data_owner() {
    if [[ ! -d data ]]; then
        if (( EUID == 0 )); then
            install -d -m 0700 -o 1000 -g 1000 data
        else
            install -d -m 0700 data
        fi
    fi
    local actual_uid actual_gid
    actual_uid=$(stat -c '%u' data) || return
    actual_gid=$(stat -c '%g' data) || return
    if [[ "$actual_uid" == 0 ]]; then
        printf '%s/data is owned by root. Set its owner to the non-root UID that should run the bot.\n' "$PWD" >&2
        return 1
    fi
    export APP_UID="$actual_uid" APP_GID="$actual_gid"
}

start_bot() {
    "${compose[@]}" up -d --wait --wait-timeout 60 "$@" || return
    local attempt
    for attempt in {1..30}; do
        if "${compose[@]}" exec -T bot python -m app.core.healthcheck /ready; then
            printf 'Bot is ready.\n'
            return 0
        fi
        sleep 2
    done
    printf 'Bot is running but not ready. Use bash deploy.sh --logs.\n' >&2
    return 1
}

case "${1:-}" in
    --update)
        source scripts/auto-update.sh
        run_update
        exit ;;
    --install-update-timer)
        (( $# <= 2 )) || fail 'Usage: bash deploy.sh --install-update-timer [HH:MM]'
        source scripts/auto-update.sh
        install_update_timer "${2:-04:00}"
        exit ;;
    --status)
        "${compose[@]}" ps
        "${compose[@]}" exec -T bot python -m app.core.healthcheck /ready
        exit ;;
    --logs)
        "${compose[@]}" logs -f --tail=100
        exit ;;
    --backup-config)
        snapshot=$(mktemp -d)
        trap 'rm -f -- "$snapshot/.env" "$snapshot/state.db"; rmdir -- "$snapshot"' EXIT
        install -m 0600 .env "$snapshot/.env"
        if [[ -f data/state.db ]]; then
            python3 - "$snapshot/state.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect("file:data/state.db?mode=ro", uri=True) as source:
    with sqlite3.connect(sys.argv[1]) as destination:
        source.backup(destination)
PY
        fi
        archive="bilionid-config-backup-$(date -u +%Y%m%dT%H%M%S)-$$.tar.gz"
        tar -czf "$archive" -C "$snapshot" .
        printf 'Backup saved: %s\n' "$archive"
        exit ;;
esac

docker info --format '{{.ServerVersion}}' >/dev/null
if [[ "${1:-}" == --enable-docker-monitoring ]]; then
    (
        temp=$(mktemp ./.env.docker-monitor.XXXXXX)
        trap 'rm -f -- "$temp"' EXIT
        awk '!/^[[:blank:]]*(export[[:blank:]]+)?(COMPOSE_PROFILES|DOCKER_MONITOR_ENABLED|DOCKER_PROXY_URL)[[:blank:]]*=/' .env > "$temp"
        printf '%s\n' 'COMPOSE_PROFILES=docker-monitor' 'DOCKER_MONITOR_ENABLED=true' \
            'DOCKER_PROXY_URL=http://docker-monitor-proxy:2375' >> "$temp"
        if ! cmp -s .env "$temp"; then
            backup=$(mktemp ./.env.backup.XXXXXX)
            cp -- .env "$backup"
            mv -fT -- "$temp" .env
            printf 'Previous settings saved in %s.\n' "$backup"
        fi
    )
fi
"${compose[@]}" config --quiet
if [[ "${1:-}" == --check ]]; then
    printf 'Docker and Compose configuration are ready.\n'
    exit 0
fi

chmod 0600 .env
prepare_data_owner
start_bot --build
if [[ "${1:-}" == --enable-docker-monitoring ]]; then
    "${compose[@]}" exec -T bot python - <<'PY'
import asyncio
from app.core.config import get_settings
from app.monitoring.docker.client import DockerMonitor

settings = get_settings()
docker = DockerMonitor(enabled=settings.DOCKER_MONITOR_ENABLED, proxy_url=settings.DOCKER_PROXY_URL)
containers = asyncio.run(docker.get_containers())
if not containers:
    raise SystemExit("Docker monitor returned no containers. Check: docker compose logs docker-monitor-proxy")
print(f"Docker monitor found {len(containers)} local containers.")
PY
fi
