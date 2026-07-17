from django.urls import path

from . import views


app_name = "orientation_feedback"

urlpatterns = [
    path("admin-portal/tools/orientation-feedback/", views.session_list_view, name="session_list"),
    path("admin-portal/tools/orientation-feedback/create/", views.session_create_view, name="session_create"),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/edit/",
        views.session_edit_view,
        name="session_edit",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/questions/",
        views.session_questions_view,
        name="session_questions",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/facilitate/",
        views.facilitator_view,
        name="facilitator",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/status/",
        views.facilitator_status_view,
        name="facilitator_status",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/qr.svg",
        views.qr_view,
        name="qr",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/start/",
        views.start_view,
        name="start",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/close/",
        views.close_view,
        name="close",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/cancel/",
        views.cancel_view,
        name="cancel",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/analytics/",
        views.analytics_view,
        name="analytics",
    ),
    path(
        "admin-portal/tools/orientation-feedback/<uuid:public_id>/export.csv",
        views.export_view,
        name="export",
    ),
    path("orientation-feedback/", views.public_entry_view, name="public_entry"),
    path("orientation-feedback/open/", views.public_open_view, name="public_open"),
    path("orientation-feedback/validate/", views.public_validate_view, name="public_validate"),
    path("orientation-feedback/verify/", views.public_verify_view, name="public_verify"),
    path("orientation-feedback/confirm/", views.public_confirm_view, name="public_confirm"),
    path("orientation-feedback/respond/", views.public_response_view, name="public_response"),
    path("orientation-feedback/submit/", views.public_submit_view, name="public_submit"),
    path("orientation-feedback/thanks/", views.public_thanks_view, name="public_thanks"),
]
