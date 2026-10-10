"""Public cookie-authenticated HTTP transport; game rules live in gameplay.py."""
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from . import gameplay


def cookie_name(public_id, kind="player"):
    # TOKEN also lets Django's standard exception reporter redact these cookies.
    return f"quitizz_{kind}_token_{str(public_id).replace('-', '')}"


def credential(request, public_id):
    return request.COOKIES.get(cookie_name(public_id), "")


def set_cookie(response, request, public_id, value, *, kind="player", max_age=8 * 3600):
    response.set_cookie(cookie_name(public_id, kind), value, max_age=max_age,
        path=reverse("quitizz:play", kwargs={"public_id": public_id}), httponly=True,
        secure=bool(settings.SESSION_COOKIE_SECURE or request.is_secure()), samesite="Lax")


def private(response):
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def endpoint(kind):
    def decorate(view):
        @wraps(view)
        def wrapped(request, public_id):
            received_at = timezone.now()
            try:
                if request.method == "POST" and (len(request.body) > 4096 or request.content_type != "application/x-www-form-urlencoded"):
                    return private(JsonResponse({"error": "Request cannot be accepted."}, status=413))
                # Invalid capabilities never receive existence-specific error details.
                ip = request.META.get("REMOTE_ADDR", "unknown")
                if kind in {"exchange", "join"}:
                    gameplay.throttle(kind, public_id, ip, 300)
                elif kind in {"answer", "state"}:
                    gameplay.throttle(kind, public_id, credential(request, public_id), 60)
                    gameplay.throttle(f"{kind}_ip", public_id, ip, 3000)
                request.quitizz_received_at = received_at
                return private(view(request, public_id))
            except gameplay.Unavailable as exc:
                return private(JsonResponse({"error": exc.messages[0]}, status=404))
            except gameplay.RateLimited as exc:
                response = private(JsonResponse({"error": exc.messages[0]}, status=429))
                response["Retry-After"] = "60"
                return response
            except ValidationError as exc:
                return private(JsonResponse({"error": exc.messages[0]}, status=400))
        return wrapped
    return decorate


@require_GET
@ensure_csrf_cookie
@endpoint("play")
def play(request, public_id):
    gameplay.resolve(public_id)
    return render(request, "quitizz/play.html", {"public_id": public_id})


@require_POST
@sensitive_post_parameters("capability")
@endpoint("exchange")
def exchange(request, public_id):
    grant = gameplay.exchange(public_id, request.POST.get("capability", ""))
    response = JsonResponse({"exchanged": True})
    set_cookie(response, request, public_id, grant, kind="grant", max_age=600)
    return response


@require_POST
@sensitive_variables("value")
@endpoint("join")
def join(request, public_id):
    participant, value = gameplay.join(public_id, request.COOKIES.get(cookie_name(public_id, "grant"), ""),
        request.POST.get("nickname", ""), credential(request, public_id))
    response = JsonResponse({"joined": True, "nickname": participant.nickname})
    max_age = max(0, int((participant.reconnect_expires_at - timezone.now()).total_seconds()))
    set_cookie(response, request, public_id, value, max_age=max_age)
    response.delete_cookie(cookie_name(public_id, "grant"), path=reverse("quitizz:play", kwargs={"public_id": public_id}), samesite="Lax")
    return response


@require_GET
@endpoint("state")
def state(request, public_id):
    return JsonResponse(gameplay.state(public_id, credential(request, public_id)))


@require_POST
@endpoint("answer")
def answer(request, public_id):
    return JsonResponse(gameplay.submit(public_id, credential(request, public_id), request.POST.get("question", ""),
        request.POST.get("choice", ""), received_at=request.quitizz_received_at))


@require_POST
@endpoint("state")
@sensitive_variables("value")
def socket_identity(request, public_id):
    session = gameplay.resolve(public_id)
    value = credential(request, public_id)
    participant = gameplay.identity(session, value)
    response = JsonResponse({"ready": True})
    # Original reconnect cookie stays scoped to HTTP play. This HttpOnly bridge
    # is sent only to this session's player socket, never exposed to JS or URLs.
    response.set_cookie(cookie_name(public_id, "socket"), value,
        path=f"/ws/quitizz/{public_id}/player/", httponly=True, samesite="Strict",
        secure=bool(settings.SESSION_COOKIE_SECURE or request.is_secure()),
        max_age=max(0, int((participant.reconnect_expires_at - timezone.now()).total_seconds())))
    return response
