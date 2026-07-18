from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.notifications.submission_readiness import SubmissionReadinessEmailService


class Command(BaseCommand):
    help = "Send scope-limited grade submission readiness exception reports."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true", help="Bypass only the successful-delivery duplicate guard.")
        parser.add_argument("--as-of-date", help="Use YYYY-MM-DD for Manila deadline-date evaluation.")
        parser.add_argument("--tenant-id", type=int)

    def handle(self, *args, **options):
        as_of_date = None
        if options["as_of_date"]:
            try:
                as_of_date = date.fromisoformat(options["as_of_date"])
            except ValueError as exc:
                raise CommandError("--as-of-date must use YYYY-MM-DD.") from exc
        result = SubmissionReadinessEmailService.run(
            now=timezone.now(), as_of_date=as_of_date, tenant_id=options["tenant_id"],
            dry_run=options["dry_run"], force=options["force"],
        )
        self.stdout.write(
            f"Submission readiness alerts: tenants={result['tenants']} eligible_assignments={result['eligible']} "
            f"sent={result['sent']} dry_run={result['dry_run']} duplicates={result['duplicates']} failed={result['failed']}"
        )
        if result["failed"]:
            self.stderr.write(self.style.WARNING("One or more recipient deliveries failed; see notification logs."))
