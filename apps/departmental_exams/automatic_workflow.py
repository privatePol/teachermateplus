from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

from .automatic_processing_isolation import AUTOMATIC_PROCESSING_TIMEOUT_CODE
from .contribution_authorization import ContributorEligibilityService
from .contribution_services import ContributionRosterService, Stage5LockService
from .generation_algorithms import allocate_difficulties
from .generation_readiness import (
    Stage6ReadinessService,
    resolve_automatic_generation_max_states,
)
from .generation_services import ExamGenerationService
from .exam_units import ExaminationUnit, resolve_examination_unit
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamCourseEquivalencyMembership,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamSet,
    normalize_contribution_deadline_to_minute,
)
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    DepartmentalExamAuthorizationService,
    ExaminationCycleConfigurationService,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomaticProcessingResult:
    cycle_course_id: int
    status: str
    code: str
    message: str
    generation_revision: int | None = None


class FacultyContributionPreparationService:
    """Prepare every authorized Included automatic course independently."""

    @staticmethod
    def _course_details(course):
        campuses = tuple(
            dict.fromkeys(
                snapshot.campus.name
                for snapshot in course.offering_snapshots.all()
            )
        )
        return {
            "cycle_course_id": course.id,
            "course_code": course.course.code,
            "course_title": course.course.title,
            "campuses": campuses,
        }

    @classmethod
    def _item(cls, course, *, status, reason, recommended_action):
        return {
            **cls._course_details(course),
            "status": status,
            "reason": reason,
            "recommended_action": recommended_action,
        }

    @staticmethod
    def _draft_attention(configuration, readiness):
        if not (configuration.coverage or "").strip():
            return (
                "Coverage not configured",
                "Set course Coverage or apply a cycle Default Coverage.",
            )
        if configuration.contribution_deadline is None:
            return (
                "Effective contribution deadline is missing",
                "Set a future course or cycle contribution deadline.",
            )
        if configuration.contribution_deadline <= timezone.now():
            return (
                "Effective contribution deadline is not in the future",
                "Set a future contribution deadline before preparing this course.",
            )
        blockers = tuple(readiness.get("blockers") or ())
        if blockers:
            return (
                "Course configuration incomplete",
                "Review the course configuration and resolve: " + ", ".join(blockers) + ".",
            )
        return None

    @classmethod
    def _require_cycle_authority(cls, *, user, cycle, courses):
        DepartmentalExamAuthorizationService.require_enabled(tenant_id=cycle.tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active user is required for faculty contribution preparation.")
        if not courses:
            DepartmentalExamAuthorizationService.require_automatic_tenant_permission(
                user=user,
                permission=(
                    DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
                ),
                tenant_id=cycle.tenant_id,
            )
            return
        permission_map = DepartmentalExamAuthorizationService.automatic_permission_map(
            user=user,
            courses=courses,
            permissions=(
                DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
            ),
        )
        if any(
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            not in permission_map[course.id]
            for course in courses
        ):
            raise PermissionDenied(
                "You do not have automatic examination authority for every participating campus."
            )

    @classmethod
    def authorize(cls, *, cycle, user):
        if cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
            raise ValidationError(
                "Prepare Faculty Contributions is available only for Automatic Generation cycles."
            )
        courses = list(
            CycleCourse.objects.filter(
                cycle=cycle,
                inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            )
            .select_related("cycle", "cycle__tenant", "course", "configuration")
            .prefetch_related("offering_snapshots__campus")
            .order_by("course__code", "course_id")
        )
        cls._require_cycle_authority(user=user, cycle=cycle, courses=courses)
        return courses

    @classmethod
    def prepare(
        cls,
        *,
        cycle_id,
        tenant_id,
        actor,
        expected_updated_at=None,
        request=None,
    ):
        DepartmentalExamAuthorizationService.require_enabled(tenant_id=tenant_id)
        cycle = ExaminationCycle.objects.select_related("tenant").get(
            pk=cycle_id,
            tenant_id=tenant_id,
        )
        courses = cls.authorize(cycle=cycle, user=actor)
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError(
                "Open the examination cycle before preparing faculty contributions."
            )
        if (
            expected_updated_at is not None
            and expected_updated_at
            != ExaminationCycleConfigurationService.transition_token(cycle)
        ):
            raise CourseExamConfigurationConflict(
                "The examination cycle changed after this page was loaded."
            )

        opened = 0
        initialized = 0
        already_prepared = []
        prepared = []
        needs_attention = []
        preserved = []

        for course in courses:
            configuration = getattr(course, "configuration", None)
            if configuration is None:
                needs_attention.append(
                    cls._item(
                        course,
                        status="Needs Attention",
                        reason="Course configuration is missing",
                        recommended_action="Configure the course examination and apply the required defaults.",
                    )
                )
                continue
            if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED:
                preserved.append(
                    cls._item(
                        course,
                        status="Preserved",
                        reason="Course contributions are already closed",
                        recommended_action="Use the governed per-course reopen action only if additional faculty work is required.",
                    )
                )
                continue
            if (
                configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.DRAFT
                and (
                    configuration.opened_at is not None
                    or configuration.contributor_roster_initialized_at is not None
                )
            ):
                preserved.append(
                    cls._item(
                        course,
                        status="Preserved",
                        reason="Historical course state prevents automatic reopening",
                        recommended_action="Review this course individually.",
                    )
                )
                continue

            try:
                if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
                    if configuration.contributor_roster_initialized_at is not None:
                        already_prepared.append(
                            cls._item(
                                course,
                                status="Already Prepared",
                                reason="Contributions are open and the contributor roster is already initialized",
                                recommended_action="No action is needed. Use Synchronize only as an explicit per-course decision.",
                            )
                        )
                        continue
                    inventory = ContributorEligibilityService.source_inventory(
                        cycle_course=course
                    )
                    if not inventory.eligible_sources:
                        needs_attention.append(
                            cls._item(
                                course,
                                status="Needs Attention",
                                reason="No qualifying teaching assignments found",
                                recommended_action="Correct active accepted teaching assignments and Faculty Portal access, then run Prepare again.",
                            )
                        )
                        continue
                    result = ContributionRosterService.initialize(
                        cycle_course_id=course.id,
                        tenant_id=tenant_id,
                        actor=actor,
                        request=request,
                    )
                    if result["changed"]:
                        initialized += 1
                    prepared.append(
                        cls._item(
                            course,
                            status="Prepared",
                            reason="Missing contributor roster initialized",
                            recommended_action="Faculty may begin contribution work.",
                        )
                    )
                    continue

                readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
                    cycle_course=course,
                    configuration=configuration,
                    user=actor,
                )
                attention = cls._draft_attention(configuration, readiness)
                if attention is not None:
                    needs_attention.append(
                        cls._item(
                            course,
                            status="Needs Attention",
                            reason=attention[0],
                            recommended_action=attention[1],
                        )
                    )
                    continue
                inventory = ContributorEligibilityService.preparation_source_inventory(
                    cycle_course=course
                )
                if not inventory.eligible_sources:
                    needs_attention.append(
                        cls._item(
                            course,
                            status="Needs Attention",
                            reason="No qualifying teaching assignments found",
                            recommended_action="Correct active accepted teaching assignments and Faculty Portal access, then run Prepare again.",
                        )
                    )
                    continue
                had_roster = configuration.contributor_roster_initialized_at is not None
                updated, changed = CourseExamConfigurationService.open_for_contribution(
                    cycle_course_id=course.id,
                    tenant_id=tenant_id,
                    user=actor,
                    expected_revision=configuration.revision,
                    request=request,
                )
                if changed:
                    opened += 1
                if not had_roster and updated.contributor_roster_initialized_at is not None:
                    initialized += 1
                prepared.append(
                    cls._item(
                        course,
                        status="Prepared",
                        reason="Contributions opened and missing contributor roster initialized",
                        recommended_action="Faculty may begin contribution work.",
                    )
                )
            except (CourseExamConfigurationConflict, ValidationError, PermissionDenied) as exc:
                message = " ".join(getattr(exc, "messages", ()) or (str(exc),))
                needs_attention.append(
                    cls._item(
                        course,
                        status="Needs Attention",
                        reason=message or "Course lifecycle prevents preparation",
                        recommended_action="Review this course individually, correct the reported state, and run Prepare again.",
                    )
                )
            except Exception as exc:  # fault isolation is intentional
                logger.error(
                    "Automatic faculty contribution preparation failed for tenant=%s cycle_course=%s error_type=%s.",
                    tenant_id,
                    course.id,
                    exc.__class__.__name__,
                )
                needs_attention.append(
                    cls._item(
                        course,
                        status="Needs Attention",
                        reason="Course preparation failed safely",
                        recommended_action="Review the secured application log and prepare this course individually.",
                    )
                )

        successfully_prepared = len(prepared) + len(already_prepared)
        return {
            "total_considered": len(courses),
            "successfully_prepared": successfully_prepared,
            "contributions_opened": opened,
            "rosters_initialized": initialized,
            "already_prepared_count": len(already_prepared),
            "needs_attention_count": len(needs_attention),
            "preserved_count": len(preserved),
            "prepared": prepared,
            "already_prepared": already_prepared,
            "needs_attention": needs_attention,
            "preserved": preserved,
        }


