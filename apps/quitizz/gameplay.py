"""Transport-independent Phase 2A game rules. Lock order: session, participant, question.

Session row locks serialize host/join/answer/removal races on InnoDB. Immutable
question content is never rewritten. No countdown jobs or per-player ticks.
"""
import hashlib
import hmac
import secrets
import unicodedata
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.core.services.features import FeatureSettingsService

from .access import require_access
from .models import QuiTizzParticipant, QuiTizzResponse, QuiTizzSession, gameplay_write
from .services import QuiTizzService, StaleRevision
from . import realtime


HOURS = 8
CAPABILITY_SALT = "teachermateplus.quitizz.join.v1"
GRANT_SALT = "teachermateplus.quitizz.exchange.v1"
POLICY = {"version": 1, "correct_min": 700, "speed_max": 300, "wrong": 0}
TERMINAL = {"COMPLETED", "CANCELLED"}


class Unavailable(ValidationError):
    def __init__(self):
        super().__init__("QuiTizz is unavailable. Scan the current QR code or contact the host.")


class RateLimited(ValidationError):
    def __init__(self):
        super().__init__("Please wait a moment before trying again.")


def normalize_nickname(value):
    if not isinstance(value, str):
        raise ValidationError("Enter a nickname of 1 to 32 characters.")
    nickname = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(nickname) <= 32 or any(unicodedata.category(c).startswith("C") for c in nickname):
        raise ValidationError("Enter a nickname of 1 to 32 characters without control characters.")
    key = unicodedata.normalize("NFKC", nickname.casefold())
    if len(key) > 64:
        raise ValidationError("Choose a shorter nickname.")
    # ASCII digest keeps uniqueness independent of MariaDB accent/case collation.
    return nickname, hashlib.sha256(key.encode("utf-8")).hexdigest()


@sensitive_variables("identity")
def throttle(kind, session_id, identity, limit, seconds=60):
    """Cache abstraction; LocMem is per-process, shared Redis required at rollout.

    High IP limits accommodate shared Wi-Fi; participant credentials get separate
    budgets. Never use an untrusted forwarded header as the network identity.
    """
    bucket = int(timezone.now().timestamp()) // seconds
    digest = hashlib.sha256(f"{session_id}:{identity}:{bucket}".encode()).hexdigest()
    key = f"quitizz:{kind}:{digest}"
    cache = caches["quitizz"]
    try:
        if cache.add(key, 1, timeout=seconds + 1):
            return
        try:
            count = cache.incr(key)
        except ValueError:
            if cache.add(key, 1, timeout=seconds + 1):
                return
            count = cache.incr(key)
    except Exception:
        # Fail closed on shared-cache outage; never silently claim enforcement
        # with a per-worker fallback. No exception/Redis URL in public output.
        raise RateLimited() from None
    if count > limit:
        raise RateLimited()


def available(session, *, joining=False, now=None):
    now = now or timezone.now()
    if (not FeatureSettingsService.is_quitizz_enabled(tenant_id=session.tenant_id)
            or not session.tenant.is_active or not session.campus.is_active):
        raise Unavailable()
    if joining and (session.status in TERMINAL or not session.joining_open or not session.expires_at or now >= session.expires_at):
        raise Unavailable()
    if session.expires_at and now >= session.expires_at:
        raise Unavailable()


def resolve(public_id, *, lock=False):
    qs = QuiTizzSession.objects
    if lock:
        qs = qs.select_for_update()
    try:
        # Joining related rows in SELECT FOR UPDATE would also lock the shared
        # tenant/campus on MariaDB. Independent sessions must not share that lock.
        if not lock:
            qs = qs.select_related("tenant", "campus")
        session = qs.get(public_id=public_id)
    except (QuiTizzSession.DoesNotExist, ValidationError, ValueError, TypeError):
        raise Unavailable()
    available(session)
    return session


def capability(session):
    available(session, joining=True)
    return signing.dumps({"session": str(session.public_id), "generation": str(session.join_generation)}, salt=CAPABILITY_SALT)


