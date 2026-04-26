from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db.models import Count

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, StudentActivityScore, StudentPeriodGrade
from apps.grading.services import FacultyGradingService, GradingGovernanceService

User = get_user_model()


class GradeDistributionMonitorService:
    """Read-only grade distribution analytics for academic governance monitoring."""

    SOURCE_PERIOD = "period"
    SOURCE_ACTIVITY = "activity"

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

    @classmethod
    def build_context(cls, request):
        selected = cls._selected_filters(request)
        offerings_qs = cls._filtered_offerings(request, selected)
        offerings = list(offerings_qs.distinct())
        offering_ids = [offering.id for offering in offerings]
        offering_map = {offering.id: offering for offering in offerings}

        faculty_by_offering = cls._faculty_by_offering(offering_ids)
        active_counts = cls._active_enrollment_counts(offering_ids)
        thresholds = cls._passing_thresholds(offerings)
        settings = cls._threshold_settings(request)

        if selected["source"] == cls.SOURCE_ACTIVITY:
            rows = cls._activity_rows(
                offering_ids=offering_ids,
                offering_map=offering_map,
                faculty_by_offering=faculty_by_offering,
                active_counts=active_counts,
                thresholds=thresholds,
                settings=settings,
                selected=selected,
            )
        else:
            rows = cls._period_rows(
                offering_ids=offering_ids,
                offering_map=offering_map,
                faculty_by_offering=faculty_by_offering,
                active_counts=active_counts,
                thresholds=thresholds,
                settings=settings,
                selected=selected,
            )

        rows = cls._attach_comparison_averages(rows)
        summary = cls._summary(rows, len(offering_ids))
        filter_options = cls._filter_options(request, offerings_qs)

        query_params = request.GET.copy()
        query_params["export"] = "csv"

        return {
            "selected": selected,
            "filter_options": filter_options,
            "settings": settings,
            "summary": summary,
            "rows": rows,
            "export_query": query_params.urlencode(),
        }

    @classmethod
    def _selected_filters(cls, request):
        source = request.GET.get("source") or cls.SOURCE_PERIOD
        if source not in {cls.SOURCE_PERIOD, cls.SOURCE_ACTIVITY}:
            source = cls.SOURCE_PERIOD
        return {
            "source": source,
            "campus_id": cls._safe_int(request.GET.get("campus_id")),
            "department_id": cls._safe_int(request.GET.get("department_id")),
            "academic_year_id": cls._safe_int(request.GET.get("academic_year_id")),
            "term_id": cls._safe_int(request.GET.get("term_id")),
            "faculty_id": cls._safe_int(request.GET.get("faculty_id")),
            "course_id": cls._safe_int(request.GET.get("course_id")),
            "offering_id": cls._safe_int(request.GET.get("offering_id")),
            "period_id": cls._safe_int(request.GET.get("period_id")),
            "component_id": cls._safe_int(request.GET.get("component_id")),
            "subcomponent_id": cls._safe_int(request.GET.get("subcomponent_id")),
        }

    @classmethod
    def _filtered_offerings(cls, request, selected):
        offerings = AdminScopeService.scoped_course_offerings(request).filter(status=CourseOffering.Status.OPEN)
        if selected["campus_id"]:
            offerings = offerings.filter(campus_id=selected["campus_id"])
        if selected["department_id"]:
            offerings = offerings.filter(department_id=selected["department_id"])
        if selected["academic_year_id"]:
            offerings = offerings.filter(academic_year_id=selected["academic_year_id"])
        if selected["term_id"]:
            offerings = offerings.filter(term_id=selected["term_id"])
        if selected["course_id"]:
            offerings = offerings.filter(course_id=selected["course_id"])
        if selected["offering_id"]:
            offerings = offerings.filter(id=selected["offering_id"])
        if selected["faculty_id"]:
            faculty_offering_ids = FacultyAssignment.objects.filter(
                offering_id__in=offerings.values("id"),
                faculty_user_id=selected["faculty_id"],
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            ).values("offering_id")
            offerings = offerings.filter(id__in=faculty_offering_ids)
        return offerings.select_related(
            "tenant",
            "campus",
            "department",
            "program",
            "academic_year",
            "term",
            "course",
            "section",
        )

    @classmethod
    def _faculty_by_offering(cls, offering_ids):
        assignments = (
            FacultyAssignment.objects.filter(
                offering_id__in=offering_ids,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            )
            .select_related("faculty_user")
            .order_by("offering_id", "-is_primary", "-accepted_at", "-assigned_at")
        )
        faculty_by_offering = {}
        for assignment in assignments:
            faculty_by_offering.setdefault(assignment.offering_id, assignment.faculty_user)
        return faculty_by_offering

    @classmethod
    def _active_enrollment_counts(cls, offering_ids):
        rows = (
            Enrollment.objects.filter(
                course_offering_id__in=offering_ids,
                is_active=True,
                enrollment_status=Enrollment.Status.ACTIVE,
            )
            .values("course_offering_id")
            .annotate(total=Count("id"))
        )
        return {row["course_offering_id"]: row["total"] for row in rows}

    @classmethod
    def _passing_thresholds(cls, offerings):
        thresholds = {}
        for offering in offerings:
            thresholds[offering.id] = FacultyGradingService.resolve_passing_threshold(offering)
        return thresholds

    @classmethod
    def _period_rows(cls, offering_ids, offering_map, faculty_by_offering, active_counts, thresholds, settings, selected):
        grades = StudentPeriodGrade.objects.filter(
            offering_id__in=offering_ids,
            period_grade__isnull=False,
        )
        if selected["period_id"]:
            grades = grades.filter(template_period_id=selected["period_id"])

        grouped = defaultdict(list)
        period_names = {}
        for row in grades.values("offering_id", "template_period_id", "template_period__name", "period_grade"):
            group_key = (row["offering_id"], row["template_period_id"])
            grouped[group_key].append(cls._decimal(row["period_grade"], Decimal("0")))
            period_names[group_key] = row["template_period__name"]

        rows = []
        for (offering_id, period_id), values in grouped.items():
            offering = offering_map.get(offering_id)
            if not offering:
                continue
            period_name = period_names.get((offering_id, period_id))
            rows.append(
                cls._build_metric_row(
                    source_label="Period Grade",
                    level_label="Period Grade",
                    activity_title="",
                    offering=offering,
                    faculty=faculty_by_offering.get(offering_id),
                    period_name=period_name or "-",
                    component_name="",
                    subcomponent_name="",
                    values=values,
                    active_count=active_counts.get(offering_id, 0),
                    passing_threshold=thresholds.get(offering_id, Decimal("75.00")),
                    settings=settings,
                )
            )
        return rows

    @classmethod
    def _activity_rows(cls, offering_ids, offering_map, faculty_by_offering, active_counts, thresholds, settings, selected):
        scores = StudentActivityScore.objects.filter(
            activity__offering_id__in=offering_ids,
            activity__is_active=True,
            is_active=True,
            computed_score__isnull=False,
        )
        if selected["period_id"]:
            scores = scores.filter(activity__template_period_id=selected["period_id"])
        if selected["component_id"]:
            scores = scores.filter(activity__template_component_id=selected["component_id"])
        if selected["subcomponent_id"]:
            scores = scores.filter(activity__template_subcomponent_id=selected["subcomponent_id"])

        activity_ids = list(scores.values_list("activity_id", flat=True).distinct())
        activity_map = {
            activity.id: activity
            for activity in GradeActivity.objects.filter(id__in=activity_ids).select_related(
                "offering",
                "template_period",
                "template_component",
                "template_subcomponent",
                "template_detail",
            )
        }
        grouped = defaultdict(list)
        for row in scores.values("activity_id", "computed_score"):
            grouped[row["activity_id"]].append(cls._decimal(row["computed_score"], Decimal("0")))

        rows = []
        for activity_id, values in grouped.items():
            activity = activity_map.get(activity_id)
            if not activity:
                continue
            offering = offering_map.get(activity.offering_id)
            if not offering:
                continue
            rows.append(
                cls._build_metric_row(
                    source_label="Activity Score",
                    level_label="Activity",
                    activity_title=activity.title,
                    offering=offering,
                    faculty=faculty_by_offering.get(activity.offering_id),
                    period_name=activity.template_period.name,
                    component_name=activity.template_component.name,
                    subcomponent_name=activity.template_subcomponent.name if activity.template_subcomponent else "",
                    values=values,
                    active_count=active_counts.get(activity.offering_id, 0),
                    passing_threshold=thresholds.get(activity.offering_id, Decimal("75.00")),
                    settings=settings,
                )
            )
        return rows

    @classmethod
    def _build_metric_row(
        cls,
        *,
        source_label,
        level_label,
        activity_title,
        offering,
        faculty,
        period_name,
        component_name,
        subcomponent_name,
        values,
        active_count,
        passing_threshold,
        settings,
    ):
        graded_count = len(values)
        average = cls._round(sum(values) / Decimal(graded_count)) if graded_count else None
        highest = max(values) if values else None
        lowest = min(values) if values else None
        spread = cls._round(highest - lowest) if highest is not None and lowest is not None else None
        high_min = settings["high_grade_band_min"]
        high_max = settings["high_grade_band_max"]
        high_count = sum(1 for value in values if high_min <= value <= high_max)
        band_80_89 = sum(1 for value in values if Decimal("80") <= value < Decimal("90"))
        band_75_79 = sum(1 for value in values if Decimal("75") <= value < Decimal("80"))
        below_passing = sum(1 for value in values if value < passing_threshold)
        exact_100 = sum(1 for value in values if value == Decimal("100"))
        incomplete = active_count > 0 and graded_count < active_count
        flags = cls._flags(
            graded_count=graded_count,
            high_pct=cls._pct(high_count, graded_count),
            exact_100_pct=cls._pct(exact_100, graded_count),
            spread=spread,
            incomplete=incomplete,
            settings=settings,
        )
        return {
            "source_label": source_label,
            "level_label": level_label,
            "activity_title": activity_title,
            "faculty_name": cls._user_name(faculty),
            "campus": offering.campus.code,
            "department": offering.department.code,
            "course_code": offering.course.code,
            "course_title": offering.course.title,
            "section": offering.section.code,
            "school_year": offering.academic_year.code,
            "term": offering.term.code,
            "period": period_name,
            "component": component_name,
            "subcomponent": subcomponent_name,
            "graded_count": graded_count,
            "active_count": active_count,
            "average": average,
            "highest": cls._round(highest) if highest is not None else None,
            "lowest": cls._round(lowest) if lowest is not None else None,
            "spread": spread,
            "high_pct": cls._pct(high_count, graded_count),
            "band_80_89_pct": cls._pct(band_80_89, graded_count),
            "band_75_79_pct": cls._pct(band_75_79, graded_count),
            "below_passing_pct": cls._pct(below_passing, graded_count),
            "exact_100_pct": cls._pct(exact_100, graded_count),
            "flags": flags,
            "has_review_flag": any(flag["kind"] == "review" for flag in flags),
            "department_key": (offering.campus_id, offering.department_id, period_name),
            "course_key": (offering.course_id, period_name),
        }

    @classmethod
    def _flags(cls, *, graded_count, high_pct, exact_100_pct, spread, incomplete, settings):
        flags = []
        min_count = settings["minimum_student_count_for_flag"]
        if graded_count == 0 or incomplete:
            flags.append({"label": "Incomplete Data", "class": "text-bg-secondary", "kind": "status"})
            return flags
        if graded_count < min_count:
            flags.append({"label": "Small Sample", "class": "text-bg-info", "kind": "status"})
            return flags
        if high_pct >= settings["high_grade_concentration_threshold_percent"]:
            flags.append({"label": "High Grade Concentration", "class": "text-bg-warning", "kind": "review"})
        if exact_100_pct >= settings["exact_100_threshold_percent"]:
            flags.append({"label": "High Perfect Score Rate", "class": "text-bg-warning", "kind": "review"})
        if spread is not None and spread <= settings["low_variation_threshold"]:
            flags.append({"label": "Low Grade Variation", "class": "text-bg-warning", "kind": "review"})
        if not flags:
            flags.append({"label": "No Review Flag", "class": "text-bg-success", "kind": "status"})
        return flags

    @classmethod
    def _attach_comparison_averages(cls, rows):
        department_values = defaultdict(list)
        course_values = defaultdict(list)
        for row in rows:
            if row["average"] is not None:
                department_values[row["department_key"]].append(row["average"])
                course_values[row["course_key"]].append(row["average"])
        for row in rows:
            dept_values = department_values.get(row["department_key"], [])
            course_values_for_row = course_values.get(row["course_key"], [])
            row["department_average"] = cls._round(sum(dept_values) / Decimal(len(dept_values))) if dept_values else None
            row["subject_average"] = (
                cls._round(sum(course_values_for_row) / Decimal(len(course_values_for_row)))
                if course_values_for_row
                else None
            )
        return sorted(
            rows,
            key=lambda row: (
                0 if row["has_review_flag"] else 1,
                0 if any(flag["label"] == "Incomplete Data" for flag in row["flags"]) else 1,
                -row["high_pct"],
                row["faculty_name"],
                row["course_code"],
            ),
        )

    @classmethod
    def _summary(cls, rows, offering_count):
        review_rows = sum(1 for row in rows if row["has_review_flag"])
        incomplete_rows = sum(1 for row in rows if any(flag["label"] == "Incomplete Data" for flag in row["flags"]))
        high_concentration_rows = sum(
            1 for row in rows if any(flag["label"] == "High Grade Concentration" for flag in row["flags"])
        )
        high_perfect_rows = sum(
            1 for row in rows if any(flag["label"] == "High Perfect Score Rate" for flag in row["flags"])
        )
        return {
            "offerings": offering_count,
            "rows": len(rows),
            "review_rows": review_rows,
            "incomplete_rows": incomplete_rows,
            "high_concentration_rows": high_concentration_rows,
            "high_perfect_rows": high_perfect_rows,
        }

    @classmethod
    def _filter_options(cls, request, offerings_qs):
        scoped_offerings = list(offerings_qs.distinct()[:500])
        offering_ids = [offering.id for offering in scoped_offerings]
        template_ids = set()
        for offering in scoped_offerings[:200]:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            if template:
                template_ids.add(template.id)
        periods = AdminScopeService.scoped_template_periods(request)
        components = AdminScopeService.scoped_template_components(request)
        subcomponents = AdminScopeService.scoped_template_subcomponents(request)
        if template_ids:
            periods = periods.filter(template_id__in=template_ids)
            components = components.filter(template_period__template_id__in=template_ids)
            subcomponents = subcomponents.filter(template_component__template_period__template_id__in=template_ids)
        return {
            "campuses": AdminScopeService.scoped_campuses(request).order_by("code"),
            "departments": AdminScopeService.scoped_departments(request).order_by("code"),
            "academic_years": AdminScopeService.scoped_academic_years(request).order_by("-start_date"),
            "terms": AdminScopeService.scoped_terms(request).order_by("-academic_year__start_date", "sequence_no"),
            "faculty": User.objects.filter(id__in=AdminScopeService.scoped_faculty_users(request))
            .filter(id__in=FacultyAssignment.objects.filter(offering_id__in=offering_ids).values("faculty_user_id"))
            .order_by("last_name", "first_name", "username"),
            "courses": AdminScopeService.scoped_courses(request).order_by("code"),
            "offerings": scoped_offerings,
            "periods": periods.order_by("template__name", "sequence_no"),
            "components": components.order_by("template_period__template__name", "template_period__sequence_no", "sort_order"),
            "subcomponents": subcomponents.order_by(
                "template_component__template_period__template__name",
                "template_component__template_period__sequence_no",
                "template_component__sort_order",
                "sort_order",
            ),
        }

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

    @staticmethod
    def _pct(value, total):
        if not total:
            return Decimal("0.0")
        return ((Decimal(value) / Decimal(total)) * Decimal("100")).quantize(Decimal("0.1"))

    @staticmethod
    def _user_name(user):
        if not user:
            return "Unassigned"
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.username


def build_grade_distribution_query(base_query, **extra):
    query = base_query.copy()
    for key, value in extra.items():
        query[key] = value
    return urlencode(query, doseq=True)
