from calendar import monthrange
from datetime import date
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import CourseOffering
from apps.admin_portal.services import AdminScopeService
from apps.core.decorators import permission_required, portal_required
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.tenants.models import Campus, Tenant

from .academic_integration import AcademicCoverageIntegrationService
from .academic_integration import AcademicOfferingSourceIntegrationService
from .forms import (
    ChecklistPreparationForm,
    AttendanceClosureForm,
    CoverageForm,
    CoverageAdoptionForm,
    CoverageReconciliationForm,
    CutoffPublicationForm,
    CutoffScopeForm,
    CreateCheckingRoundForm,
    DailyEncodingForm,
    ExceptionEncodingForm,
    FacultyAttendanceFilterForm,
    MonthlyArrangementForm,
    MonthlyChecklistForm,
    RecurringCombinedClassForm,
    MeetingGenerationForm,
    ReconciliationForm,
    SavedRouteForm,
    ScheduleCorrectionForm,
    SourceChangeReconciliationForm,
    SubstitutionForm,
)
from .models import (
    AttendanceResult,
    AttendanceCutoffPublication,
    CheckingRound,
    CoverageReconciliation,
    FacultyCoverage,
    MeetingReconciliation,
    MonthlyChecklistArrangement,
    OfferingAttendanceSourceChange,
    RecurringCombinedClass,
    SavedCheckerRoute,
    ScheduleSlot,
    ScheduleVersion,
    TeachingMeeting,
)
from .observations import (
    FACULTY_ATTRIBUTION_MEETING,
    FACULTY_ATTRIBUTION_ADOPTION,
    FACULTY_ATTRIBUTION_ASSIGNMENT,
    FACULTY_ATTRIBUTION_RESULT,
    FACULTY_ATTRIBUTION_SUBSTITUTION,
    AttendanceResultService,
    CheckingRoundService,
    ObservationService,
    StaleAttendanceReview,
    resolve_attendance_faculty,
    require_confirmable_meeting_faculty,
    prime_meeting_readiness,
)
from .permissions import (
    CORRECT_PERMISSION,
    ENCODE_PERMISSION,
    MANAGE_COVERAGE_PERMISSION,
    MANAGE_MEETINGS_PERMISSION,
    MANAGE_ROUTES_PERMISSION,
    MANAGE_SCHEDULES_PERMISSION,
    MANAGE_SUBSTITUTIONS_PERMISSION,
    PRINT_PERMISSION,
    PUBLISH_PERMISSION,
    RECONCILE_PERMISSION,
    VIEW_PERMISSION,
    can_faculty_view_own_attendance,
    require_attendance_permission,
)
from .route_services import SavedRouteService, ordered_meetings
from .monthly_checklists import DAY_GROUPS, MonthlyArrangementService, build_monthly_rows
from .checklist_export import checklist_xlsx, print_settings
from .combined_classes import RecurringCombinedClassService
from .selectors import unresolved_meetings
from .services import CoverageService, MeetingService, ReconciliationService, ScheduleService, SubstitutionService
from .daily_encoding import (
    deduplicate_daily_issues,
    expected_daily_occurrences,
    inspect_daily_occurrences,
    prepare_daily_encoding,
    resolve_occurrence_faculty,
)
from .cutoffs import faculty_published_entries, publish_cutoff, published_tardiness_summary, review_cutoff
from .closures import save_closure


def _scope(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id")
    campus_id = scope.get("campus_id")
    department_ids = list(scope.get("department_ids") or [])
    if scope.get("department_id") and scope["department_id"] not in department_ids:
        department_ids.append(scope["department_id"])
    if not tenant_id or not campus_id:
        raise PermissionDenied("Select a tenant and campus scope first.")
    return tenant_id, campus_id, department_ids


def _page_permission(request, code, department_ids):
    department_id = department_ids[0] if department_ids else getattr(request.user, "default_department_id", None)
    require_attendance_permission(
        user=request.user,
        permission_code=code,
        tenant_id=request.scope["tenant_id"],
        campus_id=request.scope["campus_id"],
        department_id=department_id,
    )


def _can_page(request, code, department_ids):
    try:
        _page_permission(request, code, department_ids)
        return True
    except PermissionDenied:
        return False


def _can_all_departments(request, code, department_ids):
    targets = set(department_ids) or {getattr(request.user, "default_department_id", None)}
    try:
        for department_id in targets:
            require_attendance_permission(
                user=request.user, permission_code=code, tenant_id=request.scope["tenant_id"],
                campus_id=request.scope["campus_id"], department_id=department_id,
            )
        return True
    except PermissionDenied:
        return False


def _meeting_queryset(tenant_id, campus_id, department_ids):
    from .college_sync import retired_meeting_ids
    queryset = TeachingMeeting.objects.exclude(pk__in=retired_meeting_ids()).filter(tenant_id=tenant_id, campus_id=campus_id)
    if department_ids:
        queryset = queryset.filter(department_id__in=department_ids)
    return queryset.select_related("schedule_slot", "faculty_user", "coverage").prefetch_related("offering_links")


def _offering_queryset(request, tenant_id, campus_id, department_ids):
    queryset = AdminScopeService.scoped_course_offerings(request).filter(tenant_id=tenant_id, campus_id=campus_id)
    if department_ids:
        queryset = queryset.filter(department_id__in=department_ids)
    return queryset.select_related("course", "section", "department").order_by("course__code", "section__code")


def _faculty_queryset(request):
    return User.objects.filter(id__in=AdminScopeService.scoped_faculty_users(request)).order_by(
        "last_name", "first_name", "username"
    )


@portal_required("ADMIN")
def setup_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    query = {
        key: request.GET[key]
        for key in ("academic_year", "term", "month", "day_group")
        if request.GET.get(key)
    }
    target = reverse("faculty_attendance:checklist")
    if "weekdays" in request.GET:
        query["weekdays"] = request.GET.getlist("weekdays")
    if query:
        from urllib.parse import urlencode

        target = f"{target}?{urlencode(query, doseq=True)}"
    return redirect(target)


@portal_required("ADMIN")
def corrections_view(request):
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, MANAGE_SCHEDULES_PERMISSION, department_ids)
    offerings = _offering_queryset(request, tenant_id, campus_id, department_ids)
    meetings = _meeting_queryset(tenant_id, campus_id, department_ids)
    slots = ScheduleSlot.objects.filter(schedule_version__offering__in=offerings).select_related(
        "schedule_version__offering__course", "schedule_version__offering__section"
    )
    faculty = _faculty_queryset(request)
    forms = {
        "schedule_form": ScheduleCorrectionForm(offering_queryset=offerings),
        "coverage_form": CoverageForm(offering_queryset=offerings, faculty_queryset=faculty),
        "meeting_form": MeetingGenerationForm(slot_queryset=slots, offering_queryset=offerings),
        "substitution_form": SubstitutionForm(meeting_queryset=meetings, faculty_queryset=faculty),
    }
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "schedule":
                form = ScheduleCorrectionForm(request.POST, offering_queryset=offerings)
                forms["schedule_form"] = form
                if form.is_valid():
                    ScheduleService.create_version(
                        actor=request.user,
                        offering=form.cleaned_data["offering"],
                        effective_from=form.cleaned_data["effective_from"],
                        corrected_slots=[{
                            "weekday": form.cleaned_data["weekday"],
                            "start_time": form.cleaned_data["start_time"],
                            "end_time": form.cleaned_data["end_time"],
                            "building": form.cleaned_data["building"],
                            "floor": form.cleaned_data["floor"],
                            "room": form.cleaned_data["room"],
                            "room_text": form.cleaned_data["room"],
                        }],
                        correction_reason=form.cleaned_data["correction_reason"],
                        supersede_current=True,
                    )
                    messages.success(request, "Structured schedule version saved.")
                    return redirect("faculty_attendance:corrections")
            elif action == "coverage":
                form = CoverageForm(request.POST, offering_queryset=offerings, faculty_queryset=faculty)
                forms["coverage_form"] = form
                if form.is_valid():
                    CoverageService.create(
                        actor=request.user,
                        offering=form.cleaned_data["offering"],
                        faculty_user=form.cleaned_data["faculty_user"],
                        effective_from=form.cleaned_data["effective_from"],
                        effective_until=form.cleaned_data["effective_until"],
                        reason=form.cleaned_data["reason"],
                        supersede_current=True,
                    )
                    messages.success(request, "Faculty coverage saved.")
                    return redirect("faculty_attendance:corrections")
            elif action == "meeting":
                form = MeetingGenerationForm(request.POST, slot_queryset=slots, offering_queryset=offerings)
                forms["meeting_form"] = form
                if form.is_valid():
                    MeetingService.generate(
                        actor=request.user,
                        schedule_slot=form.cleaned_data["schedule_slot"],
                        meeting_date=form.cleaned_data["meeting_date"],
                        offerings=list(form.cleaned_data["combined_offerings"]),
                    )
                    messages.success(request, "Teaching meeting generated with explicit linked sections.")
                    return redirect("faculty_attendance:corrections")
            elif action == "substitution":
                messages.error(request, "Use Faculty Assignments to replace faculty, including temporary replacements and returns.")
                return redirect("faculty_attendance:corrections")
        except (ValidationError, PermissionDenied) as exc:
            target = forms.get(f"{action}_form")
            if target:
                target.add_error(None, exc)
    versions = ScheduleVersion.objects.filter(offering__in=offerings).prefetch_related("slots").order_by("offering_id", "-version_number")
    coverages = FacultyCoverage.objects.filter(offering__in=offerings).select_related("faculty_user", "offering__course")
    return render(request, "faculty_attendance/corrections.html", {
        **forms,
        "offerings": offerings,
        "versions": versions,
        "coverages": coverages,
        "unresolved": unresolved_meetings(tenant_id=tenant_id, campus_id=campus_id).filter(department_id__in=department_ids) if department_ids else unresolved_meetings(tenant_id=tenant_id, campus_id=campus_id),
        "coverage_reconciliations": CoverageReconciliation.objects.filter(tenant_id=tenant_id, campus_id=campus_id, status="PENDING"),
        "can_manage_schedules": True,
        "can_manage_coverage": _can_page(request, MANAGE_COVERAGE_PERMISSION, department_ids),
        "can_manage_meetings": _can_page(request, MANAGE_MEETINGS_PERMISSION, department_ids),
        "can_manage_substitutions": _can_page(request, MANAGE_SUBSTITUTIONS_PERMISSION, department_ids),
    })


