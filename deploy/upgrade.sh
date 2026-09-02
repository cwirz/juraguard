#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

usage() {
    printf 'Usage: %s --image REGISTRY/IMAGE:TAG\n' "$0" >&2
    exit 2
}

image=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --image) [ "$#" -ge 2 ] || usage; image=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ -n "$image" ] || usage

require_root
require_install
acquire_lock
old_image=$(config_value JURAGUARD_IMAGE)
backup=$("$INSTALL_DIR/bin/backup")
new_image=$(resolve_image "$image")
set_config_value JURAGUARD_IMAGE "$new_image"

if ! "${COMPOSE[@]}" up -d || ! wait_healthy 30; then
    set_config_value JURAGUARD_IMAGE "$old_image"
    "${COMPOSE[@]}" stop >/dev/null || true
    if JURAGUARD_SKIP_PRE_RESTORE_BACKUP=true JURAGUARD_EXISTING_BACKUP="$backup" \
        "$INSTALL_DIR/bin/restore" --archive "$backup" --confirm; then
        die "upgrade health check failed; pre-upgrade image and data restored from $backup"
    fi
    die "upgrade and automatic restore failed; services remain stopped; recover from $backup"
fi

printf 'Upgraded to %s\nBackup: %s\n' "$new_image" "$backup"
