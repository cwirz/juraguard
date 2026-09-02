#!/bin/bash
echo "Starting changelog update process"

# Resolve the script directory for locating sibling scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Clone from the current GitLab instance (works on gitlab.com, self-hosted, etc.)
git clone https://oauth2:${GITLAB_ACCESS_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git repo

if [ $? -ne 0 ]; then
    echo "Failed to clone repository. Check if GITLAB_ACCESS_TOKEN has correct permissions."
    exit 1
fi

cd repo

# Configure git identity
git config --local user.name 'gitlab-runner'
git config --local user.email 'gitlab-runner@pyango.ch'

# Checkout the branch we're working on
git checkout ${CI_COMMIT_REF_NAME}

# Get the latest changes
git pull origin ${CI_COMMIT_REF_NAME}

# Fetch all tags
git tag -d $(git tag) 2>/dev/null || true # delete all local tags
git fetch --all --tags # fetch all remote to local

LAST_TAG=`git tag -l --sort=-v:refname "v*" | head -n 1`
CLEANED_LAST_TAG=`git tag -l --sort=-v:refname "v*" | head -n 1 | sed 's/v//g'`
if [ "$CLEANED_LAST_TAG" = "" ]; then
    echo "No tag found. Initial tag is set to 0.0.0"
    CLEANED_LAST_TAG=0.0.0
fi

