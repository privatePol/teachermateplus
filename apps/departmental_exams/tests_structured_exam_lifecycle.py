import threading
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .blueprint_services import (
    BlueprintMutationService,
    Stage6Conflict,
    StructuredExamLifecyclePolicy,
)
from .exam_units import ExamCourseEquivalencyService
from .models import (
    CourseExamConfiguration,
    ExamBlueprint,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamSection,
    ExaminationCycle,
)
from .services import (
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase, Stage4TransactionTestCase


class StructuredExamLifecycleTests(Stage4TestCase):
    def _enable_structured(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

    def _draft_course(self, *, code="STRUCT", processing_mode=None):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Structured outcomes",
        )
        if processing_mode is not None:
            cycle.processing_mode = processing_mode
            cycle.save(update_fields=["processing_mode", "updated_at"])
        parent = self.make_course(cycle=cycle, code=code)
        configuration = self.make_configuration(parent)
        return cycle, parent, configuration

    def _sections(self):
        return (
            {
                "title": "Part I - Problems",
                "instructions": "",
                "display_order": 1,
                "item_quota": 30,
            },
            {
                "title": "Part II - Theories",
                "instructions": "",
                "display_order": 2,
                "item_quota": 20,
            },
        )

    def _save_sections(self, parent, *, actor=None):
        return BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=actor or self.configurer,
            expected_revision=0,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=self._sections(),
        )[0]

    def _open(self, parent, configuration, *, actor=None):
        return CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=actor or self.configurer,
            expected_revision=configuration.revision,
        )[0]

    def _automatic_unit(self, *, size=2, final_source="OVERRIDE"):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Shared structured outcomes",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parents = tuple(
            self.make_course(cycle=cycle, code=f"STRUCT-EQ-{offset}")
            for offset in range(size)
        )
        deadline = self.future_deadline()
        configurations = tuple(
            self.make_configuration(
                parent,
                deadline=deadline,
                final_source=final_source,
            )
            for parent in parents
        )
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Structured shared examination",
            primary_cycle_course_id=parents[0].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )
        return cycle, group, parents, configurations

    def _independent_automatic_courses(self, *, size=2):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Independent structured outcomes",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parents = tuple(
            self.make_course(cycle=cycle, code=f"STRUCT-INDEPENDENT-{offset}")
            for offset in range(size)
        )
        deadline = self.future_deadline()
        configurations = tuple(
            self.make_configuration(parent, deadline=deadline)
            for parent in parents
        )
        return cycle, parents, configurations

    def _save_cycle_final_default(self, *, cycle, value):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.admin,
            expected_updated_at=(
                ExaminationCycleConfigurationService.transition_token(cycle)
            ),
            default_questions_required_per_faculty=(
                cycle.default_questions_required_per_faculty
            ),
            default_final_item_count=value,
            default_contribution_deadline=cycle.default_contribution_deadline,
            default_coverage=cycle.default_coverage,
            contributor_instructions=cycle.contributor_instructions,
            processing_mode=cycle.processing_mode,
            automatic_campus_contribution_policy=(
                cycle.automatic_campus_contribution_policy
            ),
            automatic_contributor_completion_policy=(
                cycle.automatic_contributor_completion_policy
            ),
            reason="Keep the cycle defaults operationally aligned.",
        )

    def test_feature_defaults_off_and_legacy_open_remains_blueprint_free(self):
        _cycle, parent, configuration = self._draft_course(code="LEGACY")

        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant.id
            )
        )
        opened = self._open(parent, configuration)

        self.assertEqual(
            opened.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=parent).exists())

    def test_feature_off_preserves_automatic_flat_open_without_blueprint(self):
        _cycle, parent, configuration = self._draft_course(
            code="AUTO-FLAT",
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
        )

        opened = self._open(parent, configuration, actor=self.admin)

        self.assertEqual(
            opened.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=parent).exists())

    def test_feature_on_allows_valid_preopen_structure_and_audits_nonconfidential_metadata(self):
        self._enable_structured()
        _cycle, parent, _configuration = self._draft_course()

        blueprint = self._save_sections(parent)

        self.assertEqual(blueprint.mode, ExamBlueprint.Mode.USE_SECTIONS)
        self.assertEqual(
            list(
                blueprint.sections.order_by("display_order").values_list(
                    "title", "item_quota"
                )
            ),
            [("Part I - Problems", 30), ("Part II - Theories", 20)],
        )
        audit = AuditLog.objects.get(action="DE_EXAM_BLUEPRINT_CREATED")
        self.assertEqual(audit.metadata_json["blueprint_revision"], 1)
        self.assertEqual(audit.metadata_json["section_count"], 2)
        self.assertNotIn("Part I - Problems", str(audit.metadata_json))

    def test_incomplete_structured_blueprint_blocks_open_without_mutation(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        ExamBlueprint.objects.create(
            cycle_course=parent,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            created_by=self.configurer,
            updated_by=self.configurer,
        )

        with self.assertRaisesRegex(ValidationError, "at least one valid section"):
            self._open(parent, configuration)

        configuration.refresh_from_db()
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.DRAFT,
        )
        self.assertIsNone(configuration.opened_at)

    def test_valid_structure_allows_open_and_permanently_freezes_it(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        blueprint = self._save_sections(parent)

        opened = self._open(parent, configuration)
        blueprint.refresh_from_db()

        self.assertEqual(
            opened.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertIsNotNone(blueprint.structure_frozen_at)
        self.assertEqual(blueprint.structure_frozen_by, self.configurer)
        self.assertEqual(blueprint.structure_final_item_count, 50)
        audit = AuditLog.objects.get(action="DE_EXAM_BLUEPRINT_FROZEN")
        self.assertEqual(audit.metadata_json["blueprint_revision"], 1)
        self.assertEqual(audit.metadata_json["section_quota_total"], 50)
        self.assertNotIn("Part I - Problems", str(audit.metadata_json))

    def test_admin_configuration_and_blueprint_pages_expose_preopen_then_frozen_state(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        self.client.force_login(self.configurer)

        configuration_page = self.client.get(
            reverse("departmental_exams:course_configuration", args=[parent.id])
        )
        blueprint_page = self.client.get(
            reverse("departmental_exams:blueprint_configuration", args=[parent.id])
        )

        self.assertEqual(configuration_page.status_code, 200)
        self.assertContains(configuration_page, "Configure Exam Structure / Blueprint")
        self.assertEqual(blueprint_page.status_code, 200)
        self.assertContains(blueprint_page, "before opening faculty contribution")

        self._save_sections(parent)
        self._open(parent, configuration)
        frozen_page = self.client.get(
            reverse("departmental_exams:blueprint_configuration", args=[parent.id])
        )
        self.assertEqual(frozen_page.status_code, 200)
        self.assertContains(frozen_page, "FROZEN")
        self.assertContains(frozen_page, "Reopening contribution later does not unfreeze")

    def test_open_freeze_rejects_section_and_mode_mutation_through_supported_and_orm_paths(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        blueprint = self._save_sections(parent)
        self._open(parent, configuration)
        blueprint.refresh_from_db()
        sections = list(blueprint.sections.order_by("display_order"))

        with self.assertRaisesRegex(Stage6Conflict, "never opened"):
            BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
                expected_revision=blueprint.revision,
                mode=ExamBlueprint.Mode.USE_SECTIONS,
                sections=self._sections(),
            )

        for field, value in (
            ("title", "Changed title"),
            ("display_order", 3),
            ("item_quota", 29),
        ):
            section = ExamSection.objects.get(pk=sections[0].pk)
            setattr(section, field, value)
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
                    section.save(update_fields=[field])

        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.create(
                blueprint=blueprint,
                title="Part III",
                display_order=3,
                item_quota=1,
            )
        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.filter(pk=sections[0].pk).update(item_quota=29)
        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.get(pk=sections[0].pk).delete()

        blueprint.mode = ExamBlueprint.Mode.NO_SECTIONS
        with self.assertRaisesRegex(ValidationError, "Frozen exam structure"):
            blueprint.save(update_fields=["mode"])

    def test_reopen_does_not_unfreeze_structure(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        blueprint = self._save_sections(parent)
        configuration = self._open(parent, configuration)
        frozen_at = ExamBlueprint.objects.get(pk=blueprint.pk).structure_frozen_at

        configuration, _changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="No contributor obligations exist for this lifecycle test.",
        )
        configuration, _changed = CourseExamConfigurationService.reopen_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
        )
        blueprint.refresh_from_db()

        self.assertEqual(blueprint.structure_frozen_at, frozen_at)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_BLUEPRINT_FROZEN").count(),
            1,
        )
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )

    def test_incompatible_final_item_count_change_is_rejected_after_freeze(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course()
        self._save_sections(parent)
        configuration = self._open(parent, configuration)

        with self.assertRaisesRegex(ValidationError, "Only Draft configurations"):
            CourseExamConfigurationService.save_course_draft(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                user=self.configurer,
                expected_revision=configuration.revision,
                questions_required_per_faculty=50,
                questions_required_per_faculty_mode="OVERRIDE",
                final_item_count=60,
                final_item_count_mode="OVERRIDE",
                coverage="Structured outcomes",
                additional_instructions="",
                contribution_deadline=configuration.contribution_deadline,
            )
        configuration.final_item_count = 60
        with self.assertRaisesRegex(ValidationError, "incompatible"):
            configuration.save(update_fields=["final_item_count"])
        configuration.refresh_from_db()
        self.assertEqual(configuration.final_item_count, 50)

    def test_tenant_campus_department_and_direct_deny_boundaries_remain_fail_closed(self):
        self._enable_structured()
        _cycle, parent, _configuration = self._draft_course()
        other_scope = self.make_user(
            "structured-other-scope",
            self.other_department,
            ("admin_portal.access", "departmental_exams.configure"),
            campus=self.other_campus,
        )

        with self.assertRaises(Http404):
            BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.other_tenant.id,
                actor=self.configurer,
                expected_revision=0,
                mode=ExamBlueprint.Mode.USE_SECTIONS,
                sections=self._sections(),
            )
        with self.assertRaises(PermissionDenied):
            self._save_sections(parent, actor=other_scope)

        UserPermission.objects.create(
            user=self.configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._save_sections(parent)
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=parent).exists())

    def test_equivalent_alias_uses_only_the_authoritative_primary_blueprint(self):
        self._enable_structured()
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Shared structured outcomes",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        primary = self.make_course(cycle=cycle, code="STRUCT-P")
        secondary = self.make_course(cycle=cycle, code="STRUCT-S")
        deadline = self.future_deadline()
        primary_configuration = self.make_configuration(primary, deadline=deadline)
        secondary_configuration = self.make_configuration(secondary, deadline=deadline)
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Structured shared examination",
            primary_cycle_course_id=primary.id,
            member_ids=(primary.id, secondary.id),
            actor=self.admin,
        )

        blueprint = self._save_sections(secondary, actor=self.admin)

        self.assertEqual(blueprint.cycle_course_id, primary.id)
        self.assertFalse(
            ExamBlueprint.objects.filter(cycle_course=secondary).exists()
        )
        self.assertEqual(primary_configuration.final_item_count, 50)
        self.assertEqual(secondary_configuration.final_item_count, 50)

    def test_frozen_equivalency_primary_membership_and_retirement_are_immutable(self):
        self._enable_structured()
        _cycle, group, parents, configurations = self._automatic_unit(size=3)
        blueprint = self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)

        with self.assertRaisesRegex(ValidationError, "ownership cannot change"):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=parents[1].id,
                member_ids=tuple(parent.id for parent in parents),
                actor=self.admin,
            )
        with self.assertRaisesRegex(ValidationError, "ownership cannot change"):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=parents[0].id,
                member_ids=(parents[0].id, parents[1].id),
                actor=self.admin,
            )
        with self.assertRaisesRegex(ValidationError, "ownership cannot change"):
            ExamCourseEquivalencyService.retire_group(
                group_id=group.id,
                actor=self.admin,
                reason="This frozen ownership must remain permanent.",
            )

        group.refresh_from_db()
        blueprint.refresh_from_db()
        self.assertTrue(group.is_active)
        self.assertEqual(group.primary_cycle_course_id, parents[0].id)
        self.assertEqual(
            set(
                ExamCourseEquivalencyMembership.objects.filter(
                    group=group,
                    active_marker=1,
                ).values_list("cycle_course_id", flat=True)
            ),
            {parent.id for parent in parents},
        )
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)
        self.assertEqual(
            ExamBlueprint.objects.filter(cycle_course__in=parents).count(),
            1,
        )

    def test_frozen_blueprint_fk_attnames_cannot_bypass_lifecycle_guards(self):
        self._enable_structured()
        _cycle, _group, parents, configurations = self._automatic_unit(size=2)
        blueprint = self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)
        blueprint.refresh_from_db()
        original_actor_ids = {
            "structure_frozen_by_id": blueprint.structure_frozen_by_id,
            "created_by_id": blueprint.created_by_id,
            "updated_by_id": blueprint.updated_by_id,
        }

        blueprint.cycle_course_id = parents[1].id
        with self.assertRaisesRegex(ValidationError, "ownership cannot be changed"):
            blueprint.save(update_fields=["cycle_course_id"])
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)

        for field_name, original_value in original_actor_ids.items():
            setattr(blueprint, field_name, self.configurer.id)
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    blueprint.save(update_fields=[field_name])
            blueprint.refresh_from_db()
            self.assertEqual(getattr(blueprint, field_name), original_value)

        frozen_at = blueprint.structure_frozen_at
        blueprint.updated_at = timezone.now()
        blueprint.save(update_fields=["updated_at"])
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)
        self.assertEqual(blueprint.structure_frozen_at, frozen_at)

    def test_multiple_unfrozen_blueprints_block_equivalency_creation_without_rewrite(self):
        self._enable_structured()
        cycle, parents, _configurations = self._independent_automatic_courses()
        for parent in parents:
            self._save_sections(parent, actor=self.admin)
        before = list(
            ExamBlueprint.objects.filter(cycle_course__in=parents)
            .order_by("cycle_course_id")
            .values(
                "id",
                "cycle_course_id",
                "mode",
                "revision",
                "structure_frozen_at",
            )
        )

        with self.assertRaisesRegex(
            ValidationError, "multiple independently configured exam structures"
        ):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Invalid duplicate structures",
                primary_cycle_course_id=parents[0].id,
                member_ids=tuple(parent.id for parent in parents),
                actor=self.admin,
            )

        self.assertFalse(
            ExamCourseEquivalencyGroup.objects.filter(cycle=cycle).exists()
        )
        self.assertEqual(
            list(
                ExamBlueprint.objects.filter(cycle_course__in=parents)
                .order_by("cycle_course_id")
                .values(
                    "id",
                    "cycle_course_id",
                    "mode",
                    "revision",
                    "structure_frozen_at",
                )
            ),
            before,
        )

    def test_sole_alias_owned_blueprint_blocks_equivalency_creation_without_rewrite(self):
        self._enable_structured()
        cycle, parents, _configurations = self._independent_automatic_courses()
        blueprint = self._save_sections(parents[0], actor=self.admin)

        with self.assertRaisesRegex(
            ValidationError, "does not belong to the proposed authoritative primary"
        ):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Invalid sole alias-owned structure",
                primary_cycle_course_id=parents[1].id,
                member_ids=tuple(parent.id for parent in parents),
                actor=self.admin,
            )

        self.assertFalse(
            ExamCourseEquivalencyGroup.objects.filter(cycle=cycle).exists()
        )
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)

    def test_sole_primary_owned_blueprint_allows_equivalency_creation(self):
        self._enable_structured()
        cycle, parents, _configurations = self._independent_automatic_courses()
        blueprint = self._save_sections(parents[0], actor=self.admin)

        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Valid sole primary-owned structure",
            primary_cycle_course_id=parents[0].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )

        self.assertTrue(group.is_active)
        self.assertEqual(group.primary_cycle_course_id, parents[0].id)
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)

    def test_multiple_unfrozen_blueprints_block_member_replacement_without_rewrite(self):
        self._enable_structured()
        cycle, parents, _configurations = self._independent_automatic_courses(
            size=3
        )
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Initial unconfigured unit",
            primary_cycle_course_id=parents[0].id,
            member_ids=(parents[0].id, parents[1].id),
            actor=self.admin,
        )
        self._save_sections(parents[0], actor=self.admin)
        self._save_sections(parents[2], actor=self.admin)
        before = list(
            ExamBlueprint.objects.filter(cycle_course__in=parents)
            .order_by("cycle_course_id")
            .values("id", "cycle_course_id", "mode", "revision")
        )

        with self.assertRaisesRegex(
            ValidationError, "multiple independently configured exam structures"
        ):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=parents[0].id,
                member_ids=(parents[0].id, parents[2].id),
                actor=self.admin,
            )

        self.assertEqual(
            set(
                ExamCourseEquivalencyMembership.objects.filter(
                    group=group,
                    active_marker=1,
                ).values_list("cycle_course_id", flat=True)
            ),
            {parents[0].id, parents[1].id},
        )
        self.assertEqual(
            list(
                ExamBlueprint.objects.filter(cycle_course__in=parents)
                .order_by("cycle_course_id")
                .values("id", "cycle_course_id", "mode", "revision")
            ),
            before,
        )

    def test_sole_blueprint_blocks_primary_replacement_without_rewrite(self):
        self._enable_structured()
        _cycle, group, parents, _configurations = self._automatic_unit(size=2)
        blueprint = self._save_sections(parents[0], actor=self.admin)
        before_members = set(
            ExamCourseEquivalencyMembership.objects.filter(
                group=group,
                active_marker=1,
            ).values_list("cycle_course_id", flat=True)
        )

        with self.assertRaisesRegex(
            ValidationError, "does not belong to the proposed authoritative primary"
        ):
            ExamCourseEquivalencyService.replace_members(
                group_id=group.id,
                primary_cycle_course_id=parents[1].id,
                member_ids=tuple(parent.id for parent in parents),
                actor=self.admin,
            )

        group.refresh_from_db()
        blueprint.refresh_from_db()
        self.assertEqual(group.primary_cycle_course_id, parents[0].id)
        self.assertEqual(
            set(
                ExamCourseEquivalencyMembership.objects.filter(
                    group=group,
                    active_marker=1,
                ).values_list("cycle_course_id", flat=True)
            ),
            before_members,
        )
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)

    def test_preexisting_alias_blueprint_blocks_first_open_without_rewrite(self):
        self._enable_structured()
        cycle, parents, configurations = self._independent_automatic_courses()
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Legacy inconsistent structures",
            primary_cycle_course_id=parents[0].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )
        self._save_sections(parents[0], actor=self.admin)
        ExamBlueprint.objects.create(
            cycle_course=parents[1],
            mode=ExamBlueprint.Mode.NO_SECTIONS,
            created_by=self.admin,
            updated_by=self.admin,
        )
        before = list(
            ExamBlueprint.objects.filter(cycle_course__in=parents)
            .order_by("cycle_course_id")
            .values(
                "id",
                "cycle_course_id",
                "mode",
                "revision",
                "structure_frozen_at",
                "structure_frozen_by_id",
                "structure_final_item_count",
            )
        )

        with self.assertRaisesRegex(
            ValidationError, "Administrative reconciliation is required"
        ):
            self._open(parents[0], configurations[0], actor=self.admin)

        for configuration in configurations:
            configuration.refresh_from_db()
            self.assertEqual(
                configuration.workflow_status,
                CourseExamConfiguration.WorkflowStatus.DRAFT,
            )
            self.assertIsNone(configuration.opened_at)
        self.assertEqual(
            list(
                ExamBlueprint.objects.filter(cycle_course__in=parents)
                .order_by("cycle_course_id")
                .values(
                    "id",
                    "cycle_course_id",
                    "mode",
                    "revision",
                    "structure_frozen_at",
                    "structure_frozen_by_id",
                    "structure_final_item_count",
                )
            ),
            before,
        )

    def test_single_primary_blueprint_equivalency_unit_opens_normally(self):
        self._enable_structured()
        cycle, parents, configurations = self._independent_automatic_courses()
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Valid structured unit",
            primary_cycle_course_id=parents[0].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )
        blueprint = self._save_sections(parents[1], actor=self.admin)

        opened = self._open(parents[1], configurations[1], actor=self.admin)
        blueprint.refresh_from_db()

        self.assertEqual(
            opened.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertEqual(blueprint.cycle_course_id, parents[0].id)
        self.assertIsNotNone(blueprint.structure_frozen_at)
        self.assertEqual(
            ExamBlueprint.objects.filter(cycle_course__in=parents).count(),
            1,
        )

    def test_unconfigured_equivalency_merge_remains_allowed(self):
        cycle, parents, _configurations = self._independent_automatic_courses()

        group = ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Legacy unconfigured unit",
            primary_cycle_course_id=parents[0].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )

        self.assertTrue(group.is_active)
        self.assertEqual(
            set(
                ExamCourseEquivalencyMembership.objects.filter(
                    group=group,
                    active_marker=1,
                ).values_list("cycle_course_id", flat=True)
            ),
            {parent.id for parent in parents},
        )
        self.assertFalse(
            ExamBlueprint.objects.filter(cycle_course__in=parents).exists()
        )

    def test_independently_frozen_units_cannot_be_merged(self):
        self._enable_structured()
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Independent structured outcomes",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parents = tuple(
            self.make_course(cycle=cycle, code=f"FROZEN-{offset}")
            for offset in range(2)
        )
        deadline = self.future_deadline()
        configurations = tuple(
            self.make_configuration(parent, deadline=deadline)
            for parent in parents
        )
        self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)
        self._save_sections(parents[1], actor=self.admin)

        with self.assertRaisesRegex(ValidationError, "ownership cannot change"):
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.id,
                name="Invalid frozen merge",
                primary_cycle_course_id=parents[0].id,
                member_ids=tuple(parent.id for parent in parents),
                actor=self.admin,
            )

        self.assertFalse(
            ExamCourseEquivalencyGroup.objects.filter(cycle=cycle).exists()
        )
        self.assertEqual(
            set(
                ExamBlueprint.objects.filter(cycle_course__in=parents)
                .values_list("cycle_course_id", flat=True)
            ),
            {parent.id for parent in parents},
        )
        self.assertEqual(
            ExamBlueprint.objects.filter(
                cycle_course__in=parents,
                structure_frozen_at__isnull=False,
            ).count(),
            1,
        )

    def test_unfrozen_equivalency_replacement_preserves_legacy_behavior(self):
        _cycle, group, parents, _configurations = self._automatic_unit(size=2)

        updated = ExamCourseEquivalencyService.replace_members(
            group_id=group.id,
            primary_cycle_course_id=parents[1].id,
            member_ids=tuple(parent.id for parent in parents),
            actor=self.admin,
        )

        self.assertEqual(updated.primary_cycle_course_id, parents[1].id)
        self.assertFalse(
            ExamBlueprint.objects.filter(cycle_course__in=parents).exists()
        )

    def test_incompatible_cycle_default_rolls_back_frozen_unit_and_unrelated_course(self):
        self._enable_structured()
        cycle, _group, parents, configurations = self._automatic_unit(
            size=2,
            final_source="DEFAULT",
        )
        unrelated = self.make_course(cycle=cycle, code="UNRELATED")
        unrelated_configuration = self.make_configuration(
            unrelated,
            final_source="DEFAULT",
        )
        self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)

        with self.assertRaisesRegex(ValidationError, "propagation would conflict"):
            self._save_cycle_final_default(cycle=cycle, value=60)

        cycle.refresh_from_db()
        for configuration in (*configurations, unrelated_configuration):
            configuration.refresh_from_db()
            self.assertEqual(configuration.final_item_count, 50)
        self.assertEqual(cycle.default_final_item_count, 50)

    def test_unrelated_default_tracking_continues_when_frozen_unit_does_not_track(self):
        self._enable_structured()
        cycle, _group, parents, configurations = self._automatic_unit(
            size=2,
            final_source="DEFAULT",
        )
        unrelated = self.make_course(cycle=cycle, code="UNRELATED-TRACKING")
        unrelated_configuration = self.make_configuration(
            unrelated,
            final_source="DEFAULT",
        )
        self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)
        configurations[1].final_item_count_source = (
            CourseExamConfiguration.ValueSource.OVERRIDE
        )
        configurations[1].save(update_fields=["final_item_count_source"])

        updated_cycle, changed = self._save_cycle_final_default(
            cycle=cycle,
            value=60,
        )

        self.assertTrue(changed)
        self.assertEqual(updated_cycle.default_final_item_count, 60)
        for configuration in configurations:
            configuration.refresh_from_db()
            self.assertEqual(configuration.final_item_count, 50)
        unrelated_configuration.refresh_from_db()
        self.assertEqual(unrelated_configuration.final_item_count, 60)

    def test_unfrozen_equivalency_members_continue_tracking_cycle_default(self):
        cycle, _group, _parents, configurations = self._automatic_unit(
            size=2,
            final_source="DEFAULT",
        )

        updated_cycle, changed = self._save_cycle_final_default(
            cycle=cycle,
            value=60,
        )

        self.assertTrue(changed)
        self.assertEqual(updated_cycle.default_final_item_count, 60)
        for configuration in configurations:
            configuration.refresh_from_db()
            self.assertEqual(configuration.final_item_count, 60)

    def test_alias_bulk_final_count_writes_cannot_bypass_frozen_invariant(self):
        self._enable_structured()
        _cycle, _group, parents, configurations = self._automatic_unit(size=2)
        self._save_sections(parents[0], actor=self.admin)
        self._open(parents[0], configurations[0], actor=self.admin)
        alias_configuration = configurations[1]
        alias_configuration.final_item_count = 60

        with self.assertRaisesRegex(ValidationError, "protected cycle-default"):
            CourseExamConfiguration.objects.bulk_update(
                [alias_configuration],
                ["final_item_count"],
            )
        with self.assertRaisesRegex(ValidationError, "protected cycle-default"):
            CourseExamConfiguration.objects.filter(
                pk=alias_configuration.pk
            ).update(final_item_count=60)
        with self.assertRaisesRegex(ValidationError, "incompatible"):
            alias_configuration.save(update_fields=["final_item_count"])

        alias_configuration.refresh_from_db()
        self.assertEqual(alias_configuration.final_item_count, 50)

    def test_frozen_structure_rejects_every_direct_queryset_mutation_path(self):
        self._enable_structured()
        _cycle, parent, configuration = self._draft_course(code="ORM-FROZEN")
        blueprint = self._save_sections(parent)
        self._open(parent, configuration)
        blueprint.refresh_from_db()
        section = blueprint.sections.order_by("id").first()

        with self.assertRaisesRegex(ValidationError, "Frozen exam structure"):
            ExamBlueprint.objects.filter(pk=blueprint.pk).update(revision=2)
        blueprint.revision = 2
        with self.assertRaisesRegex(ValidationError, "Frozen exam structure"):
            ExamBlueprint.objects.bulk_update([blueprint], ["revision"])
        with self.assertRaisesRegex(ValidationError, "Frozen exam structure"):
            ExamBlueprint.objects.filter(pk=blueprint.pk).delete()

        section.item_quota = 29
        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.bulk_update([section], ["item_quota"])
        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.filter(pk=section.pk).delete()
        with self.assertRaisesRegex(ValidationError, "Frozen exam sections"):
            ExamSection.objects.bulk_create(
                [
                    ExamSection(
                        blueprint=blueprint,
                        title="Invalid section",
                        display_order=3,
                        item_quota=1,
                    )
                ]
            )

    def test_unfrozen_direct_queryset_mutations_remain_supported(self):
        self._enable_structured()
        _cycle, parent, _configuration = self._draft_course(code="ORM-DRAFT")
        blueprint = self._save_sections(parent)
        section = blueprint.sections.order_by("id").first()

        self.assertEqual(
            ExamBlueprint.objects.filter(pk=blueprint.pk).update(revision=2),
            1,
        )
        blueprint.refresh_from_db()
        blueprint.revision = 3
        ExamBlueprint.objects.bulk_update([blueprint], ["revision"])
        section.title = "Updated before Open"
        ExamSection.objects.bulk_update([section], ["title"])
        self.assertEqual(
            ExamSection.objects.filter(pk=section.pk).update(item_quota=29),
            1,
        )
        self.assertEqual(ExamSection.objects.filter(pk=section.pk).delete()[0], 1)

    def test_bulk_create_cannot_fabricate_blueprint_freeze_evidence(self):
        _cycle, parent, _configuration = self._draft_course(code="ORM-BULK")
        fabricated = ExamBlueprint(
            cycle_course=parent,
            mode=ExamBlueprint.Mode.NO_SECTIONS,
            created_by=self.configurer,
            updated_by=self.configurer,
            structure_frozen_at=timezone.now(),
            structure_frozen_by=self.configurer,
            structure_final_item_count=50,
        )

        with self.assertRaisesRegex(ValidationError, "freeze evidence"):
            ExamBlueprint.objects.bulk_create([fabricated])

        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=parent).exists())


