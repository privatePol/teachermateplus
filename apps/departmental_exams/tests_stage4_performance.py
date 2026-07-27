"""Bounded-query and batched-propagation regression tests for Stage 4."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from unittest.mock import patch

from .models import CourseExamConfiguration, ExaminationCycle
from .services import ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TestCase


class Stage4PerformanceTests(Stage4TestCase):
    def _measure_assigned_page(self, additional_rows, *, scope_suffix):
        cycle = self.make_cycle(
            mode=ExaminationCycle.ItemCountMode.PER_COURSE,
            scope_suffix=scope_suffix,
        )
        for index in range(additional_rows + 1):
            self.make_course(cycle=cycle, code=f"PERF-{index}")
        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("departmental_exams:assigned_course_examinations"))
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_assigned_course_readiness_and_snapshots_have_bounded_query_growth(self):
        small = self._measure_assigned_page(1, scope_suffix="performance-small")
        # A separate cycle prevents fixture reuse from hiding list-query growth.
        large = self._measure_assigned_page(25, scope_suffix="performance-large")
        self.assertLessEqual(large, small + 4)

    def test_large_fixed_mode_propagation_uses_bounded_batch_size(self):
        cycle = self.make_cycle()
        for index in range(205):
            self.make_course(cycle=cycle, code=f"BATCH-{index}")
        batch_sizes = []
        original_bulk_create = CourseExamConfiguration.objects.bulk_create
        with patch.object(
            CourseExamConfiguration.objects,
            "bulk_create",
            side_effect=lambda rows, **kwargs: (
                batch_sizes.append(len(rows)), original_bulk_create(rows, **kwargs)
            )[1],
        ):
            cycle, _ = ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=44, contributor_instructions="",
            )
        self.assertEqual(CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle).count(), 205)
        self.assertEqual(ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE, 200)
        self.assertEqual(batch_sizes, [200, 5])
