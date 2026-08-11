from unittest.mock import patch
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.http import Http404
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.academics.models import CourseOffering, Section
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.navigation.models import MenuItemPermission
from apps.tenants.models import Program

from .automatic_workflow import (
    AutomaticContributionReopenService,
    AutomaticExamDeadlineService,
    AutomaticGenerationSummaryService,
    AutomaticProcessingResult,
)
from .approval_services import ExamApprovalLockService
from .blueprint_services import (
    BlueprintMutationService,
    QuestionPlacementService,
    ScenarioMutationService,
    Stage6Conflict,
)
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamBlueprint,
    ExamGenerationRevision,
    ExamScenario,
    ExaminationCycle,
    FacultyContribution,
    Question,
    QuestionBlueprintPlacement,
)
from .stage4_test_support import Stage4TestCase
from .services import DepartmentalExamAuthorizationService
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class AutomaticWorkflowTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.generation_manager = self._make_generation_manager()

    def _make_generation_manager(self):
        user = get_user_model().objects.create_user(
            "automatic-generation-manager",
            "automatic-generation-manager@example.edu",
            "Pass123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="AUTO_GEN_MANAGER", name="Automatic Generation Manager")
        for code in (
            "admin_portal.access",
            "departmental_exams.view_generated_exams",
            "departmental_exams.print_generated_exams",
            "departmental_exams.manage_exam_generation",
        ):
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=None,
            department=None,
        )
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="admin_portal.access"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        return user

    def _make_generation_viewer(self):
        user = get_user_model().objects.create_user(
            "automatic-generation-viewer",
            "automatic-generation-viewer@example.edu",
            "Pass123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code="AUTO_GEN_VIEWER",
            name="Automatic Generation Viewer",
        )
        for code in (
            "admin_portal.access",
            "departmental_exams.view_generated_exams",
        ):
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=None,
            department=None,
        )
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="admin_portal.access"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        return user

    @staticmethod
    def _automatic(parent):
        cycle = parent.cycle
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parent.cycle = cycle
        return cycle

    def _ready_automatic_course(self, *, due=True, clear_manual_assignment=True):
        parent, problem = self.ready_generation_course()
        self._automatic(parent)
        if clear_manual_assignment:
            parent.responsible_department = None
            parent.reviewer = None
            parent.save(
                update_fields=["responsible_department", "reviewer", "updated_at"]
            )
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        if due:
            CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
                reopened_contribution_deadline=timezone.now()
                - timezone.timedelta(minutes=1)
            )
            configuration.refresh_from_db()
        return parent, configuration, problem

    def _process_with_proved_selection(self, *, parent, problem):
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(problem),
        ):
            return AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )

    def _ready_sectioned_automatic_course(self, *, include_scenario=False):
        parent, configuration, _contributions, _assignments, blueprint, sections = (
            self.prepare_sections()
        )
        placements = []
        for question in Question.objects.filter(
            contribution__cycle_course=parent
        ).order_by("contribution_id", "position"):
            section = sections[0] if question.position <= 20 else sections[1]
            placements.append(
                QuestionBlueprintPlacement(
                    blueprint=blueprint,
                    question=question,
                    section=section,
                    placed_by=self.reviewer,
                )
            )
        QuestionBlueprintPlacement.objects.bulk_create(placements)
        scenario = None
        if include_scenario:
            scenario_questions = list(
                Question.objects.filter(
                    contribution__cycle_course=parent,
                    blueprint_placement__section=sections[0],
                ).order_by("id")[:2]
            )
            scenario, _changed = ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Existing scenario",
                stimulus="Use the shared facts for both items.",
                question_ids=[question.id for question in scenario_questions],
                section_id=sections[0].id,
            )
        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self._automatic(parent)
        parent.responsible_department = None
        parent.reviewer = None
        parent.save(update_fields=["responsible_department", "reviewer", "updated_at"])
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        configuration.refresh_from_db()
        return parent, configuration, problem, blueprint, sections, scenario

    @staticmethod
    def _create_current_automatic_revision(*, parent, configuration, blueprint):
        return ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=1,
            status=ExamGenerationRevision.Status.GENERATED,
            current_marker=1,
            source_input_fingerprint="a" * 64,
            algorithm_version="test-current-input-guard",
            generated_by=None,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=configuration.revision,
            blueprint_revision_snapshot=blueprint.revision,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=configuration.final_item_count,
            request_token_digest="c" * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )

    def test_processing_mode_defaults_manual_and_automatic_is_explicit(self):
        cycle = self.make_cycle(scope_suffix="processing-default")
        self.assertEqual(
            cycle.processing_mode,
            ExaminationCycle.ProcessingMode.MANUAL_REVIEW,
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.full_clean()
        cycle.save(update_fields=["processing_mode", "updated_at"])
        cycle.refresh_from_db()
        self.assertEqual(
            cycle.processing_mode,
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
        )

    def test_automatic_permissions_are_seeded_and_navigation_linked(self):
        codes = {
            "departmental_exams.view_generated_exams",
            "departmental_exams.print_generated_exams",
            "departmental_exams.manage_exam_generation",
        }
        self.assertEqual(
            set(Permission.objects.filter(code__in=codes).values_list("code", flat=True)),
            codes,
        )
        self.assertEqual(
            set(
                MenuItemPermission.objects.filter(
                    menu_item__code="DE_EXAM_ASSIGNED_COURSES",
                    permission__code__in=codes,
                ).values_list("permission__code", flat=True)
            ),
            codes - {"departmental_exams.print_generated_exams"},
        )

    def test_before_deadline_skips_without_close_or_generation(self):
        parent, configuration, _campuses, _offerings = self.make_stage6_open_course()
        self._automatic(parent)
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        configuration.refresh_from_db()
        self.assertEqual(result.code, "NOT_DUE")
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertFalse(ExamGenerationRevision.objects.exists())

    def test_due_ready_course_without_department_or_reviewer_generates_once(self):
        parent, configuration, problem = self._ready_automatic_course()
        first = self._process_with_proved_selection(parent=parent, problem=problem)
        second = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        revision = ExamGenerationRevision.objects.get()
        self.assertEqual(first.status, "GENERATED")
        self.assertEqual(second.code, "CURRENT_GENERATION_EXISTS")
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(
            revision.generation_trigger,
            ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
        )
        self.assertIsNone(revision.generated_by)
        generation_audit = AuditLog.objects.get(
            action="DE_EXAM_GENERATED",
            entity_type="ExamGenerationRevision",
            entity_id=str(revision.id),
        )
        self.assertEqual(generation_audit.portal, "SYSTEM")
        self.assertIsNone(generation_audit.actor_user)
        configuration.refresh_from_db()
        self.assertEqual(
            configuration.automatic_processing_status,
            CourseExamConfiguration.AutomaticProcessingStatus.SKIPPED,
        )

    def test_due_incomplete_open_course_auto_closes_and_reports_blocker(self):
        parent, configuration, _campuses, _offerings = self.make_stage6_open_course()
        self._automatic(parent)
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            contribution_deadline=timezone.now() - timezone.timedelta(minutes=1)
        )
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        configuration.refresh_from_db()
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.CLOSED,
        )
        self.assertIn(
            result.code,
            {
                "ROSTER_STALE",
                "ACTIVE_CONTRIBUTORS_INCOMPLETE",
                "BLUEPRINT_MISSING",
            },
        )
        self.assertFalse(ExamGenerationRevision.objects.exists())

    def test_process_due_isolates_one_course_failure(self):
        error = RuntimeError("isolated failure")
        successful = AutomaticProcessingResult(
            22, "GENERATED", "GENERATED", "Set A and Set B generated.", 1
        )
        with patch(
            "apps.departmental_exams.automatic_workflow.CycleCourse.objects.filter"
        ) as queryset, patch.object(
            AutomaticExamDeadlineService,
            "process_course",
            side_effect=[error, successful],
        ), patch.object(
            AutomaticExamDeadlineService,
            "_record_error",
            side_effect=RuntimeError("secondary recording failure"),
        ), patch(
            "apps.departmental_exams.automatic_workflow.logger.error"
        ) as logger_error:
            queryset.return_value.values_list.return_value.order_by.return_value = [
                (11, self.tenant.id),
                (22, self.tenant.id),
            ]
            results = AutomaticExamDeadlineService.process_due()
        self.assertEqual([item.status for item in results], ["ERROR", "GENERATED"])
        self.assertEqual(logger_error.call_count, 2)

    def test_command_finishes_all_courses_and_exits_nonzero_on_error(self):
        results = [
            AutomaticProcessingResult(
                11,
                "ERROR",
                "RuntimeError",
                "Course processing failed; inspect the secured application log.",
            ),
            AutomaticProcessingResult(
                22,
                "GENERATED",
                "GENERATED",
                "Set A and Set B generated.",
                1,
            ),
        ]
        stdout = StringIO()
        with patch.object(
            AutomaticExamDeadlineService,
            "process_due",
            return_value=results,
        ), self.assertRaises(CommandError):
            call_command(
                "process_departmental_exam_deadlines",
                stdout=stdout,
            )
        output = stdout.getvalue()
        self.assertIn("course=11 status=ERROR code=RuntimeError", output)
        self.assertIn("course=22 status=GENERATED code=GENERATED R1", output)
        self.assertNotIn("isolated failure", output)

    def test_solver_failure_does_not_rollback_automatic_close(self):
        parent, configuration, _campuses, _offerings = self.make_stage6_open_course()
        self._automatic(parent)
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            contribution_deadline=timezone.now() - timezone.timedelta(minutes=1)
        )
        with patch.object(
            Stage6ReadinessService,
            "build_problem",
            side_effect=RuntimeError("solver preparation failed"),
        ), patch("apps.departmental_exams.automatic_workflow.logger.exception"):
            results = AutomaticExamDeadlineService.process_due()
        configuration.refresh_from_db()
        self.assertEqual(results[0].status, "ERROR")
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.CLOSED,
        )
        self.assertEqual(
            configuration.automatic_processing_status,
            CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
        )
        summary = AutomaticGenerationSummaryService.build(cycle=parent.cycle)
        self.assertEqual(
            summary["not_generated"][0]["reason"],
            "Automatic generation failed during processing.",
        )
        self.assertIn(
            "secured processor log",
            summary["not_generated"][0]["recommended_action"],
        )

    def test_automatic_regeneration_needs_no_reason_and_preserves_r1(self):
        parent, _configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        refreshed_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(refreshed_problem),
        ):
            outcome = ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_current_revision=1,
                expected_input_fingerprint=refreshed_problem.input_fingerprint,
                request_token="r" * 40,
                regeneration=True,
                regeneration_reason="",
            )
        r1, r2 = ExamGenerationRevision.objects.order_by("revision_number")
        self.assertEqual(outcome.revision, r2)
        self.assertEqual((r1.status, r1.current_marker), ("SUPERSEDED", None))
        self.assertEqual((r2.status, r2.current_marker), ("GENERATED", 1))
        self.assertEqual(r2.generated_by, self.generation_manager)
        self.assertEqual(r2.generation_trigger, "MANUAL")

    def test_reopen_supersedes_current_preserves_submissions_and_allows_fresh_r2(self):
        parent, configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        submitted_ids = set(
            FacultyContribution.objects.filter(
                cycle_course=parent,
                status=FacultyContribution.Status.SUBMITTED,
            ).values_list("id", flat=True)
        )
        reopened = AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
        )
        r1 = ExamGenerationRevision.objects.get(revision_number=1)
        self.assertEqual((r1.status, r1.current_marker), ("SUPERSEDED", None))
        self.assertEqual(reopened.workflow_status, "OPEN")
        self.assertEqual(
            set(
                FacultyContribution.objects.filter(
                    id__in=submitted_ids,
                    status=FacultyContribution.Status.SUBMITTED,
                ).values_list("id", flat=True)
            ),
            submitted_ids,
        )
        CourseExamConfiguration.objects.filter(pk=reopened.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        fresh_problem, _readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertIsNone(fresh_problem)
        # The processor closes first, so obtain the post-close problem inside
        # generation by returning a structurally valid selection for the same pool.
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            side_effect=lambda **kwargs: self.proved_selection(
                Stage6ReadinessService.build_problem(cycle_course=parent)[0]
            ),
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )
        r2 = ExamGenerationRevision.objects.get(revision_number=2)
        self.assertEqual(result.status, "GENERATED")
        self.assertEqual(r2.current_marker, 1)
        self.assertEqual(r2.supersedes, r1)

    def test_summary_is_generated_first_content_safe_and_scope_denies_win(self):
        parent, _configuration, problem = self._ready_automatic_course(
            clear_manual_assignment=False
        )
        self._process_with_proved_selection(parent=parent, problem=problem)
        client = Client()
        client.force_login(self.generation_manager)
        url = reverse(
            "departmental_exams:automatic_generation_summary",
            args=[parent.cycle_id],
        )
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(
            body.index("Questionnaires Generated"),
            body.index("Questionnaires Not Generated"),
        )
        self.assertNotIn("Question 1-1", body)
        self.assertNotIn("Correct answer:", body)

        denied_campus = parent.offering_snapshots.order_by("campus_id").first().campus
        for code in (
            "departmental_exams.view_generated_exams",
            "departmental_exams.manage_exam_generation",
        ):
            UserPermission.objects.create(
                user=self.generation_manager,
                permission=Permission.objects.get(code=code),
                grant_type=UserPermission.GrantType.DENY,
                tenant=self.tenant,
                campus=denied_campus,
            )
        self.assertEqual(client.get(url).status_code, 403)

    def test_configurer_alone_cannot_view_automatic_generated_content(self):
        parent, _configuration, problem = self._ready_automatic_course(
            clear_manual_assignment=False
        )
        result = self._process_with_proved_selection(parent=parent, problem=problem)
        client = Client()
        client.force_login(self.configurer)
        response = client.get(
            reverse(
                "departmental_exams:generated_revision_detail",
                args=[ExamGenerationRevision.objects.get(revision_number=result.generation_revision).id],
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_legacy_reviewer_and_inactive_automatic_role_cannot_view_content(self):
        parent, _configuration, problem = self._ready_automatic_course(
            clear_manual_assignment=False
        )
        self._process_with_proved_selection(parent=parent, problem=problem)
        revision = ExamGenerationRevision.objects.get()
        url = reverse("departmental_exams:generated_revision_detail", args=[revision.id])
        client = Client()
        client.force_login(self.reviewer)
        self.assertEqual(client.get(url).status_code, 403)
        confidential_inputs_url = reverse(
            "departmental_exams:blueprint_review",
            args=[parent.id],
        )
        self.assertEqual(client.get(confidential_inputs_url).status_code, 403)

        client.force_login(self.generation_manager)
        self.assertEqual(client.get(confidential_inputs_url).status_code, 200)

        UserRole.objects.filter(
            user=self.generation_manager,
            role__code="AUTO_GEN_MANAGER",
        ).update(is_active=False)
        client.force_login(self.generation_manager)
        self.assertEqual(client.get(url).status_code, 403)

    def test_generation_management_requires_every_participating_campus(self):
        parent, _configuration, _problem = self._ready_automatic_course(
            clear_manual_assignment=False
        )
        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            code="AUTO-NORTH",
            name="Automatic North",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            code="AUTO-NORTH",
            name="Automatic North",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            academic_year=parent.cycle.academic_year,
            term=parent.cycle.term,
            course=parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=offering,
            campus=self.other_campus,
        )
        main_only_manager = self.make_user(
            "main-only-generation-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
        )
        client = Client()
        client.force_login(main_only_manager)
        self.assertEqual(
            client.get(
                reverse(
                    "departmental_exams:automatic_generation_summary",
                    args=[parent.cycle_id],
                )
            ).status_code,
            403,
        )

    def test_wrong_tenant_revision_lookup_fails_closed(self):
        parent, _configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        revision = ExamGenerationRevision.objects.get()
        with self.assertRaises(Http404):
            ExamGenerationService.revision_for_tenant(
                revision_id=revision.id,
                tenant_id=self.other_tenant.id,
            )

    def test_feature_off_prevents_all_automatic_mutation_before_and_after_deadline(self):
        parent, configuration, _campuses, _offerings = self.make_stage6_open_course()
        self._automatic(parent)
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        audit_count = AuditLog.objects.count()
        before_deadline = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            contribution_deadline=timezone.now() - timezone.timedelta(minutes=1)
        )
        after_deadline = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        configuration.refresh_from_db()
        self.assertEqual(before_deadline.code, "FEATURE_DISABLED")
        self.assertEqual(after_deadline.code, "FEATURE_DISABLED")
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.OPEN,
        )
        self.assertEqual(configuration.automatic_processing_status, "")
        self.assertFalse(ExamGenerationRevision.objects.exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)

        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        enabled = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        configuration.refresh_from_db()
        self.assertNotEqual(enabled.code, "FEATURE_DISABLED")
        self.assertEqual(
            configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.CLOSED,
        )

    def test_current_r1_blocks_blueprint_placement_scenario_and_member_mutations(self):
        parent, configuration, _problem, blueprint, sections, scenario = (
            self._ready_sectioned_automatic_course(include_scenario=True)
        )
        self._create_current_automatic_revision(
            parent=parent,
            configuration=configuration,
            blueprint=blueprint,
        )
        placement = QuestionBlueprintPlacement.objects.filter(
            blueprint=blueprint
        ).exclude(question_id__in=scenario.members.values("question_id")).first()
        scenario_ids = list(
            scenario.members.order_by("position").values_list("question_id", flat=True)
        )
        section_payload = [
            {
                "id": section.id,
                "title": f"{section.title} changed",
                "instructions": section.instructions,
                "display_order": section.display_order,
                "item_quota": section.item_quota,
            }
            for section in sections
        ]
        mutations = (
            lambda: BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_revision=blueprint.revision,
                mode=ExamBlueprint.Mode.USE_SECTIONS,
                sections=section_payload,
            ),
            lambda: QuestionPlacementService.place(
                question_id=placement.question_id,
                section_id=(
                    sections[1].id
                    if placement.section_id == sections[0].id
                    else sections[0].id
                ),
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_placement_revision=placement.revision,
            ),
            lambda: ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                title="Changed scenario",
                stimulus="Changed shared facts.",
                question_ids=list(reversed(scenario_ids)),
                section_id=scenario.section_id,
                scenario_id=scenario.id,
                expected_revision=scenario.revision,
            ),
            lambda: ScenarioMutationService.delete(
                scenario_id=scenario.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_revision=scenario.revision,
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(Stage6Conflict):
                mutation()
        r1 = ExamGenerationRevision.objects.get(revision_number=1)
        self.assertEqual((r1.status, r1.current_marker), ("GENERATED", 1))
        blueprint.refresh_from_db()
        placement.refresh_from_db()
        scenario.refresh_from_db()
        self.assertEqual(blueprint.revision, 2)
        self.assertEqual(placement.revision, 1)
        self.assertEqual(
            list(scenario.members.order_by("position").values_list("question_id", flat=True)),
            scenario_ids,
        )

    def test_reopen_supersedes_r1_allows_input_change_and_generates_r2(self):
        parent, configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        reopened = AutomaticContributionReopenService.reopen(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=configuration.revision,
            new_deadline=timezone.now() + timezone.timedelta(days=1),
        )
        CourseExamConfiguration.objects.filter(pk=reopened.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        self.assertIsNone(
            AutomaticExamDeadlineService._close_due_intake(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                now=timezone.now(),
            )
        )
        blueprint = ExamBlueprint.objects.get(cycle_course=parent)
        blueprint, changed = BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=blueprint.revision,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=[
                {
                    "title": "Revised questionnaire",
                    "instructions": "Answer every item.",
                    "display_order": 1,
                    "item_quota": 50,
                }
            ],
        )
        self.assertTrue(changed)
        section = blueprint.sections.get()
        QuestionBlueprintPlacement.objects.bulk_create(
            [
                QuestionBlueprintPlacement(
                    blueprint=blueprint,
                    question=question,
                    section=section,
                    placed_by=self.generation_manager,
                )
                for question in Question.objects.filter(
                    contribution__cycle_course=parent
                ).order_by("id")
            ]
        )
        fresh_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(fresh_problem),
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )
        r1, r2 = ExamGenerationRevision.objects.order_by("revision_number")
        self.assertEqual(result.generation_revision, 2)
        self.assertEqual((r1.status, r1.current_marker), ("SUPERSEDED", None))
        self.assertEqual((r2.status, r2.current_marker), ("GENERATED", 1))
        self.assertEqual(r2.supersedes, r1)
        self.assertNotEqual(r1.source_input_fingerprint, r2.source_input_fingerprint)

    def test_null_department_blueprint_placement_and_scenario_audits_are_safe(self):
        parent, _configuration, _problem, blueprint, _sections, _scenario = (
            self._ready_sectioned_automatic_course()
        )
        audit_floor = AuditLog.objects.order_by("-id").values_list("id", flat=True).first() or 0
        blueprint.refresh_from_db()
        sections = list(blueprint.sections.order_by("display_order"))
        BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=blueprint.revision,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=[
                {
                    "id": section.id,
                    "title": f"{section.title} audited" if index == 0 else section.title,
                    "instructions": section.instructions,
                    "display_order": section.display_order,
                    "item_quota": section.item_quota,
                }
                for index, section in enumerate(sections)
            ],
        )
        existing_member_ids = ExamScenario.objects.filter(
            blueprint=blueprint
        ).values_list("members__question_id", flat=True)
        candidates = list(
            QuestionBlueprintPlacement.objects.filter(
                blueprint=blueprint,
                section=sections[0],
            )
            .exclude(question_id__in=existing_member_ids)
            .select_related("question")
            .order_by("question_id")[:4]
        )
        placement = candidates[0]
        QuestionPlacementService.place(
            question_id=placement.question_id,
            section_id=sections[1].id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_placement_revision=placement.revision,
        )
        scenario, _changed = ScenarioMutationService.save(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            title="Null department scenario",
            stimulus="Use this null-department scenario.",
            question_ids=[row.question_id for row in candidates[1:3]],
            section_id=sections[0].id,
        )
        scenario, _changed = ScenarioMutationService.save(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            title="Null department scenario updated",
            stimulus="Use this updated null-department scenario.",
            question_ids=[row.question_id for row in reversed(candidates[1:4])],
            section_id=sections[0].id,
            scenario_id=scenario.id,
            expected_revision=scenario.revision,
        )
        ScenarioMutationService.delete(
            scenario_id=scenario.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
            expected_revision=scenario.revision,
        )
        audits = list(
            AuditLog.objects.filter(
                id__gt=audit_floor,
                action__in=(
                    "DE_EXAM_BLUEPRINT_UPDATED",
                    "DE_EXAM_QUESTION_PLACED",
                    "DE_EXAM_SCENARIO_CREATED",
                    "DE_EXAM_SCENARIO_UPDATED",
                    "DE_EXAM_SCENARIO_DELETED",
                ),
            )
        )
        self.assertEqual(len(audits), 5)
        self.assertTrue(all(audit.tenant_id == self.tenant.id for audit in audits))
        self.assertTrue(all(audit.campus_id is None for audit in audits))

    def test_automatic_revision_is_rejected_by_approval_service_before_lock(self):
        parent, _configuration, problem = self._ready_automatic_course()
        self._process_with_proved_selection(parent=parent, problem=problem)
        revision = ExamGenerationRevision.objects.get()
        with self.assertRaises(PermissionDenied):
            ExamApprovalLockService.approve_and_lock(
                revision_id=revision.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_revision_number=revision.revision_number,
                expected_source_input_fingerprint=revision.source_input_fingerprint,
            )
        revision.refresh_from_db()
        self.assertEqual((revision.status, revision.current_marker), ("GENERATED", 1))
        self.assertIsNone(revision.locked_at)

    def test_viewer_current_only_manager_history_and_rendered_actions_match_rbac(self):
        viewer = self._make_generation_viewer()
        parent, _configuration, problem = self._ready_automatic_course(
            clear_manual_assignment=False
        )
        self._process_with_proved_selection(parent=parent, problem=problem)
        fresh_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(fresh_problem),
        ):
            ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
                expected_current_revision=1,
                expected_input_fingerprint=fresh_problem.input_fingerprint,
                request_token="v" * 40,
                regeneration=True,
            )
        r1, r2 = ExamGenerationRevision.objects.order_by("revision_number")
        client = Client()
        client.force_login(viewer)
        current_url = reverse(
            "departmental_exams:generated_revision_detail", args=[r2.id]
        )
        historical_url = reverse(
            "departmental_exams:generated_revision_detail", args=[r1.id]
        )
        current_response = client.get(current_url)
        self.assertEqual(current_response.status_code, 200)
        self.assertNotContains(current_response, "Regenerate")
        self.assertNotContains(current_response, "Confidential revision history")
        self.assertEqual(client.get(historical_url).status_code, 403)

        assigned = client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertContains(assigned, "View Current Generation")
        self.assertNotContains(assigned, "Generation Actions")
        summary = client.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[parent.cycle_id],
            )
        )
        self.assertEqual(summary.status_code, 200)
        self.assertContains(summary, "View Current Generation")
        self.assertNotContains(summary, "Regenerate Set A &amp; Set B", html=True)
        self.assertNotContains(summary, "Reopen Contributions")

        client.force_login(self.generation_manager)
        manager_history = client.get(historical_url)
        self.assertEqual(manager_history.status_code, 200)
        self.assertContains(manager_history, "Confidential revision history")
        manager_current = client.get(current_url)
        self.assertContains(manager_current, "Regenerate")

        permission = Permission.objects.get(
            code="departmental_exams.view_generated_exams"
        )
        denied_campus = parent.offering_snapshots.order_by("campus_id").first().campus
        UserPermission.objects.create(
            user=viewer,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=denied_campus,
        )
        client.force_login(viewer)
        self.assertEqual(client.get(current_url).status_code, 403)

        partial_viewer = self.make_user(
            "partial-automatic-viewer",
            self.department,
            ("admin_portal.access", "departmental_exams.view_generated_exams"),
        )
        client.force_login(partial_viewer)
        self.assertEqual(client.get(current_url).status_code, 403)

        viewer.default_tenant = self.other_tenant
        viewer.save(update_fields=["default_tenant", "updated_at"])
        client.force_login(viewer)
        self.assertIn(client.get(current_url).status_code, (403, 404))

        viewer.default_tenant = self.tenant
        viewer.is_active = False
        viewer.save(update_fields=["default_tenant", "is_active", "updated_at"])
        client.force_login(viewer)
        self.assertIn(client.get(current_url).status_code, (302, 403))

    def test_query_bound_for_multiple_draft_courses_and_campuses(self):
        def make_draft_cycle(*, suffix, count):
            cycle = self.make_cycle(
                status=ExaminationCycle.Status.OPEN,
                default_questions_required_per_faculty=50,
                default_final_item_count=50,
                default_contribution_deadline=self.future_deadline(),
                scope_suffix=suffix,
            )
            cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            cycle.save(update_fields=["processing_mode", "updated_at"])
            for index in range(count):
                parent = self.make_course(cycle=cycle, code=f"QUERY-{suffix}-{index}")
                self.make_configuration(parent)
                program = Program.objects.create(
                    tenant=self.tenant,
                    campus=self.other_campus,
                    department=self.other_department,
                    code=f"QP-{suffix}-{index}",
                    name=f"Query Program {suffix} {index}",
                )
                section = Section.objects.create(
                    tenant=self.tenant,
                    campus=self.other_campus,
                    department=self.other_department,
                    program=program,
                    code=f"QS-{suffix}-{index}",
                    name=f"Query Section {suffix} {index}",
                )
                offering = CourseOffering.objects.create(
                    tenant=self.tenant,
                    campus=self.other_campus,
                    department=self.other_department,
                    program=program,
                    academic_year=cycle.academic_year,
                    term=cycle.term,
                    course=parent.course,
                    section=section,
                )
                CycleCourseOffering.objects.create(
                    cycle_course=parent,
                    offering=offering,
                    campus=self.other_campus,
                )
            return cycle

        one = make_draft_cycle(suffix="query-one", count=1)
        many = make_draft_cycle(suffix="query-many", count=4)
        with CaptureQueriesContext(connection) as one_queries:
            AutomaticGenerationSummaryService.build(cycle=one)
        with CaptureQueriesContext(connection) as many_queries:
            many_summary = AutomaticGenerationSummaryService.build(cycle=many)
        self.assertLessEqual(len(many_queries), len(one_queries) + 1)
        self.assertEqual(
            many_summary["not_generated"][0]["recommended_action"],
            "Open contributions / complete course configuration.",
        )

        one_courses = list(
            CycleCourse.objects.filter(cycle=one)
            .select_related("cycle")
            .prefetch_related("offering_snapshots")
        )
        many_courses = list(
            CycleCourse.objects.filter(cycle=many)
            .select_related("cycle")
            .prefetch_related("offering_snapshots")
        )
        permission_codes = (
            DepartmentalExamAuthorizationService.VIEW_GENERATED_PERMISSION,
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        )
        with CaptureQueriesContext(connection) as legacy_many_queries:
            for course in many_courses:
                DepartmentalExamAuthorizationService.has_automatic_course_permission(
                    user=self.generation_manager,
                    cycle_course=course,
                    permissions=permission_codes,
                )
        with CaptureQueriesContext(connection) as set_many_queries:
            permission_map = (
                DepartmentalExamAuthorizationService.automatic_permission_map(
                    user=self.generation_manager,
                    courses=many_courses,
                    permissions=permission_codes,
                )
            )
        self.assertTrue(
            all(
                DepartmentalExamAuthorizationService.ANY_AUTOMATIC_PERMISSION
                in permission_map[course.id]
                for course in many_courses
            )
        )
        self.assertLess(len(set_many_queries), len(legacy_many_queries))
        print(
            "QUERY_BOUND "
            f"summary_one={len(one_queries)} summary_many={len(many_queries)} "
            f"legacy_auth_many={len(legacy_many_queries)} "
            f"set_auth_many={len(set_many_queries)}"
        )