@portal_required("ADMIN")
def checklist_view(request):
    if request.method == "GET" and "start_date" not in request.GET:
        return monthly_checklist_view(request)
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, ENCODE_PERMISSION, department_ids)
    routes = SavedCheckerRoute.objects.filter(tenant_id=tenant_id, campus_id=campus_id, owner=request.user, is_active=True)
    data = request.POST if request.method == "POST" else request.GET
    form_class = CreateCheckingRoundForm if request.method == "POST" else ChecklistPreparationForm
    form = form_class(data or None, route_queryset=routes)
    meetings = []
    if form.is_valid():
        start = form.cleaned_data["start_date"]
        end = form.cleaned_data["end_date"]
        queryset = _meeting_queryset(tenant_id, campus_id, department_ids).filter(meeting_date__range=(start, end))
        meetings = ordered_meetings(queryset, form.cleaned_data.get("route"))
        if request.method == "POST":
            selected = form.cleaned_data["meeting_ids"]
            selected_map = {row.pk: row for row in meetings}
            if set(selected) - set(selected_map):
                form.add_error("meeting_ids", "Meeting selection is stale or outside the active scope.")
            else:
                try:
                    checking_round = CheckingRoundService.create(
                        actor=request.user,
                        meetings=[selected_map[row_id] for row_id in selected],
                        checking_date=start,
                        checking_end_date=end,
                        label=form.cleaned_data["label"],
                        saved_route=form.cleaned_data.get("route"),
                    )
                    return redirect("faculty_attendance:round", public_id=checking_round.public_id)
                except (ValidationError, PermissionDenied) as exc:
                    form.add_error(None, exc)
    return render(request, "faculty_attendance/legacy_checklist.html", {
        "form": form,
        "meetings": meetings,
        "route_form": SavedRouteForm(),
        "routes": routes,
        "selected_route": form.cleaned_data.get("route") if form.is_bound and form.is_valid() else None,
        "can_manage_routes": _can_all_departments(request, MANAGE_ROUTES_PERMISSION, department_ids),
        "can_print": _can_all_departments(request, PRINT_PERMISSION, department_ids),
    })


CHECKLIST_SESSION_KEY = "faculty_attendance_checklist_settings"
CHECKLIST_SETTING_FIELDS = ("academic_year", "term", "month", "weekdays", "paper", "orientation", "text_size")


def _checklist_request_data(request, *, tenant_id, campus_id, academic_years, terms):
    """Restore only still-authorized selections from this authenticated session."""
    saved = request.session.get(CHECKLIST_SESSION_KEY, {})
    remembered = {}
    if isinstance(saved, dict) and saved.get("scope") == [tenant_id, campus_id]:
        validator = MonthlyChecklistForm(academic_year_queryset=academic_years, term_queryset=terms)
        values = saved.get("values", {})
        if isinstance(values, dict):
            for name in CHECKLIST_SETTING_FIELDS:
                if name not in values:
                    continue
                try:
                    validator.fields[name].clean(values[name])
                except (ValidationError, ValueError, TypeError):
                    continue
                remembered[name] = values[name]
            year = remembered.get("academic_year")
            if not year or not terms.filter(pk=remembered.get("term"), academic_year_id=year).exists():
                remembered.pop("term", None)
    data = QueryDict(mutable=True)
    for name, value in remembered.items():
        if name == "weekdays":
            data.setlist(name, [str(day) for day in value])
        else:
            data[name] = str(value)
    # A changed year cannot inherit an old semester. An explicitly supplied
    # semester is still checked by the form against the new year and scope.
    if "academic_year" in request.GET and request.GET["academic_year"] != str(remembered.get("academic_year", "")):
        if "term" not in request.GET:
            data.pop("term", None)
    if "day_group" in request.GET and "weekdays" not in request.GET:
        data.pop("weekdays", None)
    for name in (*CHECKLIST_SETTING_FIELDS, "day_group"):
        if name in request.GET:
            data.setlist(name, request.GET.getlist(name))
    return data or None


def _checklist_session_values(form):
    values = {}
    for name in CHECKLIST_SETTING_FIELDS:
        if name not in form.cleaned_data:
            continue
        value = form.cleaned_data[name]
        if name in ("academic_year", "term"):
            value = value.pk if value else None
        elif name == "month":
            value = value.strftime("%Y-%m") if value else None
        elif name == "weekdays":
            value = sorted(set(value))
        else:
            value = value or {"paper": "A4", "orientation": "landscape", "text_size": "11"}[name]
        if value not in (None, "", []):
            values[name] = value
    return values


