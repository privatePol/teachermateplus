import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


_snapshot_session = ContextVar("quitizz_snapshot_session", default=None)
_gameplay_write = ContextVar("quitizz_gameplay_write", default=False)


@contextmanager
def gameplay_write():
    """Internal service-only allowance; snapshot content still cannot be updated."""
    token = _gameplay_write.set(True)
    try:
        yield
    finally:
        _gameplay_write.reset(token)


@contextmanager
def snapshot_creation(session_id):
    """Permit snapshot inserts only during the atomic launch service operation."""
    token = _snapshot_session.set(session_id)
    try:
        yield
    finally:
        _snapshot_session.reset(token)


class ValidatedModel(TimeStampedModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class QuiTizz(ValidatedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["tenant", "campus", "owner", "is_archived"], name="qt_owner_scope_idx")]
        constraints = [models.CheckConstraint(condition=models.Q(revision__gt=0), name="qt_revision_positive")]

    def clean(self):
        if self.campus_id and self.tenant_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError({"campus": "Campus must belong to the selected tenant."})
        self.title = self.title.strip()
        if not self.title:
            raise ValidationError({"title": "Enter a title."})


class QuestionContent(ValidatedModel):
    class Choice(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        C = "C", "C"
        D = "D", "D"

    position = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    prompt = models.TextField()
    choice_a = models.TextField()
    choice_b = models.TextField()
    choice_c = models.TextField()
    choice_d = models.TextField()
    correct_choice = models.CharField(max_length=1, choices=Choice.choices)
    timer_seconds = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])

    class Meta:
        abstract = True

    def clean(self):
        errors = {}
        for name in ("prompt", "choice_a", "choice_b", "choice_c", "choice_d"):
            value = getattr(self, name).strip()
            setattr(self, name, value)
            if not value:
                errors[name] = "This field is required."
        if errors:
            raise ValidationError(errors)


def question_constraints(prefix, parent):
    return [
        models.UniqueConstraint(fields=[parent, "position"], name=f"{prefix}_unique_position"),
        models.CheckConstraint(condition=models.Q(position__gt=0), name=f"{prefix}_position_positive"),
        models.CheckConstraint(condition=models.Q(timer_seconds__gt=0), name=f"{prefix}_timer_positive"),
        models.CheckConstraint(condition=models.Q(correct_choice__in=["A", "B", "C", "D"]), name=f"{prefix}_answer_ad"),
        models.CheckConstraint(condition=~models.Q(prompt="") & ~models.Q(choice_a="") & ~models.Q(choice_b="") & ~models.Q(choice_c="") & ~models.Q(choice_d=""), name=f"{prefix}_content_required"),
    ]


class QuiTizzQuestion(QuestionContent):
    quitizz = models.ForeignKey(QuiTizz, on_delete=models.CASCADE, related_name="questions")

    class Meta:
        ordering = ["position", "id"]
        constraints = question_constraints("qtq", "quitizz")


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        allowed = getattr(self.model, "GAMEPLAY_FIELDS", frozenset())
        if _gameplay_write.get() and kwargs and set(kwargs) <= allowed:
            return super().update(**kwargs)
        raise ValidationError("Launched snapshots are immutable.")

    def delete(self):
        raise ValidationError("Launched snapshots are preserved.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Launched snapshots are immutable.")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("ignore_conflicts") or kwargs.get("update_conflicts"):
            raise ValidationError("Snapshot conflicts must fail the complete launch.")
        objs = list(objs)
        for obj in objs:
            obj.check_creation()
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class ImmutableModel(ValidatedModel):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def check_creation(self):
        pass

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Launched snapshots are immutable.")
        self.check_creation()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Launched snapshots are preserved.")


