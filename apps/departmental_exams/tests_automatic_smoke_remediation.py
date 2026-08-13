from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission
from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    Section,
    Term,
)
from apps.tenants.models import Campus, Department, Program, Tenant

from .contribution_services import ContributionRosterService
from .generation_readiness import Stage6ReadinessService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    ExamGenerationRevision,
    FacultyContribution,
)
from .services import (
    CourseExamConfigurationReadinessService,
    CycleCourseInclusionService,
    DepartmentalExamAuthorizationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class AutomaticSmokeRemediationTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.generation_manager = self.make_user(
            "automatic-smoke-manager",
            None,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
            campus=self.campus,
        )

    def make_automatic_cycle(self, *, period=ExaminationCycle.ExamPeriod.MIDTERM):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Prepare a complete questionnaire pool.",
            scope_suffix=f"auto-{period.lower()}-{ExaminationCycle.objects.count()}",
        )
        cycle.exam_period = period
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["exam_period", "processing_mode", "updated_at"])
        return cycle

    def make_automatic_course(self, *, cycle=None, code="AUTO", configuration=True):
        cycle = cycle or self.make_automatic_cycle()
        parent = self.make_course(cycle=cycle, department=None, code=code)
        if configuration:
            self.make_configuration(
                parent,
                workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
                opened_at=timezone.now(),
                deadline=cycle.default_contribution_deadline,
                deadline_source=CourseExamConfiguration.ValueSource.DEFAULT,
            )
        return parent

    def make_other_tenant_automatic_course(self, *, code="AUTO-OTHER"):
        campus = Campus.objects.create(
            tenant=self.other_tenant,
            code=f"{code}-CAMPUS",
            name="Other Tenant Campus",
        )
        department = Department.objects.create(
            tenant=self.other_tenant,
            campus=campus,
            code=f"{code}-DEPT",
            name="Other Tenant Department",
        )
        year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            code=f"{code}-AY",
            name="Other Tenant Academic Year",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        term = Term.objects.create(
            tenant=self.other_tenant,
            academic_year=year,
            code=f"{code}-TERM",
            name="Other Tenant Term",
        )
        course = Course.objects.create(
            tenant=self.other_tenant,
            code=code,
            title="Other Tenant Automatic Course",
        )
        program = Program.objects.create(
            tenant=self.other_tenant,
            campus=campus,
            department=department,
            code=f"{code}-PROGRAM",
            name="Other Tenant Program",
        )
        section = Section.objects.create(
            tenant=self.other_tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"{code}-SECTION",
            name="Other Tenant Section",
        )
        offering = CourseOffering.objects.create(
            tenant=self.other_tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=year,
            term=term,
            course=course,
            section=section,
        )
        cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=year,
            term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            status=ExaminationCycle.Status.DRAFT,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=self.admin,
        )
        parent = CycleCourse.objects.create(cycle=cycle, course=course)
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=offering,
            campus=campus,
        )
        return parent

    def add_same_course_to_cycle(self, *, source_parent, cycle, slug):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=f"AUTO-{slug}",
            name=f"Automatic {slug}",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=program,
            code=f"AUTO-{slug}",
            name=f"Automatic {slug}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=program,
            academic_year=cycle.academic_year,
            term=cycle.term,
            course=source_parent.course,
            section=section,
        )
        parent = CycleCourse.objects.create(
            cycle=cycle,
            course=source_parent.course,
            responsible_department=None,
            reviewer=None,
        )
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=offering,
            campus=self.campus,
        )
        self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            deadline=cycle.default_contribution_deadline,
            deadline_source=CourseExamConfiguration.ValueSource.DEFAULT,
        )
        return parent, offering

    def test_automatic_null_department_defaults_get_post_and_readiness(self):
        cycle = self.make_cycle(scope_suffix="auto-default-propagation")
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parent = self.make_course(cycle=cycle, department=None, code="AUTO-DEFAULT")
        deadline = self.future_deadline()
        initial_stage6 = Stage6ReadinessService.evaluate(cycle_course=parent)
        initial_codes = {blocker["code"] for blocker in initial_stage6["blockers"]}
        self.assertIn("CYCLE_NOT_OPEN", initial_codes)
        self.assertIn("CONFIGURATION_MISSING", initial_codes)
        self.assertIn("BLUEPRINT_MISSING", initial_codes)

        cycle, changed = ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=deadline,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            contributor_instructions="Automatic contributor guidance.",
        )
        self.assertTrue(changed)
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        self.assertEqual(configuration.questions_required_per_faculty_source, "DEFAULT")
        self.assertEqual(configuration.final_item_count_source, "DEFAULT")
        self.assertEqual(configuration.contribution_deadline_source, "DEFAULT")
        propagated_codes = {
            blocker["code"]
            for blocker in Stage6ReadinessService.evaluate(cycle_course=parent)[
                "blockers"
            ]
        }
        self.assertIn("CYCLE_NOT_OPEN", propagated_codes)
        self.assertNotIn("CONFIGURATION_MISSING", propagated_codes)

        cycle.status = ExaminationCycle.Status.OPEN
        cycle.save(update_fields=["status", "updated_at"])
        parent.refresh_from_db()
        self.client.force_login(self.generation_manager)
        url = reverse("departmental_exams:course_configuration", args=[parent.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save Draft Configuration")
        self.assertContains(response, "No Exam Department assigned")
        self.assertNotContains(response, "Needs Exam Department")

        response = self.client.post(
            url,
            {
                "expected_revision": configuration.revision,
                "questions_required_per_faculty_mode": "DEFAULT",
                "questions_required_per_faculty": "50",
                "final_item_count_mode": "DEFAULT",
                "final_item_count": "50",
                "contribution_deadline_mode": "DEFAULT",
                "contribution_deadline": timezone.localtime(deadline).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "coverage": "Automatic course outcomes",
                "additional_instructions": "No reviewer assignment required.",
            },
        )
        self.assertRedirects(response, url)
        configuration.refresh_from_db()
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent,
            configuration=configuration,
            user=self.generation_manager,
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        stage6 = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertNotIn(
            "CONFIGURATION_MISSING",
            {blocker["code"] for blocker in stage6["blockers"]},
        )
        self.assertIn(
            "BLUEPRINT_MISSING",
            {blocker["code"] for blocker in stage6["blockers"]},
        )

        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[parent.id]
        )
        response = self.client.post(administration_url, {"responsible_department": ""})
        self.assertRedirects(response, administration_url)
        parent.refresh_from_db()
        self.assertIsNone(parent.responsible_department_id)
        self.assertIsNone(parent.reviewer_id)

    def test_automatic_exempt_restore_keeps_administration_reachable_and_downstream_closed(self):
        cycle = self.make_automatic_cycle()
        cycle.status = ExaminationCycle.Status.DRAFT
        cycle.save(update_fields=["status", "updated_at"])
        parent = self.make_automatic_course(cycle=cycle, code="AUTO-INCLUSION")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[parent.id]
        )
        exempt_url = reverse(
            "departmental_exams:cycle_course_exempt", args=[parent.id]
        )
        restore_url = reverse(
            "departmental_exams:cycle_course_restore", args=[parent.id]
        )
        configuration_url = reverse(
            "departmental_exams:course_configuration", args=[parent.id]
        )
        self.client.force_login(self.generation_manager)

        response = self.client.get(administration_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, exempt_url)

        response = self.client.post(
            exempt_url,
            {
                "exemption_category": CycleCourse.ExemptionCategory.INTERNSHIP,
                "reason": "Approved internship assessment workflow",
                "expected_updated_at": CycleCourseInclusionService.transition_token(
                    parent
                ),
            },
            follow=True,
        )
        self.assertRedirects(response, administration_url)
        self.assertEqual(response.status_code, 200)
        parent.refresh_from_db()
        self.assertEqual(
            parent.inclusion_status,
            CycleCourse.InclusionStatus.EXEMPT,
        )
        exempt_audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
            entity_id=str(parent.id),
        )
        self.assertEqual(exempt_audit.actor_user_id, self.generation_manager.id)
        self.assertEqual(exempt_audit.after_json["inclusion_status"], "EXEMPT")
        self.assertContains(response, "Examination status:")
        self.assertContains(response, "Exempt")
        self.assertContains(response, restore_url)
        self.assertContains(response, "inclusion-management-only")
        self.assertNotContains(response, "Save Responsibility")
        for downstream_url in (
            configuration_url,
            reverse("departmental_exams:blueprint_configuration", args=[parent.id]),
            reverse("departmental_exams:blueprint_review", args=[parent.id]),
            reverse("departmental_exams:generation_workspace", args=[parent.id]),
        ):
            with self.subTest(downstream_url=downstream_url):
                self.assertNotContains(response, downstream_url)

        response = self.client.get(restore_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restore this course examination?")

        assigned_response = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertEqual(assigned_response.status_code, 200)
        self.assertContains(assigned_response, parent.course.code)
        self.assertContains(assigned_response, administration_url)
        self.assertNotContains(assigned_response, configuration_url)
        listed = {course.id: course for course in assigned_response.context["courses"]}
        self.assertTrue(listed[parent.id].can_administer)
        self.assertFalse(listed[parent.id].can_configure)
        self.assertFalse(listed[parent.id].can_manage_generation)
        self.assertFalse(listed[parent.id].can_view_generation)

        cycle_list_response = self.client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        )
        self.assertEqual(cycle_list_response.status_code, 200)
        self.assertContains(cycle_list_response, administration_url)
        self.assertNotContains(cycle_list_response, configuration_url)

        self.assertEqual(self.client.get(configuration_url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_generation_management(
                user=self.generation_manager,
                cycle_course=parent,
            )

        response = self.client.post(
            restore_url,
            {
                "reason": "Restore the approved questionnaire workflow",
                "expected_updated_at": CycleCourseInclusionService.transition_token(
                    parent
                ),
            },
            follow=True,
        )
        self.assertRedirects(response, administration_url)
        self.assertEqual(response.status_code, 200)
        parent.refresh_from_db()
        self.assertEqual(
            parent.inclusion_status,
            CycleCourse.InclusionStatus.INCLUDED,
        )
        restore_audit = AuditLog.objects.get(
            action="DE_EXAM_CYCLE_COURSE_RESTORED",
            entity_id=str(parent.id),
        )
        self.assertEqual(restore_audit.actor_user_id, self.generation_manager.id)
        self.assertEqual(restore_audit.after_json["inclusion_status"], "INCLUDED")
        DepartmentalExamAuthorizationService.require_generation_management(
            user=self.generation_manager,
            cycle_course=parent,
        )
        self.assertEqual(self.client.get(configuration_url).status_code, 200)
        self.assertContains(response, "Save Responsibility")
        self.assertNotContains(response, "inclusion-management-only")

        assigned_response = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        restored = {
            course.id: course for course in assigned_response.context["courses"]
        }[parent.id]
        self.assertTrue(restored.can_administer)
        self.assertTrue(restored.can_configure)
        self.assertTrue(restored.can_manage_generation)
        self.assertContains(assigned_response, configuration_url)

    def test_automatic_lists_never_use_manual_configurer_or_reviewer_scope(self):
        cycle = self.make_automatic_cycle()
        cycle.status = ExaminationCycle.Status.DRAFT
        cycle.save(update_fields=["status", "updated_at"])
        parent = self.make_automatic_course(cycle=cycle, code="AUTO-LIST-PARTITION")
        parent.responsible_department = self.department
        parent.save(update_fields=["responsible_department", "updated_at"])
        CycleCourseInclusionService.exempt(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.generation_manager,
            exemption_category=CycleCourse.ExemptionCategory.INTERNSHIP,
            reason="Approved internship assessment workflow",
            expected_updated_at=CycleCourseInclusionService.transition_token(parent),
        )
        parent.refresh_from_db()

        combined_manual = self.make_user(
            "automatic-manual-combined",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            ),
        )
        direct_denied = self.make_user(
            "automatic-manage-direct-denied",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
                "departmental_exams.manage_exam_generation",
            ),
        )
        UserPermission.objects.create(
            user=direct_denied,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )

        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        cycle_list_url = reverse(
            "departmental_exams:cycle_course_list", args=[cycle.id]
        )
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[parent.id]
        )
        cases = (
            ("configure-only", self.configurer, None),
            ("reviewer-only", self.reviewer, self.reviewer),
            ("combined-manual", combined_manual, combined_manual),
            ("manage-direct-deny", direct_denied, direct_denied),
        )
        for boundary, user, reviewer in cases:
            with self.subTest(boundary=boundary):
                parent.reviewer = reviewer
                parent.save(update_fields=["reviewer", "updated_at"])
                self.client.force_login(user)

                assigned_response = self.client.get(assigned_url)
                self.assertEqual(assigned_response.status_code, 200)
                self.assertNotContains(assigned_response, parent.course.code)
                self.assertNotIn(
                    parent.id,
                    {course.id for course in assigned_response.context["courses"]},
                )
                self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
                self.assertEqual(self.client.get(administration_url).status_code, 403)

        CycleCourseInclusionService.restore(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.generation_manager,
            reason="Restore the included Automatic workflow boundary",
            expected_updated_at=CycleCourseInclusionService.transition_token(parent),
        )
        configuration_url = reverse(
            "departmental_exams:course_configuration", args=[parent.id]
        )
        for boundary, user in (
            ("included-combined-manual", combined_manual),
            ("included-manage-direct-deny", direct_denied),
        ):
            with self.subTest(boundary=boundary):
                parent.reviewer = user
                parent.save(update_fields=["reviewer", "updated_at"])
                self.client.force_login(user)
                assigned_response = self.client.get(assigned_url)
                self.assertEqual(assigned_response.status_code, 200)
                self.assertNotIn(
                    parent.id,
                    {course.id for course in assigned_response.context["courses"]},
                )
                self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
                self.assertEqual(self.client.get(administration_url).status_code, 403)
                self.assertEqual(self.client.get(configuration_url).status_code, 403)

    def test_automatic_inclusion_management_requires_permission_and_honors_direct_deny(self):
        parent = self.make_automatic_course(code="AUTO-RBAC")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[parent.id]
        )
        exempt_url = reverse(
            "departmental_exams:cycle_course_exempt", args=[parent.id]
        )
        restore_url = reverse(
            "departmental_exams:cycle_course_restore", args=[parent.id]
        )

        self.client.force_login(self.configurer)
        for url in (administration_url, exempt_url, restore_url):
            with self.subTest(boundary="missing-manage", url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        UserPermission.objects.create(
            user=self.generation_manager,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(self.generation_manager)
        for url in (administration_url, exempt_url, restore_url):
            with self.subTest(boundary="direct-deny", url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_automatic_inclusion_management_fails_closed_for_feature_tenant_and_campus_scope(self):
        parent = self.make_automatic_course(code="AUTO-SCOPE")
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        cycle_list_url = reverse(
            "departmental_exams:cycle_course_list", args=[parent.cycle_id]
        )
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[parent.id]
        )
        self.client.force_login(self.generation_manager)

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(assigned_url).status_code, 403)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(administration_url).status_code, 403)
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

        with self.assertRaises(CycleCourse.DoesNotExist):
            CycleCourseInclusionService.exempt(
                cycle_course_id=parent.id,
                tenant_id=self.other_tenant.id,
                user=self.generation_manager,
                exemption_category=CycleCourse.ExemptionCategory.INTERNSHIP,
                reason="Approved internship assessment workflow",
                expected_updated_at=CycleCourseInclusionService.transition_token(
                    parent
                ),
            )
        parent.refresh_from_db()
        self.assertEqual(
            parent.inclusion_status,
            CycleCourse.InclusionStatus.INCLUDED,
        )

        wrong_tenant_parent = self.make_other_tenant_automatic_course(
            code="AUTO-WRONG-TENANT"
        )
        assigned_response = self.client.get(assigned_url)
        self.assertEqual(assigned_response.status_code, 200)
        self.assertNotContains(assigned_response, wrong_tenant_parent.course.code)
        self.assertNotIn(
            wrong_tenant_parent.id,
            {course.id for course in assigned_response.context["courses"]},
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "departmental_exams:cycle_course_list",
                    args=[wrong_tenant_parent.cycle_id],
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "departmental_exams:cycle_course_administration",
                    args=[wrong_tenant_parent.id],
                )
            ).status_code,
            404,
        )

        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            code=f"AUTO-NORTH-{parent.id}",
            name="Automatic North Program",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            code=f"AUTO-NORTH-{parent.id}",
            name="Automatic North Section",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            academic_year=parent.cycle.academic_year,
            term=parent.cycle.term,
            course=parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=parent,
            offering=offering,
            campus=self.other_campus,
        )
        self.assertEqual(self.client.get(assigned_url).status_code, 403)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(administration_url).status_code, 403)

    def test_manual_null_department_rules_remain_blocking(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        parent = self.make_course(cycle=cycle, department=None, code="MANUAL-NULL")
        readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=parent,
            configuration=None,
            user=self.configurer,
        )
        self.assertIn("Needs Exam Department", readiness["blockers"])
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_configure_cycle_course(
                user=self.configurer,
                cycle_course=parent,
            )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("departmental_exams:course_configuration", args=[parent.id])
        )
        self.assertContains(response, "Needs Exam Department")
        self.assertNotContains(response, "Save Draft Configuration")

    def test_automatic_rosters_are_independent_and_preserve_submitted_history(self):
        midterm = self.make_automatic_cycle(period=ExaminationCycle.ExamPeriod.MIDTERM)
        midterm_parent = self.make_automatic_course(cycle=midterm, code="SHARED")
        final = self.make_automatic_cycle(period=ExaminationCycle.ExamPeriod.FINAL)
        final_parent, final_offering = self.add_same_course_to_cycle(
            source_parent=midterm_parent,
            cycle=final,
            slug="FINAL",
        )
        faculty = self.make_faculty("automatic-shared-faculty")
        self.make_assignment(midterm_parent, faculty)
        final_assignment = self.make_assignment(
            final_parent,
            faculty,
            offering=final_offering,
        )

        ContributionRosterService.initialize(
            cycle_course_id=midterm_parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        ContributionRosterService.initialize(
            cycle_course_id=final_parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        midterm_snapshot = list(
            FacultyContribution.objects.filter(cycle_course=midterm_parent).values_list(
                "id", "revision", "status"
            )
        )

        submitted = FacultyContribution.objects.get(
            cycle_course=final_parent,
            faculty_user=faculty,
        )
        submitted.status = FacultyContribution.Status.SUBMITTED
        submitted.submitted_at = timezone.now()
        submitted.save(update_fields=["status", "submitted_at", "updated_at"])
        final_assignment.is_active = False
        final_assignment.save(update_fields=["is_active", "updated_at"])
        replacement = self.make_faculty("automatic-final-replacement")
        self.make_assignment(final_parent, replacement, offering=final_offering)

        result = ContributionRosterService.synchronize(
            cycle_course_id=final_parent.id,
            tenant_id=self.tenant.id,
            actor=self.generation_manager,
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(
            list(
                FacultyContribution.objects.filter(cycle_course=midterm_parent).values_list(
                    "id", "revision", "status"
                )
            ),
            midterm_snapshot,
        )
        submitted.refresh_from_db()
        self.assertEqual(submitted.status, FacultyContribution.Status.SUBMITTED)
        self.assertEqual(submitted.roster_status, FacultyContribution.RosterStatus.ACTIVE)
        self.assertTrue(submitted.eligibility_sources.get().is_current)
        self.assertTrue(
            FacultyContribution.objects.filter(
                cycle_course=final_parent,
                faculty_user=replacement,
                status=FacultyContribution.Status.DRAFT,
            ).exists()
        )

        final_configuration = CourseExamConfiguration.objects.get(cycle_course=final_parent)
        final_configuration.workflow_status = CourseExamConfiguration.WorkflowStatus.CLOSED
        final_configuration.save(update_fields=["workflow_status", "updated_at"])
        with self.assertRaisesMessage(
            ValidationError,
            "The course must be open for faculty contribution.",
        ):
            ContributionRosterService.synchronize(
                cycle_course_id=final_parent.id,
                tenant_id=self.tenant.id,
                actor=self.generation_manager,
            )

    def test_monitoring_filters_are_combined_scoped_and_cycle_aware(self):
        midterm = self.make_automatic_cycle(period=ExaminationCycle.ExamPeriod.MIDTERM)
        midterm_parent = self.make_automatic_course(cycle=midterm, code="FILTER-MID")
        final = self.make_automatic_cycle(period=ExaminationCycle.ExamPeriod.FINAL)
        final_parent = self.make_automatic_course(cycle=final, code="FILTER-FINAL")
        other_final_parent = self.make_automatic_course(cycle=final, code="FILTER-OTHER")
        self.client.force_login(self.generation_manager)
        url = reverse("departmental_exams:contributor_monitoring")

        response = self.client.get(url, {"cycle": final.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_cycle_id"], final.id)
        self.assertEqual(
            {course.id for course in response.context["courses"]},
            {final_parent.id, other_final_parent.id},
        )
        response = self.client.get(url, {"period": ExaminationCycle.ExamPeriod.MIDTERM})
        self.assertEqual(
            {course.id for course in response.context["courses"]},
            {midterm_parent.id},
        )
        response = self.client.get(url, {"course": final_parent.course_id})
        self.assertEqual(
            {course.id for course in response.context["courses"]},
            {final_parent.id},
        )
        response = self.client.get(
            url,
            {
                "cycle": final.id,
                "period": ExaminationCycle.ExamPeriod.FINAL,
                "course": other_final_parent.course_id,
            },
        )
        self.assertEqual(
            [course.id for course in response.context["courses"]],
            [other_final_parent.id],
        )
        response = self.client.get(url, {"cycle": 999999, "period": "STALE"})
        self.assertIsNone(response.context["selected_cycle_id"])
        self.assertEqual(response.context["selected_period"], "")
        self.assertEqual(
            {course.id for course in response.context["courses"]},
            {midterm_parent.id, final_parent.id, other_final_parent.id},
        )

        other_tenant = Tenant.objects.create(code="AUTO-OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(
            tenant=other_tenant, code="OTHER", name="Other Campus"
        )
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        other_course = Course.objects.create(
            tenant=other_tenant, code="OTHER-COURSE", title="Other Tenant Course"
        )
        other_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code="OTHER-AY",
            name="Other AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        other_term = Term.objects.create(
            tenant=other_tenant,
            academic_year=other_year,
            code="OTHER-T1",
            name="Other Term",
        )
        other_program = Program.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="OTHER-P",
            name="Other Program",
        )
        other_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="OTHER-S",
            name="Other Section",
        )
        other_offering = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=other_year,
            term=other_term,
            course=other_course,
            section=other_section,
        )
        other_cycle = ExaminationCycle.objects.create(
            tenant=other_tenant,
            academic_year=other_year,
            term=other_term,
            exam_period=ExaminationCycle.ExamPeriod.FINAL,
            status=ExaminationCycle.Status.OPEN,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=self.admin,
        )
        other_parent = CycleCourse.objects.create(
            cycle=other_cycle,
            course=other_course,
        )
        CycleCourseOffering.objects.create(
            cycle_course=other_parent,
            offering=other_offering,
            campus=other_campus,
        )
        response = self.client.get(url)
        self.assertNotContains(response, other_course.code)
        self.assertNotIn(other_parent.id, {course.id for course in response.context["courses"]})

    def test_assigned_course_automatic_ux_and_summary_are_not_repeated(self):
        cycle = self.make_automatic_cycle()
        first = self.make_automatic_course(cycle=cycle, code="ASSIGNED-A")
        second = self.make_automatic_course(cycle=cycle, code="ASSIGNED-B")
        self.client.force_login(self.generation_manager)
        url = reverse("departmental_exams:assigned_course_examinations")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Exam Department assigned", count=2)
        self.assertNotContains(response, "Needs Exam Department")
        self.assertNotContains(response, "Read-only")
        self.assertContains(response, "Confidential Inputs", count=2)
        summary_url = reverse(
            "departmental_exams:automatic_generation_summary", args=[cycle.id]
        )
        self.assertContains(response, summary_url, count=1)
        self.assertEqual(
            {course.id for course in response.context["courses"]},
            {first.id, second.id},
        )

    def test_automatic_child_pages_expose_module_home_navigation(self):
        cycle = self.make_automatic_cycle()
        parent = self.make_automatic_course(cycle=cycle, code="NAV")
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        revision = ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=1,
            status=ExamGenerationRevision.Status.GENERATED,
            current_marker=1,
            source_input_fingerprint="a" * 64,
            algorithm_version="navigation-test",
            generated_by=None,
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=configuration.revision,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="b" * 64,
            final_item_count_snapshot=configuration.final_item_count,
            request_token_digest="c" * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )
        self.client.force_login(self.generation_manager)
        routes = (
            reverse("departmental_exams:assigned_course_examinations"),
            reverse("departmental_exams:cycle_course_list", args=[cycle.id]),
            reverse("departmental_exams:cycle_course_administration", args=[parent.id]),
            reverse("departmental_exams:course_configuration", args=[parent.id]),
            reverse("departmental_exams:contributor_monitoring") + f"?cycle={cycle.id}",
            reverse("departmental_exams:blueprint_configuration", args=[parent.id]),
            reverse("departmental_exams:blueprint_review", args=[parent.id]),
            reverse("departmental_exams:generation_workspace", args=[parent.id]),
            reverse("departmental_exams:automatic_generation_summary", args=[cycle.id]),
            reverse("departmental_exams:generated_revision_detail", args=[revision.id]),
            reverse("departmental_exams:automatic_contribution_reopen", args=[parent.id]),
        )
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Departmental Exam Builder")
