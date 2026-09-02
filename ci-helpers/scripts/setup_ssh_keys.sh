#!/bin/bash
# Setup SSH keys for CI/CD pipelines
# Expects environment variables:
#   SSH_PRIVATE_KEY - Base64-encoded private key
#   SSH_PUBLIC_KEY  - Public key content

set -e

echo "Setting up SSH keys..."
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "$SSH_PRIVATE_KEY" | base64 -d > ~/.ssh/id_rsa
chmod 600 ~/.ssh/id_rsa
echo "$SSH_PUBLIC_KEY" > ~/.ssh/id_rsa.pub
