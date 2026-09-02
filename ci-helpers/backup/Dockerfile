FROM duplicati/duplicati:latest

# Install PostgreSQL client tools and cron for database backup/restore
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    lsb-release \
    cron \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client-14 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backup

# Copy scripts
COPY scripts/ /app/backup/scripts/
RUN chmod +x /app/backup/scripts/*.sh

# Create required directories
RUN mkdir -p /data /media-files /database

# Expose Duplicati web UI port
EXPOSE 8200

ENTRYPOINT ["/app/backup/scripts/entrypoint.sh"]
CMD ["server"]
