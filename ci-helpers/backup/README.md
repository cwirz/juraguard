# Backup & Restore Integration Guide

This module provides backup and restore functionality using Duplicati and MinIO.

## Features

- **Duplicati Web UI**: Access backup management at port 8200
- **Manual Backup Configuration**: Configure backups via the Duplicati UI
- **CLI Restore**: Automated restore for review apps via command line

## Commands

| Command | Description |
|---------|-------------|
| `server` | Start Duplicati server with web UI (default) |
| `restore-cli` | Run CLI restore - clears volumes and restores from backup |

## Environment Variables

### Server Mode

| Variable | Required | Description |
|----------|----------|-------------|
| `DUPLICATI_PASSPHRASE` | No | Used as UI password if `DUPLICATI_WEBUI_PASSWORD` not set |
| `DUPLICATI_WEBUI_PASSWORD` | No | Password for web UI (defaults to `DUPLICATI_PASSPHRASE` or "backup") |

### Restore CLI Mode

| Variable | Required | Description |
|----------|----------|-------------|
| `BACKUP_TARGET_URL` | Yes | S3 URL, e.g. `s3://bucket-name/path` |
| `MINIO_HOST` | Yes | MinIO server hostname |
| `MINIO_ACCESS_KEY` | Yes | MinIO access key |
| `MINIO_SECRET_KEY` | Yes | MinIO secret key |
| `DUPLICATI_PASSPHRASE` | Yes | Encryption passphrase for backups |

## Docker Compose Examples

### Production (`docker-compose.deploy.yml`)

```yaml
services:
  backup:
    build: ../ci-helpers/backup
    image: "${CI_REGISTRY_IMAGE}/ci-helpers/backup:${CI_COMMIT_REF_SLUG}"
    restart: always
    command: ["server"]
    environment:
      - DUPLICATI_PASSPHRASE
      - DUPLICATI_WEBUI_PASSWORD
    volumes:
      - database:/database:ro
      - media-files:/media-files:ro
      - duplicati-data:/data
```

### Review App Restore (`docker-compose.deploy.reviewapp.yml`)

```yaml
services:
  restore-worker:
    build: ../ci-helpers/backup
    image: "${CI_REGISTRY_IMAGE}/ci-helpers/backup:${CI_COMMIT_REF_SLUG}"
    restart: "no"
    command: ["restore-cli"]
    environment:
      - BACKUP_TARGET_URL
      - MINIO_HOST=${MINIO_HOST}
      - MINIO_ACCESS_KEY
      - MINIO_SECRET_KEY
      - DUPLICATI_PASSPHRASE
    volumes:
      - database:/database
      - media-files:/media-files
```

### Local Development (`docker-compose-arm64.yml`)

```yaml
services:
  backup:
    build: ../ci-helpers/backup
    command: ["server"]
    environment:
      - DUPLICATI_PASSPHRASE
    ports:
      - "8200:8200"
    volumes:
      - database:/database:ro
      - ./media-files:/media-files:ro
      - duplicati-data:/data
```

## GitLab CI Jobs

### Manual Restore Job (`backend/.gitlab-ci.yml`)

```yaml
restore-review:
  stage: backup
  when: manual
  script:
    - docker compose -f docker-compose.deploy.reviewapp.yml run --rm restore-worker
  rules:
    - if: $CI_MERGE_REQUEST_IID
  tags:
    - $CI_MERGE_REQUEST_IID
```

## Required CI/CD Variables

Set these in **Settings -> CI/CD -> Variables**:

| Variable | Description |
|----------|-------------|
| `MINIO_HOST` | e.g. `minio.helios.pyango.ch` |
| `MINIO_ACCESS_KEY` | MinIO Access Key ID |
| `MINIO_SECRET_KEY` | MinIO Secret Access Key |
| `DUPLICATI_PASSPHRASE` | Encryption password for backups |
| `BACKUP_TARGET_URL` | e.g. `s3://backups/helios` |

## Usage

### Configuring Backups (Production)

1. Access Duplicati UI at the configured URL
2. Add a new backup job
3. Configure source folders: `/database/` and `/media-files/`
4. Configure destination: S3/MinIO with your credentials
5. Set schedule as needed

### Manual Restore (Review Apps)

Trigger the `restore-review` job in GitLab CI, or run manually:

```bash
docker compose -f docker-compose.deploy.reviewapp.yml run --rm restore-worker
```

This will:
1. Clear `/database/` and `/media-files/` volumes
2. Restore all files from the latest backup
