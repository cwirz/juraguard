# Security Policy

## Supported versions

Until the first public release, only the latest default-branch revision receives security fixes. Internal prerelease tags `v0.0.1` through `v0.0.7` are unsupported. During public beta, only the latest published release receives security fixes; upgrading is required when a replacement is published.

## Reporting a vulnerability

Do not open a public issue. Use [GitHub private vulnerability reporting](https://github.com/cwirz/juraguard/security/advisories/new). Until that channel is enabled with the public mirror, email [info@pyango.ch](mailto:info@pyango.ch) with `Juraguard security` in the subject. Include:

- affected revision or image tag
- reproduction steps or proof of concept
- expected impact
- any known mitigation

Avoid accessing other users' data, disrupting services, or publishing details before a fix is available. Maintainers will acknowledge reports on a best-effort basis during beta, assess severity, and coordinate remediation and disclosure privately. No response-time SLA is offered for the community edition.

For ordinary bugs without security impact, use the public issue tracker after the GitHub mirror is published.
