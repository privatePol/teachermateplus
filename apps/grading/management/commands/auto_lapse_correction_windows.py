from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.grading.services import GradingGovernanceService


class Command(BaseCommand):
    help = "Automatically lapse approved correction requests whose 24-hour correction window has expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which correction windows would lapse without writing changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        result = GradingGovernanceService.auto_lapse_expired_correction_windows(
            at=timezone.now(),
            dry_run=dry_run,
        )

        mode_label = "DRY RUN" if dry_run else "LIVE RUN"
        self.stdout.write(self.style.NOTICE(f"[{mode_label}] Checked at {result['checked_at']:%Y-%m-%d %H:%M:%S %Z}"))
        self.stdout.write(self.style.NOTICE(f"Expired windows found: {result['count']}"))

        for row in result["rows"]:
            self.stdout.write(
                " - Window #{window_id} | Request #{request_id} | Offering {offering_id} | Period {template_period_id} | Ended {window_end_at}".format(
                    **row
                )
            )

        if result["count"] == 0:
            self.stdout.write(self.style.SUCCESS("No expired active correction windows found."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No database changes were made."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Lapsed {result['count']} correction window(s)."))
