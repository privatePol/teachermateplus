"""Admin presentation for the protected, current-cycle equivalency service."""

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .exam_units import (
    ExamCourseEquivalencyService,
    _compatibility_errors,
    configuration_compatibility_key,
    resolve_examination_unit,
)
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    FacultyContribution,
    ExaminationCycle,
)
from .services import DepartmentalExamAuthorizationService
from .setup_services import CourseSetupService
from .views import _tenant_id, portal_required


PERMISSION = DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
TOKEN_SALT = "departmental-exam-course-equivalency-ui-v1"
TOKEN_MAX_AGE = 600


class EquivalencyForm(forms.Form):
    name = forms.CharField(max_length=255, label="Shared examination name")
    members = forms.MultipleChoiceField(label="Operational course codes", widget=forms.CheckboxSelectMultiple)
    primary = forms.ChoiceField(label="Primary course code")

    def __init__(self, *args, candidates=(), editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(str(row.id), f"{row.course.code} — {row.course.title}") for row in candidates]
        self.fields["members"].choices = choices
        self.fields["primary"].choices = choices
        if editing:
            self.fields["name"].disabled = True
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs["class"] = "form-control" if field is self.fields["name"] else "form-select"

    def clean(self):
        cleaned = super().clean()
        members = cleaned.get("members") or []
        if len(members) < 2:
            self.add_error("members", "Select at least two course codes.")
        if cleaned.get("primary") and cleaned["primary"] not in members:
            self.add_error("primary", "The primary must be a selected member.")
        return cleaned


class RetirementForm(forms.Form):
    reason = forms.CharField(min_length=10, max_length=500, widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}))


def _members_allowed(*, user, cycle, members):
    return DepartmentalExamAuthorizationService.has_automatic_courses_permission(
        user=user, cycle=cycle, courses=members, permissions=(PERMISSION,),
        require_included=False,
    )


def _course_rows(members, primary_id=None):
    rows = []
    for member in members:
        snapshots = list(member.offering_snapshots.all())
        rows.append({
            "course": member,
            "primary": member.id == primary_id,
            "campuses": sorted({row.campus.name for row in snapshots}),
            "offering_count": len(snapshots),
            "contribution_count": FacultyContribution.objects.filter(
                cycle_course=member, active_marker=1,
            ).count(),
            "settings": configuration_compatibility_key(
                CourseExamConfiguration.objects.filter(cycle_course=member).first()
            ),
        })
    return rows


def _member_evidence(members):
    """Canonical identity and offering snapshot shown in a signed review."""
    evidence = []
    for member in members:
        snapshots = sorted(member.offering_snapshots.all(), key=lambda row: row.id)
        offerings = [
            [row.id, row.offering_id, row.campus_id, row.campus.name]
            for row in snapshots
        ]
        evidence.append({
            "cycle_course_id": member.id,
            "course_id": member.course_id,
            "code": member.course.code,
            "title": member.course.title,
            "offerings": offerings,
            "campuses": sorted({row[3] for row in offerings}),
            "offering_count": len(offerings),
        })
    return evidence


def _review_from_state(state, members):
    evidence = {row["cycle_course_id"]: row for row in state["member_evidence"]}
    rows = []
    for member in members:
        row = evidence[member.id]
        rows.append({
            **row,
            "primary": member.id == state["primary"],
            "contribution_count": FacultyContribution.objects.filter(
                cycle_course=member, active_marker=1,
            ).count(),
            "settings": configuration_compatibility_key(
                CourseExamConfiguration.objects.filter(cycle_course=member).first()
            ),
        })
    return {
        "token": signing.dumps(state, salt=TOKEN_SALT),
        "state": state,
        "members": rows,
    }


def _group_lock_reason(*, cycle, members):
    try:
        ExamCourseEquivalencyService._require_mutable(cycle=cycle, members=members)
        ExamCourseEquivalencyService._require_structure_ownership_mutable(
            member_ids=(member.id for member in members)
        )
    except ValidationError as exc:
        return " ".join(exc.messages)
    return ""