def _monthly_checklist_context(request, *, require_print=False):
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, PRINT_PERMISSION if require_print else VIEW_PERMISSION, department_ids)
    academic_years = AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id)
    terms = AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id)
    form = MonthlyChecklistForm(
        _checklist_request_data(request, tenant_id=tenant_id, campus_id=campus_id,
                                academic_years=academic_years, terms=terms),
        academic_year_queryset=academic_years,
        term_queryset=terms,
        initial={"month": timezone.localdate().replace(day=1), "day_group": "MW"},
    )
    context = {
        "monthly_form": form,
        "rows": [],
        "corrections": [],
        "date_columns": [],
        "day_groups": DAY_GROUPS,
        "can_manage_routes": _can_page(request, MANAGE_ROUTES_PERMISSION, department_ids),
        "can_encode": _can_page(request, ENCODE_PERMISSION, department_ids),
        "can_manage_combined": _can_all_departments(request, MANAGE_MEETINGS_PERMISSION, department_ids),
        "can_manage_corrections": _can_all_departments(request, MANAGE_SCHEDULES_PERMISSION, department_ids),
        "can_initialize_coverage": _can_page(request, MANAGE_COVERAGE_PERMISSION, department_ids)
            and _can_page(request, RECONCILE_PERMISSION, department_ids),
        "tenant_name": Tenant.objects.filter(pk=tenant_id).values_list("name", flat=True).first() or str(tenant_id),
        "campus_name": Campus.objects.filter(pk=campus_id).values_list("name", flat=True).first() or str(campus_id),
        "filter_query": request.GET.urlencode(),
    }
    valid = form.is_valid()
    if form.is_bound:
        # Only cleaned values survive a validation error or a scope change.
        # Django logout flushes this session, including these selections.
        request.session[CHECKLIST_SESSION_KEY] = {
            "scope": [tenant_id, campus_id], "values": _checklist_session_values(form),
        }
    elif CHECKLIST_SESSION_KEY in request.session:
        request.session.pop(CHECKLIST_SESSION_KEY)
    if not valid:
        return context
    academic_year = form.cleaned_data["academic_year"]
    term = form.cleaned_data["term"]
    selected_month = form.cleaned_data["month"]
    day_group = form.cleaned_data["day_group"]
    query = QueryDict(mutable=True)
    for name, value in _checklist_session_values(form).items():
        if name != "weekdays":
            query[name] = str(value)
    query["day_group"] = day_group
    query["scope_tenant_id"] = str(tenant_id)
    query["scope_campus_id"] = str(campus_id)
    context["filter_query"] = query.urlencode()
    arrangement = MonthlyChecklistArrangement.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, academic_year=academic_year,
        term=term, owner=request.user, day_group=day_group,
    ).prefetch_related("entries").first()
    offerings = list(_offering_queryset(request, tenant_id, campus_id, department_ids).filter(
        academic_year=academic_year, term=term, is_active=True,
    ).prefetch_related("faculty_assignments__faculty_user", "attendance_coverages__faculty_user"))
    for department_id in {offering.department_id for offering in offerings}:
        require_attendance_permission(
            user=request.user, permission_code=PRINT_PERMISSION if require_print else VIEW_PERMISSION,
            tenant_id=tenant_id, campus_id=campus_id, department_id=department_id,
        )
    offering_department_ids = {offering.department_id for offering in offerings}
    context["can_manage_routes"] = _can_all_departments(request, MANAGE_ROUTES_PERMISSION, offering_department_ids)
    context["can_manage_combined"] = _can_all_departments(request, MANAGE_MEETINGS_PERMISSION, offering_department_ids)
    context["can_manage_corrections"] = _can_all_departments(
        request, MANAGE_SCHEDULES_PERMISSION, offering_department_ids
    )
    context["can_encode"] = _can_all_departments(request, ENCODE_PERMISSION, offering_department_ids)
    context["can_print"] = _can_all_departments(request, PRINT_PERMISSION, offering_department_ids)
    rows, corrections, date_columns = build_monthly_rows(
        offerings=offerings, year=selected_month.year, month=selected_month.month,
        day_group=day_group, arrangement=arrangement,
        printable_only=require_print,
        combined_classes=_scoped_combined_classes(
            tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year.pk,
            term_id=term.pk, offering_ids={row.pk for row in offerings},
        ),
        dated_meetings=list(_meeting_queryset(tenant_id, campus_id, department_ids).filter(
            meeting_date__range=(
                date(selected_month.year, selected_month.month, 1),
                date(selected_month.year, selected_month.month, monthrange(selected_month.year, selected_month.month)[1]),
            ), offering_links__offering__academic_year=academic_year,
            offering_links__offering__term=term,
        ).select_related("substitution__substitute_faculty").distinct()),
    )
    settings = print_settings(form.cleaned_data)
    batch_size = settings["dates_per_sheet"]
    context.update({
        "rows": rows, "corrections": corrections, "date_columns": date_columns,
        "date_batches": [date_columns[start:start + batch_size] for start in range(0, len(date_columns), batch_size)],
        "print_settings": settings,
        "can_initialize_coverage": _can_all_departments(request, MANAGE_COVERAGE_PERMISSION, offering_department_ids)
            and _can_all_departments(request, RECONCILE_PERMISSION, offering_department_ids),
        "arrangement": arrangement, "selected_academic_year": academic_year,
        "selected_term": term, "selected_month": selected_month,
        "selected_day_group": day_group, "selected_day_group_label": DAY_GROUPS[day_group][1],
    })
    return context


def _scoped_combined_classes(*, tenant_id, campus_id, academic_year_id, term_id, offering_ids):
    groups = RecurringCombinedClass.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year_id, term_id=term_id,
    ).prefetch_related("offering_links__offering__course", "offering_links__offering__section")
    return [group for group in groups if {link.offering_id for link in group.offering_links.all()} <= offering_ids]


def _daily_preview_rows(occurrences, issues, route=None, *, assignment_evidence=None):
    """Pair each preview occurrence with every relevant read-only blocker."""
    rows = []
    route_positions = {}
    if route is not None:
        entries = route.entries.select_related("schedule_slot__schedule_version__offering").order_by("position")
        for entry in entries:
            slot = entry.schedule_slot
            route_positions[(slot.schedule_version.offering_id, slot.weekday, slot.start_time, slot.end_time)] = entry.position
    for occurrence in occurrences:
        offering_ids = {item.pk for item in occurrence.linked_offerings}
        occurrence_issues = [
            issue
            for issue in issues
            if issue.meeting_date in (None, occurrence.meeting_date)
            and offering_ids.intersection(item.pk for item in issue.affected_offerings)
        ]
        faculty = resolve_occurrence_faculty(occurrence, assignment_evidence=assignment_evidence)
        if faculty is None:
            faculty_name = "Faculty unresolved"
        else:
            faculty_name = _last_first_name(faculty)
        positions = [
            route_positions[(offering.pk, occurrence.meeting_date.weekday(), occurrence.start_time, occurrence.end_time)]
            for offering in occurrence.linked_offerings
            if (offering.pk, occurrence.meeting_date.weekday(), occurrence.start_time, occurrence.end_time) in route_positions
        ]
        rows.append({
            "occurrence": occurrence,
            "issues": occurrence_issues,
            "faculty": faculty,
            "faculty_name": faculty_name,
            "route_position": min(positions) if positions else None,
            "start_time": occurrence.start_time,
        })
    if route is not None:
        rows.sort(key=lambda row: (
            row["start_time"],
            0 if row["route_position"] is not None else 1,
            row["route_position"] or 0,
            (row["occurrence"].source_room or "").strip().casefold(),
            row["occurrence"].end_time,
        ))
    else:
        rows.sort(key=lambda row: (
            row["start_time"],
            (row["occurrence"].source_room or "").strip().casefold(),
            row["occurrence"].end_time,
        ))
    return rows


@portal_required("ADMIN")
def monthly_checklist_view(request):
    return render(request, "faculty_attendance/checklist.html", _monthly_checklist_context(request))


