# JuraGuard open-source readiness audit

**Audit date:** 2026-09-01 (re-audit of the 2026-08-31 assessment)
**Baseline commit:** `cc64c35`
**Remediation branch:** `chore/open-source-audit` / GitLab MR `!7`
**Verdict:** **Not ready for public release**
**Exit rule:** close every P0 and P1 item before making the project or registry public. Resolve P2 items before calling the project stable, or record an explicit owner decision and follow-up for each deferral.

## Executive summary

The 2026-09-01 re-audit found that the prior assessment closed important application risks but overstated release safety. Merge-request child pipelines could still replace mutable images, default-branch pipelines still mutated release tags, production Traefik retained a writable Docker API path, release workflows lacked blocking supply-chain checks, and public source and release artifacts remained unavailable.

MR `!7` addresses the repository-controlled findings and corrects public claims. Public release remains blocked until its full QA passes and operators publish and verify the GitHub mirror, GHCR image, immutable installer/checksums, private vulnerability reporting, repository metadata, full-history secret scan, and required GitLab project settings. A finding is not closed merely because code exists until its acceptance evidence passes.

## Re-audit findings — 2026-09-01

| ID | Severity | Finding | MR `!7` target / remaining acceptance |
|---|---|---|---|
| RA-001 | P0 | MR-triggered child pipelines can publish mutable `latest` images. | Enforce protected-default rules on publishing templates and jobs; prove an MR pipeline has no release publisher. |
| RA-002 | P1 | Default-branch `auto-changelog` and `auto-tagging` can tag a stale pipeline SHA. | Remove implicit release mutation; use only an explicit protected SemVer release workflow. |
| RA-003 | P1 | Root Git and Docker contexts do not exclude `.env*`. | Exclude all environment files while retaining `!.env.example`; verify no image context can include secrets. |
| RA-004 | P1 | Production Traefik mounts the Docker socket; read-only filesystem mode does not make the Docker API read-only. | Use a fixed file-provider route with no Docker socket; pass Compose and HTTPS route checks. |
| RA-005 | P1 | Public CI/release lacks blocking dependency, secret, license, and container vulnerability gates. | Add reproducible blocking gates and make release publication depend on them. |
| RA-006 | P2 | Installer and deployment resources rely on mutable network downloads without complete checksum/provenance verification. | Require immutable release assets, checksum verification, review-before-execute instructions, and provenance validation before public release. |
| RA-007 | P2 | `deploy/check.sh` is absent from public CI/release acceptance. | Run it as a blocking check in both workflows. |
| RA-008 | P2 | Privileged helper images/downloads include floating inputs. | Pin only verifiable inputs; track any source without a trustworthy immutable identifier. |
| RA-009 | P2 | Default console email can expose live verification/recovery links in logs. | Fail safe: require operational SMTP or disable email-dependent delivery without logging secrets. |
| RA-010 | P2 | Backup archives contain configuration and credential-decryption material without archive-level encryption. | Require encrypted off-host storage now; ship archive encryption only with a robust, separately keyed, tested restore path. |
| RA-011 | P2 | Integration remote URLs can persist query strings, including query credentials. | Reject every query string at the trust boundary and migrate/validate existing rows. |
| RA-012 | P3 | Release output is amd64-only while installer accepts other architectures. | Declare `linux/amd64` and reject unsupported hosts before mutation. |
| RA-013 | P0 | The GitHub repository is public but empty; GHCR `0.1.0`, tag `v0.1.0`, and installer endpoint are unavailable. | Operator action: publish and verify all surfaces from a logged-out clean environment. |
| RA-014 | P1 | Public confidential security reporting and GitHub repository metadata are not operational. | Enable private vulnerability reporting; publish policy/templates; set description, homepage, and topics. |
| RA-015 | P1 | README pricing contradicted the live free-beta offer. | Use the product decision: managed beta is free; final pricing is TBD. |
| RA-016 | P1 | Dependency license inventory, attribution, support boundary, and hash-locked contributor setup were incomplete. | Add a blocking license gate/inventory, Pyango GmbH attribution, no-SLA community support boundary, and hash-locked install command. |
| RA-017 | P2 | Changelog classified only `v0.0.1`–`v0.0.5` as unsupported internal snapshots. | Classify `v0.0.1`–`v0.0.7` consistently. |

The original findings remain below for historical traceability. Evidence line numbers refer to the earlier baseline unless the re-audit table says otherwise.

## Severity

