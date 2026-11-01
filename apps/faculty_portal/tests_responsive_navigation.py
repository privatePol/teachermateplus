"""Render real scoped menus and optionally export disposable browser fixtures."""

from html.parser import HTMLParser
import os
from pathlib import Path
import re
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.faculty_portal.tests_dashboard_responsive import DashboardFixture
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class FacultyResponsiveNavigationTests(DashboardFixture, TestCase):
    widths = (375, 390, 440, 767, 768, 992)

    def setUp(self):
        super().setUp()
        self.permission, _ = Permission.objects.get_or_create(code="faculty_analytics.read",
            defaults={"module": "faculty_analytics", "action": "read"})
        RolePermission.objects.get_or_create(role=Role.objects.get(code="FACULTY"), permission=self.permission)
        group, _ = MenuGroup.objects.get_or_create(portal="FACULTY", code="FACULTY_DASHBOARD",
                                                  defaults={"label": "Dashboard"})
        self.menu, _ = MenuItem.objects.update_or_create(portal="FACULTY", code="FACULTY_ANALYTICS",
            defaults={"menu_group": group, "label": "Grading Analytics", "route_name": "faculty_portal:analytics",
                      "is_active": True})
        MenuItemPermission.objects.get_or_create(menu_item=self.menu, permission=self.permission)
        for number in range(1, 8):
            self.add_offering(number)

    def assert_links(self, response, *, analytics):
        links = Links(response.content.decode()).links
        for route, count in (("analytics", int(analytics)), ("parallel_section_comparison", 2), ("activity_history", 2)):
            matching = [link for link in links if link.get("href") == reverse(f"faculty_portal:{route}")]
            self.assertEqual(len(matching), count, route)
            for link in matching:
                self.assertNotIn("faculty-mobile-hidden", link.get("class", "").split())
        html = response.content.decode()
        self.assertLess(html.index("</main>"), html.index('class="faculty-utility-stack"'))
        for label in ("Feedback", "Quick Guide", "Help &amp; Privacy", "Data Privacy Notice"):
            self.assertContains(response, label)
        for row in response.context["stats"]["grade_status_rows"]:
            self.assertTrue(row["review_url"])
            self.assertTrue(row["performance_url"])
        return html

    def export(self, html, variant):
        destination = os.environ.get("TMP_FACULTY_RESPONSIVE_EVIDENCE")
        if not destination:
            return
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        bootstrap = output / "bootstrap.min.css"
        self.assertTrue(bootstrap.exists(), "Provide the existing local Bootstrap CSS for offline browser tests")
        # No server/authenticated session is used by Chrome: only synthetic
        # test output and local CSS/JS are exported. Strip external scripts.
        html = html.replace('href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"',
                            'href="bootstrap.min.css"')
        html = html.replace('/media/logos/', (Path(settings.BASE_DIR) / 'media' / 'logos').as_uri() + '/')
        for asset in ("css/faculty_dashboard.css", "js/faculty_sidebar.js"):
            source = Path(settings.BASE_DIR) / "static" / asset
            target = output / source.name
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            html = html.replace(f"/static/{asset}", source.name)
        html = re.sub(r'<script\b[^>]*src="https?://[^\"]*"[^>]*>.*?</script>', '', html, flags=re.S)
        (output / f"{variant}.html").write_text(html, encoding="utf-8")

    def test_authorized_denied_and_feature_disabled_menu_matrix(self):
        for variant in ("authorized", "denied", "feature-disabled"):
            denied = None
            if variant == "denied":
                denied = UserPermission.objects.create(user=self.faculty, permission=self.permission,
                    grant_type="DENY", tenant=self.tenant, campus=self.campus)
            enabled = variant != "feature-disabled"
            with patch("apps.core.context_processors.FeatureSettingsService.is_faculty_quick_tour_enabled", return_value=False), \
                 patch("apps.core.context_processors.FeatureSettingsService.can_user_access_grade_prediction", return_value=enabled), \
                 patch("apps.core.context_processors.FeatureSettingsService.is_grade_prediction_at_risk_enabled", return_value=enabled):
                for width in self.widths:
                    with self.subTest(variant=variant, width=width):
                        response = self.client.get(reverse("faculty_portal:dashboard"), HTTP_VIEWPORT_WIDTH=str(width))
                        self.assertEqual(response.status_code, 200)
                        html = self.assert_links(response, analytics=variant != "denied")
                        self.assertEqual('data-tour-id="at-risk-monitor"' in html, enabled)
                self.export(html, variant)
            if denied:
                self.assertEqual(self.client.get(reverse("faculty_portal:analytics")).status_code, 403)
                denied.delete()

    def test_inactive_menu_remains_absent(self):
        self.menu.is_active = False
        self.menu.save(update_fields=["is_active"])
        self.assert_links(self.dashboard(), analytics=False)

    def test_portal_direct_deny_blocks_requested_routes_at_all_widths(self):
        UserPermission.objects.create(user=self.faculty, permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type="DENY", tenant=self.tenant, campus=self.campus)
        for width in self.widths:
            for route in ("dashboard", "parallel_section_comparison", "activity_history"):
                with self.subTest(width=width, route=route):
                    response = self.client.get(reverse(f"faculty_portal:{route}"), HTTP_VIEWPORT_WIDTH=str(width))
                    self.assertEqual(response.status_code, 403)
                    self.assertNotContains(response, 'data-tour-id="performance-trends"', status_code=403)