@portal_required("ADMIN")
def daily_encoding_view(request):
    """One checker-facing entry from a monthly row to internal dated rounds."""
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, VIEW_PERMISSION, department_ids)
    academic_years = AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id)
    terms = AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id)
    routes = SavedCheckerRoute.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, owner=request.user, is_active=True
    )
    data = request.POST if request.method == "POST" else request.GET
    form = DailyEncodingForm(
        data or None,
        academic_year_queryset=academic_years,
        term_queryset=terms,
        route_queryset=routes,
    )
    context = {"form": form, "rounds": [], "issues": [], "occurrences": []}
    if not form.is_valid():
        return render(request, "faculty_attendance/daily_encoding.html", context)
    academic_year = form.cleaned_data["academic_year"]
    term = form.cleaned_data["term"]
    meeting_date = form.cleaned_data["meeting_date"]
    offerings = list(
        _offering_queryset(request, tenant_id, campus_id, department_ids)
        .filter(academic_year=academic_year, term=term, is_active=True)
        .prefetch_related("attendance_source_changes")
    )
    for department_id in {offering.department_id for offering in offerings}:
        require_attendance_permission(
            user=request.user,
            permission_code=VIEW_PERMISSION,
            tenant_id=tenant_id,
            campus_id=campus_id,
            department_id=department_id,
        )
    combined = _scoped_combined_classes(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year.pk,
        term_id=term.pk,
        offering_ids={item.pk for item in offerings},
    )
    occurrences, issues = expected_daily_occurrences(
        offerings=offerings,
        term=term,
        start_date=meeting_date,
        end_date=meeting_date,
        combined_classes=combined,
    )
    from .assignment_attribution import AssignmentEvidence
    assignment_evidence = AssignmentEvidence(offerings)
    issues = deduplicate_daily_issues([*issues, *inspect_daily_occurrences(occurrences, assignment_evidence=assignment_evidence)])
    context.update(
        {
            "selected_academic_year": academic_year,
            "selected_term": term,
            "meeting_date": meeting_date,
            "tenant_name": Tenant.objects.filter(pk=tenant_id).values_list("name", flat=True).first() or str(tenant_id),
            "campus_name": Campus.objects.filter(pk=campus_id).values_list("name", flat=True).first() or str(campus_id),
            "occurrences": occurrences,
            "issues": issues,
            "can_manage_corrections": _can_all_departments(request, MANAGE_SCHEDULES_PERMISSION, department_ids),
            "can_manage_combined": _can_all_departments(request, MANAGE_MEETINGS_PERMISSION, department_ids),
            "can_reconcile": _can_all_departments(request, RECONCILE_PERMISSION, department_ids),
            "can_view_faculty_assignments": PermissionService.has_permission(request.user, "faculty_assignments.read", tenant_id=tenant_id, campus_id=campus_id),
            "can_view_course_offerings": PermissionService.has_permission(request.user, "offerings.view", tenant_id=tenant_id, campus_id=campus_id),
        }
    )
    if request.method == "POST":
        try:
            _page_permission(request, ENCODE_PERMISSION, department_ids)
            rounds, post_issues = prepare_daily_encoding(
                actor=request.user,
                offerings=offerings,
                academic_year=academic_year,
                term=term,
                meeting_date=meeting_date,
                saved_route=form.cleaned_data.get("route"),
            )
            context["issues"] = deduplicate_daily_issues([*issues, *post_issues])
            context["rounds"] = rounds
            if rounds and not post_issues:
                messages.success(request, "Daily attendance list is ready. Continue with each listed department.")
            if not post_issues:
                from urllib.parse import urlencode

                return redirect(
                    f"{reverse('faculty_attendance:daily_encoding')}?{urlencode({'academic_year': academic_year.pk, 'term': term.pk, 'meeting_date': meeting_date.isoformat(), 'route': form.cleaned_data.get('route').pk if form.cleaned_data.get('route') else ''})}"
                )
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
    context["rounds"] = context["rounds"] or list(
        CheckingRound.objects.filter(
            tenant_id=tenant_id,
            campus_id=campus_id,
            academic_year=academic_year,
            term=term,
            daily_occurrence_date=meeting_date,
        ).filter(Q(department_id__in=department_ids) if department_ids else Q()).order_by("department_id")
    )
    context["occurrence_rows"] = _daily_preview_rows(occurrences, context["issues"], form.cleaned_data.get("route"),
                                                   assignment_evidence=assignment_evidence)
    return render(request, "faculty_attendance/daily_encoding.html", context)


@portal_required("ADMIN")
def cutoff_review_view(request):
    tenant_id, campus_id, _department_ids = _scope(request)
    academic_years = AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id)
    terms = AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id)
    action = request.POST.get("action") if request.method == "POST" else None
    form_class = CutoffPublicationForm if request.method == "POST" and action != "closure" else CutoffScopeForm
    form = form_class(
        request.POST if request.method == "POST" else request.GET or None,
        academic_year_queryset=academic_years,
        term_queryset=terms,
    )
    context = {"form": form, "review": None, "publication_form": None, "closure_form": None}
    if not form.is_valid():
        return render(request, "faculty_attendance/cutoff_review.html", context)
    values = form.cleaned_data
    try:
        if request.method == "POST" and action != "closure":
            publication = publish_cutoff(
                actor=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                academic_year=values["academic_year"],
                term=values["term"],
                start_date=values["start_date"],
                end_date=values["end_date"],
                expected_fingerprint=values["review_fingerprint"],
                submission_key=values["submission_key"],
                publication_reason=values["publication_reason"],
            )
            messages.success(request, f"Campus cutoff published as version {publication.version}. Faculty can now see their own entries.")
            from urllib.parse import urlencode

            return redirect(
                f"{reverse('faculty_attendance:cutoff_review')}?{urlencode({'academic_year': values['academic_year'].pk, 'term': values['term'].pk, 'start_date': values['start_date'].isoformat(), 'end_date': values['end_date'].isoformat()})}"
            )
        review = review_cutoff(
            actor=request.user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            academic_year=values["academic_year"],
            term=values["term"],
            start_date=values["start_date"],
            end_date=values["end_date"],
        )
        if action == "closure":
            closure_form = AttendanceClosureForm(request.POST)
            context["closure_form"] = closure_form
            if closure_form.is_valid():
                allowed_ids = {item.meeting.pk for item in review.records} | {
                    item.meeting_id for item in review.blockers
                    if item.code in {"UNVERIFIED_ATTENDANCE", "CLOSURE_REVIEW_PENDING"} and item.meeting_id
                }
                meeting = TeachingMeeting.objects.filter(
                    pk=closure_form.cleaned_data["meeting_id"], tenant_id=tenant_id, campus_id=campus_id,
                    meeting_date__range=(values["start_date"], values["end_date"]),
                    offering_links__offering__academic_year=values["academic_year"],
                    offering_links__offering__term=values["term"],
                ).distinct().first()
                if meeting is None or meeting.pk not in allowed_ids:
                    closure_form.add_error(None, "Choose an expected dated meeting; resolve its source or faculty coverage first.")
                else:
                    try:
                        saved = save_closure(
                            actor=request.user, meeting=meeting,
                            status=closure_form.cleaned_data["status"],
                            kind=closure_form.cleaned_data["kind"],
                            pay_basis=closure_form.cleaned_data["pay_basis"],
                            reason=closure_form.cleaned_data["reason"],
                            expected_revision=closure_form.cleaned_data["expected_revision"],
                        )
                    except ValidationError as exc:
                        closure_form.add_error(None, exc)
                    else:
                        messages.success(request, f"Dated closure decision saved as revision {saved.revision}.")
                        from urllib.parse import urlencode
                        return redirect(
                            f"{reverse('faculty_attendance:cutoff_review')}?{urlencode({'academic_year': values['academic_year'].pk, 'term': values['term'].pk, 'start_date': values['start_date'].isoformat(), 'end_date': values['end_date'].isoformat()})}"
                        )
    except PermissionDenied:
        return HttpResponseForbidden("Complete-campus cutoff publication authority is required.")
    except (ValidationError, StaleAttendanceReview) as exc:
        form.add_error(None, exc)
        return render(request, "faculty_attendance/cutoff_review.html", context)
    context.update(
        {
            "review": review,
            "selected_academic_year": values["academic_year"],
            "selected_term": values["term"],
            "tenant_name": Tenant.objects.filter(pk=tenant_id).values_list("name", flat=True).first() or str(tenant_id),
            "campus_name": Campus.objects.filter(pk=campus_id).values_list("name", flat=True).first() or str(campus_id),
            "publication_form": CutoffPublicationForm(
                initial={
                    "academic_year": values["academic_year"].pk,
                    "term": values["term"].pk,
                    "start_date": values["start_date"],
                    "end_date": values["end_date"],
                    "review_fingerprint": review.fingerprint,
                    "submission_key": str(uuid4()),
                },
                academic_year_queryset=academic_years,
                term_queryset=terms,
            ),
        }
    )
    # College routine encoding uses manually checked Present/A/N findings.
    # Retain existing closure services/history, without a routine closure action.
    context.update(_cutoff_display_context(request, review))
    return render(request, "faculty_attendance/cutoff_review.html", context)


