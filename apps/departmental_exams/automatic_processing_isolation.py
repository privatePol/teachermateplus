from __future__ import annotations

import logging
import math
import multiprocessing
from dataclasses import dataclass


logger = logging.getLogger(__name__)


AUTOMATIC_PROCESSING_TIMEOUT_CODE = "AUTOMATIC_PROCESSING_TIMEOUT"
AUTOMATIC_PROCESSING_CHILD_EXIT_CODE = "AUTOMATIC_PROCESSING_CHILD_EXIT"
DEFAULT_AUTOMATIC_COURSE_TIMEOUT_SECONDS = 300
DEFAULT_CHILD_TERMINATION_GRACE_SECONDS = 5


def resolve_automatic_course_timeout_seconds(override=None):
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    configured = (
        override
        if override is not None
        else getattr(
            settings,
            "DEPARTMENTAL_EXAM_AUTOMATIC_COURSE_TIMEOUT_SECONDS",
            DEFAULT_AUTOMATIC_COURSE_TIMEOUT_SECONDS,
        )
    )
    if isinstance(configured, bool):
        raise ImproperlyConfigured(
            "DEPARTMENTAL_EXAM_AUTOMATIC_COURSE_TIMEOUT_SECONDS must be a positive number."
        )
    try:
        timeout_seconds = float(configured)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            "DEPARTMENTAL_EXAM_AUTOMATIC_COURSE_TIMEOUT_SECONDS must be a positive number."
        ) from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ImproperlyConfigured(
            "DEPARTMENTAL_EXAM_AUTOMATIC_COURSE_TIMEOUT_SECONDS must be a positive number."
        )
    return timeout_seconds


@dataclass(frozen=True)
class WallClockProcessOutcome:
    timed_out: bool
    exitcode: int | None


class ChildProcessTerminationError(RuntimeError):
    pass


