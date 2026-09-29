"""Versioned tenant intent and guarded application to future exam cycles."""

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.academics.models import Course, CourseOffering
from apps.core.services.audit import AuditService
from apps.tenants.models import Tenant

from .exam_units import ExamCourseEquivalencyService, _compatibility_errors
from .models import (
    AnswerKeyRelease,
    CourseEquivalencyCyclePlan,
    CourseEquivalencyDefinition,
    CourseEquivalencyDefinitionMember,
    CourseEquivalencyDefinitionRevision,
    CourseExamConfiguration,
    CycleCourse,
    ExamBlueprint,
    ExamBlueprintDisposition,
    ExamBlueprintRestoration,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamGenerationRevision,
    ExamScenario,
    ExamSection,
    ExaminationCycle,
    FacultyContribution,
    QuestionBlueprintPlacement,
    QuestionnairePrintRelease,
    _equivalency_lifecycle_service_scope,
)
from .services import CourseExamConfigurationConflict, DepartmentalExamAuthorizationService


def _reason(value):
    normalized = " ".join((value or "").split())
    if not 10 <= len(normalized) <= 500:
        raise ValidationError({"reason": "Enter a reason of 10 to 500 characters."})
    return normalized


def _member_ids(revision):
    return tuple(revision.memberships.order_by("course_id").values_list("course_id", flat=True))


def applied_plan_is_current(plan):
    group = plan.applied_group
    if (group is None or not group.is_active or group.cycle_id != plan.cycle_id or
            group.primary_cycle_course.course_id != plan.revision.primary_course_id):
        return False
    actual = set(ExamCourseEquivalencyMembership.objects.filter(
        group=group, active_marker=1,
    ).values_list("cycle_course__course_id", flat=True))
    return actual == set(_member_ids(plan.revision))


def _campus_ids(*, tenant_id, course_ids):
    campuses = set(CourseOffering.objects.filter(
        tenant_id=tenant_id, course_id__in=course_ids, is_active=True,
    ).exclude(status=CourseOffering.Status.ARCHIVED).values_list("campus_id", flat=True))
    campuses.update(Course.objects.filter(pk__in=course_ids, tenant_id=tenant_id)
                    .exclude(campus_id__isnull=True).values_list("campus_id", flat=True))
    return campuses


def member_evidence(*, tenant_id, course_ids):
    """Fingerprint the exact Course identity and current offering-campus rows."""
    result = {}
    for course in Course.objects.filter(pk__in=course_ids, tenant_id=tenant_id).order_by("pk"):
        offerings = list(CourseOffering.objects.filter(
            tenant_id=tenant_id, course=course, is_active=True,
        ).exclude(status=CourseOffering.Status.ARCHIVED).order_by("pk")
                         .values_list("id", "campus_id", "campus__name"))
        value = [course.pk, course.code, course.title, course.is_active, course.campus_id, offerings]
        result[str(course.pk)] = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
    return result


def historical_group_evidence(group):
    """Canonical displayed identity plus the complete historical membership graph."""
    memberships = []
    for row in ExamCourseEquivalencyMembership.objects.filter(group=group).select_related(
        "cycle_course__course",
    ).order_by("pk"):
        child = row.cycle_course
        campuses = [list(value) for value in child.offering_snapshots.order_by("pk").values_list(
            "pk", "offering_id", "campus_id", "campus__name",
        )]
        memberships.append({
            "id": row.pk, "cycle_course_id": child.pk, "course_id": child.course_id,
            "code": child.course.code, "title": child.course.title,
            "active": row.active_marker == 1,
            "updated_at": row.updated_at.isoformat(),
            "offering_campuses": campuses,
        })
    primary = group.primary_cycle_course
    return {
        "group_id": group.pk, "cycle_id": group.cycle_id,
        "version": group.updated_at.isoformat(), "active": group.is_active,
        "label": group.name, "primary_cycle_course_id": primary.pk,
        "primary_course_id": primary.course_id,
        "primary_code": primary.course.code, "primary_title": primary.course.title,
        "memberships": memberships,
    }


