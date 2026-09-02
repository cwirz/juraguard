#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

fail() {
    printf 'safety check failed: %s\n' "$*" >&2
    exit 1
}

assert_contains() {
    local file=$1 text=$2
    grep -Fq -- "$text" "$file" || fail "$file does not contain $text"
}

assert_contains "$SCRIPT_DIR/install.sh" 'image=ghcr.io/cwirz/juraguard:0.1.0'
assert_contains "$SCRIPT_DIR/install.sh" 'source_ref=v0.1.0'
assert_contains "$SCRIPT_DIR/install.sh" 'base_url="https://raw.githubusercontent.com/cwirz/juraguard/$source_ref/deploy"'
assert_contains "$SCRIPT_DIR/install.sh" 'exec -T juraguard cat /data/owner_setup_token'
assert_contains "$SCRIPT_DIR/install.sh" 'https://%s/setup/?token=%s'
assert_contains "$SCRIPT_DIR/install.sh" 'Linux/x86_64'
assert_contains "$SCRIPT_DIR/install.sh" 'EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend'
assert_contains "$SCRIPT_DIR/install.sh" 'download and verify install.sh before running it'
assert_contains "$SCRIPT_DIR/install.sh" 'sha256sum --check --strict SHA256SUMS'
assert_contains "$SCRIPT_DIR/install.sh" 'installer does not match --source-ref'
assert_contains "$SCRIPT_DIR/../ci-helpers/jobs/deploy.yml" 'CI_COMMIT_REF_PROTECTED == "true"'

WORKFLOW_DIR="$SCRIPT_DIR/../.github/workflows"
assert_contains "$WORKFLOW_DIR/ci.yml" 'contents: read'
assert_contains "$WORKFLOW_DIR/ci.yml" 'python-version: "3.13"'
assert_contains "$WORKFLOW_DIR/ci.yml" 'fetch-depth: 0'
assert_contains "$WORKFLOW_DIR/ci.yml" 'bash deploy/check.sh'
assert_contains "$WORKFLOW_DIR/ci.yml" 'docker build --platform linux/amd64'
assert_contains "$WORKFLOW_DIR/ci.yml" '--scanners vuln --pkg-types os --ignore-unfixed'
assert_contains "$WORKFLOW_DIR/ci.yml" '--scanners license --pkg-types library'
assert_contains "$WORKFLOW_DIR/release.yml" 'packages: write'
assert_contains "$WORKFLOW_DIR/release.yml" 'subject-digest: ${{ steps.build.outputs.digest }}'
assert_contains "$WORKFLOW_DIR/release.yml" 'sbom: true'
assert_contains "$WORKFLOW_DIR/release.yml" 'CANONICAL_RELEASE_ACTOR: ${{ vars.CANONICAL_RELEASE_ACTOR }}'
assert_contains "$WORKFLOW_DIR/release.yml" 'environment: public-release'
assert_contains "$WORKFLOW_DIR/release.yml" 'bash deploy/check.sh'
assert_contains "$WORKFLOW_DIR/release.yml" 'platforms: linux/amd64'
assert_contains "$WORKFLOW_DIR/release.yml" 'fetch-depth: 0'
assert_contains "$WORKFLOW_DIR/release.yml" '--scanners vuln --pkg-types os --ignore-unfixed'
assert_contains "$WORKFLOW_DIR/release.yml" '--scanners license --pkg-types library'
if grep -Fq 'packages: write' "$WORKFLOW_DIR/ci.yml" || grep -Fq 'push: true' "$WORKFLOW_DIR/ci.yml"; then
    fail "fork-safe CI can publish packages"
