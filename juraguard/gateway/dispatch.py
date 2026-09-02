import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Integration, SetupLink, personal_workspace
from .hardening import security_event
from .providers import get_provider
from .remote import RemoteError, call_tool
from .security import validate_remote_url
from .tool_defs import META_TOOLS


TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in META_TOOLS}
READ_META_TOOLS = {"gateway_search_tools", "gateway_list_integrations", "gateway_get_integration"}

SEARCH_STOPWORDS = {
    "a", "about", "all", "an", "and", "are", "be", "can", "could", "do", "does", "for", "give",
    "help", "i", "is", "me", "my", "of", "or", "please", "show", "some", "tell", "the", "to", "using",
    "want", "with", "would", "you",
}


def public_integration(integration, *, tools=False):
    def public_url(value):
        try:
            return validate_remote_url(value, resolve=False) if value else ""
        except ValidationError:
            return ""

    result = {
        "name": integration.name,
        "slug": integration.slug,
        "description": integration.description,
        "provider_type": integration.provider_type,
        "remote_url": public_url(integration.remote_url),
        "base_url": public_url(integration.base_url),
        "write_enabled": integration.write_enabled,
        "active": integration.active,
        "connected": integration.connected,
        "catalog_updated_at": integration.catalog_updated_at.isoformat() if integration.catalog_updated_at else None,
    }
    if tools:
        result["tools"] = (
            [public_tool(integration, tool) for tool in integration.tool_catalog if isinstance(tool, dict)]
            if integration.connected else []
        )
    return result


def public_tool(integration, tool):
    description = tool.get("description", "")
    return {
        "name": f"{integration.slug}__{tool['name']}",
        "description": description if isinstance(description, str) else "",
        "inputSchema": tool.get("inputSchema", {"type": "object"}),
        "annotations": tool.get("annotations", {}),
        "integration": integration.slug,
    }


def search_tools(user, query=""):
    normalized_query = " ".join(re.findall(r"[a-z0-9]+", query.lower()))
    terms = set(normalized_query.split()) - SEARCH_STOPWORDS
    matches = []
    for integration in Integration.objects.filter(workspace=personal_workspace(user), active=True):
        if not integration.connected:
            continue
        for tool in integration.tool_catalog:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            item = public_tool(integration, tool)
            full_name = " ".join(re.findall(r"[a-z0-9]+", item["name"].lower()))
            bare_name = " ".join(re.findall(r"[a-z0-9]+", tool["name"].lower()))
            name_terms = set(bare_name.split())
            description_terms = set(re.findall(r"[a-z0-9]+", item["description"].lower()))
            name_overlap = len(terms & name_terms)
            description_overlap = len(terms & description_terms)
            if normalized_query in {full_name, bare_name}:
                rank = 0
            elif terms and (full_name.startswith(normalized_query) or bare_name.startswith(normalized_query)):
                rank = 1
            else:
                rank = 2
            if rank == 2 and (not terms or not name_overlap and not description_overlap):
                continue
            score = name_overlap * 3 + description_overlap
            matches.append((rank, -score, item["name"], item))
    return [item for _, _, _, item in sorted(matches)[:20]]


def _integration(user, slug):
    try:
        return Integration.objects.get(workspace=personal_workspace(user), slug=slug)
    except Integration.DoesNotExist as exc:
        raise ValueError("Integration not found.") from exc


def _setup_url(request, integration):
    base_url = ""
    if settings.PUBLIC_BASE_URL:
        try:
            parsed = urlsplit(settings.PUBLIC_BASE_URL)
            parsed.port
        except ValueError as exc:
            raise ValueError("PUBLIC_BASE_URL must be a valid HTTPS origin.") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin without credentials or a path.")
        base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    else:
        hostname = urlsplit(f"//{request.get_host()}").hostname
        try:
            local = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        except (TypeError, ValueError):
            local = False
        if not local:
            raise ValueError("PUBLIC_BASE_URL is required outside localhost.")
    token = SetupLink.issue(integration)
    security_event("setup_link_created", request, workspace=integration.workspace, integration=integration,
                   outcome="success")
    path = reverse("credential_setup", args=[token])
    if base_url:
        return f"{base_url}{path}"
    return request.build_absolute_uri(path)


def _validate_agent_arguments(name, arguments):
    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        raise ValueError("Unknown gateway tool.")
    properties = schema["properties"]
    if set(arguments) - set(properties) or set(schema.get("required", ())) - set(arguments):
        raise ValueError("Tool arguments do not match the published schema.")
    expected_types = {"string": str, "object": dict, "boolean": bool}
    for key, value in arguments.items():
        rule = properties[key]
        expected = expected_types.get(rule.get("type"))
        if expected and not isinstance(value, expected):
            raise ValueError("Tool arguments do not match the published schema.")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError("Tool arguments do not match the published schema.")
        if "const" in rule and (type(value) is not type(rule["const"]) or value != rule["const"]):
            raise ValueError("Tool arguments do not match the published schema.")
    for key in schema.get("required", ()):
        if properties[key].get("type") == "string" and not arguments[key]:
            raise ValueError("Tool arguments do not match the published schema.")


