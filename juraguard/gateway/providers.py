from .gitlab_provider import GitLabProvider
from .hetzner_provider import HetznerProvider
from .provider_types import BuiltinProvider as BuiltinProvider
from .provider_types import CredentialField as CredentialField


PROVIDER_CLASSES = (GitLabProvider, HetznerProvider)
GENERIC_PROVIDER_CHOICES = (
    ("generic_oauth", "Generic OAuth MCP"),
    ("generic_custom", "Generic custom MCP"),
)


def providers():
    return {provider_class.key: provider_class() for provider_class in PROVIDER_CLASSES}


def get_provider(key):
    return providers().get(key)


def provider_choices():
    return [(provider.key, provider.label) for provider in providers().values()] + list(GENERIC_PROVIDER_CHOICES)
