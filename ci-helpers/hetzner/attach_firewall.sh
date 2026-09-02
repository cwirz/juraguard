#!/bin/bash

set -e

FIREWALL_NAME=$1
SERVER_NAME=$2

if [ -z "$FIREWALL_NAME" ]; then
  echo "Error: Firewall name is required"
  exit 1
fi

if [ -z "$SERVER_NAME" ]; then
  echo "Error: Server name is required"
  exit 1
fi

echo "Attaching firewall ${FIREWALL_NAME} to server ${SERVER_NAME}..."
hcloud firewall apply-to-resource --type server --server ${SERVER_NAME} ${FIREWALL_NAME}
echo "Firewall ${FIREWALL_NAME} attached successfully to ${SERVER_NAME}"