class WallClockProcessController:
    """Run one child with bounded terminate/kill cleanup."""

    def __init__(
        self,
        *,
        timeout_seconds=None,
        termination_grace_seconds=DEFAULT_CHILD_TERMINATION_GRACE_SECONDS,
        context=None,
    ):
        self.timeout_seconds = resolve_automatic_course_timeout_seconds(
            timeout_seconds
        )
        self.termination_grace_seconds = float(termination_grace_seconds)
        if (
            not math.isfinite(self.termination_grace_seconds)
            or self.termination_grace_seconds <= 0
        ):
            raise ValueError("Child-process termination grace must be positive.")
        self.context = context or multiprocessing.get_context("spawn")

    def _stop(self, process):
        process.terminate()
        process.join(self.termination_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(self.termination_grace_seconds)
        if process.is_alive():
            raise ChildProcessTerminationError(
                f"Child process {process.name} did not exit after terminate and kill."
            )

    def run(self, *, target, args=(), kwargs=None, name=None, after_start=None):
        process = self.context.Process(
            target=target,
            args=args,
            kwargs=kwargs or {},
            name=name,
        )
        started = False
        timed_out = False
        try:
            process.start()
            started = True
            if after_start is not None:
                after_start()
            process.join(self.timeout_seconds)
            timed_out = process.is_alive()
            if timed_out:
                self._stop(process)
            return WallClockProcessOutcome(
                timed_out=timed_out,
                exitcode=process.exitcode,
            )
        finally:
            if started and process.is_alive():
                self._stop(process)
            if started and not process.is_alive():
                process.close()


def _automatic_course_child(
    result_connection,
    *,
    cycle_course_id,
    tenant_id,
    now,
    max_states,
):
    """Spawn-safe Django entry point; return content-safe result metadata only."""

    import django

    django.setup()

    from django.db import connections

    from .automatic_workflow import AutomaticExamDeadlineService

    connections.close_all()
    try:
        try:
            result = AutomaticExamDeadlineService.process_course(
                cycle_course_id=cycle_course_id,
                tenant_id=tenant_id,
                now=now,
                max_states=max_states,
            )
            payload = {
                "kind": "result",
                "cycle_course_id": result.cycle_course_id,
                "status": str(result.status),
                "code": str(result.code),
                "message": str(result.message),
                "generation_revision": result.generation_revision,
            }
        except Exception as exc:
            payload = {
                "kind": "error",
                "code": exc.__class__.__name__[:64],
            }
        try:
            result_connection.send(payload)
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_connection.close()
        connections.close_all()


class ProcessIsolatedAutomaticCourseRunner:
    """Execute each automatic examination unit in a fresh spawned process."""

    def __init__(
        self,
        *,
        timeout_seconds=None,
        termination_grace_seconds=DEFAULT_CHILD_TERMINATION_GRACE_SECONDS,
        context=None,
    ):
        self.controller = WallClockProcessController(
            timeout_seconds=timeout_seconds,
            termination_grace_seconds=termination_grace_seconds,
            context=context,
        )

    @property
    def timeout_seconds(self):
        return self.controller.timeout_seconds

    @staticmethod
    def _record_failure(*, cycle_course_id, tenant_id, code, message, now):
        from .automatic_workflow import AutomaticExamDeadlineService

        return AutomaticExamDeadlineService.record_processing_failure(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
            code=code,
            message=message,
            now=now,
        )

    def __call__(self, *, cycle_course_id, tenant_id, now, max_states=None):
        from .automatic_workflow import AutomaticProcessingResult

        receive_connection, send_connection = self.controller.context.Pipe(
            duplex=False
        )
        try:
            outcome = self.controller.run(
                target=_automatic_course_child,
                kwargs={
                    "result_connection": send_connection,
                    "cycle_course_id": cycle_course_id,
                    "tenant_id": tenant_id,
                    "now": now,
                    "max_states": max_states,
                },
                name=f"departmental-exam-course-{cycle_course_id}",
                after_start=send_connection.close,
            )
            if outcome.timed_out:
                logger.error(
                    "Automatic departmental-exam processing timed out for tenant=%s course=%s timeout_seconds=%s.",
                    tenant_id,
                    cycle_course_id,
                    self.timeout_seconds,
                )
                return self._record_failure(
                    cycle_course_id=cycle_course_id,
                    tenant_id=tenant_id,
                    code=AUTOMATIC_PROCESSING_TIMEOUT_CODE,
                    message=(
                        "Automatic generation exceeded the per-course processing time "
                        "limit. Administrator review is required."
                    ),
                    now=now,
                )
            if outcome.exitcode != 0:
                logger.error(
                    "Automatic departmental-exam child exited abnormally for tenant=%s course=%s exitcode=%s.",
                    tenant_id,
                    cycle_course_id,
                    outcome.exitcode,
                )
                return self._record_failure(
                    cycle_course_id=cycle_course_id,
                    tenant_id=tenant_id,
                    code=AUTOMATIC_PROCESSING_CHILD_EXIT_CODE,
                    message=(
                        "Course processing ended unexpectedly; inspect the secured "
                        "application log."
                    ),
                    now=now,
                )
            try:
                if not receive_connection.poll(1):
                    raise EOFError
                payload = receive_connection.recv()
            except (EOFError, OSError):
                payload = None

            if not isinstance(payload, dict):
                return self._record_failure(
                    cycle_course_id=cycle_course_id,
                    tenant_id=tenant_id,
                    code=AUTOMATIC_PROCESSING_CHILD_EXIT_CODE,
                    message=(
                        "Course processing ended unexpectedly; inspect the secured "
                        "application log."
                    ),
                    now=now,
                )
            if payload.get("kind") == "error":
                code = str(payload.get("code") or AUTOMATIC_PROCESSING_CHILD_EXIT_CODE)[
                    :64
                ]
                return self._record_failure(
                    cycle_course_id=cycle_course_id,
                    tenant_id=tenant_id,
                    code=code,
                    message=(
                        "Course processing failed; inspect the secured application log."
                    ),
                    now=now,
                )
            if (
                payload.get("kind") != "result"
                or payload.get("cycle_course_id") != cycle_course_id
            ):
                return self._record_failure(
                    cycle_course_id=cycle_course_id,
                    tenant_id=tenant_id,
                    code=AUTOMATIC_PROCESSING_CHILD_EXIT_CODE,
                    message=(
                        "Course processing ended unexpectedly; inspect the secured "
                        "application log."
                    ),
                    now=now,
                )
            return AutomaticProcessingResult(
                cycle_course_id=payload["cycle_course_id"],
                status=payload["status"],
                code=payload["code"],
                message=payload["message"],
                generation_revision=payload.get("generation_revision"),
            )
        finally:
            try:
                send_connection.close()
            except OSError:
                pass
            receive_connection.close()
