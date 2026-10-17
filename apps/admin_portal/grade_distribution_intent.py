"""One-use non-JS form intents prevent browser POST refresh from rerunning work.

Only short random form identifiers are stored in the session, never analytics.
AJAX uses fetch (page refresh is GET) and its independent stale-response guards.
This is a UX replay guard, not an authorization substitute.
"""
import secrets
import time

KEY = "gdm_manual_form_intents"


def issue_manual_intent(request):
    now = time.time()
    outstanding = [(value, issued) for value, issued in request.session.get(KEY, []) if now-issued < 3600]
    value = secrets.token_urlsafe(32)
    request.session[KEY] = (outstanding + [(value, now)])[-16:]
    return value


def consume_manual_intent(request):
    supplied = request.GET.get("manual_intent", "")
    if len(request.GET.getlist("manual_intent")) != 1 or len(supplied) > 100 or not supplied.isascii():
        return False
    now = time.time()
    outstanding = request.session.get(KEY, [])
    matched = any(now-issued < 3600 and secrets.compare_digest(value, supplied) for value, issued in outstanding)
    if matched:
        request.session[KEY] = [(value, issued) for value, issued in outstanding if value != supplied and now-issued < 3600]
    return matched