@sensitive_variables("token", "value")
def verify_signed(session, token, *, salt, max_age):
    try:
        value = signing.loads(token, salt=salt, max_age=max_age)
    except (signing.BadSignature, TypeError, ValueError):
        raise Unavailable()
    if value != {"session": str(session.public_id), "generation": str(session.join_generation)}:
        raise Unavailable()


@transaction.atomic
@sensitive_variables("token")
def exchange(public_id, token):
    session = resolve(public_id, lock=True)
    available(session, joining=True)
    verify_signed(session, token, salt=CAPABILITY_SALT, max_age=HOURS * 3600)
    return signing.dumps({"session": str(session.public_id), "generation": str(session.join_generation)}, salt=GRANT_SALT)


@sensitive_variables("credential")
def credential_digest(session, credential):
    return hmac.new(settings.SECRET_KEY.encode(), f"quitizz:{session.public_id}:{credential}".encode(), hashlib.sha256).hexdigest()


@sensitive_variables("credential", "secret")
def identity(session, credential, *, lock=False):
    try:
        participant_id, secret = credential.split(".", 1)
        participant_id = uuid.UUID(participant_id)
        if len(secret) != 43:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise Unavailable()
    qs = QuiTizzParticipant.objects.filter(session=session, public_id=participant_id)
    if lock:
        qs = qs.select_for_update()
    participant = qs.first()
    if (not participant or participant.removed_at or timezone.now() >= participant.reconnect_expires_at
            or not hmac.compare_digest(participant.reconnect_digest, credential_digest(session, credential))):
        raise Unavailable()
    return participant


@transaction.atomic
@sensitive_variables("grant", "credential")
def join(public_id, grant, nickname, credential=""):
    session = resolve(public_id, lock=True)
    available(session, joining=True)
    verify_signed(session, grant, salt=GRANT_SALT, max_age=600)
    # A refresh/retry with the valid cookie restores the existing participant.
    if credential:
        return identity(session, credential, lock=True), credential
    nickname, key = normalize_nickname(nickname)
    if session.participants.filter(nickname_key=key).exists():
        raise ValidationError("That nickname is already in use. Choose another.")
    participant_id = uuid.uuid4()
    credential = f"{participant_id}.{secrets.token_urlsafe(32)}"
    participant = QuiTizzParticipant(session=session, public_id=participant_id, nickname=nickname, nickname_key=key,
        reconnect_digest=credential_digest(session, credential), reconnect_expires_at=session.expires_at)
    try:
        with transaction.atomic():
            # Values explicitly validated above; DB constraints backstop racing inserts.
            QuiTizzParticipant.objects.bulk_create([participant])
    except IntegrityError:
        raise ValidationError("That nickname is already in use. Choose another.")
    realtime.notify(session, "participant_joined")
    return participant, credential


def write(obj, **fields):
    fields["updated_at"] = timezone.now()
    with gameplay_write():
        type(obj).objects.filter(pk=obj.pk).update(**fields)
    for name, value in fields.items():
        setattr(obj, name, value)


def ranked_participants(session):
    return session.participants.filter(removed_at__isnull=True).order_by(
        "-total_score", "-correct_count", "cumulative_response_ms", "joined_at", "public_id")