| Level | Meaning |
|---|---|
| P0 | Stop-ship. Immediate compromise, unusable public distribution, or unresolved right to distribute. |
| P1 | Release blocker. Must close before public visibility or a supported public release. |
| P2 | Required for a mature/stable project; may defer only with an explicit owner decision and tracked follow-up. |
| P3 | Later hardening or optional community enhancement. |

## P0 stop-ship findings

### OSS-001 — MR pipelines publish unreviewed `latest` images

- **Evidence:** `juraguard/.gitlab-ci.yml:71-72`; `ci-helpers/jobs/push.yml:12-16`. MR pipeline `374103` ran successful `push-latest` jobs for the JuraGuard, backup, and CI-helper child pipelines.
- **Impact:** unmerged contributor code can replace the mutable image recommended to users. This is a direct software-supply-chain compromise path.
- **Fix:** permit `push-latest` only from the protected default branch after all required checks. Never publish `latest` from merge-request, fork, or tag pipelines.
- **Acceptance:** an MR pipeline has no `push-latest` job and does not change the registry digest; a successful protected-default pipeline updates it.

### OSS-002 — public source and image are inaccessible

- **Evidence:** GitLab project `386` has `visibility: private`; anonymous access to documented source and registry paths fails. `README.md:13,18,35-39,42`, `deploy/install.sh:25,94`, and `docs/self-hosting.md:20,33,37,103` depend on those paths.
- **Impact:** the public cannot inspect source, pull the image, install, report issues, fork, or contribute.
- **Fix:** after every other stop-ship item is closed, publish one canonical repository and image registry. Keep release infrastructure private until then.
- **Acceptance:** from a logged-out clean environment, clone source, pull the supported image, run the quickstart, and open issue/MR pages without private Pyango access.

### OSS-003 — shipped licensing model is unresolved

- **Evidence:** `juraguard/LICENSE-COMMERCIAL:1-5` is a terms placeholder; `juraguard/config/settings.py:99-113` always installs the commercial app; `juraguard/gateway/views.py:17-19` imports it; `juraguard/Dockerfile:15` copies it into the distributed image. `README.md:67-80` describes separation that the artifact does not provide.
- **Impact:** redistribution rights and the license of the combined source/image are unclear. Calling the artifact open source may be inaccurate.
- **Fix:** owner and counsel choose one model: AGPL-only; a genuinely separable open-core build; or reviewed dual licensing. Replace placeholder terms and make source, image, README, and UI consistent.
- **Acceptance:** either the AGPL build passes without proprietary files, or counsel-approved terms clearly cover every shipped file and combination; automated license inventory has no unresolved item.

## P1 release blockers

### Security and product authorization

| ID | Evidence | Gap and impact | Minimal remediation | Acceptance |
|---|---|---|---|---|
| SEC-001 | `juraguard/gateway/views.py:64-75`; `deploy/compose.production.yml:17-19,63-69` | First-owner creation is public and unauthenticated. A scanner can claim a fresh self-hosted instance before its operator. | Require a generated one-time bootstrap secret out of band, or bind setup to localhost until claimed. | Remote setup without the secret fails; correct secret succeeds once and is invalidated. |
| SEC-002 | `juraguard/gateway/oauth_server.py:83-100`; `gateway/protocol.py:123-132`; `gateway/tool_defs.py:40-92`; `gateway/dispatch.py:223-241` | One `mcp` grant covers reads, integration administration, deletion, and enabled upstream writes; consent does not expose that authority. | Split read, call, write, and manage scopes; default tokens exclude administration and writes; show requested capabilities. | Basic token cannot manage integrations or call writes; explicitly approved scopes can. |
| SEC-003 | `juraguard/docker/entrypoint.sh:6`; `gateway/remote.py:14-15,194-215`; `gateway/outbound.py:15,51-73` | Two synchronous workers can both be held by slow upstream calls, blocking other tenants and health/login traffic. | Isolate outbound work or use suitable async workers; add per-user/integration concurrency limits and hard total deadlines. | Slow calls by one tenant do not materially delay another tenant's health/login request. |
| SEC-004 | `gateway/oauth_server.py:31-33,219-237`; `gateway/hardening.py:24,118-119` | Anonymous dynamic registration has a global 1,000-row cap. Attackers can exhaust it for up to 30 days. | Add per-source quotas and safe expiry/LRU behavior, reserve capacity, or use stateless signed registrations. | Legitimate registration still succeeds after hostile cap-filling attempts. |
| SEC-005 | `gateway/oauth_server.py:415-432`; `gateway/urls.py:17-32,39-45`; `templates/gateway/oauth_approve.html:12` | Owners cannot list or revoke clients/token families without possessing the compromised raw token. A stolen refresh token remains usable for up to 30 days. | Add owner-side client/session listing and immediate family/client revocation. | Owner revokes a client without its token; all access and refresh tokens fail immediately. |