def _cutoff_display_context(request, review):
    """Labels/links only; use the review's scoped dated evidence, not live faculty."""
    from urllib.parse import urlencode
    labels = {
        "MEETING_RECONCILIATION_PENDING": "Faculty or historical attendance needs review",
        "UNVERIFIED_ATTENDANCE": "Class attendance has not been verified",
        "UNMATERIALIZED_OCCURRENCE": "Class has not been prepared or encoded",
        "COVERAGE_CHANGE_PENDING": "Assigned faculty needs review",
        "FACULTY_ATTRIBUTION_MISSING": "Faculty attribution is missing",
        "FACULTY_ATTRIBUTION_CONFLICT": "Dated faculty and saved attendance disagree",
        "CLOSURE_REVIEW_PENDING": "Saved no-class decision needs review",
        "RECORDED_MEETING_SOURCE_MISMATCH": "Recorded class and schedule need review",
    }
    can_view = {}
    def daily_link(day, department_id):
        if not day or not department_id:
            return ""
        if department_id not in can_view:
            can_view[department_id] = _can_page(request, VIEW_PERMISSION, [department_id])
        if not can_view[department_id]:
            return ""
        return reverse("faculty_attendance:daily_encoding") + "?" + urlencode({
            "academic_year": review.academic_year_id, "term": review.term_id, "meeting_date": day.isoformat()})
    blockers = [{"blocker": b, "title": labels.get(b.code, b.code.replace("_", " ").capitalize()),
        "details": b.details, "url": daily_link(b.details.get("date"), b.details.get("department_id"))}
        for b in review.blockers]
    records = []
    for record in review.records:
        sections = record.meeting.sections_snapshot
        courses = list(dict.fromkeys((s.get("course_code", ""), s.get("course_title", "")) for s in sections))
        records.append({"record": record, "courses": courses, "sections": sections,
            "url": daily_link(record.meeting.meeting_date, record.meeting.department_id),
            "history": list(record.result.history.order_by("revision")) if record.result else [],
            "closure_history": sorted(record.meeting.closure_decisions.all(), key=lambda c: c.revision)})
    return {"cutoff_blockers": blockers, "supporting_records": records}


@portal_required("ADMIN")
def monthly_arrangement_save_view(request):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, MANAGE_ROUTES_PERMISSION, department_ids)
    form = MonthlyArrangementForm(request.POST)
    query = {
        "academic_year": request.POST.get("academic_year_id", ""),
        "term": request.POST.get("term_id", ""),
        "month": request.POST.get("month", ""),
        "day_group": request.POST.get("day_group", ""),
        "paper": request.POST.get("paper", "A4"),
        "orientation": request.POST.get("orientation", "landscape"),
        "text_size": request.POST.get("text_size", "11"),
    }
    if form.is_valid():
        try:
            MonthlyArrangementService.save(
                actor=request.user, tenant_id=tenant_id, campus_id=campus_id,
                academic_year_id=form.cleaned_data["academic_year_id"], term_id=form.cleaned_data["term_id"],
                day_group=form.cleaned_data["day_group"], tokens=form.cleaned_data["row_tokens"],
                expected_revision=form.cleaned_data.get("expected_revision"),
            )
            messages.success(request, "Classroom arrangement saved.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    else:
        messages.error(request, "Arrangement could not be saved. Review the selected scope and reload if needed.")
    from urllib.parse import urlencode
    return redirect(f"{reverse('faculty_attendance:checklist')}?{urlencode(query)}")


@portal_required("ADMIN")
def monthly_print_view(request):
    context = _monthly_checklist_context(request, require_print=True)
    if not context["monthly_form"].is_valid():
        messages.error(request, "Select a valid academic scope, month, and day group before printing.")
        return redirect("faculty_attendance:checklist")
    context["printed_at"] = timezone.localtime()
    return render(request, "faculty_attendance/monthly_print_checklist.html", context)


@portal_required("ADMIN")
def monthly_export_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    context = _monthly_checklist_context(request, require_print=True)
    if not context["monthly_form"].is_valid():
        return HttpResponse("Select valid checklist and print settings before exporting.", status=400)
    response = HttpResponse(checklist_xlsx(context), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="monthly-attendance-checklist.xlsx"'
    return response


@portal_required("ADMIN")
def combined_classes_view(request):
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, MANAGE_MEETINGS_PERMISSION, department_ids)
    academic_years = AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id)
    terms = AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id)
    offerings = list(_offering_queryset(request, tenant_id, campus_id, department_ids).filter(is_active=True).select_related("academic_year", "term"))
    form = RecurringCombinedClassForm(
        request.POST or None, academic_year_queryset=academic_years, term_queryset=terms,
        offering_queryset=CourseOffering.objects.filter(pk__in=[row.pk for row in offerings]).order_by("course__code", "section__code"),
    )
    if request.method == "POST" and form.is_valid():
        try:
            RecurringCombinedClassService.create(
                actor=request.user, tenant_id=tenant_id, campus_id=campus_id,
                academic_year_id=form.cleaned_data["academic_year"].pk, term_id=form.cleaned_data["term"].pk,
                offering_ids=list(form.cleaned_data["offerings"].values_list("pk", flat=True)),
                weekday=form.cleaned_data["weekday"], start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"], effective_from=form.cleaned_data["effective_from"],
                effective_until=form.cleaned_data["effective_until"], reason=form.cleaned_data["reason"],
            )
            messages.success(request, "Sections taught together saved for the selected recurring meeting.")
            return redirect("faculty_attendance:combined_classes")
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
    offering_ids = {row.pk for row in offerings}
    groups = RecurringCombinedClass.objects.filter(tenant_id=tenant_id, campus_id=campus_id).select_related(
        "academic_year", "term", "created_by"
    ).prefetch_related("offering_links__offering__course", "offering_links__offering__section")
    groups = [group for group in groups if {link.offering_id for link in group.offering_links.all()} <= offering_ids]
    return render(request, "faculty_attendance/combined_classes.html", {"form": form, "groups": groups})


