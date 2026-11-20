#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"

usage() {
    printf '%s\n' \
        'bash deploy.sh --init              Create .env from the sample' \
        'bash deploy.sh --check             Validate Docker and Compose configuration' \
        'bash deploy.sh                     Build and start the bot' \
        'bash deploy.sh --status            Show container status and readiness' \
        'bash deploy.sh --logs              Follow service logs' \
        'bash deploy.sh --backup-config     Back up .env and a consistent SQLite snapshot'
}

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
    ''|--check|--status|--logs|--backup-config) ;;
    *) usage >&2; exit 2 ;;
esac

if [[ ! -f .env ]]; then
    printf 'Run bash deploy.sh --init and configure .env first.\n' >&2
    exit 1
fi

compose=(docker compose --env-file .env)

case "${1:-}" in
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
"${compose[@]}" config --quiet
if [[ "${1:-}" == --check ]]; then
    printf 'Docker and Compose configuration are ready.\n'
    exit 0
fi

chmod 0600 .env
install -d -m 0700 data
actual_uid=$(stat -c '%u' data)
actual_gid=$(stat -c '%g' data)
export APP_UID="${APP_UID:-$actual_uid}" APP_GID="${APP_GID:-$actual_gid}"
if [[ "$APP_UID" == 0 || "$APP_UID" != "$actual_uid" ]]; then
    printf 'Set APP_UID to the non-root owner of %s/data (current UID %s).\n' "$PWD" "$actual_uid" >&2
    exit 1
fi

"${compose[@]}" up -d --build --wait --wait-timeout 60
for attempt in {1..30}; do
    if "${compose[@]}" exec -T bot python -m app.core.healthcheck /ready; then
        printf 'Bot is ready.\n'
        exit 0
    fi
    sleep 2
done
printf 'Bot is running but not ready. Use bash deploy.sh --logs.\n' >&2
exit 1
