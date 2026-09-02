#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

require_root
require_install
acquire_lock
umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$BACKUP_DIR/juraguard-$timestamp.tar.gz"
temporary="$archive.tmp"
if [ -e "$archive" ] || [ -e "$temporary" ]; then
    die "backup already exists for timestamp $timestamp"
fi
stage=$(mktemp -d)
was_running=false
env_file=

cleanup() {
    rm -rf "$stage" "$temporary"
    [ -z "$env_file" ] || rm -f "$env_file"
    if [ "$was_running" = true ]; then
        "${COMPOSE[@]}" up -d >/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if [ -n "$("${COMPOSE[@]}" ps --status running -q 2>/dev/null)" ]; then
    was_running=true
    "${COMPOSE[@]}" stop >/dev/null
fi

mkdir -p "$stage/juraguard-backup/config" "$stage/juraguard-backup/state" "$stage/juraguard-backup/install"
cp -a "$CONFIG_FILE" "$stage/juraguard-backup/config/juraguard.env"
cp -a "$STATE_DIR/data" "$STATE_DIR/traefik" "$stage/juraguard-backup/state/"
cp -a "$INSTALL_DIR/compose.yml" "$INSTALL_DIR/bin" "$stage/juraguard-backup/install/"

database_url=$(config_value DATABASE_URL)
if [ -n "$database_url" ]; then
    backup_image=$(config_value POSTGRES_BACKUP_IMAGE)
    backup_image=${backup_image:-postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297}
    validate_image "$backup_image"
    env_file=$(mktemp)
    chmod 600 "$env_file"
    printf 'PGDATABASE=%s\n' "$database_url" > "$env_file"
    docker run --rm --network host --env-file "$env_file" "$backup_image" \
        pg_dump --format=custom --no-owner --no-privileges \
        > "$stage/juraguard-backup/database.dump"
    rm -f "$env_file"
    env_file=
    printf 'postgresql\n' > "$stage/juraguard-backup/database-type"
else
    printf 'sqlite\n' > "$stage/juraguard-backup/database-type"
fi

tar -C "$stage" -czf "$temporary" juraguard-backup
chmod 600 "$temporary"
mv "$temporary" "$archive"
trap - EXIT INT TERM
rm -rf "$stage"
if [ "$was_running" = true ]; then
    "${COMPOSE[@]}" up -d >/dev/null
fi
printf '%s\n' "$archive"
