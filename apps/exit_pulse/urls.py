from django.urls import path

from . import views


app_name = "exit_pulse"

urlpatterns = [
    path("faculty/exit-pulse/", views.landing_view, name="landing"),
    path("faculty/exit-pulse/create/", views.create_view, name="create"),
    path(
        "faculty/exit-pulse/assignment-comparison/",
        views.assignment_comparison_view,
        name="assignment_comparison",
    ),
    path(
        "faculty/exit-pulse/history/<uuid:session_public_id>/",
        views.history_view,
        name="history",
    ),
    path("faculty/exit-pulse/<uuid:public_id>/live/", views.live_view, name="live"),
    path("faculty/exit-pulse/<uuid:public_id>/status/", views.status_view, name="status"),
    path("faculty/exit-pulse/<uuid:public_id>/qr.svg", views.qr_view, name="qr"),
    path("faculty/exit-pulse/<uuid:public_id>/extend/", views.extend_view, name="extend"),
    path("faculty/exit-pulse/<uuid:public_id>/close/", views.close_view, name="close"),
    path("faculty/exit-pulse/<uuid:public_id>/cancel/", views.cancel_view, name="cancel"),
    path("faculty/exit-pulse/<uuid:public_id>/results/", views.results_view, name="results"),
    path("pulse/", views.public_survey_view, name="public_survey"),
    path("pulse/open/", views.public_open_view, name="public_open"),
    path("pulse/verify/", views.public_verify_view, name="public_verify"),
    path("pulse/respond/", views.public_response_view, name="public_response"),
    path("pulse/submit/", views.public_submit_view, name="public_submit"),
    path("pulse/thanks/", views.public_thanks_view, name="public_thanks"),
]
