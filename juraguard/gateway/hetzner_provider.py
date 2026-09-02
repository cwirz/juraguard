from urllib.parse import urlencode, urlsplit, urlunsplit

from django.core.exceptions import ValidationError

from .outbound import OutboundError, request
from .provider_types import BuiltinProvider, CredentialField
from .security import validate_remote_url


READ = {"readOnlyHint": True, "destructiveHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True}
ID = {"type": "integer", "minimum": 1}
PAYLOAD = {"type": "object", "description": "Hetzner Cloud API request body."}
LIST_PROPERTIES = {
    "page": {"type": "integer", "minimum": 1},
    "per_page": {"type": "integer", "minimum": 1, "maximum": 50},
}


def _schema(properties, required=()):
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = list(required)
    return result


def _tool(description, method, path, *, properties=None, required=(), query=(), destructive=False):
    return {
        "description": description,
        "inputSchema": _schema(properties or {}, required),
        "annotations": READ if method == "GET" else DESTRUCTIVE if destructive else WRITE,
        "method": method,
        "path": path,
        "query": query,
    }


TOOLS = {}


def _add_resource(resource, *, writable=True):
    label = resource.replace("_", " ")
    TOOLS[f"{resource}_list"] = _tool(
        f"List Hetzner Cloud {label}.", "GET", resource,
        properties=LIST_PROPERTIES, query=tuple(LIST_PROPERTIES),
    )
    TOOLS[f"{resource}_get"] = _tool(
        f"Get one Hetzner Cloud {label} resource.", "GET", f"{resource}/{{id}}",
        properties={"id": ID}, required=("id",),
    )
    if writable:
        TOOLS[f"{resource}_create"] = _tool(
            f"Create a Hetzner Cloud {label} resource.", "POST", resource,
            properties={"payload": PAYLOAD}, required=("payload",),
        )
        TOOLS[f"{resource}_update"] = _tool(
            f"Update a Hetzner Cloud {label} resource.", "PUT", f"{resource}/{{id}}",
            properties={"id": ID, "payload": PAYLOAD}, required=("id", "payload"),
        )
        TOOLS[f"{resource}_delete"] = _tool(
            f"Delete a Hetzner Cloud {label} resource.", "DELETE", f"{resource}/{{id}}",
            properties={"id": ID}, required=("id",), destructive=True,
        )


for _resource in (
    "servers", "volumes", "networks", "firewalls", "floating_ips", "primary_ips", "ssh_keys", "images",
):
    _add_resource(_resource)
# Custom images are created through servers/{id}/actions/create_image, not POST /images.
TOOLS.pop("images_create")
for _resource in ("actions", "locations", "server_types"):
    _add_resource(_resource, writable=False)


def _add_actions(resource, actions, destructive=()):
    for action in actions:
        TOOLS[f"{resource}_{action}"] = _tool(
            f"Run {action.replace('_', ' ')} on a Hetzner Cloud {resource.replace('_', ' ')} resource.",
            "POST", f"{resource}/{{id}}/actions/{action}",
            properties={"id": ID, "payload": PAYLOAD}, required=("id",),
            destructive=action in destructive,
        )


_add_actions("servers", (
    "poweron", "poweroff", "shutdown", "reboot", "reset", "rebuild", "enable_rescue", "disable_rescue",
    "create_image", "attach_iso", "detach_iso", "change_type", "change_dns_ptr", "change_protection",
    "reset_password", "enable_backup", "disable_backup", "attach_to_network", "detach_from_network",
    "change_alias_ips",
), destructive=("poweroff", "reset", "rebuild", "disable_backup"))
_add_actions("volumes", ("attach_server", "detach_server", "resize", "change_protection"))
_add_actions("networks", (
    "add_subnet", "delete_subnet", "add_route", "delete_route", "change_ip_range", "change_protection",
), destructive=("delete_subnet", "delete_route"))
_add_actions("firewalls", ("apply_to_resources", "remove_from_resources", "set_rules"))
_add_actions("floating_ips", ("assign", "unassign", "change_dns_ptr", "change_protection"))
_add_actions("primary_ips", ("assign", "unassign", "change_dns_ptr", "change_protection"))

WRITE_TOOLS = frozenset(name for name, spec in TOOLS.items() if spec["method"] != "GET")


def normalize_url(value):
    value = value.strip().rstrip("/")
    validate_remote_url(value, resolve=False)
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        raise ValidationError("Hetzner Cloud URL cannot contain a query or fragment.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _headers(credentials):
    token = credentials.get("api_token")
    if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
        raise ValueError("Stored Hetzner Cloud credential is invalid.")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def validate_credentials(base_url, credentials):
    try:
        payload = request(f"{normalize_url(base_url)}/servers?per_page=1", headers=_headers(credentials)).json()
    except (OutboundError, ValueError) as exc:
        raise ValidationError("Hetzner Cloud connection failed. Check the URL and API token.") from exc
    if not isinstance(payload.get("servers"), list):
        raise ValidationError("Hetzner Cloud returned an invalid server response.")


def catalog(write_enabled):
    return [
        {key: spec[key] for key in ("description", "inputSchema", "annotations")} | {"name": name}
        for name, spec in TOOLS.items() if write_enabled or name not in WRITE_TOOLS
    ]


def _validate_arguments(spec, arguments):
    schema = spec["inputSchema"]
    properties = schema["properties"]
    if not isinstance(arguments, dict) or set(arguments) - set(properties) or set(schema.get("required", ())) - set(arguments):
        raise ValueError("Tool arguments do not match the published schema.")
    types = {"integer": int, "string": str, "object": dict}
    for key, value in arguments.items():
        rule = properties[key]
        if type(value) is not types[rule["type"]]:
            raise ValueError("Tool arguments do not match the published schema.")
        if isinstance(value, int) and (value < rule.get("minimum", value) or value > rule.get("maximum", value)):
            raise ValueError("Tool arguments do not match the published schema.")


def call_tool(integration, name, arguments):
    spec = TOOLS.get(name)
    if not spec:
        raise ValueError("Unknown Hetzner Cloud tool.")
    if name in WRITE_TOOLS and not integration.write_enabled:
        raise ValueError("Hetzner Cloud write access is not granted.")
    _validate_arguments(spec, arguments)
    path = spec["path"].format(id=arguments.get("id"))
    query = {key: arguments[key] for key in spec["query"] if key in arguments}
    url = f"{integration.base_url}/{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    try:
        response = request(
            url, method=spec["method"], headers=_headers(integration.get_credentials()),
            json_body=arguments.get("payload") if spec["method"] not in {"GET", "DELETE"} else None,
            allowed_status=(200, 201, 202, 204),
        )
        return {"success": True} if response.status == 204 or not response.body else response.json((dict, list))
    except OutboundError as exc:
        raise ValueError(str(exc)) from exc


class HetznerProvider(BuiltinProvider):
    key = "hetzner"
    label = "Hetzner Cloud"
    default_base_url = "https://api.hetzner.cloud/v1"
    credential_fields = (CredentialField("api_token", "API token"),)

    normalize_url = staticmethod(normalize_url)
    validate_credentials = staticmethod(validate_credentials)
    catalog = staticmethod(catalog)
    call_tool = staticmethod(call_tool)
