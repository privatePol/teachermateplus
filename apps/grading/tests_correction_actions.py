"""Submission/signing regressions. All records/files belong to disposable tests."""
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.http import QueryDict
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserSignatureCredential
from apps.accounts.services import UserSignatureService
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.faculty_portal.forms import GradeCorrectionRequestForm
from apps.grading import tests as fixtures
from apps.grading.correction_actions import (
    AdminOnBehalfSubmissionService as AdminSubmission,
    CorrectionSignatureRequired, CorrectionSubmissionService as Submission,
    new_action_token,
)
from apps.grading.models import (
    CorrectionActionReceipt, CorrectionApprovalRouteStep, CorrectionStepNotificationIntent,
    GradeCorrectionAttachment, GradeCorrectionRequest, GradeCorrectionSignatureEvidence,
    StudentActivityScore,
)
from apps.grading.reporting import CorrectionOfficialReportService as Report
from apps.grading.services import GradingGovernanceService as Governance
from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole


class ActionFixtures:
    _grant_faculty_correction_access = fixtures.CorrectionWorkflowTests._grant_faculty_correction_access
    _set_correction_lifecycle = fixtures.CorrectionWorkflowTests._set_correction_lifecycle

    def setUp(self):
        fixtures.CorrectionWorkflowTests.setUp(self)
        fixtures.CorrectionWorkflowTests._grant_faculty_correction_access(self)
        fixtures.CorrectionWorkflowTests._grant_admin_on_behalf_access(self)
        self.url = reverse("faculty_portal:period_corrections", args=[self.offering.pk, self.period.pk])
        for code in ("admin_portal.access", "corrections.review"):
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": "test", "action": "review"})
            for role in (self.reviewer_role, self.cao_role, self.super_admin_role):
                RolePermission.objects.get_or_create(role=role, permission=permission)

    def token(self):
        return new_action_token(kind="SUBMIT", user=self.faculty_user, offering=self.offering, period=self.period)

    def post_data(self, token=None, value="35"):
        data = QueryDict(mutable=True)
        data.setlist("students", [str(self.student1.pk)])
        data.setlist("grade_activities", [str(self.activity.pk)])
        data.update(submission_token=token or self.token(), justification="Correct the recorded quiz score.",
            correction_payload=json.dumps([dict(student_id=str(self.student1.pk), grade_activity_id=str(self.activity.pk), new_value=value)]))
        return data

    def form(self, data):
        file = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4\nfixture evidence", content_type="application/pdf")
        form = GradeCorrectionRequestForm(data, {"attachment": file},
            student_queryset=type(self.student1).objects.filter(pk=self.student1.pk),
            activity_queryset=type(self.activity).objects.filter(pk=self.activity.pk).select_related(
                "template_component", "template_subcomponent", "template_detail"))
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def submit(self, data):
        form = self.form(data)
        return Submission.submit(token=data["submission_token"], payload=Submission.payload(data, form.files),
            user=self.faculty_user, offering=self.offering, period=self.period, form=form)

    def review_token(self, petition, user=None):
        user = user or self.reviewer_user
        step = Governance.get_pending_correction_step(request_obj=petition)
        return step.pk, new_action_token(kind="REVIEW", user=user, offering=self.offering,
            period=self.period, request_id=petition.pk, step_id=step.pk)

    def deny(self, user, code, tenant=None, campus=None):
        return UserPermission.objects.create(user=user, permission=Permission.objects.get(code=code),
            tenant=tenant, campus=campus, grant_type="DENY")


