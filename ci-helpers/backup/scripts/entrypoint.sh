#!/bin/bash
set -e

# Configuration
DUPLICATI_DATA_DIR="/data"
BACKUP_DIR="${BACKUP_DIR:-/database}"

# Main entrypoint logic
case "$1" in
    "server")
        echo "Starting Duplicati Server..."
        
        # Ensure data directory exists
        mkdir -p "$DUPLICATI_DATA_DIR"
        
        # Build server arguments
        SERVER_ARGS="--webservice-port=8200 --webservice-interface=any --webservice-allowed-hostnames=* --disable-db-encryption=true"
        
        # Set password - use provided password, or DUPLICATI_PASSPHRASE, or default
        if [ -n "$DUPLICATI_WEBUI_PASSWORD" ]; then
            UI_PASSWORD="$DUPLICATI_WEBUI_PASSWORD"
        elif [ -n "$DUPLICATI_PASSPHRASE" ]; then
            UI_PASSWORD="$DUPLICATI_PASSPHRASE"
        else
            UI_PASSWORD="backup"
        fi
        SERVER_ARGS="$SERVER_ARGS --webservice-password=$UI_PASSWORD"
        
        echo "Starting duplicati-server with args: $SERVER_ARGS"
        exec duplicati-server $SERVER_ARGS
        ;;

    "db-backup")
        echo "========================================="
        echo "Starting PostgreSQL Database Backup"
        echo "========================================="
        
        # Check required environment variables
        if [ -z "$POSTGRES_HOST" ]; then
            echo "Error: POSTGRES_HOST is required"
            exit 1
        fi
        
        # Set defaults
        POSTGRES_PORT="${POSTGRES_PORT:-5432}"
        POSTGRES_USER="${POSTGRES_USER:-postgres}"
        
        if [ -z "$POSTGRES_PASSWORD" ]; then
            echo "Error: POSTGRES_PASSWORD is required"
            exit 1
        fi
        
        export PGPASSWORD="$POSTGRES_PASSWORD"
        
        # Wait for PostgreSQL to be ready
        echo "Waiting for PostgreSQL at $POSTGRES_HOST:$POSTGRES_PORT..."
        for i in $(seq 1 60); do
            if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" > /dev/null 2>&1; then
                echo "PostgreSQL is ready!"
                break
            fi
            echo "Waiting... ($i/60)"
            sleep 1
        done
        
        if ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" > /dev/null 2>&1; then
            echo "Error: PostgreSQL is not ready after 60 seconds"
            exit 1
        fi
        
        # Create backup directory
        mkdir -p "$BACKUP_DIR"
        
        # Run pg_dumpall to backup everything
        BACKUP_FILE="$BACKUP_DIR/db_backup.sql"
        echo "Running pg_dumpall to $BACKUP_FILE..."
        
        pg_dumpall -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" > "$BACKUP_FILE"
        
        # Show backup info
        BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        echo "========================================="
        echo "Backup completed successfully!"
        echo "File: $BACKUP_FILE"
        echo "Size: $BACKUP_SIZE"
        echo "========================================="
        ;;

    "db-backup-cron")
        echo "========================================="
        echo "Starting PostgreSQL Backup Cron Service"
        echo "========================================="
        
        # Default: run at 3 AM daily
        CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * *}"
        
        echo "Backup schedule: $CRON_SCHEDULE"
        echo "Backup directory: $BACKUP_DIR"
        
        # Export all env vars for the cron job
        printenv | grep -E '^(POSTGRES_|BACKUP_|PATH=)' > /etc/environment
        
        # Create crontab entry
        echo "$CRON_SCHEDULE /app/backup/scripts/entrypoint.sh db-backup >> /var/log/backup.log 2>&1" > /etc/cron.d/db-backup
        chmod 0644 /etc/cron.d/db-backup
        crontab /etc/cron.d/db-backup
        
        # Create log file
        touch /var/log/backup.log
        
        # Run initial backup
        echo "Running initial backup..."
        /app/backup/scripts/entrypoint.sh db-backup
        
        # Start cron in foreground
        echo "Starting cron daemon..."
        cron
        
        # Tail the log file to keep container running and show output
        tail -f /var/log/backup.log
        ;;

    "restore-cli")
        echo "========================================="
        echo "Starting Complete Restore Operation"
        echo "========================================="
        
        # STEP 1: Download backup files from Duplicati/S3
        echo ""
        echo "STEP 1: Downloading backup files from S3..."
        
        OPTS=""
        if [ -n "$MINIO_HOST" ]; then
            OPTS="$OPTS --s3-server-name=$MINIO_HOST --use-ssl"
        fi

        # Create temporary restore directory
        TEMP_RESTORE_DIR="/tmp/restore-$$"
        mkdir -p "$TEMP_RESTORE_DIR"
        
        echo "Restoring files from ${BACKUP_TARGET_URL} to temporary location..."
        
        # Restore to temporary directory first
        duplicati-cli restore "${BACKUP_TARGET_URL}" \
            --restore-path="$TEMP_RESTORE_DIR" \
            --aws-access-key-id="${MINIO_ACCESS_KEY}" \
            --aws-secret-access-key="${MINIO_SECRET_KEY}" \
            --s3-client=minio \
            --overwrite=true \
            --passphrase="${DUPLICATI_PASSPHRASE}" \
            --no-local-db \
            $OPTS

        echo "Download and decrypt successful!"
        
        # STEP 2: Restore PostgreSQL database from backup file
        if [ -n "$POSTGRES_HOST" ]; then
            echo ""
            echo "STEP 2: Restoring PostgreSQL database..."
            
            # Find the backup file in various possible locations
            BACKUP_FILE=""
            for possible_path in \
                "$TEMP_RESTORE_DIR/backups/db_backup.sql" \
                "$TEMP_RESTORE_DIR/database/db_backup.sql" \
                "$TEMP_RESTORE_DIR/db_backup.sql"
            do
                if [ -f "$possible_path" ]; then
                    BACKUP_FILE="$possible_path"
                    break
                fi
            done
            
            if [ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ]; then
                echo "Found backup file: $BACKUP_FILE"
                
                # Check required environment variables for DB restore
                if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_PASSWORD" ]; then
                    echo "Warning: POSTGRES_USER or POSTGRES_PASSWORD not set, skipping database restore"
                else
                    POSTGRES_PORT="${POSTGRES_PORT:-5432}"
                    export PGPASSWORD="$POSTGRES_PASSWORD"
                    
                    echo "Waiting for PostgreSQL to be ready..."
                    
                    # Wait for PostgreSQL to be ready (max 120 seconds)
                    for i in $(seq 1 120); do
                        if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" > /dev/null 2>&1; then
                            echo "PostgreSQL is ready!"
                            break
                        fi
                        echo "Waiting for PostgreSQL... ($i/120)"
                        sleep 1
                    done
                    
                    if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" > /dev/null 2>&1; then
                        echo "Dropping and recreating public schema in postgres database..."
                        psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>/dev/null || true
                        
                        echo "Restoring EVERYTHING from $BACKUP_FILE..."
                        psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -f "$BACKUP_FILE" 2>&1 || {
                            echo "Warning: psql completed with some errors (often normal for restore operations)"
                        }
                        echo "Database restore completed!"
                    else
                        echo "Error: PostgreSQL is not ready after 120 seconds, skipping database restore"
                    fi
                fi
            else
                echo "No database backup file found in restored data"
                echo "Searched in: $TEMP_RESTORE_DIR/backups/, $TEMP_RESTORE_DIR/database/, $TEMP_RESTORE_DIR/"
            fi
        fi
        
        # STEP 3: Move media files
        if [ -d "$TEMP_RESTORE_DIR/media-files" ]; then
            echo ""
            echo "STEP 3: Restoring media files..."
            echo "Clearing /media-files..."
            rm -rf /media-files/*
            echo "Moving restored media files..."
            mv "$TEMP_RESTORE_DIR/media-files"/* /media-files/ 2>/dev/null || true
            echo "Media files restored!"
        fi
        
        # Cleanup temp directory
        rm -rf "$TEMP_RESTORE_DIR"
        
        echo ""
        echo "========================================="
        echo "Restore operation completed successfully!"
        echo "========================================="
        ;;
        
    *)
        # Run any other command passed
        exec "$@"
        ;;
esac