def can_manage_definition(*, user, tenant_id, course_ids):
    authorization = DepartmentalExamAuthorizationService
    try:
        authorization.require_enabled(tenant_id=tenant_id)
    except PermissionDenied:
        return False
    if not user or not user.is_authenticated or not user.is_active:
        return False
    course_ids = tuple(set(course_ids))
    if not course_ids or Course.objects.filter(pk__in=course_ids, tenant_id=tenant_id).count() != len(course_ids):
        return False
    campuses = _campus_ids(tenant_id=tenant_id, course_ids=course_ids)
    if not campuses:
        campuses = {None}
    return all(authorization._has_scoped_permission(
        user=user, permission=authorization.MANAGE_GENERATION_PERMISSION,
        tenant_id=tenant_id, campus_id=campus_id,
    ) for campus_id in campuses)


def require_manage_definition(*, user, tenant_id, course_ids):
    if not can_manage_definition(user=user, tenant_id=tenant_id, course_ids=course_ids):
        raise PermissionDenied("Complete member-campus equivalency authority is required.")


class PersistentCourseEquivalencyService:
    @classmethod
    @transaction.atomic
    def save_definition(cls, *, tenant_id, actor, label, member_course_ids,
                        primary_course_id, definition_id=None, expected_version=None,
                        adoption_group_id=None, adoption_reason=None,
                        expected_member_evidence=None, adoption_expected_evidence=None):
        Tenant.objects.select_for_update().get(pk=tenant_id)
        try:
            submitted_ids = tuple(int(value) for value in member_course_ids)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"members": "Choose valid course IDs."}) from exc
        ids = tuple(sorted(set(submitted_ids)))
        if len(ids) < 2 or len(ids) != len(submitted_ids):
            raise ValidationError({"members": "Select at least two distinct courses."})
        try:
            primary_course_id = int(primary_course_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"primary": "Choose a primary from the selected courses."}) from exc
        if primary_course_id not in ids:
            raise ValidationError({"primary": "Choose a primary from the selected courses."})
        if adoption_group_id is not None:
            group = ExamCourseEquivalencyGroup.objects.select_for_update().select_related(
                "cycle", "primary_cycle_course__course"
            ).filter(pk=adoption_group_id, cycle__tenant_id=tenant_id,
                     cycle__exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
                     is_active=True).first()
            if group is None:
                raise CourseExamConfigurationConflict("The historical Midterm group changed. Review it again.")
            memberships = list(ExamCourseEquivalencyMembership.objects.select_for_update().filter(
                group=group,
            ).select_related("cycle_course__cycle").prefetch_related(
                "cycle_course__offering_snapshots",
            ).order_by("pk"))
            active_members = [row.cycle_course for row in memberships if row.active_marker == 1]
            DepartmentalExamAuthorizationService.require_automatic_courses_permission(
                user=actor, cycle=group.cycle, courses=[row.cycle_course for row in memberships],
                permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
                require_included=False,
            )
            current_adoption_evidence = historical_group_evidence(group)
            if (adoption_expected_evidence is None or
                    adoption_expected_evidence != current_adoption_evidence):
                raise CourseExamConfigurationConflict("The historical group changed. Review its current members and primary again.")
            historic_ids = {row.course_id for row in active_members}
            if historic_ids != set(ids) or group.primary_cycle_course.course_id != primary_course_id:
                raise CourseExamConfigurationConflict("The historical member or primary mapping changed. Review it again.")
            adoption_reason = _reason(adoption_reason)
        label = " ".join((label or "").split())
        if not label or len(label) > 255:
            raise ValidationError({"label": "Enter an equivalent label of at most 255 characters."})
        courses = list(Course.objects.filter(pk__in=ids, tenant_id=tenant_id, is_active=True).order_by("pk"))
        if len(courses) != len(ids):
            raise ValidationError({"members": "One selected course is unavailable."})
        definition = None
        previous_ids = ()
        if definition_id is not None:
            definition = CourseEquivalencyDefinition.objects.select_for_update().filter(
                pk=definition_id, tenant_id=tenant_id, is_active=True,
            ).first()
            if definition is None:
                raise ValidationError("The saved group is no longer active.")
            previous = definition.revisions.get(version=definition.current_version)
            previous_ids = _member_ids(previous)
            if expected_version != definition.current_version:
                raise CourseExamConfigurationConflict("The saved group changed. Refresh the list and review it again.")
        require_manage_definition(user=actor, tenant_id=tenant_id,
                                  course_ids=set(ids) | set(previous_ids))
        if expected_member_evidence is not None and expected_member_evidence != member_evidence(
            tenant_id=tenant_id, course_ids=set(ids) | set(previous_ids),
        ):
            raise CourseExamConfigurationConflict(
                "A course code, title, or offering-campus snapshot changed. Refresh and review the group again."
            )
        collision = CourseEquivalencyDefinitionMember.objects.filter(
            course_id__in=ids,
            revision__definition__tenant_id=tenant_id,
            revision__definition__is_active=True,
            revision__version=F("revision__definition__current_version"),
        )
        if definition is not None:
            collision = collision.exclude(revision__definition=definition)
        if collision.exists():
            raise ValidationError({"members": "A course already belongs to another active saved group."})
        if definition is None:
            with _equivalency_lifecycle_service_scope():
                definition = CourseEquivalencyDefinition.objects.create(
                    tenant_id=tenant_id, current_version=1, created_by=actor, updated_by=actor,
                )
            version = 1
        else:
            version = definition.current_version + 1
            definition.current_version = version
            definition.updated_by = actor
            with _equivalency_lifecycle_service_scope():
                definition.save(update_fields=["current_version", "updated_by", "updated_at"])
        with _equivalency_lifecycle_service_scope():
            revision = CourseEquivalencyDefinitionRevision.objects.create(
                definition=definition, version=version, label=label,
                primary_course_id=primary_course_id, created_by=actor,
            )
            CourseEquivalencyDefinitionMember.objects.bulk_create([
                CourseEquivalencyDefinitionMember(
                    revision=revision, course=course,
                    code_snapshot=course.code, title_snapshot=course.title,
                ) for course in courses
            ])
        AuditService.log_event(
            action="DE_EXAM_EQUIVALENCY_DEFINITION_SAVED", portal="ADMIN",
            entity_type="CourseEquivalencyDefinition", entity_id=definition.id,
            actor=actor, tenant=tenant_id,
            metadata={"version": version, "primary_course_id": primary_course_id,
                      "member_course_ids": list(ids), "adopted_cycle_group_id": adoption_group_id,
                      "adoption_reason": adoption_reason},
        )
        return definition

    @classmethod
    @transaction.atomic
    def retire_definition(cls, *, tenant_id, definition_id, actor, reason, expected_version):
        Tenant.objects.select_for_update().get(pk=tenant_id)
        definition = CourseEquivalencyDefinition.objects.select_for_update().filter(
            pk=definition_id, tenant_id=tenant_id, is_active=True,
        ).first()
        if definition is None:
            raise ValidationError("The saved group is no longer active.")
        if expected_version != definition.current_version:
            raise CourseExamConfigurationConflict("The saved group changed. Refresh the list and review it again.")
        revision = definition.revisions.get(version=definition.current_version)
        require_manage_definition(user=actor, tenant_id=tenant_id, course_ids=_member_ids(revision))
        definition.is_active = False
        definition.retired_by = actor
        definition.retired_at = timezone.now()
        definition.retirement_reason = _reason(reason)
        with _equivalency_lifecycle_service_scope():
            definition.save(update_fields=["is_active", "retired_by", "retired_at", "retirement_reason", "updated_at"])
        AuditService.log_event(
            action="DE_EXAM_EQUIVALENCY_DEFINITION_RETIRED", portal="ADMIN",
            entity_type="CourseEquivalencyDefinition", entity_id=definition.id,
            actor=actor, tenant=tenant_id,
            metadata={"version": definition.current_version, "reason": definition.retirement_reason},
        )
        return definition

    @staticmethod
    def snapshot_cycle(cycle):
        if cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
            return
        present = set(CycleCourse.objects.filter(cycle=cycle).values_list("course_id", flat=True))
        for definition in CourseEquivalencyDefinition.objects.filter(
            tenant=cycle.tenant, is_active=True,
        ).order_by("pk"):
            revision = definition.revisions.get(version=definition.current_version)
            ids = _member_ids(revision)
            participating = len(present.intersection(ids))
            status = (CourseEquivalencyCyclePlan.Status.NOT_APPLICABLE if participating < 2
                      else CourseEquivalencyCyclePlan.Status.PENDING)
            with _equivalency_lifecycle_service_scope():
                CourseEquivalencyCyclePlan.objects.create(
                    cycle=cycle, definition=definition, revision=revision, status=status,
                    reason=("Fewer than two saved member courses have offerings in this cycle."
                            if participating < 2 else "Awaiting compatible member settings and application."),
                )

    @staticmethod
    def relevant_plan(cycle_course):
        return CourseEquivalencyCyclePlan.objects.filter(
            cycle=cycle_course.cycle,
            revision__memberships__course_id=cycle_course.course_id,
        ).select_related("revision", "definition", "applied_group").first()

    @classmethod
    @transaction.atomic
    def ensure_for_course(cls, *, cycle_course, actor):
        cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle_course.cycle_id)
        plan = cls.relevant_plan(cycle_course)
        if plan and plan.status == plan.Status.APPLIED:
            if applied_plan_is_current(plan):
                return plan
            plan = CourseEquivalencyCyclePlan.objects.select_for_update().get(pk=plan.pk)
            plan.status = plan.Status.BLOCKED
            plan.reason = "The applied cycle group changed. Review its historical mapping and record a reasoned exception or correction."
            with _equivalency_lifecycle_service_scope():
                plan.save(update_fields=["status", "reason", "updated_at"])
            return plan
        if plan is None or plan.status in (
            CourseEquivalencyCyclePlan.Status.EXCEPTED,
        ):
            return plan
        plan = CourseEquivalencyCyclePlan.objects.select_for_update().get(pk=plan.pk)
        ids = _member_ids(plan.revision)
        members = tuple(CycleCourse.objects.select_for_update().filter(
            cycle=cycle, course_id__in=ids,
        ).select_related("cycle", "course").order_by("pk"))
        if len(members) < 2:
            plan.status = plan.Status.NOT_APPLICABLE
            plan.reason = "Fewer than two saved member courses have offerings in this cycle."
        elif Course.objects.filter(pk__in=ids, tenant_id=cycle.tenant_id, is_active=True).count() != len(ids):
            plan.status = plan.Status.BLOCKED
            plan.reason = "A saved member Course is inactive or unavailable. Correct the definition or record a cycle exception."
        elif cycle.status != ExaminationCycle.Status.OPEN:
            plan.status = plan.Status.BLOCKED
            plan.reason = "Open the cycle before applying its saved equivalency definition."
        elif len(members) != len(ids):
            plan.status = plan.Status.BLOCKED
            plan.reason = "Some saved member courses have no offerings in this cycle. Correct the cycle or record a reasoned exception."
        elif plan.revision.primary_course_id not in {row.course_id for row in members}:
            plan.status = plan.Status.BLOCKED
            plan.reason = "The saved primary course is absent from this cycle."
        else:
            configs = {row.cycle_course_id: row for row in CourseExamConfiguration.objects.filter(cycle_course__in=members)}
            errors = _compatibility_errors(members=members, configurations=configs)
            if errors:
                plan.status = plan.Status.BLOCKED
                plan.reason = " ".join(errors)
            else:
                DepartmentalExamAuthorizationService.require_automatic_courses_permission(
                    user=actor, cycle=cycle, courses=members,
                    permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
                )
                primary = next(row for row in members if row.course_id == plan.revision.primary_course_id)
                try:
                    group = ExamCourseEquivalencyService.create_group(
                        cycle_id=cycle.id, name=plan.revision.label,
                        primary_cycle_course_id=primary.id,
                        member_ids=[row.id for row in members], actor=actor,
                    )
                except ValidationError as exc:
                    plan.status = plan.Status.BLOCKED
                    plan.reason = " ".join(exc.messages)
                else:
                    plan.status = plan.Status.APPLIED
                    plan.reason = ""
                    plan.applied_group = group
                    AuditService.log_event(
                        action="DE_EXAM_EQUIVALENCY_PLAN_APPLIED", portal="SYSTEM",
                        entity_type="CourseEquivalencyCyclePlan", entity_id=plan.id,
                        actor=actor, tenant=cycle.tenant_id,
                        metadata={"cycle_id": cycle.id, "definition_id": plan.definition_id,
                                  "version": plan.revision.version, "group_id": group.id},
                    )
        with _equivalency_lifecycle_service_scope():
            plan.save(update_fields=["status", "reason", "applied_group", "updated_at"])
        return plan

    @classmethod
    def require_applied_or_excepted(cls, *, cycle_course, actor):
        plan = cls.ensure_for_course(cycle_course=cycle_course, actor=actor)
        if plan and plan.status in (plan.Status.PENDING, plan.Status.BLOCKED):
            raise ValidationError(
                "Saved equivalent course group needs correction: " + plan.reason
            )
        return plan

    @classmethod
    @transaction.atomic
    def record_exception(cls, *, cycle_id, plan_id, actor, reason):
        cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle_id)
        plan = CourseEquivalencyCyclePlan.objects.select_for_update().get(pk=plan_id, cycle=cycle)
        ids = _member_ids(plan.revision)
        require_manage_definition(user=actor, tenant_id=cycle.tenant_id, course_ids=ids)
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError("A non-Open cycle plan cannot be excepted here.")
        if plan.applied_group_id and plan.applied_group.is_active:
            raise ValidationError("Retire the active cycle group through its protected workflow before recording an exception.")
        members = tuple(CycleCourse.objects.select_for_update().filter(
            cycle=cycle, course_id__in=ids,
        ).select_related("cycle").prefetch_related("offering_snapshots").order_by("pk"))
        DepartmentalExamAuthorizationService.require_automatic_courses_permission(
            user=actor, cycle=cycle, courses=members,
            permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
            require_included=False,
        )
        ExamCourseEquivalencyService._require_mutable(cycle=cycle, members=members)
        normalized_reason = _reason(reason)
        BlueprintDispositionService.restore_for_separation(
            cycle=cycle, members=members,
            primary_cycle_course_id=next(
                (member.pk for member in members if member.course_id == plan.revision.primary_course_id),
                None,
            ),
            actor=actor, reason=normalized_reason,
            retired_group=plan.applied_group if plan.applied_group_id else None,
        )
        plan.status = plan.Status.EXCEPTED
        plan.exception_by = actor
        plan.exception_at = timezone.now()
        plan.exception_reason = normalized_reason
        plan.reason = "Cycle exception: " + plan.exception_reason
        with _equivalency_lifecycle_service_scope():
            plan.save(update_fields=["status", "reason", "exception_by", "exception_at", "exception_reason", "updated_at"])
        AuditService.log_event(
            action="DE_EXAM_EQUIVALENCY_CYCLE_EXCEPTION", portal="ADMIN",
            entity_type="CourseEquivalencyCyclePlan", entity_id=plan.id,
            actor=actor, tenant=cycle.tenant_id,
            metadata={"cycle_id": cycle.id, "definition_id": plan.definition_id,
                      "version": plan.revision.version, "reason": plan.exception_reason},
        )
        return plan


