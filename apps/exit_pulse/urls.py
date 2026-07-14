from django.urls import path

from . import views


app_name = "exit_pulse"

urlpatterns = [
    path("faculty/exit-pulse/", views.landing_view, name="landing"),
    path("faculty/exit-pulse/create/", views.create_view, name="create"),
    path("faculty/exit-pulse/<uuid:public_id>/live/", views.live_view, name="live"),
    path("faculty/exit-pulse/<uuid:public_id>/status/", views.status_view, name="status"),
    path("faculty/exit-pulse/<uuid:public_id>/qr.svg", views.qr_view, name="qr"),
    path("faculty/exit-pulse/<uuid:public_id>/extend/", views.extend_view, name="extend"),
    path("faculty/exit-pulse/<uuid:public_id>/close/", views.close_view, name="close"),
    path("faculty/exit-pulse/<uuid:public_id>/cancel/", views.cancel_view, name="cancel"),
    path("faculty/exit-pulse/<uuid:public_id>/results/", views.results_view, name="results"),
    path("pulse/<str:public_token>/", views.public_survey_view, name="public_survey"),
    path("pulse/<str:public_token>/submit/", views.public_submit_view, name="public_submit"),
]