class QuiTizzSession(ImmutableModel):
    class Status(models.TextChoices):
        READY = "READY", "Ready"
        LOBBY = "LOBBY", "Lobby"
        QUESTION_OPEN = "QUESTION_OPEN", "Question open"
        QUESTION_CLOSED = "QUESTION_CLOSED", "Question closed"
        ANSWER_REVEALED = "ANSWER_REVEALED", "Answer revealed"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    GAMEPLAY_FIELDS = frozenset({"status", "joining_open", "join_generation", "expires_at", "completed_at",
        "current_position", "state_version", "scoring_policy_snapshot", "updated_at"})

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.ForeignKey(QuiTizz, on_delete=models.PROTECT, related_name="sessions")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    title_snapshot = models.CharField(max_length=200)
    source_revision = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    scoring_policy_snapshot = models.JSONField(default=dict, blank=True)
    current_position = models.PositiveIntegerField(default=0)
    state_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    joining_open = models.BooleanField(default=False)
    join_generation = models.UUIDField(default=uuid.uuid4, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "campus", "host", "status"], name="qts_host_scope_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(source_revision__gt=0) & models.Q(state_version__gt=0), name="qts_versions_positive"),
            models.CheckConstraint(condition=models.Q(status__in=["READY", "LOBBY", "QUESTION_OPEN", "QUESTION_CLOSED", "ANSWER_REVEALED", "COMPLETED", "CANCELLED"]), name="qts_gameplay_status"),
        ]

    def clean(self):
        if self.source_id and (self.source.tenant_id != self.tenant_id or self.source.campus_id != self.campus_id or self.source.owner_id != self.host_id):
            raise ValidationError("Session source, host and scope must match.")


class QuiTizzSessionQuestion(QuestionContent, ImmutableModel):
    GAMEPLAY_FIELDS = frozenset({"opened_at", "deadline_at", "closed_at", "revealed_at", "updated_at"})
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(QuiTizzSession, on_delete=models.PROTECT, related_name="questions")
    opened_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    revealed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = question_constraints("qtsq", "session")

    def check_creation(self):
        if self.session_id != _snapshot_session.get():
            raise ValidationError("Questions can only be snapshotted during launch.")


class QuiTizzParticipant(ValidatedModel):
    session = models.ForeignKey(QuiTizzSession, on_delete=models.PROTECT, related_name="participants")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nickname = models.CharField(max_length=32)
    nickname_key = models.CharField(max_length=64)
    reconnect_digest = models.CharField(max_length=64, editable=False)
    reconnect_expires_at = models.DateTimeField()
    joined_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    total_score = models.PositiveBigIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    cumulative_response_ms = models.PositiveBigIntegerField(default=0)
    final_rank = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["joined_at", "public_id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "nickname_key"], name="qtp_unique_nickname"),
            models.CheckConstraint(condition=~models.Q(nickname="") & ~models.Q(nickname_key=""), name="qtp_nickname_required"),
            models.CheckConstraint(condition=models.Q(total_score__gte=0) & models.Q(correct_count__gte=0) & models.Q(cumulative_response_ms__gte=0), name="qtp_totals_nonnegative"),
            models.CheckConstraint(condition=models.Q(final_rank__isnull=True) | models.Q(final_rank__gt=0), name="qtp_rank_positive"),
        ]
        indexes = [models.Index(fields=["session", "removed_at", "joined_at"], name="qtp_session_active_idx")]

    def clean(self):
        from .gameplay import normalize_nickname
        self.nickname, self.nickname_key = normalize_nickname(self.nickname)


class QuiTizzResponse(ValidatedModel):
    participant = models.ForeignKey(QuiTizzParticipant, on_delete=models.PROTECT, related_name="responses")
    session_question = models.ForeignKey(QuiTizzSessionQuestion, on_delete=models.PROTECT, related_name="responses")
    selected_choice = models.CharField(max_length=1, choices=QuestionContent.Choice.choices)
    received_at = models.DateTimeField()
    elapsed_ms = models.PositiveBigIntegerField()
    is_correct = models.BooleanField()
    awarded_points = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["participant", "session_question"], name="qtr_one_response"),
            models.CheckConstraint(condition=models.Q(selected_choice__in=["A", "B", "C", "D"]), name="qtr_choice_ad"),
            models.CheckConstraint(condition=models.Q(elapsed_ms__gte=0) & models.Q(awarded_points__gte=0) & models.Q(awarded_points__lte=1000), name="qtr_score_bounds"),
        ]

    def clean(self):
        if self.participant_id and self.session_question_id and self.participant.session_id != self.session_question.session_id:
            raise ValidationError("Response session must match participant session.")
