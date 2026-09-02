from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from django.core.exceptions import ValidationError

from .outbound import OutboundError, request
from .security import validate_remote_url
from .provider_types import BuiltinProvider, CredentialField


READ = {"readOnlyHint": True, "destructiveHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False}
SENSITIVE_FIELDS = frozenset({
    "access_token",
    "import_url",
    "password",
    "private_token",
    "refresh_token",
    "runner_token",
    "runners_token",
    "secret",
    "token",
})
PROJECT = {"type": ["string", "integer"], "description": "Project ID or URL-encoded project path."}
ID = {"type": "integer", "minimum": 1}
TEXT = {"type": "string", "minLength": 1}
PAGE = {"type": "integer", "minimum": 1}
PER_PAGE = {"type": "integer", "minimum": 1, "maximum": 100}


def schema(properties, required=()):
    value = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        value["required"] = list(required)
    return value


def tool(description, properties, required, method, path, path_args=(), query=(), body=()):
    return {
        "description": description,
        "inputSchema": schema(properties, required),
        "annotations": READ if method == "GET" else WRITE,
        "method": method,
        "path": path,
        "path_args": path_args,
        "query": query,
        "body": body,
    }


TOOLS = {
    "current_user_get": tool("Get the current GitLab user.", {}, (), "GET", "user"),
    "projects_list": tool("List accessible GitLab projects.", {
        "search": {"type": "string"}, "membership": {"type": "boolean"}, "page": PAGE, "per_page": PER_PAGE,
    }, (), "GET", "projects", query=("search", "membership", "page", "per_page")),
    "projects_get": tool("Get one GitLab project.", {"project": PROJECT}, ("project",), "GET",
                         "projects/{project}", ("project",)),
    "issues_list": tool("List project issues.", {
        "project": PROJECT, "state": {"type": "string", "enum": ["opened", "closed", "all"]},
        "search": {"type": "string"}, "page": PAGE, "per_page": PER_PAGE,
    }, ("project",), "GET", "projects/{project}/issues", ("project",), ("state", "search", "page", "per_page")),
    "issues_get": tool("Get one project issue.", {"project": PROJECT, "issue_iid": ID},
                       ("project", "issue_iid"), "GET", "projects/{project}/issues/{issue_iid}",
                       ("project", "issue_iid")),
    "issues_create": tool("Create a project issue.", {
        "project": PROJECT, "title": TEXT, "description": {"type": "string"}, "labels": {"type": "string"},
    }, ("project", "title"), "POST", "projects/{project}/issues", ("project",),
        body=("title", "description", "labels")),
    "issue_notes_list": tool("List notes on an issue.", {
        "project": PROJECT, "issue_iid": ID, "page": PAGE, "per_page": PER_PAGE,
    }, ("project", "issue_iid"), "GET", "projects/{project}/issues/{issue_iid}/notes",
        ("project", "issue_iid"), ("page", "per_page")),
    "issue_notes_create": tool("Add a note to an issue.", {
        "project": PROJECT, "issue_iid": ID, "body": TEXT,
    }, ("project", "issue_iid", "body"), "POST", "projects/{project}/issues/{issue_iid}/notes",
        ("project", "issue_iid"), body=("body",)),
    "merge_requests_list": tool("List project merge requests.", {
        "project": PROJECT, "state": {"type": "string", "enum": ["opened", "closed", "merged", "all"]},
        "search": {"type": "string"}, "page": PAGE, "per_page": PER_PAGE,
    }, ("project",), "GET", "projects/{project}/merge_requests", ("project",),
        ("state", "search", "page", "per_page")),
    "merge_requests_get": tool("Get one project merge request.", {"project": PROJECT, "mr_iid": ID},
                               ("project", "mr_iid"), "GET", "projects/{project}/merge_requests/{mr_iid}",
                               ("project", "mr_iid")),
    "merge_request_notes_list": tool("List notes on a merge request.", {
        "project": PROJECT, "mr_iid": ID, "page": PAGE, "per_page": PER_PAGE,
    }, ("project", "mr_iid"), "GET", "projects/{project}/merge_requests/{mr_iid}/notes",
        ("project", "mr_iid"), ("page", "per_page")),
    "merge_request_notes_create": tool("Add a note to a merge request.", {
        "project": PROJECT, "mr_iid": ID, "body": TEXT,
    }, ("project", "mr_iid", "body"), "POST", "projects/{project}/merge_requests/{mr_iid}/notes",
        ("project", "mr_iid"), body=("body",)),
    "pipelines_list": tool("List project pipelines.", {
        "project": PROJECT, "status": {"type": "string"}, "ref": {"type": "string"},
        "page": PAGE, "per_page": PER_PAGE,
    }, ("project",), "GET", "projects/{project}/pipelines", ("project",),
        ("status", "ref", "page", "per_page")),
    "pipelines_get": tool("Get one project pipeline.", {"project": PROJECT, "pipeline_id": ID},
                          ("project", "pipeline_id"), "GET", "projects/{project}/pipelines/{pipeline_id}",
                          ("project", "pipeline_id")),
    "pipeline_jobs_list": tool("List jobs in a project pipeline.", {
        "project": PROJECT, "pipeline_id": ID, "page": PAGE, "per_page": PER_PAGE,
    }, ("project", "pipeline_id"), "GET", "projects/{project}/pipelines/{pipeline_id}/jobs",
        ("project", "pipeline_id"), ("page", "per_page")),
    "pipelines_create": tool("Create a project pipeline.", {"project": PROJECT, "ref": TEXT},
                             ("project", "ref"), "POST", "projects/{project}/pipeline", ("project",), body=("ref",)),
}

WRITE_TOOLS = frozenset(name for name, spec in TOOLS.items() if spec["method"] != "GET")


def normalize_url(value):
    value = value.strip().rstrip("/")
    validate_remote_url(value, resolve=False)
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        raise ValidationError("GitLab URL cannot contain a query or fragment.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", ""))


