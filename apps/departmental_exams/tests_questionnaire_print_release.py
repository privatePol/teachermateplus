from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .automatic_workflow import AutomaticGenerationSummaryService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
    QuestionnairePrintRelease,
)
from .questionnaire_printing import QuestionnairePrintReleaseService
from .stage4_test_support import Stage4TestCase


MANILA = ZoneInfo("Asia/Manila")


class QuestionnairePrintReleaseTests(Stage4TestCase):
    PRINT_SCHOOL_NAME = "National College of Business and Arts"
    PRINT_CAMPUS_LINE = "Cubao-Fairview-Taytay"

    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_NAME",
            self.PRINT_SCHOOL_NAME,
            tenant_id=self.tenant.id,
        )
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            self.PRINT_CAMPUS_LINE,
            tenant_id=self.tenant.id,
        )
        self.manager_user = self.make_user(
            "questionnaire-release-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
        )
        self.faculty = self.make_user(
            "questionnaire-print-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent = self.make_course(cycle=cycle, department=None, code="PRINT-101")
        self.configuration = self.make_configuration(
            self.parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now() - timezone.timedelta(days=2),
        )
        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.parent.offering_snapshots.get().offering,
            faculty_user=self.faculty,
            accepted_by=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(),
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self.contribution = FacultyContribution.objects.create(
            cycle_course=self.parent,
            faculty_user=self.faculty,
            source_assignment=self.assignment,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=self.configuration.revision,
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        FacultyContributionEligibilitySource.objects.create(
            contribution=self.contribution,
            assignment=self.assignment,
            assignment_id_snapshot=self.assignment.id,
            offering_id_snapshot=self.assignment.offering_id,
            tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=self.campus.id,
        )
        self.questions = [
            Question.objects.create(
                contribution=self.contribution,
                question_text=f"Safe question {position}",
                choice_a=f"Choice A{position}",
                choice_b=f"Choice B{position}",
                choice_c=f"Choice C{position}",
                choice_d=f"Choice D{position}",
                correct_answer="D",
                difficulty=("EASY" if position == 1 else "MODERATE"),
                position=position,
            )
            for position in (1, 2)
        ]
        self.r2 = self._make_revision(self.parent, revision_number=2)

    def _make_revision(
        self,
        parent,
        *,
        revision_number,
        supersedes=None,
        with_sets=True,
    ):
        revision = ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=revision_number,
            source_input_fingerprint=str(revision_number) * 64,
            algorithm_version="automatic-print-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="r" * 64,
            final_item_count_snapshot=2,
            request_token_digest=(str(revision_number + 3) * 64)[:64],
            supersedes=supersedes,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=4,
        )
        if not with_sets:
            return revision
        for set_code in (GeneratedExamSet.SetCode.A, GeneratedExamSet.SetCode.B):
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot={"PRIVATE-CAMPUS": 2},
                difficulty_quotas_snapshot={
                    "EASY": 1,
                    "MODERATE": 1,
                    "DIFFICULT": 0,
                },
                section_quotas_snapshot={"0": 2},
                item_count=2,
            )
            for position, question in enumerate(self.questions, start=1):
                GeneratedExamItem.objects.create(
                    generated_set=generated_set,
                    position=position,
                    source_question=question,
                    source_question_revision=question.revision,
                    source_question_digest="SECRET-DIGEST-" + "x" * 50,
                    source_contributor=self.faculty,
                    source_contributor_id_snapshot=self.faculty.id,
                    source_contributor_name_snapshot="CONFIDENTIAL CONTRIBUTOR",
                    source_campus=self.campus,
                    campus_code_snapshot="PRIVATE-CAMPUS",
                    campus_name_snapshot="Private provenance campus",
                    difficulty_snapshot=("EASY" if position == 1 else "MODERATE"),
                    section_title_snapshot="Internal section",
                    question_text_snapshot=f"Released {set_code} question {position}",
                    choices_snapshot=[
                        f"{set_code} choice A{position}",
                        f"{set_code} choice B{position}",
                        f"{set_code} choice C{position}",
                        f"{set_code} choice D{position}",
                    ],
                    correct_answer_snapshot="D",
                )
        return revision

    def _release(self, *, revision=None, print_from=None, print_until=None):
        now = timezone.now()
        return QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id,
            revision_id=(revision or self.r2).id,
            tenant_id=self.tenant.id,
            actor=self.manager_user,
            print_from=print_from or now - timezone.timedelta(minutes=5),
            print_until=print_until or now + timezone.timedelta(hours=2),
        )

    def _faculty_client(self, user=None):
        client = Client()
        client.force_login(user or self.faculty)
        return client

    def _print_url(self, release, set_code="A", contribution=None):
        return reverse(
            "departmental_exams:questionnaire_print",
            args=[
                (contribution or self.contribution).id,
                release.id,
                set_code,
            ],
        )

    def _admin_print_url(self, revision=None, set_code="A"):
        return reverse(
            "departmental_exams:admin_questionnaire_print",
            args=[(revision or self.r2).id, set_code],
        )

    def test_admin_direct_prints_exact_set_a_and_b_without_faculty_release(self):
        client = Client()
        client.force_login(self.manager_user)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        page = client.get(reverse("departmental_exams:questionnaire_print_release"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Print Set A")
        self.assertContains(page, "Print Set B")

        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                response = client.get(self._admin_print_url(set_code=set_code))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["revision_number"], 2)
                self.assertEqual(response.context["set_code"], set_code)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])
                body = response.content.decode()
                self.assertIn(f"Released {set_code} question 1", body)
                self.assertNotIn("CONFIDENTIAL CONTRIBUTOR", body)
                self.assertNotIn("PRIVATE-CAMPUS", body)
                self.assertNotIn("Private provenance campus", body)
                self.assertNotIn("SECRET-DIGEST", body)
                self.assertNotIn("MODERATE", body)

        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        audits = AuditLog.objects.filter(
            action="DE_ADMIN_QUESTIONNAIRE_PRINT_SET_ACCESSED"
        )
        self.assertEqual(audits.count(), 2)
        for audit in audits:
            metadata = str(audit.metadata_json).lower()
            self.assertNotIn("answer", metadata)
            self.assertNotIn("question_text", metadata)
            self.assertNotIn("fingerprint", metadata)

    def test_admin_direct_print_preserves_requested_historical_revision(self):
        ExamGenerationRevision.objects.filter(pk=self.r2.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        r3 = self._make_revision(
            self.parent,
            revision_number=3,
            supersedes=self.r2,
        )
        client = Client()
        client.force_login(self.manager_user)

        response = client.get(self._admin_print_url(revision=self.r2, set_code="A"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revision_number"], 2)
        self.assertContains(response, "Revision R2")
        self.assertNotEqual(response.context["revision_number"], r3.revision_number)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_admin_direct_print_permission_and_direct_deny_fail_closed(self):
        print_user = self.make_user(
            "questionnaire-admin-printer",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.print_generated_exams",
            ),
        )
        client = Client()
        client.force_login(print_user)
        self.assertEqual(client.get(self._admin_print_url()).status_code, 200)
        self.assertEqual(
            client.get(reverse("departmental_exams:questionnaire_print_release")).status_code,
            200,
        )
        UserPermission.objects.create(
            user=print_user,
            permission=Permission.objects.get(
                code="departmental_exams.print_generated_exams"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._admin_print_url()).status_code, 403)
        self.assertEqual(
            client.get(reverse("departmental_exams:questionnaire_print_release")).status_code,
            403,
        )
        unauthorized = Client()
        unauthorized.force_login(self.configurer)
        self.assertEqual(unauthorized.get(self._admin_print_url()).status_code, 403)

    def test_authorized_admin_releases_exact_revision_and_records_safe_audit(self):
        client = Client()
        client.force_login(self.manager_user)
        release_url = reverse("departmental_exams:questionnaire_print_release")
        initial_page = client.get(release_url)
        self.assertEqual(initial_page.status_code, 200)
        self.assertContains(initial_page, "Questionnaire Print Release")
        now = timezone.localtime().replace(second=0, microsecond=0)
        response = client.post(
            release_url,
            {
                "action": "release",
                "cycle_course_id": self.parent.id,
                "generation_revision": self.r2.id,
                "print_from": now.strftime("%Y-%m-%dT%H:%M"),
                "print_until": (now + timezone.timedelta(hours=3)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            },
        )

        self.assertRedirects(
            response,
            release_url,
        )
        release = QuestionnairePrintRelease.objects.get()
        self.assertEqual(release.generation_revision, self.r2)
        self.assertEqual(release.cycle_course, self.parent)
        audit = AuditLog.objects.get(action="DE_QUESTIONNAIRE_PRINT_RELEASED")
        self.assertEqual(audit.metadata_json["revision_id"], self.r2.id)
        self.assertNotIn("question", str(audit.metadata_json).lower())
        self.assertNotIn("choice", str(audit.metadata_json).lower())
        self.assertNotIn("answer", str(audit.metadata_json).lower())

    def test_wrong_tenant_course_revision_and_invalid_window_are_rejected(self):
        other_parent = self.make_course(cycle=self.parent.cycle, department=None, code="PRINT-OTHER")
        other_revision = self._make_revision(
            other_parent,
            revision_number=1,
            with_sets=False,
        )
        now = timezone.now()
        with self.assertRaises(Http404):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=self.r2.id,
                tenant_id=self.other_tenant.id,
                actor=self.manager_user,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaisesRegex(ValidationError, "does not belong"):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=other_revision.id,
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaisesRegex(ValidationError, "later than"):
            self._release(print_from=now, print_until=now)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_regenerated_r3_is_not_substituted_and_explicit_release_replaces_r2(self):
        r2_release = self._release()
        self.r2.status = ExamGenerationRevision.Status.SUPERSEDED
        self.r2.current_marker = None
        self.r2.save(update_fields=["status", "current_marker", "updated_at"])
        r3 = self._make_revision(
            self.parent,
            revision_number=3,
            supersedes=self.r2,
        )

        list_response = self._faculty_client().get(
            reverse("departmental_exams:contribution_list")
        )
        self.assertContains(list_response, "Released R2")
        self.assertNotContains(list_response, "Released R3")
        self.assertEqual(
            QuestionnairePrintRelease.objects.get(status="ACTIVE").generation_revision,
            self.r2,
        )
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        admin_page = admin_client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertContains(admin_page, "A newer generated revision exists.")
        self.assertContains(
            admin_page,
            "It is not printable until it receives its own explicit release.",
        )

        r3_release = self._release(revision=r3)
        r2_release.refresh_from_db()
        self.assertEqual(r2_release.status, QuestionnairePrintRelease.Status.REVOKED)
        self.assertIsNone(r2_release.active_marker)
        self.assertEqual(r3_release.generation_revision, r3)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DE_QUESTIONNAIRE_PRINT_RELEASE_REVOKED",
                entity_id=str(r2_release.id),
            ).exists()
        )
        self.assertContains(
            self._faculty_client().get(reverse("departmental_exams:contribution_list")),
            "Released R3",
        )

    def test_assigned_faculty_sees_both_print_actions_inside_active_window(self):
        release = self._release()
        response = self._faculty_client().get(
            reverse("departmental_exams:contribution_list")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._print_url(release, "A"))
        self.assertContains(response, self._print_url(release, "B"))
        self.assertContains(response, "Print Questionnaire")

    def test_unrelated_faculty_cannot_see_or_access_print_output(self):
        release = self._release()
        unrelated = self.make_user(
            "unrelated-questionnaire-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        client = self._faculty_client(unrelated)
        list_response = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(list_response, "Print Set A", status_code=403)
        self.assertEqual(client.get(self._print_url(release)).status_code, 404)

    def test_before_and_after_window_hide_buttons_and_direct_url_denies(self):
        now = timezone.now()
        scheduled = self._release(
            print_from=now + timezone.timedelta(hours=1),
            print_until=now + timezone.timedelta(hours=2),
        )
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(scheduled)).status_code, 403)

        expired = self._release(
            print_from=now - timezone.timedelta(hours=2),
            print_until=now - timezone.timedelta(hours=1),
        )
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(expired)).status_code, 403)

    def test_set_a_and_b_are_sanitized_no_store_and_audited(self):
        release = self._release()
        client = self._faculty_client()
        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                response = client.get(self._print_url(release, set_code))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertContains(
                    response,
                    (
                        '<span class="running-course-code">'
                        f"{self.parent.course.code}</span>"
                    ),
                    html=True,
                )
                self.assertContains(
                    response,
                    (
                        '<span class="running-course-title">'
                        f"{self.parent.course.title}</span>"
                    ),
                    html=True,
                )
                self.assertContains(response, self.PRINT_SCHOOL_NAME)
                self.assertContains(response, self.PRINT_CAMPUS_LINE)
                self.assertContains(response, self.parent.cycle.term.name)
                self.assertContains(response, self.parent.cycle.academic_year.name)
                self.assertContains(
                    response,
                    self.parent.cycle.get_exam_period_display(),
                )
                self.assertContains(response, "DEPARTMENTAL EXAMINATIONS")
                self.assertContains(response, self.parent.course.title)
                self.assertContains(response, self.parent.course.code)
                self.assertContains(response, f"SET {set_code}")
                self.assertContains(response, "shade the circle on the answer sheet")
                self.assertContains(response, "STRICTLY NO ERASURES ALLOWED")
                self.assertContains(response, "Pencil No. 2")
                self.assertContains(response, f"Released {set_code} question 1")
                self.assertContains(response, f"{set_code} choice A1")
                self.assertContains(
                    response,
                    (
                        '<div class="question-line"><span>1.</span><span>'
                        f"Released {set_code} question 1</span></div>"
                    ),
                    html=True,
                )
                body = response.content.decode()
                for forbidden in (
                    "correct_answer_snapshot",
                    "difficulty_snapshot",
                    "Private provenance campus",
                    "PRIVATE-CAMPUS",
                    "CONFIDENTIAL CONTRIBUTOR",
                    "SECRET-DIGEST",
                    "source_question",
                    "source_contributor",
                    "source_campus",
                    "contribution_id",
                    "campus_quotas_snapshot",
                    "fingerprint",
                    "HMAC",
                    "automatic-print-test-v1",
                    "Correct answer:",
                    "answer key",
                    "revision history",
                ):
                    self.assertNotIn(forbidden, body)
        audits = AuditLog.objects.filter(
            action="DE_QUESTIONNAIRE_PRINT_SET_ACCESSED"
        ).order_by("id")
        self.assertEqual(audits.count(), 2)
        self.assertEqual(
            [audit.metadata_json["set_code"] for audit in audits],
            ["A", "B"],
        )
        for audit in audits:
            metadata = str(audit.metadata_json).lower()
            self.assertNotIn("question", metadata)
            self.assertNotIn("choice", metadata)
            self.assertNotIn("answer", metadata)

    def test_lost_current_assignment_or_direct_deny_fails_closed(self):
        release = self._release()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(release)).status_code, 403)

        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._print_url(release)).status_code, 403)

    def test_summary_displays_actual_persisted_set_and_difficulty_counts(self):
        summary = AutomaticGenerationSummaryService.build(cycle=self.parent.cycle)
        generated = summary["generated"][0]
        self.assertEqual(
            generated["actual_set_counts"],
            (
                {
                    "set_code": "A",
                    "total": 2,
                    "campuses": (
                        {
                            "campus_code": "PRIVATE-CAMPUS",
                            "campus_name": "Private provenance campus",
                            "total": 2,
                            "easy": 1,
                            "moderate": 1,
                            "difficult": 0,
                        },
                    ),
                },
                {
                    "set_code": "B",
                    "total": 2,
                    "campuses": (
                        {
                            "campus_code": "PRIVATE-CAMPUS",
                            "campus_name": "Private provenance campus",
                            "total": 2,
                            "easy": 1,
                            "moderate": 1,
                            "difficult": 0,
                        },
                    ),
                },
            ),
        )
        response = Client()
        response.force_login(self.manager_user)
        page = response.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[self.parent.cycle_id],
            )
        )
        self.assertContains(page, "Set A — 2 actual items")
        self.assertContains(page, "Set B — 2 actual items")
        self.assertContains(
            page,
            "Easy 1 &middot; Moderate 1 &middot; Difficult 0",
            html=False,
        )

    def test_summary_waiting_deadline_and_draft_wording(self):
        waiting_parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-WAIT",
        )
        deadline = timezone.now().astimezone(MANILA).replace(
            hour=9,
            minute=30,
            second=0,
            microsecond=0,
        ) + timezone.timedelta(days=3)
        self.make_configuration(
            waiting_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            deadline=deadline,
        )
        draft_parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-DRAFT",
        )
        self.make_configuration(
            draft_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.DRAFT,
        )
        client = Client()
        client.force_login(self.manager_user)
        response = client.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[self.parent.cycle_id],
            )
        )
        self.assertContains(response, "Contribution deadline has not arrived yet.")
        self.assertContains(
            response,
            f"Deadline:</strong> {deadline.strftime('%b')} {deadline.day}, {deadline.year} 9:30 AM",
            html=False,
        )
        self.assertContains(
            response,
            "Automatic generation will run after the deadline.",
        )
        self.assertContains(response, "Course setup is not yet complete.")
        self.assertContains(
            response,
            "Complete the course configuration and open contributions before automatic generation can proceed.",
        )
