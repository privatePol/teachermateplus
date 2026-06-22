from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


class MobileApiCorsMiddleware:
    """Allow configured Flutter clients to call the mobile API with session cookies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_mobile_api_request(request):
            origin = request.headers.get("Origin")
            if request.method == "OPTIONS" and self._is_allowed_origin(origin):
                response = HttpResponse(status=204)
            else:
                response = self.get_response(request)
            self._apply_cors_headers(request, response, origin)
            return response
        return self.get_response(request)

    def _is_mobile_api_request(self, request) -> bool:
        return request.path.startswith("/api/mobile/v1/")

    def _is_allowed_origin(self, origin) -> bool:
        return bool(origin and origin in getattr(settings, "MOBILE_API_CORS_ALLOWED_ORIGINS", ()))

    def _apply_cors_headers(self, request, response, origin):
        patch_vary_headers(response, ("Origin",))
        if not self._is_allowed_origin(origin):
            return
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "accept, content-type, x-csrftoken"
        response["Access-Control-Max-Age"] = "600"