def readiness_blocker_text(report):
    code = ((report.get("blockers") or [{}])[0]).get("code", "")
    shortages = report.get("shortages") or ()
    if shortages:
        shortage = shortages[0]
        label = shortage.get("label", "questions")
        return (
            f"Insufficient {label} questions: {shortage.get('available', 0)} "
            f"available / {shortage.get('required', 0)} required"
        )
    blockers = report.get("blockers") or ()
    if blockers:
        return blockers[0].get("message") or "Generation readiness is blocked."
    return "Generation readiness could not be established."


def readiness_recommendation(report):
    code = ((report.get("blockers") or [{}])[0]).get("code", "")
    return {
        "CONFIGURATION_MISSING": "Configure the course examination.",
        "CONFIGURATION_DRAFT": "Complete the course configuration and open contributions before automatic generation can proceed.",
        "FINAL_COUNT_INVALID": "Set a valid final item count.",
        "CONTRIBUTION_NOT_CLOSED": "Wait for automatic deadline processing.",
        "WAITING_FOR_DEADLINE": "Monitor faculty contributions until the deadline.",
        "AUTOMATIC_PROCESSING_PENDING": "No admin action is needed; automatic processing is pending.",
        "ROSTER_STALE": "Synchronize the contributor roster.",
        "ACTIVE_CONTRIBUTORS_INCOMPLETE": "Reopen contributions with a new deadline if more time is needed.",
        "BLOCKED_DRAFTS_UNRESOLVED": "Resolve current Blocked Draft contributions.",
        "QUESTION_SHORTAGES": "Obtain enough eligible Submitted questions.",
        "UNIQUE_QUESTION_SHORTAGES": "Obtain enough unique usable Submitted questions.",
        "HARD_CONSTRAINTS_INFEASIBLE": "Review the eligible pool and required allocation constraints.",
        "FEASIBILITY_LIMIT": "Contact an administrator to review the solver limit.",
        AUTOMATIC_PROCESSING_TIMEOUT_CODE: (
            "Administrator review is required before reopening contributions for retry."
        ),
        "PROCESSING_ERROR": "Review the secured processor log, correct the failure, and rerun deadline processing.",
    }.get(code, "Review the readiness details and correct the blocking input.")


