from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from . import billing
from commercial.views import validate_license
from . import oauth_server
from .protocol import mcp


urlpatterns = [
    path("", views.landing, name="landing"),
    path("docs/", views.docs, name="docs"),
    path("setup/", views.owner_setup, name="owner_setup"),
    path("login/", views.login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/token/rotate/", views.rotate_token, name="rotate_token"),
    path("oauth/clients/", views.oauth_clients, name="oauth_clients"),
    path("oauth/clients/<int:pk>/revoke/", views.oauth_client_revoke, name="oauth_client_revoke"),
    path("billing/", views.billing_page, name="billing"),
    path("billing/checkout/", views.billing_checkout, name="billing_checkout"),
    path("billing/portal/", views.billing_portal, name="billing_portal"),
    path("polar/webhook/", billing.webhook, name="polar_webhook"),
    path("license/", views.license_page, name="license"),
    path("license/remove/", views.license_remove, name="license_remove"),
    path("api/licenses/validate/", validate_license, name="validate_license"),
    path("organization-controls/", views.organization_controls, name="organization_controls"),
    path("integrations/add/", views.integration_create, name="integration_create"),
    path("integrations/<int:pk>/edit/", views.integration_edit, name="integration_edit"),
    path("integrations/<int:pk>/toggle/", views.integration_toggle, name="integration_toggle"),
    path("integrations/<int:pk>/reconnect/", views.integration_reconnect, name="integration_reconnect"),
    path("integrations/<int:pk>/test/", views.integration_test, name="integration_test"),
    path("integrations/<int:pk>/oauth/callback/", views.upstream_oauth_callback, name="upstream_oauth_callback"),
    path("integrations/<int:pk>/delete/", views.integration_delete, name="integration_delete"),
    path("connect/<str:token>/", views.credential_setup, name="credential_setup"),
    path(".well-known/oauth-protected-resource/mcp/", oauth_server.protected_resource_metadata,
         name="oauth_resource_metadata"),
    path(".well-known/oauth-authorization-server", oauth_server.authorization_server_metadata,
         name="oauth_server_metadata"),
    path(".well-known/openid-configuration", oauth_server.authorization_server_metadata),
    path("oauth/authorize/", oauth_server.authorize, name="oauth_authorize"),
    path("oauth/token/", oauth_server.token, name="oauth_token"),
    path("oauth/register/", oauth_server.register, name="oauth_register"),
    path("oauth/revoke/", oauth_server.revoke, name="oauth_revoke"),
    path("oauth/upstream-client/<int:pk>/<str:token>/", views.upstream_client_document,
         name="upstream_client_document"),
    path("mcp/", mcp, name="mcp"),
]
