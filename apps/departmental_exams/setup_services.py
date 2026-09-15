"""Explicit, revision-bound setup of existing examination units; GETs never write."""
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from .exam_units import resolve_examination_unit, validate_examination_unit
from .models import (
    AnswerKeyRelease, CourseExamConfiguration, CycleCourse, ExaminationCycle,
    ExamBlueprint, ExamScenario, ExamSection, ExamGenerationRevision,
    FacultyContribution, QuestionnairePrintRelease, QuestionBlueprintPlacement,
    _classification_service_scope,
)
from .services import (
    CourseExamConfigurationService, CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService, DepartmentalExamAuthorizationService,
)


def case_aware_automatic_enabled(course):
    return (
        course.cycle.processing_mode == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        and course.exam_classification == CycleCourse.ExamClassification.DEPARTMENTAL
        and FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
            tenant_id=course.cycle.tenant_id)
    )


def _configuration_requirement_reasons(configuration):
    """Translate Stage 4 codes into actionable, row-safe opening explanations."""
    reasons = []
    if (
        configuration.questions_required_per_faculty is None
        or configuration.questions_required_per_faculty_source not in ("DEFAULT", "OVERRIDE")
    ):
        reasons.append("Configure a Faculty quota from 50 to 75.")
    if (
        configuration.final_item_count is None
        or configuration.final_item_count_source not in ("DEFAULT", "OVERRIDE")
    ):
        reasons.append("Configure a final item count from 50 to 75.")
    if not (configuration.coverage or "").strip() or configuration.coverage_source not in (
        "DEFAULT",
        "OVERRIDE",
    ):
        reasons.append("Configure examination coverage.")
    if (
        configuration.contribution_deadline is None
        or configuration.contribution_deadline_source not in ("DEFAULT", "OVERRIDE")
    ):
        reasons.append("Configure a future contribution deadline.")
    elif configuration.contribution_deadline <= timezone.now():
        reasons.append(
            "The contribution deadline has passed. Configure a future deadline before first Open."
        )
    return reasons


def _opening_requirement_explanations(configuration, blockers):
    explanations = []
    for blocker in blockers:
        if blocker == "Needs Configuration":
            details = _configuration_requirement_reasons(configuration)
            explanations.extend(details or [
                "Inherited settings no longer match the current cycle defaults; review and save the course configuration."
            ])
        elif blocker == "Contribution Deadline Passed":
            explanations.append(
                "The contribution deadline has passed. Configure a future deadline before first Open."
            )
        elif blocker == "Cycle Not Open":
            explanations.append("Open the examination cycle before opening faculty contributions.")
        elif blocker == "Not Authorized":
            explanations.append("You are not authorized to configure every examination-unit member.")
        elif blocker == "Open for Faculty Contribution":
            explanations.append("Faculty contribution intake is already open.")
        elif blocker == "Closed":
            explanations.append("The contribution intake is closed and preserved as history.")
        elif blocker != "Exempt":
            explanations.append(str(blocker))
    return explanations


