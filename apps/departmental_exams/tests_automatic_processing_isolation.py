import multiprocessing
import os
from unittest.mock import patch

from django.db import connection
from django.utils import timezone

from .automatic_processing_isolation import (
    AUTOMATIC_PROCESSING_TIMEOUT_CODE,
    ProcessIsolatedAutomaticCourseRunner,
    _automatic_course_child,
)
from .automatic_processing_spawn_test_support import (
    automatic_course_child_with_generation_barrier,
)
from .automatic_workflow import AutomaticExamDeadlineService
from .contribution_services import ContributionRosterService
from .models import (
    CourseExamConfiguration,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
    GenerationSourceQuestionSnapshot,
)
from .stage4_test_support import Stage4TransactionTestCase
from .tests_stage6_lifecycle import Stage6FixtureMixin


class _BarrierProcessProxy:
    """Delegate to a real spawn Process, synchronizing only its first join."""

    def __init__(
        self,
        *,
        process,
        barrier_receive_connection,
        barrier_send_connection,
        barrier_wait_seconds,
        owner,
    ):
        self._process = process
        self._barrier_receive_connection = barrier_receive_connection
        self._barrier_send_connection = barrier_send_connection
        self._barrier_wait_seconds = barrier_wait_seconds
        self._owner = owner
        self._first_join = True

    @property
    def name(self):
        return self._process.name

    @property
    def exitcode(self):
        return self._process.exitcode

    def start(self):
        self._process.start()
        self._barrier_send_connection.close()

    def join(self, timeout=None):
        if self._first_join:
            self._first_join = False
            try:
                if self._barrier_receive_connection.poll(
                    self._barrier_wait_seconds
                ):
                    self._owner.barrier_payload = (
                        self._barrier_receive_connection.recv()
                    )
            except (EOFError, OSError):
                self._owner.barrier_payload = {"kind": "barrier_connection_closed"}
            return
        self._process.join(timeout)

    def is_alive(self):
        return self._process.is_alive()

    def terminate(self):
        self._owner.terminated = True
        self._process.terminate()

    def kill(self):
        self._owner.killed = True
        self._process.kill()

    def close(self):
        self._owner.alive_before_close = self._process.is_alive()
        self._owner.final_exitcode = self._process.exitcode
        self._process.close()
        self._barrier_receive_connection.close()
        self._owner.process_closed = True


class _GenerationBarrierSpawnContext:
    """Use a real spawn child with a deterministic in-transaction barrier."""

    def __init__(self, *, barrier_wait_seconds=60):
        self._spawn_context = multiprocessing.get_context("spawn")
        self._barrier_receive, self._barrier_send = self._spawn_context.Pipe(
            duplex=False
        )
        self.barrier_wait_seconds = barrier_wait_seconds
        self.barrier_payload = None
        self.original_target = None
        self.process = None
        self.terminated = False
        self.killed = False
        self.process_closed = False
        self.alive_before_close = None
        self.final_exitcode = None

    def Pipe(self, *, duplex):
        return self._spawn_context.Pipe(duplex=duplex)

    def Process(self, *, target, args, kwargs, name):
        self.original_target = target
        real_process = self._spawn_context.Process(
            target=automatic_course_child_with_generation_barrier,
            kwargs={
                "automatic_course_child": target,
                "child_args": args,
                "child_kwargs": kwargs,
                "barrier_connection": self._barrier_send,
            },
            name=name,
        )
        self.process = _BarrierProcessProxy(
            process=real_process,
            barrier_receive_connection=self._barrier_receive,
            barrier_send_connection=self._barrier_send,
            barrier_wait_seconds=self.barrier_wait_seconds,
            owner=self,
        )
        return self.process


