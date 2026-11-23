#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# != 2 || $EUID != 0 || ! $2 =~ ^(PRIMARY|STANDBY)$ ]]; then
    printf 'Usage: sudo bash scripts/replace-live.sh /absolute/path/to/ha-bot PRIMARY|STANDBY\n' >&2
    exit 2
fi

release=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
target=$(realpath -e -- "$1")
expected_role=$2
if [[ $target == / || $target == "$release" || ! -f $target/.env || ! -f $target/docker-compose.yml ]]; then
    printf 'The target must be the existing HA Bot installation.\n' >&2
    exit 2
fi
if [[ -L $target/data || -L $target/.env ]]; then
    printf 'This installer expects .env and data inside the installation directory.\n' >&2
    exit 2
fi

compose=(env -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME docker compose --env-file .env)
if ! current=$(cd -- "$target" && "${compose[@]}" -f docker-compose.yml config --format json); then
    printf 'Could not read the existing Compose configuration.\n' >&2
    exit 1
fi
role=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["bot"]["environment"].get("NODE_ROLE", "PRIMARY"))' <<< "$current")
polling=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["bot"]["environment"].get("TELEGRAM_POLLING_ENABLED", ""))' <<< "$current")
unset current
polling=${polling,,}
if [[ $role != "$expected_role" || ( $role == STANDBY && $polling =~ ^(1|true|t|yes|y|on)$ ) ||
      ( $role == PRIMARY && $polling =~ ^(0|false|f|no|n|off)$ ) ]]; then
    printf 'Unexpected polling assignment on this VPS. Verify /ha on both nodes before replacing either.\n' >&2
    exit 1
fi

parent=$(dirname -- "$target")
stage=$(mktemp -d -- "$parent/.ha-bot-unified.XXXXXX")
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
previous="${target}.previous-$stamp"
failed="${target}.failed-$stamp"
trap 'if [[ -d $stage ]]; then printf "Staged release: %s\n" "$stage"; fi' EXIT

cp -a -- "$release/." "$stage/"
chown -R --reference="$target" -- "$stage"
chmod --reference="$target" -- "$stage"
# The separate Docker proxy reads this bind mount without DAC override capabilities.
chmod a+r -- "$stage/app/monitoring/docker/proxy.py"
cp -p -- "$target/.env" "$stage/.env"

machine=$(cat /etc/machine-id)
journal="/var/log/journal/$machine"
[[ -d $journal ]] || journal="/run/log/journal/$machine"
journal_gid=$(getent group systemd-journal | cut -d: -f3)
if [[ ! -d $journal || ! $journal_gid =~ ^[0-9]+$ ]]; then
    printf 'Could not find the server journal and the systemd-journal group.\n' >&2
    exit 1
fi

cd -- "$target"
files=$("${compose[@]}" -f docker-compose.yml config --environment | sed -n 's/^COMPOSE_FILE=//p')
files=${files:-docker-compose.yml}
case ":$files:" in
    *:docker-compose.server-logs.yml:*) ;;
    *) files+=":docker-compose.server-logs.yml" ;;
esac

cd -- "$stage"
next=$(mktemp .env.install.XXXXXX)
awk '!/^[[:blank:]]*(export[[:blank:]]+)?(COMPOSE_FILE|SERVER_JOURNAL_DIR|SERVER_JOURNAL_GID|HA_AUTO_FAILOVER|IMAGE_TAG)[[:blank:]]*=/' .env > "$next"
printf '\nCOMPOSE_FILE=%s\nSERVER_JOURNAL_DIR=%s\nSERVER_JOURNAL_GID=%s\nHA_AUTO_FAILOVER=false\nIMAGE_TAG=unified-%s\n' \
    "$files" "$journal" "$journal_gid" "$stamp" >> "$next"
chown --reference=.env -- "$next"
mv -f -- "$next" .env

printf 'Building the unified version before stopping the old bot.\n'
"${compose[@]}" config --quiet
"${compose[@]}" build bot
data_owner=1000:1000
if [[ -d $target/data ]]; then
    data_owner=$(stat -c '%u:%g' -- "$target/data")
fi
if [[ $data_owner == 0:* ]]; then
    printf 'Existing data must be owned by the non-root UID running the bot.\n' >&2
    exit 1
fi
journal_check='import asyncio; from app.monitoring.server_logs import read_server_journal; assert asyncio.run(read_server_journal(1)), "Empty system journal"; print("System journal read OK")'
docker run --rm --network none --user "$data_owner" --group-add "$journal_gid" \
    --mount "type=bind,src=$journal,dst=/host-journal,readonly" --entrypoint python \
    "bilionid-ha-bot:unified-$stamp" -c "$journal_check"

printf 'Stopping the old Compose project.\n'
cd -- "$target"
"${compose[@]}" down
if [[ -d data ]] && ! cp -a -- data "$stage/data"; then
    "${compose[@]}" up -d
    exit 1
fi
cd -- "$parent"
if ! mv -- "$target" "$previous"; then
    (cd -- "$target" && "${compose[@]}" up -d)
    exit 1
fi
if ! mv -- "$stage" "$target"; then
    mv -- "$previous" "$target"
    (cd -- "$target" && "${compose[@]}" up -d)
    exit 1
fi

cd -- "$target"
deploy=(env -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME bash deploy.sh)
if "${deploy[@]}" && "${deploy[@]}" --status &&
    "${compose[@]}" exec -T bot python -c "$journal_check"; then
    printf 'Unified bot is running (%s). Previous installation: %s\n' "$expected_role" "$previous"
else
    printf 'New deployment failed. Restoring the previous version.\n' >&2
    "${compose[@]}" down
    cd -- "$parent"
    mv -- "$target" "$failed"
    mv -- "$previous" "$target"
    (cd -- "$target" && "${compose[@]}" up -d)
    exit 1
fi
