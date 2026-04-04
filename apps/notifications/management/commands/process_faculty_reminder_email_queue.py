from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.services import FacultyReminderService


class Command(BaseCommand):
    help = "Process pending faculty reminder email queue entries and send them in batches."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        processed = FacultyReminderService.process_email_queue(
            now=now,
            batch_size=options["batch_size"],
            dry_run=options["dry_run"],
        )
        label = "Would process" if options["dry_run"] else "Processed"
        self.stdout.write(self.style.SUCCESS(f"{label} faculty reminder email queue: {processed}"))
