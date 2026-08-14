from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

from .contribution_authorization import ContributorEligibilityService
from .contribution_services import ContributionRosterService, Stage5LockService
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
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
    if code == "UNIQUE_QUESTION_SHORTAGES":
        return "Insufficient unique usable questions for the required allocation."
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
        "CONFIGURATION_DRAFT": "Open contributions / complete course configuration.",
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
    def process_course(cls, *, cycle_course_id, tenant_id, now=None, max_states=None):
        now = now or timezone.now()
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
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED,
                code="CURRENT_GENERATION_EXISTS",
                now=now,
            )
            return AutomaticProcessingResult(
                course.id,
                "SKIPPED",
                "CURRENT_GENERATION_EXISTS",
                "A current generation already exists; automatic regeneration was not performed.",
                current.revision_number,
            )

        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=course)
        if problem is None or not readiness["ready"]:
            code = (readiness.get("blockers") or [{"code": "READINESS_BLOCKED"}])[0][
                "code"
            ]
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.BLOCKED,
                code=code,
                now=now,
            )
            return AutomaticProcessingResult(
                course.id, "BLOCKED", code, readiness_blocker_text(readiness)
            )

        request_token = hashlib.sha256(
            (
                f"automatic:{tenant_id}:{course.id}:{configuration.revision}:"
                f"{problem.input_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        outcome = ExamGenerationService.generate(
            cycle_course_id=course.id,
            tenant_id=tenant_id,
            actor=None,
            expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token=request_token,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            max_states=max_states,
        )
        status = (
            CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED
            if outcome.reused
            else CourseExamConfiguration.AutomaticProcessingStatus.GENERATED
        )
        code = "CURRENT_GENERATION_EXISTS" if outcome.reused else "GENERATED"
        cls._record_status(
            course=course,
            configuration=configuration,
            status=status,
            code=code,
            now=now,
        )
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
            configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.CLOSED
            configuration.closed_at = now
            configuration.closed_by = None
            configuration.revision += 1
            configuration.save(
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
                course=course,
                configuration=configuration,
                metadata={"deadline": deadline},
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
    def process_due(cls, *, now=None, max_states=None):
        now = now or timezone.now()
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
        results = []
        for course_id, tenant_id in candidates:
            try:
                results.append(
                    cls.process_course(
                        cycle_course_id=course_id,
                        tenant_id=tenant_id,
                        now=now,
                        max_states=max_states,
                    )
                )
            except Exception as exc:  # per-course fault isolation is intentional
                logger.error(
                    "Automatic departmental-exam processing failed for tenant=%s course=%s error_type=%s.",
                    tenant_id,
                    course_id,
                    exc.__class__.__name__,
                )
                results.append(
                    AutomaticProcessingResult(
                        course_id,
                        "ERROR",
                        exc.__class__.__name__,
                        "Course processing failed; inspect the secured application log.",
                    )
                )
                try:
                    cls._record_error(
                        cycle_course_id=course_id,
                        tenant_id=tenant_id,
                        code=exc.__class__.__name__,
                        now=now,
                    )
                except Exception as record_exc:
                    logger.error(
                        "Automatic departmental-exam error recording failed for tenant=%s course=%s original_error_type=%s recording_error_type=%s.",
                        tenant_id,
                        course_id,
                        exc.__class__.__name__,
                        record_exc.__class__.__name__,
                    )
        return results

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
        if configuration is not None:
            cls._record_status(
                course=course,
                configuration=configuration,
                status=CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
                code=str(code)[:64],
                now=now,
            )


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
        DepartmentalExamAuthorizationService.require_generation_management(
            user=actor,
            cycle_course=course,
        )
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError("Only an Open cycle permits contribution reopen.")
        if configuration is None or configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.CLOSED:
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
            .filter(cycle_course=course)
            .order_by("revision_number")
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

        before_revision = configuration.revision
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.OPEN
        configuration.closed_at = None
        configuration.closed_by = None
        configuration.reopened_contribution_deadline = normalized_deadline
        configuration.revision += 1
        configuration.automatic_processing_status = ""
        configuration.automatic_processing_code = ""
        configuration.automatic_processed_at = None
        configuration.save(
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
            cycle_course=course,
            configuration=configuration,
            actor=actor,
            request=request,
            initializing=False,
        )
        AuditService.log_event(
            action="DE_EXAM_CONTRIBUTION_REOPENED",
            portal="ADMIN",
            entity_type="CourseExamConfiguration",
            entity_id=configuration.id,
            actor=actor,
            tenant=tenant_id,
            metadata={
                "cycle_id": cycle.id,
                "cycle_course_id": course.id,
                "previous_configuration_revision": before_revision,
                "resulting_configuration_revision": configuration.revision,
                "new_deadline": normalized_deadline,
                "superseded_generation_revision": (
                    current.revision_number if current else None
                ),
            },
            request=request,
        )
        return configuration


class AutomaticGenerationSummaryService:
    @classmethod
    def build(cls, *, cycle, now=None):
        if (
            cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            raise ValidationError("This cycle does not use automatic generation.")
        now = now or timezone.now()
        courses = list(
            CycleCourse.objects.filter(
                cycle=cycle,
                inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            )
            .select_related("course", "configuration")
            .prefetch_related(
                "offering_snapshots__campus",
                "faculty_contributions",
                Prefetch(
                    "generation_revisions",
                    queryset=ExamGenerationRevision.objects.select_related(
                        "generated_by"
                    ).prefetch_related("generated_sets"),
                ),
            )
            .order_by("course__code", "course_id")
        )
        generated = []
        not_generated = []
        last_processed_at = None
        for course in courses:
            campuses = tuple(
                dict.fromkeys(
                    snapshot.campus.name
                    for snapshot in course.offering_snapshots.all()
                )
            )
            contributions = list(course.faculty_contributions.all())
            configuration = getattr(course, "configuration", None)
            if configuration and configuration.automatic_processed_at:
                last_processed_at = max(
                    filter(
                        None,
                        (last_processed_at, configuration.automatic_processed_at),
                    )
                )
            current = next(
                (
                    row
                    for row in course.generation_revisions.all()
                    if row.current_marker == 1
                    and row.status == ExamGenerationRevision.Status.GENERATED
                ),
                None,
            )
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
            if current:
                sets = {item.set_code: item for item in current.generated_sets.all()}
                current_readiness = Stage6ReadinessService.evaluate(
                    cycle_course=course
                )
                generated.append(
                    {
                        **common,
                        "revision": current,
                        "set_a_generated": "A" in sets,
                        "set_b_generated": "B" in sets,
                        "difficulty_quotas": (
                            sets["A"].difficulty_quotas_snapshot
                            if "A" in sets
                            else {}
                        ),
                        "warnings": current_readiness.get("warnings", ()),
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
                            "message": "Course examination configuration is still Draft.",
                        }
                    ]
                }
                status = "Draft"
            elif (
                configuration.automatic_processing_status
                == CourseExamConfiguration.AutomaticProcessingStatus.ERROR
            ):
                report = {
                    "blockers": [
                        {
                            "code": "PROCESSING_ERROR",
                            "message": "Automatic generation failed during processing.",
                        }
                    ]
                }
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
                            "message": "Contribution deadline has not arrived.",
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
                report = Stage6ReadinessService.evaluate(cycle_course=course)
                status = (
                    configuration.get_automatic_processing_status_display()
                    if configuration.automatic_processing_status
                    else report.get("status", "Not generated").title()
                )
            not_generated.append(
                {
                    **common,
                    "status": status,
                    "reason": readiness_blocker_text(report),
                    "recommended_action": readiness_recommendation(report),
                    "warnings": report.get("warnings", ()),
                    "configuration": configuration,
                }
            )
        return {
            "generated": generated,
            "not_generated": not_generated,
            "total": len(courses),
            "generated_count": len(generated),
            "not_generated_count": len(not_generated),
            "last_processed_at": last_processed_at,
        }
