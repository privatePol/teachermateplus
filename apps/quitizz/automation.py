"""Persisted game-show progression. All writes require the session row lock.

No Redis game state, browser authority, or per-player scheduling. A restart
executes one overdue phase, then gives the next phase its full interval.
"""
from datetime import timedelta
from collections import OrderedDict
from threading import Condition, Thread

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from .access import require_access
from .models import QuiTizzSession, QuiTizzParticipant
from .services import QuiTizzService, StaleRevision
from . import gameplay as game, realtime

POLICY = {"version": 1, "suspense_seconds": 5, "results_seconds": 7, "prepare_seconds": 5}


def microseconds(delta):
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def throttle_healthy():
    """Use the actual dedicated throttle backend; never substitute LocMem."""
    if (settings.DJANGO_ENV in {"staging", "production"} or settings.QUITIZZ_DEPLOYMENT in {"staging", "production"}) and settings.CACHES["quitizz"]["BACKEND"] != "django.core.cache.backends.redis.RedisCache":
        return False
    try:
        caches["quitizz"].get("quitizz:health:probe")
        return True
    except Exception:
        return False


def authorize(session):
    game.available(session)
    require_access(session.host, session.tenant_id, session.campus_id, "host")
    if session.source.tenant_id != session.tenant_id or session.source.campus_id != session.campus_id or session.source.owner_id != session.host_id:
        raise PermissionDenied("QuiTizz is unavailable.")
    if not FeatureSettingsService.is_quitizz_automatic_enabled(tenant_id=session.tenant_id):
        raise PermissionDenied("Automatic QuiTizz is unavailable.")


def phase_fields(session, phase, now, seconds=None):
    return {"show_phase": phase, "phase_started_at": now,
        "next_transition_at": now + timedelta(seconds=seconds) if seconds is not None and session.playback_mode == "AUTOMATIC" else None,
        "paused_at": None, "pause_remaining_us": None, "pause_reason": ""}


def duration(session, key):
    return (session.automation_policy_snapshot or POLICY)[key]


def after_command(session, changes, question, action, now):
    """Keep legacy manual commands compatible; automatic controls share phases."""
    if action == "open_question":
        changes.update(phase_fields(session, "ANSWERING", now, question.timer_seconds))
        game.write(question, active_started_at=now, active_elapsed_us=0)
    elif action == "close_question":
        changes.update(phase_fields(session, "SUSPENSE", now, duration(session, "suspense_seconds")))
    elif action == "reveal":
        changes.update(phase_fields(session, "RESULTS", now, duration(session, "results_seconds")))
    elif action in {"start", "next"}:
        changes.update(phase_fields(session, "NONE", now))
    elif action in {"complete", "cancel"}:
        changes.update(phase_fields(session, "FINISHED", now))


def pause_locked(session, question, now, reason="HOST", dispatch=None):
    if session.paused_at or session.status in game.TERMINAL:
        return False
    deadline = session.next_transition_at or (question.deadline_at if question and session.status == "QUESTION_OPEN" else None)
    if deadline is None and reason == "HOST":
        raise ValidationError("There is no timed phase to pause.")
    remaining = max(0, microseconds(deadline - now)) if deadline else 0
    if question and session.status == "QUESTION_OPEN":
        started = question.active_started_at or question.opened_at
        elapsed = max(0, microseconds(min(now, question.deadline_at) - started))
        game.write(question, active_elapsed_us=question.active_elapsed_us + elapsed, active_started_at=None)
    game.write(session, paused_at=now, pause_remaining_us=remaining, pause_reason=reason,
        next_transition_at=None, state_version=session.state_version + 1)
    QuiTizzService.audit("AUTO_SUSPEND" if reason != "HOST" else "GAME_CONTROL", session,
        None if reason != "HOST" else session.host, command="pause", reason=reason,
        origin="scheduler" if reason != "HOST" else "host", state_version=session.state_version)
    realtime.notify(session, "phase_changed", dispatch=dispatch)
    return True


def complete_locked(session, question, now):
    players = list(game.ranked_participants(session))
    for rank, player in enumerate(players, 1):
        player.final_rank = rank
    QuiTizzParticipant.objects.bulk_update(players, ["final_rank"])
    return {**phase_fields(session, "FINISHED", now), "status": "COMPLETED", "completed_at": now, "joining_open": False}


def open_next_locked(session, now):
    target = session.questions.filter(position=session.current_position + 1).first()
    if not target:
        raise ValidationError("There is no next question.")
    if target.opened_at:
        raise StaleRevision("This question has already opened.")
    game.write(target, opened_at=now, deadline_at=now + timedelta(seconds=target.timer_seconds), active_started_at=now, active_elapsed_us=0)
    return {**phase_fields(session, "ANSWERING", now, target.timer_seconds), "status": "QUESTION_OPEN", "current_position": target.position}


