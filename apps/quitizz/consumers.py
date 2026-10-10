"""Notification-only subscriptions; authorized HTTP owns all gameplay data."""
import asyncio
from contextlib import suppress
from importlib import import_module
from types import SimpleNamespace
from urllib.parse import urlsplit

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user
from django.core.exceptions import PermissionDenied, ValidationError
from django.http.request import validate_host
from django.utils import timezone

from apps.core.services.scope import ScopeService
from . import gameplay
from .access import require_access
from .public_views import cookie_name
from .realtime import group, tenant_group


class SameOriginValidator:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        headers = scope.get("headers", [])
        origins = [v.decode("latin1") for k, v in headers if k == b"origin"]
        hosts = [v.decode("latin1") for k, v in headers if k == b"host"]
        valid = False
        try:
            origin = urlsplit(origins[0]) if len(origins) == 1 else None
            host = urlsplit("//" + hosts[0]) if len(hosts) == 1 else None
            # Secure cookies require HTTPS even when TLS terminates at Nginx
            # and the ASGI upstream itself uses plain loopback WebSockets.
            expected_scheme = "https" if settings.SESSION_COOKIE_SECURE or scope.get("scheme") in {"https", "wss"} else "http"
            valid = bool(origin and host and origin.scheme == expected_scheme
                         and not origin.username and not origin.password and not origin.query and not origin.fragment
                         and origin.path in {"", "/"}
                         and origin.hostname == host.hostname
                         and (origin.port or (443 if origin.scheme == "https" else 80))
                         == (host.port or (443 if origin.scheme == "https" else 80))
                         and validate_host(host.hostname, settings.ALLOWED_HOSTS))
        except (ValueError, IndexError):
            pass
        if not valid:
            await send({"type": "websocket.close", "code": 4403})
            return
        await self.application(scope, receive, send)


