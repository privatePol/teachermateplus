from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService

from .models import (
    BlockedContributionResolution,
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamBlueprint,
    ExamGenerationRevision,
    ExamScenario,
    ExamScenarioMember,
    ExamSection,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
    QuestionBlueprintPlacement,
    QuestionImportBatch,
    QuestionImportRow,
)
from .services import DepartmentalExamAuthorizationService


@dataclass(frozen=True)
class SafeDeleteBlocker:
    code: str
    message: str


@dataclass(frozen=True)
class SafeDeleteCounts:
    cycle_courses: int = 0
    offering_snapshots: int = 0
    draft_configurations: int = 0
    included_courses: int = 0
    exempt_courses: int = 0
    faculty_contributions: int = 0
    questions: int = 0
    blueprints: int = 0
    generation_revisions: int = 0


@dataclass(frozen=True)
class SafeDeleteEligibility:
    eligible: bool
    blockers: tuple[SafeDeleteBlocker, ...]
    counts: SafeDeleteCounts


@dataclass(frozen=True)
class SafeDeleteResult:
    deleted: bool
    cycle_id: int
    blockers: tuple[SafeDeleteBlocker, ...]
    counts: SafeDeleteCounts


class _DeletionAuditFailure(Exception):
    pass


class ExaminationCycleSafeDeleteService:
    """Fail-closed hard deletion for setup-only examination cycles."""

    MANAGE_PERMISSION = "departmental_exams.manage_cycles"
    SETUP_AUDIT_ACTIONS = frozenset(
        {
            "DE_EXAM_CYCLE_CREATED",
            "DE_EXAM_CYCLE_OPENED",
            "DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            "DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED",
            "DE_EXAM_CYCLE_COURSE_EXEMPTED",
            "DE_EXAM_CYCLE_COURSE_RESTORED",
            "DE_EXAM_COURSE_CONFIGURATION_SAVED",
            "DE_EXAM_COURSE_OVERRIDES_REMOVED",
        }
    )

    CLOSED_BLOCKER = SafeDeleteBlocker(
        "cycle_closed", "This examination cycle is Closed and must be preserved."
    )
    HISTORICAL_BLOCKER = SafeDeleteBlocker(
        "historical_activity",
        "This cycle contains historical activity that must be preserved.",
    )
    PROTECTED_RELATION_BLOCKER = SafeDeleteBlocker(
        "unexpected_descendant",
        "This cycle contains historical activity that must be preserved.",
    )
    PROTECTED_DELETE_BLOCKER = SafeDeleteBlocker(
        "protected_delete",
        "This cycle contains protected activity and was not deleted.",
    )
    AUDIT_FAILURE_BLOCKER = SafeDeleteBlocker(
        "audit_failure",
        "The deletion could not be safely recorded. Nothing was deleted.",
    )

    @classmethod
    def _authorize(cls, *, user, tenant_id):
        DepartmentalExamAuthorizationService.require_permission(
            user=user,
            permission=cls.MANAGE_PERMISSION,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _query(model, *, lock, order_by="id", **filters):
        queryset = model.objects.filter(**filters).order_by(order_by)
        if lock:
            queryset = queryset.select_for_update()
        return list(queryset)

    @classmethod
    def _load_state(cls, *, cycle, lock):
        courses = cls._query(CycleCourse, lock=lock, cycle=cycle)
        course_ids = [course.id for course in courses]
        configurations = cls._query(
            CourseExamConfiguration,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        snapshots = cls._query(
            CycleCourseOffering,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        contributions = cls._query(
            FacultyContribution,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        contribution_ids = [row.id for row in contributions]
        eligibility_sources = cls._query(
            FacultyContributionEligibilitySource,
            lock=lock,
            order_by="contribution_id",
            contribution_id__in=contribution_ids,
        )
        questions = cls._query(
            Question,
            lock=lock,
            order_by="contribution_id",
            contribution_id__in=contribution_ids,
        )
        question_ids = [row.id for row in questions]
        import_batches = cls._query(
            QuestionImportBatch,
            lock=lock,
            order_by="contribution_id",
            contribution_id__in=contribution_ids,
        )
        import_batch_ids = [row.id for row in import_batches]
        import_rows = cls._query(
            QuestionImportRow,
            lock=lock,
            order_by="batch_id",
            batch_id__in=import_batch_ids,
        )
        resolutions = cls._query(
            BlockedContributionResolution,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        blueprints = cls._query(
            ExamBlueprint,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        blueprint_ids = [row.id for row in blueprints]
        sections = cls._query(
            ExamSection,
            lock=lock,
            order_by="blueprint_id",
            blueprint_id__in=blueprint_ids,
        )
        placements = cls._query(
            QuestionBlueprintPlacement,
            lock=lock,
            order_by="blueprint_id",
            blueprint_id__in=blueprint_ids,
        )
        scenarios = cls._query(
            ExamScenario,
            lock=lock,
            order_by="blueprint_id",
            blueprint_id__in=blueprint_ids,
        )
        scenario_ids = [row.id for row in scenarios]
        scenario_members = cls._query(
            ExamScenarioMember,
            lock=lock,
            order_by="scenario_id",
            scenario_id__in=scenario_ids,
        )
        revisions = cls._query(
            ExamGenerationRevision,
            lock=lock,
            order_by="cycle_course_id",
            cycle_course_id__in=course_ids,
        )
        revision_ids = [row.id for row in revisions]
        generated_sets = cls._query(
            GeneratedExamSet,
            lock=lock,
            order_by="generation_revision_id",
            generation_revision_id__in=revision_ids,
        )
        generated_set_ids = [row.id for row in generated_sets]
        generated_items = cls._query(
            GeneratedExamItem,
            lock=lock,
            order_by="generated_set_id",
            generated_set_id__in=generated_set_ids,
        )
        return {
            "courses": courses,
            "course_ids": course_ids,
            "configurations": configurations,
            "snapshots": snapshots,
            "contributions": contributions,
            "eligibility_sources": eligibility_sources,
            "questions": questions,
            "import_batches": import_batches,
            "import_rows": import_rows,
            "resolutions": resolutions,
            "blueprints": blueprints,
            "sections": sections,
            "placements": placements,
            "scenarios": scenarios,
            "scenario_members": scenario_members,
            "revisions": revisions,
            "generated_sets": generated_sets,
            "generated_items": generated_items,
        }

    @staticmethod
    def _related_query(relation, instances):
        return relation.related_model._base_manager.filter(
            **{f"{relation.field.name}__in": instances}
        )

    @classmethod
    def _has_unknown_related_activity(cls, *, cycle, state):
        checks = (
            ([cycle], {CycleCourse}),
            (
                state["courses"],
                {
                    CycleCourseOffering,
                    CourseExamConfiguration,
                    FacultyContribution,
                    BlockedContributionResolution,
                    ExamBlueprint,
                    ExamGenerationRevision,
                },
            ),
            (state["configurations"], set()),
            (state["snapshots"], set()),
        )
        for instances, allowed_models in checks:
            if not instances:
                continue
            model = type(instances[0])
            for relation in model._meta.related_objects:
                if relation.related_model in allowed_models:
                    continue
                try:
                    if cls._related_query(relation, instances).exists():
                        return True
                except Exception:
                    return True
        return False

    @classmethod
    def _linked_audits(cls, *, cycle, state, lock):
        link = Q(metadata_json__cycle_id=cycle.id) | Q(
            metadata_json__cycle_id=str(cycle.id)
        ) | Q(entity_type="ExaminationCycle", entity_id=str(cycle.id))
        entity_groups = (
            ("CycleCourse", state["course_ids"]),
            (
                "CourseExamConfiguration",
                [row.id for row in state["configurations"]],
            ),
            ("FacultyContribution", [row.id for row in state["contributions"]]),
            ("Question", [row.id for row in state["questions"]]),
            ("ExamBlueprint", [row.id for row in state["blueprints"]]),
            ("ExamScenario", [row.id for row in state["scenarios"]]),
            (
                "ExamGenerationRevision",
                [row.id for row in state["revisions"]],
            ),
        )
        for entity_type, entity_ids in entity_groups:
            if entity_ids:
                link |= Q(
                    entity_type=entity_type,
                    entity_id__in=[str(value) for value in entity_ids],
                )
        queryset = AuditLog.objects.filter(
            tenant_id=cycle.tenant_id,
            action__startswith="DE_EXAM_",
        ).filter(link).order_by("id")
        if lock:
            queryset = queryset.select_for_update()
        return list(queryset)

    @staticmethod
    def _counts(state):
        courses = state["courses"]
        return SafeDeleteCounts(
            cycle_courses=len(courses),
            offering_snapshots=len(state["snapshots"]),
            draft_configurations=sum(
                row.workflow_status == CourseExamConfiguration.WorkflowStatus.DRAFT
                for row in state["configurations"]
            ),
            included_courses=sum(
                row.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
                for row in courses
            ),
            exempt_courses=sum(
                row.inclusion_status == CycleCourse.InclusionStatus.EXEMPT
                for row in courses
            ),
            faculty_contributions=len(state["contributions"]),
            questions=len(state["questions"]),
            blueprints=len(state["blueprints"]),
            generation_revisions=len(state["revisions"]),
        )

    @classmethod
    def _evaluate_loaded(cls, *, cycle, state, audits):
        blockers = []

        def add(code, message):
            if code not in {blocker.code for blocker in blockers}:
                blockers.append(SafeDeleteBlocker(code, message))

        if cycle.status == ExaminationCycle.Status.CLOSED:
            blockers.append(cls.CLOSED_BLOCKER)
        elif cycle.status not in (
            ExaminationCycle.Status.DRAFT,
            ExaminationCycle.Status.OPEN,
        ):
            blockers.append(cls.HISTORICAL_BLOCKER)

        configurations = state["configurations"]
        if any(
            row.workflow_status != CourseExamConfiguration.WorkflowStatus.DRAFT
            or row.opened_at is not None
            or row.opened_by_id is not None
            or row.closed_at is not None
            or row.closed_by_id is not None
            for row in configurations
        ):
            add(
                "contribution_workflow_started",
                "Faculty contributions have already been opened.",
            )
        if any(
            row.reopened_contribution_deadline is not None for row in configurations
        ):
            add(
                "reopen_history",
                "This cycle contains approval, lock, or reopen history.",
            )
        if any(
            row.contributor_roster_initialized_at is not None
            or row.contributor_roster_initialized_by_id is not None
            or row.contributor_roster_revision != 0
            for row in configurations
        ):
            add(
                "roster_initialized",
                "A contributor roster has already been prepared.",
            )
        if any(
            row.automatic_processing_status
            or row.automatic_processing_code
            or row.automatic_processed_at is not None
            for row in configurations
        ):
            add(
                "automatic_processing",
                "Automatic processing has already started.",
            )
        if any(row.contributor_instructions_snapshot for row in configurations):
            add(
                "opened_snapshot",
                "Faculty contributions have already been opened.",
            )
        if state["contributions"] or state["eligibility_sources"]:
            add(
                "faculty_contributions",
                "Faculty contribution records already exist.",
            )
        if state["questions"]:
            add(
                "questions",
                "Questions have already been encoded or imported.",
            )
        if state["import_batches"] or state["import_rows"]:
            add(
                "question_imports",
                "Question import activity already exists.",
            )
        if state["resolutions"]:
            add(
                "contributor_resolution",
                "This cycle contains contributor-resolution history that must be preserved.",
            )
        if (
            state["blueprints"]
            or state["sections"]
            or state["placements"]
            or state["scenarios"]
            or state["scenario_members"]
        ):
            add("blueprint", "An examination blueprint already exists.")
        if (
            state["revisions"]
            or state["generated_sets"]
            or state["generated_items"]
        ):
            add(
                "generation",
                "Generated examination revisions already exist.",
            )
        if any(audit.action not in cls.SETUP_AUDIT_ACTIONS for audit in audits):
            blockers.append(cls.HISTORICAL_BLOCKER)
        if cls._has_unknown_related_activity(cycle=cycle, state=state):
            blockers.append(cls.PROTECTED_RELATION_BLOCKER)

        deduplicated = []
        seen = set()
        for blocker in blockers:
            if blocker.code not in seen:
                deduplicated.append(blocker)
                seen.add(blocker.code)
        blockers = tuple(deduplicated)
        return SafeDeleteEligibility(
            eligible=not blockers,
            blockers=blockers,
            counts=cls._counts(state),
        )

    @classmethod
    def evaluate(cls, *, cycle_id, tenant_id, user):
        cls._authorize(user=user, tenant_id=tenant_id)
        cycle = ExaminationCycle.objects.select_related(
            "academic_year", "term"
        ).get(id=cycle_id, tenant_id=tenant_id)
        state = cls._load_state(cycle=cycle, lock=False)
        audits = cls._linked_audits(cycle=cycle, state=state, lock=False)
        return cls._evaluate_loaded(cycle=cycle, state=state, audits=audits)

    @classmethod
    def delete(cls, *, cycle_id, tenant_id, user, request=None):
        cls._authorize(user=user, tenant_id=tenant_id)
        try:
            with transaction.atomic():
                cycle = (
                    ExaminationCycle.objects.select_for_update()
                    .select_related("academic_year", "term")
                    .get(id=cycle_id, tenant_id=tenant_id)
                )
                state = cls._load_state(cycle=cycle, lock=True)
                audits = cls._linked_audits(cycle=cycle, state=state, lock=True)
                cls._authorize(user=user, tenant_id=tenant_id)
                eligibility = cls._evaluate_loaded(
                    cycle=cycle,
                    state=state,
                    audits=audits,
                )
                if not eligibility.eligible:
                    return SafeDeleteResult(
                        deleted=False,
                        cycle_id=cycle.id,
                        blockers=eligibility.blockers,
                        counts=eligibility.counts,
                    )

                deleted_cycle_id = cycle.id
                audit_metadata = {
                    "deleted_cycle_id": deleted_cycle_id,
                    "tenant_id": cycle.tenant_id,
                    "academic_year_id": cycle.academic_year_id,
                    "academic_year_code": cycle.academic_year.code,
                    "term_id": cycle.term_id,
                    "term_code": cycle.term.code,
                    "exam_period": cycle.exam_period,
                    "processing_mode": cycle.processing_mode,
                    "status_at_deletion": cycle.status,
                    "cycle_courses_removed": eligibility.counts.cycle_courses,
                    "offering_snapshots_removed": eligibility.counts.offering_snapshots,
                    "draft_configurations_removed": eligibility.counts.draft_configurations,
                    "included_courses_removed": eligibility.counts.included_courses,
                    "exempt_courses_removed": eligibility.counts.exempt_courses,
                }

                configuration_ids = [row.id for row in state["configurations"]]
                snapshot_ids = [row.id for row in state["snapshots"]]
                course_ids = state["course_ids"]
                if configuration_ids:
                    CourseExamConfiguration.objects.filter(
                        id__in=configuration_ids
                    ).delete()
                if snapshot_ids:
                    CycleCourseOffering.objects.filter(id__in=snapshot_ids).delete()
                if course_ids:
                    CycleCourse.objects.filter(id__in=course_ids).delete()
                cycle.delete()
                try:
                    AuditService.log_event(
                        action="DE_EXAM_CYCLE_DELETED",
                        portal="ADMIN",
                        entity_type="ExaminationCycle",
                        entity_id=deleted_cycle_id,
                        actor=user,
                        tenant=tenant_id,
                        metadata=audit_metadata,
                        request=request,
                    )
                except Exception as exc:
                    raise _DeletionAuditFailure from exc
                return SafeDeleteResult(
                    deleted=True,
                    cycle_id=deleted_cycle_id,
                    blockers=(),
                    counts=eligibility.counts,
                )
        except ProtectedError:
            return SafeDeleteResult(
                deleted=False,
                cycle_id=cycle_id,
                blockers=(cls.PROTECTED_DELETE_BLOCKER,),
                counts=SafeDeleteCounts(),
            )
        except _DeletionAuditFailure:
            return SafeDeleteResult(
                deleted=False,
                cycle_id=cycle_id,
                blockers=(cls.AUDIT_FAILURE_BLOCKER,),
                counts=SafeDeleteCounts(),
            )
