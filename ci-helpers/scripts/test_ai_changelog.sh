#!/bin/bash
echo "=========================================="
echo "AI Changelog Generator - DRY RUN TEST"
echo "=========================================="
echo ""

# Clone repository for testing
echo "Cloning repository..."
git clone https://oauth2:${GITLAB_ACCESS_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git repo

if [ $? -ne 0 ]; then
    echo "Failed to clone repository. Check if GITLAB_ACCESS_TOKEN has correct permissions."
    exit 1
fi

cd repo

# Configure git identity
git config --local user.name 'gitlab-runner'
git config --local user.email 'gitlab-runner@test.local'

# Checkout the branch we're working on
git checkout ${CI_COMMIT_REF_NAME}

# Get the latest changes
git pull origin ${CI_COMMIT_REF_NAME}

echo ""
echo "=========================================="
echo "Testing AI Changelog Generation"
echo "=========================================="
echo ""

# Get the latest commits to test with
LATEST_COMMIT=$(git rev-parse HEAD)
BEFORE_COMMIT=$(git rev-parse HEAD~3 2>/dev/null || git rev-parse HEAD~1 2>/dev/null || echo "0000000000000000000000000000000000000000")

echo "Testing with commit range: $BEFORE_COMMIT..$LATEST_COMMIT"
echo ""

# Test 1: Minor Release
echo "----------------------------------------"
echo "TEST 1: Minor Release (Default)"
echo "----------------------------------------"
TEST_MESSAGE="Test commit message for minor release"
TEST_TAG="v0.0.999-test"

echo "Calling AI changelog generator..."
CHANGELOG_MINOR=$(python3 $CI_PROJECT_DIR/ci-helpers/scripts/ai_changelog_generator.py \
    --release-type "minor" \
    --tag "$TEST_TAG" \
    --message "$TEST_MESSAGE" \
    --before-sha "$BEFORE_COMMIT" \
    --after-sha "$LATEST_COMMIT" \
    --repo-path "$(pwd)" 2>&1)

MINOR_EXIT_CODE=$?
echo ""
echo "Exit Code: $MINOR_EXIT_CODE"
echo ""
echo "Generated Changelog (Minor):"
echo "----------------------------"
echo "$CHANGELOG_MINOR"
echo ""

# Test 2: Major Release
echo "----------------------------------------"
echo "TEST 2: Major Release"
echo "----------------------------------------"
TEST_MESSAGE="Test commit message for major release #major_release"
TEST_TAG="v0.1.0-test"

echo "Calling AI changelog generator..."
CHANGELOG_MAJOR=$(python3 $CI_PROJECT_DIR/ci-helpers/scripts/ai_changelog_generator.py \
    --release-type "major" \
    --tag "$TEST_TAG" \
    --message "$TEST_MESSAGE" \
    --before-sha "$BEFORE_COMMIT" \
    --after-sha "$LATEST_COMMIT" \
    --repo-path "$(pwd)" 2>&1)

MAJOR_EXIT_CODE=$?
echo ""
echo "Exit Code: $MAJOR_EXIT_CODE"
echo ""
echo "Generated Changelog (Major):"
echo "----------------------------"
echo "$CHANGELOG_MAJOR"
echo ""

# Test 3: Public Release
echo "----------------------------------------"
echo "TEST 3: Public Release"
echo "----------------------------------------"
TEST_MESSAGE="Test commit message for public release #public_release"
TEST_TAG="v1.0.0-test"

echo "Calling AI changelog generator..."
CHANGELOG_PUBLIC=$(python3 $CI_PROJECT_DIR/ci-helpers/scripts/ai_changelog_generator.py \
    --release-type "public" \
    --tag "$TEST_TAG" \
    --message "$TEST_MESSAGE" \
    --before-sha "$BEFORE_COMMIT" \
    --after-sha "$LATEST_COMMIT" \
    --repo-path "$(pwd)" 2>&1)

PUBLIC_EXIT_CODE=$?
echo ""
echo "Exit Code: $PUBLIC_EXIT_CODE"
echo ""
echo "Generated Changelog (Public):"
echo "----------------------------"
echo "$CHANGELOG_PUBLIC"
echo ""

# Summary
echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
echo ""

if [ $MINOR_EXIT_CODE -eq 0 ]; then
    echo "✅ Minor Release: PASSED"
else
    echo "❌ Minor Release: FAILED (exit code: $MINOR_EXIT_CODE)"
fi

if [ $MAJOR_EXIT_CODE -eq 0 ]; then
    echo "✅ Major Release: PASSED"
else
    echo "❌ Major Release: FAILED (exit code: $MAJOR_EXIT_CODE)"
fi

if [ $PUBLIC_EXIT_CODE -eq 0 ]; then
    echo "✅ Public Release: PASSED"
else
    echo "❌ Public Release: FAILED (exit code: $PUBLIC_EXIT_CODE)"
fi

echo ""
echo "=========================================="
echo "Environment Information"
echo "=========================================="
echo "Python Version: $(python3 --version)"
echo "Git Version: $(git --version)"
echo ""
echo "Installed Python Packages:"
pip3 list | grep -E "(anthropic|gitpython)"
echo ""

# Check if API key is available
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  WARNING: ANTHROPIC_API_KEY is not set!"
    echo "   The script will fall back to simple changelog generation."
else
    echo "✅ ANTHROPIC_API_KEY is set"
fi

echo ""
echo "=========================================="
echo "DRY RUN TEST COMPLETE"
echo "=========================================="
echo ""
echo "NOTE: This was a dry run. No changes were made to CHANGELOG.md"
echo "      Remove this test job before merging to master!"
echo ""

# Exit with success if at least the minor test passed
if [ $MINOR_EXIT_CODE -eq 0 ]; then
    exit 0
else
    exit 1
fi