### Distribution, licensing, and release integrity

| ID | Evidence | Gap and impact | Minimal remediation | Acceptance |
|---|---|---|---|---|
| REL-001 | `README.md:10-14,35-39`; `templates/gateway/base.html:35` | Source, image, legal, and UI links use the moved `pyango/mcp-gateway/monorepo` path. Registry redirects are not reliable. | Replace every old path with the chosen canonical public path. | Automated link scan passes and clean anonymous install uses no redirect/private access. |
| REL-002 | `README.md:18,42,48`; `ci-helpers/jobs/push.yml:9-10` | Git tag `v0.0.3` becomes image tag `v0-0-3`, contradicting documented matching tags. | Publish exact `$CI_COMMIT_TAG` for tag pipelines; use slug only for branch-only aliases. | Tag `vX.Y.Z` produces pullable image `:vX.Y.Z`. |
| REL-003 | `ci-helpers/jobs/security.yml:7-24`; `juraguard/.gitlab-ci.yml:23-88` | Container scan is an unused, fail-open template. No active dependency, secret, SAST, or license scan gates release. | Enable appropriate scanners and fail protected branches/releases at documented thresholds. | Seeded critical dependency/secret fails the pipeline; reports remain available. |
| REL-004 | `juraguard/requirements.txt:1-7`; `requirements-dev.txt:1-2`; `Dockerfile:8-9` | Direct pins do not lock transitives or artifact hashes. Builds are not deterministic and index compromise is less detectable. | Generate complete hashed locks/constraints and install with hash enforcement. | Two clean builds resolve identical versions/hashes; altered or missing hash fails. |
| REL-005 | `CHANGELOG.md:5`; `ci-helpers/scripts/auto_tag.sh:35-52`; `ci-helpers/jobs/tagging.yml:11-15` | Every default-branch commit can release; marker names map incorrectly to SemVer components. | Replace implicit tagging with explicit protected release workflow and tested major/minor/patch mapping. | Ordinary merge creates no release; automated bump tests cover all levels. |
| REL-006 | `ci-helpers/scripts/auto_changelog.sh:212-216`; `ci-helpers/scripts/auto_tag.sh:23-33,55-68`; `ci-helpers/jobs/tagging.yml:1-36` | Changelog creates an untested skipped commit, while the older commit is tagged. Released source omits its own generated changelog. | Generate release notes without branch mutation, or test and tag the exact release commit. | Tagged source contains its release changelog and equals the fully tested SHA. |
| REL-007 | `SECURITY.md:3-6`; `CHANGELOG.md:3-14`; existing tags `v0.0.1`–`v0.0.3` | Policy says releases have not begun while tags exist; versions are under Unreleased. Support and upgrade expectations are unknowable. | Publish dated release sections and supported/EOL policy aligned with actual tags. | Every tag is documented and explicitly supported or EOL. |
| REL-008 | `README.md:18`; `ci-helpers/jobs/push.yml:9-10` | Docs promise commit-SHA images, but CI publishes only mutable ref-slug tags. | Publish immutable commit-SHA tags and expose digest in release metadata. | SHA tag exists and matches release/default digest. |
| REL-009 | `LICENSE:4`; absent `AUTHORS`/license inventory; unlocked transitive dependencies | Project ownership, attribution duties, and dependency compatibility have not been established. | Record copyright owner/year and inbound contribution policy; generate SBOM/license report; add NOTICE where required. | Owner/legal sign-off and CI license scan show no unresolved or forbidden license. |
| REL-010 | `ci-helpers/backup/database-duplicati-config.json:28`; `ci-helpers/hetzner/*.sh`; `ci-helpers/jobs/security.yml:2`; `docs/research/helios-reference.md` | Public tree contains private hostnames, usernames, infrastructure assumptions, and internal-only CI dependencies. No secret was found in sampled files, but history is unscanned. | Remove/sanitize internal operations artifacts and scan all Git history before publication. | Repository/history scan reports no secret or non-public endpoint; public CI needs no VPN/private registry. |
| REL-011 | `README.md:207-219`; `requirements.txt:5` | “Python 3.12+” is unbounded; installation failed on Python 3.14.6 because pinned psycopg binary was unavailable. | Publish an exact tested support range or update dependencies and CI for newer Python. | Fresh install and checks pass on every documented Python version. |

### GitLab project controls