def departmental_no_sections_default_eligibility(course, *, actor=None):
    """Read-only eligibility for the DEPTAL implicit No Sections first-open default.

    This deliberately rejects every persisted structure or lifecycle trace.  It is
    shared by readiness and the locked mutation path so a GET can describe the
    effective default without creating it.
    """
    from .contribution_authorization import ContributorEligibilityService

    unit = resolve_examination_unit(course, validate=False)
    member_ids = unit.member_ids
    reasons = []
    if course.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        reasons.append("The DEPTAL No Sections default is available only in Automatic Generation.")
    if not FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
        tenant_id=course.cycle.tenant_id
    ):
        reasons.append(
            "Enable Structured Case/Scenario Exam Lifecycle or configure an allowed explicit structure."
        )
    if any(
        member.inclusion_status != CycleCourse.InclusionStatus.INCLUDED
        for member in unit.members
    ):
        reasons.append("Every examination-unit member must be Included.")
    classifications = {member.exam_classification for member in unit.members}
    if classifications != {CycleCourse.ExamClassification.DEPARTMENTAL}:
        reasons.append("Every examination-unit member must be consistently classified DEPTAL.")
    try:
        validate_examination_unit(unit)
    except ValidationError as exc:
        reasons.extend(exc.messages)

    configurations = {
        row.cycle_course_id: row
        for row in CourseExamConfiguration.objects.filter(cycle_course_id__in=member_ids)
    }
    effective_configurations = []
    for member in unit.members:
        configuration = configurations.get(member.id) or CourseSetupService.effective(member)
        effective_configurations.append(configuration)
        if configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.DRAFT:
            reasons.append("Every examination-unit member must remain Draft before first Open.")
        if (
            configuration.opened_at
            or configuration.closed_at
            or configuration.reopened_contribution_deadline
            or configuration.contributor_roster_initialized_at
            or configuration.contributor_roster_revision
            or configuration.automatic_processing_status
            or configuration.automatic_processing_code
            or configuration.automatic_processed_at
        ):
            reasons.append(
                "Preserved contribution, roster, or automatic-processing history prevents default creation."
            )
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=member,
            configuration=configuration,
            user=actor,
            for_mutation=bool(actor),
        )
        reasons.extend(
            _opening_requirement_explanations(
                configuration, readiness["blockers"]
            )
        )
        if not ContributorEligibilityService.preparation_source_inventory(
            cycle_course=member
        ).eligible_sources:
            reasons.append("No qualifying accepted Faculty teaching assignments.")

    compatibility = {
        (
            config.final_item_count,
            config.questions_required_per_faculty,
            config.active_contribution_deadline,
            (config.coverage or "").strip(),
        )
        for config in effective_configurations
    }
    if len(compatibility) > 1:
        reasons.append(
            "Equivalent examination-unit members have conflicting effective configuration values."
        )

    blueprints = list(
        ExamBlueprint.objects.filter(cycle_course_id__in=member_ids).order_by(
            "cycle_course_id", "id"
        )
    )
    if blueprints:
        if len(blueprints) > 1:
            reasons.append(
                "Conflicting exam structure blueprints require administrative reconciliation."
            )
        elif blueprints[0].cycle_course_id != unit.primary.id:
            reasons.append(
                "An alias-owned exam structure blueprint requires administrative reconciliation."
            )
        elif blueprints[0].structure_frozen_at:
            reasons.append("A preserved frozen exam structure prevents default creation.")
        else:
            reasons.append("An explicit exam structure already exists and will be preserved.")
    if FacultyContribution.objects.filter(cycle_course_id__in=member_ids).exists():
        reasons.append("Existing contribution or question activity prevents default creation.")
    if ExamGenerationRevision.objects.filter(cycle_course_id__in=member_ids).exists():
        reasons.append("Existing generation activity prevents default creation.")
    if QuestionnairePrintRelease.objects.filter(cycle_course_id__in=member_ids).exists():
        reasons.append("Existing questionnaire release history prevents default creation.")
    if AnswerKeyRelease.objects.filter(
        Q(cycle_course_id__in=member_ids) | Q(recipient_course_id__in=member_ids)
    ).exists():
        reasons.append("Existing Answer Key release history prevents default creation.")
    return {
        "eligible": not reasons,
        "reasons": list(dict.fromkeys(reasons)),
        "unit": unit,
        "effective_final_item_count": (
            effective_configurations[0].final_item_count
            if effective_configurations
            else None
        ),
    }


