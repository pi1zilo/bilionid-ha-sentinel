update_branch() {
    local prefix branch
    prefix=$(git rev-parse --show-prefix 2>/dev/null) && [[ -z "$prefix" ]] \
        || fail 'Auto-update requires a Git checkout in the deployment directory.'
    branch=$(git symbolic-ref --quiet --short HEAD) || fail 'Check out the branch to deploy.'
    git remote get-url origin >/dev/null 2>&1 || fail 'Set the origin remote to your GitHub repository.'
    printf '%s\n' "$branch"
}

save_update_state() {
    printf '%s\n' "$current" "$candidate" "$previous_image_ref" "$1" > "$update_state.tmp"
    mv -fT -- "$update_state.tmp" "$update_state"
}

rollback_update() {
    local head
    local -a saved
    mapfile -t saved < "$update_state" || return
    if [[ ${#saved[@]} -ne 4 ]]; then
        printf 'Invalid update state: %s\n' "$update_state" >&2
        return 1
    fi
    head=$(git rev-parse HEAD) || return
    if [[ "$head" == "${saved[1]}" ]]; then
        git reset --keep "${saved[0]}" || return
    elif [[ "$head" != "${saved[0]}" ]]; then
        printf 'HEAD changed during the update. Check the Git checkout before retrying.\n' >&2
        return 1
    fi
    docker image tag "$rollback_image" "${saved[2]}" || return
    if [[ "${saved[3]}" == applying ]]; then
        prepare_data_owner || return
        start_bot --no-build --pull never --force-recreate || return
    fi
    rm -- "$update_state" || return
    printf 'Restored %s.\n' "${saved[0]}"
}

run_update() (
    branch=$(update_branch)
    update_state=$(git rev-parse --git-path bilionid-update.pending)
    project_hash=$(printf '%s' "$PWD" | sha256sum)
    rollback_image="bilionid-ha-bot:rollback-${project_hash:0:12}"

    if [[ -f "$update_state" ]]; then
        printf 'Recovering an interrupted update.\n'
        rollback_update
        exit
    fi

    current=$(git rev-parse HEAD)
    tracked_changes=$(git status --porcelain --untracked-files=no)
    [[ -z "$tracked_changes" ]] || fail 'Commit or restore local changes before updating.'

    candidate_ref=refs/bilionid/update-candidate
    GIT_TERMINAL_PROMPT=0 timeout 120s git fetch --no-tags --no-write-fetch-head origin \
        "+refs/heads/$branch:$candidate_ref" </dev/null
    candidate=$(git rev-parse "$candidate_ref^{commit}")
    if [[ "$current" == "$candidate" ]]; then
        printf 'Already up to date on %s.\n' "$branch"
        exit 0
    fi
    git merge-base --is-ancestor "$current" "$candidate" \
        || fail 'The remote branch cannot fast-forward this checkout.'
    tracked_runtime=$(git ls-tree --name-only "$candidate" -- .env data)
    [[ -z "$tracked_runtime" ]] || fail 'The new commit tracks .env or data/.'

    previous_container=$("${compose[@]}" ps --quiet --status running bot)
    if [[ -z "$previous_container" ]]; then
        printf 'Bot is stopped. Skipping the automatic update.\n'
        exit 0
    fi
    read -r previous_image_id previous_image_ref < <(
        docker inspect --format '{{.Image}} {{.Config.Image}}' "$previous_container"
    )
    prepare_data_owner
    docker image tag "$previous_image_id" "$rollback_image"
    save_update_state building
    trap 'if ! rollback_update; then printf "Rollback failed. Update state kept in %s.\n" "$update_state" >&2; fi' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    printf 'Updating %s from %s to %s.\n' "$branch" "$current" "$candidate"
    git -c merge.autoStash=false merge --ff-only --no-overwrite-ignore "$candidate"
    "${compose[@]}" config --quiet
    "${compose[@]}" build
    save_update_state applying
    start_bot --no-build --force-recreate
    rm -- "$update_state"
    trap - EXIT INT TERM
    printf 'Update complete: %s.\n' "$candidate"
)

install_update_timer() {
    local time="$1" unit_dir script_path
    [[ "$time" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || fail 'Use a time in HH:MM format.'
    update_branch >/dev/null
    systemctl --user show-environment >/dev/null || fail 'Start the systemd user manager before installing the timer.'

    script_path="$PWD/deploy.sh"
    [[ "$script_path" != *$'\n'* && "$script_path" != *$'\r'* ]] || fail 'Unsupported newline in deployment path.'
    script_path=${script_path//\\/\\\\}
    script_path=${script_path//\"/\\\"}
    script_path=${script_path//%/%%}
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p -- "$unit_dir"
    cat > "$unit_dir/bilionid-ha-update.service" <<EOF
[Unit]
Description=Update BilioniD HA Bot from GitHub

[Service]
Type=oneshot
ExecStart=:/bin/bash "$script_path" --update
EOF
    cat > "$unit_dir/bilionid-ha-update.timer" <<EOF
[Unit]
Description=Check GitHub for BilioniD HA Bot updates every day

[Timer]
OnCalendar=*-*-* $time:00 Europe/Moscow

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable bilionid-ha-update.timer
    systemctl --user restart bilionid-ha-update.timer
    printf 'Auto-update scheduled daily at %s Moscow time.\n' "$time"
}