@transaction.atomic
def command(*, public_id, user, tenant_id, campus_id, version, action, participant_id=None, mode=None, request=None):
    require_access(user, tenant_id, campus_id, "host")
    session = get_object_or_404(QuiTizzSession.objects.select_for_update(), public_id=public_id,
        tenant_id=tenant_id, campus_id=campus_id, host=user)
    available(session)
    # Recheck after acquiring the authoritative row lock.
    require_access(user, tenant_id, campus_id, "host")
    try:
        expected = int(version)
    except (ValueError, TypeError):
        raise StaleRevision("Reload the session before using this control.")
    if expected != session.state_version or session.status in TERMINAL:
        raise StaleRevision("Session changed. Reload before using this control.")
    now = timezone.now()
    changes = {"state_version": session.state_version + 1}
    question = session.questions.filter(position=session.current_position).first() if session.current_position else None
    from . import automation
    if action in {"pause", "resume", "set_mode", "reveal_now", "next_now", "end_challenge"}:
        if session.playback_mode == "AUTOMATIC" and action in {"reveal_now", "next_now"}:
            automation.authorize(session)
        return automation.host_control(session, question, action, now, mode=mode, request=request)
    if session.paused_at and action not in {"remove", "open_joining", "close_joining", "complete", "cancel"}:
        raise ValidationError("Resume the session before progressing.")
    if session.playback_mode == "AUTOMATIC" and action in {"open_question", "close_question", "reveal", "next"}:
        automation.authorize(session)
        if not automation.throttle_healthy():
            raise ValidationError("Shared gameplay protection is unavailable.")
    if action in {"open_joining", "close_joining"}:
        opening = action == "open_joining"
        if session.joining_open == opening:
            raise StaleRevision("Joining already changed. Reload the session.")
        changes["joining_open"] = opening
        if opening:
            changes["join_generation"] = uuid.uuid4()
            changes["expires_at"] = session.expires_at or now + timedelta(hours=HOURS)
            if session.status == "READY":
                changes["status"] = "LOBBY"
    elif action == "remove":
        try:
            participant = session.participants.select_for_update().get(public_id=participant_id, removed_at__isnull=True)
        except (QuiTizzParticipant.DoesNotExist, ValidationError, ValueError, TypeError):
            raise Unavailable()
        session.participants.filter(pk=participant.pk).update(removed_at=now, updated_at=now)
    elif action in {"start", "open_question", "next"}:
        if action == "start":
            if session.status != "LOBBY":
                raise ValidationError("Open the lobby before starting.")
            position = 1
            changes.update(joining_open=False, scoring_policy_snapshot=dict(POLICY))
        elif action == "next":
            if session.status != "ANSWER_REVEALED":
                raise ValidationError("Reveal the current answer before advancing.")
            position = session.current_position + 1
        else:
            if session.status != "QUESTION_CLOSED" or not question or question.opened_at:
                raise ValidationError("The current question cannot be reopened.")
            position = session.current_position
        target = session.questions.filter(position=position).first()
        if not target:
            raise ValidationError("There is no next question. Complete the session.")
        if target.opened_at:
            raise StaleRevision("This question has already opened.")
        if action == "open_question":
            write(target, opened_at=now, deadline_at=now + timedelta(seconds=target.timer_seconds))
            changes.update(status="QUESTION_OPEN", current_position=position)
        else:
            changes.update(status="QUESTION_CLOSED", current_position=position)
    elif action == "close_question":
        if session.status != "QUESTION_OPEN" or not question:
            raise ValidationError("There is no open question to close.")
        write(question, closed_at=now)
        changes["status"] = "QUESTION_CLOSED"
    elif action == "reveal":
        if session.status != "QUESTION_CLOSED" or not question or not question.opened_at:
            raise ValidationError("Close the question before revealing.")
        write(question, revealed_at=now)
        changes["status"] = "ANSWER_REVEALED"
    elif action in {"complete", "cancel"}:
        if action == "complete" and session.status != "ANSWER_REVEALED":
            raise ValidationError("Reveal the current answer before completing, or cancel the session.")
        # Cancellation freezes a currently open question without revealing its key.
        if question and question.opened_at and not question.closed_at:
            write(question, closed_at=now)
        if action == "complete":
            participants = list(ranked_participants(session))
            for rank, participant in enumerate(participants, 1):
                participant.final_rank = rank
            QuiTizzParticipant.objects.bulk_update(participants, ["final_rank"])
        changes.update(status="COMPLETED" if action == "complete" else "CANCELLED", joining_open=False, completed_at=now)
    else:
        raise ValidationError("Unknown session control.")
    automation.after_command(session, changes, target if action == "open_question" else question, action, now)
    write(session, **changes)
    QuiTizzService.audit("GAME_CONTROL", session, user, request, command=action, state_version=session.state_version,
        participant=str(participant_id) if action == "remove" else None)
    events = {"open_joining": "joining_opened", "close_joining": "joining_closed", "remove": "participant_removed",
              "start": "session_started", "next": "question_prepared", "open_question": "question_opened",
              "close_question": "question_closed", "reveal": "answer_revealed", "complete": "session_completed",
              "cancel": "session_cancelled"}
    realtime.notify(session, events[action])
    if action == "remove":
        realtime.removed(session, participant)
    return session


