#!/bin/bash
# Important to pull master because we are in a git-limbo state and cant get the latest tags somehow
echo "Update master to be in the correct state"
git config --global user.name 'gitlab-runner'
git config --global user.email 'gitlab-runner@pyango.ch'

git clone https://$GITLAB_ACCESS_USER:$GITLAB_ACCESS_TOKEN@gitlab.pyango.ch/$CI_PROJECT_PATH repo
cd repo/$CONTEXT

git tag -d $(git tag) # delete all local tags
git fetch --all --tags # fetch all remote to local

LAST_TAG=`git tag -l --sort=-v:refname "v*" | head -n 1 | sed 's/v//g'`
LAST_TAG_ARRAY=(${LAST_TAG//./ })
SHA_LAST_TAG=`git show-ref -s v$LAST_TAG`

echo "Last tag: $LAST_TAG"
echo "Tag array: $LAST_TAG_ARRAY"
echo "Commit: $CI_COMMIT_SHA"
echo "Last tag SHA: $SHA_LAST_TAG"

set_version(){
  if [[ $CI_COMMIT_MESSAGE == *"update changelog.md by gitlab bot"* ]]; then
    return
  fi
  if [[ $CI_COMMIT_SHA != $SHA_LAST_TAG ]]; then
    if [[ $CI_COMMIT_MESSAGE == *"#major_release"* ]]; then
      echo "major release coming!"
      LAST_TAG_ARRAY[1]=$((${LAST_TAG_ARRAY[1]}+1))
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.0
      echo "release number is $NEW_TAG"
    elif [[ $CI_COMMIT_MESSAGE == *"#public_release"* ]]; then
      echo "public release coming!"
      LAST_TAG_ARRAY[0]=$((${LAST_TAG_ARRAY[0]}+1))
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.0.0
      echo "release number is $NEW_TAG"
    else
      echo "minor release coming!"
      LAST_TAG_ARRAY[2]=$((${LAST_TAG_ARRAY[2]}+1))
      NEW_TAG=v${LAST_TAG_ARRAY[0]}.${LAST_TAG_ARRAY[1]}.${LAST_TAG_ARRAY[2]}
      echo "release number is $NEW_TAG"
    fi
    echo "Setting new version $NEW_TAG"
    yarn install
    yarn version $NEW_TAG
    git add package.json
    git commit -m "Update package.json to new version $NEW_TAG"
    echo "Pushing to master now"
    git push origin master -o ci.skip
  else
    echo "Commit was already tagged. Will not tag and release again."
  fi
}

set_version
