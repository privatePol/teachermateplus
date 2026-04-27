from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.services import UserDeactivationService


class Command(BaseCommand):
    help = "Deactivate user accounts whose scheduled deactivation time is due."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List due deactivations without applying them.")
        parser.add_argument("--limit", type=int, default=None, help="Maximum number of due schedules to process.")

    def handle(self, *args, **options):
        result = UserDeactivationService.apply_due(
            dry_run=options["dry_run"],
            limit=options.get("limit"),
        )
        mode = "would be processed" if result["dry_run"] else "processed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['count']} scheduled user deactivation(s) {mode} at {result['checked_at'].isoformat()}."
            )
        )
        for row in result["rows"]:
            self.stdout.write(
                f"- schedule={row['schedule_id']} user={row['username']} scheduled_for={row['scheduled_for']}"
            )
