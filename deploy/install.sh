#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: install.sh --domain HOSTNAME [options]

Options:
  --image IMAGE          Image tag or digest (default: ghcr.io/cwirz/juraguard:0.1.0)
  --source-ref REF       Git tag/commit/branch for deployment files (default: v0.1.0)
  --replace-managed      Explicitly replace changed Compose/scripts
EOF
    exit 2
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

installer_path=${BASH_SOURCE[0]:-}
case "$installer_path" in
    ""|/dev/fd/*|/proc/*/fd/*) die "download and verify install.sh before running it" ;;
esac
[ -f "$installer_path" ] || die "download and verify install.sh before running it"

case "$(uname -s)/$(uname -m)" in
    Linux/x86_64) ;;
    *) die "only linux/amd64 is supported" ;;
esac

[ "$(id -u)" -eq 0 ] || die "run as root (use sudo)"

domain=
image=ghcr.io/cwirz/juraguard:0.1.0
source_ref=v0.1.0
replace_managed=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --domain) [ "$#" -ge 2 ] || usage; domain=$2; shift 2 ;;
        --image) [ "$#" -ge 2 ] || usage; image=$2; shift 2 ;;
        --source-ref) [ "$#" -ge 2 ] || usage; source_ref=$2; shift 2 ;;
        --replace-managed) replace_managed=true; shift ;;
        *) usage ;;
    esac
done

if ! [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || [ "${#domain}" -gt 253 ]; then
    die "--domain must be a public DNS hostname"
fi
domain=${domain,,}
if [[ ! "$source_ref" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$ ]] \
    && [[ ! "$source_ref" =~ ^[0-9a-f]{40}$ ]]; then
    die "--source-ref must be an immutable SemVer tag or full commit SHA"
fi
case "$image" in -*|*[$'\t\r\n ']*|'') die "invalid image reference" ;; esac

install_docker() {
    local distribution architecture codename temporary
    # Supported systems provide this standard file.
    # shellcheck disable=SC1091
    . /etc/os-release
    distribution=${ID:-}
    case "$distribution" in debian|ubuntu) ;; *) die "only Debian and Ubuntu are supported" ;; esac
    codename=${VERSION_CODENAME:-}
    [ -n "$codename" ] || die "could not determine distribution codename"
    architecture=$(dpkg --print-architecture)

    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    temporary=$(mktemp)
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        "https://download.docker.com/linux/$distribution/gpg" -o "$temporary"
    install -m 0644 "$temporary" /etc/apt/keyrings/docker.asc
    rm -f "$temporary"
    printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
        "$distribution" "$codename" "$architecture" > /etc/apt/sources.list.d/docker.sources
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
}

if ! command -v docker >/dev/null 2>&1; then
    install_docker
fi
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is required"
if ! command -v curl >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl openssl
fi
command -v flock >/dev/null 2>&1 || die "flock from util-linux is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum from coreutils is required"
exec 9>/run/lock/juraguard.lock
flock -n 9 || die "another Juraguard operation is running"

install_dir=/opt/juraguard
config_dir=/etc/juraguard
config_file=$config_dir/juraguard.env
state_dir=/var/lib/juraguard
backup_dir=/var/backups/juraguard
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT INT TERM
base_url="https://raw.githubusercontent.com/cwirz/juraguard/$source_ref/deploy"

download() {
    local path=$1
    mkdir -p "$temporary/$(dirname "$path")"
    curl --fail --silent --show-error --location --retry 3 --proto '=https' --tlsv1.2 \
        "$base_url/$path" -o "$temporary/$path"
}

download SHA256SUMS
files=(install.sh compose.production.yml traefik.dynamic.yml lib.sh backup.sh restore.sh upgrade.sh)
for file in "${files[@]}"; do download "$file"; done
(cd "$temporary" && sha256sum --check --strict SHA256SUMS)
cmp -s "$installer_path" "$temporary/install.sh" || die "installer does not match --source-ref"
sed "s/__JURAGUARD_DOMAIN__/$domain/g" "$temporary/traefik.dynamic.yml" > "$temporary/traefik-dynamic.rendered.yml"

targets=(
    "$install_dir/compose.yml"
    "$config_dir/traefik-dynamic.yml"
    "$install_dir/bin/lib.sh"
    "$install_dir/bin/backup"
    "$install_dir/bin/restore"
    "$install_dir/bin/upgrade"
)
sources=(compose.production.yml traefik-dynamic.rendered.yml lib.sh backup.sh restore.sh upgrade.sh)
for index in "${!targets[@]}"; do
    if [ -e "${targets[$index]}" ] && ! cmp -s "$temporary/${sources[$index]}" "${targets[$index]}" && [ "$replace_managed" != true ]; then
        die "${targets[$index]} differs; inspect it, then re-run with --replace-managed"
    fi
done

install -d -m 0750 "$install_dir/bin" "$config_dir"
install -d -m 0700 "$state_dir" "$state_dir/data" "$state_dir/traefik" "$backup_dir"
install -m 0644 "$temporary/compose.production.yml" "$install_dir/compose.yml"
install -m 0644 "$temporary/traefik-dynamic.rendered.yml" "$config_dir/traefik-dynamic.yml"
install -m 0640 "$temporary/lib.sh" "$install_dir/bin/lib.sh"
install -m 0750 "$temporary/backup.sh" "$install_dir/bin/backup"
install -m 0750 "$temporary/restore.sh" "$install_dir/bin/restore"
install -m 0750 "$temporary/upgrade.sh" "$install_dir/bin/upgrade"

if [ -f "$config_file" ]; then
    chmod 600 "$config_file"
    configured_domain=$(awk -F= '$1 == "JURAGUARD_DOMAIN" {sub(/^[^=]*=/, ""); print; exit}' "$config_file")
    [ "$configured_domain" = "$domain" ] || die "existing config uses domain $configured_domain; not overwriting it"
else
    docker pull "$image" >/dev/null
    if [[ "$image" == *@sha256:* ]]; then
        pinned_image=$image
    else
        pinned_image=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")
        [[ "$pinned_image" == *@sha256:* ]] || die "registry did not return a content digest for $image"
    fi
    secret=$(openssl rand -hex 48)
    umask 077
    config_temp=$(mktemp "$config_dir/juraguard.env.XXXXXX")
    cat > "$config_temp" <<EOF
JURAGUARD_DOMAIN=$domain
JURAGUARD_IMAGE=$pinned_image
JURAGUARD_SOURCE_REF=$source_ref
DJANGO_SECRET_KEY=$secret
CREDENTIAL_ENCRYPTION_KEYS=
LICENSE_ENCRYPTION_KEYS=
DATABASE_URL=
POSTGRES_BACKUP_IMAGE=postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=localhost
EMAIL_PORT=25
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=false
DEFAULT_FROM_EMAIL=noreply@$domain
EOF
    mv "$config_temp" "$config_file"
    unset secret
fi

configured_image=$(awk -F= '$1 == "JURAGUARD_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "$config_file")
docker pull "$configured_image" >/dev/null
app_owner=$(docker run --rm --entrypoint sh "$configured_image" -c 'printf "%s:%s" "$(id -u)" "$(id -g)"')
[[ "$app_owner" =~ ^[0-9]+:[0-9]+$ ]] || die "could not determine application container ownership"
chown -R "$app_owner" "$state_dir/data"

docker compose --env-file "$config_file" -f "$install_dir/compose.yml" config --quiet
docker compose --env-file "$config_file" -f "$install_dir/compose.yml" up -d --remove-orphans

attempts=30
while [ "$attempts" -gt 0 ]; do
    container=$(docker compose --env-file "$config_file" -f "$install_dir/compose.yml" ps -q juraguard)
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    [ "$status" = healthy ] && break
    sleep 2
    attempts=$((attempts - 1))
done
[ "${status:-}" = healthy ] || die "Juraguard did not become healthy; inspect docker compose logs"

setup_token=$(docker compose --env-file "$config_file" -f "$install_dir/compose.yml" \
    exec -T juraguard cat /data/owner_setup_token)
[[ "$setup_token" =~ ^[A-Za-z0-9_-]{43}$ ]] || die "could not read the owner setup token"
printf 'Juraguard owner setup: https://%s/setup/?token=%s\nConfig: %s\nBackups: %s\n' \
    "$domain" "$setup_token" "$config_file" "$backup_dir"