class Subscription(AsyncJsonWebsocketConsumer):
    audience = None
    host_reauthorization_seconds = 30

    @database_sync_to_async
    def authorize(self, *, initial=True):
        public_id = self.scope["url_route"]["kwargs"]["public_id"]
        peer = self.scope.get("client") or ("unknown", 0)
        if initial:
            gameplay.throttle("socket_ip", public_id, peer[0], 3000)
        session = gameplay.resolve(public_id)
        expires = session.expires_at
        groups = [tenant_group(session.tenant_id), "qt.global"]
        if self.audience == "host":
            user = self.scope["user"]
            browser_session = self.scope["session"]
            if not initial:
                # AuthMiddleware's user/session objects are handshake snapshots.
                # Reload both for defense-in-depth admission/heartbeat checks.
                browser_session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=browser_session.session_key)
                user = get_user(SimpleNamespace(session=browser_session))
                if user.pk != self.scope["user"].pk:
                    raise PermissionDenied()
            if not user.is_authenticated or not user.is_active or session.host_id != user.pk:
                raise PermissionDenied()
            tenant_id = ScopeService._parse_int(browser_session.get(ScopeService.SESSION_TENANT_KEY))
            campus_id = ScopeService._parse_int(browser_session.get(ScopeService.SESSION_CAMPUS_KEY))
            if (tenant_id, campus_id) != (session.tenant_id, session.campus_id):
                raise PermissionDenied()
            require_access(user, tenant_id, campus_id, "host")
            if initial:
                gameplay.throttle("socket_host", public_id, user.pk, 30)
            login_expiry = browser_session.get_expiry_date()
            expires = min(expires, login_expiry) if expires else login_expiry
        else:
            credential = self.scope.get("cookies", {}).get(cookie_name(public_id, "socket"), "")
            if initial:
                gameplay.throttle("socket_player", public_id, credential, 30)
            participant = gameplay.identity(session, credential)
            expires = min(expires, participant.reconnect_expires_at) if expires else participant.reconnect_expires_at
            groups.append(group(session.pk, f"p{participant.pk}"))
        groups.append(group(session.pk, self.audience))
        ttl = (expires - timezone.now()).total_seconds()
        if ttl <= 0:
            raise PermissionDenied()
        return groups, ttl

    async def connect(self):
        self.subscription_groups = []
        self.registration_lock = asyncio.Lock()
        self.output_lock = asyncio.Lock()
        self.expiry_task = None
        self.reauthorization_task = None
        self.shutdown_task = None
        self.close_sent = False
        self.revoked = False
        self.admitted = False
        # Let Channels dispatch revocations while admission awaits DB/transport.
        # Even admitted sockets can emit only constant transport notifications.
        self.admission_task = asyncio.create_task(self.admit())

    async def register_and_recheck(self, groups):
        # Cleanup owns the same lock and cannot clear an in-flight add's intent.
        async with self.registration_lock:
            for name in groups:
                if self.revoked:
                    raise PermissionDenied()
                if name not in self.subscription_groups:
                    self.subscription_groups.append(name)
                await self.channel_layer.group_add(name, self.channel_name)
                if self.revoked:
                    await self.channel_layer.group_discard(name, self.channel_name)
                    self.subscription_groups.remove(name)
                    raise PermissionDenied()
        return await self.recheck()

    async def recheck(self):
        checked_groups, ttl = await self.authorize(initial=False)
        if self.revoked or checked_groups != self.subscription_groups:
            raise PermissionDenied()
        return ttl

    async def admit(self):
        try:
            groups, _ = await self.authorize()
            ttl = await self.register_and_recheck(groups)
            async with self.output_lock:
                if self.revoked:
                    return
                # Acceptance grants transport only. No ready or protected data
                # can race a subsequent authorization mutation: HTTP recovers it.
                await self.accept()
                self.expiry_task = asyncio.create_task(self.expire(ttl))
                self.admitted = True
            if self.audience == "host" and not self.revoked:
                self.reauthorization_task = asyncio.create_task(self.reauthorize_host())
        except (PermissionDenied, ValidationError):
            await self.terminate("participant_unavailable")
        except Exception:
            await self.terminate("participant_unavailable", code=1013)

    async def expire(self, ttl):
        await asyncio.sleep(ttl)
        await self.terminate("participant_unavailable")

    async def reauthorize_host(self):
        try:
            while not self.revoked:
                await asyncio.sleep(self.host_reauthorization_seconds)
                await self.recheck()
        except (PermissionDenied, ValidationError):
            await self.terminate("participant_unavailable")
        except Exception:
            await self.terminate("participant_unavailable", code=1013)

    async def cleanup(self):
        async with self.registration_lock:
            for name in list(self.subscription_groups):
                try:
                    await self.channel_layer.group_discard(name, self.channel_name)
                except Exception:
                    # Keep failed intent for a retry on disconnect.
                    continue
                self.subscription_groups.remove(name)

    async def shutdown(self, event, code, was_admitted):
        for task in (self.admission_task, self.expiry_task, self.reauthorization_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await self.cleanup()
        async with self.output_lock:
            if not self.close_sent:
                self.close_sent = True
                if was_admitted and event:
                    await self.send_json({"event": "feature_unavailable" if event == "feature_unavailable" else "participant_unavailable"})
                await self.close(code=code)

    async def terminate(self, event=None, *, code=4403):
        if self.shutdown_task is None:
            self.revoked = True
            was_admitted, self.admitted = self.admitted, False
            # One owner cancels/awaits background tasks and sends at most one
            # close. Shield it from cancellation of the admission/timer caller.
            self.shutdown_task = asyncio.create_task(self.shutdown(event, code, was_admitted))
        await asyncio.shield(self.shutdown_task)

    async def disconnect(self, code):
        await self.terminate()
        await self.cleanup()

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        # No JSON mutation protocol. Only a literal ping, bounded by client and
        # server cadence, refreshes group membership (Redis group TTL).
        now = asyncio.get_running_loop().time()
        if self.revoked or not self.admitted:
            return
        if text_data != "ping" or now - getattr(self, "last_ping", -100) < 10:
            await self.terminate(code=4400)
            return
        self.last_ping = now
        try:
            # Redis may have lost membership and its revocation notification.
            # Re-register and recheck as transport hygiene; pong grants no UI access.
            async with self.output_lock:
                await self.register_and_recheck(list(self.subscription_groups))
                if not self.revoked:
                    await self.send_json({"event": "pong"})
        except (PermissionDenied, ValidationError):
            await self.quitizz_revoke({"event": "participant_unavailable"})
            return
        except Exception:
            await self.terminate(code=1013)
            return

    async def quitizz_event(self, event):
        if getattr(self, "revoked", False) or not getattr(self, "admitted", False):
            return
        try:
            async with self.output_lock:
                # Deliberately zero authorization reads. No final read could
                # linearize revocation with send; this constant reveals no state.
                if not self.revoked and self.admitted:
                    await self.send_json({"event": "sync_required"})
        except Exception:
            await self.terminate(code=1013)

    async def quitizz_revoke(self, event):
        await self.terminate(event["event"])


class HostConsumer(Subscription):
    audience = "host"


class PlayerConsumer(Subscription):
    audience = "players"
