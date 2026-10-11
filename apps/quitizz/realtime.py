"""Best-effort committed constant wakeups. No gameplay state or identity.

Every lifecycle notification recovers canonical state through authorized HTTP.
Publication uses one shared message per audience with zero per-recipient DB
queries. Redis is not a game-state or permission store. Admission/close races
cannot disclose protected data because canonical HTTP alone supplies it.
"""
import asyncio
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from apps.core.services.features import FeatureSettingsService

logger = logging.getLogger(__name__)
HOST_EVENTS = {"participant_joined", "participant_removed", "answer_received", "joining_opened", "joining_closed"}
PLAYER_EVENTS = {"session_started", "question_prepared", "question_opened", "question_closed", "answer_revealed",
                 "session_completed", "session_cancelled", "phase_changed"}


def group(session_id, audience):
    return f"qt.s{int(session_id)}.{audience}"


def tenant_group(tenant_id):
    return f"qt.t{int(tenant_id)}"


def send(group_name, event):
    try:
        layer = get_channel_layer()
        if layer is not None:
            async def deliver():
                # Bound best-effort publication separately from the channel
                # layer's blocking receive timeout; never stall a committed
                # HTTP mutation waiting indefinitely for Redis.
                await asyncio.wait_for(layer.group_send(group_name, event), timeout=2)
            async_to_sync(deliver)()
    except Exception:
        # Do not log connection strings, exception text, credentials or payloads.
        logger.warning("QuiTizz realtime notification unavailable; HTTP recovery remains authoritative.")


def notify(session, name, *, dispatch=None, **payload):
    if name not in HOST_EVENTS | PLAYER_EVENTS:
        raise ValueError("Unknown QuiTizz event")
    if payload:
        raise ValueError("Unsafe QuiTizz event payload")
    session_id, tenant_id = session.pk, session.tenant_id
    event = {"type": "quitizz.event", "event": "sync_required"}
    publish = dispatch or send

    def committed():
        try:
            if not FeatureSettingsService.is_quitizz_enabled(tenant_id=tenant_id):
                return
            publish(group(session_id, "host"), event)
            if name in PLAYER_EVENTS:
                publish(group(session_id, "players"), event)
        except Exception:
            logger.warning("QuiTizz realtime notification unavailable; recover through HTTP.")
    transaction.on_commit(committed)


def removed(session, participant):
    transaction.on_commit(lambda: send(group(session.pk, f"p{participant.pk}"),
                                      {"type": "quitizz.revoke", "event": "participant_unavailable"}))
