from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.exit_pulse.services import ExitPulseResponseService


class Command(BaseCommand):
    help = "Anonymize expired Exit Pulse browser-token hashes while preserving anonymous response data."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        count = ExitPulseResponseService.anonymize_expired_identifiers(
            now=timezone.now(),
            tenant_id=options["tenant_id"],
            dry_run=options["dry_run"],
        )
        label = "Would anonymize" if options["dry_run"] else "Anonymized"
        self.stdout.write(self.style.SUCCESS(f"{label} Exit Pulse technical identifiers: {count}"))
