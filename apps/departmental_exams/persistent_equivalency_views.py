"""AJAX administration of saved Course equivalency and cycle exceptions."""

import json

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from apps.academics.models import Course, CourseOffering

from .models import (
    CourseEquivalencyCyclePlan,
    CourseEquivalencyDefinition,
    CycleCourse,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamBlueprint,
    ExamSection,
    ExaminationCycle,
)
from .persistent_equivalency import (
    BlueprintDispositionService,
    PersistentCourseEquivalencyService,
    can_manage_definition,
    historical_group_evidence,
    member_evidence,
    require_manage_definition,
)
from .services import CourseExamConfigurationConflict, DepartmentalExamAuthorizationService
from .views import _tenant_id, portal_required


HISTORICAL_ADOPTION_SALT = "departmental-exams-historical-adoption-v1"
EXISTING_CYCLE_APPLY_SALT = "departmental-exams-existing-cycle-apply-v1"


def _definition_rows(user, tenant_id):
    rows = []
    for definition in CourseEquivalencyDefinition.objects.filter(tenant_id=tenant_id).order_by("-is_active", "id"):
        revision = definition.revisions.select_related("primary_course").get(version=definition.current_version)
        members = list(revision.memberships.select_related("course").order_by("course__code", "course_id"))
        ids = [row.course_id for row in members]
        if not can_manage_definition(user=user, tenant_id=tenant_id, course_ids=ids):
            continue
        evidence = member_evidence(tenant_id=tenant_id, course_ids=ids)
        rows.append({
            "id": definition.id, "version": revision.version, "label": revision.label,
            "active": definition.is_active, "primary_id": revision.primary_course_id,
            "primary_code": revision.primary_course.code,
            "members": [{"id": row.course_id, "code": row.course.code,
                         "title": row.course.title,
                         "evidence": evidence[str(row.course_id)]} for row in members],
            "evidence": evidence,
            "retirement_reason": definition.retirement_reason,
        })
    return rows


def _historical_rows(user, tenant_id):
    rows = []
    groups = ExamCourseEquivalencyGroup.objects.filter(
        cycle__tenant_id=tenant_id, cycle__exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
    ).select_related("cycle", "primary_cycle_course__course").order_by("-cycle_id", "-id")
    for group in groups:
        memberships = list(ExamCourseEquivalencyMembership.objects.filter(group=group)
                           .select_related("cycle_course__course", "cycle_course__cycle")
                           .prefetch_related("cycle_course__offering_snapshots")
                           .order_by("cycle_course__course__code", "id"))
        all_members = [row.cycle_course for row in memberships]
        if not all_members or not DepartmentalExamAuthorizationService.has_automatic_courses_permission(
            user=user, cycle=group.cycle, courses=all_members,
            permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
            require_included=False,
        ):
            continue
        evidence = historical_group_evidence(group)
        rows.append({
            "group": group, "review": evidence,
            "members": evidence["memberships"],
            "adoptable": group.is_active and sum(row["active"] for row in evidence["memberships"]) >= 2,
            "review_token": signing.dumps(
                {"actor_id": user.pk, "tenant_id": tenant_id, "evidence": evidence},
                salt=HISTORICAL_ADOPTION_SALT, compress=True,
            ),
        })
    return rows


def _plan_rows(user, cycle):
    if cycle is None:
        return []
    rows = []
    for plan in CourseEquivalencyCyclePlan.objects.filter(cycle=cycle).select_related(
        "revision", "definition", "applied_group",
    ).order_by("id"):
        ids = list(plan.revision.memberships.values_list("course_id", flat=True))
        if can_manage_definition(user=user, tenant_id=cycle.tenant_id, course_ids=ids):
            rows.append(plan)
    return rows


