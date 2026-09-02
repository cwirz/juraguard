# CI Helpers

Reusable CI/CD infrastructure for GitLab CI monorepo pipelines. Copy this folder into any project to get a fully working CI/CD setup -- only the service triggers in the root `.gitlab-ci.yml` are project-specific.

## Folder Structure

```
ci-helpers/
  jobs/                        # GitLab CI job templates (includable yml)
    general.yml                #   workflow, stages, .mr, .branch triggers
    all.yml                    #   convenience include for all service job templates
    build.yml                  #   .build, .build-arm64
    pull.yml                   #   .pull (pull cache from registry)
    push.yml                   #   .push, .push-latest, .push-arm64
    deploy.yml                 #   .deploy (production deployment)
    reviewapp.yml              #   .review (review app deployment)
    cleanup.yml                #   .cleanup (remove images after pipeline)
    lint.yml                   #   .lint
    test.yml                   #   .test, .pages-test
    pages.yml                  #   .pages (GitLab Pages artifacts)
    initialize.yml             #   .initialize
    security.yml               #   .container_scanning (GitLab Container Scanning)
    tagging.yml                #   .auto-tagging, .auto-changelog + concrete jobs
    ci_helpers_build.yml       #   ci-helpers, ci-helpers-mr, backup-mr, backup triggers
  hetzner/                     # Hetzner Cloud review app infrastructure
    hetzner_scripts.yml        #   .hetzner-scripts (reusable !reference snippets)
    hetzner_deploy.yml         #   deploy-hetzner-instance, stop-hetzner-instance jobs
    setup.sh                   #   Remote server provisioning (Docker, GitLab Runner, Netbird)
    setup_dns.sh               #   Create/update Hetzner DNS A records
    cleanup_dns.sh             #   Delete Hetzner DNS A records
    setup_firewall.sh          #   Create Hetzner Cloud firewall
    attach_firewall.sh         #   Attach firewall to server
    setup_authentik.sh         #   Create Authentik forward auth provider + outpost
    cleanup_authentik.sh       #   Delete Authentik provider, outpost, app + CI variable
    setup_ssh_connection.sh    #   Wait for SSH, copy authorized keys to server
  docker/                      # CI helper Docker image (Ubuntu + hcloud + Python + AI deps)
    Dockerfile
    requirements.txt           #   Python deps: anthropic, gitpython
    .gitlab-ci.yml             #   Child pipeline: pull, build, push, cleanup
  backup/                      # Backup/restore Docker image (Duplicati + PostgreSQL)
    Dockerfile
    .gitlab-ci.yml             #   Child pipeline: pull, build, push, cleanup
    scripts/                   #   entrypoint.sh, backup.sh, restore.sh
    README.md                  #   Detailed backup/restore documentation
  scripts/                     # Shared helper scripts
    docker_login.sh            #   Docker login (GitLab + Docker Hub registries)
    setup_ssh_keys.sh          #   Setup SSH keys from CI variables
    auto_tag.sh                #   Auto-increment semver tag on default branch
    auto_changelog.sh          #   Generate changelog (AI-powered with fallback)
    ai_changelog_generator.py  #   Claude-based changelog generation
    test_ai_changelog.sh       #   Test script for AI changelog
    yarn_version.sh            #   Yarn version bumping
```

## How the Pipeline Works

### 1. Root `.gitlab-ci.yml` -- the orchestrator

The root pipeline file defines **which services exist** and triggers child pipelines for each one. It includes shared infrastructure from `ci-helpers/` and declares one trigger per service.

```yaml
# .gitlab-ci.yml (root)
include:
  - local: "ci-helpers/root.yml"

# Each service gets two triggers:
# .mr    -- runs on merge requests (CI validation)
# .branch -- runs on default branch + tags (build & deploy)

website-mr:
  extends: .mr
  variables:
    CONTEXT: "website"

website:
  extends: .branch
  variables:
    CONTEXT: "website"

backend-mr:
  extends: .mr
  variables:
    CONTEXT: "backend"

backend:
  extends: .branch
  variables:
    CONTEXT: "backend"
```

