#!/usr/bin/env bash

set -Eeuo pipefail

INSTALL_DIR=${JURAGUARD_INSTALL_DIR:-/opt/juraguard}
CONFIG_FILE=${JURAGUARD_CONFIG_FILE:-/etc/juraguard/juraguard.env}
# These are consumed by scripts that source this shared file.
# shellcheck disable=SC2034
STATE_DIR=${JURAGUARD_STATE_DIR:-/var/lib/juraguard}
# shellcheck disable=SC2034
BACKUP_DIR=${JURAGUARD_BACKUP_DIR:-/var/backups/juraguard}
COMPOSE=(docker compose --env-file "$CONFIG_FILE" -f "$INSTALL_DIR/compose.yml")

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_root() {
    [ "$(id -u)" -eq 0 ] || die "run as root (use sudo)"
}

require_install() {
    [ -f "$CONFIG_FILE" ] || die "missing $CONFIG_FILE; run the installer first"
    [ -f "$INSTALL_DIR/compose.yml" ] || die "missing $INSTALL_DIR/compose.yml; run the installer first"
    command -v docker >/dev/null 2>&1 || die "Docker is not installed"
    docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is not installed"
}

acquire_lock() {
    [ "${JURAGUARD_LOCK_HELD:-}" != 1 ] || return 0
    command -v flock >/dev/null 2>&1 || die "flock is required"
    exec 9>/run/lock/juraguard.lock
    flock -n 9 || die "another Juraguard operation is running"
    export JURAGUARD_LOCK_HELD=1
}

config_value() {
    local key=$1
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$CONFIG_FILE"
}

set_config_value() {
    local key=$1 value=$2 temporary
    temporary=$(mktemp "${CONFIG_FILE}.XXXXXX")
    chmod 600 "$temporary"
    awk -F= -v key="$key" -v value="$value" '
        BEGIN {found = 0}
        $1 == key {print key "=" value; found = 1; next}
        {print}
        END {if (!found) print key "=" value}
    ' "$CONFIG_FILE" > "$temporary"
    chown --reference="$CONFIG_FILE" "$temporary"
    mv "$temporary" "$CONFIG_FILE"
}

validate_image() {
    case "$1" in
        -*|*[$'\t\r\n ']*|'') die "invalid image reference" ;;
    esac
}

resolve_image() {
    local requested=$1 resolved
    validate_image "$requested"
    docker pull "$requested" >/dev/null
    if [[ "$requested" == *@sha256:* ]]; then
        printf '%s\n' "$requested"
        return
    fi
    resolved=$(docker image inspect --format '{{index .RepoDigests 0}}' "$requested")
    [[ "$resolved" == *@sha256:* ]] || die "registry did not return a content digest for $requested"
    printf '%s\n' "$resolved"
}

wait_healthy() {
    local attempts=${1:-30} status
    while [ "$attempts" -gt 0 ]; do
        status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "$("${COMPOSE[@]}" ps -q juraguard)" 2>/dev/null || true)
        [ "$status" = healthy ] && return 0
        sleep 2
        attempts=$((attempts - 1))
    done
    return 1
}