class CorrectionActionTests(ActionFixtures, TestCase):
    @staticmethod
    def export_qa(name, contents):
        from django.conf import settings
        from pathlib import Path
        target = getattr(settings, "CORRECTION_QA_DIR", None)
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(contents)

    def test_http_missing_signature_popup_preserves_token_and_form_then_retry_succeeds(self):
        fixtures.CorrectionWorkflowTests._prepare_correction_filing_page(self)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.export_qa("correction-form.html", page.content)
        data = self.post_data(page.context["form"]["submission_token"].value())
        credential = UserSignatureCredential.objects.get(user=self.faculty_user)
        original = bytes(credential.encrypted_blob)
        credential.encrypted_blob = None
        credential.save(update_fields=["encrypted_blob"])
        def post():
            values = {k: data.getlist(k) if k in ("students", "grade_activities") else data[k] for k in data}
            values["attachment"] = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4\nfixture evidence", content_type="application/pdf")
            return self.client.post(self.url, values)
        failed = post()
        self.assertEqual(failed.status_code, 200)
        self.assertContains(failed, 'id="correction-signature-required"')
        self.assertContains(failed, '.showModal()')
        self.assertEqual(failed.context["form"]["submission_token"].value(), data["submission_token"])
        self.assertEqual(failed.context["form"]["justification"].value(), data["justification"])
        self.assertEqual(GradeCorrectionRequest.objects.count(), 0)
        credential.encrypted_blob = original
        credential.save(update_fields=["encrypted_blob"])
        submitted = post()
        self.assertEqual(submitted.status_code, 302)
        repeated = post()
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)
        self.assertEqual(GradeCorrectionAttachment.objects.count(), 1)
        self.assertIn("#correction-", submitted.url)

    def test_http_invalid_grid_then_valid_retry_and_missing_token_fails_closed(self):
        fixtures.CorrectionWorkflowTests._prepare_correction_filing_page(self)
        data = self.post_data(self.client.get(self.url).context["form"]["submission_token"].value(), "999")
        failed = self.client.post(self.url, data)
        self.assertEqual(failed.status_code, 200)
        self.assertContains(failed, "correction-validation-summary")
        self.assertFalse(CorrectionActionReceipt.objects.exists())
        form = self.form(self.post_data(data["submission_token"]))
        valid = dict(form.data)
        valid = {k: v if k in ("students", "grade_activities") else v[-1] for k, v in valid.items()}
        valid["attachment"] = form.files["attachment"]
        valid.pop("submission_token")
        result = self.client.post(self.url, valid)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(GradeCorrectionRequest.objects.exists())
        valid["submission_token"] = data["submission_token"]
        valid["attachment"] = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4\nfixture evidence", content_type="application/pdf")
        self.assertEqual(self.client.post(self.url, valid).status_code, 302)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)

    def test_signature_setup_uses_authorized_target_context_without_changing_default_tenant(self):
        from apps.tenants.models import Tenant, Campus
        other = Tenant.objects.create(code="DEFAULT-OTHER", name="Other default")
        campus = Campus.objects.create(tenant=other, code="OTHER", name="Other")
        self.faculty_user.default_tenant = other
        self.faculty_user.default_campus = campus
        self.faculty_user.save(update_fields=["default_tenant", "default_campus"])
        self.client.force_login(self.faculty_user)
        url = reverse("accounts:faculty_signature") + "?correction_context=" + self.token()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        preview = self.client.get(reverse("accounts:faculty_signature_preview"))
        self.assertEqual(preview.status_code, 200)
        self.faculty_user.refresh_from_db()
        self.assertEqual(self.faculty_user.default_tenant_id, other.pk)
        self.client.force_login(self.reviewer_user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_review_post_recovers_same_step_and_missing_signature_keeps_remarks(self):
        petition, _ = self.submit(self.post_data())
        self.reviewer_user.privacy_consent_version = self.faculty_user.privacy_consent_version
        self.reviewer_user.privacy_consent_at = timezone.now()
        self.reviewer_user.save(update_fields=["privacy_consent_version", "privacy_consent_at"])
        self.client.force_login(self.reviewer_user)
        url = reverse("admin_portal:grade_correction_request_review", args=[petition.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.export_qa("correction-review.html", page.content)
        data = dict(action_token=page.context["form"]["action_token"].value(),
            expected_step_id=page.context["form"]["expected_step_id"].value(),
            decision="APPROVE", review_remarks="Recorded signed decision")
        credential = UserSignatureCredential.objects.get(user=self.reviewer_user)
        saved = bytes(credential.encrypted_blob)
        credential.encrypted_blob = None
        credential.save(update_fields=["encrypted_blob"])
        failed = self.client.post(url, data)
        self.assertEqual(failed.status_code, 200)
        self.assertTrue(failed.context["signature_required"])
        self.assertEqual(failed.context["form"]["review_remarks"].value(), data["review_remarks"])
        credential.encrypted_blob = saved
        credential.save(update_fields=["encrypted_blob"])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(petition.signature_evidence.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="CORRECTION_APPROVAL_REVIEWED").count(), 1)

    def test_submission_replay_has_one_request_attachment_audit_intent_and_signature(self):
        data = self.post_data()
        SystemSettingService.set(FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True, tenant_id=self.tenant.pk, value_type="BOOL")
        with self.captureOnCommitCallbacks(execute=True) as callbacks, patch("django.core.mail.EmailMultiAlternatives.send") as send:
            first, created = self.submit(data)
            second, repeated = self.submit(data)
        self.assertEqual(callbacks, [])
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)
        self.assertEqual(GradeCorrectionAttachment.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="CREATE", entity_type="GradeCorrectionRequest").count(), 1)
        self.assertEqual(CorrectionStepNotificationIntent.objects.count(), 1)
        self.assertEqual(GradeCorrectionSignatureEvidence.objects.count(), 1)
        self.assertEqual(first.approval_steps.count(), 1)
        send.assert_not_called()

    def test_changed_payload_same_token_rejected_and_distinct_tokens_remain_legitimate(self):
        data = self.post_data()
        self.submit(data)
        with self.assertRaises(ValidationError):
            self.submit(self.post_data(data["submission_token"], "36"))
        self.submit(self.post_data())
        self.assertEqual(GradeCorrectionRequest.objects.count(), 2)

    def test_signed_context_rejects_other_user_tenant_campus_and_period(self):
        data = self.post_data()
        form = self.form(data)
        payload = Submission.payload(data, form.files)
        with self.assertRaises(PermissionDenied):
            Submission.recover(token=data["submission_token"], payload=payload, user=self.reviewer_user,
                offering=self.offering, period=self.period)
        # Even another authorized co-faculty cannot reuse the first actor's token.
        from apps.academics.models import FacultyAssignment
        UserRole.objects.create(user=self.cao_user, role=self.faculty_role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            faculty_user=self.cao_user, is_primary=False, accepted_at=timezone.now(),
            accepted_by=self.cao_user, response_status="ACCEPTED")
        with self.assertRaises(ValidationError):
            Submission.recover(token=data["submission_token"], payload=payload, user=self.cao_user,
                offering=self.offering, period=self.period)
        from apps.grading.correction_actions import token_key
        for key in ("tenant_id", "campus_id"):
            original = getattr(self.offering, key)
            setattr(self.offering, key, original + 999)
            with self.assertRaises(ValidationError):
                token_key(data["submission_token"], kind="SUBMIT", user=self.faculty_user, offering=self.offering, period=self.period)
            setattr(self.offering, key, original)
        original = self.period.pk
        self.period.pk += 999
        with self.assertRaises(ValidationError):
            token_key(data["submission_token"], kind="SUBMIT", user=self.faculty_user, offering=self.offering, period=self.period)
        self.period.pk = original

    def test_global_direct_deny_blocks_filing_and_superuser_override(self):
        self.deny(self.faculty_user, "corrections.create")
        with self.assertRaises(PermissionDenied):
            self.submit(self.post_data())
        self.faculty_user.is_superuser = True
        self.assertFalse(PermissionService.has_permission(self.faculty_user, "corrections.create", self.tenant.pk, self.campus.pk))
        self.assertEqual(GradeCorrectionRequest.objects.count(), 0)

    def test_missing_corrupt_and_disabled_signatures_cannot_create_then_retry_succeeds(self):
        data = self.post_data()
        credential = UserSignatureCredential.objects.get(user=self.faculty_user)
        encrypted = bytes(credential.encrypted_blob)
        for replacement in (None, b"invalid"):
            credential.encrypted_blob = replacement
            credential.save()
            with self.assertRaises(CorrectionSignatureRequired): self.submit(data)
            self.assertEqual(CorrectionActionReceipt.objects.count(), 0)
        credential.encrypted_blob = encrypted
        credential.save()
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY, False,
            tenant_id=self.tenant.pk, value_type="BOOL")
        with self.assertRaises(CorrectionSignatureRequired): self.submit(data)
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY, True,
            tenant_id=self.tenant.pk, value_type="BOOL")
        self.submit(data)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)

    def test_legal_long_signer_names_and_approval_captions_are_retained(self):
        self.faculty_user.first_name = "F" * 150
        self.faculty_user.last_name = "L" * 150
        self.faculty_user.save(update_fields=["first_name", "last_name"])
        petition, _ = self.submit(self.post_data())
        step = Governance.get_pending_correction_step(request_obj=petition)
        step.approver_label = "R" * 150
        step.save(update_fields=["approver_label"])
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        evidence = petition.signature_evidence.get(action="SUBMIT")
        self.assertEqual(evidence.signer_name, self.faculty_user.full_name)
        self.assertGreater(len(evidence.signer_name), 255)
        self.assertGreater(len(petition.signature_evidence.get(action="APPROVE").signing_role), 150)

    def test_recovery_after_signature_removal_and_changed_lifecycle_uses_original_evidence(self):
        data = self.post_data()
        petition, _ = self.submit(data)
        form = self.form(data)
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        UserSignatureService.clear_signature(user=self.faculty_user)
        found = Submission.recover(token=data["submission_token"], payload=Submission.payload(data, form.files),
            user=self.faculty_user, offering=self.offering, period=self.period)
        self.assertEqual(found.pk, petition.pk)
        self.assertEqual(found.signature_evidence.count(), 2)

    def test_audit_failure_rolls_back_receipt_records_intents_and_new_file(self):
        from django.conf import settings
        from pathlib import Path
        from apps.grading.models import CorrectionEmailDelivery
        SystemSettingService.set(FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True, tenant_id=self.tenant.pk, value_type="BOOL")
        before = {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()}
        original = __import__("apps.core.services.audit", fromlist=["AuditService"]).AuditService.log_event
        def audit(**kwargs):
            if kwargs["action"] == "CREATE": raise RuntimeError("simulated audit failure")
            return original(**kwargs)
        with patch("apps.core.services.audit.AuditService.log_event", side_effect=audit):
            with self.assertRaises(RuntimeError): self.submit(self.post_data())
        self.assertFalse(GradeCorrectionRequest.objects.exists())
        self.assertFalse(CorrectionActionReceipt.objects.exists())
        self.assertFalse(GradeCorrectionSignatureEvidence.objects.exists())
        self.assertFalse(CorrectionStepNotificationIntent.objects.exists())
        self.assertFalse(CorrectionEmailDelivery.objects.exists())
        self.assertEqual(before, {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()})

    def test_storage_failure_rolls_back_all_database_effects(self):
        with patch("django.core.files.storage.FileSystemStorage.save", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError): self.submit(self.post_data())
        self.assertFalse(GradeCorrectionRequest.objects.exists())
        self.assertFalse(CorrectionActionReceipt.objects.exists())

    def test_missing_approver_signature_blocks_approval_but_allows_rejection(self):
        petition, _ = self.submit(self.post_data())
        UserSignatureService.clear_signature(user=self.reviewer_user)
        with self.assertRaises(CorrectionSignatureRequired):
            Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        petition.refresh_from_db()
        self.assertEqual(petition.status, "PENDING")
        rejected = Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=False)
        self.assertEqual(rejected.status, "REJECTED")
        self.assertEqual(petition.signature_evidence.count(), 1)

    def test_approval_direct_deny_and_wrong_reviewer_never_capture_signatures(self):
        petition, _ = self.submit(self.post_data())
        self.deny(self.reviewer_user, "corrections.review")
        with self.assertRaises(PermissionDenied):
            Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        with self.assertRaises(ValidationError):
            Governance.review_correction_request(request_obj=petition, reviewer=self.faculty_user, approved=True)
        self.assertEqual(petition.signature_evidence.count(), 1)

    def test_local_query_cost_and_no_network_for_submission_and_recovery(self):
        from time import perf_counter
        from django.test.utils import CaptureQueriesContext
        data = self.post_data()
        form = self.form(data)
        payload = Submission.payload(data, form.files)
        started = perf_counter()
        with CaptureQueriesContext(connection) as creation:
            petition, _ = Submission.submit(token=data["submission_token"], payload=payload, user=self.faculty_user,
                offering=self.offering, period=self.period, form=form)
        first_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        with CaptureQueriesContext(connection) as recovery:
            replay = Submission.recover(token=data["submission_token"], payload=payload, user=self.faculty_user,
                offering=self.offering, period=self.period)
        replay_ms = (perf_counter() - started) * 1000
        self.assertEqual(petition.pk, replay.pk)
        self.assertLess(len(recovery), len(creation))
        print(f"LOCAL_SUBMISSION_DIAGNOSTIC first_queries={len(creation)} first_ms={first_ms:.2f} recovery_queries={len(recovery)} recovery_ms={replay_ms:.2f}")

    def test_review_token_cannot_advance_a_second_step_even_with_both_roles(self):
        CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=1, approver_role=self.reviewer_role)
        CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=2, approver_role=self.cao_role)
        UserRole.objects.create(user=self.reviewer_user, role=self.cao_role, tenant=self.tenant, campus=self.campus)
        petition, _ = self.submit(self.post_data())
        step_id, token = self.review_token(petition)
        kwargs = dict(request_obj=petition, reviewer=self.reviewer_user, approved=True, review_remarks="Reviewed",
            action_token=token, expected_step_id=step_id, dispatch_notifications=False)
        Governance.review_correction_request(**kwargs)
        replay = Governance.review_correction_request(**kwargs)
        self.assertTrue(replay.action_replayed)
        self.assertEqual(petition.approval_steps.filter(status="APPROVED").count(), 1)
        self.assertEqual(petition.approval_steps.filter(status="PENDING").count(), 1)
        with self.assertRaises(ValidationError):
            Governance.review_correction_request(**{**kwargs, "review_remarks": "different"})

    def test_signature_evidence_survives_removal_and_is_immutable(self):
        from PIL import Image
        from pypdf import PdfReader
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        evidence = list(petition.signature_evidence.order_by("pk"))
        original = [(r.content_sha256, bytes(r.encrypted_blob)) for r in evidence]
        UserSignatureService.clear_signature(user=self.faculty_user)
        replacement = BytesIO()
        Image.new("RGBA", (40, 12), (255, 0, 0, 255)).save(replacement, format="PNG")
        UserSignatureService.store_signature(user=self.reviewer_user, actor=self.reviewer_user,
            uploaded_file=SimpleUploadedFile("replacement.png", replacement.getvalue(), content_type="image/png"))
        self.assertEqual(original, [(r.content_sha256, bytes(r.encrypted_blob)) for r in petition.signature_evidence.order_by("pk")])
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
            True, tenant_id=self.tenant.pk, value_type="BOOL")
        petition.refresh_from_db()
        raw = Report.build_pdf_bytes(request_obj=petition)
        self.export_qa("correction-v3-signed.pdf", raw)
        pdf = PdfReader(BytesIO(raw))
        images = [image for page in pdf.pages for image in page.images]
        self.assertTrue(images)
        self.assertTrue(all(image.image.convert("RGB").getpixel((0, 0)) == (0, 0, 0) for image in images))
        with self.assertRaises(ValidationError): evidence[0].save()
        with self.assertRaises(ValidationError): evidence[0].delete()
        with self.assertRaises(ValidationError): petition.signature_evidence.update(signer_name="forged")

    def test_on_behalf_path_never_captures_absent_facultys_signature(self):
        self.faculty_user.is_active = False
        self.faculty_user.save(update_fields=["is_active"])
        petition = Governance.create_correction_request(user=self.faculty_user, initiated_by_user=self.super_admin_user,
            request_source="ADMIN_ON_BEHALF", on_behalf_reason="Existing administrative workflow",
            offering=self.offering, template_period=self.period, justification="Administrative filing",
            items=[dict(student_id=self.student1.pk, grade_activity_id=self.activity.pk, new_value="35")],
            dispatch_notifications=False)
        self.assertEqual(petition.initiated_by_user_id, self.super_admin_user.pk)
        evidence = petition.signature_evidence.get()
        self.assertEqual(evidence.signer_id, self.super_admin_user.pk)
        self.assertNotEqual(evidence.signer_id, self.faculty_user.pk)

    def test_pdf_v3_preserves_values_and_unsigned_legacy_is_not_retroactively_signed(self):
        from pypdf import PdfReader
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        petition.refresh_from_db()
        rows_before = Report.build_report_data(request_obj=petition)["official_grade_rows"]
        raw = Report.build_pdf_bytes(request_obj=petition)
        self.export_qa("correction-v3.pdf", raw)
        text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)
        for heading in ("I. ACADEMIC CONTEXT", "II. OFFICIAL GRADE SUMMARY", "III. JUSTIFICATION AND REMARKS",
                        "IV. APPROVAL SUMMARY AND SIGNATURES", "V. REGISTRAR REFERENCE"):
            self.assertIn(heading, text)
        self.assertNotIn("Applicable Campus", text)
        self.assertIn("Approved At", text)
        self.assertIn("E-Signature", text)
        self.assertEqual(rows_before, Report.build_report_data(request_obj=petition)["official_grade_rows"])
        # A genuine legacy fixture has no signing evidence, even though its user has a credential.
        legacy = GradeCorrectionRequest.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering, template_period=self.period, requested_by_user=self.faculty_user,
            status="CLOSED", justification="Historical unsigned request")
        text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(Report.build_pdf_bytes(request_obj=legacy))).pages)
        self.assertIn("No signature captured at action time", text)
        self.assertFalse(legacy.signature_evidence.exists())

    def test_pdf_usage_actor_is_the_actual_generator_not_the_signer(self):
        from apps.accounts.models import UserSignatureUsageLog
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
            True, tenant_id=self.tenant.pk, value_type="BOOL")
        petition.refresh_from_db()
        Report.build_pdf_bytes(request_obj=petition, generated_by_user=self.super_admin_user)
        usage = UserSignatureUsageLog.objects.filter(document_type="CORRECTION_OFFICIAL_REPORT")
        self.assertEqual(usage.count(), 2)
        self.assertEqual(set(usage.values_list("actor_id", flat=True)), {self.super_admin_user.pk})
        self.assertEqual(set(usage.values_list("user_id", flat=True)), {self.faculty_user.pk, self.reviewer_user.pk})

    def test_pdf_does_not_present_unverified_review_time_as_approval_time(self):
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        petition.refresh_from_db()
        with patch.object(Governance, "_correction_approval_audit_matches", return_value=False):
            with patch.object(Report, "_table", wraps=Report._table) as table:
                Report.build_pdf_bytes(request_obj=petition)
        approval_rows = next(call.args[0] for call in table.call_args_list if len(call.args[0][0]) == 7)
        self.assertEqual(approval_rows[1][5].getPlainText(), "-")

    def test_long_pdf_wraps_and_does_not_truncate_remarks(self):
        from pypdf import PdfReader
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        petition.refresh_from_db()
        petition.justification = "Detailed justification. " * 500 + "END-JUSTIFICATION"
        petition.review_remarks = "Detailed review. " * 100 + "END-REVIEW"
        raw = Report.build_pdf_bytes(request_obj=petition)
        self.export_qa("correction-v3-long.pdf", raw)
        pages = PdfReader(BytesIO(raw)).pages
        text = "\n".join(p.extract_text() for p in pages)
        self.assertGreater(len(pages), 1)
        self.assertIn("END-JUSTIFICATION", text)
        self.assertIn("END-REVIEW", text)
        self.assertIn("AIMS REFERENCE", text)

    def test_multi_page_grade_table_repeats_headers_and_retains_every_row(self):
        from pypdf import PdfReader
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        petition.refresh_from_db()
        report = Report.build_report_data(request_obj=petition)
        report["official_grade_rows"] = [[f"STUDENT-{i:04d}", "Long student name " * 4, "80", "85"] for i in range(100)]
        with patch.object(Report, "build_report_data", return_value=report):
            raw = Report.build_pdf_bytes(request_obj=petition)
        self.export_qa("correction-v3-many-students.pdf", raw)
        pages = PdfReader(BytesIO(raw)).pages
        text = "\n".join(p.extract_text() for p in pages)
        self.assertGreater(len(pages), 2)
        self.assertGreater(text.count("Student No."), 1)
        for i in range(100): self.assertEqual(text.count(f"STUDENT-{i:04d}"), 1)