`ci-helpers/root.yml` includes everything the root pipeline needs: `general.yml` (workflow rules, stages, `.mr`/`.branch` templates), `tagging.yml` (auto-changelog + auto-tagging), `ci_helpers_build.yml` (build jobs for the ci-helpers and backup images), and the Hetzner review app infrastructure.

The `CONTEXT` variable is the directory name of the service. It is used throughout the templates to locate `Dockerfile`, `docker-compose.deploy.yml`, and to tag images.

### Selective review apps

MR build, lint, test, security, and image-push jobs always follow their normal
rules. Hetzner review infrastructure and jobs extending `.review` run only
when the MR has the exact `review-app` label. Root MR variables such as
`CI_MERGE_REQUEST_LABELS` are available to the service child pipelines.

Each deployment registers an `on_stop` job. Merge/close automatically invokes
teardown, which runs `docker compose down --remove-orphans` without deleting
volumes before the MR server is removed. To disable a running app on an open
MR, stop and verify its service environments first and its infrastructure
environment last, then remove the label. Label removal alone does not reliably
start a pipeline and is not a teardown signal.

### 2. Service `.gitlab-ci.yml` -- the child pipeline

Each service directory has its own `.gitlab-ci.yml` that defines the actual CI jobs. It includes the job templates it needs from `ci-helpers/jobs/`:

```yaml
# backend/.gitlab-ci.yml
include:
  - local: "ci-helpers/jobs/all.yml"    # includes all job templates

stages:
  - pull
  - build
  - lint
  - test
  - push
  - cleanup
  - deploy
  - review

before_script:
  - cd $CONTEXT

workflow:
  name: $CONTEXT
  rules:
    - if: $CI_PIPELINE_SOURCE == "parent_pipeline"
    - if: $CI_PIPELINE_SOURCE == "schedule"
      when: never

# Simple jobs just extend the template
pull:
  extends: .pull

build:
  extends: .build

push:
  extends: .push

cleanup:
  extends: .cleanup

# Deploy jobs can add service-specific setup
deploy-production-rollout:
  extends: .deploy
  before_script:
    - !reference [.deploy, before_script]
    - docker volume create --name=media-files
  script:
    - docker rollout --file docker-compose.deploy.yml -t 600 backend
```

### 3. Job template chain

The templates in `ci-helpers/jobs/` define reusable `.deploy`, `.review`, `.build`, etc. jobs. Each has its own `before_script` that handles common setup (docker login, cd to context, network creation).

When a service extends a template, it inherits everything. If it only overrides `script:`, the template's `before_script` still runs. But if it also overrides `before_script:`, the template's `before_script` is **replaced entirely** -- this is where `!reference` becomes critical.

## The `!reference` Flag -- Critical Pattern

**This is the most important thing to understand when working with ci-helpers.**

GitLab CI does not merge `before_script` arrays when you override them. If a template defines:

```yaml
.deploy:
  before_script:
    - source $CI_PROJECT_DIR/ci-helpers/scripts/docker_login.sh
    - cd $CONTEXT
    - docker network create $NETWORK_NAME || true
```

And your service does this:

```yaml
# BAD -- template before_script is completely lost
deploy-production:
  extends: .deploy
  before_script:
    - docker volume create --name=media-files
```

The docker login, `cd $CONTEXT`, and network creation are **silently dropped**. The job will fail because it's not logged in and not in the right directory.

**The correct pattern** uses `!reference` to pull in the template's `before_script` first:

```yaml
# GOOD -- template before_script runs, then service-specific setup
deploy-production:
  extends: .deploy
  before_script:
    - !reference [.deploy, before_script]
    - docker volume create --name=media-files
```

### When you need `!reference`

Use `!reference` whenever you override `before_script` on a job that extends a template:

```yaml
# Extending .deploy and adding before_script steps
deploy-production:
  extends: .deploy
  before_script:
    - !reference [.deploy, before_script]
    - docker volume create --name=my-volume

# Extending .review and adding before_script steps
review:
  extends: .review
  before_script:
    - !reference [.review, before_script]
    - docker volume create --name=my-volume
```

