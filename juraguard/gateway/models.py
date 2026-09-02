import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import models, transaction
from django.utils import timezone

from .security import (
    decrypt_headers,
    decrypt_value,
    encrypt_headers,
    encrypt_value,
    opaque_hash,
    validate_remote_url_syntax,
)
from .providers import provider_choices


class Workspace(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspace")
    name = models.CharField(max_length=100, default="Personal")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def external_customer_id(self):
        return f"workspace-{self.pk}"


def personal_workspace(user):
    workspace, _ = Workspace.objects.get_or_create(owner=user)
    return workspace


class BillingAccount(models.Model):
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE, related_name="billing")
    polar_customer_id = models.CharField(max_length=100, blank=True, unique=True, null=True)
    polar_subscription_id = models.CharField(max_length=100, blank=True, unique=True, null=True)
    product_id = models.CharField(max_length=100, blank=True)
    plan = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=32, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_start = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class PolarWebhookEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=80)
    occurred_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)


class GatewayToken(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gateway_token")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, user):
        token = f"mcpg_{secrets.token_urlsafe(32)}"
        cls.objects.update_or_create(user=user, defaults={"token_hash": opaque_hash(token)})
        return token


class Integration(models.Model):
    GENERIC_CUSTOM = "generic_custom"
    GENERIC_OAUTH = "generic_oauth"
    GITLAB = "gitlab"
    HETZNER = "hetzner"
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="integrations")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True, max_length=500)
    provider_type = models.CharField(max_length=32, choices=provider_choices, default=GENERIC_CUSTOM)
    remote_url = models.URLField(max_length=500, blank=True, validators=[validate_remote_url_syntax])
    base_url = models.URLField(max_length=500, blank=True, validators=[validate_remote_url_syntax])
    write_enabled = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    encrypted_headers = models.TextField(blank=True)
    encrypted_credentials = models.TextField(blank=True)
    encrypted_oauth_state = models.TextField(blank=True)
    tool_catalog = models.JSONField(default=list, blank=True)
    catalog_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "slug"], name="gateway_unique_workspace_slug")
        ]

    @property
    def connected(self):
        try:
            if self.provider_type == self.GENERIC_OAUTH and self.encrypted_oauth_state:
                return bool(self.get_oauth_state().get("access_token"))
            if self.encrypted_headers:
                self.get_headers()
            if self.encrypted_credentials:
                self.get_credentials()
            return bool(self.encrypted_headers or self.encrypted_credentials)
        except ValueError:
            return False

    @property
    def endpoint_url(self):
        return self.base_url or self.remote_url

    def client_metadata_token(self):
        return signing.dumps((self.pk, self.workspace_id), salt="gateway.upstream-client")

    def valid_client_metadata_token(self, token):
        try:
            return signing.loads(token, salt="gateway.upstream-client") == [self.pk, self.workspace_id]
        except signing.BadSignature:
            return False

    def set_headers(self, headers: dict[str, str]):
        self.encrypted_headers = encrypt_headers(headers)

    def get_headers(self):
        return decrypt_headers(self.encrypted_headers)

    def set_credentials(self, value: dict):
        self.encrypted_credentials = encrypt_value(value)

    def get_credentials(self):
        value = decrypt_value(self.encrypted_credentials, {})
        if not isinstance(value, dict):
            raise ValueError("Stored credentials are invalid.")
        return value

    def set_oauth_state(self, value: dict):
        self.encrypted_oauth_state = encrypt_value(value)

    def get_oauth_state(self):
        value = decrypt_value(self.encrypted_oauth_state, {})
        if not isinstance(value, dict):
            raise ValueError("Stored OAuth state is invalid.")
        return value


class SetupLink(models.Model):
    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name="setup_links")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, integration):
        token = secrets.token_urlsafe(32)
        now = timezone.now()
        with transaction.atomic():
            Integration.objects.select_for_update().get(pk=integration.pk)
            cls.objects.filter(integration=integration, used_at__isnull=True).update(used_at=now)
            cls.objects.create(
                integration=integration,
                token_hash=opaque_hash(token),
                expires_at=now + timedelta(minutes=15),
            )
        return token

    @property
    def usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


class RateLimitBucket(models.Model):
    scope = models.CharField(max_length=48)
    identifier_hash = models.CharField(max_length=64)
    window = models.PositiveBigIntegerField()
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "identifier_hash", "window"],
                name="gateway_unique_rate_limit_bucket",
            )
        ]


class OAuthClient(models.Model):
    client_id = models.CharField(max_length=500, unique=True)
    client_name = models.CharField(max_length=200, blank=True)
    redirect_uris = models.JSONField(default=list)
    is_dynamic = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OAuthAuthorizationCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, unique=True)
    redirect_uri = models.URLField(max_length=1000)
    resource = models.URLField(max_length=1000)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=48, default="mcp:read")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OAuthAccessToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    family_id = models.UUIDField(null=True, blank=True, db_index=True)
    resource = models.URLField(max_length=1000)
    scope = models.CharField(max_length=48, default="mcp:read")
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OAuthRefreshToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    family_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    resource = models.URLField(max_length=1000)
    scope = models.CharField(max_length=48, default="mcp:read")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
