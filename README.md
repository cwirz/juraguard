# Juraguard

Juraguard puts any number of remote Streamable HTTP MCP servers behind one compact endpoint. Agents search cached tool catalogs through a small set of gateway meta-tools, then call an exact namespaced tool only when needed. This reduces context use while credentials remain server-side.

Juraguard is a small AGPL-3.0 Django application: one container, SQLite, static files, and generated instance secrets in one persistent `/data` volume. Cloud deployments can use PostgreSQL; the self-host default needs no Redis, Node runtime, sidecars, or telemetry.

Juraguard's managed service is in free public beta. Self-hosting is prerelease; container deployments are the only supported self-host path.

[GitLab](https://gitlab.pyango.ch/pyango/juraguard/monorepo) is the authoritative development and release repository. The planned [GitHub mirror](https://github.com/cwirz/juraguard) will be the public contribution surface; it is not published yet.

> [!WARNING]
> The public source mirror, `v0.1.0` image, and installer are not published yet. The image and production-install commands below will not work until the first release. A source checkout from GitLab is currently the only usable self-host path.

## Run from source

From a source checkout, run locally with Docker Compose:

```sh
docker compose -f juraguard/docker-compose.yml up --build -d
```

Within 15 minutes of first start, read the token with `docker compose -f juraguard/docker-compose.yml exec juraguard cat /data/owner_setup_token`, then open `http://localhost:8000/setup/?token=TOKEN`. Stop with `docker compose -f juraguard/docker-compose.yml down`; the named volume keeps local data.

### Published image (not available yet)

```sh
docker run --name juraguard \
  -p 8000:8000 \
  -v juraguard-data:/data \
  ghcr.io/cwirz/juraguard:0.1.0
```

Within 15 minutes of first start, get the private setup token with `docker exec juraguard cat /data/owner_setup_token`, then open `http://localhost:8000/setup/?token=TOKEN` and create the first owner. The image generates a persistent Django encryption secret, migrates SQLite, collects static files, and starts Gunicorn automatically. Keep the volume: losing `/data/secret_key` makes stored integration headers unreadable.

GitHub release automation will publish version, stable major/minor, commit-SHA, and stable `latest` tags only after required checks on a canonical SemVer release tag. GHCR images include SBOM and provenance attestations.

## Production self-hosting

The supported one-host package targets Debian/Ubuntu, including a Hetzner Cloud VPS. It installs Docker from Docker's official apt repository when absent, runs the application behind bundled Traefik automatic HTTPS, keeps SQLite by default, and pins the selected application image to its registry digest.

Download the installer and checksum manifest from the same immutable release, verify it, review it, then run it with your own DNS hostname:

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

The installer prints the private owner setup URL; open it within 15 minutes. Full firewall, DNS, PostgreSQL, SMTP, backup, restore, upgrade, recovery, key-loss, and uninstall instructions are in [the self-hosting guide](docs/self-hosting.md).

Core commands:

```sh
sudo /opt/juraguard/bin/backup
sudo /opt/juraguard/bin/upgrade --image ghcr.io/cwirz/juraguard:RELEASE_TAG
sudo /opt/juraguard/bin/restore --archive /path/to/juraguard-TIMESTAMP.tar.gz --confirm
curl --fail https://juraguard.example.com/health/
```

Useful environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated public hostnames |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated HTTPS origins for dashboard forms |
| `MCP_ALLOWED_ORIGINS` | empty | Extra exact origins accepted by `/mcp/` |
| `ALLOW_PRIVATE_NETWORKS` | `false` | Allow private/reserved targets and plain HTTP for trusted self-hosted networks |
| `CREDENTIAL_ENCRYPTION_KEYS` | derived from `DJANGO_SECRET_KEY` | Comma-separated Fernet keys, newest first, for explicit credential-key rotation |
| `PUBLIC_BASE_URL` | empty | Canonical HTTPS origin used for OAuth, callbacks, and private setup links |
| `TRUST_PROXY_HEADERS` | `false` | Trust forwarding headers only behind a proxy that overwrites them |
| `PRODUCT_NAME` | `Juraguard` | Visible product name |
| `WEB_CONCURRENCY` | `2` | Gunicorn worker count |
| `WEB_THREADS` | `4` | Request threads per Gunicorn worker |

### Cloud billing and organization controls

Cloud billing is enabled only with `DEPLOYMENT_MODE=cloud`. Set `POLAR_ACCESS_TOKEN`, `POLAR_WEBHOOK_SECRET`,
`POLAR_MONTHLY_PRODUCT_ID`, `POLAR_ANNUAL_PRODUCT_ID`, `POLAR_BETA_DISCOUNT_ID`, and `PUBLIC_BASE_URL`.
`POLAR_SERVER_URL` defaults to Polar production and accepts only Polar's production or sandbox HTTPS API origin.
Set `POLAR_BILLING_ENABLED=false` only for cloud development/tests. Beta deployments must explicitly set
`CLOUD_BETA_ACCESS=true`; it defaults to false. Product, customer, and discount
identifiers are always selected server-side. Polar handles checkout, payment data, tax, invoices, and its customer portal.

Current builds use license-key validation to enable self-hosted organization controls. Validation uses `LICENSE_VALIDATION_URL`, `LICENSE_SIGNING_PUBLIC_KEY` (URL-safe base64 raw
Ed25519 public key), `LICENSE_VALIDATION_TIMEOUT` (default 5 seconds), and `LICENSE_GRACE_DAYS` (default 7).
Signed documents default to a 24-hour maximum lifetime with 300 seconds of explicit clock skew.
All source, including `juraguard/commercial/`, and the shipped image are AGPL-3.0. License-key checks are current
product behavior, not a separate proprietary-code boundary; AGPL users may modify them under the license terms.
The personal gateway needs no license. Cloud issuance additionally requires
`LICENSE_SIGNING_PRIVATE_KEY` (URL-safe base64 raw Ed25519 private key). Run `issue_license` and `revoke_license` only
in cloud mode; schedule `refresh_license` on self-hosted instances.

Put the service behind an HTTPS reverse proxy for anything beyond local evaluation. Production deployments must set `PUBLIC_BASE_URL` to the public HTTPS origin, for example `https://juraguard.example.com`; request-derived setup links are accepted only on localhost when it is unset.

## First run

1. Use the private tokenized setup URL within 15 minutes of the first start. Setup closes when the window expires or the first account exists.
2. Add an integration: GitLab, Generic OAuth MCP, or Generic custom MCP.
3. For GitLab, enter its base URL and one PAT. For OAuth MCP, approve the remote provider in its browser flow. Generic custom MCP retains the 15-minute, one-use secret-header link.
4. Configure an OAuth-capable MCP client with `https://your-host/mcp/`; Juraguard discovery opens browser login and approval automatically.
5. For clients without MCP OAuth, generate the revocable fallback bearer token on the dashboard. It is shown once; rotating it revokes the old token.

The dashboard can edit, enable/disable, reconnect, and delete integrations. Agent-side create/reconnect operations return the same private setup-link flow and cannot accept credential fields.

## Architecture

- Django templates, sessions, forms, and CSRF provide the web UI.
- SQLite stores users, hashed gateway/setup/OAuth tokens and codes, integrations, and cached catalogs.
- Fernet encrypts remote OAuth credentials, GitLab PATs, and custom headers with configured rotation keys or a key derived from the persistent Django secret.
- GitLab is a direct provider adapter with a bounded catalog for users, projects, issues, merge requests, pipelines/jobs, and notes. Write tools require the integration's explicit write toggle.
- POST-only `/mcp/` accepts JSON-RPC `initialize`, `notifications/initialized`, `ping`, `tools/list`, and `tools/call`.
- `tools/list` returns only deterministic lexical search/call and minimal integration-management meta-tools.
- The HTTP client initializes each upstream operation, keeps its session ID through the initialized notification and tool request, and supports JSON plus request-scoped SSE responses.
- WhiteNoise serves collected static assets from the same Gunicorn container.

Liveness uses `GET /health/live/`; database-backed readiness uses `GET /health/ready/` (and compatibility alias `/health/`).

## Security model

- Gateway, setup, OAuth access, refresh, and authorization-code values are random opaque values stored only as SHA-256 hashes. Authorization codes and access tokens expire; refresh tokens rotate and reuse revokes their family.
- Remote OAuth credentials, GitLab PATs, and integration headers are encrypted at rest and never included in web pages or MCP results. Juraguard constructs GitLab's `PRIVATE-TOKEN` header server-side and redacts token-like provider fields.
- MCP OAuth uses protected-resource and authorization-server discovery, exact registered redirects, PKCE S256, state passthrough, browser login/approval, and exact audience binding to the canonical `/mcp/` URL. Dynamic registration accepts constrained public clients only; Client ID Metadata Documents are preferred.
- Setup links expire after 15 minutes, are invalidated when replaced, and become unusable after a successful connection.
- Web mutations require authenticated POST forms with CSRF protection.
- `/mcp/` validates bearer authentication, JSON-RPC shapes, request size, and browser `Origin`.
- Every upstream call resolves and validates DNS, then connects to that exact validated IP while preserving the original HTTPS hostname for Host, SNI, and certificate checks. Loopback, private, link-local, multicast, unspecified, reserved, and metadata destinations are blocked because only globally routable addresses pass by default.
- Upstream URLs reject userinfo, query strings, and fragments. HTTPS is mandatory unless `ALLOW_PRIVATE_NETWORKS=true`.
- Upstream calls have finite connect/read timeouts, a 2 MiB response cap, and redirects disabled.
- Only the selected integration's credentials are sent upstream. Juraguard's own OAuth and fallback bearer tokens are never forwarded.
- Errors returned to agents omit response bodies, credentials, and internal exception details.

`ALLOW_PRIVATE_NETWORKS=true` deliberately relaxes SSRF protection for trusted self-hosted networks. Use it only when all gateway users are trusted and network access is intended.

## Client setup

Juraguard supports Claude Code/Desktop, Cursor, VS Code/GitHub Copilot, Codex CLI, OpenCode, Gemini CLI, Windsurf, and any Streamable HTTP MCP client.

**OAuth is the preferred setup for every supported AI client.** Use only your endpoint, such as `https://juraguard.example.com/mcp/`; the client opens browser login and approval. Do not add an `Authorization` header unless the client cannot use OAuth.

### Claude Code

```sh
claude mcp add --transport http juraguard https://juraguard.example.com/mcp/
```

Run `/mcp` in Claude Code, authenticate `juraguard`, then verify with `claude mcp get juraguard`.

For Claude Desktop, open **Customize → Connectors → Add custom connector**, enter the same URL, then complete browser approval.

### Cursor

```sh
agent mcp add
```

Choose HTTP, name it `juraguard`, and enter `https://juraguard.example.com/mcp/`. Run `agent mcp login juraguard`, then `agent mcp list`.

### VS Code / GitHub Copilot

```sh
code --add-mcp '{"name":"juraguard","type":"http","url":"https://juraguard.example.com/mcp/"}'
```

VS Code opens browser approval on first connection; verify with **MCP: List Servers**.

### Codex CLI

```sh
codex mcp add juraguard --url https://juraguard.example.com/mcp/
codex mcp login juraguard
```

Verify with `codex mcp list`.

### OpenCode

```sh
opencode mcp add juraguard --url https://juraguard.example.com/mcp/
```

OpenCode starts OAuth automatically when you first use Juraguard. To connect immediately, run `opencode mcp auth juraguard`.

### Gemini CLI

```sh
gemini mcp add --transport http juraguard https://juraguard.example.com/mcp/
```

Start Gemini, run `/mcp auth juraguard`, then verify with `gemini mcp list`.

### Windsurf

Windsurf has no supported installer command. Open **Windsurf Settings → Tools → Add Server**, enter `https://juraguard.example.com/mcp/`, complete browser approval, then refresh the MCP server list.

### Generic Streamable HTTP

Clients without MCP OAuth can use a manually generated fallback token. Keep it outside version control:

```sh
export JURAGUARD_TOKEN='...'
curl https://juraguard.example.com/mcp/ \
  -H "Authorization: Bearer $JURAGUARD_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

The running application also provides copyable setup examples at `/docs/`.

## Scope and positioning

The current product supports direct GitLab and remote HTTP MCP integrations. Self-hosting intentionally excludes stdio process execution, WebSocket, SSE GET endpoints, Redis-backed sessions, AI/embedding search, teams, marketplace, policy engines, cloud billing, cloud provisioning, and magic-link login.

Self-hosting is unlimited. The managed service is currently a free beta. Final pricing will be announced before the beta ends; beta users receive 50% off their first 12 paid months.

## Development and tests

Python 3.13 is the supported source-development version for the container-only public beta.

```sh
cd juraguard
python -m venv /tmp/juraguard-venv
/tmp/juraguard-venv/bin/pip install --require-hashes -r requirements-dev.lock
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py migrate
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py check
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py test
/tmp/juraguard-venv/bin/ruff check .
```

Tests mock upstream HTTP; they do not require network access. CI runs Django checks, migration drift checks, tests, Ruff, then builds and smoke-tests the container across a restart with persistent `/data`. Registry credentials are optional for fork validation; pushes occur only when credentials are available.

Validate deployment scripts and production Compose without creating a repository `.env` file:

```sh
bash deploy/check.sh
```

The check runs script safety tests, checksum verification, `bash -n`, ShellCheck when installed, and required `docker compose config` validation.

## Documentation

- [Documentation index](docs/README.md)
- [Single-host Debian/Ubuntu and Hetzner self-hosting](docs/self-hosting.md)
- [Operations, backup, recovery, and rollback](docs/operations.md)
- [Contributing](CONTRIBUTING.md), [support](SUPPORT.md), [GitHub mirror operations](docs/github-mirror.md), [security policy](SECURITY.md), and [code of conduct](CODE_OF_CONDUCT.md)
- [CI helper reference](ci-helpers/README.md) and [optional backup helper reference](ci-helpers/backup/README.md)

The `ci-helpers` documentation belongs to shared, vendored Pyango CI tooling. Some examples describe services not present in Juraguard; it is maintainer reference, not Juraguard setup guidance.

## Contributing

After the GitHub mirror is published, issues and pull requests will be welcome there. See [CONTRIBUTING.md](CONTRIBUTING.md) for the project workflow and [SUPPORT.md](SUPPORT.md) for support boundaries.

## License

All Juraguard source, including organization-control and cloud code, is licensed under the [GNU Affero General Public License v3.0](LICENSE). Network users must be offered the corresponding source for modified deployments.