class AutomaticExamDeadlineService:
    """Idempotent, per-course automatic close and first-generation processor."""

    @staticmethod
    def _audit(*, action, course, configuration, metadata=None):
        AuditService.log_event(
            action=action,
            portal="SYSTEM",
            entity_type="CourseExamConfiguration",
            entity_id=configuration.id,
            actor=None,
            tenant=course.cycle.tenant_id,
            metadata={
                "cycle_id": course.cycle_id,
                "cycle_course_id": course.id,
                "configuration_revision": configuration.revision,
                "automatic": True,
                **(metadata or {}),
            },
        )

    @classmethod
    def _record_status(cls, *, course, configuration, status, code, now):
        changed = (
            configuration.automatic_processing_status != status
            or configuration.automatic_processing_code != code
        )
        configuration.automatic_processing_status = status
        configuration.automatic_processing_code = code
        configuration.automatic_processed_at = now
        configuration.save(
            update_fields=[
                "automatic_processing_status",
                "automatic_processing_code",
                "automatic_processed_at",
                "updated_at",
            ]
        )
        if changed and status in (
            CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
            CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
        ):
            cls._audit(
                action="DE_EXAM_AUTOMATIC_GENERATION_BLOCKED",
                course=course,
                configuration=configuration,
                metadata={"blocker_code": code},
            )

    @classmethod
    def _canonical_exempt_result_locked(cls, *, course, configuration, now):
        if course.inclusion_status != CycleCourse.InclusionStatus.EXEMPT:
            return None
        if configuration is not None and (
            configuration.automatic_processing_status
            != CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED
            or configuration.automatic_processing_code != "NOT_APPLICABLE"
            or configuration.automatic_processed_at is None
        ):
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED,
                code="NOT_APPLICABLE",
                now=now,
            )
        return AutomaticProcessingResult(
            course.id,
            "SKIPPED",
            "NOT_APPLICABLE",
            "Course is not applicable.",
        )

    @classmethod
    @transaction.atomic
    def _record_status_authoritatively(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        status,
        code,
        now,
    ):
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        exempt_result = cls._canonical_exempt_result_locked(
            course=course,
            configuration=configuration,
            now=now,
        )
        if exempt_result is not None:
            return exempt_result
        if configuration is None:
            return None
        cls._record_status(
            course=course,
            configuration=configuration,
            status=status,
            code=code,
            now=now,
        )
        return None

    @classmethod
    @transaction.atomic
    def _preserve_exempt_outcome(cls, *, cycle_course_id, tenant_id, now):
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        return cls._canonical_exempt_result_locked(
            course=course,
            configuration=configuration,
            now=now,
        )

    @classmethod
    def process_course(cls, *, cycle_course_id, tenant_id, now=None, max_states=None):
        now = now or timezone.now()
        requested_course = CycleCourse.objects.select_related("cycle", "course").get(
            pk=cycle_course_id,
            cycle__tenant_id=tenant_id,
        )
        cycle_course_id = resolve_examination_unit(requested_course).primary.id
        preparation = cls._close_due_intake(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
            now=now,
        )
        if preparation is not None:
            return preparation
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(
            tenant_id=tenant_id
        ):
            return AutomaticProcessingResult(
                cycle_course_id,
                "SKIPPED",
                "FEATURE_DISABLED",
                "Departmental Exam Builder is disabled for this tenant.",
            )
        course = CycleCourse.objects.select_related("cycle", "configuration").get(
            pk=cycle_course_id,
            cycle__tenant_id=tenant_id,
        )
        configuration = course.configuration

        current = ExamGenerationService.current_for_course(cycle_course=course)
        if current is not None:
            exempt_result = cls._record_status_authoritatively(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                status=CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED,
                code="CURRENT_GENERATION_EXISTS",
                now=now,
            )
            if exempt_result is not None:
                return exempt_result
            return AutomaticProcessingResult(
                course.id,
                "SKIPPED",
                "CURRENT_GENERATION_EXISTS",
                "A current generation already exists; automatic regeneration was not performed.",
                current.revision_number,
            )

        automatic_state_budget = resolve_automatic_generation_max_states(max_states)
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=course,
            automatic_max_states=automatic_state_budget,
        )
        if problem is None or not readiness["ready"]:
            code = (readiness.get("blockers") or [{"code": "READINESS_BLOCKED"}])[0][
                "code"
            ]
            exempt_result = cls._record_status_authoritatively(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                status=CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
                code=code,
                now=now,
            )
            if exempt_result is not None:
                return exempt_result
            return AutomaticProcessingResult(
                course.id, "BLOCKED", code, readiness_blocker_text(readiness)
            )

        request_token = hashlib.sha256(
            (
                f"automatic:{tenant_id}:{course.id}:{configuration.revision}:"
                f"{problem.input_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        try:
            outcome = ExamGenerationService.generate(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                actor=None,
                expected_current_revision=0,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token=request_token,
                generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
                max_states=automatic_state_budget,
            )
        except Exception:
            exempt_result = cls._preserve_exempt_outcome(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                now=now,
            )
            if exempt_result is not None:
                return exempt_result
            raise
        status = (
            CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED
            if outcome.reused
            else CourseExamConfiguration.AutomaticProcessingStatus.GENERATED
        )
        code = "CURRENT_GENERATION_EXISTS" if outcome.reused else "GENERATED"
        exempt_result = cls._record_status_authoritatively(
            cycle_course_id=course.id,
            tenant_id=tenant_id,
            status=status,
            code=code,
            now=now,
        )
        if exempt_result is not None:
            return exempt_result
        return AutomaticProcessingResult(
            course.id,
            status,
            code,
            (
                "A current generation already exists; automatic regeneration was not performed."
                if outcome.reused
                else "Set A and Set B generated."
            ),
            outcome.revision.revision_number,
        )

    @classmethod
    @transaction.atomic
    def _close_due_intake(cls, *, cycle_course_id, tenant_id, now):
        """Commit deadline closure independently of later solver failures."""
        cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        unit = resolve_examination_unit(course, for_update=True)
        configurations = {
            row.cycle_course_id: row
            for row in CourseExamConfiguration.objects.select_for_update()
            .filter(cycle_course_id__in=unit.member_ids)
            .order_by("cycle_course_id")
        }
        configuration = configurations.get(unit.primary.id)
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(
            tenant_id=tenant_id
        ):
            return AutomaticProcessingResult(
                course.id,
                "SKIPPED",
                "FEATURE_DISABLED",
                "Departmental Exam Builder is disabled for this tenant.",
            )
        if (
            cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            or cycle.status != ExaminationCycle.Status.OPEN
            or course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED
            or configuration is None
        ):
            return AutomaticProcessingResult(
                course.id, "SKIPPED", "NOT_APPLICABLE", "Course is not applicable."
            )
        deadline = configuration.active_contribution_deadline
        if deadline is None or now < deadline:
            return AutomaticProcessingResult(
                course.id,
                "SKIPPED",
                "NOT_DUE",
                "Contribution deadline has not arrived.",
            )
        if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
            for member in unit.members:
                member_configuration = configurations[member.id]
                member_configuration.workflow_status = (
                    CourseExamConfiguration.WorkflowStatus.CLOSED
                )
                member_configuration.closed_at = now
                member_configuration.closed_by = None
                member_configuration.revision += 1
                member_configuration.save(
                    update_fields=[
                        "workflow_status",
                        "closed_at",
                        "closed_by",
                        "revision",
                        "updated_at",
                    ]
                )
                cls._audit(
                    action="DE_EXAM_CONTRIBUTION_AUTOMATICALLY_CLOSED",
                    course=member,
                    configuration=member_configuration,
                    metadata={
                        "deadline": deadline,
                        **(
                            {
                                "equivalency_primary_cycle_course_id": (
                                    unit.primary.id
                                )
                            }
                            if unit.grouped
                            else {}
                        ),
                    },
                )
        elif configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.CLOSED:
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
                code="CONFIGURATION_NOT_OPEN",
                now=now,
            )
            return AutomaticProcessingResult(
                course.id,
                "BLOCKED",
                "CONFIGURATION_NOT_OPEN",
                "Course configuration is not open for automatic deadline processing.",
            )
        return None

    @classmethod
    def process_due(
        cls,
        *,
        now=None,
        max_states=None,
        course_processor=None,
    ):
        now = now or timezone.now()
        course_processor = course_processor or cls.process_course
        candidates = list(
            CycleCourse.objects.filter(
                cycle__processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
                cycle__status=ExaminationCycle.Status.OPEN,
                inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
                configuration__workflow_status__in=(
                    CourseExamConfiguration.WorkflowStatus.OPEN,
                    CourseExamConfiguration.WorkflowStatus.CLOSED,
                ),
            )
            .values_list("id", "cycle__tenant_id")
            .order_by("id")
        )
        primary_by_candidate = dict(
            ExamCourseEquivalencyMembership.objects.filter(
                cycle_course_id__in=[course_id for course_id, _tenant_id in candidates],
                active_marker=1,
                group__is_active=True,
            ).values_list("cycle_course_id", "group__primary_cycle_course_id")
        )
        ordered_candidates = []
        seen_primary_ids = set()
        for course_id, tenant_id in candidates:
            primary_id = primary_by_candidate.get(course_id, course_id)
            if primary_id in seen_primary_ids:
                continue
            seen_primary_ids.add(primary_id)
            ordered_candidates.append((primary_id, tenant_id))
        # Reopen and eligible Open-cycle Restore clear these fields explicitly.
        # Until then, a Closed terminal row is an operator-visible final result,
        # not permission to repeat the expensive solver every five minutes.
        terminal_primary_ids = set(
            CourseExamConfiguration.objects.filter(
                cycle_course_id__in=[
                    course_id for course_id, _tenant_id in ordered_candidates
                ],
                workflow_status=CourseExamConfiguration.WorkflowStatus.CLOSED,
                automatic_processing_status__in=(
                    CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
                    CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
                    CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED,
                ),
                automatic_processed_at__isnull=False,
            ).values_list("cycle_course_id", flat=True)
        )
        results = []
        for primary_id, tenant_id in ordered_candidates:
            if primary_id in terminal_primary_ids:
                continue
            try:
                results.append(
                    course_processor(
                        cycle_course_id=primary_id,
                        tenant_id=tenant_id,
                        now=now,
                        max_states=max_states,
                    )
                )
            except Exception as exc:  # per-course fault isolation is intentional
                results.append(
                    cls.record_processing_failure(
                        cycle_course_id=primary_id,
                        tenant_id=tenant_id,
                        code=exc.__class__.__name__,
                        message=(
                            "Course processing failed; inspect the secured application log."
                        ),
                        now=now,
                    )
                )
        return results

    @classmethod
    def record_processing_failure(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        code,
        message,
        now,
    ):
        safe_code = str(code)[:64]
        logger.error(
            "Automatic departmental-exam processing failed for tenant=%s course=%s error_type=%s.",
            tenant_id,
            cycle_course_id,
            safe_code,
        )
        failure_result = AutomaticProcessingResult(
            cycle_course_id,
            "ERROR",
            safe_code,
            message,
        )
        try:
            exempt_result = cls._record_error(
                cycle_course_id=cycle_course_id,
                tenant_id=tenant_id,
                code=safe_code,
                now=now,
            )
            if exempt_result is not None:
                failure_result = exempt_result
        except Exception as record_exc:
            logger.error(
                "Automatic departmental-exam error recording failed for tenant=%s course=%s original_error_type=%s recording_error_type=%s.",
                tenant_id,
                cycle_course_id,
                safe_code,
                record_exc.__class__.__name__,
            )
        return failure_result

    @classmethod
    @transaction.atomic
    def _record_error(cls, *, cycle_course_id, tenant_id, code, now):
        try:
            _cycle, course, configuration = Stage5LockService.lock_cycle_course(
                cycle_course_id=cycle_course_id,
                tenant_id=tenant_id,
            )
        except (CycleCourse.DoesNotExist, Http404, PermissionDenied):
            return
        exempt_result = cls._canonical_exempt_result_locked(
            course=course,
            configuration=configuration,
            now=now,
        )
        if exempt_result is not None:
            return exempt_result
        if configuration is not None:
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
                code=str(code)[:64],
                now=now,
            )
        return None