class AdminOnBehalfFixtures(ActionFixtures):
    def admin_data(self, token=None, value="35"):
        data = self.post_data(token or new_action_token(kind="ON_BEHALF", user=self.reviewer_user,
            offering=self.offering, period=self.period), value)
        data.update(faculty_user=str(self.faculty_user.pk), on_behalf_reason="Original faculty is unavailable.",
            campus=str(self.campus.pk), academic_year=str(self.academic_year.pk), term=str(self.term.pk),
            section=str(self.section.pk), course=str(self.course.pk), template_period=str(self.period.pk))
        return data

    def admin_submit(self, data):
        form = self.form(data)
        return AdminSubmission.submit(token=data["submission_token"], payload=AdminSubmission.payload(data, form.files),
            user=self.reviewer_user, faculty=self.faculty_user, on_behalf_reason=data["on_behalf_reason"],
            offering=self.offering, period=self.period, form=form)

    def direct_create(self, actor=None):
        return Governance.create_correction_request(user=self.faculty_user, initiated_by_user=actor or self.reviewer_user,
            request_source="ADMIN_ON_BEHALF", on_behalf_reason="Faculty is unavailable.",
            offering=self.offering, template_period=self.period, justification="Administrative correction",
            items=[dict(student_id=self.student1.pk, grade_activity_id=self.activity.pk, new_value="35")],
            dispatch_notifications=False)


