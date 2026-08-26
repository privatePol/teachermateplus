import hashlib

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.accounts.models import User
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Department

from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    ExamGenerationRevision,
    FacultyContribution,
    Question,
    normalize_contribution_deadline_to_minute,
)


class CourseExamConfigurationConflict(ValidationError):
    """Raised for stale cycle/configuration confirmations."""


class CycleCourseCampusPresentationService:
    """Prepare distinct, deterministic campus display data from prefetched snapshots."""

    @staticmethod
    def _format_list(labels):
        if len(labels) < 2:
            return "".join(labels)
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return f"{', '.join(labels[:-1])}, and {labels[-1]}"

    @classmethod
    def from_prefetched_snapshots(cls, cycle_course):
        snapshots = getattr(cycle_course, "_prefetched_objects_cache", {}).get(
            "offering_snapshots"
        )
        if snapshots is None:
            raise ValueError("Offering snapshots must be prefetched for campus display.")

        campuses_by_id = {}
        for snapshot in snapshots:
            if snapshot.campus_id is None:
                raise ValueError("Campus display requires a stable campus identity.")
            campuses_by_id.setdefault(snapshot.campus_id, snapshot.campus)

        campuses = tuple(
            sorted(
                campuses_by_id.values(),
                key=lambda campus: (
                    (campus.name or "").casefold(),
                    campus.name or "",
                    campus.pk,
                ),
            )
        )
        labels = tuple(campus.name for campus in campuses)
        return {
            "campuses": campuses,
            "labels": labels,
            "list_text": cls._format_list(labels),
            "offering_count": len(snapshots),
        }


class CourseExamDefaultTrackingPolicy:
    """Canonical eligibility policy for live cycle-default tracking."""

    @staticmethod
    def parent_exclusion_reason(cycle_course):
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            return "EXEMPT"
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return None
        if not cycle_course.responsible_department_id:
            return "NULL_RESPONSIBILITY"
        if not cycle_course.responsible_department.is_active:
            return "INACTIVE_RESPONSIBILITY"
        return None

    @classmethod
    def exclusion_reason(
        cls, *, cycle_course, configuration, has_downstream_activity
    ):
        reason = cls.parent_exclusion_reason(cycle_course)
        if reason:
            return reason
        if configuration is not None:
            if configuration.opened_at is not None:
                return "EVER_OPENED"
            if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
                return "OPEN"
            if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED:
                return "CLOSED"
        if has_downstream_activity:
            return "DOWNSTREAM_ACTIVITY"
        return None

    @staticmethod
    def has_downstream_activity(cycle_course):
        cached = getattr(cycle_course, "_has_downstream_activity_cache", None)
        if cached is not None:
            return cached
        annotated_contributions = getattr(
            cycle_course, "has_contribution_activity", None
        )
        annotated_questions = getattr(cycle_course, "has_question_activity", None)
        if annotated_contributions is not None and annotated_questions is not None:
            result = bool(annotated_contributions or annotated_questions)
        else:
            result = (
                FacultyContribution.objects.filter(
                    cycle_course=cycle_course
                ).exists()
                or Question.objects.filter(
                    contribution__cycle_course=cycle_course
                ).exists()
            )
        cycle_course._has_downstream_activity_cache = result
        return result

    @classmethod
    def is_live_default_tracking(
        cls, *, cycle_course, configuration, has_downstream_activity
    ):
        return cls.exclusion_reason(
            cycle_course=cycle_course,
            configuration=configuration,
            has_downstream_activity=has_downstream_activity,
        ) is None


class CourseExamConfigurationReadinessService:
    """One authoritative Stage 4 readiness evaluator; it performs no writes."""

    @classmethod
    def evaluate_readiness(cls, *, cycle_course, configuration=None, user=None, for_mutation=False):
        cycle = cycle_course.cycle
        configuration = configuration if configuration is not None else getattr(cycle_course, "configuration", None)
        blockers = []
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            blockers.append("Exempt")
        if (
            cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            if not cycle_course.responsible_department_id:
                blockers.append("Needs Exam Department")
            elif not cycle_course.responsible_department.is_active:
                blockers.append("Exam Department Inactive")
        if cycle.status != ExaminationCycle.Status.OPEN:
            blockers.append("Cycle Not Open")
        if not configuration:
            blockers.append("Needs Configuration")
        else:
            has_downstream_activity = (
                CourseExamDefaultTrackingPolicy.has_downstream_activity(cycle_course)
            )
            tracks_current_defaults = (
                CourseExamDefaultTrackingPolicy.is_live_default_tracking(
                    cycle_course=cycle_course,
                    configuration=configuration,
                    has_downstream_activity=has_downstream_activity,
                )
            )
            if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
                blockers.append("Open for Faculty Contribution")
            if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED:
                blockers.append("Closed")
            if configuration.final_item_count is None or not 50 <= configuration.final_item_count <= 75:
                blockers.append("Needs Configuration")
            if configuration.questions_required_per_faculty is None or not 50 <= configuration.questions_required_per_faculty <= 75:
                blockers.append("Needs Configuration")
            if configuration.final_item_count_source not in ("DEFAULT", "OVERRIDE") or configuration.questions_required_per_faculty_source not in ("DEFAULT", "OVERRIDE"):
                blockers.append("Needs Configuration")
            if (
                configuration.contribution_deadline is None
                and configuration.contribution_deadline_source is not None
            ) or (
                configuration.contribution_deadline is not None
                and configuration.contribution_deadline_source not in ("DEFAULT", "OVERRIDE")
            ):
                blockers.append("Needs Configuration")
            # Only rows eligible for propagation track the current defaults.
            # Protected rows retain their historical materialized values.
            if tracks_current_defaults:
                if configuration.final_item_count_source == "DEFAULT" and configuration.final_item_count != cycle.default_final_item_count:
                    blockers.append("Needs Configuration")
                if configuration.questions_required_per_faculty_source == "DEFAULT" and configuration.questions_required_per_faculty != cycle.default_questions_required_per_faculty:
                    blockers.append("Needs Configuration")
                if configuration.contribution_deadline_source == "DEFAULT":
                    try:
                        deadline_matches_default = (
                            normalize_contribution_deadline_to_minute(
                                configuration.contribution_deadline
                            )
                            == normalize_contribution_deadline_to_minute(
                                cycle.default_contribution_deadline
                            )
                        )
                    except ValidationError:
                        deadline_matches_default = False
                    if not deadline_matches_default:
                        blockers.append("Needs Configuration")
                if (
                    configuration.coverage_source == "DEFAULT"
                    and configuration.coverage != cycle.default_coverage
                ):
                    blockers.append("Needs Configuration")
                if configuration.cycle_defaults_revision_snapshot != cycle.defaults_revision:
                    blockers.append("Needs Configuration")
            if not (configuration.coverage or "").strip():
                blockers.append("Needs Configuration")
            if not configuration.contribution_deadline:
                blockers.append("Needs Configuration")
            elif configuration.contribution_deadline <= timezone.now():
                blockers.append("Contribution Deadline Passed")
        if for_mutation and user:
            try:
                DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=cycle_course)
            except PermissionDenied:
                blockers.append("Not Authorized")
        blockers = list(dict.fromkeys(blockers))
        ready = not blockers
        return {
            "ready": ready,
            "blockers": blockers,
            "label": "Ready to Open" if ready else blockers[0],
            "can_open": ready and configuration and configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.OPEN,
        }


