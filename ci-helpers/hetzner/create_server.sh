#!/bin/bash
set -euo pipefail

# Creates a Hetzner Cloud server with fallback across EU locations and server types.
# If the requested server type is unavailable at the default location, it tries
# other European locations (cheaper than US/Asia). If the type is unavailable
# everywhere in EU, it moves to the next larger server type in the same family.
#
# Usage: bash create_server.sh <server-name> <server-type>
# Example: bash create_server.sh test-helios-533 cx22

SERVER_NAME="$1"
INITIAL_SERVER_TYPE="$2"
SSH_KEY="review-app"
IMAGE="ubuntu-24.04"

# European locations only (cheaper pricing)
EU_LOCATIONS="fsn1 nbg1 hel1"

echo "=== Hetzner Server Creation with Fallback ==="
echo "Server name: ${SERVER_NAME}"
echo "Requested server type: ${INITIAL_SERVER_TYPE}"
echo "EU locations to try: ${EU_LOCATIONS}"
echo ""

# Extract the server type family prefix (e.g., "cx" from "cx22", "cpx" from "cpx11")
get_family_prefix() {
    echo "$1" | sed 's/[0-9]*$//'
}

# Try creating a server with a given type and location
try_create_server() {
    local server_type="$1"
    local location="$2"
    echo "  Attempting: type=${server_type}, location=${location}..."
    if hcloud server create \
        --name "${SERVER_NAME}" \
        --type "${server_type}" \
        --image "${IMAGE}" \
        --ssh-key "${SSH_KEY}" \
        --location "${location}" 2>&1; then
        return 0
    else
        echo "  Failed for type=${server_type} in location=${location}"
        return 1
    fi
}

FAMILY=$(get_family_prefix "${INITIAL_SERVER_TYPE}")
echo "Server type family: ${FAMILY}"

# Get all types in same family, sorted by number of cores
AVAILABLE_TYPES=$(hcloud server-type list -o noheader -o columns=name,cores | grep "^${FAMILY}" | sort -k2 -n | awk '{print $1}')
echo "Available types in family: ${AVAILABLE_TYPES}"

# Build list of types to try: start from INITIAL_SERVER_TYPE and go up
TYPES_TO_TRY=""
FOUND=false
for type in ${AVAILABLE_TYPES}; do
    if [ "${type}" = "${INITIAL_SERVER_TYPE}" ]; then
        FOUND=true
    fi
    if [ "${FOUND}" = true ]; then
        TYPES_TO_TRY="${TYPES_TO_TRY} ${type}"
    fi
done

if [ -z "${TYPES_TO_TRY}" ]; then
    echo "WARNING: Could not find ${INITIAL_SERVER_TYPE} in available types. Trying all types in family."
    TYPES_TO_TRY="${AVAILABLE_TYPES}"
fi

echo "Types to try (in order): ${TYPES_TO_TRY}"

# Try each type across all EU locations
for server_type in ${TYPES_TO_TRY}; do
    echo ""
    echo "--- Trying server type: ${server_type} ---"
    for location in ${EU_LOCATIONS}; do
        if try_create_server "${server_type}" "${location}"; then
            echo ""
            echo "=== Server '${SERVER_NAME}' created successfully ==="
            echo "  Type: ${server_type}"
            echo "  Location: ${location}"
            exit 0
        fi
    done
    echo "Type ${server_type} unavailable in all EU locations, trying next size..."
done

echo ""
echo "=== ERROR: Failed to create server '${SERVER_NAME}' ==="
echo "Tried all types (${TYPES_TO_TRY}) across all EU locations (${EU_LOCATIONS})"
exit 1
