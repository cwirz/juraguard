from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from commercial.service import usable_claims

from .models import BillingAccount, Workspace


def _workspace(value):
    if isinstance(value, Workspace):
        return value
    return Workspace.objects.filter(owner=value).first()


def has_entitlement(workspace_or_user, name):
    if name not in settings.LICENSE_ENTITLEMENTS:
        return False
    if settings.DEPLOYMENT_MODE == "cloud":
        workspace = _workspace(workspace_or_user)
        if workspace is None:
            return False
        if settings.CLOUD_BETA_ACCESS:
            return True
        now = timezone.now()
        return BillingAccount.objects.filter(workspace=workspace).filter(
            Q(status="active", current_period_end__gt=now) | Q(status="trialing", trial_end__gt=now)
        ).exists()
    claims = usable_claims()
    return bool(claims and name in claims["entitlements"])