def _payload(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValidationError({"__all__": "Send a valid JSON form."})
    if not isinstance(data, dict):
        raise ValidationError({"__all__": "Send a valid JSON form."})
    return data


def _error(exc, status=400):
    if isinstance(exc, ValidationError) and hasattr(exc, "message_dict"):
        errors = exc.message_dict
    else:
        errors = {"__all__": getattr(exc, "messages", [str(exc)])}
    return JsonResponse({"ok": False, "errors": errors}, status=status)


def _response(request, *, cycle, notice=""):
    tenant_id = _tenant_id(request)
    definitions = _definition_rows(request.user, tenant_id)
    historical = _historical_rows(request.user, tenant_id)
    plans = _plan_rows(request.user, cycle)
    planned_definition_ids = {plan.definition_id for plan in plans}
    reviewable_definition_ids = set()
    if (cycle and cycle.status == ExaminationCycle.Status.OPEN and
            cycle.processing_mode == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION):
        for definition in definitions:
            if not definition["active"] or definition["id"] in planned_definition_ids:
                continue
            course_ids = {member["id"] for member in definition["members"]}
            members = list(CycleCourse.objects.filter(cycle=cycle, course_id__in=course_ids)
                           .select_related("cycle").prefetch_related("offering_snapshots"))
            if (len(members) == len(course_ids) and
                    DepartmentalExamAuthorizationService.has_automatic_courses_permission(
                        user=request.user, cycle=cycle, courses=members,
                        permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,))):
                reviewable_definition_ids.add(definition["id"])
    html = render_to_string("departmental_exams/admin/_saved_equivalency_lists.html", {
        "definitions": definitions, "historical": historical,
        "plans": plans, "cycle": cycle,
        "reviewable_definition_ids": reviewable_definition_ids,
    }, request=request)
    return {"html": html, "definitions": definitions, "notice": notice}


def _cycle(request):
    value = request.GET.get("cycle_id") or request.POST.get("cycle_id")
    if not value:
        return None
    return get_object_or_404(ExaminationCycle, pk=value, tenant_id=_tenant_id(request))


def _require_landing_scope(request):
    tenant_id = _tenant_id(request)
    authorization = DepartmentalExamAuthorizationService
    authorization.require_enabled(tenant_id=tenant_id)
    permission = authorization.MANAGE_GENERATION_PERMISSION
    campuses = set(CourseOffering.objects.filter(tenant_id=tenant_id, is_active=True)
                   .values_list("campus_id", flat=True))
    campuses.update(Course.objects.filter(tenant_id=tenant_id)
                    .exclude(campus_id__isnull=True).values_list("campus_id", flat=True))
    if not any(authorization._has_scoped_permission(
        user=request.user, permission=permission, tenant_id=tenant_id,
        campus_id=campus_id,
    ) for campus_id in (campuses or {None})):
        raise PermissionDenied("No equivalent-course management scope is available.")


@portal_required("ADMIN")
@require_GET
def saved_equivalency_landing(request):
    _require_landing_scope(request)
    cycle = _cycle(request)
    data = _response(request, cycle=cycle)
    if request.GET.get("format") == "fragment":
        return JsonResponse(data)
    cycle_options = []
    active_definitions = [row for row in data["definitions"] if row["active"]]
    for option in ExaminationCycle.objects.filter(
            tenant_id=_tenant_id(request), status=ExaminationCycle.Status.OPEN,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
    ).select_related("academic_year", "term").order_by("-pk"):
        for definition in active_definitions:
            course_ids = {row["id"] for row in definition["members"]}
            members = list(CycleCourse.objects.filter(cycle=option, course_id__in=course_ids)
                           .select_related("cycle").prefetch_related("offering_snapshots"))
            if len(members) == len(course_ids) and DepartmentalExamAuthorizationService.has_automatic_courses_permission(
                    user=request.user, cycle=option, courses=members,
                    permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,)):
                cycle_options.append(option)
                break
    return render(request, "departmental_exams/admin/saved_equivalent_courses.html", {
        "list_html": data["html"], "cycle": cycle,
        "cycle_options": cycle_options,
        "definitions_json": data["definitions"],
    })


@portal_required("ADMIN")
@require_GET
def search_equivalent_courses(request):
    tenant_id = _tenant_id(request)
    _require_landing_scope(request)
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"courses": []})
    candidates = Course.objects.filter(tenant_id=tenant_id, is_active=True).filter(
        Q(code__icontains=query) | Q(title__icontains=query)
    ).order_by("code", "pk")
    visible = []
    for row in candidates.iterator(chunk_size=80):
        if not can_manage_definition(user=request.user, tenant_id=tenant_id,
                                     course_ids=(row.id,)):
            continue
        visible.append({"id": row.id, "code": row.code, "title": row.title,
                        "evidence": member_evidence(tenant_id=tenant_id, course_ids=(row.id,))[str(row.id)]})
        if len(visible) == 20:
            break
    return JsonResponse({"courses": visible})


