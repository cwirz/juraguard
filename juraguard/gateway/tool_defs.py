from .providers import provider_choices


META_TOOLS = [
    {
        "name": "gateway_search_tools",
        "description": "Search cached tools across active integrations using deterministic lexical matching.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words found in tool names or descriptions."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_call_tool",
        "description": "Call one exact namespaced tool returned by gateway_search_tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_list_integrations",
        "description": "List integrations without private credentials.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "gateway_get_integration",
        "description": "Get one integration and its cached tools without private credentials.",
        "inputSchema": {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_create_integration",
        "description": "Create an integration. Returns a safe browser URL for private credential or OAuth setup.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "slug": {"type": "string"},
                "description": {"type": "string"},
                "provider_type": {"type": "string", "enum": [key for key, _ in provider_choices()]},
                "remote_url": {"type": "string"},
                "base_url": {"type": "string"},
                "write_enabled": {"type": "boolean"},
                "active": {"type": "boolean"},
            },
            "required": ["name", "slug"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_update_integration",
        "description": "Update non-secret integration settings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "remote_url": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_delete_integration",
        "description": "Delete an integration after explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {"slug": {"type": "string"}, "confirm": {"const": True}},
            "required": ["slug", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gateway_reconnect_integration",
        "description": "Return a new short-lived private browser URL for replacing secret headers.",
        "inputSchema": {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
            "additionalProperties": False,
        },
    },
]
