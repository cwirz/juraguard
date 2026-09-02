# MCP protocol architecture research

> Historical design research, not current product documentation. Juraguard ships interoperable Streamable HTTP MCP using protocol version `2025-06-18`, including `initialize`, `tools/list`, and `tools/call`. See the root README for current behavior.

## Baseline
- Modern MCP is 2026-07-28 and later: no `initialize` handshake, every request carries version in `_meta`, and HTTP also carries `MCP-Protocol-Version` that must match the body. Legacy is 2025-11-25 and earlier, where `initialize` still exists. Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.md
- Streamable HTTP is the remote transport to implement in 2026: one POST MCP endpoint, request scoped JSON or SSE response, no GET stream, no protocol sessions, no resumable SSE. SSE and HTTP+SSE are deprecated, compat only. stdio remains canonical for local subprocess servers. Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.md

## Auth and tools
- HTTP transports should use OAuth 2.1, Protected Resource Metadata, RFC8414 or OIDC discovery, PKCE, resource indicators, and bearer tokens. stdio should use env credentials, not the HTTP auth flow. DCR is deprecated, Client ID Metadata Documents are preferred. Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration.md
- Tool contract is `tools/list` and `tools/call`; catalog may change by auth but not by connection state. `list_changed` rides `subscriptions/listen`. `x-mcp-header` is HTTP only, tool annotations are untrusted unless server is trusted. Sources: https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/subscriptions.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md

## Security floor
- Must validate `Origin`, bind local servers to localhost, and treat metadata discovery as SSRF sensitive. Do not passthrough tokens, validate audience, and do not treat state handles as auth. Local MCP servers can be arbitrary code execution, so avoid spawning untrusted commands in the gateway. Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md , https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices.md , https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index.md

## Smallest interoperable MVP
- One Django app, one MCP POST endpoint, one auth flow, one tool catalog, one tool call proxy, one integration registry.
- Support Streamable HTTP only, OAuth 2.1 + PRM + PKCE + bearer, `server/discover`, `tools/list`, `tools/call`, and `list_changed` if the backend needs dynamic catalogs.
- Add one-time browser credential links for agent-created integrations: create integration in Django, return an opaque connect URL, complete OAuth in browser, store tokens server-side, then mark the link spent.

## Explicit exclusions
- No SSE GET endpoint, no session IDs, no `Last-Event-ID`, no websocket transport, no stdio relay in the gateway, no token passthrough, no arbitrary command execution, no custom auth shortcuts.
- Do not implement tool search beyond protocol discovery plus `list_changed`.

## Client compatibility cheat sheet
- Claude Code: supports remote HTTP, deprecated SSE, stdio, and ws. Official commands are `claude mcp add --transport http|sse|stdio`, `claude mcp add-json`, and `.mcp.json` with `type: "http"` as the Streamable HTTP alias. Source: https://docs.anthropic.com/en/docs/claude-code/mcp
- VS Code / GitHub Copilot: `.vscode/mcp.json` or user `mcp.json`, `servers` entries with `type: "http" | "sse" | "stdio"`, and `code --add-mcp`. Source: https://code.visualstudio.com/docs/agent-customization/mcp-servers , https://code.visualstudio.com/docs/agents/reference/mcp-configuration
- Gemini CLI: `settings.json` with `mcpServers`, supports `command`, `url`, `httpUrl`, stdio, SSE, Streamable HTTP, OAuth, and `gemini mcp add`. Source: https://github.com/google-gemini/gemini-cli/raw/main/docs/tools/mcp-server.md
- Windsurf / Devin Desktop: `~/.codeium/windsurf/mcp_config.json`, supports `stdio`, `http`, and `sse`, plus one-click registry deeplinks. Source: https://docs.windsurf.com/windsurf/cascade/mcp
- Cursor: primary MCP docs exist and support stdio, SSE, Streamable HTTP, `mcpServers`, and OAuth, but the exact doc path was not pinned in this pass. Treat as verified support, but keep an uncertainty note in implementation docs. Source root: https://docs.cursor.com/
- Codex CLI, OpenCode, Windsurf CLI, and other generic clients: no primary doc path was confirmed here. Treat as uncertain until verified.

## Recommendation
- Ship Streamable HTTP first. That is the smallest interoperable gateway for 2026 and covers modern remote clients without legacy transport baggage.
- Keep legacy SSE and stdio behind explicit later flags if a real client requires them.

## Sources
- MCP spec index: https://modelcontextprotocol.io/llms.txt
- Streamable HTTP: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http.md
- stdio: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.md
- Versioning: https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning.md
- Authorization: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index.md
- Tools: https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md
- Security best practices: https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices.md
