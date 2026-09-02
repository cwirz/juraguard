from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from polar_sdk.webhooks import WebhookUnknownTypeError, WebhookVerificationError

from gateway.entitlements import has_entitlement
from gateway.models import BillingAccount, PolarWebhookEvent, personal_workspace


CLOUD = override_settings(
    DEPLOYMENT_MODE="cloud",
    POLAR_BILLING_ENABLED=True,
    POLAR_ACCESS_TOKEN="polar-secret",
    POLAR_WEBHOOK_SECRET="webhook-secret",
    POLAR_MONTHLY_PRODUCT_ID="monthly-product",
    POLAR_ANNUAL_PRODUCT_ID="annual-product",
    POLAR_BETA_DISCOUNT_ID="beta-discount",
    POLAR_SERVER_URL="https://sandbox-api.polar.sh",
    CLOUD_BETA_ACCESS=False,
)


@CLOUD
class BillingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", "owner@example.com", "password")
        self.workspace = personal_workspace(self.user)
        self.client.force_login(self.user)

    @patch("gateway.billing._client")
    def test_checkout_uses_allowlisted_server_values_and_workspace_correlation(self, client_factory):
        client_factory.return_value.checkouts.create.return_value.url = "https://checkout.polar.sh/session"

        response = self.client.post(reverse("billing_checkout"), {"plan": "monthly", "product": "attacker"})

        self.assertRedirects(response, "https://checkout.polar.sh/session", fetch_redirect_response=False)
        checkout = client_factory.return_value.checkouts.create.call_args.kwargs["request"]
        self.assertEqual(checkout.products, ["monthly-product"])
        self.assertEqual(checkout.discount_id, "beta-discount")
        self.assertEqual(checkout.external_customer_id, self.workspace.external_customer_id)
        self.assertEqual(checkout.customer_email, self.user.email)
        self.assertEqual(checkout.metadata["workspace_id"], str(self.workspace.pk))
        self.assertEqual(self.client.post(reverse("billing_checkout"), {"plan": "unknown"}).status_code, 400)

    @patch("gateway.billing._client")
    def test_portal_verifies_customer_ownership(self, client_factory):
        BillingAccount.objects.create(workspace=self.workspace, polar_customer_id="customer-1")
        client_factory.return_value.customers.get_external.return_value.id = "customer-1"
        client_factory.return_value.customer_sessions.create.return_value.customer_portal_url = "https://polar.sh/portal"

        response = self.client.post(reverse("billing_portal"))

        self.assertRedirects(response, "https://polar.sh/portal", fetch_redirect_response=False)
        request = client_factory.return_value.customer_sessions.create.call_args.kwargs["request"]
        self.assertEqual(request.external_customer_id, self.workspace.external_customer_id)
        client_factory.return_value.customers.get_external.return_value.id = "customer-other"
        self.assertEqual(self.client.post(reverse("billing_portal")).status_code, 404)

    @patch("gateway.billing.validate_event", side_effect=WebhookVerificationError("bad signature"))
    def test_webhook_rejects_invalid_signature(self, validate):
        response = self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        validate.assert_called_once()

    def subscription_event(
        self,
        event_type="subscription.active",
        workspace=None,
        customer_id="customer-1",
        subscription_id="subscription-1",
        status="active",
        event_at=None,
    ):
        workspace = workspace or self.workspace
        data = SimpleNamespace(
            id=subscription_id,
            customer_id=customer_id,
            product_id="monthly-product",
            status=SimpleNamespace(value=status),
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            trial_start=None,
            trial_end=None,
            customer=SimpleNamespace(external_id=workspace.external_customer_id),
        )
        return SimpleNamespace(TYPE=event_type, data=data, timestamp=event_at or timezone.now())

    @patch("gateway.billing.validate_event")
    def test_webhook_lifecycle_is_idempotent(self, validate):
        validate.return_value = self.subscription_event()
        headers = {"HTTP_WEBHOOK_ID": "event-1"}

        first = self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", **headers)
        duplicate = self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", **headers)

        self.assertEqual((first.status_code, duplicate.status_code), (200, 200))
        account = BillingAccount.objects.get(workspace=self.workspace)
        self.assertEqual((account.status, account.plan), ("active", "monthly"))
        self.assertEqual(account.last_event_at, validate.return_value.timestamp)
        self.assertEqual(PolarWebhookEvent.objects.filter(event_id="event-1").count(), 1)
        self.assertEqual(PolarWebhookEvent.objects.get(event_id="event-1").occurred_at, validate.return_value.timestamp)

        validate.return_value = self.subscription_event("subscription.revoked")
        self.client.post(
            reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="event-2"
        )
        account.refresh_from_db()
        self.assertEqual(account.status, "revoked")

    @patch("gateway.billing.validate_event")
    def test_stale_event_is_ignored_and_new_subscription_can_replace_terminal_one(self, validate):
        revoked_at = timezone.now()
        validate.return_value = self.subscription_event("subscription.revoked", event_at=revoked_at)
        self.client.post(
            reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="event-revoked"
        )

        validate.return_value = self.subscription_event(event_at=revoked_at - timedelta(minutes=1))
        self.client.post(
            reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="event-stale-active"
        )
        account = BillingAccount.objects.get(workspace=self.workspace)
        self.assertEqual((account.status, account.polar_subscription_id), ("revoked", "subscription-1"))

        validate.return_value = self.subscription_event(
            subscription_id="subscription-2", event_at=revoked_at + timedelta(minutes=1)
        )
        self.client.post(
            reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="event-resubscribe"
        )
        account.refresh_from_db()
        self.assertEqual((account.status, account.polar_subscription_id), ("active", "subscription-2"))

    @patch("gateway.billing.validate_event")
    def test_same_timestamp_revoked_overrides_active(self, validate):
        event_at = timezone.now()
        validate.return_value = self.subscription_event(event_at=event_at)
        self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="active")
        validate.return_value = self.subscription_event("subscription.revoked", event_at=event_at)
        self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="revoked")

        self.assertEqual(BillingAccount.objects.get(workspace=self.workspace).status, "revoked")

    @patch("gateway.billing.validate_event")
    def test_same_timestamp_active_does_not_override_revoked(self, validate):
        event_at = timezone.now()
        validate.return_value = self.subscription_event("subscription.revoked", event_at=event_at)
        self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="revoked")
        validate.return_value = self.subscription_event(event_at=event_at)
        self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="active")

        self.assertEqual(BillingAccount.objects.get(workspace=self.workspace).status, "revoked")

    @patch("gateway.billing.validate_event", side_effect=WebhookUnknownTypeError("future.event"))
    def test_unknown_valid_webhook_is_acknowledged(self, validate):
        response = self.client.post(
            reverse("polar_webhook"),
            b'{"type":"future.event"}',
            content_type="application/json",
            HTTP_WEBHOOK_ID="event-future",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PolarWebhookEvent.objects.filter(event_id="event-future").exists())

    @patch("gateway.billing.validate_event")
    def test_webhook_cannot_move_customer_between_workspaces(self, validate):
        other_user = get_user_model().objects.create_user("other")
        other = personal_workspace(other_user)
        BillingAccount.objects.create(workspace=other, polar_customer_id="customer-1")
        validate.return_value = self.subscription_event(customer_id="customer-1")

        response = self.client.post(
            reverse("polar_webhook"), b"{}", content_type="application/json", HTTP_WEBHOOK_ID="event-cross"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingAccount.objects.filter(workspace=self.workspace).exists())

    def test_cloud_entitlement_uses_beta_or_subscription_status(self):
        self.assertFalse(has_entitlement(self.workspace, "organization_controls"))
        account = BillingAccount.objects.create(workspace=self.workspace, status="past_due")
        self.assertFalse(has_entitlement(self.workspace, "organization_controls"))
        account.status = "trialing"
        account.trial_end = timezone.now() + timedelta(days=1)
        account.save()
        self.assertTrue(has_entitlement(self.workspace, "organization_controls"))
        account.trial_end = timezone.now() - timedelta(seconds=1)
        account.save()
        self.assertFalse(has_entitlement(self.workspace, "organization_controls"))
        account.status = "active"
        account.current_period_end = timezone.now() - timedelta(seconds=1)
        account.save()
        self.assertFalse(has_entitlement(self.workspace, "organization_controls"))
        with override_settings(CLOUD_BETA_ACCESS=True):
            self.assertTrue(has_entitlement(self.workspace, "organization_controls"))

    def test_protected_route_enforces_entitlement_server_side(self):
        self.assertEqual(self.client.get(reverse("organization_controls")).status_code, 403)
        BillingAccount.objects.create(
            workspace=self.workspace, status="active", current_period_end=timezone.now() + timedelta(days=1)
        )
        self.assertEqual(self.client.get(reverse("organization_controls")).status_code, 200)


class ModeGatingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner")
        self.client.force_login(self.user)

    def test_cloud_billing_routes_are_absent_in_self_hosted_mode(self):
        self.assertEqual(self.client.get(reverse("billing")).status_code, 404)
        self.assertEqual(self.client.post(reverse("polar_webhook"), b"{}", content_type="application/json").status_code, 404)

    @override_settings(DEPLOYMENT_MODE="cloud", POLAR_BILLING_ENABLED=False)
    def test_explicitly_disabled_cloud_billing_is_absent(self):
        self.assertEqual(self.client.get(reverse("billing")).status_code, 404)

    @CLOUD
    def test_license_routes_are_absent_in_cloud_mode(self):
        self.assertEqual(self.client.get(reverse("license")).status_code, 404)
        self.assertEqual(self.client.post(reverse("license_remove")).status_code, 404)
