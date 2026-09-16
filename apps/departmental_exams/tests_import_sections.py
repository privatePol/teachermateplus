from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService

from .contribution_authorization import ContributionConflict
from .csv_import import QuestionCSVImportService
from .docx_import import QuestionDOCXImportService
from .import_sections import ImportSectionError
from .models import (
    ExamBlueprint, ExamScenarioMember, ExamSection, FacultyContribution,
    Question, QuestionBlueprintPlacement, QuestionImportBatch,
)
from .stage4_test_support import Stage4TestCase
from . import stage4_test_support
from .tests_docx_import import make_docx
from . import tests_docx_import, tests_stage5_csv_resume
from .tests_faculty_cases import FacultyCaseFixtureMixin
from .tests_stage5_contributions import Stage5FixtureMixin


class SectionTargetedImportTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL",
        )

    def upload(self, source, count=2):
        if source == "csv":
            return tests_stage5_csv_resume.Stage5ResumableCSVImportTests.upload(count)
        return make_docx([
            line for number in range(1, count + 1)
            for line in tests_docx_import.DOCXImportServiceTests.valid_paragraphs(number, f"Word item {number}")
        ])

    def preview(self, source="csv", count=2, **overrides):
        self.contribution.refresh_from_db()
        service = QuestionCSVImportService if source == "csv" else QuestionDOCXImportService
        kwargs = dict(
            contribution_id=self.contribution.id, uploaded_file=self.upload(source, count),
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            target_section_id=self.section_b.id,
        )
        kwargs.update(overrides)
        return service.create_preview(**kwargs)

    def process(self, batch, **overrides):
        kwargs = dict(token=batch.token, expected_file_sha256=batch.file_sha256,
                      user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                      chunk_size=1)
        kwargs.update(overrides)
        return QuestionCSVImportService.process_next_chunk(**kwargs)[0]

    def assert_placements(self, batch, count):
        questions = list(Question.objects.filter(import_batch=batch).order_by("import_row_number"))
        self.assertEqual(len(questions), count)
        self.assertEqual(QuestionBlueprintPlacement.objects.filter(question__in=questions).count(), count)
        for question in questions:
            self.assertEqual(question.blueprint_placement.section_id, self.section_b.id)
            self.assertEqual(question.blueprint_placement.blueprint_id, self.blueprint.id)
        self.assertFalse(ExamScenarioMember.objects.filter(question__in=questions).exists())

    def test_csv_and_word_upload_preview_confirmation_preserve_cases(self):
        case = self.save_case(html='<table><tr><td class="tmp-align-right">100</td></tr></table>')
        self.add_question(scenario=case, text="Encoded first")
        self.add_question(scenario=case, text="Encoded second")
        before = list(case.members.order_by("position", "pk").values())
        for source in ("csv", "docx"):
            with self.subTest(source=source):
                url = reverse(f"departmental_exams:{source}_upload", args=[self.contribution.id])
                page = self.client.get(url)
                self.assertContains(page, "Add these questions to section")
                self.assertEqual(list(page.context["form"].fields["target_section_id"].choices),
                                 [("", "Select Exam Section"), (str(self.section_a.id), "Section A"),
                                  (str(self.section_b.id), "Section B")])
                response = self.client.post(url, {
                    **page.context["form"].initial, "target_section_id": self.section_b.id,
                    f"{source}_file": self.upload(source),
                })
                self.assertEqual(response.status_code, 302)
                preview = self.client.get(response["Location"])
                self.assertContains(preview, "Add these questions to section:</strong> Section B")
                batch = preview.context["batch"]
                self.assertEqual(batch.target_section_id, self.section_b.id)
                # A forged confirmation target cannot override the persisted batch.
                result = self.client.post(reverse(f"departmental_exams:{source}_confirm", args=[batch.token]),
                                          {**preview.context["confirm_form"].initial,
                                           "target_section_id": self.section_a.id})
                self.assertEqual(result.status_code, 302)
                self.assert_placements(batch, 2)
        case.refresh_from_db()
        self.assertEqual(list(case.members.order_by("position", "pk").values()), before)
        self.assertIn('<table>', case.stimulus)
        self.assertIn('tmp-align-right', case.stimulus)

    def test_missing_foreign_and_malformed_targets_rejected_at_form_and_service(self):
        parent = self.make_course(cycle=self.cycle, code="FOREIGN")
        blueprint = ExamBlueprint.objects.create(cycle_course=parent, mode="USE_SECTIONS",
                                                created_by=self.configurer, updated_by=self.configurer)
        foreign = ExamSection.objects.create(blueprint=blueprint, title="Foreign", display_order=1, item_quota=50)
        for source in ("csv", "docx"):
            for target in (None, "bad", foreign.pk):
                with self.subTest(source=source, target=target):
                    with self.assertRaises(ImportSectionError):
                        self.preview(source, target_section_id=target)
                    response = self.client.post(reverse(f"departmental_exams:{source}_upload", args=[self.contribution.id]), {
                        "expected_contribution_revision": self.contribution.revision,
                        "target_section_id": target or "", f"{source}_file": self.upload(source),
                    })
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("target_section_id", response.context["form"].errors)
        self.assertFalse(QuestionImportBatch.objects.exists())

    def test_disabled_structured_policy_rejects_even_without_supplied_target(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            False, tenant_id=self.tenant.id, value_type="BOOL",
        )
        for source in ("csv", "docx"):
            with self.subTest(source=source), self.assertRaises(PermissionDenied):
                self.preview(source, target_section_id=None)

    def test_resume_retry_has_exact_placements_and_unpublished_rows_stay_hidden(self):
        for source in ("csv", "docx"):
            with self.subTest(source=source):
                workspace_url = reverse(
                    "departmental_exams:contribution_workspace",
                    args=[self.contribution.id],
                )
                visible_before = self.client.get(workspace_url).context["saved_count"]
                batch = self.preview(source)
                self.process(batch)
                self.assert_placements(batch, 1)
                workspace = self.client.get(workspace_url)
                self.assertContains(workspace, "Add these questions to section:</strong> Section B")
                partial = Question.objects.get(import_batch=batch)
                self.assertNotIn(partial, workspace.context["questions"])
                self.assertEqual(workspace.context["saved_count"], visible_before)
                section_b_group = next(
                    group for group in workspace.context["presentation_sections"]
                    if group["section"] == self.section_b
                )
                self.assertEqual(section_b_group["saved_question_count"], visible_before)
                self.assertContains(
                    workspace,
                    f"Final exam: 20 items | Your saved questions: {visible_before}",
                )
                with patch("apps.departmental_exams.csv_import.QuestionBlueprintPlacement.objects.bulk_create",
                           side_effect=RuntimeError("placement write interrupted")):
                    with self.assertRaises(RuntimeError):
                        self.process(batch)
                self.assert_placements(batch, 1)
                batch.refresh_from_db()
                self.assertEqual(batch.status, "PAUSED")
                self.assertEqual(self.process(batch).status, "CONFIRMED")
                self.process(batch)
                self.assert_placements(batch, 2)
                published = self.client.get(workspace_url)
                self.assertEqual(published.context["saved_count"], visible_before + 2)
                section_b_group = next(
                    group for group in published.context["presentation_sections"]
                    if group["section"] == self.section_b
                )
                self.assertEqual(section_b_group["saved_question_count"], visible_before + 2)

    def test_final_audit_failure_rolls_back_questions_and_placements_together(self):
        batch = self.preview()
        self.process(batch)
        with patch("apps.departmental_exams.csv_import.AuditService.log_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.process(batch)
        self.assert_placements(batch, 1)
        self.assertEqual(self.process(batch).status, "CONFIRMED")
        self.assert_placements(batch, 2)

    def test_missing_legacy_target_reupload_preserves_completed_content(self):
        accepted = self.preview(count=1)
        self.process(accepted)
        accepted_ids = list(Question.objects.filter(import_batch=accepted).values_list("id", flat=True))
        QuestionImportBatch.objects.filter(pk=accepted.pk).update(target_section=None)
        for source in ("csv", "docx"):
            for started in (False, True):
                with self.subTest(source=source, started=started):
                    batch = self.preview(source)
                    if started:
                        self.process(batch)
                    QuestionImportBatch.objects.filter(pk=batch.pk).update(target_section=None)
                    with self.assertRaisesRegex(ImportSectionError, "Upload the file again"):
                        self.process(batch)
                    batch.refresh_from_db()
                    self.assertEqual(batch.status, "FAILED")
                    self.assertIn("Upload the file again", batch.failure_message)
                    self.assertFalse(Question.objects.filter(import_batch=batch).exists())
        self.assertEqual(self.process(accepted).status, "CONFIRMED")
        self.assertEqual(list(Question.objects.filter(import_batch=accepted).values_list("id", flat=True)), accepted_ids)

    def test_stale_target_rejected_on_every_chunk(self):
        batch = self.preview()
        self.process(batch)
        with patch("apps.departmental_exams.faculty_case_services.FacultyCasePolicy.context",
                   return_value=(self.blueprint, (self.section_a,))):
            with self.assertRaises(ImportSectionError):
                self.process(batch)
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_corrupt_missing_or_wrong_placement_fails_closed(self):
        for corrupt in ("missing", "wrong"):
            with self.subTest(corrupt=corrupt):
                batch = self.preview()
                self.process(batch)
                placements = QuestionBlueprintPlacement.objects.filter(question__import_batch=batch)
                if corrupt == "missing":
                    placements.delete()
                else:
                    placements.update(section=self.section_a)
                with self.assertRaisesRegex(ValidationError, "placements are inconsistent"):
                    self.process(batch)
                self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_quota_revision_deadline_and_eligibility_guards_clean_only_partial_import(self):
        case = self.save_case()
        linked = self.add_question(scenario=case)
        for guard in ("quota", "revision", "deadline", "eligibility"):
            with self.subTest(guard=guard):
                batch = self.preview()
                self.process(batch)
                if guard == "quota":
                    Question.objects.bulk_create([
                        Question(contribution=self.contribution, position=position,
                                 **{**self.payload(f"Quota filler {position}"), "difficulty": "EASY", "correct_answer": "A"})
                        for position in range(3, 51)
                    ])
                    error = ContributionConflict
                elif guard == "revision":
                    FacultyContribution.objects.filter(pk=self.contribution.pk).update(revision=self.contribution.revision + 1)
                    error = ContributionConflict
                elif guard == "deadline":
                    error = PermissionDenied
                else:
                    from apps.academics.models import FacultyAssignment
                    FacultyAssignment.objects.filter(faculty_user=self.faculty).update(is_active=False)
                    error = PermissionDenied
                clock = (patch("apps.departmental_exams.contribution_authorization.timezone.now",
                               return_value=self.configuration.active_contribution_deadline + timedelta(seconds=1))
                         if guard == "deadline" else nullcontext())
                with clock, self.assertRaises(error):
                    self.process(batch)
                self.assertFalse(Question.objects.filter(import_batch=batch).exists())
                self.assertTrue(Question.objects.filter(pk=linked.pk).exists())
                self.assertEqual(case.members.get().question_id, linked.pk)
                Question.objects.filter(contribution=self.contribution, question_text__startswith="Quota filler ").delete()

    def test_owner_boundary_cannot_create_placements(self):
        batch = self.preview()
        with self.assertRaises(Http404):
            self.process(batch, user=self.other_faculty)
        self.assertFalse(QuestionBlueprintPlacement.objects.exists())

    def test_async_confirmation_and_status_retain_target_and_safe_reupload_message(self):
        for source in ("csv", "docx"):
            with self.subTest(source=source):
                batch = self.preview(source, count=11)
                url = reverse(f"departmental_exams:{source}_confirm", args=[batch.token])
                response = self.client.post(url, {"file_sha256": batch.file_sha256},
                                            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["target_section_id"], self.section_b.id)
                self.assertEqual(response.json()["status"], "IMPORTING")
                self.assert_placements(batch, 10)
                status = self.client.get(reverse(f"departmental_exams:{source}_status", args=[batch.token]))
                self.assertEqual(status.json()["target_section_title"], "Section B")
                QuestionImportBatch.objects.filter(pk=batch.pk).update(target_section=None)
                stopped = self.client.post(url, {"file_sha256": batch.file_sha256},
                                           HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertEqual(stopped.status_code, 400)
                failure = self.client.get(reverse(f"departmental_exams:{source}_status", args=[batch.token])).json()
                self.assertEqual(failure["failure_code"], "IMPORT_SECTION_CHANGED")
                self.assertIn("Upload the file again", stopped.json()["error"])
                self.assertFalse(stopped.json()["can_resume"])
                self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_word_staged_correction_keeps_batch_target(self):
        batch = self.preview("docx", count=1)
        url = reverse("departmental_exams:docx_row_edit", args=[batch.token, 2])
        page = self.client.get(url)
        self.assertContains(page, "Add these questions to section:</strong> Section B")
        payload = {**page.context["form"].initial, "question_text": "Corrected Word question",
                   "target_section_id": self.section_a.id}
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.target_section_id, self.section_b.id)
        self.process(batch)
        self.assert_placements(batch, 1)
        self.assertEqual(Question.objects.get(import_batch=batch).question_text, "Corrected Word question")

    def _staged_correction_state(self, batch):
        return (
            QuestionImportBatch.objects.filter(pk=batch.pk).values().get(),
            list(batch.rows.order_by("row_number").values()),
            FacultyContribution.objects.filter(pk=self.contribution.pk).values().get(),
            list(Question.objects.order_by("pk").values()),
            list(QuestionBlueprintPlacement.objects.order_by("pk").values()),
        )

    def _assert_rejected_word_correction(self, batch, exception, guidance, status):
        before = self._staged_correction_state(batch)
        payload = self.payload("Correction must not be saved")
        with self.assertRaisesRegex(exception, guidance):
            QuestionDOCXImportService.update_staged_row(
                token=batch.token, row_number=2, payload=payload,
                user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
            )
        self.assertEqual(self._staged_correction_state(batch), before)
        response = self.client.post(
            reverse("departmental_exams:docx_row_edit", args=[batch.token, 2]),
            {**payload, "correct_answer": "A", "difficulty": "EASY",
             "expected_contribution_revision": self.contribution.revision},
        )
        self.assertContains(response, guidance if status == 400 else "current read-only or eligibility state", status_code=status)
        self.assertEqual(self._staged_correction_state(batch), before)

    def test_word_correction_rejects_historical_null_target_without_mutation(self):
        for status in ("READY", "INVALID"):
            with self.subTest(status=status):
                batch = self.preview("docx")
                if status == "INVALID":
                    QuestionDOCXImportService.update_staged_row(
                        token=batch.token, row_number=2,
                        payload={**self.payload("Existing invalid row"), "choice_b": "1"},
                        user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                        expected_contribution_revision=self.contribution.revision,
                    )
                batch.refresh_from_db()
                self.assertEqual(batch.status, status)
                QuestionImportBatch.objects.filter(pk=batch.pk).update(target_section=None)
                self._assert_rejected_word_correction(batch, ImportSectionError, "Upload the file again", 400)

    def test_word_correction_rejects_foreign_or_stale_target_without_mutation(self):
        batch = self.preview("docx")
        parent = self.make_course(cycle=self.cycle, code="CORRECTION-FOREIGN")
        blueprint = ExamBlueprint.objects.create(cycle_course=parent, mode="USE_SECTIONS",
                                                created_by=self.configurer, updated_by=self.configurer)
        foreign = ExamSection.objects.create(blueprint=blueprint, title="Foreign", display_order=1, item_quota=50)
        QuestionImportBatch.objects.filter(pk=batch.pk).update(target_section=foreign)
        self._assert_rejected_word_correction(batch, ImportSectionError, "Upload the file again", 400)
        QuestionImportBatch.objects.filter(pk=batch.pk).update(target_section=self.section_b)
        with patch("apps.departmental_exams.faculty_case_services.FacultyCasePolicy.context",
                   return_value=(self.blueprint, (self.section_a,))):
            self._assert_rejected_word_correction(batch, ImportSectionError, "Upload the file again", 400)

    def test_word_correction_rejects_unavailable_structured_policy_without_mutation(self):
        batch = self.preview("docx")
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            False, tenant_id=self.tenant.id, value_type="BOOL",
        )
        self._assert_rejected_word_correction(
            batch, PermissionDenied, "Sectioned question import requires enabled, frozen section placement.", 403,
        )


class NoSectionsImportTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        from .blueprint_services import BlueprintMutationService
        from .services import CourseExamConfigurationService

        for key in (FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
                    FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY):
            SystemSettingService.set(key, True, tenant_id=self.tenant.id, value_type="BOOL")
        cycle = self.make_cycle(status="OPEN", default_questions_required_per_faculty=50,
                                default_final_item_count=50, default_contribution_deadline=self.future_deadline(),
                                default_coverage="No Sections coverage")
        self.parent = self.make_course(cycle=cycle, code="FLAT")
        config = self.make_configuration(self.parent)
        self.faculty = self.make_faculty("flat-import-owner")
        self.make_assignment(self.parent, self.faculty)
        BlueprintMutationService.save_structure(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.configurer,
            expected_revision=0, mode="NO_SECTIONS", sections=(),
        )
        CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=config.revision,
        )
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)
        self.client.force_login(self.faculty)

    def test_csv_and_word_use_implicit_section_without_selector(self):
        for source in ("csv", "docx"):
            with self.subTest(source=source):
                url = reverse(f"departmental_exams:{source}_upload", args=[self.contribution.id])
                page = self.client.get(url)
                self.assertNotIn("target_section_id", page.context["form"].fields)
                self.assertNotContains(page, "Add these questions to section")
                upload = (tests_stage5_csv_resume.Stage5ResumableCSVImportTests.upload(1)
                          if source == "csv" else make_docx(tests_docx_import.DOCXImportServiceTests.valid_paragraphs()))
                response = self.client.post(url, {**page.context["form"].initial, f"{source}_file": upload})
                self.assertEqual(response.status_code, 302)
                preview = self.client.get(response["Location"])
                batch = preview.context["batch"]
                self.assertIsNone(batch.target_section_id)
                if source == "docx":
                    edit_url = reverse("departmental_exams:docx_row_edit", args=[batch.token, 2])
                    edit = self.client.get(edit_url)
                    self.assertEqual(self.client.post(edit_url, {
                        **edit.context["form"].initial, "question_text": "Corrected No Sections question",
                    }).status_code, 302)
                    batch.refresh_from_db()
                    self.assertIsNone(batch.target_section_id)
                    self.assertEqual(batch.rows.get(row_number=2).payload["question_text"], "Corrected No Sections question")
                self.assertEqual(self.client.post(
                    reverse(f"departmental_exams:{source}_confirm", args=[batch.token]),
                    preview.context["confirm_form"].initial,
                ).status_code, 302)
                self.assertEqual(Question.objects.filter(import_batch=batch).count(), 1)
        self.assertFalse(QuestionBlueprintPlacement.objects.exists())

    def test_explicit_target_is_rejected_for_no_sections(self):
        for source, service in (("csv", QuestionCSVImportService), ("docx", QuestionDOCXImportService)):
            with self.subTest(source=source), self.assertRaises(ImportSectionError):
                service.create_preview(
                    contribution_id=self.contribution.id,
                    uploaded_file=(tests_stage5_csv_resume.Stage5ResumableCSVImportTests.upload(1)
                                   if source == "csv" else make_docx(tests_docx_import.DOCXImportServiceTests.valid_paragraphs())),
                    user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                    expected_contribution_revision=self.contribution.revision, target_section_id=123,
                )