def _group_context(group, *, user):
    try:
        unit = resolve_examination_unit(group.primary_cycle_course, validate=False)
    except ValidationError:
        return None
    if (unit.group is None or unit.group.id != group.id
        or unit.group.cycle_id != unit.primary.cycle_id
        or unit.primary.id not in unit.member_ids or len(unit.member_ids) < 2
        or any(member.cycle_id != group.cycle_id for member in unit.members)
        or not _members_allowed(
        user=user, cycle=group.cycle, members=unit.members,
    )):
        return None
    members = tuple(
        CycleCourse.objects.filter(pk__in=unit.member_ids)
        .select_related("course")
        .prefetch_related("offering_snapshots__campus")
        .order_by("course__code", "id")
    )
    return {
        "group": group, "members": _course_rows(members, group.primary_cycle_course_id),
        "lock_reason": _group_lock_reason(cycle=group.cycle, members=members),
    }


def authorized_membership_context(*, user, cycle_course, permissions):
    """Safe, short member-code context for already authorized admin output pages."""
    if cycle_course.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        return None
    try:
        unit = resolve_examination_unit(cycle_course, validate=False)
    except ValidationError:
        return None
    if (not unit.group or unit.group.cycle_id != unit.primary.cycle_id
        or unit.primary.id not in unit.member_ids
        or len(unit.member_ids) < 2
        or any(member.cycle_id != unit.group.cycle_id for member in unit.members)
        or not DepartmentalExamAuthorizationService.has_automatic_courses_permission(
        user=user, cycle=unit.primary.cycle, courses=unit.members,
        permissions=permissions,
    )):
        return None
    return {
        "name": unit.group.name,
        "primary": unit.primary.course.code,
        "members": [member.course.code for member in unit.members],
    }


def _state(*, cycle, group, members, name, primary_id, action, actor_id, reason=""):
    affected_ids = {member.id for member in members}
    if group:
        affected_ids.update(
            ExamCourseEquivalencyMembership.objects.filter(
                group=group, active_marker=1,
            ).values_list("cycle_course_id", flat=True)
        )
    affected = list(
        CycleCourse.objects.filter(pk__in=affected_ids)
        .select_related("cycle", "course")
        .prefetch_related("offering_snapshots__campus")
        .order_by("pk")
    )
    return {
        "cycle": cycle.id, "tenant": cycle.tenant_id, "actor": actor_id,
        "group": group.id if group else None,
        "action": action, "name": name, "primary": primary_id,
        "members": sorted(member.id for member in members), "reason": reason,
        "fingerprints": [[row.id, CourseSetupService.fingerprint(row)] for row in affected],
        "member_evidence": _member_evidence(affected),
        "group_updated_at": str(group.updated_at) if group else None,
    }


def _validate_preview(*, cycle, group, members, primary_id):
    affected = {member.id: member for member in members}
    if group:
        for member in resolve_examination_unit(group.primary_cycle_course, validate=False).members:
            affected[member.id] = member
    affected_members = tuple(affected.values())
    ExamCourseEquivalencyService._require_mutable(cycle=cycle, members=affected_members)
    ExamCourseEquivalencyService._require_structure_ownership_mutable(member_ids=affected)
    ExamCourseEquivalencyService._require_single_structure_blueprint(
        member_ids=(member.id for member in members), proposed_primary_id=primary_id,
    )
    if ExamCourseEquivalencyMembership.objects.filter(
        cycle_course__in=members, active_marker=1, group__is_active=True,
    ).exclude(group=group).exists():
        raise ValidationError("A selected course already belongs to another active equivalency group.")
    configurations = {
        row.cycle_course_id: row for row in CourseExamConfiguration.objects.filter(
            cycle_course__in=members,
        )
    }
    errors = _compatibility_errors(members=members, configurations=configurations)
    if errors:
        raise ValidationError(errors)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
