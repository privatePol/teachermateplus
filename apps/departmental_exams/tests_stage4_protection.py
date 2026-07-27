"""Regression protections against RBAC/navigation and future-stage leakage."""

from django.urls import NoReverseMatch, reverse

from apps.navigation.models import MenuGroup, MenuItem
from apps.rbac.models import Permission

from .stage4_test_support import Stage4TestCase


class Stage4FutureStageProtectionTests(Stage4TestCase):
    def test_original_permissions_and_two_item_menu_remain_the_only_surface(self):
        self.assertSetEqual(
            set(Permission.objects.filter(module="departmental_exams").values_list("code", flat=True)),
            {
                "departmental_exams.manage_cycles",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            },
        )
        group = MenuGroup.objects.get(portal="ADMIN", code="DEPARTMENTAL_EXAMS")
        self.assertSetEqual(
            set(MenuItem.objects.filter(menu_group=group).values_list("code", flat=True)),
            {"DE_EXAM_CYCLES", "DE_EXAM_ASSIGNED_COURSES"},
        )

    def test_stage_five_and_later_routes_are_not_exposed(self):
        for route_name in (
            "departmental_exams:faculty_contribution_list",
            "departmental_exams:question_encode",
            "departmental_exams:question_import",
            "departmental_exams:questionnaire_generate",
            "departmental_exams:answer_key",
            "departmental_exams:pdf_generate",
            "departmental_exams:pair_code",
            "departmental_exams:qr_code",
        ):
            with self.assertRaises(NoReverseMatch):
                reverse(route_name)