class AutomaticContributionReopenService:
    @classmethod
    @transaction.atomic
    def reopen(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        actor,
        expected_revision,
        new_deadline,
        request=None,
    ):
        cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        unit = resolve_examination_unit(course, for_update=True)
        course = unit.primary
        configurations = {
            row.cycle_course_id: row
            for row in CourseExamConfiguration.objects.select_for_update()
            .filter(cycle_course_id__in=unit.member_ids)
            .order_by("cycle_course_id")
        }
        configuration = configurations.get(course.id)
        DepartmentalExamAuthorizationService.require_generation_management(
            user=actor,
            cycle_course=course,
        )
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError("Only an Open cycle permits contribution reopen.")
        if configuration is None or any(
            configurations.get(member.id) is None
            or configurations[member.id].workflow_status
            != CourseExamConfiguration.WorkflowStatus.CLOSED
            for member in unit.members
        ):
            raise ValidationError("Only a closed automatic contribution intake may be reopened.")
        if configuration.revision != expected_revision:
            raise CourseExamConfigurationConflict(
                "The course configuration changed after this page was loaded."
            )
        normalized_deadline = normalize_contribution_deadline_to_minute(new_deadline)
        if normalized_deadline is None or normalized_deadline <= timezone.now():
            raise ValidationError("The new contribution deadline must be in the future.")

        revisions = list(
            ExamGenerationRevision.objects.select_for_update()
            .filter(cycle_course_id__in=unit.member_ids)
            .order_by("revision_number")
        )
        if any(row.cycle_course_id != course.id for row in revisions):
            raise ValidationError(
                "Secondary equivalency members cannot own generation revisions."
            )
        current = next((row for row in revisions if row.current_marker == 1), None)
        if current and current.status == ExamGenerationRevision.Status.LOCKED:
            raise ValidationError("A permanently locked manual examination cannot be reopened.")
        if current:
            current.status = ExamGenerationRevision.Status.SUPERSEDED
            current.current_marker = None
            current.save(update_fields=["status", "current_marker", "updated_at"])
            ExamGenerationService._audit(
                action="DE_EXAM_GENERATION_SUPERSEDED_FOR_REOPEN",
                revision=current,
                actor=actor,
                request=request,
                metadata={"stale_after_reopen": True},
            )

        for member in unit.members:
            member_configuration = configurations[member.id]
            before_revision = member_configuration.revision
            member_configuration.workflow_status = (
                CourseExamConfiguration.WorkflowStatus.OPEN
            )
            member_configuration.closed_at = None
            member_configuration.closed_by = None
            member_configuration.reopened_contribution_deadline = normalized_deadline
            member_configuration.revision += 1
            member_configuration.automatic_processing_status = ""
            member_configuration.automatic_processing_code = ""
            member_configuration.automatic_processed_at = None
            member_configuration.save(
                update_fields=[
                    "workflow_status",
                    "closed_at",
                    "closed_by",
                    "reopened_contribution_deadline",
                    "revision",
                    "automatic_processing_status",
                    "automatic_processing_code",
                    "automatic_processed_at",
                    "updated_at",
                ]
            )
            ContributionRosterService._synchronize_locked(
                cycle_course=member,
                configuration=member_configuration,
                actor=actor,
                request=request,
                initializing=False,
            )
            AuditService.log_event(
                action="DE_EXAM_CONTRIBUTION_REOPENED",
                portal="ADMIN",
                entity_type="CourseExamConfiguration",
                entity_id=member_configuration.id,
                actor=actor,
                tenant=tenant_id,
                metadata={
                    "cycle_id": cycle.id,
                    "cycle_course_id": member.id,
                    **(
                        {"equivalency_primary_cycle_course_id": course.id}
                        if unit.grouped
                        else {}
                    ),
                    "previous_configuration_revision": before_revision,
                    "resulting_configuration_revision": member_configuration.revision,
                    "new_deadline": normalized_deadline,
                    "superseded_generation_revision": (
                        current.revision_number if current else None
                    ),
                },
                request=request,
            )
        configuration = configurations[course.id]
        return configuration