def automatic_structure_blockers(course):
    """Reject unsupported inputs even on legacy Automatic retry/worker routes."""
    if course.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        return []
    unit = resolve_examination_unit(course, validate=False)
    blueprints = ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids)
    reasons = []
    if case_aware_automatic_enabled(unit.primary):
        rows = list(blueprints)
        if not rows:
            default = departmental_no_sections_default_eligibility(unit.primary)
            return [] if default["eligible"] else default["reasons"]
        if len(rows) != 1 or rows[0].cycle_course_id != unit.primary.id:
            return ["Configure exactly one primary-owned Departmental blueprint; conflicting or alias-owned blueprints require administrative reconciliation."]
        if any(member.exam_classification != "DEPARTMENTAL" for member in unit.members):
            reasons.append("Equivalent examination-unit members must have consistent Departmental classification.")
        blueprint = rows[0]
        sections = list(blueprint.sections.order_by("display_order", "id"))
        configurations = [CourseSetupService.effective(member) for member in unit.members]
        final_count = configurations[0].final_item_count
        if final_count is None or any(config.final_item_count != final_count for config in configurations):
            reasons.append("Configure one authoritative final item count across the examination unit.")
        if blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS:
            if sections:
                reasons.append("No Sections mode cannot retain explicit sections.")
        elif blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
            if (not sections or any(not row.title.strip() or row.item_quota < 1 or row.display_order < 1 for row in sections)
                    or len({row.display_order for row in sections}) != len(sections)
                    or sum(row.item_quota for row in sections) != final_count):
                reasons.append("Configure positive ordered section quotas that sum to the exact final item count.")
        else:
            reasons.append("The Departmental blueprint mode is invalid.")
        if any(config.opened_at for config in configurations) and (
            blueprint.structure_frozen_at is None or blueprint.structure_final_item_count != final_count
        ):
            reasons.append("Opened Departmental contributions require a valid permanently frozen blueprint.")
        return reasons
    if blueprints.exclude(mode=ExamBlueprint.Mode.NO_SECTIONS).exists() or ExamSection.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Explicit Exam Sections require enabled Departmental Case-aware Automatic generation. Contributions cannot open for this structure until that prerequisite is met.")
    if ExamScenario.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Case narratives and Linked Question groups require enabled Departmental Case-aware Automatic generation.")
    if QuestionBlueprintPlacement.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Section placements cannot be ignored by Automatic generation. Administrative review is required.")
    if len(blueprints) > 1 or any(b.cycle_course_id != unit.primary.id for b in blueprints):
        reasons.append("Administrative reconciliation is required for conflicting or alias-owned blueprints.")
    if (
        unit.primary.exam_classification == CycleCourse.ExamClassification.DEPARTMENTAL
        and not blueprints.exists()
    ):
        reasons.append(
            "Configure the Departmental exam blueprint. The automatic No Sections default requires enabled Structured Case/Scenario Exam Lifecycle."
        )
    return reasons


