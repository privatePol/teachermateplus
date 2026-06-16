from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.academics.services import AcademicGovernanceService
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.academics.models import CourseOffering, FacultyAssignment
from apps.grading.models import (
    GradeActivity,
    GradeCorrectionRequest,
    GradeSubmission,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.predictions.services import PredictionSnapshotService


class FacultyDashboardService:
    @staticmethod
    def _format_decimal_display(value):
        if value in (None, ""):
            return ""
        decimal_value = Decimal(str(value))
        formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _student_name(student):
        return " ".join(
            part
            for part in [
                getattr(student, "last_name", ""),
                getattr(student, "first_name", ""),
                getattr(student, "middle_name", ""),
            ]
            if part
        ).strip()

    @staticmethod
    def _course_label(offering):
        return f"{offering.course.code} / {offering.section.code}"

    @staticmethod
    def _severity_class(severity: str) -> str:
        return {
            "High": "danger",
            "Medium": "warning",
            "Low": "secondary",
        }.get(severity, "secondary")

    @classmethod
    def _action(
        cls,
        *,
        rank: int,
        severity: str,
        summary: str,
        detail: str = "",
        url: str,
        button_label: str,
    ):
        return {
            "rank": rank,
            "severity": severity,
            "severity_class": cls._severity_class(severity),
            "summary": summary,
            "detail": detail,
            "url": url,
            "button_label": button_label,
        }


class FacultyPerformanceService(FacultyDashboardService):
    TREND_AT_RISK = "AT_RISK"
    TREND_INCOMPLETE = "INCOMPLETE"
    TREND_NO_BASELINE = "NO_BASELINE"
    TREND_IMPROVING = "IMPROVING"
    TREND_DECLINING = "DECLINING"
    TREND_STABLE = "STABLE"

    @staticmethod
    def _round(value):
        if value is None:
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"))

    @classmethod
    def _eligible_enrollments(cls, offering, student_ids=None):
        queryset = (
            Enrollment.objects.filter(
                course_offering_id=offering.id,
                is_active=True,
                student__is_active=True,
                student__department__is_active=True,
            )
            .filter(Q(student__program__isnull=True) | Q(student__program__is_active=True))
            .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "student__student_no")
        )
        if student_ids is not None:
            queryset = queryset.filter(student_id__in=student_ids)
        return list(queryset)

    @classmethod
    def _period_context(cls, *, offering, template_period, student_ids):
        template = template_period.template
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related("subcomponents", "subcomponents__details")
            .order_by("sort_order", "id")
        )
        score_lookup = defaultdict(list)
        raw_score_lookup = defaultdict(list)
        scores = StudentActivityScore.objects.filter(
            activity__offering_id=offering.id,
            activity__template_period_id=template_period.id,
            activity__is_active=True,
            student_id__in=student_ids,
            is_active=True,
        ).select_related("activity")
        for score in scores:
            activity = score.activity
            score_lookup[
                (
                    score.student_id,
                    activity.template_component_id,
                    activity.template_subcomponent_id,
                    activity.template_detail_id,
                )
            ].append(Decimal(score.computed_score or 0))
            raw_score_lookup[(score.student_id, activity.template_component_id)].append(
                (Decimal(score.raw_score or 0), Decimal(activity.total_score or 0))
            )
        return {
            "template": template,
            "base_value": FacultyGradingService.resolve_base_value(offering, template),
            "components": components,
            "score_lookup": score_lookup,
            "raw_score_lookup": raw_score_lookup,
        }

    @classmethod
    def _period_details(cls, *, offering, template_period, enrollments, include_details=False):
        student_ids = [enrollment.student_id for enrollment in enrollments]
        if not student_ids:
            return {}
        context = cls._period_context(
            offering=offering,
            template_period=template_period,
            student_ids=student_ids,
        )
        return {
            enrollment.student_id: FacultyGradingService.build_period_grade_detail_for_student(
                offering=offering,
                template_period=template_period,
                student_id=enrollment.student_id,
                template=context["template"],
                base_value=context["base_value"],
                components=context["components"],
                score_lookup=context["score_lookup"],
                raw_score_lookup=context["raw_score_lookup"],
                include_details=include_details,
            )
            for enrollment in enrollments
        }

    @classmethod
    def _missing_by_student(cls, readiness):
        return {
            row["student_id"]: int(row.get("missing_activity_records") or 0)
            for row in readiness.get("missing_students", [])
        }

    @classmethod
    def _weakest_component(cls, details):
        component_values = defaultdict(list)
        component_labels = {}
        for detail in details.values():
            for component in detail.get("component_breakdown", []):
                score = component.get("score")
                if score is None:
                    continue
                code = component.get("code") or str(component.get("id"))
                component_labels[code] = component.get("name") or code
                component_values[code].append(Decimal(score))
        averages = {
            code: sum(values, Decimal("0")) / Decimal(len(values))
            for code, values in component_values.items()
            if values
        }
        if not averages:
            return None
        weakest_code = min(averages, key=lambda code: (averages[code], component_labels[code]))
        return {
            "code": weakest_code,
            "name": component_labels[weakest_code],
            "average": cls._round(averages[weakest_code]),
        }

    @classmethod
    def _previous_period(cls, template_period):
        periods = list(FacultyGradingService.get_template_periods(template_period.template))
        prior_periods = [
            period
            for period in periods
            if (period.sequence_no, period.id) < (template_period.sequence_no, template_period.id)
        ]
        return prior_periods[-1] if prior_periods else None

    @classmethod
    def _build_trend_row(
        cls,
        *,
        enrollment,
        current_detail,
        previous_detail,
        missing_output_count,
        passing_threshold,
    ):
        current_grade = current_detail.get("period_grade")
        previous_grade = previous_detail.get("period_grade") if previous_detail else None
        delta = None
        if current_grade is not None and previous_grade is not None:
            delta = cls._round(Decimal(current_grade) - Decimal(previous_grade))

        weakest = cls._weakest_component({enrollment.student_id: current_detail})
        missing_outputs = []
        for component in current_detail.get("component_breakdown", []):
            for activity in component.get("activities", []):
                if activity.get("missing"):
                    missing_outputs.append(activity.get("title") or "Activity")
            for subcomponent in component.get("subcomponents", []):
                for activity in subcomponent.get("activities", []):
                    if activity.get("missing"):
                        missing_outputs.append(activity.get("title") or "Activity")
                for detail in subcomponent.get("details", []):
                    for activity in detail.get("activities", []):
                        if activity.get("missing"):
                            missing_outputs.append(activity.get("title") or detail.get("name") or "Activity")
        below_passing = current_grade is not None and Decimal(current_grade) < Decimal(passing_threshold)
        if below_passing:
            trend_label = cls.TREND_AT_RISK
            primary_reason = "Below passing grade"
            if missing_output_count:
                primary_reason += f"; {missing_output_count} missing output"
                if missing_output_count != 1:
                    primary_reason += "s"
        elif missing_output_count:
            trend_label = cls.TREND_INCOMPLETE
            primary_reason = "Missing activities"
        elif current_grade is None:
            trend_label = cls.TREND_NO_BASELINE
            primary_reason = "Incomplete data"
        elif previous_grade is None:
            trend_label = cls.TREND_NO_BASELINE
            primary_reason = "No previous baseline"
        elif delta >= Decimal("3"):
            trend_label = cls.TREND_IMPROVING
            primary_reason = f"Improved by {cls._format_decimal_display(delta)} points"
        elif delta <= Decimal("-3"):
            trend_label = cls.TREND_DECLINING
            primary_reason = f"Declined by {cls._format_decimal_display(abs(delta))} points"
        else:
            trend_label = cls.TREND_STABLE
            primary_reason = "Performance is stable"

        return {
            "student": enrollment.student,
            "student_id": enrollment.student_id,
            "student_name": cls._student_name(enrollment.student),
            "student_no": enrollment.student.student_no,
            "current_grade": current_grade,
            "previous_grade": previous_grade,
            "delta": delta,
            "trend_label": trend_label,
            "trend_display": trend_label.replace("_", " "),
            "missing_output_count": missing_output_count,
            "missing_outputs": missing_outputs,
            "weakest_component": weakest,
            "primary_reason": primary_reason,
            "is_below_passing": below_passing,
            "warnings": current_detail.get("warnings", []),
            "detail": current_detail,
        }

    @classmethod
    def _analyze_class(cls, *, offering, grading_period, student_ids=None, include_details=False):
        enrollments = cls._eligible_enrollments(offering, student_ids=student_ids)
        current_details = cls._period_details(
            offering=offering,
            template_period=grading_period,
            enrollments=enrollments,
            include_details=include_details,
        )
        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=offering,
            template_period=grading_period,
        )
        missing_by_student = cls._missing_by_student(readiness)
        previous_period = cls._previous_period(grading_period)
        previous_details = (
            cls._period_details(
                offering=offering,
                template_period=previous_period,
                enrollments=enrollments,
                include_details=False,
            )
            if previous_period
            else {}
        )
        passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
        has_performance_data = readiness["students_with_any_grade"] > 0
        rows = [
            cls._build_trend_row(
                enrollment=enrollment,
                current_detail=current_details.get(enrollment.student_id, {}),
                previous_detail=previous_details.get(enrollment.student_id),
                missing_output_count=missing_by_student.get(enrollment.student_id, 0),
                passing_threshold=passing_threshold,
            )
            for enrollment in enrollments
        ]
        grades = [Decimal(row["current_grade"]) for row in rows if row["current_grade"] is not None]
        missing_output_count = sum(row["missing_output_count"] for row in rows)
        weakest_component = cls._weakest_component(current_details)
        if not enrollments:
            message = "No active students are available for this class."
        elif not has_performance_data:
            message = "No performance data available yet. Encode scores first to generate the class snapshot."
        elif readiness.get("missing_template_bucket_count"):
            message = "The grading setup is incomplete. Add the required activities before relying on this snapshot."
        elif readiness["students_missing_any_grade"]:
            message = "Some required scores or attendance records are still missing."
        else:
            message = ""
        return {
            "rows": rows,
            "class_average": (
                cls._round(sum(grades, Decimal("0")) / Decimal(len(grades)))
                if grades and has_performance_data
                else None
            ),
            "at_risk_count": (
                sum(row["trend_label"] == cls.TREND_AT_RISK for row in rows)
                if has_performance_data
                else 0
            ),
            "missing_output_count": missing_output_count,
            "weakest_component": weakest_component if has_performance_data else None,
            "readiness": readiness,
            "passing_threshold": passing_threshold,
            "previous_period": previous_period,
            "has_performance_data": has_performance_data,
            "message": message,
        }

    @classmethod
    def get_class_performance_snapshot(cls, faculty_load, grading_period):
        return cls._analyze_class(offering=faculty_load, grading_period=grading_period)

    @classmethod
    def get_class_component_breakdown(cls, faculty_load, grading_period):
        analysis = cls._analyze_class(offering=faculty_load, grading_period=grading_period)
        component_values = defaultdict(list)
        component_labels = {}
        for row in analysis["rows"]:
            for component in row["detail"].get("component_breakdown", []):
                score = component.get("score")
                if score is None:
                    continue
                code = component.get("code") or str(component.get("id"))
                component_labels[code] = component.get("name") or code
                component_values[code].append(Decimal(score))
        output = []
        for code, values in component_values.items():
            output.append(
                {
                    "code": code,
                    "name": component_labels[code],
                    "average": cls._round(sum(values, Decimal("0")) / Decimal(len(values))),
                }
            )
        return sorted(output, key=lambda row: (row["average"], row["name"]))

    @classmethod
    def get_student_performance_trend(cls, student, faculty_load, grading_period):
        analysis = cls._analyze_class(
            offering=faculty_load,
            grading_period=grading_period,
            student_ids=[student.id],
            include_details=True,
        )
        return analysis["rows"][0] if analysis["rows"] else None

    @classmethod
    def _student_period_detail_history(cls, student, offering, current_period):
        template = FacultyGradingService.resolve_template_for_offering(offering)
        base_value = FacultyGradingService.resolve_base_value(offering, template)
        periods = list(
            FacultyGradingService.get_template_periods(template)
            .filter(sequence_no__lte=current_period.sequence_no)
            .order_by("sequence_no", "id")
        )
        history = []
        for period in periods:
            components = list(
                period.components.filter(is_active=True)
                .prefetch_related("subcomponents", "subcomponents__details")
                .order_by("sort_order", "id")
            )
            detail = FacultyGradingService.build_period_grade_detail_for_student(
                offering=offering,
                template_period=period,
                student_id=student.id,
                template=template,
                base_value=base_value,
                components=components,
                include_details=False,
            )
            has_score_data = StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=period.id,
                activity__is_active=True,
                student_id=student.id,
                is_active=True,
            ).exists()
            has_attendance_data = AttendanceRecord.objects.filter(
                session__offering_id=offering.id,
                session__template_period_id=period.id,
                session__is_active=True,
                student_id=student.id,
                is_active=True,
            ).exists()
            history.append(
                {
                    "period": period,
                    "detail": detail,
                    "has_data": has_score_data or has_attendance_data,
                }
            )
        return history

    @classmethod
    def get_student_period_grade_trend(
        cls,
        student,
        offering,
        current_period,
        *,
        period_history=None,
    ):
        history = period_history or cls._student_period_detail_history(
            student,
            offering,
            current_period,
        )
        return [
            {
                "period": row["period"].code,
                "period_label": row["period"].name,
                "grade": (
                    float(row["detail"]["period_grade"])
                    if row["has_data"] and row["detail"].get("period_grade") is not None
                    else None
                ),
            }
            for row in history
        ]

    @classmethod
    def get_student_component_trend(
        cls,
        student,
        offering,
        current_period,
        *,
        period_history=None,
    ):
        history = period_history or cls._student_period_detail_history(
            student,
            offering,
            current_period,
        )
        rows = []
        for row in history:
            components = {}
            def add_component_value(label, value, *, parent_label=""):
                resolved_label = label
                if resolved_label in components and parent_label:
                    resolved_label = f"{parent_label} / {label}"
                components[resolved_label] = (
                    float(value)
                    if row["has_data"] and value is not None
                    else None
                )

            for component in row["detail"].get("component_breakdown", []):
                component_name = component.get("name") or component.get("code") or "Component"
                add_component_value(component_name, component.get("score"))
                for subcomponent in component.get("subcomponents", []):
                    subcomponent_name = (
                        subcomponent.get("name")
                        or subcomponent.get("code")
                        or "Subcomponent"
                    )
                    add_component_value(
                        subcomponent_name,
                        subcomponent.get("score"),
                        parent_label=component_name,
                    )
                    for detail in subcomponent.get("details", []):
                        detail_name = detail.get("name") or detail.get("code") or "Detail"
                        add_component_value(
                            detail_name,
                            detail.get("score"),
                            parent_label=subcomponent_name,
                        )
            rows.append(
                {
                    "period": row["period"].code,
                    "period_label": row["period"].name,
                    "components": components,
                }
            )
        return rows

    @staticmethod
    def _chart_x(index, count, *, start=56.0, end=684.0):
        if count <= 1:
            return (start + end) / 2
        return start + ((end - start) * index / (count - 1))

    @staticmethod
    def _chart_y(value, *, top=24.0, bottom=204.0):
        bounded = min(max(float(value), 0.0), 100.0)
        return bottom - ((bottom - top) * bounded / 100.0)

    @classmethod
    def _build_period_grade_chart(cls, period_trend):
        points = []
        segments = []
        active_segment = []
        for index, row in enumerate(period_trend):
            point = {
                **row,
                "x": round(cls._chart_x(index, len(period_trend)), 2),
                "y": (
                    round(cls._chart_y(row["grade"]), 2)
                    if row["grade"] is not None
                    else None
                ),
            }
            points.append(point)
            if point["y"] is None:
                if active_segment:
                    segments.append(" ".join(active_segment))
                    active_segment = []
                continue
            active_segment.append(f"{point['x']},{point['y']}")
        if active_segment:
            segments.append(" ".join(active_segment))
        return {
            "points": points,
            "segments": segments,
            "period_count": len(period_trend),
            "grade_point_count": sum(row["grade"] is not None for row in period_trend),
        }

    @classmethod
    def _build_component_series(cls, component_trend):
        labels = []
        for row in component_trend:
            for label in row["components"]:
                if label not in labels:
                    labels.append(label)
        series = []
        for label in labels:
            values = [row["components"].get(label) for row in component_trend]
            segments = []
            active_segment = []
            points = []
            for index, value in enumerate(values):
                x = round(cls._chart_x(index, len(values), start=8.0, end=192.0), 2)
                y = (
                    round(cls._chart_y(value, top=6.0, bottom=54.0), 2)
                    if value is not None
                    else None
                )
                points.append({"x": x, "y": y, "value": value})
                if y is None:
                    if active_segment:
                        segments.append(" ".join(active_segment))
                        active_segment = []
                    continue
                active_segment.append(f"{x},{y}")
            if active_segment:
                segments.append(" ".join(active_segment))
            series.append(
                {
                    "label": label,
                    "values": values,
                    "points": points,
                    "segments": segments,
                    "has_data": any(value is not None for value in values),
                }
            )
        return series

    @classmethod
    def get_student_trend_interpretation(cls, period_trend, component_trend):
        grade_rows = [row for row in period_trend if row["grade"] is not None]
        if not grade_rows:
            return "No computed grade trend is available yet."
        if len(grade_rows) == 1:
            return (
                f"The student currently has one computed period grade: "
                f"{grade_rows[0]['grade']:.2f} for {grade_rows[0]['period_label']}."
            )
        previous, current = grade_rows[-2], grade_rows[-1]
        delta = current["grade"] - previous["grade"]
        if delta >= 3:
            message = (
                f"The student's grade improved from {previous['grade']:.2f} in "
                f"{previous['period_label']} to {current['grade']:.2f} in "
                f"{current['period_label']}."
            )
        elif delta <= -3:
            message = (
                f"The student's grade declined from {previous['grade']:.2f} in "
                f"{previous['period_label']} to {current['grade']:.2f} in "
                f"{current['period_label']}."
            )
        else:
            message = (
                f"The student's grade remained stable from {previous['grade']:.2f} in "
                f"{previous['period_label']} to {current['grade']:.2f} in "
                f"{current['period_label']}."
            )

        component_rows = [
            row for row in component_trend if any(value is not None for value in row["components"].values())
        ]
        if len(component_rows) >= 2:
            prior_components = component_rows[-2]["components"]
            current_components = component_rows[-1]["components"]
            drops = [
                (label, current_components[label] - previous_value)
                for label, previous_value in prior_components.items()
                if previous_value is not None
                and current_components.get(label) is not None
                and current_components[label] - previous_value < 0
            ]
            if drops:
                largest_drop = min(drops, key=lambda item: item[1])
                message += f" The largest component drop appears in {largest_drop[0]}."
        return message

    @classmethod
    def get_student_trend_visualization(cls, student, offering, current_period):
        history = cls._student_period_detail_history(student, offering, current_period)
        period_trend = cls.get_student_period_grade_trend(
            student,
            offering,
            current_period,
            period_history=history,
        )
        component_trend = cls.get_student_component_trend(
            student,
            offering,
            current_period,
            period_history=history,
        )
        component_series = cls._build_component_series(component_trend)
        return {
            "period_trend": period_trend,
            "component_trend": component_trend,
            "period_chart": cls._build_period_grade_chart(period_trend),
            "component_series": component_series,
            "has_component_data": any(row["has_data"] for row in component_series),
            "interpretation": cls.get_student_trend_interpretation(
                period_trend,
                component_trend,
            ),
        }

    @classmethod
    def get_students_requiring_attention(cls, faculty_load, grading_period):
        analysis = cls._analyze_class(offering=faculty_load, grading_period=grading_period)
        attention_labels = {
            cls.TREND_AT_RISK,
            cls.TREND_INCOMPLETE,
            cls.TREND_DECLINING,
        }
        return [row for row in analysis["rows"] if row["trend_label"] in attention_labels]

    @classmethod
    def get_parallel_sections_for_faculty(cls, faculty, course_code, academic_term):
        return (
            CourseOffering.objects.filter(
                faculty_assignments__faculty_user_id=faculty.id,
                faculty_assignments__is_active=True,
                faculty_assignments__accepted_at__isnull=False,
                faculty_assignments__response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                tenant_id=academic_term.tenant_id,
                academic_year_id=academic_term.academic_year_id,
                term_id=academic_term.id,
                course__code=course_code,
                is_active=True,
                tenant__is_active=True,
                campus__is_active=True,
                academic_year__is_active=True,
                term__is_active=True,
                department__is_active=True,
                program__is_active=True,
                course__is_active=True,
                section__is_active=True,
            )
            .select_related("tenant", "campus", "academic_year", "term", "course", "section")
            .distinct()
            .order_by("section__code", "id")
        )

    @classmethod
    def get_parallel_section_comparison(
        cls,
        faculty,
        course_code,
        academic_term,
        grading_period,
    ):
        rows = []
        period_code = grading_period.code
        for offering in cls.get_parallel_sections_for_faculty(faculty, course_code, academic_term):
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                period = FacultyGradingService.get_template_periods(template).filter(code=period_code).first()
            except Exception:
                period = None
            if not period:
                continue
            snapshot = cls.get_class_performance_snapshot(offering, period)
            rows.append(
                {
                    "offering": offering,
                    "period": period,
                    "section_name": offering.section.code,
                    "class_average": snapshot["class_average"],
                    "at_risk_count": snapshot["at_risk_count"],
                    "missing_output_count": snapshot["missing_output_count"],
                    "weakest_component": snapshot["weakest_component"],
                    "message": snapshot["message"],
                }
            )

        max_at_risk = max((row["at_risk_count"] for row in rows), default=0)
        max_missing = max((row["missing_output_count"] for row in rows), default=0)
        for row in rows:
            row["average_bar_width"] = float(row["class_average"] or 0)
            row["at_risk_bar_width"] = (
                round((row["at_risk_count"] / max_at_risk) * 100, 2) if max_at_risk else 0
            )
            row["missing_bar_width"] = (
                round((row["missing_output_count"] / max_missing) * 100, 2) if max_missing else 0
            )
        return rows

    @classmethod
    def get_parallel_section_interpretation(cls, comparison_data):
        rows = list(comparison_data)
        if len(rows) < 2:
            return "Parallel comparison requires at least two sections of the same course code handled by you."
        rows_with_average = [row for row in rows if row["class_average"] is not None]
        if len(rows_with_average) < 2:
            return "There is not enough encoded grade data to compare these sections yet."

        lowest = min(rows_with_average, key=lambda row: row["class_average"])
        highest = max(rows_with_average, key=lambda row: row["class_average"])
        average_gap = Decimal(highest["class_average"]) - Decimal(lowest["class_average"])
        missing_sorted = sorted(rows, key=lambda row: row["missing_output_count"], reverse=True)
        missing_leader = missing_sorted[0]
        next_missing = missing_sorted[1]["missing_output_count"]

        if average_gap >= Decimal("3"):
            message = (
                f"Section {lowest['section_name']} needs attention because it has the lowest "
                f"class average, {cls._format_decimal_display(lowest['class_average'])}."
            )
            if missing_leader["section_name"] == lowest["section_name"] and missing_leader["missing_output_count"]:
                message += " It also has the highest missing output count."
            return message
        if (
            missing_leader["missing_output_count"] >= 2
            and missing_leader["missing_output_count"] > next_missing
        ):
            return (
                f"The section averages are similar, but Section {missing_leader['section_name']} "
                "has more missing outputs. Review submission compliance for this section."
            )
        return "All sections are performing within a normal range. No section shows a major performance gap."

    @classmethod
    def get_chart_data_for_parallel_sections(cls, comparison_data):
        rows = list(comparison_data)
        return {
            "labels": [row["section_name"] for row in rows],
            "class_averages": [
                float(row["class_average"]) if row["class_average"] is not None else None
                for row in rows
            ],
            "at_risk_counts": [int(row["at_risk_count"]) for row in rows],
            "missing_output_counts": [int(row["missing_output_count"]) for row in rows],
        }


