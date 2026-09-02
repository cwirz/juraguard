# MCP gateway landscape (Aug 2026)

> Dated market and client research, retained for design provenance. It does not define current Juraguard behavior or support commitments.

## Shortlist
- Docker MCP Gateway - best fit for a simple self-hosted gateway. It is a Docker CLI plugin plus gateway, runs MCP servers as isolated containers, keeps secrets in Docker Desktop secrets, supports dynamic discovery, OAuth, and shared client profiles. Source: https://github.com/docker/mcp-gateway
- MetaMCP - good if you want aggregation plus middleware. It runs with Docker Compose, groups servers into namespaces, emits unified endpoints, and supports API-key auth or MCP OAuth over SSE or Streamable HTTP. Source: https://github.com/metatool-ai/metamcp
- Obot - broader governance platform, not just a gateway. It ships an MCP gateway, registry, credentials management, composite servers, and can host npx/uvx/container MCP servers as Docker or Kubernetes workloads. Source: https://github.com/obot-platform/obot
- IBM ContextForge - broad registry and proxy. It federates MCP, A2A, REST, and gRPC APIs, ships via PyPI or Docker, exposes one endpoint, and includes auth, retries, rate limiting, and plugins. Source: https://github.com/IBM/mcp-context-forge
- mcpgate - self-hosted gateway distro with Docker Compose or single-container Docker, PII pseudonymization, hooks, context map, and OpenAPI import. Pricing page is self-hosted only, not SaaS $5/month. Sources: https://github.com/Mcpgate-de/mcpgate and https://mcpgate.de/pricing/
- MCPProxy.app - adjacent, not a hosted gateway. It is an open-source desktop proxy with BM25 tool search, quarantine, and token optimization. Source: https://mcpproxy.app/

## What matters for your target
- Unlimited user-added MCP servers - strongest evidence is Docker MCP Gateway, MetaMCP, Obot, ContextForge, and mcpgate. Docker MCP Gateway and MCPProxy.app emphasize discovery/search over loading every tool into context. Sources: https://github.com/docker/mcp-gateway, https://github.com/metatool-ai/metamcp, https://github.com/obot-platform/obot, https://github.com/IBM/mcp-context-forge, https://github.com/Mcpgate-de/mcpgate, https://mcpproxy.app/
- Tool catalog kept out of model context via search meta-tool - best match is Docker MCP Gateway, which exposes compact meta-tools and `gateway_search_tools` plus on-demand routing. MCPProxy.app also does this with `retrieve_tools`. Sources: https://github.com/docker/mcp-gateway, https://mcpproxy.app/
- Agent-driven config with secure browser credential links - closest fit is Docker MCP Gateway and Obot. Docker MCP Gateway has setup wizard plus per-client export and OAuth flows; Obot can issue scoped credentials and manage MCP OAuth, shared credentials, and secret bindings. Sources: https://github.com/docker/mcp-gateway, https://github.com/obot-platform/obot
- Single-container self-hosting - best verified matches are Docker MCP Gateway, mcpgate, and ContextForge. Sources: https://github.com/docker/mcp-gateway, https://github.com/Mcpgate-de/mcpgate, https://github.com/IBM/mcp-context-forge
- Swiss-hosted $5/month cloud - not verified from a primary source. The Swiss-hosted pricing page I verified for mcpgate is 0 euro free, 399 euro per month team, and custom enterprise, so it is not the claimed $5/month offer. Source: https://mcpgate.de/pricing/

## Harness setup patterns
- Claude Desktop and Claude Code - `claude mcp add` supports HTTP, SSE, stdio, and WebSocket entries; configs live in `~/.claude.json` or `.mcp.json`, and the Desktop Code tab reads the same settings files as the CLI. Sources: https://docs.anthropic.com/en/docs/claude-code/mcp and https://docs.anthropic.com/en/docs/claude-code/desktop
- Cursor - use `.cursor/mcp.json` or `~/.cursor/mcp.json`; supports stdio, SSE, and Streamable HTTP, static OAuth, and config interpolation. Source: https://cursor.com/docs/context/mcp
- VS Code / Copilot - use `.vscode/mcp.json` or user config, trust prompts, remote HTTP or SSE, sandboxing for local stdio servers, and optional auto-discovery from other apps. Source: https://code.visualstudio.com/docs/copilot/chat/mcp-servers
- OpenAI Codex CLI - user config is `~/.codex/config.toml`, project config is `.codex/config.toml`, and MCP servers live under `mcp_servers.<id>`. Source: https://developers.openai.com/codex/config-basic and https://developers.openai.com/codex/config-reference
- OpenCode - config supports local and remote MCP servers, OAuth, headers, and `opencode mcp auth`. Source: https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/mcp-servers.mdx
- Gemini CLI - use `~/.gemini/settings.json` or `.gemini/settings.json` with `mcpServers`, plus `gemini mcp add` for stdio, HTTP, and SSE. Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md
- Windsurf - use `~/.codeium/windsurf/mcp_config.json`, supports stdio, Streamable HTTP, SSE, OAuth, and one-click registry install. Source: https://docs.windsurf.com/windsurf/cascade/mcp
- Generic MCP clients - the protocol itself supports stdio and remote transports, and the official docs index lists Claude, Cursor, VS Code, and more as supported clients. Sources: https://modelcontextprotocol.io/ and https://modelcontextprotocol.io/llms.txt

## Bottom line
- If you want the closest single-container self-hosted gateway with a compact agent surface, Docker MCP Gateway is the cleanest fit.
- If you want broader governance, shared credentials, or registry control, Obot or ContextForge are stronger but heavier.
