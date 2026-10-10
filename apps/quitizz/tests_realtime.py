import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer, get_channel_layer
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.cache import caches
from django.db import connection, transaction
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.services.scope import ScopeService
from apps.rbac.models import UserPermission, UserRole
from . import gameplay as game, realtime
from .checks import realtime_configuration
from .consumers import HostConsumer, PlayerConsumer, SameOriginValidator, Subscription
from .models import QuiTizzParticipant, QuiTizzResponse
from .public_views import cookie_name
from . import tests_gameplay as phase2a


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer", "CONFIG": {"capacity": 256}}})
class RealtimeTests(TestCase):
    setUpTestData = classmethod(phase2a.GameplayTests.setUpTestData.__func__)
    new_user = classmethod(phase2a.GameplayTests.new_user.__func__)
    grant = classmethod(phase2a.GameplayTests.grant.__func__)
    enable = phase2a.GameplayTests.enable
    args = phase2a.GameplayTests.args
    quiz = phase2a.GameplayTests.quiz
    content = phase2a.GameplayTests.content
    question = phase2a.GameplayTests.question
    mutate = phase2a.GameplayTests.mutate
    launch = phase2a.GameplayTests.launch
    url = phase2a.GameplayTests.url
    command = phase2a.GameplayTests.command
    lobby = phase2a.GameplayTests.lobby
    player = phase2a.GameplayTests.player
    open_question = phase2a.GameplayTests.open_question
    answer = phase2a.GameplayTests.answer

    def setUp(self):
        caches["quitizz"].clear()
        self.enable()
        self.client.force_login(self.user)
        quiz = self.quiz()
        self.question(quiz)
        self.question(quiz, prompt="Second")
        self.session = self.launch(quiz)
        self.lobby()
        self.participant, self.credential = self.player()
        self.client.get(self.url("host"))  # writes the same HTTP-selected scope

    @database_sync_to_async
    def committed(self, function, *args, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return function(*args, **kwargs)

    def socket(self, role="player", credential=None, session=None, origin="http://testserver", cookie=None):
        from config.asgi import application
        public_id = (session or self.session).public_id
        if cookie is None:
            cookie = (f"{settings.SESSION_COOKIE_NAME}={self.client.cookies[settings.SESSION_COOKIE_NAME].value}"
                      if role == "host" else f"{cookie_name(public_id, 'socket')}={credential or self.credential}")
        return WebsocketCommunicator(application, f"/ws/quitizz/{public_id}/{role}/", headers=[
            (b"host", b"testserver"), (b"origin", origin.encode()), (b"cookie", cookie.encode())])

    async def connected(self, socket):
        self.assertTrue((await socket.connect(timeout=20))[0])
        self.assertTrue(await socket.receive_nothing(timeout=.01))  # no ready/data
        return socket

    @database_sync_to_async
    def http_state(self, role, *, credential=None, session=None):
        from django.test import Client
        browser = self.client if role == "host" else Client()
        session = session or self.session
        if role != "host":
            browser.cookies[cookie_name(session.public_id)] = credential or self.credential
        return browser.get(self.url("host_state" if role == "host" else "state", session=session))

    async def assert_http_denied(self, role):
        response = await self.http_state(role)
        self.assertIn(response.status_code, {302, 403, 404} if role == "host" else {404})
        if role == "player":
            self.assertEqual(set(response.json()), {"error"})
        for protected in [b'"participants"', b'"answered_count"', b'"question"', b'"feedback"']:
            self.assertNotIn(protected, response.content)

    async def test_same_origin_and_authenticated_host_and_player(self):
        for role in ["host", "player"]:
            socket = await self.connected(self.socket(role))
            await socket.send_to(text_data="ping")
            self.assertEqual(await socket.receive_json_from(), {"event": "pong"})
            await socket.disconnect()

    async def admission_race(self, role, revoke, *, registered=False, lose_notification=False):
        layer = get_channel_layer()
        original_add = layer.group_add
        registrations = []
        triggered = False

        async def add(name, channel):
            nonlocal triggered
            registrations.append((name, channel))
            target = realtime.group(self.session.pk, "host" if role == "host" else "players")
            if not triggered and (not registered or name == target):
                triggered = True
                if registered:
                    await original_add(name, channel)
                if lose_notification:
                    with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                        await self.committed(revoke)
                else:
                    await self.committed(revoke)
                if registered:
                    return
            await original_add(name, channel)

        socket = self.socket(role)
        with patch.object(layer, "group_add", side_effect=add):
            accepted, code = await socket.connect(timeout=20)
            self.assertFalse(accepted)
            self.assertEqual(code, 4403)
        self.assertTrue(triggered)
        self.assertTrue(registrations)
        await self.assert_http_denied(role)
        # Cleanup must happen on rejection, before a client disconnect arrives.
        for name, channel in registrations:
            self.assertNotIn(channel, layer.groups.get(name, {}))
        await socket.send_to(text_data="ping")
        await layer.group_send(realtime.group(self.session.pk, "host" if role == "host" else "players"),
                               {"type": "quitizz.event", "event": "session_started", "version": 100})
        self.assertTrue(await socket.receive_nothing())  # no ready, pong or gameplay
        await socket.disconnect()

    async def test_removal_before_first_subscription_rejects_and_cleans_admission(self):
        await self.admission_race("player", lambda: self.command("remove", participant_id=self.participant.public_id))

    async def test_feature_off_before_first_subscription_rejects_host_and_heartbeat(self):
        await self.admission_race("host", lambda: self.enable(False))

    async def test_removal_after_registration_rechecked_even_if_notification_lost(self):
        await self.admission_race("player", lambda: self.command("remove", participant_id=self.participant.public_id),
                                  registered=True, lose_notification=True)

    async def test_feature_off_after_registration_rechecked_even_if_notification_lost(self):
        await self.admission_race("host", lambda: self.enable(False), registered=True, lose_notification=True)

    async def test_revocation_during_accept_cancels_admission_without_ready(self):
        for role, consumer, revoke in [
            ("player", PlayerConsumer, lambda: self.command("remove", participant_id=self.participant.public_id)),
            ("host", HostConsumer, lambda: self.enable(False)),
        ]:
            layer = get_channel_layer()
            instances = []
            async def delayed_accept(instance, *args, **kwargs):
                instances.append(instance)
                await self.committed(revoke)
                # Revocation must be handled while connect is still pending.
                await asyncio.Event().wait()
            with patch.object(consumer, "accept", delayed_accept):
                socket = self.socket(role)
                self.assertFalse((await socket.connect(timeout=20))[0])
                self.assertEqual(instances[0].subscription_groups, [])
                self.assertFalse(instances[0].admitted)
                self.assertTrue(instances[0].revoked)
                self.assertTrue(await socket.receive_nothing())
                await socket.disconnect()
            self.assertFalse(any(instances[0].channel_name in members for members in layer.groups.values()))

    async def provisional_accept_race(self, role, revoke, *, before_accept=False):
        consumer = HostConsumer if role == "host" else PlayerConsumer
        original_accept = consumer.accept
        instances = []

        async def accept(instance, *args, **kwargs):
            instances.append(instance)
            if not before_accept:
                await original_accept(instance, *args, **kwargs)
            # Revoke after the last pre-accept check, on either side of the
            # actual transport accept. Lose notification to test DB authority.
            with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                await self.committed(revoke)
            if before_accept:
                await original_accept(instance, *args, **kwargs)

        socket = self.socket(role)
        with patch.object(consumer, "accept", accept):
            self.assertTrue((await socket.connect(timeout=20))[0])
            self.assertTrue(await socket.receive_nothing())
        instance = instances[0]
        self.assertTrue(instance.admitted)  # harmless admission TOCTOU permitted
        self.assertFalse(instance.revoked)
        self.assertTrue(instance.admission_task.done())
        layer = get_channel_layer()
        await layer.send(instance.channel_name, {"type": "quitizz.event", "event": "question_opened", "prompt": "SECRET", "answered_count": 99})
        self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
        await self.assert_http_denied(role)
        await socket.disconnect()
        self.assertTrue(instance.shutdown_task.done())
        self.assertFalse(any(instance.channel_name in members for members in layer.groups.values()))

    async def test_removal_during_transport_accept_never_emits_ready_or_gameplay(self):
        for before_accept in [True, False]:
            await self.provisional_accept_race(
                "player", lambda: self.command("remove", participant_id=self.participant.public_id),
                before_accept=before_accept)
            await self.committed(QuiTizzParticipant.objects.filter(pk=self.participant.pk).update, removed_at=None)

    async def test_feature_off_during_transport_accept_never_emits_ready_or_gameplay(self):
        for role in ["host", "player"]:
            for before_accept in [True, False]:
                await self.provisional_accept_race(role, lambda: self.enable(False), before_accept=before_accept)
                await self.committed(self.enable)

    async def test_direct_deny_during_transport_accept_never_emits_ready_or_gameplay(self):
        for before_accept in [True, False]:
            await self.provisional_accept_race(
                "host", lambda: self.grant(self.user, "quitizz.host", grant_type="DENY"), before_accept=before_accept)
            await self.committed(UserPermission.objects.filter(user=self.user, grant_type="DENY").delete)

    async def test_direct_deny_allows_only_wakeup_and_http_denies_without_client_heartbeat(self):
        socket = await self.connected(self.socket("host"))
        await self.committed(self.grant, self.user, "quitizz.host", grant_type="DENY")
        layer = get_channel_layer()
        for name in ["session_started", "question_opened", "answer_received"]:
            await layer.group_send(realtime.group(self.session.pk, "host"),
                                   {"type": "quitizz.event", "event": name, "version": 100})
        for _ in range(3):
            self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
        await self.assert_http_denied("host")
        self.assertTrue(await socket.receive_nothing())
        await socket.disconnect()

    async def test_idle_host_direct_deny_is_rechecked_by_server_without_ping(self):
        # Accelerate only the cadence, retaining the real owned timer/check.
        with patch.object(HostConsumer, "host_reauthorization_seconds", .05):
            socket = await self.connected(self.socket("host"))
            await self.committed(self.grant, self.user, "quitizz.host", grant_type="DENY")
            self.assertEqual(await socket.receive_json_from(timeout=5), {"event": "participant_unavailable"})
            self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 4403})
            await socket.disconnect()

    async def test_outbound_with_lost_removal_and_feature_notifications_is_constant_http_denies(self):
        for role, revoke in [
            ("player", lambda: self.command("remove", participant_id=self.participant.public_id)),
            ("host", lambda: self.enable(False)),
        ]:
            socket = await self.connected(self.socket(role))
            with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                await self.committed(revoke)
            layer = get_channel_layer()
            target = realtime.group(self.session.pk, "host" if role == "host" else "players")
            await layer.group_send(target, {"type": "quitizz.event", "event": "session_started", "version": 100})
            await layer.group_send(target, {"type": "quitizz.event", "event": "question_opened", "version": 101})
            for _ in range(2):
                self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
            await self.assert_http_denied(role)
            self.assertTrue(await socket.receive_nothing())
            await socket.disconnect()

    async def outbound_send_race(self, role, revoke):
        consumer = HostConsumer if role == "host" else PlayerConsumer
        socket = await self.connected(self.socket(role))
        original_send = consumer.send_json
        triggered = False
        async def send(instance, payload, *args, **kwargs):
            nonlocal triggered
            self.assertEqual(payload, {"event": "sync_required"})
            if not triggered:
                triggered = True
                # Revoke exactly after outbound preparation and before delivery.
                # Lost revocation notifications cannot affect confidentiality.
                with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                    await self.committed(revoke)
            await original_send(instance, payload, *args, **kwargs)
        with patch.object(consumer, "send_json", send), \
             patch.object(consumer, "authorize", side_effect=AssertionError("Outbound must not query authorization")):
            await get_channel_layer().group_send(
                realtime.group(self.session.pk, "host" if role == "host" else "players"),
                {"type": "quitizz.event", "event": "answer_revealed", "prompt": "SECRET",
                 "choices": {"A": "SECRET"}, "correct_choice": "A", "score": 99, "rank": 1,
                 "participants": ["SECRET"], "nickname": "SECRET", "version": 999,
                 "position": 1, "deadline": "SECRET", "answered_count": 100})
            self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
        self.assertTrue(triggered)
        await self.assert_http_denied(role)
        await socket.disconnect()

    async def test_direct_deny_during_outbound_send_is_safe_without_authorization_recheck(self):
        await self.outbound_send_race("host", lambda: self.grant(self.user, "quitizz.host", grant_type="DENY"))

    async def test_removal_during_outbound_send_is_safe_without_authorization_recheck(self):
        await self.outbound_send_race("player", lambda: self.command("remove", participant_id=self.participant.public_id))

    async def test_feature_off_during_outbound_send_is_safe_for_host_and_player(self):
        for role in ["host", "player"]:
            await self.outbound_send_race(role, lambda: self.enable(False))
            await self.committed(self.enable)

    async def test_expiry_during_outbound_send_is_safe_even_if_scheduled_close_is_delayed(self):
        await self.outbound_send_race("player", lambda: QuiTizzParticipant.objects.filter(pk=self.participant.pk).update(
            reconnect_expires_at=timezone.now() - timedelta(seconds=1)))

    async def test_session_expiry_during_outbound_send_denies_host_and_player_http(self):
        for role in ["host", "player"]:
            await self.outbound_send_race(role, lambda: game.write(self.session,
                expires_at=timezone.now() - timedelta(seconds=1)))
            await self.committed(game.write, self.session, expires_at=timezone.now() + timedelta(hours=1))

    async def test_permission_revoked_during_outbound_send_http_denies(self):
        await self.outbound_send_race("host", lambda: UserPermission.objects.filter(
            user=self.user, permission__code="quitizz.host").delete())

    async def final_admission_read_race(self, role, revoke):
        consumer = HostConsumer if role == "host" else PlayerConsumer
        original = Subscription.__dict__["authorize"]
        triggered = False
        async def authorize(instance, *, initial=True):
            nonlocal triggered
            result = await original.__get__(instance, consumer)(initial=initial)
            if not initial and not triggered:
                triggered = True
                with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                    await self.committed(revoke)
            return result
        with patch.object(consumer, "authorize", authorize):
            socket = await self.connected(self.socket(role))
        self.assertTrue(triggered)
        await get_channel_layer().group_send(realtime.group(self.session.pk, "host" if role == "host" else "players"),
                                            {"type": "quitizz.event", "event": "sync_required"})
        self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
        await self.assert_http_denied(role)
        await socket.disconnect()

    async def test_host_deny_during_final_admission_read_is_notification_only(self):
        await self.final_admission_read_race("host", lambda: self.grant(self.user, "quitizz.host", grant_type="DENY"))

    async def test_player_removal_during_final_admission_read_is_notification_only(self):
        await self.final_admission_read_race("player", lambda: self.command("remove", participant_id=self.participant.public_id))

    async def test_feature_off_during_final_admission_read_is_notification_only(self):
        for role in ["host", "player"]:
            await self.final_admission_read_race(role, lambda: self.enable(False))
            await self.committed(self.enable)

    async def test_player_expiry_during_final_admission_read_is_notification_only(self):
        await self.final_admission_read_race("player", lambda: QuiTizzParticipant.objects.filter(pk=self.participant.pk).update(
            reconnect_expires_at=timezone.now() - timedelta(seconds=1)))

    async def expiry_registration_race(self, *, scheduled=False, disconnect=False):
        instances = []
        original_accept = PlayerConsumer.accept
        async def accept(instance, *args, **kwargs):
            instances.append(instance)
            await original_accept(instance, *args, **kwargs)
        if scheduled:
            await self.committed(QuiTizzParticipant.objects.filter(pk=self.participant.pk).update,
                                 reconnect_expires_at=timezone.now() + timedelta(seconds=1))
        with patch.object(PlayerConsumer, "accept", accept):
            socket = await self.connected(self.socket())
        instance = instances[0]
        layer = get_channel_layer()
        original_add = layer.group_add
        pending, resume = asyncio.Event(), asyncio.Event()
        target = realtime.group(self.session.pk, "players")
        await layer.group_discard(target, instance.channel_name)
        completed_adds = []
        async def delayed_add(name, channel):
            if name == target:
                pending.set()
                await resume.wait()
            await original_add(name, channel)
            completed_adds.append((name, instance.revoked))
        with patch.object(layer, "group_add", side_effect=delayed_add):
            await socket.send_to(text_data="ping")
            await asyncio.wait_for(pending.wait(), 5)
            if disconnect:
                termination = asyncio.create_task(instance.disconnect(1000))
            elif not scheduled:
                termination = asyncio.create_task(instance.expire(0))
            else:
                termination = None  # actual scheduled expiry owns revocation
            async def revoked():
                while not instance.revoked:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(revoked(), 5)
            # Cleanup cannot drop ownership while group_add is still pending.
            self.assertIn(target, instance.subscription_groups)
            self.assertTrue(instance.registration_lock.locked())
            resume.set()
            if termination:
                await asyncio.wait_for(termination, 5)
            await asyncio.wait_for(asyncio.shield(instance.shutdown_task), 5)
        outputs = []
        while not await socket.receive_nothing():
            outputs.append(await socket.receive_output())
        self.assertEqual(sum(item["type"] == "websocket.close" for item in outputs), 1)
        self.assertFalse(any(item["type"] == "websocket.send" and json.loads(item["text"])["event"] == "pong"
                             for item in outputs))
        self.assertIn((target, True), completed_adds)
        self.assertFalse(instance.subscription_groups)
        self.assertFalse(any(instance.channel_name in members for members in layer.groups.values()))
        await socket.disconnect()
        self.assertFalse(any(instance.channel_name in members for members in layer.groups.values()))
        self.assertTrue(all(task is None or task.done() for task in
                            [instance.admission_task, instance.expiry_task, instance.reauthorization_task, instance.shutdown_task]))

    async def test_expiry_cleanup_serializes_heartbeat_late_group_add(self):
        await self.expiry_registration_race()

    async def test_scheduled_expiry_serializes_heartbeat_late_group_add(self):
        await self.expiry_registration_race(scheduled=True)

    async def test_disconnect_serializes_heartbeat_late_group_add(self):
        await self.expiry_registration_race(disconnect=True)

    async def test_host_recheck_reloads_user_permission_and_selected_scope(self):
        # A selectable alternate scope must be role-accessible. Otherwise the
        # normal HTTP scope middleware intentionally restores the default scope.
        await self.committed(UserRole.objects.create, user=self.user, role=self.role,
                             tenant=self.tenant, campus=self.other_campus)
        def change_scope():
            browser = self.client.session
            browser[ScopeService.SESSION_CAMPUS_KEY] = self.other_campus.pk
            browser.save()
        for revoke, restore in [
            (lambda: type(self.user).objects.filter(pk=self.user.pk).update(is_active=False),
             lambda: type(self.user).objects.filter(pk=self.user.pk).update(is_active=True)),
            (lambda: self.grant(self.user, "quitizz.host", grant_type="DENY"),
             lambda: UserPermission.objects.filter(user=self.user, grant_type="DENY").delete()),
            (change_scope, None),
        ]:
            await self.admission_race("host", revoke, registered=True, lose_notification=True)
            if restore:
                await self.committed(restore)
                await self.committed(self.client.force_login, self.user)
                await self.committed(self.client.get, self.url("host"))

    async def test_failed_registration_cleans_even_partially_applied_group_add(self):
        layer = get_channel_layer()
        original = layer.group_add
        registrations = []
        async def broken_add(name, channel):
            await original(name, channel)
            registrations.append((name, channel))
            raise RuntimeError("transport failure")
        socket = self.socket()
        with patch.object(layer, "group_add", side_effect=broken_add):
            self.assertFalse((await socket.connect())[0])
        for name, channel in registrations:
            self.assertNotIn(channel, layer.groups.get(name, {}))
        await socket.disconnect()

    async def test_heartbeat_rejects_revocation_when_notification_and_membership_lost(self):
        for role, revoke in [
            ("player", lambda: self.command("remove", participant_id=self.participant.public_id)),
            ("host", lambda: self.enable(False)),
        ]:
            socket = await self.connected(self.socket(role))
            layer = get_channel_layer()
            layer.groups.clear()  # Redis restart/lost memberships
            with patch("apps.quitizz.signals.send"), patch("apps.quitizz.realtime.send"):
                await self.committed(revoke)
            await socket.send_to(text_data="ping")
            self.assertEqual((await socket.receive_json_from())["event"], "participant_unavailable")
            self.assertEqual((await socket.receive_output())["type"], "websocket.close")
            self.assertFalse(layer.groups)
            await socket.send_to(text_data="ping")
            self.assertTrue(await socket.receive_nothing())
            await socket.disconnect()

    async def test_hostile_missing_malformed_and_wrong_port_origins_rejected(self):
        for origin in ["https://evil.example", "null", "http://testserver:8080", "http://u@testserver", "http://testserver/path"]:
            socket = self.socket(origin=origin)
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()
        socket = self.socket()
        socket.scope["headers"] = [(b"host", b"testserver")]
        self.assertFalse((await socket.connect())[0])
        await socket.disconnect()

    async def test_host_unauthenticated_and_nonowner_rejected(self):
        for cookie in ["", "sessionid=invalid"]:
            socket = self.socket("host", cookie=cookie)
            self.assertFalse((await socket.connect())[0]); await socket.disconnect()
        await self.committed(self.client.force_login, self.other_user)
        socket = self.socket("host")
        self.assertFalse((await socket.connect())[0]); await socket.disconnect()

    async def test_host_wrong_tenant_campus_and_direct_deny_rejected(self):
        for tenant, campus in [(self.other_tenant, self.foreign_campus), (self.tenant, self.other_campus)]:
            await self.committed(UserRole.objects.create, user=self.user, role=self.role, tenant=tenant, campus=campus)
            await self.committed(self.enable, tenant=tenant)
            await self.committed(self.grant, self.user, "quitizz.host", tenant=tenant, campus=campus)
        def scope(tenant, campus):
            session = self.client.session
            session[ScopeService.SESSION_TENANT_KEY] = tenant
            session[ScopeService.SESSION_CAMPUS_KEY] = campus
            session.save()
        for tenant, campus in [(self.other_tenant.pk, self.foreign_campus.pk), (self.tenant.pk, self.other_campus.pk)]:
            await self.committed(scope, tenant, campus)
            socket = self.socket("host")
            self.assertFalse((await socket.connect())[0]); await socket.disconnect()
            await self.assert_http_denied("host")
        await self.committed(scope, self.tenant.pk, self.campus.pk)
        await self.committed(self.grant, self.user, "quitizz.host", grant_type="DENY")
        socket = self.socket("host")
        self.assertFalse((await socket.connect())[0]); await socket.disconnect()

    async def test_invalid_cross_session_removed_expired_credentials_rejected(self):
        # Different session must have a real launched question, but shares scope.
        def launch_other():
            quiz = self.quiz("Other"); self.question(quiz); return self.launch(quiz)
        other = await self.committed(launch_other)
        for credential, session in [("invalid", self.session), (self.credential, other)]:
            socket = self.socket(credential=credential, session=session)
            self.assertFalse((await socket.connect())[0]); await socket.disconnect()
            self.assertEqual((await self.http_state("player", credential=credential, session=session)).status_code, 404)
        await self.committed(QuiTizzParticipant.objects.filter(pk=self.participant.pk).update,
                             reconnect_expires_at=timezone.now() - timedelta(seconds=1))
        socket = self.socket(); self.assertFalse((await socket.connect())[0]); await socket.disconnect()
        await self.assert_http_denied("player")
        await self.committed(QuiTizzParticipant.objects.filter(pk=self.participant.pk).update,
                             reconnect_expires_at=timezone.now() + timedelta(hours=1), removed_at=timezone.now())
        socket = self.socket(); self.assertFalse((await socket.connect())[0]); await socket.disconnect()
        await self.assert_http_denied("player")

    async def test_removal_reaches_host_revokes_active_player_and_reconnect(self):
        host = await self.connected(self.socket("host")); player = await self.connected(self.socket())
        await self.committed(self.command, "remove", participant_id=self.participant.public_id)
        self.assertEqual(await host.receive_json_from(), {"event": "sync_required"})
        self.assertEqual(await player.receive_json_from(), {"event": "participant_unavailable"})
        self.assertEqual((await player.receive_output())["type"], "websocket.close")
        retry = self.socket(); self.assertFalse((await retry.connect())[0]); await retry.disconnect()
        await host.disconnect(); await player.disconnect()

    async def test_feature_off_revokes_connections_rejects_new_and_blocks_broadcasts(self):
        host = await self.connected(self.socket("host")); player = await self.connected(self.socket())
        await self.committed(self.enable, False)
        for socket in [host, player]:
            self.assertEqual(await socket.receive_json_from(), {"event": "feature_unavailable"})
            self.assertEqual((await socket.receive_output())["type"], "websocket.close")
            await socket.disconnect()
        for role in ["host", "player"]:
            retry = self.socket(role); self.assertFalse((await retry.connect())[0]); await retry.disconnect()
        with patch("apps.quitizz.realtime.send") as send:
            await self.committed(realtime.notify, self.session, "session_started")
            send.assert_not_called()

    async def test_lifecycle_deadline_reveal_completion_privacy_and_recovery(self):
        host = await self.connected(self.socket("host")); player = await self.connected(self.socket())
        for action, name in [("start", "session_started"), ("open_question", "question_opened"),
                             ("close_question", "question_closed"), ("reveal", "answer_revealed"),
                             ("next", "question_prepared"), ("open_question", "question_opened"),
                             ("close_question", "question_closed"), ("reveal", "answer_revealed"),
                             ("complete", "session_completed")]:
            await self.committed(self.command, action)
            a, b = await host.receive_json_from(), await player.receive_json_from()
            self.assertEqual(a, {"event": "sync_required"}); self.assertEqual(a, b)
            self.assertFalse(set(b) & {"correct_choice", "is_correct", "points", "score", "rank", "nickname", "participants"})
            state = await self.committed(game.state, self.session.public_id, self.credential)
            response = await self.http_state("player")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], state["status"])
            self.assertEqual((await self.http_state("host")).status_code, 200)
            if action == "open_question":
                self.assertIn("deadline", state["question"])
                self.assertNotIn("feedback", state)
            if action == "reveal":
                self.assertEqual(state["feedback"]["correct_choice"], "B")
        await player.disconnect()
        retry = await self.connected(self.socket())
        state = await self.committed(game.state, self.session.public_id, self.credential)
        self.assertEqual(state["status"], "COMPLETED"); self.assertIn("summary", state)
        await retry.disconnect(); await host.disconnect()

    async def test_join_answer_count_idempotence_and_host_only_events(self):
        host = await self.connected(self.socket("host")); player = await self.connected(self.socket())
        await self.committed(self.player, "Private nickname")
        event = await host.receive_json_from()
        self.assertEqual(event, {"event": "sync_required"})
        self.assertNotIn("nickname", event); self.assertTrue(await player.receive_nothing())
        await self.committed(self.command, "start")
        await host.receive_json_from(); await player.receive_json_from()
        await self.committed(self.command, "open_question")
        await host.receive_json_from(); await player.receive_json_from()
        question = await self.committed(self.session.questions.get, position=1)
        await self.committed(self.answer, self.credential, question)
        event = await host.receive_json_from()
        self.assertEqual(event, {"event": "sync_required"})
        self.assertFalse(set(event) & {"selected_choice", "is_correct", "points", "nickname", "participant"})
        self.assertTrue(await player.receive_nothing())
        await self.committed(self.answer, self.credential, question)
        self.assertTrue(await host.receive_nothing())
        state = await self.committed(game.host_state, self.session)
        self.assertEqual(state["answered_count"], 1)
        await host.disconnect(); await player.disconnect()

    async def test_no_websocket_mutations(self):
        socket = await self.connected(self.socket())
        await socket.send_json_to({"action": "answer", "choice": "B", "score": 1000})
        self.assertEqual((await socket.receive_output())["type"], "websocket.close")
        self.assertEqual(await self.committed(QuiTizzResponse.objects.count), 0)
        await self.committed(self.session.refresh_from_db)
        self.assertEqual(self.session.status, "LOBBY")
        await socket.disconnect()

    async def test_joining_open_close_and_cancel_are_explicit_and_safe(self):
        host = await self.connected(self.socket("host")); player = await self.connected(self.socket())
        for action, expected in [("close_joining", "joining_closed"), ("open_joining", "joining_opened")]:
            await self.committed(self.command, action)
            self.assertEqual(await host.receive_json_from(), {"event": "sync_required"})
            self.assertTrue(await player.receive_nothing())
        await self.committed(self.command, "cancel")
        self.assertEqual(await host.receive_json_from(), {"event": "sync_required"})
        self.assertEqual(await player.receive_json_from(), {"event": "sync_required"})
        await host.disconnect(); await player.disconnect()

    async def test_active_socket_expires_without_server_tick_loop(self):
        await self.committed(QuiTizzParticipant.objects.filter(pk=self.participant.pk).update,
                             reconnect_expires_at=timezone.now() + timedelta(seconds=1))
        socket = await self.connected(self.socket())
        self.assertEqual(await socket.receive_json_from(timeout=3), {"event": "participant_unavailable"})
        self.assertEqual((await socket.receive_output())["type"], "websocket.close")
        await self.assert_http_denied("player")
        await socket.disconnect()

    def test_broad_direct_deny_and_session_expiry_reject_authorization(self):
        from apps.rbac.models import Permission
        UserPermission.objects.create(user=self.user, permission=Permission.objects.get(code="quitizz.host"),
                                      grant_type="DENY", tenant=None, campus=None)
        host = HostConsumer()
        browser = self.client.session
        host.scope = {"url_route": {"kwargs": {"public_id": self.session.public_id}}, "user": self.user, "session": browser}
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied): async_to_sync(host.authorize)()
        game.write(self.session, expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(game.Unavailable): async_to_sync(host.authorize)()

    def test_no_event_before_commit_rollback_and_channel_loss_preserve_db(self):
        with patch("apps.quitizz.realtime.send") as send:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    self.command("start")
                    send.assert_not_called()
            self.assertEqual(send.call_count, 2)
        with patch("apps.quitizz.realtime.send") as send:
            try:
                with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
                    self.command("open_question"); raise RuntimeError("rollback")
            except RuntimeError:
                pass
            send.assert_not_called()
        with patch("apps.quitizz.realtime.get_channel_layer", side_effect=RuntimeError("transport down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.command("open_question")
                question = self.session.questions.get(position=1)
                self.answer(self.credential, question)
        state = game.state(self.session.public_id, self.credential)
        self.assertTrue(state["accepted"]); self.assertNotIn("feedback", state)
        self.assertEqual(QuiTizzResponse.objects.count(), 1)

    def test_cookie_bridge_csrf_scoping_and_revoked_access(self):
        from django.test import Client
        visitor = Client(enforce_csrf_checks=True)
        visitor.cookies[cookie_name(self.session.public_id)] = self.credential
        visitor.get(self.url("play"))
        self.assertEqual(visitor.post(self.url("socket_identity"), "connect=1", content_type="application/x-www-form-urlencoded").status_code, 403)
        response = visitor.post(self.url("socket_identity"), "connect=1", content_type="application/x-www-form-urlencoded",
                                HTTP_X_CSRFTOKEN=visitor.cookies[settings.CSRF_COOKIE_NAME].value)
        cookie = response.cookies[cookie_name(self.session.public_id, "socket")]
        self.assertEqual(cookie["path"], f"/ws/quitizz/{self.session.public_id}/player/")
        self.assertTrue(cookie["httponly"]); self.assertEqual(cookie["samesite"], "Strict")
        self.assertNotIn(self.credential, response.content.decode())
        self.command("remove", participant_id=self.participant.public_id)
        self.assertEqual(visitor.post(self.url("socket_identity"), "connect=1", content_type="application/x-www-form-urlencoded",
                                HTTP_X_CSRFTOKEN=visitor.cookies[settings.CSRF_COOKIE_NAME].value).status_code, 404)

    def test_bounded_connect_recovery_and_answer_queries_1_20_100(self):
        credentials = [self.credential]
        counts, heartbeats, forwarding = {}, {}, {}
        for size in [1, 20, 100]:
            for i in range(len(credentials), size): credentials.append(self.player(f"P{i}")[1])
            result = []
            async def handshake(role, operation=None):
                socket = await self.connected(self.socket(role))
                if operation == "heartbeat":
                    await socket.send_to(text_data="ping")
                    self.assertEqual(await socket.receive_json_from(), {"event": "pong"})
                elif operation == "forward":
                    await get_channel_layer().group_send(
                        realtime.group(self.session.pk, "host" if role == "host" else "players"),
                        {"type": "quitizz.event", "event": "question_closed", "version": 3})
                    self.assertEqual(await socket.receive_json_from(), {"event": "sync_required"})
                await socket.disconnect()
            for role in ["host", "player"]:
                with CaptureQueriesContext(connection) as queries:
                    async_to_sync(handshake)(role)
                result.append(len(queries))
            for operation, measurements in [("heartbeat", heartbeats), ("forward", forwarding)]:
                measurements[size] = []
                for index, role in enumerate(["host", "player"]):
                    with CaptureQueriesContext(connection) as queries:
                        async_to_sync(handshake)(role, operation)
                    measurements[size].append(len(queries) - result[index])
            with CaptureQueriesContext(connection) as queries: game.state(self.session.public_id, self.credential)
            result.append(len(queries))
            with CaptureQueriesContext(connection) as queries: game.host_state(self.session)
            result.append(len(queries))
            from django.test import Client
            browser = Client(); browser.cookies[cookie_name(self.session.public_id)] = self.credential
            for client, name in [(self.client, "host_state"), (browser, "state")]:
                with CaptureQueriesContext(connection) as queries:
                    response = client.get(self.url(name))
                self.assertEqual(response.status_code, 200)
                result.append(len(queries))
            counts[size] = result
        self.assertEqual(len({tuple(value) for value in counts.values()}), 1)
        self.assertLessEqual(counts[100][0], 48); self.assertLessEqual(counts[100][1], 9)
        for measurements in [heartbeats, forwarding]:
            self.assertEqual(len({tuple(value) for value in measurements.values()}), 1)
            self.assertLessEqual(measurements[100][0], 16)
            self.assertLessEqual(measurements[100][1], 3)
        self.assertEqual(forwarding, {size: [0, 0] for size in [1, 20, 100]})
        question = self.open_question()
        answers = {}
        with patch("apps.quitizz.realtime.send") as send:
            for index, credential in enumerate(credentials, 1):
                with CaptureQueriesContext(connection) as queries, self.captureOnCommitCallbacks(execute=True):
                    self.answer(credential, question, received_at=question.opened_at)
                if index in {1, 20, 100}: answers[index] = len(queries)
            self.assertEqual(send.call_count, 100)
        self.assertEqual(len(set(answers.values())), 1)
        print(f"QUITIZZ_REALTIME_QUERIES host_connect/player_connect/player_service/host_service/host_HTTP/player_HTTP={counts}; "
              f"host/player_heartbeat={heartbeats}; host/player_forward={forwarding}; answers_with_commit={answers}")

    def test_open_question_canonical_http_queries_do_not_grow_with_population(self):
        from django.test import Client
        self.open_question(); self.command("open_joining")
        count = 1
        measurements = {}
        browser = Client(); browser.cookies[cookie_name(self.session.public_id)] = self.credential
        for size in [1, 20, 100]:
            while count < size:
                self.player(f"OpenLoad{count}"); count += 1
            measurements[size] = []
            for client, name in [(self.client, "host_state"), (browser, "state")]:
                with CaptureQueriesContext(connection) as queries:
                    response = client.get(self.url(name))
                self.assertEqual(response.status_code, 200)
                measurements[size].append(len(queries))
        self.assertEqual(len({tuple(value) for value in measurements.values()}), 1)
        print(f"QUITIZZ_OPEN_QUESTION_HTTP_QUERIES host/player={measurements}")

    async def test_100_connected_clients_receive_one_shared_safe_event(self):
        def players():
            return [self.credential] + [self.player(f"Load{i}")[1] for i in range(99)]
        credentials = await self.committed(players)
        sockets = [self.socket(credential=value) for value in credentials]
        await asyncio.gather(*(self.connected(socket) for socket in sockets))
        await self.committed(self.command, "start")
        events = await asyncio.gather(*(socket.receive_json_from(timeout=5) for socket in sockets))
        self.assertTrue(all(event == events[0] for event in events))
        self.assertEqual(events[0], {"event": "sync_required"})
        # One authorized HTTP recovery per browser; no per-player broadcaster read.
        @database_sync_to_async
        def recover_all():
            results = []
            for credential in credentials:
                from django.test import Client
                browser = Client(); browser.cookies[cookie_name(self.session.public_id)] = credential
                with CaptureQueriesContext(connection) as queries:
                    response = browser.get(self.url("state"))
                self.assertEqual(response.status_code, 200)
                results.append(len(queries))
            return results
        queries = await recover_all()
        self.assertEqual(len(set(queries)), 1)
        await asyncio.gather(*(socket.disconnect() for socket in sockets))
        self.assertFalse(get_channel_layer().groups)
        print("QUITIZZ_WEBSOCKET_CLIENTS 100 authorized sockets; 100 identical shared invalidations; clean disconnect")
        print(f"QUITIZZ_100_LIFECYCLE_HTTP_RECOVERY requests=100 per_request_queries={queries[0]} total_queries={sum(queries)}")

    async def test_independent_session_groups_do_not_cross(self):
        def other_session():
            quiz = self.quiz("Independent"); self.question(quiz)
            session = self.launch(quiz)
            game.command(**self.args(), public_id=session.public_id, version=1, action="open_joining")
            session.refresh_from_db()
            _, credential = game.join(session.public_id, game.exchange(session.public_id, game.capability(session)), "Other")
            return session, credential
        other, credential = await self.committed(other_session)
        first = await self.connected(self.socket())
        second = await self.connected(self.socket(credential=credential, session=other))
        await self.committed(self.command, "start")
        self.assertEqual(await first.receive_json_from(), {"event": "sync_required"})
        self.assertTrue(await second.receive_nothing())
        await first.disconnect(); await second.disconnect()


class RealtimeConfigurationTests(SimpleTestCase):
    async def test_forwarder_filters_all_stale_internal_data_without_database_access(self):
        consumer = PlayerConsumer()
        consumer.admitted = True; consumer.revoked = False; consumer.output_lock = asyncio.Lock()
        consumer.authorize = AsyncMock(side_effect=AssertionError("No outbound authorization query"))
        consumer.send_json = AsyncMock()
        await consumer.quitizz_event({"type": "quitizz.event", "event": "answer_revealed",
            "correct_choice": "B", "prompt": "private", "version": 55, "answered_count": 100})
        consumer.send_json.assert_awaited_once_with({"event": "sync_required"})
        consumer.authorize.assert_not_called()

    def test_every_producer_serializes_only_constant_wakeups_to_appropriate_groups(self):
        from types import SimpleNamespace
        session = SimpleNamespace(pk=1, tenant_id=2)
        with patch("apps.quitizz.realtime.transaction.on_commit", side_effect=lambda callback: callback()), \
             patch("apps.quitizz.realtime.FeatureSettingsService.is_quitizz_enabled", return_value=True), \
             patch("apps.quitizz.realtime.send") as send:
            for name in realtime.HOST_EVENTS | realtime.PLAYER_EVENTS:
                send.reset_mock(); realtime.notify(session, name)
                expected = [realtime.group(1, "host")]
                if name in realtime.PLAYER_EVENTS:
                    expected.append(realtime.group(1, "players"))
                self.assertEqual([call.args[0] for call in send.call_args_list], expected)
                self.assertTrue(all(call.args[1] == {"type": "quitizz.event", "event": "sync_required"}
                                    for call in send.call_args_list))
    @override_settings(SESSION_COOKIE_SECURE=True, ALLOWED_HOSTS=["testserver"])
    async def test_https_origin_through_plain_tls_terminated_upstream(self):
        application = AsyncMock()
        validator = SameOriginValidator(application)
        scope = {"scheme": "ws", "headers": [(b"host", b"testserver"), (b"origin", b"https://testserver")]}
        send = AsyncMock(); receive = AsyncMock()
        await validator(scope, receive, send)
        application.assert_awaited_once_with(scope, receive, send)
        send.assert_not_called()

    @override_settings(SESSION_COOKIE_SECURE=True, ALLOWED_HOSTS=["testserver"])
    async def test_cross_scheme_origin_rejected_before_authentication(self):
        application = AsyncMock()
        validator = SameOriginValidator(application)
        send = AsyncMock()
        await validator({"scheme": "ws", "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver")]}, AsyncMock(), send)
        application.assert_not_called()
        send.assert_awaited_once_with({"type": "websocket.close", "code": 4403})

    def test_publisher_delivers_through_channel_layer_without_database(self):
        layer = InMemoryChannelLayer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)("qt.test", channel)
        event = {"type": "quitizz.event", "event": "session_started", "version": 2}
        with patch("apps.quitizz.realtime.get_channel_layer", return_value=layer):
            realtime.send("qt.test", event)
        self.assertEqual(async_to_sync(layer.receive)(channel), event)

    def test_hung_publisher_is_cancelled_and_failure_is_non_authoritative(self):
        cancelled = []
        class SlowLayer:
            async def group_send(self, name, event):
                try:
                    await asyncio.sleep(60)
                finally:
                    cancelled.append(True)
        real_wait_for = asyncio.wait_for
        async def fast_deadline(awaitable, timeout):
            self.assertEqual(timeout, 2)
            return await real_wait_for(awaitable, timeout=.01)
        with patch("apps.quitizz.realtime.get_channel_layer", return_value=SlowLayer()), \
             patch("apps.quitizz.realtime.asyncio.wait_for", side_effect=fast_deadline), \
             self.assertLogs("apps.quitizz.realtime", level="WARNING"):
            realtime.send("qt.test", {"type": "quitizz.event"})
        self.assertEqual(cancelled, [True])

    async def test_revoked_socket_discards_queued_notifications(self):
        consumer = PlayerConsumer()
        consumer.revoked = True
        consumer.send_json = AsyncMock()
        await consumer.quitizz_event({"type": "quitizz.event", "event": "question_opened", "version": 2})
        consumer.send_json.assert_not_called()

    @override_settings(DJANGO_ENV="production", QUITIZZ_REDIS_NAMESPACE="local")
    def test_production_locmem_warns(self):
        self.assertEqual({issue.id for issue in realtime_configuration(None)}, {"quitizz.W001", "quitizz.W002"})

    @override_settings(DJANGO_ENV="production", QUITIZZ_REDIS_NAMESPACE="school-production",
        CACHES={"quitizz": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}},
        CHANNEL_LAYERS={"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}})
    def test_shared_production_config_does_not_warn(self):
        self.assertEqual(realtime_configuration(None), [])

    def test_event_payload_whitelist(self):
        for key in ["correct_choice", "question", "position", "deadline", "answered_count", "version", "nickname"]:
            with self.assertRaises(ValueError): realtime.notify(None, "answer_revealed", **{key: "private"})
        with self.assertRaises(ValueError): realtime.notify(None, "unknown")

    def test_environment_config_separates_staging_production_and_rejects_empty_namespace(self):
        code = ("from config.settings.base import QUITIZZ_PREFIX, CACHES, CHANNEL_LAYERS; "
                "from channels_redis.core import RedisChannelLayer; import json; "
                "assert CHANNEL_LAYERS['default']['CONFIG']['hosts'][0]['socket_timeout'] > RedisChannelLayer.brpop_timeout; "
                "print(json.dumps([QUITIZZ_PREFIX,CACHES['quitizz']['BACKEND'],CHANNEL_LAYERS['default']['BACKEND']]))")
        prefixes = []
        for deployment in ["staging", "production"]:
            env = {**os.environ, "QUITIZZ_DEPLOYMENT": deployment, "QUITIZZ_REDIS_NAMESPACE": "school",
                   "QUITIZZ_REDIS_URL": "redis://127.0.0.1:6379/15"}
            result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            prefix, cache, layer = json.loads(result.stdout)
            self.assertEqual(cache, "django.core.cache.backends.redis.RedisCache")
            self.assertEqual(layer, "channels_redis.core.RedisChannelLayer")
            prefixes.append(prefix)
        self.assertNotEqual(*prefixes)
        env["QUITIZZ_REDIS_NAMESPACE"] = ""
        self.assertNotEqual(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True).returncode, 0)

    def test_throttle_uses_dedicated_cache_preserves_ttl_and_fails_closed(self):
        with patch("apps.quitizz.gameplay.caches") as caches:
            cache = caches.__getitem__.return_value
            cache.add.return_value = True
            game.throttle("answer", "session", "credential", 60)
            caches.__getitem__.assert_called_once_with("quitizz")
            self.assertEqual(cache.add.call_args.kwargs["timeout"], 61)
            self.assertNotIn("credential", cache.add.call_args.args[0])
            cache.add.side_effect = RuntimeError("Redis unavailable")
            with self.assertRaises(game.RateLimited): game.throttle("answer", "session", "credential", 60)
