from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class FacultyHelpGuideTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="FGUIDE", name="Faculty Guide School")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.user = User.objects.create_user(
            username="faculty_guide",
            password="testpass123",
            email="faculty_guide@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        permission, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        role = Role.objects.create(code="FACULTY", name="Faculty")
        RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        self.client.force_login(self.user)

    def test_revised_faculty_guide_explains_zero_blank_and_base_50(self):
        response = self.client.get(reverse("faculty_portal:guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/guide_role_based.html")
        self.assertContains(response, "A saved 0 is complete and counts in computation.")
        self.assertContains(response, "A blank score is missing and can block submission.")
        self.assertContains(response, "TeacherMate+ uses Raw Score Base-50, so raw 0 is transmuted to 50.")
        self.assertContains(response, "All faculty-created Participation/Output items are then averaged equally")
        self.assertNotContains(response, "Direct Percentage")
        self.assertContains(response, 'id="guide-assignments"', html=False)
        self.assertContains(response, 'id="guide-submission"', html=False)
        self.assertContains(response, 'id="guide-notes"', html=False)
        self.assertContains(response, 'id="guide-classlist"', html=False)
        self.assertContains(response, 'id="guide-exit-pulse"', html=False)
        self.assertContains(response, "confidential, identity-validated classroom feedback")
        self.assertContains(response, "there is no separate consent checkbox")
        self.assertContains(response, "not grading, attendance, faculty evaluation, or ranking")
        self.assertContains(response, "Review Dashboard")
        self.assertContains(response, "Open Class History")
        self.assertContains(response, "Compare Assignments")
        self.assertContains(response, "Privacy and Accountability")
        self.assertContains(response, "Troubleshoot Participation")
        self.assertContains(response, "excluded from the weighted response-rate denominator")
        self.assertContains(response, "Historical assignments cannot start new sessions")
        self.assertContains(response, "Back to Faculty Portal")
        self.assertContains(response, reverse("faculty_portal:dashboard"))
        self.assertContains(response, "Full Guide Manual")
        self.assertContains(response, reverse("faculty_portal:guide_manual"))
        self.assertContains(response, "Operational Policies")
        self.assertContains(response, reverse("faculty_portal:operational_policies"))
        self.assertContains(response, "Semester Faculty Workflow")
        self.assertContains(response, "Daily Faculty Workflow")
        self.assertContains(response, "separate Print Set A and Print Set B actions")
        self.assertContains(response, "inside its Print From/Print Until window")
        self.assertContains(response, "regenerated revision")
        self.assertContains(response, "new release")
        self.assertContains(response, "Letter as the default paper size")
        self.assertContains(response, "choose the paper size")
        self.assertContains(
            response,
            "Faculty do not receive Answer Keys, Question Selection Audit reports, or Automatic Generation Audit reports.",
        )
        self.assertContains(response, "portal-img/semester_workflow.png")
        self.assertContains(response, "imahe/faculty_workflow.png")
        self.assertNotContains(response, "Top Faculty Tasks")
        self.assertNotContains(response, "Start Here: Daily Faculty Workflow")
        self.assertContains(response, "From the left navigation bar, click My Classes.")
        self.assertContains(response, "Click the Grade Activities button from the periodic card.")
        self.assertContains(response, "If the date is in the future, expect a TeacherMate+ reminder email")
        self.assertContains(response, "From the Activities page, click the activity")
        self.assertContains(response, "Click the Attendance button.")
        self.assertContains(response, "Review Period Snapshot first")
        self.assertContains(response, "faculty_helpguide/1_myclasses.png")
        self.assertContains(response, "faculty_helpguide/1_dashboard.png")
        self.assertContains(response, "faculty_helpguide/2_activities.png")
        self.assertContains(response, "faculty_helpguide/2_encodescores.png")
        self.assertContains(response, "faculty_helpguide/2_attendance.png")
        self.assertContains(response, "faculty_helpguide/3_summary.png")
        self.assertContains(response, 'id="facultyHelpImageModal"', html=False)
        self.assertContains(response, 'id="facultyHelpAccordion"', html=False)
        self.assertContains(response, 'class="accordion-button"', html=False)
        self.assertContains(response, 'class="accordion-button collapsed"', html=False)
        self.assertContains(response, 'class="accordion-collapse collapse show"', html=False)
        self.assertContains(response, "table-responsive faculty-help-table-wrap")
        self.assertContains(response, "Component Average Trend")
        self.assertContains(response, "Which section has the most missing outputs?")
        self.assertContains(response, "Rule-based interpretation")

    def test_grouped_help_link_opens_faculty_quick_guide(self):
        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="faculty-utility-stack"', count=1, html=False)
        self.assertContains(response, 'class="faculty-utility-toggle"', count=1, html=False)
        self.assertContains(response, 'aria-controls="faculty-utility-actions"', count=1, html=False)
        self.assertContains(response, 'class="faculty-utility-actions"', count=1, html=False)
        self.assertContains(response, "Help &amp; Privacy", count=1, html=False)
        self.assertContains(response, 'class="help-fab"', count=1, html=False)
        self.assertContains(response, "<span>Quick Guide</span>", count=1, html=True)
        self.assertContains(response, 'data-tour-id="user-guide"', count=1, html=False)
        self.assertContains(response, f'href="{reverse("faculty_portal:guide")}"', html=False)
        self.assertContains(response, 'target="_blank"', html=False)
        self.assertContains(response, 'rel="noopener noreferrer"', html=False)
        self.assertContains(response, "Faculty Quick Guide")

    def test_full_faculty_manual_covers_current_gradebook_and_performance_workflows(self):
        response = self.client.get(reverse("faculty_portal:guide_manual"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/guide_manual.html")
        self.assertContains(response, "Gradebook Essentials")
        self.assertContains(response, "Blank Is Missing; Zero Is a Score")
        self.assertContains(response, "Participation/Output Detail Averaging")
        self.assertContains(response, "Every faculty-created Participation/Output item is averaged equally.")
        self.assertContains(response, "Component Weights Still Apply")
        self.assertNotContains(response, "Direct Percentage")
        self.assertNotContains(response, "Weighted Details")
        self.assertContains(response, "Check and Explain Grades")
        self.assertContains(response, "Class Performance and Student Consultation")
        self.assertContains(response, "Parallel Section Comparison")
        self.assertContains(response, "Request Gradebook Reopen")
        self.assertContains(response, reverse("faculty_portal:guide"))
        self.assertContains(response, reverse("faculty_portal:operational_policies"))

    def test_faculty_guide_can_restore_legacy_template(self):
        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        response = self.client.get(reverse("faculty_portal:guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/guide.html")
        self.assertContains(response, "All created Participation/Output items are averaged equally.")
        self.assertContains(response, "A blank score remains missing.")
        self.assertNotContains(response, "Direct Percentage")
        self.assertContains(response, reverse("faculty_portal:operational_policies"))

    def test_faculty_operational_policies_are_scannable_and_protected(self):
        response = self.client.get(reverse("faculty_portal:operational_policies"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/operational_policies.html")
        self.assertContains(response, "Faculty Operational Policies")
        self.assertContains(response, "Faculty must")
        self.assertContains(response, "Faculty must not")
        self.assertContains(response, "A saved 0 is a recorded score")
        self.assertContains(response, "final periodic grades are encoded separately in Pinnacle-AIMS")
        self.assertContains(response, "Pinnacle-AIMS remains the official source for enrollment information")
        self.assertContains(response, "does not replace the Registrar&#x27;s enrollment system", html=False)
        self.assertContains(response, "Institutional academic, registrar, privacy, and records-management policies remain controlling")

        self.client.logout()
        protected_response = self.client.get(reverse("faculty_portal:operational_policies"))
        self.assertNotEqual(protected_response.status_code, 200)
