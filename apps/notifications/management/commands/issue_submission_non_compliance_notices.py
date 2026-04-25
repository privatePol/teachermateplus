from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.services import SubmissionNonComplianceNoticeService


class Command(BaseCommand):
    help = "Issue overdue periodic grade submission non-compliance notices and send emails."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = SubmissionNonComplianceNoticeService.issue_due_notices(
            now=timezone.now(),
            tenant_id=options["tenant_id"],
            dry_run=options["dry_run"],
        )
        label = "Would issue" if options["dry_run"] else "Issued"
        self.stdout.write(
            self.style.SUCCESS(
                f"{label} submission non-compliance notices: {result['issued']} "
                f"(resolved: {result['resolved']})"
            )
        )
