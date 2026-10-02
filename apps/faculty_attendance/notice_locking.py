"""One stable campus mutex for source writes and all faculty/month notice aggregates.

The deliberately coarser dedicated row avoids multi-faculty lock inversions and
locking the Campus parent of unrelated FK writes. Acquire BEFORE source locks;
hold to commit. Aggregate queries use current reads even under REPEATABLE READ.
"""
from django.db import IntegrityError, connection, transaction

from .models import AttendanceNoticeMutex


def lock_notice_campus(campus_id):
    if not connection.in_atomic_block:
        raise RuntimeError('Attendance notice locking requires an atomic source transaction.')
    try:
        # Nested savepoint contains a first-use unique-key race. Under InnoDB
        # REPEATABLE READ the loser's ordinary get may still have an old snapshot.
        with transaction.atomic():
            AttendanceNoticeMutex.objects.get_or_create(campus_id=campus_id)
    except IntegrityError:
        # The locking read below sees the winner's committed row, not that snapshot.
        pass
    AttendanceNoticeMutex.objects.select_for_update().get(campus_id=campus_id)


def lock_result_notice_scope(result_id):
    from .models import AttendanceResult
    campus_id = AttendanceResult.objects.values_list('meeting__campus_id', flat=True).get(pk=result_id)
    lock_notice_campus(campus_id)


def lock_observation_notice_scope(observation_id):
    from .models import AttendanceObservation
    campus_id = AttendanceObservation.objects.values_list('round_meeting__meeting__campus_id', flat=True).get(pk=observation_id)
    lock_notice_campus(campus_id)