| ID | Evidence | Gap and impact | Minimal remediation | Acceptance |
|---|---|---|---|---|
| GL-001 | Project setting `only_allow_merge_if_pipeline_succeeds: false` | GitLab permits merging failed or untested commits even after CI gates are repaired. | Require a successful pipeline and prohibit skipped-pipeline merges. | MR with failed/skipped required checks cannot merge. |
| GL-002 | `ci_allow_fork_pipelines_to_run_in_parent_project: true`; MR pipelines currently publish images | Untrusted fork code can execute in the parent pipeline context. Existing release jobs turn this into a registry integrity risk even if protected variables remain masked. | Use a secret-free external-contributor pipeline; prohibit all registry/deploy/release jobs for fork and MR pipelines; review runner isolation. | Fork MR can run lint/tests but receives no release credentials and cannot mutate packages, images, environments, or repository state. |
| GL-003 | Protected `master` allows Maintainers to push and merge; no CODEOWNERS evidence; project allows merging with unresolved discussions | Direct pushes and unreviewed sensitive changes remain possible; ownership and review enforcement are not encoded. | Deny direct pushes, define CODEOWNERS for security/release areas, require appropriate approvals and resolved discussions. | Sensitive change requires independent owner approval; unresolved discussions and direct pushes block integration. |

## P2 stable-readiness findings

These are not permission to publish with unresolved P0/P1 items. Each P2 item must be closed before a stable release or explicitly accepted by the owner in a public tracker.

### Security hardening and lifecycle

| ID | Evidence | Gap / action | Acceptance |
|---|---|---|---|
| SEC-101 | `gateway/hardening.py:20-35,71-127`; `gateway/views.py:273-277`; `gateway/oauth_server.py:219-237` | IP-only process-local throttles are bypassable in a distributed deployment and unfair behind NAT. Add shared per-user/workspace quotas, integration/catalog caps, and concurrency limits. | Limits hold across replicas and distinguish users behind one address. |
| SEC-102 | `gateway/outbound.py:51-73` | Total deadline is checked only after reads while each read gets a fresh timeout. Set each operation timeout to the remaining monotonic deadline. | Drip-fed response cannot exceed configured total deadline. |
| SEC-103 | `gateway/views.py:331-345`; `commercial/views.py:8-44` | Public license and billing webhook endpoints read request bodies without endpoint-specific byte/rate limits. Reject oversized bodies with 413 and excess attempts with 429. | Oversized/rapid requests are bounded before expensive parsing or signature work. |
| SEC-104 | `gateway/models.py:177-211`; `gateway/views.py:218-264` | Setup link is consumed before upstream validation; transient failure makes the documented retry impossible. Consume only after success or issue a safe replacement. | One transient upstream failure can be retried, while replay after success fails. |
| SEC-105 | `gateway/models.py:177-226`; `commercial/models.py:94-121` | No scheduled cleanup/retention policy exists for expired setup links, auth codes, access/refresh tokens, or webhook events. Define periods and purge jobs. | Expired records are purged on schedule and policy is documented. |
| SEC-106 | `gateway/forms.py:59-91`; `gateway/models.py:69-74`; `gateway/views.py:190-202` | Remote URL query strings are stored and returned in plaintext; users may put credentials there. Reject credential-like/all query values or store/display only redacted values. | Secret-like query is rejected; no response, admin page, or log reveals it. |
| SEC-107 | `commercial/service.py:34-49` | Commercial license validation can follow redirects and read unbounded responses. Pin exact origin, disable redirects, require content type, and cap bytes/deadline. | Redirect, wrong type, and oversized response fail safely. |
| SEC-108 | `gateway/protocol.py:32-62`; JSON-RPC entry points | Object/array JSON-RPC IDs and deeply nested JSON can trigger invalid behavior or 500s. Validate scalar IDs and bound body/depth before dispatch. | Invalid IDs/depth return bounded protocol errors, never 500. |
| SEC-109 | `gateway/views.py:140-217`; `gateway/models.py:69-94` | Switching integration type or remote URL can retain stale credentials and connection state. Clear incompatible state and require reconnect. | Type/URL change makes old credentials unusable and absent from storage/response. |
| SEC-110 | Setup/OAuth capability responses and HTML views | Sensitive setup and authorization responses lack an explicit no-store/referrer-policy/CSP baseline. Add route-appropriate headers. | Browser/network assertions confirm secrets are not cached or leaked by referrer. |

### CI, release, and supply chain

