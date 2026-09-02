#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

for script in "$SCRIPT_DIR"/*.sh; do
    bash -n "$script"
done

bash "$SCRIPT_DIR/test-safety.sh"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$SCRIPT_DIR" && sha256sum --check --strict SHA256SUMS)
else
    (cd "$SCRIPT_DIR" && shasum -a 256 --check SHA256SUMS)
fi

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck --severity=warning -x -P SCRIPTDIR "$SCRIPT_DIR"/*.sh
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    printf 'docker compose is required for deployment validation\n' >&2
    exit 1
fi
config=$(mktemp)
dynamic=$(mktemp)
trap 'rm -f "$config" "$dynamic"' EXIT INT TERM
sed 's/__JURAGUARD_DOMAIN__/juraguard.example.com/g' "$SCRIPT_DIR/traefik.dynamic.yml" > "$dynamic"
cat > "$config" <<'EOF'
JURAGUARD_DOMAIN=juraguard.example.com
JURAGUARD_IMAGE=registry.example.com/juraguard@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
DJANGO_SECRET_KEY=not-a-real-secret
CREDENTIAL_ENCRYPTION_KEYS=test-credential-key
LICENSE_ENCRYPTION_KEYS=test-license-key
EOF
printf 'TRAEFIK_DYNAMIC_CONFIG=%s\n' "$dynamic" >> "$config"
rendered=$(docker compose --env-file "$config" -f "$SCRIPT_DIR/compose.production.yml" config)
grep -q 'CREDENTIAL_ENCRYPTION_KEYS: test-credential-key' <<<"$rendered"
grep -q 'LICENSE_ENCRYPTION_KEYS: test-license-key' <<<"$rendered"
grep -q -- '--providers.file.filename=/etc/traefik/dynamic.yml' <<<"$rendered"
grep -q 'Host(`juraguard.example.com`)' "$dynamic"
grep -q 'certResolver: letsencrypt' "$dynamic"
! grep -q '/var/run/docker.sock\|--providers.docker' <<<"$rendered"
! grep -qi 'caddy\|ACME_EMAIL' <<<"$rendered"

printf 'deployment checks passed\n'
