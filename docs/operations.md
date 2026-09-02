# Operations

This page describes the supported single-host package. See [self-hosting](self-hosting.md) for installation, DNS, firewall, SMTP, monitoring, recovery, and uninstall details.

## Health

`GET /health/live/` reports whether the process responds. `GET /health/ready/` (also `/health/` for compatibility) returns HTTP
200 only when the database is reachable. Neither endpoint returns internal errors. Monitor readiness through the client hostname:

```sh
curl --fail https://juraguard.example.com/health/ready/
```

## Backup and restore

Production state lives under `/var/lib/juraguard`; protected environment configuration, including `DJANGO_SECRET_KEY`, is `/etc/juraguard/juraguard.env`. Create a consistent, timestamped archive with:

```sh
sudo /opt/juraguard/bin/backup
```

The script stops the stack briefly, archives application and Traefik state plus recovery configuration, and runs `pg_dump` when `DATABASE_URL` is set. Archives are mode `0600` under `/var/backups/juraguard`. They are not encrypted; move copies to protected off-host storage.

Restore validates paths and rejects archive links/special files. It makes a pre-restore backup, but replacing state or an external PostgreSQL database remains destructive and therefore requires `--confirm`:

```sh
sudo /opt/juraguard/bin/restore \
  --archive /var/backups/juraguard/juraguard-20260830T120000Z.tar.gz \
  --confirm
```

Afterward confirm `/health/ready/`, dashboard login, and one read-only provider catalog refresh. Test this procedure regularly on an isolated host.

PostgreSQL restores always target `DATABASE_URL` from the current protected configuration, never the URL stored in the archive. Configure the intended destination first. When it differs from the archived URL, verify it and add `--confirm-database-target`.

The configured `DJANGO_SECRET_KEY` is required to decrypt stored provider credentials. Losing it cannot be repaired from the database: restore it from backup or reconnect every integration. The same restriction applies to the generated commercial license encryption key in application data.

## Credential and token rotation

- Rotate the fallback gateway bearer token from the dashboard. The previous token stops working immediately.
- Reconnect an integration to replace its OAuth credentials, PAT, or custom headers.
- Provider credentials default to a key derived from `DJANGO_SECRET_KEY`. For explicit rotation, set
  `CREDENTIAL_ENCRYPTION_KEYS=new,old`, reconnect each integration to re-encrypt with `new`, then remove `old`. Invalid or missing
  keys fail closed. Lost keys cannot be recovered; reconnect integrations rather than attempting automatic recovery.
- The commercial key uses its own Fernet key. By default it is `/data/license_encryption_key`; losing it does not expose the license but requires entering a replacement. For managed rotation, set `LICENSE_ENCRYPTION_KEYS=new,old`, replace the license once to re-encrypt with `new`, then remove `old`. Invalid keys fail safely and lock commercial features.
- Rotate Ed25519 signing keys as a coordinated cloud/private and self-host/public configuration change, then run `refresh_license`. Cached documents signed by a removed key fail closed; keep the prior configuration available for rollback.

## Upgrade and rollback

Upgrade to a release tag with:

```sh
sudo /opt/juraguard/bin/upgrade \
  --image ghcr.io/cwirz/juraguard:0.1.0
```

Use a real published tag. The script backs up first, resolves the image to an immutable digest, starts it, and checks health. A failed health check restores the prior image, database, and state before restarting. After upgrade, check login and one read-only tool call.

For application rollback, redeploy the previous image tag. Database migrations are applied automatically at startup; inspect migrations before crossing releases that include irreversible schema changes. Restore the pre-upgrade archive when a migration is not backward-compatible.

Never copy a production archive or `/var/lib/juraguard` plus `/etc/juraguard` into an untrusted environment: together they contain account data and keys that decrypt provider credentials.
