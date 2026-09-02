import fcntl
import os
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import connection, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from commercial.models import InstanceLicense
from commercial.service import LicenseServerError, install as install_license
from commercial.service import remove as remove_license
from commercial.service import usable_claims

from . import billing
from .entitlements import has_entitlement
from .forms import BuiltinSetupForm, HeaderSetupForm, IntegrationForm, LicenseKeyForm, OwnerSetupForm
from .hardening import security_event
from .models import (
    BillingAccount,
    GatewayToken,
    Integration,
    OAuthAccessToken,
    OAuthClient,
    OAuthRefreshToken,
    SetupLink,
    personal_workspace,
)
from .providers import get_provider
from .remote import RemoteError, list_tools
from .security import opaque_hash
from .upstream_oauth import UpstreamOAuthError, callback as complete_upstream_oauth
from .upstream_oauth import client_metadata as upstream_client_metadata
from .upstream_oauth import start as start_upstream_oauth


def _create_first_owner(form):
    if connection.vendor == "postgresql":
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [8472645110252201])
            if get_user_model().objects.exists():
                return None
            return form.save()

    descriptor = os.open(settings.DATA_DIR / "owner_setup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with transaction.atomic():
            if get_user_model().objects.exists():
                return None
            return form.save()
    finally:
        os.close(descriptor)


def landing(request):
    return render(request, "gateway/landing.html")


def docs(request):
    base_url = settings.PUBLIC_BASE_URL.rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    return render(request, "gateway/docs.html", {"mcp_url": f"{base_url}/mcp/"})


def owner_setup(request):
    if settings.DEPLOYMENT_MODE == "cloud":
        raise Http404
    if get_user_model().objects.exists():
        return redirect("login")
    if time.time() >= settings.OWNER_SETUP_DEADLINE:
        raise Http404
    if not request.session.get("owner_setup_authorized"):
        token = request.GET.get("token", "")
        if not token or not secrets.compare_digest(token, settings.OWNER_SETUP_TOKEN):
            raise Http404
        request.session["owner_setup_authorized"] = True
        return redirect("owner_setup")
    form = OwnerSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = _create_first_owner(form)
        if user is None:
            return redirect("login")
        request.session.pop("owner_setup_authorized", None)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Owner account created. Generate your gateway token next.")
        return redirect("dashboard")
    return render(request, "gateway/owner_setup.html", {
        "form": form,
        "heading": "Create the owner account",
        "intro": "First run only. This account controls integrations and gateway access.",
        "submit_label": "Create owner",
    })


@login_required
def dashboard(request):
    return _dashboard_response(request)


def _dashboard_response(request, shown_token=None):
    integrations = Integration.objects.filter(workspace=personal_workspace(request.user))
    token_exists = GatewayToken.objects.filter(user=request.user).exists()
    return render(request, "gateway/dashboard.html", {
        "integrations": integrations,
        "token_exists": token_exists,
        "shown_token": shown_token,
        "organization_controls": has_entitlement(request.user, "organization_controls"),
    })


@login_required
@require_POST
def rotate_token(request):
    token = GatewayToken.issue(request.user)
    messages.success(request, "New token generated. Copy it now; it will not be shown again.")
    response = _dashboard_response(request, token)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


@login_required
def oauth_clients(request):
    now = timezone.now()
    grants = {}
    token_sets = (
        ("access_expires_at", OAuthAccessToken.objects.filter(
            user=request.user, revoked_at__isnull=True, expires_at__gt=now,
        ).select_related("client")),
        ("refresh_expires_at", OAuthRefreshToken.objects.filter(
            user=request.user, used_at__isnull=True, revoked_at__isnull=True, expires_at__gt=now,
        ).select_related("client")),
    )
    for expiry_field, tokens in token_sets:
        for token in tokens:
            grant = grants.setdefault(token.client_id, {
                "client": token.client,
                "scopes": set(),
                "first_granted_at": token.created_at,
                "last_granted_at": token.created_at,
                "access_expires_at": None,
                "refresh_expires_at": None,
            })
            grant["scopes"].update(token.scope.split())
            grant["first_granted_at"] = min(grant["first_granted_at"], token.created_at)
            grant["last_granted_at"] = max(grant["last_granted_at"], token.created_at)
            current_expiry = grant[expiry_field]
            grant[expiry_field] = max(current_expiry, token.expires_at) if current_expiry else token.expires_at
    for grant in grants.values():
        grant["scope"] = " ".join(sorted(grant.pop("scopes")))
    ordered = sorted(grants.values(), key=lambda grant: (grant["client"].client_name, grant["client"].client_id))
    return render(request, "gateway/oauth_clients.html", {"grants": ordered})


@login_required
@require_POST
def oauth_client_revoke(request, pk):
    access = OAuthAccessToken.objects.filter(user=request.user, client_id=pk)
    refresh = OAuthRefreshToken.objects.filter(user=request.user, client_id=pk)
    if not access.exists() and not refresh.exists():
        raise Http404
    now = timezone.now()
    access.filter(revoked_at__isnull=True).update(revoked_at=now)
    refresh.filter(revoked_at__isnull=True).update(revoked_at=now)
    client = get_object_or_404(OAuthClient, pk=pk)
    security_event("oauth_client_revoked", request, workspace=personal_workspace(request.user), outcome="success")
    messages.success(request, f"Access revoked for {client.client_name or client.client_id}.")
    return redirect("oauth_clients")


@login_required
def integration_create(request):
    form = IntegrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        integration = form.save(commit=False)
        integration.workspace = personal_workspace(request.user)
        integration.save()
        security_event("integration_created", request, workspace=integration.workspace, integration=integration,
                       outcome="success")
        provider = get_provider(integration.provider_type)
        if provider:
            messages.success(request, f"{provider.label} connected. Credentials are encrypted and hidden.")
            return redirect("dashboard")
        if integration.provider_type == Integration.GENERIC_OAUTH:
            try:
                return start_upstream_oauth(request, integration)
            except UpstreamOAuthError as exc:
                messages.warning(request, f"OAuth unavailable: {exc} Use the token fallback below.")
        token = SetupLink.issue(integration)
        security_event("setup_link_created", request, workspace=integration.workspace, integration=integration,
                       outcome="success")
        return redirect("credential_setup", token=token)
    return render(request, "gateway/integration_form.html", {"form": form, "heading": "Add integration"})


@login_required
def integration_edit(request, pk):
    integration = get_object_or_404(Integration, workspace=personal_workspace(request.user), pk=pk)
    form = IntegrationForm(request.POST or None, instance=integration)
    if request.method == "POST" and form.is_valid():
        changed_url = "remote_url" in form.changed_data or "base_url" in form.changed_data
        integration = form.save(commit=False)
        if changed_url and not get_provider(integration.provider_type):
            integration.tool_catalog = []
            integration.catalog_updated_at = None
        integration.save()
        security_event("integration_updated", request, workspace=integration.workspace, integration=integration,
                       outcome="success")
        messages.success(request, "Integration updated.")
        return redirect("dashboard")
    return render(request, "gateway/integration_form.html", {"form": form, "heading": "Edit integration"})


@login_required
@require_POST
def integration_toggle(request, pk):
    integration = get_object_or_404(Integration, workspace=personal_workspace(request.user), pk=pk)
    integration.active = not integration.active
    integration.save(update_fields=["active", "updated_at"])
    security_event("integration_toggled", request, workspace=integration.workspace, integration=integration,
                   outcome="success")
    return redirect("dashboard")


@login_required
@require_POST
def integration_reconnect(request, pk):
    integration = get_object_or_404(Integration, workspace=personal_workspace(request.user), pk=pk)
    if get_provider(integration.provider_type):
        return redirect("integration_edit", pk=integration.pk)
    if integration.provider_type == Integration.GENERIC_OAUTH:
        try:
            return start_upstream_oauth(request, integration)
        except UpstreamOAuthError as exc:
            messages.warning(request, f"OAuth unavailable: {exc} Use the token fallback below.")
    token = SetupLink.issue(integration)
    security_event("setup_link_created", request, workspace=integration.workspace, integration=integration,
                   outcome="success")
    return redirect("credential_setup", token=token)


@login_required
@require_POST
def integration_delete(request, pk):
    integration = get_object_or_404(Integration, workspace=personal_workspace(request.user), pk=pk)
    security_event("integration_deleted", request, workspace=integration.workspace, integration=integration,
                   outcome="success")
    integration.delete()
    messages.success(request, "Integration deleted.")
    return redirect("dashboard")


def credential_setup(request, token):
    link = SetupLink.objects.select_related("integration").filter(token_hash=opaque_hash(token)).first()
    if link is None or not link.usable:
        security_event("setup_link_failed", request, outcome="denied", reason="invalid_or_expired")
        raise Http404("This setup link is invalid, expired, or already used.")
    integration = link.integration
    if integration.provider_type == Integration.GENERIC_OAUTH:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if integration.workspace != personal_workspace(request.user):
            raise Http404
        now = timezone.now()
        claimed = SetupLink.objects.filter(
            pk=link.pk, token_hash=opaque_hash(token), used_at__isnull=True, expires_at__gt=now,
        ).update(used_at=now)
        if not claimed:
            security_event("setup_link_failed", request, outcome="denied", reason="replayed")
            raise Http404("This setup link is invalid, expired, or already used.")
        try:
            response = start_upstream_oauth(request, integration)
        except UpstreamOAuthError as exc:
            security_event("setup_link_failed", request, workspace=integration.workspace, integration=integration,
                           outcome="failed", reason="oauth_start_failed")
            return render(request, "gateway/oauth_error.html", {
                "message": f"{exc} This one-use link was consumed; return to the dashboard to try again.",
            }, status=400)
        security_event("setup_link_used", request, workspace=integration.workspace, integration=integration,
                       outcome="success")
        return response

    provider = get_provider(integration.provider_type)
    form = (BuiltinSetupForm(request.POST or None, integration=integration)
            if provider else HeaderSetupForm(request.POST or None))
    if request.method == "POST" and form.is_valid():
        now = timezone.now()
        with transaction.atomic():
            claimed = SetupLink.objects.select_for_update().select_related("integration").filter(
                pk=link.pk, token_hash=opaque_hash(token), used_at__isnull=True, expires_at__gt=now,
            ).first()
            if claimed is None:
                security_event("setup_link_failed", request, outcome="denied", reason="replayed")
                raise Http404("This setup link is invalid, expired, or already used.")
            claimed.used_at = now
            claimed.save(update_fields=["used_at"])
        integration = claimed.integration
        if provider:
            integration.set_credentials({field.name: form.cleaned_data[field.name] for field in provider.credential_fields})
            integration.tool_catalog = provider.catalog(integration.write_enabled)
            integration.catalog_updated_at = now
            integration.save(update_fields=[
                "encrypted_credentials", "tool_catalog", "catalog_updated_at", "updated_at",
            ])
            security_event("setup_link_used", request, workspace=integration.workspace, integration=integration,
                           outcome="success")
            return render(request, "gateway/credential_complete.html", {"integration": integration})

        integration.set_headers(form.cleaned_data["headers"])
        try:
            integration.tool_catalog = list_tools(integration)
        except RemoteError as exc:
            security_event("setup_link_failed", request, workspace=integration.workspace, integration=integration,
                           outcome="failed", reason="upstream_rejected")
            form.add_error(None, f"Connection failed: {exc}")
        else:
            integration.catalog_updated_at = now
            integration.save(update_fields=[
                "encrypted_headers", "encrypted_oauth_state", "tool_catalog", "catalog_updated_at", "updated_at",
            ])
            security_event("setup_link_used", request, workspace=integration.workspace, integration=integration,
                           outcome="success")
            return render(request, "gateway/credential_complete.html", {"integration": integration})
    return render(request, "gateway/credential_setup.html", {"form": form, "integration": link.integration})


@login_required
@require_POST
def integration_test(request, pk):
    integration = get_object_or_404(Integration, workspace=personal_workspace(request.user), pk=pk)
    provider = get_provider(integration.provider_type)
    try:
        if provider:
            provider.test_connection(integration)
        else:
            list_tools(integration)
    except (ValidationError, ValueError, RemoteError) as exc:
        messages.error(request, f"Connection failed: {exc}")
    else:
        messages.success(request, f"{integration.get_provider_type_display()} connection succeeded.")
    return redirect("dashboard")


def upstream_client_document(request, pk, token):
    integration = get_object_or_404(Integration, pk=pk, provider_type=Integration.GENERIC_OAUTH)
    if not integration.valid_client_metadata_token(token):
        raise Http404
    return JsonResponse(upstream_client_metadata(request, integration))


@login_required
def upstream_oauth_callback(request, pk):
    integration = get_object_or_404(
        Integration,
        workspace=personal_workspace(request.user),
        pk=pk,
        provider_type=Integration.GENERIC_OAUTH,
    )
    try:
        complete_upstream_oauth(request, integration)
        integration.tool_catalog = list_tools(integration)
        integration.catalog_updated_at = timezone.now()
        integration.encrypted_headers = ""
        integration.save(update_fields=[
            "encrypted_headers", "tool_catalog", "catalog_updated_at", "updated_at",
        ])
    except (UpstreamOAuthError, RemoteError) as exc:
        security_event("integration_oauth_failed", request, workspace=integration.workspace,
                       integration=integration, outcome="failed", reason="provider_rejected")
        return render(request, "gateway/oauth_error.html", {"message": str(exc)}, status=400)
    security_event("integration_oauth_connected", request, workspace=integration.workspace,
                   integration=integration, outcome="success")
    messages.success(request, "Remote MCP connected with OAuth.")
    return redirect("dashboard")


def health_live(request):
    return JsonResponse({"status": "ok"})


def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})


