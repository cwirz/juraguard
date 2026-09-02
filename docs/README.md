# Documentation

The root [README](../README.md) is the canonical guide for installation, configuration, architecture, security, clients, and development.

## Operators

- [Single-host self-hosting](self-hosting.md): Debian/Ubuntu and Hetzner install, DNS, firewall, TLS, SMTP, backup, upgrades, recovery, and uninstall
- [Operations](operations.md): health checks, backup, restore, credential recovery, token rotation, and rollback
- [Security policy](../SECURITY.md): supported versions and private vulnerability reporting
- [Support](../SUPPORT.md): community and managed-service support boundaries

## Contributors and maintainers

- [Contributing](../CONTRIBUTING.md)
- [GitHub mirror operations](github-mirror.md)
- [Changelog](../CHANGELOG.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)
- [Shared CI helpers](../ci-helpers/README.md)
- [Optional shared backup helpers](../ci-helpers/backup/README.md)

The `ci-helpers` directory is vendored shared infrastructure. Its documentation includes generic examples for services that are not part of Juraguard.

## Historical research

Files in [research/](research/) record design inputs and dated comparisons. They are non-authoritative and may describe rejected approaches or older protocol drafts.

- [MCP protocol architecture](research/mcp-protocol-architecture.md)
- [Naming options](research/name-options.md)
- [MCP gateway landscape](research/mcp-gateway-landscape.md)
