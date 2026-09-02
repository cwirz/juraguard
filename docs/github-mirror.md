# GitHub mirror operations

GitLab project `386` at <https://gitlab.pyango.ch/pyango/juraguard/monorepo> remains the authoritative source for development, review, merge decisions, and releases. <https://github.com/cwirz/juraguard> is the planned public mirror, issue tracker, pull-request surface, and GHCR publisher; it has not been published yet. Do not configure GitHub-to-GitLab pull mirroring.

## Configure

1. Configure a protected GitLab push mirror for canonical branches and tags using a least-scoped GitHub credential. Store the credential only in GitLab's protected mirror configuration.
2. Keep GitHub Actions permissions read-only by default. Permit package, identity-token, and attestation writes only in the release job. After the first release, explicitly set the `ghcr.io/cwirz/juraguard` package visibility to public.
3. Set the GitHub repository variable `CANONICAL_RELEASE_ACTOR` to the account used by the protected GitLab push mirror. Create the `public-release` GitHub environment, restrict its deployment branches to tags, and require maintainer approval. Release automation also requires the tagged commit to exist on mirrored `master`.
4. Protect the canonical GitLab release path. GitHub release automation accepts only `vX.Y.Z` tags with an optional SemVer prerelease suffix and publishes no image from pull requests.
5. Enable GitHub private vulnerability reporting, add the repository description/homepage/topics, and verify `SECURITY.md` is detected before announcing the mirror.

## Accept GitHub contributions

Review GitHub issues and pull requests publicly, but do not merge pull requests on GitHub. Verify every contributed commit has a valid DCO `Signed-off-by` line. Import accepted commits into a GitLab branch without changing authorship or sign-off, open and merge a GitLab merge request, then let the push mirror update GitHub. Resolve conflicts on GitLab. This is a maintainer process, not automated bidirectional synchronization.

## Verify

After setup and each release, confirm GitHub's default branch and release tag resolve to the same commit IDs as GitLab. Confirm fork pull requests run only read-only CI. For a release tag, confirm required checks passed before publication, the expected version and SHA tags resolve to one GHCR digest, `latest` and major/minor tags exist only for stable releases, the package is public, and SBOM plus provenance attestations refer to that digest.
