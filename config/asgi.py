import os
from pathlib import Path

from django.core.asgi import get_asgi_application

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from apps.quitizz.routing import websocket_urlpatterns
from apps.quitizz.consumers import SameOriginValidator

application = ProtocolTypeRouter({
    "http": django_application,
    "websocket": SameOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
})
