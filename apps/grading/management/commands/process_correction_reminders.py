from django.core.management.base import BaseCommand

from apps.grading.correction_reminders import CorrectionReminderService


class Command(BaseCommand):
    help = "Discover current correction approvers and dispatch durable emails (poll every minute)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            from django.core.management.base import CommandError
            raise CommandError("limit must be positive")
        try:
            discovered = CorrectionReminderService.discover()
            counts = CorrectionReminderService.dispatch(limit=options["limit"])
        except Exception:
            # Prevent management-command tracebacks from exposing raw backend exceptions.
            from django.core.management.base import CommandError
            raise CommandError("Correction email processing failed; durable rows will be retried.") from None
        self.stdout.write(f"requests={discovered} sent={counts['sent']} failed={counts['failed']} skipped={counts['skipped']}")
