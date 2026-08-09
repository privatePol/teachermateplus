from __future__ import annotations

from .models import ExamGenerationRevision


class FinalExamLockPolicy:
    """Authoritative supported-write guard after the CycleCourse lock is held."""

    MESSAGE = "The final examination is locked and cannot be changed."

    @classmethod
    def require_not_locked(cls, cycle_course, *, conflict_class=None):
        # This deliberately does not acquire another row lock. Callers already
        # own the CycleCourse lock, which serializes Stage 4/6 mutation writers.
        locked = ExamGenerationRevision.objects.filter(
            cycle_course=cycle_course,
            current_marker=1,
            status=ExamGenerationRevision.Status.LOCKED,
        ).only("id").exists()
        if locked:
            # The lazy import avoids reversing the existing service dependency.
            from .blueprint_services import Stage6Conflict

            raise (conflict_class or Stage6Conflict)(cls.MESSAGE)
