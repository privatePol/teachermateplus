from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class GovernanceAnomalyDetectionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.other_tenant = Tenant.objects.create(code="OTHER", name="Other")
        self.other_campus = Campus.objects.create(tenant=self.other_tenant, code="BR", name="Branch")

        self.admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.dashboard_read = Permission.objects.create(
            code="dashboard.read",
            module="dashboard",
            action="read",
        )
        self.audit_logs_read = Permission.objects.create(
            code="audit_logs.read",
            module="audit_logs",
            action="read",
        )

        self.audit_role = Role.objects.create(code="AUDITOR", name="Auditor")
        RolePermission.objects.create(role=self.audit_role, permission=self.admin_access)
        RolePermission.objects.create(role=self.audit_role, permission=self.dashboard_read)
        RolePermission.objects.create(role=self.audit_role, permission=self.audit_logs_read)

        self.dashboard_role = Role.objects.create(code="DASH_ONLY", name="Dashboard Only")
        RolePermission.objects.create(role=self.dashboard_role, permission=self.admin_access)
        RolePermission.objects.create(role=self.dashboard_role, permission=self.dashboard_read)

        self.admin = self._user("admin")
        self.dashboard_user = self._user("dash")
        UserRole.objects.create(user=self.admin, role=self.audit_role, tenant=self.tenant, campus=self.campus)
        UserRole.objects.create(
            user=self.dashboard_user,
            role=self.dashboard_role,
            tenant=self.tenant,
            campus=self.campus,
        )

    def _user(self, username):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _codes(self, log):
        return {flag["rule_code"] for flag in log.metadata_json.get("anomaly_flags_json", [])}

    def test_reopen_anomaly_flags_are_stored_in_audit_metadata(self):
        for index in range(3):
            AuditLog.objects.create(
                actor_user=self.admin,
                portal="ADMIN",
                action="REOPEN",
                entity_type="GradingPeriodLock",
                entity_id=str(index),
                tenant=self.tenant,
                campus=self.campus,
                metadata_json={"impact_summary": {"course_offering_id": 101}},
            )

        log = AuditService.log_event(
            action="REOPEN",
            portal="ADMIN",
            entity_type="GradingPeriodLock",
            entity_id=99,
            actor=self.admin,
            tenant=self.tenant,
            campus=self.campus,
            metadata={
                "critical_action": True,
                "impact_summary": {
                    "course_offering_id": 101,
                    "target_offering_count": 2,
                    "deadline_at": (timezone.now() - timedelta(days=1)).isoformat(),
                },
            },
        )

        codes = self._codes(log)
        self.assertIn("PERIOD_REOPEN_HIGH_DAILY_COUNT", codes)
        self.assertIn("PERIOD_REOPEN_SAME_CLASS_REPEAT", codes)
        self.assertIn("PERIOD_REOPEN_AFTER_DEADLINE", codes)
        self.assertIn("PERIOD_REOPEN_MULTI_CLASS", codes)
        self.assertTrue(log.metadata_json["has_anomaly_flags"])

    def test_hotfix_scope_anomaly_flags(self):
        log = AuditService.log_event(
            action="APPROVE",
            portal="ADMIN",
            entity_type="TemplateHotfixRequest",
            entity_id=1,
            actor=self.admin,
            tenant=self.tenant,
            campus=self.campus,
            metadata={
                "critical_action": True,
                "impact_summary": {
                    "target_offering_count": 11,
                    "campus_count": 2,
                    "near_or_after_deadline_offering_count": 1,
                    "submitted_or_reopened_offering_count": 1,
                },
            },
        )

        codes = self._codes(log)
        self.assertIn("HOTFIX_LARGE_SCOPE", codes)
        self.assertIn("HOTFIX_MULTI_CAMPUS", codes)
        self.assertIn("HOTFIX_NEAR_OR_AFTER_DEADLINE", codes)
        self.assertIn("HOTFIX_PARTIAL_RESULT", codes)

    def test_correction_fail_to_pass_and_final_grade_flags(self):
        period_log = AuditService.log_event(
            action="RECOMPUTE",
            portal="SYSTEM",
            entity_type="StudentPeriodGrade",
            entity_id=7,
            actor=self.admin,
            tenant=self.tenant,
            campus=self.campus,
            before_data={"period_grade": "74"},
            after_data={"period_grade": "76"},
            metadata={
                "reason": "CORRECTION_SCORE_APPLY",
                "offering_id": 88,
                "student_id": 44,
                "passing_threshold": "75",
            },
        )
        final_log = AuditService.log_event(
            action="RECOMPUTE",
            portal="SYSTEM",
            entity_type="StudentFinalGrade",
            entity_id=8,
            actor=self.admin,
            tenant=self.tenant,
            campus=self.campus,
            before_data={"final_grade": "74"},
            after_data={"final_grade": "76"},
            metadata={
                "reason": "CORRECTION_SCORE_APPLY",
                "offering_id": 88,
                "student_id": 44,
                "passing_threshold": "75",
            },
        )

        self.assertIn("CORRECTION_FAIL_TO_PASS", self._codes(period_log))
        self.assertIn("CORRECTION_FINAL_GRADE_CHANGED", self._codes(final_log))
        self.assertIn("CORRECTION_FAIL_TO_PASS", self._codes(final_log))

    def test_role_permission_anomaly_flags(self):
        target_role = Role.objects.create(code="CRITICAL", name="Critical")
        UserRole.objects.create(user=self.admin, role=target_role, tenant=self.tenant, campus=self.campus)

        log = AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="RolePermission",
            entity_id=target_role.id,
            actor=self.admin,
            tenant=self.tenant,
            campus=self.campus,
            metadata={
                "critical_action": True,
                "impact_summary": {
                    "critical_added_permission_codes": ["actual_data_reset.run"],
                    "affected_active_user_count": 12,
                },
            },
        )

        codes = self._codes(log)
        self.assertIn("ROLE_CRITICAL_PERMISSION_GRANTED", codes)
        self.assertIn("ROLE_SELF_PERMISSION_CHANGE", codes)
        self.assertIn("ROLE_PERMISSION_MANY_USERS", codes)

    def test_dashboard_alerts_require_audit_permission_and_follow_scope(self):
        AuditLog.objects.create(
            actor_user=self.admin,
            portal="ADMIN",
            action="APPROVE",
            entity_type="TemplateHotfixRequest",
            entity_id="10",
            tenant=self.tenant,
            campus=self.campus,
            metadata_json={
                "has_anomaly_flags": True,
                "max_anomaly_severity": "high",
                "anomaly_flags_json": [
                    {
                        "rule_code": "HOTFIX_LARGE_SCOPE",
                        "severity": "high",
                        "message": "Visible scoped alert.",
                    }
                ],
            },
        )
        AuditLog.objects.create(
            actor_user=self.admin,
            portal="ADMIN",
            action="APPROVE",
            entity_type="TemplateHotfixRequest",
            entity_id="11",
            tenant=self.other_tenant,
            campus=self.other_campus,
            metadata_json={
                "has_anomaly_flags": True,
                "max_anomaly_severity": "high",
                "anomaly_flags_json": [
                    {
                        "rule_code": "HOTFIX_LARGE_SCOPE",
                        "severity": "high",
                        "message": "Other tenant alert.",
                    }
                ],
            },
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Governance Alerts")
        self.assertContains(response, "Visible scoped alert.")
        self.assertNotContains(response, "Other tenant alert.")

        self.client.force_login(self.dashboard_user)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Governance Alerts")

    def test_recent_critical_actions_report_shows_flags_column(self):
        AuditLog.objects.create(
            actor_user=self.admin,
            portal="ADMIN",
            action="APPROVE",
            entity_type="TemplateHotfixRequest",
            entity_id="20",
            tenant=self.tenant,
            campus=self.campus,
            metadata_json={
                "critical_action": True,
                "reason": "Approved scoped hotfix.",
                "has_anomaly_flags": True,
                "max_anomaly_severity": "high",
                "anomaly_flags_json": [
                    {
                        "rule_code": "HOTFIX_LARGE_SCOPE",
                        "severity": "high",
                        "message": "Template hotfix affects more than 10 offerings.",
                    }
                ],
            },
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_portal:recent_critical_actions"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flags")
        self.assertContains(response, "Template hotfix affects more than 10 offerings.")