@portal_required("ADMIN")
@require_POST
def save_equivalent_definition(request):
    tenant_id = _tenant_id(request)
    try:
        data = _payload(request)
        if "evidence" not in data:
            raise CourseExamConfigurationConflict("Course search state is missing. Refresh and review the group again.")
        definition = PersistentCourseEquivalencyService.save_definition(
            tenant_id=tenant_id, actor=request.user, label=data.get("label"),
            member_course_ids=data.get("members") or (),
            primary_course_id=data.get("primary"),
            definition_id=data.get("id"), expected_version=data.get("version"),
            expected_member_evidence=data.get("evidence"),
        )
    except CourseExamConfigurationConflict as exc:
        return _error(exc, 409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    cycle = _cycle(request)
    return JsonResponse({"ok": True, **_response(request, cycle=cycle,
                        notice="Saved equivalent group version " + str(definition.current_version) + ".")})


@portal_required("ADMIN")
@require_POST
def retire_equivalent_definition(request):
    try:
        data = _payload(request)
        PersistentCourseEquivalencyService.retire_definition(
            tenant_id=_tenant_id(request), actor=request.user,
            definition_id=data.get("id"), expected_version=data.get("version"),
            reason=data.get("reason"),
        )
    except CourseExamConfigurationConflict as exc:
        return _error(exc, 409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"ok": True, **_response(request, cycle=_cycle(request),
                        notice="Saved equivalent group retired. Existing cycle snapshots remain.")})


@portal_required("ADMIN")
@require_POST
def adopt_historical_equivalency(request):
    tenant_id = _tenant_id(request)
    try:
        data = _payload(request)
        try:
            state = signing.loads(data.get("review_token") or "", salt=HISTORICAL_ADOPTION_SALT, max_age=1200)
        except signing.BadSignature as exc:
            raise CourseExamConfigurationConflict("The historical review expired or changed. Review it again.") from exc
        if (state.get("actor_id") != request.user.pk or state.get("tenant_id") != tenant_id or
                state.get("evidence", {}).get("group_id") != data.get("group_id")):
            raise CourseExamConfigurationConflict("The historical review is for a different operator or group.")
        group = get_object_or_404(ExamCourseEquivalencyGroup.objects.select_related(
            "cycle", "primary_cycle_course__course",
        ), pk=data.get("group_id"), cycle__tenant_id=tenant_id,
            cycle__exam_period=ExaminationCycle.ExamPeriod.MIDTERM)
        rows = list(ExamCourseEquivalencyMembership.objects.filter(group=group)
                    .select_related("cycle_course__cycle").prefetch_related("cycle_course__offering_snapshots"))
        all_members = [row.cycle_course for row in rows]
        DepartmentalExamAuthorizationService.require_automatic_courses_permission(
            user=request.user, cycle=group.cycle, courses=all_members,
            permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
            require_included=False,
        )
        current = historical_group_evidence(group)
        if not group.is_active or current != state["evidence"]:
            raise CourseExamConfigurationConflict("The historical group's primary or members changed. Review current details again.")
        members = [row for row in current["memberships"] if row["active"]]
        if len(members) < 2:
            raise CourseExamConfigurationConflict("The historical active membership changed. Review it again.")
        from .persistent_equivalency import _reason
        _reason(data.get("reason"))
        definition = PersistentCourseEquivalencyService.save_definition(
            tenant_id=tenant_id, actor=request.user,
            label=current["label"],
            member_course_ids=[row["course_id"] for row in members],
            primary_course_id=current["primary_course_id"],
            adoption_group_id=group.id,
            adoption_reason=data.get("reason"),
            adoption_expected_evidence=state["evidence"],
        )
    except CourseExamConfigurationConflict as exc:
        return JsonResponse({"ok": False, "errors": {"__all__": [str(exc)]},
                             **_response(request, cycle=_cycle(request))}, status=409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"ok": True, **_response(request, cycle=_cycle(request),
                        notice="Historical group reviewed and adopted as saved group " + str(definition.id) + ".")})


