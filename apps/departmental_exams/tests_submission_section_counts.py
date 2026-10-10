"""Synthetic, disposable-database coverage for the narrow submission policy."""
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .automatic_workflow import AutomaticContributionReopenService
from .blueprint_services import BlueprintMutationService
from .contribution_services import (
    ContributionSectionCountMismatch, ContributionSectionCountService,
    QuestionMutationService,
)
from .exam_units import ExamCourseEquivalencyService
from .faculty_case_services import FacultyCaseMutationService
from .models import (
    CourseExamConfiguration, CycleCourse, ExamBlueprint, ExamScenario,
    ExamScenarioMember, ExamSection, FacultyContribution, Question,
    QuestionBlueprintPlacement, QuestionIdentityReservation, QuestionImportBatch,
    _classification_service_scope, _exam_structure_lifecycle_service_scope,
)
from .services import CourseExamConfigurationService
from .setup_services import CourseSetupService
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class SubmissionSectionCountTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL",
        )
        self.cycle = self.make_cycle(
            status="OPEN", default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
        )
        self.cycle.processing_mode = "AUTOMATIC_GENERATION"
        self.cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent = self._departmental_course(self.cycle, "COUNT")
        self.configuration = self.make_configuration(
            self.parent, deadline=self.cycle.default_contribution_deadline,
        )
        self.faculty = self.make_faculty("section-count-owner")
        self.make_assignment(self.parent, self.faculty)
        self.blueprint = self._structure(self.parent)
        self.configuration = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            user=self.admin, expected_revision=self.configuration.revision,
        )[0]
        self.blueprint.refresh_from_db()
        self.contribution = FacultyContribution.objects.get(cycle_course=self.parent, faculty_user=self.faculty)
        self.first, self.second = self.blueprint.sections.order_by("display_order", "id")
        self.client.force_login(self.faculty)

    def _departmental_course(self, cycle, code):
        course = self.make_course(cycle=cycle, code=code)
        CourseSetupService.classify(
            course_id=course.id, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL", expected_state=CourseSetupService.fingerprint(course),
        )
        course.refresh_from_db()
        return course

    def _structure(self, course):
        return BlueprintMutationService.save_structure(
            cycle_course_id=course.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=0, mode="USE_SECTIONS",
            sections=[
                # Misleading titles and nonconsecutive orders prove identity/order,
                # rather than title matching or literal section IDs, governs roles.
                {"title": "PART II (stored first)", "instructions": "", "display_order": 2, "item_quota": 10},
                {"title": "PART I (stored second)", "instructions": "", "display_order": 7, "item_quota": 40},
            ],
        )[0]

    def _fill(self, first_count, second_count, *, contribution=None, sections=None):
        contribution = contribution or self.contribution
        sections = sections or (self.first, self.second)
        offset = contribution.questions.count()
        target_sections = [sections[0]] * first_count + [sections[1]] * second_count
        questions = [Question(
            contribution=contribution,
            question_text=f"Synthetic contribution {contribution.id} item {offset + index}",
            choice_a="One", choice_b="Two", choice_c="Three", choice_d="Four",
            correct_answer="D", difficulty="EASY", position=offset + index,
        ) for index, _section in enumerate(target_sections, start=1)]
        Question.objects.bulk_create(questions)
        QuestionBlueprintPlacement.objects.bulk_create([
            QuestionBlueprintPlacement(
                blueprint=section.blueprint, question=question, section=section,
                placed_by=self.admin,
            ) for question, section in zip(questions, target_sections)
        ])
        return questions

    def _submit(self, *, contribution=None, expected_revision=None):
        contribution = contribution or self.contribution
        contribution.refresh_from_db()
        return QuestionMutationService.submit(
            contribution_id=contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=(contribution.revision if expected_revision is None else expected_revision),
        )

    def _snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in (
            FacultyContribution, Question, QuestionBlueprintPlacement,
            ExamScenario, ExamScenarioMember, CourseExamConfiguration,
            QuestionIdentityReservation, AuditLog,
        )]

    def _reject(self, first_count, second_count):
        self._fill(first_count, second_count)
        before = self._snapshot()
        with self.assertRaises(ContributionSectionCountMismatch) as error:
            self._submit()
        self.assertIn(f"{first_count} / 10 required", error.exception.messages[0])
        self.assertIn(f"{second_count} / 40 required", error.exception.messages[0])
        self.assertEqual(before, self._snapshot())

    def test_0_50_rejected_atomically(self):
        self._reject(0, 50)

    def test_1_49_rejected_atomically(self):
        self._reject(1, 49)

    def test_9_41_rejected_atomically(self):
        self._reject(9, 41)

    def test_11_39_rejected_atomically(self):
        self._reject(11, 39)

    def test_10_40_submits_with_advisory_difficulty_and_idempotent_replay(self):
        self._fill(10, 40)
        submitted, changed = self._submit()
        self.assertTrue(changed)
        self.assertEqual(submitted.status, "SUBMITTED")
        before = self._snapshot()
        self.assertFalse(self._submit()[1])
        self.assertEqual(before, self._snapshot())

    def test_linked_questions_count_individually_and_narrative_does_not_count(self):
        case = FacultyCaseMutationService.save(
            contribution_id=self.contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_scenario_revision=0, title="Synthetic Case",
            raw_content="<p>Synthetic narrative</p>", section_id=self.first.id,
        )[0]
        for index in range(10):
            self.contribution.refresh_from_db()
            QuestionMutationService.create(
                contribution_id=self.contribution.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                payload=self.payload(f"Linked synthetic item {index}"),
                section_id=self.first.id, scenario_id=case.id,
            )
        self._fill(0, 40)
        report = ContributionSectionCountService.evaluate(contribution=self.contribution)
        self.assertEqual([r["current"] for r in report["rows"]], [10, 40])
        self.assertTrue(self._submit()[1])

    def test_incomplete_draft_saves_and_conditional_workspace_confirmation_guidance(self):
        question = QuestionMutationService.create(
            contribution_id=self.contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self.payload("Incomplete synthetic Draft"), section_id=self.second.id,
        )
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, "DRAFT")
        self.assertEqual(self.contribution.questions.count(), 1)
        self.assertEqual(question.blueprint_placement.section_id, self.second.id)
        before = self._snapshot()
        for route in ("contribution_workspace", "contribution_submit"):
            page = self.client.get(reverse("departmental_exams:" + route, args=[self.contribution.id]))
            self.assertContains(page, "Your required section counts for Final Submission: 10 / 40.")
            self.assertContains(page, "0 / 10 required")
            self.assertContains(page, "1 / 40 required")
            self.assertContains(page, "You may save an incomplete Draft.")
        self.assertEqual(before, self._snapshot())

    def test_authenticated_http_rejection_shows_counts_without_page_state_mask(self):
        self._fill(0, 50)
        before = self._snapshot()
        response = self.client.post(reverse("departmental_exams:contribution_submit", args=[self.contribution.id]), {
            "expected_contribution_revision": self.contribution.revision,
            "confirm_exact_quota": "on",
        })
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "departmental_exams/faculty/contribution_submit.html")
        self.assertContains(response, "0 / 10 required", status_code=400)
        self.assertContains(response, "50 / 40 required", status_code=400)
        self.assertContains(response, "Edit your Draft section assignments", status_code=400)
        self.assertNotContains(response, "page state is missing or invalid", status_code=400)
        self.assertEqual(before, self._snapshot())

    def test_http_stale_revision_keeps_409_before_section_business_validation(self):
        self._fill(0, 50)
        before = self._snapshot()
        response = self.client.post(reverse("departmental_exams:contribution_submit", args=[self.contribution.id]), {
            "expected_contribution_revision": self.contribution.revision + 1,
            "confirm_exact_quota": "on",
        })
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Page state is out of date", status_code=409)
        self.assertEqual(before, self._snapshot())

    def test_http_direct_deny_stays_403_without_section_details(self):
        self._fill(0, 50)
        UserPermission.objects.create(
            user=self.faculty, permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant, campus=self.campus, grant_type=UserPermission.GrantType.DENY,
        )
        before = self._snapshot()
        response = self.client.post(reverse("departmental_exams:contribution_submit", args=[self.contribution.id]), {
            "expected_contribution_revision": self.contribution.revision,
            "confirm_exact_quota": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "0 / 10 required", status_code=403)
        self.assertEqual(before, self._snapshot())

    def test_deadline_and_active_import_checks_still_precede_section_counts(self):
        self._fill(0, 50)
        batch = self._batch("PAUSED")
        with self.assertRaises(PermissionDenied):
            self._submit()
        batch.status = "EXPIRED"
        batch.active_contribution = None
        batch.next_row_number = None
        batch.payload_purged_at = timezone.now()
        batch.save(update_fields=["status", "active_contribution", "next_row_number", "payload_purged_at"])
        CourseExamConfiguration.objects.filter(pk=self.configuration.id).update(
            reopened_contribution_deadline=timezone.now() - timezone.timedelta(seconds=1),
        )
        before = self._snapshot()
        with self.assertRaises(PermissionDenied):
            self._submit()
        self.assertEqual(before, self._snapshot())

    def _batch(self, status):
        progress = {}
        if status == "PAUSED":
            progress = {
                "active_contribution": self.contribution,
                "next_row_number": 1,
                "started_at": timezone.now(),
                "progress_updated_at": timezone.now(),
            }
        return QuestionImportBatch.objects.create(
            tenant=self.tenant, contribution=self.contribution, uploading_user=self.faculty,
            status=status, contribution_revision_snapshot=self.contribution.revision,
            file_sha256="a" * 64, filename_sha256="b" * 64,
            total_rows=1, valid_rows=1, expires_at=self.future_deadline(),
            **progress,
        )

    def test_unconfirmed_question_is_not_counted_but_confirmed_question_is(self):
        questions = self._fill(10, 40)
        batch = self._batch("READY")
        Question.objects.filter(pk=questions[0].id).update(import_batch=batch, import_row_number=1)
        before = self._snapshot()
        with self.assertRaises(ContributionSectionCountMismatch) as error:
            self._submit()
        self.assertIn("9 / 10 required", error.exception.messages[0])
        self.assertEqual(before, self._snapshot())
        batch.status = "CONFIRMED"
        batch.confirming_user = self.faculty
        batch.confirmed_at = timezone.now()
        batch.payload_purged_at = timezone.now()
        batch.committed_rows = batch.total_rows
        batch.save(update_fields=["status", "confirming_user", "confirmed_at", "payload_purged_at", "committed_rows"])
        self.assertTrue(self._submit()[1])

    def test_equivalent_secondary_uses_actual_primary_frozen_section_ids(self):
        cycle = self.make_cycle(
            status="OPEN", scope_suffix="SECONDARY", default_questions_required_per_faculty=50,
            default_final_item_count=50, default_contribution_deadline=self.future_deadline(),
        )
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.save(update_fields=["processing_mode"])
        primary = self._departmental_course(cycle, "PRIMARY")
        secondary = self._departmental_course(cycle, "SECONDARY")
        configs = [self.make_configuration(c, deadline=cycle.default_contribution_deadline) for c in (primary, secondary)]
        self.make_assignment(secondary, self.faculty)
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id, name="Synthetic equivalent unit", actor=self.admin,
            primary_cycle_course_id=primary.id, member_ids=[primary.id, secondary.id],
        )
        blueprint = self._structure(primary)
        CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=secondary.id, tenant_id=self.tenant.id,
            user=self.admin, expected_revision=configs[1].revision,
        )
        contribution = FacultyContribution.objects.get(cycle_course=secondary, faculty_user=self.faculty)
        first, second = blueprint.sections.order_by("display_order", "id")
        questions = self._fill(0, 50, contribution=contribution, sections=(first, second))
        with self.assertRaises(ContributionSectionCountMismatch):
            self._submit(contribution=contribution)
        report = ContributionSectionCountService.evaluate(contribution=contribution)
        self.assertEqual([r["section_id"] for r in report["rows"]], [first.id, second.id])
        QuestionBlueprintPlacement.objects.filter(question__in=questions[:10]).update(section=first)
        self.assertTrue(self._submit(contribution=contribution)[1])
        self.assertFalse(ExamBlueprint.active_objects.filter(cycle_course=secondary).exists())

    def test_correction_resubmission_checked_and_original_history_unchanged(self):
        self._fill(10, 40)
        original = self._submit()[0]
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin,
            expected_revision=self.configuration.revision, new_deadline=self.future_deadline(),
            reason="Synthetic authorized section correction.", selected_contribution_ids=[original.id],
        )
        successor = FacultyContribution.objects.get(supersedes=original)
        history = (
            FacultyContribution.objects.filter(pk=original.pk).values().get(),
            list(original.questions.order_by("pk").values()),
            list(QuestionBlueprintPlacement.objects.filter(question__contribution=original).order_by("pk").values()),
        )
        copied = successor.questions.filter(blueprint_placement__section=self.first).first()
        QuestionBlueprintPlacement.objects.filter(question=copied).update(section=self.second)
        before = self._snapshot()
        with self.assertRaises(ContributionSectionCountMismatch):
            self._submit(contribution=successor)
        self.assertEqual(before, self._snapshot())
        QuestionBlueprintPlacement.objects.filter(question=copied).update(section=self.first)
        self.assertTrue(self._submit(contribution=successor)[1])
        self.assertEqual(history, (
            FacultyContribution.objects.filter(pk=original.pk).values().get(),
            list(original.questions.order_by("pk").values()),
            list(QuestionBlueprintPlacement.objects.filter(question__contribution=original).order_by("pk").values()),
        ))
        self.assertIsNone(ContributionSectionCountService.evaluate(contribution=original))

    def test_other_section_targets_keep_existing_submission_behavior(self):
        with _exam_structure_lifecycle_service_scope():
            ExamSection.objects.filter(pk=self.first.id).update(item_quota=20)
            ExamSection.objects.filter(pk=self.second.id).update(item_quota=30)
        self._fill(0, 50)
        self.assertIsNone(ContributionSectionCountService.evaluate(contribution=self.contribution))
        self.assertTrue(self._submit()[1])

    def test_quota_60_does_not_generalize_exam_targets_into_contributor_quotas(self):
        FacultyContribution.objects.filter(pk=self.contribution.id).update(quota_snapshot=60)
        self.contribution.refresh_from_db()
        self._fill(0, 60)
        self.assertIsNone(ContributionSectionCountService.evaluate(contribution=self.contribution))
        self.assertTrue(self._submit()[1])

    def test_standardized_no_sections_submission_and_ui_remain_unchanged(self):
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.parent.id).update(exam_classification="STANDARDIZED")
        with _exam_structure_lifecycle_service_scope():
            self.blueprint.sections.all().delete()
            ExamBlueprint.objects.filter(pk=self.blueprint.id).update(mode="NO_SECTIONS")
        Question.objects.bulk_create([Question(
            contribution=self.contribution, question_text=f"Standardized synthetic item {i}",
            choice_a="One", choice_b="Two", choice_c="Three", choice_d="Four",
            correct_answer="D", difficulty="EASY", position=i,
        ) for i in range(1, 51)])
        self.contribution.refresh_from_db()
        page = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertNotContains(page, "Your required section counts")
        self.assertTrue(self._submit()[1])

    def test_manual_departmental_10_40_is_also_checked(self):
        self.cycle.processing_mode = "MANUAL_REVIEW"
        self.cycle.save(update_fields=["processing_mode"])
        self._reject(0, 50)

    def test_existing_submitted_unbalanced_history_is_not_revalidated_or_mutated(self):
        self._fill(0, 50)
        FacultyContribution.objects.filter(pk=self.contribution.id).update(
            status="SUBMITTED", submitted_at=timezone.now(),
        )
        before = self._snapshot()
        self.assertFalse(self._submit()[1])
        self.assertEqual(before, self._snapshot())

    def test_invalid_or_unfrozen_shapes_do_not_enable_the_narrow_policy(self):
        self._fill(0, 50)
        frozen = {
            "structure_frozen_at": self.blueprint.structure_frozen_at,
            "structure_frozen_by_id": self.blueprint.structure_frozen_by_id,
            "structure_final_item_count": self.blueprint.structure_final_item_count,
        }
        changes = (
            {"structure_frozen_at": None, "structure_frozen_by_id": None, "structure_final_item_count": None},
            {"structure_final_item_count": 60},
        )
        for change in changes:
            with self.subTest(change=change), _exam_structure_lifecycle_service_scope():
                ExamBlueprint.objects.filter(pk=self.blueprint.id).update(**frozen)
                ExamBlueprint.objects.filter(pk=self.blueprint.id).update(**change)
                self.assertIsNone(ContributionSectionCountService.evaluate(contribution=self.contribution))
