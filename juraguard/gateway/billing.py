import json

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from polar_sdk import Polar
from polar_sdk.models.checkoutcreate import CheckoutCreate
from polar_sdk.models.customersessioncustomerexternalidcreate import CustomerSessionCustomerExternalIDCreate
from polar_sdk.models.subscriptionstatus import SubscriptionStatus
from polar_sdk.webhooks import WebhookUnknownTypeError, WebhookVerificationError, validate_event

from .models import BillingAccount, PolarWebhookEvent, Workspace


SUBSCRIPTION_EVENTS = {
    "subscription.created",
    "subscription.updated",
    "subscription.active",
    "subscription.canceled",
    "subscription.uncanceled",
    "subscription.revoked",
    "subscription.past_due",
    "subscription.paused",
    "subscription.resumed",
}
TERMINAL_SUBSCRIPTION_STATUSES = {
    SubscriptionStatus.CANCELED.value,
    SubscriptionStatus.INCOMPLETE_EXPIRED.value,
    SubscriptionStatus.UNPAID.value,
    "revoked",  # Polar exposes revocation as an event, not a SubscriptionStatus value.
}
ACCESS_RESTRICTING_SUBSCRIPTION_STATUSES = TERMINAL_SUBSCRIPTION_STATUSES | {
    SubscriptionStatus.PAST_DUE.value,
    SubscriptionStatus.PAUSED.value,
}


def _client():
    return Polar(access_token=settings.POLAR_ACCESS_TOKEN, server_url=settings.POLAR_SERVER_URL, timeout_ms=5000)


def require_cloud_billing():
    if settings.DEPLOYMENT_MODE != "cloud" or not settings.POLAR_BILLING_ENABLED:
        raise Http404


def create_checkout(request, workspace, plan):
    require_cloud_billing()
    products = {"monthly": settings.POLAR_MONTHLY_PRODUCT_ID, "annual": settings.POLAR_ANNUAL_PRODUCT_ID}
    if plan not in products:
        return HttpResponseBadRequest("Choose monthly or annual billing.")
    checkout = _client().checkouts.create(request=CheckoutCreate(
        products=[products[plan]],
        external_customer_id=workspace.external_customer_id,
        customer_email=request.user.email,
        customer_metadata={"workspace_id": str(workspace.pk)},
        metadata={"workspace_id": str(workspace.pk), "plan": plan},
        discount_id=settings.POLAR_BETA_DISCOUNT_ID,
        allow_discount_codes=False,
        success_url=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/billing/?checkout=success",
    ))
    return redirect(checkout.url)


def create_portal(request, workspace):
    require_cloud_billing()
    account = BillingAccount.objects.filter(workspace=workspace).exclude(polar_customer_id__isnull=True).first()
    if account is None:
        raise Http404
    client = _client()
    customer = client.customers.get_external(external_id=workspace.external_customer_id)
    if customer.id != account.polar_customer_id:
        raise Http404
    session = client.customer_sessions.create(request=CustomerSessionCustomerExternalIDCreate(
        external_customer_id=workspace.external_customer_id,
        return_url=f"{settings.PUBLIC_BASE_URL.rstrip('/')}/billing/",
    ))
    return redirect(session.customer_portal_url)


def _event_id(request):
    return request.headers.get("webhook-id", "")


def _status(event_type, subscription):
    if event_type == "subscription.revoked":
        return "revoked"
    value = subscription.status
    return value.value if hasattr(value, "value") else str(value)


def _process_subscription(event_type, subscription, event_at):
    external_id = subscription.customer.external_id
    if not isinstance(external_id, str) or not external_id.startswith("workspace-"):
        return
    try:
        workspace_id = int(external_id.removeprefix("workspace-"))
    except ValueError:
        return
    workspace = Workspace.objects.filter(pk=workspace_id).first()
    if workspace is None or external_id != workspace.external_customer_id:
        return
    if BillingAccount.objects.exclude(workspace=workspace).filter(
        polar_customer_id=subscription.customer_id
    ).exists() or BillingAccount.objects.exclude(workspace=workspace).filter(
        polar_subscription_id=subscription.id
    ).exists():
        return
    account, _ = BillingAccount.objects.select_for_update().get_or_create(workspace=workspace)
    status = _status(event_type, subscription)
    if account.last_event_at and (
        event_at < account.last_event_at
        or event_at == account.last_event_at and status not in ACCESS_RESTRICTING_SUBSCRIPTION_STATUSES
    ):
        return
    if account.polar_customer_id and account.polar_customer_id != subscription.customer_id:
        return
    if account.polar_subscription_id and account.polar_subscription_id != subscription.id:
        if account.status not in TERMINAL_SUBSCRIPTION_STATUSES:
            return
    plans = {
        settings.POLAR_MONTHLY_PRODUCT_ID: "monthly",
        settings.POLAR_ANNUAL_PRODUCT_ID: "annual",
    }
    if subscription.product_id not in plans:
        return
    account.polar_customer_id = subscription.customer_id
    account.polar_subscription_id = subscription.id
    account.product_id = subscription.product_id
    account.plan = plans[subscription.product_id]
    account.status = status
    account.current_period_start = subscription.current_period_start
    account.current_period_end = subscription.current_period_end
    account.trial_start = subscription.trial_start
    account.trial_end = subscription.trial_end
    account.last_event_at = event_at
    account.save()


@csrf_exempt
@require_POST
def webhook(request):
    require_cloud_billing()
    event_id = _event_id(request)
    try:
        event = validate_event(request.body, dict(request.headers), settings.POLAR_WEBHOOK_SECRET)
    except WebhookUnknownTypeError:
        event_type = json.loads(request.body).get("type", "unknown")
        with transaction.atomic():
            PolarWebhookEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": event_type})
        return HttpResponse(status=200)
    except (WebhookVerificationError, ValueError, json.JSONDecodeError):
        return HttpResponse(status=400)
    event_type = event.TYPE
    with transaction.atomic():
        _, created = PolarWebhookEvent.objects.get_or_create(
            event_id=event_id,
            defaults={"event_type": event_type, "occurred_at": event.timestamp},
        )
        if not created:
            return HttpResponse(status=200)
        if event_type in SUBSCRIPTION_EVENTS:
            _process_subscription(event_type, event.data, event.timestamp)
    return HttpResponse(status=200)
