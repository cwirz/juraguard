#!/bin/bash

APP_NAME="$1"
DOMAIN="$2"
MAIN_DOMAIN=$(echo $DOMAIN | awk -F '.' '{print $(NF-1)"."$NF}')

flow_response=$(curl -s -X GET "$AUTHENTIK_HOST/api/v3/flows/instances/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json")

echo "Auth flow response: $flow_response"

authentication_flow_uuid=$(echo "$flow_response" | jq -r '.results[] | select(.slug == "default-authentication-flow") | .pk')
authorization_flow_uuid=$(echo "$flow_response" | jq -r '.results[] | select(.slug == "default-provider-authorization-implicit-consent") | .pk')

if [ -z "$authentication_flow_uuid" ]; then
  echo "Error: Couldn't find the authentication flow UUID"
  exit 1
fi
if [ -z "$authorization_flow_uuid" ]; then
  echo "Error: Couldn't find the authorization flow UUID"
  exit 1
fi


# Create forward auth provider
provider_response=$(curl -s -X POST "$AUTHENTIK_HOST/api/v3/providers/proxy/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\":\"$APP_NAME\",
    \"authentication_flow\":\"$authentication_flow_uuid\",
    \"authorization_flow\":\"$authorization_flow_uuid\",
    \"mode\":\"forward_domain\",
    \"external_host\":\"https://$DOMAIN\",
    \"cookie_domain\":\"$MAIN_DOMAIN\"
  }")

echo "Provider response: $provider_response"

provider_id=$(echo $provider_response | jq -r '.pk')

# Create outpost
outpost_response=$(curl -s -X POST "$AUTHENTIK_HOST/api/v3/outposts/instances/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json" \
 -d "{
    \"name\":\"$APP_NAME\",
    \"type\":\"proxy\",
    \"providers\":[$provider_id],
    \"config\": {
      \"authentik_host\": \"https://authentik.helios.pyango.ch/\",
      \"authentik_host_browser\": \"\",
      \"authentik_host_insecure\": false,
      \"container_image\": null,
      \"docker_labels\": null,
      \"docker_map_ports\": true,
      \"docker_network\": null,
      \"kubernetes_disabled_components\": [],
      \"kubernetes_image_pull_secrets\": [],
      \"kubernetes_ingress_annotations\": {},
      \"kubernetes_ingress_class_name\": null,
      \"kubernetes_ingress_secret_name\": \"authentik-outpost-tls\",
      \"kubernetes_json_patches\": null,
      \"kubernetes_namespace\": \"default\",
      \"kubernetes_replicas\": 1,
      \"kubernetes_service_type\": \"ClusterIP\",
      \"log_level\": \"info\",
      \"object_naming_template\": \"ak-outpost-%(name)s\"
    }
  }")
echo "Outpost response: $outpost_response"

outpost_id=$(echo $outpost_response | jq -r '.pk')
token_identifier=$(echo $outpost_response | jq -r '.token_identifier')
# Fetch the actual token
token_response=$(curl -s -X GET "$AUTHENTIK_HOST/api/v3/core/tokens/$token_identifier/view_key/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json")

if [ -z "$token_response" ]; then
  echo "Error: No response from Authentik API for token"
  exit 1
fi

actual_token=$(echo "$token_response" | jq -r '.key')

if [ -z "$actual_token" ]; then
  echo "Error: Couldn't extract the token key"
  exit 1
fi

# Create application
app_response=$(curl -s -X POST "$AUTHENTIK_HOST/api/v3/core/applications/" \
  -H "Authorization: Bearer $AUTHENTIK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$APP_NAME\",\"slug\":\"$APP_NAME\",\"provider\":$provider_id,\"group\":\"$APP_NAME\"}")

echo "App response: $app_response"

# Set GitLab CI/CD variable
gitlab_response=$(curl --request POST --header "PRIVATE-TOKEN: $GITLAB_ACCESS_TOKEN" \
  "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/variables" \
  --form "key=AUTHENTIK_OUTPOST_TOKEN_${CI_MERGE_REQUEST_IID}" --form "value=$actual_token")
echo "Gitlab response: $gitlab_response"

echo "Outpost token saved as AUTHENTIK_OUTPOST_TOKEN_${CI_MERGE_REQUEST_IID} in GitLab CI/CD variables"
