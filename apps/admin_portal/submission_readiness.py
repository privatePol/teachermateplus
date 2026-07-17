from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, FacultyAssignment
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradeEncodingControl,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService


@dataclass(frozen=True)
class GradeSubmissionReadinessResult:
    assignment: FacultyAssignment
    template_period: GradingTemplatePeriod | None
    status: str
    status_label: str
    progress_percent: Decimal
    last_activity_at: object | None
    submission_deadline: object | None
    submission_eligible: bool
    submission_blockers: tuple[str, ...]


class GradeSubmissionReadinessService:
    """Compute operational submission readiness without exposing student data."""

    READY = "READY"
    NEARLY_READY = "NEARLY_READY"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    SUBMITTED = "SUBMITTED"
    OVERDUE = "OVERDUE"

    STATUS_LABELS = {
        READY: "Ready",
        NEARLY_READY: "Nearly Ready",
        NEEDS_ATTENTION: "Needs Attention",
        SUBMITTED: "Submitted",
        OVERDUE: "Overdue",
    }
    STATUS_PRIORITY = {
        OVERDUE: 0,
        NEEDS_ATTENTION: 1,
        NEARLY_READY: 2,
        READY: 3,
        SUBMITTED: 4,
    }
    NEARLY_READY_THRESHOLD = Decimal("90.00")

    @staticmethod
    def _round_percent(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _latest(current, candidate):
        if candidate is None:
            return current
        if current is None or candidate > current:
            return candidate
        return current

    @classmethod
    def _normalized_period(cls, value):
        return GradingGovernanceService._normalize_period_key(value)

    @classmethod
    def _resolve_periods(cls, assignments, selected_period_code):
        active_setting_map = {
            (row.tenant_id, row.campus_id, row.term_id): row.period.code
            for row in ActiveGradingPeriodSetting.objects.filter(
                is_active=True,
                tenant_id__in={a.offering.tenant_id for a in assignments},
                campus_id__in={a.offering.campus_id for a in assignments},
                term_id__in={a.offering.term_id for a in assignments},
                period__is_active=True,
            ).select_related("period")
        }

        template_cache = {}
        template_by_assignment = {}
        for assignment in assignments:
            offering = assignment.offering
            cache_key = (
                offering.tenant_id,
                offering.term_id,
                offering.course_id,
                offering.department_id,
                offering.program_id,
                offering.section.program_id,
            )
            if cache_key not in template_cache:
                try:
                    template_cache[cache_key] = FacultyGradingService.resolve_template_for_offering(offering)
                except ValidationError:
                    template_cache[cache_key] = None
            template_by_assignment[assignment.id] = template_cache[cache_key]

        template_ids = {template.id for template in template_cache.values() if template is not None}
        periods = list(
            GradingTemplatePeriod.objects.filter(
                grading_template_id__in=template_ids,
                is_active=True,
            )
            .prefetch_related(
                Prefetch(
                    "components",
                    queryset=GradingTemplateComponent.objects.filter(is_active=True)
                    .prefetch_related(
                        Prefetch(
                            "subcomponents",
                            queryset=GradingTemplateSubcomponent.objects.filter(is_active=True)
                            .prefetch_related(
                                Prefetch(
                                    "details",
                                    queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by(
                                        "sort_order", "id"
                                    ),
                                )
                            )
                            .order_by("sort_order", "id"),
                        )
                    )
                    .order_by("sort_order", "id"),
                )
            )
            .order_by("sequence_no", "id")
        )
        period_map = {}
        for period in periods:
            period_map[(period.grading_template_id, cls._normalized_period(period.code))] = period
            period_map.setdefault((period.grading_template_id, cls._normalized_period(period.name)), period)

        result = {}
        for assignment in assignments:
            template = template_by_assignment.get(assignment.id)
            if template is None:
                result[assignment.id] = None
                continue
            offering = assignment.offering
            target_code = selected_period_code or active_setting_map.get(
                (offering.tenant_id, offering.campus_id, offering.term_id)
            )
            result[assignment.id] = period_map.get((template.id, cls._normalized_period(target_code)))
        return result

    @classmethod
    def calculate(cls, assignments, *, selected_period_code=None, now=None):
        assignments = list(assignments)
        if not assignments:
            return []
        now = now or timezone.now()
        period_by_assignment = cls._resolve_periods(assignments, selected_period_code)
        offering_ids = {assignment.offering_id for assignment in assignments}
        period_ids = {period.id for period in period_by_assignment.values() if period is not None}

        eligible_students = defaultdict(set)
        for offering_id, student_id in (
            Enrollment.objects.filter(
                course_offering_id__in=offering_ids,
                is_active=True,
                student__is_active=True,
                student__department__is_active=True,
            )
            .filter(Q(student__program__isnull=True) | Q(student__program__is_active=True))
            .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
            .values_list("course_offering_id", "student_id")
        ):
            eligible_students[offering_id].add(student_id)

        activity_rows = list(
            GradeActivity.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
                is_active=True,
                template_component__is_active=True,
            )
            .filter(
                Q(template_subcomponent__isnull=True, template_detail__isnull=True)
                | Q(template_subcomponent__is_active=True, template_detail__isnull=True)
                | Q(template_subcomponent__is_active=True, template_detail__is_active=True)
            )
            .values(
                "id",
                "offering_id",
                "template_period_id",
                "template_component_id",
                "template_subcomponent_id",
                "template_detail_id",
                "updated_at",
            )
        )
        activities_by_key = defaultdict(list)
        activity_key_by_id = {}
        for row in activity_rows:
            key = (row["offering_id"], row["template_period_id"])
            activities_by_key[key].append(row)
            activity_key_by_id[row["id"]] = key

        session_rows = list(
            AttendanceSession.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
                is_active=True,
            ).values("id", "offering_id", "template_period_id", "updated_at")
        )
        sessions_by_key = defaultdict(list)
        session_key_by_id = {}
        for row in session_rows:
            key = (row["offering_id"], row["template_period_id"])
            sessions_by_key[key].append(row)
            session_key_by_id[row["id"]] = key

        score_counts = defaultdict(lambda: defaultdict(set))
        score_students = defaultdict(set)
        score_last_activity = {}
        for row in StudentActivityScore.objects.filter(
            activity_id__in=activity_key_by_id,
            is_active=True,
        ).values("activity_id", "student_id", "updated_at"):
            key = activity_key_by_id[row["activity_id"]]
            score_counts[key][row["student_id"]].add(row["activity_id"])
            score_students[key].add(row["student_id"])
            score_last_activity[key] = cls._latest(score_last_activity.get(key), row["updated_at"])

        attendance_counts = defaultdict(lambda: defaultdict(set))
        attendance_students = defaultdict(set)
        attendance_last_activity = {}
        for row in AttendanceRecord.objects.filter(
            session_id__in=session_key_by_id,
            is_active=True,
        ).values("session_id", "student_id", "updated_at"):
            key = session_key_by_id[row["session_id"]]
            attendance_counts[key][row["student_id"]].add(row["session_id"])
            attendance_students[key].add(row["student_id"])
            attendance_last_activity[key] = cls._latest(attendance_last_activity.get(key), row["updated_at"])

        submissions = {
            (row.offering_id, row.template_period_id): row
            for row in GradeSubmission.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
            )
        }
        period_grade_last_activity = {}
        for row in (
            StudentPeriodGrade.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
            )
            .values("offering_id", "template_period_id", "updated_at")
        ):
            key = (row["offering_id"], row["template_period_id"])
            period_grade_last_activity[key] = cls._latest(period_grade_last_activity.get(key), row["updated_at"])

        locks = list(
            GradingPeriodLock.objects.filter(
                is_active=True,
                tenant_id__in={a.offering.tenant_id for a in assignments},
                campus_id__in={a.offering.campus_id for a in assignments},
                academic_year_id__in={a.offering.academic_year_id for a in assignments},
                term_id__in={a.offering.term_id for a in assignments},
            ).order_by("-updated_at", "-id")
        )
        controls = list(
            GradeEncodingControl.objects.filter(
                is_active=True,
                status=GradeEncodingControl.Status.CLOSED,
                tenant_id__in={a.offering.tenant_id for a in assignments},
                academic_year_id__in={a.offering.academic_year_id for a in assignments},
                term_id__in={a.offering.term_id for a in assignments},
            ).order_by("-updated_at", "-id")
        )

        results = []
        for assignment in assignments:
            offering = assignment.offering
            period = period_by_assignment.get(assignment.id)
            blockers = []
            if period is None:
                blockers.append("No matching active grading period or published template is configured.")
                results.append(
                    GradeSubmissionReadinessResult(
                        assignment=assignment,
                        template_period=None,
                        status=cls.NEEDS_ATTENTION,
                        status_label=cls.STATUS_LABELS[cls.NEEDS_ATTENTION],
                        progress_percent=Decimal("0.00"),
                        last_activity_at=None,
                        submission_deadline=None,
                        submission_eligible=False,
                        submission_blockers=tuple(blockers),
                    )
                )
                continue

            key = (offering.id, period.id)
            active_activities = activities_by_key[key]
            active_sessions = sessions_by_key[key]
            activity_buckets = {
                (row["template_component_id"], row["template_subcomponent_id"], row["template_detail_id"])
                for row in active_activities
            }
            components = list(period.components.all())
            requirements = GradingGovernanceService._template_activity_requirements(
                offering=offering,
                template_period=period,
                components=components,
                activity_buckets=activity_buckets,
            )
            required_count = len(requirements["required_items"])
            missing_setup_count = len(requirements["missing_items"])
            has_attendance_component = any(
                subcomponent.is_attendance_component
                for component in components
                for subcomponent in component.subcomponents.all()
            )

            eligible_ids = eligible_students[offering.id]
            expected_activity_count = len(active_activities)
            expected_attendance_count = len(active_sessions) if has_attendance_component else 0
            completed_students = 0
            for student_id in eligible_ids:
                if (
                    len(score_counts[key].get(student_id, set())) >= expected_activity_count
                    and len(attendance_counts[key].get(student_id, set())) >= expected_attendance_count
                ):
                    completed_students += 1
            student_coverage = (
                cls._round_percent(Decimal(completed_students) * Decimal("100") / Decimal(len(eligible_ids)))
                if eligible_ids
                else Decimal("0.00")
            )
            setup_coverage = (
                cls._round_percent(
                    Decimal(max(required_count - missing_setup_count, 0)) * Decimal("100") / Decimal(required_count)
                )
                if required_count
                else Decimal("0.00")
            )
            has_any_records = bool((score_students[key] | attendance_students[key]) & eligible_ids)
            progress = min(student_coverage, setup_coverage) if has_any_records else Decimal("0.00")

            submission = submissions.get(key)
            is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)

            normalized_period = cls._normalized_period(period.code or period.name)
            matching_locks = [
                lock
                for lock in locks
                if lock.tenant_id == offering.tenant_id
                and lock.campus_id == offering.campus_id
                and lock.academic_year_id == offering.academic_year_id
                and lock.term_id == offering.term_id
                and cls._normalized_period(lock.period_code) == normalized_period
                and (
                    (lock.scope_type == GradingPeriodLock.ScopeType.COURSE and lock.course_offering_id == offering.id)
                    or (lock.scope_type == GradingPeriodLock.ScopeType.CAMPUS and lock.course_offering_id is None)
                )
            ]
            matching_locks.sort(
                key=lambda lock: (1 if lock.course_offering_id else 0, lock.updated_at, lock.id),
                reverse=True,
            )
            lock = matching_locks[0] if matching_locks else None
            deadline = lock.deadline_at if lock else None
            is_overdue = bool(deadline and deadline < now and not is_submitted)

            closed_control = None
            applicable_controls = [
                control
                for control in controls
                if control.tenant_id == offering.tenant_id
                and control.academic_year_id == offering.academic_year_id
                and control.term_id == offering.term_id
                and (not control.period_code or cls._normalized_period(control.period_code) == normalized_period)
                and (control.campus_id is None or control.campus_id == offering.campus_id)
                and (control.course_offering_id is None or control.course_offering_id == offering.id)
            ]
            applicable_controls.sort(
                key=lambda control: (
                    1 if control.course_offering_id else 0,
                    1 if control.campus_id else 0,
                    1 if control.period_code else 0,
                    control.updated_at,
                    control.id,
                ),
                reverse=True,
            )
            if applicable_controls:
                closed_control = applicable_controls[0]

            if not eligible_ids:
                blockers.append("No eligible active students are available for submission.")
            if not has_any_records:
                blockers.append("No grade or attendance records have been encoded.")
            if missing_setup_count:
                blockers.append("Required grading setup is incomplete.")
            if completed_students < len(eligible_ids):
                blockers.append("Some eligible students still have incomplete required records.")
            if lock and lock.is_locked:
                blockers.append("The grading period is locked.")
            if closed_control:
                blockers.append("Grade encoding is closed by academic governance.")
            if is_overdue:
                blockers.append("The submission deadline has passed.")

            eligible = not blockers and not is_submitted
            if is_submitted:
                status = cls.SUBMITTED
                progress = Decimal("100.00")
            elif is_overdue:
                status = cls.OVERDUE
            elif eligible:
                status = cls.READY
            elif progress >= cls.NEARLY_READY_THRESHOLD:
                status = cls.NEARLY_READY
            else:
                status = cls.NEEDS_ATTENTION

            last_activity = None
            for row in active_activities:
                last_activity = cls._latest(last_activity, row["updated_at"])
            for row in active_sessions:
                last_activity = cls._latest(last_activity, row["updated_at"])
            last_activity = cls._latest(last_activity, score_last_activity.get(key))
            last_activity = cls._latest(last_activity, attendance_last_activity.get(key))
            last_activity = cls._latest(last_activity, period_grade_last_activity.get(key))
            if submission:
                last_activity = cls._latest(last_activity, submission.updated_at)

            results.append(
                GradeSubmissionReadinessResult(
                    assignment=assignment,
                    template_period=period,
                    status=status,
                    status_label=cls.STATUS_LABELS[status],
                    progress_percent=progress,
                    last_activity_at=last_activity,
                    submission_deadline=deadline,
                    submission_eligible=eligible,
                    submission_blockers=tuple(blockers),
                )
            )
        return results

    @classmethod
    def sort_key(cls, result):
        deadline = result.submission_deadline
        deadline_key = deadline.timestamp() if deadline else float("inf")
        offering = result.assignment.offering
        return (
            cls.STATUS_PRIORITY[result.status],
            deadline_key,
            offering.course.code.casefold(),
            offering.section.code.casefold(),
            result.assignment.id,
        )

    @classmethod
    def summary(cls, results):
        results = list(results)
        assignment_count = len(results)
        ready = sum(row.status == cls.READY for row in results)
        submitted = sum(row.status == cls.SUBMITTED for row in results)
        needs_attention = sum(row.status in {cls.NEEDS_ATTENTION, cls.NEARLY_READY} for row in results)
        overdue = sum(row.status == cls.OVERDUE for row in results)
        operationally_ready = ready + submitted
        readiness_percent = (
            round((operationally_ready / assignment_count) * 100, 1) if assignment_count else 0
        )
        return {
            "total_faculty": len({row.assignment.faculty_user_id for row in results}),
            "ready": ready,
            "needs_attention": needs_attention,
            "submitted": submitted,
            "overdue": overdue,
            "assignment_count": assignment_count,
            "readiness_percent": readiness_percent,
        }
