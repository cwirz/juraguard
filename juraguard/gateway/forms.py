import json

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from .models import Integration
from .providers import get_provider, providers
from .security import clean_secret_headers, validate_remote_url

class LicenseKeyForm(forms.Form):
    license_key = forms.CharField(
        label="License key",
        strip=True,
        widget=forms.PasswordInput(render_value=False),
        help_text="Encrypted at rest and never shown again.",
    )


class OwnerSetupForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")


class IntegrationForm(forms.ModelForm):
    class Meta:
        model = Integration
        fields = ("provider_type", "name", "slug", "description", "base_url", "remote_url", "write_enabled", "active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_base_url = self.instance.base_url
        provider = get_provider(self.instance.provider_type)
        if self.instance.pk and provider:
            try:
                self._original_base_url = provider.normalize_url(self.instance.base_url)
            except ValidationError:
                self._original_base_url = ""
        self.fields["provider_type"].help_text = "Integration type cannot be changed after creation."
        builtin_keys = " ".join(providers())
        for builtin_provider in providers().values():
            for credential in builtin_provider.credential_fields:
                field = self.fields.setdefault(
                    credential.name,
                    forms.CharField(
                        label=credential.label,
                        required=False,
                        widget=forms.PasswordInput(render_value=False),
                        help_text=credential.help_text or "Stored encrypted. Never shown again or returned through MCP.",
                    ),
                )
                provider_fields = set(getattr(field, "provider_fields", "").split())
                provider_fields.add(builtin_provider.key)
                field.provider_fields = " ".join(sorted(provider_fields))
        self.fields["base_url"].label = "Provider base URL"
        self.fields["base_url"].provider_fields = builtin_keys
        self.fields["remote_url"].provider_fields = "generic_oauth generic_custom"
        self.fields["write_enabled"].provider_fields = builtin_keys
        self.fields["remote_url"].label = "Remote MCP URL"
        self.fields["write_enabled"].label = "Allow write tools"
        if self.instance.pk:
            self.fields["provider_type"].disabled = True

    def clean(self):
        cleaned = super().clean()
        provider_type = cleaned.get("provider_type")
        if self.instance.pk and provider_type != self.instance.provider_type:
            raise ValidationError("Integration type cannot be changed.")
        provider = get_provider(provider_type)
        if provider:
            cleaned["base_url"] = cleaned.get("base_url") or provider.default_base_url
            if not cleaned.get("base_url"):
                self.add_error("base_url", f"{provider.label} base URL is required.")
            else:
                try:
                    cleaned["base_url"] = provider.normalize_url(cleaned["base_url"])
                except ValidationError as exc:
                    self.add_error("base_url", exc)
            base_changed = self.instance.pk and cleaned.get("base_url") != self._original_base_url
            credential_names = [field.name for field in provider.credential_fields]
            supplied = {name: cleaned.get(name) for name in credential_names}
            credential_field = credential_names[0]
            if base_changed and not all(supplied.values()):
                label = provider.credential_fields[0].label.lower()
                self.add_error(credential_field, f"Enter a new {label} when changing the {provider.label} base URL.")
            if not all(supplied.values()) and not self.instance.encrypted_credentials:
                self.add_error(credential_field, f"{provider.credential_fields[0].label} is required.")
            if all(supplied.values()) and cleaned.get("base_url") and "base_url" not in self.errors:
                try:
                    provider.validate_credentials(cleaned["base_url"], supplied)
                except ValidationError as exc:
                    self.add_error(credential_field, exc)
            cleaned["remote_url"] = ""
        elif not cleaned.get("remote_url"):
            self.add_error("remote_url", "Remote MCP URL is required.")
        else:
            try:
                cleaned["remote_url"] = validate_remote_url(cleaned["remote_url"])
            except ValidationError as exc:
                self.add_error("remote_url", exc)
            cleaned["base_url"] = ""
        return cleaned

    def save(self, commit=True):
        integration = super().save(commit=False)
        provider = get_provider(integration.provider_type)
        if provider:
            credentials = {field.name: self.cleaned_data.get(field.name) for field in provider.credential_fields}
            if all(credentials.values()):
                integration.set_credentials(credentials)
            integration.tool_catalog = provider.catalog(integration.write_enabled)
        if commit:
            integration.save()
        return integration


class HeaderSetupForm(forms.Form):
    headers = forms.CharField(
        label="Secret HTTP headers",
        help_text='JSON object, for example {"Authorization": "Bearer …"}. Values are encrypted before storage.',
        widget=forms.Textarea(attrs={"rows": 7, "spellcheck": "false"}),
    )

    def clean_headers(self):
        try:
            value = json.loads(self.cleaned_data["headers"])
        except json.JSONDecodeError as exc:
            raise ValidationError("Enter valid JSON.") from exc
        return clean_secret_headers(value)


class BuiltinSetupForm(forms.Form):
    def __init__(self, *args, integration, **kwargs):
        super().__init__(*args, **kwargs)
        self.integration = integration
        self.provider = get_provider(integration.provider_type)
        for field in self.provider.credential_fields:
            self.fields[field.name] = forms.CharField(
                label=field.label, widget=forms.PasswordInput(render_value=False),
                help_text=field.help_text or "Encrypted at rest and never shown or returned through MCP.",
            )

    def clean(self):
        cleaned = super().clean()
        if not self.errors:
            self.provider.validate_credentials(self.integration.base_url, cleaned)
        return cleaned
