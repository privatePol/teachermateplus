"""Spawn-safe targets used only by automatic-processing integration tests.

Keep this module free of top-level Django imports.  A multiprocessing spawn
child imports its target module before Django's app registry is initialized.
"""

from __future__ import annotations

import time


def automatic_course_child_with_generation_barrier(
    *,
    automatic_course_child,
    child_args,
    child_kwargs,
    barrier_connection,
):
    """Run the production child and pause after all generation rows are written."""

    import django

    django.setup()

    from unittest.mock import patch

    from django.db import transaction

    from .generation_services import ExamGenerationService
    from .models import (
        ExamGenerationRevision,
        GeneratedExamItem,
        GeneratedExamSet,
        GenerationSourceAuditSnapshot,
        GenerationSourceQuestionSnapshot,
    )

    original_audit = ExamGenerationService._audit
    barrier_sent = False

    def audit_then_wait(*, action, revision, actor, request, metadata=None):
        nonlocal barrier_sent

        original_audit(
            action=action,
            revision=revision,
            actor=actor,
            request=request,
            metadata=metadata,
        )
        if barrier_sent:
            return
        barrier_sent = True
        using = revision._state.db or "default"
        barrier_connection.send(
            {
                "kind": "generation_transaction_barrier",
                "in_atomic_block": transaction.get_connection(using).in_atomic_block,
                "autocommit": transaction.get_connection(using).get_autocommit(),
                "revision_count": ExamGenerationRevision.objects.using(using)
                .filter(pk=revision.pk, current_marker=1)
                .count(),
                "set_count": GeneratedExamSet.objects.using(using)
                .filter(generation_revision_id=revision.pk)
                .count(),
                "item_count": GeneratedExamItem.objects.using(using)
                .filter(generated_set__generation_revision_id=revision.pk)
                .count(),
                "source_audit_count": GenerationSourceAuditSnapshot.objects.using(using)
                .filter(generation_revision_id=revision.pk)
                .count(),
                "source_question_count": GenerationSourceQuestionSnapshot.objects.using(
                    using
                )
                .filter(audit_snapshot__generation_revision_id=revision.pk)
                .count(),
            }
        )
        while True:
            time.sleep(60)

    try:
        with patch.object(ExamGenerationService, "_audit", side_effect=audit_then_wait):
            automatic_course_child(*child_args, **child_kwargs)
    finally:
        barrier_connection.close()