@portal_required("ADMIN")
def route_save_view(request, route_id=None):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    tenant_id, campus_id, _department_ids = _scope(request)
    route = get_object_or_404(SavedCheckerRoute, pk=route_id, owner=request.user) if route_id else None
    form = SavedRouteForm(request.POST)
    if form.is_valid():
        try:
            SavedRouteService.save(
                actor=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                name=form.cleaned_data["name"],
                schedule_slot_ids=form.cleaned_data["schedule_slot_ids"],
                expected_revision=form.cleaned_data["expected_revision"],
                route=route,
            )
            messages.success(request, "Checker route order saved.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    else:
        messages.error(request, "Route order could not be saved; reload and review the entries.")
    return redirect(request.POST.get("return_url") or reverse("faculty_attendance:checklist"))


@portal_required("ADMIN")
def route_reset_view(request, route_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    route = get_object_or_404(SavedCheckerRoute, pk=route_id, owner=request.user)
    try:
        SavedRouteService.reset(actor=request.user, route=route, expected_revision=int(request.POST.get("expected_revision", 0)))
        messages.success(request, "Saved route reset to deterministic default ordering.")
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("faculty_attendance:checklist")


def _round_for_request(request, public_id, permission_code):
    tenant_id, campus_id, department_ids = _scope(request)
    checking_round = get_object_or_404(
        CheckingRound.objects.select_related("campus__tenant", "department", "saved_route", "academic_year", "term"),
        public_id=public_id,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    if department_ids and checking_round.department_id not in department_ids:
        raise Http404
    require_attendance_permission(
        user=request.user,
        permission_code=permission_code,
        tenant_id=tenant_id,
        campus_id=campus_id,
        department_id=checking_round.department_id,
    )
    return checking_round


def _exception_values_from_result(result):
    """Return the saved exception values without treating blank as a finding."""
    values = {
        "absence_code": "", "missed_hours": "", "missed_periods": "", "late_flag": False,
        "late_minutes": "", "early_flag": False, "early_minutes": "", "reason": "",
    }
    if not result or result.status != AttendanceResult.Status.EXCEPTION:
        return values
    for finding in result.findings_snapshot:
        if finding["finding_type"] == "ABSENCE":
            values["absence_code"] = finding.get("notice_status", "")
            values["missed_hours"] = finding.get("missed_hours") or ""
            values["missed_periods"] = finding.get("missed_periods") or ""
        elif finding["finding_type"] == "LATE":
            values["late_flag"] = True
            values["late_minutes"] = finding.get("minutes", "")
        elif finding["finding_type"] == "EARLY":
            values["early_flag"] = True
            values["early_minutes"] = finding.get("minutes", "")
    values["reason"] = result.correction_reason
    return values


def _legacy_period_absence_findings(result):
    if not result or result.status != AttendanceResult.Status.EXCEPTION:
        return []
    absences = [item for item in result.findings_snapshot if item.get("finding_type") == "ABSENCE"]
    if not any(item.get("missed_periods") not in (None, "") for item in absences):
        return []
    return absences


def _result_summary(result):
    if not result:
        return []
    if result.status == AttendanceResult.Status.PRESENT:
        return ["Present confirmed"]
    summary = []
    if result.absent_without_notice_hours:
        summary.append(f"A — {result.absent_without_notice_hours} missed hours")
    if result.absent_with_notice_hours:
        summary.append(f"N — {result.absent_with_notice_hours} missed hours")
    if result.missed_periods:
        summary.append(f"{result.missed_periods} missed periods")
    if result.late_flag:
        summary.append(f"L — {result.late_minutes} late minutes")
    if result.early_flag:
        summary.append(f"E — {result.early_minutes} early-dismissal minutes")
    return summary or [result.get_status_display()]


def _last_first_name(user):
    name = ", ".join(part.strip() for part in (user.last_name, user.first_name) if part and part.strip())
    return name or (user.full_name or "").strip() or user.username


def _round_rows_context(request, checking_round, bound_form=None, selected_present_rows=None):
    from .college_sync import retired_meeting_ids
    selected_present_rows = selected_present_rows or set()
    rows = list(
        checking_round.manifest_rows.exclude(meeting_id__in=retired_meeting_ids()).select_related(
            "meeting",
            "meeting__faculty_user",
            "meeting__substitution",
            "meeting__substitution__substitute_faculty",
            "meeting__attendance_result",
            "meeting__attendance_result__faculty_user",
            "meeting__coverage_adoption__faculty_user",
        )
        .prefetch_related("meeting__attendance_result__history")
        .order_by("sequence")
    )
    counts = {"unverified": 0, "present": 0, "exception": 0}
    visible_rows, unresolved_rows = [], []
    can_correct = _can_page(request, CORRECT_PERMISSION, [checking_round.department_id])
    prime_meeting_readiness([row.meeting for row in rows])
    for row in rows:
        result = getattr(row.meeting, "attendance_result", None)
        row.attributed_faculty, attribution_source = resolve_attendance_faculty(row.meeting, result=result)
        if row.attributed_faculty is None:
            unresolved_rows.append(row)
            continue
        visible_rows.append(row)
        local_start = timezone.localtime(row.meeting.starts_at)
        local_end = timezone.localtime(row.meeting.ends_at)
        row.index_group_key = (local_start.time(), local_end.time())
        row.filter_time_key = f"{local_start.time().isoformat()}/{local_end.time().isoformat()}"
        row.filter_faculty_id = str(row.attributed_faculty.pk)
        row.filter_faculty_name = _last_first_name(row.attributed_faculty)
        result_revision = result.revision if result else 0
        row.present_row_value = f"{row.meeting_id}:{result_revision}"
        row.expected_result_revision = result_revision
        row.default_submission_key = f"{checking_round.public_id}-{row.meeting_id}-{result_revision}"
        try:
            require_confirmable_meeting_faculty(row.meeting)
            row.coverage_confirmable = True
            row.coverage_message = ""
        except ValidationError as exc:
            row.coverage_confirmable = False
            row.coverage_message = "; ".join(exc.messages)
        status = result.status if result else (AttendanceResult.Status.PRESENT
            if row.coverage_confirmable else AttendanceResult.Status.UNVERIFIED)
        if result and status == "UNVERIFIED" and not (result.findings_snapshot or result.late_flag or result.early_flag
                or result.absent_without_notice_hours or result.absent_with_notice_hours or result.missed_periods) and row.coverage_confirmable:
            status = "PRESENT"
        row.display_status = status
        counts[status.lower()] += 1
        row.can_confirm_present = row.coverage_confirmable and (
            result is None or result.status == AttendanceResult.Status.UNVERIFIED
        ) and status == AttendanceResult.Status.PRESENT
        row.present_selected = row.present_row_value in selected_present_rows
        row.present_note = request.POST.get(f"present_note_{row.meeting_id}", "") if request.method == "POST" else ""
        row.editing_is_correction = bool(result and result.revision)
        row.has_saved_absence = bool(result and any(f.get("finding_type") == "ABSENCE" for f in result.findings_snapshot))
        row.can_correct_result = can_correct
        row.result_summary = (_result_summary(result) if result and result.status != AttendanceResult.Status.UNVERIFIED
            else ["Present by default; no exception recorded"] if status == AttendanceResult.Status.PRESENT
            else _result_summary(result))
        row.faculty_attribution_label = {
            FACULTY_ATTRIBUTION_RESULT: "Saved attendance attribution",
            FACULTY_ATTRIBUTION_SUBSTITUTION: "Explicit meeting substitute",
            FACULTY_ATTRIBUTION_MEETING: "Dated faculty coverage",
            FACULTY_ATTRIBUTION_ADOPTION: "Checker-approved dated coverage (original history retained)",
            FACULTY_ATTRIBUTION_ASSIGNMENT: "Existing accepted faculty assignment",
        }.get(attribution_source, "")
        row.exception_values = _exception_values_from_result(result)
        row.legacy_period_absence_findings = _legacy_period_absence_findings(result)
        row.has_legacy_period_absence = bool(row.legacy_period_absence_findings)
        row.is_bound_exception = False
        if bound_form is not None and bound_form.is_bound and str(row.meeting_id) == str(bound_form.data.get("meeting_id", "")):
            row.is_bound_exception = True
            row.exception_values.update({
                "late_flag": bool(bound_form.data.get("late_flag")),
                "late_minutes": bound_form.data.get("late_minutes", ""),
                "early_flag": bool(bound_form.data.get("early_flag")),
                "early_minutes": bound_form.data.get("early_minutes", ""),
                "reason": bound_form.data.get("reason", ""),
                "expected_revision": bound_form.data.get("expected_revision", result_revision),
                "submission_key": bound_form.data.get("submission_key", ""),
            })
            if not row.has_legacy_period_absence:
                row.exception_values.update({
                    "absence_code": bound_form.data.get("absence_code", ""),
                    "missed_hours": bound_form.data.get("missed_hours", ""),
                })
    time_options = {}
    faculty_options = {}
    for row in visible_rows:
        time_key = row.filter_time_key
        if time_key not in time_options:
            start = timezone.localtime(row.meeting.starts_at) if timezone.is_aware(row.meeting.starts_at) else row.meeting.starts_at
            end = timezone.localtime(row.meeting.ends_at) if timezone.is_aware(row.meeting.ends_at) else row.meeting.ends_at
            time_options[time_key] = (
                start.time().isoformat(),
                f"{start.strftime('%I:%M %p').lstrip('0')} – {end.strftime('%I:%M %p').lstrip('0')}",
            )
        faculty_options[row.filter_faculty_id] = row.filter_faculty_name
    ordered_times = sorted(time_options.items(), key=lambda item: item[1][0])
    ordered_faculty = sorted(faculty_options.items(), key=lambda item: (item[1].casefold(), item[0]))
    return visible_rows, counts, unresolved_rows, ordered_times, ordered_faculty


def _round_row_json_response(request, checking_round, meeting_id, *, bound_form=None, message, status=200):
    rows, counts, _unresolved, _times, _faculty = _round_rows_context(request, checking_round, bound_form=bound_form)
    row = next((item for item in rows if item.meeting_id == meeting_id), None)
    if row is None:
        return JsonResponse({"ok": False, "message": "That class is not on this frozen daily list."}, status=400)
    result = getattr(row.meeting, "attendance_result", None)
    row_html = render_to_string(
        "faculty_attendance/_round_meeting_row.html",
        {"checking_round": checking_round, "row": row, "exception_form": bound_form or ExceptionEncodingForm()},
        request=request,
    )
    return JsonResponse({
        "ok": status < 400,
        "message": message,
        "row_html": row_html,
        "counts": counts,
        "result": {
            "status": row.display_status,
            "revision": result.revision if result else 0,
            "summary": row.result_summary,
        },
    }, status=status)


@portal_required("ADMIN")
def round_view(request, public_id):
    checking_round = _round_for_request(request, public_id, ENCODE_PERMISSION)
    bound_form = None
    selected_present_rows = set()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "exception":
                try:
                    posted_meeting_id = int(request.POST.get("meeting_id", 0))
                except (TypeError, ValueError):
                    posted_meeting_id = 0
                manifest_row = checking_round.manifest_rows.select_related("meeting__attendance_result").filter(
                    meeting_id=posted_meeting_id
                ).first()
                existing = getattr(manifest_row.meeting, "attendance_result", None) if manifest_row else None
                legacy_period_absences = _legacy_period_absence_findings(existing)
                bound_form = ExceptionEncodingForm(
                    request.POST,
                    preserve_legacy_period_absence=bool(legacy_period_absences),
                    correcting_saved_result=bool(existing and existing.revision),
                    saved_absence=bool(existing and any(f.get("finding_type") == "ABSENCE" for f in existing.findings_snapshot)),
                )
                if bound_form.is_valid():
                    cleaned = bound_form.cleaned_data
                    if manifest_row is None or manifest_row.meeting_id != cleaned["meeting_id"]:
                        raise ValidationError("That class is not on this frozen daily list.")
                    if existing and existing.revision:
                        require_attendance_permission(
                            user=request.user, permission_code=CORRECT_PERMISSION,
                            tenant_id=checking_round.tenant_id, campus_id=checking_round.campus_id,
                            department_id=checking_round.department_id,
                        )
                        if existing.revision != cleaned["expected_revision"]:
                            raise StaleAttendanceReview("Attendance result changed; reload and review before saving.")
                    # Recheck dated eligibility before recording any observation,
                    # including direct requests for cards omitted from the page.
                    require_confirmable_meeting_faculty(manifest_row.meeting)
                    findings = []
                    if legacy_period_absences:
                        findings.extend(legacy_period_absences)
                    elif cleaned["absence_code"]:
                        findings.append({"finding_type": "ABSENCE", "segment_key": "checker-entry", "notice_status": cleaned["absence_code"], "missed_hours": cleaned["missed_hours"], "missed_periods": None})
                    if cleaned["late_flag"]:
                        findings.append({"finding_type": "LATE", "segment_key": "arrival", "minutes": cleaned["late_minutes"]})
                    if cleaned["early_flag"]:
                        findings.append({"finding_type": "EARLY", "segment_key": "dismissal", "minutes": cleaned["early_minutes"]})
                    observation = ObservationService.record(
                        actor=request.user,
                        checking_round=checking_round,
                        meeting_id=cleaned["meeting_id"],
                        manifest_revision=checking_round.manifest_revision,
                        submission_key=request.POST.get("submission_key") or str(uuid4()),
                        findings=findings,
                        note=cleaned["reason"],
                        preserve_legacy_period_absence=bool(legacy_period_absences),
                        correction_revision=cleaned["expected_revision"] if existing and existing.revision else None,
                    )
                    if observation is None:
                        if is_ajax:
                            return _round_row_json_response(request, checking_round, cleaned["meeting_id"],
                                message="No exception recorded. This assigned class remains Present by default.")
                        messages.success(request, "No exception recorded. This assigned class remains Present by default.")
                        return redirect("faculty_attendance:round", public_id=public_id)
                    else:
                        result = AttendanceResultService.select_observation(
                            actor=request.user,
                            observation=observation,
                            expected_revision=cleaned["expected_revision"],
                            reason=cleaned["reason"],
                        )
                        if is_ajax:
                            return _round_row_json_response(
                                request, checking_round, cleaned["meeting_id"],
                                message="Finding saved." if result.revision == 1 else f"Correction saved as revision {result.revision}.",
                            )
                        messages.success(request, "Attendance exception saved with revision history.")
                        return redirect("faculty_attendance:round", public_id=public_id)
                if is_ajax:
                    return _round_row_json_response(
                        request, checking_round, int(request.POST.get("meeting_id", 0)), bound_form=bound_form,
                        message="Save was not completed. Review the highlighted fields.", status=400,
                    )
            elif action == "confirm_present":
                reviewed = []
                for value in request.POST.getlist("present_rows"):
                    meeting_id, revision = value.split(":", 1)
                    meeting_id = int(meeting_id)
                    reviewed.append({
                        "meeting_id": meeting_id,
                        "result_revision": int(revision),
                        "note": request.POST.get(f"present_note_{meeting_id}", ""),
                    })
                    selected_present_rows.add(value)
                outcome = AttendanceResultService.confirm_present(
                    actor=request.user,
                    checking_round=checking_round,
                    manifest_revision=int(request.POST.get("manifest_revision", 0)),
                    reviewed_rows=reviewed,
                    submission_key=request.POST.get("submission_key", ""),
                )
                if is_ajax:
                    rows, counts, _hidden, _times, _faculty = _round_rows_context(request, checking_round)
                    changed = set(outcome["confirmed_meeting_ids"])
                    return JsonResponse({"ok": True, "message": "Selected reviewed classes confirmed present. Saved findings were preserved; attendance was not published or finalized.",
                        "counts": counts, "rows": [{"meeting_id": row.meeting_id, "row_html": render_to_string(
                            "faculty_attendance/_round_meeting_row.html", {"checking_round": checking_round,
                            "row": row, "exception_form": ExceptionEncodingForm()}, request=request)}
                            for row in rows if row.meeting_id in changed], "replayed": outcome.get("replayed", False),
                        "submission_key": str(uuid4())})
                messages.success(request, "Selected reviewed classes confirmed present. Saved findings were preserved; attendance was not published or finalized.")
                return redirect("faculty_attendance:round", public_id=public_id)
        except (ValidationError, PermissionDenied, ValueError, StaleAttendanceReview) as exc:
            if is_ajax and action == "confirm_present":
                return JsonResponse({"ok": False, "message": f"Confirmation was not completed: {exc}. Reload and review the current statuses before retrying."},
                    status=409 if isinstance(exc, StaleAttendanceReview) else 403 if isinstance(exc, PermissionDenied) else 400)
            if is_ajax and action == "exception":
                return _round_row_json_response(
                    request, checking_round, int(request.POST.get("meeting_id", 0)), bound_form=bound_form,
                    message=str(exc), status=409 if isinstance(exc, StaleAttendanceReview) else 403 if isinstance(exc, PermissionDenied) else 400,
                )
            if bound_form:
                bound_form.add_error(None, exc)
            else:
                messages.error(request, f"Attendance changed or is unresolved: {exc}. Reload and review.")
    rows, counts, unresolved_rows, time_options, faculty_options = _round_rows_context(
        request, checking_round, bound_form, selected_present_rows
    )
    return render(request, "faculty_attendance/round.html", {
        "checking_round": checking_round,
        "rows": rows,
        "counts": counts,
        "index_rows": sorted(rows, key=lambda row: (*row.index_group_key, row.sequence)),
        "unresolved_rows": unresolved_rows,
        "time_filter_options": time_options,
        "faculty_filter_options": faculty_options,
        "exception_form": bound_form or ExceptionEncodingForm(),
        "selected_present_rows": selected_present_rows,
        "present_submission_key": request.POST.get("submission_key") or str(uuid4()),
        "can_print": _can_page(request, PRINT_PERMISSION, [checking_round.department_id]),
    })


@portal_required("ADMIN")
def print_view(request, public_id):
    checking_round = _round_for_request(request, public_id, PRINT_PERMISSION)
    rows = checking_round.manifest_rows.select_related("meeting", "meeting__faculty_user").order_by("sequence")
    return render(request, "faculty_attendance/print_checklist.html", {"checking_round": checking_round, "rows": rows, "printed_at": timezone.localtime()})


@portal_required("ADMIN")
def reconciliation_view(request):
    tenant_id, campus_id, department_ids = _scope(request)
    _page_permission(request, RECONCILE_PERMISSION, department_ids)
    coverage_items = CoverageReconciliation.objects.filter(tenant_id=tenant_id, campus_id=campus_id, status="PENDING").select_related("offering__course", "prior_faculty", "proposed_faculty")
    meeting_items = MeetingReconciliation.objects.filter(meeting__tenant_id=tenant_id, meeting__campus_id=campus_id, status="PENDING").select_related("meeting", "meeting__attendance_result")
    source_items = OfferingAttendanceSourceChange.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, status=OfferingAttendanceSourceChange.Status.PENDING
    ).select_related("offering__course", "offering__section")
    if department_ids:
        coverage_items = coverage_items.filter(department_id__in=department_ids)
        meeting_items = meeting_items.filter(meeting__department_id__in=department_ids)
        source_items = source_items.filter(department_id__in=department_ids)
    adoption_items = _meeting_queryset(tenant_id, campus_id, department_ids).filter(
        unresolved_coverage=True, faculty_user__isnull=True, coverage_adoption__isnull=True,
        substitution__isnull=True,
    ).filter(Q(attendance_result__isnull=True) | Q(attendance_result__revision=0))
    adoption_form = None
    adoption_error = False
    if request.method == "POST":
        kind = request.POST.get("kind")
        try:
            if kind == "adoption":
                _page_permission(request, MANAGE_COVERAGE_PERMISSION, department_ids)
                adoption_form = CoverageAdoptionForm(request.POST)
                if adoption_form.is_valid():
                    item = get_object_or_404(adoption_items, pk=adoption_form.cleaned_data["meeting_id"])
                    from .coverage_initialization import CoverageAdoptionService
                    CoverageAdoptionService.adopt(actor=request.user, meeting=item, reason=adoption_form.cleaned_data["reason"])
                    messages.success(request, "Verified dated coverage adopted; original meeting and round snapshots retained.")
                    return redirect("faculty_attendance:reconciliation")
                adoption_error = True
            elif kind == "coverage":
                item = get_object_or_404(coverage_items, pk=request.POST.get("item_id"))
                form = CoverageReconciliationForm(request.POST)
                if form.is_valid():
                    AcademicCoverageIntegrationService.resolve(actor=request.user, reconciliation=item, **form.cleaned_data)
                    messages.success(request, "Coverage reconciliation resolved.")
                    return redirect("faculty_attendance:reconciliation")
            elif kind == "source":
                item = get_object_or_404(source_items, pk=request.POST.get("item_id"))
                form = SourceChangeReconciliationForm(request.POST)
                if form.is_valid():
                    AcademicOfferingSourceIntegrationService.resolve(
                        actor=request.user, source_change=item, **form.cleaned_data
                    )
                    messages.success(request, "Course Offering source history resolved without rewriting recorded attendance.")
                    return redirect("faculty_attendance:reconciliation")
            else:
                item = get_object_or_404(meeting_items, pk=request.POST.get("item_id"))
                form = ReconciliationForm(request.POST)
                if form.is_valid():
                    ReconciliationService.resolve(actor=request.user, reconciliation=item, **form.cleaned_data)
                    messages.success(request, "Meeting reconciliation resolved; historical snapshots remain intact.")
                    return redirect("faculty_attendance:reconciliation")
        except (ValidationError, PermissionDenied) as exc:
            if kind == "adoption":
                if isinstance(exc, PermissionDenied):
                    return HttpResponseForbidden("Legacy meeting recovery authority is required.")
                adoption_form.add_error(None, " ".join(exc.messages))
                adoption_error = True
            messages.error(request, str(exc))
    can_initialize_coverage = _can_page(request, MANAGE_COVERAGE_PERMISSION, department_ids)
    recovery_items = []
    if can_initialize_coverage:
        from .coverage_initialization import CoverageAdoptionService
        for item in adoption_items.order_by("starts_at", "pk"):
            item.recovery_candidate = None
            item.recovery_error = ""
            try:
                item.recovery_candidate = CoverageAdoptionService.preview(actor=request.user, meeting=item)
            except PermissionDenied:
                continue
            except ValidationError as exc:
                item.recovery_error = " ".join(exc.messages)
            item.recovery_sections = item.sections_snapshot or [
                {"course_code": link.offering.course.code, "course_title": link.offering.course.title,
                 "section_code": link.offering.section.code} for link in item.offering_links.all()]
            item.recovery_form = (
                adoption_form if adoption_form and request.POST.get("meeting_id") == str(item.pk)
                else CoverageAdoptionForm(initial={"meeting_id": item.pk}))
            item.recovery_form.auto_id = f"adoption-{item.pk}-%s"
            recovery_items.append(item)
    return render(request, "faculty_attendance/reconciliation.html", {
        "coverage_items": coverage_items,
        "source_items": source_items,
        "meeting_items": meeting_items,
        "adoption_items": recovery_items,
        "adoption_error": adoption_error,
        "adoption_form_errors": adoption_form.errors if adoption_form and adoption_error else None,
        "can_initialize_coverage": can_initialize_coverage,
        "coverage_form": CoverageReconciliationForm(),
        "source_form": SourceChangeReconciliationForm(),
        "meeting_form": ReconciliationForm(),
        "can_manage_corrections": _can_page(request, MANAGE_SCHEDULES_PERMISSION, department_ids),
    })


@portal_required("FACULTY")
def my_attendance_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or request.user.default_tenant_id
    campus_id = scope.get("campus_id") or request.user.default_campus_id
    if not tenant_id or not can_faculty_view_own_attendance(user=request.user, tenant_id=tenant_id, campus_id=campus_id):
        return HttpResponseForbidden("My Attendance is disabled or unavailable for this account.")
    today = timezone.localdate()
    form = FacultyAttendanceFilterForm(request.GET or None)
    start = date(today.year, today.month, 1)
    end = today
    if form.is_valid():
        start = form.cleaned_data.get("start_date") or start
        end = form.cleaned_data.get("end_date") or end
        if end < start:
            form.add_error("end_date", "End date cannot precede start date.")
            end = today
    include_history = request.GET.get("history") == "1"
    entries, publications, history_entries = faculty_published_entries(
        faculty_user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
        start_date=start,
        end_date=end,
        include_history=include_history,
    )
    summary = published_tardiness_summary(
        faculty_user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
        year=end.year,
        month=end.month,
    )
    return render(
        request,
        "faculty_attendance/my_attendance.html",
        {
            "form": form,
            "entries": entries,
            "publications": publications,
            "history_entries": history_entries,
            "include_history": include_history,
            "summary": summary,
            "start_date": start,
            "end_date": end,
        },
    )
