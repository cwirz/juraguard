from django.conf import settings
from django.http import Http404


class DeploymentModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.DEPLOYMENT_MODE == "self_hosted" and request.path_info.startswith("/accounts/"):
            raise Http404
        return self.get_response(request)
