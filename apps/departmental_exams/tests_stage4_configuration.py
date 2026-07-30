"""Focused Stage 4 regression coverage.  Execution is deferred to Gate 3."""

from django.test import TestCase
from django.utils import timezone

from .forms import CourseExamConfigurationForm, ExaminationCycleConfigurationForm
from .models import CourseExamConfiguration, ExaminationCycle


class Stage4ConfigurationFormTests(TestCase):
    def test_cycle_default_ranges_are_enforced(self):
        form = ExaminationCycleConfigurationForm(data={
            "default_questions_required_per_faculty": "49",
            "default_final_item_count": "76",
            "contributor_instructions": "  Cycle guidance  ",
            "expected_updated_at": "2026-07-26T00:00:00+00:00",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("default_questions_required_per_faculty", form.errors)
        self.assertIn("default_final_item_count", form.errors)

    def test_cycle_defaults_accept_boundaries(self):
        form = ExaminationCycleConfigurationForm(data={
            "default_questions_required_per_faculty": "50",
            "default_final_item_count": "75",
            "contributor_instructions": "  Cycle guidance  ",
            "expected_updated_at": "2026-07-26T00:00:00+00:00",
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["default_questions_required_per_faculty"], 50)
        self.assertEqual(form.cleaned_data["default_final_item_count"], 75)
        self.assertEqual(form.cleaned_data["contributor_instructions"], "Cycle guidance")

    def test_course_count_boundaries_and_sources_remain_separate(self):
        form = CourseExamConfigurationForm(data={
            "final_item_count": "50",
            "final_item_count_mode": "OVERRIDE",
            "final_item_count_source": "OVERRIDE",
            "questions_required_per_faculty": "75",
            "questions_required_per_faculty_mode": "OVERRIDE",
            "questions_required_per_faculty_source": "OVERRIDE",
            "cycle_defaults_revision_snapshot": "0",
            "coverage": "  Core outcomes  ",
            "additional_instructions": "  Optional note  ",
            "contribution_deadline": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M"),
            "expected_revision": "1",
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["final_item_count"], 50)
        self.assertEqual(form.cleaned_data["questions_required_per_faculty"], 75)
        self.assertEqual(form.cleaned_data["coverage"], "Core outcomes")
        self.assertEqual(form.cleaned_data["additional_instructions"], "Optional note")

    def test_count_outside_approved_range_is_rejected(self):
        form = CourseExamConfigurationForm(data={
            "final_item_count": "76",
            "final_item_count_mode": "OVERRIDE",
            "final_item_count_source": "OVERRIDE",
            "questions_required_per_faculty": "49",
            "questions_required_per_faculty_mode": "OVERRIDE",
            "questions_required_per_faculty_source": "OVERRIDE",
            "cycle_defaults_revision_snapshot": "0",
            "coverage": "Coverage",
            "contribution_deadline": "2026-08-01T12:00",
            "expected_revision": "1",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("final_item_count", form.errors)
        self.assertIn("questions_required_per_faculty", form.errors)