| ID | Evidence | Gap / action | Acceptance |
|---|---|---|---|
| REL-101 | `juraguard/.gitlab-ci.yml`; no generated release artifacts | No SBOM, provenance, attestation, or signing is published. Generate CycloneDX/SPDX SBOMs and sign immutable images/releases with verifiable provenance. | Logged-out consumer verifies signature, source SHA, builder identity, digest, and SBOM. |
| REL-102 | `deploy/check.sh`; current CI jobs | Deployment validation and ShellCheck are not enforced in CI. Run `deploy/check.sh`, ShellCheck, and Compose config validation on relevant changes. | Deliberately invalid shell/Compose change fails pipeline. |
| REL-103 | Current CI test job | CI does not prove the documented Python range. Test the oldest and newest supported versions, initially 3.12 and 3.13 unless support policy chooses otherwise. | Both clean matrix jobs install and pass checks/tests. |
| REL-104 | Current test settings use SQLite | Production PostgreSQL behavior and migrations are not integration-tested. Add a PostgreSQL job including upgrade migration rehearsal. | Fresh and upgrade-path DB tests pass on supported PostgreSQL versions. |
| REL-105 | No coverage report/gate | Regressions can silently remove meaningful test coverage. Publish coverage and enforce a justified non-decreasing floor. | MR reports changed coverage and blocks material unexplained regression. |
| REL-106 | ARM image template is unused/fail-open; no support statement | OS/CPU support is unstated and ARM is not proven. Declare supported platforms; build/test each or explicitly mark unsupported. | Manifest and docs exactly match tested architectures. |
| REL-107 | Docker build and unlocked OS/package inputs | Images are only partly reproducible. Pin build inputs by digest/version and document reproducible-build limits. | Two builds from same source have explainable/controlled differences and same resolved dependencies. |
| REL-108 | No dependency-update automation | Pinned dependencies can age unnoticed. Add controlled update automation with tests, security priority, and release-note review. | Scheduled updates open reviewable MRs; stale/vulnerable pins alert owners. |
| REL-109 | `CHANGELOG.md` | Changelog lacks dated, versioned sections and standard Added/Changed/Fixed/Security structure. Adopt a consistent release-note format. | Every release has complete user-facing notes and upgrade/security callouts. |
| REL-110 | Public CI depends on private runners/analyzer/registry paths | Forks cannot safely reproduce the required checks without Pyango infrastructure. Provide a secret-free public pipeline and isolate privileged release pipeline. | External fork runs all contribution-required checks without company network access. |
| REL-111 | `SECURITY.md` | Security contact has no fallback, response target, disclosure/CVE process, or optional encryption key. Publish maintainable commitments. | Reporter can reach a monitored channel and policy defines acknowledgement, coordination, and CVE flow. |
| REL-112 | Registry cleanup disabled; image tags mutable | Retention, immutability, and rollback guarantees are undefined. Protect release tags/digests and adopt cleanup rules that preserve supported releases. | Supported digest survives cleanup; release tag cannot be overwritten. |

### Deployment and operations

