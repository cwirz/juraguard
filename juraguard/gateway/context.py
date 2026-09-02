from django.conf import settings


def product(request):
    return {
        "product_name": settings.PRODUCT_NAME,
        "cloud_mode": settings.DEPLOYMENT_MODE == "cloud",
        "billing_enabled": settings.POLAR_BILLING_ENABLED,
    }