class BlueprintDispositionService:
    @staticmethod
    def _section_evidence(blueprint):
        return list(ExamSection.objects.filter(blueprint=blueprint).order_by("display_order", "id")
                    .values_list("display_order", "title", "instructions", "item_quota"))

    @classmethod
    def structure_digest(cls, blueprint):
        value = [blueprint.pk, blueprint.cycle_course_id, blueprint.cycle_course.course.code,
                 blueprint.cycle_course.course.title, blueprint.revision, blueprint.mode,
                 cls._section_evidence(blueprint)]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                              .encode("utf-8")).hexdigest()

    @classmethod
    def restore_for_separation(cls, *, cycle, members, primary_cycle_course_id, actor, reason,
                               retired_group=None):
        """Restore a retained member only while both structures still match recovery evidence.

        The caller holds the cycle lock and an atomic transaction. Any failure must
        abort retirement or exception before its group/plan state is changed.
        """
        member_ids = tuple(sorted(member.pk for member in members))
        retained = list(ExamBlueprintDisposition.objects.select_for_update().filter(
            blueprint__cycle_course_id__in=member_ids, restoration__isnull=True,
        ).select_related("blueprint", "primary_blueprint").order_by("pk"))
        if not retained:
            return ()
        if len(member_ids) != 2 or len(retained) != 1:
            raise ValidationError("Retained blueprint recovery requires exactly the original two members.")
        disposition = retained[0]
        if (disposition.primary_blueprint.cycle_course_id != primary_cycle_course_id or
                disposition.blueprint.cycle_course_id not in member_ids or
                disposition.blueprint.cycle_course_id == primary_cycle_course_id):
            raise ValidationError("The retained blueprint no longer matches this group's primary and members.")
        blueprints = {row.pk: row for row in ExamBlueprint.objects.select_for_update().filter(
            pk__in=(disposition.primary_blueprint_id, disposition.blueprint_id),
        ).select_related("cycle_course__course").order_by("cycle_course_id", "pk")}
        primary = blueprints[disposition.primary_blueprint_id]
        secondary = blueprints[disposition.blueprint_id]
        evidence = disposition.evidence
        sections = json.loads(json.dumps(cls._section_evidence(primary)))
        if (primary.structure_frozen_at or secondary.structure_frozen_at or
                primary.revision != evidence.get("primary_revision") or
                secondary.revision != evidence.get("secondary_revision") or
                primary.mode != evidence.get("mode") or secondary.mode != primary.mode or
                sections != evidence.get("sections") or
                json.loads(json.dumps(cls._section_evidence(secondary))) != sections):
            raise ValidationError("The retained structures changed or froze. Separation requires a fresh protected review.")
        if (QuestionBlueprintPlacement.objects.filter(blueprint__in=(primary, secondary)).exists() or
                ExamScenario.objects.filter(blueprint__in=(primary, secondary)).exists() or
                FacultyContribution.objects.filter(cycle_course_id__in=member_ids).exists() or
                ExamGenerationRevision.objects.filter(cycle_course_id__in=member_ids).exists() or
                QuestionnairePrintRelease.objects.filter(cycle_course_id__in=member_ids).exists() or
                AnswerKeyRelease.objects.filter(cycle_course_id__in=member_ids).exists()):
            raise ValidationError("Placement, contribution, generation, or release history blocks blueprint restoration.")
        with _equivalency_lifecycle_service_scope():
            restoration = ExamBlueprintRestoration.objects.create(
                disposition=disposition, retired_group=retired_group, actor=actor,
                reason=_reason(reason),
                evidence={"cycle_id": cycle.pk, "member_cycle_course_ids": list(member_ids),
                          "primary_blueprint_id": primary.pk, "secondary_blueprint_id": secondary.pk,
                          "primary_revision": primary.revision, "secondary_revision": secondary.revision,
                          "sections": sections},
            )
        AuditService.log_event(
            action="DE_EXAM_BLUEPRINT_RESTORED_AFTER_SEPARATION", portal="ADMIN",
            entity_type="ExamBlueprintRestoration", entity_id=restoration.pk,
            actor=actor, tenant=cycle.tenant_id,
            metadata={"cycle_id": cycle.pk, "disposition_id": disposition.pk,
                      "retired_group_id": getattr(retired_group, "pk", None),
                      "primary_blueprint_id": primary.pk, "secondary_blueprint_id": secondary.pk},
        )
        return (restoration,)

    @classmethod
    @transaction.atomic
    def retain_secondary(cls, *, cycle_id, primary_cycle_course_id, secondary_cycle_course_id,
                         actor, reason, expected_primary_revision, expected_secondary_revision,
                         plan_id=None, expected_primary_digest=None, expected_secondary_digest=None):
        cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle_id)
        if plan_id is not None:
            plan = CourseEquivalencyCyclePlan.objects.select_for_update().select_related("revision").get(
                pk=plan_id, cycle=cycle,
            )
            if plan.status not in (plan.Status.PENDING, plan.Status.BLOCKED):
                raise ValidationError("The cycle plan no longer permits blueprint recovery.")
            planned_ids = set(_member_ids(plan.revision))
            selected_course_ids = set(CycleCourse.objects.filter(
                pk__in=(primary_cycle_course_id, secondary_cycle_course_id), cycle=cycle,
            ).values_list("course_id", flat=True))
            selected_primary_course_id = CycleCourse.objects.filter(
                pk=primary_cycle_course_id, cycle=cycle,
            ).values_list("course_id", flat=True).first()
            if (planned_ids != selected_course_ids or
                    selected_primary_course_id != plan.revision.primary_course_id):
                raise ValidationError("The selected blueprints no longer match the saved cycle plan.")
        members = tuple(CycleCourse.objects.select_for_update().filter(
            cycle=cycle, pk__in=(primary_cycle_course_id, secondary_cycle_course_id),
        ).select_related("cycle", "course").order_by("pk"))
        if len(members) != 2 or cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
            raise ValidationError("Select two distinct Automatic cycle courses.")
        if ExamCourseEquivalencyMembership.objects.filter(
            cycle_course__in=members, active_marker=1, group__is_active=True,
        ).exists():
            raise ValidationError("Resolve active group membership before blueprint recovery.")
        DepartmentalExamAuthorizationService.require_automatic_courses_permission(
            user=actor, cycle=cycle, courses=members,
            permissions=(DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,),
        )
        ExamCourseEquivalencyService._require_mutable(cycle=cycle, members=members)
        blueprints = {row.cycle_course_id: row for row in ExamBlueprint.objects.select_for_update().filter(
            cycle_course__in=members,
        ).order_by("pk")}
        primary = blueprints.get(primary_cycle_course_id)
        secondary = blueprints.get(secondary_cycle_course_id)
        if not primary or not secondary or primary.pk == secondary.pk:
            raise ValidationError("Both courses require separate blueprints for this recovery.")
        if (primary.revision != expected_primary_revision or
                secondary.revision != expected_secondary_revision):
            raise CourseExamConfigurationConflict("A blueprint changed. Review current structures again.")
        if ((expected_primary_digest is not None and
                expected_primary_digest != cls.structure_digest(primary)) or
                (expected_secondary_digest is not None and
                 expected_secondary_digest != cls.structure_digest(secondary))):
            raise CourseExamConfigurationConflict("A displayed course or blueprint structure changed. Review it again.")
        if (primary.structure_frozen_at or secondary.structure_frozen_at or
                ExamBlueprintDisposition.objects.filter(blueprint__in=(primary, secondary)).exists()):
            raise ValidationError("Frozen or already retained structures cannot be reconciled.")
        configurations = {row.cycle_course_id: row for row in CourseExamConfiguration.objects.select_for_update().filter(cycle_course__in=members)}
        errors = _compatibility_errors(members=members, configurations=configurations)
        if errors:
            raise ValidationError(errors)
        if primary.mode != secondary.mode or cls._section_evidence(primary) != cls._section_evidence(secondary):
            raise ValidationError("Blueprint modes and ordered section content/quotas must match exactly.")
        if (QuestionBlueprintPlacement.objects.filter(blueprint__in=(primary, secondary)).exists()
                or ExamScenario.objects.filter(blueprint__in=(primary, secondary)).exists()):
            raise ValidationError("Blueprints with placements or scenarios cannot use this recovery.")
        member_ids = [row.pk for row in members]
        if (FacultyContribution.objects.filter(cycle_course_id__in=member_ids).exists()
                or ExamGenerationRevision.objects.filter(cycle_course_id__in=member_ids).exists()
                or QuestionnairePrintRelease.objects.filter(cycle_course_id__in=member_ids).exists()
                or AnswerKeyRelease.objects.filter(cycle_course_id__in=member_ids).exists()):
            raise ValidationError("Contribution, generation, or release history blocks blueprint recovery.")
        with _equivalency_lifecycle_service_scope():
            disposition = ExamBlueprintDisposition.objects.create(
                blueprint=secondary, primary_blueprint=primary, actor=actor, reason=_reason(reason),
                evidence={"primary_cycle_course_id": primary_cycle_course_id,
                          "secondary_cycle_course_id": secondary_cycle_course_id,
                          "primary_revision": primary.revision, "secondary_revision": secondary.revision,
                          "mode": primary.mode, "sections": cls._section_evidence(primary)},
            )
        AuditService.log_event(
            action="DE_EXAM_BLUEPRINT_RETAINED_HISTORICAL", portal="ADMIN",
            entity_type="ExamBlueprintDisposition", entity_id=disposition.id,
            actor=actor, tenant=cycle.tenant_id,
            metadata={"cycle_id": cycle.id, "primary_blueprint_id": primary.id,
                      "secondary_blueprint_id": secondary.id, "reason": disposition.reason},
        )
        return disposition
