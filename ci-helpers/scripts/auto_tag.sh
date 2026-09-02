#!/bin/bash
echo "Starting tagging process"
echo "Running on GitLab instance: $CI_SERVER_HOST"

git config --local user.name 'gitlab-runner'
git config --local user.email 'gitlab-runner@pyango.ch'

# Set up authentication using access token - works on any GitLab instance
git remote set-url origin https://oauth2:${GITLAB_ACCESS_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git

# Fetch all tags
git tag -d $(git tag) 2>/dev/null || true # delete all local tags
git fetch --all --tags # fetch all remote to local

LAST_TAG=`git tag -l --sort=-v:refname "v*" | head -n 1 | sed 's/v//g'`
if [ "$LAST_TAG" = "" ]; then
    echo "No tag found. Initial tag is set to 0.0.0"
    LAST_TAG=0.0.0
fi
LAST_TAG_ARRAY=(${LAST_TAG//./ })
SHA_LAST_TAG=`git show-ref -s v$LAST_TAG`

echo "Last tag: v$LAST_TAG"
echo "Commit: $CI_COMMIT_SHA"
echo "Last tag SHA: $SHA_LAST_TAG"

do_tagging(){
  if [[ $CI_COMMIT_MESSAGE == *"update changelog.md by gitlab bot"* ]]; then
    echo "Skipping - this is a changelog update commit"
    return
  fi

  if [[ $CI_COMMIT_SHA != $SHA_LAST_TAG ]]; then

    if [[ $CI_COMMIT_MESSAGE == *"#major_release"* ]]; then
      echo "major release coming!"
      LAST_TAG_ARRAY[1]=$((${LAST_TAG_ARRAY[1]}+1))
      LAST_TAG_ARRAY[2]=0
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.0
      echo "release number is $NEW_TAG"
    elif [[ $CI_COMMIT_MESSAGE == *"#public_release"* ]]; then
      echo "public release coming!"
      LAST_TAG_ARRAY[0]=$((${LAST_TAG_ARRAY[0]}+1))
      LAST_TAG_ARRAY[1]=0
      LAST_TAG_ARRAY[2]=0
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.0.0
      echo "release number is $NEW_TAG"
    else
      echo "minor release coming!"
      LAST_TAG_ARRAY[2]=$((${LAST_TAG_ARRAY[2]}+1))
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.${LAST_TAG_ARRAY[2]}
      echo "release number is $NEW_TAG"
    fi

    echo "Adding a new tag $NEW_TAG"
    git tag $NEW_TAG -m "New release"

    echo "Pushing tag to origin"
    git push origin $NEW_TAG

    echo "Making the release via API at ${CI_API_V4_URL}"
    curl --fail --silent --show-error \
      --header "PRIVATE-TOKEN: $GITLAB_ACCESS_TOKEN" \
      --data "name=Release $NEW_TAG" \
      --data "tag_name=$NEW_TAG" \
      --data "ref=$CI_COMMIT_REF_SLUG" \
      --data "description=$CI_COMMIT_MESSAGE" \
      --request POST ${CI_API_V4_URL}/projects/$CI_PROJECT_ID/releases

    if [ $? -eq 0 ]; then
      echo "Release created successfully"
    else
      echo "Failed to create release"
      exit 1
    fi
  else
    echo "Commit was already tagged. Will not tag and release again."
  fi
}

do_tagging