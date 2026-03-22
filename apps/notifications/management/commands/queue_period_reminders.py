from django.core.management.base import BaseCommand

from apps.notifications.services import ReminderService


class Command(BaseCommand):
    help = "Queue one-day-before grading period close reminders for faculty."

    def handle(self, *args, **options):
        created = ReminderService.queue_period_deadline_reminders()
        self.stdout.write(self.style.SUCCESS(f"Queued reminders: {created}"))
