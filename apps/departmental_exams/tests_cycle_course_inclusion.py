from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    Term,
)
from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    FacultyContribution,
    Question,
)
from .services import (
    CourseExamConfigurationService,
    CycleCourseInclusionService,
    DepartmentalExamAuthorizationService,
    ExaminationCycleConfigurationService,
)


class CycleCourseInclusionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="DE-S3", name="Departmental Exam Stage 3")
        cls.other_tenant = Tenant.objects.create(code="DE-S3-OTHER", name="Other Stage 3 Tenant")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main Campus")
        cls.other_campus = Campus.objects.create(
            tenant=cls.other_tenant, code="OTHER", name="Other Campus"
        )
        cls.department = Department.objects.create(
            tenant=cls.tenant, campus=cls.campus, code="EXAM", name="Exam Department"
        )
        cls.other_department = Department.objects.create(
            tenant=cls.other_tenant,
            campus=cls.other_campus,
            code="OTHER",
            name="Other Department",
        )
        cls.year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="AY-S3",
            name="AY Stage 3",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant,
            academic_year=cls.year,
            code="T-S3",
            name="Term Stage 3",
        )
        cls.program = Program.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            code="P-S3",
            name="Program Stage 3",
        )
        cls.section = Section.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            code="S-S3",
            name="Section Stage 3",
        )
        cls.course = Course.objects.create(
            tenant=cls.tenant,
            code="DE-S3-101",
            title="Stage 3 Course",
            exam_department=cls.department,
        )
        cls.offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            academic_year=cls.year,
            term=cls.term,
            course=cls.course,
            section=cls.section,
        )
        cls.admin = get_user_model().objects.create_superuser(
            "stage3-admin",
            "stage3-admin@example.edu",
            "Pass123!",
            default_tenant=cls.tenant,
            default_campus=cls.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("departmental_exams.manage_cycles", "departmental_exams", "manage_cycles"),
            ("departmental_exams.configure", "departmental_exams", "configure"),
            ("departmental_exams.review_generate", "departmental_exams", "review_generate"),
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action, "is_active": True},
            )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=cls.tenant.id,
            value_type="BOOL",
        )

    def setUp(self):
        self.cycle = ExaminationCycle.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            term=self.term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        self.cycle_course = CycleCourse.objects.create(
            cycle=self.cycle,
            course=self.course,
            responsible_department=self.department,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.cycle_course,
            offering=self.offering,
            campus=self.campus,
        )
        self.configurer = self._scoped_user(
            "stage3-configurer",
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )

    def _scoped_user(self, username, *, tenant, campus, department, permissions):
        user = get_user_model().objects.create_user(
            username,
            f"{username}@example.edu",
            "Pass123!",
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code=username.upper(), name=username)
        for code in permissions:
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
        )
        return user

    def _exempt_url(self):
        return reverse("departmental_exams:cycle_course_exempt", args=[self.cycle_course.id])

    def _restore_url(self):
        return reverse("departmental_exams:cycle_course_restore", args=[self.cycle_course.id])

    def _administration_url(self):
        return reverse(
            "departmental_exams:cycle_course_administration",
            args=[self.cycle_course.id],
        )

    def _token(self):
        self.cycle_course.refresh_from_db()
        return CycleCourseInclusionService.transition_token(self.cycle_course)

    def _exempt(self, *, user=None, token=None, reason="Approved output-based assessment"):
        self.client.force_login(user or self.configurer)
        return self.client.post(
            self._exempt_url(),
            {
                "exemption_category": CycleCourse.ExemptionCategory.PERFORMANCE_BASED,
                "reason": reason,
                "expected_updated_at": token or self._token(),
            },
        )

    def _restore(self, *, user=None, token=None, reason="Restore the written departmental examination"):
        self.client.force_login(user or self.configurer)
        return self.client.post(
            self._restore_url(),
            {
                "reason": reason,
                "expected_updated_at": token or self._token(),
            },
        )

    def _create_contribution(self):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.admin,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        return FacultyContribution.objects.create(
            cycle_course=self.cycle_course,
            faculty_user=self.admin,
            source_assignment=assignment,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=1,
        )

    def test_exempt_and_restore_are_atomic_audited_transitions(self):
        original_department_id = self.cycle_course.responsible_department_id
        original_snapshot_ids = list(
            self.cycle_course.offering_snapshots.values_list("id", flat=True)
        )

        response = self._exempt()

        self.assertRedirects(
            response,
            reverse(
                "departmental_exams:cycle_course_administration",
                args=[self.cycle_course.id],
            ),
        )
        self.cycle_course.refresh_from_db()
        self.assertEqual(
            self.cycle_course.inclusion_status,
            CycleCourse.InclusionStatus.EXEMPT,
        )
        self.assertEqual(
            self.cycle_course.exemption_category,
            CycleCourse.ExemptionCategory.PERFORMANCE_BASED,
        )
        self.assertEqual(self.cycle_course.exemption_changed_by_id, self.configurer.id)
        exempt_audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
            entity_id=str(self.cycle_course.id),
        )
        self.assertEqual(exempt_audit.before_json["inclusion_status"], "INCLUDED")
        self.assertEqual(exempt_audit.after_json["inclusion_status"], "EXEMPT")
        self.assertEqual(exempt_audit.metadata_json["offering_count"], 1)

        response = self._restore()

        self.assertEqual(response.status_code, 302)
        self.cycle_course.refresh_from_db()
        self.assertEqual(
            self.cycle_course.inclusion_status,
            CycleCourse.InclusionStatus.INCLUDED,
        )
        self.assertEqual(self.cycle_course.exemption_category, "")
        self.assertEqual(self.cycle_course.exemption_reason, "")
        self.assertEqual(self.cycle_course.responsible_department_id, original_department_id)
        self.assertEqual(
            list(self.cycle_course.offering_snapshots.values_list("id", flat=True)),
            original_snapshot_ids,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(self.cycle_course.id),
            ).exists()
        )

    def test_include_is_not_a_route_and_reviewer_sees_exempt_row_read_only(self):
        reviewer = self._scoped_user(
            "stage3-reviewer",
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            permissions=("admin_portal.access", "departmental_exams.review_generate"),
        )
        self.cycle_course.reviewer = reviewer
        self.cycle_course.save(update_fields=["reviewer", "updated_at"])
        self._exempt()

        with self.assertRaises(NoReverseMatch):
            reverse("departmental_exams:cycle_course_include", args=[self.cycle_course.id])
        self.client.force_login(reviewer)
        response = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exempt")
        self.assertContains(response, "Read-only")
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=CycleCourse.objects.get(id=self.cycle_course.id),
            )

    def test_same_target_double_submission_is_idempotent(self):
        token = self._token()
        first = self._exempt(token=token)
        second = self._exempt(token=token)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )

    def test_stale_conflicting_confirmation_returns_conflict(self):
        stale_token = self._token()
        self.cycle_course.reviewer = self.admin
        self.cycle_course.save(update_fields=["reviewer", "updated_at"])

        response = self._exempt(token=stale_token)

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "changed after this page was loaded", status_code=409)
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, "INCLUDED")

    def test_transition_rolls_back_when_audit_fails(self):
        with patch(
            "apps.departmental_exams.services.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                CycleCourseInclusionService.exempt(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    exemption_category=CycleCourse.ExemptionCategory.INTERNSHIP,
                    reason="Approved internship assessment workflow",
                    expected_updated_at=self._token(),
                )
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, "INCLUDED")

    def test_configuration_only_is_preserved_through_exempt_and_restore(self):
        configuration = CourseExamConfiguration.objects.create(
            cycle_course=self.cycle_course,
            final_item_count=60,
            final_item_count_source="OVERRIDE",
            general_instructions="Retain this configuration while exempt.",
        )
        expected_configuration = {
            "id": configuration.id,
            "final_item_count": configuration.final_item_count,
            "final_item_count_source": configuration.final_item_count_source,
            "general_instructions": configuration.general_instructions,
            "revision": configuration.revision,
        }

        self.assertEqual(self._exempt().status_code, 302)
        self.cycle_course.refresh_from_db()
        configuration.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.EXEMPT)
        self.assertEqual(
            {
                "id": configuration.id,
                "final_item_count": configuration.final_item_count,
                "final_item_count_source": configuration.final_item_count_source,
                "general_instructions": configuration.general_instructions,
                "revision": configuration.revision,
            },
            expected_configuration,
        )
        self.assertEqual(
            CourseExamConfiguration.objects.get(cycle_course=self.cycle_course).pk,
            expected_configuration["id"],
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )

        self.assertEqual(self._restore().status_code, 302)
        self.cycle_course.refresh_from_db()
        configuration.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        self.assertEqual(
            {
                "id": configuration.id,
                "final_item_count": configuration.final_item_count,
                "final_item_count_source": configuration.final_item_count_source,
                "general_instructions": configuration.general_instructions,
                "revision": configuration.revision,
            },
            expected_configuration,
        )
        self.assertEqual(
            CourseExamConfiguration.objects.get(cycle_course=self.cycle_course).pk,
            expected_configuration["id"],
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )

    def test_stage4_exempt_closes_activity_free_open_configuration_without_manual_close_audit(self):
        self.cycle.default_questions_required_per_faculty = 50
        self.cycle.default_final_item_count = 50
        self.cycle.save(update_fields=["default_questions_required_per_faculty", "default_final_item_count"])
        configuration, _ = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=self.cycle_course.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=0,
            final_item_count=50,
            final_item_count_mode="DEFAULT",
            questions_required_per_faculty=50,
            questions_required_per_faculty_mode="DEFAULT",
            coverage="Required learning outcomes",
            additional_instructions="",
            contribution_deadline=timezone.now() + timezone.timedelta(days=7),
        )
        configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.OPEN
        configuration.opened_at = timezone.now()
        configuration.opened_by = self.configurer
        configuration.save(
            update_fields=["workflow_status", "opened_at", "opened_by", "updated_at"]
        )
        self.assertEqual(self._exempt().status_code, 302)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)
        exempt_audit = AuditLog.objects.get(action="DE_EXAM_CYCLE_COURSE_EXEMPTED", entity_id=str(self.cycle_course.id))
        self.assertEqual(exempt_audit.after_json["configuration"]["workflow_status"], "CLOSED")
        self.assertFalse(AuditLog.objects.filter(action="DE_EXAM_COURSE_CONTRIBUTION_CLOSED").exists())
        self.assertEqual(self._restore().status_code, 302)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, CourseExamConfiguration.WorkflowStatus.CLOSED)

    def test_substantive_contribution_and_question_data_block_exemption(self):
        contribution = self._create_contribution()
        response = self._exempt()
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "downstream faculty contribution data", status_code=400)
        self.assertTrue(FacultyContribution.objects.filter(id=contribution.id).exists())

        question = Question.objects.create(
            contribution=contribution,
            question_text="Which response remains protected?",
            choice_a="The current response",
            choice_b="A stale response",
            choice_c="No response",
            choice_d="Any response",
            correct_answer="A",
            difficulty=Question.Difficulty.EASY,
            position=1,
        )
        response = self._exempt()
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Question.objects.filter(id=question.id).exists())
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)

        self.cycle.status = ExaminationCycle.Status.OPEN
        self.cycle.save(update_fields=["status", "updated_at"])
        response = self._exempt()
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Only Draft examination cycles", status_code=400)

    def test_manage_and_review_permissions_do_not_authorize_transition(self):
        manager = self._scoped_user(
            "stage3-manager",
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            permissions=("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        reviewer = self._scoped_user(
            "stage3-review-only",
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            permissions=("admin_portal.access", "departmental_exams.review_generate"),
        )
        for user in (manager, reviewer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self._exempt_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self._exempt_url(),
                        {
                            "exemption_category": "INTERNSHIP",
                            "reason": "Approved internship assessment workflow",
                            "expected_updated_at": self._token(),
                        },
                    ).status_code,
                    403,
                )

    def test_exact_direct_deny_feature_flag_and_wrong_tenant_fail_closed(self):
        UserPermission.objects.create(
            user=self.configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(self._exempt_url()).status_code, 403)

        UserPermission.objects.filter(user=self.configurer).delete()
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self._exempt().status_code, 403)

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        other_user = self._scoped_user(
            "stage3-other-tenant",
            tenant=self.other_tenant,
            campus=self.other_campus,
            department=self.other_department,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.other_tenant.id,
            value_type="BOOL",
        )
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(self._exempt_url()).status_code, 404)

    def test_null_responsibility_is_superuser_only_and_inactive_department_blocks(self):
        self.cycle_course.responsible_department = None
        self.cycle_course.save(update_fields=["responsible_department", "updated_at"])
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.get(self._exempt_url()).status_code, 403)

        response = self._exempt(user=self.admin)
        self.assertEqual(response.status_code, 302)

        self._restore(user=self.admin)
        self.cycle_course.responsible_department = self.department
        self.cycle_course.save(update_fields=["responsible_department", "updated_at"])
        self.department.is_active = False
        self.department.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._exempt_url()).status_code, 403)
        self.assertEqual(
            self.client.post(
                self._exempt_url(),
                {
                    "exemption_category": CycleCourse.ExemptionCategory.INTERNSHIP,
                    "reason": "Approved internship assessment workflow",
                    "expected_updated_at": self._token(),
                },
            ).status_code,
            403,
        )

    def test_inactive_department_denies_restore_get_and_post_for_superuser(self):
        self.assertEqual(self._exempt(user=self.admin).status_code, 302)
        self.department.is_active = False
        self.department.save(update_fields=["is_active", "updated_at"])

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._restore_url()).status_code, 403)
        self.assertEqual(
            self.client.post(
                self._restore_url(),
                {
                    "reason": "Restore after department reactivation.",
                    "expected_updated_at": self._token(),
                },
            ).status_code,
            403,
        )
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.EXEMPT)
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            0,
        )

    def test_administration_hides_transition_actions_for_inactive_department(self):
        self.department.is_active = False
        self.department.save(update_fields=["is_active", "updated_at"])
        audit_count = AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count()

        self.client.force_login(self.admin)
        response = self.client.get(self._administration_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self._exempt_url())
        self.assertNotContains(response, self._restore_url())
        self.assertContains(
            response,
            "Inclusion status cannot be changed while the responsible exam department is inactive.",
        )
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count(),
            audit_count,
        )

    def test_administration_hides_restore_for_inactive_exempt_department(self):
        self.assertEqual(self._exempt(user=self.admin).status_code, 302)
        self.department.is_active = False
        self.department.save(update_fields=["is_active", "updated_at"])
        audit_count = AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count()

        self.client.force_login(self.admin)
        response = self.client.get(self._administration_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self._exempt_url())
        self.assertNotContains(response, self._restore_url())
        self.assertContains(
            response,
            "Inclusion status cannot be changed while the responsible exam department is inactive.",
        )
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.EXEMPT)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count(),
            audit_count,
        )

    def test_administration_renders_correct_actions_for_active_and_null_responsibility(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._administration_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._exempt_url())

        self.assertEqual(self._exempt(user=self.admin).status_code, 302)
        response = self.client.get(self._administration_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._restore_url())

        self.assertEqual(self._restore(user=self.admin).status_code, 302)
        self.cycle_course.responsible_department = None
        self.cycle_course.save(update_fields=["responsible_department", "updated_at"])
        audit_count = AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count()
        response = self.client.get(self._administration_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._exempt_url())
        self.assertNotContains(
            response,
            "Inclusion status cannot be changed while the responsible exam department is inactive.",
        )
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        self.assertIsNone(self.cycle_course.responsible_department_id)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count(),
            audit_count,
        )

    def test_wrong_state_confirmation_gets_redirect_without_mutation_or_audit(self):
        self.client.force_login(self.configurer)
        audit_count = AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count()
        response = self.client.get(self._restore_url())
        self.assertRedirects(response, self._administration_url())
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count(),
            audit_count,
        )

        self.assertEqual(self._exempt().status_code, 302)
        audit_count = AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count()
        response = self.client.get(self._exempt_url())
        self.assertRedirects(response, self._administration_url())
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.EXEMPT)
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.cycle_course.id)).count(),
            audit_count,
        )

    def test_lock_included_cycle_course_requires_atomic_scope_and_included_state(self):
        with patch(
            "apps.departmental_exams.services.transaction.get_connection"
        ) as get_connection:
            get_connection.return_value.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                CycleCourseInclusionService.lock_included_cycle_course(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.tenant.id,
                )
        with transaction.atomic():
            locked = CycleCourseInclusionService.lock_included_cycle_course(
                cycle_course_id=self.cycle_course.id,
                tenant_id=self.tenant.id,
            )
        self.assertEqual(locked.id, self.cycle_course.id)
        with transaction.atomic():
            with self.assertRaises(CycleCourse.DoesNotExist):
                CycleCourseInclusionService.lock_included_cycle_course(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.other_tenant.id,
                )

        self.assertEqual(self._exempt().status_code, 302)
        with transaction.atomic():
            with self.assertRaises(PermissionDenied):
                CycleCourseInclusionService.lock_included_cycle_course(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.tenant.id,
                )

    def test_locked_administration_post_preserves_forced_exempt_transition(self):
        alternate_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="EXAM-ALT",
            name="Alternate Exam Department",
        )
        configurer_membership = UserRole.objects.get(user=self.configurer)
        UserRole.objects.create(
            user=self.configurer,
            role=configurer_membership.role,
            tenant=self.tenant,
            campus=self.campus,
            department=alternate_department,
        )
        reviewer = self._scoped_user(
            "stage3-stale-save-reviewer",
            tenant=self.tenant,
            campus=self.campus,
            department=alternate_department,
            permissions=("admin_portal.access", "departmental_exams.review_generate"),
        )
        original_require = (
            DepartmentalExamAuthorizationService.require_configure_cycle_course
        )
        token = self._token()
        triggered = False

        def force_exempt(**kwargs):
            nonlocal triggered
            if not triggered:
                triggered = True
                CycleCourseInclusionService.exempt(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    exemption_category=CycleCourse.ExemptionCategory.INTERNSHIP,
                    reason="Approved internship assessment workflow",
                    expected_updated_at=token,
                )
            return original_require(**kwargs)

        self.client.force_login(self.configurer)
        with patch(
            "apps.departmental_exams.views.DepartmentalExamAuthorizationService.require_configure_cycle_course",
            side_effect=force_exempt,
        ):
            response = self.client.post(
                self._administration_url(),
                {
                    "responsible_department": alternate_department.id,
                    "reviewer_id": reviewer.id,
                },
            )

        self.assertEqual(response.status_code, 302)
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.EXEMPT)
        self.assertEqual(
            self.cycle_course.exemption_category,
            CycleCourse.ExemptionCategory.INTERNSHIP,
        )
        self.assertEqual(
            self.cycle_course.exemption_reason,
            "Approved internship assessment workflow",
        )
        self.assertEqual(self.cycle_course.exemption_changed_by_id, self.configurer.id)
        self.assertIsNotNone(self.cycle_course.exemption_changed_at)
        self.assertEqual(
            self.cycle_course.responsible_department_id, alternate_department.id
        )
        self.assertEqual(self.cycle_course.reviewer_id, reviewer.id)
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            0,
        )

    def test_locked_administration_post_preserves_forced_restore_transition(self):
        self.assertEqual(self._exempt().status_code, 302)
        original_require = (
            DepartmentalExamAuthorizationService.require_configure_cycle_course
        )
        token = self._token()
        triggered = False

        def force_restore(**kwargs):
            nonlocal triggered
            if not triggered:
                triggered = True
                CycleCourseInclusionService.restore(
                    cycle_course_id=self.cycle_course.id,
                    tenant_id=self.tenant.id,
                    user=self.configurer,
                    reason="Restore the written departmental examination",
                    expected_updated_at=token,
                )
            return original_require(**kwargs)

        self.client.force_login(self.configurer)
        with patch(
            "apps.departmental_exams.views.DepartmentalExamAuthorizationService.require_configure_cycle_course",
            side_effect=force_restore,
        ):
            response = self.client.post(
                self._administration_url(),
                {
                    "responsible_department": self.department.id,
                    "reviewer_id": "",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.cycle_course.refresh_from_db()
        self.assertEqual(self.cycle_course.inclusion_status, CycleCourse.InclusionStatus.INCLUDED)
        self.assertEqual(self.cycle_course.exemption_category, "")
        self.assertEqual(self.cycle_course.exemption_reason, "")
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_RESTORED",
                entity_id=str(self.cycle_course.id),
            ).count(),
            1,
        )

    def test_restore_revalidates_and_clears_ineligible_reviewer(self):
        reviewer = self._scoped_user(
            "stage3-restore-reviewer",
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            permissions=("admin_portal.access", "departmental_exams.review_generate"),
        )
        self.cycle_course.reviewer = reviewer
        self.cycle_course.save(update_fields=["reviewer", "updated_at"])
        self._exempt()
        UserRole.objects.filter(user=reviewer).update(is_active=False)

        response = self._restore()

        self.assertEqual(response.status_code, 302)
        self.cycle_course.refresh_from_db()
        self.assertIsNone(self.cycle_course.reviewer_id)
        audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_COURSE_RESTORED",
            entity_id=str(self.cycle_course.id),
        )
        self.assertTrue(audit.metadata_json["reviewer_revalidated"])
        self.assertTrue(audit.metadata_json["reviewer_cleared"])

    def test_model_validation_enforces_coherent_active_state(self):
        self.cycle_course.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        self.cycle_course.exemption_category = CycleCourse.ExemptionCategory.CAPSTONE
        self.cycle_course.exemption_reason = "short"
        self.cycle_course.exemption_changed_by = self.configurer
        self.cycle_course.exemption_changed_at = timezone.now()
        with self.assertRaises(ValidationError):
            self.cycle_course.full_clean()

        self.cycle_course.inclusion_status = CycleCourse.InclusionStatus.INCLUDED
        self.cycle_course.exemption_reason = "Stale exemption reason"
        with self.assertRaises(ValidationError):
            self.cycle_course.full_clean()