fi
while IFS= read -r action; do
    [[ "$action" =~ uses:[[:space:]]+[^[:space:]]+@[0-9a-f]{40}([[:space:]]*#[[:space:]].*)?$ ]] || \
        fail "GitHub Action is not pinned to a full commit SHA: $action"
done < <(grep -hE '^[[:space:]]*- uses:' "$WORKFLOW_DIR"/*.yml)

mkdir "$WORK/bin"
cat > "$WORK/bin/id" <<'EOF'
#!/bin/sh
[ "${1:-}" = -u ] && { printf '0\n'; exit 0; }
exec /usr/bin/id "$@"
EOF
cat > "$WORK/bin/chown" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$WORK/bin/stat" <<'EOF'
#!/bin/sh
printf '0:0\n'
EOF
cat > "$WORK/bin/sleep" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$WORK/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "$1" = compose ]; then
    shift
    for argument in "$@"; do
        case "$argument" in
            version) exit 0 ;;
            stop) printf 'compose-stop\n' >> "$FAKE_EVENTS"; exit 0 ;;
            up)
                count=$(($(cat "$FAKE_STATE" 2>/dev/null || printf '0') + 1))
                printf '%s\n' "$count" > "$FAKE_STATE"
                printf 'compose-up-%s\n' "$count" >> "$FAKE_EVENTS"
                exit 0
                ;;
            ps) printf 'fake-container\n'; exit 0 ;;
        esac
    done
fi
if [ "$1" = pull ]; then exit 0; fi
if [ "$1" = image ] && [ "$2" = inspect ]; then
    printf 'registry.example/juraguard@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'
    exit 0
fi
if [ "$1" = inspect ]; then
    count=$(cat "$FAKE_STATE" 2>/dev/null || printf '0')
    if [ "${FAKE_MODE:-healthy}" = upgrade ] && [ "$count" -lt 2 ]; then printf 'unhealthy\n'; else printf 'healthy\n'; fi
    exit 0
fi
if [ "$1" = run ]; then
    printf 'docker-run:%s\n' "$*" >> "$FAKE_LOG"
    while [ "$#" -gt 0 ]; do
        if [ "$1" = --env-file ]; then
            awk -F= '$1 == "PGDATABASE" {sub(/^[^=]*=/, ""); print "pg-target=" $0}' "$2" >> "$FAKE_LOG"
            break
        fi
        shift
    done
    printf 'pg-restore\n' >> "$FAKE_EVENTS"
    exit 0
fi
exit 1
EOF
chmod +x "$WORK/bin"/*
export PATH="$WORK/bin:$PATH" JURAGUARD_LOCK_HELD=1

setup_case() {
    local archived_url=$2 current_url=$3 root="$WORK/$1" archive_root
    mkdir -p "$root/install/bin" "$root/state/data" "$root/state/traefik"
    : > "$root/install/compose.yml"
    printf 'current-state\n' > "$root/state/data/state"
    cat > "$root/config.env" <<EOF
JURAGUARD_IMAGE=registry.example/juraguard@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
DATABASE_URL=$current_url
POSTGRES_BACKUP_IMAGE=postgres:test
EOF
    archive_root="$root/archive/juraguard-backup"
    mkdir -p "$archive_root/config" "$archive_root/state/data" "$archive_root/state/traefik"
    cat > "$archive_root/config/juraguard.env" <<EOF
JURAGUARD_IMAGE=registry.example/juraguard@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
DATABASE_URL=$archived_url
POSTGRES_BACKUP_IMAGE=postgres:test
EOF
    printf 'archived-state\n' > "$archive_root/state/data/state"
    printf 'postgresql\n' > "$archive_root/database-type"
    : > "$archive_root/database.dump"
    tar -C "$root/archive" -czf "$root/backup.tar.gz" juraguard-backup
    : > "$root/events"
    : > "$root/docker.log"
    export JURAGUARD_INSTALL_DIR="$root/install" JURAGUARD_CONFIG_FILE="$root/config.env"
    export JURAGUARD_STATE_DIR="$root/state" JURAGUARD_BACKUP_DIR="$root/backups"
    export FAKE_EVENTS="$root/events" FAKE_LOG="$root/docker.log" FAKE_STATE="$root/up-count"
    export TEST_ROOT="$root" TEST_BACKUP="$root/backup.tar.gz"
}

archived_url=postgresql://archived.invalid/juraguard
current_url=postgresql://current.invalid/juraguard
setup_case restore-target "$archived_url" "$current_url"
if output=$(JURAGUARD_SKIP_PRE_RESTORE_BACKUP=true JURAGUARD_EXISTING_BACKUP="$TEST_BACKUP" \
    bash "$SCRIPT_DIR/restore.sh" --archive "$TEST_BACKUP" --confirm 2>&1); then
    fail "mismatched PostgreSQL target was accepted without confirmation"
fi
[[ "$output" == *'pass --confirm-database-target'* ]] || fail "mismatch refusal was not explicit"
[ ! -s "$FAKE_LOG" ] || fail "database command ran before target confirmation"

JURAGUARD_SKIP_PRE_RESTORE_BACKUP=true JURAGUARD_EXISTING_BACKUP="$TEST_BACKUP" \
    bash "$SCRIPT_DIR/restore.sh" --archive "$TEST_BACKUP" --confirm --confirm-database-target >/dev/null
grep -Fxq "pg-target=$current_url" "$FAKE_LOG" || fail "pg_restore did not receive current target"
! grep -Fq "$archived_url" "$FAKE_LOG" || fail "pg_restore received archived target"
grep -Fxq "DATABASE_URL=$current_url" "$JURAGUARD_CONFIG_FILE" || fail "restored config lost current target"

setup_case upgrade-rollback "$current_url" "$current_url"
cat > "$JURAGUARD_INSTALL_DIR/bin/backup" <<'EOF'
#!/bin/sh
printf '%s\n' "$TEST_BACKUP"
EOF
cat > "$JURAGUARD_INSTALL_DIR/bin/restore" <<'EOF'
#!/bin/sh
printf 'restore:%s existing=%s\n' "$*" "${JURAGUARD_EXISTING_BACKUP:-}" >> "$FAKE_LOG"
exec bash "$TEST_RESTORE_SCRIPT" "$@"
EOF
chmod +x "$JURAGUARD_INSTALL_DIR/bin/backup" "$JURAGUARD_INSTALL_DIR/bin/restore"
export TEST_RESTORE_SCRIPT="$SCRIPT_DIR/restore.sh" FAKE_MODE=upgrade
if output=$(bash "$SCRIPT_DIR/upgrade.sh" --image registry.example/juraguard:new 2>&1); then
    fail "failed upgrade unexpectedly succeeded"
fi
[[ "$output" == *'pre-upgrade image and data restored'* ]] || fail "failed upgrade did not report restored backup"
grep -Fq "restore:--archive $TEST_BACKUP --confirm existing=$TEST_BACKUP" "$FAKE_LOG" || fail "upgrade did not invoke restore with existing backup"
pg_line=$(awk '$0 == "pg-restore" {print NR; exit}' "$FAKE_EVENTS")
old_up_line=$(awk '$0 == "compose-up-2" {print NR; exit}' "$FAKE_EVENTS")
if [ -z "$pg_line" ] || [ -z "$old_up_line" ] || [ "$pg_line" -ge "$old_up_line" ]; then
    fail "old app restarted before schema restore"
fi
[ "$(grep -c '^compose-up-' "$FAKE_EVENTS")" -eq 2 ] || fail "unexpected app restart during failed upgrade"

printf 'deployment safety checks passed\n'