class StructuredExamLifecycleConcurrencyTests(Stage4TransactionTestCase):
    def _structured_fixture(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Concurrent structured outcomes",
        )
        parent = self.make_course(cycle=cycle, code="STRUCT-CONCURRENT")
        configuration = self.make_configuration(parent)
        blueprint = BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_revision=0,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=(
                {
                    "title": "Part I",
                    "instructions": "",
                    "display_order": 1,
                    "item_quota": 30,
                },
                {
                    "title": "Part II",
                    "instructions": "",
                    "display_order": 2,
                    "item_quota": 20,
                },
            ),
        )[0]
        return parent, configuration, blueprint

    def _assert_open_wins_against_stale_mutation(self, *, target):
        parent, configuration, blueprint = self._structured_fixture()
        target_id = (
            blueprint.id
            if target == "blueprint"
            else blueprint.sections.order_by("id").first().id
        )
        stale_loaded = threading.Event()
        freeze_written = threading.Event()
        mutation_started = threading.Event()
        mutation_done = threading.Event()
        release_open = threading.Event()
        open_errors = []
        mutation_errors = []
        original_freeze = StructuredExamLifecyclePolicy.freeze

        def holding_freeze(**kwargs):
            result = original_freeze(**kwargs)
            freeze_written.set()
            if not release_open.wait(timeout=20):
                raise AssertionError("Timed out waiting to commit the Open transition.")
            return result

        def open_worker():
            close_old_connections()
            try:
                if not stale_loaded.wait(timeout=20):
                    raise AssertionError("Mutation worker did not load stale state.")
                actor = get_user_model().objects.get(pk=self.configurer.id)
                with patch.object(
                    StructuredExamLifecyclePolicy,
                    "freeze",
                    side_effect=holding_freeze,
                ):
                    CourseExamConfigurationService.open_for_contribution(
                        cycle_course_id=parent.id,
                        tenant_id=self.tenant.id,
                        user=actor,
                        expected_revision=configuration.revision,
                    )
            except Exception as exc:  # pragma: no cover - asserted by parent thread
                open_errors.append(exc)
                release_open.set()
                freeze_written.set()
            finally:
                close_old_connections()

        def mutation_worker():
            close_old_connections()
            try:
                if target == "blueprint":
                    stale = ExamBlueprint.objects.get(pk=target_id)
                else:
                    stale = ExamSection.objects.get(pk=target_id)
                stale_loaded.set()
                if not freeze_written.wait(timeout=20):
                    raise AssertionError("Open worker did not write freeze evidence.")
                mutation_started.set()
                if target == "blueprint":
                    stale.mode = ExamBlueprint.Mode.NO_SECTIONS
                    stale.save(update_fields=["mode"])
                else:
                    stale.title = "Stale concurrent mutation"
                    stale.save(update_fields=["title"])
            except Exception as exc:  # pragma: no cover - asserted by parent thread
                mutation_errors.append(exc)
            finally:
                mutation_done.set()
                close_old_connections()

        mutation_thread = threading.Thread(target=mutation_worker)
        open_thread = threading.Thread(target=open_worker)
        mutation_thread.start()
        self.assertTrue(stale_loaded.wait(timeout=20))
        open_thread.start()
        self.assertTrue(freeze_written.wait(timeout=20))
        self.assertTrue(mutation_started.wait(timeout=20))
        self.assertFalse(
            mutation_done.wait(timeout=0.25),
            "The stale mutation completed before the Open transaction committed.",
        )
        release_open.set()
        open_thread.join(timeout=20)
        mutation_thread.join(timeout=20)
        self.assertFalse(open_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual(open_errors, [])
        self.assertEqual(len(mutation_errors), 1)
        self.assertIsInstance(mutation_errors[0], ValidationError)
        self.assertIn("Frozen exam", str(mutation_errors[0]))
        blueprint.refresh_from_db()
        self.assertIsNotNone(blueprint.structure_frozen_at)
        if target == "blueprint":
            self.assertEqual(blueprint.mode, ExamBlueprint.Mode.USE_SECTIONS)
        else:
            section = ExamSection.objects.get(pk=target_id)
            self.assertEqual(section.title, "Part I")

    @skipUnless(
        connection.vendor == "mysql"
        and getattr(connection, "mysql_is_mariadb", False),
        "This proof requires MariaDB/InnoDB; SQLite cannot prove row-lock scheduling.",
    )
    def test_open_blocks_then_rejects_stale_blueprint_mutation_on_mariadb(self):
        self.assertTrue(connection.features.has_select_for_update)
        self._assert_open_wins_against_stale_mutation(target="blueprint")

    @skipUnless(
        connection.vendor == "mysql"
        and getattr(connection, "mysql_is_mariadb", False),
        "This proof requires MariaDB/InnoDB; SQLite cannot prove row-lock scheduling.",
    )
    def test_open_blocks_then_rejects_stale_section_mutation_on_mariadb(self):
        self.assertTrue(connection.features.has_select_for_update)
        self._assert_open_wins_against_stale_mutation(target="section")