| ID | Evidence | Gap / action | Acceptance |
|---|---|---|---|
| OPS-101 | `deploy/compose.production.yml`; `docker-compose.deploy.yml` | No CPU, memory, or PID bounds. Add conservative configurable limits and capacity guidance. | Resource exhaustion is contained and alerts before service loss. |
| OPS-102 | Container definitions | App lacks read-only filesystem, dropped capabilities, and `no-new-privileges`; Traefik receives raw Docker socket. Harden containers and replace/proxy socket access where practical. | Runtime works with least privilege; compromise test cannot write image FS or control unrelated containers. |
| OPS-103 | Production compose passes secrets as environment values | Secrets can leak through process/container inspection. Support Docker secrets or `_FILE` inputs and document secure permissions/rotation. | Secret values are absent from container environment and survive safe rotation. |
| OPS-104 | Compose uses `/health/`; image health check uses `/health/ready/` | Health contracts disagree. Use explicit liveness/readiness endpoints consistently and document dependencies. | Orchestrator tests distinguish alive process from unavailable database. |
| OPS-105 | No metrics/export/alert path in repository | Operators lack visibility into latency, saturation, auth abuse, upstream failures, DB health, and license/webhook failures. Add minimal metrics and alert runbook without secret labels. | Fault injection produces actionable signal with bounded cardinality and no sensitive data. |
| OPS-106 | Persistent directories/volumes in Compose | Ownership, capacity, restore ownership, and backup guarantees are not encoded or tested. Document and validate volume permissions, free space, and restore behavior. | Fresh/restore deployment starts under non-root UID and capacity thresholds alert. |
| OPS-107 | `deploy/install.sh:94-104,115-117,133-139`; `docs/self-hosting.md:16-27,41` | Installer defaults deploy assets to mutable `master`, despite image digest pinning and managed-file drift checks. Default to release/commit ref and publish signed checksums. | Install from release tag; tampered managed file aborts unless explicitly replaced; image remains digest-pinned. |
| OPS-108 | `deploy/backup.sh:11-24,39-64`; `docs/operations.md:22-24` | Backup archive is mode `0600` but unencrypted and contains config, state, install files, and optional DB dump. Encrypt it or require encrypted off-host storage. | Off-host archive cannot be read without its separate key. |
| OPS-109 | `gateway/migrations/0005_backfill_personal_workspaces.py:5-12`; `0006_enforce_workspace_ownership.py:14-30` | Static review cannot prove a real production dataset survives the backfill/schema-removal upgrade path. Rehearse on a disposable production-like clone. | Clone upgrade reaches healthy state; data checks and rollback/restore succeed. |
| OPS-110 | Review-app Traefik image/reference and ACME defaults | Review proxy inputs are mutable and company ACME identity is hardcoded. Pin proxy assets and make operator identity explicit. | Review deployment resolves immutable proxy image and operator-owned ACME contact. |
| OPS-111 | `deploy/compose.production.yml:46-48`; `config/settings.py:41,47-49,142-159`; `commercial/crypto.py:16-31` | Empty key/DB variables intentionally fall back to persistent generated keys and SQLite, but preflight does not prove `/data` is durable or state the single-node SQLite ceiling. Validate persistence and document when PostgreSQL is required. | Restart decrypts existing data; missing/unwritable volume fails before startup; scale guidance selects PostgreSQL. |
| OPS-112 | `deploy/compose.production.yml:49-55`; `config/settings.py:195-211` | Self-hosted mode defaults to console email, making verification/recovery links operationally unsafe or unusable unless SMTP is configured. Require SMTP for those flows or explicitly disable them and surface degraded status. | Recovery reaches a test mailbox; console mode never emits live secret links. |
| OPS-113 | `juraguard/docker/entrypoint.sh:4-5` | Every app process runs migrations and `collectstatic` at startup. This is acceptable for the current single-instance baseline but unsafe for multi-replica rollout. Move schema upgrade to one explicit job before supporting scale-out. | Multi-replica mode starts no concurrent migration; failed upgrade blocks app replacement. |
| OPS-114 | `juraguard/docker/entrypoint.sh:6` | Gunicorn access logs are discarded. Emit structured redacted access/error logs with request IDs. | Normal and failed requests are visible without tokens, cookies, credentials, or sensitive query values. |
| OPS-115 | `juraguard/docker-compose.deploy.yml:3` | Internal cloud deployment uses mutable branch-slug image tags, although the public installer pins its selected image by digest. Pin cloud deployments and rollback metadata by digest too. | Repeated deploy of one release uses one digest and can select a known prior digest. |

### Documentation, community, governance, and accessibility

| ID | Evidence | Gap / action | Acceptance |
|---|---|---|---|
| GOV-101 | README, package/UI names, moved GitLab project, documented domains | Canonical product name, repository path, registry, website, support, and commercial domains are not consistently declared. Choose them and replace all aliases/stale links. | One source-of-truth table and automated link check match every public surface. |
| GOV-102 | Missing `GOVERNANCE.md`, `MAINTAINERS.md`, and `SUPPORT.md` | Decision rights, maintainer admission/removal, succession, support boundary, and escalation are undefined. Publish lean policies with named current owners. | Contributor can determine who decides, reviews, releases, supports, and takes over an inactive project. |
| GOV-103 | No GitLab issue/MR templates | Reports will omit reproduction, security, compatibility, release-note, and checklist information. Add minimal bug, feature, and MR templates; route vulnerabilities privately. | New issue/MR forms collect actionable fields and warn against public secret/security reports. |
| GOV-104 | `CONTRIBUTING.md` | Contribution checks are clear, but fork/branch/review mechanics and inbound licensing choice are missing. Choose DCO, CLA, or explicit inbound=outbound and document it. | External contributor can submit a compliant fork MR without private knowledge. |
| GOV-105 | `juraguard/.env.example` / example environment files | Example focuses on commercial variables and does not present one safe, complete baseline for required production settings. Publish validated minimal and optional sections with no real secret. | Copying the example plus generated secrets passes preflight and starts a safe baseline. |
| GOV-106 | README product/status/pricing language | Maturity, roadmap, stable-support promise, and commercial beta/pricing relationship are ambiguous. Publish current status and support/EOL expectations without speculative promises. | User can tell whether deployment is experimental/stable, what is free, and what support exists. |
| GOV-107 | Forgot-password UI and invalid-form markup | Forgot-password link target is below WCAG 2.2 target-size guidance; invalid fields need verified `aria-invalid` and `aria-describedby` linkage. Fix and keyboard/screen-reader test auth flows. | Automated and manual checks confirm labels, error announcement, focus order, and adequate targets. |
| GOV-108 | GitLab project metadata is empty; cleanup and squash defaults are off | Public discovery and contribution defaults are weak. Add description/topics/avatar; choose squash policy; enable appropriate registry cleanup after immutability rules. | Logged-out project page explains the product and default MR/repository behavior matches contribution docs. |

