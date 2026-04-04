from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.services import FacultyReminderService


class Command(BaseCommand):
    help = "Queue due faculty reminder emails into the dedicated reminder email queue."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        queued = FacultyReminderService.queue_due_email_notifications(
            now=now,
            tenant_id=options["tenant_id"],
            dry_run=options["dry_run"],
        )
        label = "Would queue" if options["dry_run"] else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{label} faculty reminder emails: {queued}"))
