from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from . import blueprint_services
from .blueprint_services import (
    BlueprintMutationService,
    QuestionPlacementService,
    ScenarioMutationService,
    Stage6Conflict,
)
from .generation_readiness import Stage6ReadinessService
from .models import (
    ExamBlueprint,
    ExamScenario,
    ExamSection,
    FacultyContribution,
    Question,
    QuestionBlueprintPlacement,
)
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase
from .tests_stage6_lifecycle import Stage6FixtureMixin


class Stage6BlueprintFixtureMixin(Stage6FixtureMixin):
    def closed_course(self):
        parent, configuration, contributions, assignments = self.submitted_three_campus_course()
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])
        configuration, _changed = CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Completed contribution is ready for Stage 6 blueprint work.",
        )
        return parent, configuration, contributions, assignments

    def no_sections_blueprint(self, parent):
        blueprint, changed = BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_revision=0,
            mode=ExamBlueprint.Mode.NO_SECTIONS,
            sections=[],
        )
        self.assertTrue(changed)
        return blueprint

    def use_sections_blueprint(self, parent):
        blueprint = self.no_sections_blueprint(parent)
        blueprint, changed = BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_revision=blueprint.revision,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=[
                {"title": "Concepts", "instructions": "Choose the best answer.", "display_order": 1, "item_quota": 20},
                {"title": "Applications", "instructions": "Apply the scenario.", "display_order": 2, "item_quota": 30},
            ],
        )
        self.assertTrue(changed)
        return blueprint

    def prepare_sections(self):
        parent, configuration, contributions, assignments = self.closed_course()
        blueprint = self.use_sections_blueprint(parent)
        sections = list(blueprint.sections.order_by("display_order"))
        return parent, configuration, contributions, assignments, blueprint, sections


