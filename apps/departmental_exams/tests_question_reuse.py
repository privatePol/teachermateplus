import csv
import io

from django.urls import reverse
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.academics.models import AcademicYear, CourseOffering, FacultyAssignment, Term
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission, UserRole

from .contribution_services import QuestionMutationService, QuestionPayloadService
from .csv_import import CSV_HEADERS, QuestionCSVImportService
from .models import (CycleCourse, CycleCourseOffering, ExaminationCycle,
                     FacultyContribution, FacultyContributionEligibilitySource, Question,
                     ExamBlueprint, ExamSection, ExamScenario, ExamScenarioMember,
                     QuestionBankCaseMember, QuestionBankItem, QuestionBankRevision,
                     QuestionBlueprintPlacement, _exam_structure_lifecycle_service_scope)
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin
from .tests_faculty_cases import FacultyCaseFixtureMixin
from .tests_question_rich_editor import rich_payload
from .duplicate_contract import case_member_identity, pool_claims, reconcile
from .faculty_case_services import FacultyCaseMutationService
from .scenario_content import canonicalize_scenario_content
from .my_questions import (
    authoring_scopes,
    create_case as create_bank_case,
    create_question as create_bank_question,
    require_owner as require_bank_owner,
    revise_case as revise_bank_case,
    revise_historical_case,
    revise_question as revise_bank_question,
)
from django.http import Http404


class QuestionReuseHTTPTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.destination_course, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("reuse-owner")
        self.other = self.make_faculty("reuse-other")
        self.make_assignment(self.destination_course, self.faculty)
        self.make_assignment(self.destination_course, self.other)
        self.initialize(self.destination_course)
        self.destination = FacultyContribution.objects.get(cycle_course=self.destination_course, faculty_user=self.faculty)
        self.other_destination = FacultyContribution.objects.get(cycle_course=self.destination_course, faculty_user=self.other)
        self.client.force_login(self.faculty)
        self.url = reverse("departmental_exams:question_reuse", args=[self.destination.id])
        self.workspace_url = reverse("departmental_exams:contribution_workspace", args=[self.destination.id])
        self.previous = self._previous_source()

    def _previous_source(self, *, owner=None, campus=None):
        owner = owner or self.faculty
        campus = campus or self.campus
        old_year = AcademicYear.objects.create(tenant=self.tenant, code=f"OLD-{FacultyContribution.objects.count()}",
            name="Previous academic year", start_date="2025-06-01", end_date="2026-05-31")
        from apps.academics.models import Term
        term = Term.objects.create(tenant=self.tenant, academic_year=old_year, code="T1", name="First semester")
        cycle = ExaminationCycle.objects.create(
            tenant=self.tenant, academic_year=old_year, term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            status=ExaminationCycle.Status.CLOSED, created_by=self.admin)
        course = CycleCourse.objects.create(cycle=cycle, course=self.destination_course.course)
        if campus == self.campus:
            offering = self.destination_course.offering_snapshots.first().offering
            old_offering = CourseOffering.objects.create(
                tenant=self.tenant, campus=campus, department=offering.department,
                program=offering.program, academic_year=old_year, term=term,
                course=self.destination_course.course, section=offering.section)
            CycleCourseOffering.objects.create(cycle_course=course, offering=old_offering, campus=campus)
        else:
            old_offering = self.add_grouped_offering(
                course, campus=campus, department=self.other_department,
                slug=f"reuse-{course.id}")
        assignment = self.make_assignment(course, owner, campus=campus, offering=old_offering)
        source = FacultyContribution.objects.create(
            cycle_course=course, faculty_user=owner, source_assignment=assignment,
            source_campus=campus, quota_snapshot=50, configuration_revision_snapshot=1,
            status=FacultyContribution.Status.SUBMITTED, submitted_at=timezone.now())
        FacultyContributionEligibilitySource.objects.create(
            contribution=source, assignment=assignment, assignment_id_snapshot=assignment.id,
            offering_id_snapshot=old_offering.id, tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=campus.id, eligibility_proven_at=timezone.now())
        return source

    def _source_question(self, text, *, source=None, position=None, difficulty="EASY"):
        source = source or self.previous
        position = position or source.questions.count() + 1
        return Question.objects.create(
            contribution=source, position=position,
            **QuestionPayloadService.validate({**self.payload(text), "difficulty": difficulty}))

    def _enable(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_QUESTION_REUSE_ENABLED_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL")

    def _copy(self, page, *tokens, **extra):
        return self.client.post(self.url, {
            "expected_contribution_revision": self.destination.revision,
            "selected_items": list(tokens), **extra,
        })

    def test_feature_gate_and_authenticated_workspace_to_copy(self):
        self._source_question("Previous unique stem")
        self.assertNotContains(self.client.get(self.workspace_url), "Use My Questions in Draft")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self._enable()
        self.assertContains(self.client.get(self.workspace_url), "Use My Questions in Draft")
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Previous unique stem")
        self.assertEqual(page.content.decode().count("Previous unique stem"), 1)
        self.assertContains(page, "reuse-item-header")
        self.assertContains(page, 'data-reuse-index-panel')
        self.assertContains(page, 'data-reuse-toolbar')
        self.assertContains(page, 'id="reuse-card-question-')
        token = page.context["page"].object_list[0]["token"]
        response = self._copy(page, token)
        self.assertRedirects(response, self.workspace_url, fetch_redirect_response=False)
        self.assertEqual(self.destination.questions.count(), 1)
        self.assertEqual(self.previous.questions.count(), 1)
        self.assertContains(self.client.get(self.workspace_url), "Previous unique stem")
        self.assertEqual(self._copy(page, token).status_code, 409)
        self.assertEqual(self.destination.questions.count(), 1)

    def test_my_questions_revision_is_owner_only_and_does_not_rewrite_draft_copy(self):
        self._enable()
        item = create_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            payload=self.payload("Bank wording one"),
        )
        page = self.client.get(self.url)
        bank_entry = next(row for row in page.context["eligible_items"] if row.get("bank_item") == item)
        response = self._copy(page, bank_entry["token"])
        self.assertRedirects(response, self.workspace_url, fetch_redirect_response=False)
        copied = self.destination.questions.get()
        self.assertEqual(copied.question_text, "Bank wording one")
        self.assertEqual(copied.source_bank_revision.item_id, item.id)

        revise_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            item_id=item.id,
            expected_revision=1,
            payload=self.payload("Bank wording two"),
        )
        copied.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.current_revision, 2)
        self.assertEqual(copied.question_text, "Bank wording one")
        self.assertEqual(copied.source_bank_revision.revision, 1)
        with self.assertRaises(ValidationError):
            revise_bank_question(
                actor=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                item_id=item.id,
                expected_revision=1,
                payload=self.payload("Stale overwrite attempt"),
            )
        with self.assertRaises(Http404):
            require_bank_owner(
                user=self.admin,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                item_id=item.id,
            )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:my_question_edit", args=[item.id])
            ).status_code,
            404,
        )
        self.client.force_login(self.faculty)

    def test_retained_accepted_assignment_allows_authoring_between_terms(self):
        assignments = self.faculty.faculty_assignments.filter(
            offering__course=self.destination_course.course
        ).select_related("offering")
        for assignment in assignments:
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])
            assignment.offering.status = CourseOffering.Status.ARCHIVED
            assignment.offering.is_active = False
            assignment.offering.save(update_fields=["status", "is_active"])
        scopes = authoring_scopes(
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertEqual([scope.course_id for scope in scopes], [self.destination_course.course_id])

    def test_direct_deny_prevents_between_term_authoring(self):
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        self.assertEqual(
            authoring_scopes(
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            ),
            [],
        )

    def test_my_questions_add_route_works_after_exam_cycle_closes(self):
        self.destination_course.cycle.status = ExaminationCycle.Status.CLOSED
        self.destination_course.cycle.save(update_fields=["status", "updated_at"])
        page = self.client.get(reverse("departmental_exams:my_questions"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Add question")
        self.assertNotContains(page, "Create whole Case")
        response = self.client.post(
            reverse(
                "departmental_exams:my_question_create",
                args=[self.campus.id, self.destination_course.course_id],
            ),
            {
                **self.payload("Created between cycles"),
                "correct_answer": "D",
                "difficulty": "EASY",
                "content_format": "PLAIN_TEXT",
                "expected_item_revision": 0,
            },
        )
        self.assertRedirects(
            response,
            reverse("departmental_exams:my_questions"),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            QuestionBankItem.objects.filter(
                owner=self.faculty,
                origin_question__isnull=True,
                kind=QuestionBankItem.Kind.QUESTION,
            ).exists()
        )

    def test_my_questions_rich_question_save_redisplay_edit_and_draft_copy(self):
        self._enable()
        create_url = reverse("departmental_exams:my_question_create", args=[
            self.campus.id, self.destination_course.course_id,
        ])
        page = self.client.get(create_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-question-rich-field="question_text"')
        self.assertContains(page, 'data-question-rich-field="choice_a"')
        self.assertContains(page, 'data-question-preview-button disabled')
        preview_url = reverse("departmental_exams:my_content_preview", args=[
            self.campus.id, self.destination_course.course_id,
        ])
        payload = rich_payload(question_text="<p><strong>Original bank stem</strong></p>")
        preview = self.client.post(preview_url, payload)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("<strong>Original bank stem</strong>", preview.json()["fields"]["question_text"])
        self.assertEqual(self.client.post(create_url, {
            **payload, "expected_item_revision": 0,
        }).status_code, 302)
        item = QuestionBankItem.objects.get(
            owner=self.faculty, kind=QuestionBankItem.Kind.QUESTION,
            origin_question__isnull=True,
        )
        original = QuestionBankRevision.objects.get(item=item, revision=1)
        self.assertEqual(original.content_format, Question.ContentFormat.RICH_HTML_V1)
        edit_url = reverse("departmental_exams:my_question_edit", args=[item.id])
        edit_page = self.client.get(edit_url)
        self.assertContains(edit_page, "Original bank stem")
        self.assertContains(edit_page, 'data-question-rich-field="choice_a"')
        revised = {**payload, "question_text": "<p><em>Revised bank stem</em></p>"}
        self.assertEqual(self.client.post(edit_url, {
            **revised, "expected_item_revision": 1,
        }).status_code, 302)
        current = QuestionBankRevision.objects.get(item=item, revision=2)
        self.assertIn("<em>Revised bank stem</em>", current.question_text)
        original.refresh_from_db()
        self.assertIn("<strong>Original bank stem</strong>", original.question_text)
        page = self.client.get(self.url)
        entry = next(row for row in page.context["eligible_items"] if row.get("bank_item") == item)
        self.assertEqual(self._copy(page, entry["token"]).status_code, 302)
        copied = self.destination.questions.get(source_bank_revision=current)
        self.assertEqual(copied.content_format, Question.ContentFormat.RICH_HTML_V1)
        self.assertEqual(copied.question_text, current.question_text)
        self.assertEqual(copied.choice_a, current.choice_a)

    def test_my_content_preview_rejects_wrong_course_campus_and_direct_deny(self):
        other_course = self.make_course(
            cycle=self.destination_course.cycle, code="PREVIEW-UNASSIGNED"
        )
        payloads = (
            rich_payload(question_text="<p>Private question preview</p>"),
            {"input_format": "html", "stimulus": "<p>Private Case preview</p>"},
        )
        for payload in payloads:
            with self.subTest(input_format=payload.get("input_format", "question")):
                wrong_course = reverse(
                    "departmental_exams:my_content_preview",
                    args=[self.campus.id, other_course.course_id],
                )
                wrong_campus = reverse(
                    "departmental_exams:my_content_preview",
                    args=[self.other_campus.id, self.destination_course.course_id],
                )
                self.assertEqual(self.client.post(wrong_course, payload).status_code, 403)
                self.assertEqual(self.client.post(wrong_campus, payload).status_code, 403)

        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        permitted_url = reverse(
            "departmental_exams:my_content_preview",
            args=[self.campus.id, self.destination_course.course_id],
        )
        for payload in payloads:
            with self.subTest(denied_format=payload.get("input_format", "question")):
                self.assertEqual(self.client.post(permitted_url, payload).status_code, 403)

    def test_my_questions_navigation_remains_available_without_cycle_or_contribution(self):
        between_terms = self.make_faculty("between-terms")
        self.make_assignment(self.destination_course, between_terms)
        self.destination_course.cycle.status = ExaminationCycle.Status.CLOSED
        self.destination_course.cycle.save(update_fields=["status", "updated_at"])
        self.client.force_login(between_terms)

        response = self.client.get(reverse("departmental_exams:my_questions"))

        self.assertEqual(response.status_code, 200)
        codes = [
            node["item"].code
            for group in response.context["portal_menu"]
            for node in group["items"]
        ]
        self.assertIn("DE_EXAM_MY_QUESTIONS", codes)
        self.assertContains(
            response,
            f'href="{reverse("departmental_exams:my_questions")}"',
        )
        self.assertFalse(
            FacultyContribution.objects.filter(faculty_user=between_terms).exists()
        )

    def test_edit_and_catalogue_recheck_exact_course_assignment_on_get_and_post(self):
        item = create_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            payload=self.payload("Course A bank wording"),
        )
        historical = self._source_question("Course A historical wording")
        course_b = self.make_course(cycle=self.destination_course.cycle, code="S5-B")
        self.make_assignment(course_b, self.faculty)
        FacultyAssignment.objects.filter(
            faculty_user=self.faculty,
            offering__course=self.destination_course.course,
        ).update(
            response_status=FacultyAssignment.ResponseStatus.DECLINED,
            accepted_at=None,
        )

        catalogue = self.client.get(reverse("departmental_exams:my_questions"))
        self.assertEqual(catalogue.status_code, 200)
        self.assertNotContains(catalogue, "Course A bank wording")
        self.assertNotContains(catalogue, "Course A historical wording")

        edit_url = reverse("departmental_exams:my_question_edit", args=[item.id])
        historical_url = reverse(
            "departmental_exams:my_historical_question_edit", args=[historical.id]
        )
        edit_payload = {
            **self.payload("Unauthorized revision"),
            "content_format": Question.ContentFormat.PLAIN_TEXT,
            "expected_item_revision": 1,
        }
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertEqual(self.client.post(edit_url, edit_payload).status_code, 403)
        self.assertEqual(self.client.get(historical_url).status_code, 403)
        edit_payload["expected_item_revision"] = 0
        self.assertEqual(self.client.post(historical_url, edit_payload).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.current_revision, 1)
        self.assertFalse(QuestionBankItem.objects.filter(origin_question=historical).exists())
    def test_corrected_submitted_question_replaces_source_card_without_rewriting_history(self):
        self._enable()
        source = self._source_question("Original submitted wording")
        revision = revise_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            historical_question_id=source.id,
            expected_revision=0,
            payload=self.payload("Corrected future wording"),
        )
        source.refresh_from_db()
        self.assertEqual(source.question_text, "Original submitted wording")
        page = self.client.get(self.url)
        matching = [
            entry for entry in page.context["eligible_items"]
            if entry.get("bank_revision") == revision
        ]
        self.assertEqual(len(matching), 1)
        self.assertNotContains(page, "Original submitted wording")
        self.assertContains(page, "Corrected future wording")

    def test_bank_revision_change_rejects_stale_use_selection(self):
        self._enable()
        item = create_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            payload=self.payload("Before stale Use"),
        )
        page = self.client.get(self.url)
        old_entry = next(
            entry for entry in page.context["eligible_items"]
            if entry.get("bank_item") == item
        )
        revise_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            item_id=item.id,
            expected_revision=1,
            payload=self.payload("After stale Use"),
        )
        self.assertEqual(self._copy(page, old_entry["token"]).status_code, 409)
        self.assertFalse(self.destination.questions.exists())

    def test_whole_case_bank_revisions_are_atomic(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        item = create_bank_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            case={"title": "Bank Case", "stimulus": "Case narrative"},
            first_member=self.payload("First linked MCQ"),
        )
        revise_bank_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            item_id=item.id,
            expected_revision=1,
            member_payload=self.payload("Second linked MCQ"),
            append_member=True,
        )
        self.assertEqual(QuestionBankRevision.objects.get(item=item, revision=1).members.count(), 1)
        self.assertEqual(QuestionBankRevision.objects.get(item=item, revision=2).members.count(), 2)
        with self.assertRaises(ValidationError):
            revise_bank_case(
                actor=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                item_id=item.id,
                expected_revision=2,
                member_payload={**self.payload("Invalid linked MCQ"), "correct_answer": "Z"},
                append_member=True,
            )
        item.refresh_from_db()
        self.assertEqual(item.current_revision, 2)
        self.assertEqual(QuestionBankRevision.objects.filter(item=item).count(), 2)

    def test_authoring_scope_cannot_cross_course(self):
        with self.assertRaises(PermissionDenied):
            create_bank_question(
                actor=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                course_id=self.destination_course.course_id + 100000,
                payload=self.payload("Wrong course"),
            )

    def test_exact_source_and_filters(self):
        self._enable()
        own = self._source_question("Calculus retained", difficulty="DIFFICULT")
        self._source_question("Other text", difficulty="EASY")
        self._source_question("Another faculty secret", source=self._previous_source(owner=self.other))
        page = self.client.get(self.url, {"difficulty": "DIFFICULT", "search": "Calculus"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual([item["object"].id for item in page.context["page"].object_list], [own.id])
        self.assertNotContains(page, "Another faculty secret")
        self.assertEqual(self._copy(page, "q:999:tampered", difficulty="DIFFICULT", search="Calculus").status_code, 409)
        self.assertFalse(self.destination.questions.exists())

    def test_latest_submitted_version_remains_source_during_unfinished_correction(self):
        self._enable()
        self._source_question("Superseded answer")
        self.previous.active_marker = None
        self.previous.save(update_fields=["active_marker", "updated_at"])
        latest = FacultyContribution.objects.create(
            cycle_course=self.previous.cycle_course, faculty_user=self.faculty,
            source_assignment=self.previous.source_assignment, source_campus=self.campus,
            quota_snapshot=50, configuration_revision_snapshot=1,
            status=FacultyContribution.Status.SUBMITTED, submitted_at=timezone.now(),
            supersedes=self.previous)
        original_source = self.previous.eligibility_sources.first()
        FacultyContributionEligibilitySource.objects.create(
            contribution=latest, assignment=original_source.assignment,
            assignment_id_snapshot=original_source.assignment_id_snapshot,
            offering_id_snapshot=original_source.offering_id_snapshot,
            tenant_id_snapshot=original_source.tenant_id_snapshot,
            campus_id_snapshot=original_source.campus_id_snapshot,
            eligibility_proven_at=original_source.eligibility_proven_at)
        self._source_question("Latest accepted answer", source=latest)
        latest.active_marker = None
        latest.save(update_fields=["active_marker", "updated_at"])
        FacultyContribution.objects.create(
            cycle_course=latest.cycle_course, faculty_user=self.faculty,
            source_assignment=latest.source_assignment, source_campus=self.campus,
            quota_snapshot=50, configuration_revision_snapshot=1,
            status=FacultyContribution.Status.DRAFT, supersedes=latest)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual([entry["object"].question_text for entry in page.context["page"].object_list],
                         ["Latest accepted answer"])
        self.assertNotContains(page, "Superseded answer")

    def test_source_marker_change_rejects_stale_post(self):
        self._enable()
        self._source_question("Stable until correction")
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        self.previous.active_marker = None
        self.previous.save(update_fields=["active_marker", "updated_at"])
        result = self._copy(page, token)
        self.assertEqual(result.status_code, 409)
        self.assertFalse(self.destination.questions.exists())

    def test_future_period_is_not_a_source(self):
        self._enable()
        self._source_question("Future content")
        year = self.previous.cycle_course.cycle.academic_year
        year.start_date = "2027-06-01"
        year.end_date = "2028-05-31"
        year.save(update_fields=["start_date", "end_date", "updated_at"])
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["page"].object_list), 0)

    def test_draft_history_excluded_and_authenticated_cross_batch_copy(self):
        self._enable()
        self._source_question("Unsubmitted old question")
        self.previous.status = FacultyContribution.Status.DRAFT
        self.previous.submitted_at = None
        self.previous.save(update_fields=["status", "submitted_at", "updated_at"])
        self.assertEqual(len(self.client.get(self.url).context["page"].object_list), 0)
        self.previous.status = FacultyContribution.Status.SUBMITTED
        self.previous.submitted_at = timezone.now()
        self.previous.save(update_fields=["status", "submitted_at", "updated_at"])
        for index in range(12):
            self._source_question(f"Other historical item {index}")
        first_page = self.client.get(self.url)
        second_page = self.client.get(self.url, {"batch": "1", "page": "2"})
        self.assertEqual(len(first_page.context["page"].object_list), 12)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(second_page.json()["next_page"], None)
        self.assertIn("Other historical item 11", second_page.json()["html"])
        self.assertNotIn("Unsubmitted old question", second_page.json()["html"])
        self.assertEqual(self.client.get(self.url, {"batch": "1", "page": "99"}).json()["html"], "")
        self.assertNotContains(first_page, "Page 1 of")
        source_ids_before = list(self.previous.questions.order_by("position").values_list("id", flat=True))
        first_token = first_page.context["page"].object_list[0]["token"]
        last_token = self.client.get(self.url, {"page": "2"}).context["page"].object_list[0]["token"]
        result = self._copy(first_page, first_token, last_token)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(list(self.destination.questions.order_by("position").values_list(
            "question_text", flat=True)), ["Unsubmitted old question", "Other historical item 11"])
        self.assertEqual(list(self.previous.questions.order_by("position").values_list("id", flat=True)),
                         source_ids_before)

    def test_cross_batch_selection_rechecks_filters_and_source_versions(self):
        self._enable()
        for index in range(13):
            self._source_question(f"Historical item {index}")
        first = self.client.get(self.url).context["page"].object_list[0]
        last = self.client.get(self.url, {"page": "2"}).context["page"].object_list[0]
        self.assertEqual(self._copy(None, first["token"], last["token"],
                                    search="Historical item 0").status_code, 409)
        self.assertFalse(self.destination.questions.exists())
        last["object"].question_text = "Edited historical item"
        last["object"].revision += 1
        last["object"].save(update_fields=["question_text", "revision"])
        self.assertEqual(self._copy(None, first["token"], last["token"]).status_code, 409)
        self.assertFalse(self.destination.questions.exists())

    def test_other_campus_source_requires_current_exact_permission_and_obeys_deny(self):
        self._enable()
        other_role = UserRole.objects.get(user=self.faculty, campus=self.campus).role
        UserRole.objects.create(user=self.faculty, role=other_role, tenant=self.tenant,
                                campus=self.other_campus, department=self.other_department)
        source = self._previous_source(campus=self.other_campus)
        historical = self._source_question("Other-campus owned history", source=source)
        bank_only = create_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.other_campus.id,
            course_id=self.destination_course.course_id,
            payload=self.payload("Other-campus bank-only question"),
        )
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Other-campus owned history")
        self.assertContains(page, "Other-campus bank-only question")
        revision = revise_bank_question(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.other_campus.id,
            historical_question_id=historical.id,
            expected_revision=0,
            payload=self.payload("Other-campus corrected revision"),
        )
        source.source_assignment.response_status = FacultyAssignment.ResponseStatus.DECLINED
        source.source_assignment.accepted_at = None
        source.source_assignment.save(
            update_fields=["response_status", "accepted_at", "updated_at"]
        )
        adopted = self.client.get(self.url)
        self.assertEqual(adopted.status_code, 200)
        self.assertContains(adopted, "Other-campus corrected revision")
        self.assertNotContains(adopted, "Other-campus owned history")
        self.assertNotContains(adopted, "Other-campus bank-only question")
        self.assertEqual(
            [
                entry["bank_revision"].id
                for entry in adopted.context["eligible_items"]
                if entry.get("bank_revision") == revision
            ],
            [revision.id],
        )
        adopted_entry = next(
            entry
            for entry in adopted.context["eligible_items"]
            if entry.get("bank_revision") == revision
        )
        copied = self._copy(adopted, adopted_entry["token"])
        self.assertEqual(copied.status_code, 302)
        self.assertEqual(
            list(
                self.destination.questions.values_list("question_text", flat=True)
            ),
            ["Other-campus corrected revision"],
        )
        UserPermission.objects.create(
            user=self.faculty, permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant, campus=self.other_campus,
            grant_type=UserPermission.GrantType.DENY)
        denied = self.client.get(self.url)
        self.assertEqual(denied.status_code, 200)
        self.assertNotContains(denied, "Other-campus corrected revision")
        self.destination.refresh_from_db()
        self.assertEqual(self._copy(None, adopted_entry["token"]).status_code, 409)
        self.assertEqual(bank_only.current_revision, 1)

    def test_exact_course_id_excludes_other_course_and_destination_deny_blocks_route(self):
        self._enable()
        self._source_question("Same course allowed")
        other_course = self.make_course(cycle=self.previous.cycle_course.cycle, code="NOT-SAME")
        assignment = self.make_assignment(other_course, self.faculty)
        other_source = FacultyContribution.objects.create(
            cycle_course=other_course, faculty_user=self.faculty,
            source_assignment=assignment, source_campus=self.campus,
            quota_snapshot=50, configuration_revision_snapshot=1,
            status=FacultyContribution.Status.SUBMITTED, submitted_at=timezone.now())
        FacultyContributionEligibilitySource.objects.create(
            contribution=other_source, assignment=assignment,
            assignment_id_snapshot=assignment.id,
            offering_id_snapshot=assignment.offering_id,
            tenant_id_snapshot=self.tenant.id, campus_id_snapshot=self.campus.id,
            eligibility_proven_at=timezone.now())
        self._source_question("Different course private content", source=other_source)
        page = self.client.get(self.url)
        self.assertContains(page, "Same course allowed")
        self.assertNotContains(page, "Different course private content")
        UserPermission.objects.create(
            user=self.faculty, permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant, campus=self.campus,
            grant_type=UserPermission.GrantType.DENY)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_active_import_blocks_reuse_get_and_post(self):
        self._enable()
        self._source_question("Earlier question")
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS)
        writer.writerow([self.payload("Pending import question")[field] for field in CSV_HEADERS])
        writer.writerow([self.payload("Second pending import question")[field] for field in CSV_HEADERS])
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.destination.id,
            uploaded_file=SimpleUploadedFile("pending.csv", stream.getvalue().encode()),
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.destination.revision)
        QuestionCSVImportService.process_next_chunk(
            token=batch.token, expected_file_sha256=batch.file_sha256,
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            chunk_size=1)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self._copy(page, token).status_code, 403)
        self.assertFalse(self.destination.questions.filter(question_text="Earlier question").exists())

    def test_other_contributors_pending_import_claim_is_skipped_confidentially(self):
        self._enable()
        self._source_question("Pending reserved question")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS)
        for text in ("Other contributor accepted", "Pending reserved question"):
            writer.writerow([self.payload(text)[field] for field in CSV_HEADERS])
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.other_destination.id,
            uploaded_file=SimpleUploadedFile("other.csv", stream.getvalue().encode()),
            user=self.other, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.other_destination.revision)
        QuestionCSVImportService.process_next_chunk(
            token=batch.token, expected_file_sha256=batch.file_sha256,
            user=self.other, tenant_id=self.tenant.id, campus_id=self.campus.id,
            chunk_size=1)
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        response = self.client.post(self.url, {
            "expected_contribution_revision": self.destination.revision,
            "selected_items": [token],
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 duplicate question skipped")
        self.assertNotContains(response, "Other contributor accepted")
        self.assertFalse(self.destination.questions.exists())

    def test_duplicates_skip_without_mutating_draft_and_no_source_period_collision(self):
        self._enable()
        self._source_question("Repeated stem")
        existing = QuestionMutationService.create(
            contribution_id=self.destination.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.destination.revision,
            payload=self.payload("Repeated stem"))
        self.destination.refresh_from_db()
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        response = self._copy(page, token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.destination.questions.count(), 1)
        self.assertEqual(self.destination.questions.first().id, existing.id)
        self.destination.refresh_from_db()
        self.assertEqual(self.destination.revision, existing.contribution.revision)

    def test_batch_duplicate_skips_but_similar_question_copies(self):
        self._enable()
        original = self._source_question("What is 1 + 1?")
        self._source_question("What is 1 + 1?")
        near = self._source_question("What is 1 + 2?")
        page = self.client.get(self.url)
        tokens = [entry["token"] for entry in page.context["page"].object_list]
        response = self.client.post(self.url, {
            "expected_contribution_revision": self.destination.revision,
            "selected_items": tokens,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 questions copied")
        self.assertContains(response, "1 duplicate question skipped")
        self.assertEqual(list(self.destination.questions.order_by("position").values_list(
            "question_text", flat=True)), [original.question_text, near.question_text])
        self.assertEqual(self.previous.questions.count(), 3)

    def test_capacity_is_checked_before_duplicate_skip(self):
        self._enable()
        self._source_question("Already represented")
        QuestionMutationService.create(
            contribution_id=self.destination.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.destination.revision,
            payload=self.payload("Already represented"))
        self.destination.refresh_from_db()
        Question.objects.bulk_create([
            Question(contribution=self.destination, position=index + 1,
                     **QuestionPayloadService.validate(self.payload(f"Filler {index}")))
            for index in range(1, 50)
        ])
        with transaction.atomic():
            reconcile(self.destination_course)
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        result = self._copy(page, token)
        self.assertEqual(result.status_code, 409)
        self.assertContains(result, "Deselect 1 question", status_code=409)
        self.assertEqual(self.destination.questions.count(), 50)

    def test_stale_destination_revision_blocks_copy(self):
        self._enable()
        self._source_question("Never copied after Draft edit")
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        QuestionMutationService.create(
            contribution_id=self.destination.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.destination.revision,
            payload=self.payload("New Draft edit"))
        result = self._copy(page, token)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(list(self.destination.questions.values_list("question_text", flat=True)),
                         ["New Draft edit"])

    def test_plain_and_canonical_rich_content_are_preserved_in_new_questions(self):
        self._enable()
        plain = self._source_question("Literal <mark> must remain plain")
        rich = Question.objects.create(
            contribution=self.previous, position=2,
            **QuestionPayloadService.validate(rich_payload()))
        page = self.client.get(self.url)
        self.assertContains(page, "&lt;mark&gt;")
        self.assertContains(page, "Cash")
        tokens = [entry["token"] for entry in page.context["page"].object_list]
        response = self._copy(page, *tokens)
        self.assertEqual(response.status_code, 302)
        copies = list(self.destination.questions.order_by("position"))
        self.assertEqual(len(copies), 2)
        for source, copy in zip((plain, rich), copies):
            for field in (*QuestionPayloadService.TEXT_FIELDS, "content_format",
                          "correct_answer", "difficulty"):
                self.assertEqual(getattr(copy, field), getattr(source, field))


class QuestionReuseCaseHTTPTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.destination_course = self.parent
        self.destination = self.contribution
        self.previous = QuestionReuseHTTPTests._previous_source(self)
        QuestionReuseHTTPTests._enable(self)
        self.url = reverse("departmental_exams:question_reuse", args=[self.destination.id])

    def _source_case(self, *, title="Old Case", narrative="<p>Historical narrative</p>"):
        canonical = canonicalize_scenario_content(narrative).html
        with _exam_structure_lifecycle_service_scope():
            blueprint = ExamBlueprint.objects.create(
                cycle_course=self.previous.cycle_course, mode=ExamBlueprint.Mode.USE_SECTIONS,
                created_by=self.configurer, updated_by=self.configurer,
                structure_frozen_at=timezone.now(), structure_frozen_by=self.configurer,
                structure_final_item_count=50)
            section = ExamSection.objects.create(blueprint=blueprint, title="Old title",
                                                 display_order=1, item_quota=50)
        case = ExamScenario.objects.create(
            blueprint=blueprint, section=section, contribution=self.previous,
            title=title, stimulus=canonical, content_format=ExamScenario.ContentFormat.RICH_HTML_V1,
            created_by=self.faculty, updated_by=self.faculty, active_marker=None)
        first = QuestionReuseHTTPTests._source_question(self, "Case first", difficulty="EASY")
        second = QuestionReuseHTTPTests._source_question(self, "Case second", difficulty="DIFFICULT")
        ExamScenarioMember.objects.create(scenario=case, question=first, position=1, active_marker=None)
        ExamScenarioMember.objects.create(scenario=case, question=second, position=2, active_marker=None)
        return case, first, second

    def _post(self, token, **extra):
        self.destination.refresh_from_db()
        return self.client.post(self.url, {
            "expected_contribution_revision": self.destination.revision,
            "selected_items": [token], **extra,
        })

    def test_whole_case_duplicate_linked_choices_show_bound_error_without_write(self):
        create_url = reverse(
            "departmental_exams:my_case_create",
            args=[self.campus.id, self.destination_course.course_id],
        )
        linked = rich_payload(question_text="<p>Which entry is correct?</p>")
        linked["choice_a"] = linked["choice_b"]
        posted = {
            "expected_item_revision": 0,
            "title": "Retained Case title",
            "stimulus": "<p>Retained Case narrative</p>",
            **linked,
        }
        before_items = QuestionBankItem.objects.count()
        before_revisions = QuestionBankRevision.objects.count()

        response = self.client.post(create_url, posted)

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "Choices must be distinct after text normalization.", status_code=400
        )
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertIn(
            "Choices must be distinct after text normalization.", form.non_field_errors()
        )
        for field in ("title", "stimulus", "question_text", "choice_a", "choice_b"):
            self.assertEqual(form[field].value(), posted[field])
        self.assertContains(response, "Retained Case title", status_code=400)
        self.assertContains(response, "Retained Case narrative", status_code=400)
        self.assertContains(response, "Which entry is correct?", status_code=400)
        self.assertEqual(QuestionBankItem.objects.count(), before_items)
        self.assertEqual(QuestionBankRevision.objects.count(), before_revisions)

    def test_rich_whole_case_and_linked_mcq_save_edit_redisplay_and_copy(self):
        create_url = reverse("departmental_exams:my_case_create", args=[
            self.campus.id, self.destination_course.course_id,
        ])
        create_page = self.client.get(create_url)
        self.assertEqual(create_page.status_code, 200)
        self.assertContains(create_page, "data-case-editor-form")
        self.assertContains(create_page, 'data-question-rich-field="question_text"')
        preview_url = reverse("departmental_exams:my_content_preview", args=[
            self.campus.id, self.destination_course.course_id,
        ])
        case_html = "<p><strong>Original Case narrative</strong></p>"
        preview = self.client.post(preview_url, {
            "input_format": "html", "stimulus": case_html,
        })
        self.assertEqual(preview.status_code, 200)
        self.assertIn("<strong>Original Case narrative</strong>", preview.json()["html"])
        payload = rich_payload(question_text="<p><em>Original linked stem</em></p>")
        self.assertEqual(self.client.post(create_url, {
            "expected_item_revision": 0, "title": "Rich bank Case",
            "stimulus": case_html, **payload,
        }).status_code, 302)
        item = QuestionBankItem.objects.get(
            owner=self.faculty, kind=QuestionBankItem.Kind.CASE,
            origin_scenario__isnull=True,
        )
        first = QuestionBankRevision.objects.get(item=item, revision=1)
        self.assertIn("<strong>Original Case narrative</strong>", first.stimulus)
        self.assertEqual(first.members.get(position=1).content_format, Question.ContentFormat.RICH_HTML_V1)
        case_url = reverse("departmental_exams:my_case_edit", args=[item.id])
        self.assertContains(self.client.get(case_url), "Original Case narrative")
        self.assertEqual(self.client.post(case_url, {
            "expected_item_revision": 1, "title": "Rich bank Case",
            "stimulus": "<p><u>Revised Case narrative</u></p>",
        }).status_code, 302)
        member_url = reverse("departmental_exams:my_case_member_edit", args=[item.id, 1])
        self.assertContains(self.client.get(member_url), "Original linked stem")
        self.assertEqual(self.client.post(member_url, {
            **payload, "question_text": "<p><strong>Revised linked stem</strong></p>",
            "expected_item_revision": 2,
        }).status_code, 302)
        current = QuestionBankRevision.objects.get(item=item, revision=3)
        self.assertIn("<u>Revised Case narrative</u>", current.stimulus)
        self.assertIn("<strong>Revised linked stem</strong>", current.members.get(position=1).question_text)
        first.refresh_from_db()
        self.assertIn("Original Case narrative", first.stimulus)
        self.assertIn("Original linked stem", first.members.get(position=1).question_text)
        page = self.client.get(self.url)
        entry = next(row for row in page.context["eligible_items"] if row.get("bank_item") == item)
        self.assertEqual(self._post(entry["token"], target_section_id=str(self.section_a.id)).status_code, 302)
        copied = ExamScenario.objects.get(contribution=self.destination, source_bank_revision=current)
        self.assertEqual(copied.stimulus, current.stimulus)
        linked = copied.members.get(position=1).question
        self.assertEqual(linked.question_text, current.members.get(position=1).question_text)
        self.assertEqual(linked.choice_a, current.members.get(position=1).choice_a)

    def test_current_bank_case_revision_copies_as_one_provenanced_graph(self):
        item = create_bank_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            case={"title": "Bank Case", "stimulus": "Case narrative"},
            first_member=self.payload("First bank member"),
        )
        revise_bank_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            item_id=item.id,
            expected_revision=1,
            member_payload=self.payload("Second bank member"),
            append_member=True,
        )
        page = self.client.get(self.url)
        entry = next(row for row in page.context["eligible_items"] if row.get("bank_item") == item)
        response = self._post(entry["token"], target_section_id=str(self.section_a.id))
        self.assertEqual(response.status_code, 302)
        scenario = ExamScenario.objects.get(contribution=self.destination)
        self.assertEqual(scenario.members.count(), 2)
        self.assertEqual(scenario.source_bank_revision.revision, 2)
        self.assertEqual(
            set(self.destination.questions.values_list("source_bank_revision__revision", flat=True)),
            {2},
        )

    def test_bank_case_id_collision_does_not_enter_historical_locks_or_audit_ids(self):
        item = create_bank_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            course_id=self.destination_course.course_id,
            case={"title": "Collision bank Case", "stimulus": "Bank narrative"},
            first_member=self.payload("Collision bank member"),
        )
        revision = QuestionBankRevision.objects.get(item=item, revision=1)
        historical_case, _first, _second = self._source_case(
            title="Unselected colliding historical Case"
        )
        self.assertEqual(revision.id, historical_case.id)
        page = self.client.get(self.url)
        entry = next(
            row for row in page.context["eligible_items"] if row.get("bank_item") == item
        )

        response = self._post(entry["token"], target_section_id=str(self.section_a.id))

        self.assertEqual(response.status_code, 302)
        audit = AuditLog.objects.filter(
            action="DE_EXAM_QUESTIONS_REUSED",
            entity_id=str(self.destination.id),
        ).latest("id")
        self.assertEqual(audit.metadata_json["source_case_ids"], [])
        self.assertEqual(audit.metadata_json["source_bank_item_ids"], [item.id])
        self.assertEqual(audit.metadata_json["source_bank_revision_ids"], [revision.id])

    def test_bank_case_members_are_position_ordered_for_catalogue_edit_and_copy(self):
        item = QuestionBankItem.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            course=self.destination_course.course,
            owner=self.faculty,
            kind=QuestionBankItem.Kind.CASE,
        )
        revision = QuestionBankRevision.objects.create(
            item=item,
            revision=1,
            title="Reverse insertion Case",
            stimulus=canonicalize_scenario_content("Ordering narrative").html,
            scenario_content_format=ExamScenario.ContentFormat.RICH_HTML_V1,
            created_by=self.faculty,
        )
        second_payload = QuestionPayloadService.validate(self.payload("Position two"))
        first_payload = QuestionPayloadService.validate(self.payload("Position one"))
        QuestionBankCaseMember.objects.create(
            revision=revision, position=2, **second_payload
        )
        QuestionBankCaseMember.objects.create(
            revision=revision, position=1, **first_payload
        )

        my_questions = self.client.get(reverse("departmental_exams:my_questions"))
        my_entry = next(
            row for row in my_questions.context["page"].object_list
            if row.get("item") == item
        )
        self.assertEqual(
            [member.position for member in my_entry["revision"].members.all()],
            [1, 2],
        )
        edit = self.client.get(
            reverse("departmental_exams:my_case_member_edit", args=[item.id, 1])
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.context["form"].initial["question_text"], "Position one")

        page = self.client.get(self.url)
        entry = next(
            row for row in page.context["eligible_items"] if row.get("bank_item") == item
        )
        self.assertEqual([member.position for member in entry["members"]], [1, 2])
        response = self._post(entry["token"], target_section_id=str(self.section_a.id))
        self.assertEqual(response.status_code, 302)
        copied_case = ExamScenario.objects.get(
            contribution=self.destination, source_bank_revision=revision
        )
        self.assertEqual(
            list(
                copied_case.members.order_by("position").values_list(
                    "question__question_text", flat=True
                )
            ),
            ["Position one", "Position two"],
        )

    def test_adopted_cross_campus_case_retains_historical_reuse_after_assignment_decline(self):
        other_role = UserRole.objects.get(user=self.faculty, campus=self.campus).role
        UserRole.objects.create(
            user=self.faculty,
            role=other_role,
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
        )
        source = QuestionReuseHTTPTests._previous_source(
            self, campus=self.other_campus
        )
        self.previous = source
        historical_case, _first, _second = self._source_case(
            title="Other-campus historical Case"
        )
        historical_case.members.update(active_marker=1)
        before = self.client.get(self.url)
        self.assertContains(before, "Other-campus historical Case")

        revision = revise_historical_case(
            actor=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.other_campus.id,
            scenario_id=historical_case.id,
            title="Other-campus corrected Case",
            stimulus="Corrected Case narrative",
        )
        source.source_assignment.response_status = FacultyAssignment.ResponseStatus.DECLINED
        source.source_assignment.accepted_at = None
        source.source_assignment.save(
            update_fields=["response_status", "accepted_at", "updated_at"]
        )

        adopted = self.client.get(self.url)
        self.assertEqual(adopted.status_code, 200)
        self.assertContains(adopted, "Other-campus corrected Case")
        self.assertNotContains(adopted, "Other-campus historical Case")
        entry = next(
            row
            for row in adopted.context["eligible_items"]
            if row.get("bank_revision") == revision
        )
        response = self._post(
            entry["token"], target_section_id=str(self.section_a.id)
        )
        self.assertEqual(response.status_code, 302)
        copied = ExamScenario.objects.get(
            contribution=self.destination,
            source_bank_revision=revision,
        )
        self.assertEqual(copied.title, "Other-campus corrected Case")
        self.assertEqual(
            list(
                copied.members.order_by("position").values_list(
                    "question__question_text", flat=True
                )
            ),
            ["Case first", "Case second"],
        )

        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            tenant=self.tenant,
            campus=self.other_campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        denied = self.client.get(self.url)
        self.assertEqual(denied.status_code, 200)
        self.assertNotContains(denied, "Other-campus corrected Case")
        self.assertEqual(
            self._post(
                entry["token"], target_section_id=str(self.section_a.id)
            ).status_code,
            409,
        )

    def test_linked_historical_mcq_cannot_be_adopted_outside_whole_case(self):
        historical_case, linked, _second = self._source_case(
            title="Protected historical Case"
        )
        historical_case.members.update(active_marker=1)
        standalone = QuestionReuseHTTPTests._source_question(
            self, "Independent historical MCQ"
        )
        direct_url = reverse(
            "departmental_exams:my_historical_question_edit", args=[linked.id]
        )
        payload = {
            **self.payload("Improper standalone correction"),
            "content_format": Question.ContentFormat.PLAIN_TEXT,
            "expected_item_revision": 0,
        }
        before = self.client.get(self.url)
        self.assertContains(before, "Protected historical Case")
        self.assertEqual(self.client.get(direct_url).status_code, 404)
        self.assertEqual(self.client.post(direct_url, payload).status_code, 404)
        with self.assertRaises(Http404):
            revise_bank_question(
                actor=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
                historical_question_id=linked.id,
                expected_revision=0,
                payload=self.payload("Improper service correction"),
            )
        self.assertFalse(QuestionBankItem.objects.filter(origin_question=linked).exists())
        self.assertFalse(QuestionBankRevision.objects.exists())
        self.assertFalse(
            AuditLog.objects.filter(action="DE_MY_QUESTION_REVISED").exists()
        )

        still_available = self.client.get(self.url)
        case_entry = next(
            entry for entry in still_available.context["eligible_items"]
            if entry["kind"] == "case" and entry["object"].id == historical_case.id
        )
        self.assertEqual(
            self._post(case_entry["token"], target_section_id=str(self.section_a.id)).status_code,
            302,
        )
        case_url = reverse(
            "departmental_exams:my_historical_case_edit", args=[historical_case.id]
        )
        self.assertEqual(self.client.get(case_url).status_code, 200)
        self.assertEqual(
            self.client.post(case_url, {
                "expected_item_revision": 0,
                "title": "Corrected whole Case",
                "stimulus": "Corrected whole Case narrative",
            }).status_code,
            302,
        )
        self.assertTrue(QuestionBankItem.objects.filter(origin_scenario=historical_case).exists())

        standalone_url = reverse(
            "departmental_exams:my_historical_question_edit", args=[standalone.id]
        )
        self.assertEqual(self.client.get(standalone_url).status_code, 200)
        self.assertEqual(
            self.client.post(standalone_url, {
                **QuestionPayloadService.validate(
                    self.payload("Corrected independent MCQ")
                ),
                "expected_item_revision": 0,
            }).status_code,
            302,
        )
        self.assertTrue(QuestionBankItem.objects.filter(origin_question=standalone).exists())

    def test_submitted_one_member_faculty_case_reuses_as_one_whole_case(self):
        year = AcademicYear.objects.create(
            tenant=self.tenant, code="ONE-MEMBER-OLD", name="Earlier Case year",
            start_date="2024-06-01", end_date="2025-05-31")
        term = Term.objects.create(
            tenant=self.tenant, academic_year=year, code="T1", name="First semester")
        source_cycle = ExaminationCycle.objects.create(
            tenant=self.tenant, academic_year=year, term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            created_by=self.admin)
        self.assertEqual(source_cycle.processing_mode, ExaminationCycle.ProcessingMode.MANUAL_REVIEW)
        source_course = CycleCourse.objects.create(
            cycle=source_cycle, course=self.parent.course,
            responsible_department=self.department)
        destination_offering = self.parent.offering_snapshots.select_related("offering").first().offering
        source_offering = CourseOffering.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.department,
            program=destination_offering.program, section=destination_offering.section,
            academic_year=year, term=term, course=self.parent.course)
        CycleCourseOffering.objects.create(
            cycle_course=source_course, offering=source_offering, campus=self.campus)
        self.make_assignment(source_course, self.faculty, offering=source_offering)
        self.make_configuration(
            source_course, workflow="OPEN", opened_at=timezone.now(),
            deadline=self.future_deadline())
        with _exam_structure_lifecycle_service_scope():
            blueprint = ExamBlueprint.objects.create(
                cycle_course=source_course, mode=ExamBlueprint.Mode.USE_SECTIONS,
                created_by=self.configurer, updated_by=self.configurer,
                structure_frozen_at=timezone.now(), structure_frozen_by=self.configurer,
                structure_final_item_count=50)
            section = ExamSection.objects.create(
                blueprint=blueprint, title="Original section", display_order=1, item_quota=50)
        self.initialize(source_course)
        source = FacultyContribution.objects.get(
            cycle_course=source_course, faculty_user=self.faculty)
        self.assertEqual(source.status, FacultyContribution.Status.DRAFT)
        narrative = "<p>One linked question case</p>"
        case, created = FacultyCaseMutationService.save(
            contribution_id=source.id, user=self.faculty, tenant_id=self.tenant.id,
            campus_id=self.campus.id, expected_contribution_revision=source.revision,
            expected_scenario_revision=0, title="One member source",
            raw_content=narrative, section_id=section.id)
        self.assertTrue(created)
        source.refresh_from_db()
        linked = QuestionMutationService.create(
            contribution_id=source.id, user=self.faculty, tenant_id=self.tenant.id,
            campus_id=self.campus.id, expected_contribution_revision=source.revision,
            payload=self.payload("Single linked question"),
            section_id=section.id, scenario_id=case.id)
        fillers = [Question(
            contribution=source, position=position,
            **QuestionPayloadService.validate(self.payload(f"Source filler {position}")))
            for position in range(2, 51)]
        Question.objects.bulk_create(fillers)
        QuestionBlueprintPlacement.objects.bulk_create([
            QuestionBlueprintPlacement(
                blueprint=blueprint, section=section, question=question,
                placed_by=self.faculty)
            for question in fillers
        ])
        source.refresh_from_db()
        submitted, changed = QuestionMutationService.submit(
            contribution_id=source.id, user=self.faculty, tenant_id=self.tenant.id,
            campus_id=self.campus.id, expected_contribution_revision=source.revision)
        self.assertTrue(changed)
        self.assertEqual(submitted.status, FacultyContribution.Status.SUBMITTED)
        case.refresh_from_db()
        linked.refresh_from_db()
        self.assertEqual(case.members.count(), 1)
        before = {
            "source": (submitted.status, submitted.revision, submitted.submitted_at),
            "case": (case.id, case.title, case.stimulus, case.revision),
            "question": (linked.id, linked.question_text, linked.choice_a,
                         linked.choice_b, linked.choice_c, linked.choice_d,
                         linked.correct_answer, linked.difficulty, linked.revision),
            "member": list(case.members.values_list("id", "question_id", "position")),
        }

        filters = {"content_type": "case", "search": "Single linked question"}
        page = self.client.get(self.url, filters)
        self.assertEqual(page.status_code, 200)
        items = page.context["page"].object_list
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["kind"], items[0]["size"]), ("case", 1))
        self.assertContains(page, 'data-size="1"')
        self.assertContains(page, "One linked question case")
        self.assertContains(page, "Single linked question")
        self.assertContains(page, "reuse-question-stem")
        self.assertContains(page, f'id="reuse-card-case-{items[0]["dom_id"]}"')
        self.assertContains(page, f'id="reuse-member-{linked.id}" data-reuse-member')
        self.assertContains(page, f"Correct answer:</strong> {linked.correct_answer}")
        token = items[0]["token"]
        copied_response = self._post(
            token, target_section_id=str(self.section_b.id), **filters)
        self.assertEqual(copied_response.status_code, 302)
        copied_case = ExamScenario.objects.get(contribution=self.destination)
        copied_members = list(copied_case.members.select_related("question").order_by("position"))
        self.assertEqual((copied_case.title, copied_case.stimulus, copied_case.section_id),
                         (case.title, case.stimulus, self.section_b.id))
        self.assertEqual(len(copied_members), 1)
        copy = copied_members[0].question
        self.assertEqual((copied_members[0].position, copy.position), (1, 1))
        for field in (*QuestionPayloadService.TEXT_FIELDS, "content_format",
                      "correct_answer", "difficulty"):
            self.assertEqual(getattr(copy, field), getattr(linked, field))
        self.assertEqual(copy.blueprint_placement.section_id, self.section_b.id)
        submitted.refresh_from_db()
        case.refresh_from_db()
        linked.refresh_from_db()
        self.assertEqual((submitted.status, submitted.revision, submitted.submitted_at),
                         before["source"])
        self.assertEqual((case.id, case.title, case.stimulus, case.revision), before["case"])
        self.assertEqual((linked.id, linked.question_text, linked.choice_a,
                          linked.choice_b, linked.choice_c, linked.choice_d,
                          linked.correct_answer, linked.difficulty, linked.revision),
                         before["question"])
        self.assertEqual(list(case.members.values_list("id", "question_id", "position")),
                         before["member"])

    def test_historical_whole_case_filter_and_explicit_section(self):
        source_case, first, second = self._source_case()
        response = self.client.get(self.url, {"difficulty": "DIFFICULT", "search": "Case second"})
        self.assertEqual(response.status_code, 200)
        items = response.context["page"].object_list
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "case")
        self.assertEqual(items[0]["size"], 2)
        self.assertContains(response, "Case first")
        self.assertContains(response, "Historical narrative")
        token = items[0]["token"]
        filters = {"difficulty": "DIFFICULT", "search": "Case second"}
        self.assertEqual(self._post(token, **filters).status_code, 409)
        result = self._post(token, target_section_id=str(self.section_b.id), **filters)
        self.assertEqual(result.status_code, 302)
        copied = ExamScenario.objects.get(contribution=self.destination)
        self.assertEqual((copied.title, copied.stimulus, copied.section_id),
                         (source_case.title, source_case.stimulus, self.section_b.id))
        members = list(copied.members.order_by("position"))
        self.assertEqual([row.question.question_text for row in members],
                         [first.question_text, second.question_text])
        self.assertEqual([row.question.blueprint_placement.section_id for row in members],
                         [self.section_b.id, self.section_b.id])
        self.assertEqual(self.previous.questions.count(), 2)
        self.assertIsNone(ExamScenario.objects.get(pk=source_case.id).active_marker)

    def test_empty_historical_case_is_not_selectable(self):
        case, _first, _second = self._source_case(title="Empty historical Case")
        case.members.all().delete()
        page = self.client.get(self.url, {
            "content_type": "case", "search": "Empty historical Case"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["page"].object_list), 0)

    def test_shared_case_member_is_not_offered_as_standalone(self):
        question = QuestionReuseHTTPTests._source_question(self, "Shared context question")
        # An Admin-owned Case link is excluded even if its current marker is archived.
        ExamScenarioMember.objects.create(
            scenario=ExamScenario.objects.create(
                blueprint=self.blueprint, section=self.section_a, contribution=None,
                title="Shared", stimulus=canonicalize_scenario_content("<p>Shared context</p>").html,
                content_format=ExamScenario.ContentFormat.RICH_HTML_V1,
                created_by=self.configurer, updated_by=self.configurer),
            question=question, position=1, active_marker=None)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["page"].object_list), 0)
        self.assertContains(page, "Case context is shared or incomplete")

    def test_owned_case_with_member_outside_selected_source_is_excluded_whole(self):
        case, _first, _second = self._source_case()
        other_source = QuestionReuseHTTPTests._previous_source(self)
        outside = QuestionReuseHTTPTests._source_question(
            self, "Outside source member", source=other_source)
        ExamScenarioMember.objects.create(
            scenario=case, question=outside, position=3, active_marker=None)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["page"].object_list), 0)
        self.assertContains(page, "Case context is shared or incomplete")

    def test_case_duplicate_is_atomic_and_capacity_rejects_full_selection(self):
        source_case, first, _second = self._source_case(
            narrative="<p>Confidential narrative ₱500</p>")
        existing_case = self.save_case(html=source_case.stimulus)
        current_first = self.add_question(scenario=existing_case, text=first.question_text)
        self.assertEqual(case_member_identity(first, source_case),
                         case_member_identity(current_first, existing_case))
        self.assertIn(case_member_identity(first, source_case), pool_claims(self.parent)[1])
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        self.contribution.refresh_from_db()
        before_revision = self.contribution.revision
        result = self._post(token, target_section_id=str(self.section_a.id))
        self.assertEqual(result.status_code, 302)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, before_revision)
        self.assertEqual(ExamScenario.objects.filter(contribution=self.contribution).count(), 1)
        self.assertEqual(self.contribution.questions.count(), 1)

    def test_over_capacity_rejects_whole_case_without_truncation(self):
        self._source_case()
        questions = [Question(contribution=self.destination, position=index,
                              **QuestionPayloadService.validate(self.payload(f"Filler {index}")))
                     for index in range(1, 50)]
        Question.objects.bulk_create(questions)
        QuestionBlueprintPlacement.objects.bulk_create([
            QuestionBlueprintPlacement(blueprint=self.blueprint, section=self.section_a,
                                       question=question, placed_by=self.faculty)
            for question in questions])
        with transaction.atomic():
            reconcile(self.destination_course)
        page = self.client.get(self.url)
        self.assertEqual(page.context["remaining"], 1)
        token = page.context["page"].object_list[0]["token"]
        result = self._post(token, target_section_id=str(self.section_a.id))
        self.assertEqual(result.status_code, 409)
        self.assertContains(result, "Deselect 1 question", status_code=409)
        self.assertEqual(self.destination.questions.count(), 49)

    def test_sole_frozen_section_is_selected_automatically(self):
        self._source_case()
        with _exam_structure_lifecycle_service_scope():
            self.section_b.delete()
            self.section_a.item_quota = 50
            self.section_a.save(update_fields=["item_quota", "updated_at"])
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["sole_section"].id, self.section_a.id)
        token = page.context["page"].object_list[0]["token"]
        result = self._post(token)
        self.assertEqual(result.status_code, 302)
        copied = ExamScenario.objects.get(contribution=self.destination)
        self.assertEqual(copied.section_id, self.section_a.id)

    def test_changed_historical_case_member_set_rejects_stale_selection(self):
        case, _first, _second = self._source_case()
        page = self.client.get(self.url)
        token = page.context["page"].object_list[0]["token"]
        member = case.members.order_by("position").last()
        member.position = 3
        member.save(update_fields=["position", "updated_at"])
        result = self._post(token, target_section_id=str(self.section_a.id))
        self.assertEqual(result.status_code, 409)
        self.assertFalse(self.destination.questions.exists())
