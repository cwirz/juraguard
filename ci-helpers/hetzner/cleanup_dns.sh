#!/bin/bash
# Required environment variables:
# HETZNER_API_KEY: Your Hetzner Cloud API token
# DOMAIN: The domain to update (e.g., example.com)
# SUBDOMAIN: The subdomain to remove (e.g., review-app)

set -e  # Exit on error

echo "=== DNS Cleanup Script Started ==="
echo "Timestamp: $(date)"

MAIN_DOMAIN=$(echo $DOMAIN | awk -F '.' '{print $(NF-1)"."$NF}')

# Validate required environment variables
if [ -z "$HETZNER_API_KEY" ] || [ -z "$MAIN_DOMAIN" ] || [ -z "$SUBDOMAIN" ]; then
    echo "ERROR: Missing required environment variables"
    echo "HETZNER_API_KEY: ${HETZNER_API_KEY:+set}"
    echo "DOMAIN: ${DOMAIN:-not set}"
    echo "MAIN_DOMAIN: ${MAIN_DOMAIN:-not set}"
    echo "SUBDOMAIN: ${SUBDOMAIN:-not set}"
    exit 1
fi

echo "Configuration:"
echo "  Main Domain: $MAIN_DOMAIN"
echo "  Subdomain: $SUBDOMAIN"
echo "  Full FQDN to delete: $SUBDOMAIN.$MAIN_DOMAIN"
echo ""

# Get the zone by name
echo "Step 1: Fetching zone information for $MAIN_DOMAIN..."
ZONE_RESPONSE=$(curl -s -H "Authorization: Bearer $HETZNER_API_KEY" \
    "https://api.hetzner.cloud/v1/zones?name=$MAIN_DOMAIN")

echo "Zone API Response: $ZONE_RESPONSE"

ZONE_ID=$(echo "$ZONE_RESPONSE" | jq -r '.zones[0].id // empty')

if [ -z "$ZONE_ID" ]; then
    echo "ERROR: Zone not found for domain $MAIN_DOMAIN"
    echo "Please verify the domain exists in your Hetzner Cloud Console"
    exit 1
fi

echo "✓ Zone found - ID: $ZONE_ID"
echo ""

# Check if an RRSet exists for this subdomain
echo "Step 2: Checking if RRSet exists..."
RRSET_RESPONSE=$(curl -s -H "Authorization: Bearer $HETZNER_API_KEY" \
    "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets?name=$SUBDOMAIN&type=A")

echo "RRSet Response: $RRSET_RESPONSE"

EXISTING_RRSET=$(echo "$RRSET_RESPONSE" | jq -r '.rrsets[0] // empty')

echo "Existing RRSet: ${EXISTING_RRSET:-none}"
echo ""

if [ -z "$EXISTING_RRSET" ]; then
    echo "ℹ No A record found for $SUBDOMAIN.$MAIN_DOMAIN"
    echo "Nothing to delete - exiting successfully"
    echo ""
    echo "=== DNS Cleanup Script Completed ==="
    echo "Timestamp: $(date)"
    exit 0
fi

# Delete the RRSet
echo "Step 3: Deleting A record..."
DELETE_RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X DELETE \
    -H "Authorization: Bearer $HETZNER_API_KEY" \
    "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets/$SUBDOMAIN/A")

HTTP_CODE=$(echo "$DELETE_RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2)
DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '/HTTP_CODE:/d')

echo "Delete HTTP Code: $HTTP_CODE"
echo "Delete Response: $DELETE_BODY"

if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "200" ]; then
    echo "✓ Successfully deleted A record for $SUBDOMAIN.$MAIN_DOMAIN"
else
    echo "WARNING: Unexpected response when deleting record"
    echo "HTTP Code: $HTTP_CODE"
    echo "Response: $DELETE_BODY"
fi

echo ""
echo "=== DNS Cleanup Script Completed Successfully ==="
echo "Timestamp: $(date)"