class ImportSectionMigrationTests(FacultyCaseFixtureMixin, stage4_test_support.Stage4TransactionTestCase):
    def test_upgrade_keeps_historical_targets_null_and_preserves_content(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        current = [("departmental_exams", "0029_question_import_target_section")]
        previous = [("departmental_exams", "0028_case_generation_snapshots")]
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate(previous)
            executor = MigrationExecutor(connection)
            historical = executor.loader.project_state(previous).apps
            old = lambda name: historical.get_model("departmental_exams", name)
            # Create actual 0028 rows, before target_section or v4 import plans
            # existed.  Current services would create irreversible v4 evidence.
            case = old("ExamScenario").objects.create(
                blueprint_id=self.blueprint.pk, contribution_id=self.contribution.pk,
                section_id=self.section_a.pk, title="Historical Case",
                stimulus="<p>Historical narrative</p>", content_format="RICH_HTML_V1",
                created_by_id=self.faculty.pk, updated_by_id=self.faculty.pk,
            )
            linked = old("Question").objects.create(
                contribution_id=self.contribution.pk, position=1,
                question_text="Historical linked question", choice_a="A", choice_b="B",
                choice_c="C", choice_d="D", correct_answer="A", difficulty="EASY",
            )
            old("ExamScenarioMember").objects.create(scenario_id=case.pk, question_id=linked.pk, position=1)
            old("QuestionBlueprintPlacement").objects.create(
                blueprint_id=self.blueprint.pk, section_id=self.section_a.pk,
                question_id=linked.pk, placed_by_id=self.faculty.pk,
            )
            now = timezone.now()
            batch_fields = dict(
                tenant_id=self.tenant.pk, contribution_id=self.contribution.pk,
                uploading_user_id=self.faculty.pk,
                contribution_revision_snapshot=self.contribution.revision,
                file_sha256="a" * 64, filename_sha256="b" * 64,
                total_rows=1, valid_rows=1, resulting_question_count=2,
                expires_at=now + timedelta(hours=1),
            )
            complete = old("QuestionImportBatch").objects.create(
                **batch_fields, status="CONFIRMED", committed_rows=1,
                confirmed_at=now, payload_purged_at=now,
            )
            imported = old("Question").objects.create(
                contribution_id=self.contribution.pk, position=2,
                import_batch_id=complete.pk, import_row_number=2, entry_method="CSV",
                question_text="Historical imported question", choice_a="A", choice_b="B",
                choice_c="C", choice_d="D", correct_answer="A", difficulty="EASY",
            )
            old("QuestionBlueprintPlacement").objects.create(
                blueprint_id=self.blueprint.pk, section_id=self.section_b.pk,
                question_id=imported.pk, placed_by_id=self.faculty.pk,
            )
            pending = old("QuestionImportBatch").objects.create(**batch_fields, status="READY")
            old("QuestionImportRow").objects.create(
                batch_id=pending.pk, row_number=2,
                payload={"question_text": "Historical pending row"}, fingerprint="c" * 64,
            )
            batches = [complete.pk, pending.pk]
            before = list(old("QuestionImportBatch").objects.filter(pk__in=batches).order_by("pk").values())
            questions = list(old("Question").objects.order_by("pk").values())
            placements = list(old("QuestionBlueprintPlacement").objects.order_by("pk").values())
            members = list(old("ExamScenarioMember").objects.order_by("pk").values())
            executor.migrate(current)
            upgraded = MigrationExecutor(connection).loader.project_state(current).apps
            new = lambda name: upgraded.get_model("departmental_exams", name)
            after = list(new("QuestionImportBatch").objects.filter(pk__in=batches).order_by("pk").values())
            self.assertEqual([row.pop("target_section_id") for row in after], [None, None])
            self.assertEqual(after, before)
            self.assertEqual(list(new("Question").objects.order_by("pk").values()), questions)
            self.assertEqual(list(new("QuestionBlueprintPlacement").objects.order_by("pk").values()), placements)
            self.assertEqual(list(new("ExamScenarioMember").objects.order_by("pk").values()), members)
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(cursor, QuestionImportBatch._meta.db_table)
                columns = connection.introspection.get_table_description(cursor, QuestionImportBatch._meta.db_table)
            self.assertTrue(any(item["index"] and item["columns"] == ["target_section_id"] for item in constraints.values()))
            self.assertTrue(any(item["foreign_key"] == (ExamSection._meta.db_table, "id") for item in constraints.values()))
            self.assertTrue(next(column for column in columns if column.name == "target_section_id").null_ok)
        finally:
            MigrationExecutor(connection).migrate(leaves)
