#!/bin/bash

APP_NAME="$1"
DOMAIN="$2"

# Delete application
app_response=$(curl -s -X DELETE "$AUTHENTIK_HOST/api/v3/core/applications/$APP_NAME/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY")

echo "App response: $app_response"

# Get provider ID
provider_response=$(curl -s -X GET "$AUTHENTIK_HOST/api/v3/providers/proxy/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json")

echo "Provider response: $provider_response"
provider_id=$(echo $provider_response | jq -r ".results[] | select(.name==\"$APP_NAME\") | .pk")

# Delete provider
if [ ! -z "$provider_id" ]; then
  curl -s -X DELETE "$AUTHENTIK_HOST/api/v3/providers/proxy/$provider_id/" \
    -H "Authorization: Bearer $AUTHENTIK_API_KEY"
  echo "Deleted provider with ID: $provider_id"
fi

# Get outpost ID
outpost_response=$(curl -s -X GET "$AUTHENTIK_HOST/api/v3/outposts/instances/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json")

echo "Output response: $outpost_response"

outpost_id=$(echo $outpost_response | jq -r ".results[] | select(.name==\"$APP_NAME\") | .pk")

# Delete outpost
if [ ! -z "$outpost_id" ]; then
  curl -s -X DELETE "$AUTHENTIK_HOST/api/v3/outposts/instances/$outpost_id/" \
    -H "Authorization: Bearer $AUTHENTIK_API_KEY"
  echo "Deleted outpost with ID: $outpost_id"
fi

# Remove GitLab CI/CD variable
curl --request DELETE --header "PRIVATE-TOKEN: $GITLAB_ACCESS_TOKEN" \
  "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/variables/AUTHENTIK_OUTPOST_TOKEN_${CI_MERGE_REQUEST_IID}"

echo "Removed AUTHENTIK_OUTPOST_TOKEN_${CI_MERGE_REQUEST_IID} from GitLab CI/CD variables"