@transaction.atomic
def equivalent_courses_view(request, cycle_id, group_id=None):
    cycle_queryset = ExaminationCycle.objects
    if request.method == "POST":
        cycle_queryset = cycle_queryset.select_for_update()
    cycle = get_object_or_404(
        cycle_queryset,
        pk=cycle_id, tenant_id=_tenant_id(request),
        processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
    )
    DepartmentalExamAuthorizationService.require_enabled(tenant_id=cycle.tenant_id)
    if request.method == "POST":
        list(CycleCourse.objects.select_for_update().filter(cycle=cycle).order_by("pk"))
    courses = list(
        CycleCourse.objects.filter(cycle=cycle).select_related("course", "cycle")
        .prefetch_related("offering_snapshots__campus")
        .order_by("course__code", "id")
    )
    management_map = DepartmentalExamAuthorizationService.automatic_inclusion_management_map(
        user=request.user, courses=courses,
    )
    active_member_ids = set(ExamCourseEquivalencyMembership.objects.filter(
        cycle_course__in=courses, active_marker=1, group__is_active=True,
    ).values_list("cycle_course_id", flat=True))
    groups = []
    for group in ExamCourseEquivalencyGroup.objects.filter(cycle=cycle, is_active=True).select_related(
        "cycle", "primary_cycle_course__course"
    ).order_by("name", "id"):
        context = _group_context(group, user=request.user)
        if context:
            groups.append(context)
    selected_group = next((row for row in groups if row["group"].id == group_id), None)
    if group_id is not None and selected_group is None:
        raise Http404
    member_ids = {row["course"].id for row in selected_group["members"]} if selected_group else set()
    candidates = []
    for course in courses:
        if course.id in member_ids:
            candidates.append(course)
        elif (course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
              and PERMISSION in management_map[course.id]
              and course.id not in active_member_ids):
            candidates.append(course)
    if not groups and not candidates:
        raise PermissionDenied("No equivalent-course management scope is available.")
    initial = None
    if selected_group:
        initial = {
            "name": selected_group["group"].name,
            "members": [str(value) for value in member_ids],
            "primary": str(selected_group["group"].primary_cycle_course_id),
        }
    form = EquivalencyForm(candidates=candidates, editing=bool(selected_group), initial=initial)
    retirement_form = RetirementForm()
    review = None
    error = ""
    status = 200
    completed = ""
    if request.method == "POST":
        intent = request.POST.get("intent", "")
        if intent in ("review", "review_retirement"):
            if intent == "review_retirement":
                if not selected_group:
                    raise Http404
                retirement_form = RetirementForm(request.POST)
                if retirement_form.is_valid():
                    try:
                        members = tuple(row["course"] for row in selected_group["members"])
                        lock_reason = _group_lock_reason(cycle=cycle, members=members)
                        if lock_reason:
                            raise ValidationError(lock_reason)
                        state = _state(
                            cycle=cycle, group=selected_group["group"], members=members,
                            name=selected_group["group"].name,
                            primary_id=selected_group["group"].primary_cycle_course_id,
                            action="retire", actor_id=request.user.id,
                            reason=retirement_form.cleaned_data["reason"],
                        )
                        review = _review_from_state(state, members)
                    except ValidationError as exc:
                        error = " ".join(exc.messages)
            else:
                form = EquivalencyForm(request.POST, candidates=candidates, editing=bool(selected_group), initial=initial)
                if form.is_valid():
                    ids = {int(value) for value in form.cleaned_data["members"]}
                    members = tuple(course for course in candidates if course.id in ids)
                    affected = {member.id: member for member in members}
                    if selected_group:
                        affected.update((row["course"].id, row["course"]) for row in selected_group["members"])
                    if not _members_allowed(user=request.user, cycle=cycle, members=tuple(affected.values())):
                        raise PermissionDenied("Complete member-campus management authority is required.")
                    try:
                        primary_id = int(form.cleaned_data["primary"])
                        _validate_preview(
                            cycle=cycle, group=selected_group["group"] if selected_group else None,
                            members=members, primary_id=primary_id,
                        )
                        state = _state(
                            cycle=cycle, group=selected_group["group"] if selected_group else None,
                            members=members, name=form.cleaned_data["name"].strip(),
                            primary_id=primary_id,
                            actor_id=request.user.id,
                            action="replace" if selected_group else "create",
                        )
                        review = _review_from_state(state, members)
                    except ValidationError as exc:
                        error = " ".join(exc.messages)
        elif intent == "confirm":
            try:
                state = signing.loads(request.POST.get("confirmation", ""), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
            except signing.BadSignature:
                state = None
            if not state or state.get("cycle") != cycle.id or state.get("tenant") != cycle.tenant_id or state.get("group") != group_id or state.get("actor") != request.user.id:
                error = "The review expired or is invalid. Review the current course codes again."
                status = 409
            else:
                ids = set(state["members"])
                allowed_ids = {row.id for row in candidates}
                if not ids <= allowed_ids:
                    requested = tuple(course for course in courses if course.id in ids)
                    if len(requested) == len(ids) and not _members_allowed(
                        user=request.user, cycle=cycle, members=requested,
                    ):
                        raise PermissionDenied("The selected course scope changed.")
                    return render(request, "departmental_exams/admin/equivalent_courses.html", {
                        "cycle": cycle, "groups": groups, "selected_group": selected_group,
                        "candidates": candidates, "form": form,
                        "retirement_form": retirement_form, "review": None,
                        "error": "The selected course codes are no longer available. No change was saved; review the refreshed state.",
                        "completed": "",
                    }, status=409)
                members = tuple(course for course in candidates if course.id in ids)
                affected = {member.id: member for member in members}
                if selected_group:
                    affected.update((row["course"].id, row["course"]) for row in selected_group["members"])
                if not _members_allowed(user=request.user, cycle=cycle, members=tuple(affected.values())):
                    raise PermissionDenied("Complete member-campus management authority is required.")
                current = _state(
                    cycle=cycle, group=selected_group["group"] if selected_group else None,
                    members=members, name=state["name"], primary_id=state["primary"],
                    action=state["action"], actor_id=request.user.id,
                    reason=state["reason"],
                )
                if current != state:
                    error = "The group, a member, or its effective settings changed. No change was saved; review the refreshed state."
                    status = 409
                    try:
                        _validate_preview(
                            cycle=cycle, group=selected_group["group"] if selected_group else None,
                            members=members, primary_id=current["primary"],
                        )
                    except ValidationError:
                        pass
                    else:
                        review = _review_from_state(current, members)
                else:
                    try:
                        if state["action"] == "retire" and selected_group:
                            ExamCourseEquivalencyService.retire_group(
                                group_id=group_id, actor=request.user, reason=state["reason"],
                            )
                            completed = "Equivalent course group retired. Its course records remain separate and unchanged."
                        elif state["action"] == "replace" and selected_group:
                            ExamCourseEquivalencyService.replace_members(
                                group_id=group_id, primary_cycle_course_id=state["primary"],
                                member_ids=state["members"], actor=request.user,
                            )
                            completed = "Equivalent course group updated."
                        elif state["action"] == "create" and not selected_group:
                            ExamCourseEquivalencyService.create_group(
                                cycle_id=cycle.id, name=state["name"],
                                primary_cycle_course_id=state["primary"],
                                member_ids=state["members"], actor=request.user,
                            )
                            completed = "Equivalent course group created."
                        else:
                            raise ValidationError("The reviewed action no longer matches this page.")
                    except ValidationError as exc:
                        error = " ".join(exc.messages)
                        status = 409
                    else:
                        messages.success(request, completed)
                        return redirect("departmental_exams:equivalent_courses", cycle_id=cycle.id)
        else:
            error = "Choose an available review action."
            status = 400
    return render(request, "departmental_exams/admin/equivalent_courses.html", {
        "cycle": cycle, "groups": groups, "selected_group": selected_group,
        "candidates": candidates, "form": form, "retirement_form": retirement_form,
        "review": review, "error": error, "completed": completed,
    }, status=status)