@portal_required("ADMIN")
@require_POST
def except_equivalency_plan(request):
    try:
        data = _payload(request)
        cycle = get_object_or_404(ExaminationCycle, pk=data.get("cycle_id"), tenant_id=_tenant_id(request))
        PersistentCourseEquivalencyService.record_exception(
            cycle_id=cycle.id, plan_id=data.get("plan_id"), actor=request.user,
            reason=data.get("reason"),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"ok": True, **_response(request, cycle=cycle,
                        notice="Reasoned exception recorded for this cycle.")})


@portal_required("ADMIN")
@require_POST
def apply_equivalency_plan(request):
    try:
        data = _payload(request)
        cycle = get_object_or_404(ExaminationCycle, pk=data.get("cycle_id"), tenant_id=_tenant_id(request))
        plan = get_object_or_404(CourseEquivalencyCyclePlan.objects.select_related("revision"),
                                 pk=data.get("plan_id"), cycle=cycle)
        ids = list(plan.revision.memberships.values_list("course_id", flat=True))
        require_manage_definition(user=request.user, tenant_id=cycle.tenant_id, course_ids=ids)
        members = list(CycleCourse.objects.filter(cycle=cycle, course_id__in=ids)
                       .select_related("cycle").prefetch_related("offering_snapshots"))
        if len(members) < 2:
            raise ValidationError("Fewer than two saved members are present in this cycle.")
        DepartmentalExamAuthorizationService.require_automatic_courses_permission(
            user=request.user, cycle=cycle, courses=members,
            permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
            require_included=False,
        )
        updated = PersistentCourseEquivalencyService.ensure_for_course(
            cycle_course=members[0], actor=request.user,
        )
        if updated is None or updated.id != plan.id:
            raise CourseExamConfigurationConflict("The cycle plan changed. Refresh and review it again.")
    except CourseExamConfigurationConflict as exc:
        return _error(exc, 409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"ok": True, **_response(request, cycle=cycle,
                        notice="Cycle plan status: " + updated.get_status_display() + ".")})


@portal_required("ADMIN")
@require_GET
def review_existing_cycle_application(request):
    cycle = _cycle(request)
    if cycle is None:
        raise Http404
    definition = get_object_or_404(CourseEquivalencyDefinition,
                                   pk=request.GET.get("definition_id"), tenant_id=cycle.tenant_id)
    try:
        evidence = PersistentCourseEquivalencyService.existing_cycle_review(
            cycle=cycle, definition=definition, actor=request.user)
    except CourseExamConfigurationConflict as exc:
        return _error(exc, 409)
    except ValidationError as exc:
        return _error(exc, 409)
    token = signing.dumps({"actor_id": request.user.pk, "tenant_id": cycle.tenant_id,
                           "cycle_id": cycle.pk, "definition_id": definition.pk,
                           "evidence": evidence}, salt=EXISTING_CYCLE_APPLY_SALT, compress=True)
    return JsonResponse({"ok": True, "evidence": evidence, "review_token": token})


@portal_required("ADMIN")
@require_POST
def apply_existing_cycle_application(request):
    try:
        data = _payload(request)
        cycle = get_object_or_404(ExaminationCycle, pk=data.get("cycle_id"), tenant_id=_tenant_id(request))
        try:
            state = signing.loads(data.get("review_token") or "",
                                  salt=EXISTING_CYCLE_APPLY_SALT, max_age=1200)
        except signing.BadSignature as exc:
            raise CourseExamConfigurationConflict("The review expired. Review current facts again.") from exc
        if (state.get("actor_id") != request.user.pk or
                state.get("tenant_id") != cycle.tenant_id or
                state.get("cycle_id") != cycle.pk or
                state.get("definition_id") != data.get("definition_id")):
            raise CourseExamConfigurationConflict("The review belongs to a different actor, cycle, or group.")
        PersistentCourseEquivalencyService.apply_to_existing_cycle(
            cycle_id=cycle.pk, definition_id=state["definition_id"],
            actor=request.user, expected_review=state["evidence"])
    except (CourseExamConfigurationConflict, ValidationError, ValueError, TypeError, KeyError) as exc:
        return _error(exc, 409)
    return JsonResponse({"ok": True, **_response(request, cycle=cycle,
                        notice="Saved group pinned and applied to this cycle.")})