## P3 later improvements

- Add OCI image labels for source, revision, version, license, authors, and documentation.
- Explicitly declare whether distribution is container-only or a supported Python package; do not imply an unpublished package channel.
- Publish source-asset provenance and a `NOTICE` file when the completed dependency/asset inventory requires one.
- Avoid fixed container names and all-interface development binds where parallel or shared-host development matters.
- Add an independent Code of Conduct escalation/appeal contact rather than routing every case to project leadership.
- Maintain a user-facing changelog distinct from internal release mechanics if operator notes become too technical.
- Add `FUNDING.yml` and `CITATION.cff` only if funding and academic citation are genuinely supported.
- Separate historical/internal research documents from the public support documentation set.

## Verified strengths to preserve

- Outbound requests validate global IPs, pin resolution, preserve TLS SNI/hostname verification, disable redirects, bound responses, and defend against rebinding.
- OAuth uses random opaque values, hash-only token storage, PKCE, resource binding, refresh rotation, and replay-triggered family revocation.
- Credential encryption supports key rotation and permission checks; tenant scoping and CSRF protections are consistently present.
- Logs and tool responses redact credentials; GitLab paths are schema-validated/quoted, upstream writes are gated, and token-like responses are filtered.
- Billing webhooks are signed and idempotent.
- Container runs as non-root and base image is digest-pinned.
- Restore rejects unsafe archive paths, links, and special files; requires explicit confirmation for a database-target mismatch; creates a pre-restore backup.
- Liveness/readiness are cleanly split; readiness checks DB reachability without exposing internals.
- Installer already pins the selected application image by digest and detects managed-file drift.
- README, threat model, client examples, self-hosting, operations, and contribution-check documentation provide a solid base.
- Landing page Lighthouse accessibility scored 100 desktop/mobile; language, skip links, landmarks, focus handling, and navigation are present.

## Owner and legal decisions required

Implementation must not guess these:

1. **Distribution:** canonical public forge, registry, installer URL, website, and whether releases are container-only.
2. **License:** AGPL-only, separable open core, or reviewed dual license; exact commercial terms and artifact boundary.
3. **Ownership:** copyright holder/year, trademark ownership/clearance, inbound contribution policy, and required attribution/NOTICE.
4. **Governance:** maintainers, security responders, release authority, succession, Code of Conduct escalation, and approval rules.
5. **Support:** supported versions, Python/PostgreSQL/OS/CPU matrix, EOL duration, response commitments, and community/commercial boundary.
6. **Product:** current maturity, public roadmap, commercial beta/pricing claims, and which capabilities are intentionally proprietary.
7. **Operations:** public CI/runners/registry architecture, production observability baseline, backup encryption responsibility, and data retention periods.

## Recommended release sequence

1. **Contain now:** disable MR/fork `latest` publication and protect registry/release jobs.
2. **Decide legal/product boundary:** resolve licensing, ownership, trademark, inbound contributions, and commercial separation.
3. **Clean public boundary:** choose canonical paths; remove internal infrastructure; scan all Git history; make CI reproducible without private services.
4. **Close security blockers:** protect first-owner bootstrap, split OAuth authority, isolate/bound outbound work, prevent registration exhaustion, add owner revocation.
5. **Build trusted releases:** hashed locks, mandatory scanners/tests, immutable SHA/SemVer images, explicit release workflow, SBOM/signing/provenance, accurate changelog/support policy.
6. **Harden deployment:** fail-closed configuration, SMTP behavior, one-run migrations, immutable deploys, logs/metrics/alerts, least privilege, secrets, backups, upgrade rehearsal.
7. **Complete community surface:** governance/support/maintainer docs, templates, contribution licensing, accessibility fixes, and project metadata.
8. **Private release candidate:** reproduce install, upgrade, rollback, token compromise, abuse, backup/restore, and fork-CI tests from clean environments.
9. **Publish last:** change visibility only after P0/P1 closure, owner sign-off, and a signed immutable release candidate.

## Public-release exit checklist

### Rights and public boundary