def login_view(request):
    if settings.DEPLOYMENT_MODE == "cloud":
        from allauth.account.views import login as account_login

        return account_login(request)
    return auth_views.LoginView.as_view(
        template_name="gateway/login.html",
        extra_context={"heading": "Log in", "intro": "Manage integrations and gateway access.", "submit_label": "Log in"},
    )(request)


@login_required
def billing_page(request):
    billing.require_cloud_billing()
    workspace = personal_workspace(request.user)
    account = BillingAccount.objects.filter(workspace=workspace).first()
    return render(request, "gateway/billing.html", {"billing": account})


@login_required
@require_POST
def billing_checkout(request):
    workspace = personal_workspace(request.user)
    response = billing.create_checkout(request, workspace, request.POST.get("plan", ""))
    security_event("billing_checkout", request, workspace=workspace,
                   outcome="success" if response.status_code < 400 else "failed")
    return response


@login_required
@require_POST
def billing_portal(request):
    workspace = personal_workspace(request.user)
    response = billing.create_portal(request, workspace)
    security_event("billing_portal", request, workspace=workspace,
                   outcome="success" if response.status_code < 400 else "failed")
    return response


@login_required
def license_page(request):
    if settings.DEPLOYMENT_MODE != "self_hosted":
        raise Http404
    instance = InstanceLicense.current()
    form = LicenseKeyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            install_license(form.cleaned_data["license_key"])
        except (LicenseServerError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            security_event("license_installed", request, workspace=personal_workspace(request.user), outcome="success")
            messages.success(request, "License installed and validated.")
        return redirect("license")
    claims = usable_claims(instance)
    return render(request, "gateway/license.html", {"form": form, "license": instance, "claims": claims})


@login_required
@require_POST
def license_remove(request):
    if settings.DEPLOYMENT_MODE != "self_hosted":
        raise Http404
    remove_license()
    security_event("license_removed", request, workspace=personal_workspace(request.user), outcome="success")
    messages.success(request, "License and cached entitlement document removed.")
    return redirect("license")


@login_required
def organization_controls(request):
    if not has_entitlement(request.user, "organization_controls"):
        raise PermissionDenied
    return render(request, "gateway/organization_controls.html")
