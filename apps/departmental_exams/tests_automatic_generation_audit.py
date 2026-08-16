from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.rbac.models import (
    Permission,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)

from .automatic_generation_audit import AutomaticGenerationAuditService
from .automatic_workflow import AutomaticExamDeadlineService
from .generation_algorithms import solve_automatic_identity_aware_two_sets
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    AutomaticGenerationAuditRun,
    CourseExamConfiguration,
    ExamGenerationRevision,
    ExaminationCycle,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
    GenerationSourceQuestionSnapshot,
    Question,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class AutomaticGenerationAuditTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.manager = self._make_automatic_user(
            "automatic-audit-manager",
            (
                "departmental_exams.manage_exam_generation",
                "departmental_exams.print_generated_exams",
            ),
        )
        self.auditor = self._make_automatic_user(
            "automatic-auditor",
            ("departmental_exams.audit_generated_exams",),
        )
        self.parent, _problem = self.ready_generation_course()
        cycle = self.parent.cycle
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent.cycle = cycle
        self.parent.responsible_department = None
        self.parent.reviewer = None
        self.parent.save(
            update_fields=["responsible_department", "reviewer", "updated_at"]
        )
        configuration = CourseExamConfiguration.objects.get(
            cycle_course=self.parent
        )
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=self.parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._automatic_selection(problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=self.parent.id,
                tenant_id=self.tenant.id,
            )
        self.assertEqual(result.status, "GENERATED")
        self.revision = ExamGenerationRevision.objects.get()
        self.client = Client()
        self.client.force_login(self.auditor)

    def _make_automatic_user(self, username, permission_codes):
        user = get_user_model().objects.create_user(
            username,
            f"{username}@example.edu",
            "Pass123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code=username.upper().replace("-", "_")[:50],
            name=username,
        )
        for code in ("admin_portal.access", *permission_codes):
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
    def _automatic_selection(problem):
        selection = solve_automatic_identity_aware_two_sets(
            margins=problem.margins,
            blocks=problem.blocks,
            campus_quotas=problem.campus_quotas,
            difficulty_quotas=problem.difficulty_quotas,
            secret=settings.SECRET_KEY,
            hmac_context={"test_selection": "automatic-audit"},
            max_states=ExamGenerationService.AUTOMATIC_DEFAULT_MAX_STATES,
        )
        if not selection.feasible:
            raise AssertionError(selection)
        return selection

    def _run_url(self):
        return reverse("departmental_exams:questionnaire_print_release")

    def _result_url(self, run, *, printable=False):
        return reverse(
            (
                "departmental_exams:automatic_generation_audit_result_print"
                if printable
                else "departmental_exams:automatic_generation_audit_result"
            ),
            args=[self.revision.id, run.id],
        )

    def _run_from_page(self):
        response = self.client.post(
            self._run_url(),
            {
                "action": "run_audit",
                "revision_id": self.revision.id,
            },
        )
        run = AutomaticGenerationAuditRun.objects.latest("id")
        self.assertRedirects(response, self._result_url(run))
        return run

    @staticmethod
    def _findings(run):
        return {row["code"]: row for row in run.findings_snapshot}

    def test_authorized_auditor_runs_exact_revision_pass_and_views_safe_result(self):
        page = self.client.get(self._run_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Run Automatic Audit")
        self.assertNotContains(page, "Release an exact revision")

        run = self._run_from_page()

        self.assertEqual(run.generation_revision, self.revision)
        self.assertEqual(
            run.status,
            AutomaticGenerationAuditRun.Status.PASS,
            run.findings_snapshot,
        )
        self.assertEqual(run.check_version, "automatic-audit-v1")
        findings = self._findings(run)
        expected_codes = {
            "SET_A_ITEM_COUNT",
            "SET_B_ITEM_COUNT",
            "SET_A_POSITIONS",
            "SET_B_POSITIONS",
            "SET_A_LOGICAL_UNIQUENESS",
            "SET_B_LOGICAL_UNIQUENESS",
            "SET_A_DIFFICULTY_DISTRIBUTION",
            "SET_B_DIFFICULTY_DISTRIBUTION",
            "SET_A_CAMPUS_ALLOCATION",
            "SET_B_CAMPUS_ALLOCATION",
            "SET_OVERLAP",
            "ELIGIBLE_SUBMITTED_SOURCES",
            "CORRECT_ANSWER_COMPLETENESS",
            "SOURCE_AUDIT_COUNTS",
            "SOURCE_MEMBERSHIP_CONSISTENCY",
            "SOURCE_AUDIT_DIGESTS",
            "REVISION_SNAPSHOT_INTEGRITY",
        }
        self.assertEqual(set(findings), expected_codes)
        self.assertTrue(all(row["status"] == "PASS" for row in findings.values()))
        for printable in (False, True):
            response = self.client.get(self._result_url(run, printable=printable))
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            body = response.content.decode()
            self.assertIn("Overall" if not printable else "Automatic Generation Audit", body)
            source_hash = GenerationSourceQuestionSnapshot.objects.filter(
                audit_snapshot__generation_revision=self.revision
            ).values_list("normalized_fingerprint", flat=True).first()
            self.assertNotIn(source_hash, body)
            self.assertNotIn("HMAC", body)

        for event in AuditLog.objects.filter(
            action__in=(
                "DE_AUTOMATIC_GENERATION_AUDIT_RUN",
                "DE_AUTOMATIC_GENERATION_AUDIT_RESULT_VIEWED",
                "DE_AUTOMATIC_GENERATION_AUDIT_RESULT_PRINTED",
            )
        ):
            metadata = str(event.metadata_json).lower()
            self.assertNotIn("question", metadata)
            self.assertNotIn("answer", metadata)
            self.assertNotIn("fingerprint", metadata)
            self.assertNotIn("hmac", metadata)

    def test_unauthorized_and_direct_denied_auditors_fail_closed(self):
        unauthorized = Client()
        unauthorized.force_login(self.configurer)
        self.assertEqual(unauthorized.get(self._run_url()).status_code, 403)
        self.assertEqual(
            unauthorized.post(
                self._run_url(),
                {"action": "run_audit", "revision_id": self.revision.id},
            ).status_code,
            403,
        )
        denied_campus = self.parent.offering_snapshots.order_by("campus_id").first().campus
        UserPermission.objects.create(
            user=self.auditor,
            permission=Permission.objects.get(
                code="departmental_exams.audit_generated_exams"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=denied_campus,
        )
        self.assertEqual(self.client.get(self._run_url()).status_code, 403)
        self.assertFalse(AutomaticGenerationAuditRun.objects.exists())

    def test_item_count_and_position_defects_fail_deterministically(self):
        set_a = GeneratedExamSet.objects.get(
            generation_revision=self.revision,
            set_code="A",
        )
        GeneratedExamSet.objects.filter(pk=set_a.pk).update(item_count=49)
        run = AutomaticGenerationAuditService.run(
            revision_id=self.revision.id,
            tenant_id=self.tenant.id,
            actor=self.auditor,
        )
        self.assertEqual(run.status, "FAIL")
        self.assertEqual(self._findings(run)["SET_A_ITEM_COUNT"]["status"], "FAIL")

        GeneratedExamSet.objects.filter(pk=set_a.pk).update(item_count=50)
        first_item = set_a.items.order_by("position").first()
        GeneratedExamItem.objects.filter(pk=first_item.pk).update(position=99)
        second = AutomaticGenerationAuditService.run(
            revision_id=self.revision.id,
            tenant_id=self.tenant.id,
            actor=self.auditor,
        )
        self.assertEqual(second.status, "FAIL")
        self.assertEqual(self._findings(second)["SET_A_POSITIONS"]["status"], "FAIL")

    def test_logical_difficulty_campus_and_source_defects_are_detected(self):
        set_a_items = list(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=self.revision,
                generated_set__set_code="A",
            ).order_by("position")[:2]
        )
        sources = list(
            GenerationSourceQuestionSnapshot.objects.filter(
                audit_snapshot__generation_revision=self.revision,
                source_question_id_snapshot__in=[
                    item.source_question_id for item in set_a_items
                ],
            ).order_by("source_question_id_snapshot")
        )
        GenerationSourceQuestionSnapshot.objects.filter(pk=sources[1].pk).update(
            normalized_fingerprint=sources[0].normalized_fingerprint,
            eligible_for_generation=False,
            exclusion_code="TEST_INELIGIBLE",
            source_question_digest="f" * 64,
        )
        first = set_a_items[0]
        replacement_difficulty = (
            "DIFFICULT" if first.difficulty_snapshot != "DIFFICULT" else "EASY"
        )
        GeneratedExamItem.objects.filter(pk=first.pk).update(
            difficulty_snapshot=replacement_difficulty,
            source_campus_id=self.other_campus.id,
            campus_code_snapshot=self.other_campus.code,
        )

        run = AutomaticGenerationAuditService.run(
            revision_id=self.revision.id,
            tenant_id=self.tenant.id,
            actor=self.auditor,
        )
        findings = self._findings(run)
        self.assertEqual(run.status, "FAIL")
        for code in (
            "SET_A_LOGICAL_UNIQUENESS",
            "SET_A_DIFFICULTY_DISTRIBUTION",
            "SET_A_CAMPUS_ALLOCATION",
            "ELIGIBLE_SUBMITTED_SOURCES",
            "SOURCE_AUDIT_COUNTS",
            "SOURCE_MEMBERSHIP_CONSISTENCY",
            "SOURCE_AUDIT_DIGESTS",
            "REVISION_SNAPSHOT_INTEGRITY",
        ):
            with self.subTest(code=code):
                self.assertEqual(findings[code]["status"], "FAIL")

    def test_legacy_revision_warns_only_for_unavailable_source_evidence(self):
        audit = GenerationSourceAuditSnapshot.objects.get(
            generation_revision=self.revision
        )
        GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot=audit
        ).delete()
        GenerationSourceAuditSnapshot.objects.filter(pk=audit.pk).delete()

        run = AutomaticGenerationAuditService.run(
            revision_id=self.revision.id,
            tenant_id=self.tenant.id,
            actor=self.auditor,
        )

        self.assertEqual(run.status, "WARNING", run.findings_snapshot)
        findings = self._findings(run)
        self.assertEqual(findings["SET_A_ITEM_COUNT"]["status"], "PASS")
        self.assertEqual(findings["SET_B_ITEM_COUNT"]["status"], "PASS")
        self.assertEqual(findings["SET_OVERLAP"]["status"], "PASS")
        self.assertEqual(findings["SOURCE_AUDIT_COUNTS"]["status"], "WARNING")
        self.assertTrue(
            findings["SOURCE_AUDIT_COUNTS"]["metrics"]["check_unavailable"]
        )

    def test_audit_reruns_preserve_history(self):
        first = self._run_from_page()
        second = self._run_from_page()
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            list(
                self.revision.automatic_audit_runs.order_by("id").values_list(
                    "id", flat=True
                )
            ),
            [first.id, second.id],
        )
        page = self.client.get(self._run_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self._result_url(second))

    def test_regeneration_creates_distinct_revision_requiring_its_own_audit(self):
        first_run = AutomaticGenerationAuditService.run(
            revision_id=self.revision.id,
            tenant_id=self.tenant.id,
            actor=self.manager,
        )
        question = Question.objects.filter(
            contribution__cycle_course=self.parent
        ).order_by("id").first()
        Question.objects.filter(pk=question.pk).update(
            question_text=question.question_text + " revised"
        )
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=self.parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        selection = self._automatic_selection(problem)
        with patch(
            "apps.departmental_exams.generation_services.solve_automatic_identity_aware_two_sets",
            return_value=selection,
        ):
            outcome = ExamGenerationService.generate(
                cycle_course_id=self.parent.id,
                tenant_id=self.tenant.id,
                actor=self.manager,
                expected_current_revision=self.revision.revision_number,
                expected_input_fingerprint=problem.input_fingerprint,
                request_token="a" * 40,
                regeneration=True,
                regeneration_reason="Deterministic audit revision isolation test.",
            )
        second_revision = outcome.revision
        self.assertNotEqual(second_revision.id, self.revision.id)
        self.assertFalse(second_revision.automatic_audit_runs.exists())
        second_run = AutomaticGenerationAuditService.run(
            revision_id=second_revision.id,
            tenant_id=self.tenant.id,
            actor=self.auditor,
        )
        self.assertEqual(first_run.generation_revision_id, self.revision.id)
        self.assertEqual(second_run.generation_revision_id, second_revision.id)
        self.assertEqual(AutomaticGenerationAuditRun.objects.count(), 2)
