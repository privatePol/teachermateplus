from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_portal.help_guide import build_admin_help_sections
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class AdminHelpGuideTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="GUIDE", name="Guide School")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.portal_permission = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.dashboard_permission = Permission.objects.create(
            code="dashboard.read",
            module="dashboard",
            action="read",
        )
        self.course_permission = Permission.objects.create(
            code="courses.read",
            module="courses",
            action="read",
        )
        self.reset_permission = Permission.objects.create(
            code="actual_data_reset.run",
            module="actual_data_reset",
            action="run",
        )
        self.grading_template_permission = Permission.objects.create(
            code="grading_templates.read",
            module="grading_templates",
            action="read",
        )
        self.course_template_assignment_permission = Permission.objects.create(
            code="course_template_assignments.read",
            module="course_template_assignments",
            action="read",
        )
        self.hotfix_permission = Permission.objects.create(
            code="template_hotfixes.read",
            module="template_hotfixes",
            action="read",
        )
        self.departmental_exam_configure_permission, _ = Permission.objects.get_or_create(
            code="departmental_exams.configure",
            defaults={"module": "departmental_exams", "action": "configure"},
        )
        self.planning_readiness_permission, _ = Permission.objects.get_or_create(
            code="departmental_exams.view_planning_readiness",
            defaults={"module": "departmental_exams", "action": "view_planning_readiness"},
        )

    def _make_user(self, *, username, role_code, permissions):
        user = User.objects.create_user(
            username=username,
            password="testpass123",
            email=f"{username}@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code=role_code, name=role_code.replace("_", " ").title())
        for permission in permissions:
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        return user

    def test_shared_admin_footer_renders_guide_link_once(self):
        user = self._make_user(
            username="admin_footer_guide",
            role_code="CAMPUS_ADMIN",
            permissions=[self.portal_permission, self.dashboard_permission, self.course_permission],
        )
        self.client.force_login(user)

        for url in (reverse("admin_portal:dashboard"), reverse("admin_portal:course_list")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="admin-utility-footer"', count=1, html=False)
                self.assertContains(response, 'id="admin-practical-guide-link"', count=1, html=False)
                self.assertContains(response, f'href="{reverse("admin_portal:guide")}"', html=False)
                self.assertContains(response, "<span>Admin Practical Guide</span>", count=1, html=True)
                self.assertNotContains(response, "admin-help-fab")

    def test_campus_admin_does_not_receive_superadmin_help(self):
        user = self._make_user(
            username="campus_admin_guide",
            role_code="CAMPUS_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
                self.course_permission,
                self.reset_permission,
            ],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        section_codes = {section["code"] for section in sections}
        rendered_text = " ".join(
            topic["title"] for section in sections for topic in section["topics"]
        )

        self.assertIn("academic-setup", section_codes)
        self.assertNotIn("superadmin", section_codes)
        self.assertNotIn("Tenants, Roles, Permissions, Menus, and High-Risk Tools", rendered_text)

    def test_superadmin_role_receives_sensitive_help(self):
        user = self._make_user(
            username="superadmin_guide",
            role_code="SUPER_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
                self.reset_permission,
            ],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )

        self.assertIn("superadmin", {section["code"] for section in sections})

    def test_departmental_exam_configurer_receives_stage41_help(self):
        user = self._make_user(
            username="departmental_exam_guide",
            role_code="DEPARTMENTAL_EXAM_CONFIGURER",
            permissions=[
                self.portal_permission,
                self.departmental_exam_configure_permission,
            ],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        section = next(
            row for row in sections if row["code"] == "departmental-exam-builder"
        )
        self.assertEqual(
            section["topics"][0]["code"],
            "departmental-exam-course-control",
        )
        topic = section["topics"][0]
        self.assertIn("configuration alone does not block Exempt", topic["steps"][4])
        self.assertIn("Exempt-only faculty contribution or question blocker", topic["steps"][5])
        self.assertIn("blocks Exempt only", topic["check_first"][1])
        self.assertIn("not a Restore blocker", topic["actions"][1]["avoid"])
        self.assertIn("No downstream data is deleted", topic["actions"][1]["editable"])
        self.assertIn("shared across its listed campuses", topic["steps"][1])
        self.assertIn("cycle-wide contribution deadline", topic["steps"][6])
        self.assertIn("NOT CONFIGURED", topic["steps"][7])
        self.client.force_login(user)
        role_response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(role_response.status_code, 200)
        self.assertTemplateUsed(role_response, "admin_portal/guide_role_based.html")
        self.assertContains(role_response, "New cycle courses start Included")
        response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin_portal/guide.html")
        self.assertContains(response, "Restore to Included")
        self.assertContains(response, "saved exam configuration is preserved")
        self.assertContains(response, "configuration alone does not block Exempt")
        self.assertContains(response, "blocks choosing Exempt")
        self.assertContains(response, "does not use the Exempt-only contribution/question blocker")
        self.assertContains(response, "No downstream data is deleted")
        self.assertContains(response, "cycle-wide contribution deadline")
        self.assertContains(response, "NOT CONFIGURED")

    def test_departmental_exam_output_guides_cover_release_and_audit_operations(self):
        output_permission, _ = Permission.objects.get_or_create(
            code="departmental_exams.manage_exam_generation",
            defaults={"module": "departmental_exams", "action": "manage_exam_generation"},
        )
        answer_key_release_permission, _ = Permission.objects.get_or_create(
            code="departmental_exams.release_answer_keys",
            defaults={"module": "departmental_exams", "action": "release_answer_keys"},
        )
        user = self._make_user(
            username="departmental_exam_output_guide",
            role_code="DEPARTMENTAL_EXAM_OUTPUT_GUIDE",
            permissions=[self.portal_permission, output_permission, answer_key_release_permission],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        topic = next(
            topic
            for section in sections
            for topic in section["topics"]
            if topic["code"] == "departmental-exam-confidential-output"
        )
        guide_text = " ".join(topic["steps"])
        self.assertIn("Bulk Print Release", guide_text)
        self.assertIn("current Generated revision", guide_text)
        self.assertIn("Select All", guide_text)
        self.assertIn("displayed-record badge", guide_text)
        self.assertIn("Admin Direct Print", guide_text)
        self.assertIn("Letter (default), A4, or Legal", guide_text)
        self.assertIn("scientific notation", guide_text)
        self.assertIn("escaped immutable snapshots", guide_text)
        self.assertIn("Submitted Questions", guide_text)
        self.assertIn("Duplicate/Equivalent Copies", guide_text)
        self.assertIn("Source (Q### r#)", guide_text)
        self.assertIn("Selected representative (EQ-###)", guide_text)
        self.assertIn("not AI judgment", guide_text)
        self.assertIn("legacy source snapshot", guide_text)
        self.assertIn("all examination sessions", guide_text)
        self.assertIn("newer revision requires its own explicit release", guide_text)

        self.client.force_login(user)
        practical = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(practical.status_code, 200)
        self.assertContains(practical, "Questionnaire Output, Answer Keys, and Generation Audits")
        self.assertContains(practical, "primary-owned current Generated revision")
        self.assertContains(practical, "Select All")
        self.assertContains(practical, "displayed-record badge")
        self.assertContains(practical, "Letter (default), A4, or Legal")
        self.assertContains(practical, "scientific notation")
        self.assertContains(practical, "Equivalent copy not selected (EQ-###)")
        self.assertContains(practical, "PASS, WARNING, or FAIL")
        self.assertContains(practical, "Faculty Answer Key Release")
        self.assertContains(practical, "all examination sessions for the grouped course have concluded")
        full = self.client.get(reverse("admin_portal:guide"), {"view": "full"})
        self.assertEqual(full.status_code, 200)
        self.assertContains(full, "Bulk Print Release")
        self.assertContains(full, "Selected Unique")
        self.assertContains(full, "not a raw hash")
        self.assertContains(full, "not AI judgment")
        self.assertContains(full, "default to Letter paper and also support A4 and Legal")
        self.assertContains(full, "Supported scientific notation")

    def test_planning_readiness_help_is_read_only_and_view_permission_scoped(self):
        user = self._make_user(
            username="planning_readiness_guide",
            role_code="PLANNING_READINESS_GUIDE",
            permissions=[self.portal_permission, self.planning_readiness_permission],
        )
        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        topic = next(
            topic
            for section in sections
            for topic in section["topics"]
            if topic["code"] == "departmental-exam-planning-readiness"
        )
        topic_text = " ".join(topic["steps"])
        self.assertIn("Course.exam_department", topic_text)
        self.assertIn("both view_planning_readiness and print_planning_readiness", topic_text)
        self.assertEqual(topic["permissions"], ["departmental_exams.view_planning_readiness"])
        self.assertIn("read-only", topic["actions"][0]["editable"])

        self.client.force_login(user)
        response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Planning &amp; Readiness")
        self.assertContains(response, "unassigned courses appear only with global department scope")

    def test_admin_guide_can_restore_legacy_template(self):
        user = User.objects.create_superuser(
            username="guide_root",
            password="testpass123",
            email="guide_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        revised_response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(revised_response.status_code, 200)
        self.assertTemplateUsed(revised_response, "admin_portal/guide_role_based.html")

        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        legacy_response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(legacy_response.status_code, 200)
        self.assertTemplateUsed(legacy_response, "admin_portal/guide.html")

    def test_practical_guide_links_to_full_guide_and_back(self):
        user = User.objects.create_superuser(
            username="guide_link_root",
            password="testpass123",
            email="guide_link_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        practical_response = self.client.get(reverse("admin_portal:guide"))
        self.assertTemplateUsed(practical_response, "admin_portal/guide_role_based.html")
        self.assertContains(practical_response, "Open Full Admin Guide")
        self.assertContains(practical_response, "?view=full")

        full_response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})
        self.assertTemplateUsed(full_response, "admin_portal/guide.html")
        self.assertContains(full_response, "Back to Admin Practical Guide")
        self.assertContains(full_response, "?view=practical")

    def test_explicit_practical_view_works_when_legacy_is_tenant_default(self):
        user = User.objects.create_superuser(
            username="guide_practical_override_root",
            password="testpass123",
            email="guide_practical_override_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)
        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        response = self.client.get(reverse("admin_portal:guide"), {"view": "practical"})

        self.assertTemplateUsed(response, "admin_portal/guide_role_based.html")
        self.assertContains(response, "Admin Portal Practical Guide")
        self.assertContains(response, "Open Full Admin Guide")

    def test_full_guide_keeps_superadmin_incident_section_hidden_from_campus_admin(self):
        user = self._make_user(
            username="campus_admin_full_guide",
            role_code="CAMPUS_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})

        self.assertTemplateUsed(response, "admin_portal/guide.html")
        self.assertNotContains(response, "14. Production Incident Response")
        self.assertNotContains(response, 'href="#incident-response"', html=False)

    def test_revised_admin_guide_preserves_existing_deep_link_anchors(self):
        user = User.objects.create_superuser(
            username="guide_anchor_root",
            password="testpass123",
            email="guide_anchor_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, 'id="grading-template-calculator"', html=False)
        self.assertContains(response, 'id="assignment-acceptance"', html=False)

    def test_practical_guide_uses_accessible_accordion_and_keeps_overview_visible(self):
        user = User.objects.create_superuser(
            username="guide_accordion_root",
            password="testpass123",
            email="guide_accordion_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, "Admin Portal Practical Guide")
        self.assertNotContains(response, "Admin Portal Help Guide")
        self.assertContains(response, "How to Use This Practical Guide")
        self.assertContains(response, 'id="adminPracticalGuideAccordion"', html=False)
        self.assertContains(response, 'id="help-collapse-start"', html=False)
        self.assertContains(response, 'class="accordion-collapse collapse show"', html=False)
        self.assertContains(response, 'data-bs-target="#help-collapse-grading-setup"', html=False)
        self.assertContains(response, 'aria-controls="help-collapse-grading-setup"', html=False)
        self.assertContains(response, 'data-help-collapse="#help-collapse-grading-setup"', html=False)
        self.assertContains(response, "table-responsive help-table-wrap")
        self.assertContains(response, "bootstrap.Collapse.getOrCreateInstance")

    def test_grading_template_help_names_exact_menu_and_builder_steps(self):
        user = self._make_user(
            username="grading_guide_admin",
            role_code="GRADING_ADMIN",
            permissions=[
                self.portal_permission,
                self.grading_template_permission,
                self.course_template_assignment_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, "Admin Portal -&gt; Grading -&gt; Grading Templates")
        self.assertContains(response, "Click the Builder icon on the template row.")
        self.assertContains(response, "Detail Computation to Average Activities")
        self.assertContains(response, "For the normal regular template, leave Effective term blank")
        self.assertContains(response, "After Summer, the course automatically uses the blank/default regular template again")
        self.assertNotContains(response, "Do not confuse Direct Percentage")

    def test_admin_guides_link_to_dedicated_grading_setup_guide(self):
        user = User.objects.create_superuser(
            username="grading_setup_link_root",
            password="testpass123",
            email="grading_setup_link_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)
        setup_url = reverse("admin_portal:grading_setup_guide")

        practical_response = self.client.get(reverse("admin_portal:guide"))
        self.assertContains(practical_response, setup_url)
        self.assertContains(practical_response, "Grading Template Setup Guide")

        full_response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})
        self.assertContains(full_response, setup_url)
        self.assertContains(full_response, "Build a Template")

    def test_grading_setup_guide_explains_template_profile_and_override_decisions(self):
        user = self._make_user(
            username="grading_setup_reader",
            role_code="GRADING_SETUP_READER",
            permissions=[
                self.portal_permission,
                self.grading_template_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:grading_setup_guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin_portal/grading/grading_setup_guide.html")
        self.assertContains(response, "Template")
        self.assertContains(response, "Period")
        self.assertContains(response, "Component")
        self.assertContains(response, "Subcomponent")
        self.assertContains(response, "Detail")
        self.assertContains(response, "Raw Score (Base-50)")
        self.assertContains(response, "Direct Percentage")
        self.assertContains(response, "For a 50-point quiz, faculty enters 42 out of 50")
        self.assertContains(response, "TeacherMate+ does not use the detail percentages")
        self.assertContains(response, "Participation/Output subcomponent percentage still matters")
        self.assertContains(response, "One per template?")
        self.assertContains(response, "No. One broad profile may serve many courses")
        self.assertContains(
            response,
            "controls how period grades are combined to compute the official final grade",
        )
        self.assertContains(response, "If no profile matches")
        self.assertContains(response, "averages every active grading period")
        self.assertContains(response, "Average All Active Template Periods")
        self.assertContains(response, "Simple Regular and Summer assignment rule")
        self.assertContains(response, "Regular LA Template")
        self.assertContains(response, "LA Summer Template")
        self.assertContains(response, "After Summer, the course automatically goes back to the blank/default regular template")
        self.assertContains(response, "You do not need to reassign the regular template again for 1st semester")
        self.assertContains(response, "at least one active Participation/Output activity")
        self.assertContains(response, "Unused or inactive detail rows will not block submission")
        self.assertContains(response, "existing strict checks for the weighted setup still apply")
        self.assertContains(
            response,
            "One approved course must use Base-40",
        )
        self.assertNotContains(response, "governed fallback")
        self.assertNotContains(response, "source of truth")

    def test_grading_setup_guide_keeps_all_sections_and_practical_examples(self):
        user = self._make_user(
            username="grading_setup_examples",
            role_code="GRADING_SETUP_EXAMPLES",
            permissions=[
                self.portal_permission,
                self.grading_template_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:grading_setup_guide"))

        for section_title in (
            "1. Before You Start",
            "2. Build the Template Structure",
            "3. Choose the Score Entry Method",
            "4. Choose Weighted Details or Average Activities",
            "5. What to Do After Building the Template",
            "6. When to Create a Tenant Grading Profile",
            "7. When to Use Course Base Value Overrides",
            "8. Final Readiness Checklist",
        ):
            self.assertContains(response, section_title)

        self.assertContains(response, "Period: MIDTERM")
        self.assertContains(response, "Component: Class Standing")
        self.assertContains(response, "Subcomponent: Participation/Output")
        self.assertContains(response, "Details: Recitation, Assignment/Activities, Oral Presentation")
        self.assertContains(response, "Recitation 20%, Assignment 30%, and Oral Presentation 50%")
        self.assertContains(response, "33.34%, 33.33%, and 33.33%")
        self.assertContains(response, "zero values do not block template publication in this mode")
        self.assertContains(response, "BSA program uses a specific published template")
        self.assertContains(response, "1st and 2nd Semester")
        self.assertContains(response, "PRELIM, MIDTERM, PRE-FINAL, and FINAL")
        self.assertContains(response, "Create a separate Summer template")
        self.assertContains(response, "MIDTERM, PRE-FINAL, and FINAL")
        self.assertContains(response, "Mark the Summer term as")
        self.assertContains(response, "Course Template Assignments")
        self.assertContains(response, "Assign the regular template with blank")
        self.assertContains(response, "exact Summer row is checked first")

    def test_grading_setup_guide_requires_grading_template_read_permission(self):
        user = self._make_user(
            username="grading_setup_blocked",
            role_code="NON_GRADING_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:grading_setup_guide"))

        self.assertEqual(response.status_code, 403)

    def test_hotfix_help_is_visible_with_hotfix_permission(self):
        user = self._make_user(
            username="hotfix_guide_admin",
            role_code="HOTFIX_REVIEWER",
            permissions=[
                self.portal_permission,
                self.hotfix_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, "Change a Published Template Using a Hotfix")
        self.assertContains(response, "Admin Portal -&gt; Grading -&gt; Template Hotfix Requests")
        self.assertContains(response, "type APPLY HOTFIX")
        self.assertContains(response, "submitted offerings in restricted modes are skipped")

    def test_hotfix_help_is_hidden_without_hotfix_permission(self):
        user = self._make_user(
            username="non_hotfix_guide_admin",
            role_code="BASIC_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertNotContains(response, "Change a Published Template Using a Hotfix")