def score(is_correct, elapsed_us, duration_us):
    if not is_correct:
        return 0
    remaining = max(0, min(duration_us, duration_us - elapsed_us))
    return max(700, min(1000, 700 + (300 * remaining // duration_us)))


@transaction.atomic
@sensitive_variables("credential")
def submit(public_id, credential, question_id, selected_choice, *, received_at=None):
    received_at = received_at or timezone.now()
    session = resolve(public_id, lock=True)
    if session.status in TERMINAL:
        raise Unavailable()
    participant = identity(session, credential, lock=True)
    question = session.questions.filter(public_id=question_id, position=session.current_position).first()
    if not question or selected_choice not in {"A", "B", "C", "D"}:
        raise ValidationError("This answer cannot be accepted. Refresh the question.")
    previous = QuiTizzResponse.objects.filter(participant=participant, session_question=question).first()
    if previous:
        if previous.selected_choice != selected_choice:
            raise ValidationError("Your first accepted answer is locked.")
        return {"accepted": True, "question": str(question.public_id)}
    if (session.status != "QUESTION_OPEN" or session.paused_at or not question.opened_at or question.closed_at
            or received_at < (question.active_started_at or question.opened_at) or received_at > question.deadline_at):
        raise ValidationError("The question is closed. This answer cannot be accepted.")
    elapsed = received_at - (question.active_started_at or question.opened_at)
    elapsed_us = question.active_elapsed_us + (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    correct = selected_choice == question.correct_choice
    points = score(correct, elapsed_us, question.timer_seconds * 1_000_000)
    response = QuiTizzResponse(participant=participant, session_question=question, selected_choice=selected_choice,
        received_at=received_at, elapsed_ms=elapsed_us // 1000, is_correct=correct, awarded_points=points)
    try:
        with transaction.atomic():
            QuiTizzResponse.objects.bulk_create([response])
    except IntegrityError:
        previous = QuiTizzResponse.objects.filter(participant=participant, session_question=question).first()
        if not previous or previous.selected_choice != selected_choice:
            raise ValidationError("Your first accepted answer is locked.")
        return {"accepted": True, "question": str(question.public_id)}
    # Update only this player. Rankings are calculated once, on completion.
    session.participants.filter(pk=participant.pk).update(total_score=F("total_score") + points,
        correct_count=F("correct_count") + int(correct), cumulative_response_ms=F("cumulative_response_ms") + response.elapsed_ms,
        updated_at=timezone.now())
    # Retries above return before scheduling the host's constant sync signal.
    realtime.notify(session, "answer_received")
    return {"accepted": True, "question": str(question.public_id)}


@sensitive_variables("credential")
def _state(public_id, credential):
    session = resolve(public_id)
    participant = identity(session, credential)
    now = timezone.now()
    result = {"status": session.status, "version": session.state_version, "server_now": now.isoformat(),
        "nickname": participant.nickname, "title": session.title_snapshot, "joining_open": session.joining_open}
    result.update(phase_state(session))
    if session.status == "CANCELLED":
        return result
    question = session.questions.filter(position=session.current_position).first() if session.current_position else None
    if question and question.opened_at:
        result["question"] = {"id": str(question.public_id), "position": question.position, "prompt": question.prompt,
            "choices": {letter: getattr(question, f"choice_{letter.lower()}") for letter in "ABCD"},
            "deadline": question.deadline_at.isoformat()}
        response = QuiTizzResponse.objects.filter(participant=participant, session_question=question).first()
        result["accepted"] = response is not None
        result["can_answer"] = session.status == "QUESTION_OPEN" and not session.paused_at and not response and now <= question.deadline_at
        if question.revealed_at and session.status in {"ANSWER_REVEALED", "COMPLETED"}:
            result["feedback"] = {"correct_choice": question.correct_choice, "is_correct": bool(response and response.is_correct),
                "points": response.awarded_points if response else 0, "answered": response is not None}
    if session.status == "COMPLETED":
        result["summary"] = {"total_score": participant.total_score, "correct_count": participant.correct_count,
            "rank": participant.final_rank}
    return result


def _presentation_state(session, question=None, *, counts=None):
    """Host-authorized projection. Never include identities beside responses."""
    question = question or (session.questions.filter(position=session.current_position).first() if session.current_position else None)
    result = {"title": session.title_snapshot, "status": session.status, "version": session.state_version,
        "server_now": timezone.now().isoformat(), "joining_open": session.joining_open,
        "position": session.current_position}
    result.update(phase_state(session))
    result.update(counts if counts is not None else {"question_count": session.questions.count(),
        "participant_count": session.participants.filter(removed_at__isnull=True).count(),
        "answered_count": question.responses.count() if question else 0})
    if question and question.opened_at and session.status != "CANCELLED":
        result["question"] = {"id": str(question.public_id), "position": question.position, "prompt": question.prompt,
            "choices": {letter: getattr(question, f"choice_{letter.lower()}") for letter in "ABCD"},
            "deadline": question.deadline_at.isoformat()}
    if question and question.revealed_at and session.status in {"ANSWER_REVEALED", "COMPLETED"}:
        from django.db.models import Count
        counts = dict(question.responses.values("selected_choice").annotate(total=Count("pk")).values_list("selected_choice", "total"))
        result["reveal"] = {"correct_choice": question.correct_choice,
            "distribution": {letter: counts.get(letter, 0) for letter in "ABCD"}}
        players = ranked_participants(session) if session.status != "COMPLETED" else session.participants.filter(removed_at__isnull=True).order_by("final_rank")
        result["leaderboard"] = [{"rank": player.final_rank if session.status == "COMPLETED" else rank, "nickname": player.nickname, "score": player.total_score}
            for rank, player in enumerate(players[:5], 1)]
    return result


def _host_state(session):
    # One list query, independent of population; no per-row response lookups.
    participants = list(session.participants.filter(removed_at__isnull=True).values("public_id", "nickname", "joined_at"))
    question = session.questions.filter(position=session.current_position).first() if session.current_position else None
    counts = {"participant_count": len(participants), "question_count": session.questions.count(),
        "answered_count": question.responses.count() if question else 0}
    return {**_presentation_state(session, question, counts=counts), "status": session.status, "version": session.state_version, "joining_open": session.joining_open,
        "position": session.current_position, "participant_count": len(participants), "participants": participants,
        "question_id": str(question.public_id) if question else None,
        "question_opened": bool(question and question.opened_at),
        "automatic_available": FeatureSettingsService.is_quitizz_automatic_enabled(tenant_id=session.tenant_id)}


def phase_state(session):
    return {"playback_mode": session.playback_mode, "show_phase": session.show_phase,
        "phase_started_at": session.phase_started_at.isoformat() if session.phase_started_at else None,
        "phase_deadline": session.next_transition_at.isoformat() if session.next_transition_at else None,
        "paused": bool(session.paused_at), "pause_reason": session.pause_reason,
        "remaining_us": session.pause_remaining_us}


def consistent_snapshot(session, builder):
    # Optimistic retry avoids holding gameplay locks during projector/player GET.
    for _ in range(3):
        result = builder(session)
        if QuiTizzSession.objects.filter(pk=session.pk, state_version=result["version"]).exists():
            return result
        session.refresh_from_db()
        available(session)
    raise StaleRevision("Session changed. Recover the current state.")


def presentation_state(session):
    return consistent_snapshot(session, _presentation_state)


def host_state(session):
    return consistent_snapshot(session, _host_state)


@sensitive_variables("credential")
def state(public_id, credential):
    for _ in range(3):
        result = _state(public_id, credential)
        if QuiTizzSession.objects.filter(public_id=public_id, state_version=result["version"]).exists():
            return result
    raise StaleRevision("Session changed. Recover the current state.")
