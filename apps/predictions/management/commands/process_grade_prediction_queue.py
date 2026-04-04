from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.predictions.services import PredictionQueueProcessor


class Command(BaseCommand):
    help = "Process pending grade prediction snapshot jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--username", type=str, default="")

    def handle(self, *args, **options):
        user = None
        username = (options.get("username") or "").strip()
        if username:
            user = get_user_model().objects.filter(username=username).first()
        processed, failed = PredictionQueueProcessor.process_pending(limit=options["limit"], user=user)
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} prediction job(s); failed {failed}.")) 

