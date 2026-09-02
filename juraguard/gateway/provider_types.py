from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    help_text: str = ""


class BuiltinProvider(ABC):
    key = ""
    label = ""
    default_base_url = ""
    credential_fields = ()

    @abstractmethod
    def normalize_url(self, value):
        raise NotImplementedError

    @abstractmethod
    def validate_credentials(self, base_url, credentials):
        raise NotImplementedError

    @abstractmethod
    def catalog(self, write_enabled):
        raise NotImplementedError

    @abstractmethod
    def call_tool(self, integration, name, arguments):
        raise NotImplementedError

    def test_connection(self, integration):
        self.validate_credentials(integration.base_url, integration.get_credentials())
