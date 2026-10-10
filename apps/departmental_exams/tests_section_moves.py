"""Synthetic section-move coverage; no persistent database or email required."""
import json
from html.parser import HTMLParser
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission

from .automatic_workflow import AutomaticContributionReopenService
from .blueprint_services import BlueprintMutationService
from .contribution_services import (
    ContributionConflict, ContributionSectionCountMismatch, ContributionSectionMovePolicy,
    QuestionMutationService, SectionMoveInvalid,
)
from .exam_units import ExamCourseEquivalencyService
from .faculty_case_services import FacultyCaseMutationService
from .models import (
    CourseExamConfiguration, CycleCourse, ExamBlueprint, ExamScenario, ExamScenarioMember,
    ExamSection, FacultyContribution, Question, QuestionBlueprintPlacement,
    QuestionIdentityReservation, _classification_service_scope, _exam_structure_lifecycle_service_scope,
    _final_item_count_propagation_service_scope,
)
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase
from .tests_faculty_cases import FacultyCaseFixtureMixin
from . import tests_submission_section_counts as count_fixtures


class SectionMoveTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.blueprint.refresh_from_db()

    def move(self, questions=(), cases=(), destination=None, **overrides):
        self.contribution.refresh_from_db()
        kwargs = dict(contribution_id=self.contribution.id, user=self.faculty,
                      tenant_id=self.tenant.id, campus_id=self.campus.id,
                      expected_contribution_revision=self.contribution.revision,
                      selected_questions=[(q.id, q.revision) for q in questions],
                      selected_cases=[self.case_selection(case) for case in cases],
                      destination_section_id=(destination or self.section_b).id)
        kwargs.update(overrides)
        return QuestionMutationService.move_many(**kwargs)

    def case_selection(self, case):
        case.refresh_from_db()
        return ContributionSectionMovePolicy.case_selection(
            case, list(case.members.select_related("question").order_by("position", "id")))

    def whole_case(self, section=None):
        case = self.save_case(section=section)
        first = self.add_question(scenario=case, section=section, text=f"Synthetic member one {case.id}")
        second = self.add_question(scenario=case, section=section, text=f"Synthetic member two {case.id}")
        case.refresh_from_db()
        return case, first, second

    def snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (
            FacultyContribution, Question, QuestionBlueprintPlacement, ExamScenario,
            ExamScenarioMember, QuestionIdentityReservation, AuditLog))

    def rejection(self, exception, **kwargs):
        before = self.snapshot()
        with self.assertRaises(exception):
            self.move(**kwargs)
        self.assertEqual(before, self.snapshot())

    def test_single_preserves_fields_positions_and_reconciles_once(self):
        question = self.add_question(text="Synthetic standalone")
        before = Question.objects.filter(pk=question.id).values().get()
        claims = list(QuestionIdentityReservation.objects.order_by("pk").values())
        self.contribution.refresh_from_db()
        revision = self.contribution.revision
        from . import duplicate_contract
        with patch.object(duplicate_contract, "reconcile", wraps=duplicate_contract.reconcile) as reconcile:
            result = self.move([question])
            self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(result["moved_count"], 1)
        after = Question.objects.filter(pk=question.id).values().get()
        self.assertEqual({k: v for k, v in before.items() if k not in ("revision", "updated_at")},
                         {k: v for k, v in after.items() if k not in ("revision", "updated_at")})
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, revision + 1)
        placement = QuestionBlueprintPlacement.objects.get(question=question)
        self.assertEqual((placement.section_id, placement.revision), (self.section_b.id, 2))
        self.assertEqual(claims, list(QuestionIdentityReservation.objects.order_by("pk").values()))
        audit = AuditLog.objects.filter(action="DE_EXAM_ITEMS_SECTION_MOVED").get()
        self.assertNotIn("Synthetic standalone", json.dumps(audit.metadata_json))
        self.assertNotIn("correct_answer", audit.metadata_json)

    def test_bulk_mixed_sources_and_same_section_noop(self):
        first = self.add_question(text="Synthetic first")
        second = self.add_question(section=self.section_b, text="Synthetic second")
        result = self.move([first, second])
        self.assertEqual((result["moved_count"], result["unchanged_count"]), (1, 1))
        first.refresh_from_db()
        before = self.snapshot()
        self.assertEqual(self.move([first, second])["moved_count"], 0)
        self.assertEqual(before, self.snapshot())

    def test_invalid_duplicate_missing_foreign_stale_question_ids(self):
        q = self.add_question()
        foreign = Question.objects.create(contribution=self.other_contribution, position=1,
                                          **{**self.payload("Synthetic foreign"), "correct_answer": "D", "difficulty": "EASY"})
        for pairs in ([(q.id, q.revision), (q.id, q.revision)], [(999999, 1)], [(foreign.id, 1)],
                      [(q.id, 999)], [(q.id, 0)], [("bad", 1)], [[q.id]], [(float(q.id), 1)], ["11"]):
            with self.subTest(pairs=pairs):
                self.rejection(ContributionConflict, selected_questions=pairs)

    def test_empty_and_invalid_destination_rejected(self):
        self.rejection(SectionMoveInvalid)
        q = self.add_question()
        for destination in ("bad", 0, 999999):
            self.rejection(SectionMoveInvalid, questions=[q], destination_section_id=destination)

    def test_unassigned_and_foreign_source_placement_rejected(self):
        q = self.add_question()
        QuestionBlueprintPlacement.objects.filter(question=q).delete()
        self.rejection(SectionMoveInvalid, questions=[q])
        other = self.make_course(cycle=self.cycle, code="FOREIGN-STRUCTURE")
        foreign_blueprint = ExamBlueprint.objects.create(cycle_course=other, mode="USE_SECTIONS", created_by=self.admin, updated_by=self.admin)
        foreign_section = ExamSection.objects.create(blueprint=foreign_blueprint, title="Foreign", display_order=1, item_quota=50)
        QuestionBlueprintPlacement.objects.create(question=q, blueprint=foreign_blueprint, section=foreign_section, placed_by=self.admin)
        self.rejection(SectionMoveInvalid, questions=[q])
        self.rejection(SectionMoveInvalid, questions=[q], destination_section_id=foreign_section.id)

    def test_whole_case_and_standalone_atomically_preserve_member_order(self):
        case, first, second = self.whole_case()
        standalone = self.add_question(text="Synthetic separate")
        before_members = list(case.members.order_by("position").values())
        before_case = ExamScenario.objects.filter(pk=case.id).values().get()
        before_questions = list(Question.objects.order_by("id").values())
        result = self.move([standalone], [case])
        self.assertEqual((result["moved_count"], result["case_count"]), (3, 1))
        case.refresh_from_db()
        self.assertEqual(case.section_id, self.section_b.id)
        self.assertEqual(case.revision, before_case["revision"] + 1)
        self.assertEqual(before_members, list(case.members.order_by("position").values()))
        for before, after in zip(before_questions, Question.objects.order_by("id").values()):
            self.assertEqual({k: v for k, v in before.items() if k not in ("revision", "updated_at")},
                             {k: v for k, v in after.items() if k not in ("revision", "updated_at")})
        after_case = ExamScenario.objects.filter(pk=case.id).values().get()
        self.assertEqual({k: v for k, v in before_case.items() if k not in ("section_id", "revision", "updated_by_id", "updated_at")},
                         {k: v for k, v in after_case.items() if k not in ("section_id", "revision", "updated_by_id", "updated_at")})
        self.assertEqual(set(QuestionBlueprintPlacement.objects.filter(question__in=[first, second]).values_list("section_id", flat=True)), {self.section_b.id})
        before = self.snapshot()
        self.assertEqual(self.move(cases=[case])["moved_count"], 0)
        self.assertEqual(before, self.snapshot())

    def test_individual_and_mixed_linked_selections_rejected(self):
        case, first, second = self.whole_case()
        standalone = self.add_question(text="Synthetic selectable")
        self.rejection(SectionMoveInvalid, questions=[first])
        self.rejection(SectionMoveInvalid, questions=[standalone, second])
        self.rejection(SectionMoveInvalid, questions=[first], cases=[case])

    def test_partial_stale_case_or_member_selection_rejected(self):
        case, first, second = self.whole_case()
        selection = self.case_selection(case)
        mutations = [{**selection, "members": selection["members"][:1]},
                     {**selection, "revision": selection["revision"] - 1},
                     {**selection, "members": [[row[0], row[1], row[2] + 1, row[3]] for row in selection["members"]]}]
        for supplied in mutations:
            self.rejection(ContributionConflict, selected_cases=[supplied])

    def test_foreign_admin_shared_and_retained_cases_cannot_move(self):
        case = self.save_case(contribution=self.other_contribution)
        self.rejection(SectionMoveInvalid, cases=[case])
        case, first, second = self.whole_case()
        ExamScenario.objects.filter(pk=case.id).update(contribution=None, created_by=self.reviewer, updated_by=self.reviewer)
        self.rejection(SectionMoveInvalid, cases=[case])
        self.rejection(SectionMoveInvalid, questions=[first])
        ExamScenario.objects.filter(pk=case.id).update(contribution=self.contribution, active_marker=None)
        self.rejection(SectionMoveInvalid, cases=[case])
        ExamScenarioMember.objects.filter(question=first).update(active_marker=None)
        self.rejection(SectionMoveInvalid, questions=[first])

    def test_invalid_graph_and_source_disagreement_rejected(self):
        case, first, second = self.whole_case()
        member = case.members.get(question=second)
        for updates in ({"position": 4}, {"active_marker": None}):
            ExamScenarioMember.objects.filter(pk=member.id).update(**updates)
            self.rejection(SectionMoveInvalid, cases=[case])
            ExamScenarioMember.objects.filter(pk=member.id).update(position=2, active_marker=1)
        QuestionBlueprintPlacement.objects.filter(question=second).update(section=self.section_b)
        self.rejection(SectionMoveInvalid, cases=[case])

    def test_foreign_member_graph_is_rejected_atomically(self):
        case, first, second = self.whole_case()
        Question.objects.filter(pk=second.id).update(contribution=self.other_contribution)
        self.rejection(SectionMoveInvalid, cases=[case])

    def test_transaction_rolls_back_if_reconciliation_fails(self):
        case, first, second = self.whole_case()
        before = self.snapshot()
        with patch("apps.departmental_exams.duplicate_contract.reconcile", side_effect=ValidationError("Synthetic conflict")):
            with self.assertRaises(ValidationError):
                self.move(cases=[case])
        self.assertEqual(before, self.snapshot())

    def test_omitted_case_id_edit_cannot_relocate_but_content_edit_works(self):
        case, first, second = self.whole_case()
        self.contribution.refresh_from_db()
        kwargs = dict(contribution_id=self.contribution.id, question_id=first.id, user=self.faculty,
                      tenant_id=self.tenant.id, campus_id=self.campus.id,
                      expected_contribution_revision=self.contribution.revision,
                      expected_question_revision=first.revision, payload=self.payload("Synthetic updated member"))
        before = self.snapshot()
        with self.assertRaisesMessage(ValidationError, "Individual Linked Questions"):
            QuestionMutationService.update(**kwargs, section_id=self.section_b.id)
        self.assertEqual(before, self.snapshot())
        QuestionMutationService.update(**kwargs, section_id=self.section_a.id)
        first.refresh_from_db()
        self.assertIn("Synthetic updated member", first.question_text)
        self.assertEqual(first.blueprint_placement.section_id, self.section_a.id)

    def test_owner_scope_permission_deadline_and_current_draft(self):
        q = self.add_question()
        for override in ({"user": self.other_faculty}, {"tenant_id": self.other_tenant.id}, {"campus_id": self.other_campus.id}):
            self.rejection((PermissionDenied, Http404), questions=[q], **override)
        permission = Permission.objects.get(code="faculty_portal.access")
        deny = UserPermission.objects.create(user=self.faculty, permission=permission, grant_type="DENY",
                                            tenant=self.tenant, campus=self.campus)
        self.rejection(PermissionDenied, questions=[q])
        deny.delete()
        for change, exception in (({"status": "SUBMITTED", "submitted_at": timezone.now()}, PermissionDenied),
                                  ({"active_marker": None}, Http404)):
            FacultyContribution.objects.filter(pk=self.contribution.id).update(**change)
            self.rejection(exception, questions=[q])
            FacultyContribution.objects.filter(pk=self.contribution.id).update(status="DRAFT", submitted_at=None, active_marker=1)
        CourseExamConfiguration.objects.filter(pk=self.configuration.id).update(contribution_deadline=timezone.now() - timezone.timedelta(seconds=1))
        self.rejection(PermissionDenied, questions=[q])

    def test_stale_contribution_and_successful_replay(self):
        q = self.add_question()
        self.contribution.refresh_from_db()
        revision = self.contribution.revision
        self.rejection(ContributionConflict, questions=[q], expected_contribution_revision=revision - 1)
        self.move([q])
        self.rejection(ContributionConflict, questions=[q], expected_contribution_revision=revision)

    def test_import_contention_unconfirmed_and_confirmed_provenance(self):
        q = self.add_question()
        batch = count_fixtures.SubmissionSectionCountTests._batch(self, "PAUSED")
        self.rejection(PermissionDenied, questions=[q])
        batch.status = "READY"
        batch.active_contribution = None
        batch.next_row_number = None
        batch.save(update_fields=["status", "active_contribution", "next_row_number"])
        Question.objects.filter(pk=q.id).update(import_batch=batch, import_row_number=1, entry_method="CSV")
        self.rejection(SectionMoveInvalid, questions=[q])
        batch.status = "CONFIRMED"
        batch.confirming_user = self.faculty
        batch.confirmed_at = timezone.now()
        batch.payload_purged_at = timezone.now()
        batch.committed_rows = batch.total_rows
        batch.save(update_fields=["status", "confirming_user", "confirmed_at", "payload_purged_at", "committed_rows"])
        q.refresh_from_db()
        self.move([q])
        q.refresh_from_db()
        self.assertEqual((q.import_batch_id, q.import_row_number, q.entry_method), (batch.id, 1, "CSV"))

    def test_duplicate_pool_guard_is_not_bypassed(self):
        q = self.add_question()
        with patch("apps.departmental_exams.duplicate_contract.require_clean_pool", side_effect=ValidationError("Synthetic duplicate")) as guard:
            self.rejection(ValidationError, questions=[q])
        self.assertEqual(guard.call_count, 1)

    def test_structure_availability_and_non_departmental_quota_60(self):
        q = self.add_question()
        FacultyContribution.objects.filter(pk=self.contribution.id).update(quota_snapshot=60)
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.parent.id).update(exam_classification="STANDARDIZED")
        self.assertEqual(self.move([q])["moved_count"], 1)
        q.refresh_from_db()
        original = self.blueprint.structure_frozen_at
        for change in ({"structure_frozen_at": None, "structure_frozen_by_id": None, "structure_final_item_count": None}, {"structure_final_item_count": 60}):
            with _exam_structure_lifecycle_service_scope():
                ExamBlueprint.objects.filter(pk=self.blueprint.id).update(**change)
            self.rejection(SectionMoveInvalid, questions=[q])
            with _exam_structure_lifecycle_service_scope():
                ExamBlueprint.objects.filter(pk=self.blueprint.id).update(structure_frozen_at=original, structure_frozen_by_id=self.blueprint.structure_frozen_by_id, structure_final_item_count=50)
        with _exam_structure_lifecycle_service_scope():
            ExamSection.objects.filter(pk=self.section_b.id).update(item_quota=19)
        self.rejection(SectionMoveInvalid, questions=[q])

    def test_one_or_no_sections_cannot_move(self):
        q = self.add_question()
        with _exam_structure_lifecycle_service_scope():
            self.section_b.delete()
            ExamSection.objects.filter(pk=self.section_a.id).update(item_quota=50)
        self.rejection(SectionMoveInvalid, questions=[q])
        QuestionBlueprintPlacement.objects.filter(question=q).delete()
        with _exam_structure_lifecycle_service_scope():
            self.section_a.delete()
            ExamBlueprint.objects.filter(pk=self.blueprint.id).update(mode="NO_SECTIONS")
        self.rejection(SectionMoveInvalid, questions=[q])

    def test_http_confirm_only_mutates_and_counts_refresh(self):
        case, first, second = self.whole_case()
        standalone = self.add_question(text="Synthetic HTTP standalone")
        url = reverse("departmental_exams:question_move", args=[self.contribution.id])
        self.contribution.refresh_from_db()
        before = self.snapshot()
        choose = self.client.post(url, {"expected_contribution_revision": self.contribution.revision,
                                      "selected_questions": [f"{standalone.id}:{standalone.revision}"],
                                      "selected_cases": [json.dumps(self.case_selection(case))]})
        self.assertEqual(choose.status_code, 200)
        self.assertEqual(before, self.snapshot())
        review_data = {**choose.context["form"].initial, "phase": "review", "destination_section_id": self.section_b.id}
        review = self.client.post(url, review_data)
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, "1 whole Case(s)")
        self.assertContains(review, "3 question(s)")
        self.assertEqual(before, self.snapshot())
        confirmed = {**review.context["form"].initial, "phase": "confirm"}
        response = self.client.post(url, confirmed)
        self.assertEqual(response.status_code, 302)
        workspace = self.client.get(response.url)
        groups = workspace.context["presentation_sections"]
        self.assertEqual([row["saved_question_count"] for row in groups], [0, 3])
        after = self.snapshot()
        self.assertEqual(self.client.post(url, confirmed).status_code, 409)
        self.assertEqual(after, self.snapshot())

    def test_http_tampering_missing_token_invalid_business_and_csrf(self):
        q = self.add_question()
        self.contribution.refresh_from_db()
        url = reverse("departmental_exams:question_move", args=[self.contribution.id])
        initial = dict(expected_contribution_revision=self.contribution.revision, selected_questions=[f"{q.id}:{q.revision}"])
        choose = self.client.post(url, initial)
        review = self.client.post(url, {**choose.context["form"].initial, "phase": "review", "destination_section_id": self.section_b.id})
        confirmed = {**review.context["form"].initial, "phase": "confirm"}
        for updates in ({"destination_section_id": self.section_a.id}, {"selected_questions": "[]"}, {"confirmation_token": ""}):
            before = self.snapshot()
            self.assertEqual(self.client.post(url, {**confirmed, **updates}).status_code, 409)
            self.assertEqual(before, self.snapshot())
        case, first, second = self.whole_case()
        self.contribution.refresh_from_db()
        response = self.client.post(url, {**initial, "expected_contribution_revision": self.contribution.revision,
                                         "selected_questions": [f"{first.id}:{first.revision}"]})
        self.assertContains(response, "Individual Case-linked questions cannot move", status_code=400)
        self.assertNotContains(response, "page state is missing or invalid", status_code=400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)
        self.assertEqual(csrf_client.post(url, initial).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_correction_whole_case_move_preserves_original_history(self):
        case, first, second = self.whole_case()
        self.contribution.refresh_from_db()
        self.contribution.status = "SUBMITTED"
        self.contribution.submitted_at = timezone.now()
        self.contribution.save(update_fields=["status", "submitted_at"])
        original = self.contribution
        successor = AutomaticContributionReopenService._draft_successor(
            original, actor=self.configurer, configuration=self.configuration,
            reason="Synthetic correction", now=timezone.now())
        def history():
            return (FacultyContribution.objects.filter(pk=original.id).values().get(),
                    list(original.questions.order_by("id").values()),
                    list(QuestionBlueprintPlacement.objects.filter(question__contribution=original).order_by("id").values()),
                    list(ExamScenario.objects.filter(contribution=original).values()),
                    list(ExamScenarioMember.objects.filter(scenario__contribution=original).order_by("id").values()))
        before = history()
        self.contribution = successor
        copied = ExamScenario.objects.get(contribution=successor)
        self.assertEqual(self.move(cases=[copied])["moved_count"], 2)
        self.assertEqual(before, history())

    def test_http_stale_case_and_member_confirmation_is_atomic(self):
        case, first, second = self.whole_case()
        url = reverse("departmental_exams:question_move", args=[self.contribution.id])
        def confirmation():
            self.contribution.refresh_from_db()
            choose = self.client.post(url, {"expected_contribution_revision": self.contribution.revision,
                                           "selected_cases": [json.dumps(self.case_selection(case))]})
            review = self.client.post(url, {**choose.context["form"].initial, "phase": "review",
                                           "destination_section_id": self.section_b.id})
            return {**review.context["form"].initial, "phase": "confirm"}
        approved = confirmation()
        ExamScenario.objects.filter(pk=case.id).update(revision=case.revision + 1)
        before = self.snapshot()
        self.assertEqual(self.client.post(url, approved).status_code, 409)
        self.assertEqual(before, self.snapshot())
        approved = confirmation()
        Question.objects.filter(pk=first.id).update(revision=first.revision + 1)
        before = self.snapshot()
        self.assertEqual(self.client.post(url, approved).status_code, 409)
        self.assertEqual(before, self.snapshot())

    def test_expired_confirmation_rejects_without_any_mutation(self):
        case, first, second = self.whole_case()
        standalone = self.add_question(text="Synthetic expiring standalone")
        self.contribution.refresh_from_db()
        url = reverse("departmental_exams:question_move", args=[self.contribution.id])
        before = self.snapshot()
        choose = self.client.post(url, {
            "expected_contribution_revision": self.contribution.revision,
            "selected_questions": [f"{standalone.id}:{standalone.revision}"],
            "selected_cases": [json.dumps(self.case_selection(case))],
        })
        self.assertEqual(choose.status_code, 200)
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() - 1801):
            review = self.client.post(url, {
                **choose.context["form"].initial, "phase": "review",
                "destination_section_id": self.section_b.id,
            })
        self.assertEqual(review.status_code, 200)
        response = self.client.post(url, {**review.context["form"].initial, "phase": "confirm"})
        self.assertContains(response, "Move confirmation expired", status_code=409)
        self.assertContains(response, "review a new move", status_code=409)
        self.assertEqual(before, self.snapshot())

    def test_http_permission_and_unrecognized_diagnostics_remain_protected(self):
        q = self.add_question()
        self.contribution.refresh_from_db()
        url = reverse("departmental_exams:question_move", args=[self.contribution.id])
        data = {"expected_contribution_revision": self.contribution.revision,
                "selected_questions": [f"{q.id}:{q.revision}"]}
        with patch.object(QuestionMutationService, "preview_move", side_effect=ValidationError("Private internal diagnostic")):
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "Private internal diagnostic", status_code=400)
        UserPermission.objects.create(user=self.faculty, permission=Permission.objects.get(code="faculty_portal.access"),
                                      grant_type="DENY", tenant=self.tenant, campus=self.campus)
        before = self.snapshot()
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(before, self.snapshot())

    def test_empty_case_and_duplicate_case_ids_rejected(self):
        case = self.save_case()
        self.rejection(SectionMoveInvalid, cases=[case])
        self.rejection(ContributionConflict, selected_cases=[self.case_selection(case)] * 2)

    def test_feature_disabled_and_invalid_section_titles_disable_move(self):
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        q = self.add_question()
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
                                 False, tenant_id=self.tenant.id, value_type="BOOL")
        self.rejection(SectionMoveInvalid, questions=[q])
        page = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertNotContains(page, "data-section-move-button")
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
                                 True, tenant_id=self.tenant.id, value_type="BOOL")
        with _exam_structure_lifecycle_service_scope():
            ExamSection.objects.filter(pk=self.section_b.id).update(title=" ")
        self.rejection(SectionMoveInvalid, questions=[q])

    def test_no_sections_linked_edit_keeps_implicit_structure_and_no_move_ui(self):
        cycle = self.make_cycle(status="OPEN", scope_suffix="MOVE-NO-SECTIONS", default_questions_required_per_faculty=50,
                                default_final_item_count=50, default_contribution_deadline=self.future_deadline())
        parent = self.make_course(cycle=cycle, code="MOVE-NOSEC")
        config = self.make_configuration(parent)
        self.make_assignment(parent, self.faculty)
        BlueprintMutationService.save_structure(cycle_course_id=parent.id, tenant_id=self.tenant.id,
                                               actor=self.admin, expected_revision=0, mode="NO_SECTIONS", sections=())
        CourseExamConfigurationService.open_for_contribution(cycle_course_id=parent.id, tenant_id=self.tenant.id,
                                                            user=self.admin, expected_revision=config.revision)
        self.contribution = FacultyContribution.objects.get(cycle_course=parent, faculty_user=self.faculty)
        case = FacultyCaseMutationService.save(contribution_id=self.contribution.id, user=self.faculty,
                                              tenant_id=self.tenant.id, campus_id=self.campus.id,
                                              expected_contribution_revision=self.contribution.revision,
                                              title="Synthetic implicit Case", raw_content="<p>Synthetic facts</p>")[0]
        self.contribution.refresh_from_db()
        question = QuestionMutationService.create(contribution_id=self.contribution.id, user=self.faculty,
                                                  tenant_id=self.tenant.id, campus_id=self.campus.id,
                                                  expected_contribution_revision=self.contribution.revision,
                                                  payload=self.payload("Synthetic implicit member"), scenario_id=case.id)
        self.contribution.refresh_from_db()
        QuestionMutationService.update(contribution_id=self.contribution.id, question_id=question.id, user=self.faculty,
                                       tenant_id=self.tenant.id, campus_id=self.campus.id,
                                       expected_contribution_revision=self.contribution.revision,
                                       expected_question_revision=question.revision, payload=self.payload("Synthetic implicit edited"))
        self.assertFalse(QuestionBlueprintPlacement.objects.filter(question=question).exists())
        self.assertEqual(question.exam_scenario_membership.scenario_id, case.id)
        page = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(page, "<strong>1 / 50</strong> saved / configured", html=True)
        self.assertNotContains(page, "data-section-move-button")
        self.rejection(SectionMoveInvalid, cases=[case])


