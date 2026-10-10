import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


_snapshot_session = ContextVar("quitizz_snapshot_session", default=None)


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

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "campus", "host", "status"], name="qts_host_scope_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(source_revision__gt=0) & models.Q(state_version__gt=0), name="qts_versions_positive"),
            models.CheckConstraint(condition=models.Q(status="READY"), name="qts_foundation_status"),
        ]

    def clean(self):
        if self.source_id and (self.source.tenant_id != self.tenant_id or self.source.campus_id != self.campus_id or self.source.owner_id != self.host_id):
            raise ValidationError("Session source, host and scope must match.")


class QuiTizzSessionQuestion(QuestionContent, ImmutableModel):
    session = models.ForeignKey(QuiTizzSession, on_delete=models.PROTECT, related_name="questions")

    class Meta:
        ordering = ["position", "id"]
        constraints = question_constraints("qtsq", "session")

    def check_creation(self):
        if self.session_id != _snapshot_session.get():
            raise ValidationError("Questions can only be snapshotted during launch.")
