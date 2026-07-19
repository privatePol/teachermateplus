from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from apps.academics.models import CourseOffering, FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.faculty_portal.services import FacultyPerformanceService
from apps.grading.models import GradeActivity, GradeSubmission, StudentActivityScore
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.rbac.models import UserRole


class AcademicPerformanceInsightService:
    """Read-only academic-leadership comparisons built from official grade computation."""

    ROLE_AREA_CHAIR = "AREA_CHAIR"
    ROLE_COLLEGE_DEAN = "COLLEGE_DEAN"
    ROLE_CAO = "CAO"
    AREA_CHAIR_CODES = {"AC", "AREA_CHAIR", "AREA_CHAIRPERSON"}
    DEAN_CODES = {"DEAN", "COLLEGE_DEAN"}
    MAX_LIVE_OFFERINGS = 100

    CONSISTENT = "Consistent"
    MINOR_DIFFERENCE = "Minor Difference"
    NEEDS_REVIEW = "Needs Review"
    INCOMPLETE_SETUP = "Incomplete Setup"
    STATUS_NORMAL = "Normal"
    STATUS_NEEDS_ATTENTION = "Needs Attention"
    STATUS_HIGH_RISK = "High Risk"
    STATUS_INCOMPLETE_DATA = "Incomplete Data"

    # These are neutral review bands, not institutional grade classifications.
    # Keeping the rule here ensures HTML and CSV surfaces use the same threshold
    # aware interpretation for every profile and course offering.
    DISTRIBUTION_BANDS = (
        ("Strongly above threshold", Decimal("15"), None),
        ("Above threshold", Decimal("0"), Decimal("15")),
        ("Near threshold", Decimal("-5"), Decimal("0")),
        ("Below threshold", Decimal("-15"), Decimal("-5")),
        ("Well below threshold", None, Decimal("-15")),
    )

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _faculty_name(user):
        if not user:
            return "Unassigned"
        return (user.full_name or "").strip() or user.username

    @classmethod
    def get_role_scope(cls, request):
        role_codes = {
            str(code).strip().upper()
            for code in UserRole.objects.filter(
                user=request.user,
                is_active=True,
                role__is_active=True,
            ).values_list("role__code", flat=True)
        }
        if request.user.is_superuser or cls.ROLE_CAO in role_codes:
            role_mode = cls.ROLE_CAO
        elif role_codes & cls.DEAN_CODES:
            role_mode = cls.ROLE_COLLEGE_DEAN
        else:
            role_mode = cls.ROLE_AREA_CHAIR
        campuses = AdminScopeService.active_scoped_campuses(request)
        departments = AdminScopeService.active_scoped_departments(request)
        current_campus_id = getattr(request, "scope", {}).get("campus_id")
        if role_mode == cls.ROLE_AREA_CHAIR and current_campus_id:
            campuses = campuses.filter(id=current_campus_id)
            departments = departments.filter(campus_id=current_campus_id)
        return {
            "role_codes": role_codes,
            "role_mode": role_mode,
            "can_compare_campuses": request.user.is_superuser
            or role_mode in {cls.ROLE_COLLEGE_DEAN, cls.ROLE_CAO},
            "campuses": campuses.order_by("code"),
            "departments": departments.order_by(
                "campus__code",
                "code",
            ),
        }

    @classmethod
    def selected_filters(cls, request):
        return {
            "academic_year_id": cls._safe_int(request.GET.get("academic_year_id")),
            "term_id": cls._safe_int(request.GET.get("term_id")),
            "period_code": (request.GET.get("period_code") or "").strip(),
            "campus_id": cls._safe_int(request.GET.get("campus_id")),
            "department_id": cls._safe_int(request.GET.get("department_id")),
            "course_code": (request.GET.get("course_code") or "").strip(),
            "faculty_id": cls._safe_int(request.GET.get("faculty_id")),
        }

    @staticmethod
    def required_filters_present(filters):
        return bool(filters["academic_year_id"] and filters["term_id"] and filters["period_code"])

    @classmethod
    def get_scoped_offerings(cls, request, filters, *, apply_required_filters=True):
        role_scope = cls.get_role_scope(request)
        requested_all_campuses = (
            role_scope["can_compare_campuses"]
            and (request.GET.get("campus_id") or "").strip().lower() == "all"
        )
        offerings = AdminScopeService.scoped_monitoring_course_offerings(
            request,
            include_all_campuses=requested_all_campuses,
        ).filter(
            status=CourseOffering.Status.OPEN,
            faculty_assignments__is_active=True,
            faculty_assignments__response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        )
        if filters["campus_id"]:
            offerings = offerings.filter(campus_id=filters["campus_id"])
        if filters["department_id"]:
            department_ids = AdminScopeService.expand_department_filter_ids(
                filters["department_id"],
                campus_id=filters["campus_id"],
            )
            department_faculty_ids = UserRole.objects.filter(
                role__code="FACULTY",
                role__is_active=True,
                is_active=True,
            ).filter(
                Q(department_id__in=department_ids)
                | Q(department__isnull=True, user__default_department_id__in=department_ids)
            ).values("user_id")
            offerings = offerings.filter(
                faculty_assignments__faculty_user_id__in=department_faculty_ids,
            )
        if apply_required_filters and filters["academic_year_id"]:
            offerings = offerings.filter(academic_year_id=filters["academic_year_id"])
        if apply_required_filters and filters["term_id"]:
            offerings = offerings.filter(term_id=filters["term_id"])
        if filters["course_code"]:
            offerings = offerings.filter(course__code=filters["course_code"])
        if filters["faculty_id"]:
            offerings = offerings.filter(faculty_assignments__faculty_user_id=filters["faculty_id"])
        return offerings.distinct().order_by(
            "campus__code",
            "course__code",
            "section__code",
            "id",
        )

    @classmethod
    def get_filter_options(cls, request, filters):
        role_scope = cls.get_role_scope(request)
        base_offerings = cls.get_scoped_offerings(
            request,
            {**filters, "academic_year_id": None, "term_id": None, "period_code": ""},
            apply_required_filters=False,
        )
        academic_years = AdminScopeService.active_scoped_academic_years(request).filter(
            id__in=base_offerings.values("academic_year_id")
        )
        terms = AdminScopeService.active_scoped_terms(request).filter(
            id__in=base_offerings.values("term_id")
        )
        period_source = base_offerings
        if filters["academic_year_id"]:
            period_source = period_source.filter(academic_year_id=filters["academic_year_id"])
        if filters["term_id"]:
            period_source = period_source.filter(term_id=filters["term_id"])
        periods = {}
        for offering in period_source[: cls.MAX_LIVE_OFFERINGS]:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
            except ValidationError:
                continue
            for period in FacultyGradingService.get_template_periods(template):
                periods.setdefault(period.code, period.name)
        faculty = (
            FacultyAssignment.objects.filter(
                offering__in=base_offerings,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            )
            .select_related("faculty_user")
            .order_by("faculty_user__last_name", "faculty_user__first_name")
        )
        faculty_users = []
        seen_faculty = set()
        for assignment in faculty:
            if assignment.faculty_user_id in seen_faculty:
                continue
            seen_faculty.add(assignment.faculty_user_id)
            faculty_users.append(assignment.faculty_user)
        courses = (
            base_offerings.values("course__code", "course__title")
            .distinct()
            .order_by("course__code")
        )
        return {
            "role_scope": role_scope,
            "academic_years": academic_years.order_by("-start_date"),
            "terms": terms.order_by("-academic_year__start_date", "sequence_no"),
            "periods": [
                {"code": code, "name": name}
                for code, name in sorted(periods.items(), key=lambda item: item[0])
            ],
            "courses": courses,
            "faculty": faculty_users,
        }

    @classmethod
    def _period_for_offering(cls, offering, period_code):
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            return None
        return FacultyGradingService.get_template_periods(template).filter(code=period_code).first()

    @classmethod
    def _faculty_for_offering(cls, offering):
        assignment = (
            offering.faculty_assignments.filter(
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            )
            .select_related("faculty_user")
            .order_by("-is_primary", "-accepted_at", "-assigned_at")
            .first()
        )
        return assignment.faculty_user if assignment else None

    @classmethod
    def _main_concern(cls, snapshot, coverage):
        if not coverage["computed_grade_count"]:
            return "Incomplete data"
        if (
            coverage["average"] is not None
            and Decimal(coverage["average"]) < Decimal(coverage["passing_threshold"])
        ):
            return "Class average is below the passing grade"
        if coverage["below_threshold_count"] and coverage["no_grade_count"]:
            return "At-risk students and missing outputs need review"
        if coverage["below_threshold_count"]:
            return "Some students are below the passing grade"
        if coverage["no_grade_count"]:
            return "Some required activity scores are missing"
        if snapshot["weakest_component"]:
            return f"Review {snapshot['weakest_component']['name']}"
        return "No major concern found"

    @classmethod
    def get_academic_performance_status(cls, snapshot, coverage):
        if not coverage["computed_grade_count"] or snapshot["readiness"].get(
            "missing_template_bucket_count"
        ):
            return cls.STATUS_INCOMPLETE_DATA
        class_average = coverage["average"]
        passing_threshold = Decimal(coverage["passing_threshold"])
        student_count = coverage["computed_grade_count"]
        if (
            class_average is not None
            and Decimal(class_average) < passing_threshold
        ) or (
            student_count
            and coverage["below_threshold_count"] * 2 >= student_count
        ):
            return cls.STATUS_HIGH_RISK
        if coverage["below_threshold_count"] or coverage["no_grade_count"]:
            return cls.STATUS_NEEDS_ATTENTION
        return cls.STATUS_NORMAL

    @classmethod
    def get_section_performance_summary(cls, offering, grading_period):
        snapshot = FacultyPerformanceService.get_class_performance_snapshot(offering, grading_period)
        faculty = cls._faculty_for_offering(offering)
        coverage = cls._coverage_summary(offering, grading_period, snapshot)
        context = cls._grading_context(offering, grading_period)
        return {
            "offering": offering,
            "period": grading_period,
            "campus": offering.campus,
            "department": offering.department,
            "course_code": offering.course.code,
            "course_title": offering.course.title,
            "section": offering.section.code,
            "faculty": faculty,
            "faculty_name": cls._faculty_name(faculty),
            "class_average": coverage["average"],
            "at_risk_count": coverage["below_threshold_count"],
            "missing_output_count": snapshot["missing_output_count"],
            "weakest_component": snapshot["weakest_component"],
            "main_concern": cls._main_concern(snapshot, coverage),
            "status": cls.get_academic_performance_status(snapshot, coverage),
            "message": snapshot["message"],
            "has_performance_data": snapshot["has_performance_data"],
            "passing_threshold": snapshot["passing_threshold"],
            "student_count": len(snapshot["rows"]),
            "average_bar_width": float(coverage["average"] or 0),
            "coverage": coverage,
            "grading_context": context,
            "trend_summary": cls._trend_summary(coverage["rows"]),
        }

    @classmethod
    def _grading_context(cls, offering, grading_period=None):
        """Return only existing configuration facts used to decide comparability."""
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            template = None
        profile = FacultyGradingService.resolve_grading_profile_for_offering(offering)
        strategy = (
            FacultyGradingService.resolve_period_grade_strategy(offering, template=template)
            if template
            else {"mode": "MISSING_TEMPLATE"}
        )
        threshold = FacultyGradingService.resolve_passing_threshold(offering)
        transmutation_signature = tuple(
            (str(row["min"]), str(row["max"]), str(row["grade"]))
            for row in (strategy.get("transmutation_table") or [])
        )
        return {
            "template_id": template.id if template else None,
            "template_label": template.code if template else "No published template",
            "profile_id": profile.id if profile else None,
            "profile_label": profile.profile_code if profile else "Template/default configuration",
            "formula_mode": strategy.get("mode") or "UNKNOWN",
            "passing_threshold": threshold,
            "period_id": grading_period.id if grading_period else None,
            "period_code": grading_period.code if grading_period else "",
            "signature": (
                template.id if template else None,
                profile.id if profile else None,
                strategy.get("mode") or "UNKNOWN",
                transmutation_signature,
                str(threshold),
                grading_period.id if grading_period else None,
            ),
        }

    @classmethod
    def _coverage_summary(cls, offering, grading_period, snapshot):
        """Use the established readiness result to decide whether a grade is usable.

        A live computed value with required records still missing remains visible,
        but is not included in pass/fail or distribution denominators.
        """
        readiness = snapshot["readiness"]
        missing_by_student = cls._missing_by_student(readiness)
        setup_incomplete = bool(readiness.get("missing_template_bucket_count"))
        threshold = Decimal(snapshot["passing_threshold"])
        usable = []
        unavailable_reasons = Counter()
        rows = []
        for row in snapshot["rows"]:
            grade = row.get("current_grade")
            missing_count = missing_by_student.get(row["student_id"], 0)
            reason = ""
            if setup_incomplete:
                reason = "Incomplete required components"
            elif grade is None:
                reason = "No computed period grade"
            elif missing_count:
                reason = "Missing required scores"
            if reason:
                unavailable_reasons[reason] += 1
            else:
                usable.append(Decimal(grade))
            rows.append({**row, "usable_grade": not bool(reason), "unusable_reason": reason})

        usable_count = len(usable)
        enrolled_count = len(rows)
        passing_count = sum(value >= threshold for value in usable)
        below_count = sum(value < threshold for value in usable)
        ordered = sorted(usable)
        median = None
        if ordered:
            midpoint = len(ordered) // 2
            median = (
                ordered[midpoint]
                if len(ordered) % 2
                else (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")
            )
        submission = GradeSubmission.objects.filter(
            offering=offering,
            template_period=grading_period,
        ).first()
        lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=grading_period)
        if submission and submission.status == GradeSubmission.Status.SUBMITTED:
            state = "Submitted"
        elif lock and lock.is_locked:
            state = "Locked"
        elif enrolled_count and usable_count == enrolled_count:
            state = "Complete"
        else:
            state = "Provisional / incomplete"
        return {
            "rows": rows,
            "active_enrollment_count": enrolled_count,
            "computed_grade_count": usable_count,
            "no_grade_count": enrolled_count - usable_count,
            "coverage_rate": cls._pct(usable_count, enrolled_count),
            "passing_count": passing_count,
            "passing_rate": cls._pct(passing_count, usable_count),
            "below_threshold_count": below_count,
            "below_threshold_rate": cls._pct(below_count, usable_count),
            "average": GradingGovernanceService._round(sum(usable) / Decimal(usable_count)) if usable_count else None,
            "median": GradingGovernanceService._round(median) if median is not None else None,
            "highest": GradingGovernanceService._round(max(usable)) if usable else None,
            "lowest": GradingGovernanceService._round(min(usable)) if usable else None,
            "unusable_reasons": dict(unavailable_reasons),
            "state": state,
            "is_provisional": state == "Provisional / incomplete",
            "distribution": cls._distribution(usable, threshold),
            "passing_threshold": threshold,
        }

    @staticmethod
    def _missing_by_student(readiness):
        return {
            row["student_id"]: int(row.get("missing_activity_records") or 0)
            + int(row.get("missing_attendance_records") or 0)
            for row in readiness.get("missing_students", [])
        }

    @classmethod
    def _distribution(cls, values, threshold):
        rows = []
        for label, lower_offset, upper_offset in cls.DISTRIBUTION_BANDS:
            lower = threshold + lower_offset if lower_offset is not None else None
            upper = threshold + upper_offset if upper_offset is not None else None
            count = sum(
                (lower is None or value >= lower)
                and (upper is None or value < upper)
                for value in values
            )
            rows.append(
                {
                    "label": label,
                    "count": count,
                    "denominator": len(values),
                    "percentage": cls._pct(count, len(values)),
                    "lower": lower,
                    "upper": upper,
                }
            )
        return rows

    @staticmethod
    def _pct(numerator, denominator):
        if not denominator:
            return Decimal("0.0")
        return (Decimal(numerator) * Decimal("100") / Decimal(denominator)).quantize(Decimal("0.1"))

    @classmethod
    def _trend_summary(cls, rows):
        counts = Counter(row.get("trend_label") for row in rows if row.get("usable_grade"))
        return {
            "improving": counts[FacultyPerformanceService.TREND_IMPROVING],
            "stable": counts[FacultyPerformanceService.TREND_STABLE],
            "declining": counts[FacultyPerformanceService.TREND_DECLINING],
            "insufficient": counts[FacultyPerformanceService.TREND_INCOMPLETE]
            + counts[FacultyPerformanceService.TREND_NO_BASELINE],
        }

    @classmethod
    def get_course_section_comparison(cls, request, filters):
        if not cls.required_filters_present(filters):
            return {"rows": [], "limited": False}
        offerings = list(
            cls.get_scoped_offerings(request, filters)[: cls.MAX_LIVE_OFFERINGS + 1]
        )
        limited = len(offerings) > cls.MAX_LIVE_OFFERINGS
        rows = []
        for offering in offerings[: cls.MAX_LIVE_OFFERINGS]:
            period = cls._period_for_offering(offering, filters["period_code"])
            if not period:
                continue
            rows.append(cls.get_section_performance_summary(offering, period))
        grouped_contexts = defaultdict(set)
        comparison_groups = defaultdict(list)
        for row in rows:
            grouped_contexts[row["course_code"]].add(row["grading_context"]["signature"])
            comparison_groups[(row["course_code"], row["grading_context"]["signature"])].append(row)
        for row in rows:
            signatures = grouped_contexts[row["course_code"]]
            peers = comparison_groups[(row["course_code"], row["grading_context"]["signature"])]
            if len(peers) < 2 and len(signatures) > 1:
                row["comparison_status"] = "Not comparable"
                row["comparison_detail"] = "Different grading configuration"
            elif len(peers) < 2:
                row["comparison_status"] = "Not comparable"
                row["comparison_detail"] = "At least two matching sections are required"
            elif row["coverage"]["computed_grade_count"] == 0:
                row["comparison_status"] = "Not comparable"
                row["comparison_detail"] = "Insufficient comparable data"
            else:
                row["comparison_status"] = "Comparable"
                row["comparison_detail"] = "Same template/profile formula and passing threshold"
        max_at_risk = max((row["at_risk_count"] for row in rows), default=0)
        max_missing = max((row["missing_output_count"] for row in rows), default=0)
        for row in rows:
            row["at_risk_bar_width"] = (
                round((row["at_risk_count"] / max_at_risk) * 100, 2) if max_at_risk else 0
            )
            row["missing_bar_width"] = (
                round((row["missing_output_count"] / max_missing) * 100, 2) if max_missing else 0
            )
        return {"rows": rows, "limited": limited}

    @classmethod
    def get_students_for_review(cls, request, filters, *, comparison=None):
        """Advisory student rows only; this method never creates intervention cases."""
        comparison = comparison or cls.get_course_section_comparison(request, filters)
        rows = []
        for section in comparison["rows"]:
            coverage = section["coverage"]
            threshold = Decimal(coverage["passing_threshold"])
            activity_ids = list(
                GradeActivity.objects.filter(
                    offering=section["offering"],
                    template_period=section["period"],
                    is_active=True,
                ).values_list("id", flat=True)
            )
            zero_counts = Counter()
            zero_modes = defaultdict(set)
            for score in StudentActivityScore.objects.filter(
                activity_id__in=activity_ids,
                student_id__in=[row["student_id"] for row in coverage["rows"]],
                is_active=True,
                raw_score=Decimal("0"),
            ).select_related("activity__template_component", "activity__template_subcomponent", "activity__template_detail"):
                zero_counts[score.student_id] += 1
                mode = FacultyGradingService.resolve_score_input_mode(
                    template_component=score.activity.template_component,
                    template_subcomponent=score.activity.template_subcomponent,
                    template_detail=score.activity.template_detail,
                )
                zero_modes[score.student_id].add(mode)
            for student_row in coverage["rows"]:
                indicators = []
                current_grade = student_row.get("current_grade")
                if not student_row["usable_grade"]:
                    indicators.append(student_row["unusable_reason"] or "No usable computed grade")
                elif Decimal(current_grade) < threshold:
                    indicators.append("Below applicable passing threshold")
                elif Decimal(current_grade) < threshold + Decimal("3"):
                    indicators.append("Near applicable passing threshold")
                if (
                    student_row["usable_grade"]
                    and student_row.get("trend_label") == FacultyPerformanceService.TREND_DECLINING
                ):
                    indicators.append("Material decline")
                missing_count = int(student_row.get("missing_output_count") or 0)
                if missing_count >= 2:
                    indicators.append("Multiple missing graded activities")
                if zero_counts[student_row["student_id"]] >= 2:
                    modes = zero_modes[student_row["student_id"]]
                    indicators.append(
                        "Repeated saved zero scores ("
                        + ("Direct Percentage" if "DIRECT_PERCENTAGE" in modes else "Raw/Base")
                        + ")"
                    )
                if indicators:
                    rows.append(
                        {
                            "offering": section["offering"],
                            "period": section["period"],
                            "student": student_row["student"],
                            "student_id": student_row["student_id"],
                            "course_code": section["course_code"],
                            "course_title": section["course_title"],
                            "section": section["section"],
                            "current_grade": current_grade,
                            "usable_grade": student_row["usable_grade"],
                            "passing_threshold": threshold,
                            "missing_output_count": missing_count,
                            "indicators": indicators,
                            "advisory_label": "Academic Concern — For Faculty Review",
                        }
                    )
        return {"rows": rows, "limited": comparison["limited"]}

    @classmethod
    def get_report_interpretation(cls, rows):
        all_rows = list(rows)
        if not all_rows:
            return "No performance data is available yet. Grade encoding may not have started."
        incomplete_count = sum(not row["has_performance_data"] for row in all_rows)
        rows = [row for row in all_rows if row["has_performance_data"]]
        if not rows:
            return "No performance data is available yet. Grade encoding may not have started."
        lowest = min(
            (row for row in rows if row["class_average"] is not None),
            key=lambda row: row["class_average"],
            default=None,
        )
        missing = max(rows, key=lambda row: row["missing_output_count"], default=None)
        if incomplete_count:
            return "One or more sections have incomplete data. Review grade encoding first."
        if lowest and missing and lowest["offering"].id == missing["offering"].id and missing["missing_output_count"]:
            return (
                f"Section {lowest['section']} needs attention because it has the lowest average "
                "and the highest missing output count."
            )
        if lowest and Decimal(lowest["class_average"]) < Decimal(lowest["passing_threshold"]):
            return f"Section {lowest['section']} needs attention because its class average is below passing."
        if missing and missing["missing_output_count"]:
            return f"Section {missing['section']} has the most missing outputs. Review score encoding."
        return "All compared sections are within normal range."

    @classmethod
    def _suggested_check(cls, row):
        if row["status"] == cls.STATUS_INCOMPLETE_DATA:
            return "Review grade encoding or activity setup first."
        if row["missing_output_count"]:
            return "Review missing activity scores and activity setup."
        if row["weakest_component"]:
            return f"Review {row['weakest_component']['name']} performance."
        return "Review component performance."

    @classmethod
    def get_attention_panel(cls, rows):
        rows = list(rows)
        if not rows:
            return {
                "state": "empty",
                "message": "No section performance data is available for the selected filters.",
                "items": [],
            }
        incomplete_rows = [
            row for row in rows if row["status"] == cls.STATUS_INCOMPLETE_DATA
        ]
        attention_rows = [
            row
            for row in rows
            if row["status"] in {cls.STATUS_NEEDS_ATTENTION, cls.STATUS_HIGH_RISK}
        ]
        if incomplete_rows:
            return {
                "state": "incomplete",
                "message": (
                    "Some sections have incomplete data. Review grade encoding or activity "
                    "setup first."
                ),
                "items": [
                    {
                        "section": row["section"],
                        "main_issue": row["main_concern"],
                        "suggested_check": cls._suggested_check(row),
                    }
                    for row in incomplete_rows[:5]
                ],
            }
        if attention_rows:
            return {
                "state": "attention",
                "message": "The following sections need attention.",
                "items": [
                    {
                        "section": row["section"],
                        "main_issue": row["main_concern"],
                        "suggested_check": cls._suggested_check(row),
                    }
                    for row in attention_rows[:5]
                ],
            }
        return {
            "state": "normal",
            "message": "All compared sections are within normal range.",
            "items": [],
        }

    @classmethod
    def get_activity_profile(cls, offering, period):
        components = list(
            period.components.filter(is_active=True)
            .prefetch_related("subcomponents")
            .order_by("sort_order", "id")
        )
        activities = list(
            GradeActivity.objects.filter(
                offering=offering,
                template_period=period,
                is_active=True,
                template_component__is_active=True,
            )
            .select_related("template_component", "template_subcomponent", "template_detail")
            .order_by(
                "template_component__sort_order",
                "template_component__id",
                "template_subcomponent__sort_order",
                "template_subcomponent__id",
                "activity_date",
                "title",
                "id",
            )
        )
        def activity_category(activity):
            if activity.template_subcomponent_id:
                return activity.template_subcomponent.name
            return activity.template_component.name

        counts = Counter(activity_category(activity) for activity in activities)
        activity_ids = [activity.id for activity in activities]
        active_enrollments = FacultyPerformanceService._eligible_enrollments(offering)
        active_student_count = len(active_enrollments)
        active_student_ids = [enrollment.student_id for enrollment in active_enrollments]
        score_counts = {
            row["activity_id"]: row["total"]
            for row in StudentActivityScore.objects.filter(
                activity_id__in=activity_ids,
                student_id__in=active_student_ids,
                is_active=True,
            ).values("activity_id").annotate(total=Count("student_id", distinct=True))
        }
        category_score_counts = Counter()
        category_expected_counts = Counter()
        for activity in activities:
            category = activity_category(activity)
            category_score_counts[category] += score_counts.get(activity.id, 0)
            category_expected_counts[category] += active_student_count
        required_categories = []
        for component in components:
            active_subcomponents = [row for row in component.subcomponents.all() if row.is_active]
            if active_subcomponents:
                required_categories.extend(
                    row.name
                    for row in active_subcomponents
                    if not row.is_attendance_component
                )
            else:
                required_categories.append(component.name)
        missing_components = [name for name in required_categories if counts.get(name, 0) == 0]
        scores = [Decimal(activity.total_score) for activity in activities]
        faculty = cls._faculty_for_offering(offering)
        return {
            "offering": offering,
            "period": period,
            "campus": offering.campus,
            "course_code": offering.course.code,
            "course_title": offering.course.title,
            "section": offering.section.code,
            "faculty_name": cls._faculty_name(faculty),
            "component_counts": dict(counts),
            "total_activities": len(activities),
            "missing_components": missing_components,
            "minimum_max_score": min(scores) if scores else None,
            "maximum_max_score": max(scores) if scores else None,
            "max_score_difference": max(scores) - min(scores) if scores else None,
            "active_student_count": active_student_count,
            "category_missing_score_rates": {
                category: cls._pct(
                    max(category_expected_counts[category] - category_score_counts[category], 0),
                    category_expected_counts[category],
                )
                for category in counts
            },
            "no_score_categories": [
                category for category in counts if category_score_counts[category] == 0
            ],
            "concentrated_categories": [
                component.name
                for component in components
                if Decimal(component.weight_percentage or 0) >= Decimal("70")
            ],
            "grading_context": cls._grading_context(offering, period),
            "activities": activities,
        }

    @classmethod
    def get_activity_consistency_status(cls, profile, group_profiles):
        if profile["missing_components"]:
            return cls.INCOMPLETE_SETUP
        totals = [row["total_activities"] for row in group_profiles]
        difference = max(totals, default=0) - min(totals, default=0)
        if difference == 0:
            return cls.CONSISTENT
        if difference == 1:
            return cls.MINOR_DIFFERENCE
        return cls.NEEDS_REVIEW

    @classmethod
    def get_ready_for_comparison(cls, consistency_status, *, has_comparable_sections):
        if not has_comparable_sections:
            return {
                "label": "Not Available",
                "detail": "No comparable section is available for this course and period.",
                "style": "secondary",
            }
        if consistency_status == cls.CONSISTENT:
            return {
                "label": "Yes",
                "detail": "Activity setup is consistent across comparable sections.",
                "style": "success",
            }
        if consistency_status == cls.MINOR_DIFFERENCE:
            return {
                "label": "Review Activity Setup",
                "detail": "Comparable sections have a small difference in activity counts.",
                "style": "warning",
            }
        if consistency_status == cls.INCOMPLETE_SETUP:
            return {
                "label": "No / Incomplete Data",
                "detail": "A required component has no active activity.",
                "style": "danger",
            }
        if consistency_status == "Not Comparable":
            return {
                "label": "Not Available",
                "detail": "No compatible section is available for this course and period.",
                "style": "secondary",
            }
        return {
            "label": "Review Activity Setup",
            "detail": "Activity counts differ across comparable sections.",
            "style": "warning",
        }

    @classmethod
    def get_activity_setup_summary(cls, profile, consistency_status=None):
        if profile["missing_components"]:
            setup_status = cls.INCOMPLETE_SETUP
        elif consistency_status:
            setup_status = consistency_status
        else:
            setup_status = "Complete"
        return {
            "total_activities": profile["total_activities"],
            "category_counts": profile["component_counts"],
            "status": setup_status,
        }

    @classmethod
    def get_section_review_guidance(cls, summary, ready_for_comparison):
        if summary["status"] == cls.STATUS_INCOMPLETE_DATA:
            return (
                "This section has incomplete data. Review activity setup or grade encoding "
                "before interpreting performance."
            )
        weakest_name = (
            summary["weakest_component"]["name"]
            if summary["weakest_component"]
            else "the component results"
        )
        if summary["status"] == cls.STATUS_HIGH_RISK:
            return (
                "This section needs attention because the class average or at-risk count is "
                f"high. Review {weakest_name} and the encoded activity scores."
            )
        if summary["missing_output_count"]:
            return (
                f"This section has missing outputs. Review score encoding and {weakest_name} "
                "before finalizing the report."
            )
        if ready_for_comparison["label"] != "Yes":
            return (
                f"Review {weakest_name}. Also review the activity setup before making a fair "
                "section comparison."
            )
        return (
            f"This section is performing normally. {weakest_name} is the weakest component, "
            "but the class average remains above passing."
        )

    @classmethod
    def get_section_comparison_context(cls, summary, comparable_rows):
        peers = [
            row
            for row in comparable_rows
            if row["offering"].id != summary["offering"].id
            and row["class_average"] is not None
            and row["grading_context"]["signature"] == summary["grading_context"]["signature"]
        ]
        if summary["class_average"] is None or not peers:
            return "No comparable section is available for this course and period."
        current_average = Decimal(summary["class_average"])
        peer = max(
            peers,
            key=lambda row: abs(current_average - Decimal(row["class_average"])),
        )
        difference = current_average - Decimal(peer["class_average"])
        if difference == 0:
            return f"This section has the same class average as {peer['section']}."
        direction = "higher" if difference > 0 else "lower"
        return (
            f"This section is {abs(difference):.2f} points {direction} than "
            f"{peer['section']}."
        )

    @classmethod
    def get_activity_consistency_comparison(cls, request, filters):
        if not cls.required_filters_present(filters):
            return {"rows": [], "limited": False}
        offerings = list(
            cls.get_scoped_offerings(request, filters)[: cls.MAX_LIVE_OFFERINGS + 1]
        )
        limited = len(offerings) > cls.MAX_LIVE_OFFERINGS
        profiles = []
        for offering in offerings[: cls.MAX_LIVE_OFFERINGS]:
            period = cls._period_for_offering(offering, filters["period_code"])
            if period:
                profiles.append(cls.get_activity_profile(offering, period))
        grouped = defaultdict(list)
        course_profiles = defaultdict(list)
        for profile in profiles:
            course_profiles[profile["course_code"]].append(profile)
            grouped[(profile["course_code"], profile["grading_context"]["signature"])].append(profile)
        rows = []
        for profile in profiles:
            peers = grouped[(profile["course_code"], profile["grading_context"]["signature"])]
            if len(peers) < 2:
                if len(course_profiles[profile["course_code"]]) > 1:
                    profile["consistency_status"] = "Not Comparable"
                    rows.append(profile)
                continue
            profile["consistency_status"] = cls.get_activity_consistency_status(profile, peers)
            rows.append(profile)
        return {
            "rows": rows,
            "limited": limited,
            "has_unmatched_sections": any(
                row.get("consistency_status") == "Not Comparable" for row in rows
            ) or (bool(profiles) and not bool(rows)),
        }

    @classmethod
    def get_activity_interpretation(cls, rows, *, has_unmatched_sections=False):
        if not rows:
            if has_unmatched_sections:
                return "Comparison requires at least two sections with the same course code."
            return "No activities are configured yet for the selected grading period."
        if all(row["consistency_status"] == "Not Comparable" for row in rows):
            return "Selected sections use different grading configurations and are not compared."
        if any(row["consistency_status"] == cls.INCOMPLETE_SETUP for row in rows):
            return "Some sections have incomplete activity setup. Review required components first."
        if any(row["consistency_status"] == cls.NEEDS_REVIEW for row in rows):
            return "Activity counts differ across comparable sections. Review assessment consistency."
        if any(row["consistency_status"] == cls.MINOR_DIFFERENCE for row in rows):
            return "Comparable sections have a small difference in activity counts."
        return "Activity setup is consistent across the compared sections."

    @classmethod
    def get_campus_risk_summary(cls, section_rows):
        grouped = defaultdict(list)
        for row in section_rows:
            department = row["department"]
            grouped[
                (
                    row["campus"].id,
                    row["campus"].code,
                    row["campus"].name,
                    department.id,
                    department.code,
                    department.name,
                )
            ].append(row)
        output = []
        status_priority = {
            cls.STATUS_NORMAL: 0,
            cls.STATUS_NEEDS_ATTENTION: 1,
            cls.STATUS_HIGH_RISK: 2,
            cls.STATUS_INCOMPLETE_DATA: 3,
        }
        for (
            _campus_id,
            code,
            name,
            department_id,
            department_code,
            department_name,
        ), rows in grouped.items():
            averages = [
                Decimal(row["class_average"])
                for row in rows
                if row["class_average"] is not None
            ]
            weakest = Counter(
                row["weakest_component"]["name"]
                for row in rows
                if row["weakest_component"]
            )
            output.append(
                {
                    "campus_code": code,
                    "campus_name": name,
                    "campus_id": _campus_id,
                    "department_id": department_id,
                    "department_code": department_code,
                    "department_name": department_name,
                    "section_count": len(rows),
                    "course_count": len({row["course_code"] for row in rows}),
                    "overall_average": (
                        sum(averages, Decimal("0")) / Decimal(len(averages))
                        if averages
                        else None
                    ),
                    "at_risk_count": sum(row["at_risk_count"] for row in rows),
                    "missing_output_count": sum(row["missing_output_count"] for row in rows),
                    "courses_needing_attention": len(
                        {
                            row["course_code"]
                            for row in rows
                            if row["status"] != cls.STATUS_NORMAL
                        }
                    ),
                    "dominant_weakest_component": weakest.most_common(1)[0][0] if weakest else None,
                    "status": max(
                        (row["status"] for row in rows),
                        key=lambda status: status_priority[status],
                        default=cls.STATUS_INCOMPLETE_DATA,
                    ),
                }
            )
        return sorted(
            output,
            key=lambda row: (row["campus_code"], row["department_code"]),
        )

    @classmethod
    def get_section_detail(cls, offering, period, comparable_rows=None):
        summary = cls.get_section_performance_summary(offering, period)
        comparable_rows = [
            row
            for row in (comparable_rows or [])
            if row["grading_context"]["signature"] == summary["grading_context"]["signature"]
        ]
        comparable_profiles = [
            cls.get_activity_profile(row["offering"], row["period"])
            for row in comparable_rows
        ]
        activity_profile = next(
            (
                profile
                for profile in comparable_profiles
                if profile["offering"].id == offering.id
            ),
            None,
        ) or cls.get_activity_profile(offering, period)
        has_comparable_sections = len(comparable_profiles) > 1
        consistency_status = (
            cls.get_activity_consistency_status(activity_profile, comparable_profiles)
            if has_comparable_sections
            else None
        )
        ready_for_comparison = cls.get_ready_for_comparison(
            consistency_status,
            has_comparable_sections=has_comparable_sections,
        )
        return {
            "summary": summary,
            "components": FacultyPerformanceService.get_class_component_breakdown(
                offering,
                period,
            ),
            "activity_profile": activity_profile,
            "activity_setup_summary": cls.get_activity_setup_summary(
                activity_profile,
                consistency_status,
            ),
            "ready_for_comparison": ready_for_comparison,
            "review_guidance": cls.get_section_review_guidance(
                summary,
                ready_for_comparison,
            ),
            "comparison_context": cls.get_section_comparison_context(
                summary,
                comparable_rows,
            ),
        }