class CourseSetupService:
    SALT = "departmental-exams.selected-setup.v1"
    MAX_AGE = 1800

    @staticmethod
    def effective(course):
        saved = CourseExamConfiguration.objects.filter(cycle_course=course).first()
        if saved:
            return saved
        cycle = course.cycle
        values = {"cycle_course": course, "cycle_defaults_revision_snapshot": cycle.defaults_revision}
        for field in ("questions_required_per_faculty", "final_item_count", "contribution_deadline", "coverage"):
            value = getattr(cycle, "default_" + field)
            values[field] = value
            values[field + "_source"] = "DEFAULT" if value not in (None, "") else None
        return CourseExamConfiguration(**values)

    @classmethod
    def materialize(cls, course, *, actor, request=None):
        saved = CourseExamConfiguration.objects.filter(cycle_course=course).first()
        if saved:
            return saved
        effective = cls.effective(course)
        return CourseExamConfigurationService.save_course_draft(
            cycle_course_id=course.id, tenant_id=course.cycle.tenant_id, user=actor,
            expected_revision=0, final_item_count=effective.final_item_count,
            questions_required_per_faculty=effective.questions_required_per_faculty,
            final_item_count_mode="DEFAULT", questions_required_per_faculty_mode="DEFAULT",
            coverage=effective.coverage or "", coverage_mode="DEFAULT", additional_instructions="",
            contribution_deadline=effective.contribution_deadline,
            contribution_deadline_mode="DEFAULT", request=request,
        )[0]

    @staticmethod
    def require_classification_mutable(unit):
        if unit.primary.cycle.status == ExaminationCycle.Status.CLOSED:
            raise ValidationError("Closed cycles retain their historical classification.")
        configurations = CourseExamConfiguration.objects.filter(cycle_course_id__in=unit.member_ids)
        if any(c.opened_at or c.workflow_status != "DRAFT" for c in configurations):
            raise ValidationError("Exam classification is permanently frozen at first Open, including after reopen.")
        if ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids, structure_frozen_at__isnull=False).exists():
            raise ValidationError("The frozen examination unit retains its classification.")
        if FacultyContribution.objects.filter(cycle_course_id__in=unit.member_ids).exists() or ExamGenerationRevision.objects.filter(cycle_course_id__in=unit.member_ids).exists():
            raise ValidationError("Historical contribution or generation activity prevents reclassification.")

    @classmethod
    @transaction.atomic
    def classify(cls, *, course_id, tenant_id, actor, classification, expected_state, request=None):
        course = CycleCourse.objects.select_related("cycle").get(pk=course_id, cycle__tenant_id=tenant_id)
        course.cycle = ExaminationCycle.objects.select_for_update().get(pk=course.cycle_id)
        unit = resolve_examination_unit(course, for_update=True, validate=False)
        members = list(CycleCourse.objects.select_for_update().filter(pk__in=unit.member_ids).select_related("cycle").order_by("pk"))
        for member in members:
            DepartmentalExamAuthorizationService.require_configure_cycle_course(user=actor, cycle_course=member)
        if cls.fingerprint(course) != expected_state:
            raise CourseExamConfigurationConflict("The examination unit changed. Review its current classification and settings.")
        cls.require_classification_mutable(unit)
        if classification not in ("STANDARDIZED", "DEPARTMENTAL"):
            raise ValidationError("Select an explicit exam classification.")
        if classification == "STANDARDIZED":
            blueprints = ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids)
            if blueprints.exclude(mode="NO_SECTIONS").exists() or ExamSection.objects.filter(blueprint__in=blueprints).exists() or ExamScenario.objects.filter(blueprint__in=blueprints).exists():
                raise ValidationError("Existing explicit structure cannot be replaced by Standardized classification.")
            if QuestionBlueprintPlacement.objects.filter(blueprint__in=blueprints).exists():
                raise ValidationError("Existing placements require explicit structure review before classification.")
        before = {str(m.id): m.exam_classification for m in members}
        with _classification_service_scope():
            CycleCourse.objects.filter(pk__in=unit.member_ids).update(exam_classification=classification, updated_at=timezone.now())
        AuditService.log_event(action="DE_EXAM_CLASSIFICATION_CHANGED", portal="ADMIN", entity_type="CycleCourse", entity_id=unit.primary.id, actor=actor, tenant=tenant_id, before_data=before, after_data={"classification": classification}, metadata={"member_ids": list(unit.member_ids)}, request=request)

    @staticmethod
    def fingerprint(course):
        unit = resolve_examination_unit(course, validate=False)
        effective = [CourseSetupService.effective(member) for member in unit.members]
        evidence = {
            "effective_configurations": [
                {field.attname: getattr(configuration, field.attname)
                 for field in CourseExamConfiguration._meta.concrete_fields}
                for configuration in effective
            ],
            "cycle_settings": list(ExaminationCycle.objects.filter(pk=course.cycle_id).values()),
            "structure_settings": list(ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values()),
            "structured_enabled": FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(tenant_id=course.cycle.tenant_id),
            "unit": unit.fingerprint_metadata(),
            "cycle": list(ExaminationCycle.objects.filter(pk=course.cycle_id).values("updated_at", "status", "defaults_revision")),
            "members": list(CycleCourse.objects.filter(pk__in=unit.member_ids).order_by("pk").values("id", "updated_at", "exam_classification", "inclusion_status")),
            "configs": list(CourseExamConfiguration.objects.filter(cycle_course_id__in=unit.member_ids).order_by("cycle_course_id").values("cycle_course_id", "revision", "updated_at", "workflow_status", "opened_at", "closed_at", "contributor_roster_initialized_at", "contributor_roster_revision", "automatic_processing_status", "automatic_processed_at")),
            "blueprints": list(ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values("id", "revision", "mode", "updated_at", "structure_frozen_at")),
            "sections": list(ExamSection.objects.filter(blueprint__cycle_course_id__in=unit.member_ids).order_by("pk").values()),
            "contributions": list(FacultyContribution.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values("id", "cycle_course_id", "status", "roster_status", "revision", "updated_at")),
            "generation": list(ExamGenerationRevision.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values("id", "cycle_course_id", "status", "revision_number", "current_marker", "updated_at")),
            "questionnaire_releases": list(QuestionnairePrintRelease.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values("id", "cycle_course_id", "status", "active_marker", "updated_at")),
            "answer_key_releases": list(AnswerKeyRelease.objects.filter(Q(cycle_course_id__in=unit.member_ids) | Q(recipient_course_id__in=unit.member_ids)).order_by("pk").values("id", "cycle_course_id", "recipient_course_id", "status", "active_marker", "updated_at")),
        }
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    @transaction.atomic
    def preview(cls, *, cycle, actor, selected_ids=None):
        # Match opening's cycle-first lock order. Supported configuration,
        # structure and membership writers serialize on this cycle as well.
        # This transaction ends before rendering/awaiting user confirmation.
        cycle = ExaminationCycle.objects.select_for_update().get(
            pk=cycle.id, tenant_id=cycle.tenant_id)
        list(CycleCourse.objects.select_for_update().filter(cycle=cycle).order_by("pk"))
        for _attempt in range(3):
            cycle.refresh_from_db()
            rows = cls._review_rows(cycle=cycle, actor=actor, selected_ids=selected_ids)
            # The signed fingerprint precedes ALL displayed unit reads. Never
            # replace it with this later hash: a change requires a fresh review,
            # including defaults, membership, blueprint and section details.
            if all(row["fingerprint"] == cls.fingerprint(
                CycleCourse.objects.select_related("cycle").get(pk=row["course"].id)
            ) for row in rows):
                return rows
        # Fail closed even for re-entrant/test writers that bypass serialization.
        for row in rows:
            row["status"] = "Blocked"
            row["reasons"] = ["The examination unit changed while preparing review. Reload and review again."]
        return rows

    @classmethod
    def _review_rows(cls, *, cycle, actor, selected_ids=None):
        from .contribution_authorization import ContributorEligibilityService
        courses = CycleCourse.objects.filter(cycle=cycle).select_related("cycle", "course", "responsible_department").order_by("course__code", "id")
        if selected_ids is not None:
            courses = courses.filter(pk__in=selected_ids)
        courses = list(courses)
        if selected_ids is not None and set(c.id for c in courses) != set(selected_ids):
            raise PermissionDenied("Selected courses are outside this examination cycle.")
        rows, seen = [], set()
        for requested in courses:
            try:
                DepartmentalExamAuthorizationService.require_cycle_course_inclusion_management(user=actor, cycle_course=requested)
            except PermissionDenied:
                if selected_ids is not None:
                    raise
                continue
            review_fingerprint = cls.fingerprint(requested)
            unit = resolve_examination_unit(requested, validate=False)
            if unit.primary.id in seen:
                continue
            seen.add(unit.primary.id)
            course = unit.primary
            config = cls.effective(course)
            reasons = []
            if cycle.status != "OPEN":
                reasons.append("Open the examination cycle before opening faculty contributions.")
            if cycle.processing_mode != "AUTOMATIC_GENERATION":
                reasons.append("Legacy Manual exams retain their existing authorized workflow.")
            if len({m.exam_classification for m in unit.members}) != 1:
                reasons.append("Equivalency members have inconsistent classifications.")
            try:
                validate_examination_unit(unit)
            except ValidationError as exc:
                reasons.extend(exc.messages)
            reasons.extend(automatic_structure_blockers(course))
            configurations = list(CourseExamConfiguration.objects.filter(
                cycle_course_id__in=unit.member_ids))
            any_open = any(c.workflow_status == "OPEN" for c in configurations)
            all_open = (len(configurations) == len(unit.member_ids)
                        and all(c.workflow_status == "OPEN" for c in configurations))
            if course.inclusion_status == "EXEMPT":
                status = "Exempt"
                reasons = [
                    "This examination unit is Exempt and is excluded from contribution opening."
                ]
            elif cycle.status == "CLOSED" or cycle.processing_mode != "AUTOMATIC_GENERATION":
                status = "Preserved history"
                reasons = [
                    "This course uses a closed or Manual lifecycle. Existing configuration and history are preserved; unified setup will not change them."
                ]
            elif any_open:
                if not all_open:
                    reasons.append("Not every examination-unit member is Open. Administrative review is required.")
                status = "Blocked" if reasons else "Already open"
                if status == "Already open":
                    reasons = [
                        "Faculty contribution intake is already open; setup will not rebuild or reopen it."
                    ]
            elif any(c.opened_at or c.workflow_status == "CLOSED" for c in configurations) or ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids, structure_frozen_at__isnull=False).exists():
                status = "Preserved history"
                reasons = [
                    "Prior opening, closure, or frozen-structure evidence is preserved. Use the existing lifecycle records; unified setup will not replace them."
                ]
            else:
                for member in unit.members:
                    effective = cls.effective(member)
                    readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=member, configuration=effective)
                    reasons.extend(_opening_requirement_explanations(
                        effective, readiness["blockers"]))
                    if not ContributorEligibilityService.preparation_source_inventory(cycle_course=member).eligible_sources:
                        reasons.append("No qualifying accepted Faculty teaching assignments.")
                if course.exam_classification == "UNCLASSIFIED_LEGACY":
                    reasons.append("Explicit classification is required for this legacy course before using unified setup.")
                status = "Blocked" if reasons else "Ready"
                if status == "Ready":
                    reasons = ["All opening requirements are satisfied."]
            blueprints = list(
                ExamBlueprint.objects.filter(
                    cycle_course_id__in=unit.member_ids
                ).order_by("cycle_course_id", "id")
            )
            blueprint = blueprints[0] if blueprints else None
            default_eligibility = None
            if not blueprint and course.exam_classification == "DEPARTMENTAL":
                default_eligibility = departmental_no_sections_default_eligibility(course)
            if blueprint:
                structure_display = (
                    "No Sections — explicit"
                    if blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS
                    else f"Use Sections — {blueprint.sections.count()} section(s)"
                )
            elif default_eligibility and default_eligibility["eligible"]:
                structure_display = "No Sections — default (created on Open)"
            elif course.exam_classification == "STANDARDIZED":
                structure_display = "No Sections — default (created on Open)"
            else:
                structure_display = "Not configured"
            can_configure_structure = bool(
                case_aware_automatic_enabled(course)
                and cycle.status == ExaminationCycle.Status.OPEN
                and course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
                and {member.exam_classification for member in unit.members}
                == {CycleCourse.ExamClassification.DEPARTMENTAL}
                and not any(
                    configuration.workflow_status
                    != CourseExamConfiguration.WorkflowStatus.DRAFT
                    or configuration.opened_at
                    or configuration.closed_at
                    or configuration.contributor_roster_initialized_at
                    or configuration.contributor_roster_revision
                    or configuration.automatic_processing_status
                    or configuration.automatic_processing_code
                    or configuration.automatic_processed_at
                    for configuration in configurations
                )
                and (
                    not blueprints
                    or (
                        len(blueprints) == 1
                        and blueprints[0].cycle_course_id == unit.primary.id
                        and blueprints[0].structure_frozen_at is None
                    )
                )
                and not FacultyContribution.objects.filter(
                    cycle_course_id__in=unit.member_ids
                ).exists()
                and not ExamGenerationRevision.objects.filter(
                    cycle_course_id__in=unit.member_ids
                ).exists()
                and not QuestionnairePrintRelease.objects.filter(
                    cycle_course_id__in=unit.member_ids
                ).exists()
                and not AnswerKeyRelease.objects.filter(
                    Q(cycle_course_id__in=unit.member_ids)
                    | Q(recipient_course_id__in=unit.member_ids)
                ).exists()
            )
            rows.append({"course": course, "configuration": config, "status": status,
                         "reasons": list(dict.fromkeys(reasons)),
                         "fingerprint": review_fingerprint,
                         "member_ids": list(unit.member_ids),
                         "structure_display": structure_display,
                         "sections": list(blueprint.sections.order_by("display_order", "id")) if blueprint else [],
                         "can_configure_structure": can_configure_structure})
        return rows

    @classmethod
    def confirmation(cls, *, cycle, actor, rows):
        return signing.dumps({"actor": actor.id, "tenant": cycle.tenant_id, "cycle": cycle.id, "batch": uuid.uuid4().hex,
            "rows": [{"id": r["course"].id, "fingerprint": r["fingerprint"], "status": r["status"]} for r in rows]}, salt=cls.SALT)

    @classmethod
    @transaction.atomic
    def open_selection(cls, *, cycle, actor, token, request=None, required_course_id=None):
        try:
            state = signing.loads(token, salt=cls.SALT, max_age=cls.MAX_AGE)
        except signing.BadSignature as exc:
            raise CourseExamConfigurationConflict("The setup preview expired or is invalid. Review the selection again.") from exc
        if (state.get("actor"), state.get("tenant"), state.get("cycle")) != (actor.id, cycle.tenant_id, cycle.id):
            raise PermissionDenied("This setup confirmation belongs to a different actor or cycle.")
        cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle.id, tenant_id=cycle.tenant_id)
        ids = [row["id"] for row in state["rows"]]
        if not ids:
            raise ValidationError("Select at least one course.")
        # Cycle-first serialization matches all supported configuration/roster writers.
        list(CycleCourse.objects.select_for_update().filter(cycle=cycle).order_by("pk"))
        if required_course_id is not None:
            requested = CycleCourse.objects.select_related("cycle").get(pk=required_course_id, cycle=cycle)
            unit = resolve_examination_unit(requested, for_update=True, validate=False)
            if ids != [unit.primary.id]:
                raise PermissionDenied("This confirmation belongs to a different examination unit.")
        rows = cls.preview(cycle=cycle, actor=actor, selected_ids=ids)
        receipt = AuditLog.objects.filter(action="DE_EXAM_SETUP_BATCH_OPENED", entity_id=state["batch"], tenant_id=cycle.tenant_id, actor_user=actor).exists()
        if receipt:
            return rows, True
        old = {r["id"]: r for r in state["rows"]}
        if cycle.status != "OPEN" or any(r["status"] != "Ready" or r["course"].id not in old
                                         or old[r["course"].id].get("status") != "Ready"
                                         or r["fingerprint"] != old[r["course"].id]["fingerprint"]
                                         for r in rows):
            raise CourseExamConfigurationConflict("No courses opened. The selection is blocked or changed; review the refreshed readiness and remove blocked courses.")
        for row in rows:
            if row["status"] == "Already open":
                continue
            for member_id in row["member_ids"]:
                member = CycleCourse.objects.select_related("cycle").get(pk=member_id)
                cls.materialize(member, actor=actor, request=request)
            for member_id in row["member_ids"]:
                configuration = CourseExamConfiguration.objects.get(cycle_course_id=member_id)
                CourseExamConfigurationService.open_for_contribution(cycle_course_id=member_id, tenant_id=cycle.tenant_id, user=actor, expected_revision=configuration.revision, request=request)
        AuditService.log_event(action="DE_EXAM_SETUP_BATCH_OPENED", portal="ADMIN", entity_type="ExamSetupBatch", entity_id=state["batch"], actor=actor, tenant=cycle.tenant_id, metadata={"cycle_id": cycle.id, "results": [{"course_id": r["course"].id, "result": "Already open" if r["status"] == "Already open" else "Opened"} for r in rows]}, request=request)
        return rows, False

    @classmethod
    def open_group_locked(cls, unit, *, requested, actor, expected_revision, request=None):
        """Validate every grouped member before any status changes invalidate compatibility."""
        from .blueprint_services import StructuredExamLifecyclePolicy
        from .exam_units import validate_examination_unit
        service = CourseExamConfigurationService
        # Check the original windows before prepare_structure can write anything.
        for config in CourseExamConfiguration.objects.select_for_update().filter(
            cycle_course_id__in=unit.member_ids
        ).order_by("cycle_course_id"):
            service.require_existing_intake_deadline(config)
        cls.prepare_structure(unit.primary, actor=actor, request=request)
        validate_examination_unit(unit)
        configurations = {c.cycle_course_id: c for c in CourseExamConfiguration.objects.select_for_update().filter(cycle_course_id__in=unit.member_ids).order_by("cycle_course_id")}
        requested_config = configurations[requested.id]
        if all(c.workflow_status == "OPEN" for c in configurations.values()):
            return requested_config, False
        service._require_revision(requested_config, expected_revision)
        validated = []
        for member in unit.members:
            config = configurations[member.id]
            DepartmentalExamAuthorizationService.require_configure_cycle_course(user=actor, cycle_course=member)
            service._require_cycle_open_for_workflow(member)
            service._require_no_activity(member)
            if config.workflow_status != "DRAFT" or config.opened_at:
                raise ValidationError("Grouped historical intake requires the governed reopen workflow.")
            readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=member, configuration=config, user=actor, for_mutation=True)
            if readiness["blockers"]:
                raise ValidationError(readiness["blockers"])
            structure = StructuredExamLifecyclePolicy.validate_for_open(cycle_course=member, configuration=config)
            validated.append((member, config, structure))
        for member, config, structure in validated:
            service._open_validated_locked(parent=member, configuration=config, user=actor,
                expected_revision=config.revision, structured_lifecycle=structure, request=request)
        return requested_config, True

    @classmethod
    def prepare_structure(cls, course, *, actor, request=None):
        """Called under the existing cycle/parent transaction, before first Open."""
        reasons = automatic_structure_blockers(course)
        if reasons:
            raise ValidationError(reasons)
        if course.exam_classification == "UNCLASSIFIED_LEGACY":
            return
        unit = resolve_examination_unit(course, for_update=True, validate=False)
        for member in unit.members:
            if member.exam_classification != course.exam_classification:
                raise ValidationError("Equivalency members have inconsistent classifications.")
            cls.materialize(member, actor=actor, request=request)
        blueprints = ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids)
        if not blueprints.exists():
            if course.exam_classification == "DEPARTMENTAL":
                default = departmental_no_sections_default_eligibility(
                    unit.primary, actor=actor
                )
                if not default["eligible"]:
                    raise ValidationError(default["reasons"])
                action = "DE_EXAM_DEPARTMENTAL_DEFAULT_STRUCTURE_CREATED"
                origin = "DEPTAL_NO_SECTIONS_DEFAULT"
                final_item_count = default["effective_final_item_count"]
            else:
                action = "DE_EXAM_STANDARD_STRUCTURE_CREATED"
                origin = "STANDARDIZED_NO_SECTIONS_DEFAULT"
                final_item_count = cls.effective(unit.primary).final_item_count
            blueprint = ExamBlueprint.objects.create(
                cycle_course=unit.primary, mode="NO_SECTIONS", revision=1,
                created_by=actor, updated_by=actor)
            AuditService.log_event(
                action=action, portal="ADMIN", entity_type="ExamBlueprint",
                entity_id=blueprint.id, actor=actor,
                tenant=course.cycle.tenant_id,
                metadata={"cycle_course_id": unit.primary.id,
                          "member_cycle_course_ids": list(unit.member_ids),
                          "mode": "NO_SECTIONS", "origin": origin,
                          "effective_final_item_count": final_item_count},
                request=request)