def host_control(session, question, action, now, *, mode=None, request=None):
    if action in {"reveal_now", "next_now"}:
        if session.paused_at:
            raise ValidationError("Resume the session before progressing.")
        if session.playback_mode == "AUTOMATIC" and not throttle_healthy():
            raise ValidationError("Shared gameplay protection is unavailable.")
    if action == "pause":
        if session.paused_at:
            raise StaleRevision("The session is already paused.")
        pause_locked(session, question, now)
        return session
    if action == "resume":
        if not session.paused_at:
            raise StaleRevision("The session is not paused.")
        if session.playback_mode == "AUTOMATIC":
            authorize(session)
        if not throttle_healthy():
            raise ValidationError("Shared gameplay protection is unavailable. Keep the session paused.")
        deadline = now + timedelta(microseconds=session.pause_remaining_us)
        changes = {"paused_at": None, "pause_remaining_us": None, "pause_reason": "",
            "next_transition_at": deadline if session.playback_mode == "AUTOMATIC" and session.show_phase != "NONE" else None}
        if question and session.status == "QUESTION_OPEN":
            # A zero-time suspension never extends an expired answer cutoff.
            game.write(question, deadline_at=deadline if session.pause_remaining_us else question.deadline_at,
                active_started_at=now)
    elif action == "set_mode":
        if mode not in {"MANUAL", "AUTOMATIC"} or mode == session.playback_mode:
            raise ValidationError("Select a different valid playback mode.")
        if mode == "AUTOMATIC":
            authorize(session)
            if not throttle_healthy():
                raise ValidationError("Shared gameplay protection is unavailable.")
        changes = {"playback_mode": mode, "next_transition_at": None}
        # Preserve the current question, cutoff, and accepted responses. A manual
        # question with prior pause accounting keeps that accounting after flips.
        phase = session.show_phase
        if session.status == "QUESTION_OPEN":
            phase = "ANSWERING"
        elif session.status == "QUESTION_CLOSED" and question and question.opened_at:
            phase = "SUSPENSE"
        elif session.status == "ANSWER_REVEALED" and phase not in {"RESULTS", "PREPARING"}:
            phase = "RESULTS"
        changes["show_phase"] = phase
        if mode == "AUTOMATIC" and not session.paused_at:
            if phase == "ANSWERING":
                changes["next_transition_at"] = question.deadline_at
            elif phase in {"SUSPENSE", "RESULTS", "PREPARING"}:
                key = {"SUSPENSE": "suspense_seconds", "RESULTS": "results_seconds", "PREPARING": "prepare_seconds"}[phase]
                changes.update(phase_started_at=now, next_transition_at=now + timedelta(seconds=duration(session, key)))
    elif action == "reveal_now":
        if not question or not question.opened_at or session.status not in {"QUESTION_OPEN", "QUESTION_CLOSED"}:
            raise ValidationError("There is no unrevealed question.")
        game.write(question, closed_at=question.closed_at or min(now, question.deadline_at), revealed_at=now)
        changes = {**phase_fields(session, "RESULTS", now, duration(session, "results_seconds")), "status": "ANSWER_REVEALED"}
    elif action == "next_now":
        if session.status != "ANSWER_REVEALED":
            raise ValidationError("Reveal before advancing.")
        if session.playback_mode == "AUTOMATIC":
            authorize(session)
            if not throttle_healthy():
                raise ValidationError("Shared gameplay protection is unavailable.")
            changes = open_next_locked(session, now)
        else:
            target = session.questions.filter(position=session.current_position + 1, opened_at__isnull=True).first()
            if not target:
                raise ValidationError("There is no next question.")
            changes = {**phase_fields(session, "NONE", now), "status": "QUESTION_CLOSED", "current_position": target.position}
    elif action == "end_challenge":
        if session.status == "ANSWER_REVEALED":
            changes = complete_locked(session, question, now)
        else:
            if question and question.opened_at and not question.closed_at:
                game.write(question, closed_at=min(now, question.deadline_at))
            changes = {**phase_fields(session, "FINISHED", now), "status": "CANCELLED", "completed_at": now, "joining_open": False}
    else:
        raise ValidationError("Unknown control.")
    game.write(session, **changes, state_version=session.state_version + 1)
    QuiTizzService.audit("GAME_CONTROL", session, session.host, request, command=action, origin="host", state_version=session.state_version)
    realtime.notify(session, "phase_changed")
    return session


