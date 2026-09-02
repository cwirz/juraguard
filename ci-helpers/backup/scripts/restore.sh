#!/bin/bash
set -e

echo "[$(date)] Starting restore job..."

# Ensure we have required variables
if [ -z "$BACKUP_TARGET_URL" ]; then
    echo "Error: BACKUP_TARGET_URL is not set."
    exit 1
fi

WORK_DIR="/restore_tmp"
# Create directories explicitly to prevent Duplicati warnings/errors about missing folders
mkdir -p "$WORK_DIR/tmp"
mkdir -p "$WORK_DIR/media-files"

# 1. Restore files from Duplicati
echo "Restoring files from ${BACKUP_TARGET_URL}..."

OPTS=""
if [ -n "$MINIO_HOST" ]; then
    OPTS="$OPTS --s3-server-name=$MINIO_HOST --use-ssl"
fi

LOG_FILE="${WORK_DIR}/duplicati.log"

# We use --restore-path to avoid overwriting running system files directly
# We restore the latest version
set +e  # Temporarily disable strict error checking for Duplicati
duplicati-cli restore "${BACKUP_TARGET_URL}" \
    --restore-path="${WORK_DIR}" \
    --aws-access-key-id="${MINIO_ACCESS_KEY}" \
    --aws-secret-access-key="${MINIO_SECRET_KEY}" \
    --s3-client=minio \
    --overwrite=true \
    --passphrase="${DUPLICATI_PASSPHRASE}" \
    --no-local-db \
    --log-file="${LOG_FILE}" \
    --log-file-log-level=Information \
    $OPTS

EXIT_CODE=$?
set -e  # Re-enable strict error checking

# Duplicati returns 0=Success, 1=Warning, 2=Error. We generally accept warnings.
if [ $EXIT_CODE -ne 0 ]; then
    echo "Duplicati exited with code $EXIT_CODE. Dumping log:"
    cat "$LOG_FILE" || echo "No log file found."
    
    # If strictly > 1 (Fatal error), we exit, but at least we saw the log now.
    if [ $EXIT_CODE -gt 1 ]; then
        exit $EXIT_CODE
    fi
fi

# 2. Restore Database
# The backup script created /tmp/db_dump.sql, so it will be in $WORK_DIR/tmp/db_dump.sql
DUMP_FILE="${WORK_DIR}/tmp/db_dump.sql"

if [ -f "$DUMP_FILE" ]; then
    echo "Restoring database from $DUMP_FILE..."
    export PGPASSWORD="${POSTGRES_PASSWORD}"
    
    # We use 'template1' DB to drop/create the target DB to ensure we can connect even if target DB is missing
    psql -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d template1 -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";"
    psql -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d template1 -c "CREATE DATABASE \"${POSTGRES_DB}\";"
    
    # Import the dump
    psql -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "$DUMP_FILE"
    echo "Database restore complete."
else
    echo "Warning: No database dump found at $DUMP_FILE"
fi

# 3. Restore Media Files
# The backup script backed up /media-files, so it will be in $WORK_DIR/media-files
SOURCE_MEDIA="${WORK_DIR}/media-files"
TARGET_MEDIA="/media-files"

if [ -d "$SOURCE_MEDIA" ]; then
    echo "Restoring media files..."
    # Copy contents, overwrite existing
    cp -rf "${SOURCE_MEDIA}/." "${TARGET_MEDIA}/"
    echo "Media files restore complete."
else
    echo "Warning: No media files found at $SOURCE_MEDIA"
fi

# 4. Cleanup
rm -rf "$WORK_DIR"
echo "[$(date)] Restore job finished successfully."