def create_integration(user, arguments, request):
    try:
        with transaction.atomic():
            validate_slug(arguments.get("slug", ""))
            provider_type = arguments.get("provider_type", Integration.GENERIC_CUSTOM)
            provider = get_provider(provider_type)
            if provider:
                if arguments.get("remote_url"):
                    raise ValidationError("Built-in providers require base_url only.")
                base_url = provider.normalize_url(arguments.get("base_url") or provider.default_base_url)
                validate_remote_url(base_url)
                remote_url = ""
            else:
                if not arguments.get("remote_url") or arguments.get("base_url"):
                    raise ValidationError("Generic integrations require remote_url only.")
                remote_url = validate_remote_url(arguments["remote_url"])
                base_url = ""
            integration = Integration.objects.create(
                workspace=personal_workspace(user),
                name=arguments["name"][:100],
                slug=arguments["slug"],
                description=arguments.get("description", "")[:500],
                provider_type=provider_type,
                remote_url=remote_url,
                base_url=base_url,
                write_enabled=arguments.get("write_enabled", False),
                active=arguments.get("active", True),
            )
            setup_url = _setup_url(request, integration)
    except (KeyError, ValidationError, IntegrityError) as exc:
        raise ValueError("Integration details are invalid or the slug is already used.") from exc
    security_event("integration_created", request, workspace=integration.workspace, integration=integration,
                   outcome="success")
    return {**public_integration(integration), "setup_url": setup_url, "setup_expires_in": 900}


def update_integration(user, arguments):
    integration = _integration(user, arguments.get("slug"))
    try:
        for field in ("name", "description", "active"):
            if field in arguments:
                setattr(integration, field, arguments[field])
        if "remote_url" in arguments:
            validate_remote_url(arguments["remote_url"])
            integration.remote_url = arguments["remote_url"]
            integration.tool_catalog = []
            integration.catalog_updated_at = None
        integration.full_clean(exclude=["encrypted_headers", "tool_catalog"])
        integration.save()
    except (ValidationError, IntegrityError) as exc:
        raise ValueError("Integration details are invalid.") from exc
    return public_integration(integration)


def _read_scope_allows(user, name, arguments):
    if name in READ_META_TOOLS:
        return True
    if name != "gateway_call_tool":
        return False
    namespaced = arguments.get("name", "")
    if "__" not in namespaced:
        return False
    slug, tool_name = namespaced.split("__", 1)
    integration = _integration(user, slug)
    return any(
        tool.get("name") == tool_name
        and isinstance(tool.get("annotations"), dict)
        and tool["annotations"].get("readOnlyHint") is True
        for tool in integration.tool_catalog
        if isinstance(tool, dict)
    )


def execute(user, name, arguments, request, scopes):
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    _validate_agent_arguments(name, arguments)
    if "mcp:write" not in scopes and not _read_scope_allows(user, name, arguments):
        raise ValueError("This tool requires mcp:write scope.")
    if name == "gateway_search_tools":
        return {"tools": search_tools(user, str(arguments.get("query", "")))}
    if name == "gateway_list_integrations":
        return {
            "integrations": [
                public_integration(item) for item in Integration.objects.filter(workspace=personal_workspace(user))
            ]
        }
    if name == "gateway_get_integration":
        return public_integration(_integration(user, arguments.get("slug")), tools=True)
    if name == "gateway_create_integration":
        return create_integration(user, arguments, request)
    if name == "gateway_update_integration":
        result = update_integration(user, arguments)
        security_event("integration_updated", request, workspace=personal_workspace(user), outcome="success")
        return result
    if name == "gateway_reconnect_integration":
        integration = _integration(user, arguments.get("slug"))
        return {"setup_url": _setup_url(request, integration), "setup_expires_in": 900}
    if name == "gateway_delete_integration":
        if arguments.get("confirm") is not True:
            raise ValueError("Deletion requires confirm=true.")
        integration = _integration(user, arguments.get("slug"))
        security_event("integration_deleted", request, workspace=integration.workspace, integration=integration,
                       outcome="success")
        integration.delete()
        return {"deleted": True}
    if name == "gateway_call_tool":
        return _call_namespaced(user, arguments)
    raise ValueError("Unknown gateway tool.")


def _call_namespaced(user, arguments):
    namespaced = arguments.get("name", "")
    if "__" not in namespaced:
        raise ValueError("Use an exact namespaced tool name from gateway_search_tools.")
    slug, tool_name = namespaced.split("__", 1)
    integration = _integration(user, slug)
    if not integration.active or not integration.connected:
        raise ValueError("Integration is not active and connected.")
    if tool_name not in {tool.get("name") for tool in integration.tool_catalog if isinstance(tool, dict)}:
        raise ValueError("Tool is not present in the cached catalog.")
    try:
        provider = get_provider(integration.provider_type)
        if provider:
            result = provider.call_tool(integration, tool_name, arguments.get("arguments", {}))
        else:
            result = call_tool(integration, tool_name, arguments.get("arguments", {}))
    except RemoteError as exc:
        raise ValueError(str(exc)) from exc
    integration.catalog_updated_at = integration.catalog_updated_at or timezone.now()
    return result
