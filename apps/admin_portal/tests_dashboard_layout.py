"""Pending queue/count/list parity and layout regression fixtures."""
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import AcademicGovernanceService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import ClassListChangeRequest, ClassListChangeRequestItem
from apps.grading.models import (
    GradeCorrectionRequest, GradeCorrectionRequestItem, GradeCorrectionUnlockWindow,
    GradeSubmissionReopenRequest, GradingPeriodLock,
)
from apps.grading.services import GradingGovernanceService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant
from .dashboard import monitoring_offering_ids, request_queues, selected_campus_cml_requests
from .services import AdminScopeService
from . import tests_reopen_requests as fixtures


class DashboardQueueTests(TestCase):
    def setUp(self):
        fixtures.GradeSubmissionReopenRequestReviewTests.setUp(self)
        role = Role.objects.get(code="DEAN")
        for code in ("corrections.read", "class_list_change_requests.view", "users.read", "audit_logs.read", "grading_periods.lock"):
            module, action = code.split(".")
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": module, "action": action})
            RolePermission.objects.get_or_create(role=role, permission=permission)
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=self.academic_year, term=self.term)
        self.client.force_login(self.admin_user)

    def correction(self, status="PENDING", **kwargs):
        values = dict(tenant=self.tenant, campus=self.campus, offering=self.offering,
                      template_period=self.period, requested_by_user=self.faculty_user,
                      faculty_department=self.department, justification="Dashboard request", status=status)
        values.update(kwargs)
        return GradeCorrectionRequest.objects.create(**values)

    def test_dashboard_performance_evidence(self):
        import json
        from statistics import median
        from time import perf_counter
        self.correction()
        self.cml()
        self.dashboard()  # warm session registry and templates equally in both runs
        samples = []
        for _ in range(7):
            started = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                self.dashboard()
            samples.append({"queries": len(queries), "ms": round((perf_counter()-started)*1000, 3)})
        result = {"samples": samples, "median_ms": median(row["ms"] for row in samples)}
        print("DASHBOARD_PERFORMANCE " + json.dumps(result))
        if os.environ.get("TMP_ADMIN_EVIDENCE"):
            Path(os.environ["TMP_ADMIN_EVIDENCE"], "performance-" + os.environ.get("TMP_PERF_PHASE", "candidate") + ".json").write_text(json.dumps(result), encoding="utf-8")

    def cml(self, status="PENDING", **kwargs):
        values = dict(tenant=self.tenant, campus=self.campus, offering=self.offering,
                      faculty_requester=self.faculty_user, request_type="ADD", status=status)
        values.update(kwargs)
        return ClassListChangeRequest.objects.create(**values)

    def dashboard(self):
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        return response

    def deny(self, code):
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code=code),
                                      tenant=self.tenant, campus=self.campus, grant_type="DENY")

    def counts(self):
        response = self.dashboard()
        return response.context["correction_queue"]["pending_count"], response.context["cml_queue"]["pending_count"]

    def clone_offering(self, **kwargs):
        values = {field.attname: getattr(self.offering, field.attname)
                  for field in CourseOffering._meta.fields if not field.primary_key}
        values.update(kwargs)
        return CourseOffering.objects.create(**values)

    def test_status_counts_are_requests_not_changed_items_or_students(self):
        correction = self.correction()
        cml = self.cml()
        for _ in range(4):
            GradeCorrectionRequestItem.objects.create(correction_request=correction, new_value="1")
            ClassListChangeRequestItem.objects.create(request=cml, reference_student_name="Reference")
        for status in ("APPROVED", "CLOSED", "LAPSED", "REJECTED"):
            self.correction(status)
        for status in ("APPROVED", "REJECTED", "CANCELLED"):
            self.cml(status)
        self.assertEqual(self.counts(), (1, 1))

    def test_approved_windows_do_not_trigger_validation_or_enter_pending_count(self):
        self.correction()
        now = timezone.now()
        variants = [dict(), dict(end_at=now-timedelta(seconds=1)), dict(start_at=now+timedelta(hours=1)),
                    dict(is_consumed=True), dict(is_active=False)]
        for values in variants:
            window = dict(start_at=now-timedelta(hours=1), end_at=now+timedelta(hours=2), is_active=True, is_consumed=False)
            window.update(values)
            GradeCorrectionUnlockWindow.objects.create(correction_request=self.correction("APPROVED"),
                                                       offering=self.offering, template_period=self.period, **window)
        before = list(GradeCorrectionUnlockWindow.objects.values())
        with patch.object(GradingGovernanceService, "get_approved_correction_governance_state", side_effect=AssertionError("approved validation")), patch.object(GradingGovernanceService, "auto_lapse_expired_correction_windows", side_effect=AssertionError("lapse write")), CaptureQueriesContext(connection) as queries:
            response = self.dashboard()
        self.assertEqual(response.context["correction_queue"]["pending_count"], 1)
        self.assertContains(response, "Pending Grade Corrections")
        self.assertNotContains(response, "Active approved windows")
        self.assertEqual(before, list(GradeCorrectionUnlockWindow.objects.values()))
        self.assertFalse(any("grade_correction_unlock_windows" in q["sql"] for q in queries))

    def test_dashboard_uses_sql_counts_without_loading_request_rows(self):
        self.correction()
        self.cml()
        original = QuerySet._fetch_all
        def no_listing(queryset):
            if queryset.model in (GradeCorrectionRequest, ClassListChangeRequest):
                raise AssertionError("request listing loaded on dashboard")
            return original(queryset)
        with patch.object(QuerySet, "_fetch_all", no_listing), CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.counts(), (1, 1))
        queue_sql = [q["sql"] for q in queries if 'FROM "grade_correction_requests"' in q["sql"] or 'FROM "class_list_change_requests"' in q["sql"]]
        self.assertEqual(len(queue_sql), 2)
        self.assertTrue(all("COUNT(" in sql and "DISTINCT" in sql for sql in queue_sql))
        self.assertFalse(any('FROM "grade_correction_request_items"' in q["sql"] or 'FROM "class_list_change_request_items"' in q["sql"] for q in queries))

    def test_active_term_and_year_filter_match_both_destination_lists(self):
        correction, cml = self.correction(), self.cml()
        old_term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year,
                                      code="OLD", name="Old term", sequence_no=2,
                                      start_date=self.term.start_date, end_date=self.term.end_date)
        old_offering = self.clone_offering(term_id=old_term.pk)
        self.correction(offering=old_offering)
        self.cml(offering=old_offering)
        response = self.dashboard()
        for key, expected in (("correction_queue", correction), ("cml_queue", cml)):
            queue = response.context[key]
            self.assertEqual(queue["pending_count"], 1)
            listing = self.client.get(queue["url"])
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.context["page_obj"].paginator.count, queue["pending_count"])
            self.assertEqual([row.pk for row in listing.context["page_obj"]], [expected.pk])
            self.assertContains(listing, 'name="academic_scope"')
        # Existing general listing still exposes the authorized historical statuses.
        listing = self.client.get(reverse("admin_portal:class_list_change_request_list"), {"term_id": old_term.pk})
        self.assertEqual(listing.context["page_obj"].paginator.count, 1)

    def test_missing_active_configuration_is_unavailable_and_never_all_history(self):
        self.correction()
        self.cml()
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=None, term=None)
        response = self.dashboard()
        for key in ("correction_queue", "cml_queue"):
            self.assertIsNone(response.context[key]["pending_count"])
            listing = self.client.get(response.context[key]["url"])
            self.assertEqual(listing.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "Configure the active AY / term", count=2)

    def test_invalid_active_term_configuration_is_unavailable(self):
        SystemSettingService.set(AcademicGovernanceService.ACTIVE_TERM_KEY, "NONEXISTENT", tenant_id=self.tenant.pk, value_type="STRING")
        self.assertEqual(self.counts(), (None, None))

    def test_other_academic_year_with_same_term_code_is_excluded(self):
        self.correction()
        self.cml()
        year = AcademicYear.objects.create(tenant=self.tenant, code="OLD", name="Old year",
            start_date=self.academic_year.start_date, end_date=self.academic_year.end_date)
        term = Term.objects.create(tenant=self.tenant, academic_year=year, code=self.term.code,
            name="First term in old year", sequence_no=1, start_date=self.term.start_date, end_date=self.term.end_date)
        offering = self.clone_offering(academic_year_id=year.pk, term_id=term.pk)
        self.correction(offering=offering)
        self.cml(offering=offering)
        self.assertEqual(self.counts(), (1, 1))

    def test_cml_invalid_or_outside_term_filter_does_not_broaden_listing(self):
        self.cml()
        for term_id in ("invalid", "999999"):
            response = self.client.get(reverse("admin_portal:class_list_change_request_list"), {"term_id": term_id})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_other_correction_statuses_remain_available_in_existing_listing(self):
        self.correction()
        self.correction("APPROVED")
        self.correction("CLOSED")
        listing = self.client.get(reverse("admin_portal:grade_correction_request_list"), {"status": "APPROVED"})
        self.assertEqual(listing.context["page_obj"].paginator.count, 1)
        self.assertEqual(listing.context["page_obj"][0].status, "APPROVED")
        self.assertContains(listing, 'value="CLOSED"')
        self.assertEqual(self.counts(), (1, 0))

    def test_direct_denies_hide_counts_buttons_skip_selectors_and_block_lists(self):
        self.correction()
        self.cml()
        for code in ("corrections.read", "class_list_change_requests.view"):
            self.deny(code)
        with patch("apps.admin_portal.services.AdminScopeService.scoped_grade_correction_requests", side_effect=AssertionError("unauthorized count")), patch("apps.admin_portal.services.AdminScopeService.scoped_class_list_change_requests", side_effect=AssertionError("unauthorized count")):
            response = self.dashboard()
        for key in ("correction_queue", "cml_queue"):
            self.assertIsNone(response.context[key])
        for label in ("View Grade Corrections", "View CML Requests", "Pending Grade Corrections", "CML Change Request"):
            self.assertNotContains(response, label)
        for route in ("grade_correction_request_list", "class_list_change_request_list"):
            self.assertEqual(self.client.get(reverse("admin_portal:"+route)).status_code, 403)

    def test_individual_deny_does_not_hide_other_authorized_queue(self):
        self.deny("corrections.read")
        response = self.dashboard()
        self.assertIsNone(response.context["correction_queue"])
        self.assertEqual(response.context["cml_queue"]["pending_count"], 0)
        self.assertContains(response, "View CML Requests")

    def test_listing_permissions_do_not_grant_review_authority(self):
        correction, cml = self.correction(), self.cml()
        response = self.dashboard()
        for key in ("correction_queue", "cml_queue"):
            listing = self.client.get(response.context[key]["url"])
            self.assertNotContains(listing, '>Review</a>')
        for route, pk in (("grade_correction_request_review", correction.pk), ("class_list_change_request_review", cml.pk)):
            self.assertEqual(self.client.get(reverse("admin_portal:"+route, args=[pk])).status_code, 403)

    def test_current_superuser_permission_semantics_are_preserved(self):
        self.correction()
        self.cml()
        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=["is_superuser"])
        self.deny("corrections.read")
        self.deny("class_list_change_requests.view")
        self.assertEqual(self.counts(), (1, 1))

    def test_correction_faculty_supervision_is_preserved(self):
        self.correction()
        outside = Department.objects.create(tenant=self.tenant, campus=self.campus, code="OUT", name="Outside")
        UserRole.objects.filter(user=self.faculty_user).update(department=outside)
        self.faculty_user.default_department = outside
        self.faculty_user.save(update_fields=["default_department"])
        response = self.dashboard()
        self.assertEqual(response.context["correction_queue"]["pending_count"], 0)
        listing = self.client.get(response.context["correction_queue"]["url"])
        self.assertEqual(listing.context["page_obj"].paginator.count, 0)

    def test_cml_department_and_program_scope_remain_in_existing_selector(self):
        self.cml()
        outside = Department.objects.create(tenant=self.tenant, campus=self.campus, code="OUT", name="Outside")
        outside_offering = self.clone_offering(department_id=outside.pk)
        self.cml(offering=outside_offering)
        outside_program = Program.objects.create(tenant=self.tenant, campus=self.campus,
            department=outside, code="OUT", name="Outside program")
        section = Section.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            program=self.program, code="SECOND", name="Second section")
        self.cml(offering=self.clone_offering(program_id=outside_program.pk, section_id=section.pk))
        self.assertEqual(self.counts(), (0, 1))

    def test_cross_tenant_and_campus_rows_are_excluded(self):
        self.correction()
        self.cml()
        tenant = Tenant.objects.create(code="OTHER", name="Other")
        campus = Campus.objects.create(tenant=tenant, code="OTHER", name="Other")
        offering = self.clone_offering(tenant_id=tenant.pk, campus_id=campus.pk)
        self.correction(tenant=tenant, campus=campus, offering=offering)
        self.cml(tenant=tenant, campus=campus, offering=offering)
        campus2 = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second")
        offering2 = self.clone_offering(campus_id=campus2.pk)
        self.correction(campus=campus2, offering=offering2)
        self.cml(campus=campus2, offering=offering2)
        self.assertEqual(self.counts(), (1, 1))

    def test_pagination_and_filters_preserve_count_list_parity(self):
        for _ in range(23):
            self.correction()
            self.cml()
        response = self.dashboard()
        for key in ("correction_queue", "cml_queue"):
            queue = response.context[key]
            self.assertEqual(queue["pending_count"], 23)
            listing = self.client.get(queue["url"]+"&page=2")
            self.assertEqual(len(listing.context["page_obj"]), 3)
            self.assertEqual(listing.context["page_obj"].paginator.count, 23)
            self.assertIn("academic_scope=active", listing.context["page_obj"].querystring)
            self.assertIn("status=PENDING", listing.context["page_obj"].querystring)

    def test_no_tenant_selection_does_not_expand_queues(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        request.scope = ScopeService.build_scope(self.admin_user)
        request.scope["tenant_id"] = None
        correction, cml = request_queues(request, permissions={"corrections.read", "class_list_change_requests.view"})
        self.assertIsNone(correction["pending_count"])
        self.assertIsNone(cml["pending_count"])

    def multi_campus_cml(self):
        """A legitimate tenant-wide role can select either campus."""
        UserRole.objects.filter(user=self.admin_user, role__code="DEAN").update(campus=None, department=None)
        campus = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second campus")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="B", name="B department")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department, code="B", name="B program")
        course = Course.objects.create(tenant=self.tenant, campus=campus, department=department, code="B", title="B course")
        section = Section.objects.create(tenant=self.tenant, campus=campus, department=department, program=program, code="B", name="B section")
        offering = self.clone_offering(campus_id=campus.pk, department_id=department.pk,
            program_id=program.pk, course_id=course.pk, section_id=section.pk)
        faculty = User.objects.create_user(username="b_faculty", email="b_faculty@example.test", default_tenant=self.tenant,
            default_campus=campus, default_department=department)
        UserRole.objects.create(user=faculty, role=Role.objects.get(code="FACULTY"), tenant=self.tenant,
            campus=campus, department=department)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=campus, offering=offering, faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED, accepted_at=timezone.now(), accepted_by=faculty)
        return campus, self.cml(remarks="CML_A_ONLY"), self.cml(campus=campus, offering=offering,
            faculty_requester=faculty, remarks="CML_B_ONLY")

    def test_multi_campus_cml_selected_count_and_destination_are_exactly_one(self):
        campus, a, b = self.multi_campus_cml()
        response = self.dashboard()
        self.assertEqual(response.context["cml_queue"]["pending_count"], 1)
        listing = self.client.get(response.context["cml_queue"]["url"])
        self.assertEqual([row.pk for row in listing.context["page_obj"]], [a.pk])
        self.assertNotContains(listing, "CML_B_ONLY")

    def test_multi_campus_cml_direct_deny_for_other_campus_cannot_leak(self):
        campus, a, b = self.multi_campus_cml()
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="class_list_change_requests.view"),
            tenant=self.tenant, campus=campus, grant_type="DENY")
        response = self.dashboard()
        self.assertEqual(response.context["cml_queue"]["pending_count"], 1)
        for filters in ({}, {"academic_scope": "active", "status": "PENDING"}, {"campus_id": campus.pk},
                        {"academic_scope": "", "term_id": self.term.pk, "campus_id": campus.pk}):
            listing = self.client.get(reverse("admin_portal:class_list_change_request_list"), filters)
            self.assertEqual(listing.status_code, 200)
            self.assertNotIn(b.pk, [row.pk for row in listing.context["page_obj"]])
        selected_b = self.client.get(reverse("admin_portal:class_list_change_request_list"),
            {"scope_tenant_id": self.tenant.pk, "scope_campus_id": campus.pk})
        self.assertEqual(selected_b.status_code, 403)
        dashboard_b = self.dashboard()
        self.assertIsNone(dashboard_b.context["cml_queue"])

    def test_cml_forged_and_invalid_page_filters_only_narrow_selected_campus(self):
        campus, a, b = self.multi_campus_cml()
        for campus_id in (str(campus.pk), "invalid", "999999", "9" * 5000):
            listing = self.client.get(reverse("admin_portal:class_list_change_request_list"), {"campus_id": campus_id})
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.context["page_obj"].paginator.count, 0)
        listing = self.client.get(reverse("admin_portal:class_list_change_request_list"))
        self.assertEqual([row.pk for row in listing.context["page_obj"]], [a.pk])

    def test_cml_authorized_topbar_selection_and_scope_bound_link_preserve_access(self):
        campus, a, b = self.multi_campus_cml()
        response = self.client.get(reverse("admin_portal:dashboard"),
            {"scope_tenant_id": self.tenant.pk, "scope_campus_id": campus.pk})
        self.assertEqual(response.context["cml_queue"]["pending_count"], 1)
        url = response.context["cml_queue"]["url"]
        self.assertIn(f"scope_campus_id={campus.pk}", url)
        self.client.get(reverse("admin_portal:dashboard"), {"scope_tenant_id": self.tenant.pk, "scope_campus_id": self.campus.pk})
        listing = self.client.get(url)
        self.assertEqual([row.pk for row in listing.context["page_obj"]], [b.pk])

    def test_cml_scope_bound_link_rechecks_new_campus_deny(self):
        campus, a, b = self.multi_campus_cml()
        response = self.client.get(reverse("admin_portal:dashboard"), {"scope_tenant_id": self.tenant.pk, "scope_campus_id": campus.pk})
        url = response.context["cml_queue"]["url"]
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="class_list_change_requests.view"),
            tenant=self.tenant, campus=campus, grant_type="DENY")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_cml_missing_or_unauthorized_selected_scope_returns_no_records(self):
        campus, a, b = self.multi_campus_cml()
        request = RequestFactory().get("/")
        request.user = self.admin_user
        for selected in (None, 999999):
            request.scope = ScopeService.build_scope(self.admin_user)
            request.scope["campus_id"] = selected
            self.assertFalse(selected_campus_cml_requests(request).exists())

    def test_cml_review_route_cannot_disclose_another_selected_campus(self):
        campus, a, b = self.multi_campus_cml()
        permission, _ = Permission.objects.get_or_create(code="class_list_change_requests.review",
            defaults={"module": "class_list_change_requests", "action": "review"})
        RolePermission.objects.get_or_create(role=Role.objects.get(code="DEAN"), permission=permission)
        route = reverse("admin_portal:class_list_change_request_review", args=[b.pk])
        self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual(self.client.post(route, {"decision": "APPROVE"}).status_code, 404)
        b.refresh_from_db()
        self.assertEqual(b.status, "PENDING")
        UserPermission.objects.create(user=self.admin_user, permission=permission, tenant=self.tenant, campus=campus, grant_type="DENY")
        self.assertEqual(self.client.get(route, {"scope_tenant_id": self.tenant.pk, "scope_campus_id": campus.pk}).status_code, 403)

    def test_dashboard_resolves_monitoring_scope_once_for_reopen_and_corrections(self):
        self.correction()
        with patch.object(AdminScopeService, "scoped_monitoring_course_offerings",
                          wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            response = self.dashboard()
        self.assertEqual(response.context["correction_queue"]["pending_count"], 1)
        self.assertEqual(selector.call_count, 1)

    def test_monitoring_scope_is_never_reused_across_dashboard_requests(self):
        with patch.object(AdminScopeService, "scoped_monitoring_course_offerings",
                          wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            self.dashboard()
            self.dashboard()
        self.assertEqual(selector.call_count, 2)

    def test_monitoring_scope_re_resolves_when_selection_changes_on_same_request(self):
        campus, a, b = self.multi_campus_cml()
        request = RequestFactory().get("/")
        request.user = self.admin_user
        request.scope = ScopeService.build_scope(self.admin_user, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        with patch.object(AdminScopeService, "scoped_monitoring_course_offerings",
                          wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            self.assertEqual(set(monitoring_offering_ids(request)), {self.offering.pk})
            request.scope = ScopeService.build_scope(self.admin_user, tenant_id=self.tenant.pk, campus_id=campus.pk)
            self.assertEqual(set(monitoring_offering_ids(request)), {b.offering_id})
        self.assertEqual(selector.call_count, 2)

    def test_monitoring_scope_resolves_only_for_authorized_cards(self):
        self.deny("reopen_requests.read")
        self.deny("reopen_requests.review")
        with patch.object(AdminScopeService, "scoped_monitoring_course_offerings",
                          wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            self.dashboard()
        self.assertEqual(selector.call_count, 1)
        self.deny("corrections.read")
        with patch.object(AdminScopeService, "scoped_monitoring_course_offerings",
                          wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            self.dashboard()
        self.assertEqual(selector.call_count, 0)

    def test_export_real_populated_layout_and_denied_navigation_for_chrome(self):
        from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
        group = MenuGroup.objects.create(portal="ADMIN", code="LAYOUT_TEST", label="Administration")
        for code, label, route, permission in (
            ("DASH", "Dashboard", "dashboard", "dashboard.read"),
            ("USERS", "Users", "user_list", "users.read"),
            ("AUDIT", "Audit Logs", "audit_log_list", "audit_logs.read"),
        ):
            item = MenuItem.objects.create(portal="ADMIN", menu_group=group, code=code, label=label, route_name="admin_portal:"+route)
            MenuItemPermission.objects.create(menu_item=item, permission=Permission.objects.get(code=permission))
        self.correction()
        self.cml()
        GradeSubmissionReopenRequest.objects.create(tenant=self.tenant, campus=self.campus, submission=self.submission,
            offering=self.offering, template_period=self.period, requested_by_user=self.faculty_user, justification="Browser fixture")
        GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, period_code="PRELIM", course_offering=self.offering, deadline_at=timezone.now()+timedelta(days=1))
        for variant in ("authorized", "denied"):
            if variant == "denied":
                for code in ("users.read", "audit_logs.read", "corrections.read", "class_list_change_requests.view"):
                    self.deny(code)
            response = self.dashboard()
            self.assertNotContains(response, "Quick Actions")
            if os.environ.get("TMP_ADMIN_EVIDENCE"):
                Path(os.environ["TMP_ADMIN_EVIDENCE"], "layout-"+variant+".html").write_text(response.content.decode(), encoding="utf-8")

    def refresh_queues(self, token=None, **params):
        if token is None:
            token = self.dashboard().context['dashboard_scope_token']
        return self.client.get(reverse('admin_portal:dashboard_queue_counts'), {'scope_token': token, **params})

    def test_queue_refresh_counts_current_pending_requests_and_returns_no_rows(self):
        self.correction()
        self.cml()
        token = self.dashboard().context['dashboard_scope_token']
        self.correction()
        self.correction('APPROVED')
        self.cml('REJECTED')
        with patch.object(GradingGovernanceService, 'get_approved_correction_governance_state', side_effect=AssertionError('approved validation')), patch.object(GradingGovernanceService, 'auto_lapse_expired_correction_windows', side_effect=AssertionError('maintenance')):
            response = self.refresh_queues(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['correction']['pending_count'], 2)
        self.assertEqual(response.json()['cml']['pending_count'], 1)
        self.assertEqual(response.json()['scope_token'], token)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(set(response.json()), {'scope_token', 'correction', 'cml'})
        for name in ('correction', 'cml'):
            row = response.json()[name]
            listing = self.client.get(row['url'])
            self.assertEqual(listing.context['page_obj'].paginator.count, row['pending_count'])

    def test_queue_refresh_rechecks_permissions_without_reopen_access(self):
        self.correction()
        self.cml()
        token = self.dashboard().context['dashboard_scope_token']
        self.deny('reopen_requests.read')
        self.deny('reopen_requests.review')
        self.assertEqual(self.refresh_queues(token).status_code, 200)
        self.deny('corrections.read')
        response = self.refresh_queues(token)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['correction'])
        self.assertEqual(response.json()['cml']['pending_count'], 1)
        self.deny('class_list_change_requests.view')
        self.assertEqual(self.refresh_queues(token).status_code, 403)

    def test_correction_only_refresh_and_dashboard_permission_revocation(self):
        self.deny('class_list_change_requests.view')
        response = self.refresh_queues()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['cml'])
        token = response.json()['scope_token']
        self.deny('dashboard.read')
        self.assertEqual(self.refresh_queues(token).status_code, 403)

    def test_queue_refresh_is_get_only_and_rejects_invalid_or_expired_tokens(self):
        url = reverse('admin_portal:dashboard_queue_counts')
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.refresh_queues('forged').status_code, 409)
        token = self.dashboard().context['dashboard_scope_token']
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()+86401):
            self.assertEqual(self.refresh_queues(token).status_code, 409)
        self.client.logout()
        self.assertIn(self.client.get(url).status_code, (302, 401))

    def test_queue_refresh_scope_selection_cannot_be_forged_or_restore_another_tab_scope(self):
        campus, a, b = self.multi_campus_cml()
        token = self.dashboard().context['dashboard_scope_token']
        self.assertEqual(self.refresh_queues(token).json()['cml']['pending_count'], 1)
        for endpoint in ('dashboard_queue_counts', 'dashboard_reopen_card'):
            response = self.client.get(reverse('admin_portal:'+endpoint), {'scope_token': token, 'scope_tenant_id': self.tenant.pk, 'scope_campus_id': campus.pk})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.client.session[ScopeService.SESSION_CAMPUS_KEY], self.campus.pk)
        self.client.get(reverse('admin_portal:dashboard'), {'scope_tenant_id': self.tenant.pk, 'scope_campus_id': campus.pk})
        self.assertEqual(self.refresh_queues(token).status_code, 409)
        self.assertEqual(self.client.session[ScopeService.SESSION_CAMPUS_KEY], campus.pk)
        token_b = self.dashboard().context['dashboard_scope_token']
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code='class_list_change_requests.view'), tenant=self.tenant, campus=campus, grant_type='DENY')
        response = self.refresh_queues(token_b)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['cml'])

    def test_queue_refresh_rejects_changed_academic_scope_and_departments(self):
        token = self.dashboard().context['dashboard_scope_token']
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=None, term=None)
        self.assertEqual(self.refresh_queues(token).status_code, 409)
        response = self.refresh_queues()
        self.assertIsNone(response.json()['correction']['pending_count'])
        self.assertIsNone(response.json()['cml']['pending_count'])
        token = response.json()['scope_token']
        with patch.object(ScopeService, 'get_accessible_department_ids', return_value=[]):
            self.assertEqual(self.refresh_queues(token).status_code, 409)

    def test_queue_refresh_preserves_idle_expiry_with_save_every_request_and_denial(self):
        from django.contrib.sessions.models import Session
        token = self.dashboard().context['dashboard_scope_token']
        key = self.client.session.session_key
        deadline = timezone.now()+timedelta(seconds=90)
        Session.objects.filter(session_key=key).update(expire_date=deadline)
        with self.settings(SESSION_SAVE_EVERY_REQUEST=True):
            self.assertEqual(self.refresh_queues(token).status_code, 200)
            self.assertEqual(Session.objects.get(session_key=key).expire_date, deadline)
            self.assertEqual(self.refresh_queues(token, scope_campus_id=self.campus.pk).status_code, 409)
            self.assertEqual(Session.objects.get(session_key=key).expire_date, deadline)
            self.deny('corrections.read')
            self.deny('class_list_change_requests.view')
            self.assertEqual(self.refresh_queues(token).status_code, 403)
            self.assertEqual(Session.objects.get(session_key=key).expire_date, deadline)
        Session.objects.filter(session_key=key).update(expire_date=timezone.now()-timedelta(seconds=1))
        self.assertIn(self.refresh_queues(token).status_code, (302, 401))

    def test_queue_refresh_uses_count_sql_and_no_workflow_or_listings(self):
        import json
        from statistics import median
        from time import perf_counter
        self.correction()
        self.cml()
        token = self.dashboard().context['dashboard_scope_token']
        original = QuerySet._fetch_all
        def no_listing(queryset):
            if queryset.model in (GradeCorrectionRequest, ClassListChangeRequest):
                raise AssertionError('polling loaded request rows')
            return original(queryset)
        samples = []
        with patch.object(QuerySet, '_fetch_all', no_listing), patch.object(AdminScopeService, 'scoped_monitoring_course_offerings', wraps=AdminScopeService.scoped_monitoring_course_offerings) as selector:
            for _ in range(7):
                started = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self.refresh_queues(token)
                self.assertEqual(response.status_code, 200)
                sql = [q['sql'] for q in queries if 'FROM "grade_correction_requests"' in q['sql'] or 'FROM "class_list_change_requests"' in q['sql']]
                self.assertEqual(len(sql), 2)
                self.assertTrue(all('COUNT(' in q and 'DISTINCT' in q for q in sql))
                samples.append({'queries': len(queries), 'ms': round((perf_counter()-started)*1000, 3)})
            self.assertEqual(selector.call_count, 7)
        result = {'samples': samples, 'median_ms': median(row['ms'] for row in samples)}
        print('QUEUE_PERFORMANCE ' + json.dumps(result))
        if os.environ.get('TMP_ADMIN_EVIDENCE'):
            Path(os.environ['TMP_ADMIN_EVIDENCE'], 'queue-performance.json').write_text(json.dumps(result), encoding='utf-8')

    def test_concurrent_scope_change_during_poll_is_rejected_and_never_overwritten(self):
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.sessions.models import Session
        from . import dashboard as selectors
        campus, a, b = self.multi_campus_cml()
        token = self.dashboard().context['dashboard_scope_token']
        key = self.client.session.session_key
        deadline = timezone.now()+timedelta(minutes=20)
        original = selectors.request_queues
        def changed_in_another_tab(*args, **kwargs):
            result = original(*args, **kwargs)
            concurrent = SessionStore(session_key=key)
            concurrent[ScopeService.SESSION_CAMPUS_KEY] = campus.pk
            concurrent.set_expiry(deadline)
            concurrent.save()
            return result
        with self.settings(SESSION_SAVE_EVERY_REQUEST=True), patch.object(selectors, 'request_queues', side_effect=changed_in_another_tab):
            self.assertEqual(self.refresh_queues(token).status_code, 409)
        saved = Session.objects.get(session_key=key)
        self.assertEqual(saved.expire_date, deadline)
        self.assertEqual(saved.get_decoded()[ScopeService.SESSION_CAMPUS_KEY], campus.pk)

    def test_refresh_preserves_supervision_and_cross_tenant_isolation(self):
        self.test_cross_tenant_and_campus_rows_are_excluded()
        response = self.refresh_queues()
        self.assertEqual(response.json()['correction']['pending_count'], 1)
        self.assertEqual(response.json()['cml']['pending_count'], 1)
        outside = Department.objects.create(tenant=self.tenant, campus=self.campus, code='UNSUP', name='Unsupervised')
        UserRole.objects.filter(user=self.faculty_user).update(department=outside)
        self.faculty_user.default_department = outside
        self.faculty_user.save(update_fields=['default_department'])
        response = self.refresh_queues()
        self.assertEqual(response.json()['correction']['pending_count'], 0)

    def test_refresh_preserves_existing_superuser_semantics(self):
        self.test_current_superuser_permission_semantics_are_preserved()
        response = self.refresh_queues()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['correction']['pending_count'], 1)
        self.assertEqual(response.json()['cml']['pending_count'], 1)
