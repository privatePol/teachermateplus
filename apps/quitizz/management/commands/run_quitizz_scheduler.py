"""Run independently of Gunicorn/Daphne; activation needs a separate gate."""
import logging
import signal
import time
from threading import Event

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections, connections
from apps.quitizz import automation
from apps.quitizz.models import QuiTizzSession

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Advance persisted automatic QuiTizz phases (database-authoritative)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One bounded pass; intended for disposable test validation.")

    def handle(self, *args, **options):
        active = settings.QUITIZZ_SCHEDULER_ACTIVE_SECONDS
        idle = settings.QUITIZZ_SCHEDULER_IDLE_SECONDS
        batch = settings.QUITIZZ_SCHEDULER_BATCH_SIZE
        if not (0.1 <= active <= 2 and active <= idle <= 10 and 1 <= batch <= 500):
            raise CommandError("Invalid QuiTizz scheduler interval/batch configuration.")
        stopped, publisher = Event(), automation.NotificationQueue()
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: stopped.set())
        safety_at, cursor, errors = 0, 0, 0
        try:
            while not stopped.is_set():
                close_old_connections()
                try:
                    if time.monotonic() >= safety_at:
                        healthy = automation.throttle_healthy()
                        ids = list(QuiTizzSession.objects.filter(playback_mode="AUTOMATIC", paused_at__isnull=True, pk__gt=cursor)
                            .exclude(status__in=["COMPLETED", "CANCELLED"]).order_by("pk").values_list("pk", flat=True)[:batch])
                        for pk in ids:
                            try:
                                automation.safety_check(pk, healthy=healthy, dispatch=publisher.enqueue)
                            except OperationalError:
                                close_old_connections()
                                logger.warning("QuiTizz safety check database contention; retry on next scan.")
                            except Exception:
                                logger.error("QuiTizz safety check failed; review scheduler health.")
                        cursor = ids[-1] if len(ids) == batch else 0
                        safety_at = time.monotonic() + 2
                    candidates = automation.due_candidates(batch_size=batch)
                    for pk, version, phase in candidates:
                        try:
                            automation.advance(pk, version=version, phase=phase, dispatch=publisher.enqueue)
                        except OperationalError:
                            close_old_connections()
                            logger.warning("QuiTizz transition database contention; retry through persisted deadline.")
                        except Exception:
                            # A bad game/audit must not stop unrelated games. No
                            # content, credentials, DSN or exception text logged.
                            logger.error("QuiTizz transition failed; review scheduler health.")
                    errors = 0
                    scheduled = bool(candidates) or QuiTizzSession.objects.filter(playback_mode="AUTOMATIC", next_transition_at__isnull=False).exists()
                    delay = active if scheduled else idle
                except OperationalError:
                    connections.close_all()
                    errors = min(errors + 1, 4)
                    delay = min(5, active * 2 ** errors)
                    logger.warning("QuiTizz scheduler database unavailable; bounded reconnect backoff.")
                if options["once"]:
                    break
                stopped.wait(delay)
        finally:
            publisher.stop()
            connections.close_all()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
