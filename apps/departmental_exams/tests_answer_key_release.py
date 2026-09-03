from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.template import TemplateDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.navigation.models import MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus

from .answer_key_release import (
    ANSWER_KEY_RELEASE_ATTESTATION_VERSION,
    AnswerKeyReleaseService,
    AnswerKeyViewerReportService,
    FacultyAnswerKeyReleaseService,
)
from .exam_units import ExamCourseEquivalencyService
from .models import (
    AnswerKeyRelease,
    CourseExamConfiguration,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
    QuestionnairePrintRelease,
)
from .stage4_test_support import Stage4TestCase


class AnswerKeyReleaseTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.release_manager = self.make_user(
            "answer-key-release-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.release_answer_keys",
            ),
        )
        self.generation_manager = self.make_user(
            "generation-manager-only",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
        )
        self.faculty = self.make_user(
            "answer-key-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent = self.make_course(cycle=cycle, department=None, code="KEY-101")
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
                question_text=f"Confidential key source {position}",
                choice_a="A",
                choice_b="B",
                choice_c="C",
                choice_d="D",
                correct_answer=("A" if position == 1 else "D"),
                difficulty="EASY",
                position=position,
            )
            for position in (1, 2)
        ]
        self.r4 = self._make_revision(4)

    def _make_revision(
        self,
        number,
        *,
        supersedes=None,
        item_count=2,
        cycle_course=None,
    ):
        cycle_course = cycle_course or self.parent
        for position in range(len(self.questions) + 1, item_count + 1):
            self.questions.append(
                Question.objects.create(
                    contribution=self.contribution,
                    question_text=f"Confidential key source {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty="EASY",
                    position=position,
                )
            )
        revision = ExamGenerationRevision.objects.create(
            cycle_course=cycle_course,
            revision_number=number,
            source_input_fingerprint=(str(number) * 64)[:64],
            algorithm_version="answer-key-release-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="r" * 64,
            final_item_count_snapshot=item_count,
            request_token_digest=(str(number + 4) * 64)[:64],
            supersedes=supersedes,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=4,
        )
        answers_by_set = {
            "A": tuple(
                ("A", "D")[position - 1]
                if item_count == 2
                else "ABCD"[(position - 1) % 4]
                for position in range(1, item_count + 1)
            ),
            "B": tuple(
                ("C", "B")[position - 1]
                if item_count == 2
                else "DCBA"[(position - 1) % 4]
                for position in range(1, item_count + 1)
            ),
        }
        for set_code, answers in answers_by_set.items():
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot={"MAIN": 2},
                difficulty_quotas_snapshot={"EASY": 2},
                section_quotas_snapshot={"0": 2},
                item_count=item_count,
            )
            for position, (question, answer) in enumerate(
                zip(self.questions, answers), start=1
            ):
                GeneratedExamItem.objects.create(
                    generated_set=generated_set,
                    position=position,
                    source_question=question,
                    source_question_revision=question.revision,
                    source_question_digest="s" * 64,
                    source_contributor=self.faculty,
                    source_contributor_id_snapshot=self.faculty.id,
                    source_contributor_name_snapshot="Private Contributor",
                    source_campus=self.campus,
                    campus_code_snapshot="MAIN",
                    campus_name_snapshot="Main",
                    difficulty_snapshot="EASY",
                    section_title_snapshot="",
                    question_text_snapshot=f"Private Set {set_code} question {position}",
                    choices_snapshot=["A", "B", "C", "D"],
                    correct_answer_snapshot=answer,
                )
        return revision

    def _release(self, *, revision=None, start=None, end=None, actor=None):
        now = timezone.now()
        return AnswerKeyReleaseService.release(
            cycle_course_id=self.parent.id,
            revision_id=(revision or self.r4).id,
            tenant_id=self.tenant.id,
            actor=actor or self.release_manager,
            available_from=start or now - timezone.timedelta(minutes=5),
            available_until=end or now + timezone.timedelta(hours=2),
            attestation_confirmed=True,
        )

    def _faculty_client(self):
        client = Client()
        client.force_login(self.faculty)
        return client

    def _url(self, release, set_code="A", *, printable=False):
        return reverse(
            (
                "departmental_exams:faculty_answer_key_print"
                if printable
                else "departmental_exams:faculty_answer_key"
            ),
            args=[self.contribution.id, release.id, set_code],
        )

    def _master_url(self, release_id, set_code="A"):
        return reverse(
            "departmental_exams:faculty_checking_master_print",
            args=[self.contribution.id, release_id, set_code],
        )

    def _replace_current_revision(self, *, item_count):
        current = ExamGenerationRevision.objects.get(
            cycle_course=self.parent,
            current_marker=1,
        )
        current.status = ExamGenerationRevision.Status.SUPERSEDED
        current.current_marker = None
        current.save(update_fields=["status", "current_marker", "updated_at"])
        return self._make_revision(
            current.revision_number + 1,
            supersedes=current,
            item_count=item_count,
        )

    @staticmethod
    def _master_rows(response):
        return [row for column in response.context["answer_columns"] for row in column]

    def test_model_lifecycle_constraints_immutability_and_delete_prohibition(self):
        release = self._release()
        self.assertEqual(release.attestation_version, ANSWER_KEY_RELEASE_ATTESTATION_VERSION)
        release.available_until += timezone.timedelta(hours=1)
        with self.assertRaisesRegex(ValidationError, "immutable"):
            release.save()
        release.refresh_from_db()
        with self.assertRaisesRegex(ValidationError, "historical"):
            release.delete()
        invalid_window = timezone.now()
        with self.assertRaises(ValidationError):
            AnswerKeyReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=self.r4.id,
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=invalid_window,
                available_until=invalid_window,
                attestation_confirmed=True,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnswerKeyRelease.objects.create(
                cycle_course=self.parent,
                generation_revision=self.r4,
                available_from=timezone.now(),
                available_until=timezone.now() + timezone.timedelta(hours=1),
                released_by=self.release_manager,
                attestation_version=ANSWER_KEY_RELEASE_ATTESTATION_VERSION,
            )

    def test_admin_confirmation_is_required_and_exact_release_is_recorded(self):
        client = Client()
        client.force_login(self.release_manager)
        now = timezone.localtime().replace(second=0, microsecond=0)
        payload = {
            "action": "answer_key_release",
            "cycle_course_id": self.parent.id,
            "generation_revision": self.r4.id,
            "available_from": now.strftime("%Y-%m-%dT%H:%M"),
            "available_until": (now + timezone.timedelta(hours=2)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
        }
        denied = client.post(
            reverse("departmental_exams:questionnaire_print_release"),
            payload,
        )
        self.assertEqual(denied.status_code, 400)
        self.assertContains(
            denied,
            "Confirm that all examination sessions have concluded.",
            status_code=400,
        )
        self.assertFalse(AnswerKeyRelease.objects.exists())

        payload["sessions_concluded"] = "on"
        allowed = client.post(
            reverse("departmental_exams:questionnaire_print_release"),
            payload,
        )
        self.assertEqual(allowed.status_code, 302)
        release = AnswerKeyRelease.objects.get()
        self.assertEqual(release.generation_revision_id, self.r4.id)
        self.assertEqual(release.cycle_course_id, self.parent.id)

    def test_dedicated_permission_is_required_and_direct_deny_wins(self):
        with self.assertRaises(PermissionDenied):
            self._release(actor=self.generation_manager)
        UserPermission.objects.create(
            user=self.release_manager,
            permission=Permission.objects.get(
                code="departmental_exams.release_answer_keys"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._release()

    def test_cross_tenant_course_and_revision_are_rejected(self):
        now = timezone.now()
        with self.assertRaises(Http404):
            AnswerKeyReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=self.r4.id,
                tenant_id=self.other_tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        other_parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-OTHER",
        )
        with self.assertRaisesRegex(ValidationError, "does not belong"):
            AnswerKeyReleaseService.release(
                cycle_course_id=other_parent.id,
                revision_id=self.r4.id,
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )

    def test_before_inside_after_and_revoked_release_enforce_ui_and_direct_urls(self):
        now = timezone.now()
        scheduled = self._release(
            start=now + timezone.timedelta(hours=1),
            end=now + timezone.timedelta(hours=2),
        )
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "View Set A Answer Key",
        )
        self.assertEqual(client.get(self._url(scheduled)).status_code, 403)

        active = self._release()
        self.assertContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "View Set A Answer Key",
        )
        self.assertEqual(client.get(self._url(active)).status_code, 200)

        expired = self._release(
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
        )
        self.assertEqual(client.get(self._url(expired)).status_code, 403)
        AnswerKeyReleaseService.revoke(
            release_id=expired.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        self.assertEqual(client.get(self._url(expired)).status_code, 403)

    def test_replace_and_revoke_preserve_history_and_safe_admin_audits(self):
        first = self._release()
        second = self._release()
        first.refresh_from_db()
        self.assertEqual(first.status, AnswerKeyRelease.Status.REVOKED)
        self.assertEqual(second.status, AnswerKeyRelease.Status.ACTIVE)
        self.assertEqual(AnswerKeyRelease.objects.count(), 2)
        AnswerKeyReleaseService.revoke(
            release_id=second.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        self.assertFalse(
            AnswerKeyRelease.objects.filter(
                status=AnswerKeyRelease.Status.ACTIVE,
                active_marker=1,
            ).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(action="DE_ANSWER_KEY_RELEASED").count(), 2
        )
        self.assertEqual(
            AuditLog.objects.filter(action="DE_ANSWER_KEY_RELEASE_REVOKED").count(),
            2,
        )
        for audit in AuditLog.objects.filter(entity_type="AnswerKeyRelease"):
            metadata = str(audit.metadata_json)
            self.assertNotIn("Private Set", metadata)
            self.assertNotIn("correct_answer", metadata)

    def test_set_a_and_b_exact_answers_no_store_scalar_context_and_safe_audits(self):
        release = self._release()
        client = self._faculty_client()
        expected = {"A": [(1, "A"), (2, "D")], "B": [(1, "C"), (2, "B")]}
        for set_code in ("A", "B"):
            for printable in (False, True):
                with self.subTest(set_code=set_code, printable=printable):
                    response = client.get(
                        self._url(release, set_code, printable=printable)
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertIn("private", response["Cache-Control"])
                    self.assertEqual(
                        [
                            (row["position"], row["correct_answer"])
                            for row in response.context["items"]
                        ],
                        expected[set_code],
                    )
                    for forbidden_key in (
                        "revision",
                        "cycle_course",
                        "generated_set",
                    ):
                        self.assertNotIn(forbidden_key, response.context)
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).count(),
            2,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_PRINTED"
            ).count(),
            2,
        )
        for audit in AuditLog.objects.filter(portal="FACULTY"):
            metadata = str(audit.metadata_json)
            self.assertNotIn("Private Set", metadata)
            self.assertNotIn("correct_answer", metadata)

    def test_current_assignment_is_required(self):
        release = self._release()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "View Set A Answer Key",
        )
        self.assertEqual(client.get(self._url(release)).status_code, 403)

    def test_r4_to_r5_blocks_r4_and_does_not_release_r5(self):
        release = self._release()
        self.r4.status = ExamGenerationRevision.Status.SUPERSEDED
        self.r4.current_marker = None
        self.r4.save(update_fields=["status", "current_marker", "updated_at"])
        r5 = self._make_revision(5, supersedes=self.r4)
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "View Set A Answer Key",
        )
        self.assertEqual(client.get(self._url(release)).status_code, 403)
        self.assertEqual(
            AnswerKeyRelease.objects.get(pk=release.id).generation_revision_id,
            self.r4.id,
        )
        self.assertFalse(
            AnswerKeyRelease.objects.filter(generation_revision=r5).exists()
        )
        admin = Client()
        admin.force_login(self.release_manager)
        page = admin.get(reverse("departmental_exams:questionnaire_print_release"))
        self.assertContains(page, "The released revision was superseded.")

    def test_resources_never_exposes_answer_key(self):
        self._release()
        response = self._faculty_client().get(reverse("departmental_exams:resources"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Answer Key")

    def test_checking_master_active_set_a_and_b_use_exact_persisted_answers(self):
        release = self._release()
        client = self._faculty_client()
        expected = {"A": {1: "A", 2: "D"}, "B": {1: "C", 2: "B"}}

        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                response = client.get(self._master_url(release.id, set_code))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "FOR FACULTY CHECKING ONLY")
                self.assertContains(response, f"SET {set_code}")
                self.assertNotContains(response, "Private Set")
                self.assertNotContains(response, "Confidential key source")
                self.assertNotContains(response, "Pair Code")
                self.assertEqual(
                    response.content.decode().count("data-master-item"),
                    75,
                )
                rows = self._master_rows(response)
                self.assertEqual(len(rows), 75)
                shaded = {
                    row["position"]: [
                        bubble["code"]
                        for bubble in row["bubbles"]
                        if bubble["is_shaded"]
                    ]
                    for row in rows
                }
                self.assertEqual(
                    {position: values[0] for position, values in shaded.items() if values},
                    expected[set_code],
                )
                for row in rows[:2]:
                    self.assertEqual(
                        sum(bubble["is_shaded"] for bubble in row["bubbles"]),
                        1,
                    )
                for row in rows[2:]:
                    self.assertTrue(row["is_unused"])
                    self.assertFalse(any(bubble["is_shaded"] for bubble in row["bubbles"]))

    def test_checking_master_no_release_scheduled_expired_and_revoked_deny(self):
        client = self._faculty_client()
        self.assertEqual(client.get(self._master_url(999999)).status_code, 403)
        now = timezone.now()

        scheduled = self._release(
            start=now + timezone.timedelta(hours=1),
            end=now + timezone.timedelta(hours=2),
        )
        self.assertEqual(client.get(self._master_url(scheduled.id)).status_code, 403)
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Pre-Shaded Master",
        )

        expired = self._release(
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
        )
        self.assertEqual(client.get(self._master_url(expired.id)).status_code, 403)
        AnswerKeyReleaseService.revoke(
            release_id=expired.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        self.assertEqual(client.get(self._master_url(expired.id)).status_code, 403)

    def test_checking_master_stale_revision_denies_without_releasing_new_revision(self):
        release = self._release()
        self.r4.status = ExamGenerationRevision.Status.SUPERSEDED
        self.r4.current_marker = None
        self.r4.save(update_fields=["status", "current_marker", "updated_at"])
        r5 = self._make_revision(5, supersedes=self.r4)

        client = self._faculty_client()
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Pre-Shaded Master",
        )
        self.assertFalse(AnswerKeyRelease.objects.filter(generation_revision=r5).exists())

    def test_checking_master_current_assignment_and_direct_allow_are_required(self):
        release = self._release()
        client = self._faculty_client()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)

        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)

    def test_checking_master_cross_tenant_course_and_nonparticipating_campus_deny(self):
        release = self._release()
        client = self._faculty_client()

        foreign_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-CROSS",
        )
        foreign_revision = self._make_revision(1, cycle_course=foreign_course)
        foreign_release = AnswerKeyRelease.objects.create(
            cycle_course=foreign_course,
            generation_revision=foreign_revision,
            available_from=timezone.now() - timezone.timedelta(minutes=5),
            available_until=timezone.now() + timezone.timedelta(hours=1),
            released_by=self.release_manager,
            attestation_version=ANSWER_KEY_RELEASE_ATTESTATION_VERSION,
        )
        self.assertEqual(client.get(self._master_url(foreign_release.id)).status_code, 403)

        self.parent.offering_snapshots.update(campus=self.other_campus)
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)

        other_tenant_campus = Campus.objects.create(
            tenant=self.other_tenant,
            code="OTHER",
            name="Other Tenant Campus",
        )
        other_tenant_role = Role.objects.create(
            code="S4_CROSS_TENANT_FACULTY",
            name="Cross Tenant Faculty",
        )
        RolePermission.objects.create(
            role=other_tenant_role,
            permission=Permission.objects.get(code="faculty_portal.access"),
        )
        UserRole.objects.create(
            user=self.faculty,
            role=other_tenant_role,
            tenant=self.other_tenant,
            campus=other_tenant_campus,
            is_active=True,
        )
        self.faculty.default_tenant = self.other_tenant
        self.faculty.default_campus = other_tenant_campus
        self.faculty.save(
            update_fields=["default_tenant", "default_campus", "updated_at"]
        )
        client = Client()
        client.force_login(self.faculty)
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 404)

    def test_checking_master_invalid_answer_snapshot_fails_closed(self):
        release = self._release()
        with patch.object(
            FacultyAnswerKeyReleaseService,
            "_authorized_release",
            return_value=(
                release,
                "A",
                [
                    {"position": 1, "correct_answer_snapshot": None},
                    {"position": 2, "correct_answer_snapshot": "D"},
                ],
            ),
        ):
            with self.assertRaises(PermissionDenied):
                FacultyAnswerKeyReleaseService.build_checking_master_context(
                    contribution=self.contribution,
                    release_id=release.id,
                    set_code="A",
                    actor=self.faculty,
                )

    def test_checking_master_revision_count_mismatch_and_noncontiguous_items_deny(self):
        release = self._release()
        client = self._faculty_client()
        ExamGenerationRevision.objects.filter(pk=self.r4.pk).update(
            final_item_count_snapshot=3
        )
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)

        ExamGenerationRevision.objects.filter(pk=self.r4.pk).update(
            final_item_count_snapshot=2
        )
        set_a = GeneratedExamSet.objects.get(
            generation_revision=self.r4,
            set_code="A",
        )
        GeneratedExamItem.objects.filter(generated_set=set_a, position=2).update(
            position=3
        )
        self.assertEqual(client.get(self._master_url(release.id)).status_code, 403)

    def test_checking_master_count_over_75_denies(self):
        release = self._release()
        ExamGenerationRevision.objects.filter(pk=self.r4.pk).update(
            final_item_count_snapshot=76
        )
        self.assertEqual(
            self._faculty_client().get(self._master_url(release.id)).status_code,
            403,
        )

    def test_checking_master_50_60_and_75_item_layouts(self):
        client = self._faculty_client()
        for item_count in (50, 60, 75):
            with self.subTest(item_count=item_count):
                revision = self._replace_current_revision(item_count=item_count)
                release = self._release(revision=revision)
                response = client.get(self._master_url(release.id, "A"))
                self.assertEqual(response.status_code, 200)
                rows = self._master_rows(response)
                active_rows = rows[:item_count]
                unused_rows = rows[item_count:]
                self.assertEqual(response.context["final_item_count"], item_count)
                self.assertTrue(all(not row["is_unused"] for row in active_rows))
                self.assertTrue(all(row["is_unused"] for row in unused_rows))
                self.assertTrue(
                    all(
                        sum(bubble["is_shaded"] for bubble in row["bubbles"]) == 1
                        for row in active_rows
                    )
                )
                self.assertFalse(
                    any(
                        bubble["is_shaded"]
                        for row in unused_rows
                        for bubble in row["bubbles"]
                    )
                )
                self.assertContains(response, "UNUSED", count=75 - item_count)

    def test_checking_master_paper_allowlist_no_store_and_get_only(self):
        release = self._release()
        client = self._faculty_client()
        expected = (
            ({}, "letter", "Letter", "8.5in", "11in"),
            ({"paper": "a4"}, "a4", "A4", "210mm", "297mm"),
            ({"paper": "legal"}, "legal", "Legal", "8.5in", "14in"),
            ({"paper": "tabloid};body{display:none"}, "letter", "Letter", "8.5in", "11in"),
        )
        for query, value, css_size, width, height in expected:
            with self.subTest(query=query):
                response = client.get(self._master_url(release.id), query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paper_size"], value)
                self.assertEqual(response.context["paper_css_size"], css_size)
                self.assertEqual(response.context["paper_sheet_width"], width)
                self.assertEqual(response.context["paper_sheet_height"], height)
                self.assertIn(f"size: {css_size} portrait", response.content.decode())
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])
        self.assertEqual(client.post(self._master_url(release.id)).status_code, 405)

    def test_checking_master_course_card_and_answer_key_print_workflow(self):
        client = self._faculty_client()
        unavailable = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(unavailable, "Print Set A Master")
        release = self._release()

        course_card = client.get(reverse("departmental_exams:contribution_list"))
        self.assertContains(course_card, "View Set A Answer Key")
        self.assertContains(course_card, "View Set B Answer Key")
        self.assertContains(course_card, "CHECKING MASTER")
        self.assertContains(course_card, "Print Set A Master")
        self.assertContains(course_card, "Print Set B Master")
        self.assertNotContains(course_card, "Print Set A Answer Key")
        self.assertNotContains(course_card, "Print Set B Answer Key")

        answer_key = client.get(self._url(release, "A"))
        self.assertContains(answer_key, "Print Set A Answer Key")
        self.assertContains(
            answer_key,
            f'<li class="breadcrumb-item"><a href="{reverse("departmental_exams:contribution_list")}">Question Bank</a></li>',
            html=True,
        )

        template_source = (
            Path(__file__).resolve().parents[2]
            / "templates"
            / "departmental_exams"
            / "faculty"
            / "contribution_list.html"
        ).read_text(encoding="utf-8")
        self.assertIn(">Print Set A</a>", template_source)
        self.assertIn(">Print Set B</a>", template_source)

    def test_checking_master_uses_distinct_content_safe_audit(self):
        release = self._release()
        response = self._faculty_client().get(self._master_url(release.id, "B"))
        self.assertEqual(response.status_code, 200)
        audit = AuditLog.objects.get(action="DE_FACULTY_CHECKING_MASTER_PRINTED")
        self.assertEqual(audit.entity_type, "AnswerKeyRelease")
        self.assertEqual(str(audit.entity_id), str(release.id))
        metadata = str(audit.metadata_json)
        for forbidden in (
            "correct_answer",
            "shaded",
            "Private Set",
            "Confidential key source",
        ):
            self.assertNotIn(forbidden, metadata)

    def test_checking_master_creates_no_media_artifact_or_dependency(self):
        release = self._release()
        release_count = AnswerKeyRelease.objects.count()
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self._faculty_client().get(self._master_url(release.id))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(Path(media_root).iterdir()), [])
        self.assertEqual(AnswerKeyRelease.objects.count(), release_count)
        self.assertFalse(Permission.objects.filter(code__icontains="checking_master").exists())

    def test_bulk_list_uses_current_marker_not_highest_revision_and_ui_contract(self):
        r5 = self._replace_current_revision(item_count=2)
        ExamGenerationRevision.objects.filter(pk=r5.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        ExamGenerationRevision.objects.filter(pk=self.r4.pk).update(
            status=ExamGenerationRevision.Status.GENERATED,
            current_marker=1,
        )
        self.r4.refresh_from_db()
        release = self._release(revision=self.r4)

        client = Client()
        client.force_login(self.release_manager)
        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertEqual(response.status_code, 200)
        expected_value = f"{self.parent.id}:{self.r4.id}"
        stale_value = f"{self.parent.id}:{r5.id}"
        self.assertContains(response, 'id="bulk-answer-key-release-form"', html=False)
        self.assertContains(response, 'id="bulk-answer-key-select-all"', html=False)
        self.assertContains(response, f'value="{expected_value}"', html=False)
        self.assertNotContains(response, f'value="{stale_value}"', html=False)
        expected_tag = next(
            tag
            for tag in response.content.decode().split(">")
            if f'value="{expected_value}"' in tag
        )
        self.assertNotIn("checked", expected_tag)
        self.assertContains(response, "Currently Released / Available")
        self.assertContains(
            response,
            reverse("departmental_exams:answer_key_viewers", args=[release.id]),
        )
        self.assertContains(
            response,
            "js/departmental_exam_release_center.js",
            html=False,
        )

    def test_bulk_list_supports_manual_locked_and_lists_equivalency_primary_once(self):
        manual_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            scope_suffix="manual-bulk-key",
        )
        manual_cycle.processing_mode = ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        manual_cycle.save(update_fields=["processing_mode", "updated_at"])
        manual_course = self.make_course(
            cycle=manual_cycle,
            department=self.department,
            code="KEY-MANUAL",
        )
        RolePermission.objects.create(
            role=self.release_manager.user_roles.get().role,
            permission=Permission.objects.get(
                code="departmental_exams.review_generate"
            ),
        )
        manual_course.reviewer = self.release_manager
        manual_course.save(update_fields=["reviewer", "updated_at"])
        self.make_configuration(manual_course)
        manual_revision = self._make_revision(1, cycle_course=manual_course)
        self.assertFalse(
            AnswerKeyReleaseService.revision_is_eligible(manual_revision)
        )
        ExamGenerationRevision.objects.filter(pk=manual_revision.pk).update(
            status=ExamGenerationRevision.Status.LOCKED,
            locked_at=timezone.now(),
            locked_by=self.release_manager,
            approval_attestation_version="manual-lock-v1",
        )
        manual_revision.refresh_from_db()
        self.assertTrue(
            AnswerKeyReleaseService.revision_is_eligible(manual_revision)
        )

        group_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            scope_suffix="bulk-key-unit",
        )
        group_cycle.processing_mode = (
            ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        group_cycle.save(update_fields=["processing_mode", "updated_at"])
        primary = self.make_course(cycle=group_cycle, code="KEY-UNIT-P")
        secondary = self.make_course(cycle=group_cycle, code="KEY-UNIT-S")
        shared_deadline = self.future_deadline()
        self.make_configuration(primary, deadline=shared_deadline)
        self.make_configuration(secondary, deadline=shared_deadline)
        ExamCourseEquivalencyService.create_group(
            cycle_id=group_cycle.id,
            name="Bulk Answer Key Unit",
            primary_cycle_course_id=primary.id,
            member_ids=(primary.id, secondary.id),
            actor=self.admin,
        )
        primary_revision = self._make_revision(1, cycle_course=primary)
        secondary_revision = self._make_revision(1, cycle_course=secondary)

        client = Client()
        client.force_login(self.release_manager)
        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertContains(
            response,
            f'value="{manual_course.id}:{manual_revision.id}"',
            html=False,
        )
        self.assertContains(
            response,
            f'value="{primary.id}:{primary_revision.id}"',
            html=False,
        )
        self.assertNotContains(
            response,
            f'value="{secondary.id}:{secondary_revision.id}"',
            html=False,
        )

    def test_bulk_exact_pair_attestation_duplicate_and_all_or_nothing_validation(self):
        now = timezone.now()
        with self.assertRaisesRegex(ValidationError, "all selected courses"):
            AnswerKeyReleaseService.bulk_release(
                selections=((self.parent.id, self.r4.id),),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=False,
            )
        with self.assertRaisesRegex(ValidationError, "only one revision"):
            AnswerKeyReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r4.id),
                    (self.parent.id, self.r4.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )

        other_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-BULK-ROLLBACK",
        )
        other_revision = self._make_revision(1, cycle_course=other_course)
        other_set = GeneratedExamSet.objects.get(
            generation_revision=other_revision,
            set_code=GeneratedExamSet.SetCode.B,
        )
        GeneratedExamItem.objects.filter(
            generated_set=other_set,
            position=2,
        ).update(position=3)
        with self.assertRaisesRegex(ValidationError, "complete revision"):
            AnswerKeyReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r4.id),
                    (other_course.id, other_revision.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())
        with self.assertRaisesRegex(ValidationError, "does not belong"):
            AnswerKeyReleaseService.bulk_release(
                selections=((self.parent.id, other_revision.id),),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())

    def test_bulk_post_requires_attestation_and_applies_one_common_window(self):
        other_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-BULK-POST",
        )
        other_revision = self._make_revision(1, cycle_course=other_course)
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        local_end = local_start + timezone.timedelta(hours=2)
        payload = {
            "action": "bulk_answer_key_release",
            "selections": (
                f"{self.parent.id}:{self.r4.id}",
                f"{other_course.id}:{other_revision.id}",
            ),
            "available_from": local_start.strftime("%Y-%m-%dT%H:%M"),
            "available_until": local_end.strftime("%Y-%m-%dT%H:%M"),
        }
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        denied = client.post(url, payload)
        self.assertEqual(denied.status_code, 400)
        self.assertContains(
            denied,
            "Confirm that all examination sessions for all selected courses have concluded.",
            status_code=400,
        )
        self.assertFalse(AnswerKeyRelease.objects.exists())

        payload["sessions_concluded"] = "on"
        allowed = client.post(url, payload)
        self.assertEqual(allowed.status_code, 302)
        releases = list(AnswerKeyRelease.objects.order_by("cycle_course_id"))
        self.assertEqual(len(releases), 2)
        self.assertEqual(
            {release.generation_revision_id for release in releases},
            {self.r4.id, other_revision.id},
        )
        self.assertEqual({release.available_from for release in releases}, {local_start})
        self.assertEqual({release.available_until for release in releases}, {local_end})

    def test_bulk_cross_tenant_and_direct_deny_fail_closed_without_writes(self):
        now = timezone.now()
        with self.assertRaises(Http404):
            AnswerKeyReleaseService.bulk_release(
                selections=((self.parent.id, self.r4.id),),
                tenant_id=self.other_tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        UserPermission.objects.create(
            user=self.release_manager,
            permission=Permission.objects.get(
                code="departmental_exams.release_answer_keys"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.bulk_release(
                selections=((self.parent.id, self.r4.id),),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())

    def test_one_unauthorized_row_rolls_back_an_earlier_authorized_release(self):
        unauthorized_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-BULK-UNAUTHORIZED",
        )
        unauthorized_course.offering_snapshots.update(campus=self.other_campus)
        unauthorized_revision = self._make_revision(
            1,
            cycle_course=unauthorized_course,
        )
        now = timezone.now()
        with self.assertRaises(PermissionDenied):
            AnswerKeyReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r4.id),
                    (unauthorized_course.id, unauthorized_revision.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())
        self.assertFalse(
            AuditLog.objects.filter(action="DE_ANSWER_KEY_RELEASED").exists()
        )

    def test_stale_expected_revision_rejects_the_complete_batch(self):
        other_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="KEY-BULK-STALE",
        )
        stale_revision = self._make_revision(1, cycle_course=other_course)
        ExamGenerationRevision.objects.filter(pk=stale_revision.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        current_revision = self._make_revision(2, cycle_course=other_course)
        now = timezone.now()
        with self.assertRaisesRegex(ValidationError, "current final revision"):
            AnswerKeyReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r4.id),
                    (other_course.id, stale_revision.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.release_manager,
                available_from=now,
                available_until=now + timezone.timedelta(hours=1),
                attestation_confirmed=True,
            )
        self.assertFalse(AnswerKeyRelease.objects.exists())
        self.assertFalse(
            AnswerKeyRelease.objects.filter(
                generation_revision=current_revision
            ).exists()
        )

    def test_identical_retry_is_noop_and_changed_window_replaces_history(self):
        start = timezone.now()
        end = start + timezone.timedelta(hours=2)
        first = AnswerKeyReleaseService.bulk_release(
            selections=((self.parent.id, self.r4.id),),
            tenant_id=self.tenant.id,
            actor=self.release_manager,
            available_from=start,
            available_until=end,
            attestation_confirmed=True,
        )[0]
        retried = AnswerKeyReleaseService.bulk_release(
            selections=((self.parent.id, self.r4.id),),
            tenant_id=self.tenant.id,
            actor=self.release_manager,
            available_from=start,
            available_until=end,
            attestation_confirmed=True,
        )[0]
        self.assertEqual(retried.id, first.id)
        self.assertEqual(AnswerKeyRelease.objects.count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_ANSWER_KEY_RELEASED").count(),
            1,
        )
        changed = AnswerKeyReleaseService.bulk_release(
            selections=((self.parent.id, self.r4.id),),
            tenant_id=self.tenant.id,
            actor=self.release_manager,
            available_from=start,
            available_until=end + timezone.timedelta(hours=1),
            attestation_confirmed=True,
        )[0]
        first.refresh_from_db()
        self.assertNotEqual(changed.id, first.id)
        self.assertEqual(first.status, AnswerKeyRelease.Status.REVOKED)
        self.assertEqual(AnswerKeyRelease.objects.count(), 2)

    def test_operational_statuses_cover_window_revocation_and_supersession(self):
        now = timezone.now()
        scheduled = self._release(
            start=now + timezone.timedelta(hours=1),
            end=now + timezone.timedelta(hours=2),
        )
        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=scheduled,
                expected_revision=self.r4,
                now=now,
            ),
            "Scheduled",
        )
        available = self._release(
            start=now - timezone.timedelta(minutes=1),
            end=now + timezone.timedelta(hours=1),
        )
        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=available,
                expected_revision=self.r4,
                now=now,
            ),
            "Currently Released / Available",
        )
        ended = self._release(
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
        )
        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=ended,
                expected_revision=self.r4,
                now=now,
            ),
            "Ended",
        )
        AnswerKeyReleaseService.revoke(
            release_id=ended.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        ended.refresh_from_db()
        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=ended,
                expected_revision=self.r4,
                now=now,
            ),
            "Revoked",
        )
        stale_release = self._release()
        current = self._replace_current_revision(item_count=2)
        stale_release.refresh_from_db()
        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=stale_release,
                expected_revision=current,
                now=now,
            ),
            "Superseded / No Longer Faculty Accessible",
        )

    def test_current_row_ignores_revoked_release_from_superseded_revision(self):
        r4_release = self._release()
        AnswerKeyReleaseService.revoke(
            release_id=r4_release.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        r5 = self._replace_current_revision(item_count=2)

        client = Client()
        client.force_login(self.release_manager)
        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertEqual(response.status_code, 200)
        current_row = next(
            row
            for row in response.context["bulk_answer_key_rows"]
            if row["revision"].id == r5.id
        )
        self.assertEqual(current_row["release_status"], "Not released")
        self.assertIsNone(current_row["active_release"])
        self.assertIsNone(current_row["displayed_release"])

        r4_release.refresh_from_db()
        self.assertEqual(r4_release.status, AnswerKeyRelease.Status.REVOKED)
        self.assertEqual(r4_release.generation_revision_id, self.r4.id)
        self.assertContains(
            response,
            reverse("departmental_exams:answer_key_viewers", args=[r4_release.id]),
            count=1,
        )

    def test_current_row_uses_release_for_exact_current_revision(self):
        r4_release = self._release()
        AnswerKeyReleaseService.revoke(
            release_id=r4_release.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        r5 = self._replace_current_revision(item_count=2)
        r5_release = self._release(revision=r5)

        client = Client()
        client.force_login(self.release_manager)
        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertEqual(response.status_code, 200)
        current_row = next(
            row
            for row in response.context["bulk_answer_key_rows"]
            if row["revision"].id == r5.id
        )
        self.assertEqual(
            current_row["release_status"],
            "Currently Released / Available",
        )
        self.assertEqual(current_row["active_release"].id, r5_release.id)
        self.assertEqual(current_row["displayed_release"].id, r5_release.id)
        self.assertContains(
            response,
            reverse("departmental_exams:answer_key_viewers", args=[r5_release.id]),
        )

    def test_superseded_historical_release_status_precedes_revocation_state(self):
        r4_release = self._release()
        AnswerKeyReleaseService.revoke(
            release_id=r4_release.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        r5 = self._replace_current_revision(item_count=2)
        r4_release.refresh_from_db()

        self.assertEqual(
            AnswerKeyReleaseService.operational_status(
                release=r4_release,
                expected_revision=r5,
            ),
            "Superseded / No Longer Faculty Accessible",
        )

    def test_viewed_audit_requires_successful_actual_page_render(self):
        client = self._faculty_client()
        client.get(reverse("departmental_exams:contribution_list"))
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )
        now = timezone.now()
        unavailable = self._release(
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
        )
        self.assertEqual(client.get(self._url(unavailable)).status_code, 403)
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )
        AnswerKeyReleaseService.revoke(
            release_id=unavailable.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        self.assertEqual(client.get(self._url(unavailable)).status_code, 403)
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )
        release = self._release()
        with patch(
            "apps.departmental_exams.faculty_views.render",
            side_effect=TemplateDoesNotExist("forced Answer Key render failure"),
        ):
            with self.assertRaises(TemplateDoesNotExist):
                client.get(self._url(release, "A"))
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )
        self.assertEqual(client.get(self._url(release, "A")).status_code, 200)
        self.assertEqual(client.get(self._url(release, "B")).status_code, 200)
        audits = AuditLog.objects.filter(
            action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
        ).order_by("id")
        self.assertEqual(audits.count(), 2)
        self.assertEqual(
            {audit.metadata_json["set_code"] for audit in audits},
            {"A", "B"},
        )
        for audit in audits:
            self.assertEqual(str(audit.entity_id), str(release.id))
            self.assertEqual(audit.metadata_json["revision_id"], self.r4.id)
            self.assertEqual(audit.metadata_json["cycle_course_id"], self.parent.id)
            for forbidden in (
                "correct_answer",
                "question",
                "choices",
                "fingerprint",
            ):
                self.assertNotIn(forbidden, str(audit.metadata_json).lower())

    def test_who_viewed_aggregates_exact_release_and_is_confidential(self):
        release = self._release()
        faculty_client = self._faculty_client()
        faculty_client.get(self._url(release, "A"))
        faculty_client.get(self._url(release, "A"))
        faculty_client.get(self._url(release, "B"))

        persisted_release, rows = AnswerKeyViewerReportService.report(
            release_id=release.id,
            tenant_id=self.tenant.id,
            actor=self.release_manager,
        )
        self.assertEqual(persisted_release.id, release.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["faculty"].id, self.faculty.id)
        self.assertEqual(rows[0]["sets_viewed"], ("A", "B"))
        self.assertLessEqual(rows[0]["first_viewed"], rows[0]["last_viewed"])

        admin_client = Client()
        admin_client.force_login(self.release_manager)
        report = admin_client.get(
            reverse("departmental_exams:answer_key_viewers", args=[release.id])
        )
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, self.faculty.username)
        self.assertContains(report, "Set A")
        self.assertContains(report, "Set B")
        self.assertIn("no-store", report["Cache-Control"])
        self.assertIn("private", report["Cache-Control"])

        denied_client = Client()
        denied_client.force_login(self.generation_manager)
        self.assertEqual(
            denied_client.get(
                reverse(
                    "departmental_exams:answer_key_viewers",
                    args=[release.id],
                )
            ).status_code,
            403,
        )
        with self.assertRaises(Http404):
            AnswerKeyViewerReportService.report(
                release_id=release.id,
                tenant_id=self.other_tenant.id,
                actor=self.release_manager,
            )

    def test_release_center_filter_selection_and_ajax_source_contract(self):
        self.parent.course.exam_department = self.department
        self.parent.course.save(update_fields=["exam_department", "updated_at"])
        client = Client()
        client.force_login(self.release_manager)
        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="questionnaire-releases-tab"', html=False)
        self.assertContains(response, 'id="answer-key-releases-tab"', html=False)
        self.assertContains(response, 'id="answer-key-course-search"', html=False)
        self.assertContains(response, 'id="answer-key-department-filter"', html=False)
        self.assertContains(response, 'id="answer-key-campus-filter"', html=False)
        self.assertContains(response, 'id="answer-key-status-filter"', html=False)
        self.assertContains(
            response,
            f'data-course-search="{self.parent.course.code} {self.parent.course.title}"',
            html=False,
        )
        self.assertContains(
            response,
            f'data-department-ids="{self.department.id} "',
            html=False,
        )
        self.assertContains(
            response,
            f'data-campus-ids="{self.campus.id} "',
            html=False,
        )
        self.assertContains(response, 'data-release-status="Not released"', html=False)
        self.assertContains(response, 'data-eligible="true"', html=False)
        self.assertContains(response, "Select All Visible")
        self.assertContains(response, 'name="csrfmiddlewaretoken"', html=False)
        self.assertContains(
            response,
            "js/departmental_exam_release_center.js",
            html=False,
        )

        script = (
            Path(__file__).resolve().parents[2]
            / "static"
            / "js"
            / "departmental_exam_release_center.js"
        ).read_text(encoding="utf-8")
        for contract in (
            '!row.hidden && row.dataset.eligible === "true"',
            "selection && !selection.disabled",
            "const deselectUnavailableRows = function ()",
            '(row.hidden || row.dataset.eligible !== "true" || selection.disabled)',
            "selection.checked = false",
            "visibleEligibleRows().forEach",
            "selectedCount.textContent = visibleSelected",
            "selectAll.indeterminate =",
            "row.hidden = !(",
            "toLocaleLowerCase()",
            "matchesDepartment",
            "matchesCampus",
            'row.dataset.releaseStatus === statusValue',
            "[search, department, campus, status].forEach",
            'control === search ? "input" : "change"',
            'form.addEventListener("submit", function ()',
            "input.checked = !input.disabled && selected.has(input.value)",
            '"X-Requested-With": "XMLHttpRequest"',
            "body: new FormData(form)",
            "currentCourse.replaceWith(refreshedCourse)",
            "HTMLFormElement.prototype.submit.call(form)",
            "window.scrollTo(0, scrollPosition)",
        ):
            self.assertIn(contract, script)
        self.assertGreaterEqual(script.count("deselectUnavailableRows();"), 2)
        self.assertLess(
            script.index("row.hidden = !("),
            script.index("deselectUnavailableRows();"),
        )
        self.assertLess(
            script.index('form.addEventListener("submit", function ()'),
            script.index("body: new FormData(form)"),
        )

    def test_ajax_answer_key_release_is_safe_and_non_ajax_fallback_still_redirects(self):
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        payload = {
            "action": "answer_key_release",
            "cycle_course_id": self.parent.id,
            "generation_revision": self.r4.id,
            "available_from": local_start.strftime("%Y-%m-%dT%H:%M"),
            "available_until": (
                local_start + timezone.timedelta(hours=2)
            ).strftime("%Y-%m-%dT%H:%M"),
            "sessions_concluded": "on",
        }
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")

        ajax = client.post(
            url,
            payload,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(ajax.status_code, 200)
        body = ajax.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["section"], "answer-key-releases")
        self.assertEqual(body["affected_course_ids"], [self.parent.id])
        self.assertEqual(body["refresh_url"], url)
        self.assertEqual(AnswerKeyRelease.objects.count(), 1)
        serialized = str(body).lower()
        for confidential in (
            "confidential key source",
            "private set",
            "correct_answer",
            "choices_snapshot",
        ):
            self.assertNotIn(confidential, serialized)
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )

        fallback = client.post(url, payload)
        self.assertEqual(fallback.status_code, 302)
        self.assertEqual(AnswerKeyRelease.objects.count(), 1)

    def test_ajax_bulk_answer_key_release_returns_affected_courses_only(self):
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        client = Client()
        client.force_login(self.release_manager)
        response = client.post(
            reverse("departmental_exams:questionnaire_print_release"),
            {
                "action": "bulk_answer_key_release",
                "selections": (f"{self.parent.id}:{self.r4.id}",),
                "available_from": local_start.strftime("%Y-%m-%dT%H:%M"),
                "available_until": (
                    local_start + timezone.timedelta(hours=2)
                ).strftime("%Y-%m-%dT%H:%M"),
                "sessions_concluded": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["affected_course_ids"],
            [self.parent.id],
        )
        self.assertEqual(response.json()["section"], "answer-key-releases")
        self.assertEqual(AnswerKeyRelease.objects.count(), 1)

    def test_ajax_validation_stale_revision_and_direct_deny_stay_fail_closed(self):
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        payload = {
            "action": "answer_key_release",
            "cycle_course_id": self.parent.id,
            "generation_revision": self.r4.id,
            "available_from": local_start.strftime("%Y-%m-%dT%H:%M"),
            "available_until": (
                local_start + timezone.timedelta(hours=2)
            ).strftime("%Y-%m-%dT%H:%M"),
        }
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        headers = {
            "HTTP_X_REQUESTED_WITH": "XMLHttpRequest",
            "HTTP_ACCEPT": "application/json",
        }

        missing_attestation = client.post(url, payload, **headers)
        self.assertEqual(missing_attestation.status_code, 400)
        self.assertFalse(missing_attestation.json()["success"])
        self.assertIn("sessions", missing_attestation.json()["message"].lower())
        self.assertFalse(AnswerKeyRelease.objects.exists())

        self._replace_current_revision(item_count=2)
        payload["sessions_concluded"] = "on"
        stale = client.post(url, payload, **headers)
        self.assertEqual(stale.status_code, 400)
        self.assertFalse(stale.json()["success"])
        self.assertFalse(AnswerKeyRelease.objects.exists())

        UserPermission.objects.create(
            user=self.generation_manager,
            permission=Permission.objects.get(
                code="departmental_exams.release_answer_keys"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        denied = Client()
        denied.force_login(self.generation_manager)
        unauthorized = denied.post(url, payload, **headers)
        self.assertEqual(unauthorized.status_code, 403)
        self.assertFalse(AnswerKeyRelease.objects.exists())

    def test_ajax_revoke_refreshes_derived_state_without_faculty_view_audit(self):
        release = self._release()
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")

        response = client.post(
            url,
            {"action": "answer_key_revoke", "release_id": release.id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        release.refresh_from_db()
        self.assertEqual(release.status, AnswerKeyRelease.Status.REVOKED)
        refreshed = client.get(url + "?section=answer-key-releases")
        row = next(
            item
            for item in refreshed.context["bulk_answer_key_rows"]
            if item["course"].id == self.parent.id
        )
        self.assertEqual(row["release_status"], "Revoked")
        self.assertIsNone(row["active_release"])
        self.assertEqual(row["displayed_release"].id, release.id)
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )

    def test_ajax_r4_release_does_not_leak_into_r5_current_bulk_row(self):
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        client = Client()
        client.force_login(self.release_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        response = client.post(
            url,
            {
                "action": "answer_key_release",
                "cycle_course_id": self.parent.id,
                "generation_revision": self.r4.id,
                "available_from": local_start.strftime("%Y-%m-%dT%H:%M"),
                "available_until": (
                    local_start + timezone.timedelta(hours=2)
                ).strftime("%Y-%m-%dT%H:%M"),
                "sessions_concluded": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        r4_release = AnswerKeyRelease.objects.get()
        r5 = self._replace_current_revision(item_count=2)

        refreshed = client.get(
            response.json()["refresh_url"] + "?section=answer-key-releases"
        )
        row = next(
            item
            for item in refreshed.context["bulk_answer_key_rows"]
            if item["course"].id == self.parent.id
        )
        self.assertEqual(row["revision"].id, r5.id)
        self.assertEqual(row["release_status"], "Not released")
        self.assertEqual(
            row["filter_release_status"],
            "Superseded / No Longer Faculty Accessible",
        )
        self.assertIsNone(row["displayed_release"])
        content = refreshed.content.decode()
        current_row = content.split(
            f'id="answer-key-row-{self.parent.id}"', 1
        )[1].split("</tr>", 1)[0]
        self.assertNotIn(
            reverse(
                "departmental_exams:answer_key_viewers",
                args=[r4_release.id],
            ),
            current_row,
        )
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ).exists()
        )

    def test_questionnaire_release_and_revoke_support_ajax_without_new_logic(self):
        local_start = timezone.localtime().replace(second=0, microsecond=0)
        client = Client()
        client.force_login(self.generation_manager)
        url = reverse("departmental_exams:questionnaire_print_release")
        headers = {
            "HTTP_X_REQUESTED_WITH": "XMLHttpRequest",
            "HTTP_ACCEPT": "application/json",
        }
        released = client.post(
            url,
            {
                "action": "release",
                "cycle_course_id": self.parent.id,
                "generation_revision": self.r4.id,
                "print_from": local_start.strftime("%Y-%m-%dT%H:%M"),
                "print_until": (
                    local_start + timezone.timedelta(hours=2)
                ).strftime("%Y-%m-%dT%H:%M"),
            },
            **headers,
        )
        self.assertEqual(released.status_code, 200)
        self.assertEqual(released.json()["section"], "questionnaire-releases")
        questionnaire_release = QuestionnairePrintRelease.objects.get()

        revoked = client.post(
            url,
            {"action": "revoke", "release_id": questionnaire_release.id},
            **headers,
        )
        self.assertEqual(revoked.status_code, 200)
        questionnaire_release.refresh_from_db()
        self.assertEqual(
            questionnaire_release.status,
            QuestionnairePrintRelease.Status.REVOKED,
        )


class AnswerKeyPermissionMigrationSafetyTests(TestCase):
    def test_seed_is_assignable_without_automatic_grants_and_reverse_is_safe(self):
        rbac_migration = import_module(
            "apps.rbac.migrations.0035_seed_answer_key_release_permission"
        )
        navigation_migration = import_module(
            "apps.navigation.migrations.0023_add_answer_key_release_permission"
        )
        navigation_migration.remove_permission(django_apps, None)
        permission = Permission.objects.filter(
            code="departmental_exams.release_answer_keys"
        ).first()
        if permission:
            RolePermission.objects.filter(permission=permission).delete()
            UserPermission.objects.filter(permission=permission).delete()
        rbac_migration.unseed_permission(django_apps, None)

        rbac_migration.seed_permission(django_apps, None)
        permission = Permission.objects.get(
            code="departmental_exams.release_answer_keys"
        )
        self.assertTrue(permission.is_active)
        self.assertFalse(RolePermission.objects.filter(permission=permission).exists())
        self.assertFalse(UserPermission.objects.filter(permission=permission).exists())
        navigation_migration.add_permission(django_apps, None)
        self.assertTrue(
            MenuItemPermission.objects.filter(
                menu_item__code="DE_EXAM_QUESTIONNAIRE_PRINT_RELEASE",
                permission=permission,
            ).exists()
        )
        rbac_migration.unseed_permission(django_apps, None)
        self.assertTrue(Permission.objects.filter(pk=permission.pk).exists())
        navigation_migration.remove_permission(django_apps, None)
        rbac_migration.unseed_permission(django_apps, None)
        self.assertFalse(Permission.objects.filter(pk=permission.pk).exists())


class AnswerKeyModelMigrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(("departmental_exams", "0018_resumable_question_csv_import"))
        self.to_state = self._state(("departmental_exams", "0019_answer_key_release"))

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def _state(self, departmental_target):
        return [
            departmental_target if app_label == "departmental_exams" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def test_0019_forward_and_reverse_create_and_remove_only_release_table(self):
        table = "departmental_exam_answer_key_releases"
        self.executor.migrate(self.from_state)
        self.assertNotIn(table, connection.introspection.table_names())
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        self.assertIn(table, connection.introspection.table_names())
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.from_state)
        self.assertNotIn(table, connection.introspection.table_names())
