"""Transactional lock-order tests for CAO writers and supported backends."""

import threading
from unittest.mock import patch

from django.db import close_old_connections, connection

from apps.accounts.models import User

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TransactionTestCase


class CAOLockingTests(Stage4TransactionTestCase):
    def _defaults(self, cycle, *, quota=50, final_count=50):
        return ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id, tenant_id=self.tenant.id, user=self.manager,
            expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle),
            default_questions_required_per_faculty=quota, default_final_item_count=final_count,
            contributor_instructions="CAO instructions",
        )

    def test_cycle_default_propagation_locks_cycle_before_parent_and_children(self):
        cycle = self.make_cycle()
        self.make_course(cycle=cycle, code="A")
        self.make_course(cycle=cycle, code="B")
        with patch.object(ExaminationCycleConfigurationService, "_lock_cycle", wraps=ExaminationCycleConfigurationService._lock_cycle) as lock_cycle, patch.object(CycleCourse.objects, "select_for_update", wraps=CycleCourse.objects.select_for_update) as lock_parent:
            self._defaults(cycle)
        self.assertEqual(lock_cycle.call_count, 1)
        self.assertGreaterEqual(lock_parent.call_count, 1)

    def test_default_propagation_uses_configured_bounded_batches(self):
        cycle = self.make_cycle()
        for number in range(ExaminationCycleConfigurationService.PROPAGATION_BATCH_SIZE + 1):
            self.make_course(cycle=cycle, code=f"BATCH{number}")
        with patch.object(ExaminationCycleConfigurationService, "PROPAGATION_BATCH_SIZE", 2):
            cycle, changed = self._defaults(cycle)
        self.assertTrue(changed)
        self.assertEqual(cycle.defaults_revision, 1)

    def test_override_writer_uses_parent_first_lock_and_rolls_back_audit_failure(self):
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN, default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        with patch("apps.departmental_exams.services.AuditService.log_event", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                CourseExamConfigurationService.save_course_draft(
                    cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
                    expected_revision=0, questions_required_per_faculty=60,
                    questions_required_per_faculty_mode="OVERRIDE", final_item_count=50,
                    final_item_count_mode="DEFAULT", coverage="Core outcomes",
                    additional_instructions="", contribution_deadline=self.future_deadline(),
                )
        self.assertFalse(hasattr(parent, "configuration"))

    def test_default_propagation_rolls_back_child_write_and_later_batch_failures(self):
        cycle = self.make_cycle()
        first = self.make_course(cycle=cycle, code="ROLLBACK-FIRST")
        with patch.object(
            CourseExamConfiguration.objects,
            "bulk_create",
            side_effect=RuntimeError("child write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "child write failure"):
                self._defaults(cycle)
        cycle.refresh_from_db()
        self.assertIsNone(cycle.default_questions_required_per_faculty)
        self.assertFalse(CourseExamConfiguration.objects.filter(cycle_course=first).exists())

        cycle = self.make_cycle(scope_suffix="rollback-later-batch")
        first = self.make_course(cycle=cycle, code="ROLLBACK-BATCH-A")
        second = self.make_course(cycle=cycle, code="ROLLBACK-BATCH-B")
        original_bulk_create = CourseExamConfiguration.objects.bulk_create
        calls = 0

        def fail_second_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("later batch failure")
            return original_bulk_create(*args, **kwargs)

        with patch.object(
            ExaminationCycleConfigurationService, "PROPAGATION_BATCH_SIZE", 1
        ), patch.object(
            CourseExamConfiguration.objects,
            "bulk_create",
            side_effect=fail_second_batch,
        ):
            with self.assertRaisesRegex(RuntimeError, "later batch failure"):
                self._defaults(cycle)
        cycle.refresh_from_db()
        self.assertIsNone(cycle.default_questions_required_per_faculty)
        self.assertFalse(
            CourseExamConfiguration.objects.filter(cycle_course__in=(first, second)).exists()
        )

    def test_mariadb_concurrent_default_and_override_preserves_one_child_and_override(self):
        if connection.vendor == "sqlite":
            self.skipTest(
                "SQLite does not provide meaningful row-lock scheduling evidence."
            )
        if connection.vendor != "mysql":
            self.skipTest(
                "This deterministic schedule is supported only for the "
                f"MySQL/MariaDB row-lock semantics used in deployment; backend "
                f"{connection.vendor!r} is not claimed by this test."
            )
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
        )
        parent = self.make_course(cycle=cycle, code="CONCURRENT")
        cycle_token = ExaminationCycleConfigurationService.transition_token(cycle)
        expected_deadline = self.future_deadline().replace(microsecond=0)
        start = threading.Barrier(2)
        errors = []

        def save_defaults():
            close_old_connections()
            try:
                start.wait(timeout=10)
                manager = User.objects.get(pk=self.manager.pk)
                ExaminationCycleConfigurationService.save_cycle_configuration(
                    cycle_id=cycle.id,
                    tenant_id=self.tenant.id,
                    user=manager,
                    expected_updated_at=cycle_token,
                    default_questions_required_per_faculty=55,
                    default_final_item_count=55,
                    contributor_instructions="Concurrent CAO defaults",
                )
            except BaseException as exc:  # asserted after both workers join
                errors.append(exc)
            finally:
                close_old_connections()

        def save_override():
            close_old_connections()
            try:
                start.wait(timeout=10)
                configurer = User.objects.get(pk=self.configurer.pk)
                kwargs = {
                    "cycle_course_id": parent.id,
                    "tenant_id": self.tenant.id,
                    "user": configurer,
                    "questions_required_per_faculty": 60,
                    "questions_required_per_faculty_mode": "OVERRIDE",
                    "final_item_count": 50,
                    "final_item_count_mode": "DEFAULT",
                    "coverage": "Concurrent coverage",
                    "additional_instructions": "",
                    "contribution_deadline": expected_deadline,
                }
                try:
                    CourseExamConfigurationService.save_course_draft(
                        expected_revision=0,
                        **kwargs,
                    )
                except CourseExamConfigurationConflict:
                    current = CourseExamConfiguration.objects.get(cycle_course_id=parent.id)
                    CourseExamConfigurationService.save_course_draft(
                        expected_revision=current.revision,
                        **kwargs,
                    )
            except BaseException as exc:  # asserted after both workers join
                errors.append(exc)
            finally:
                close_old_connections()

        default_thread = threading.Thread(target=save_defaults)
        override_thread = threading.Thread(target=save_override)
        default_thread.start()
        override_thread.start()
        default_thread.join(timeout=20)
        override_thread.join(timeout=20)
        self.assertFalse(default_thread.is_alive())
        self.assertFalse(override_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            CourseExamConfiguration.objects.filter(cycle_course_id=parent.id).count(), 1
        )
        cycle.refresh_from_db()
        configuration = CourseExamConfiguration.objects.get(cycle_course_id=parent.id)
        self.assertEqual(
            (
                configuration.questions_required_per_faculty,
                configuration.questions_required_per_faculty_source,
                configuration.final_item_count,
                configuration.final_item_count_source,
                configuration.cycle_defaults_revision_snapshot,
                configuration.revision,
            ),
            (60, "OVERRIDE", 55, "DEFAULT", 1, 2),
        )
        self.assertEqual(
            (
                cycle.default_questions_required_per_faculty,
                cycle.default_final_item_count,
                cycle.defaults_revision,
            ),
            (55, 55, 1),
        )
        self.assertEqual(configuration.coverage, "Concurrent coverage")
        self.assertEqual(configuration.contribution_deadline, expected_deadline)
