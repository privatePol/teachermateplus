from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.admin_portal.course_exam_department import (
    BulkExamDepartmentAssignmentService,
)
from apps.admin_portal.forms import BulkExamDepartmentAssignmentForm, CourseForm
from apps.admin_portal.services import AdminScopeService
from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.models import CycleCourse, ExaminationCycle
from apps.departmental_exams.services import (
    DepartmentalExamAuthorizationService,
    ExaminationCycleService,
)
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class BulkExamDepartmentAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        cls.other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        cls.cubao = Campus.objects.create(
            tenant=cls.tenant, code="NCBA-CUBAO", name="Cubao"
        )
        cls.fairview = Campus.objects.create(
            tenant=cls.tenant, code="NCBA-FAIRVIEW", name="Fairview"
        )
        cls.taytay = Campus.objects.create(
            tenant=cls.tenant, code="NCBA-TAYTAY", name="Taytay"
        )
        cls.other_campus = Campus.objects.create(
            tenant=cls.other_tenant, code="OTHER-CAMPUS", name="Other Campus"
        )
        cls.cubao_department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            code="BSBA",
            name="Business Administration",
        )
        cls.fairview_department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.fairview,
            code="BSBA",
            name="Business Administration",
        )
        cls.taytay_department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.taytay,
            code="BSBA",
            name="Business Administration",
        )
        cls.inactive_department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            code="OLD",
            name="Inactive Department",
            is_active=False,
        )
        cls.other_department = Department.objects.create(
            tenant=cls.other_tenant,
            campus=cls.other_campus,
            code="BSBA",
            name="Business Administration",
        )
        cls.same_tenant_out_of_scope_department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            code="HRM",
            name="Hospitality Management",
        )

        cls.course_accounting = Course.objects.create(
            tenant=cls.tenant,
            code="ACCT101",
            title="Principles of Accounting",
        )
        cls.course_english = Course.objects.create(
            tenant=cls.tenant,
            code="ENG101",
            title="English Composition",
        )
        cls.course_assigned_other = Course.objects.create(
            tenant=cls.tenant,
            code="MATH101",
            title="College Algebra",
            exam_department=cls.cubao_department,
        )
        cls.course_assigned_same = Course.objects.create(
            tenant=cls.tenant,
            code="SCI101",
            title="General Science",
            exam_department=cls.fairview_department,
        )
        cls.inactive_course = Course.objects.create(
            tenant=cls.tenant,
            code="OLD101",
            title="Inactive Course",
            is_active=False,
        )
        cls.other_course = Course.objects.create(
            tenant=cls.other_tenant,
            code="OTHER101",
            title="Other Tenant Course",
        )
        cls.admin = get_user_model().objects.create_superuser(
            username="bulk-exam-admin",
            email="bulk-exam-admin@example.edu",
            password="Pass123!",
            default_tenant=cls.tenant,
            default_campus=cls.cubao,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("courses.read", "courses", "read"),
            ("courses.update", "courses", "update"),
            ("departmental_exams.manage_cycles", "departmental_exams", "manage_cycles"),
            ("departmental_exams.review_generate", "departmental_exams", "review_generate"),
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action, "is_active": True},
            )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=cls.tenant.id,
            value_type="BOOL",
        )

        cls.academic_year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="AY26",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant,
            academic_year=cls.academic_year,
            code="1ST",
            name="First Semester",
        )
        cls.program = Program.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            department=cls.cubao_department,
            code="BSBA",
            name="Business Administration",
        )
        cls.section = Section.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            department=cls.cubao_department,
            program=cls.program,
            code="BSBA-1A",
            name="BSBA 1A",
        )
        cls.offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=cls.cubao,
            department=cls.cubao_department,
            program=cls.program,
            academic_year=cls.academic_year,
            term=cls.term,
            course=cls.course_accounting,
            section=cls.section,
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin_portal:bulk_exam_department_assignment")

    def _post(self, *, department=None, courses=None, replace=False, follow=False):
        payload = {
            "department": (department or self.fairview_department).id,
            "course_ids": [course.id for course in (courses or [])],
            "assignment_status": "all",
        }
        if replace:
            payload["replace_existing"] = "on"
        return self.client.post(self.url, payload, follow=follow)

    def _scoped_user(self, username, *, role_active=True, department=None):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.edu",
            password="Pass123!",
            default_tenant=self.tenant,
            default_campus=self.cubao,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code=f"BULK_{username.upper()}", name=f"Bulk {username}", is_active=role_active
        )
        for code in ("admin_portal.access", "courses.read", "courses.update"):
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=code)
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.cubao,
            department=department,
            is_active=True,
        )
        return user

    def _service_request(self, user=None):
        request = RequestFactory().post(self.url)
        request.user = user or self.admin
        request.scope = ScopeService.build_scope(
            request.user,
            tenant_id=self.tenant.id,
            campus_id=self.cubao.id,
        )
        return request

    def _bulk_form(self, data):
        return BulkExamDepartmentAssignmentForm(
            data=data,
            department_queryset=Department.objects.filter(
                tenant=self.tenant,
                is_active=True,
            ).select_related("campus"),
            course_queryset=Course.objects.filter(
                tenant=self.tenant,
                is_active=True,
            ),
        )

    def _course_form_data(self, course, *, exam_department_id):
        return {
            "tenant": course.tenant_id,
            "campus": course.campus_id or "",
            "department": course.department_id or "",
            "exam_department": exam_department_id or "",
            "code": course.code,
            "title": f"{course.title} Updated",
            "units": course.units or "",
            "course_type": course.course_type or "",
            "default_base_value": course.default_base_value or "",
            "syllabus_url": course.syllabus_url or "",
            "is_active": "on" if course.is_active else "",
        }

    def test_authorized_administrator_can_open_page_and_list_button_agrees(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Assign Exam Departments")
        course_list = self.client.get(reverse("admin_portal:course_list"))
        self.assertContains(course_list, "Bulk Assign Exam Departments")

    def test_unauthorized_user_is_denied_direct_url_and_button(self):
        user = self._scoped_user("bulk-without-update")
        role = UserRole.objects.get(user=user).role
        RolePermission.objects.filter(
            role=role, permission__code="courses.update"
        ).delete()
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        course_list = self.client.get(reverse("admin_portal:course_list"))
        self.assertNotContains(course_list, "Bulk Assign Exam Departments")

    def test_inactive_role_and_direct_deny_fail_closed(self):
        inactive_role_user = self._scoped_user("inactive-role", role_active=False)
        self.client.force_login(inactive_role_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        denied_user = self._scoped_user("direct-denied")
        UserPermission.objects.create(
            user=denied_user,
            permission=Permission.objects.get(code="courses.update"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.cubao,
        )
        self.client.force_login(denied_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        denied_list = self.client.get(reverse("admin_portal:course_list"))
        self.assertNotContains(denied_list, "Bulk Assign Exam Departments")

    def test_direct_denied_user_post_cannot_mutate_course(self):
        denied_user = self._scoped_user("direct-denied-post")
        UserPermission.objects.create(
            user=denied_user,
            permission=Permission.objects.get(code="courses.update"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.cubao,
        )
        self.client.force_login(denied_user)
        response = self._post(courses=[self.course_accounting])
        self.assertEqual(response.status_code, 403)
        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)

    def test_unauthorized_direct_service_invocation_is_denied(self):
        user = self._scoped_user("service-without-update")
        role = UserRole.objects.get(user=user).role
        RolePermission.objects.filter(
            role=role,
            permission__code="courses.update",
        ).delete()
        with self.assertRaises(PermissionDenied):
            BulkExamDepartmentAssignmentService.assign(
                request=self._service_request(user),
                department_id=self.cubao_department.id,
                course_ids=[self.course_accounting.id],
                replace_existing=False,
            )
        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)

    def test_inactive_user_fails_closed(self):
        user = self._scoped_user("inactive-user")
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def test_feature_disabled_behavior_matches_existing_course_field(self):
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        bulk_response = self.client.get(self.url)
        edit_response = self.client.get(
            reverse("admin_portal:course_update", args=[self.course_accounting.id])
        )
        self.assertEqual(bulk_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 200)
        self.assertIn("exam_department", edit_response.context["form"].fields)

    def test_department_labels_are_campus_qualified_and_duplicate_names_stay_distinct(self):
        response = self.client.get(self.url)
        choices = list(response.context["form"].fields["department"].queryset)
        same_named = [department for department in choices if department.code == "BSBA"]
        self.assertEqual(len(same_named), 3)
        self.assertEqual(
            {department.id for department in same_named},
            {
                self.cubao_department.id,
                self.fairview_department.id,
                self.taytay_department.id,
            },
        )
        for department in same_named:
            self.assertContains(
                response,
                f"BSBA — Business Administration — {department.campus.code}",
            )

    def test_department_selectors_share_scoped_deterministic_ordering_and_empty_choices(self):
        campus_01 = Campus.objects.create(
            tenant=self.tenant, code="NCBA-01", name="Alpha Campus"
        )
        campus_02 = Campus.objects.create(
            tenant=self.tenant, code="NCBA-02", name="Beta Campus"
        )
        departments = [
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_01,
                code="ZZZ",
                name="Last Department",
            ),
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_01,
                code="AAA",
                name="First Department",
            ),
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_01,
                code="SHARED",
                name="Shared Department",
            ),
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_02,
                code="ZZZ",
                name="Last Department",
            ),
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_02,
                code="AAA",
                name="First Department",
            ),
            Department.objects.create(
                tenant=self.tenant,
                campus=campus_02,
                code="SHARED",
                name="Shared Department",
            ),
        ]

        response = self.client.get(self.url)
        responsible_field = response.context["form"].fields["department"]
        responsible_queryset = responsible_field.queryset
        current_queryset = response.context["department_options"]
        current_field = response.context["department_filter_form"].fields[
            "current_department_id"
        ]
        responsible_ids = list(responsible_queryset.values_list("id", flat=True))
        current_ids = list(current_queryset.values_list("id", flat=True))
        created_ids = {department.id for department in departments}

        self.assertEqual(
            responsible_queryset.query.order_by,
            ("campus__code", "campus__name", "code", "name", "pk"),
        )
        self.assertEqual(current_queryset.query.order_by, responsible_queryset.query.order_by)
        self.assertEqual(current_ids, responsible_ids)

        def grouped_signature(field):
            return [
                (
                    group_name,
                    [
                        (str(option["value"]), str(option["label"]))
                        for option in options
                    ],
                )
                for group_name, options, _index in field.widget.optgroups(
                    field.widget.attrs.get("name", "department"),
                    [""],
                )
            ]

        responsible_groups = grouped_signature(responsible_field)
        current_groups = grouped_signature(current_field)
        self.assertIsNone(responsible_groups[0][0])
        self.assertEqual(responsible_groups[0][1], [("", "Select a Department")])
        self.assertIsNone(current_groups[0][0])
        self.assertEqual(current_groups[0][1], [("", "Any Department")])
        self.assertEqual(responsible_groups[1:], current_groups[1:])
        self.assertEqual(
            [group_name for group_name, _options in responsible_groups[1:3]],
            ["NCBA-01 — Alpha Campus", "NCBA-02 — Beta Campus"],
        )
        self.assertEqual(
            [department_id for department_id in responsible_ids if department_id in created_ids],
            [
                departments[1].id,
                departments[2].id,
                departments[0].id,
                departments[4].id,
                departments[5].id,
                departments[3].id,
            ],
        )
        self.assertNotIn(self.inactive_department.id, responsible_ids)
        self.assertNotIn(self.other_department.id, responsible_ids)
        self.assertIn(departments[2].id, responsible_ids)
        self.assertIn(departments[5].id, responsible_ids)
        self.assertNotEqual(departments[2].id, departments[5].id)

        course_form = CourseForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(
                tenant=self.tenant,
                is_active=True,
            ),
        )
        course_groups = grouped_signature(course_form.fields["exam_department"])
        self.assertEqual(course_groups[1:], responsible_groups[1:])

        responsible_choices = list(responsible_field.choices)
        self.assertEqual(str(responsible_choices[0][0]), "")
        self.assertEqual(responsible_choices[0][1], "Select a Department")

        content = response.content.decode()
        responsible_start = content.index('id="id_department"')
        responsible_html = content[
            responsible_start : content.index("</select>", responsible_start)
        ]
        current_start = content.index('id="current-department"')
        current_html = content[
            current_start : content.index("</select>", current_start)
        ]
        self.assertLess(
            responsible_html.index('<option value=""'),
            responsible_html.index(f'<option value="{responsible_ids[0]}"'),
        )
        self.assertLess(
            current_html.index('<option value=""'),
            current_html.index(f'<option value="{current_ids[0]}"'),
        )
        self.assertIn('<optgroup label="NCBA-01 — Alpha Campus">', responsible_html)
        self.assertIn('<optgroup label="NCBA-02 — Beta Campus">', current_html)
        self.assertIn(
            f'value="{departments[2].id}" data-campus-code="NCBA-01"',
            responsible_html,
        )
        self.assertContains(response, 'id="selected-target-summary"')
        self.assertContains(response, "Responsible lead department:")
        self.assertContains(response, "Selected Courses:")
        self.assertContains(response, "Replacement mode:")
        self.assertIn('<optgroup label="NCBA-01 — Alpha Campus">', str(course_form["exam_department"]))

    def test_course_create_and_edit_exam_department_choices_use_exact_campus_labels(self):
        department_queryset = Department.objects.filter(
            tenant=self.tenant,
            code="BSBA",
        )
        expected = {
            str(department.id): (
                f"BSBA — Business Administration — {department.campus.code}"
            )
            for department in (
                self.cubao_department,
                self.fairview_department,
                self.taytay_department,
            )
        }

        for instance in (Course(), self.course_assigned_other):
            with self.subTest(instance_pk=instance.pk):
                form = CourseForm(
                    instance=instance,
                    tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
                    campus_queryset=Campus.objects.filter(tenant=self.tenant),
                    department_queryset=Department.objects.filter(tenant=self.tenant),
                    exam_department_queryset=department_queryset,
                )
                actual = {
                    str(value): label
                    for value, label in form.fields["exam_department"].choices
                    if value
                }
                self.assertEqual(actual, expected)
                self.assertEqual(set(actual), set(expected))

    def test_course_ordinary_department_label_behavior_is_unchanged(self):
        form = CourseForm(
            initial={"campus": self.cubao.id},
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
        )
        department_field = form.fields["department"]
        self.assertEqual(
            department_field.label_from_instance(self.cubao_department),
            "BSBA - Business Administration",
        )

    def test_course_edit_preserves_only_current_active_out_of_scope_exam_department(self):
        target_queryset = Department.objects.filter(id=self.fairview_department.id)
        form = CourseForm(
            instance=self.course_assigned_other,
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
            exam_department_queryset=target_queryset,
        )
        choice_ids = {
            int(str(value))
            for value, _label in form.fields["exam_department"].choices
            if value
        }
        self.assertEqual(
            choice_ids,
            {self.cubao_department.id, self.fairview_department.id},
        )
        self.assertNotIn(self.taytay_department.id, choice_ids)

        bound_form = CourseForm(
            data=self._course_form_data(
                self.course_assigned_other,
                exam_department_id=self.cubao_department.id,
            ),
            instance=self.course_assigned_other,
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
            exam_department_queryset=target_queryset,
        )
        self.assertTrue(bound_form.is_valid(), bound_form.errors)
        saved_course = bound_form.save()
        self.assertEqual(saved_course.exam_department_id, self.cubao_department.id)

    def test_course_edit_rejects_different_out_of_scope_exam_department(self):
        target_queryset = Department.objects.filter(id=self.fairview_department.id)
        form = CourseForm(
            data=self._course_form_data(
                self.course_assigned_other,
                exam_department_id=self.taytay_department.id,
            ),
            instance=self.course_assigned_other,
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
            exam_department_queryset=target_queryset,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("exam_department", form.errors)
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id,
            self.cubao_department.id,
        )

    def test_search_matches_code_case_insensitively(self):
        response = self.client.get(
            self.url, {"assignment_status": "all", "q": "acct"}
        )
        self.assertEqual(
            [row["course"].id for row in response.context["course_rows"]],
            [self.course_accounting.id],
        )

    def test_search_matches_title_case_insensitively(self):
        response = self.client.get(
            self.url, {"assignment_status": "all", "q": "composition"}
        )
        self.assertEqual(
            [row["course"].id for row in response.context["course_rows"]],
            [self.course_english.id],
        )

    def test_assignment_status_filters_unassigned_assigned_and_all(self):
        expected = {
            "unassigned": {self.course_accounting.id, self.course_english.id},
            "assigned": {
                self.course_assigned_other.id,
                self.course_assigned_same.id,
            },
            "all": {
                self.course_accounting.id,
                self.course_english.id,
                self.course_assigned_other.id,
                self.course_assigned_same.id,
            },
        }
        for status, expected_ids in expected.items():
            with self.subTest(status=status):
                response = self.client.get(self.url, {"assignment_status": status})
                self.assertEqual(
                    {row["course"].id for row in response.context["course_rows"]},
                    expected_ids,
                )

    def test_current_department_filter_uses_exact_department_id(self):
        response = self.client.get(
            self.url,
            {
                "assignment_status": "all",
                "current_department_id": self.fairview_department.id,
            },
        )
        self.assertEqual(
            [row["course"].id for row in response.context["course_rows"]],
            [self.course_assigned_same.id],
        )

    def test_multiple_selected_courses_receive_exact_fk_and_unselected_stays_unchanged(self):
        response = self._post(
            courses=[self.course_accounting, self.course_english], follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.course_accounting.refresh_from_db()
        self.course_english.refresh_from_db()
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_accounting.exam_department_id, self.fairview_department.id
        )
        self.assertEqual(
            self.course_english.exam_department_id, self.fairview_department.id
        )
        self.assertEqual(
            self.course_assigned_other.exam_department_id, self.cubao_department.id
        )

    def test_default_mode_does_not_overwrite_and_reports_skip(self):
        response = self._post(
            courses=[self.course_accounting, self.course_assigned_other], follow=True
        )
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id, self.cubao_department.id
        )
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("Updated: 1." in message for message in messages))
        self.assertTrue(
            any("Skipped (replacement not authorized): 1." in message for message in messages)
        )
        self.assertTrue(any("Total selected: 2." in message for message in messages))

    def test_explicit_replacement_overwrites_existing_assignment(self):
        response = self._post(
            courses=[self.course_assigned_other], replace=True, follow=True
        )
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id, self.fairview_department.id
        )
        self.assertContains(response, "Updated: 1.")
        self.assertContains(response, "Skipped (replacement not authorized): 0.")

    def test_strict_replacement_form_accepts_only_missing_or_exact_browser_token(self):
        base_data = {
            "department": self.fairview_department.id,
            "course_ids": [str(self.course_assigned_other.id)],
        }
        missing = self._bulk_form(base_data)
        self.assertTrue(missing.is_valid(), missing.errors)
        self.assertIs(missing.cleaned_data["replace_existing"], False)

        checked = self._bulk_form({**base_data, "replace_existing": "on"})
        self.assertTrue(checked.is_valid(), checked.errors)
        self.assertIs(checked.cleaned_data["replace_existing"], True)

    def test_ambiguous_replacement_tokens_are_rejected_without_overwrite(self):
        for token in ("false", "off", "no", "yes", "1", "anything"):
            with self.subTest(token=token):
                response = self.client.post(
                    self.url,
                    {
                        "department": self.fairview_department.id,
                        "course_ids": [self.course_assigned_other.id],
                        "assignment_status": "all",
                        "replace_existing": token,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("replace_existing", response.context["form"].errors)
                self.course_assigned_other.refresh_from_db()
                self.assertEqual(
                    self.course_assigned_other.exam_department_id,
                    self.cubao_department.id,
                )

    def test_service_rejects_non_boolean_replacement_values(self):
        request = self._service_request()
        for value in ("false", "off", "no", "yes", "1", "anything", 0, 1, object()):
            with self.subTest(value=repr(value)):
                with self.assertRaisesMessage(
                    ValidationError,
                    "Replacement authorization must be an explicit boolean value.",
                ):
                    BulkExamDepartmentAssignmentService.assign(
                        request=request,
                        department_id=self.fairview_department.id,
                        course_ids=[self.course_assigned_other.id],
                        replace_existing=value,
                    )
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id,
            self.cubao_department.id,
        )

    def test_service_false_skips_and_true_replaces_with_exact_counts(self):
        request = self._service_request()
        skipped = BulkExamDepartmentAssignmentService.assign(
            request=request,
            department_id=self.fairview_department.id,
            course_ids=[self.course_assigned_other.id],
            replace_existing=False,
        )
        self.assertEqual(
            (
                skipped.updated_count,
                skipped.unchanged_same_count,
                skipped.skipped_existing_count,
                skipped.total_selected,
            ),
            (0, 0, 1, 1),
        )
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id,
            self.cubao_department.id,
        )

        replaced = BulkExamDepartmentAssignmentService.assign(
            request=request,
            department_id=self.fairview_department.id,
            course_ids=[self.course_assigned_other.id],
            replace_existing=True,
        )
        self.assertEqual(
            (
                replaced.updated_count,
                replaced.unchanged_same_count,
                replaced.skipped_existing_count,
                replaced.total_selected,
            ),
            (1, 0, 0, 1),
        )
        self.course_assigned_other.refresh_from_db()
        self.assertEqual(
            self.course_assigned_other.exam_department_id,
            self.fairview_department.id,
        )

    def test_same_department_is_unchanged_and_reported(self):
        response = self._post(courses=[self.course_assigned_same], follow=True)
        self.course_assigned_same.refresh_from_db()
        self.assertEqual(
            self.course_assigned_same.exam_department_id, self.fairview_department.id
        )
        self.assertContains(
            response, "Unchanged (already assigned to the same Department): 1."
        )
        self.assertContains(response, "Updated: 0.")

    def test_cross_tenant_department_is_rejected(self):
        response = self._post(
            department=self.other_department, courses=[self.course_accounting]
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["department"])
        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)

    def test_same_tenant_out_of_scope_department_post_is_rejected(self):
        user = self._scoped_user(
            "department-scoped-target-forgery",
            department=self.cubao_department,
        )
        self.client.force_login(user)
        response = self._post(
            department=self.same_tenant_out_of_scope_department,
            courses=[self.course_accounting],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("department", response.context["form"].errors)
        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)

    def test_same_tenant_out_of_scope_course_post_is_rejected(self):
        out_of_scope_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            department=self.same_tenant_out_of_scope_department,
            code="HRM101",
            title="Introduction to Hospitality",
        )
        user = self._scoped_user(
            "department-scoped-course-forgery",
            department=self.cubao_department,
        )
        self.client.force_login(user)
        response = self._post(
            department=self.cubao_department,
            courses=[out_of_scope_course],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("course_ids", response.context["form"].errors)
        out_of_scope_course.refresh_from_db()
        self.assertIsNone(out_of_scope_course.exam_department_id)

    def test_cross_tenant_course_and_inactive_course_are_rejected(self):
        for course in (self.other_course, self.inactive_course):
            with self.subTest(course=course.code):
                response = self._post(courses=[self.course_accounting, course])
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors["course_ids"])
                self.course_accounting.refresh_from_db()
                self.assertIsNone(self.course_accounting.exam_department_id)

    def test_inactive_department_is_rejected(self):
        response = self._post(
            department=self.inactive_department, courses=[self.course_accounting]
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["department"])
        self.assertIsNone(Course.objects.get(id=self.course_accounting.id).exam_department_id)

    def test_service_rejects_inactive_target_department_before_mutation(self):
        request = self._service_request()
        with self.assertRaises(ValidationError):
            BulkExamDepartmentAssignmentService.assign(
                request=request,
                department_id=self.inactive_department.id,
                course_ids=[self.course_accounting.id],
                replace_existing=False,
            )
        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)
        self.assertFalse(
            AuditLog.objects.filter(
                entity_type="Course",
                entity_id=str(self.course_accounting.id),
                metadata_json__source="bulk_exam_department_assignment",
            ).exists()
        )

    def test_service_locks_authorized_target_department_queryset(self):
        request = self._service_request()
        scoped_queryset = AdminScopeService.active_scoped_departments(request)
        with patch.object(
            scoped_queryset,
            "select_for_update",
            wraps=scoped_queryset.select_for_update,
        ) as select_for_update, patch.object(
            AdminScopeService,
            "active_scoped_departments",
            return_value=scoped_queryset,
        ):
            BulkExamDepartmentAssignmentService.assign(
                request=request,
                department_id=self.fairview_department.id,
                course_ids=[self.course_accounting.id],
                replace_existing=False,
            )
        select_for_update.assert_called_once_with()

    def test_stale_form_department_validation_cannot_bypass_service_revalidation(self):
        form = self._bulk_form(
            {
                "department": self.fairview_department.id,
                "course_ids": [str(self.course_accounting.id)],
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        Department.objects.filter(id=self.fairview_department.id).update(is_active=False)

        with self.assertRaises(ValidationError):
            BulkExamDepartmentAssignmentService.assign(
                request=self._service_request(),
                department_id=form.cleaned_data["department"].id,
                course_ids=form.cleaned_data["course_ids"],
                replace_existing=form.cleaned_data["replace_existing"],
            )

        self.course_accounting.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)
        self.assertFalse(
            AuditLog.objects.filter(
                entity_type="Course",
                entity_id=str(self.course_accounting.id),
                metadata_json__source="bulk_exam_department_assignment",
            ).exists()
        )

    def test_missing_department_and_empty_selection_are_rejected(self):
        missing_department = self.client.post(
            self.url,
            {"course_ids": [self.course_accounting.id], "assignment_status": "all"},
        )
        self.assertTrue(missing_department.context["form"].errors["department"])
        empty_selection = self.client.post(
            self.url,
            {"department": self.fairview_department.id, "assignment_status": "all"},
        )
        self.assertTrue(empty_selection.context["form"].errors["course_ids"])

    def test_malformed_and_duplicate_ids_are_rejected_without_work(self):
        malformed = self.client.post(
            self.url,
            {
                "department": self.fairview_department.id,
                "course_ids": ["not-an-id"],
                "assignment_status": "all",
            },
        )
        self.assertTrue(malformed.context["form"].errors["course_ids"])
        duplicate = self.client.post(
            self.url,
            {
                "department": self.fairview_department.id,
                "course_ids": [
                    str(self.course_accounting.id),
                    str(self.course_accounting.id),
                ],
                "assignment_status": "all",
            },
        )
        self.assertIn(
            "Duplicate Course IDs are not allowed.",
            duplicate.context["form"].errors["course_ids"],
        )
        self.assertIsNone(Course.objects.get(id=self.course_accounting.id).exam_department_id)

    def test_any_invalid_selected_record_prevents_all_updates(self):
        response = self.client.post(
            self.url,
            {
                "department": self.fairview_department.id,
                "course_ids": [self.course_accounting.id, self.other_course.id],
                "assignment_status": "all",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(Course.objects.get(id=self.course_accounting.id).exam_department_id)
        self.assertFalse(
            AuditLog.objects.filter(
                entity_type="Course", entity_id=str(self.course_accounting.id)
            ).exists()
        )

    def test_second_audit_failure_rolls_back_all_course_and_audit_writes(self):
        request = self._service_request()
        original_log_event = AuditService.log_event
        call_count = 0

        def fail_second_audit(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated audit failure")
            return original_log_event(**kwargs)

        with patch.object(
            AuditService,
            "log_event",
            side_effect=fail_second_audit,
        ):
            with self.assertRaisesMessage(RuntimeError, "simulated audit failure"):
                BulkExamDepartmentAssignmentService.assign(
                    request=request,
                    department_id=self.fairview_department.id,
                    course_ids=[self.course_accounting.id, self.course_english.id],
                    replace_existing=False,
                )

        self.course_accounting.refresh_from_db()
        self.course_english.refresh_from_db()
        self.assertIsNone(self.course_accounting.exam_department_id)
        self.assertIsNone(self.course_english.exam_department_id)
        self.assertFalse(
            AuditLog.objects.filter(
                entity_type="Course",
                metadata_json__source="bulk_exam_department_assignment",
            ).exists()
        )

    def test_ordinary_course_fields_and_offering_department_remain_unchanged(self):
        before = {
            "campus_id": self.course_accounting.campus_id,
            "department_id": self.course_accounting.department_id,
            "offering_department_id": self.offering.department_id,
        }
        self._post(courses=[self.course_accounting])
        self.course_accounting.refresh_from_db()
        self.offering.refresh_from_db()
        self.assertEqual(self.course_accounting.campus_id, before["campus_id"])
        self.assertEqual(self.course_accounting.department_id, before["department_id"])
        self.assertEqual(self.offering.department_id, before["offering_department_id"])

    def test_per_course_audit_records_only_exam_department_change(self):
        self._post(courses=[self.course_accounting, self.course_assigned_other], replace=True)
        logs = AuditLog.objects.filter(
            action="UPDATE",
            entity_type="Course",
            metadata_json__source="bulk_exam_department_assignment",
        ).order_by("entity_id")
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertEqual(
                set(log.before_json), {"exam_department_id", "exam_department_label"}
            )
            self.assertEqual(
                set(log.after_json), {"exam_department_id", "exam_department_label"}
            )
            self.assertEqual(
                log.after_json["exam_department_id"], self.fairview_department.id
            )

    def test_unchanged_and_skipped_courses_create_no_update_audit(self):
        self._post(
            courses=[self.course_assigned_same, self.course_assigned_other],
        )
        self.assertFalse(
            AuditLog.objects.filter(
                action="UPDATE",
                entity_type="Course",
                entity_id__in=[
                    str(self.course_assigned_same.id),
                    str(self.course_assigned_other.id),
                ],
                metadata_json__source="bulk_exam_department_assignment",
            ).exists()
        )

    def test_existing_cycle_snapshot_is_unchanged(self):
        cycle = ExaminationCycle.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        snapshot = CycleCourse.objects.create(
            cycle=cycle,
            course=self.course_accounting,
            responsible_department=self.cubao_department,
        )
        self._post(courses=[self.course_accounting])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.responsible_department_id, self.cubao_department.id)

    def test_future_cycle_snapshots_updated_course_exam_department(self):
        self._post(courses=[self.course_accounting])
        cycle = ExaminationCycleService.create_cycle(
            user=self.admin,
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            exam_period=ExaminationCycle.ExamPeriod.FINAL,
        )
        snapshot = CycleCourse.objects.get(cycle=cycle, course=self.course_accounting)
        self.assertEqual(snapshot.responsible_department_id, self.fairview_department.id)

    def test_exact_department_reviewer_authorization_remains_distinct(self):
        reviewer = get_user_model().objects.create_user(
            username="exact-reviewer",
            email="exact-reviewer@example.edu",
            password="Pass123!",
            default_tenant=self.tenant,
            default_campus=self.cubao,
        )
        role = Role.objects.create(code="EXACT_REVIEWER", name="Exact Reviewer")
        RolePermission.objects.create(
            role=role,
            permission=Permission.objects.get(code="departmental_exams.review_generate"),
        )
        UserRole.objects.create(
            user=reviewer,
            role=role,
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
        )
        self.assertTrue(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=reviewer,
                tenant_id=self.tenant.id,
                responsible_department=self.cubao_department,
            )
        )
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=reviewer,
                tenant_id=self.tenant.id,
                responsible_department=self.fairview_department,
            )
        )

    def test_get_query_growth_is_bounded_for_450_courses(self):
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(self.url, {"assignment_status": "all"})
        self.assertEqual(small_response.status_code, 200)
        Course.objects.bulk_create(
            [
                Course(
                    tenant=self.tenant,
                    code=f"BULK{number:03d}",
                    title=f"Bulk Course {number:03d}",
                )
                for number in range(446)
            ],
            batch_size=200,
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(self.url, {"assignment_status": "all"})
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(large_response.context["filtered_course_count"], 450)
        self.assertLessEqual(len(large_context), len(small_context) + 2)