@portal_required("ADMIN")
@require_GET
def review_blueprint_recovery(request):
    cycle = _cycle(request)
    if cycle is None:
        raise Http404
    plan = get_object_or_404(CourseEquivalencyCyclePlan.objects.select_related("revision"),
                             pk=request.GET.get("plan_id"), cycle=cycle)
    ids = list(plan.revision.memberships.values_list("course_id", flat=True))
    members = list(CycleCourse.objects.filter(cycle=cycle, course_id__in=ids)
                   .select_related("cycle", "course").prefetch_related("offering_snapshots"))
    if len(members) != 2 or len(ids) != 2:
        return _error(ValidationError("Blueprint recovery requires exactly two present saved members."), 409)
    DepartmentalExamAuthorizationService.require_automatic_courses_permission(
        user=request.user, cycle=cycle, courses=members,
        permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
        require_included=False,
    )
    primary = next((row for row in members if row.course_id == plan.revision.primary_course_id), None)
    if primary is None:
        return _error(ValidationError("The saved primary is unavailable in this cycle."), 409)
    secondary = next(row for row in members if row.pk != primary.pk)
    blueprints = {row.cycle_course_id: row for row in ExamBlueprint.objects.filter(
        cycle_course__in=members,
    )}
    if set(blueprints) != {primary.pk, secondary.pk}:
        return _error(ValidationError("Both members must have separate blueprints."), 409)
    def description(member):
        blueprint = blueprints[member.pk]
        return {"cycle_course_id": member.pk, "code": member.course.code,
                "blueprint_id": blueprint.pk, "revision": blueprint.revision,
                "digest": BlueprintDispositionService.structure_digest(blueprint),
                "mode": blueprint.mode,
                "sections": list(ExamSection.objects.filter(blueprint=blueprint)
                                 .order_by("display_order", "pk")
                                 .values("display_order", "title", "instructions", "item_quota"))}
    return JsonResponse({"primary": description(primary),
                         "secondary": description(secondary), "plan_id": plan.pk})


@portal_required("ADMIN")
@require_POST
def retain_secondary_blueprint(request):
    try:
        data = _payload(request)
        cycle = get_object_or_404(ExaminationCycle, pk=data.get("cycle_id"), tenant_id=_tenant_id(request))
        plan = get_object_or_404(CourseEquivalencyCyclePlan.objects.select_related("revision"),
                                 pk=data.get("plan_id"), cycle=cycle)
        primary = get_object_or_404(CycleCourse, pk=data.get("primary_cycle_course_id"),
                                    cycle=cycle, course_id=plan.revision.primary_course_id)
        secondary = get_object_or_404(CycleCourse, pk=data.get("secondary_cycle_course_id"),
                                      cycle=cycle, course_id__in=plan.revision.memberships.values_list("course_id", flat=True))
        if secondary.pk == primary.pk:
            raise ValidationError("Choose a distinct secondary member.")
        if not data.get("primary_digest") or not data.get("secondary_digest"):
            raise CourseExamConfigurationConflict("Blueprint review evidence is missing. Review current structures again.")
        BlueprintDispositionService.retain_secondary(
            cycle_id=cycle.id, primary_cycle_course_id=primary.pk,
            secondary_cycle_course_id=secondary.pk, actor=request.user,
            reason=data.get("reason"),
            expected_primary_revision=int(data.get("primary_revision")),
            expected_secondary_revision=int(data.get("secondary_revision")),
            expected_primary_digest=data.get("primary_digest"),
            expected_secondary_digest=data.get("secondary_digest"),
            plan_id=plan.id,
        )
        PersistentCourseEquivalencyService.ensure_for_course(cycle_course=primary, actor=request.user)
    except CourseExamConfigurationConflict as exc:
        return _error(exc, 409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"ok": True, **_response(request, cycle=cycle,
                        notice="Secondary blueprint retained as historical; cycle plan revalidated.")})
