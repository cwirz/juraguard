#!/bin/bash
# Generate a deterministic 8-character hash from the branch name.
# Used to create short, DNS-safe identifiers for review app deployments.
#
# Usage:
#   BRANCH_HASH=$(bash ci-helpers/scripts/generate_branch_slug.sh "$CI_COMMIT_REF_SLUG")
#
# Example:
#   $ bash ci-helpers/scripts/generate_branch_slug.sh "feature-my-long-branch-name"
#   a3f2b8c1

BRANCH_NAME="$1"

if [ -z "$BRANCH_NAME" ]; then
  echo "Error: branch name argument is required" >&2
  exit 1
fi

echo -n "$BRANCH_NAME" | sha256sum | cut -c1-8
