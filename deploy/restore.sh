#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

usage() {
    printf 'Usage: %s --archive BACKUP.tar.gz --confirm [--confirm-database-target]\n' "$0" >&2
    exit 2
}

archive=
confirmed=false
database_target_confirmed=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive) [ "$#" -ge 2 ] || usage; archive=$2; shift 2 ;;
        --confirm) confirmed=true; shift ;;
        --confirm-database-target) database_target_confirmed=true; shift ;;
        *) usage ;;
    esac
done
[ "$confirmed" = true ] || die "restore is destructive; pass --confirm"
[ -n "$archive" ] || usage
[ -f "$archive" ] || die "backup archive is not a regular file: $archive"

require_root
require_install
acquire_lock
umask 077
stage=$(mktemp -d)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_env=

cleanup() {
    rm -rf "$stage"
    [ -z "$pg_env" ] || rm -f "$pg_env"
}
trap cleanup EXIT INT TERM

while IFS= read -r member; do
    case "/$member/" in
        /juraguard-backup/*) ;;
        *) die "archive contains an unexpected path: $member" ;;
    esac
    case "/$member/" in
        *'/../'*|*'/./'*) die "archive contains an unsafe path: $member" ;;
    esac
done < <(tar -tzf "$archive")

while IFS= read -r line; do
    case "${line:0:1}" in
        -|d) ;;
        *) die "archive contains links or special files" ;;
    esac
done < <(tar -tvzf "$archive")

tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$stage"
root="$stage/juraguard-backup"
[ -f "$root/config/juraguard.env" ] || die "archive has no Juraguard configuration"
[ -d "$root/state/data" ] || die "archive has no application data"
[ -f "$root/database-type" ] || die "archive has no database type marker"

current_database_url=$(config_value DATABASE_URL)
database_type=$(tr -d '\r\n' < "$root/database-type")
if [ "$database_type" = postgresql ]; then
    [ -f "$root/database.dump" ] || die "PostgreSQL backup has no database dump"
    archived_database_url=$(awk -F= '$1 == "DATABASE_URL" {sub(/^[^=]*=/, ""); print; exit}' "$root/config/juraguard.env")
    [ -n "$archived_database_url" ] || die "PostgreSQL backup has no DATABASE_URL"
    [ -n "$current_database_url" ] || die "configure the PostgreSQL restore destination in the current DATABASE_URL first"
    if [ "$archived_database_url" != "$current_database_url" ] && [ "$database_target_confirmed" != true ]; then
        die "current DATABASE_URL differs from the archive; verify the destination and pass --confirm-database-target"
    fi
    backup_image=$(awk -F= '$1 == "POSTGRES_BACKUP_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "$root/config/juraguard.env")
    backup_image=${backup_image:-postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297}
    validate_image "$backup_image"
elif [ "$database_type" = sqlite ]; then
    [ -z "$current_database_url" ] || die "SQLite backup cannot replace a configured PostgreSQL database"
else
    die "unsupported database type: $database_type"
fi

if [ "${JURAGUARD_SKIP_PRE_RESTORE_BACKUP:-false}" = true ]; then
    pre_restore=${JURAGUARD_EXISTING_BACKUP:-}
    [ -n "$pre_restore" ] || die "internal restore requires an existing backup"
else
    pre_restore=$("$INSTALL_DIR/bin/backup")
fi
"${COMPOSE[@]}" stop >/dev/null

if [ "$database_type" = postgresql ]; then
    pg_env=$(mktemp)
    chmod 600 "$pg_env"
    printf 'PGDATABASE=%s\n' "$current_database_url" > "$pg_env"
    docker run --rm --network host --env-file "$pg_env" \
        -v "$root/database.dump:/backup/database.dump:ro" "$backup_image" \
        sh -c 'exec pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$PGDATABASE" /backup/database.dump'
    rm -f "$pg_env"
    pg_env=
fi

restored_state="$STATE_DIR.restore-$timestamp"
app_owner=$(stat -c '%u:%g' "$STATE_DIR/data")
cp -a "$root/state" "$restored_state"
chmod 700 "$restored_state" "$restored_state/data" "$restored_state/traefik"
chown -R "$app_owner" "$restored_state/data"
mv "$STATE_DIR" "$STATE_DIR.pre-restore-$timestamp"
mv "$restored_state" "$STATE_DIR"
install -m 600 "$root/config/juraguard.env" "$CONFIG_FILE.restore"
mv "$CONFIG_FILE.restore" "$CONFIG_FILE"
set_config_value DATABASE_URL "$current_database_url"

"${COMPOSE[@]}" up -d
if ! wait_healthy 30; then
    "${COMPOSE[@]}" stop >/dev/null || true
    die "restored application did not become healthy; services stopped; recover from $pre_restore"
fi
trap - EXIT INT TERM
rm -rf "$stage"
printf 'Restore complete. Pre-restore backup: %s\nPrevious state: %s\n' \
    "$pre_restore" "$STATE_DIR.pre-restore-$timestamp"
