"""Bounded-query and payload tests for CAO default propagation."""

from django.test.utils import CaptureQueriesContext
from django.db import connection
from unittest.mock import patch

from .models import (
    CourseExamConfiguration,
    ExaminationCycle,
    FacultyContribution,
    Question,
)
from .services import (
    DepartmentalExamAuthorizationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class CAOPerformanceTests(Stage4TestCase):
    def _propagate(self, cycle, *, quota=50, final_count=50):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=quota,
            default_final_item_count=final_count,
            contributor_instructions="CAO instructions",
        )

    def _measure_writer(self, *, parent_count, scope_suffix):
        cycle = self.make_cycle(scope_suffix=scope_suffix)
        for number in range(parent_count):
            self.make_course(cycle=cycle, code=f"PERF-{scope_suffix}-{number}")
        contribution_manager = FacultyContribution.objects
        question_manager = Question.objects
        with patch.object(
            DepartmentalExamAuthorizationService, "require_permission"
        ) as cycle_permission, patch.object(
            DepartmentalExamAuthorizationService, "require_configure_cycle_course"
        ) as per_course_permission, patch.object(
            contribution_manager,
            "filter",
            wraps=contribution_manager.filter,
        ) as contribution_queries, patch.object(
            question_manager,
            "filter",
            wraps=question_manager.filter,
        ) as question_queries, patch(
            "apps.departmental_exams.services.AuditService.log_event"
        ) as audit, CaptureQueriesContext(connection) as queries:
            _cycle, changed = self._propagate(cycle)

        self.assertTrue(changed)
        cycle_permission.assert_called_once_with(
            user=self.manager,
            permission="departmental_exams.manage_cycles",
            tenant_id=self.tenant.id,
        )
        per_course_permission.assert_not_called()
        audit.assert_called_once()
        for activity_call in contribution_queries.call_args_list:
            self.assertEqual(set(activity_call.kwargs), {"cycle_course_id__in"})
            self.assertLessEqual(
                len(activity_call.kwargs["cycle_course_id__in"]),
                ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE,
            )
        for activity_call in question_queries.call_args_list:
            self.assertEqual(
                set(activity_call.kwargs),
                {"contribution__cycle_course_id__in"},
            )
            self.assertLessEqual(
                len(activity_call.kwargs["contribution__cycle_course_id__in"]),
                ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE,
            )
        return {
            "query_count": len(queries),
            "contribution_activity_queries": contribution_queries.call_count,
            "question_activity_queries": question_queries.call_count,
        }

    def test_propagation_queries_are_exactly_set_wise_per_batch(self):
        # A five-parent test batch exposes the 5/6 boundary and reaches three
        # batches at 11 parents without changing the production bound.
        with patch.object(
            ExaminationCycleConfigurationService, "PROPAGATION_BATCH_SIZE", 5
        ):
            one_parent = self._measure_writer(
                parent_count=1, scope_suffix="one-parent"
            )
            full_batch = self._measure_writer(
                parent_count=5, scope_suffix="full-batch"
            )
            over_boundary = self._measure_writer(
                parent_count=6, scope_suffix="over-boundary"
            )
            three_batches = self._measure_writer(
                parent_count=11, scope_suffix="three-batches"
            )

        self.assertEqual(
            [
                one_parent["contribution_activity_queries"],
                full_batch["contribution_activity_queries"],
                over_boundary["contribution_activity_queries"],
                three_batches["contribution_activity_queries"],
            ],
            [1, 1, 2, 3],
        )
        self.assertEqual(
            [
                one_parent["question_activity_queries"],
                full_batch["question_activity_queries"],
                over_boundary["question_activity_queries"],
                three_batches["question_activity_queries"],
            ],
            [1, 1, 2, 3],
        )
        self.assertEqual(
            one_parent["query_count"],
            full_batch["query_count"],
        )
        first_batch_slope = (
            over_boundary["query_count"] - full_batch["query_count"]
        )
        second_batch_slope = (
            three_batches["query_count"] - over_boundary["query_count"]
        )
        self.assertGreater(first_batch_slope, 0)
        self.assertEqual(second_batch_slope, first_batch_slope)

    def test_propagation_audit_payload_is_bounded_to_one_batch_of_identifiers(self):
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            scope_suffix="bounded-audit",
        )
        for number in range(ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE + 2):
            parent = self.make_course(cycle=cycle, code=f"AUDIT{number}")
            self.make_configuration(
                parent,
                quota=50,
                final_count=50,
                quota_source="DEFAULT",
                final_source="DEFAULT",
            )
        configuration_manager = CourseExamConfiguration.objects
        with patch.object(
            configuration_manager,
            "bulk_update",
            wraps=configuration_manager.bulk_update,
        ) as bulk_update, patch(
            "apps.departmental_exams.services.AuditService.log_event"
        ) as audit:
            _cycle, changed = self._propagate(cycle, quota=55, final_count=55)
        self.assertTrue(changed)
        self.assertEqual(CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle).count(), ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE + 2)
        payload = audit.call_args.kwargs["metadata"]["propagation"]
        self.assertEqual(payload["created"], 0)
        self.assertEqual(
            payload["updated"],
            ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE + 2,
        )
        self.assertEqual(
            len(payload["affected_configuration_ids"]),
            ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE,
        )
        self.assertEqual(payload["excluded_by_reason"], {})
        self.assertEqual(
            [len(call.args[0]) for call in bulk_update.call_args_list],
            [ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE, 2],
        )