### When you do NOT need `!reference`

If you only override `script:` (not `before_script`), the template's `before_script` is inherited automatically:

```yaml
# FINE -- only overriding script, before_script is inherited from .deploy
deploy-production:
  extends: .deploy
  script:
    - docker compose -f docker-compose.deploy.yml pull
    - docker compose -f docker-compose.deploy.yml up -d
```

### Template `before_script` reference

| Template   | `before_script` contents |
|------------|--------------------------|
| `.deploy`  | docker_login.sh, `cd $CONTEXT`, `docker network create` |
| `.review`  | docker_login.sh, `cd $CONTEXT`, `docker network create` |
| `.pull`    | docker_login.sh |
| `.push`    | docker_login.sh |
| `.build`   | _(none)_ |
| `.cleanup` | _(none)_ |

## Docker Images

ci-helpers builds and maintains two Docker images:

### `ci-helpers/docker` -- CI utilities image

- **Base**: Ubuntu + hcloud CLI + Python 3 + git
- **Python packages**: `anthropic`, `gitpython` (for AI changelog generation)
- **Built on**: merge to default branch and on tags
- **Used by**: `auto_changelog.sh` (runs AI changelog generator inside this container)

### `ci-helpers/backup` -- Backup/restore image

- **Base**: Duplicati + PostgreSQL client
- **Built on**: merge to default branch and on tags
- **Used by**: `db-backup` service in database stack, `restore-review` job

Both images are built via child pipelines triggered from `ci_helpers_build.yml` and pushed to the GitLab container registry.

## Auto Changelog & Tagging

On every push to the default branch, two jobs run automatically:

1. **`auto-changelog`** -- Generates a changelog entry for the new version
   - Tries AI-powered generation first (runs `ai_changelog_generator.py` inside the `ci-helpers/docker` image)
   - Falls back to commit message extraction if AI is unavailable
   - Commits and pushes the updated `CHANGELOG.md`

2. **`auto-tagging`** -- Creates a semver git tag based on the commit message
   - Default: patch bump (`v0.0.X`)
   - `#major_release` in commit message: minor bump (`v0.X.0`)
   - `#public_release` in commit message: major bump (`vX.0.0`)

### Required CI/CD Variables for AI Changelog

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Optional | Anthropic API key for Claude-powered changelogs. Without it, falls back to commit message extraction. |
| `GITLAB_ACCESS_TOKEN` | Yes | Used to clone/push the repo for changelog updates |

## Docker Login

All Docker registry authentication is handled by `scripts/docker_login.sh`. It is sourced (not executed) in templates so the `DOCKER_CONFIG` variable persists.

The script creates a per-job config directory (`$CI_PROJECT_DIR/.docker-$CI_JOB_ID`) to isolate credentials between concurrent jobs, and symlinks any installed CLI plugins (like `docker-rollout`) so they remain discoverable.

### Required CI/CD Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `CI_REGISTRY` | GitLab (automatic) | Registry URL |
| `CI_REGISTRY_USER` | GitLab (automatic) | Registry username |
| `CI_REGISTRY_PASSWORD` | GitLab (automatic) | Registry password |
| `DOCKER_HUB_USERNAME` | Manual | Primary Docker Hub username |
| `DOCKER_TOKEN` | Manual | Primary Docker Hub access token |
| `DOCKER_HUB_FALLBACK_USERNAME` | Optional | Fallback Docker Hub username used after rate limit errors |
| `DOCKER_HUB_FALLBACK_TOKEN` | Optional | Fallback Docker Hub access token used after rate limit errors |

If a `docker compose pull` command fails with a Docker Hub pull rate limit error, the CI helper automatically logs into Docker Hub with the fallback account and retries the same command once.

If fallback variables are not set, the helper keeps the current behavior and fails normally.

## Container Scanning

Container scanning uses GitLab's `gtcs` analyzer. To add scanning to a service:

