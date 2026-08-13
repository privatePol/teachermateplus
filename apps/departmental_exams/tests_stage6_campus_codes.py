from collections import Counter
from unittest.mock import patch

from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .approval_services import ExamApprovalLockService
from .automatic_workflow import AutomaticExamDeadlineService
from .blueprint_services import QuestionPlacementService
from .contribution_services import ContributionRosterService
from .generation_algorithms import AllocationError, CAMPUS_WEIGHTS, allocate_campuses
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    CourseExamConfiguration,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
)
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase
from .stage6_campus_codes import (
    Stage6CampusCodeAmbiguity,
    canonicalize_participating_campus_rows,
    canonicalize_stage6_campus_code,
)
from .tests_stage6c_approval import Stage6CFixtureMixin


class Stage6CampusCodeCanonicalizerTests(SimpleTestCase):
    def test_supported_real_legacy_and_normalized_aliases(self):
        aliases = {
            "CUBAO": "CUBAO",
            "NCBA-CUBAO": "CUBAO",
            "NCBA-01": "CUBAO",
            "FAIRVIEW": "FAIRVIEW",
            "NCBA-FAIRVIEW": "FAIRVIEW",
            "NCBA-02": "FAIRVIEW",
            "TAYTAY": "TAYTAY",
            "NCBA-TAYTAY": "TAYTAY",
            "NCBA-03": "TAYTAY",
            "  ncba-cubao  ": "CUBAO",
            "NcBa-FaIrViEw": "FAIRVIEW",
            " ncba-03 ": "TAYTAY",
        }
        for value, expected in aliases.items():
            with self.subTest(value=value):
                self.assertEqual(canonicalize_stage6_campus_code(value), expected)

    def test_unknown_and_unsupported_abbreviations_remain_fail_closed(self):
        for value, normalized in (
            ("UNKNOWN", "UNKNOWN"),
            ("NCBA-FAIRV", "NCBA-FAIRV"),
            ("NCBA-FVW", "NCBA-FVW"),
        ):
            with self.subTest(value=value):
                canonical = canonicalize_stage6_campus_code(value)
                self.assertEqual(canonical, normalized)
                self.assertNotIn(canonical, CAMPUS_WEIGHTS)
                with self.assertRaises(AllocationError):
                    allocate_campuses(50, (canonical,))

    def test_distinct_campuses_cannot_collapse_to_one_canonical_key(self):
        with self.assertRaisesRegex(
            Stage6CampusCodeAmbiguity,
            "Distinct participating campuses.*CUBAO",
        ):
            canonicalize_participating_campus_rows(
                ((101, "CUBAO"), (202, "NCBA-CUBAO"))
            )
        self.assertEqual(
            canonicalize_participating_campus_rows(
                ((101, "NCBA-CUBAO"), (101, "NCBA-CUBAO"))
            ),
            ("CUBAO",),
        )


