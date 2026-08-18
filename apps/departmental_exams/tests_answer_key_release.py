from importlib import import_module

from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.navigation.models import MenuItemPermission
from apps.rbac.models import Permission, RolePermission, UserPermission

from .answer_key_release import (
    ANSWER_KEY_RELEASE_ATTESTATION_VERSION,
    AnswerKeyReleaseService,
)
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

    def _make_revision(self, number, *, supersedes=None):
        revision = ExamGenerationRevision.objects.create(
            cycle_course=self.parent,
            revision_number=number,
            source_input_fingerprint=(str(number) * 64)[:64],
            algorithm_version="answer-key-release-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="r" * 64,
            final_item_count_snapshot=2,
            request_token_digest=(str(number + 4) * 64)[:64],
            supersedes=supersedes,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=4,
        )
        for set_code, answers in (("A", ("A", "D")), ("B", ("C", "B"))):
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot={"MAIN": 2},
                difficulty_quotas_snapshot={"EASY": 2},
                section_quotas_snapshot={"0": 2},
                item_count=2,
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
