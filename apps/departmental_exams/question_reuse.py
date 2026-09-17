"""Owner-only historical question selection and atomic Draft copying."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils.html import strip_tags

from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService

from .contribution_authorization import ContributionAuthorizationService, ContributionConflict
from .contribution_services import QuestionPayloadService, Stage5LockService
from .duplicate_contract import (case_member_identity, pool_claims, require_clean_pool,
                                 standalone_identity, reconcile)
from .faculty_case_services import FacultyCasePolicy
from .import_sections import resolve_import_section
from .models import (ExamScenario, ExamScenarioMember, ExaminationCycle, FacultyContribution,
                     Question, QuestionBlueprintPlacement)
from .question_content import visible_text
from .scenario_content import canonicalize_scenario_content


class ReuseCapacityError(ValidationError):
    def __init__(self, excess):
        self.excess = excess
        super().__init__(f"Deselect {excess} question{'s' if excess != 1 else ''} to fit the remaining Draft slots. A Case cannot be split.")


def _period_key(cycle):
    # The model's configured choices are the authoritative exam periods.
    periods = {code: position for position, (code, _label) in enumerate(ExaminationCycle.ExamPeriod.choices)}
    if cycle.exam_period not in periods:
        return None
    return (cycle.academic_year.start_date, cycle.term.sequence_no,
            cycle.term.start_date or cycle.academic_year.start_date,
            periods[cycle.exam_period])


def _text(question):
    try:
        return visible_text(question.question_text, question.content_format)
    except ValidationError:
        # Invalid historical markup is escaped by the template and rejected on copy.
        return strip_tags(question.question_text or "")


def _token(kind, source, object_id, object_revision, members=(), marker=None):
    snapshot = [kind, source.id, source.revision, source.active_marker,
                source.submitted_at.isoformat(), object_id, object_revision, marker,
                [(row.id, row.position, row.active_marker, row.question_id,
                  row.question.revision) for row in members]]
    fingerprint = hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()[:24]
    return f"{kind}:{object_id}:{fingerprint}"


def _campus_ids(request):
    scope = getattr(request, "scope", {}) or {}
    values = scope.get("campus_ids")
    if not values:
        values = (scope.get("campus_id"),)
    return {int(value) for value in values if value is not None}


def _authorized_sources(*, request, destination):
    cycle = destination.cycle_course.cycle
    destination_key = _period_key(cycle)
    if destination_key is None:
        return []
    permitted_campuses = _campus_ids(request)
    sources = (FacultyContribution.objects.filter(
        faculty_user=request.user, status=FacultyContribution.Status.SUBMITTED,
        submitted_at__isnull=False,
        cycle_course__course_id=destination.cycle_course.course_id,
        cycle_course__cycle__tenant_id=cycle.tenant_id,
    ).exclude(cycle_course__cycle_id=cycle.id)
        .exclude(correction_successor__status=FacultyContribution.Status.SUBMITTED)
        .select_related("cycle_course__cycle__academic_year", "cycle_course__cycle__term")
        .prefetch_related("eligibility_sources"))
    result = []
    campus_allowed = {}
    for source in sources:
        source_cycle = source.cycle_course.cycle
        key = _period_key(source_cycle)
        if key is None or key >= destination_key or source.source_campus_id not in permitted_campuses:
            continue
        if not any(row.tenant_id_snapshot == cycle.tenant_id
                   and row.campus_id_snapshot == source.source_campus_id
                   for row in source.eligibility_sources.all()):
            continue
        if source.source_campus_id not in campus_allowed:
            campus_allowed[source.source_campus_id] = PermissionService.has_assigned_permission(
                request.user, "faculty_portal.access", tenant_id=cycle.tenant_id,
                campus_id=source.source_campus_id, exact_scope=True,
            )
        if not campus_allowed[source.source_campus_id]:
            continue
        result.append((key, source))
    by_cycle = defaultdict(list)
    for key, source in result:
        by_cycle[source.cycle_course.cycle_id].append((key, source))
    # Ambiguous parallel history has no authoritative latest correction chain.
    unambiguous = [rows[0] for rows in by_cycle.values() if len(rows) == 1]
    return [source for _key, source in sorted(unambiguous,
            key=lambda pair: (pair[0], pair[1].id), reverse=True)]


def _source_items(sources):
    if not sources:
        return [], 0
    source_by_id = {source.id: source for source in sources}
    questions = list(Question.objects.filter(contribution_id__in=source_by_id)
                     .select_related("contribution").order_by("contribution_id", "position", "id"))
    cases = list(ExamScenario.objects.filter(contribution_id__in=source_by_id)
                 .order_by("contribution_id", "created_at", "id"))
    question_by_id = {question.id: question for question in questions}
    memberships = list(ExamScenarioMember.objects.filter(
        Q(question_id__in=question_by_id) | Q(scenario_id__in=[case.id for case in cases]))
                       .select_related("scenario", "question").order_by("scenario_id", "position", "id"))
    by_question, by_case = defaultdict(list), defaultdict(list)
    for row in memberships:
        by_question[row.question_id].append(row)
        by_case[row.scenario_id].append(row)
    cases_by_source, questions_by_source = defaultdict(list), defaultdict(list)
    for case in cases:
        cases_by_source[case.contribution_id].append(case)
    for question in questions:
        questions_by_source[question.contribution_id].append(question)
    entries, excluded = [], 0
    for source in sources:
        owned_ids = {case.id for case in cases_by_source[source.id]}
        for case in cases_by_source[source.id]:
            rows = by_case[case.id]
            valid = (bool(rows)
                     and [row.position for row in rows] == list(range(1, len(rows) + 1))
                     and all(row.question_id in question_by_id
                             and row.question.contribution_id == source.id
                             and {link.scenario_id for link in by_question[row.question_id]} == {case.id}
                             for row in rows))
            if not valid:
                excluded += len(rows)
                continue
            entries.append({"kind": "case", "source": source, "object": case,
                            "members": rows, "size": len(rows),
                            "token": _token("c", source, case.id, case.revision, rows, case.active_marker)})
        for question in questions_by_source[source.id]:
            if by_question[question.id]:
                if any(row.scenario_id not in owned_ids for row in by_question[question.id]):
                    excluded += 1
                continue
            entries.append({"kind": "question", "source": source, "object": question,
                            "members": (), "size": 1,
                            "token": _token("q", source, question.id, question.revision)})
    return entries, excluded


def catalogue(*, request, destination, filters):
    sources = _authorized_sources(request=request, destination=destination)
    years = {str(source.cycle_course.cycle.academic_year_id): source.cycle_course.cycle.academic_year
             for source in sources}
    terms = {str(source.cycle_course.cycle.term_id): source.cycle_course.cycle.term for source in sources}
    entries, excluded = _source_items(sources)
    case_available = FacultyCasePolicy.context(
        contribution=destination,
        tenant_id=destination.cycle_course.cycle.tenant_id,
        required=False,
    ) is not None
    year, term = filters.get("academic_year", ""), filters.get("semester", "")
    period, difficulty = filters.get("exam_period", ""), filters.get("difficulty", "")
    kind, query = filters.get("content_type", ""), (filters.get("search", "") or "").strip().casefold()
    if len(query) > 200:
        raise ValidationError("Search may not exceed 200 characters.")
    if difficulty and difficulty not in Question.Difficulty.values:
        raise ValidationError("Select a valid difficulty.")
    if period and period not in ExaminationCycle.ExamPeriod.values:
        raise ValidationError("Select a valid exam period.")
    if kind and kind not in {"question", "case"}:
        raise ValidationError("Select a valid content type.")
    if year and year not in years or term and term not in terms:
        raise ValidationError("Select an available academic year and semester.")
    visible = []
    for entry in entries:
        if entry["kind"] == "case" and not case_available:
            continue
        source_cycle = entry["source"].cycle_course.cycle
        if (year and str(source_cycle.academic_year_id) != year
                or term and str(source_cycle.term_id) != term
                or period and source_cycle.exam_period != period
                or kind and entry["kind"] != kind):
            continue
        member_questions = ([row.question for row in entry["members"]] if entry["kind"] == "case"
                            else [entry["object"]])
        if difficulty and not any(question.difficulty == difficulty for question in member_questions):
            continue
        if query and not (entry["kind"] == "case" and query in entry["object"].title.casefold()) \
                and not any(query in _text(question).casefold() for question in member_questions):
            continue
        visible.append(entry)
    page = Paginator(visible, 12).get_page(filters.get("page") or 1)
    return {"page": page, "eligible_items": visible, "years": years, "terms": terms,
            "periods": ExaminationCycle.ExamPeriod.choices,
            "difficulties": Question.Difficulty.choices,
            "excluded_context_count": excluded, "filters": filters}


def require_enabled(*, tenant_id):
    if not FeatureSettingsService.is_departmental_exam_question_reuse_enabled(tenant_id=tenant_id):
        raise PermissionDenied("Question reuse is not enabled.")


def require_destination(*, request, destination):
    scope = getattr(request, "scope", {}) or {}
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id")
    require_enabled(tenant_id=tenant_id)
    ContributionAuthorizationService.require_mutable_locked(
        user=request.user, contribution=destination,
        configuration=destination.cycle_course.configuration,
        request_tenant_id=tenant_id, request_campus_id=campus_id,
    )
    ContributionAuthorizationService.require_no_active_import(contribution=destination)


def destination_sections(*, destination, tenant_id):
    context = FacultyCasePolicy.context(contribution=destination, tenant_id=tenant_id, required=False)
    if context is None:
        # Fail closed if a sectioned structure exists while its feature is unavailable.
        resolve_import_section(contribution=destination, tenant_id=tenant_id)
        return (), None
    _blueprint, sections = context
    return sections, sections[0] if len(sections) == 1 else None


def _validated_payload(question):
    payload = {field: getattr(question, field) for field in QuestionPayloadService.TEXT_FIELDS}
    payload.update(content_format=question.content_format,
                   correct_answer=question.correct_answer, difficulty=question.difficulty)
    cleaned = QuestionPayloadService.validate(payload)
    if any(cleaned[field] != payload[field] for field in cleaned):
        raise ValidationError("A selected historical question cannot be copied safely. Review that source and try again.")
    return cleaned


def copy_selected(*, request, destination_id, expected_revision, filters, selected_tokens,
                  target_section_id=None):
    if not selected_tokens or len(selected_tokens) != len(set(selected_tokens)) or len(selected_tokens) > 75:
        raise ValidationError("Select valid visible questions or whole Cases.")
    tenant_id = (getattr(request, "scope", {}) or {}).get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    initial = FacultyContribution.objects.select_related("cycle_course__cycle").filter(
        pk=destination_id, faculty_user=request.user, active_marker=1,
        cycle_course__cycle__tenant_id=tenant_id,
    ).first()
    if initial is None:
        raise Http404
    # Acquire cycle locks in one global order, including the historical source.
    initial_catalog = catalogue(request=request, destination=initial, filters=filters)
    available = {entry["token"]: entry for entry in initial_catalog["eligible_items"]}
    if any(token not in available for token in selected_tokens):
        raise ContributionConflict("The visible selection changed. Reload the reuse page and select again.")
    source_cycle_ids = {available[token]["source"].cycle_course.cycle_id for token in selected_tokens}
    with transaction.atomic():
        locked_cycles = list(ExaminationCycle.objects.select_for_update().filter(
            pk__in=source_cycle_ids | {initial.cycle_course.cycle_id}, tenant_id=tenant_id
        ).order_by("pk"))
        if len(locked_cycles) != len(source_cycle_ids | {initial.cycle_course.cycle_id}):
            raise PermissionDenied("Historical course access is unavailable.")
        _cycle, _course, configuration, destination = Stage5LockService.lock_contribution(
            contribution_id=destination_id, user=request.user, tenant_id=tenant_id)
        require_enabled(tenant_id=tenant_id)
        scope = getattr(request, "scope", {}) or {}
        ContributionAuthorizationService.require_mutable_locked(
            user=request.user, contribution=destination, configuration=configuration,
            request_tenant_id=tenant_id, request_campus_id=scope.get("campus_id"))
        ContributionAuthorizationService.require_revision(
            contribution=destination, expected_revision=expected_revision)
        ContributionAuthorizationService.require_no_active_import(contribution=destination)
        current_catalog = catalogue(request=request, destination=destination, filters=filters)
        current = {entry["token"]: entry for entry in current_catalog["eligible_items"]}
        if any(token not in current for token in selected_tokens):
            raise ContributionConflict("A source changed after this page was loaded. Reload and select again.")
        requested = set(selected_tokens)
        entries = [entry for entry in current_catalog["eligible_items"]
                   if entry["token"] in requested]
        source_ids = {entry["source"].id for entry in entries}
        list(FacultyContribution.objects.select_for_update().filter(pk__in=source_ids).order_by("pk"))
        question_ids = {row.question_id for entry in entries for row in entry["members"]}
        question_ids.update(entry["object"].id for entry in entries if entry["kind"] == "question")
        list(Question.objects.select_for_update().filter(pk__in=question_ids).order_by("pk"))
        case_ids = {entry["object"].id for entry in entries if entry["kind"] == "case"}
        list(ExamScenario.objects.select_for_update().filter(pk__in=case_ids).order_by("pk"))
        list(ExamScenarioMember.objects.select_for_update().filter(scenario_id__in=case_ids).order_by("pk"))
        questions = list(Question.objects.select_for_update().filter(contribution=destination).order_by("pk"))
        selected_count = sum(entry["size"] for entry in entries)
        remaining = destination.quota_snapshot - len(questions)
        if selected_count > remaining:
            raise ReuseCapacityError(selected_count - remaining)
        sections, sole_section = destination_sections(destination=destination, tenant_id=tenant_id)
        if sections:
            target = sole_section or FacultyCasePolicy.section_for(
                blueprint=sections[0].blueprint, sections=sections, section_id=target_section_id)
            if sole_section and target_section_id not in (None, "", str(sole_section.id), sole_section.id):
                raise ValidationError("The destination section changed. Reload and try again.")
        else:
            if target_section_id not in (None, "", 0, "0"):
                raise ValidationError("This destination does not use explicit sections.")
            target = None
        if any(entry["kind"] == "case" for entry in entries) and FacultyCasePolicy.context(
                contribution=destination, tenant_id=tenant_id, for_update=True, required=False) is None:
            raise PermissionDenied("Case authoring is unavailable for this Draft.")
        require_clean_pool(destination.cycle_course)
        _unit, claims = pool_claims(destination.cycle_course)
        seen = set(claims)
        accepted, skipped = [], []
        for entry in entries:
            member_questions = ([row.question for row in entry["members"]] if entry["kind"] == "case"
                                else [entry["object"]])
            for question in member_questions:
                _validated_payload(question)
            if entry["kind"] == "case":
                case = entry["object"]
                if case.content_format == ExamScenario.ContentFormat.RICH_HTML_V1:
                    if canonicalize_scenario_content(case.stimulus).html != case.stimulus:
                        raise ValidationError("A selected historical Case cannot be copied safely.")
                keys = [case_member_identity(question, case) for question in member_questions]
            else:
                keys = [standalone_identity(member_questions[0])]
            if len(keys) != len(set(keys)) or any(key in seen for key in keys):
                skipped.append(entry)
            else:
                accepted.append(entry)
                seen.update(keys)
        copied_question_ids = []
        for entry in accepted:
            source_case = entry["object"] if entry["kind"] == "case" else None
            new_case = None
            if source_case is not None:
                new_case = ExamScenario(
                    blueprint=(target.blueprint if target else FacultyCasePolicy.context(
                        contribution=destination, tenant_id=tenant_id, for_update=True)[0]),
                    section=target, contribution=destination,
                    title=source_case.title, stimulus=source_case.stimulus,
                    content_format=source_case.content_format,
                    created_by=request.user, updated_by=request.user,
                )
                new_case.full_clean()
                new_case.save()
            members = ([row.question for row in entry["members"]] if source_case else [entry["object"]])
            for index, source_question in enumerate(members, start=1):
                copy = Question(
                    contribution=destination, position=len(questions) + 1,
                    entry_method=Question.EntryMethod.MANUAL,
                    **_validated_payload(source_question),
                )
                copy.full_clean()
                copy.save()
                questions.append(copy)
                copied_question_ids.append(copy.id)
                if target is not None:
                    placement = QuestionBlueprintPlacement(
                        blueprint=target.blueprint, section=target, question=copy,
                        placed_by=request.user,
                    )
                    placement.full_clean()
                    placement.save()
                if new_case is not None:
                    member = ExamScenarioMember(scenario=new_case, question=copy, position=index)
                    member.full_clean()
                    member.save()
        if accepted:
            reconcile(destination.cycle_course)
            before_revision = destination.revision
            destination.revision += 1
            destination.save(update_fields=["revision", "updated_at"])
            AuditService.log_event(
                action="DE_EXAM_QUESTIONS_REUSED", portal="FACULTY",
                entity_type="FacultyContribution", entity_id=destination.id,
                actor=request.user, tenant=tenant_id, campus=destination.source_campus_id,
                metadata={
                    "destination_cycle_course_id": destination.cycle_course_id,
                    "source_contribution_ids": sorted(source_ids),
                    "source_question_ids": sorted(question_ids),
                    "source_case_ids": sorted(case_ids),
                    "copied_question_ids": copied_question_ids,
                    "copied_questions": sum(entry["size"] for entry in accepted),
                    "copied_cases": sum(entry["kind"] == "case" for entry in accepted),
                    "skipped_questions": sum(entry["size"] for entry in skipped),
                    "skipped_cases": sum(entry["kind"] == "case" for entry in skipped),
                    "revision_before": before_revision, "revision_after": destination.revision,
                }, request=request,
            )
        return {
            "copied_questions": sum(entry["size"] for entry in accepted),
            "copied_cases": sum(entry["kind"] == "case" for entry in accepted),
            "skipped_questions": sum(entry["size"] for entry in skipped),
            "skipped_cases": sum(entry["kind"] == "case" for entry in skipped),
        }
