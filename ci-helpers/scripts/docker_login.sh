#!/bin/bash
# Docker login helper script
# Logs in to all available container registries:
#   - GitLab Container Registry (if CI_REGISTRY_PASSWORD is set)
#   - Docker Hub (if DOCKER_TOKEN is set)
#
# Docker Hub automatic fallback:
#   - primary account: DOCKER_HUB_USERNAME / DOCKER_TOKEN
#   - fallback account: DOCKER_HUB_FALLBACK_USERNAME / DOCKER_HUB_FALLBACK_TOKEN

set -e

# Use a per-job Docker config directory to avoid polluting ~/.docker/config.json.
# Stale credentials there override the automatic CI_JOB_TOKEN auth that Docker
# executor runners use to pull job images. Using $CI_JOB_ID ensures concurrent
# jobs don't interfere with each other.
if [ -z "$DOCKER_CONFIG" ] || [ "$DOCKER_CONFIG" = "$HOME/.docker" ]; then
  export DOCKER_CONFIG="${CI_PROJECT_DIR:-.}/.docker-${CI_JOB_ID:-$$}"
fi
mkdir -p "$DOCKER_CONFIG"

# Symlink CLI plugins (e.g. docker-rollout) so they remain discoverable
# when DOCKER_CONFIG points away from the default ~/.docker directory.
for plugins_dir in "$HOME/.docker/cli-plugins" "/usr/local/lib/docker/cli-plugins" "/usr/libexec/docker/cli-plugins"; do
  if [ -d "$plugins_dir" ]; then
    mkdir -p "$DOCKER_CONFIG/cli-plugins"
    ln -sf "$plugins_dir"/* "$DOCKER_CONFIG/cli-plugins/" 2>/dev/null || true
    break
  fi
done

if [ -n "$CI_REGISTRY_PASSWORD" ]; then
  echo "Logging in to GitLab Container Registry ($CI_REGISTRY)..."
  echo "$CI_REGISTRY_PASSWORD" | docker login "$CI_REGISTRY" -u "$CI_REGISTRY_USER" --password-stdin
fi

DOCKER_HUB_PRIMARY_USERNAME="$DOCKER_HUB_USERNAME"
DOCKER_HUB_PRIMARY_TOKEN="$DOCKER_TOKEN"
DOCKER_HUB_ACTIVE_USERNAME="$DOCKER_HUB_PRIMARY_USERNAME"
DOCKER_HUB_ACTIVE_TOKEN="$DOCKER_HUB_PRIMARY_TOKEN"
DOCKER_HUB_USING_FALLBACK=0

dockerhub_login() {
  local username="$1"
  local token="$2"

  if [ -z "$username" ] || [ -z "$token" ]; then
    return 1
  fi

  echo "Logging in to Docker Hub as ${username}..."
  echo "$token" | docker login -u "$username" --password-stdin
}

dockerhub_switch_to_fallback() {
  if [ "$DOCKER_HUB_USING_FALLBACK" = "1" ]; then
    return 1
  fi

  if [ -z "$DOCKER_HUB_FALLBACK_USERNAME" ] || [ -z "$DOCKER_HUB_FALLBACK_TOKEN" ]; then
    echo "Docker Hub fallback credentials are not configured." >&2
    return 1
  fi

  dockerhub_login "$DOCKER_HUB_FALLBACK_USERNAME" "$DOCKER_HUB_FALLBACK_TOKEN"
  DOCKER_HUB_ACTIVE_USERNAME="$DOCKER_HUB_FALLBACK_USERNAME"
  DOCKER_HUB_ACTIVE_TOKEN="$DOCKER_HUB_FALLBACK_TOKEN"
  DOCKER_HUB_USING_FALLBACK=1

  if [ -n "$CI_REGISTRY_PASSWORD" ]; then
    echo "Re-authenticating to GitLab Container Registry ($CI_REGISTRY) to preserve credentials..."
    echo "$CI_REGISTRY_PASSWORD" | docker login "$CI_REGISTRY" -u "$CI_REGISTRY_USER" --password-stdin
  fi
}

run_with_dockerhub_fallback() {
  local status
  local output_file
  output_file=$(mktemp)

  "$@" > >(tee "$output_file") 2> >(tee -a "$output_file" >&2)
  status=$?

  if [ "$status" -eq 0 ]; then
    rm -f "$output_file"
    return 0
  fi

  if ! grep -Eiq 'pull rate limit|toomanyrequests|you have reached your pull rate limit' "$output_file"; then
    rm -f "$output_file"
    return "$status"
  fi

  echo "Docker Hub rate limit detected while using ${DOCKER_HUB_ACTIVE_USERNAME}." >&2

  if ! dockerhub_switch_to_fallback; then
    rm -f "$output_file"
    return "$status"
  fi

  echo "Retrying command with Docker Hub fallback account ${DOCKER_HUB_ACTIVE_USERNAME}..."
  rm -f "$output_file"
  "$@"
}

if [ -n "$DOCKER_HUB_ACTIVE_TOKEN" ]; then
  dockerhub_login "$DOCKER_HUB_ACTIVE_USERNAME" "$DOCKER_HUB_ACTIVE_TOKEN"
fi
