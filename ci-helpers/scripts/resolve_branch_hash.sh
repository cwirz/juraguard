#!/bin/bash
# Resolve BRANCH_HASH from the project-level CI/CD variable BRANCH_HASH_<MR_IID>.
# Falls back to CI_COMMIT_REF_SLUG for non-MR pipelines (tags, default branch).
#
# Usage (source to export into current shell):
#   source ci-helpers/scripts/resolve_branch_hash.sh

if [ -n "$CI_MERGE_REQUEST_IID" ]; then
  _VAR_REF='$'BRANCH_HASH_$CI_MERGE_REQUEST_IID
  BRANCH_HASH=$(eval echo "$_VAR_REF")
  if [ -z "$BRANCH_HASH" ]; then
    echo "Warning: BRANCH_HASH_${CI_MERGE_REQUEST_IID} not set, falling back to generate_branch_slug.sh" >&2
    BRANCH_HASH=$(echo -n "$CI_COMMIT_REF_SLUG" | sha256sum | cut -c1-8)
  fi
else
  BRANCH_HASH="$CI_COMMIT_REF_SLUG"
fi

export BRANCH_HASH
echo "BRANCH_HASH resolved to: $BRANCH_HASH"
