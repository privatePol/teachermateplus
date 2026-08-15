from unittest.mock import patch
from io import StringIO
from collections import Counter
from time import perf_counter

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.http import Http404
from django.test import Client, override_settings
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
from .contribution_services import QuestionPayloadService
from .generation_algorithms import (
    FeasibilityResult,
    IdentityBlock,
    IdentityMember,
    IdentitySelectionResult,
    proportional_campus_difficulty_score,
    solve_automatic_identity_aware_two_sets,
    solve_identity_aware_two_sets,
)
from .generation_readiness import (
    Stage6ReadinessService,
    resolve_automatic_generation_max_states,
)
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
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
    QuestionBlueprintPlacement,
)
from .stage4_test_support import Stage4TestCase
from .services import (
    DepartmentalExamAuthorizationService,
    ExaminationCycleConfigurationService,
)
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

    def _proved_selection_for(self, *, parent, problem):
        if (
            parent.cycle.processing_mode
            != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            return self.proved_selection(problem)
        return solve_automatic_identity_aware_two_sets(
            margins=problem.margins,
            blocks=problem.blocks,
            campus_quotas=problem.campus_quotas,
            difficulty_quotas=problem.difficulty_quotas,
            secret=settings.SECRET_KEY,
            hmac_context={"test_selection": "automatic"},
            max_states=ExamGenerationService.AUTOMATIC_DEFAULT_MAX_STATES,
        )

    def _process_with_proved_selection(self, *, parent, problem):
        current_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._proved_selection_for(parent=parent, problem=current_problem)
        self.assertTrue(selection.feasible, selection)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            return AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )

    @staticmethod
    def _identical_pool_selection(problem):
        blocks = tuple(problem.blocks)
        cell_counts = {}
        contributor_counts = {}
        for block in blocks:
            for member in block.members:
                cell = (member.campus, member.difficulty)
                cell_counts[cell] = cell_counts.get(cell, 0) + 1
                contributor_counts[member.contributor_id] = (
                    contributor_counts.get(member.contributor_id, 0) + 2
                )
        per_set_score = proportional_campus_difficulty_score(
            total=problem.final_count,
            campus_quotas=problem.campus_quotas,
            difficulty_quotas=problem.difficulty_quotas,
            cell_counts=cell_counts,
        )
        return IdentitySelectionResult(
            feasible=True,
            limit_hit=False,
            states_explored=1,
            set_a_block_ids=tuple(block.block_id for block in blocks),
            set_b_block_ids=tuple(block.block_id for block in blocks),
            overlap=problem.final_count,
            proportional_score=per_set_score * 2,
            contributors_represented=len(contributor_counts),
            squared_contributor_concentration=sum(
                amount * amount for amount in contributor_counts.values()
            ),
        )

    @staticmethod
    def _set_automatic_policies(
        parent,
        *,
        campus_policy=None,
        contributor_policy=None,
    ):
        cycle = parent.cycle
        fields = ["updated_at"]
        if campus_policy is not None:
            cycle.automatic_campus_contribution_policy = campus_policy
            fields.append("automatic_campus_contribution_policy")
        if contributor_policy is not None:
            cycle.automatic_contributor_completion_policy = contributor_policy
            fields.append("automatic_contributor_completion_policy")
        cycle.save(update_fields=fields)
        parent.cycle = cycle

    def _replace_automatic_question_rows(self, *, parent, rows):
        contributions = {
            contribution.source_campus_id: contribution
            for contribution in FacultyContribution.objects.filter(
                cycle_course=parent,
                status=FacultyContribution.Status.SUBMITTED,
            )
        }
        Question.objects.filter(contribution__cycle_course=parent).delete()
        positions = Counter()
        questions = []
        for sequence, (question_text, campus_id, difficulty) in enumerate(
            rows, start=1
        ):
            contribution = contributions[campus_id]
            positions[contribution.id] += 1
            questions.append(
                Question(
                    contribution=contribution,
                    question_text=question_text,
                    choice_a=f"A-{sequence}",
                    choice_b=f"B-{sequence}",
                    choice_c=f"C-{sequence}",
                    choice_d=f"D-{sequence}",
                    correct_answer="A",
                    difficulty=difficulty,
                    position=positions[contribution.id],
                    revision=1,
                )
            )
        Question.objects.bulk_create(questions)

    def _automatic_52_row_counterexample(self, *, parent, variant):
        campus_ids = list(
            parent.offering_snapshots.order_by("campus_id").values_list(
                "campus_id", flat=True
            )
        )
        campus_a, campus_b, campus_c = campus_ids
        base_campuses = [campus_a] * 16 + [campus_b] * 16 + [campus_c] * 16
        base_difficulties = (
            [Question.Difficulty.EASY] * 14
            + [Question.Difficulty.MODERATE] * 24
            + [Question.Difficulty.DIFFICULT] * 10
        )
        rows = [
            (f"Counterexample unique logical question {index}", campus_id, difficulty)
            for index, (campus_id, difficulty) in enumerate(
                zip(base_campuses, base_difficulties), start=1
            )
        ]
        if variant == "joint":
            rows.extend(
                [
                    ("Counterexample flexible logical question", campus_a, Question.Difficulty.EASY),
                    ("Counterexample flexible logical question", campus_b, Question.Difficulty.MODERATE),
                    ("Counterexample fixed logical question", campus_a, Question.Difficulty.EASY),
                    ("Counterexample fixed logical question", campus_a, Question.Difficulty.EASY),
                ]
            )
        elif variant == "campus":
            rows.extend(
                [
                    ("Campus flexible logical question", campus_a, Question.Difficulty.EASY),
                    ("Campus flexible logical question", campus_b, Question.Difficulty.EASY),
                    ("Campus fixed logical question", campus_a, Question.Difficulty.MODERATE),
                    ("Campus fixed logical question", campus_a, Question.Difficulty.MODERATE),
                ]
            )
        elif variant == "difficulty":
            rows.extend(
                [
                    ("Difficulty flexible logical question", campus_b, Question.Difficulty.EASY),
                    ("Difficulty flexible logical question", campus_b, Question.Difficulty.MODERATE),
                    ("Difficulty fixed logical question", campus_a, Question.Difficulty.EASY),
                    ("Difficulty fixed logical question", campus_a, Question.Difficulty.EASY),
                ]
            )
        else:
            raise ValueError("Unknown Automatic counterexample variant.")
        self._replace_automatic_question_rows(parent=parent, rows=rows)
        return campus_ids

    def _assert_automatic_selection_margins(self, problem):
        selection = self._proved_selection_for(
            parent=problem.cycle_course,
            problem=problem,
        )
        self.assertTrue(selection.feasible, selection)
        self.assertFalse(selection.limit_hit, selection)
        ExamGenerationService._validate_selection(
            problem=problem,
            selection=selection,
        )
        blocks = {str(block.block_id): block for block in problem.blocks}
        for selected_ids in (
            selection.set_a_block_ids,
            selection.set_b_block_ids,
        ):
            members = [
                member
                for block_id in selected_ids
                for member in blocks[str(block_id)].members
            ]
            self.assertEqual(
                Counter(member.campus for member in members),
                Counter(problem.campus_quotas),
            )
            self.assertEqual(
                Counter(member.difficulty for member in members),
                Counter(problem.difficulty_quotas),
            )
        return selection

    @staticmethod
    def _retain_one_participating_campus(parent):
        retained = (
            parent.offering_snapshots.select_related("campus")
            .order_by("campus_id", "id")
            .first()
        )
        CycleCourseOffering.objects.filter(cycle_course=parent).exclude(
            pk=retained.pk
        ).delete()
        Question.objects.filter(contribution__cycle_course=parent).exclude(
            contribution__source_campus_id=retained.campus_id
        ).delete()
        return retained

    def _four_active_two_submitted_course(self):
        parent, configuration, _problem = self._ready_automatic_course()
        draft = (
            FacultyContribution.objects.filter(cycle_course=parent)
            .order_by("id")
            .last()
        )
        FacultyContribution.objects.filter(pk=draft.pk).update(
            status=FacultyContribution.Status.DRAFT,
            submitted_at=None,
        )
        snapshot = (
            parent.offering_snapshots.select_related("campus", "offering")
            .order_by("campus_id", "id")
            .first()
        )
        faculty, assignment = self.add_faculty_source(
            parent=parent,
            campus=snapshot.campus,
            offering=snapshot.offering,
            suffix="fourth-active",
        )
        extra = FacultyContribution.objects.create(
            cycle_course=parent,
            faculty_user=faculty,
            source_assignment=assignment,
            source_campus=snapshot.campus,
            quota_snapshot=configuration.questions_required_per_faculty,
            configuration_revision_snapshot=configuration.revision,
            roster_status=FacultyContribution.RosterStatus.ACTIVE,
            status=FacultyContribution.Status.DRAFT,
        )
        FacultyContributionEligibilitySource.objects.create(
            contribution=extra,
            assignment=assignment,
            assignment_id_snapshot=assignment.id,
            offering_id_snapshot=assignment.offering_id,
            tenant_id_snapshot=assignment.tenant_id,
            campus_id_snapshot=assignment.campus_id,
            is_current=True,
        )
        return parent

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

    def test_summary_shows_clear_waiting_and_processing_states(self):
        parent, _problem = self.ready_generation_course()
        self._automatic(parent)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            workflow_status=CourseExamConfiguration.WorkflowStatus.OPEN,
            reopened_contribution_deadline=timezone.now()
            + timezone.timedelta(days=1),
        )
        Question.objects.filter(contribution__cycle_course=parent).update(
            question_text="Repeated pre-deadline question text"
        )
        blocked_report = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertIn(
            "UNIQUE_QUESTION_SHORTAGES",
            {blocker["code"] for blocker in blocked_report["blockers"]},
        )

        waiting = AutomaticGenerationSummaryService.build(cycle=parent.cycle)
        self.assertEqual(
            waiting["not_generated"][0]["status"],
            "All contributions submitted — waiting for deadline",
        )
        self.assertNotIn("Ready", waiting["not_generated"][0]["status"])
        self.assertEqual(
            waiting["not_generated"][0]["recommended_action"],
            "Monitor faculty contributions until the deadline.",
        )

        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1),
        )
        pending = AutomaticGenerationSummaryService.build(cycle=parent.cycle)
        self.assertEqual(
            pending["not_generated"][0]["status"],
            "Automatic processing",
        )
        self.assertEqual(
            pending["not_generated"][0]["recommended_action"],
            "No admin action is needed; automatic processing is pending.",
        )

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

    def test_automatic_single_campus_exact_pool_generates_with_distinct_deterministic_set_orders(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        retained = self._retain_one_participating_campus(parent)

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(problem.final_count, 50)
        self.assertEqual(len(problem.questions), 50)
        self.assertEqual(problem.campus_quotas, {retained.campus_id: 50})
        selection = self._identical_pool_selection(problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            first = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )

        revision = ExamGenerationRevision.objects.get(cycle_course=parent)
        set_a = list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=revision,
                generated_set__set_code=GeneratedExamSet.SetCode.A,
            )
            .order_by("position")
            .values_list("source_question_id", flat=True)
        )
        set_b = list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=revision,
                generated_set__set_code=GeneratedExamSet.SetCode.B,
            )
            .order_by("position")
            .values_list("source_question_id", flat=True)
        )
        second = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )

        self.assertEqual(first.status, "GENERATED")
        self.assertEqual(second.code, "CURRENT_GENERATION_EXISTS")
        self.assertEqual(set(set_a), set(set_b))
        self.assertNotEqual(set_a, set_b)
        self.assertEqual(
            set_b,
            list(
                GeneratedExamItem.objects.filter(
                    generated_set__generation_revision=revision,
                    generated_set__set_code=GeneratedExamSet.SetCode.B,
                )
                .order_by("position")
                .values_list("source_question_id", flat=True)
            ),
        )

    def test_available_with_warning_uses_represented_campuses_and_records_warning(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        missing_snapshot = (
            parent.offering_snapshots.select_related("campus")
            .order_by("campus_id", "id")
            .last()
        )
        Question.objects.filter(
            contribution__cycle_course=parent,
            contribution__source_campus_id=missing_snapshot.campus_id,
        ).delete()

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        warnings = {item["code"]: item for item in readiness["warnings"]}
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertIn("MISSING_CAMPUS_REPRESENTATION", warnings)
        self.assertIn(missing_snapshot.campus.name, warnings["MISSING_CAMPUS_REPRESENTATION"]["message"])
        self.assertNotIn(missing_snapshot.campus_id, problem.campus_quotas)

        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        summary = AutomaticGenerationSummaryService.build(cycle=parent.cycle)

        self.assertEqual(result.status, "GENERATED")
        self.assertIn(
            "MISSING_CAMPUS_REPRESENTATION",
            {item["code"] for item in summary["generated"][0]["warnings"]},
        )

    def test_strict_policy_preserves_missing_campus_blocker(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        missing_snapshot = (
            parent.offering_snapshots.select_related("campus")
            .order_by("campus_id", "id")
            .last()
        )
        Question.objects.filter(
            contribution__cycle_course=parent,
            contribution__source_campus_id=missing_snapshot.campus_id,
        ).delete()
        self._set_automatic_policies(
            parent,
            campus_policy=(
                ExaminationCycle.AutomaticCampusContributionPolicy.STRICT
            ),
        )

        _problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertFalse(readiness["ready"])
        self.assertIn(
            "MISSING_CAMPUS_REPRESENTATION",
            {item["code"] for item in readiness["blockers"]},
        )
        self.assertFalse(readiness["warnings"])

    def test_sufficient_pool_uses_submitted_questions_and_warns_for_two_of_four_active_contributors(self):
        parent = self._four_active_two_submitted_course()

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        warnings = {item["code"]: item for item in readiness["warnings"]}
        draft_question_ids = set(
            Question.objects.filter(
                contribution__cycle_course=parent,
                contribution__status=FacultyContribution.Status.DRAFT,
            ).values_list("id", flat=True)
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(
            {
                "required": warnings["ACTIVE_CONTRIBUTORS_INCOMPLETE"]["required"],
                "submitted": warnings["ACTIVE_CONTRIBUTORS_INCOMPLETE"]["submitted"],
            },
            {"required": 4, "submitted": 2},
        )
        self.assertFalse(draft_question_ids.intersection(problem.questions))

        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )

        self.assertEqual(result.status, "GENERATED")

    def test_require_all_preserves_contributor_incomplete_blocker(self):
        parent = self._four_active_two_submitted_course()
        self._set_automatic_policies(
            parent,
            contributor_policy=(
                ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL
            ),
        )

        _problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertFalse(readiness["ready"])
        self.assertIn(
            "ACTIVE_CONTRIBUTORS_INCOMPLETE",
            {item["code"] for item in readiness["blockers"]},
        )

    def test_cycle_automatic_policies_are_draft_only(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.DRAFT)
        updated, changed = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(
                cycle
            ),
            default_questions_required_per_faculty=(
                cycle.default_questions_required_per_faculty
            ),
            default_final_item_count=cycle.default_final_item_count,
            contributor_instructions=cycle.contributor_instructions,
            processing_mode=cycle.processing_mode,
            automatic_campus_contribution_policy=(
                ExaminationCycle.AutomaticCampusContributionPolicy.STRICT
            ),
            automatic_contributor_completion_policy=(
                ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL
            ),
        )
        self.assertTrue(changed)
        self.assertEqual(
            updated.automatic_campus_contribution_policy,
            ExaminationCycle.AutomaticCampusContributionPolicy.STRICT,
        )
        self.assertEqual(
            updated.automatic_contributor_completion_policy,
            ExaminationCycle.AutomaticContributorCompletionPolicy.REQUIRE_ALL,
        )
        updated.status = ExaminationCycle.Status.OPEN
        updated.save(update_fields=["status", "updated_at"])
        with self.assertRaisesRegex(ValidationError, "Draft"):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=updated.id,
                tenant_id=self.tenant.id,
                user=self.manager,
                expected_updated_at=(
                    ExaminationCycleConfigurationService.transition_token(updated)
                ),
                default_questions_required_per_faculty=(
                    updated.default_questions_required_per_faculty
                ),
                default_final_item_count=updated.default_final_item_count,
                contributor_instructions=updated.contributor_instructions,
                processing_mode=updated.processing_mode,
                automatic_campus_contribution_policy=(
                    ExaminationCycle.AutomaticCampusContributionPolicy
                    .AVAILABLE_WITH_WARNING
                ),
                automatic_contributor_completion_policy=(
                    updated.automatic_contributor_completion_policy
                ),
                reason="A valid Open-cycle administrative reason.",
            )

    def test_manual_generation_does_not_apply_automatic_set_b_rotation(self):
        parent, problem = self.ready_generation_course()
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(problem),
        ) as manual_solver, patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets"
        ) as automatic_solver, patch(
            "apps.departmental_exams.generation_services.confidential_hmac_rank"
        ) as rotation_rank:
            outcome = ExamGenerationService.generate(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_current_revision=0,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="m" * 40,
            )

        self.assertFalse(outcome.reused)
        self.assertEqual(
            manual_solver.call_args.kwargs["max_states"],
            ExamGenerationService.DEFAULT_MAX_STATES,
        )
        automatic_solver.assert_not_called()
        rotation_rank.assert_not_called()

    @override_settings(DEPARTMENTAL_EXAM_GENERATION_MAX_STATES=123_456)
    def test_automatic_readiness_and_generation_share_configured_state_budget(self):
        parent, _configuration, _problem = self._ready_automatic_course()

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            wraps=solve_automatic_identity_aware_two_sets,
        ) as readiness_solver, patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            wraps=solve_automatic_identity_aware_two_sets,
        ) as generation_solver:
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )

        self.assertEqual(result.status, "GENERATED")
        self.assertGreaterEqual(readiness_solver.call_count, 2)
        self.assertEqual(
            {
                call.kwargs["max_states"]
                for call in readiness_solver.call_args_list
            },
            {123_456},
        )
        self.assertEqual(generation_solver.call_count, 1)
        self.assertEqual(
            generation_solver.call_args.kwargs["max_states"],
            123_456,
        )
        self.assertEqual(GeneratedExamSet.objects.count(), 2)
        self.assertEqual(GeneratedExamItem.objects.count(), 100)

    @override_settings(DEPARTMENTAL_EXAM_GENERATION_MAX_STATES=1)
    def test_automatic_very_low_state_budget_blocks_readiness_consistently(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        questions = list(
            Question.objects.filter(contribution__cycle_course=parent).order_by("id")
        )
        for question in questions:
            question.question_text = (
                f"State-limit logical question {question.position}"
            )
        Question.objects.bulk_update(questions, ["question_text"])

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            wraps=solve_automatic_identity_aware_two_sets,
        ) as readiness_solver, patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            wraps=solve_automatic_identity_aware_two_sets,
        ) as generation_solver:
            problem, readiness = Stage6ReadinessService.build_problem(
                cycle_course=parent
            )
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )

        self.assertIsNone(problem)
        self.assertTrue(readiness["solver_limit_hit"])
        self.assertIn(
            "FEASIBILITY_LIMIT",
            {blocker["code"] for blocker in readiness["blockers"]},
        )
        self.assertEqual(readiness["unique_question_count"], 50)
        self.assertEqual(readiness["duplicate_question_count"], 100)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.code, "FEASIBILITY_LIMIT")
        self.assertEqual(
            {
                call.kwargs["max_states"]
                for call in readiness_solver.call_args_list
            },
            {1},
        )
        generation_solver.assert_not_called()
        self.assertFalse(ExamGenerationRevision.objects.exists())

    def test_automatic_without_blueprint_builds_flat_problem_and_generates_sets(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        ExamBlueprint.objects.filter(cycle_course=parent).delete()

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertNotIn(
            "BLUEPRINT_MISSING",
            {blocker["code"] for blocker in readiness["blockers"]},
        )
        self.assertIsNone(problem.blueprint)
        self.assertEqual(problem.section_quotas, {0: problem.final_count})
        self.assertEqual(problem.section_order, (0,))
        self.assertEqual(readiness["scenario_count"], 0)

        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        revision = ExamGenerationRevision.objects.get()

        self.assertEqual(result.status, "GENERATED")
        self.assertEqual(revision.generated_sets.count(), 2)
        self.assertEqual(
            sum(row.items.count() for row in revision.generated_sets.all()),
            problem.final_count * 2,
        )
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=parent).exists())
        for generated_set in revision.generated_sets.all():
            self.assertEqual(
                generated_set.section_quotas_snapshot,
                {"0": problem.final_count},
            )
            self.assertFalse(
                generated_set.items.exclude(
                    source_section__isnull=True,
                    section_id_snapshot__isnull=True,
                    section_title_snapshot="Questionnaire",
                    scenario_id_snapshot__isnull=True,
                ).exists()
            )

    def test_automatic_flat_generation_retains_one_logical_duplicate_question(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        duplicate_source, duplicate_row = list(
            Question.objects.filter(contribution__cycle_course=parent)
            .order_by("id")[:2]
        )
        Question.objects.filter(pk=duplicate_row.pk).update(
            question_text=duplicate_source.question_text
        )

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(readiness["submitted_question_count"], 150)
        self.assertEqual(readiness["unique_question_count"], 149)
        self.assertEqual(readiness["duplicate_question_count"], 1)
        self.assertIn(duplicate_source.id, problem.questions)
        self.assertIn(duplicate_row.id, problem.questions)
        duplicate_blocks = [
            block
            for block in problem.blocks
            if block.members[0].source_id in {duplicate_source.id, duplicate_row.id}
        ]
        self.assertEqual(len(duplicate_blocks), 2)
        self.assertEqual(
            len({block.logical_group_id for block in duplicate_blocks}),
            1,
        )
        self.assertIn(
            "REDUNDANT_DUPLICATE_QUESTIONS",
            {item["code"] for item in readiness["warnings"]},
        )
        self._process_with_proved_selection(parent=parent, problem=problem)
        for generated_set in ExamGenerationRevision.objects.get().generated_sets.all():
            self.assertEqual(
                Counter(
                    generated_set.items.values_list("source_campus_id", flat=True)
                ),
                Counter(problem.campus_quotas),
            )
            self.assertEqual(
                Counter(
                    generated_set.items.values_list("difficulty_snapshot", flat=True)
                ),
                Counter(problem.difficulty_quotas),
            )
            fingerprints = [
                QuestionPayloadService.question_fingerprint(item.question_text_snapshot)
                for item in generated_set.items.all()
            ]
            self.assertEqual(len(fingerprints), len(set(fingerprints)))

    def test_automatic_duplicate_only_pool_blocks_as_insufficient_unique_questions(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        Question.objects.filter(contribution__cycle_course=parent).update(
            question_text="Repeated normalized question text"
        )

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )

        self.assertIsNone(problem)
        self.assertIn(
            "UNIQUE_QUESTION_SHORTAGES",
            {blocker["code"] for blocker in readiness["blockers"]},
        )
        self.assertEqual(readiness["submitted_question_count"], 150)
        self.assertEqual(readiness["unique_question_count"], 1)
        self.assertEqual(readiness["duplicate_question_count"], 149)
        self.assertEqual(result.code, "UNIQUE_QUESTION_SHORTAGES")
        self.assertEqual(
            result.message,
            "Insufficient Total questions: 1 available / 50 required",
        )
        self.assertFalse(ExamGenerationRevision.objects.exists())

    def test_automatic_duplicate_metrics_use_raw_200_unique_123_semantics(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        self.assertEqual(resolve_automatic_generation_max_states(), 1_000_000)
        campus_a, campus_b, campus_c = list(
            parent.offering_snapshots.order_by("campus_id").values_list(
                "campus_id", flat=True
            )
        )
        fixed_capacities = (
            (campus_a, Question.Difficulty.DIFFICULT, 10),
            (campus_a, Question.Difficulty.EASY, 15),
            (campus_a, Question.Difficulty.MODERATE, 24),
            (campus_b, Question.Difficulty.DIFFICULT, 10),
            (campus_b, Question.Difficulty.EASY, 15),
            (campus_b, Question.Difficulty.MODERATE, 23),
            (campus_c, Question.Difficulty.EASY, 15),
            (campus_c, Question.Difficulty.MODERATE, 9),
        )
        logical_groups = []
        for campus_id, difficulty, count in fixed_capacities:
            logical_groups.extend([((campus_id, difficulty),)] * count)
        logical_groups.extend(
            [
                (
                    (campus_a, Question.Difficulty.MODERATE),
                    (campus_b, Question.Difficulty.MODERATE),
                ),
                (
                    (campus_b, Question.Difficulty.MODERATE),
                    (campus_c, Question.Difficulty.MODERATE),
                ),
            ]
        )
        group_sizes = [1] * 49 + [2] * 72 + [3, 4]
        rows = []
        for group_number, (options, group_size) in enumerate(
            zip(logical_groups, group_sizes), start=1
        ):
            for row_number in range(group_size):
                campus_id, difficulty = options[row_number % len(options)]
                rows.append(
                    (
                        f"SASA logical question {group_number}",
                        campus_id,
                        difficulty,
                    )
                )
        self._replace_automatic_question_rows(parent=parent, rows=rows)

        started = perf_counter()
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        readiness_elapsed = perf_counter() - started

        duplicate_warning = next(
            item
            for item in readiness["warnings"]
            if item["code"] == "REDUNDANT_DUPLICATE_QUESTIONS"
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(readiness["submitted_question_count"], 200)
        self.assertEqual(readiness["unique_question_count"], 123)
        self.assertEqual(readiness["duplicate_question_count"], 77)
        self.assertEqual(len(problem.questions), 200)
        self.assertEqual(
            len({block.logical_group_id for block in problem.blocks}),
            123,
        )
        group_sizes = Counter(block.logical_group_id for block in problem.blocks)
        self.assertEqual(Counter(group_sizes.values()), {1: 49, 2: 72, 3: 1, 4: 1})
        cross_campus_groups = {
            logical_id
            for logical_id in group_sizes
            if len(
                {
                    block.members[0].campus
                    for block in problem.blocks
                    if block.logical_group_id == logical_id
                }
            )
            > 1
        }
        self.assertEqual(len(cross_campus_groups), 2)
        self.assertEqual(readiness["minimum_overlap"], 7)
        self.assertEqual(problem.minimum_overlap, 7)
        self.assertFalse(readiness["solver_limit_hit"])
        self.assertLess(readiness["solver_states"], 10_000)
        self.assertLess(readiness_elapsed, 30)
        self.assertEqual(
            duplicate_warning["message"],
            "200 submitted \u2022 123 unique \u2022 77 duplicate copies automatically ignored.",
        )
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )
        summary = AutomaticGenerationSummaryService.build(cycle=parent.cycle)

        self.assertEqual(result.status, "GENERATED")
        self.assertEqual(
            summary["generated"][0]["pool_metrics"],
            {"submitted": 200, "unique": 123, "redundant": 77},
        )
        self.assertIn(
            "REDUNDANT_DUPLICATE_QUESTIONS",
            {item["code"] for item in summary["generated"][0]["warnings"]},
        )
        revision = ExamGenerationRevision.objects.get()
        self.assertEqual(revision.minimum_overlap, 7)
        generated_fingerprints = []
        for generated_set in revision.generated_sets.all():
            self.assertEqual(
                Counter(
                    generated_set.items.values_list("source_campus_id", flat=True)
                ),
                Counter(problem.campus_quotas),
            )
            self.assertEqual(
                Counter(
                    generated_set.items.values_list("difficulty_snapshot", flat=True)
                ),
                Counter(problem.difficulty_quotas),
            )
            fingerprints = [
                QuestionPayloadService.question_fingerprint(question_text)
                for question_text in generated_set.items.values_list(
                    "question_text_snapshot", flat=True
                )
            ]
            self.assertEqual(len(fingerprints), len(set(fingerprints)))
            generated_fingerprints.append(set(fingerprints))
        self.assertEqual(
            len(generated_fingerprints[0].intersection(generated_fingerprints[1])),
            7,
        )

    def test_automatic_global_representatives_solve_exact_52_to_50_counterexample(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        campus_a, campus_b, campus_c = self._automatic_52_row_counterexample(
            parent=parent,
            variant="joint",
        )

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertFalse(readiness["shortages"])
        self.assertEqual(readiness["submitted_question_count"], 52)
        self.assertEqual(readiness["unique_question_count"], 50)
        self.assertEqual(readiness["duplicate_question_count"], 2)
        self.assertEqual(
            problem.campus_quotas,
            {campus_a: 17, campus_b: 17, campus_c: 16},
        )
        self.assertEqual(
            problem.difficulty_quotas,
            {"EASY": 15, "MODERATE": 25, "DIFFICULT": 10},
        )
        selection = self._assert_automatic_selection_margins(problem)
        self.assertEqual(
            set(selection.set_a_block_ids),
            set(selection.set_b_block_ids),
        )
        selected_flexible = [
            problem.questions[block.members[0].source_id]
            for block in problem.blocks
            if str(block.block_id) in selection.set_a_block_ids
            and problem.questions[block.members[0].source_id].question_text
            == "Counterexample flexible logical question"
        ]
        self.assertEqual(len(selected_flexible), 1)
        self.assertEqual(selected_flexible[0].campus_id, campus_b)
        self.assertEqual(
            selected_flexible[0].difficulty,
            Question.Difficulty.MODERATE,
        )

    def test_automatic_global_representatives_avoid_fake_campus_shortage(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        self._automatic_52_row_counterexample(parent=parent, variant="campus")

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertFalse(readiness["shortages"])
        self._assert_automatic_selection_margins(problem)

    def test_automatic_global_representatives_avoid_fake_difficulty_shortage(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        self._automatic_52_row_counterexample(parent=parent, variant="difficulty")

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertFalse(readiness["shortages"])
        self._assert_automatic_selection_margins(problem)

    def test_automatic_global_representatives_preserve_better_contributor_coverage(self):
        vector = (1, 1, 1)

        def block(source_id, contributor_id, logical_group_id):
            return IdentityBlock(
                block_id=f"question:{source_id}",
                vector=vector,
                members=(
                    IdentityMember(
                        source_id=source_id,
                        contributor_id=contributor_id,
                        campus="CAMPUS",
                        difficulty="EASY",
                        section_id=0,
                    ),
                ),
                logical_group_id=logical_group_id,
            )

        result = solve_identity_aware_two_sets(
            margins=(2, 2, 2),
            blocks=(
                block(1, 1, "flexible"),
                block(2, 2, "flexible"),
                block(3, 1, "fixed"),
                block(4, 1, "fixed"),
            ),
            minimum_overlap=2,
            campus_quotas={"CAMPUS": 2},
            difficulty_quotas={"EASY": 2},
            secret=settings.SECRET_KEY,
            hmac_context={"test": "logical-contributor-alternatives"},
            max_states=10_000,
        )

        self.assertTrue(result.feasible, result)
        self.assertEqual(result.set_a_block_ids, result.set_b_block_ids)
        self.assertIn("question:2", result.set_a_block_ids)
        self.assertEqual(result.contributors_represented, 2)
        self.assertEqual(result.squared_contributor_concentration, 8)

    def test_automatic_duplicates_preserve_campus_representation(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        campuses = list(parent.offering_snapshots.order_by("campus_id").values_list("campus_id", flat=True))
        cubao_id, fairview_id, _taytay_id = campuses
        cubao_questions = list(
            Question.objects.filter(
                contribution__cycle_course=parent,
                contribution__source_campus_id=cubao_id,
            ).order_by("id")
        )
        fairview_questions = list(
            Question.objects.filter(
                contribution__cycle_course=parent,
                contribution__source_campus_id=fairview_id,
            ).order_by("id")
        )
        for duplicate, source in zip(fairview_questions, cubao_questions):
            duplicate.question_text = source.question_text
        Question.objects.bulk_update(fairview_questions, ["question_text"])

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=IdentitySelectionResult(
                feasible=True,
                limit_hit=False,
                states_explored=1,
                overlap=50,
            ),
        ):
            problem, readiness = Stage6ReadinessService.build_problem(
                cycle_course=parent
            )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(readiness["duplicate_question_count"], 50)
        self.assertIn(fairview_id, problem.campus_quotas)
        self.assertGreaterEqual(
            sum(
                question.campus_id == fairview_id
                for question in problem.questions.values()
            ),
            problem.campus_quotas[fairview_id],
        )

    def test_automatic_post_dedupe_difficulty_shortage_reports_true_availability(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        moderate = list(
            Question.objects.filter(
                contribution__cycle_course=parent,
                difficulty=Question.Difficulty.MODERATE,
            ).order_by("id")
        )
        retained = moderate[:23]
        Question.objects.filter(pk__in=[question.id for question in moderate[23:]]).delete()
        Question.objects.filter(pk=retained[-1].pk).update(
            question_text=retained[0].question_text
        )

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )

        self.assertIsNone(problem)
        self.assertIn(
            "UNIQUE_QUESTION_SHORTAGES",
            {blocker["code"] for blocker in readiness["blockers"]},
        )
        self.assertIn(
            {"dimension": "difficulty", "label": "Moderate", "required": 25, "available": 22},
            readiness["shortages"],
        )
        self.assertEqual(
            result.message,
            "Insufficient Moderate questions: 22 available / 25 required",
        )

    def test_manual_generation_keeps_duplicate_rows_in_its_existing_pool(self):
        parent, _problem = self.ready_generation_course()
        duplicate_source, duplicate_row = list(
            Question.objects.filter(contribution__cycle_course=parent).order_by("id")[:2]
        )
        Question.objects.filter(pk=duplicate_row.pk).update(
            question_text=duplicate_source.question_text
        )

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertFalse(readiness["automatic_pool"])
        self.assertEqual(readiness["duplicate_question_count"], 0)
        self.assertIn(duplicate_source.id, problem.questions)
        self.assertIn(duplicate_row.id, problem.questions)

    def test_automatic_flat_infeasibility_uses_no_scenario_wording_while_manual_keeps_it(self):
        automatic_parent, _configuration, _problem = self._ready_automatic_course()
        infeasible = FeasibilityResult(
            feasible=False,
            minimum_overlap=None,
            states_explored=1,
        )

        with patch(
            "apps.departmental_exams.generation_readiness.solve_automatic_identity_aware_two_sets",
            return_value=IdentitySelectionResult(False, False, 1),
        ), patch(
            "apps.departmental_exams.generation_readiness.solve_two_set_feasibility",
            return_value=infeasible,
        ):
            automatic_problem, automatic_readiness = Stage6ReadinessService.build_problem(
                cycle_course=automatic_parent
            )
            automatic_parent.cycle.processing_mode = (
                ExaminationCycle.ProcessingMode.MANUAL_REVIEW
            )
            automatic_parent.cycle.save(update_fields=["processing_mode", "updated_at"])
            manual_problem, manual_readiness = Stage6ReadinessService.build_problem(
                cycle_course=automatic_parent
            )

        self.assertIsNone(automatic_problem)
        automatic_message = automatic_readiness["blockers"][0]["message"]
        self.assertNotIn("scenario", automatic_message.casefold())
        self.assertIn("questionnaire allocation", automatic_message)
        self.assertIsNone(manual_problem)
        self.assertEqual(
            manual_readiness["blockers"][0]["message"],
            "Two equivalent sets cannot satisfy all hard margins and scenario bundles.",
        )

    def test_manual_without_blueprint_keeps_existing_blocker(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )

        self.assertIsNone(problem)
        self.assertIn(
            "BLUEPRINT_MISSING",
            {blocker["code"] for blocker in readiness["blockers"]},
        )

    def test_automatic_flat_insufficient_pool_does_not_generate(self):
        parent, _configuration, _problem = self._ready_automatic_course()
        ExamBlueprint.objects.filter(cycle_course=parent).delete()
        retained_ids = list(
            Question.objects.filter(contribution__cycle_course=parent)
            .order_by("id")
            .values_list("id", flat=True)[:49]
        )
        Question.objects.filter(contribution__cycle_course=parent).exclude(
            id__in=retained_ids
        ).delete()

        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        result = AutomaticExamDeadlineService.process_course(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
        )

        self.assertIsNone(problem)
        self.assertIn(
            "QUESTION_SHORTAGES",
            {blocker["code"] for blocker in readiness["blockers"]},
        )
        self.assertEqual(result.code, "QUESTION_SHORTAGES")
        self.assertFalse(ExamGenerationRevision.objects.exists())

    def test_mode_aware_assigned_course_actions_preserve_manual_workflow(self):
        automatic_parent, _configuration, _problem = self._ready_automatic_course()
        ExamBlueprint.objects.filter(cycle_course=automatic_parent).delete()
        assigned_url = reverse("departmental_exams:assigned_course_examinations")

        self.client.force_login(self.generation_manager)
        automatic_response = self.client.get(assigned_url)
        self.assertEqual(automatic_response.status_code, 200)
        self.assertContains(automatic_response, "Automatic workflow")
        self.assertContains(automatic_response, "Configure Override")
        for manual_action in (
            "Exam Blueprint",
            "Confidential Inputs",
            "Confidential Review",
            "Generate Sets",
            "Generation Actions",
            "Approve &amp; Lock",
        ):
            self.assertNotContains(automatic_response, manual_action)

        configuration_response = self.client.get(
            reverse(
                "departmental_exams:course_configuration",
                args=[automatic_parent.id],
            )
        )
        self.assertEqual(configuration_response.status_code, 200)
        self.assertNotContains(configuration_response, "Exam Blueprint")
        workspace_response = self.client.get(
            reverse(
                "departmental_exams:generation_workspace",
                args=[automatic_parent.id],
            )
        )
        self.assertEqual(workspace_response.status_code, 200)
        self.assertContains(workspace_response, "Automatic Generation Summary")
        self.assertNotContains(workspace_response, "Confidential Inputs")
        self.assertNotContains(workspace_response, "blueprint review")

        manual_cycle = automatic_parent.cycle
        manual_cycle.processing_mode = ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        manual_cycle.save(update_fields=["processing_mode", "updated_at"])
        automatic_parent.responsible_department = self.department
        automatic_parent.reviewer = self.reviewer
        automatic_parent.save(
            update_fields=["responsible_department", "reviewer", "updated_at"]
        )
        self.no_sections_blueprint(automatic_parent)
        self.client.force_login(self.configurer)
        configurer_response = self.client.get(assigned_url)
        self.assertContains(configurer_response, "Exam Blueprint")
        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(assigned_url)
        self.assertContains(reviewer_response, "Confidential Review")
        self.assertContains(reviewer_response, "Generate Sets")
        self.assertContains(reviewer_response, automatic_parent.course.code)

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
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=self._proved_selection_for(
                parent=parent,
                problem=refreshed_problem,
            ),
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
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            side_effect=lambda **kwargs: self._proved_selection_for(
                parent=parent,
                problem=Stage6ReadinessService.build_problem(cycle_course=parent)[0],
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
        self.assertContains(response, "Processing Set A and Set B")
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, "data-automatic-regeneration-form")
        self.assertContains(response, "data-automatic-action")
        self.assertContains(response, "control.disabled = active")
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
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=self._proved_selection_for(
                parent=parent,
                problem=fresh_problem,
            ),
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
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=self._proved_selection_for(
                parent=parent,
                problem=fresh_problem,
            ),
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
        self.assertContains(assigned, "View Generated Examination")
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