class AutomaticGenerationSummaryService:
    _PERSISTED_BLOCKER_MESSAGES = {
        "CONFIGURATION_NOT_OPEN": (
            "Course configuration was not open for automatic deadline processing."
        ),
        "CONFIGURATION_MISSING": "Course examination configuration is required.",
        "FINAL_COUNT_INVALID": "Final item count must be from 50 to 75.",
        "DIFFICULTY_POLICY_INVALID": (
            "Difficulty configuration must be exactly 30/50/20."
        ),
        "CONTRIBUTION_NOT_CLOSED": "Faculty contribution must be Closed.",
        "ROSTER_STALE": (
            "The contributor roster was stale when automatic processing ran."
        ),
        "ACTIVE_CONTRIBUTORS_INCOMPLETE": (
            "Currently required Active contributors had not all Submitted."
        ),
        "BLOCKED_DRAFTS_UNRESOLVED": (
            "Current Blocked Draft contributions required explicit resolution."
        ),
        "CAMPUS_PARTICIPATION_MISSING": (
            "At least one participating campus snapshot is required."
        ),
        "CAMPUS_CODE_INVALID": "Participating campus evidence was invalid.",
        "ELIGIBLE_POOL_INVALID": (
            "Submitted question rows with invalid payload or frozen campus evidence "
            "were excluded."
        ),
        "MISSING_CAMPUS_REPRESENTATION": (
            "At least one participating campus had no usable unique Submitted questions."
        ),
        "QUESTION_SHORTAGES": (
            "The eligible Submitted pool had aggregate shortages."
        ),
        "UNIQUE_QUESTION_SHORTAGES": (
            "The deduplicated Submitted pool had aggregate shortages."
        ),
        "FEASIBILITY_LIMIT": (
            "The deterministic feasibility state limit was reached."
        ),
        AUTOMATIC_PROCESSING_TIMEOUT_CODE: (
            "Automatic generation exceeded the per-course processing time limit."
        ),
        "HARD_CONSTRAINTS_INFEASIBLE": (
            "Two equivalent sets could not satisfy the required questionnaire allocation."
        ),
    }

    @classmethod
    def _persisted_processing_report(cls, configuration):
        if (
            configuration.automatic_processing_status
            == CourseExamConfiguration.AutomaticProcessingStatus.ERROR
        ):
            if (
                configuration.automatic_processing_code
                == AUTOMATIC_PROCESSING_TIMEOUT_CODE
            ):
                return {
                    "blockers": [
                        {
                            "code": AUTOMATIC_PROCESSING_TIMEOUT_CODE,
                            "message": cls._PERSISTED_BLOCKER_MESSAGES[
                                AUTOMATIC_PROCESSING_TIMEOUT_CODE
                            ],
                        }
                    ],
                    "processing_code": configuration.automatic_processing_code,
                }
            return {
                "blockers": [
                    {
                        "code": "PROCESSING_ERROR",
                        "message": "Automatic generation failed during processing.",
                    }
                ],
                "processing_code": configuration.automatic_processing_code,
            }
        code = configuration.automatic_processing_code or "AUTOMATIC_PROCESSING_BLOCKED"
        return {
            "blockers": [
                {
                    "code": code,
                    "message": cls._PERSISTED_BLOCKER_MESSAGES.get(
                        code,
                        "Automatic generation did not produce a current questionnaire.",
                    ),
                }
            ],
            "processing_code": configuration.automatic_processing_code,
        }

    @staticmethod
    def _generated_warnings(
        *,
        cycle,
        unit,
        current,
        audit_snapshot,
        common,
        optimization_evidence,
    ):
        warnings = []
        if audit_snapshot is not None and audit_snapshot.redundant_copy_count:
            warnings.append(
                {
                    "code": "REDUNDANT_DUPLICATE_QUESTIONS",
                    "message": (
                        f"{audit_snapshot.submitted_count} submitted \u2022 "
                        f"{audit_snapshot.unique_logical_count} unique \u2022 "
                        f"{audit_snapshot.redundant_copy_count} duplicate copies "
                        "automatically ignored."
                    ),
                }
            )

        expected_campuses = {
            snapshot.campus_id: snapshot.campus.name
            for member in unit.members
            for snapshot in member.offering_snapshots.all()
        }
        represented_campus_ids = {
            item.source_campus_id
            for generated_set in current.generated_sets.all()
            for item in generated_set.items.all()
        }
        missing_campus_names = tuple(
            expected_campuses[campus_id]
            for campus_id in sorted(expected_campuses)
            if campus_id not in represented_campus_ids
        )
        if (
            missing_campus_names
            and cycle.automatic_campus_contribution_policy
            == ExaminationCycle.AutomaticCampusContributionPolicy.AVAILABLE_WITH_WARNING
        ):
            warnings.append(
                {
                    "code": "MISSING_CAMPUS_REPRESENTATION",
                    "message": (
                        "No usable unique Submitted questions represent: "
                        + ", ".join(missing_campus_names)
                        + ". Feasible allocation used represented campuses only."
                    ),
                }
            )

        if (
            common["eligible_contributors"] > common["submitted_contributors"]
            and cycle.automatic_contributor_completion_policy
            == ExaminationCycle.AutomaticContributorCompletionPolicy.SUFFICIENT_POOL
        ):
            warnings.append(
                {
                    "code": "ACTIVE_CONTRIBUTORS_INCOMPLETE",
                    "message": (
                        f'{common["submitted_contributors"]} / '
                        f'{common["eligible_contributors"]} active contributors '
                        "Final Submitted; generation used the sufficient Submitted pool."
                    ),
                }
            )
        target_difficulty = allocate_difficulties(current.final_item_count_snapshot)
        actual_difficulty = {}
        for generated_set in current.generated_sets.all():
            counts = {key: 0 for key in target_difficulty}
            for item in generated_set.items.all():
                counts[item.difficulty_snapshot] = (
                    counts.get(item.difficulty_snapshot, 0) + 1
                )
            actual_difficulty[generated_set.set_code] = counts
        if any(
            counts != target_difficulty for counts in actual_difficulty.values()
        ):
            optimum_proved = (
                optimization_evidence.get("difficulty_optimality_proved") is True
            )
            optimization_limit_hit = (
                optimization_evidence.get("difficulty_optimization_limit_hit")
                is True
            )
            if optimization_limit_hit:
                message = (
                    "The preferred difficulty target could not be fully optimized "
                    "within the processing budget. The best valid difficulty mix "
                    "found was used."
                )
            elif optimum_proved:
                message = (
                    "Preferred difficulty target differs from the actual generated "
                    "mix; the closest feasible difficulty mix was used."
                )
            else:
                message = (
                    "Preferred difficulty target differs from the actual generated "
                    "mix; a hard-valid deterministic mix was used."
                )
            warnings.append(
                {
                    "code": "PREFERRED_DIFFICULTY_UNAVAILABLE",
                    "message": message,
                    "target": target_difficulty,
                    "actual": actual_difficulty,
                    "difficulty_optimality_proved": optimum_proved,
                    "difficulty_optimization_limit_hit": optimization_limit_hit,
                }
            )
        return tuple(warnings)

    @classmethod
    def build(cls, *, cycle, now=None, cycle_course_ids=None):
        if (
            cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            raise ValidationError("This cycle does not use automatic generation.")
        now = now or timezone.now()
        course_queryset = CycleCourse.objects.filter(cycle=cycle)
        if cycle_course_ids is not None:
            course_queryset = course_queryset.filter(pk__in=tuple(cycle_course_ids))
        courses = list(
            course_queryset
            .select_related("course", "configuration")
            .prefetch_related(
                "offering_snapshots__campus",
                "faculty_contributions",
                Prefetch(
                    "generation_revisions",
                    queryset=ExamGenerationRevision.objects.filter(
                        current_marker=1,
                        status=ExamGenerationRevision.Status.GENERATED,
                    ).select_related(
                        "generated_by", "source_audit_snapshot"
                    ).prefetch_related(
                        Prefetch(
                            "generated_sets",
                            queryset=GeneratedExamSet.objects.prefetch_related(
                                "items"
                            ).order_by("set_code"),
                        )
                    ),
                    to_attr="current_generated_revisions",
                ),
            )
            .order_by("course__code", "course_id")
        )
        current_revision_ids = [
            revision.id
            for course in courses
            for revision in getattr(course, "current_generated_revisions", ())
        ]
        optimization_evidence_by_revision = {}
        if current_revision_ids:
            generation_events = AuditLog.objects.filter(
                tenant_id=cycle.tenant_id,
                entity_type="ExamGenerationRevision",
                entity_id__in=[str(value) for value in current_revision_ids],
                action__in=("DE_EXAM_GENERATED", "DE_EXAM_REGENERATED"),
            ).order_by("created_at", "id")
            for event in generation_events:
                optimization_evidence_by_revision[int(event.entity_id)] = (
                    event.metadata_json or {}
                )
        grouped_course_ids = set(
            ExamCourseEquivalencyMembership.objects.filter(
                cycle_course_id__in=[course.id for course in courses],
                active_marker=1,
                group__is_active=True,
            ).values_list("cycle_course_id", flat=True)
        )
        generated = []
        not_generated = []
        exempt = []
        last_processed_at = None
        summarized_primary_ids = set()
        courses_by_id = {course.id: course for course in courses}
        resolved_units_by_member_id = {}
        for requested_course in courses:
            unit = resolved_units_by_member_id.get(requested_course.id)
            if unit is None:
                if requested_course.id in grouped_course_ids:
                    resolved = resolve_examination_unit(requested_course)
                    members = tuple(
                        courses_by_id.get(member.id, member)
                        for member in resolved.members
                    )
                    unit = ExaminationUnit(
                        primary=courses_by_id.get(resolved.primary.id, resolved.primary),
                        members=members,
                        group=resolved.group,
                        memberships=resolved.memberships,
                    )
                    for member in members:
                        resolved_units_by_member_id[member.id] = unit
                else:
                    unit = ExaminationUnit(
                        primary=requested_course,
                        members=(requested_course,),
                    )
            if unit.primary.id in summarized_primary_ids:
                continue
            summarized_primary_ids.add(unit.primary.id)
            course = unit.primary
            campuses = tuple(
                dict.fromkeys(
                    snapshot.campus.name
                    for member in unit.members
                    for snapshot in member.offering_snapshots.all()
                )
            )
            contributions = sorted(
                (
                    contribution
                    for member in unit.members
                    for contribution in member.faculty_contributions.all()
                ),
                key=lambda contribution: (
                    contribution.cycle_course_id,
                    contribution.id,
                ),
            )
            configuration = getattr(course, "configuration", None)
            if configuration and configuration.automatic_processed_at:
                last_processed_at = max(
                    filter(
                        None,
                        (last_processed_at, configuration.automatic_processed_at),
                    )
                )
            current_rows = getattr(course, "current_generated_revisions", ())
            current = current_rows[0] if current_rows else None
            common = {
                "course": course,
                "campuses": campuses,
                "target_count": (
                    configuration.final_item_count if configuration else None
                ),
                "eligible_contributors": sum(
                    item.roster_status == FacultyContribution.RosterStatus.ACTIVE
                    for item in contributions
                ),
                "submitted_contributors": sum(
                    item.roster_status == FacultyContribution.RosterStatus.ACTIVE
                    and item.status == FacultyContribution.Status.SUBMITTED
                    for item in contributions
                ),
            }
            if course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT:
                exempt.append(
                    {
                        "course": course,
                        "campuses": campuses,
                        "category": course.get_exemption_category_display(),
                        "reason": course.exemption_reason,
                        "configuration": configuration,
                    }
                )
                continue
            if current:
                sets = {item.set_code: item for item in current.generated_sets.all()}
                actual_set_counts = []
                for set_code in (GeneratedExamSet.SetCode.A, GeneratedExamSet.SetCode.B):
                    generated_set = sets.get(set_code)
                    campus_counts = {}
                    if generated_set is not None:
                        for item in generated_set.items.all():
                            campus_key = (
                                item.campus_code_snapshot,
                                item.campus_name_snapshot,
                            )
                            campus_row = campus_counts.setdefault(
                                campus_key,
                                {
                                    "campus_code": item.campus_code_snapshot,
                                    "campus_name": item.campus_name_snapshot,
                                    "total": 0,
                                    "easy": 0,
                                    "moderate": 0,
                                    "difficult": 0,
                                },
                            )
                            campus_row["total"] += 1
                            campus_row[item.difficulty_snapshot.lower()] += 1
                    actual_set_counts.append(
                        {
                            "set_code": set_code,
                            "total": sum(
                                campus_row["total"]
                                for campus_row in campus_counts.values()
                            ),
                            "difficulty": {
                                "EASY": sum(
                                    row["easy"] for row in campus_counts.values()
                                ),
                                "MODERATE": sum(
                                    row["moderate"] for row in campus_counts.values()
                                ),
                                "DIFFICULT": sum(
                                    row["difficult"] for row in campus_counts.values()
                                ),
                            },
                            "campuses": tuple(
                                campus_counts[key]
                                for key in sorted(
                                    campus_counts,
                                    key=lambda value: (value[1], value[0]),
                                )
                            ),
                        }
                    )
                audit_snapshot = getattr(current, "source_audit_snapshot", None)
                generated.append(
                    {
                        **common,
                        "revision": current,
                        "set_a_generated": "A" in sets,
                        "set_b_generated": "B" in sets,
                        "actual_set_counts": tuple(actual_set_counts),
                        "target_difficulty": allocate_difficulties(
                            current.final_item_count_snapshot
                        ),
                        "warnings": cls._generated_warnings(
                            cycle=cycle,
                            unit=unit,
                            current=current,
                            audit_snapshot=audit_snapshot,
                            common=common,
                            optimization_evidence=(
                                optimization_evidence_by_revision.get(current.id, {})
                            ),
                        ),
                        "pool_metrics": {
                            "submitted": (
                                audit_snapshot.submitted_count
                                if audit_snapshot is not None
                                else None
                            ),
                            "unique": (
                                audit_snapshot.unique_logical_count
                                if audit_snapshot is not None
                                else None
                            ),
                            "redundant": (
                                audit_snapshot.redundant_copy_count
                                if audit_snapshot is not None
                                else None
                            ),
                        },
                        "regeneration_input_fingerprint": (
                            current.source_input_fingerprint
                        ),
                    }
                )
                continue

            if configuration is None:
                report = {
                    "blockers": [
                        {
                            "code": "CONFIGURATION_MISSING",
                            "message": "Course examination configuration is required.",
                        }
                    ]
                }
                status = "Not configured"
            elif (
                configuration.workflow_status
                == CourseExamConfiguration.WorkflowStatus.DRAFT
            ):
                report = {
                    "blockers": [
                        {
                            "code": "CONFIGURATION_DRAFT",
                            "message": "Course setup is not yet complete.",
                        }
                    ]
                }
                status = "Draft"
            elif (
                configuration.automatic_processing_status
                == CourseExamConfiguration.AutomaticProcessingStatus.ERROR
                or (
                    configuration.automatic_processing_status
                    and configuration.workflow_status
                    == CourseExamConfiguration.WorkflowStatus.CLOSED
                )
            ):
                report = cls._persisted_processing_report(configuration)
                status = configuration.get_automatic_processing_status_display()
            elif (
                configuration.workflow_status
                == CourseExamConfiguration.WorkflowStatus.OPEN
                and configuration.active_contribution_deadline
                and now < configuration.active_contribution_deadline
            ):
                report = {
                    "blockers": [
                        {
                            "code": "WAITING_FOR_DEADLINE",
                            "message": "Contribution deadline has not arrived yet.",
                        }
                    ]
                }
                status = (
                    "All contributions submitted — waiting for deadline"
                    if common["eligible_contributors"]
                    and common["submitted_contributors"]
                    == common["eligible_contributors"]
                    else "Contributions open"
                )
            elif (
                configuration.workflow_status
                == CourseExamConfiguration.WorkflowStatus.OPEN
                and configuration.active_contribution_deadline
            ):
                report = {
                    "blockers": [
                        {
                            "code": "AUTOMATIC_PROCESSING_PENDING",
                            "message": "The contribution deadline has arrived; automatic processing is pending.",
                        }
                    ]
                }
                status = "Automatic processing"
            else:
                report = Stage6ReadinessService.evaluate_automatic_pool(
                    cycle_course=course,
                    exact_feasibility=False,
                )
                status = (
                    configuration.get_automatic_processing_status_display()
                    if configuration.automatic_processing_status
                    else report.get("status", "Not generated").title()
                )
            not_generated.append(
                {
                    **common,
                    "status": status,
                    "blocker_code": (
                        (report.get("blockers") or [{}])[0].get("code", "")
                    ),
                    "reason": readiness_blocker_text(report),
                    "recommended_action": readiness_recommendation(report),
                    "warnings": report.get("warnings", ()),
                    "pool_metrics": {
                        "submitted": report.get("submitted_question_count"),
                        "unique": report.get("unique_question_count"),
                        "redundant": report.get("duplicate_question_count"),
                    },
                    "configuration": configuration,
                    "active_deadline": (
                        configuration.active_contribution_deadline
                        if configuration
                        else None
                    ),
                }
            )
        return {
            "generated": generated,
            "not_generated": not_generated,
            "exempt": exempt,
            "total": len(generated) + len(not_generated),
            "generated_count": len(generated),
            "not_generated_count": len(not_generated),
            "exempt_count": len(exempt),
            "last_processed_at": last_processed_at,
        }
