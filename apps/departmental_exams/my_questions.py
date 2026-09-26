"""Faculty-owned Question Bank revisions and safe Draft materialization."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.http import Http404

from apps.academics.models import FacultyAssignment
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.tenants.models import Tenant

from .contribution_services import QuestionPayloadService
from .models import (
    ExamScenario,
    ExamScenarioMember,
    FacultyContribution,
    Question,
    QuestionBankCaseMember,
    QuestionBankItem,
    QuestionBankRevision,
)
from .scenario_content import canonicalize_scenario_content


QUESTION_FIELDS = (
    "content_format",
    "question_text",
    "choice_a",
    "choice_b",
    "choice_c",
    "choice_d",
    "correct_answer",
    "difficulty",
)


@dataclass(frozen=True)
class AuthoringScope:
    tenant_id: int
    campus_id: int
    course_id: int
    course_code: str
    course_title: str


def _portal_allowed(*, user, tenant_id, campus_id):
    return PermissionService.has_assigned_permission(
        user,
        "faculty_portal.access",
        tenant_id=tenant_id,
        campus_id=campus_id,
        exact_scope=True,
    )


def authoring_scopes(*, user, tenant_id, campus_id=None):
    """Retained accepted assignment evidence; current term/open status is irrelevant."""

    if not FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=tenant_id):
        return []
    tenant = Tenant.objects.filter(pk=tenant_id, is_active=True).first()
    if tenant is None:
        return []
    assignments = (
        FacultyAssignment.objects.filter(
            faculty_user=user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at__isnull=False,
            offering__tenant_id=tenant_id,
            offering__campus__is_active=True,
            offering__course__is_active=True,
        )
        .filter(
            Q(tenant_id=tenant_id, campus_id=F("offering__campus_id"))
            | Q(tenant__isnull=True, campus__isnull=True)
        )
        .select_related("offering__campus", "offering__course")
        .order_by("offering__course__code", "offering__campus__name", "id")
    )
    seen = set()
    result = []
    for assignment in assignments:
        offering = assignment.offering
        key = (offering.campus_id, offering.course_id)
        if key in seen or (campus_id is not None and offering.campus_id != campus_id):
            continue
        if not _portal_allowed(
            user=user, tenant_id=tenant_id, campus_id=offering.campus_id
        ):
            continue
        seen.add(key)
        result.append(
            AuthoringScope(
                tenant_id=tenant_id,
                campus_id=offering.campus_id,
                course_id=offering.course_id,
                course_code=offering.course.code,
                course_title=offering.course.title,
            )
        )
    return result
def require_scope(*, user, tenant_id, campus_id, course_id):
    match = next(
        (
            scope
            for scope in authoring_scopes(
                user=user, tenant_id=tenant_id, campus_id=campus_id
            )
            if scope.course_id == course_id
        ),
        None,
    )
    if match is None:
        raise PermissionDenied("My Questions authoring is unavailable for this course scope.")
    return match


def require_case_enabled(*, tenant_id):
    if not FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
        tenant_id=tenant_id
    ):
        raise PermissionDenied("Case authoring is unavailable.")


def require_owner(*, user, tenant_id, campus_id, item_id, for_update=False):
    if not FeatureSettingsService.is_departmental_exam_builder_enabled(
        tenant_id=tenant_id
    ):
        raise PermissionDenied("My Questions is unavailable.")
    queryset = QuestionBankItem.objects
    if for_update:
        queryset = queryset.select_for_update()
    item = queryset.filter(
        pk=item_id,
        owner=user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ).first()
    if item is None:
        raise Http404
    if not _portal_allowed(
        user=user, tenant_id=item.tenant_id, campus_id=item.campus_id
    ):
        raise PermissionDenied("My Questions access is unavailable.")
    require_scope(
        user=user,
        tenant_id=item.tenant_id,
        campus_id=item.campus_id,
        course_id=item.course_id,
    )
    return item


def current_revision(item, *, for_update=False):
    queryset = QuestionBankRevision.objects
    if for_update:
        queryset = queryset.select_for_update()
    revision = queryset.filter(item=item, revision=item.current_revision).first()
    if revision is None:
        raise ValidationError("The current Question Bank revision is unavailable.")
    return revision


def _clean_question(payload):
    cleaned = QuestionPayloadService.validate(
        {field: payload.get(field, "") for field in QUESTION_FIELDS}
    )
    return {field: cleaned[field] for field in QUESTION_FIELDS}


def _question_values(source):
    return {field: getattr(source, field) for field in QUESTION_FIELDS}


def _create_revision(*, item, actor, payload=None, case=None, members=()):
    number = item.current_revision
    if item.kind == QuestionBankItem.Kind.QUESTION:
        values = _clean_question(payload or {})
        revision = QuestionBankRevision.objects.create(
            item=item, revision=number, created_by=actor, **values
        )
    else:
        canonical = canonicalize_scenario_content((case or {}).get("stimulus", ""))
        revision = QuestionBankRevision.objects.create(
            item=item,
            revision=number,
            created_by=actor,
            title=((case or {}).get("title", "") or "").strip(),
            stimulus=canonical.html,
            scenario_content_format=ExamScenario.ContentFormat.RICH_HTML_V1,
        )
        for position, member_payload in enumerate(members, start=1):
            QuestionBankCaseMember.objects.create(
                revision=revision, position=position, **_clean_question(member_payload)
            )
        if not revision.members.exists():
            raise ValidationError("A whole Case requires at least one linked MCQ.")
    return revision


def _audit(*, action, item, actor, before, after):
    AuditService.log_event(
        action=action,
        portal="FACULTY",
        entity_type="QuestionBankItem",
        entity_id=item.id,
        actor=actor,
        tenant=item.tenant_id,
        campus=item.campus_id,
        metadata={
            "kind": item.kind,
            "course_id": item.course_id,
            "revision_before": before,
            "revision_after": after,
            "origin_question_id": item.origin_question_id,
            "origin_scenario_id": item.origin_scenario_id,
        },
    )


@transaction.atomic
def create_question(*, actor, tenant_id, campus_id, course_id, payload):
    require_scope(
        user=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=course_id,
    )
    item = QuestionBankItem.objects.create(
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=course_id,
        owner=actor,
        kind=QuestionBankItem.Kind.QUESTION,
    )
    _create_revision(item=item, actor=actor, payload=payload)
    _audit(
        action="DE_MY_QUESTION_CREATED", item=item, actor=actor, before=0, after=1
    )
    return item


@transaction.atomic
def create_case(*, actor, tenant_id, campus_id, course_id, case, first_member):
    require_scope(
        user=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=course_id,
    )
    require_case_enabled(tenant_id=tenant_id)
    item = QuestionBankItem.objects.create(
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=course_id,
        owner=actor,
        kind=QuestionBankItem.Kind.CASE,
    )
    _create_revision(
        item=item, actor=actor, case=case, members=[first_member]
    )
    _audit(action="DE_MY_CASE_CREATED", item=item, actor=actor, before=0, after=1)
    return item


def historical_question_owner(*, actor, tenant_id, campus_id, question_id):
    if not FeatureSettingsService.is_departmental_exam_builder_enabled(
        tenant_id=tenant_id
    ):
        raise PermissionDenied("My Questions is unavailable.")
    question = (
        Question.objects.select_related(
            "contribution__cycle_course__cycle", "contribution__cycle_course__course"
        )
        .filter(
            pk=question_id,
            contribution__faculty_user=actor,
            contribution__status=FacultyContribution.Status.SUBMITTED,
            contribution__cycle_course__cycle__tenant_id=tenant_id,
            contribution__source_campus_id=campus_id,
        )
        .first()
    )
    if question is None:
        raise Http404
    if ExamScenarioMember.objects.filter(question_id=question.id).exists():
        raise Http404
    require_scope(
        user=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=question.contribution.cycle_course.course_id,
    )
    return question


@transaction.atomic
def revise_question(
    *, actor, tenant_id, campus_id, payload, expected_revision, item_id=None,
    historical_question_id=None,
):
    if item_id is None:
        source = historical_question_owner(
            actor=actor,
            tenant_id=tenant_id,
            campus_id=campus_id,
            question_id=historical_question_id,
        )
        item, created = QuestionBankItem.objects.select_for_update().get_or_create(
            origin_question=source,
            defaults={
                "tenant_id": tenant_id,
                "campus_id": campus_id,
                "course_id": source.contribution.cycle_course.course_id,
                "owner": actor,
                "kind": QuestionBankItem.Kind.QUESTION,
            },
        )
        if not created:
            if item.owner_id != actor.id:
                raise Http404
            raise ValidationError("This historical question was already adopted. Reload My Questions.")
        elif expected_revision not in (0, 1):
            raise ValidationError("The historical question changed. Reload and try again.")
    else:
        item = require_owner(
            user=actor,
            tenant_id=tenant_id,
            campus_id=campus_id,
            item_id=item_id,
            for_update=True,
        )
        if item.kind != QuestionBankItem.Kind.QUESTION:
            raise Http404
        if item.current_revision != expected_revision:
            raise ValidationError("This question changed. Reload and try again.")
        item.current_revision += 1
    before = item.current_revision - 1 if item.current_revision > 1 else 0
    item.save(update_fields=["current_revision", "updated_at"])
    revision = _create_revision(item=item, actor=actor, payload=payload)
    _audit(
        action="DE_MY_QUESTION_REVISED",
        item=item,
        actor=actor,
        before=before,
        after=item.current_revision,
    )
    return revision


def _copy_member_values(member):
    return _question_values(member)


@transaction.atomic
def revise_case(
    *, actor, tenant_id, campus_id, item_id, expected_revision,
    case=None, member_position=None, member_payload=None, append_member=False,
):
    require_case_enabled(tenant_id=tenant_id)
    item = require_owner(
        user=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        item_id=item_id,
        for_update=True,
    )
    if item.kind != QuestionBankItem.Kind.CASE or item.current_revision != expected_revision:
        raise ValidationError("This whole Case changed. Reload and try again.")
    previous = current_revision(item, for_update=True)
    members = [_copy_member_values(row) for row in previous.members.order_by("position")]
    if append_member:
        members.append(member_payload)
    elif member_position is not None:
        if member_position < 1 or member_position > len(members):
            raise Http404
        members[member_position - 1] = member_payload
    before = item.current_revision
    item.current_revision += 1
    item.save(update_fields=["current_revision", "updated_at"])
    revision = _create_revision(
        item=item,
        actor=actor,
        case=case or {"title": previous.title, "stimulus": previous.stimulus},
        members=members,
    )
    _audit(
        action="DE_MY_CASE_REVISED",
        item=item,
        actor=actor,
        before=before,
        after=item.current_revision,
    )
    return revision


def _historical_case_owner(*, actor, tenant_id, campus_id, scenario_id):
    if not FeatureSettingsService.is_departmental_exam_builder_enabled(
        tenant_id=tenant_id
    ):
        raise PermissionDenied("My Questions is unavailable.")
    scenario = (
        ExamScenario.objects.select_related(
            "contribution__cycle_course__cycle", "contribution__cycle_course__course"
        )
        .prefetch_related("members__question")
        .filter(
            pk=scenario_id,
            contribution__faculty_user=actor,
            contribution__status=FacultyContribution.Status.SUBMITTED,
            contribution__cycle_course__cycle__tenant_id=tenant_id,
            contribution__source_campus_id=campus_id,
        )
        .first()
    )
    if scenario is None:
        raise Http404
    members = list(scenario.members.filter(active_marker=1).order_by("position"))
    if not members or [row.position for row in members] != list(range(1, len(members) + 1)):
        raise ValidationError("This historical Case cannot be revised safely.")
    if any(row.question.contribution_id != scenario.contribution_id for row in members):
        raise ValidationError("This historical Case cannot be revised safely.")
    require_scope(
        user=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        course_id=scenario.contribution.cycle_course.course_id,
    )
    return scenario, members


@transaction.atomic
def revise_historical_case(
    *, actor, tenant_id, campus_id, scenario_id, title, stimulus,
):
    require_case_enabled(tenant_id=tenant_id)
    scenario, source_members = _historical_case_owner(
        actor=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        scenario_id=scenario_id,
    )
    item, created = QuestionBankItem.objects.select_for_update().get_or_create(
        origin_scenario=scenario,
        defaults={
            "tenant_id": tenant_id,
            "campus_id": campus_id,
            "course_id": scenario.contribution.cycle_course.course_id,
            "owner": actor,
            "kind": QuestionBankItem.Kind.CASE,
        },
    )
    before = 0
    if not created:
        if item.owner_id != actor.id:
            raise Http404
        raise ValidationError("This historical Case was already adopted. Reload My Questions.")
    revision = _create_revision(
        item=item,
        actor=actor,
        case={"title": title, "stimulus": stimulus},
        members=[_question_values(row.question) for row in source_members],
    )
    _audit(
        action="DE_MY_CASE_REVISED",
        item=item,
        actor=actor,
        before=before,
        after=item.current_revision,
    )
    return revision


def owned_items(*, actor, tenant_id, campus_id=None, course_id=None):
    scopes = authoring_scopes(user=actor, tenant_id=tenant_id, campus_id=campus_id)
    allowed_pairs = {
        (scope.campus_id, scope.course_id)
        for scope in scopes
        if course_id is None or scope.course_id == course_id
    }
    if not allowed_pairs:
        return []
    allowed = None
    for allowed_campus_id, allowed_course_id in allowed_pairs:
        pair = Q(campus_id=allowed_campus_id, course_id=allowed_course_id)
        allowed = pair if allowed is None else allowed | pair
    queryset = QuestionBankItem.objects.filter(
        allowed, owner=actor, tenant_id=tenant_id
    )
    items = list(queryset.select_related("course", "campus").order_by("course__code", "-updated_at", "id"))
    revisions = {
        (row.item_id, row.revision): row
        for row in QuestionBankRevision.objects.filter(item__in=items).prefetch_related(
            Prefetch(
                "members",
                queryset=QuestionBankCaseMember.objects.order_by("position", "id"),
            )
        )
    }
    return [(item, revisions.get((item.id, item.current_revision))) for item in items]


def historical_items(*, actor, tenant_id, campus_id=None, course_id=None):
    scopes = authoring_scopes(user=actor, tenant_id=tenant_id, campus_id=campus_id)
    allowed_pairs = {
        (scope.campus_id, scope.course_id)
        for scope in scopes
        if course_id is None or scope.course_id == course_id
    }
    if not allowed_pairs:
        return [], []
    allowed = None
    for allowed_campus_id, allowed_course_id in allowed_pairs:
        pair = Q(
            source_campus_id=allowed_campus_id,
            cycle_course__course_id=allowed_course_id,
        )
        allowed = pair if allowed is None else allowed | pair
    contributions = FacultyContribution.objects.filter(
        allowed,
        faculty_user=actor,
        status=FacultyContribution.Status.SUBMITTED,
        submitted_at__isnull=False,
        cycle_course__cycle__tenant_id=tenant_id,
    ).exclude(correction_successor__status=FacultyContribution.Status.SUBMITTED)
    if campus_id is not None:
        contributions = contributions.filter(source_campus_id=campus_id)
    if course_id is not None:
        contributions = contributions.filter(cycle_course__course_id=course_id)
    contribution_ids = contributions.values_list("id", flat=True)
    cases = list(
        ExamScenario.objects.filter(
            contribution_id__in=contribution_ids,
            adopted_bank_item__isnull=True,
        )
        .select_related("contribution__cycle_course__course", "contribution__source_campus")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=ExamScenarioMember.objects.select_related("question").order_by(
                    "position", "id"
                ),
            )
        )
        .order_by("-contribution__submitted_at", "id")
    )
    linked_question_ids = ExamScenarioMember.objects.filter(
        scenario__contribution_id__in=contribution_ids
    ).values_list("question_id", flat=True)
    questions = list(
        Question.objects.filter(
            contribution_id__in=contribution_ids,
            adopted_bank_item__isnull=True,
        )
        .exclude(pk__in=linked_question_ids)
        .select_related("contribution__cycle_course__course", "contribution__source_campus")
        .order_by("-contribution__submitted_at", "position", "id")
    )
    return cases, questions