@transaction.atomic
def advance(session_id, *, version, phase, dispatch=None):
    options = {"skip_locked": True} if connection.features.has_select_for_update_skip_locked else {}
    session = QuiTizzSession.objects.select_for_update(**options).filter(pk=session_id).first()
    if not session or session.state_version != version or session.show_phase != phase or session.playback_mode != "AUTOMATIC" or session.paused_at or session.status in game.TERMINAL:
        return False
    now = timezone.now()  # Capture after acquiring the lock, never from a client.
    if not session.next_transition_at or now < session.next_transition_at:
        return False
    question = session.questions.filter(position=session.current_position).first()
    try:
        authorize(session)
    except (PermissionDenied, game.Unavailable):
        return pause_locked(session, question, now, "ACCESS_UNAVAILABLE", dispatch)
    if not throttle_healthy():
        return pause_locked(session, question, now, "THROTTLE_UNAVAILABLE", dispatch)
    if phase == "ANSWERING" and session.status == "QUESTION_OPEN" and question:
        game.write(question, closed_at=question.deadline_at)
        changes = {**phase_fields(session, "SUSPENSE", now, duration(session, "suspense_seconds")), "status": "QUESTION_CLOSED"}
    elif phase == "SUSPENSE" and session.status == "QUESTION_CLOSED" and question and question.opened_at:
        game.write(question, revealed_at=now)
        changes = {**phase_fields(session, "RESULTS", now, duration(session, "results_seconds")), "status": "ANSWER_REVEALED"}
    elif phase == "RESULTS" and session.status == "ANSWER_REVEALED":
        if session.questions.filter(position=session.current_position + 1).exists():
            changes = phase_fields(session, "PREPARING", now, duration(session, "prepare_seconds"))
        else:
            changes = complete_locked(session, question, now)
    elif phase == "PREPARING" and session.status == "ANSWER_REVEALED":
        changes = open_next_locked(session, now)
    else:
        return pause_locked(session, question, now, "INVALID_PHASE", dispatch)
    game.write(session, **changes, state_version=session.state_version + 1)
    QuiTizzService.audit("AUTO_CONTROL", session, None, origin="scheduler", phase=phase,
        state_version=session.state_version, host_id=session.host_id)
    realtime.notify(session, "phase_changed", dispatch=dispatch)
    return True


def due_candidates(now=None, batch_size=100):
    return list(QuiTizzSession.objects.filter(playback_mode="AUTOMATIC", next_transition_at__lte=now or timezone.now())
        .order_by("next_transition_at", "pk").values_list("pk", "state_version", "show_phase")[:batch_size])


@transaction.atomic
def safety_check(session_id, *, healthy, dispatch=None):
    """Periodic safety scan also suspends answering before its deadline."""
    session = QuiTizzSession.objects.select_for_update().filter(pk=session_id, playback_mode="AUTOMATIC", paused_at__isnull=True).first()
    if not session or session.status in game.TERMINAL:
        return False
    reason = "" if healthy else "THROTTLE_UNAVAILABLE"
    try:
        authorize(session)
    except (PermissionDenied, game.Unavailable):
        reason = "ACCESS_UNAVAILABLE"
    if reason:
        question = session.questions.filter(position=session.current_position).first()
        return pause_locked(session, question, timezone.now(), reason, dispatch or notifier.enqueue)
    return False


def suspend_tenant(tenant_id):
    sessions = QuiTizzSession.objects.filter(playback_mode="AUTOMATIC", paused_at__isnull=True).exclude(status__in=game.TERMINAL)
    if tenant_id is not None:
        sessions = sessions.filter(tenant_id=tenant_id)
    for pk in sessions.values_list("pk", flat=True).iterator(chunk_size=100):
        safety_check(pk, healthy=True)


class NotificationQueue:
    """One daemon, capped/coalesced groups; no gameplay data or durable jobs."""
    def __init__(self, capacity=256):
        self.capacity, self.pending = capacity, OrderedDict()
        self.condition, self.thread, self.stopped = Condition(), None, False

    def enqueue(self, group_name, event):
        if event != {"type": "quitizz.event", "event": "sync_required"}:
            raise ValueError("Only constant notifications may be queued")
        with self.condition:
            if self.stopped:
                return
            if group_name not in self.pending and len(self.pending) >= self.capacity:
                return  # HTTP phase recovery replaces a missed wakeup.
            self.pending[group_name] = dict(event)
            if self.thread is None:
                self.thread = Thread(target=self._run, name="quitizz-notifier", daemon=True)
                self.thread.start()
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopped or self.pending)
                if self.stopped:
                    return
                group_name, event = self.pending.popitem(last=False)
            realtime.send(group_name, event)

    def stop(self):
        with self.condition:
            self.stopped = True
            self.pending.clear()
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2.1)


notifier = NotificationQueue()
