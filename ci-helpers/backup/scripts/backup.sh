#!/bin/bash
set -e

echo "[$(date)] Starting backup job..."

# Ensure we have required variables
if [ -z "$BACKUP_TARGET_URL" ]; then
    echo "Error: BACKUP_TARGET_URL is not set."
    exit 1
fi

# 1. Dump Database
echo "Dumping database ${POSTGRES_DB} from ${POSTGRES_HOST}..."
export PGPASSWORD="${POSTGRES_PASSWORD}"
if ! pg_dump -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -c --if-exists "${POSTGRES_DB}" > /tmp/db_dump.sql; then
    echo "Error: Database dump failed!"
    exit 1
fi

# 2. Run Duplicati Backup
echo "Running Duplicati backup to ${BACKUP_TARGET_URL}..."

# Build options
OPTS=""
if [ -n "$MINIO_HOST" ]; then
    OPTS="$OPTS --s3-server-name=$MINIO_HOST --use-ssl"
fi

# We use /data for the local Duplicati database to allow incremental backups
# Note: "Default" is the backup set name
duplicati-cli backup "${BACKUP_TARGET_URL}" \
    /tmp/db_dump.sql \
    /media-files \
    --aws-access-key-id="${MINIO_ACCESS_KEY}" \
    --aws-secret-access-key="${MINIO_SECRET_KEY}" \
    --s3-client=minio \
    --passphrase="${DUPLICATI_PASSPHRASE}" \
    --retention-policy="1W:1D,4W:1W,12M:1M" \
    --allow-missing-source \
    --dbpath=/data/duplicati-backup.sqlite \
    $OPTS

# 3. Cleanup
rm /tmp/db_dump.sql
echo "[$(date)] Backup completed successfully."