SHA_LAST_TAG=`git show-ref -s $LAST_TAG`
LAST_TAG_ARRAY=(${CLEANED_LAST_TAG//./ })

echo "Last tag: $LAST_TAG"
echo "Commit: $CI_COMMIT_SHA"
echo "Last tag SHA: $SHA_LAST_TAG"
echo "Running on: $CI_SERVER_HOST"

# Resolve the Docker image for running the AI changelog generator.
# The ci-helpers image contains Python, anthropic, and gitpython.
CI_HELPERS_IMAGE="${CI_REGISTRY_IMAGE}/ci-helpers/docker:latest"

# Generate changelog description using AI (with fallback to simple extraction)
generate_ai_changelog(){
    RELEASE_TYPE=$1
    NEW_TAG=$2
    BEFORE_SHA=$3
    AFTER_SHA=$4

    AI_SCRIPT="${SCRIPT_DIR}/ai_changelog_generator.py"

    if [ ! -f "$AI_SCRIPT" ]; then
        echo "WARNING: AI changelog script not found at $AI_SCRIPT"
        return 1
    fi

    if [ -z "$ANTHROPIC_API_KEY" ]; then
        echo "WARNING: ANTHROPIC_API_KEY not set, skipping AI changelog"
        return 1
    fi

    echo "Generating AI-powered changelog for ${RELEASE_TYPE} release ${NEW_TAG}..."
    echo "Using Docker image: ${CI_HELPERS_IMAGE}"

    # Run the AI script inside the ci-helpers Docker container.
    # Mount the repo (current dir) and the AI script, pass env vars.
    AI_OUTPUT=$(docker run --rm \
        -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        -v "$(pwd):/workspace" \
        -v "${AI_SCRIPT}:/scripts/ai_changelog_generator.py:ro" \
        -w /workspace \
        "$CI_HELPERS_IMAGE" \
        python3 /scripts/ai_changelog_generator.py \
            --release-type "$RELEASE_TYPE" \
            --tag "$NEW_TAG" \
            --message "$CI_COMMIT_MESSAGE" \
            --before-sha "$BEFORE_SHA" \
            --after-sha "$AFTER_SHA" \
            --repo-path "." 2>&1 1>/tmp/ai_changelog_output.txt)

    AI_EXIT_CODE=$?
    AI_CHANGELOG=$(cat /tmp/ai_changelog_output.txt 2>/dev/null)

    # Print stderr output (logging from the AI script)
    if [ -n "$AI_OUTPUT" ]; then
        echo "$AI_OUTPUT"
    fi

    if [ $AI_EXIT_CODE -ne 0 ] || [ -z "$AI_CHANGELOG" ]; then
        echo "WARNING: AI changelog generation failed (exit code: $AI_EXIT_CODE)"
        return 1
    fi

    echo "AI changelog generated successfully"
    echo "$AI_CHANGELOG"
    return 0
}

# Fallback: extract description from commit message using awk (original logic)
extract_description_and_add_to_changelog(){
    RELASE_TYPE=$1
    MESSAGE=$2
    LAST_TAG=$3
    MESSAGE=$(echo $MESSAGE | sed 's/#major_release//g')
    MESSAGE=$(echo $MESSAGE | sed 's/#public_release//g')
    # Use CI_PROJECT_URL which is the full URL to the project
    RELEASE_LINK=${CI_PROJECT_URL}/-/releases/$LAST_TAG
    echo $MESSAGE | awk -v MESSAGE="$MESSAGE" -v RELASE_TYPE="$RELASE_TYPE" -v TAG="$LAST_TAG" -v RELEASE_LINK="$RELEASE_LINK" -F'|' '
    {
      if (RELASE_TYPE == "public"){
          print "is public";
          print "# [Public release "TAG"]("RELEASE_LINK") - "$1>>"CHANGELOG.md"
      }
      else if (RELASE_TYPE == "major"){
          print "is major";
          print "## [Major release "TAG"]("RELEASE_LINK") - "$1>>"CHANGELOG.md"
      }
      else if (RELASE_TYPE == "minor"){
          print "is minor";
          print "- [Release "TAG"]("RELEASE_LINK") - "$1>>"CHANGELOG.md"
      }
      split($2, subs, "-");
      for (i=1; i <= length(subs); i++) {
        gsub(/^[ \t]+|[ \t]+$/,"",subs[i]);
        if (length(subs[i]) != 0){
            if (RELASE_TYPE == "major" || RELASE_TYPE == "public"){
                print "- "subs[i]>>"CHANGELOG.md"
            }
            else if (RELASE_TYPE == "minor"){
                print "is minor";
                print "  - "subs[i]>>"CHANGELOG.md"
            }
        }
      }
      if (RELASE_TYPE == "major" || RELASE_TYPE == "public"){
                print "---">>"CHANGELOG.md"
      }
    }'
}

# Write changelog entry using AI output, with fallback to awk extraction
write_changelog_entry(){
    RELEASE_TYPE=$1
    NEW_TAG=$2

    RELEASE_LINK=${CI_PROJECT_URL}/-/releases/$NEW_TAG

    # Try AI generation first
    AI_DESCRIPTION=$(generate_ai_changelog "$RELEASE_TYPE" "$NEW_TAG" "$SHA_LAST_TAG" "$CI_COMMIT_SHA")
    AI_SUCCESS=$?

    if [ $AI_SUCCESS -eq 0 ] && [ -n "$(cat /tmp/ai_changelog_output.txt 2>/dev/null)" ]; then
        AI_CHANGELOG=$(cat /tmp/ai_changelog_output.txt)
        echo "Using AI-generated changelog"

        if [ "$RELEASE_TYPE" = "public" ]; then
            echo "is public"
            echo "# [Public release ${NEW_TAG}](${RELEASE_LINK}) - ${AI_CHANGELOG}" >> CHANGELOG.md
        elif [ "$RELEASE_TYPE" = "major" ]; then
            echo "is major"
            echo "## [Major release ${NEW_TAG}](${RELEASE_LINK})" >> CHANGELOG.md
            echo "$AI_CHANGELOG" >> CHANGELOG.md
            echo "---" >> CHANGELOG.md
        else
            echo "is minor"
            echo "- [Release ${NEW_TAG}](${RELEASE_LINK}) - ${AI_CHANGELOG}" >> CHANGELOG.md
        fi
    else
        echo "Using fallback changelog (commit message extraction)"
        echo "AI generation failed (exit code: $AI_SUCCESS). Diagnostic output:"
        echo "$AI_DESCRIPTION"
        extract_description_and_add_to_changelog "$RELEASE_TYPE" "$CI_COMMIT_MESSAGE" "$NEW_TAG"
    fi
}

do_autochangelog(){
  if [[ $CI_COMMIT_MESSAGE == *"update changelog.md by gitlab bot"* ]]; then
    echo "Skipping - this is already a changelog update commit"
    return
  fi

  if [[ $CI_COMMIT_MESSAGE == *"#major_release"* ]]; then
    LAST_TAG_ARRAY[1]=$((${LAST_TAG_ARRAY[1]}+1))
    LAST_TAG_ARRAY[2]=0
    NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.0
    echo "major release coming!"
    echo "release number is $NEW_TAG"
    write_changelog_entry major "$NEW_TAG"
  elif [[ $CI_COMMIT_MESSAGE == *"#public_release"* ]]; then
    LAST_TAG_ARRAY[0]=$((${LAST_TAG_ARRAY[0]}+1))
    LAST_TAG_ARRAY[1]=0
    LAST_TAG_ARRAY[2]=0
    NEW_TAG=v${LAST_TAG_ARRAY[0]}.0.0
    echo "public release coming!"
    echo "release number is $NEW_TAG"
    write_changelog_entry public "$NEW_TAG"
  else
    LAST_TAG_ARRAY[2]=$((${LAST_TAG_ARRAY[2]}+1))
    NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.${LAST_TAG_ARRAY[2]}
    echo "minor release coming!"
    echo "release number is $NEW_TAG"
    write_changelog_entry minor "$NEW_TAG"
  fi

  git add CHANGELOG.md
  git commit -m "$CI_COMMIT_MESSAGE, update changelog.md by gitlab bot"

  echo "Pushing to ${CI_COMMIT_REF_NAME} on ${CI_SERVER_HOST}"
  git push origin ${CI_COMMIT_REF_NAME} -o ci.skip

  if [ $? -eq 0 ]; then
    echo "Successfully pushed changelog update"
  else
    echo "Failed to push changelog update"
    exit 1
  fi
}

do_autochangelog