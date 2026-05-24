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
