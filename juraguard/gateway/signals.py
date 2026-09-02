from django.conf import settings
from django.contrib.auth.signals import user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Workspace
from .hardening import security_event


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_personal_workspace(sender, instance, created, **kwargs):
    if created:
        Workspace.objects.get_or_create(owner=instance)


@receiver(user_login_failed)
def record_login_failure(sender, request, **kwargs):
    if request is not None:
        security_event("auth_failure", request, outcome="denied", reason="invalid_credentials")
