from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, Count, F, Q, Value, When
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.admin_portal.grade_distribution_selection import MonitorSelection, faculty_name, logical_period
from apps.admin_portal.grade_distribution_metrics import PrivacyFamily, component_measures, percentage, threshold_for
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradingTemplatePeriod, StudentPeriodGrade
from apps.grading.services import GradingGovernanceService


class GradeDistributionMonitorService:
    """Read-only, bounded class/physical-period aggregate analytics."""
    PAGE_SIZE = 25
    MAX_CLASS_ROWS = 500
    MAX_SAVED_ROWS = 50000
    MAX_ROSTER_ROWS = 50000
    MAX_CLASS_STUDENTS = 2000

    DEFAULTS = {
        "high_grade_band_min": Decimal("90"),
        "high_grade_band_max": Decimal("100"),
        "high_grade_concentration_threshold_percent": Decimal("75"),
        "exact_100_threshold_percent": Decimal("30"),
        "low_variation_threshold": Decimal("5"),
        "minimum_student_count_for_flag": 10,
    }

    SETTING_KEYS = {
        "high_grade_band_min": "GRADE_DISTRIBUTION_HIGH_GRADE_BAND_MIN",
        "high_grade_band_max": "GRADE_DISTRIBUTION_HIGH_GRADE_BAND_MAX",
        "high_grade_concentration_threshold_percent": (
            "GRADE_DISTRIBUTION_HIGH_GRADE_CONCENTRATION_THRESHOLD_PERCENT"
        ),
        "exact_100_threshold_percent": "GRADE_DISTRIBUTION_EXACT_100_THRESHOLD_PERCENT",
        "low_variation_threshold": "GRADE_DISTRIBUTION_LOW_VARIATION_THRESHOLD",
        "minimum_student_count_for_flag": "GRADE_DISTRIBUTION_MINIMUM_STUDENT_COUNT_FOR_FLAG",
    }

    @staticmethod
    def historical_offering_q(prefix=""):
        """Same retained-history decision for saved records, limits and row rosters."""
        today = timezone.localdate()
        q = Q(**{prefix + "status__in": ("CLOSED", "ARCHIVED")})
        q |= Q(**{prefix + "term__end_date__lt": today})
        q |= Q(**{prefix + "term__end_date__isnull": True, prefix + "academic_year__end_date__lt": today})
        for field in ("is_active", "academic_year__is_active", "term__is_active",
                      "course__is_active", "section__is_active", "department__is_active", "program__is_active"):
            q |= Q(**{prefix + field: False})
        return q

    @staticmethod
    def retained_roster(offerings, tenant_id):
        # Current student tenant is a defense against cross-tenant disclosure.
        # Campus/year/term authority comes from the original enrollment and offering.
        return Enrollment.objects.filter(
            course_offering_id__in=offerings.values("id"), tenant_id=tenant_id,
            campus_id=F("course_offering__campus_id"),
            academic_year_id=F("course_offering__academic_year_id"),
            term_id=F("course_offering__term_id"), student__tenant_id=tenant_id,
        ).exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)

    @staticmethod
    def live_roster_q():
        return (Q(is_active=True, student__is_active=True, student__department__is_active=True,
                  student__campus_id=F("course_offering__campus_id"))
                & (Q(student__program__isnull=True) | Q(student__program__is_active=True)))

    @classmethod
    def validate_class_sizes(cls, offerings, saved, groups, tenant_id):
        # At most MAX_CLASS_ROWS offering aggregates; no student IDs are loaded.
        candidates = offerings.filter(id__in=groups.values("offering_id"))
        historical_ids = set(candidates.filter(
            Q(monitor_historical=True) | Q(id__in=saved.filter(template_period__is_active=False).values("offering_id"))
        ).values_list("id", flat=True))
        counts = cls.retained_roster(candidates, tenant_id).order_by().values("course_offering_id").annotate(
            historical_count=Count("student_id", distinct=True),
            live_count=Count("student_id", distinct=True, filter=cls.live_roster_q()),
        )
        for row in counts:
            size = row["historical_count" if row["course_offering_id"] in historical_ids else "live_count"]
            if size > cls.MAX_CLASS_STUDENTS:
                raise ValidationError(f"A class exceeds the {cls.MAX_CLASS_STUDENTS:,} student analysis limit. Narrow your criteria; no records were truncated.")

    @classmethod
    def prepare(cls, request, selection=None, require_context=False):
        selection = selection or MonitorSelection(request)
        selection.validate()
        if require_context or request.GET.get("context") or request.GET.get("page") or request.GET.get("export") or request.GET.get("detail"):
            selection.validate_token()
        elif request.GET.get("apply") != "1":
            raise ValidationError("Select criteria and click Apply Filters.")
        assignments = selection.filtered_assignments()
        offerings = CourseOffering.objects.filter(id__in=assignments.values("offering_id")).annotate(
            monitor_historical=Case(When(cls.historical_offering_q(), then=Value(True)),
                                    default=Value(False), output_field=BooleanField())
        ).order_by("id")
        saved = StudentPeriodGrade.objects.filter(
            offering_id__in=offerings.values("id"), tenant_id=selection.tenant_id,
            campus_id=F("offering__campus_id"), template_period__template__tenant_id=selection.tenant_id,
            student__tenant_id=selection.tenant_id,
        ).filter(Q(student__campus_id=F("offering__campus_id")) | cls.historical_offering_q("offering__")
                 | Q(template_period__is_active=False)).order_by()
        # Period identities are resolved only after Apply; physical records remain
        # distinct even if multiple templates share a logical period.
        periods = list(GradingTemplatePeriod.objects.filter(id__in=saved.values("template_period_id")).select_related("template")[:2001])
        if len(periods) > 2000:
            raise ValidationError("Period mapping limit exceeded. Narrow the campus, faculty or department criteria.")
        unresolved = sum(logical_period(p) is None for p in periods)
        period_map = {p.id: p for p in periods if logical_period(p) == selection.selected["period"]}
        saved = saved.filter(template_period_id__in=period_map)
        if saved.count() > cls.MAX_SAVED_ROWS:
            raise ValidationError(f"Search/export limit is {cls.MAX_SAVED_ROWS:,} saved grade records. Narrow your criteria.")
        groups = saved.values("offering_id", "template_period_id").annotate(saved_count=Count("id")).order_by("offering_id", "template_period_id")
        count = groups.count()
        if count > cls.MAX_CLASS_ROWS:
            raise ValidationError(f"Search/export limit is {cls.MAX_CLASS_ROWS} class/period rows. Narrow your criteria.")
        if Enrollment.objects.filter(course_offering_id__in=offerings.values("id")).count() > cls.MAX_ROSTER_ROWS:
            raise ValidationError(f"Search/export roster limit is {cls.MAX_ROSTER_ROWS:,} records. Narrow your criteria.")
        cls.validate_class_sizes(offerings, saved, groups, selection.tenant_id)
        return selection, assignments, offerings, saved, groups, period_map, unresolved, count

    @classmethod
    def build_context(cls, request):
        selection = MonitorSelection(request)
        context = {"selected": selection.selected, "filter_options": selection.options, "rows": [],
                   "searched": False, "errors": [], "page_obj": None, "page_query": "", "export_query": ""}
        if request.GET.get("apply") != "1" and not any(request.GET.get(k) for k in ("export", "page", "context")):
            return context
        try:
            selection, assignments, offerings, saved, groups, periods, unresolved, count = cls.prepare(request, selection)
            context["searched"] = True
            # Privacy requires the full source family, not a SQL count of 100s.
            # prepare() bounds this complete selection before any source loading.
            rows = cls.rows(selection, assignments, offerings, saved, list(groups), periods)
            if selection.selected["only_100"]:
                rows = [row for row in rows if row["analytics_state"] == "disclosed" and row["exact_100_count"] > 0]
            rows.sort(key=lambda row: cls.sort_key(row, selection.selected["sort"]))
            if request.GET.get("export"):
                context["rows"] = rows
            else:
                page = Paginator(rows, cls.PAGE_SIZE).get_page(request.GET.get("page"))
                context["page_obj"] = page
                context["rows"] = list(page.object_list)
            for row in context["rows"]:
                display = row.pop("_display", None)
                if display:
                    _, row["measures"], notes, row["incomplete_source_count"], row["quiz"] = display()
                    row["notes"].extend(notes)
            query = selection.query(selection.token())
            context["page_query"] = query.urlencode()
            query["export"] = "xlsx"
            context["export_query"] = query.urlencode()
            context["mapping_note"] = (f"{unresolved} saved physical period mapping(s) are unresolved and unavailable; sequence alone is not used." if unresolved else "")
            context["result_count"] = len(rows)
        except ValidationError as exc:
            context["errors"] = exc.messages
            context["searched"] = False
            context["rows"] = []
            context["page_obj"] = None
        return context

    @staticmethod
    def sort_key(row, sort="exact_100_desc"):
        identity = tuple(row[key].casefold() for key in
                         ("faculty_name", "campus", "course_code", "section"))
        identity += (row["offering_id"], row["physical_period_id"])
        if sort == "faculty_asc":
            return identity
        disclosed = row["analytics_state"] == "disclosed"
        # Never rank by a protected count, including through a hidden tie-breaker.
        if sort == "exact_100_desc":
            count = row["exact_100_count"] if disclosed else 0
        else:
            grade = sort.split("_")[1]
            count = next((item["count"] for item in row["high_grade_breakdown"] if item["grade"] == f"Exactly {grade}"), 0) if disclosed else 0
        return (not disclosed, -count, *identity)

    @classmethod
    def rows(cls, selection, assignments, offerings, saved, candidates, periods, detail=False):
        if selection.request.GET.get("export"):
            from apps.admin_portal.grade_distribution_export import validate_workbook_size
            validate_workbook_size(3 * len(candidates))
        offering_ids = {c["offering_id"] for c in candidates}
        offering_map = {o.id: o for o in offerings.filter(id__in=offering_ids).select_related(
            "campus", "tenant", "department", "program", "course", "section", "academic_year", "term")}
        faculty_map = defaultdict(list)
        for a in assignments.filter(offering_id__in=offering_ids).select_related("faculty_user").order_by("faculty_user__last_name", "faculty_user__first_name", "faculty_user_id"):
            faculty_map[a.offering_id].append(faculty_name(a.faculty_user))
        # Sorting visits every bounded class. Load rosters/saved totals once,
        # rather than adding two queries per class or one query per student.
        retained, live = defaultdict(set), defaultdict(set)
        roster = cls.retained_roster(offerings.filter(id__in=offering_ids), selection.tenant_id).annotate(
            monitor_live=Case(When(cls.live_roster_q(), then=Value(True)),
                              default=Value(False), output_field=BooleanField()))
        for entry in roster.values("course_offering_id", "student_id", "monitor_live"):
            retained[entry["course_offering_id"]].add(entry["student_id"])
            if entry["monitor_live"]:
                live[entry["course_offering_id"]].add(entry["student_id"])
        saved_map = defaultdict(list)
        for record in saved.filter(offering_id__in=offering_ids,
                template_period_id__in={c["template_period_id"] for c in candidates}).values(
                    "offering_id", "template_period_id", "student_id", "period_grade",
                    "exam_grade", "class_standing_grade", "is_finalized"):
            saved_map[(record.pop("offering_id"), record.pop("template_period_id"))].append(record)
        from apps.admin_portal.grade_distribution_data import MonitorReadBundle, configuration_service
        cohorts = {(c["offering_id"], c["template_period_id"]):
            (retained if offering_map[c["offering_id"]].monitor_historical or not periods[c["template_period_id"]].is_active else live)[c["offering_id"]]
            for c in candidates}
        bundle = MonitorReadBundle(offering_map, periods, cohorts)
        service = configuration_service(offering_map)
        # Identity contains the complete filter/authority selection, not a row ID.
        # Physical row identity remains in each detail URL and is reauthorized.
        detail_query = selection.query(selection.token()).urlencode()
        rows = []
        for candidate in candidates:
            offering = offering_map[candidate["offering_id"]]
            period = periods[candidate["template_period_id"]]
            historical = offering.monitor_historical or not period.is_active
            student_ids = (retained if historical else live)[offering.id]
            if len(student_ids) > cls.MAX_CLASS_STUDENTS:
                raise ValidationError(f"A class exceeds the {cls.MAX_CLASS_STUDENTS:,} student analysis limit. Narrow your criteria; no records were truncated.")
            records = [r for r in saved_map[(offering.id, period.id)] if r["student_id"] in student_ids]
            status = selection.selected["status"]
            chosen = [r for r in records if status == "all" or r["is_finalized"] == (status == "finalized")]
            valued = [r for r in chosen if r["period_grade"] is not None]
            t, source = threshold_for(offering, service)
            privacy = PrivacyFamily({r["student_id"]: r["is_finalized"] for r in records}, status, t)
            # Plan all sources before even the summary/CSV statistic is released.
            period_metrics, measures, notes, incomplete, quiz = component_measures(
                offering, period, records, student_ids, t, historical, privacy, bundle=bundle, service=service, defer_display=not detail)
            saved_ids = {r["student_id"] for r in records if r["period_grade"] is not None}
            excluded = sum(r["period_grade"] is not None for r in records) - len(valued)
            row = {
                "offering_id": offering.id, "physical_period_id": period.id,
                "faculty_name": "; ".join(faculty_map[offering.id]), "campus": offering.campus.code,
                "department": offering.department.code, "course_code": offering.course.code, "course_title": offering.course.title,
                "program": offering.program.code if offering.program_id else "",
                "section": offering.section.code, "school_year": offering.academic_year.code, "term": offering.term.code,
                "period": next(p["label"] for p in selection.options["periods"] if p["id"] == logical_period(period)),
                "physical_period_label": f"Physical period #{period.id}",
                "period_context": f"{period.name} ({period.code}) / {period.template.name} ({period.template.code})",
                "passing_threshold": t, "threshold_source": source,
                "missing_count": max(len(student_ids) - len(saved_ids), 0), "status_excluded_count": excluded,
                "coverage_rate": percentage(len(saved_ids), len(student_ids)),
                "historical": historical,
                "notes": (["Historical roster uses retained enrollment associations, including inactive records except non-grading statuses. Current student campus, department and program do not alter this retained cohort. Original roster and threshold snapshots are unavailable. Threshold shown is currently resolved."] if historical else []),
                **period_metrics,
            }
            row["detail_query"] = detail_query
            if callable(measures):
                row["_display"] = measures
                row["measures"] = []
            else:
                row["measures"] = measures
            row["quiz"] = quiz
            row["incomplete_source_count"] = incomplete
            row["notes"].extend(notes)
            if candidate["saved_count"] > len(records):
                row["notes"].append(
                    f'{candidate["saved_count"] - len(records)} saved record(s) are outside the dependable eligible roster and are omitted.'
                )
            if historical and not student_ids:
                row["notes"].append("No dependable eligible enrollment associations remain; saved grades cannot establish an original roster on their own.")
            if detail:
                row["notes"].append("Analytics use the saved grades displayed in the Gradebook, including the configured base for activities. Raw scores are not the displayed distribution. Saved unfinalized grades may change. Percentages round independently to two decimal places. Activities have no authoritative subjective/objective classification.")
            rows.append(row)
        return rows

    @classmethod
    def detail(cls, request, offering_id, period_id):
        selection, assignments, offerings, saved, groups, periods, unresolved, count = cls.prepare(request, require_context=True)
        candidates = list(groups.filter(offering_id=offering_id, template_period_id=period_id)[:1])
        if not candidates:
            return None
        row = cls.rows(selection, assignments, offerings, saved, candidates, periods, detail=True)[0]
        if selection.selected["only_100"] and (row["analytics_state"] != "disclosed" or not row["exact_100_count"]):
            return None
        return row

    @classmethod
    def sanitized_query(cls, request):
        query = request.GET.copy()
        for key in list(query):
            if key not in {*MonitorSelection.FILTER_KEYS, "apply", "context", "page", "export"}:
                query.pop(key)
        return query

    @classmethod
    def _threshold_settings(cls, request):
        tenant_id = getattr(request, "scope", {}).get("tenant_id")
        settings = {}
        for name, key in cls.SETTING_KEYS.items():
            default = cls.DEFAULTS[name]
            raw = SystemSettingService.get(key, tenant_id=tenant_id, default=str(default))
            if name == "minimum_student_count_for_flag":
                settings[name] = max(cls._safe_int(raw) or int(default), 1)
            else:
                settings[name] = cls._decimal(raw, default)
        return settings

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decimal(value, fallback):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return fallback

    @staticmethod
    def _round(value):
        return GradingGovernanceService._round(Decimal(value))
