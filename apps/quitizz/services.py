from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404

from apps.core.services.audit import AuditService

from .access import require_access
from .models import QuiTizz, QuiTizzQuestion, QuiTizzSession, QuiTizzSessionQuestion, snapshot_creation


CONTENT_FIELDS = ("prompt", "choice_a", "choice_b", "choice_c", "choice_d", "correct_choice", "timer_seconds")


class StaleRevision(ValidationError):
    pass


class QuiTizzService:
    @staticmethod
    def owned(user, tenant_id, campus_id):
        return QuiTizz.objects.filter(tenant_id=tenant_id, campus_id=campus_id, owner=user)

    @staticmethod
    def audit(action, obj, user, request=None, **metadata):
        return AuditService.log_event(action=f"QUITIZZ_{action}", portal="FACULTY", entity_type=type(obj).__name__, entity_id=obj.pk,
            actor=user, tenant=obj.tenant_id, campus=obj.campus_id, metadata=metadata, request=request)

    @classmethod
    def locked(cls, public_id, user, tenant_id, campus_id, revision, capability="manage", allow_archived=False):
        require_access(user, tenant_id, campus_id, capability)
        obj = get_object_or_404(cls.owned(user, tenant_id, campus_id).select_for_update(), public_id=public_id)
        try:
            expected = int(revision)
        except (ValueError, TypeError):
            raise ValidationError("A valid saved revision is required.")
        if expected != obj.revision:
            raise StaleRevision("This QuiTizz changed. Reload it before saving or launching.")
        if obj.is_archived and not allow_archived:
            raise ValidationError("Reactivate this QuiTizz before editing or launching.")
        return obj

    @classmethod
    @transaction.atomic
    def create(cls, *, user, tenant_id, campus_id, title, request=None):
        require_access(user, tenant_id, campus_id, "manage")
        obj = QuiTizz.objects.create(owner=user, tenant_id=tenant_id, campus_id=campus_id, title=title)
        cls.audit("CREATE", obj, user, request)
        return obj

    @classmethod
    @transaction.atomic
    def edit(cls, *, public_id, user, tenant_id, campus_id, revision, title, request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision)
        obj.title = title
        obj.revision += 1
        obj.save()
        cls.audit("EDIT", obj, user, request, revision=obj.revision)
        return obj

    @classmethod
    @transaction.atomic
    def archive(cls, *, public_id, user, tenant_id, campus_id, revision, archived, request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision, allow_archived=True)
        obj.is_archived = archived
        obj.revision += 1
        obj.save()
        cls.audit("ARCHIVE" if archived else "REACTIVATE", obj, user, request)
        return obj

    @classmethod
    @transaction.atomic
    def question_save(cls, *, public_id, user, tenant_id, campus_id, revision, content, question_id=None, request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision)
        question = get_object_or_404(obj.questions, pk=question_id) if question_id else QuiTizzQuestion(quitizz=obj, position=obj.questions.count() + 1)
        if set(content) != set(CONTENT_FIELDS):
            raise ValidationError("Provide the question prompt, exactly four choices, answer and timer.")
        for name in CONTENT_FIELDS:
            setattr(question, name, content[name])
        question.save()
        obj.revision += 1
        obj.save()
        cls.audit("QUESTION_EDIT" if question_id else "QUESTION_CREATE", obj, user, request, question_id=question.pk, revision=obj.revision)
        return obj

    @staticmethod
    def sequence(questions, ids):
        # Vacate all positions first to avoid intermediate unique conflicts on SQLite/MySQL.
        max_position = max(questions.values_list("position", flat=True), default=0)
        questions.update(position=F("position") + max_position + len(ids) + 1)
        for position, pk in enumerate(ids, 1):
            questions.filter(pk=pk).update(position=position)

    @classmethod
    @transaction.atomic
    def question_delete(cls, *, public_id, user, tenant_id, campus_id, revision, question_id, request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision)
        question = get_object_or_404(obj.questions, pk=question_id)
        question.delete()
        cls.sequence(obj.questions.all(), list(obj.questions.values_list("pk", flat=True)))
        obj.revision += 1
        obj.save()
        cls.audit("QUESTION_DELETE", obj, user, request, question_id=question_id, revision=obj.revision)
        return obj

    @classmethod
    @transaction.atomic
    def reorder(cls, *, public_id, user, tenant_id, campus_id, revision, question_ids, request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision)
        try:
            ids = [int(value) for value in question_ids]
        except (ValueError, TypeError):
            raise ValidationError("Provide an ordered list of question IDs.")
        current = list(obj.questions.values_list("pk", flat=True))
        if len(ids) != len(set(ids)) or set(ids) != set(current):
            raise ValidationError("Reorder must include every current question exactly once.")
        cls.sequence(obj.questions.all(), ids)
        obj.revision += 1
        obj.save()
        cls.audit("REORDER", obj, user, request, question_ids=ids, revision=obj.revision)
        return obj

    @classmethod
    @transaction.atomic
    def launch(cls, *, public_id, user, tenant_id, campus_id, revision, playback_mode="MANUAL", request=None):
        obj = cls.locked(public_id, user, tenant_id, campus_id, revision, capability="host")
        from .automation import POLICY
        from apps.core.services.features import FeatureSettingsService
        if playback_mode not in {"MANUAL", "AUTOMATIC"}:
            raise ValidationError("Select a valid playback mode.")
        if playback_mode == "AUTOMATIC" and not FeatureSettingsService.is_quitizz_automatic_enabled(tenant_id=tenant_id):
            raise ValidationError("Automatic QuiTizz is unavailable.")
        questions = list(obj.questions.all())
        if not questions:
            raise ValidationError("Add at least one question before hosting.")
        for question in questions:
            question.full_clean()
        session = QuiTizzSession.objects.create(source=obj, tenant_id=tenant_id, campus_id=campus_id, host=user,
            title_snapshot=obj.title, source_revision=obj.revision, scoring_policy_snapshot={},
            playback_mode=playback_mode, automation_policy_snapshot=dict(POLICY))
        with snapshot_creation(session.pk):
            QuiTizzSessionQuestion.objects.bulk_create([QuiTizzSessionQuestion(session=session, position=q.position,
                **{name: getattr(q, name) for name in CONTENT_FIELDS}) for q in questions])
        cls.audit("SESSION_LAUNCH", session, user, request, source_id=obj.pk, source_revision=obj.revision, question_count=len(questions))
        return session
