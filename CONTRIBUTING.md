# Contributing

GitLab at <https://gitlab.pyango.ch/pyango/juraguard/monorepo> is the authoritative development and release repository. The planned public GitHub mirror at <https://github.com/cwirz/juraguard> will accept issues and pull requests after publication.

GitHub contributions are not merged directly. A maintainer imports each accepted contribution into a GitLab branch, opens a GitLab merge request, and merges it there. The next GitLab-to-GitHub mirror update then publishes the canonical result. There is no automated bidirectional synchronization.

## Workflow

1. Open an issue for substantial behavior changes so scope can be agreed first.
2. Keep pull or merge requests focused and explain user-visible behavior and security impact.
3. Add the smallest test that proves non-trivial logic.
4. Sign off every commit under the [Developer Certificate of Origin 1.1](https://developercertificate.org/) by adding `Signed-off-by: Your Legal Name <your-email@example.com>`. `git commit -s` adds this line. The sign-off certifies that you have the right to submit that commit under the project's AGPL-3.0 license. Every commit in a contribution must carry its own valid sign-off; a pull-request checkbox does not replace it.
5. Run the project checks from the repository root:

```sh
cd juraguard
python3.13 -m venv /tmp/juraguard-venv
/tmp/juraguard-venv/bin/pip install --require-hashes -r requirements-dev.lock
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py check
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py makemigrations --check --dry-run
DATA_DIR=/tmp/juraguard-data /tmp/juraguard-venv/bin/python manage.py test
/tmp/juraguard-venv/bin/ruff check .
cd ..
bash deploy/check.sh
```

Docker is required only for container and Compose checks. Never commit tokens, credentials, `.env` files, generated `/data`, or production data.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through the [security process](SECURITY.md), not a public issue.
