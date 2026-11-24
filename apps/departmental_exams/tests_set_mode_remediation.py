"""Scoped regressions for saved section sets and returned author evidence."""
from html.parser import HTMLParser

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section
from apps.enrollment.models import Enrollment
from apps.rbac.models import Permission
from apps.students.models import Student

from . import tests_set_modes as fixtures
from .automatic_workflow import AutomaticContributionReopenService
from .contribution_services import QuestionMutationService
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (CourseExamConfiguration, CycleCourseOffering, ExamGeneratorPolicy,
    FacultyContribution, FacultyContributionEligibilitySource, GeneratedExamItem,
    PersonalizedAnswerSheetAssignment, Question)
from .questionnaire_printing import QuestionnairePrintReleaseService
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


class Links(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.hrefs = []
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


class SavedSetOutputRemediationTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    policy_course = fixtures.SectionSetIntegrationTests.policy_course

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={
            "module": "faculty_portal", "action": "access", "description": "Faculty access"})

    def automatic_course(self, *, policy=True):
        if policy:
            course, _ = self.policy_course(shuffle=True)
        else:
            course, _ = self.ready_generation_course()
        course.cycle.processing_mode = "AUTOMATIC_GENERATION"
        course.cycle.save(update_fields=["processing_mode"])
        course.refresh_from_db()
        return course

    def generate(self, course):
        problem, report = Stage6ReadinessService.build_problem(cycle_course=course)
        self.assertTrue(report["ready"], report["blockers"])
        return ExamGenerationService.generate(cycle_course_id=course.id, tenant_id=self.tenant.id,
            actor=None, generation_trigger="AUTOMATIC", expected_current_revision=0,
            expected_input_fingerprint=problem.input_fingerprint,
            request_token="three-blocker-remediation-request-token").revision

    def test_three_section_admin_navigation_key_and_release_center_print(self):
        course = self.automatic_course()
        revision = self.generate(course)
        sets = list(revision.generated_sets.order_by("set_code"))
        self.assertEqual(len(sets), 3)
        client = Client()
        client.force_login(self.admin)
        manage = client.get(reverse("departmental_exams:assigned_course_examinations"))
        self.assertEqual(manage.status_code, 200)
        summary_url = reverse("departmental_exams:automatic_generation_summary", args=[course.cycle_id])
        # Follow the existing entry route when the Manage screen offers a cycle selector.
        entry_url = reverse("departmental_exams:automatic_generation_summary_entry")
        links = Links(manage).hrefs
        self.assertTrue(summary_url in links or entry_url in links)
        if summary_url not in links:
            entry = client.get(entry_url, follow=True)
            self.assertEqual(entry.status_code, 200)
            self.assertTrue(entry.redirect_chain or summary_url in Links(entry).hrefs)
        summary = client.get(summary_url)
        self.assertEqual(summary.status_code, 200)
        detail_url = reverse("departmental_exams:generated_revision_detail", args=[revision.id])
        self.assertIn(detail_url, Links(summary).hrefs)
        detail = client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        for saved_set in sets:
            key_url = reverse("departmental_exams:generation_answer_key", args=[revision.id, saved_set.set_code])
            self.assertIn(key_url, Links(detail).hrefs)
            key = client.get(key_url)
            self.assertEqual(key.status_code, 200)
            self.assertEqual(key.context["set_code"], saved_set.set_code)
            self.assertEqual([i["correct_answer_snapshot"] for i in key.context["items"]],
                list(saved_set.items.order_by("position").values_list("correct_answer_snapshot", flat=True)))
        center = client.get(reverse("departmental_exams:questionnaire_print_release"))
        self.assertEqual(center.status_code, 200)
        release_detail_url = reverse("departmental_exams:questionnaire_print_release_details", args=[course.id])
        release_detail = client.get(release_detail_url)
        self.assertEqual(release_detail.status_code, 200)
        for saved_set in sets:
            url = reverse("departmental_exams:admin_questionnaire_print", args=[revision.id, saved_set.set_code])
            self.assertIn(url, Links(release_detail).hrefs)
            paper = client.get(url)
            self.assertEqual(paper.status_code, 200)
            self.assertEqual(paper.context["set_code"], saved_set.set_code)
            self.assertEqual([i["question_text"] for i in paper.context["items"]],
                list(saved_set.items.order_by("position").values_list("question_text_snapshot", flat=True)))
            self.assertEqual([list(i["choices"]) for i in paper.context["items"]],
                list(saved_set.items.order_by("position").values_list("choices_snapshot", flat=True)))
            self.assertNotIn("correct_answer_snapshot", paper.context["items"][0])
        self.assertNotIn(reverse("departmental_exams:admin_questionnaire_print", args=[revision.id, "A"]), Links(release_detail).hrefs)
        client.force_login(self.manager)
        self.assertIn(client.get(release_detail_url).status_code, (403, 404))
        self.assertEqual(client.get(reverse("departmental_exams:admin_questionnaire_print", args=[revision.id, sets[2].set_code])).status_code, 403)

    def test_release_center_legacy_a_b_links_keep_exact_revision_output(self):
        course = self.automatic_course(policy=False)
        revision = self.generate(course)
        client = Client()
        client.force_login(self.admin)
        details = client.get(reverse("departmental_exams:questionnaire_print_release_details", args=[course.id]))
        self.assertEqual(details.status_code, 200)
        for code in ("A", "B"):
            url = reverse("departmental_exams:admin_questionnaire_print", args=[revision.id, code])
            self.assertIn(url, Links(details).hrefs)
            paper = client.get(url)
            self.assertEqual(paper.status_code, 200)
            self.assertEqual(paper.context["set_code"], code)
            self.assertEqual(len(paper.context["items"]), 50)

    def enroll(self, offering, number):
        student = Student.objects.create(tenant=self.tenant, campus=offering.campus,
            department=offering.department, program=offering.program, student_no=number,
            first_name="Saved", last_name=number)
        return Enrollment.objects.create(tenant=self.tenant, campus=offering.campus,
            academic_year=offering.academic_year, term=offering.term,
            student=student, course_offering=offering)

    def test_reactivated_unassigned_offering_does_not_block_assigned_sheets(self):
        course = self.automatic_course()
        contribution = FacultyContribution.objects.get(cycle_course=course, source_campus=self.campus)
        offering = course.offering_snapshots.get(campus=self.campus).offering
        section = Section.objects.create(tenant=self.tenant, campus=self.campus,
            department=offering.department, program=offering.program, code="LATER", name="Later section")
        later = CourseOffering.objects.create(tenant=self.tenant, campus=self.campus,
            department=offering.department, program=offering.program, course=offering.course,
            academic_year=offering.academic_year, term=offering.term, section=section, is_active=False)
        CycleCourseOffering.objects.create(cycle_course=course, offering=later, campus=self.campus)
        faculty = contribution.faculty_user
        assignment = FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus,
            offering=later, faculty_user=faculty, response_status="ACCEPTED", accepted_by=faculty,
            responded_at=timezone.now(), accepted_at=timezone.now())
        FacultyContributionEligibilitySource.objects.create(contribution=contribution, assignment=assignment,
            assignment_id_snapshot=assignment.id, offering_id_snapshot=later.id,
            tenant_id_snapshot=self.tenant.id, campus_id_snapshot=self.campus.id)
        revision = self.generate(course)
        self.assertEqual(revision.section_set_assignments.count(), 3)
        self.assertFalse(revision.section_set_assignments.filter(offering=later).exists())
        later.is_active = True
        later.save(update_fields=["is_active"])
        self.enroll(offering, "ASSIGNED")
        self.enroll(later, "UNASSIGNED")
        now = timezone.now()
        release = QuestionnairePrintReleaseService.release(cycle_course_id=course.id, revision_id=revision.id,
            tenant_id=self.tenant.id, actor=self.admin, target_campus_id=self.campus.id,
            print_from=now-timezone.timedelta(minutes=1), print_until=now+timezone.timedelta(hours=1))
        code = revision.section_set_assignments.get(offering=offering).generated_set.set_code
        client = Client()
        client.force_login(faculty)
        overview_url = reverse("departmental_exams:personalized_answer_sheet_overview", args=[contribution.id, release.id])
        overview = client.get(overview_url)
        self.assertEqual(overview.status_code, 200)
        rows = {r["offering"].id: r for r in overview.context["offering_rows"]}
        self.assertEqual(rows[offering.id]["section_set"], code)
        self.assertTrue(rows[later.id]["section_set_unavailable"])
        self.assertContains(overview, "No saved set for this section")
        prepare = lambda row: reverse("departmental_exams:personalized_answer_sheet_prepare", args=[contribution.id, release.id, row.id])
        paper = lambda row, set_filter="all": reverse("departmental_exams:personalized_answer_sheet_print", args=[contribution.id, release.id, row.id, set_filter])
        self.assertNotIn(prepare(later), overview.content.decode())
        self.assertEqual(client.post(prepare(offering)).status_code, 302)
        self.assertEqual(client.post(prepare(later)).status_code, 403)
        self.assertEqual(client.get(paper(later)).status_code, 403)
        self.assertFalse(PersonalizedAnswerSheetAssignment.objects.filter(generation_revision=revision, course_offering=later).exists())
        printed = client.get(paper(offering))
        self.assertEqual(printed.status_code, 200)
        self.assertEqual([s["set_code"] for s in printed.context["sheets"]], [code])
        other_code = revision.generated_sets.exclude(set_code=code).first().set_code
        self.assertEqual(client.get(paper(offering, other_code)).status_code, 403)
        again = client.get(overview_url)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(PersonalizedAnswerSheetAssignment.objects.get(generation_revision=revision, course_offering=offering).set_code, code)


