from django.urls import include, path

from gateway.views import health_live, health_ready


urlpatterns = [
    path("health/", health_ready, name="health"),
    path("health/live/", health_live, name="health_live"),
    path("health/ready/", health_ready, name="health_ready"),
    path("accounts/", include("allauth.urls")),
    path("", include("gateway.urls")),
]
