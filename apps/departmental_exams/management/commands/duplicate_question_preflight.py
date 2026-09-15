"""Bounded read-only collision inspection; never prints content or digests."""
import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.departmental_exams.duplicate_contract import VERSION, pool_claims, IncompatibleImportPlan
from apps.departmental_exams.models import CycleCourse


class Command(BaseCommand):
    help = "Read-only duplicate preflight for one tenant/course examination unit."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", required=True, type=int)
        parser.add_argument("--cycle-course-id", required=True, type=int)
        parser.add_argument("--limit", type=int, default=20000)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 20000:
            raise CommandError("Limit must be between 1 and 20000.")
        course = CycleCourse.objects.select_related("cycle").filter(
            pk=options["cycle_course_id"], cycle__tenant_id=options["tenant_id"]).first()
        if course is None:
            raise CommandError("Course examination not found in the requested tenant.")
        try:
            unit, claims = pool_claims(course, limit=options["limit"])
        except IncompatibleImportPlan as exc:
            raise CommandError(exc.messages[0]) from exc
        except ValidationError as exc:
            raise CommandError("Preflight could not safely assess this scope; check structure or reduce scope.") from exc
        collisions = [[{"question_id": q, "batch_id": b, "row_number": r} for q, b, r in owners]
                      for owners in claims.values() if len(owners) > 1]
        self.stdout.write(json.dumps({"version": VERSION, "primary_cycle_course_id": unit.primary.id,
            "member_cycle_course_ids": unit.member_ids, "identities": len(claims),
            "collision_count": len(collisions), "collisions": collisions}, sort_keys=True))
