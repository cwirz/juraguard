# Self-hosting on one Linux server

This deployment supports a fresh Debian or Ubuntu server, including a Hetzner Cloud VPS. It runs one Juraguard application container and Traefik on one host. SQLite is the default; an external PostgreSQL URL is optional. Cloud mode is operator-only and is not enabled by this self-host package.

> [!WARNING]
> The public source mirror is available, but the `v0.1.0` image and installer endpoint are not published yet. Release commands in this guide will not work until those assets are published and verified.

## Before installing

- Choose a current Debian or Ubuntu release with enough memory and disk for Docker, the database, logs, backups, and expected catalogs. Start with the provider's smallest general-purpose plan that meets those needs, then monitor it. No workload benchmark or fixed user capacity is claimed.
- Create a DNS `A` record for the chosen hostname pointing to the server's public IPv4 address. Add `AAAA` only when IPv6 is configured and reachable on the server.
- Permit inbound TCP 22 from administrator addresses and TCP 80/443 from the internet. Deny other unsolicited inbound traffic. Port 80 is required for HTTP-to-HTTPS redirect and ACME validation; do not publish application port 8000.
- Ensure no other process uses ports 80 or 443.

Hetzner operators can apply those rules with a Hetzner Cloud Firewall and attach it to the server. Generic VPS operators can use their provider firewall and a host firewall. Keep an SSH session open while testing firewall changes.

## Install

Download the installer and checksum manifest from the same immutable release. Verify and review the installer before running it:

```sh
release=v0.1.0
mkdir -p /tmp/juraguard-release
curl -fsSLo /tmp/juraguard-release/install.sh \
  "https://raw.githubusercontent.com/cwirz/juraguard/$release/deploy/install.sh"
curl -fsSLo /tmp/juraguard-release/SHA256SUMS \
  "https://raw.githubusercontent.com/cwirz/juraguard/$release/deploy/SHA256SUMS"
(cd /tmp/juraguard-release && sha256sum --ignore-missing --check SHA256SUMS)
less /tmp/juraguard-release/install.sh
sudo bash /tmp/juraguard-release/install.sh \
  --domain juraguard.example.com --source-ref "$release"
```

These commands require the GitHub tag `v0.1.0` and `ghcr.io/cwirz/juraguard:0.1.0` to be published. Until both exist, they document the intended release path and will not install successfully. The installer accepts `--image REGISTRY/IMAGE:TAG` and an immutable SemVer tag or full commit SHA through `--source-ref`. It verifies every downloaded deployment file against the selected ref's checksum manifest, pulls the image, and stores its immutable registry digest. It installs Docker from Docker's official Debian/Ubuntu apt repository only when Docker is absent, creates root-owned configuration and backup directories, creates private persistent state, and starts Compose. Re-running the same installer is safe. It refuses changed managed files unless `--replace-managed` is explicitly supplied and never replaces existing `juraguard.env` content.

After DNS has propagated, Traefik obtains and renews the certificate automatically. The installer prints a private tokenized owner setup URL; open it within 15 minutes of first start. Setup closes when the window expires or the first account exists.

If the initial window expires before account creation, reopen it from the host and restart the service:

```sh
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml exec juraguard rm /data/owner_setup_deadline
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml restart juraguard
```

This starts one new 15-minute window. Host access is required; never expose a remote reset endpoint.

## Configuration and email

Runtime configuration is `/etc/juraguard/juraguard.env` with mode `0600`. State is under `/var/lib/juraguard`; backups default to `/var/backups/juraguard`. Edit configuration as root using Docker Compose env-file syntax. Percent-encode reserved characters in `DATABASE_URL`; single-quote SMTP values containing `$` or `#`. Then apply it:

```sh
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml up -d
```

SQLite needs no database setting. To use an existing external PostgreSQL service, set a percent-encoded URL and a backup client image matching the server major version:

```text
DATABASE_URL=postgresql://user:password@db.example.net:5432/juraguard?sslmode=require
POSTGRES_BACKUP_IMAGE=postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297
```

The Compose file does not start a second application or a bundled PostgreSQL server. Test external database reachability and backups before relying on it.

Self-hosted account setup and login do not require email verification. Password recovery and any other outbound email still require operational SMTP. The production package never writes recovery links to console logs, and its placeholder SMTP target fails closed until configured:

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=juraguard
EMAIL_HOST_PASSWORD=replace-with-secret
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=juraguard@example.com
```

Restart after editing. Never paste this file into tickets or logs.

## Routine operations

Health and logs:

```sh
curl --fail https://juraguard.example.com/health/
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml ps
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml logs --since 15m
```

Monitor the public HTTPS health endpoint, certificate expiry, disk space, memory, and backup job results. Container logs rotate at 10 MiB with three files; forward logs externally when retention or alerting is required.

Create a consistent timestamped backup (brief service stop):

```sh
sudo /opt/juraguard/bin/backup
```

Copy backups over encrypted transport to access-controlled, encrypted off-host storage. The archive itself is not encrypted by this script and contains the keys needed to decrypt provider credentials, so protect it accordingly. Schedule the command with systemd or cron and alert on failures. Regularly restore a recent copy on an isolated test host; an untested backup is not a recovery plan.

Upgrade to a published tag; the script backs up first, resolves the tag to a digest, and waits for health:

```sh
sudo /opt/juraguard/bin/upgrade \
  --image ghcr.io/cwirz/juraguard:0.1.0
```

To roll back application code, run `upgrade` with the previous image tag or digest. Automatic migrations may not be backward-compatible. Read release notes first; when needed, restore the pre-upgrade archive as well as the image.

Restore is destructive and requires explicit confirmation:

```sh
sudo /opt/juraguard/bin/restore \
  --archive /var/backups/juraguard/juraguard-20260830T120000Z.tar.gz \
  --confirm
```

The restore command validates archive paths and types, creates a pre-restore backup, restores state/configuration, and waits for application health. For a PostgreSQL backup, first set the current `DATABASE_URL` to the intended restore destination. Restore never connects to the archived database URL. If the archived and current URLs differ, verify the destination and add `--confirm-database-target`.

## Recovery and key loss

For a lost server, install the same deployment release on a replacement host, securely copy the archive there, then run restore. Update DNS after the restored service passes health, login, and a read-only integration call. Traefik can obtain a replacement certificate.

`DJANGO_SECRET_KEY` in the protected config encrypts stored integration credentials. The data directory may also contain a commercial license encryption key. Losing the matching key cannot be bypassed or reconstructed: restore it from backup, or reconnect affected integrations/re-enter the license. There is no insecure key-reset recovery. Rotate exposed provider credentials and application tokens.

Self-hosted personal mode does not require a commercial license. Current builds use configured license validation to enable organization controls, but all source is AGPL-3.0. `DEPLOYMENT_MODE=cloud` enables operator-only billing/tenancy behavior and requires separate secrets, SMTP, and operational controls; this installer deliberately fixes the application to self-hosted mode.

## Uninstall

Back up first. Then stop the stack and remove installed files and state explicitly:

```sh
sudo docker compose --env-file /etc/juraguard/juraguard.env \
  -f /opt/juraguard/compose.yml down
sudo rm -rf /opt/juraguard /etc/juraguard /var/lib/juraguard
```

Remove `/var/backups/juraguard` only after confirming an off-host copy. The installer does not remove Docker, firewall rules, DNS records, or external PostgreSQL data.