class ReturnedChoiceEvidenceTests(fixtures.AutomaticCaseSectionSetTests):
    def test_return_and_resubmit_preserves_standalone_and_case_fixed_order(self):
        ExamGeneratorPolicy.objects.create(cycle=self.cycle, departmental=True, shuffle_choices=True)
        cases = self.fill_and_submit(count=45, submit=False)
        standalones = [self.add_question(section=self.section_b, text=f"Neutral element mass {i}") for i in range(5)]
        linked = cases[0].members.order_by("position").first().question
        fixed_ids = {linked.id, standalones[0].id}
        Question.objects.filter(contribution=self.contribution).update(choice_a="Hydrogen", choice_b="Helium",
            choice_c="Lithium", choice_d="Beryllium", correct_answer="B", choices_fixed_order=False)
        Question.objects.filter(pk__in=fixed_ids).update(choices_fixed_order=True)
        self.contribution.refresh_from_db()
        QuestionMutationService.submit(contribution_id=self.contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision)
        original = FacultyContribution.objects.get(pk=self.contribution.pk)
        expected_flags = dict(original.questions.values_list("position", "choices_fixed_order"))
        self.configuration.refresh_from_db()
        AutomaticContributionReopenService.reopen(cycle_course_id=self.parent.id, tenant_id=self.tenant.id,
            actor=self.admin, expected_revision=self.configuration.revision,
            new_deadline=timezone.now()+timezone.timedelta(days=1), reason="Preserve author choice ordering during correction.",
            selected_contribution_ids=[original.id])
        successor = FacultyContribution.objects.get(supersedes=original)
        self.assertEqual(dict(successor.questions.values_list("position", "choices_fixed_order")), expected_flags)
        self.assertEqual(dict(original.questions.values_list("position", "choices_fixed_order")), expected_flags)
        QuestionMutationService.submit(contribution_id=successor.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=successor.revision)
        revision = self.generate()
        copies = set(successor.questions.filter(choices_fixed_order=True).values_list("id", flat=True))
        items = list(GeneratedExamItem.objects.filter(generated_set__generation_revision=revision,
            source_question_id__in=copies))
        self.assertEqual(len(items), 2)
        self.assertTrue(any(i.scenario_id_snapshot for i in items))
        self.assertTrue(any(i.scenario_id_snapshot is None for i in items))
        for item in items:
            self.assertTrue(item.choices_fixed_order_snapshot)
            self.assertEqual(item.choice_order_snapshot, [0, 1, 2, 3])
            self.assertEqual(item.correct_answer_snapshot, "B")
            self.assertEqual(item.choices_snapshot, item.original_choices_snapshot)
        self.assertTrue(GeneratedExamItem.objects.filter(generated_set__generation_revision=revision,
            choices_fixed_order_snapshot=False).exclude(choice_order_snapshot=[0, 1, 2, 3]).exists())
