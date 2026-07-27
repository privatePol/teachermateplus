"""Focused Stage 4 regression coverage.  Execution is deferred to Gate 3."""

from django.test import TestCase
from django.utils import timezone

from .forms import CourseExamConfigurationForm, ExaminationCycleConfigurationForm
from .models import ExaminationCycle


class Stage4ConfigurationFormTests(TestCase):
    def test_fixed_mode_requires_fixed_count(self):
        form = ExaminationCycleConfigurationForm(data={
            "item_count_mode": ExaminationCycle.ItemCountMode.FIXED_ALL,
            "fixed_final_item_count": "",
            "contributor_instructions": "  Cycle guidance  ",
            "expected_updated_at": "2026-07-26T00:00:00+00:00",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("fixed_final_item_count", form.errors)

    def test_per_course_mode_clears_fixed_count(self):
        form = ExaminationCycleConfigurationForm(data={
            "item_count_mode": ExaminationCycle.ItemCountMode.PER_COURSE,
            "fixed_final_item_count": "50",
            "contributor_instructions": "  Cycle guidance  ",
            "expected_updated_at": "2026-07-26T00:00:00+00:00",
        })
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["fixed_final_item_count"])
        self.assertEqual(form.cleaned_data["contributor_instructions"], "Cycle guidance")

    def test_counts_allow_one_to_two_hundred_and_remain_separate(self):
        form = CourseExamConfigurationForm(data={
            "final_item_count": "1",
            "questions_required_per_faculty": "200",
            "coverage": "  Core outcomes  ",
            "additional_instructions": "  Optional note  ",
            "contribution_deadline": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M"),
            "expected_revision": "1",
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["final_item_count"], 1)
        self.assertEqual(form.cleaned_data["questions_required_per_faculty"], 200)
        self.assertEqual(form.cleaned_data["coverage"], "Core outcomes")
        self.assertEqual(form.cleaned_data["additional_instructions"], "Optional note")

    def test_count_outside_approved_range_is_rejected(self):
        form = CourseExamConfigurationForm(data={
            "final_item_count": "201",
            "questions_required_per_faculty": "0",
            "coverage": "Coverage",
            "contribution_deadline": "2026-08-01T12:00",
            "expected_revision": "1",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("final_item_count", form.errors)
        self.assertIn("questions_required_per_faculty", form.errors)