- [ ] `OSS-002`, `OSS-003`, `REL-001`, `REL-009`, and `REL-010` closed with owner/legal evidence.
- [ ] Every file and dependency in the distributed source/image has an identified compatible license.
- [ ] Full-history secret/internal-endpoint scan completed; any exposed credential rotated before publication.
- [ ] Logged-out user can clone, inspect license, pull an immutable image, verify it, install it, and submit an issue/MR.

### Security and abuse resistance

- [ ] `SEC-001` through `SEC-005` closed with automated negative tests.
- [ ] Fresh instance cannot be remotely claimed without its one-time operator secret.
- [ ] Consent and tokens expose least-privilege read/call/write/manage authority.
- [ ] Slow upstream, registration flood, token theft, and cross-tenant tests preserve service and containment.
- [ ] Owner can enumerate and immediately revoke active client/session families.

### CI and release

- [ ] `OSS-001`, `REL-002` through `REL-008`, `REL-011`, and `GL-001` through `GL-003` closed.
- [ ] Fork/MR pipeline cannot push image/package/tag/commit, deploy, or access protected context.
- [ ] Required lint, tests, PostgreSQL migration, deploy checks, and security/license scans pass on supported platforms.
- [ ] Release source SHA, changelog, SemVer tag, immutable image digest, SBOM, provenance, and signature refer to the same tested build.
- [ ] Supported releases and EOL dates are accurate; registry cleanup cannot delete or mutate them.

### Production and recovery

- [ ] Stable-readiness operations items have no unaccepted deferral.
- [ ] Preflight rejects missing keys/database/mail requirements before starting or mutating data.
- [ ] Multi-replica rollout runs one controlled migration; rollback procedure and compatibility window are documented.
- [ ] Access/error logs are useful and redacted; minimum metrics/alerts cover saturation, failures, auth abuse, and DB health.
- [ ] Encrypted off-host backup, safe restore, and production-like upgrade/rollback rehearsal pass with recorded recovery time.

### Project and community

- [ ] Governance, maintainers, support/EOL, security, contribution licensing, and Code of Conduct escalation have accountable owners.
- [ ] Public project metadata, issue/MR templates, canonical links, safe environment example, maturity, roadmap, and pricing statements agree.
- [ ] Authentication/setup paths pass keyboard and screen-reader checks plus supported browser/device smoke tests.
- [ ] Final owner, legal, security, release, and operations sign-offs are recorded on the release issue.

## Audit method and evidence

The audit reviewed application code, templates, tests, migrations, container files, deployment/backup/restore/install scripts, CI helpers, project metadata/settings, licensing, documentation, and community files at the audited commit. Findings were independently grouped across security/code, build/release, documentation/governance/legal, and operations tracks, then deduplicated here.

Verified checks on the remediation branch:

- Python 3.13 / Django 5.2.17: hash-enforced development dependency installation, `manage.py check`, migration drift check, all 134 Django tests, and Ruff passed in Docker.
- `bash deploy/test-safety.sh`, `bash deploy/check.sh` (including ShellCheck), and `git diff --check` passed.
- GitHub, GitLab CI-helper, and application CI YAML parsed successfully.
- The pinned Python 3.13 runtime image built successfully using the complete hash lock.
- The installed application dependency set passed `pip-audit`; only the ignored virtual-environment bootstrap `pip` advisory remained, and `pip` is not an application runtime dependency.

Baseline evidence retained for context:

- GitLab pipeline `374103` and child build/test jobs passed at the audited commit. That pipeline also demonstrated the former stop-ship `push-latest` behavior; green did not mean release-ready.
- Landing Lighthouse accessibility scored 100 on desktop/mobile; login scored 95. These scores do not replace manual auth-flow testing.

## Not verifiable from this repository alone

These remain release inputs, not assumptions of safety:

- Counsel approval, trademark clearance, contributor ownership, and legal approval of the transitive dependency/license inventory.
- Mandatory CI dependency, container, secret, and license scan results; local dependency audit and complete hash locks do not replace release gates.
- Production WAF, network egress, shared quotas, runner isolation, registry immutability, and secret-protection configuration.
- Polar/commercial-provider SDK and redirect behavior outside reviewed code.
- Off-host backup/log retention, encryption, access control, alert routing, and incident-response staffing.
- Upgrade safety on real historical datasets until clone rehearsal covers every supported release jump.

## Audit totals

- **P0:** 3 stop-ship findings.
- **P1:** 19 release blockers.
- **P2:** 45 stable-readiness findings.
- **P3:** 8 later improvements.

Counts describe tracked gaps, not risk volume. Public release remains blocked until every P0/P1 item and required owner decision is closed with evidence.