class DepartmentalMoveBoundaryTests(FacultyCaseFixtureMixin, Stage4TestCase):
    move = SectionMoveTests.move
    case_selection = SectionMoveTests.case_selection
    whole_case = SectionMoveTests.whole_case
    snapshot = SectionMoveTests.snapshot
    _fill = count_fixtures.SubmissionSectionCountTests._fill
    _submit = count_fixtures.SubmissionSectionCountTests._submit
    _structure = count_fixtures.SubmissionSectionCountTests._structure
    _departmental_course = count_fixtures.SubmissionSectionCountTests._departmental_course

    def setUp(self):
        super().setUp()
        self.blueprint.refresh_from_db()
        self.cycle.processing_mode = "AUTOMATIC_GENERATION"
        self.cycle.save(update_fields=["processing_mode"])
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.parent.id).update(exam_classification="DEPARTMENTAL")
        self.parent.refresh_from_db()
        with _exam_structure_lifecycle_service_scope():
            ExamSection.objects.filter(pk=self.section_a.id).update(item_quota=10)
            ExamSection.objects.filter(pk=self.section_b.id).update(item_quota=40)
        self.first, self.second = self.section_a, self.section_b

    def test_move_repairs_counts_without_changing_submission_guard(self):
        questions = self._fill(0, 50)
        with self.assertRaises(ContributionSectionCountMismatch):
            self._submit()
        self.assertEqual(self.move(questions[:10], destination=self.first)["moved_count"], 10)
        self.assertEqual(self.contribution.status, "DRAFT")
        self.assertTrue(self._submit()[1])

    def test_equivalent_secondary_uses_only_primary_sections(self):
        cycle = self.make_cycle(status="OPEN", scope_suffix="MOVE-UNIT", default_questions_required_per_faculty=50,
                                default_final_item_count=50, default_contribution_deadline=self.future_deadline())
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.save(update_fields=["processing_mode"])
        primary = self._departmental_course(cycle, "MOVE-PRIMARY")
        secondary = self._departmental_course(cycle, "MOVE-SECONDARY")
        configs = [self.make_configuration(course, deadline=cycle.default_contribution_deadline) for course in (primary, secondary)]
        self.make_assignment(secondary, self.faculty)
        ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Synthetic move unit", actor=self.admin,
                                                primary_cycle_course_id=primary.id, member_ids=[primary.id, secondary.id])
        blueprint = self._structure(primary)
        CourseExamConfigurationService.open_for_contribution(cycle_course_id=secondary.id, tenant_id=self.tenant.id,
                                                            user=self.admin, expected_revision=configs[1].revision)
        self.contribution = FacultyContribution.objects.get(cycle_course=secondary, faculty_user=self.faculty)
        first, second = blueprint.sections.order_by("display_order", "id")
        questions = self._fill(0, 2, sections=(first, second))
        self.assertEqual(self.move(questions, destination=first)["moved_count"], 2)
        self.assertEqual(set(QuestionBlueprintPlacement.objects.filter(question__in=questions).values_list("section_id", flat=True)), {first.id})
        before = self.snapshot()
        with self.assertRaises(SectionMoveInvalid):
            self.move(questions, destination=self.section_a)
        self.assertEqual(before, self.snapshot())
        with _final_item_count_propagation_service_scope():
            CourseExamConfiguration.objects.filter(pk=configs[1].id).update(final_item_count=60)
        from .duplicate_contract import LegacyPoolConflict
        with self.assertRaises(LegacyPoolConflict):
            self.move(questions, destination=first)
        with _final_item_count_propagation_service_scope():
            CourseExamConfiguration.objects.filter(pk=configs[1].id).update(final_item_count=50)
        ExamBlueprint.objects.create(cycle_course=secondary, mode="USE_SECTIONS", created_by=self.admin, updated_by=self.admin)
        with self.assertRaises(SectionMoveInvalid):
            self.move(questions, destination=first)

    def test_automatic_whole_case_move_preserves_duplicate_claims(self):
        case, first, second = self.whole_case()
        claims = list(QuestionIdentityReservation.objects.order_by("pk").values())
        self.assertEqual(self.move(cases=[case])["moved_count"], 2)
        self.assertEqual(claims, list(QuestionIdentityReservation.objects.order_by("pk").values()))

    def test_automatic_standardized_sections_do_not_depend_on_case_authoring(self):
        questions = self._fill(1, 0)
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.parent.id).update(exam_classification="STANDARDIZED")
        FacultyContribution.objects.filter(pk=self.contribution.id).update(quota_snapshot=60)
        page = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(page, "data-section-move-button")
        self.assertFalse(page.context["case_authoring_enabled"])
        self.assertEqual(self.move(questions)["moved_count"], 1)

    def test_automatic_standardized_preserves_global_reorder_round_trip(self):
        questions = self._fill(2, 1)
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.parent.id).update(exam_classification="STANDARDIZED")
        url = reverse("departmental_exams:contribution_workspace", args=[self.contribution.id])
        page = self.client.get(url)
        self.assertContains(page, "Move up", count=3)
        self.assertContains(page, "Move down", count=3)
        self.assertContains(page, "Save displayed order", count=1)
        self.assertContains(page, "data-section-move-button")
        self.assertFalse(page.context["section_presentation_enabled"])

        class GlobalQuestionList(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.in_list = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id") == "question-list":
                    self.in_list = True
                if self.in_list and "question-card" in attrs.get("class", "").split():
                    self.ids.append(int(attrs["data-question-id"]))

        rendered = GlobalQuestionList()
        rendered.feed(page.content.decode())
        self.assertEqual(rendered.ids, [question.id for question in questions])
        placements = list(QuestionBlueprintPlacement.objects.filter(
            question__in=questions).order_by("question_id").values())
        ordered = [rendered.ids[2], rendered.ids[0], rendered.ids[1]]
        self.contribution.refresh_from_db()
        revision = self.contribution.revision
        saved = self.client.post(reverse("departmental_exams:question_reorder", args=[self.contribution.id]), {
            "expected_contribution_revision": revision,
            "ordered_question_ids": ",".join(map(str, ordered)),
        })
        self.assertEqual(saved.status_code, 302)
        refreshed = self.client.get(saved.url)
        self.assertEqual([question.id for question in refreshed.context["questions"]], ordered)
        self.assertEqual(list(Question.objects.filter(contribution=self.contribution).order_by(
            "position").values_list("id", "position")), list(zip(ordered, [1, 2, 3])))
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.revision, revision + 1)
        self.assertEqual(placements, list(QuestionBlueprintPlacement.objects.filter(
            question__in=questions).order_by("question_id").values()))
        self.assertContains(refreshed, "Save displayed order")
        self.assertContains(refreshed, "data-section-move-button")

    def test_authorized_automatic_correction_case_move_keeps_history_and_guard(self):
        case, first, second = self.whole_case()
        self._fill(8, 40)
        original = self._submit()[0]
        AutomaticContributionReopenService.reopen(
            cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin,
            expected_revision=self.configuration.revision, new_deadline=self.future_deadline(),
            reason="Synthetic whole Case section correction.", selected_contribution_ids=[original.id])
        successor = FacultyContribution.objects.get(supersedes=original)
        def history():
            return (FacultyContribution.objects.filter(pk=original.id).values().get(),
                    list(original.questions.order_by("id").values()),
                    list(QuestionBlueprintPlacement.objects.filter(question__contribution=original).order_by("id").values()),
                    list(ExamScenario.objects.filter(contribution=original).values()),
                    list(ExamScenarioMember.objects.filter(scenario__contribution=original).order_by("id").values()))
        before = history()
        self.contribution = successor
        copied = ExamScenario.objects.get(contribution=successor)
        self.assertEqual(self.move(cases=[copied])["moved_count"], 2)
        with self.assertRaises(ContributionSectionCountMismatch):
            self._submit()
        self.assertEqual(self.move(cases=[copied], destination=self.first)["moved_count"], 2)
        self.assertTrue(self._submit()[1])
        self.assertEqual(before, history())