class AdminOnBehalfSigningTests(AdminOnBehalfFixtures, TestCase):
    def test_faculty_self_multistep_pdf_keeps_petitioner_and_approver_signatures(self):
        from pypdf import PdfReader
        for order, role in ((1, self.reviewer_role), (2, self.cao_role)):
            CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=order,
                approver_role=role, approver_label=role.name)
        petition, _ = self.submit(self.post_data())
        Governance.review_correction_request(request_obj=petition, reviewer=self.reviewer_user, approved=True)
        Governance.review_correction_request(request_obj=petition, reviewer=self.cao_user, approved=True)
        petition.refresh_from_db()
        self.assertEqual(petition.status, "CLOSED")
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY, True,
            tenant_id=self.tenant.pk, value_type="BOOL")
        grades = Report.build_report_data(request_obj=petition)["official_grade_rows"]
        with patch.object(Report, "_table", wraps=Report._table) as rendered:
            raw = Report.build_pdf_bytes(request_obj=petition)
        approval_rows = next(call.args[0] for call in rendered.call_args_list if len(call.args[0][0]) == 7)
        self.assertEqual(len(approval_rows), 3)
        for row in approval_rows[1:]:
            self.assertEqual(row[2].getPlainText(), "APPROVED")
            self.assertNotEqual(row[5].getPlainText(), "-")
            self.assertGreater(row[6].drawWidth, 0)
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(raw)).pages)
        self.assertIn(f"PETITIONER: {self.faculty_user.full_name}", text)
        self.assertIn("Faculty Petitioner", text)
        self.assertNotIn("Filed administratively", text)
        self.assertEqual(petition.signature_evidence.count(), 3)
        self.assertEqual(Report.build_report_data(request_obj=petition)["official_grade_rows"], grades)
        CorrectionActionTests.export_qa("faculty-multistep-v3.pdf", raw)

    def test_actual_admin_signer_scope_timestamp_and_inactive_faculty(self):
        self.faculty_user.is_active = False
        self.faculty_user.save(update_fields=["is_active"])
        UserSignatureService.clear_signature(user=self.faculty_user)
        petition = self.direct_create()
        evidence = petition.signature_evidence.get()
        self.assertEqual((evidence.signer_id, evidence.tenant_id, evidence.campus_id, evidence.signed_at),
            (self.reviewer_user.pk, self.tenant.pk, self.campus.pk, petition.created_at))
        self.assertEqual(evidence.signing_role, "Administrative Petitioner")
        self.assertEqual(petition.requested_by_user_id, self.faculty_user.pk)
        self.assertEqual(petition.initiated_by_user_id, evidence.signer_id)
        self.assertFalse(Governance.can_user_review_correction_request(request_obj=petition, user=self.reviewer_user)[0])

    def test_missing_invalid_and_disabled_admin_signature_fail_before_creation(self):
        credential = UserSignatureCredential.objects.get(user=self.reviewer_user)
        for value in (None, b"invalid-ciphertext"):
            credential.encrypted_blob = value
            credential.save(update_fields=["encrypted_blob"])
            with self.assertRaises(CorrectionSignatureRequired): self.direct_create()
        fixtures.CorrectionWorkflowTests._register_correction_signature(self, self.reviewer_user)
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY, False,
            tenant_id=self.tenant.pk, value_type="BOOL")
        with self.assertRaises(CorrectionSignatureRequired): self.direct_create()
        self.assertFalse(GradeCorrectionRequest.objects.exists())
        self.assertFalse(GradeCorrectionSignatureEvidence.objects.exists())
        self.assertFalse(CorrectionStepNotificationIntent.objects.exists())

    def test_signature_does_not_authorize_filing_and_deny_wins(self):
        with self.assertRaises(PermissionDenied): self.direct_create(actor=self.faculty_user)
        for actor in (self.reviewer_user, self.super_admin_user):
            denial = self.deny(actor, "corrections.create_on_behalf")
            with self.assertRaises(PermissionDenied): self.direct_create(actor=actor)
            denial.delete()
        self.assertFalse(GradeCorrectionRequest.objects.exists())

    def test_target_department_scope_and_original_faculty_assignment_are_required(self):
        from apps.tenants.models import Department
        other = Department.objects.create(tenant=self.tenant, campus=self.campus, code="OTHER", name="Other")
        role = UserRole.objects.get(user=self.reviewer_user, role=self.reviewer_role)
        role.department = other
        role.save(update_fields=["department"])
        with self.assertRaises(PermissionDenied): self.direct_create()
        role.department = self.department
        role.save(update_fields=["department"])
        from apps.academics.models import FacultyAssignment
        FacultyAssignment.objects.filter(offering=self.offering, faculty_user=self.faculty_user).update(is_active=False)
        with self.assertRaises(ValidationError): self.direct_create()

    def test_cross_tenant_campus_and_scoped_superuser_deny_are_enforced(self):
        from copy import copy
        from apps.tenants.models import Tenant, Campus
        other_tenant = Tenant.objects.create(code="OUTSIDE", name="Outside")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other")
        for tenant, campus in ((other_tenant, other_campus),
                               (self.tenant, Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other"))):
            target = copy(self.offering); target.tenant = tenant; target.campus = campus
            with self.assertRaises(PermissionDenied): AdminSubmission.authorize(self.reviewer_user, target)
        self.super_admin_user.is_superuser = True
        self.super_admin_user.save(update_fields=["is_superuser"])
        self.deny(self.super_admin_user, "corrections.create_on_behalf", tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied): self.direct_create(actor=self.super_admin_user)
        self.assertFalse(GradeCorrectionRequest.objects.exists())

    def test_same_token_recovery_payload_binding_and_legitimate_later_petition(self):
        data = self.admin_data()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            first, created = self.admin_submit(data)
        self.assertTrue(created)
        self.assertFalse(callbacks)
        duplicate, created = self.admin_submit(data)
        self.assertFalse(created)
        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)
        self.assertEqual(GradeCorrectionAttachment.objects.count(), 1)
        self.assertEqual(GradeCorrectionSignatureEvidence.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="CREATE_ON_BEHALF", entity_type="GradeCorrectionRequest").count(), 1)
        self.assertEqual(CorrectionStepNotificationIntent.objects.count(), 1)
        for field, value in (("justification", "Different justification"), ("on_behalf_reason", "Other reason"),
                             ("faculty_user", str(self.cao_user.pk))):
            changed = data.copy(); changed[field] = value
            with self.assertRaises(ValidationError): self.admin_submit(changed)
        second, created = self.admin_submit(self.admin_data())
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)

    def test_replay_after_signature_removal_and_lifecycle_closure(self):
        data = self.admin_data()
        petition, _ = self.admin_submit(data)
        UserSignatureService.clear_signature(user=self.reviewer_user)
        petition.status = "CLOSED"; petition.save(update_fields=["status"])
        form = self.form(data)
        recovered = AdminSubmission.recover(token=data["submission_token"], payload=AdminSubmission.payload(data, form.files),
            user=self.reviewer_user, offering=self.offering, period=self.period)
        self.assertEqual(recovered.pk, petition.pk)
        self.assertFalse(self.admin_submit(data)[1])

    def test_token_actor_campus_period_and_faculty_identity_are_bound(self):
        data = self.admin_data()
        from apps.grading.models import GradingTemplatePeriod
        other_period = GradingTemplatePeriod.objects.create(template=self.template, code="OTHER", name="Other", sequence_no=2)
        for changes in (dict(user=self.cao_user), dict(period=other_period)):
            form = self.form(data)
            arguments = dict(token=data["submission_token"], payload=AdminSubmission.payload(data, form.files),
                user=self.reviewer_user, offering=self.offering, period=self.period, form=form,
                faculty=self.faculty_user, on_behalf_reason=data["on_behalf_reason"])
            arguments.update(changes)
            with self.assertRaises((PermissionDenied, ValidationError)): AdminSubmission.submit(**arguments)
        self.assertFalse(GradeCorrectionRequest.objects.exists())

    def test_http_missing_signature_popup_preserves_form_token_and_retry_is_idempotent(self):
        fixtures.CorrectionWorkflowTests._prepare_correction_filing_page(self)
        self.reviewer_user.privacy_consent_version = self.faculty_user.privacy_consent_version
        self.reviewer_user.privacy_consent_at = timezone.now()
        self.reviewer_user.save(update_fields=["privacy_consent_version", "privacy_consent_at"])
        self.client.force_login(self.reviewer_user)
        url = reverse("admin_portal:grade_correction_request_create_on_behalf")
        data = self.admin_data()
        page = self.client.get(url, data)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["can_file"])
        data["submission_token"] = page.context["form"]["submission_token"].value()
        UserSignatureService.clear_signature(user=self.reviewer_user)
        def post():
            form = self.form(data)
            values = {k: data.getlist(k) if k in ("students", "grade_activities") else data[k] for k in data}
            values["attachment"] = form.files["attachment"]
            return self.client.post(url, values)
        response = post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["signature_required"])
        self.assertContains(response, 'id="on-behalf-signature-required"')
        self.assertContains(response, '.showModal()')
        self.assertEqual(response.context["form"]["submission_token"].value(), data["submission_token"])
        self.assertEqual(response.context["form"]["justification"].value(), data["justification"])
        self.assertEqual(response.context["setup_form"]["on_behalf_reason"].value(), data["on_behalf_reason"])
        setup_url = reverse("accounts:admin_signature") + "?correction_context=" + data["submission_token"]
        self.assertEqual(self.client.get(setup_url).status_code, 200)
        fixtures.CorrectionWorkflowTests._register_correction_signature(self, self.reviewer_user)
        first = post(); self.assertEqual(first.status_code, 302)
        UserSignatureService.clear_signature(user=self.reviewer_user)
        duplicate = post(); self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(first.url, duplicate.url)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)
        self.assertEqual(GradeCorrectionSignatureEvidence.objects.count(), 1)
        self.assertEqual(CorrectionStepNotificationIntent.objects.count(), 1)
        CorrectionActionTests.export_qa("on-behalf-form.html", response.content)

    def test_admin_evidence_survives_replacement_removal_and_pdf_attribution(self):
        from PIL import Image
        from pypdf import PdfReader
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.route_rule.step1_role = self.cao_role
        self.route_rule.save(update_fields=["step1_role"])
        petition = self.direct_create()
        Governance.review_correction_request(request_obj=petition, reviewer=self.cao_user, approved=True)
        petition.refresh_from_db()
        self.assertEqual(petition.status, "CLOSED")
        captured = petition.signature_evidence.get(action="SUBMIT")
        original = AESGCM(UserSignatureService._encryption_key()).decrypt(bytes(captured.encryption_nonce), bytes(captured.encrypted_blob), None)
        image = BytesIO(); Image.new("RGBA", (40, 12), (255, 0, 0, 255)).save(image, format="PNG")
        UserSignatureService.store_signature(user=self.reviewer_user, actor=self.reviewer_user,
            uploaded_file=SimpleUploadedFile("replacement.png", image.getvalue(), content_type="image/png"))
        UserSignatureService.clear_signature(user=self.reviewer_user)
        captured.refresh_from_db()
        self.assertEqual(AESGCM(UserSignatureService._encryption_key()).decrypt(bytes(captured.encryption_nonce), bytes(captured.encrypted_blob), None), original)
        SystemSettingService.set(FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY, True,
            tenant_id=self.tenant.pk, value_type="BOOL")
        petition.status = "CLOSED"; petition.save(update_fields=["status"])
        raw = Report.build_pdf_bytes(request_obj=petition)
        reader = PdfReader(BytesIO(raw)); text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn(f"FACULTY CONCERNED: {self.faculty_user.full_name}", text)
        self.assertIn(f"PETITIONER: {self.reviewer_user.full_name}", text)
        self.assertIn("Filed administratively on behalf of the faculty member.", text)
        self.assertNotIn(f"PETITIONER: {self.faculty_user.full_name}", text)
        self.assertIn("Administrative Petitioner", text)
        self.assertIn("Administrative Justification:", text)
        self.assertTrue(list(reader.pages[0].images))
        CorrectionActionTests.export_qa("on-behalf-signed-v3.pdf", raw)

    def test_historical_unsigned_administrative_petition_stays_unsigned(self):
        from pypdf import PdfReader
        petition = GradeCorrectionRequest.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering, template_period=self.period, requested_by_user=self.faculty_user,
            initiated_by_user=self.reviewer_user, request_source="ADMIN_ON_BEHALF", status="CLOSED",
            justification="Historical unsigned administrative filing")
        raw = Report.build_pdf_bytes(request_obj=petition)
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(raw)).pages)
        self.assertIn("No signature captured at action time", text)
        self.assertIn(f"PETITIONER: {self.reviewer_user.full_name}", text)
        self.assertFalse(petition.signature_evidence.exists())
        CorrectionActionTests.export_qa("on-behalf-legacy-v3.pdf", raw)

    def test_audit_failure_rolls_back_request_signature_receipt_and_notification(self):
        data = self.admin_data()
        with patch("apps.grading.correction_actions.AuditService.log_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError): self.admin_submit(data)
        for model in (GradeCorrectionRequest, GradeCorrectionSignatureEvidence, CorrectionActionReceipt,
                      GradeCorrectionAttachment, CorrectionStepNotificationIntent):
            self.assertFalse(model.objects.exists())
        self.assertTrue(self.admin_submit(data)[1])


class CorrectionSubmissionConcurrencyTests(ActionFixtures, TransactionTestCase):
    def test_simultaneous_same_token_creates_one_application(self):
        data = self.post_data()
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                form = self.form(data)
                payload = Submission.payload(data, form.files)
                barrier.wait(timeout=20)
                petition, created = Submission.submit(token=data["submission_token"], payload=payload,
                    user=self.faculty_user, offering=self.offering, period=self.period, form=form)
                return petition.pk, created
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(len({pk for pk, created in results}), 1)
        self.assertEqual(sum(created for pk, created in results), 1)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 1)
        self.assertEqual(GradeCorrectionAttachment.objects.count(), 1)
        self.assertEqual(CorrectionStepNotificationIntent.objects.count(), 1)


class AdminOnBehalfConcurrencyTests(AdminOnBehalfFixtures, TransactionTestCase):
    def test_concurrent_same_token_has_one_signed_petition_audit_and_intent(self):
        data = self.admin_data()
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                form = self.form(data)
                payload = AdminSubmission.payload(data, form.files)
                barrier.wait(timeout=20)
                petition, created = AdminSubmission.submit(token=data["submission_token"], payload=payload,
                    user=self.reviewer_user, faculty=self.faculty_user, on_behalf_reason=data["on_behalf_reason"],
                    offering=self.offering, period=self.period, form=form)
                return petition.pk, created
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(len({pk for pk, created in results}), 1)
        self.assertEqual(sum(created for pk, created in results), 1)
        for model in (GradeCorrectionRequest, GradeCorrectionSignatureEvidence, GradeCorrectionAttachment,
                      CorrectionStepNotificationIntent, CorrectionActionReceipt):
            self.assertEqual(model.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="CREATE_ON_BEHALF", entity_type="GradeCorrectionRequest").count(), 1)
