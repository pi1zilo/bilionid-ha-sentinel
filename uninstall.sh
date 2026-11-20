#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"

if [[ ! -f .env ]]; then
    printf '.env was not found. Check the project directory.\n' >&2
    exit 1
fi

read -r -p 'Back up configuration and remove this Compose project? [y/N] ' answer
if [[ "$answer" =~ ^[yY]$ ]]; then
    bash deploy.sh --backup-config
    docker compose --env-file .env down
    printf 'Compose project removed. Configuration and data are in this directory.\n'
fi
