#!/bin/bash

set -e

FIREWALL_NAME=$1

if [ -z "$FIREWALL_NAME" ]; then
  echo "Error: Firewall name is required"
  exit 1
fi

if ! hcloud firewall describe ${FIREWALL_NAME} > /dev/null 2>&1; then
  echo "Creating firewall ${FIREWALL_NAME}..."
  
  # Create a temporary file for the firewall rules
  RULES_FILE=$(mktemp)
  cat > "$RULES_FILE" <<EOF
[
  {
    "direction": "in",
    "protocol": "tcp",
    "port": "80",
    "source_ips": ["0.0.0.0/0", "::/0"]
  },
  {
    "direction": "in",
    "protocol": "tcp",
    "port": "443",
    "source_ips": ["0.0.0.0/0", "::/0"]
  }
]
EOF

  hcloud firewall create --name ${FIREWALL_NAME} --rules-file "$RULES_FILE"
  
  # Clean up temporary file
  rm -f "$RULES_FILE"
  
  echo "Firewall ${FIREWALL_NAME} created successfully"
else
  echo "Firewall ${FIREWALL_NAME} already exists"
fi
