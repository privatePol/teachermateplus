"""Explicit, revision-bound setup of existing examination units; GETs never write."""
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from .exam_units import resolve_examination_unit, validate_examination_unit
from .models import (
    CourseExamConfiguration, CycleCourse, ExaminationCycle, ExamBlueprint,
    ExamScenario, ExamSection, ExamGenerationRevision, FacultyContribution,
    QuestionBlueprintPlacement, _classification_service_scope,
)
from .services import (
    CourseExamConfigurationService, CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService, DepartmentalExamAuthorizationService,
)


def automatic_structure_blockers(course):
    """Reject unsupported inputs even on legacy Automatic retry/worker routes."""
    if course.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        return []
    unit = resolve_examination_unit(course, validate=False)
    blueprints = ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids)
    reasons = []
    if blueprints.exclude(mode=ExamBlueprint.Mode.NO_SECTIONS).exists() or ExamSection.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Explicit Exam Sections are not supported by Automatic generation in Phase 1. Contributions cannot open for this structure yet.")
    if ExamScenario.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Case narratives and Linked Question groups require Case-aware Automatic generation, which is deferred.")
    if QuestionBlueprintPlacement.objects.filter(blueprint__in=blueprints).exists():
        reasons.append("Section placements cannot be ignored by Automatic generation. Administrative review is required.")
    if len(blueprints) > 1 or any(b.cycle_course_id != unit.primary.id for b in blueprints):
        reasons.append("Administrative reconciliation is required for conflicting or alias-owned blueprints.")
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
        evidence = {
            "unit": unit.fingerprint_metadata(),
            "cycle": list(ExaminationCycle.objects.filter(pk=course.cycle_id).values("updated_at", "status", "defaults_revision")),
            "members": list(CycleCourse.objects.filter(pk__in=unit.member_ids).order_by("pk").values("id", "updated_at", "exam_classification", "inclusion_status")),
            "configs": list(CourseExamConfiguration.objects.filter(cycle_course_id__in=unit.member_ids).order_by("cycle_course_id").values("cycle_course_id", "revision", "updated_at", "opened_at")),
            "blueprints": list(ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids).order_by("pk").values("id", "revision", "mode", "updated_at", "structure_frozen_at")),
            "sections": list(ExamSection.objects.filter(blueprint__cycle_course_id__in=unit.member_ids).order_by("pk").values()),
        }
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    def preview(cls, *, cycle, actor, selected_ids=None):
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
            elif cycle.status == "CLOSED" or cycle.processing_mode != "AUTOMATIC_GENERATION":
                status = "Preserved history"
            elif any_open:
                if not all_open:
                    reasons.append("Not every examination-unit member is Open. Administrative review is required.")
                status = "Blocked" if reasons else "Already open"
            elif any(c.opened_at or c.workflow_status == "CLOSED" for c in configurations) or ExamBlueprint.objects.filter(cycle_course_id__in=unit.member_ids, structure_frozen_at__isnull=False).exists():
                status = "Preserved history"
            else:
                for member in unit.members:
                    effective = cls.effective(member)
                    readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=member, configuration=effective)
                    reasons.extend(readiness["blockers"])
                    if not ContributorEligibilityService.preparation_source_inventory(cycle_course=member).eligible_sources:
                        reasons.append("No qualifying accepted Faculty teaching assignments.")
                if course.exam_classification == "UNCLASSIFIED_LEGACY":
                    reasons.append("Explicit classification is required for this legacy course before using unified setup.")
                if course.exam_classification == "DEPARTMENTAL" and not ExamBlueprint.objects.filter(cycle_course=course).exists():
                    reasons.append("Configure the Departmental exam blueprint. NO_SECTIONS is supported in Phase 1.")
                status = "Blocked" if reasons else "Ready"
            rows.append({"course": course, "configuration": config, "status": status, "reasons": list(dict.fromkeys(reasons)), "fingerprint": cls.fingerprint(course), "member_ids": list(unit.member_ids)})
        return rows

    @classmethod
    def confirmation(cls, *, cycle, actor, rows):
        return signing.dumps({"actor": actor.id, "tenant": cycle.tenant_id, "cycle": cycle.id, "batch": uuid.uuid4().hex,
            "rows": [{"id": r["course"].id, "fingerprint": r["fingerprint"], "status": r["status"]} for r in rows]}, salt=cls.SALT)

    @classmethod
    @transaction.atomic
    def open_selection(cls, *, cycle, actor, token, request=None):
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
        rows = cls.preview(cycle=cycle, actor=actor, selected_ids=ids)
        receipt = AuditLog.objects.filter(action="DE_EXAM_SETUP_BATCH_OPENED", entity_id=state["batch"], tenant_id=cycle.tenant_id, actor_user=actor).exists()
        if receipt:
            return rows, True
        old = {r["id"]: r for r in state["rows"]}
        if cycle.status != "OPEN" or any(r["status"] not in ("Ready", "Already open") or r["course"].id not in old or r["fingerprint"] != old[r["course"].id]["fingerprint"] for r in rows):
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
                raise ValidationError("Configure the Departmental blueprint before opening. NO_SECTIONS is supported in Phase 1.")
            blueprint = ExamBlueprint.objects.create(cycle_course=unit.primary, mode="NO_SECTIONS", revision=1, created_by=actor, updated_by=actor)
            AuditService.log_event(action="DE_EXAM_STANDARD_STRUCTURE_CREATED", portal="ADMIN", entity_type="ExamBlueprint", entity_id=blueprint.id, actor=actor, tenant=course.cycle.tenant_id, metadata={"cycle_course_id": unit.primary.id, "mode": "NO_SECTIONS"}, request=request)
