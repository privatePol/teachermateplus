from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission

from .generation_readiness import Stage6ReadinessService
from .models import (
    ExamGenerationRevision,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
    GenerationSourceQuestionSnapshot,
    Question,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class GenerationReportingTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, _initial_problem = self.ready_generation_course()
        first, duplicate = list(
            Question.objects.filter(contribution__cycle_course=self.parent)
            .order_by("id")[:2]
        )
        Question.objects.filter(pk=duplicate.pk).update(
            question_text=first.question_text
        )
        self.problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=self.parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        outcome = self.generate_with_proved_selection(
            parent=self.parent,
            problem=self.problem,
        )
        self.revision = outcome.revision
        self.client = Client()
        self.client.force_login(self.reviewer)

    def audit_url(self, *, printable=False, filter_code=None, revision=None):
        url = reverse(
            (
                "departmental_exams:generation_selection_audit_print"
                if printable
                else "departmental_exams:generation_selection_audit"
            ),
            args=[(revision or self.revision).id],
        )
        return f"{url}?filter={filter_code}" if filter_code else url

    def key_url(self, set_code, *, printable=False, revision=None):
        return reverse(
            (
                "departmental_exams:generation_answer_key_print"
                if printable
                else "departmental_exams:generation_answer_key"
            ),
            args=[(revision or self.revision).id, set_code],
        )

    def test_source_audit_snapshot_matches_generation_inputs_and_context(self):
        audit = GenerationSourceAuditSnapshot.objects.get(
            generation_revision=self.revision
        )
        source_rows = GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot=audit
        )
        self.assertEqual(audit.submitted_count, len(self.problem.source_audit_questions))
        self.assertEqual(audit.eligible_count, len(self.problem.questions))
        self.assertEqual(audit.unique_logical_count, len(self.problem.questions))
        self.assertEqual(audit.redundant_copy_count, 0)
        self.assertEqual(source_rows.count(), audit.submitted_count)
        self.assertTrue(
            all(
                row.assignment_context_snapshot
                for row in source_rows.only("assignment_context_snapshot")
            )
        )

    def test_selected_membership_and_filters_match_exact_persisted_sets(self):
        all_response = self.client.get(self.audit_url())
        self.assertEqual(all_response.status_code, 200)
        self.assertTrue(all_response.context["audit_available"])
        all_rows = all_response.context["rows"]
        source_count = GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot__generation_revision=self.revision
        ).count()
        self.assertEqual(len(all_rows), source_count)

        set_a_ids = set(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=self.revision,
                generated_set__set_code=GeneratedExamSet.SetCode.A,
            ).values_list("source_question_id", flat=True)
        )
        set_b_ids = set(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=self.revision,
                generated_set__set_code=GeneratedExamSet.SetCode.B,
            ).values_list("source_question_id", flat=True)
        )
        expected_counts = {
            "selected": len(set_a_ids | set_b_ids),
            "set-a": len(set_a_ids),
            "set-b": len(set_b_ids),
            "both": len(set_a_ids & set_b_ids),
            "not-selected": source_count - len(set_a_ids | set_b_ids),
            "duplicate": 2,
        }
        for filter_code, expected_count in expected_counts.items():
            with self.subTest(filter_code=filter_code):
                response = self.client.get(
                    self.audit_url(filter_code=filter_code)
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context["rows"]), expected_count)
        duplicate_response = self.client.get(
            self.audit_url(filter_code="duplicate")
        )
        self.assertContains(duplicate_response, "EQ-001", count=2)
        self.assertNotContains(
            duplicate_response,
            GenerationSourceQuestionSnapshot.objects.filter(
                audit_snapshot__generation_revision=self.revision
            ).first().normalized_fingerprint,
        )

    def test_screen_and_print_reports_number_displayed_rows_sequentially(self):
        for printable in (False, True):
            for filter_code in (None, "duplicate"):
                with self.subTest(
                    printable=printable,
                    filter_code=filter_code or "all",
                ):
                    response = self.client.get(
                        self.audit_url(
                            printable=printable,
                            filter_code=filter_code,
                        )
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(
                        response,
                        (
                            '<th class="number">No.</th>'
                            if printable
                            else "<th>No.</th>"
                        ),
                        html=True,
                    )
                    for number, row in enumerate(
                        response.context["rows"],
                        start=1,
                    ):
                        number_cell = (
                            f'<td class="number">{number}</td>'
                            if printable
                            else f'<td class="text-center">{number}</td>'
                        )
                        self.assertContains(
                            response,
                            f"{number_cell}<td>{row['question']}</td>",
                            html=True,
                        )

    def test_legacy_revision_discloses_only_exact_selected_membership(self):
        selected_item = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=self.revision
        ).first()
        selected_ids = set(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=self.revision
            ).values_list("source_question_id", flat=True)
        )
        unselected = GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot__generation_revision=self.revision
        ).exclude(source_question_id_snapshot__in=selected_ids).first()
        audit = GenerationSourceAuditSnapshot.objects.get(
            generation_revision=self.revision
        )
        GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot=audit
        ).delete()
        GenerationSourceAuditSnapshot.objects.filter(pk=audit.pk).delete()

        response = self.client.get(self.audit_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Source-pool audit unavailable for this legacy revision.",
        )
        self.assertContains(response, selected_item.question_text_snapshot)
        self.assertNotContains(response, unselected.question_text_snapshot)
        self.assertIsNone(response.context["summary"]["not_selected"])

    def test_unauthorized_and_cross_tenant_audit_access_fail_closed(self):
        unauthorized = Client()
        unauthorized.force_login(self.configurer)
        self.assertEqual(unauthorized.get(self.audit_url()).status_code, 403)

        other_admin = get_user_model().objects.create_superuser(
            "other-tenant-report-admin",
            "other-tenant-report-admin@example.edu",
            "Pass123!",
            default_tenant=self.other_tenant,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        cross_tenant = Client()
        cross_tenant.force_login(other_admin)
        self.assertEqual(cross_tenant.get(self.audit_url()).status_code, 404)

    def test_set_a_and_b_answer_keys_are_exact_no_store_and_safely_audited(self):
        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                expected = list(
                    GeneratedExamItem.objects.filter(
                        generated_set__generation_revision=self.revision,
                        generated_set__set_code=set_code,
                    )
                    .order_by("position")
                    .values_list("position", "correct_answer_snapshot")
                )
                for printable in (False, True):
                    response = self.client.get(
                        self.key_url(set_code, printable=printable)
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    actual = [
                        (row["position"], row["correct_answer_snapshot"])
                        for row in response.context["items"]
                    ]
                    self.assertEqual(actual, expected)
                    self.assertContains(
                        response,
                        f"{'SET' if printable else 'Set'} {set_code}",
                    )
                    self.assertContains(response, f"Revision R{self.revision.revision_number}")
        audits = AuditLog.objects.filter(
            action__in=(
                "DE_GENERATION_KEY_ACCESSED",
                "DE_GENERATION_KEY_PRINTED",
            )
        )
        self.assertEqual(audits.count(), 4)
        for audit in audits:
            metadata = str(audit.metadata_json).lower()
            self.assertNotIn("answer", metadata)
            self.assertNotIn("question", metadata)
            self.assertNotIn("fingerprint", metadata)

    def test_historical_answer_key_never_substitutes_new_revision(self):
        historical_first = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=self.revision,
            generated_set__set_code=GeneratedExamSet.SetCode.A,
        ).order_by("position").first()
        original_answer = historical_first.correct_answer_snapshot
        replacement = "B" if original_answer != "B" else "C"
        Question.objects.filter(pk=historical_first.source_question_id).update(
            correct_answer=replacement,
            revision=historical_first.source_question_revision + 1,
        )
        next_problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=self.parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        second = self.generate_with_proved_selection(
            parent=self.parent,
            problem=next_problem,
            token="u" * 40,
            expected_revision=1,
            regeneration=True,
            reason="Exact historical answer key regression check.",
        ).revision

        historical_response = self.client.get(
            self.key_url("A", revision=self.revision)
        )
        self.assertEqual(historical_response.status_code, 200)
        self.assertEqual(
            historical_response.context["revision"].id,
            self.revision.id,
        )
        self.assertNotEqual(second.id, self.revision.id)
        self.assertEqual(
            historical_response.context["items"][0]["correct_answer_snapshot"],
            original_answer,
        )

    def test_manual_reviewer_direct_deny_blocks_audit_and_answer_key(self):
        UserPermission.objects.create(
            user=self.reviewer,
            permission=Permission.objects.get(
                code="departmental_exams.review_generate"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(self.client.get(self.audit_url()).status_code, 403)
        self.assertEqual(self.client.get(self.key_url("A")).status_code, 403)

    def test_printable_audit_is_no_store_and_exposes_no_raw_identity_hash(self):
        source = GenerationSourceQuestionSnapshot.objects.filter(
            audit_snapshot__generation_revision=self.revision
        ).first()
        response = self.client.get(self.audit_url(printable=True))
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        body = response.content.decode()
        self.assertNotIn(source.normalized_fingerprint, body)
        self.assertNotIn("HMAC", body)
        audit = AuditLog.objects.get(
            action="DE_GENERATION_SELECTION_AUDIT_PRINTED"
        )
        metadata = str(audit.metadata_json).lower()
        self.assertNotIn("answer", metadata)
        self.assertNotIn("question", metadata)
        self.assertNotIn("fingerprint", metadata)
