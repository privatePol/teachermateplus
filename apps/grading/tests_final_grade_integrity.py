"""Official grades survive display requests; lifecycle writes remain explicit."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.faculty_portal import tests_assignment_acceptance as fixtures
from apps.grading.models import (
    GradeSubmission, GradingPeriodLock, GradingTemplate, GradingTemplateComponent,
    StudentFinalGrade, StudentPeriodGrade, TenantGradingProfile,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.grading import tests as grading_fixtures
from apps.rbac.models import Permission, RolePermission, UserPermission
from apps.student_portal.services import StudentPortalService


class FinalGradeIntegrityTests(TestCase):
    # Reuse the accepted-assignment fixture without inheriting its test methods.
    setUp = fixtures.FacultyAssignmentAcceptanceTests.setUp
    _accept_assignment = fixtures.FacultyAssignmentAcceptanceTests._accept_assignment
    _create_active_student = fixtures.FacultyAssignmentAcceptanceTests._create_active_student

    def prepare_grades(self, *, submitted=True):
        self._accept_assignment()
        self.student = self._create_active_student()
        for period, value in zip(
            [self.prelim, self.midterm, self.prefinal, self.final], [80, 82, 84, 88]
        ):
            StudentPeriodGrade.objects.create(
                tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=period, student=self.student,
                period_grade=value, is_finalized=submitted,
            )
        self.final_row = StudentFinalGrade.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.offering,
            student=self.student, final_grade=70, is_submitted=submitted,
        )
        if submitted:
            GradeSubmission.objects.create(
                tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=self.final, status=GradeSubmission.Status.SUBMITTED,
                submitted_by_user=self.faculty_user, submitted_at=timezone.now(),
            )
        self.client.force_login(self.faculty_user)
        self.url = reverse("faculty_portal:period_summary", args=[self.offering.id, self.final.id])

    @staticmethod
    def protected_records():
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (
                StudentFinalGrade, StudentPeriodGrade, GradeSubmission,
                GradingPeriodLock, GradingTemplate, GradingTemplateComponent,
                TenantGradingProfile, Permission, RolePermission, UserPermission,
            )
        }

    def test_repeated_submitted_get_preserves_official_records_and_audits(self):
        self.prepare_grades()
        before = self.protected_records()
        audits = list(AuditLog.objects.order_by("pk").values())
        for _ in range(2):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["rows"][0]["final_grade"], "70")
            self.assertEqual(self.protected_records(), before)
        self.assertEqual(list(AuditLog.objects.order_by("pk").values()), audits)

    def test_submitted_get_during_correction_does_not_refresh_average_activity_grades(self):
        self.prepare_grades()
        from apps.faculty_portal import views
        original = views._period_edit_state

        def correction_state(*args):
            state = original(*args)
            state["is_editable"] = True
            state["is_correction_active"] = True
            return state

        before = self.protected_records()
        with patch.object(views, "_period_edit_state", side_effect=correction_state), patch.object(
            views, "_period_uses_average_activity_details", return_value=True
        ), patch.object(FacultyGradingService, "recompute_period_summary_for_students") as recompute:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        recompute.assert_not_called()
        self.assertEqual(self.protected_records(), before)

    def test_student_release_remains_visible_after_faculty_get(self):
        self.prepare_grades()
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL", is_active=True,
        )
        link = SimpleNamespace(
            tenant=self.tenant, campus=self.campus, student=self.student,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
        )
        self.assertEqual(StudentPortalService.visible_grade_rows(link)[0]["final_grade"].final_grade, 70)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(StudentPortalService.visible_grade_rows(link)[0]["final_grade"].final_grade, 70)

    def test_omitted_service_transition_preserves_submitted_number_and_state(self):
        self.prepare_grades()
        before = self.protected_records()
        self.assertEqual(FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user, offering=self.offering, template=self.template,
        ), [])
        self.assertEqual(self.protected_records(), before)

    def test_explicit_service_transition_still_updates_draft_and_submitted_results(self):
        self.prepare_grades()
        for submitted in (False, True):
            FacultyGradingService.recompute_final_grades_for_students(
                user=self.faculty_user, offering=self.offering, student_ids=[self.student.id],
                is_submitted=submitted,
            )
            self.final_row.refresh_from_db()
            self.assertEqual(self.final_row.final_grade, 84)
            self.assertEqual(self.final_row.is_submitted, submitted)

    def test_draft_final_preview_does_not_create_or_overwrite_final_rows(self):
        self.prepare_grades(submitted=False)
        before = self.protected_records()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.protected_records(), before)
        self.final_row.delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertFalse(StudentFinalGrade.objects.filter(offering=self.offering).exists())

    def test_reopening_explicitly_clears_lifecycle_flags(self):
        self.prepare_grades()
        GradingGovernanceService.reopen_period(
            user=self.faculty_user, offering=self.offering, template_period=self.final,
            remarks="Authorized review",
        )
        self.final_row.refresh_from_db()
        self.assertFalse(self.final_row.is_submitted)
        self.assertEqual(self.final_row.final_grade, 70)
        self.assertFalse(StudentPeriodGrade.objects.get(
            offering=self.offering, template_period=self.final, student=self.student,
        ).is_finalized)

    def test_draft_period_refresh_preserves_a_previously_submitted_final(self):
        self.prepare_grades(submitted=False)
        self.final_row.is_submitted = True
        self.final_row.save()
        StudentPeriodGrade.objects.filter(
            offering=self.offering, template_period=self.final,
        ).delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.final_row.refresh_from_db()
        self.assertTrue(self.final_row.is_submitted)
        self.assertEqual(self.final_row.final_grade, 70)


class FinalGradeResubmissionTests(TestCase):
    setUp = grading_fixtures.CorrectionWorkflowTests.setUp

    def test_reopen_save_and_resubmit_restores_finalized_and_submitted_state(self):
        self.correction_lock.delete()
        GradingGovernanceService.reopen_period(
            user=self.faculty_user, offering=self.offering, template_period=self.period,
            remarks="Authorized score review",
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user, activity=self.activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("40")}
                           for student in (self.student1, self.student2)],
        )
        for grade in StudentFinalGrade.objects.filter(offering=self.offering):
            self.assertEqual(grade.final_grade, 90)
            self.assertFalse(grade.is_submitted)
        GradingGovernanceService.submit_period(
            user=self.faculty_user, offering=self.offering, template_period=self.period,
        )
        self.assertTrue(all(StudentFinalGrade.objects.filter(
            offering=self.offering).values_list("is_submitted", flat=True)))
        self.assertTrue(all(StudentPeriodGrade.objects.filter(
            offering=self.offering).values_list("is_finalized", flat=True)))
        self.assertEqual(GradeSubmission.objects.get(
            offering=self.offering, template_period=self.period,
        ).status, GradeSubmission.Status.SUBMITTED)