class StudentInterventionService(FacultyDashboardService):
    STATUS_CRITICAL = "CRITICAL"
    STATUS_WARNING = "WARNING"
    STATUS_MISSING_WORK = "MISSING_WORK"
    STATUS_ON_TRACK = "ON_TRACK"

    STATUS_LABELS = {
        STATUS_CRITICAL: "Needs Attention",
        STATUS_WARNING: "Monitor",
        STATUS_MISSING_WORK: "Missing Work",
        STATUS_ON_TRACK: "On Track",
    }
    STATUS_VARIANTS = {
        STATUS_CRITICAL: "danger",
        STATUS_WARNING: "warning",
        STATUS_MISSING_WORK: "info",
        STATUS_ON_TRACK: "success",
    }

    @classmethod
    def _format_decimal_display(cls, value):
        return FacultyDashboardService._format_decimal_display(value)

    @classmethod
    def _student_name(cls, student):
        return FacultyDashboardService._student_name(student)

    @classmethod
    def _course_label(cls, offering):
        return FacultyDashboardService._course_label(offering)

    @classmethod
    def _period_student_evidence(cls, *, offering, period, student_ids, passing_threshold):
        student_ids = set(student_ids)
        evidence = {
            student_id: {
                "missing_activity": False,
                "missing_attendance": False,
                "low_exam": False,
                "low_quiz": False,
            }
            for student_id in student_ids
        }
        if not student_ids:
            return evidence

        activities = list(
            GradeActivity.objects.filter(
                offering_id=offering.id,
                template_period_id=period.id,
                is_active=True,
            ).select_related("template_component", "template_subcomponent", "template_detail")
        )
        if activities:
            activity_ids = [activity.id for activity in activities]
            score_rows = list(
                StudentActivityScore.objects.filter(
                    activity_id__in=activity_ids,
                    student_id__in=student_ids,
                    is_active=True,
                )
                .select_related(
                    "activity",
                    "activity__template_component",
                    "activity__template_subcomponent",
                    "activity__template_detail",
                )
                .only(
                    "student_id",
                    "computed_score",
                    "activity__id",
                    "activity__title",
                    "activity__template_component__code",
                    "activity__template_component__name",
                    "activity__template_component__is_exam_component",
                    "activity__template_subcomponent__code",
                    "activity__template_subcomponent__name",
                    "activity__template_detail__code",
                    "activity__template_detail__name",
                )
            )
            scored_activity_ids_by_student = defaultdict(set)
            exam_values_by_student = defaultdict(list)
            quiz_values_by_student = defaultdict(list)
            threshold = Decimal(passing_threshold)

            def is_quiz_activity(activity):
                labels = [
                    activity.title,
                    getattr(activity.template_component, "code", ""),
                    getattr(activity.template_component, "name", ""),
                    getattr(activity.template_subcomponent, "code", ""),
                    getattr(activity.template_subcomponent, "name", ""),
                    getattr(activity.template_detail, "code", ""),
                    getattr(activity.template_detail, "name", ""),
                ]
                return any("QUIZ" in str(label or "").upper() for label in labels)

            for score in score_rows:
                scored_activity_ids_by_student[score.student_id].add(score.activity_id)
                if score.computed_score is None:
                    continue
                score_value = Decimal(score.computed_score)
                if FacultyGradingService.is_exam_component(score.activity.template_component):
                    exam_values_by_student[score.student_id].append(score_value)
                if is_quiz_activity(score.activity):
                    quiz_values_by_student[score.student_id].append(score_value)

            expected_activity_ids = set(activity_ids)
            for student_id in student_ids:
                if expected_activity_ids - scored_activity_ids_by_student.get(student_id, set()):
                    evidence[student_id]["missing_activity"] = True
                exam_values = exam_values_by_student.get(student_id, [])
                quiz_values = quiz_values_by_student.get(student_id, [])
                if exam_values and (sum(exam_values) / Decimal(len(exam_values))) < threshold:
                    evidence[student_id]["low_exam"] = True
                if quiz_values and (sum(quiz_values) / Decimal(len(quiz_values))) < threshold:
                    evidence[student_id]["low_quiz"] = True

        session_ids = list(
            AttendanceSession.objects.filter(
                offering_id=offering.id,
                template_period_id=period.id,
                is_active=True,
            ).values_list("id", flat=True)
        )
        if session_ids:
            recorded_session_ids_by_student = defaultdict(set)
            for row in AttendanceRecord.objects.filter(
                session_id__in=session_ids,
                student_id__in=student_ids,
                is_active=True,
            ).values("student_id", "session_id"):
                recorded_session_ids_by_student[row["student_id"]].add(row["session_id"])
            expected_session_ids = set(session_ids)
            for student_id in student_ids:
                if expected_session_ids - recorded_session_ids_by_student.get(student_id, set()):
                    evidence[student_id]["missing_attendance"] = True

        return evidence

    @classmethod
    def _classify_row(cls, *, snapshot, passing_threshold, evidence=None):
        evidence = evidence or {}
        current_grade = snapshot.current_projected_period_grade
        remaining_count = int(snapshot.remaining_item_count or 0)
        status_code = cls.STATUS_ON_TRACK
        current_standing = "On track"
        main_concern = "No immediate concern."
        suggested_intervention = "Continue normal monitoring."

        if evidence.get("missing_attendance"):
            return {
                "status_code": cls.STATUS_MISSING_WORK,
                "status_label": cls.STATUS_LABELS[cls.STATUS_MISSING_WORK],
                "status_variant": cls.STATUS_VARIANTS[cls.STATUS_MISSING_WORK],
                "current_standing": "Not ready to assess",
                "main_concern": "Attendance records are not yet complete.",
                "suggested_intervention": "Check attendance records before submission.",
            }

        if evidence.get("missing_activity"):
            return {
                "status_code": cls.STATUS_MISSING_WORK,
                "status_label": cls.STATUS_LABELS[cls.STATUS_MISSING_WORK],
                "status_variant": cls.STATUS_VARIANTS[cls.STATUS_MISSING_WORK],
                "current_standing": "Not ready to assess",
                "main_concern": "Activity scores still need review.",
                "suggested_intervention": "Review missing activity scores.",
            }

        if remaining_count > 0:
            return {
                "status_code": cls.STATUS_MISSING_WORK,
                "status_label": cls.STATUS_LABELS[cls.STATUS_MISSING_WORK],
                "status_variant": cls.STATUS_VARIANTS[cls.STATUS_MISSING_WORK],
                "current_standing": "Not ready to assess",
                "main_concern": "Some scores are not yet encoded.",
                "suggested_intervention": "Confirm whether all scores are complete before taking action.",
            }

        if current_grade is None:
            return {
                "status_code": cls.STATUS_ON_TRACK,
                "status_label": cls.STATUS_LABELS[cls.STATUS_ON_TRACK],
                "status_variant": cls.STATUS_VARIANTS[cls.STATUS_ON_TRACK],
                "current_standing": "Not ready to assess",
                "main_concern": "No immediate concern.",
                "suggested_intervention": "Continue normal monitoring.",
            }

        current_decimal = Decimal(current_grade)
        threshold = Decimal(passing_threshold)
        gap = threshold - current_decimal
        near_threshold_ceiling = threshold + Decimal("3")
        if gap >= Decimal("5"):
            status_code = cls.STATUS_CRITICAL
            current_standing = "Needs attention"
            if evidence.get("low_exam"):
                main_concern = "Exam score needs attention."
                suggested_intervention = "Review exam performance and advise the student if needed."
            elif evidence.get("low_quiz"):
                main_concern = "Quiz performance needs attention."
                suggested_intervention = "Review quiz results and provide follow-up support."
            else:
                main_concern = "Current standing needs attention."
                suggested_intervention = "Advise the student before the period closes."
        elif gap > Decimal("0"):
            status_code = cls.STATUS_WARNING
            current_standing = "Needs attention"
            if evidence.get("low_exam"):
                main_concern = "Exam score needs attention."
                suggested_intervention = "Review exam performance and advise the student if needed."
            elif evidence.get("low_quiz"):
                main_concern = "Quiz performance needs attention."
                suggested_intervention = "Review quiz results and provide follow-up support."
            else:
                main_concern = "Current standing needs attention."
                suggested_intervention = "Advise the student before the period closes."
        elif current_decimal <= near_threshold_ceiling:
            status_code = cls.STATUS_WARNING
            current_standing = "Close to threshold"
            main_concern = "Current standing is close to the passing mark."
            suggested_intervention = "Monitor the next activity and remind the student to complete requirements."

        return {
            "status_code": status_code,
            "status_label": cls.STATUS_LABELS[status_code],
            "status_variant": cls.STATUS_VARIANTS[status_code],
            "current_standing": current_standing,
            "main_concern": main_concern,
            "suggested_intervention": suggested_intervention,
        }

    @classmethod
    def build_monitor_groups(
        cls,
        *,
        user,
        monitored_offerings,
        q: str = "",
        status_filter: str = "",
        include_on_track: bool = False,
    ):
        groups = []
        counts = {
            cls.STATUS_CRITICAL: 0,
            cls.STATUS_WARNING: 0,
            cls.STATUS_MISSING_WORK: 0,
            cls.STATUS_ON_TRACK: 0,
        }
        total_rows = 0
        using_active_period_filter = False
        q_normalized = (q or "").strip().lower()
        allowed_statuses = set(counts)
        selected_status = status_filter if status_filter in allowed_statuses else ""
        active_period_setting_cache = {}

        for offering in monitored_offerings:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                periods = list(FacultyGradingService.get_template_periods(template))
                passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
            except Exception:
                continue

            active_cache_key = (offering.tenant_id, offering.campus_id, offering.term_id)
            if active_cache_key not in active_period_setting_cache:
                active_period_setting_cache[active_cache_key] = AcademicGovernanceService.resolve_active_grading_period(
                    tenant_id=offering.tenant_id,
                    campus_id=offering.campus_id,
                    term_id=offering.term_id,
                )
            active_period_setting = active_period_setting_cache[active_cache_key]
            if active_period_setting:
                using_active_period_filter = True
                periods = [
                    period
                    for period in periods
                    if AcademicGovernanceService.template_period_matches_active_period(
                        template_period=period,
                        active_period_setting=active_period_setting,
                    )
                ]

            for period in periods:
                prediction_data = PredictionSnapshotService.get_period_predictions(
                    offering=offering,
                    template_period=period,
                    user=user,
                )
                student_ids = [snapshot.student_id for snapshot in prediction_data["rows"]]
                evidence_by_student_id = cls._period_student_evidence(
                    offering=offering,
                    period=period,
                    student_ids=student_ids,
                    passing_threshold=passing_threshold,
                )
                group_rows = []
                for snapshot in prediction_data["rows"]:
                    intervention = cls._classify_row(
                        snapshot=snapshot,
                        passing_threshold=passing_threshold,
                        evidence=evidence_by_student_id.get(snapshot.student_id),
                    )
                    counts[intervention["status_code"]] += 1
                    if not include_on_track and intervention["status_code"] == cls.STATUS_ON_TRACK:
                        continue
                    if selected_status and intervention["status_code"] != selected_status:
                        continue
                    student_name = cls._student_name(snapshot.student)
                    class_period_label = f"{cls._course_label(offering)} / {period.name}"
                    if q_normalized and not (
                        q_normalized in (snapshot.student.student_no or "").lower()
                        or q_normalized in student_name.lower()
                        or q_normalized in offering.course.code.lower()
                        or q_normalized in offering.course.title.lower()
                        or q_normalized in offering.section.code.lower()
                        or q_normalized in period.name.lower()
                    ):
                        continue
                    group_rows.append(
                        {
                            "student": snapshot.student,
                            "student_name": student_name,
                            "student_no": snapshot.student.student_no,
                            "class_period": class_period_label,
                            "offering": offering,
                            "period": period,
                            "action_url": reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                            **intervention,
                        }
                    )
                    total_rows += 1

                if group_rows:
                    groups.append(
                        {
                            "offering": offering,
                            "period": period,
                            "class_period": f"{offering.course.title} ({offering.course.code}) / {period.name}",
                            "rows": sorted(
                                group_rows,
                                key=lambda row: (
                                    {
                                        cls.STATUS_MISSING_WORK: 0,
                                        cls.STATUS_CRITICAL: 1,
                                        cls.STATUS_WARNING: 2,
                                        cls.STATUS_ON_TRACK: 3,
                                    }[row["status_code"]],
                                    row["student_name"],
                                ),
                            ),
                        }
                    )

        return {
            "groups": groups,
            "counts": counts,
            "total_rows": total_rows,
            "using_active_period_filter": using_active_period_filter,
        }

    @classmethod
    def build_priority_actions(cls, *, user, active_offerings, now=None, at_risk_total: int = 0):
        now = now or timezone.now()
        active_offerings = list(active_offerings)
        if not active_offerings:
            return []

        active_offering_ids = [offering.id for offering in active_offerings]
        active_enrollments = list(
            Enrollment.objects.filter(
                course_offering_id__in=active_offering_ids,
                is_active=True,
                enrollment_status=Enrollment.Status.ACTIVE,
            ).only("course_offering_id", "student_id")
        )
        active_student_ids_by_offering = defaultdict(list)
        for enrollment in active_enrollments:
            active_student_ids_by_offering[enrollment.course_offering_id].append(enrollment.student_id)

        grade_lookup = {
            (row.offering_id, row.template_period_id, row.student_id): row.period_grade
            for row in StudentPeriodGrade.objects.filter(
                offering_id__in=active_offering_ids,
            ).only("offering_id", "template_period_id", "student_id", "period_grade")
        }

        actions = []
        past_deadline_count = 0
        near_deadline_count = 0
        locked_reopened_count = 0
        first_past_deadline = None
        first_near_deadline = None
        first_locked_reopened = None
        missing_grade_classes = set()
        first_missing_grade = None

        for offering in active_offerings:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                periods = list(FacultyGradingService.get_template_periods(template))
            except Exception:
                continue
            student_ids = active_student_ids_by_offering.get(offering.id, [])
            for period in periods:
                GradingGovernanceService.auto_lock_expired_reopened_gradebook(
                    offering=offering,
                    template_period=period,
                    at=now,
                )
                submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
                is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)
                is_auto_locked_reopened = GradingGovernanceService.is_auto_locked_reopened_after_deadline(
                    offering=offering,
                    template_period=period,
                )
                if is_auto_locked_reopened:
                    locked_reopened_count += 1
                    first_locked_reopened = first_locked_reopened or (offering, period)
                    continue
                if not is_submitted:
                    deadline = GradingGovernanceService.resolve_submission_deadline(
                        offering=offering,
                        template_period=period,
                    )
                    if deadline and deadline < now:
                        past_deadline_count += 1
                        first_past_deadline = first_past_deadline or (offering, period, deadline)
                    elif deadline and now <= deadline <= now + timezone.timedelta(hours=48):
                        near_deadline_count += 1
                        first_near_deadline = first_near_deadline or (offering, period, deadline)

                for student_id in student_ids:
                    if grade_lookup.get((offering.id, period.id, student_id)) is None:
                        missing_grade_classes.add(offering.id)
                        first_missing_grade = first_missing_grade or (offering, period)
                        break

        if first_locked_reopened:
            offering, period = first_locked_reopened
            actions.append(
                cls._action(
                    rank=0,
                    severity="High",
                    summary=(
                        f"{locked_reopened_count} reopened gradebook"
                        f"{'' if locked_reopened_count == 1 else 's'} "
                        "is locked after the deadline and needs resubmission."
                    ),
                    detail=f"Open {cls._course_label(offering)} - {period.name} Summary to finalize and resubmit.",
                    url=reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                    button_label="Resubmit Gradebook",
                )
            )

        if first_past_deadline:
            offering, period, deadline = first_past_deadline
            actions.append(
                cls._action(
                    rank=1,
                    severity="High",
                    summary=(
                        f"{past_deadline_count} class period"
                        f"{'' if past_deadline_count == 1 else 's'} "
                        "is past the submission deadline and still not submitted."
                    ),
                    detail=f"Start with {cls._course_label(offering)} - {period.name}.",
                    url=reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                    button_label="Open Class",
                )
            )

        missing_exam = cls._missing_exam_score_action_data(
            active_offering_ids=active_offering_ids,
            active_student_ids_by_offering=active_student_ids_by_offering,
        )
        if missing_exam["count"]:
            activity = missing_exam["activity"]
            actions.append(
                cls._action(
                    rank=2,
                    severity="High",
                    summary=(
                        f"{missing_exam['count']} student"
                        f"{'' if missing_exam['count'] == 1 else 's'} "
                        "has missing exam scores."
                    ),
                    detail=(
                        f"Start with {cls._course_label(activity.offering)} - "
                        f"{activity.template_period.name}: {activity.title}."
                    ),
                    url=reverse(
                        "faculty_portal:activity_scores",
                        args=[activity.offering_id, activity.template_period_id, activity.id],
                    ),
                    button_label="Complete Scores",
                )
            )

        if missing_grade_classes and first_missing_grade:
            offering, period = first_missing_grade
            count = len(missing_grade_classes)
            actions.append(
                cls._action(
                    rank=2,
                    severity="High",
                    summary=(
                        f"{count} class"
                        f"{'' if count == 1 else 'es'} "
                        "has missing grades that may affect submission."
                    ),
                    detail=f"Review {cls._course_label(offering)} - {period.name}.",
                    url=reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                    button_label="Review Missing Grades",
                )
            )

        if at_risk_total:
            follow_up_verb = "needs" if at_risk_total == 1 else "need"
            actions.append(
                cls._action(
                    rank=3,
                    severity="Medium",
                    summary=(
                        f"{at_risk_total} student"
                        f"{'' if at_risk_total == 1 else 's'} "
                        f"{follow_up_verb} follow-up this grading period."
                    ),
                    detail="Open the intervention monitor to review who needs follow-up first.",
                    url=reverse("faculty_portal:student_at_risk_monitor"),
                    button_label="View Students",
                )
            )

        if first_near_deadline:
            offering, period, deadline = first_near_deadline
            actions.append(
                cls._action(
                    rank=4,
                    severity="Medium",
                    summary=(
                        f"{near_deadline_count} class period"
                        f"{'' if near_deadline_count == 1 else 's'} "
                        "has a submission deadline within the next 48 hours."
                    ),
                    detail=f"Next up: {cls._course_label(offering)} - {period.name}.",
                    url=reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                    button_label="Open Class",
                )
            )

        correction_request = (
            GradeCorrectionRequest.objects.filter(
                offering_id__in=active_offering_ids,
                requested_by_user=user,
                status__in=[
                    GradeCorrectionRequest.Status.PENDING,
                    GradeCorrectionRequest.Status.APPROVED,
                ],
            )
            .select_related("offering", "offering__course", "offering__section", "template_period")
            .order_by("created_at")
            .first()
        )
        if correction_request:
            correction_count = GradeCorrectionRequest.objects.filter(
                offering_id__in=active_offering_ids,
                requested_by_user=user,
                status__in=[
                    GradeCorrectionRequest.Status.PENDING,
                    GradeCorrectionRequest.Status.APPROVED,
                ],
            ).count()
            actions.append(
                cls._action(
                    rank=5,
                    severity="Medium",
                    summary=(
                        f"{correction_count} correction request"
                        f"{'' if correction_count == 1 else 's'} "
                        "is waiting for review or faculty follow-up."
                    ),
                    detail=(
                        f"Open {cls._course_label(correction_request.offering)} - "
                        f"{correction_request.template_period.name}."
                    ),
                    url=reverse(
                        "faculty_portal:period_corrections",
                        args=[correction_request.offering_id, correction_request.template_period_id],
                    ),
                    button_label="Open Requests",
                )
            )

        activity_without_scores = (
            GradeActivity.objects.filter(
                offering_id__in=active_offering_ids,
                is_active=True,
            )
            .select_related("offering", "offering__course", "offering__section", "template_period")
            .annotate(active_score_count=Count("student_scores", filter=Q(student_scores__is_active=True)))
            .filter(active_score_count=0)
            .order_by("activity_date", "id")
            .first()
        )
        if activity_without_scores:
            activity_count = (
                GradeActivity.objects.filter(
                    offering_id__in=active_offering_ids,
                    is_active=True,
                )
                .annotate(active_score_count=Count("student_scores", filter=Q(student_scores__is_active=True)))
                .filter(active_score_count=0)
                .count()
            )
            actions.append(
                cls._action(
                    rank=6,
                    severity="Low",
                    summary=(
                        f"{activity_count} activit"
                        f"{'y' if activity_count == 1 else 'ies'} "
                        "still has no scores encoded."
                    ),
                    detail=(
                        f"Start with {cls._course_label(activity_without_scores.offering)} - "
                        f"{activity_without_scores.title}."
                    ),
                    url=reverse(
                        "faculty_portal:activity_scores",
                        args=[
                            activity_without_scores.offering_id,
                            activity_without_scores.template_period_id,
                            activity_without_scores.id,
                        ],
                    ),
                    button_label="Complete Scores",
                )
            )

        return sorted(actions, key=lambda item: (item["rank"], item["summary"]))

    @classmethod
    def _missing_exam_score_action_data(cls, *, active_offering_ids, active_student_ids_by_offering):
        exam_activities = list(
            GradeActivity.objects.filter(
                offering_id__in=active_offering_ids,
                is_active=True,
                template_component__is_exam_component=True,
            ).select_related(
                "offering",
                "offering__course",
                "offering__section",
                "template_period",
            )
        )
        if not exam_activities:
            return {"count": 0, "activity": None}

        activity_ids = [activity.id for activity in exam_activities]
        score_pairs = set(
            StudentActivityScore.objects.filter(
                activity_id__in=activity_ids,
                is_active=True,
            ).values_list("activity_id", "student_id")
        )
        missing_count = 0
        first_activity = None
        for activity in exam_activities:
            for student_id in active_student_ids_by_offering.get(activity.offering_id, []):
                if (activity.id, student_id) in score_pairs:
                    continue
                missing_count += 1
                first_activity = first_activity or activity
        return {"count": missing_count, "activity": first_activity}

    @classmethod
    def build_at_risk_students_preview(cls, *, user, active_offerings, tenant_id=None, limit: int = 5):
        active_offerings = list(active_offerings)
        if not active_offerings:
            return {"enabled": True, "rows": [], "total_count": 0, "view_all_url": reverse("faculty_portal:student_at_risk_monitor")}
        prediction_enabled = FeatureSettingsService.can_user_access_grade_prediction(user=user, tenant_id=tenant_id)
        at_risk_enabled = FeatureSettingsService.is_grade_prediction_at_risk_enabled(tenant_id=tenant_id, default=True)
        view_all_url = reverse("faculty_portal:student_at_risk_monitor")
        if not prediction_enabled or not at_risk_enabled:
            return {"enabled": False, "rows": [], "total_count": 0, "view_all_url": view_all_url}

        rows = []
        total_count = 0
        for offering in active_offerings:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                periods = list(FacultyGradingService.get_template_periods(template))
            except Exception:
                continue
            passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
            for period in periods:
                prediction_data = PredictionSnapshotService.get_period_predictions(
                    offering=offering,
                    template_period=period,
                    user=user,
                )
                evidence_by_student_id = cls._period_student_evidence(
                    offering=offering,
                    period=period,
                    student_ids=[snapshot.student_id for snapshot in prediction_data["rows"]],
                    passing_threshold=passing_threshold,
                )
                for snapshot in prediction_data["rows"]:
                    intervention = cls._classify_row(
                        snapshot=snapshot,
                        passing_threshold=passing_threshold,
                        evidence=evidence_by_student_id.get(snapshot.student_id),
                    )
                    if intervention["status_code"] == cls.STATUS_ON_TRACK:
                        continue
                    total_count += 1
                    rows.append(
                        cls._at_risk_preview_row(
                            offering=offering,
                            period=period,
                            snapshot=snapshot,
                            passing_threshold=passing_threshold,
                            intervention=intervention,
                        )
                    )

        rows.sort(key=lambda row: row["sort_key"])
        for row in rows:
            row.pop("sort_key", None)
        return {
            "enabled": True,
            "rows": rows[:limit],
            "total_count": total_count,
            "view_all_url": view_all_url,
        }

    @classmethod
    def _at_risk_preview_row(cls, *, offering, period, snapshot, passing_threshold, intervention):
        current_grade = snapshot.current_projected_period_grade
        grade_gap = Decimal("0")
        if current_grade is not None:
            try:
                grade_gap = max(Decimal(passing_threshold) - Decimal(current_grade), Decimal("0"))
            except (InvalidOperation, TypeError, ValueError):
                grade_gap = Decimal("0")
        remaining_count = int(snapshot.remaining_item_count or 0)

        return {
            "student_name": cls._student_name(snapshot.student),
            "student_no": snapshot.student.student_no,
            "class_label": cls._course_label(offering),
            "period_name": period.name,
            "risk_reason": intervention["main_concern"],
            "estimated_grade": current_grade,
            "estimated_grade_display": cls._format_decimal_display(current_grade),
            "status_label": intervention["status_label"],
            "status_variant": intervention["status_variant"],
            "url": reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
            "sort_key": (
                {
                    cls.STATUS_MISSING_WORK: 0,
                    cls.STATUS_CRITICAL: 1,
                    cls.STATUS_WARNING: 2,
                    cls.STATUS_ON_TRACK: 3,
                }[intervention["status_code"]],
                -grade_gap,
                -remaining_count,
                snapshot.student.last_name,
                snapshot.student.first_name,
            ),
        }
