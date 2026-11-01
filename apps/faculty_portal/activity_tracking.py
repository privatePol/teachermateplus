"""Small transactional state for explicit gradebook edits, with no history backfill."""

from django.db import transaction
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.faculty_portal.models import FacultyGradebookModification


def record_gradebook_modification(*, user, offering, template_period):
    # Call inside the same atomic block as the actual write. A rollback, including
    # a nested savepoint rollback, must discard both the edit and this marker.
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return
    if not FacultyAssignment.objects.filter(
        offering=offering, faculty_user=user, is_active=True, accepted_at__isnull=False,
    ).exists():
        return
    now = timezone.now()
    with transaction.atomic():
        state, created = FacultyGradebookModification.objects.get_or_create(
            faculty_user=user, offering=offering, template_period=template_period,
            defaults={"modified_at": now},
        )
        if not created:
            # Concurrent earlier saves must not replace a newer operation time.
            FacultyGradebookModification.objects.filter(pk=state.pk, modified_at__lt=now).update(modified_at=now)
