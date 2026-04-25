from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradeCorrectionRequest,
    GradeSubmission,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.predictions.services import PredictionComputationService, PredictionSnapshotService


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
        first_past_deadline = None
        first_near_deadline = None
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
                submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
                is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)
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
            actions.append(
                cls._action(
                    rank=3,
                    severity="Medium",
                    summary=(
                        f"{at_risk_total} student"
                        f"{'' if at_risk_total == 1 else 's'} "
                        "is currently at risk this grading period."
                    ),
                    detail="Open the at-risk monitor to review who needs follow-up first.",
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
                for snapshot in prediction_data["rows"]:
                    if not getattr(snapshot, "at_risk_flag", False):
                        continue
                    total_count += 1
                    final_requirement = PredictionComputationService.final_requirement_for_remaining_periods(
                        offering=offering,
                        template_period=period,
                        student_id=snapshot.student_id,
                        current_period_grade=snapshot.current_projected_period_grade,
                    )
                    rows.append(
                        cls._at_risk_preview_row(
                            offering=offering,
                            period=period,
                            snapshot=snapshot,
                            passing_threshold=passing_threshold,
                            final_requirement=final_requirement,
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
    def _at_risk_preview_row(cls, *, offering, period, snapshot, passing_threshold, final_requirement):
        current_grade = snapshot.current_projected_period_grade
        grade_gap = Decimal("0")
        if current_grade is not None:
            try:
                grade_gap = max(Decimal(passing_threshold) - Decimal(current_grade), Decimal("0"))
            except (InvalidOperation, TypeError, ValueError):
                grade_gap = Decimal("0")
        remaining_count = int(snapshot.remaining_item_count or 0)
        coverage = Decimal(snapshot.coverage_percent or 0)
        status_label = "At Risk" if grade_gap >= Decimal("5") or final_requirement["status"] == "NOT_REACHABLE" else "Needs Attention"
        status_variant = "danger" if status_label == "At Risk" else "warning"
        if current_grade is not None:
            reason = "Estimated grade below passing"
        elif remaining_count > 1:
            reason = "Multiple missing scores"
        elif remaining_count == 1:
            reason = "Missing required score entry"
        else:
            reason = "Predicted grade below passing"

        return {
            "student_name": cls._student_name(snapshot.student),
            "student_no": snapshot.student.student_no,
            "class_label": cls._course_label(offering),
            "period_name": period.name,
            "risk_reason": reason,
            "estimated_grade": current_grade,
            "estimated_grade_display": cls._format_decimal_display(current_grade),
            "status_label": status_label,
            "status_variant": status_variant,
            "url": reverse("faculty_portal:period_prediction", args=[offering.id, period.id]),
            "sort_key": (-grade_gap, -remaining_count, coverage, snapshot.student.last_name, snapshot.student.first_name),
        }