class ExaminationCycleConfigurationService:
    """Cycle configuration and explicit status transitions."""

    PROPAGATION_BATCH_SIZE = 200

    @staticmethod
    def transition_token(cycle):
        return cycle.updated_at.isoformat()

    @classmethod
    def _lock_cycle(cls, *, cycle_id, tenant_id):
        return ExaminationCycle.objects.select_for_update().get(id=cycle_id, tenant_id=tenant_id)

    @staticmethod
    def _configuration_audit_payload(cycle):
        """Keep cycle audit evidence bounded while preserving exact stored guidance."""
        instructions = cycle.contributor_instructions or ""
        coverage = cycle.default_coverage or ""
        return {
            "processing_mode": cycle.processing_mode,
            "automatic_campus_contribution_policy": (
                cycle.automatic_campus_contribution_policy
            ),
            "automatic_contributor_completion_policy": (
                cycle.automatic_contributor_completion_policy
            ),
            "default_questions_required_per_faculty": cycle.default_questions_required_per_faculty,
            "default_final_item_count": cycle.default_final_item_count,
            "default_contribution_deadline": cycle.default_contribution_deadline,
            "defaults_revision": cycle.defaults_revision,
            "default_coverage_sha256": hashlib.sha256(
                coverage.encode("utf-8")
            ).hexdigest(),
            "default_coverage_length": len(coverage),
            "contributor_instructions_sha256": hashlib.sha256(
                instructions.encode("utf-8")
            ).hexdigest(),
            "contributor_instructions_length": len(instructions),
        }

    @staticmethod
    def _text_audit_evidence(*, value, label):
        """Record confidential free text without putting it in broad metadata."""
        value = value or ""
        return {
            f"{label}_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            f"{label}_length": len(value),
        }

    @staticmethod
    def lifecycle_flags(cycle):
        """Derive cycle mutation availability from persisted lifecycle state."""
        return {
            "can_edit_cycle_configuration": cycle.status in (ExaminationCycle.Status.DRAFT, ExaminationCycle.Status.OPEN),
            "can_open_cycle": (
                cycle.status == ExaminationCycle.Status.DRAFT
            ),
            "can_close_cycle": cycle.status == ExaminationCycle.Status.OPEN,
        }

    @classmethod
    def _propagate_defaults_to_drafts(cls, *, cycle, changed_defaults):
        """Propagate in stable parent-ID batches while retaining parent-first locks.

        This intentionally does not use per-row permission checks: the manager
        has already been authorized for the tenant-wide cycle mutation and the
        eligible responsibility filter is data-state based.
        """
        last_parent_id = 0
        created = updated = 0
        affected_configuration_ids = []
        excluded_by_reason = {}

        def exclude(reason):
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1

        while True:
            # Cycle -> CycleCourse is the first pair of locks. We lock a
            # bounded stable ID range before deriving eligible parent IDs so
            # exclusion counters and later child locks see one coherent batch.
            parents = list(
                CycleCourse.objects.select_for_update()
                .select_related("cycle", "responsible_department")
                .filter(cycle=cycle, id__gt=last_parent_id)
                .order_by("id")[: cls.PROPAGATION_BATCH_SIZE]
            )
            if not parents:
                break
            last_parent_id = parents[-1].id
            parents_by_id = {parent.id: parent for parent in parents}
            eligible_parent_ids = []
            for parent in parents:
                reason = CourseExamDefaultTrackingPolicy.parent_exclusion_reason(parent)
                if reason:
                    exclude(reason)
                    continue
                eligible_parent_ids.append(parent.id)
            if not eligible_parent_ids:
                continue

            # CycleCourse -> CourseExamConfiguration -> downstream checks.
            # Each activity relation is queried once per locked batch; no
            # permission or activity query is issued for an individual course.
            all_existing_configurations_by_parent = {
                configuration.cycle_course_id: configuration
                for configuration in CourseExamConfiguration.objects.select_for_update().filter(
                    cycle_course_id__in=eligible_parent_ids,
                )
            }
            contribution_active_parent_ids = set(
                FacultyContribution.objects.filter(
                    cycle_course_id__in=eligible_parent_ids
                )
                .order_by()
                .values_list("cycle_course_id", flat=True)
                .distinct()
            )
            question_active_parent_ids = set(
                Question.objects.filter(
                    contribution__cycle_course_id__in=eligible_parent_ids
                )
                .order_by()
                .values_list("contribution__cycle_course_id", flat=True)
                .distinct()
            )
            creates = []
            changes = []
            for parent_id in eligible_parent_ids:
                has_activity = (
                    parent_id in contribution_active_parent_ids
                    or parent_id in question_active_parent_ids
                )
                existing_configuration = all_existing_configurations_by_parent.get(parent_id)
                parent = parents_by_id[parent_id]
                reason = CourseExamDefaultTrackingPolicy.exclusion_reason(
                    cycle_course=parent,
                    configuration=existing_configuration,
                    has_downstream_activity=has_activity,
                )
                if reason:
                    exclude(reason)
                    continue
                if existing_configuration is not None:
                    configuration = existing_configuration
                else:
                    values = {
                        "cycle_course_id": parent_id,
                        "revision": 1,
                        "cycle_defaults_revision_snapshot": cycle.defaults_revision,
                    }
                    # A new child receives every current valid default, not
                    # just the default that triggered this propagation pass.
                    for field, default in (
                        (
                            "questions_required_per_faculty",
                            cycle.default_questions_required_per_faculty,
                        ),
                        ("final_item_count", cycle.default_final_item_count),
                    ):
                        if default is not None and 50 <= default <= 75:
                            values[field] = default
                            values[f"{field}_source"] = "DEFAULT"
                    if cycle.default_contribution_deadline is not None:
                        values["contribution_deadline"] = cycle.default_contribution_deadline
                        values["contribution_deadline_source"] = "DEFAULT"
                    if cycle.default_coverage:
                        values["coverage"] = cycle.default_coverage
                        values["coverage_source"] = "DEFAULT"
                    creates.append(CourseExamConfiguration(**values))
                    continue
                prior = {
                    "questions_required_per_faculty": configuration.questions_required_per_faculty,
                    "questions_required_per_faculty_source": configuration.questions_required_per_faculty_source,
                    "final_item_count": configuration.final_item_count,
                    "final_item_count_source": configuration.final_item_count_source,
                    "contribution_deadline": configuration.contribution_deadline,
                    "contribution_deadline_source": configuration.contribution_deadline_source,
                    "coverage": configuration.coverage,
                    "coverage_source": configuration.coverage_source,
                    "cycle_defaults_revision_snapshot": configuration.cycle_defaults_revision_snapshot,
                }
                if "questions_required_per_faculty" in changed_defaults and configuration.questions_required_per_faculty_source in (None, "DEFAULT"):
                    configuration.questions_required_per_faculty = cycle.default_questions_required_per_faculty
                    configuration.questions_required_per_faculty_source = "DEFAULT" if cycle.default_questions_required_per_faculty is not None else None
                if "final_item_count" in changed_defaults and configuration.final_item_count_source in (None, "DEFAULT"):
                    configuration.final_item_count = cycle.default_final_item_count
                    configuration.final_item_count_source = "DEFAULT" if cycle.default_final_item_count is not None else None
                if "contribution_deadline" in changed_defaults and configuration.contribution_deadline_source in (None, "DEFAULT"):
                    configuration.contribution_deadline = cycle.default_contribution_deadline
                    configuration.contribution_deadline_source = "DEFAULT" if cycle.default_contribution_deadline is not None else None
                if "coverage" in changed_defaults and configuration.coverage_source in (None, "DEFAULT"):
                    configuration.coverage = cycle.default_coverage
                    configuration.coverage_source = "DEFAULT" if cycle.default_coverage else None
                configuration.cycle_defaults_revision_snapshot = cycle.defaults_revision
                if prior != {
                    "questions_required_per_faculty": configuration.questions_required_per_faculty,
                    "questions_required_per_faculty_source": configuration.questions_required_per_faculty_source,
                    "final_item_count": configuration.final_item_count,
                    "final_item_count_source": configuration.final_item_count_source,
                    "contribution_deadline": configuration.contribution_deadline,
                    "contribution_deadline_source": configuration.contribution_deadline_source,
                    "coverage": configuration.coverage,
                    "coverage_source": configuration.coverage_source,
                    "cycle_defaults_revision_snapshot": configuration.cycle_defaults_revision_snapshot,
                }:
                    if len(affected_configuration_ids) < cls.PROPAGATION_BATCH_SIZE:
                        affected_configuration_ids.append(configuration.id)
                    configuration.revision += 1
                    configuration.updated_at = timezone.now()
                    changes.append(configuration)
            if creates:
                CourseExamConfiguration.objects.bulk_create(creates, batch_size=cls.PROPAGATION_BATCH_SIZE)
                created += len(creates)
            if changes:
                CourseExamConfiguration.objects.bulk_update(
                    changes,
                    ["questions_required_per_faculty", "questions_required_per_faculty_source", "final_item_count", "final_item_count_source", "contribution_deadline", "contribution_deadline_source", "coverage", "coverage_source", "cycle_defaults_revision_snapshot", "revision", "updated_at"],
                    batch_size=cls.PROPAGATION_BATCH_SIZE,
                )
                updated += len(changes)
        return {
            "created": created,
            "updated": updated,
            "affected_configuration_ids": affected_configuration_ids,
            "excluded_by_reason": excluded_by_reason,
        }

    @classmethod
    @transaction.atomic
    def save_cycle_configuration(cls, *, cycle_id, tenant_id, user, expected_updated_at, default_questions_required_per_faculty, default_final_item_count, contributor_instructions, default_contribution_deadline=None, default_coverage=None, processing_mode=None, automatic_campus_contribution_policy=None, automatic_contributor_completion_policy=None, reason="", request=None):
        cycle = cls._lock_cycle(cycle_id=cycle_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_permission(user=user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
        if cycle.status == ExaminationCycle.Status.CLOSED:
            raise ValidationError("Closed cycles cannot change defaults.")
        if expected_updated_at != cls.transition_token(cycle):
            raise CourseExamConfigurationConflict("The examination cycle changed after this page was loaded.")
        processing_mode = processing_mode or cycle.processing_mode
        if processing_mode not in ExaminationCycle.ProcessingMode.values:
            raise ValidationError("Unsupported examination processing mode.")
        if cycle.status != ExaminationCycle.Status.DRAFT and processing_mode != cycle.processing_mode:
            raise ValidationError("Processing mode can change only while the cycle is Draft.")
        automatic_campus_contribution_policy = (
            automatic_campus_contribution_policy
            or cycle.automatic_campus_contribution_policy
        )
        automatic_contributor_completion_policy = (
            automatic_contributor_completion_policy
            or cycle.automatic_contributor_completion_policy
        )
        if (
            automatic_campus_contribution_policy
            not in ExaminationCycle.AutomaticCampusContributionPolicy.values
            or automatic_contributor_completion_policy
            not in ExaminationCycle.AutomaticContributorCompletionPolicy.values
        ):
            raise ValidationError("Unsupported automatic generation policy.")
        policies_changed = (
            cycle.automatic_campus_contribution_policy
            != automatic_campus_contribution_policy
            or cycle.automatic_contributor_completion_policy
            != automatic_contributor_completion_policy
        )
        if cycle.status != ExaminationCycle.Status.DRAFT and policies_changed:
            raise ValidationError(
                "Automatic generation policies can change only while the cycle is Draft."
            )
        for value in (default_questions_required_per_faculty, default_final_item_count):
            if value is not None and not 50 <= value <= 75:
                raise ValidationError("Cycle defaults must be from 50 to 75.")
        contributor_instructions = contributor_instructions or ""
        default_coverage = (
            cycle.default_coverage
            if default_coverage is None
            else (default_coverage or "").strip()
        )
        normalized_default_deadline = normalize_contribution_deadline_to_minute(
            default_contribution_deadline
        )
        deadline_changed = (
            normalize_contribution_deadline_to_minute(
                cycle.default_contribution_deadline
            )
            != normalized_default_deadline
        )
        effective_default_deadline = (
            normalized_default_deadline
            if deadline_changed
            else cycle.default_contribution_deadline
        )
        defaults_changed = (
            cycle.default_questions_required_per_faculty != default_questions_required_per_faculty
            or cycle.default_final_item_count != default_final_item_count
            or deadline_changed
            or cycle.default_coverage != default_coverage
        )
        reason = (reason or "").strip()
        if cycle.status == ExaminationCycle.Status.OPEN and defaults_changed and not 10 <= len(reason) <= 500:
            raise ValidationError("An administrative reason from 10 to 500 characters is required while the cycle is Open.")
        before = cls._configuration_audit_payload(cycle)
        prior_default_coverage = cycle.default_coverage
        changed = (
            defaults_changed
            or cycle.processing_mode != processing_mode
            or policies_changed
            or cycle.contributor_instructions != contributor_instructions
        )
        if not changed:
            return cycle, False
        cycle.default_questions_required_per_faculty = default_questions_required_per_faculty
        cycle.default_final_item_count = default_final_item_count
        cycle.default_contribution_deadline = effective_default_deadline
        cycle.default_coverage = default_coverage
        cycle.processing_mode = processing_mode
        cycle.automatic_campus_contribution_policy = (
            automatic_campus_contribution_policy
        )
        cycle.automatic_contributor_completion_policy = (
            automatic_contributor_completion_policy
        )
        if defaults_changed:
            cycle.defaults_revision += 1
        cycle.contributor_instructions = contributor_instructions
        cycle.full_clean()
        cycle.save(update_fields=["processing_mode", "automatic_campus_contribution_policy", "automatic_contributor_completion_policy", "default_questions_required_per_faculty", "default_final_item_count", "default_contribution_deadline", "default_coverage", "defaults_revision", "contributor_instructions", "updated_at"])
        propagation = (
            cls._propagate_defaults_to_drafts(cycle=cycle, changed_defaults={key for key, value in (("questions_required_per_faculty", default_questions_required_per_faculty), ("final_item_count", default_final_item_count), ("contribution_deadline", effective_default_deadline), ("coverage", default_coverage)) if getattr(cycle, f"default_{key}") != (before[f"default_{key}"] if key != "coverage" else prior_default_coverage)})
            if defaults_changed
            else {
                "created": 0,
                "updated": 0,
                "affected_configuration_ids": [],
                "excluded_by_reason": {},
            }
        )
        metadata = {
            "expected_updated_at": expected_updated_at,
            "propagation": {
                "created": propagation["created"],
                "updated": propagation["updated"],
                "affected_configuration_ids": propagation[
                    "affected_configuration_ids"
                ],
                "excluded_by_reason": propagation["excluded_by_reason"],
            },
        }
        if defaults_changed:
            metadata.update(cls._text_audit_evidence(value=reason, label="reason"))
        AuditService.log_event(
            action="DE_EXAM_CYCLE_CONFIGURATION_UPDATED",
            portal="ADMIN",
            entity_type="ExaminationCycle",
            entity_id=cycle.id,
            actor=user,
            tenant=tenant_id,
            before_data=before,
            after_data=cls._configuration_audit_payload(cycle),
            metadata=metadata,
            request=request,
        )
        return cycle, True

    @classmethod
    @transaction.atomic
    def open_cycle(cls, *, cycle_id, tenant_id, user, expected_updated_at, request=None):
        cycle = cls._lock_cycle(cycle_id=cycle_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_permission(user=user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
        if cycle.status == ExaminationCycle.Status.OPEN:
            return cycle, False
        if cycle.status != ExaminationCycle.Status.DRAFT:
            raise ValidationError("Only Draft cycles can be opened.")
        if expected_updated_at != cls.transition_token(cycle):
            raise CourseExamConfigurationConflict("The examination cycle changed after this page was loaded.")
        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        AuditService.log_event(action="DE_EXAM_CYCLE_OPENED", portal="ADMIN", entity_type="ExaminationCycle", entity_id=cycle.id, actor=user, tenant=tenant_id, metadata=cls._configuration_audit_payload(cycle), request=request)
        return cycle, True

    @classmethod
    @transaction.atomic
    def close_cycle(cls, *, cycle_id, tenant_id, user, expected_updated_at, request=None):
        cycle = cls._lock_cycle(cycle_id=cycle_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_permission(user=user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
        if cycle.status == ExaminationCycle.Status.CLOSED:
            return cycle, False
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError("Only Open cycles can be closed.")
        if expected_updated_at != cls.transition_token(cycle):
            raise CourseExamConfigurationConflict("The examination cycle changed after this page was loaded.")
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        AuditService.log_event(action="DE_EXAM_CYCLE_CLOSED", portal="ADMIN", entity_type="ExaminationCycle", entity_id=cycle.id, actor=user, tenant=tenant_id, request=request)
        return cycle, True


class DepartmentalExamAuthorizationService:
    """Authorization rules that intentionally do not inherit null-department expansion."""

    CONFIGURE_PERMISSION = "departmental_exams.configure"
    REVIEWER_PERMISSION = "departmental_exams.review_generate"
    VIEW_GENERATED_PERMISSION = "departmental_exams.view_generated_exams"
    PRINT_GENERATED_PERMISSION = "departmental_exams.print_generated_exams"
    MANAGE_GENERATION_PERMISSION = "departmental_exams.manage_exam_generation"
    AUDIT_GENERATED_PERMISSION = "departmental_exams.audit_generated_exams"
    RELEASE_ANSWER_KEYS_PERMISSION = "departmental_exams.release_answer_keys"
    ANY_AUTOMATIC_PERMISSION = "__any_automatic_permission__"

    @staticmethod
    def _effective_permission_annotations(
        *, user, permission_code, tenant_id, campus_id
    ):
        """Build set-based annotations equivalent to ``PermissionService``.

        ``PermissionService`` uses ``__in=[current_id, None]`` for scoped
        permissions. Django removes ``None`` from that lookup, so a concrete
        current tenant/campus requires an exact permission-bearing scope.
        Departmental Exam ownership membership remains a separate
        exact-department check in the calling queryset.
        """
        role_permission = (
            UserRole.objects.filter(
                user=user,
                is_active=True,
                role__is_active=True,
                role__role_permissions__permission__code=permission_code,
                role__role_permissions__permission__is_active=True,
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        )
        user_permissions = (
            UserPermission.objects.filter(
                user=user,
                permission__code=permission_code,
                permission__is_active=True,
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        )
        return {
            "has_role_permission": Exists(role_permission),
            "has_direct_allow": Exists(
                user_permissions.filter(grant_type=UserPermission.GrantType.ALLOW)
            ),
            "has_direct_deny": Exists(
                user_permissions.filter(grant_type=UserPermission.GrantType.DENY)
            ),
        }

    @staticmethod
    def require_enabled(*, tenant_id):
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=tenant_id):
            raise PermissionDenied("Departmental Exam Builder is not enabled.")

    @classmethod
    def require_permission(cls, *, user, permission, tenant_id, campus_id=None):
        cls.require_enabled(tenant_id=tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active user is required for Departmental Exam Builder administration.")
        if not PermissionService.has_permission(
            user, permission, tenant_id=tenant_id, campus_id=campus_id
        ):
            raise PermissionDenied("You do not have Departmental Exam Builder permission.")

    @classmethod
    def _exact_department_memberships(cls, *, user, tenant_id, responsible_department):
        """Return active ownership memberships for one exact exam department.

        Departmental Exam Builder deliberately does not inherit parent/child
        department scope.  A null-campus membership can represent the same
        department across campus selection, but permission is still evaluated
        at the responsible department's actual campus.
        """
        if not responsible_department or not responsible_department.is_active:
            return UserRole.objects.none()
        return UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=responsible_department.id,
            user__is_active=True,
        ).filter(
            Q(campus_id=responsible_department.campus_id)
            | Q(campus_id__isnull=True)
        )

    @classmethod
    def is_eligible_configurer(cls, *, user, tenant_id, responsible_department):
        if (
            not user
            or not user.is_active
            or not responsible_department
            or not responsible_department.is_active
        ):
            return False
        if user.is_superuser:
            return True
        if not cls._exact_department_memberships(
            user=user,
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        ).exists():
            return False
        return PermissionService.has_permission(
            user,
            cls.CONFIGURE_PERMISSION,
            tenant_id=tenant_id,
            campus_id=responsible_department.campus_id,
        )

    @classmethod
    def configurable_departments(cls, *, user, tenant_id):
        """Return departments a user can configure without hierarchy expansion."""
        departments = Department.objects.filter(tenant_id=tenant_id, is_active=True)
        if not user or not user.is_authenticated or not user.is_active:
            return departments.none()
        if user.is_superuser:
            return departments.order_by("campus__name", "name", "code")

        campus_id = OuterRef("campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("pk"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))
        return (
            departments.annotate(
                has_exact_membership=Exists(exact_membership),
                **cls._effective_permission_annotations(
                    user=user,
                    permission_code=cls.CONFIGURE_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                has_exact_membership=True,
                has_direct_deny=False,
            )
            .filter(Q(has_role_permission=True) | Q(has_direct_allow=True))
            .order_by("campus__name", "name", "code")
        )

    @classmethod
    def reviewable_departments(cls, *, user, tenant_id):
        """Return exact active department scopes that grant reviewer capability."""
        departments = Department.objects.filter(tenant_id=tenant_id, is_active=True)
        if not user or not user.is_authenticated or not user.is_active:
            return departments.none()
        if user.is_superuser:
            return departments
        campus_id = OuterRef("campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("pk"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))
        return departments.annotate(
            has_exact_membership=Exists(exact_membership),
            **cls._effective_permission_annotations(
                user=user,
                permission_code=cls.REVIEWER_PERMISSION,
                tenant_id=tenant_id,
                campus_id=campus_id,
            ),
        ).filter(
            has_exact_membership=True,
            has_direct_deny=False,
        ).filter(Q(has_role_permission=True) | Q(has_direct_allow=True))

    @classmethod
    def require_assigned_course_route_capability(cls, *, user, tenant_id):
        """Allow a non-disclosing empty list only for a valid route-capable user."""
        cls.require_enabled(tenant_id=tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active user is required for course examination access.")
        if user.is_superuser:
            return
        if cls.configurable_departments(user=user, tenant_id=tenant_id).exists():
            return
        if cls.reviewable_departments(user=user, tenant_id=tenant_id).exists():
            return
        automatic_courses = CycleCourse.objects.filter(
            cycle__tenant_id=tenant_id,
            cycle__processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
        ).prefetch_related("offering_snapshots")
        automatic_courses = list(automatic_courses)
        permissions = cls.automatic_permission_map(
            user=user,
            courses=automatic_courses,
            permissions=(
                cls.VIEW_GENERATED_PERMISSION,
                cls.MANAGE_GENERATION_PERMISSION,
            ),
        )
        inclusion_permissions = cls.automatic_inclusion_management_map(
            user=user,
            courses=automatic_courses,
        )
        if any(permissions.values()) or any(inclusion_permissions.values()):
            return
        raise PermissionDenied("You do not have current course examination access.")

    @classmethod
    def require_configure_cycle_course(cls, *, user, cycle_course):
        """Authorize grouped-course administration, not cycle management."""
        tenant_id = cycle_course.cycle.tenant_id
        cls.require_enabled(tenant_id=tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active user is required for course administration.")
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return cls.require_generation_management(
                user=user,
                cycle_course=cycle_course,
            )
        if user.is_superuser:
            return
        if not cycle_course.responsible_department_id:
            raise PermissionDenied(
                "Only a superuser may assign the initial exam department."
            )
        if not cls.is_eligible_configurer(
            user=user,
            tenant_id=tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            raise PermissionDenied(
                "Course examination is outside your configure scope."
            )

    @classmethod
    def require_blueprint_structure_management(cls, *, user, cycle_course):
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return cls.require_generation_management(
                user=user,
                cycle_course=cycle_course,
            )
        return cls.require_configure_cycle_course(
            user=user,
            cycle_course=cycle_course,
        )

    @classmethod
    def configurer_visible_cycle_courses(
        cls,
        *,
        user,
        cycle=None,
        tenant_id=None,
        queryset=None,
        include_null_for_superuser=True,
    ):
        """Return exact-scope grouped courses a user may administer.

        ``cycle`` supports the existing per-cycle route.  The role-aware
        assigned-course landing page supplies ``tenant_id`` to use the same
        set-based policy across cycles.  This intentionally has no department
        hierarchy expansion.
        """
        if not user or not user.is_authenticated or not user.is_active:
            return CycleCourse.objects.none()

        tenant_id = cycle.tenant_id if cycle is not None else tenant_id
        if tenant_id is None:
            return CycleCourse.objects.none()
        queryset = queryset if queryset is not None else CycleCourse.objects.all()
        cycle_filter = {"cycle": cycle} if cycle is not None else {"cycle__tenant_id": tenant_id}
        if user.is_superuser:
            courses = queryset.filter(**cycle_filter)
            if not include_null_for_superuser:
                courses = courses.filter(
                    responsible_department__isnull=False,
                    responsible_department__is_active=True,
                )
            else:
                courses = courses.filter(
                    Q(responsible_department__isnull=True)
                    | Q(responsible_department__is_active=True)
                )
            return courses

        campus_id = OuterRef("responsible_department__campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("responsible_department_id"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))
        return (
            queryset.filter(
                **cycle_filter,
                responsible_department__isnull=False,
                responsible_department__is_active=True,
            )
            .annotate(
                has_exact_configurer_membership=Exists(exact_membership),
                **cls._effective_permission_annotations(
                    user=user,
                    permission_code=cls.CONFIGURE_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                has_exact_configurer_membership=True,
                has_direct_deny=False,
            )
            .filter(Q(has_role_permission=True) | Q(has_direct_allow=True))
        )

    @classmethod
    def _reviewer_scope_memberships(cls, *, tenant_id, responsible_department):
        """Return active memberships for the exact responsible department."""
        if not responsible_department or not responsible_department.is_active:
            return UserRole.objects.none()
        return UserRole.objects.filter(
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=responsible_department.id,
            user__is_active=True,
        ).filter(
            Q(campus_id=responsible_department.campus_id)
            | Q(campus_id__isnull=True)
        )

    @classmethod
    def is_eligible_reviewer(cls, *, user, tenant_id, responsible_department):
        if not user or not user.is_active or not responsible_department:
            return False
        if not cls._reviewer_scope_memberships(
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        ).filter(user=user).exists():
            return False
        return PermissionService.has_permission(
            user,
            cls.REVIEWER_PERMISSION,
            tenant_id=tenant_id,
            campus_id=responsible_department.campus_id,
        )

    @classmethod
    def eligible_reviewers(cls, *, tenant_id, responsible_department):
        if not responsible_department:
            return User.objects.none()
        campus_id = responsible_department.campus_id
        candidate_memberships = cls._reviewer_scope_memberships(
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        )
        candidate_ids = candidate_memberships.values("user_id")

        return (
            User.objects.filter(id__in=candidate_ids, is_active=True)
            .annotate(
                **cls._effective_permission_annotations(
                    user=OuterRef("pk"),
                    permission_code=cls.REVIEWER_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                Q(is_superuser=True)
                | (
                    Q(has_direct_deny=False)
                    & (Q(has_role_permission=True) | Q(has_direct_allow=True))
                )
            )
            .order_by("last_name", "first_name", "username")
        )

    @classmethod
    def reviewer_visible_cycle_courses(
        cls, *, user, cycle=None, tenant_id=None, queryset=None
    ):
        """Return assigned CycleCourses that satisfy current reviewer eligibility.

        This is the set-based counterpart of ``is_eligible_reviewer`` for the
        routed reviewer list.  It deliberately uses the exact responsible
        department and responsible-campus permission semantics rather than the
        broader department hierarchy used for manager scope.
        """
        if not user or not user.is_authenticated or not user.is_active:
            return CycleCourse.objects.none()

        tenant_id = cycle.tenant_id if cycle is not None else tenant_id
        if tenant_id is None:
            return CycleCourse.objects.none()
        queryset = queryset if queryset is not None else CycleCourse.objects.all()
        cycle_filter = {"cycle": cycle} if cycle is not None else {"cycle__tenant_id": tenant_id}
        campus_id = OuterRef("responsible_department__campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("responsible_department_id"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))

        courses = queryset.filter(
            **cycle_filter,
            reviewer=user,
            responsible_department__isnull=False,
            responsible_department__is_active=True,
        ).annotate(has_exact_reviewer_membership=Exists(exact_membership))

        if user.is_superuser:
            if not Permission.objects.filter(
                code=cls.REVIEWER_PERMISSION,
                is_active=True,
            ).exists():
                return CycleCourse.objects.none()
            return courses.filter(has_exact_reviewer_membership=True)

        return courses.annotate(
            **cls._effective_permission_annotations(
                user=user,
                permission_code=cls.REVIEWER_PERMISSION,
                tenant_id=tenant_id,
                campus_id=campus_id,
            ),
        ).filter(
            has_exact_reviewer_membership=True,
            has_direct_deny=False,
        ).filter(Q(has_role_permission=True) | Q(has_direct_allow=True))

    @classmethod
    def require_course_responsibility(cls, *, user, cycle_course, permission=None):
        tenant_id = cycle_course.cycle.tenant_id
        cls.require_enabled(tenant_id=tenant_id)
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise PermissionDenied("Exempt course examinations are read-only.")
        if cycle_course.reviewer_id != user.id:
            raise PermissionDenied("You are not the assigned reviewer for this course examination.")
        if not cls.is_eligible_reviewer(
            user=user,
            tenant_id=tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            raise PermissionDenied("Reviewer eligibility is no longer valid for this course examination.")
        required_permission = permission or cls.REVIEWER_PERMISSION
        if required_permission != cls.REVIEWER_PERMISSION:
            cls.require_permission(
                user=user,
                permission=required_permission,
                tenant_id=tenant_id,
                campus_id=cycle_course.responsible_department.campus_id,
            )

    @staticmethod
    def participating_campus_ids(cycle_course):
        prefetched = getattr(cycle_course, "_prefetched_objects_cache", {}).get(
            "offering_snapshots"
        )
        if prefetched is not None:
            return tuple(sorted({row.campus_id for row in prefetched}))
        return tuple(
            cycle_course.offering_snapshots.order_by("campus_id")
            .values_list("campus_id", flat=True)
            .distinct()
        )

    @classmethod
    def _automatic_permission_map(
        cls, *, user, courses, permissions, require_included
    ):
        courses = list(courses)
        permission_codes = tuple(dict.fromkeys(permissions))
        result = {course.id: set() for course in courses}
        if not courses or not permission_codes:
            return result
        tenant_ids = {course.cycle.tenant_id for course in courses}
        if len(tenant_ids) != 1:
            return result
        tenant_id = next(iter(tenant_ids))
        automatic_scopes = {
            course.id: {
                "inclusion_status": course.inclusion_status,
                "campus_ids": cls.participating_campus_ids(course),
            }
            for course in courses
            if (
                course.cycle.processing_mode
                == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            )
        }
        # An active equivalency group is one authorization unit.  Every input
        # member receives the full member-campus union so a primary-only grant
        # cannot expose or mutate shared examination state through a list/view
        # path that uses this set-based map.
        from .models import ExamCourseEquivalencyMembership

        input_ids = set(result)
        group_ids = set(
            ExamCourseEquivalencyMembership.objects.filter(
                cycle_course_id__in=input_ids,
                active_marker=1,
                group__is_active=True,
            ).values_list("group_id", flat=True)
        )
        if group_ids:
            grouped_rows = list(
                ExamCourseEquivalencyMembership.objects.filter(
                    group_id__in=group_ids,
                    active_marker=1,
                    group__is_active=True,
                )
                .select_related("group__cycle", "group__primary_cycle_course", "cycle_course__cycle")
                .prefetch_related("cycle_course__offering_snapshots")
                .order_by("group_id", "cycle_course_id")
            )
            rows_by_group = {}
            for row in grouped_rows:
                rows_by_group.setdefault(row.group_id, []).append(row)
            grouped_input_ids = {
                row.cycle_course_id for row in grouped_rows if row.cycle_course_id in input_ids
            }
            for course_id in grouped_input_ids:
                automatic_scopes.pop(course_id, None)
            for rows in rows_by_group.values():
                group = rows[0].group
                members = tuple(row.cycle_course for row in rows)
                member_ids = {member.id for member in members}
                valid = (
                    group.primary_cycle_course_id in member_ids
                    and all(member.cycle_id == group.cycle_id for member in members)
                    and all(
                        member.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
                        for member in members
                    )
                    and group.cycle.tenant_id == tenant_id
                    and group.cycle.processing_mode
                    == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                )
                if not valid:
                    continue
                campus_ids = tuple(
                    sorted(
                        {
                            campus_id
                            for member in members
                            for campus_id in cls.participating_campus_ids(member)
                        }
                    )
                )
                inclusion_status = CycleCourse.InclusionStatus.INCLUDED
                for member in members:
                    if member.id in input_ids:
                        automatic_scopes[member.id] = {
                            "inclusion_status": inclusion_status,
                            "campus_ids": campus_ids,
                        }
        scoped_result = cls.automatic_scope_permission_map(
            user=user,
            tenant_id=tenant_id,
            course_scopes=automatic_scopes,
            permissions=permission_codes,
            require_included=require_included,
        )
        result.update(scoped_result)
        return result

    @classmethod
    def automatic_scope_permission_map(
        cls,
        *,
        user,
        tenant_id,
        course_scopes,
        permissions,
        require_included,
    ):
        """Authorize lightweight Automatic course/campus scope without ORM prefetch.

        ``course_scopes`` maps a CycleCourse PK to its inclusion status and the
        distinct participating Campus PKs.  It intentionally carries no course,
        contribution, offering, configuration, or revision objects, allowing
        aggregate reports to validate a selected cycle before loading those
        heavier relations.  Permission semantics remain shared with every other
        Automatic workflow path.
        """
        permission_codes = tuple(dict.fromkeys(permissions))
        scopes = {
            course_id: {
                "inclusion_status": scope["inclusion_status"],
                "campus_ids": tuple(sorted(set(scope["campus_ids"]))),
            }
            for course_id, scope in course_scopes.items()
        }
        result = {course_id: set() for course_id in scopes}
        if not scopes or not permission_codes:
            return result
        try:
            cls.require_enabled(tenant_id=tenant_id)
        except PermissionDenied:
            return result
        if not user or not user.is_authenticated or not user.is_active:
            return result

        if user.is_superuser:
            active_codes = set(
                Permission.objects.filter(
                    code__in=permission_codes,
                    is_active=True,
                ).values_list("code", flat=True)
            )
            for course_id, scope in scopes.items():
                if (
                    (
                        not require_included
                        or scope["inclusion_status"]
                        == CycleCourse.InclusionStatus.INCLUDED
                    )
                    and scope["campus_ids"]
                ):
                    result[course_id] = set(active_codes)
                    if active_codes:
                        result[course_id].add(cls.ANY_AUTOMATIC_PERMISSION)
            return result

        direct_rows = list(
            UserPermission.objects.filter(
                user=user,
                permission__code__in=permission_codes,
                permission__is_active=True,
            )
            .filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True))
            .values_list(
                "permission__code",
                "tenant_id",
                "campus_id",
                "grant_type",
            )
        )
        role_rows = list(
            UserRole.objects.filter(
                user=user,
                is_active=True,
                role__is_active=True,
                role__role_permissions__permission__code__in=permission_codes,
                role__role_permissions__permission__is_active=True,
            )
            .filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True))
            .values_list(
                "role__role_permissions__permission__code",
                "tenant_id",
                "campus_id",
            )
        )

        def direct_decision(permission, campus_id):
            matching = [
                row
                for row in direct_rows
                if row[0] == permission and row[2] in (None, campus_id)
            ]
            if any(row[3] == UserPermission.GrantType.DENY for row in matching):
                return False
            if any(row[3] == UserPermission.GrantType.ALLOW for row in matching):
                return True
            return None

        def role_allows(permission, campus_id):
            return any(
                row[0] == permission and row[2] in (None, campus_id)
                for row in role_rows
            )

        def permission_allows(permission, campus_id):
            direct = direct_decision(permission, campus_id)
            return direct is True or (
                direct is None and role_allows(permission, campus_id)
            )

        for course_id, scope in scopes.items():
            if (
                require_included
                and scope["inclusion_status"]
                != CycleCourse.InclusionStatus.INCLUDED
            ):
                continue
            campus_ids = scope["campus_ids"]
            if not campus_ids:
                continue
            for permission in permission_codes:
                if all(
                    permission_allows(permission, campus_id)
                    for campus_id in campus_ids
                ):
                    result[course_id].add(permission)
            if all(
                any(
                    permission_allows(permission, campus_id)
                    for permission in permission_codes
                )
                for campus_id in campus_ids
            ):
                result[course_id].add(cls.ANY_AUTOMATIC_PERMISSION)
        return result

    @classmethod
    def automatic_permission_map(cls, *, user, courses, permissions):
        """Return Included-only downstream Automatic workflow authority."""
        return cls._automatic_permission_map(
            user=user,
            courses=courses,
            permissions=permissions,
            require_included=True,
        )

    @classmethod
    def automatic_inclusion_management_map(cls, *, user, courses):
        """Return status-independent Automatic Exempt/Restore authority."""
        return cls._automatic_permission_map(
            user=user,
            courses=courses,
            permissions=(cls.MANAGE_GENERATION_PERMISSION,),
            require_included=False,
        )

    @staticmethod
    def _has_scoped_permission(*, user, permission, tenant_id, campus_id):
        if user.is_superuser:
            return Permission.objects.filter(code=permission, is_active=True).exists()
        scoped_direct = UserPermission.objects.filter(
            user=user,
            permission__code=permission,
            permission__is_active=True,
        ).filter(
            Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
            Q(campus_id=campus_id) | Q(campus_id__isnull=True),
        )
        if scoped_direct.filter(grant_type=UserPermission.GrantType.DENY).exists():
            return False
        if scoped_direct.filter(grant_type=UserPermission.GrantType.ALLOW).exists():
            return True
        return UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            role__role_permissions__permission__code=permission,
            role__role_permissions__permission__is_active=True,
        ).filter(
            Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
            Q(campus_id=campus_id) | Q(campus_id__isnull=True),
        ).exists()

    @classmethod
    def require_automatic_tenant_permission(
        cls, *, user, permission, tenant_id
    ):
        """Require campus-neutral authority when no participating campus exists."""
        cls.require_enabled(tenant_id=tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied(
                "An active user is required for Departmental Exam Builder administration."
            )
        if not cls._has_scoped_permission(
            user=user,
            permission=permission,
            tenant_id=tenant_id,
            campus_id=None,
        ):
            raise PermissionDenied(
                "You do not have tenant-wide automatic examination authority."
            )

    @classmethod
    def has_automatic_course_permission(cls, *, user, cycle_course, permissions):
        from .exam_units import resolve_examination_unit

        try:
            unit = resolve_examination_unit(cycle_course)
        except (ValidationError, CycleCourse.DoesNotExist):
            return False
        return cls.has_automatic_courses_permission(
            user=user,
            cycle=unit.primary.cycle,
            courses=unit.members,
            permissions=permissions,
        )

    @classmethod
    def has_automatic_courses_permission(
        cls, *, user, cycle, courses, permissions, require_included=True
    ):
        """Authorize one complete Automatic examination unit campus union."""

        courses = tuple(courses)
        tenant_id = cycle.tenant_id
        if (
            not courses
            or cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            or any(
                course.cycle_id != cycle.id
                or course.cycle.tenant_id != tenant_id
                or (
                    require_included
                    and course.inclusion_status
                    != CycleCourse.InclusionStatus.INCLUDED
                )
                for course in courses
            )
        ):
            return False
        try:
            cls.require_enabled(tenant_id=tenant_id)
        except PermissionDenied:
            return False
        if not user or not user.is_authenticated or not user.is_active:
            return False
        permission_codes = tuple(dict.fromkeys(permissions))
        campus_ids = tuple(
            sorted(
                {
                    campus_id
                    for course in courses
                    for campus_id in cls.participating_campus_ids(course)
                }
            )
        )
        if not campus_ids:
            return False
        return all(
            any(
                cls._has_scoped_permission(
                    user=user,
                    permission=permission,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                )
                for permission in permission_codes
            )
            for campus_id in campus_ids
        )

    @classmethod
    def require_automatic_courses_permission(
        cls, *, user, cycle, courses, permissions, require_included=True
    ):
        if not cls.has_automatic_courses_permission(
            user=user,
            cycle=cycle,
            courses=courses,
            permissions=permissions,
            require_included=require_included,
        ):
            raise PermissionDenied(
                "You do not have automatic examination authority for every participating campus."
            )

    @classmethod
    def require_automatic_course_permission(
        cls, *, user, cycle_course, permissions
    ):
        if not cls.has_automatic_course_permission(
            user=user,
            cycle_course=cycle_course,
            permissions=permissions,
        ):
            raise PermissionDenied(
                "You do not have automatic examination authority for every participating campus."
            )

    @classmethod
    def require_generation_management(cls, *, user, cycle_course):
        cls.require_automatic_course_permission(
            user=user,
            cycle_course=cycle_course,
            permissions=(cls.MANAGE_GENERATION_PERMISSION,),
        )

    @classmethod
    def require_generation_audit(cls, *, user, cycle_course):
        cls.require_automatic_course_permission(
            user=user,
            cycle_course=cycle_course,
            permissions=(
                cls.AUDIT_GENERATED_PERMISSION,
                cls.MANAGE_GENERATION_PERMISSION,
            ),
        )

    @classmethod
    def require_answer_key_release(cls, *, user, cycle_course):
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return cls.require_automatic_course_permission(
                user=user,
                cycle_course=cycle_course,
                permissions=(cls.RELEASE_ANSWER_KEYS_PERMISSION,),
            )
        return cls.require_course_responsibility(
            user=user,
            cycle_course=cycle_course,
            permission=cls.RELEASE_ANSWER_KEYS_PERMISSION,
        )

    @classmethod
    def can_release_answer_keys(cls, *, user, cycle_course):
        try:
            cls.require_answer_key_release(user=user, cycle_course=cycle_course)
        except PermissionDenied:
            return False
        return True

    @classmethod
    def require_automatic_inclusion_management(cls, *, user, cycle_course):
        permissions = cls.automatic_inclusion_management_map(
            user=user,
            courses=(cycle_course,),
        )
        if cls.MANAGE_GENERATION_PERMISSION not in permissions[cycle_course.id]:
            raise PermissionDenied(
                "You do not have automatic inclusion-management authority for every participating campus."
            )

    @classmethod
    def require_cycle_course_inclusion_management(cls, *, user, cycle_course):
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return cls.require_automatic_inclusion_management(
                user=user,
                cycle_course=cycle_course,
            )
        return cls.require_configure_cycle_course(
            user=user,
            cycle_course=cycle_course,
        )

    @classmethod
    def require_generation_input_management(cls, *, user, cycle_course):
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return cls.require_generation_management(
                user=user,
                cycle_course=cycle_course,
            )
        return cls.require_course_responsibility(
            user=user,
            cycle_course=cycle_course,
        )

    @classmethod
    def require_generated_exam_view(cls, *, user, cycle_course):
        if (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            return cls.require_course_responsibility(
                user=user,
                cycle_course=cycle_course,
            )
        cls.require_automatic_course_permission(
            user=user,
            cycle_course=cycle_course,
            permissions=(
                cls.VIEW_GENERATED_PERMISSION,
                cls.MANAGE_GENERATION_PERMISSION,
            ),
        )


class CycleCourseTransitionConflict(ValidationError):
    """Raised when a confirmation page no longer represents the current row."""


class CycleCourseInclusionService:
    REASON_MIN_LENGTH = 10
    REASON_MAX_LENGTH = 500

    @staticmethod
    def transition_token(cycle_course):
        return cycle_course.updated_at.isoformat()

    @classmethod
    def require_included(cls, *, cycle_course):
        """Instance-only assertion; callers must already own any required lock."""
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise PermissionDenied("Exempt course examinations cannot enter this workflow.")

    @classmethod
    def lock_included_cycle_course(cls, *, cycle_course_id, tenant_id):
        """Lock and return an Included parent for a downstream write transaction.

        Future downstream writers must call this inside ``transaction.atomic()``
        before creating children, preserving the parent-first lock order used by
        inclusion transitions.
        """
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError(
                "lock_included_cycle_course() must be called inside transaction.atomic()."
            )
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        cls.require_included(cycle_course=cycle_course)
        return cycle_course

    @classmethod
    def _locked_cycle_course(cls, *, cycle_course_id, tenant_id):
        """Acquire Stage 4 locks in cycle, parent, then child order.

        The first two locking queries intentionally have no joins.  Relations
        needed for authorization and audit are hydrated only after both parent
        rows are locked, preventing joined-row lock expansion on MariaDB.
        """
        cycle_id = CycleCourse.objects.filter(id=cycle_course_id).values_list(
            "cycle_id", flat=True
        ).get()
        try:
            ExaminationCycle.objects.select_for_update().get(
                id=cycle_id, tenant_id=tenant_id
            )
        except ExaminationCycle.DoesNotExist as exc:
            raise CycleCourse.DoesNotExist from exc
        CycleCourse.objects.select_for_update().get(id=cycle_course_id, cycle_id=cycle_id)
        return CycleCourse.objects.select_related(
            "cycle",
            "cycle__tenant",
            "course",
            "responsible_department",
            "responsible_department__campus",
            "reviewer",
        ).get(id=cycle_course_id, cycle_id=cycle_id)

    @classmethod
    def _validate_reason(cls, reason):
        cleaned = (reason or "").strip()
        if not cls.REASON_MIN_LENGTH <= len(cleaned) <= cls.REASON_MAX_LENGTH:
            raise ValidationError(
                f"Reason must be from {cls.REASON_MIN_LENGTH} to {cls.REASON_MAX_LENGTH} characters."
            )
        return cleaned

    @classmethod
    def _require_current_confirmation(cls, *, cycle_course, expected_updated_at):
        if expected_updated_at != cls.transition_token(cycle_course):
            raise CycleCourseTransitionConflict(
                "The course examination changed after this page was loaded. Review the latest state and try again."
            )

    @staticmethod
    def _require_draft_active_department(*, cycle_course):
        if cycle_course.cycle.status != ExaminationCycle.Status.DRAFT:
            raise ValidationError(
                "Only Draft examination cycles can change course inclusion."
            )
        if (
            cycle_course.responsible_department_id
            and not cycle_course.responsible_department.is_active
        ):
            raise ValidationError(
                "The responsible exam department is inactive. Reactivate or reassign it before changing inclusion."
            )

    @classmethod
    def _require_exempt_transition_window(cls, *, cycle_course):
        automatic_open = (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            and cycle_course.cycle.status == ExaminationCycle.Status.OPEN
        )
        if not automatic_open:
            cls._require_draft_active_department(cycle_course=cycle_course)
        if (
            not automatic_open
            and FacultyContribution.objects.filter(cycle_course=cycle_course).exists()
        ):
            raise ValidationError(
                "This course has downstream faculty contribution data and cannot change inclusion."
            )
        if (
            not automatic_open
            and Question.objects.filter(
                contribution__cycle_course=cycle_course
            ).exists()
        ):
            raise ValidationError(
                "This course has downstream examination question data and cannot change inclusion."
            )
        if ExamGenerationRevision.objects.filter(
            cycle_course=cycle_course,
            current_marker=1,
        ).exists():
            raise ValidationError(
                "A current generated examination revision exists. Resolve its lifecycle before exempting this course."
            )

    @classmethod
    def _require_restore_transition_window(
        cls,
        *,
        cycle_course,
        configuration,
        transition_time,
    ):
        """Allow governed Automatic OPEN restore before the effective deadline."""
        automatic_open = (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            and cycle_course.cycle.status == ExaminationCycle.Status.OPEN
        )
        if automatic_open:
            if configuration is None:
                raise ValidationError(
                    "A course examination configuration is required before restoring this course."
                )
            deadline = configuration.active_contribution_deadline
            if deadline is None:
                raise ValidationError(
                    "A contribution deadline is required before restoring this course."
                )
            if transition_time >= deadline:
                raise ValidationError(
                    "The contribution deadline has been reached. This course cannot be restored."
                )
        else:
            cls._require_draft_active_department(cycle_course=cycle_course)

        if ExamGenerationRevision.objects.filter(
            cycle_course=cycle_course,
            current_marker=1,
        ).exists():
            raise ValidationError(
                "A current generated examination revision exists. Resolve its lifecycle before restoring this course."
            )
        return automatic_open

    @staticmethod
    def _state_payload(cycle_course, configuration=None):
        payload = {
            "inclusion_status": cycle_course.inclusion_status,
            "exemption_category": cycle_course.exemption_category,
            "exemption_reason": cycle_course.exemption_reason,
            "exemption_changed_by_id": cycle_course.exemption_changed_by_id,
            "exemption_changed_at": cycle_course.exemption_changed_at,
            "reviewer_id": cycle_course.reviewer_id,
        }
        if configuration:
            payload["configuration"] = {
                "id": configuration.id,
                "workflow_status": configuration.workflow_status,
                "closed_at": configuration.closed_at,
                "closed_by_id": configuration.closed_by_id,
                "revision": configuration.revision,
                "automatic_processing_status": (
                    configuration.automatic_processing_status
                ),
                "automatic_processing_code": configuration.automatic_processing_code,
                "automatic_processed_at": configuration.automatic_processed_at,
            }
        return payload

    @classmethod
    def _audit_transition(
        cls,
        *,
        action,
        cycle_course,
        actor,
        reason,
        before,
        reviewer_revalidated,
        reviewer_cleared,
        expected_updated_at,
        request,
        configuration=None,
    ):
        offering_rows = list(
            cycle_course.offering_snapshots.values_list("id", "campus_id")
        )
        offering_campus_ids = sorted({campus_id for _id, campus_id in offering_rows})
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="CycleCourse",
            entity_id=cycle_course.id,
            actor=actor,
            tenant=cycle_course.cycle.tenant_id,
            campus=(
                cycle_course.responsible_department.campus_id
                if cycle_course.responsible_department_id
                else None
            ),
            before_data=before,
            after_data=cls._state_payload(cycle_course, configuration),
            metadata={
                "cycle_id": cycle_course.cycle_id,
                "course_id": cycle_course.course_id,
                "responsible_department_id": cycle_course.responsible_department_id,
                "responsible_department_campus_id": (
                    cycle_course.responsible_department.campus_id
                    if cycle_course.responsible_department_id
                    else None
                ),
                "offering_count": len(offering_rows),
                "offering_campus_ids": offering_campus_ids,
                "transition_reason": reason,
                "reviewer_revalidated": reviewer_revalidated,
                "reviewer_cleared": reviewer_cleared,
                "expected_updated_at": expected_updated_at,
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def exempt(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        user,
        exemption_category,
        reason,
        expected_updated_at,
        request=None,
    ):
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_cycle_course_inclusion_management(
            user=user,
            cycle_course=cycle_course,
        )
        if cycle_course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT:
            return cycle_course, False
        cls._require_current_confirmation(
            cycle_course=cycle_course,
            expected_updated_at=expected_updated_at,
        )
        cls._require_exempt_transition_window(cycle_course=cycle_course)
        reason = cls._validate_reason(reason)
        if exemption_category not in CycleCourse.ExemptionCategory.values:
            raise ValidationError("Select an approved exemption category.")

        configuration = CourseExamConfiguration.objects.select_for_update().filter(cycle_course=cycle_course).first()
        before = cls._state_payload(cycle_course, configuration)
        transition_time = timezone.now()
        if configuration:
            configuration_updates = []
            if (
                configuration.workflow_status
                == CourseExamConfiguration.WorkflowStatus.OPEN
            ):
                configuration.workflow_status = (
                    CourseExamConfiguration.WorkflowStatus.CLOSED
                )
                configuration.closed_at = transition_time
                configuration.closed_by = user
                configuration.revision += 1
                configuration_updates.extend(
                    ["workflow_status", "closed_at", "closed_by", "revision"]
                )
            if (
                cycle_course.cycle.processing_mode
                == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                and cycle_course.cycle.status == ExaminationCycle.Status.OPEN
            ):
                configuration.automatic_processing_status = (
                    CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED
                )
                configuration.automatic_processing_code = "NOT_APPLICABLE"
                configuration.automatic_processed_at = transition_time
                configuration_updates.extend(
                    [
                        "automatic_processing_status",
                        "automatic_processing_code",
                        "automatic_processed_at",
                    ]
                )
            if configuration_updates:
                configuration.save(
                    update_fields=[*dict.fromkeys(configuration_updates), "updated_at"]
                )
        cycle_course.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        cycle_course.exemption_category = exemption_category
        cycle_course.exemption_reason = reason
        cycle_course.exemption_changed_by = user
        cycle_course.exemption_changed_at = transition_time
        cycle_course.full_clean()
        cycle_course.save(
            update_fields=[
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by",
                "exemption_changed_at",
                "updated_at",
            ]
        )
        cls._audit_transition(
            action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
            cycle_course=cycle_course,
            actor=user,
            reason=reason,
            before=before,
            reviewer_revalidated=False,
            reviewer_cleared=False,
            expected_updated_at=expected_updated_at,
            request=request,
            configuration=configuration,
        )
        return cycle_course, True

    @classmethod
    @transaction.atomic
    def restore(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        user,
        reason,
        expected_updated_at,
        request=None,
    ):
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_cycle_course_inclusion_management(
            user=user,
            cycle_course=cycle_course,
        )
        if cycle_course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED:
            return cycle_course, False
        cls._require_current_confirmation(
            cycle_course=cycle_course,
            expected_updated_at=expected_updated_at,
        )
        configuration = (
            CourseExamConfiguration.objects.select_for_update()
            .filter(cycle_course=cycle_course)
            .first()
        )
        transition_time = timezone.now()
        automatic_open = cls._require_restore_transition_window(
            cycle_course=cycle_course,
            configuration=configuration,
            transition_time=transition_time,
        )
        reason = cls._validate_reason(reason)

        before = cls._state_payload(cycle_course, configuration)
        reviewer_revalidated = bool(cycle_course.reviewer_id)
        reviewer_cleared = False
        if cycle_course.reviewer_id and not DepartmentalExamAuthorizationService.is_eligible_reviewer(
            user=cycle_course.reviewer,
            tenant_id=cycle_course.cycle.tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            cycle_course.reviewer = None
            reviewer_cleared = True

        cycle_course.inclusion_status = CycleCourse.InclusionStatus.INCLUDED
        cycle_course.exemption_category = ""
        cycle_course.exemption_reason = ""
        cycle_course.exemption_changed_by = user
        cycle_course.exemption_changed_at = transition_time
        cycle_course.full_clean()
        cycle_course.save(
            update_fields=[
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by",
                "exemption_changed_at",
                "reviewer",
                "updated_at",
            ]
        )
        if automatic_open:
            configuration.workflow_status = (
                CourseExamConfiguration.WorkflowStatus.OPEN
            )
            configuration.closed_at = None
            configuration.closed_by = None
            configuration.revision += 1
            configuration.automatic_processing_status = ""
            configuration.automatic_processing_code = ""
            configuration.automatic_processed_at = None
            configuration.save(
                update_fields=[
                    "workflow_status",
                    "closed_at",
                    "closed_by",
                    "revision",
                    "automatic_processing_status",
                    "automatic_processing_code",
                    "automatic_processed_at",
                    "updated_at",
                ]
            )

            # Local import keeps the existing services module boundary acyclic.
            from .contribution_services import ContributionRosterService

            if configuration.contributor_roster_initialized_at is None:
                ContributionRosterService.initialize_for_open_locked(
                    cycle_course=cycle_course,
                    configuration=configuration,
                    actor=user,
                    request=request,
                )
            else:
                ContributionRosterService._synchronize_locked(
                    cycle_course=cycle_course,
                    configuration=configuration,
                    actor=user,
                    request=request,
                    initializing=False,
                )
        cls._audit_transition(
            action="DE_EXAM_CYCLE_COURSE_RESTORED",
            cycle_course=cycle_course,
            actor=user,
            reason=reason,
            before=before,
            reviewer_revalidated=reviewer_revalidated,
            reviewer_cleared=reviewer_cleared,
            expected_updated_at=expected_updated_at,
            request=request,
            configuration=configuration,
        )
        return cycle_course, True


class CycleCourseAdministrationService:
    """Stage 4 responsibility/reviewer writer with the same parent-first discipline."""

    REVIEWER_ELIGIBILITY_ERROR = (
        "Reviewer must have an active role, explicit department scope, and "
        "review/generate permission."
    )

    @classmethod
    @transaction.atomic
    def update_responsibility(cls, *, cycle_course_id, tenant_id, user, responsible_department, reviewer, request=None):
        cycle_course = CycleCourseInclusionService._locked_cycle_course(
            cycle_course_id=cycle_course_id, tenant_id=tenant_id
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=cycle_course)
        if cycle_course.cycle.status == ExaminationCycle.Status.CLOSED:
            raise ValidationError({"__all__": "Closed cycles cannot change course responsibility or reviewer assignment."})
        automatic_mode = (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        if not responsible_department and not automatic_mode:
            raise ValidationError({"responsible_department": "Select an exam department before assigning or changing a reviewer."})
        if responsible_department and not DepartmentalExamAuthorizationService.is_eligible_configurer(
            user=user, tenant_id=tenant_id, responsible_department=responsible_department
        ):
            raise PermissionDenied("Exam department is outside your scope.")
        configuration = CourseExamConfiguration.objects.select_for_update().filter(cycle_course=cycle_course).first()
        responsible_department_id = (
            responsible_department.id if responsible_department else None
        )
        ownership_changed = (
            responsible_department_id != cycle_course.responsible_department_id
        )
        if ownership_changed:
            if configuration and configuration.opened_at:
                raise ValidationError({"responsible_department": "Responsible exam department cannot change after first opening."})
            if FacultyContribution.objects.filter(cycle_course=cycle_course).exists() or Question.objects.filter(contribution__cycle_course=cycle_course).exists():
                raise ValidationError({"responsible_department": "Responsible exam department cannot change after downstream activity."})
        reviewer_cleared = False
        if automatic_mode:
            reviewer = None
        elif reviewer and not DepartmentalExamAuthorizationService.is_eligible_reviewer(
            user=reviewer, tenant_id=tenant_id, responsible_department=responsible_department
        ):
            if ownership_changed and reviewer.id == cycle_course.reviewer_id:
                reviewer = None
                reviewer_cleared = True
            else:
                raise ValidationError({"reviewer": cls.REVIEWER_ELIGIBILITY_ERROR})
        before = {
            "responsible_department_id": cycle_course.responsible_department_id,
            "reviewer_id": cycle_course.reviewer_id,
            "configuration_revision": configuration.revision if configuration else None,
        }
        new_reviewer_id = reviewer.id if reviewer else None
        if not ownership_changed and new_reviewer_id == cycle_course.reviewer_id:
            return cycle_course, False
        cycle_course.responsible_department = responsible_department
        cycle_course.reviewer = reviewer
        cycle_course.full_clean()
        cycle_course.save(update_fields=["responsible_department", "reviewer", "updated_at"])
        if ownership_changed and configuration:
            configuration.revision += 1
            configuration.save(update_fields=["revision", "updated_at"])
        AuditService.log_event(
            action="DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED", portal="ADMIN", entity_type="CycleCourse",
            entity_id=cycle_course.id, actor=user, tenant=tenant_id, before_data=before,
            after_data={
                "responsible_department_id": cycle_course.responsible_department_id,
                "reviewer_id": cycle_course.reviewer_id,
                "configuration_revision": configuration.revision if configuration else None,
            },
            metadata={
                "cycle_id": cycle_course.cycle_id, "course_id": cycle_course.course_id,
                "responsibility_changed": ownership_changed,
                "reviewer_revalidated": bool(reviewer), "reviewer_cleared": reviewer_cleared,
            }, request=request,
        )
        return cycle_course, True


class ExaminationCycleService:
    SNAPSHOT_BATCH_SIZE = 200

    @classmethod
    def _flush_snapshot_batch(cls, snapshot_batch):
        if snapshot_batch:
            CycleCourseOffering.objects.bulk_create(
                snapshot_batch,
                batch_size=cls.SNAPSHOT_BATCH_SIZE,
            )
            snapshot_batch.clear()

    @classmethod
    @transaction.atomic
    def create_cycle(cls, *, user, tenant, academic_year, term, exam_period, processing_mode=ExaminationCycle.ProcessingMode.MANUAL_REVIEW, request=None):
        DepartmentalExamAuthorizationService.require_permission(
            user=user,
            permission="departmental_exams.manage_cycles",
            tenant_id=tenant.id,
        )
        cycle = ExaminationCycle(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            exam_period=exam_period,
            processing_mode=processing_mode,
            created_by=user,
        )
        cycle.full_clean()
        cycle.save()
        offerings = (
            CourseOffering.objects.filter(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                is_active=True,
            )
            .exclude(status=CourseOffering.Status.ARCHIVED)
            .select_related("course__exam_department")
            .only(
                "id",
                "course_id",
                "campus_id",
                "course__id",
                "course__tenant_id",
                "course__exam_department_id",
            )
            .order_by("course_id", "id")
        )
        current_course_id = None
        cycle_course = None
        snapshot_batch = []
        course_count = 0
        # Offerings are already constrained to this cycle's tenant, year, and
        # term. Grouping by the ordered course ID and copying each offering's
        # campus ID therefore preserves the CycleCourseOffering invariants
        # without retaining the full tenant offering set in memory.
        for offering in offerings.iterator(chunk_size=cls.SNAPSHOT_BATCH_SIZE):
            if offering.course_id != current_course_id:
                cycle_course = CycleCourse(
                    cycle=cycle,
                    course=offering.course,
                    responsible_department=offering.course.exam_department,
                )
                cycle_course.full_clean()
                cycle_course.save()
                current_course_id = offering.course_id
                course_count += 1
            snapshot_batch.append(
                CycleCourseOffering(
                    cycle_course=cycle_course,
                    offering_id=offering.id,
                    campus_id=offering.campus_id,
                )
            )
            if len(snapshot_batch) >= cls.SNAPSHOT_BATCH_SIZE:
                cls._flush_snapshot_batch(snapshot_batch)
        cls._flush_snapshot_batch(snapshot_batch)
        AuditService.log_event(
            action="DE_EXAM_CYCLE_CREATED",
            portal="ADMIN",
            entity_type="ExaminationCycle",
            entity_id=cycle.id,
            actor=user,
            tenant=tenant,
            metadata={
                "exam_period": exam_period,
                "processing_mode": processing_mode,
                "course_count": course_count,
            },
            request=request,
        )
        return cycle


class CourseExamConfigurationService:
    """Parent-first, revision-protected writers for Stage 4 course configuration."""

    @staticmethod
    def _configuration_payload(configuration):
        snapshot_text = configuration.contributor_instructions_snapshot or ""
        return {
            "configuration_id": configuration.id,
            "final_item_count": configuration.final_item_count,
            "questions_required_per_faculty": configuration.questions_required_per_faculty,
            "coverage": configuration.coverage,
            "coverage_source": configuration.coverage_source,
            **ExaminationCycleConfigurationService._text_audit_evidence(
                value=configuration.additional_instructions,
                label="additional_instructions",
            ),
            "contribution_deadline": configuration.contribution_deadline,
            "contribution_deadline_source": configuration.contribution_deadline_source,
            "questions_required_per_faculty_source": configuration.questions_required_per_faculty_source,
            "final_item_count_source": configuration.final_item_count_source,
            "cycle_defaults_revision_snapshot": configuration.cycle_defaults_revision_snapshot,
            "legacy_item_count_mode_snapshot": configuration.legacy_item_count_mode_snapshot,
            "workflow_status": configuration.workflow_status,
            "opened_at": configuration.opened_at,
            "opened_by_id": configuration.opened_by_id,
            "closed_at": configuration.closed_at,
            "closed_by_id": configuration.closed_by_id,
            "contributor_instructions_snapshot_sha256": hashlib.sha256(
                snapshot_text.encode("utf-8")
            ).hexdigest(),
            "contributor_instructions_snapshot_length": len(snapshot_text),
            "revision": configuration.revision,
        }

    @classmethod
    def _lock_parent_and_configuration(cls, *, cycle_course_id, tenant_id):
        parent = CycleCourseInclusionService.lock_included_cycle_course(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        configuration = CourseExamConfiguration.objects.select_for_update().filter(cycle_course=parent).first()
        from .final_lock import FinalExamLockPolicy

        FinalExamLockPolicy.require_not_locked(parent)
        return parent, configuration

    @staticmethod
    def _require_revision(configuration, expected_revision):
        if not configuration or configuration.revision != expected_revision:
            raise CourseExamConfigurationConflict("The course configuration changed after this page was loaded. Review the latest state and try again.")

    @staticmethod
    def _require_no_activity(parent):
        if CourseExamDefaultTrackingPolicy.has_downstream_activity(parent):
            raise ValidationError("This course has downstream contribution or question activity and cannot change this workflow.")

    @staticmethod
    def _require_active_responsible_department(parent):
        if (
            parent.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return
        if not parent.responsible_department_id:
            raise ValidationError(
                {"responsible_department": "Assign an active exam department before opening faculty contribution."}
            )
        if not parent.responsible_department.is_active:
            raise ValidationError(
                {"responsible_department": "Reactivate or reassign the exam department before opening faculty contribution."}
            )

    @staticmethod
    def _require_cycle_allows_draft_save(parent):
        if parent.cycle.status == ExaminationCycle.Status.CLOSED:
            raise ValidationError({"__all__": "Closed cycles cannot be configured."})

    @staticmethod
    def _require_cycle_open_for_workflow(parent):
        if parent.cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError(
                {"__all__": "Only Open cycles can change the course contribution workflow."}
            )

    @classmethod
    def _audit(cls, *, action, parent, configuration, actor, before, request, metadata=None):
        payload = cls._configuration_payload(configuration)
        details = {"cycle_id": parent.cycle_id, "course_id": parent.course_id, "responsible_department_id": parent.responsible_department_id, **(metadata or {})}
        AuditService.log_event(action=action, portal="ADMIN", entity_type="CourseExamConfiguration", entity_id=configuration.id, actor=actor, tenant=parent.cycle.tenant_id, campus=parent.responsible_department.campus_id if parent.responsible_department_id else None, before_data=before, after_data=payload, metadata=details, request=request)

    @classmethod
    @transaction.atomic
    def save_course_draft(cls, *, cycle_course_id, tenant_id, user, expected_revision, final_item_count, questions_required_per_faculty, final_item_count_mode, questions_required_per_faculty_mode, coverage, additional_instructions, contribution_deadline, contribution_deadline_mode=None, coverage_mode=None, request=None):
        parent, configuration = cls._lock_parent_and_configuration(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
        cls._require_active_responsible_department(parent)
        cls._require_cycle_allows_draft_save(parent)
        if configuration and (configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.DRAFT or configuration.opened_at):
            raise ValidationError("Only Draft configurations can be edited. Revert an unpublished configuration first.")
        if configuration:
            cls._require_revision(configuration, expected_revision)
        elif expected_revision not in (None, 0):
            raise CourseExamConfigurationConflict("The course configuration was created after this page was loaded.")
        cycle = parent.cycle
        def resolve(value, mode, default, label):
            if mode == "DEFAULT":
                if default is None or not 50 <= default <= 75:
                    raise ValidationError(f"Configure a valid cycle default for {label} before using it.")
                return default, "DEFAULT"
            if mode == "OVERRIDE" and value is not None and 50 <= value <= 75:
                return value, "OVERRIDE"
            raise ValidationError(f"{label} override must be from 50 to 75.")
        questions_required_per_faculty, questions_required_per_faculty_source = resolve(questions_required_per_faculty, questions_required_per_faculty_mode, cycle.default_questions_required_per_faculty, "faculty quota")
        final_item_count, final_item_count_source = resolve(final_item_count, final_item_count_mode, cycle.default_final_item_count, "final item count")
        submitted_deadline = normalize_contribution_deadline_to_minute(
            contribution_deadline
        )
        existing_deadline_source = (
            configuration.contribution_deadline_source if configuration else None
        )
        if contribution_deadline_mode is None:
            if existing_deadline_source in CourseExamConfiguration.ValueSource.values:
                contribution_deadline_source = existing_deadline_source
                contribution_deadline = (
                    configuration.contribution_deadline
                    if submitted_deadline is None
                    else submitted_deadline
                )
            elif submitted_deadline is not None:
                contribution_deadline = submitted_deadline
                contribution_deadline_source = CourseExamConfiguration.ValueSource.OVERRIDE
            else:
                contribution_deadline = None
                contribution_deadline_source = None
        elif contribution_deadline_mode == CourseExamConfiguration.ValueSource.DEFAULT:
            contribution_deadline = cycle.default_contribution_deadline
            contribution_deadline_source = (
                CourseExamConfiguration.ValueSource.DEFAULT
                if contribution_deadline is not None
                else None
            )
        elif contribution_deadline_mode == CourseExamConfiguration.ValueSource.OVERRIDE:
            if submitted_deadline is None:
                raise ValidationError("A course deadline override requires a contribution deadline.")
            contribution_deadline = submitted_deadline
            contribution_deadline_source = CourseExamConfiguration.ValueSource.OVERRIDE
        else:
            raise ValidationError("Unsupported contribution deadline mode.")
        has_downstream_activity = (
            CourseExamDefaultTrackingPolicy.has_downstream_activity(parent)
            if configuration is not None
            else False
        )
        tracks_current_defaults = CourseExamDefaultTrackingPolicy.is_live_default_tracking(
            cycle_course=parent,
            configuration=configuration,
            has_downstream_activity=has_downstream_activity,
        )
        if (
            tracks_current_defaults
            and contribution_deadline_source == CourseExamConfiguration.ValueSource.DEFAULT
            and normalize_contribution_deadline_to_minute(contribution_deadline)
            != normalize_contribution_deadline_to_minute(
                cycle.default_contribution_deadline
            )
        ):
            raise ValidationError(
                "The default-sourced deadline must match the current cycle default."
            )
        if (
            configuration is not None
            and configuration.contribution_deadline_source
            == contribution_deadline_source
            and normalize_contribution_deadline_to_minute(
                configuration.contribution_deadline
            )
            == normalize_contribution_deadline_to_minute(contribution_deadline)
        ):
            contribution_deadline = configuration.contribution_deadline
        if (
            contribution_deadline is not None
            and contribution_deadline_source is None
        ):
            raise ValidationError("A course deadline override requires a contribution deadline.")
        submitted_coverage = (coverage or "").strip()
        existing_coverage_source = configuration.coverage_source if configuration else None
        if coverage_mode is None:
            coverage = submitted_coverage
            if not coverage:
                coverage_source = None
            elif (
                configuration is not None
                and submitted_coverage == configuration.coverage
                and existing_coverage_source
                in CourseExamConfiguration.ValueSource.values
            ):
                coverage_source = existing_coverage_source
            else:
                coverage_source = CourseExamConfiguration.ValueSource.OVERRIDE
        elif coverage_mode == CourseExamConfiguration.ValueSource.DEFAULT:
            coverage = (cycle.default_coverage or "").strip()
            coverage_source = (
                CourseExamConfiguration.ValueSource.DEFAULT
                if coverage
                else None
            )
        elif coverage_mode == CourseExamConfiguration.ValueSource.OVERRIDE:
            coverage = submitted_coverage
            coverage_source = (
                CourseExamConfiguration.ValueSource.OVERRIDE
                if coverage
                else None
            )
        else:
            raise ValidationError("Unsupported coverage mode.")
        if configuration is None:
            configuration = CourseExamConfiguration(cycle_course=parent, revision=1)
            before = None
        else:
            before = cls._configuration_payload(configuration)
        values = {"final_item_count": final_item_count, "final_item_count_source": final_item_count_source, "questions_required_per_faculty": questions_required_per_faculty, "questions_required_per_faculty_source": questions_required_per_faculty_source, "contribution_deadline": contribution_deadline, "contribution_deadline_source": contribution_deadline_source, "cycle_defaults_revision_snapshot": cycle.defaults_revision, "coverage": coverage, "coverage_source": coverage_source, "additional_instructions": (additional_instructions or "").strip()}
        if before and all(
            getattr(configuration, key) == value for key, value in values.items()
        ):
            return configuration, False
        for key, value in values.items():
            setattr(configuration, key, value)
        if before:
            configuration.revision += 1
        configuration.full_clean()
        configuration.save()
        cls._audit(action="DE_EXAM_COURSE_CONFIGURATION_SAVED", parent=parent, configuration=configuration, actor=user, before=before, request=request, metadata={"expected_revision": expected_revision, "resulting_revision": configuration.revision})
        return configuration, True

    @classmethod
    @transaction.atomic
    def remove_overrides(cls, *, cycle_course_id, tenant_id, user, expected_revision, return_questions_required_per_faculty, return_final_item_count, return_contribution_deadline=False, request=None):
        parent, configuration = cls._lock_parent_and_configuration(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
        cls._require_active_responsible_department(parent)
        cls._require_cycle_allows_draft_save(parent)
        cls._require_no_activity(parent)
        if not configuration or configuration.opened_at or configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.DRAFT:
            raise ValidationError("Historical or non-Draft configurations cannot return to cycle defaults.")
        cls._require_revision(configuration, expected_revision)
        if not return_questions_required_per_faculty and not return_final_item_count and not return_contribution_deadline:
            raise ValidationError("Select at least one override to return to the cycle default.")
        cycle = parent.cycle
        before = cls._configuration_payload(configuration)
        changes = {}
        if (
            return_questions_required_per_faculty
            and configuration.questions_required_per_faculty_source == "OVERRIDE"
        ):
            if cycle.default_questions_required_per_faculty is None:
                raise ValidationError("The cycle faculty quota default is not configured.")
            changes.update(
                questions_required_per_faculty=cycle.default_questions_required_per_faculty,
                questions_required_per_faculty_source="DEFAULT",
            )
        if (
            return_final_item_count
            and configuration.final_item_count_source == "OVERRIDE"
        ):
            if cycle.default_final_item_count is None:
                raise ValidationError("The cycle final item count default is not configured.")
            changes.update(
                final_item_count=cycle.default_final_item_count,
                final_item_count_source="DEFAULT",
            )
        if (
            return_contribution_deadline
            and configuration.contribution_deadline_source == "OVERRIDE"
        ):
            changes.update(
                contribution_deadline=cycle.default_contribution_deadline,
                contribution_deadline_source=(
                    "DEFAULT" if cycle.default_contribution_deadline is not None else None
                ),
            )
        if not changes:
            return configuration, False
        for field, value in changes.items():
            setattr(configuration, field, value)
        configuration.cycle_defaults_revision_snapshot = cycle.defaults_revision
        configuration.revision += 1
        configuration.full_clean()
        configuration.save()
        cls._audit(action="DE_EXAM_COURSE_OVERRIDES_REMOVED", parent=parent, configuration=configuration, actor=user, before=before, request=request, metadata={"expected_revision": expected_revision, "resulting_revision": configuration.revision})
        return configuration, True

    @classmethod
    @transaction.atomic
    def open_for_contribution(cls, *, cycle_course_id, tenant_id, user, expected_revision, request=None):
        parent, configuration = cls._lock_parent_and_configuration(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
        cls._require_active_responsible_department(parent)
        cls._require_cycle_open_for_workflow(parent)
        if not configuration:
            raise ValidationError("Configure this course examination before opening contribution.")
        if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
            return configuration, False
        cls._require_revision(configuration, expected_revision)
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=user, for_mutation=True)
        # Closed is eligible for a governed reopen, while Draft must be fully ready.
        non_status_blockers = [blocker for blocker in readiness["blockers"] if blocker != "Closed"]
        if non_status_blockers:
            raise ValidationError("Course examination is not ready to open: " + ", ".join(non_status_blockers))
        cls._require_no_activity(parent)
        before = cls._configuration_payload(configuration)
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.OPEN
        if not configuration.opened_at:
            configuration.opened_at = timezone.now()
            configuration.opened_by = user
            configuration.contributor_instructions_snapshot = parent.cycle.contributor_instructions
        configuration.closed_at = None
        configuration.closed_by = None
        configuration.revision += 1
        configuration.save(update_fields=["workflow_status", "opened_at", "opened_by", "closed_at", "closed_by", "contributor_instructions_snapshot", "revision", "updated_at"])
        # Stage 5 roster creation is part of the same atomic Open transition.
        # The import stays local to preserve the established Stage 1-4 service
        # dependency direction.
        from .contribution_services import ContributionRosterService

        ContributionRosterService.initialize_for_open_locked(
            cycle_course=parent,
            configuration=configuration,
            actor=user,
            request=request,
        )
        frozen_instructions = configuration.contributor_instructions_snapshot or ""
        cls._audit(
            action="DE_EXAM_COURSE_CONTRIBUTION_OPENED",
            parent=parent,
            configuration=configuration,
            actor=user,
            before=before,
            request=request,
            metadata={
                "expected_revision": expected_revision,
                "resulting_revision": configuration.revision,
                "reopened": bool(before["opened_at"]),
                "instruction_snapshot_copied": True,
                "contributor_instructions_snapshot_sha256": hashlib.sha256(
                    frozen_instructions.encode("utf-8")
                ).hexdigest(),
                "contributor_instructions_snapshot_length": len(frozen_instructions),
            },
        )
        return configuration, True

    @classmethod
    @transaction.atomic
    def close_contribution(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        user,
        expected_revision,
        expected_roster_revision,
        reason,
        request=None,
    ):
        parent, configuration = cls._lock_parent_and_configuration(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
        cls._require_active_responsible_department(parent)
        cls._require_cycle_open_for_workflow(parent)
        if not configuration:
            raise ValidationError("No course configuration exists.")
        if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED:
            return configuration, False
        cls._require_revision(configuration, expected_revision)
        if configuration.contributor_roster_revision != expected_roster_revision:
            raise CourseExamConfigurationConflict(
                "The contributor roster changed after the page was loaded."
            )
        if configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.OPEN:
            raise ValidationError("Only an open contribution can be closed.")
        reason = (reason or "").strip()
        if not 10 <= len(reason) <= 500:
            raise ValidationError("Reason must be from 10 to 500 characters.")
        if (
            configuration.final_item_count is None
            or not 50 <= configuration.final_item_count <= 75
            or configuration.questions_required_per_faculty is None
            or not 50 <= configuration.questions_required_per_faculty <= 75
            or not (configuration.coverage or "").strip()
            or configuration.contribution_deadline is None
            or (
                configuration.easy_percent,
                configuration.moderate_percent,
                configuration.difficult_percent,
            )
            != (30, 50, 20)
        ):
            raise ValidationError("The course configuration is not valid for contribution close.")
        # Parent-first locks serialize Close with roster, contribution, and
        # question writers. Submitted content is inspected but never changed.
        from .blueprint_services import ContributorRosterReadinessService
        from .models import FacultyContributionEligibilitySource

        list(
            FacultyContribution.objects.select_for_update()
            .filter(cycle_course=parent)
            .order_by("id")
        )
        list(
            FacultyContributionEligibilitySource.objects.select_for_update()
            .filter(contribution__cycle_course=parent)
            .order_by("id")
        )
        roster = ContributorRosterReadinessService.evaluate(
            cycle_course=parent, configuration=configuration
        )
        if not roster.current:
            raise CourseExamConfigurationConflict(
                "The contributor roster is stale. Synchronize it explicitly before closing contribution."
            )
        if roster.incomplete_active_count:
            raise ValidationError(
                f"{roster.incomplete_active_count} currently required Active contributor(s) must submit before close."
            )
        if roster.unresolved_blocked_count:
            raise ValidationError(
                f"{roster.unresolved_blocked_count} Blocked Draft contribution(s) require explicit resolution before close."
            )
        # Re-read the complete live and persisted evidence immediately before
        # closure. The configuration lock serializes roster synchronization;
        # the evidence hashes detect an assignment/eligibility drift observed
        # during this close transaction.
        confirmed_roster = ContributorRosterReadinessService.evaluate(
            cycle_course=parent, configuration=configuration
        )
        if (
            not confirmed_roster.current
            or confirmed_roster.boundary_sha256 != roster.boundary_sha256
            or confirmed_roster.live_sha256 != roster.live_sha256
        ):
            raise CourseExamConfigurationConflict(
                "The contributor roster changed while contribution close was being validated."
            )
        before = cls._configuration_payload(configuration)
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.CLOSED
        configuration.closed_at = timezone.now()
        configuration.closed_by = user
        configuration.revision += 1
        configuration.save(update_fields=["workflow_status", "closed_at", "closed_by", "revision", "updated_at"])
        cls._audit(
            action="DE_EXAM_COURSE_CONTRIBUTION_CLOSED",
            parent=parent,
            configuration=configuration,
            actor=user,
            before=before,
            request=request,
            metadata={
                "close_reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                "close_reason_length": len(reason),
                "expected_revision": expected_revision,
                "resulting_revision": configuration.revision,
                "required_active_contributors": roster.required_active_count,
                "submitted_required_contributors": roster.submitted_required_count,
                "resolved_blocked_drafts": roster.blocked_draft_count,
                "roster_revision": configuration.contributor_roster_revision,
                "roster_boundary_sha256": roster.boundary_sha256,
                "live_roster_validation_sha256": roster.live_sha256,
            },
        )
        return configuration, True

    @classmethod
    def reopen_contribution(cls, **kwargs):
        return cls.open_for_contribution(**kwargs)

    @classmethod
    @transaction.atomic
    def revert_unpublished_configuration(cls, *, cycle_course_id, tenant_id, user, expected_revision, reason, request=None):
        parent, configuration = cls._lock_parent_and_configuration(cycle_course_id=cycle_course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
        cls._require_active_responsible_department(parent)
        cls._require_cycle_open_for_workflow(parent)
        if not configuration:
            raise ValidationError("No course configuration exists.")
        cls._require_revision(configuration, expected_revision)
        cls._require_no_activity(parent)
        if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN:
            raise ValidationError("Close the contribution before reverting its configuration.")
        reason = (reason or "").strip()
        if not 10 <= len(reason) <= 500:
            raise ValidationError("Reason must be from 10 to 500 characters.")
        before = cls._configuration_payload(configuration)
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.DRAFT
        configuration.closed_at = None
        configuration.closed_by = None
        configuration.revision += 1
        configuration.save(update_fields=["workflow_status", "closed_at", "closed_by", "revision", "updated_at"])
        cls._audit(
            action="DE_EXAM_COURSE_CONFIGURATION_REVERTED",
            parent=parent,
            configuration=configuration,
            actor=user,
            before=before,
            request=request,
            metadata={
                "revert_reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                "revert_reason_length": len(reason),
                "expected_revision": expected_revision,
                "resulting_revision": configuration.revision,
            },
        )
        return configuration, True
