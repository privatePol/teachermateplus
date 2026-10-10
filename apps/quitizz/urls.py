from django.urls import path

from . import public_views, views

app_name = "quitizz"
urlpatterns = [
    path("quitizz/play/<uuid:public_id>/", public_views.play, name="play"),
    path("quitizz/play/<uuid:public_id>/exchange/", public_views.exchange, name="exchange"),
    path("quitizz/play/<uuid:public_id>/join/", public_views.join, name="join"),
    path("quitizz/play/<uuid:public_id>/state/", public_views.state, name="state"),
    path("quitizz/play/<uuid:public_id>/answer/", public_views.answer, name="answer"),
    path("quitizz/play/<uuid:public_id>/socket/", public_views.socket_identity, name="socket_identity"),
    path("faculty/quitizz/", views.quitizz_list, name="list"),
    path("faculty/quitizz/create/", views.create, name="create"),
    path("faculty/quitizz/<uuid:public_id>/edit/", views.edit, name="edit"),
    path("faculty/quitizz/<uuid:public_id>/questions/add/", views.question_edit, name="question_add"),
    path("faculty/quitizz/<uuid:public_id>/questions/<int:question_id>/edit/", views.question_edit, name="question_edit"),
    path("faculty/quitizz/<uuid:public_id>/questions/<int:question_id>/delete/", views.question_delete, name="question_delete"),
    path("faculty/quitizz/<uuid:public_id>/reorder/", views.reorder, name="reorder"),
    path("faculty/quitizz/<uuid:public_id>/archive/", views.archive, name="archive"),
    path("faculty/quitizz/<uuid:public_id>/launch/", views.launch, name="launch"),
    path("faculty/quitizz/sessions/<uuid:public_id>/host/", views.host, name="host"),
    path("faculty/quitizz/sessions/<uuid:public_id>/command/", views.host_command, name="host_command"),
    path("faculty/quitizz/sessions/<uuid:public_id>/state/", views.host_state, name="host_state"),
    path("faculty/quitizz/sessions/<uuid:public_id>/qr/", views.host_qr, name="host_qr"),
]