def catalog(write_enabled):
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
            "annotations": spec["annotations"],
        }
        for name, spec in TOOLS.items()
        if write_enabled or name not in WRITE_TOOLS
    ]


def _headers(integration, pat=None):
    token = pat if pat is not None else integration.get_credentials().get("pat")
    if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
        raise ValueError("Stored GitLab credential is invalid.")
    return {"PRIVATE-TOKEN": token, "Accept": "application/json"}


def validate_pat(base_url, pat):
    try:
        payload = request(f"{normalize_url(base_url)}/api/v4/user", headers=_headers(None, pat)).json()
    except (OutboundError, ValueError) as exc:
        raise ValidationError("GitLab connection failed. Check the URL and personal access token.") from exc
    if not isinstance(payload.get("id"), int):
        raise ValidationError("GitLab returned an invalid current-user response.")


def _validate_arguments(spec, arguments):
    properties = spec["inputSchema"]["properties"]
    required = spec["inputSchema"].get("required", [])
    if not isinstance(arguments, dict) or set(arguments) - set(properties) or set(required) - set(arguments):
        raise ValueError("Tool arguments do not match the published schema.")
    types = {"string": str, "integer": int, "boolean": bool}
    for name, value in arguments.items():
        rule = properties[name]
        allowed = rule.get("type")
        allowed = allowed if isinstance(allowed, list) else [allowed]
        if not any(type(value) is types.get(item) for item in allowed):
            raise ValueError("Tool arguments do not match the published schema.")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError("Tool arguments do not match the published schema.")
        if isinstance(value, int) and (value < rule.get("minimum", value) or value > rule.get("maximum", value)):
            raise ValueError("Tool arguments do not match the published schema.")
        if isinstance(value, str) and len(value) < rule.get("minLength", 0):
            raise ValueError("Tool arguments do not match the published schema.")


def _redact(value):
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items() if key.lower() not in SENSITIVE_FIELDS}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def call_tool(integration, name, arguments):
    spec = TOOLS.get(name)
    if not spec:
        raise ValueError("Unknown GitLab tool.")
    if name in WRITE_TOOLS and not integration.write_enabled:
        raise ValueError("GitLab write access is not granted.")
    _validate_arguments(spec, arguments)
    path_values = {key: quote(str(arguments[key]), safe="") for key in spec["path_args"]}
    path = spec["path"].format(**path_values)
    query = {key: arguments[key] for key in spec["query"] if key in arguments}
    url = f"{integration.base_url}/api/v4/{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    body = {key: arguments[key] for key in spec["body"] if key in arguments}
    try:
        response = request(
            url,
            method=spec["method"],
            headers=_headers(integration),
            json_body=body if spec["method"] != "GET" else None,
            allowed_status=(200, 201),
        )
        return _redact(response.json((dict, list)))
    except OutboundError as exc:
        raise ValueError(str(exc)) from exc


class GitLabProvider(BuiltinProvider):
    key = "gitlab"
    label = "GitLab"
    default_base_url = "https://gitlab.com"
    credential_fields = (CredentialField("pat", "Personal access token"),)

    normalize_url = staticmethod(normalize_url)
    catalog = staticmethod(catalog)
    call_tool = staticmethod(call_tool)

    def validate_credentials(self, base_url, credentials):
        validate_pat(base_url, credentials.get("pat"))
