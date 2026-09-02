#!/bin/bash

set -e

SERVER_IP=$1
SSH_KEYS=$2

if [ -z "$SERVER_IP" ]; then
  echo "Error: Server IP is required"
  exit 1
fi

if [ -z "$SSH_KEYS" ]; then
  echo "Error: SSH_KEYS path is required"
  exit 1
fi

# Wait for SSH connection to be available
connection_successful=false
for i in {1..5}; do
  echo "Attempt $i: Checking connection..."
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@$SERVER_IP echo "Connection successful"; then
    connection_successful=true
    break
  fi
  if [ $i -eq 5 ]; then
    echo "Failed to connect after 5 attempts"
    exit 1
  fi
  sleep 10
done

if [ "$connection_successful" = true ]; then
  echo "Adding known SSH host keys..."
  scp -o StrictHostKeyChecking=no "$SSH_KEYS" root@$SERVER_IP:/tmp/ssh-keys
  echo "SSH keys copied successfully"
else
  echo "Failed to establish SSH connection"
  exit 1
fi