class Stage6RealCampusCodeIntegrationTests(Stage6CFixtureMixin, Stage4TestCase):
    REAL_CODES = {
        "CUBAO": ("NCBA-CUBAO", "NCBA Cubao"),
        "FAIRVIEW": ("NCBA-FAIRVIEW", "NCBA Fairview"),
        "TAYTAY": ("NCBA-TAYTAY", "NCBA Taytay"),
    }

    def _use_real_three_campus_codes(self, parent):
        campuses = {}
        seen = set()
        snapshots = parent.offering_snapshots.select_related("campus").order_by(
            "campus_id", "id"
        )
        for snapshot in snapshots:
            campus = snapshot.campus
            if campus.id in seen:
                continue
            seen.add(campus.id)
            canonical = canonicalize_stage6_campus_code(campus.code)
            real_code, real_name = self.REAL_CODES[canonical]
            campus.code = real_code
            campus.name = real_name
            campus.save(update_fields=["code", "name", "updated_at"])
            campuses[canonical] = campus
        self.assertEqual(set(campuses), set(self.REAL_CODES))
        return campuses

    def _ready_real_three_campus_course(self):
        parent, _old_problem = self.ready_generation_course()
        campuses = self._use_real_three_campus_codes(parent)
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertIsNotNone(problem)
        return parent, problem, readiness, campuses

    def _ready_single_real_fairview_course(self):
        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        self.add_faculty_source(
            parent=parent,
            campus=campuses["CUBAO"],
            offering=offerings["CUBAO"],
            suffix="real-fairview",
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        contribution = FacultyContribution.objects.get(cycle_course=parent)
        self.add_questions(contribution)
        contribution.status = FacultyContribution.Status.SUBMITTED
        contribution.submitted_at = timezone.now()
        contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        configuration.refresh_from_db()
        configuration, _changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Single-campus real-code contribution is complete.",
        )
        self.no_sections_blueprint(parent)
        campus = campuses["CUBAO"]
        campus.code = "NCBA-FAIRVIEW"
        campus.name = "NCBA Fairview"
        campus.save(update_fields=["code", "name", "updated_at"])
        return parent, configuration, campus

    def test_real_three_campus_codes_preserve_existing_allocation(self):
        _parent, problem, readiness, _campuses = (
            self._ready_real_three_campus_course()
        )
        expected = {"CUBAO": 17, "FAIRVIEW": 16, "TAYTAY": 17}
        self.assertEqual(readiness["campus_quotas"], expected)
        self.assertEqual(problem.campus_quotas, expected)
        self.assertEqual(readiness["eligible_question_count"], 150)
        self.assertEqual(readiness["invalid_question_count"], 0)
        self.assertEqual(
            {question.campus_code for question in problem.questions.values()},
            set(expected),
        )

    def test_single_real_fairview_campus_with_fifty_questions_is_ready(self):
        parent, _configuration, campus = self._ready_single_real_fairview_course()
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        self.assertEqual(readiness["campus_quotas"], {"FAIRVIEW": 50})
        self.assertEqual(readiness["eligible_question_count"], 50)
        self.assertEqual(readiness["invalid_question_count"], 0)
        self.assertEqual(
            {question.campus_code for question in problem.questions.values()},
            {"FAIRVIEW"},
        )
        self.assertEqual(
            {question.campus_id for question in problem.questions.values()},
            {campus.id},
        )

    def test_question_placement_accepts_supported_real_campus_codes(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = (
            self.prepare_sections()
        )
        self._use_real_three_campus_codes(parent)
        question = Question.objects.filter(
            contribution__cycle_course=parent,
            contribution__source_campus__code="NCBA-FAIRVIEW",
        ).first()
        placement, changed = QuestionPlacementService.place(
            question_id=question.id,
            section_id=sections[0].id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_placement_revision=0,
        )
        self.assertTrue(changed)
        self.assertEqual(placement.blueprint_id, blueprint.id)
        self.assertEqual(placement.question_id, question.id)

    def test_confidential_workspace_accepts_real_fairview_campus_code(self):
        parent, _configuration, _campus = self._ready_single_real_fairview_course()
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])
        question = Question.objects.filter(contribution__cycle_course=parent).first()
        client = Client()
        client.force_login(self.reviewer)

        response = client.get(
            reverse("departmental_exams:blueprint_review", args=[parent.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, question.question_text)
        self.assertEqual(len(response.context["questions"]), 50)
        self.assertEqual(
            {item.stage6_campus_code for item in response.context["questions"]},
            {"FAIRVIEW"},
        )

    def test_confidential_workspace_rejects_distinct_campus_alias_collision(self):
        parent, _problem = self.ready_generation_course()
        campuses = {
            campus.code: campus
            for campus in {
                snapshot.campus
                for snapshot in parent.offering_snapshots.select_related("campus")
            }
        }
        cubao_question = Question.objects.filter(
            contribution__cycle_course=parent,
            contribution__source_campus=campuses["CUBAO"],
        ).first()
        fairview_question = Question.objects.filter(
            contribution__cycle_course=parent,
            contribution__source_campus=campuses["FAIRVIEW"],
        ).first()
        campuses["FAIRVIEW"].code = "NCBA-CUBAO"
        campuses["FAIRVIEW"].save(update_fields=["code", "updated_at"])
        review_url = reverse("departmental_exams:blueprint_review", args=[parent.id])
        client = Client()

        client.force_login(self.configurer)
        self.assertEqual(client.get(review_url).status_code, 403)

        client.force_login(self.reviewer)
        response = client.get(review_url)

        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response,
            "Distinct participating campuses resolve to the same Stage 6 campus code: CUBAO",
            status_code=409,
        )
        self.assertNotContains(response, cubao_question.question_text, status_code=409)
        self.assertNotContains(response, fairview_question.question_text, status_code=409)
        self.assertNotIn("questions", response.context)

    def test_manual_generation_and_approval_preserve_canonical_snapshots(self):
        parent, problem, _readiness, campuses = (
            self._ready_real_three_campus_course()
        )
        outcome = self.generate_with_proved_selection(
            parent=parent,
            problem=problem,
        )
        revision = outcome.revision
        expected = {"CUBAO": 17, "FAIRVIEW": 16, "TAYTAY": 17}
        generated_sets = list(
            GeneratedExamSet.objects.filter(generation_revision=revision).order_by(
                "set_code"
            )
        )
        self.assertEqual(len(generated_sets), 2)
        for generated_set in generated_sets:
            self.assertEqual(generated_set.campus_quotas_snapshot, expected)
            counts = Counter(
                generated_set.items.values_list("campus_code_snapshot", flat=True)
            )
            self.assertEqual(dict(counts), expected)

        items = GeneratedExamItem.objects.filter(
            generated_set__generation_revision=revision
        ).select_related("source_campus")
        self.assertEqual(
            {item.source_campus.code for item in items},
            {value[0] for value in self.REAL_CODES.values()},
        )
        self.assertTrue(
            all(
                item.campus_name_snapshot == item.source_campus.name
                and item.source_campus_id
                == campuses[item.campus_code_snapshot].id
                for item in items
            )
        )

        approval = ExamApprovalLockService.approve_and_lock(
            revision_id=revision.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_revision_number=revision.revision_number,
            expected_source_input_fingerprint=revision.source_input_fingerprint,
        )
        self.assertFalse(approval.reused)
        approval.revision.refresh_from_db()
        self.assertEqual(
            approval.revision.status,
            ExamGenerationRevision.Status.LOCKED,
        )

    def test_automatic_due_processing_generates_from_real_campus_codes(self):
        parent, _problem, _readiness, _campuses = (
            self._ready_real_three_campus_course()
        )
        cycle = parent.cycle
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parent.cycle = cycle
        parent.responsible_department = None
        parent.reviewer = None
        parent.save(
            update_fields=["responsible_department", "reviewer", "updated_at"]
        )
        configuration = CourseExamConfiguration.objects.get(cycle_course=parent)
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=timezone.now()
            - timezone.timedelta(minutes=1)
        )
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=parent
        )
        self.assertTrue(readiness["ready"], readiness["blockers"])
        with patch(
            "apps.departmental_exams.generation_services.solve_identity_aware_two_sets",
            return_value=self.proved_selection(problem),
        ):
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
            )
        self.assertEqual(result.status, "GENERATED")
        self.assertEqual(result.code, "GENERATED")
        revision = ExamGenerationService.current_for_course(cycle_course=parent)
        self.assertEqual(
            revision.generation_trigger,
            ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
        )
        self.assertIsNone(revision.generated_by_id)
        self.assertEqual(
            {
                item.campus_code_snapshot
                for item in GeneratedExamItem.objects.filter(
                    generated_set__generation_revision=revision
                )
            },
            set(self.REAL_CODES),
        )