```yaml
include:
  - local: "ci-helpers/jobs/security.yml"

container-scanning:
  extends: .container_scanning
```

Required CI/CD variables:
- `GITLAB_ACCESS_USER` / `GITLAB_ACCESS_TOKEN` -- for pulling the analyzer image

## Hetzner Review Apps

On merge request, the pipeline automatically:
1. Provisions a Hetzner Cloud server
2. Sets up DNS records (A + wildcard)
3. Creates an Authentik forward auth provider
4. Installs Docker, GitLab Runner, and Netbird on the server
5. Registers the server as a project runner (tagged with the MR IID)

Required CI/CD variables:
- `HETZNER_API_KEY` -- Hetzner Cloud API token
- `HETZNER_SERVER_SIZE` -- Server type (e.g. `cx22`)
- `SSH_PRIVATE_KEY` -- Base64-encoded SSH private key
- `SSH_PUBLIC_KEY` -- SSH public key
- `SSH_KEYS` -- Path to authorized keys file
- `GITLAB_ACCESS_TOKEN` -- For runner registration and API calls
- `GITLAB_RUNNER_TOKEN` -- Runner registration token
- `NETBIRD_SETUP_KEY` -- Netbird VPN setup key
- `AUTHENTIK_HOST` / `AUTHENTIK_API_KEY` -- Authentik API access
- `DOMAIN` -- Base domain for review apps

## Upgrading ci-helpers in Another Project

To bring another project up to speed or upgrade an existing one:

1. **Copy the `ci-helpers/` folder** into the project root, replacing the old one entirely

2. **Create or update the root `.gitlab-ci.yml`**:
   ```yaml
   include:
     - local: "ci-helpers/root.yml"

   my-service-mr:
     extends: .mr
     variables:
       CONTEXT: "my-service"

   my-service:
     extends: .branch
     variables:
       CONTEXT: "my-service"
   ```

3. **Create a service `.gitlab-ci.yml`** in each service directory:
   ```yaml
   include:
     - local: "ci-helpers/jobs/all.yml"

   stages:
     - pull
     - build
     - push
     - cleanup
     - deploy
     - review

   before_script:
     - cd $CONTEXT

   workflow:
     name: $CONTEXT
     rules:
       - if: $CI_PIPELINE_SOURCE == "parent_pipeline"
       - if: $CI_PIPELINE_SOURCE == "schedule"
         when: never

   pull:
     extends: .pull
   build:
     extends: .build
   push:
     extends: .push
   push-latest:
     extends: .push-latest
   cleanup:
     extends: .cleanup

   deploy-production:
     extends: .deploy

   review:
     extends: .review
   ```

4. **Audit all `before_script` overrides** -- this is the step people miss. Search for any job that both `extends` a template and defines its own `before_script`. Every one of those needs `!reference`:
   ```yaml
   # Find these in your service .gitlab-ci.yml files:
   deploy-production:
     extends: .deploy
     before_script:
       - !reference [.deploy, before_script]    # <-- add this line
       - docker volume create --name=my-volume
   ```

5. **Set the required CI/CD variables** in GitLab (Settings > CI/CD > Variables)

### Checklist after copying

- [ ] Root `.gitlab-ci.yml` includes `ci-helpers/root.yml`
- [ ] Each service has a `.gitlab-ci.yml` with `include: - local: "ci-helpers/jobs/all.yml"`
- [ ] Every `before_script` override uses `!reference [.template, before_script]`
- [ ] `GITLAB_ACCESS_TOKEN` is set for changelog/tagging
- [ ] `DOCKER_HUB_USERNAME` and `DOCKER_TOKEN` are set if pulling from Docker Hub
- [ ] Optional: `DOCKER_HUB_FALLBACK_USERNAME` and `DOCKER_HUB_FALLBACK_TOKEN` are set if you want automatic Docker Hub fallback after pull rate limits
- [ ] `DOMAIN` and `NETWORK_NAME` variables are configured
- [ ] `ANTHROPIC_API_KEY` is set if you want AI-powered changelogs
