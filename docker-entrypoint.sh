#!/bin/sh
set -e

# If running as root, fix volume permissions and drop privileges to appuser
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data
    chown -R appuser:appuser /app/data 2>/dev/null || true
    chmod 775 /app/data 2>/dev/null || chmod 777 /app/data 2>/dev/null || true

    # If docker socket is mounted, ensure socket access
    if [ -S /var/run/docker.sock ] || [ -e /var/run/docker.sock ]; then
        DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || true)
        if [ -n "$DOCKER_GID" ]; then
            if [ "$DOCKER_GID" = "0" ]; then
                usermod -aG root appuser 2>/dev/null || true
            else
                if ! getent group "$DOCKER_GID" >/dev/null 2>&1; then
                    groupadd -g "$DOCKER_GID" hostdocker 2>/dev/null || true
                fi
                usermod -aG "$DOCKER_GID" appuser 2>/dev/null || true
            fi
        fi
        chmod 666 /var/run/docker.sock 2>/dev/null || true

        # Verify appuser can access docker socket. If not, stay as root for docker access
        if ! gosu appuser test -r /var/run/docker.sock 2>/dev/null; then
            exec "$@"
        fi
    fi

    exec gosu appuser "$@"
fi

exec "$@"
