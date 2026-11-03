"""UI/save-boundary regressions for the reminder card and its existing endpoint."""
import os
from datetime import time
from html.parser import HTMLParser
from pathlib import Path

from django import forms
from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.correction_reminders import CorrectionReminderPolicy as Policy
from apps.grading.models import CorrectionEmailDelivery, CorrectionStepNotificationIntent
from apps.grading.tests_correction_reminders import ReminderFixtures
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Tenant


class ReminderMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = dict(attrs, tag=tag, ancestors=list(self.stack))
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                self.stack = self.stack[:index]
                break

    def by_id(self, value):
        return next(node for node in self.nodes if node.get("id") == value)


class CorrectionReminderUiTests(ReminderFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.reviewer_user)
        self.configuration_url = reverse("admin_portal:configurable_features_settings")
        self.settings_url = reverse("admin_portal:correction_email_settings")

    def test_grid_has_one_reminder_card_with_independent_form_ownership(self):
        response = self.client.get(self.configuration_url)
        self.assertEqual(response.status_code, 200)
        markup = ReminderMarkup(response.content.decode())
        card = markup.by_id("correction-reminders-card")
        self.assertTrue(any("settings-card-grid" in node.get("class", "").split() for node in card["ancestors"]))
        external_form = markup.by_id("correction-reminder-settings")
        self.assertEqual(external_form["action"], self.settings_url)
        self.assertFalse(any(node["tag"] == "form" for node in external_form["ancestors"]))
        self.assertFalse(any(node["tag"] == "form" and any(parent["tag"] == "form" for parent in node["ancestors"]) for node in markup.nodes))
        for name in ("enabled", "interval_days", "send_time"):
            controls = [node for node in markup.nodes if node.get("name") == name]
            self.assertEqual(len(controls), 1)
            self.assertEqual(controls[0]["form"], "correction-reminder-settings")
        self.assertEqual(markup.by_id("id_enabled")["role"], "switch")
        self.assertEqual(markup.by_id("id_send_time")["value"], "08:00")
        self.assertNotContains(response, "Configure correction reminders</a>")
        self.assertEqual(CorrectionEmailDelivery.objects.count(), 0)
        self.assertEqual(CorrectionStepNotificationIntent.objects.count(), 0)

    def test_card_save_preserves_other_tenant_and_unrelated_features_and_audits(self):
        other_tenant = Tenant.objects.create(code="UI-OTHER", name="Other tenant")
        Policy.save(other_tenant.pk, {"enabled": True, "interval_days": 7, "send_time": time(9)})
        SystemSettingService.set(FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY, True,
            tenant_id=self.tenant.pk, value_type="BOOL")
        response = self.client.post(self.settings_url, {
            "interval_days": "3", "send_time": "10:15", "return_to": "configuration",
            "tenant_id": other_tenant.pk,
        })
        self.assertRedirects(response, self.configuration_url + "#correction-reminders-card", fetch_redirect_response=False)
        self.assertEqual(Policy.get(self.tenant.pk), {"enabled": False, "interval_days": 3, "send_time": time(10, 15)})
        self.assertEqual(Policy.get(other_tenant.pk)["interval_days"], 7)
        self.assertTrue(FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=self.tenant.pk))
        self.assertTrue(AuditLog.objects.filter(entity_id=Policy.KEY, tenant_id=self.tenant.pk, action="UPDATE").exists())

    def test_general_feature_save_does_not_submit_or_change_reminder_policy(self):
        Policy.save(self.tenant.pk, {"enabled": False, "interval_days": 7, "send_time": time(11)})
        response = self.client.get(self.configuration_url)
        form = response.context["form"]
        data = {name: ("on" if form[name].value() else "") if isinstance(field, forms.BooleanField)
                else form[name].value() if form[name].value() is not None else ""
                for name, field in form.fields.items()}
        data["faculty_attendance_enabled"] = "on"
        response = self.client.post(self.configuration_url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Policy.get(self.tenant.pk), {"enabled": False, "interval_days": 7, "send_time": time(11)})
        self.assertTrue(FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=self.tenant.pk))

    def test_invalid_card_submission_keeps_values_and_errors_on_styled_page(self):
        before = Policy.get(self.tenant.pk)
        response = self.client.post(self.settings_url, {
            "enabled": "on", "interval_days": "0", "send_time": "25:99", "return_to": "configuration",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Policy.get(self.tenant.pk), before)
        self.assertFormError(response.context["form"], "send_time", "Enter a valid time.")
        self.assertContains(response, 'class="alert alert-danger" role="alert"')
        self.assertContains(response, 'name="return_to" value="configuration"')
        self.assertContains(response, "feature_settings.css")
        self.assertContains(response, 'aria-invalid="true"')
        self.export("invalid", response)

    def test_dedicated_route_preserves_defaults_saving_and_return_navigation(self):
        response = self.client.get(self.settings_url)
        self.assertContains(response, 'class="form-select"')
        self.assertContains(response, 'class="form-control"')
        self.assertContains(response, self.configuration_url + "#correction-reminders-card")
        self.assertTrue(response.context["form"]["enabled"].value())
        self.assertEqual(response.context["form"]["send_time"].value(), time(8))
        self.export("dedicated", response)
        self.export("configuration", self.client.get(self.configuration_url))
        response = self.client.post(self.settings_url, {"enabled": "on", "interval_days": "2", "send_time": "08:30"})
        self.assertRedirects(response, self.settings_url)
        self.assertEqual(Policy.get(self.tenant.pk)["send_time"], time(8, 30))

    def test_card_save_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reviewer_user)
        response = client.get(self.configuration_url)
        self.assertEqual(response.status_code, 200)
        data = {"enabled": "on", "interval_days": "1", "send_time": "08:00", "return_to": "configuration"}
        self.assertEqual(client.post(self.settings_url, data).status_code, 403)
        data["csrfmiddlewaretoken"] = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertEqual(client.post(self.settings_url, data).status_code, 302)

    def test_unauthorized_user_cannot_read_or_save_either_route(self):
        self.client.force_login(self.faculty_user)
        for url in (self.configuration_url, self.settings_url):
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"interval_days": "7", "send_time": "11:00"}).status_code, 403)

    def test_privileged_direct_deny_hides_card_and_blocks_save(self):
        self.reviewer_user.is_superuser = True
        self.reviewer_user.save(update_fields=["is_superuser"])
        UserPermission.objects.create(user=self.reviewer_user, permission=Permission.objects.get(code="system_settings.update"),
            grant_type="DENY")
        response = self.client.get(self.configuration_url)
        if response.status_code == 200:
            self.assertNotContains(response, 'id="correction-reminders-card"')
            self.assertNotContains(response, 'id="correction-reminder-settings"')
        else:
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(self.settings_url, {"interval_days": "7", "send_time": "11:00"}).status_code, 403)

    @staticmethod
    def export(name, response):
        if os.environ.get("TMP_CORRECTION_UI_EVIDENCE"):
            Path(os.environ["TMP_CORRECTION_UI_EVIDENCE"], name + ".html").write_text(response.content.decode(), encoding="utf-8")
