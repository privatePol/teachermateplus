from django.urls import path
from .consumers import HostConsumer, PlayerConsumer

websocket_urlpatterns = [
    path("ws/quitizz/<uuid:public_id>/host/", HostConsumer.as_asgi()),
    path("ws/quitizz/<uuid:public_id>/player/", PlayerConsumer.as_asgi()),
]
