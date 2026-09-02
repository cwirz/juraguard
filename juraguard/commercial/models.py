import uuid

from django.db import models


class InstanceLicense(models.Model):
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    encrypted_key = models.TextField(blank=True)
    signed_document = models.TextField(blank=True)
    status = models.CharField(max_length=32, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    grace_deadline = models.DateTimeField(null=True, blank=True)

    @classmethod
    def current(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    def __str__(self):
        return f"Juraguard instance {self.instance_id}"


class IssuedLicense(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    organization = models.CharField(max_length=200)
    entitlements = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    bound_instance_id = models.UUIDField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
