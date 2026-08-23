import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.defaultfilters import date
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.auditlog.models import AuditLog

from .contribution_services import ContributionRosterService
from .models import CycleCourse, FacultyContribution
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class ContributorMonitoringPresentationTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("monitor-primary")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(
            cycle_course=self.parent,
            faculty_user=self.faculty,
        )
        self.url = reverse("departmental_exams:contributor_monitoring")

    def _make_second_visible_course(self):
        parent = self.make_course(cycle=self.parent.cycle, code="MONITOR-SECOND")
        self.make_configuration(
            parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        faculty = self.make_faculty("monitor-secondary")
        self.make_assignment(parent, faculty)
        self.initialize(parent)
        return parent, faculty

    def test_workflow_date_column_and_exempt_presentation(self):
        draft_faculty = self.make_faculty("monitor-draft")
        self.make_assignment(self.parent, draft_faculty)
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        submitted_at = timezone.now().replace(microsecond=0)
        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=submitted_at,
        )
        self.parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        self.parent.save(update_fields=["inclusion_status", "updated_at"])

        self.client.force_login(self.configurer)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        headers = [
            strip_tags(value).strip()
            for value in re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.DOTALL)
        ]
        self.assertEqual(
            headers,
            [
                "Contributor",
                "Campus",
                "Section",
                "Progress",
                "Date Submitted",
                "Workflow",
                "Roster",
                "Deadline",
                "Blocked Draft resolution",
            ],
        )
        self.assertContains(
            response,
            '<span class="badge text-bg-success">Submitted</span>',
            html=True,
        )
        self.assertContains(
            response,
            date(timezone.localtime(submitted_at), "M j, Y g:i A"),
        )
        self.assertContains(response, "Draft")
        self.assertNotContains(
            response,
            '<span class="badge text-bg-success">Draft</span>',
            html=True,
        )
        self.assertContains(response, "&mdash;")
        self.assertContains(
            response,
            '<span class="badge text-bg-warning">Exempt</span>',
            html=True,
        )
        self.assertNotContains(response, "Initialize roster")
        self.assertNotContains(response, "Synchronize roster")

    def test_contributor_filter_combines_hides_empty_courses_and_preserves_actions(self):
        second_parent, second_faculty = self._make_second_visible_course()
        filters = {
            "cycle": self.parent.cycle_id,
            "period": self.parent.cycle.exam_period,
            "course": self.parent.course_id,
            "contributor": self.faculty.id,
        }
        expected_query = (
            f"cycle={self.parent.cycle_id}&period={self.parent.cycle.exam_period}"
            f"&course={self.parent.course_id}&contributor={self.faculty.id}"
        )
        self.client.force_login(self.configurer)

        response = self.client.get(self.url, filters)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_contributor_id"], self.faculty.id)
        self.assertEqual(response.context["filter_query"], expected_query)
        self.assertEqual([course.id for course in response.context["courses"]], [self.parent.id])
        self.assertEqual(
            [item.faculty_user_id for item in response.context["courses"][0].monitoring_contributions],
            [self.faculty.id],
        )
        self.assertEqual(
            len({course.id for course in response.context["courses"]}),
            len(response.context["courses"]),
        )
        self.assertContains(response, self.faculty.username)
        self.assertNotIn(
            f'<h2 class="h5">{second_parent.course.code} —',
            response.content.decode(),
        )
        choice_ids = {item.id for item in response.context["contributor_choices"]}
        self.assertEqual(choice_ids, {self.faculty.id, second_faculty.id})
        self.assertContains(response, f"?{expected_query}".replace("&", "&amp;"))

        print_response = self.client.get(
            reverse("departmental_exams:contributor_monitoring_print"), filters
        )
        self.assertEqual([course.id for course in print_response.context["courses"]], [self.parent.id])
        self.assertEqual(print_response.context["total_authorized_courses"], 1)
        self.assertEqual(print_response.context["courses"][0].total_contributors, 1)
        print_headers = [
            strip_tags(value).strip()
            for value in re.findall(
                r"<th[^>]*>(.*?)</th>",
                print_response.content.decode(),
                flags=re.DOTALL,
            )
        ]
        self.assertEqual(print_headers, ["Contributor", "Campus", "Section", "Progress"])

        action_response = self.client.get(
            reverse(
                "departmental_exams:roster_action",
                args=[self.parent.id, "synchronize"],
            )
            + f"?{expected_query}"
        )
        self.assertEqual(action_response.status_code, 200)
        self.assertEqual(action_response.context["filter_query"], expected_query)

    def test_invalid_or_out_of_scope_contributor_never_broadens_results(self):
        wrong_campus = self.make_faculty(
            "monitor-wrong-campus",
            campus=self.other_campus,
            department=self.other_department,
        )
        wrong_tenant = get_user_model().objects.create_user(
            "monitor-wrong-tenant",
            "monitor-wrong-tenant@example.edu",
            "Pass123!",
            default_tenant=self.other_tenant,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.configurer)

        for contributor_id in (
            "not-an-id",
            "999999999",
            str(wrong_campus.id),
            str(wrong_tenant.id),
        ):
            with self.subTest(contributor_id=contributor_id):
                response = self.client.get(self.url, {"contributor": contributor_id})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["courses"], [])
                self.assertIsNone(response.context["selected_contributor_id"])
                self.assertEqual(
                    {item.id for item in response.context["contributor_choices"]},
                    {self.faculty.id},
                )
                self.assertContains(
                    response,
                    "No authorized course examinations match the selected filters.",
                )

    def test_exempt_initialize_and_synchronize_fail_without_mutation_or_success_audit(self):
        self.assertFalse(
            ContributionRosterService.synchronize(
                cycle_course_id=self.parent.id,
                tenant_id=self.tenant.id,
                actor=self.configurer,
            )["changed"]
        )
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.parent.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        self.parent.save(update_fields=["inclusion_status", "updated_at"])
        contribution_snapshot = FacultyContribution.objects.values(
            "roster_status", "roster_blocked_at", "revision"
        ).get(pk=self.contribution.pk)
        audit_count = AuditLog.objects.filter(
            action__in=(
                "DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED",
                "DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED",
            )
        ).count()

        for method in (
            ContributionRosterService.initialize,
            ContributionRosterService.synchronize,
        ):
            with self.subTest(method=method.__name__), self.assertRaisesMessage(
                ValidationError,
                "Only included course examinations may initialize or synchronize a contributor roster.",
            ):
                method(
                    cycle_course_id=self.parent.id,
                    tenant_id=self.tenant.id,
                    actor=self.configurer,
                )

        self.assertEqual(
            FacultyContribution.objects.values(
                "roster_status", "roster_blocked_at", "revision"
            ).get(pk=self.contribution.pk),
            contribution_snapshot,
        )
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.contributor_roster_revision, 1)
        self.assertEqual(
            AuditLog.objects.filter(
                action__in=(
                    "DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED",
                    "DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED",
                )
            ).count(),
            audit_count,
        )

        self.client.force_login(self.configurer)
        for action in ("initialize", "synchronize"):
            with self.subTest(direct_action=action):
                direct = self.client.post(
                    reverse(
                        "departmental_exams:roster_action",
                        args=[self.parent.id, action],
                    ),
                    {"confirm": True},
                )
                self.assertEqual(direct.status_code, 400)
                self.assertContains(
                    direct,
                    "Only included course examinations may initialize or synchronize a contributor roster.",
                    status_code=400,
                )
        self.assertEqual(
            AuditLog.objects.filter(
                action__in=(
                    "DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED",
                    "DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED",
                )
            ).count(),
            audit_count,
        )