class AutomaticProcessingSpawnDatabaseTests(
    Stage6FixtureMixin,
    Stage4TransactionTestCase,
):
    def _ready_single_campus_automatic_course(self, *, suffix, now):
        self.campus.code = "CUBAO"
        self.campus.name = "Cubao"
        self.campus.save(update_fields=["code", "name", "updated_at"])
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            instructions="Submit authoritative MCQs.",
            scope_suffix=suffix,
        )
        parent = self.make_course(cycle=cycle, code=f"S6-{suffix}")
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=now,
            final_count=50,
            quota=50,
        )
        offering = parent.offering_snapshots.select_related("offering").get().offering
        self.add_faculty_source(
            parent=parent,
            campus=self.campus,
            offering=offering,
            suffix=suffix,
        )
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        contribution = FacultyContribution.objects.get(cycle_course=parent)
        self.add_questions(contribution)
        contribution.status = FacultyContribution.Status.SUBMITTED
        contribution.submitted_at = now
        contribution.save(update_fields=["status", "submitted_at", "updated_at"])

        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        parent.cycle = cycle
        CourseExamConfiguration.objects.filter(pk=configuration.pk).update(
            reopened_contribution_deadline=now - timezone.timedelta(minutes=1)
        )
        configuration.refresh_from_db()
        parent.responsible_department = None
        parent.reviewer = None
        parent.save(
            update_fields=["responsible_department", "reviewer", "updated_at"]
        )
        return parent, configuration

    def test_spawn_timeout_rolls_back_generation_and_next_real_course_succeeds(self):
        if (
            connection.vendor == "sqlite"
            and connection.creation.is_in_memory_db(connection.settings_dict["NAME"])
        ):
            self.skipTest(
                "Run with --settings=config.settings.spawn_test so the spawn child "
                "can open the file-backed SQLite test database."
            )

        now = timezone.now()
        first, first_configuration = self._ready_single_campus_automatic_course(
            suffix="spawn-timeout-first",
            now=now,
        )
        second, _second_configuration = self._ready_single_campus_automatic_course(
            suffix="spawn-timeout-second",
            now=now,
        )

        connection.ensure_connection()
        parent_database_connection = connection.connection
        barrier_context = _GenerationBarrierSpawnContext()
        timeout_runner = ProcessIsolatedAutomaticCourseRunner(
            timeout_seconds=0.1,
            termination_grace_seconds=5,
            context=barrier_context,
        )
        normal_runner = ProcessIsolatedAutomaticCourseRunner(timeout_seconds=60)
        parent_query_proof = []

        def process_real_course(*, cycle_course_id, tenant_id, now, max_states):
            runner = timeout_runner if cycle_course_id == first.id else normal_runner
            result = runner(
                cycle_course_id=cycle_course_id,
                tenant_id=tenant_id,
                now=now,
                max_states=max_states,
            )
            if cycle_course_id == first.id:
                parent_query_proof.append(
                    {
                        "same_connection": connection.connection
                        is parent_database_connection,
                        "configuration_exists": CourseExamConfiguration.objects.filter(
                            pk=first_configuration.pk
                        ).exists(),
                    }
                )
            return result

        spawned_database_name = str(connection.settings_dict["NAME"])
        with patch.dict(os.environ, {"DB_NAME": spawned_database_name}):
            results = AutomaticExamDeadlineService.process_due(
                now=now,
                course_processor=process_real_course,
            )

        self.assertIs(barrier_context.original_target, _automatic_course_child)
        self.assertEqual(
            barrier_context.barrier_payload,
            {
                "kind": "generation_transaction_barrier",
                "in_atomic_block": True,
                "autocommit": False,
                "revision_count": 1,
                "set_count": 2,
                "item_count": 100,
                "source_audit_count": 1,
                "source_question_count": 50,
            },
        )
        self.assertTrue(barrier_context.terminated)
        self.assertFalse(barrier_context.killed)
        self.assertTrue(barrier_context.process_closed)
        self.assertFalse(barrier_context.alive_before_close)
        self.assertIsNotNone(barrier_context.final_exitcode)

        self.assertEqual(parent_query_proof, [{
            "same_connection": True,
            "configuration_exists": True,
        }])
        self.assertEqual(
            [(result.cycle_course_id, result.status, result.code) for result in results],
            [
                (first.id, "ERROR", AUTOMATIC_PROCESSING_TIMEOUT_CODE),
                (second.id, "GENERATED", "GENERATED"),
            ],
        )

        first_configuration.refresh_from_db()
        self.assertEqual(
            first_configuration.workflow_status,
            CourseExamConfiguration.WorkflowStatus.CLOSED,
        )
        self.assertEqual(
            first_configuration.automatic_processing_status,
            CourseExamConfiguration.AutomaticProcessingStatus.ERROR,
        )
        self.assertEqual(
            first_configuration.automatic_processing_code,
            AUTOMATIC_PROCESSING_TIMEOUT_CODE,
        )
        self.assertFalse(
            ExamGenerationRevision.objects.filter(cycle_course=first).exists()
        )
        self.assertFalse(
            GeneratedExamSet.objects.filter(
                generation_revision__cycle_course=first
            ).exists()
        )
        self.assertFalse(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision__cycle_course=first
            ).exists()
        )
        self.assertFalse(
            GenerationSourceAuditSnapshot.objects.filter(
                generation_revision__cycle_course=first
            ).exists()
        )
        self.assertFalse(
            GenerationSourceQuestionSnapshot.objects.filter(
                audit_snapshot__generation_revision__cycle_course=first
            ).exists()
        )

        second_revision = ExamGenerationRevision.objects.get(
            cycle_course=second,
            current_marker=1,
        )
        self.assertEqual(
            GeneratedExamSet.objects.filter(
                generation_revision=second_revision
            ).count(),
            2,
        )
        self.assertEqual(
            GeneratedExamItem.objects.filter(
                generated_set__generation_revision=second_revision
            ).count(),
            100,
        )
        self.assertEqual(
            GenerationSourceAuditSnapshot.objects.filter(
                generation_revision=second_revision
            ).count(),
            1,
        )
        self.assertNotIn(
            f"departmental-exam-course-{first.id}",
            {child.name for child in multiprocessing.active_children()},
        )
        self.assertNotIn(
            f"departmental-exam-course-{second.id}",
            {child.name for child in multiprocessing.active_children()},
        )
