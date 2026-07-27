"""Database-aware Stage 4 locking tests; SQLite skips row-lock assertions explicitly."""

import threading
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection

from .models import CourseExamConfiguration, ExaminationCycle
from .services import CourseExamConfigurationService, ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TransactionTestCase


class Stage4LockingTests(Stage4TransactionTestCase):
    def _openable(self):
        cycle = self.make_cycle(mode=ExaminationCycle.ItemCountMode.PER_COURSE)
        cycle, _ = ExaminationCycleConfigurationService.open_cycle(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
        )
        parent = self.make_course(cycle=cycle)
        configuration, _ = CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=0,
            final_item_count=40, questions_required_per_faculty=10, coverage="Locking coverage",
            additional_instructions="", contribution_deadline=self.future_deadline(),
        )
        return cycle, parent, configuration

    def test_parent_lock_helper_precedes_child_lookup(self):
        _cycle, parent, configuration = self._openable()
        calls = []
        original_parent = __import__("apps.departmental_exams.services", fromlist=["CycleCourseInclusionService"]).CycleCourseInclusionService.lock_included_cycle_course
        with patch("apps.departmental_exams.services.CycleCourseInclusionService.lock_included_cycle_course", side_effect=lambda **kwargs: (calls.append("parent"), original_parent(**kwargs))[1]), patch("apps.departmental_exams.services.CourseExamConfiguration.objects.select_for_update", wraps=CourseExamConfiguration.objects.select_for_update) as child_lock:
            CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
            )
        self.assertEqual(calls, ["parent"])
        self.assertTrue(child_lock.called)

    def test_cycle_lock_precedes_course_parent_lock(self):
        _cycle, parent, configuration = self._openable()
        calls = []
        original_cycle = ExaminationCycle.objects.select_for_update
        from .models import CycleCourse
        original_parent = CycleCourse.objects.select_for_update

        with patch.object(
            ExaminationCycle.objects,
            "select_for_update",
            side_effect=lambda *args, **kwargs: (calls.append("cycle"), original_cycle(*args, **kwargs))[1],
        ), patch.object(
            CycleCourse.objects,
            "select_for_update",
            side_effect=lambda *args, **kwargs: (calls.append("parent"), original_parent(*args, **kwargs))[1],
        ):
            CourseExamConfigurationService.open_for_contribution(
                cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                expected_revision=configuration.revision,
            )
        self.assertEqual(calls[:2], ["cycle", "parent"])

    def test_propagation_locks_parent_batches_in_stable_id_order(self):
        cycle = self.make_cycle()
        parents = [self.make_course(cycle=cycle, code=f"LOCK-{index}") for index in range(5)]
        observed = []
        original = CourseExamConfiguration.objects.bulk_create
        with patch.object(CourseExamConfiguration.objects, "bulk_create", side_effect=lambda rows, **kwargs: (observed.extend(row.cycle_course_id for row in rows), original(rows, **kwargs))[1]):
            ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=35, contributor_instructions="",
            )
        self.assertEqual(observed, sorted(parent.id for parent in parents))

    def test_failed_propagation_rolls_back_cycle_and_created_children(self):
        cycle = self.make_cycle()
        parent = self.make_course(cycle=cycle)
        with patch("apps.departmental_exams.services.CourseExamConfiguration.objects.bulk_create", side_effect=RuntimeError("batch failure")):
            with self.assertRaises(RuntimeError):
                ExaminationCycleConfigurationService.save_cycle_configuration(
                    cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
                    expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
                    item_count_mode=ExaminationCycle.ItemCountMode.FIXED_ALL, fixed_final_item_count=35, contributor_instructions="",
                )
        cycle.refresh_from_db()
        self.assertIsNone(cycle.item_count_mode)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=parent).exists())

    @skipUnless(connection.vendor != "sqlite", "SQLite does not provide the row-lock behavior required for concurrent Stage 4 assertions.")
    def test_two_concurrent_open_requests_have_one_real_transition(self):
        _cycle, parent, configuration = self._openable()
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def open_once():
            close_old_connections()
            try:
                barrier.wait()
                result = CourseExamConfigurationService.open_for_contribution(
                    cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer, expected_revision=configuration.revision,
                )
                results.append(result[1])
            except Exception as exc:  # surfaced in the owning test thread
                errors.append(exc)
            finally:
                close_old_connections()

        workers = [threading.Thread(target=open_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertFalse(errors, errors)
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
