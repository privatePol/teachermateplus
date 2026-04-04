from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.academics.services import FacultyAssignmentWorkflowService


class Command(BaseCommand):
    help = "Queue reminders and auto-expire overdue faculty assignments that are still awaiting acknowledgment."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-reminders",
            action="store_true",
            help="Skip queueing reminder notifications.",
        )
        parser.add_argument(
            "--skip-expiry",
            action="store_true",
            help="Skip auto-expiring overdue assignments.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many reminders/expirations would be processed without saving changes.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        reminder_count = 0
        expired_count = 0

        if not options["skip_reminders"]:
            reminder_count = FacultyAssignmentWorkflowService.queue_pending_assignment_reminders(
                now=now,
                dry_run=options["dry_run"],
            )

        if not options["skip_expiry"]:
            expired_count = FacultyAssignmentWorkflowService.expire_overdue_assignments(
                now=now,
                dry_run=options["dry_run"],
            )

        mode_label = "Dry run" if options["dry_run"] else "Processed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode_label} faculty assignment workflow: {reminder_count} reminder(s), {expired_count} expiration(s)."
            )
        )
