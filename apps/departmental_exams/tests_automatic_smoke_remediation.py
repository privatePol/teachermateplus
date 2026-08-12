from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

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