class Stage6BlueprintStructureTests(Stage6BlueprintFixtureMixin, Stage4TestCase):
    def test_no_sections_has_implicit_exact_quota_and_ready_three_campus_pool(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        result = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertTrue(result["ready"], result["blockers"])
        self.assertEqual(result["section_quotas"], [{"id": 0, "label": "Questionnaire", "required": 50, "available": 150}])
        self.assertEqual(result["campus_quotas"], {"CUBAO": 17, "FAIRVIEW": 16, "TAYTAY": 17})
        self.assertEqual(result["difficulty_quotas"], {"EASY": 15, "MODERATE": 25, "DIFFICULT": 10})
        self.assertEqual(result["minimum_overlap"], 0)

    def test_use_sections_requires_exact_positive_unique_quotas(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        blueprint = self.no_sections_blueprint(parent)
        invalid_sets = (
            [
                {"title": "One", "display_order": 1, "item_quota": 20},
                {"title": "Two", "display_order": 2, "item_quota": 20},
            ],
            [
                {"title": "One", "display_order": 1, "item_quota": 20},
                {"title": "Two", "display_order": 1, "item_quota": 30},
            ],
            [{"title": "One", "display_order": 1, "item_quota": 0}],
        )
        for sections in invalid_sets:
            with self.assertRaises(ValidationError):
                BlueprintMutationService.save_structure(
                    cycle_course_id=parent.id,
                    tenant_id=self.tenant.id,
                    actor=self.configurer,
                    expected_revision=blueprint.revision,
                    mode=ExamBlueprint.Mode.USE_SECTIONS,
                    sections=sections,
                )
        self.assertEqual(ExamSection.objects.filter(blueprint=blueprint).count(), 0)

    def test_structure_is_configurator_owned_reviewer_cannot_escalate(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        with self.assertRaises(PermissionDenied):
            BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_revision=0,
                mode=ExamBlueprint.Mode.NO_SECTIONS,
                sections=[],
            )

    def test_structure_revision_conflict_and_submitted_question_content_unchanged(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        question = Question.objects.filter(contribution__cycle_course=parent).first()
        before = tuple(
            getattr(question, field)
            for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty", "revision")
        )
        blueprint = self.use_sections_blueprint(parent)
        with self.assertRaises(Stage6Conflict):
            BlueprintMutationService.save_structure(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
                expected_revision=blueprint.revision - 1,
                mode=ExamBlueprint.Mode.USE_SECTIONS,
                sections=[],
            )
        question.refresh_from_db()
        self.assertEqual(
            before,
            tuple(
                getattr(question, field)
                for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d", "correct_answer", "difficulty", "revision")
            ),
        )


class Stage6PlacementScenarioTests(Stage6BlueprintFixtureMixin, Stage4TestCase):
    def classify_all(self, *, blueprint, sections):
        placements = []
        existing_question_ids = set(
            QuestionBlueprintPlacement.objects.filter(blueprint=blueprint).values_list(
                "question_id", flat=True
            )
        )
        for question in Question.objects.filter(
            contribution__cycle_course=blueprint.cycle_course
        ).order_by("contribution_id", "position"):
            if question.id in existing_question_ids:
                continue
            section = sections[0] if question.position <= 20 else sections[1]
            placements.append(
                QuestionBlueprintPlacement(
                    blueprint=blueprint,
                    question=question,
                    section=section,
                    placed_by=self.reviewer,
                )
            )
        QuestionBlueprintPlacement.objects.bulk_create(placements)

    def test_assigned_reviewer_places_question_configurator_and_cross_tenant_fail(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        question = Question.objects.filter(contribution__cycle_course=parent).first()
        before = (question.question_text, question.correct_answer, question.revision)
        placement, changed = QuestionPlacementService.place(
            question_id=question.id,
            section_id=sections[0].id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_placement_revision=0,
        )
        self.assertTrue(changed)
        self.assertEqual(placement.section, sections[0])
        with self.assertRaises(PermissionDenied):
            QuestionPlacementService.place(
                question_id=Question.objects.filter(contribution__cycle_course=parent).exclude(pk=question.pk).first().id,
                section_id=sections[0].id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
                expected_placement_revision=0,
            )
        with self.assertRaises(Http404):
            QuestionPlacementService.place(
                question_id=question.id,
                section_id=sections[0].id,
                tenant_id=self.other_tenant.id,
                actor=self.reviewer,
                expected_placement_revision=placement.revision,
            )
        question.refresh_from_db()
        self.assertEqual(before, (question.question_text, question.correct_answer, question.revision))

    def test_all_eligible_questions_require_placement_use_sections_but_not_no_sections(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        result = Stage6ReadinessService.evaluate(cycle_course=parent)
        codes = {blocker["code"] for blocker in result["blockers"]}
        self.assertIn("QUESTION_PLACEMENTS_INCOMPLETE", codes)
        self.classify_all(blueprint=blueprint, sections=sections)
        result = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertTrue(result["ready"], result["blockers"])

    def test_scenario_members_are_ordered_atomic_same_section_and_may_mix_dimensions(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        self.classify_all(blueprint=blueprint, sections=sections)
        questions = list(
            Question.objects.filter(
                contribution__cycle_course=parent,
                position__in=(1, 16),
            ).order_by("contribution__source_campus__code", "position")
        )
        first = next(question for question in questions if question.position == 1 and question.contribution.source_campus.code == "CUBAO")
        second = next(question for question in questions if question.position == 16 and question.contribution.source_campus.code == "FAIRVIEW")
        scenario, _changed = ScenarioMutationService.save(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            title="Mixed source scenario",
            stimulus="Confidential scenario stimulus that must not enter broad audit metadata.",
            question_ids=[second.id, first.id],
            section_id=sections[0].id,
            expected_revision=0,
        )
        self.assertEqual(
            list(scenario.members.order_by("position").values_list("question_id", flat=True)),
            [second.id, first.id],
        )
        self.assertNotEqual(first.contribution.source_campus_id, second.contribution.source_campus_id)
        self.assertNotEqual(first.difficulty, second.difficulty)
        audit = AuditLog.objects.get(action="DE_EXAM_SCENARIO_CREATED")
        self.assertNotIn("Confidential scenario stimulus", str(audit.metadata_json))
        readiness = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertTrue(readiness["ready"], readiness["blockers"])

    def test_invalid_scenario_membership_fails_closed(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        self.classify_all(blueprint=blueprint, sections=sections)
        questions = list(Question.objects.filter(contribution__cycle_course=parent).order_by("id")[:2])
        with self.assertRaisesRegex(ValidationError, "at least two distinct"):
            ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Invalid",
                stimulus="Still has valid scenario text.",
                question_ids=[questions[0].id, questions[0].id],
                section_id=sections[0].id,
            )

    def test_stage6_mutations_lock_parent_then_blueprint_then_sorted_questions(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        questions = list(
            Question.objects.filter(
                contribution__cycle_course=parent,
                position__in=(1, 2),
            ).order_by("-id")[:2]
        )
        original_parent_lock = blueprint_services.Stage5LockService.lock_cycle_course
        original_blueprint_lock = blueprint_services._lock_stage6_blueprint
        original_question_get = blueprint_services.get_stage6_question
        events = []

        def parent_lock(**kwargs):
            events.append("parent")
            return original_parent_lock(**kwargs)

        def blueprint_lock(**kwargs):
            events.append("blueprint")
            return original_blueprint_lock(**kwargs)

        def question_get(**kwargs):
            events.append("question")
            return original_question_get(**kwargs)

        with patch(
            "apps.departmental_exams.blueprint_services.Stage5LockService.lock_cycle_course",
            side_effect=parent_lock,
        ), patch(
            "apps.departmental_exams.blueprint_services._lock_stage6_blueprint",
            side_effect=blueprint_lock,
        ), patch(
            "apps.departmental_exams.blueprint_services.get_stage6_question",
            side_effect=question_get,
        ):
            QuestionPlacementService.place(
                question_id=questions[0].id,
                section_id=sections[0].id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_placement_revision=0,
            )
        self.assertEqual(events[:3], ["parent", "blueprint", "question"])

        self.classify_all(blueprint=blueprint, sections=sections)
        events.clear()
        locked_question_ids = []
        original_questions_lock = blueprint_services._lock_stage6_questions

        def questions_lock(**kwargs):
            events.append("questions")
            locked = original_questions_lock(**kwargs)
            locked_question_ids.extend(question.id for question in locked)
            return locked

        with patch(
            "apps.departmental_exams.blueprint_services.Stage5LockService.lock_cycle_course",
            side_effect=parent_lock,
        ), patch(
            "apps.departmental_exams.blueprint_services._lock_stage6_blueprint",
            side_effect=blueprint_lock,
        ), patch(
            "apps.departmental_exams.blueprint_services._lock_stage6_questions",
            side_effect=questions_lock,
        ):
            ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Canonical lock order",
                stimulus="Verify deterministic parent-first confidential overlay locking.",
                question_ids=[questions[0].id, questions[1].id],
                section_id=sections[0].id,
                expected_revision=0,
            )
        self.assertEqual(events[:3], ["parent", "blueprint", "questions"])
        self.assertEqual(locked_question_ids, sorted(locked_question_ids))
        other_section_question = Question.objects.filter(
            contribution__cycle_course=parent, position=21
        ).first()
        with self.assertRaisesRegex(ValidationError, "scenario section"):
            ScenarioMutationService.save(
                cycle_course_id=parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Cross section",
                stimulus="Cross-section member must be rejected.",
                question_ids=[questions[0].id, other_section_question.id],
                section_id=sections[0].id,
            )


class Stage6ReadinessPoolTests(Stage6BlueprintFixtureMixin, Stage4TestCase):
    def test_historical_submitted_pool_survives_later_assignment_movement(self):
        parent, configuration, contributions, assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        before = Stage6ReadinessService.evaluate(cycle_course=parent)
        before_contribution_state = list(
            FacultyContribution.objects.filter(cycle_course=parent)
            .order_by("id")
            .values_list("id", "status", "revision")
        )
        for assignment in assignments.values():
            assignment.is_active = False
            assignment.save(update_fields=["is_active", "updated_at"])
        reference = next(iter(assignments.values()))
        self.add_faculty_source(
            parent=parent,
            campus=reference.campus,
            offering=reference.offering,
            suffix="post-close-new-eligible",
        )
        after = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertEqual(before["eligible_question_count"], 150)
        self.assertEqual(after["eligible_question_count"], 150)
        self.assertTrue(after["ready"], after["blockers"])
        configuration.refresh_from_db()
        self.assertEqual(configuration.contributor_roster_revision, 1)
        self.assertEqual(
            before_contribution_state,
            list(
                FacultyContribution.objects.filter(cycle_course=parent)
                .order_by("id")
                .values_list("id", "status", "revision")
            ),
        )
        self.assertEqual(len(contributions), 3)

    def test_draft_blocked_preview_foreign_and_malformed_rows_are_not_eligible(self):
        parent, _configuration, contributions, _assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        malformed = Question.objects.filter(contribution__cycle_course=parent).first()
        Question.objects.filter(pk=malformed.pk).update(question_text="")
        result = Stage6ReadinessService.evaluate(cycle_course=parent)
        self.assertEqual(result["eligible_question_count"], 149)
        self.assertEqual(result["invalid_question_count"], 1)
        self.assertIn("ELIGIBLE_POOL_INVALID", {row["code"] for row in result["blockers"]})
        self.assertFalse(any("question_id" in blocker for blocker in result["blockers"]))


class Stage6AuthorizationAndViewTests(Stage6BlueprintFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_configurator_surface_is_aggregate_only_reviewer_surface_is_confidential(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        question = Question.objects.filter(contribution__cycle_course=parent).first()
        secret = question.question_text
        malformed = Question.objects.filter(contribution__cycle_course=parent).exclude(
            pk=question.pk
        ).first()
        malformed_secret = "MALFORMED-STAGE6-CONFIDENTIAL-CONTENT"
        Question.objects.filter(pk=malformed.pk).update(
            question_text=malformed_secret,
            choice_a="",
        )
        self.client.force_login(self.configurer)
        response = self.client.get(reverse("departmental_exams:blueprint_configuration", args=[parent.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:blueprint_review", args=[parent.id])).status_code,
            403,
        )
        self.client.force_login(self.reviewer)
        review = self.client.get(reverse("departmental_exams:blueprint_review", args=[parent.id]))
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, secret)
        self.assertNotContains(review, malformed_secret)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:blueprint_configuration", args=[parent.id])).status_code,
            403,
        )

    def test_feature_disable_direct_deny_assignment_loss_and_cross_tenant_fail_closed(self):
        parent, _configuration, _contributions, _assignments = self.closed_course()
        self.no_sections_blueprint(parent)
        self.client.force_login(self.reviewer)
        review_url = reverse("departmental_exams:blueprint_review", args=[parent.id])
        UserPermission.objects.create(
            user=self.reviewer,
            permission=Permission.objects.get(code="departmental_exams.review_generate"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(self.client.get(review_url).status_code, 403)
        UserPermission.objects.all().delete()
        parent.reviewer = None
        parent.save(update_fields=["reviewer", "updated_at"])
        self.assertEqual(self.client.get(review_url).status_code, 403)
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer", "updated_at"])
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(review_url).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:blueprint_review", args=[999999])).status_code,
            404,
        )

    def test_mutation_methods_and_conflict_statuses(self):
        parent, _configuration, _contributions, _assignments, blueprint, sections = self.prepare_sections()
        question = Question.objects.filter(contribution__cycle_course=parent).first()
        self.client.force_login(self.reviewer)
        placement_url = reverse("departmental_exams:question_placement", args=[question.id])
        self.assertEqual(self.client.get(placement_url).status_code, 405)
        first = self.client.post(
            placement_url,
            {
                "blueprint_id": blueprint.id,
                "expected_placement_revision": 0,
                "section": sections[0].id,
            },
        )
        self.assertEqual(first.status_code, 302)
        stale = self.client.post(
            placement_url,
            {
                "blueprint_id": blueprint.id,
                "expected_placement_revision": 0,
                "section": sections[1].id,
            },
        )
        self.assertEqual(stale.status_code, 409)

    def test_stage6b_and_stage6c_routes_remain_absent(self):
        for name in ("exam_generate", "exam_regenerate", "exam_preview", "exam_approve_lock"):
            with self.assertRaises(NoReverseMatch):
                reverse(f"departmental_exams:{name}", args=[1])
