"""Phase 1 service and rendered-navigation contracts on disposable test data."""
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from .automatic_workflow import AutomaticContributionReopenService, AutomaticExamDeadlineService, FacultyContributionPreparationService
from .forms import ExaminationCycleForm
from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle, ExamBlueprint, ExamSection, FacultyContribution
from .services import CourseExamConfigurationService, CourseExamConfigurationConflict, ExaminationCycleService
from .setup_services import CourseSetupService, automatic_structure_blockers
from .stage4_test_support import Stage4TestCase
from . import tests_automatic_preparation as preparation_fixtures


class UnifiedSetupTests(Stage4TestCase):
    make_automatic_cycle = preparation_fixtures.AutomaticPreparationTests.make_automatic_cycle
    make_faculty_assignment = preparation_fixtures.AutomaticPreparationTests.make_faculty_assignment

    def setUp(self):
        super().setUp()
        from apps.rbac.models import Permission
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={"module": "faculty_portal", "action": "access", "description": "Faculty Portal", "is_active": True})
        self.bulk_manager = self.make_user("bulk-manager", None,
            ("admin_portal.access", "departmental_exams.manage_exam_generation"), campus=self.campus)

    def new_course(self, cycle, code="UNIFIED"):
        course = self.make_course(cycle=cycle, department=None, code=code)
        CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
            actor=self.bulk_manager, classification="STANDARDIZED",
            expected_state=CourseSetupService.fingerprint(course))
        course.refresh_from_db()
        self.make_faculty_assignment(course, username="faculty-" + code)
        return course

    def rows_token(self, cycle, courses):
        rows = CourseSetupService.preview(cycle=cycle, actor=self.bulk_manager,
            selected_ids=[c.id for c in courses])
        return rows, CourseSetupService.confirmation(cycle=cycle, actor=self.bulk_manager, rows=rows)

    def test_creation_ignores_tampered_manual_mode_and_keeps_legacy(self):
        legacy = self.make_cycle()
        self.make_course(cycle=legacy)
        created = ExaminationCycleService.create_cycle(user=self.admin, tenant=self.tenant,
            academic_year=legacy.academic_year, term=legacy.term, exam_period="FINAL",
            processing_mode="MANUAL_REVIEW")
        if isinstance(created, tuple):
            created = created[0]
        self.assertEqual(created.processing_mode, "AUTOMATIC_GENERATION")
        self.assertTrue(created.cycle_courses.exists())
        self.assertEqual(set(created.cycle_courses.values_list("exam_classification", flat=True)), {"STANDARDIZED"})
        legacy.refresh_from_db()
        self.assertEqual(legacy.processing_mode, "MANUAL_REVIEW")
        self.assertNotIn("processing_mode", ExaminationCycleForm().fields)

    def test_effective_defaults_get_no_writes_and_open_retry(self):
        cycle = self.make_automatic_cycle()
        cycle.default_questions_required_per_faculty = 63
        cycle.default_final_item_count = 60
        cycle.save()
        course = self.new_course(cycle)
        self.client.force_login(self.bulk_manager)
        response = self.client.get(reverse("departmental_exams:course_setup", args=[cycle.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "63")
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=course).exists())
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=course).exists())
        rows, token = self.rows_token(cycle, [course])
        self.assertEqual(rows[0]["status"], "Ready", rows)
        _, retry = CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(retry)
        config = CourseExamConfiguration.objects.get(cycle_course=course)
        self.assertEqual((config.questions_required_per_faculty, config.final_item_count), (63, 60))
        self.assertEqual(config.workflow_status, "OPEN")
        blueprint = ExamBlueprint.objects.get(cycle_course=course)
        self.assertEqual(blueprint.mode, "NO_SECTIONS")
        self.assertIsNotNone(blueprint.structure_frozen_at)
        revision = config.revision
        from apps.auditlog.models import AuditLog
        from .models import FacultyContribution, FacultyContributionEligibilitySource
        before_retry = (AuditLog.objects.count(), FacultyContribution.objects.count(),
                        FacultyContributionEligibilitySource.objects.count())
        _, retry = CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertTrue(retry)
        self.assertEqual((AuditLog.objects.count(), FacultyContribution.objects.count(),
                          FacultyContributionEligibilitySource.objects.count()), before_retry)
        config.refresh_from_db()
        self.assertEqual(config.revision, revision)
        with self.assertRaises(ValidationError):
            CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
                actor=self.bulk_manager, classification="DEPARTMENTAL",
                expected_state=CourseSetupService.fingerprint(course))

    def test_stale_and_cross_actor_confirmations_cannot_open_any_course(self):
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "FIRST"), self.new_course(cycle, "SECOND")
        _, token = self.rows_token(cycle, [first, second])
        CourseSetupService.materialize(second, actor=self.bulk_manager)
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.filter(workflow_status="OPEN").exists())
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=first).exists())
        with self.assertRaises(PermissionDenied):
            CourseSetupService.open_selection(cycle=cycle, actor=self.admin, token=token)

    def test_atomic_rollback_on_late_second_course_failure(self):
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "FIRST"), self.new_course(cycle, "SECOND")
        _, token = self.rows_token(cycle, [first, second])
        original = CourseExamConfigurationService.open_for_contribution
        from apps.auditlog.models import AuditLog
        from .models import FacultyContribution, FacultyContributionEligibilitySource
        audits_before = list(AuditLog.objects.order_by("pk").values())
        def fail_second(**kwargs):
            if kwargs["cycle_course_id"] == second.id:
                raise ValidationError("Late eligibility change")
            return original(**kwargs)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with patch.object(CourseExamConfigurationService, "open_for_contribution", side_effect=fail_second):
                with self.assertRaises(ValidationError):
                    CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.exists())
        self.assertFalse(ExamBlueprint.objects.exists())
        self.assertFalse(FacultyContribution.objects.exists())
        self.assertFalse(FacultyContributionEligibilitySource.objects.exists())
        self.assertEqual(list(AuditLog.objects.order_by("pk").values()), audits_before)
        self.assertEqual(callbacks, [])

    def test_explicit_legacy_classification_and_model_write_guard(self):
        cycle = self.make_automatic_cycle()
        course = self.make_course(cycle=cycle, department=None)
        self.assertEqual(course.exam_classification, "UNCLASSIFIED_LEGACY")
        CourseSetupService.materialize(course, actor=self.bulk_manager)
        course.refresh_from_db()
        self.assertEqual(course.exam_classification, "UNCLASSIFIED_LEGACY")
        with self.assertRaises(ValidationError):
            CycleCourse.objects.filter(pk=course.id).update(exam_classification="STANDARDIZED")
        course.exam_classification = "DEPARTMENTAL"
        with self.assertRaises(ValidationError):
            course.save()

    def test_unsupported_structure_blocks_preview_open_and_worker_without_closing(self):
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
            actor=self.bulk_manager, classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(course))
        course.refresh_from_db()
        configuration = CourseSetupService.materialize(course, actor=self.bulk_manager)
        blueprint = ExamBlueprint.objects.create(cycle_course=course, mode="USE_SECTIONS", created_by=self.admin, updated_by=self.admin)
        ExamSection.objects.create(
            blueprint=blueprint, title="Part I: Problem Solving", display_order=1, item_quota=20
        )
        ExamSection.objects.create(
            blueprint=blueprint, title="Part II: Multiple Choice", display_order=2, item_quota=30
        )
        self.assertTrue(automatic_structure_blockers(course))
        self.client.force_login(self.bulk_manager)
        overview = self.client.get(reverse("departmental_exams:assigned_course_examinations"))
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "Configuration Ready")
        self.assertContains(
            overview,
            "Configuration Ready does not confirm opening eligibility. "
            "Use Prepare Faculty Contributions to check opening requirements.",
        )
        for route, args in (
            ("assigned_courses_print", []),
            ("cycle_course_list", [cycle.id]),
            ("course_configuration", [course.id]),
        ):
            with self.subTest(configuration_label_consumer=route):
                consumer = self.client.get(
                    reverse("departmental_exams:" + route, args=args)
                )
                self.assertEqual(consumer.status_code, 200)
                self.assertContains(consumer, "Configuration Ready")
        preparation = self.client.get(
            reverse("departmental_exams:prepare_faculty_contributions", args=[cycle.id])
        )
        self.assertEqual(preparation.status_code, 200)
        self.assertContains(preparation, "<h1>Prepare Faculty Contributions</h1>", html=True)
        self.assertContains(
            preparation,
            '<a href="{}">Manage Course Exams</a>'.format(
                reverse("departmental_exams:assigned_course_examinations")
            ),
            html=True,
        )
        self.assertEqual(preparation.context["rows"][0]["status"], "Blocked")
        expected_reason = (
            "Explicit Exam Sections are not supported by Automatic generation in Phase 1. "
            "Contributions cannot open for this structure yet."
        )
        self.assertIn(expected_reason, preparation.context["rows"][0]["reasons"])
        rows, token = self.rows_token(cycle, [course])
        self.assertEqual(rows[0]["status"], "Blocked")
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        with self.assertRaises(ValidationError):
            CourseExamConfigurationService.open_for_contribution(cycle_course_id=course.id,
                tenant_id=self.tenant.id, user=self.bulk_manager, expected_revision=configuration.revision)
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "DRAFT")
        self.assertFalse(FacultyContribution.objects.filter(cycle_course=course).exists())
        result = AutomaticExamDeadlineService.process_course(cycle_course_id=course.id, tenant_id=self.tenant.id)
        self.assertEqual(result.code, "AUTOMATIC_STRUCTURE_UNSUPPORTED")
        blueprint.sections.all().delete()
        blueprint.mode = "NO_SECTIONS"
        blueprint.save()
        rows, token = self.rows_token(cycle, [course])
        self.assertEqual(rows[0]["status"], "Ready", rows)
        CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)

    def test_cycle_visibility_and_closed_direct_configuration(self):
        cycles = [self.make_automatic_cycle(status=status, suffix=str(index)) for index, status in enumerate(("OPEN", "OPEN", "DRAFT", "CLOSED"))]
        self.client.force_login(self.admin)
        response = self.client.get(reverse("departmental_exams:cycle_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({c.id for c in response.context["cycles"]}, {c.id for c in cycles if c.status == "OPEN"})
        response = self.client.get(reverse("departmental_exams:cycle_list"), {"cycle_status": "CLOSED"})
        self.assertEqual({c.id for c in response.context["cycles"]}, {cycles[-1].id})
        response = self.client.get(reverse("departmental_exams:cycle_configuration", args=[cycles[-1].id]))
        self.assertEqual(response.status_code, 200)

    def test_equivalency_classification_and_atomic_unit_open(self):
        from .exam_units import ExamCourseEquivalencyService
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "EQ1"), self.new_course(cycle, "EQ2")
        for course in (first, second):
            CourseSetupService.materialize(course, actor=self.bulk_manager)
        ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Equivalent exam",
            primary_cycle_course_id=first.id, member_ids=[first.id, second.id], actor=self.admin)
        CourseSetupService.classify(course_id=second.id, tenant_id=self.tenant.id,
            actor=self.bulk_manager, classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(second))
        self.assertEqual(set(CycleCourse.objects.filter(cycle=cycle).values_list("exam_classification", flat=True)), {"DEPARTMENTAL"})
        ExamBlueprint.objects.create(cycle_course=first, mode="NO_SECTIONS", created_by=self.admin, updated_by=self.admin)
        rows, token = self.rows_token(cycle, [second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["course"].id, first.id)
        self.assertEqual(rows[0]["status"], "Ready", rows)
        CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertEqual(CourseExamConfiguration.objects.filter(workflow_status="OPEN").count(), 2)
        self.assertEqual(ExamBlueprint.objects.count(), 1)
        self.client.force_login(self.bulk_manager)
        for route, args in (("assigned_course_examinations", []),
                            ("assigned_courses_print", []),
                            ("contributor_monitoring", []),
                            ("cycle_course_administration", [first.id])):
            with self.subTest(route=route):
                page = self.client.get(reverse("departmental_exams:" + route, args=args))
                self.assertContains(page, '>DEPTAL</span>')

    def test_overrides_and_first_open_history_are_preserved(self):
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        configuration = self.make_configuration(course, quota=70, final_count=60,
            workflow="DRAFT", coverage="Explicit course coverage", coverage_source="OVERRIDE",
            deadline=self.future_deadline())
        rows, token = self.rows_token(cycle, [course])
        self.assertEqual(rows[0]["configuration"].questions_required_per_faculty, 70)
        CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        configuration.refresh_from_db()
        self.assertEqual(configuration.coverage, "Explicit course coverage")
        self.assertEqual(configuration.final_item_count, 60)
        self.assertEqual(configuration.questions_required_per_faculty, 70)
        # First-open evidence remains decisive even for a returned Draft status.
        configuration.workflow_status = "DRAFT"
        configuration.save(update_fields=["workflow_status"])
        with self.assertRaises(ValidationError):
            CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
                actor=self.bulk_manager, classification="DEPARTMENTAL",
                expected_state=CourseSetupService.fingerprint(course))

    def test_cross_tenant_and_direct_deny_selection(self):
        from apps.rbac.models import Permission, UserPermission
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        other_cycle = self.make_automatic_cycle(suffix="other")
        unrelated = self.new_course(other_cycle, "OTHER")
        with self.assertRaises(PermissionDenied):
            CourseSetupService.preview(cycle=cycle, actor=self.bulk_manager, selected_ids=[unrelated.id])
        _, token = self.rows_token(cycle, [course])
        UserPermission.objects.create(user=self.bulk_manager,
            permission=Permission.objects.get(code="departmental_exams.manage_exam_generation"),
            grant_type="DENY", tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)

    def test_rendered_selection_post_confirmation_and_stale_http_409(self):
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        self.client.force_login(self.bulk_manager)
        url = reverse("departmental_exams:course_setup", args=[cycle.id])
        page = self.client.get(url)
        self.assertContains(page, 'name="courses" value="%s"' % course.id)
        rendered_id = page.context["rows"][0]["course"].id
        review = self.client.post(url, {"courses": [rendered_id]})
        self.assertEqual(review.status_code, 200)
        token = review.context["confirmation"]
        self.assertTrue(token)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=course).exists())
        CourseSetupService.materialize(course, actor=self.bulk_manager)
        stale = self.client.post(url, {"confirmation": token})
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, "No courses opened", status_code=409)
        review = self.client.post(url, {"courses": [rendered_id]})
        opened = self.client.post(url, {"confirmation": review.context["confirmation"]})
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "confirmed selection was processed")

    def test_group_primary_change_invalidates_signed_preview(self):
        from .exam_units import ExamCourseEquivalencyService
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "EQ1"), self.new_course(cycle, "EQ2")
        for course in (first, second):
            CourseSetupService.materialize(course, actor=self.bulk_manager)
        group = ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Equivalent exam",
            primary_cycle_course_id=first.id, member_ids=[first.id, second.id], actor=self.admin)
        _, token = self.rows_token(cycle, [first])
        ExamCourseEquivalencyService.replace_members(group_id=group.id,
            primary_cycle_course_id=second.id, member_ids=[first.id, second.id], actor=self.admin)
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.filter(workflow_status="OPEN").exists())

    def test_unsupported_structure_also_blocks_prepare_reopen_and_generation(self):
        from .generation_readiness import Stage6ReadinessService
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
            actor=self.bulk_manager, classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(course))
        course.refresh_from_db()
        ExamBlueprint.objects.create(cycle_course=course, mode="USE_SECTIONS",
            created_by=self.admin, updated_by=self.admin)
        with self.assertRaises(ValidationError):
            FacultyContributionPreparationService.prepare(cycle_id=cycle.id,
                tenant_id=self.tenant.id, actor=self.bulk_manager)
        self.assertFalse(CourseExamConfiguration.objects.exists())
        configuration = self.make_configuration(course, workflow="CLOSED",
            opened_at=cycle.created_at, deadline=self.future_deadline())
        with self.assertRaisesMessage(ValidationError, "Explicit Exam Sections"):
            AutomaticContributionReopenService.reopen(cycle_course_id=course.id,
                tenant_id=self.tenant.id, actor=self.bulk_manager,
                expected_revision=configuration.revision, new_deadline=self.future_deadline())
        report = Stage6ReadinessService.evaluate(cycle_course=course)
        self.assertIn("AUTOMATIC_STRUCTURE_UNSUPPORTED", {b["code"] for b in report["blockers"]})
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "CLOSED")

    def test_mixed_readiness_and_no_open_cycle_empty_state(self):
        cycle = self.make_automatic_cycle()
        ready = self.new_course(cycle, "READY")
        legacy = self.make_course(cycle=cycle, department=None, code="UNKNOWN")
        exempt = self.new_course(cycle, "EXEMPT")
        CycleCourse.objects.filter(pk=exempt.pk).update(inclusion_status="EXEMPT")
        rows, token = self.rows_token(cycle, [ready, legacy, exempt])
        self.assertEqual({r["status"] for r in rows}, {"Ready", "Blocked", "Exempt"})
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.exists())
        cycle.status = "CLOSED"
        cycle.save(update_fields=["status"])
        self.client.force_login(self.admin)
        response = self.client.get(reverse("departmental_exams:cycle_list"))
        self.assertEqual(list(response.context["cycles"]), [])
        self.assertContains(response, "Past / Closed cycles")

    def test_open_unsupported_unit_blocks_an_entire_new_selection(self):
        cycle = self.make_automatic_cycle()
        ready = self.new_course(cycle, "READY")
        historical = self.make_course(cycle=cycle, department=None, code="HISTORICAL")
        self.make_configuration(historical, workflow="OPEN", opened_at=cycle.created_at)
        ExamBlueprint.objects.create(cycle_course=historical, mode="USE_SECTIONS",
            created_by=self.admin, updated_by=self.admin)
        rows, token = self.rows_token(cycle, [ready, historical])
        self.assertEqual(next(r for r in rows if r["course"].id == historical.id)["status"], "Blocked")
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=ready).exists())

    def test_partially_open_group_is_not_reported_already_open(self):
        from .exam_units import ExamCourseEquivalencyService
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "EQ1"), self.new_course(cycle, "EQ2")
        for course in (first, second):
            CourseSetupService.materialize(course, actor=self.bulk_manager)
        ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Equivalent",
            primary_cycle_course_id=first.id, member_ids=[first.id, second.id], actor=self.admin)
        # Retained inconsistent state must be diagnosed, never repaired by setup.
        CourseExamConfiguration.objects.filter(cycle_course=first).update(
            workflow_status="OPEN", opened_at=cycle.created_at)
        rows, token = self.rows_token(cycle, [first])
        self.assertEqual(rows[0]["status"], "Blocked")
        with self.assertRaises(CourseExamConfigurationConflict):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertEqual(CourseExamConfiguration.objects.get(cycle_course=second).workflow_status, "DRAFT")

    def test_explicit_classification_keeps_manual_post_close_blueprint_contract(self):
        from .blueprint_services import BlueprintMutationService
        cycle = self.make_cycle(status="OPEN")
        course = self.make_course(cycle=cycle)
        configuration = self.make_configuration(course)
        CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
            actor=self.configurer, classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(course))
        opened, changed = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=course.id, tenant_id=self.tenant.id,
            user=self.configurer, expected_revision=configuration.revision)
        self.assertTrue(changed)
        self.assertEqual(opened.workflow_status, "OPEN")
        self.assertFalse(ExamBlueprint.objects.filter(cycle_course=course).exists())
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(workflow_status="CLOSED")
        blueprint, changed = BlueprintMutationService.save_structure(cycle_course_id=course.id,
            tenant_id=self.tenant.id, actor=self.configurer, expected_revision=0,
            mode="NO_SECTIONS", sections=[])
        self.assertTrue(changed)
        self.assertIsNone(blueprint.structure_frozen_at)
        self.client.force_login(self.configurer)
        page = self.client.get(reverse("departmental_exams:blueprint_configuration", args=[course.id]))
        self.assertFalse(page.context["structured_lifecycle_enabled"])
        cycle.refresh_from_db()
        self.assertEqual(cycle.processing_mode, "MANUAL_REVIEW")

    def test_expired_tampered_and_foreign_tenant_confirmation_no_writes(self):
        from django.core import signing
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        _, token = self.rows_token(cycle, [course])
        with patch("django.core.signing.time.time", return_value=0):
            _, expired = self.rows_token(cycle, [course])
        for invalid in (expired, token + "tampered"):
            with self.assertRaises(CourseExamConfigurationConflict):
                CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=invalid)
        state = signing.loads(token, salt=CourseSetupService.SALT)
        state["tenant"] = self.other_tenant.id
        foreign = signing.dumps(state, salt=CourseSetupService.SALT)
        with self.assertRaises(PermissionDenied):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=foreign)
        self.assertFalse(CourseExamConfiguration.objects.exists())

    def test_standardized_csv_submission_uses_implicit_no_sections(self):
        from . import tests_stage5_csv as csv_fixtures
        from .csv_import import QuestionCSVImportService
        from .contribution_services import QuestionMutationService
        from .generation_readiness import Stage6ReadinessService
        from .models import FacultyContribution, QuestionBlueprintPlacement
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        _, token = self.rows_token(cycle, [course])
        CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        contribution = FacultyContribution.objects.get(cycle_course=course)
        upload = csv_fixtures.Stage5CSVTests.csv_upload([
            csv_fixtures.Stage5CSVTests.row("Imported question %s" % index)
            for index in range(contribution.quota_snapshot)])
        batch = QuestionCSVImportService.create_preview(contribution_id=contribution.id,
            uploaded_file=upload, user=contribution.faculty_user, tenant_id=self.tenant.id,
            campus_id=self.campus.id, expected_contribution_revision=contribution.revision)
        QuestionCSVImportService.confirm(token=batch.token, expected_file_sha256=batch.file_sha256,
            user=contribution.faculty_user, tenant_id=self.tenant.id, campus_id=self.campus.id)
        contribution.refresh_from_db()
        submitted, changed = QuestionMutationService.submit(contribution_id=contribution.id,
            user=contribution.faculty_user, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=contribution.revision)
        self.assertTrue(changed)
        self.assertEqual(submitted.status, "SUBMITTED")
        self.assertEqual(submitted.questions.count(), 50)
        # An implicit placement is absence of an explicit, non-null section FK.
        self.assertFalse(QuestionBlueprintPlacement.objects.exists())
        self.assertEqual(automatic_structure_blockers(course), [])
        report = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=course)
        self.assertNotIn("AUTOMATIC_STRUCTURE_UNSUPPORTED", {b["code"] for b in report["blockers"]})

    def test_departmental_rendered_blueprint_save_without_draft_configuration(self):
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        CourseSetupService.classify(course_id=course.id, tenant_id=self.tenant.id,
            actor=self.bulk_manager, classification="DEPARTMENTAL",
            expected_state=CourseSetupService.fingerprint(course))
        self.client.force_login(self.bulk_manager)
        url = reverse("departmental_exams:blueprint_configuration", args=[course.id])
        page = self.client.get(url)
        self.assertFalse(CourseExamConfiguration.objects.exists())
        formset = page.context["section_formset"]
        data = {"mode": "NO_SECTIONS", "expected_revision": page.context["form"]["expected_revision"].value()}
        data.update({"sections-" + key: value for key, value in formset.management_form.initial.items()})
        saved = self.client.post(url, data)
        self.assertEqual(saved.status_code, 302)
        rows, token = self.rows_token(cycle, [course])
        self.assertEqual(rows[0]["status"], "Ready")
        CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertIsNotNone(ExamBlueprint.objects.get(cycle_course=course).structure_frozen_at)

    def test_deadline_revalidates_structure_under_the_closure_lock(self):
        from django.utils import timezone
        cycle = self.make_automatic_cycle()
        course = self.make_course(cycle=cycle, department=None)
        configuration = self.make_configuration(course, workflow="OPEN",
            opened_at=cycle.created_at, deadline=timezone.now() - timezone.timedelta(minutes=1))
        ExamBlueprint.objects.create(cycle_course=course, mode="USE_SECTIONS",
            created_by=self.admin, updated_by=self.admin)
        # Simulate the outer preflight seeing the old state before a writer commits.
        original = automatic_structure_blockers
        calls = 0
        def changed_after_preflight(parent):
            nonlocal calls
            calls += 1
            return [] if calls == 1 else original(parent)
        with patch("apps.departmental_exams.setup_services.automatic_structure_blockers",
                   side_effect=changed_after_preflight):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=course.id, tenant_id=self.tenant.id)
        self.assertEqual(result.code, "AUTOMATIC_STRUCTURE_UNSUPPORTED")
        configuration.refresh_from_db()
        self.assertEqual(configuration.workflow_status, "OPEN")
        self.assertIsNone(configuration.closed_at)

    def test_group_campus_union_denial_cannot_expose_or_open_primary(self):
        from apps.rbac.models import Permission, UserPermission
        from .exam_units import ExamCourseEquivalencyService
        from .models import CycleCourseOffering
        cycle = self.make_automatic_cycle()
        first, second = self.new_course(cycle, "EQ1"), self.new_course(cycle, "EQ2")
        for course in (first, second):
            CourseSetupService.materialize(course, actor=self.bulk_manager)
        ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Equivalent",
            primary_cycle_course_id=first.id, member_ids=[first.id, second.id], actor=self.admin)
        CycleCourseOffering.objects.filter(cycle_course=second).update(campus=self.other_campus)
        with self.assertRaises(PermissionDenied):
            self.rows_token(cycle, [first])
        permission = Permission.objects.get(code="departmental_exams.manage_exam_generation")
        UserPermission.objects.create(user=self.bulk_manager, permission=permission,
            grant_type="ALLOW", tenant=self.tenant, campus=self.other_campus)
        _, token = self.rows_token(cycle, [first])
        UserPermission.objects.filter(user=self.bulk_manager, permission=permission,
            campus=self.other_campus).update(grant_type="DENY")
        with self.assertRaises(PermissionDenied):
            CourseSetupService.open_selection(cycle=cycle, actor=self.bulk_manager, token=token)
        self.assertFalse(CourseExamConfiguration.objects.filter(workflow_status="OPEN").exists())

    def test_setup_and_classification_posts_require_csrf_and_exact_tenant(self):
        from django.test import Client
        from apps.academics.models import AcademicYear, Term
        cycle = self.make_automatic_cycle()
        course = self.new_course(cycle)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.bulk_manager)
        for route, identifier in (("course_setup", cycle.id), ("course_classification", course.id)):
            self.assertEqual(client.post(reverse("departmental_exams:" + route,
                                                args=[identifier]), {}).status_code, 403)
        year = AcademicYear.objects.create(tenant=self.other_tenant, code="FOREIGN", name="Foreign",
            start_date="2026-01-01", end_date="2026-12-31")
        term = Term.objects.create(tenant=self.other_tenant, academic_year=year, code="T", name="T")
        foreign_cycle = ExaminationCycle.objects.create(tenant=self.other_tenant, academic_year=year,
            term=term, exam_period="FINAL", created_by=self.admin)
        self.assertEqual(client.get(reverse("departmental_exams:course_setup",
                                            args=[foreign_cycle.id])).status_code, 404)
        self.assertFalse(CourseExamConfiguration.objects.exists())
