#!/bin/bash
# Required environment variables:
# HETZNER_API_KEY: Your Hetzner Cloud API token
# DOMAIN: The domain to update (e.g., example.com)
# SUBDOMAIN: The subdomain to create/update (e.g., review-app)
# SERVER_IP: The IP address of your Hetzner Cloud server

set -e  # Exit on error

echo "=== DNS Setup Script Started ==="
echo "Timestamp: $(date)"

MAIN_DOMAIN=$(echo $DOMAIN | awk -F '.' '{print $(NF-1)"."$NF}')

# Validate required environment variables
if [ -z "$HETZNER_API_KEY" ] || [ -z "$MAIN_DOMAIN" ] || [ -z "$SUBDOMAIN" ] || [ -z "$SERVER_IP" ]; then
    echo "ERROR: Missing required environment variables"
    echo "HETZNER_API_KEY: ${HETZNER_API_KEY:+set}"
    echo "DOMAIN: ${DOMAIN:-not set}"
    echo "MAIN_DOMAIN: ${MAIN_DOMAIN:-not set}"
    echo "SUBDOMAIN: ${SUBDOMAIN:-not set}"
    echo "SERVER_IP: ${SERVER_IP:-not set}"
    exit 1
fi

echo "Configuration:"
echo "  Main Domain: $MAIN_DOMAIN"
echo "  Subdomain: $SUBDOMAIN"
echo "  Full FQDN: $SUBDOMAIN.$MAIN_DOMAIN"
echo "  Server IP: $SERVER_IP"
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

# Check if an RRSet already exists for this subdomain
echo "Step 2: Checking if RRSet already exists..."
RRSET_RESPONSE=$(curl -s -H "Authorization: Bearer $HETZNER_API_KEY" \
    "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets?name=$SUBDOMAIN&type=A")

echo "RRSet Response: $RRSET_RESPONSE"

EXISTING_RRSET=$(echo "$RRSET_RESPONSE" | jq -r '.rrsets[0] // empty')

echo "Existing RRSet: ${EXISTING_RRSET:-none}"
echo ""

if [ -z "$EXISTING_RRSET" ]; then
    # Create new RRSet
    echo "Step 3: RRSet does not exist. Creating new A record..."
    CREATE_RESPONSE=$(curl -s -X POST \
        -H "Authorization: Bearer $HETZNER_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$SUBDOMAIN\", \"type\": \"A\", \"ttl\": 60, \"records\": [{\"value\": \"$SERVER_IP\"}]}" \
        "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets")

    echo "Create Response: $CREATE_RESPONSE"

    RRSET_ID=$(echo "$CREATE_RESPONSE" | jq -r '.rrset.id // empty')

    if [ -n "$RRSET_ID" ]; then
        echo "✓ Successfully created A record: $SUBDOMAIN.$MAIN_DOMAIN -> $SERVER_IP"
    else
        echo "ERROR: Failed to create A record"
        echo "Response: $CREATE_RESPONSE"
        exit 1
    fi
else
    # Update existing RRSet using set_records action
    echo "Step 3: RRSet exists. Updating A record..."

    UPDATE_RESPONSE=$(curl -s -X POST \
        -H "Authorization: Bearer $HETZNER_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"records\": [{\"value\": \"$SERVER_IP\"}]}" \
        "https://api.hetzner.cloud/v1/zones/$ZONE_ID/rrsets/$SUBDOMAIN/A/actions/set_records")

    echo "Update Response: $UPDATE_RESPONSE"

    ACTION_STATUS=$(echo "$UPDATE_RESPONSE" | jq -r '.action.status // empty')

    if [ -n "$ACTION_STATUS" ]; then
        echo "✓ Successfully updated A record: $SUBDOMAIN.$MAIN_DOMAIN -> $SERVER_IP"
    else
        echo "ERROR: Failed to update A record"
        echo "Response: $UPDATE_RESPONSE"
        exit 1
    fi
fi

echo ""
echo "=== DNS Setup Script Completed Successfully ==="
echo "Timestamp: $(date)"
