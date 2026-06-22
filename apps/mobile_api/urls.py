from django.urls import path

from . import views


app_name = "mobile_api"

urlpatterns = [
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/me/", views.me_view, name="me"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("notifications/", views.notifications_view, name="notifications"),
    path("classes/", views.classes_view, name="classes"),
    path("classes/<int:offering_id>/", views.class_snapshot_view, name="class_snapshot"),
    path("classes/<int:offering_id>/students/", views.students_view, name="students"),
    path("classes/<int:offering_id>/students/search/", views.student_search_view, name="student_search"),
    path(
        "classes/<int:offering_id>/students/<int:student_id>/summary/",
        views.student_summary_view,
        name="student_summary",
    ),
    path(
        "classes/<int:offering_id>/students/<int:student_id>/consultation-summary/",
        views.consultation_summary_view,
        name="consultation_summary",
    ),
    path(
        "classes/<int:offering_id>/students/<int:student_id>/grade-explanation/",
        views.grade_explanation_view,
        name="grade_explanation",
    ),
    path("classes/<int:offering_id>/attendance/today/", views.attendance_today_view, name="attendance_today"),
    path("classes/<int:offering_id>/attendance/save/", views.attendance_save_view, name="attendance_save"),
    path(
        "classes/<int:offering_id>/quick-activity/options/",
        views.quick_activity_options_view,
        name="quick_activity_options",
    ),
    path(
        "classes/<int:offering_id>/quick-activity/create/",
        views.quick_activity_create_view,
        name="quick_activity_create",
    ),
    path("classes/<int:offering_id>/missing-scores/", views.missing_scores_view, name="missing_scores"),
    path(
        "classes/<int:offering_id>/submission-readiness/",
        views.submission_readiness_view,
        name="submission_readiness",
    ),
    path("activities/<int:activity_id>/scores/", views.activity_scores_view, name="activity_scores"),
    path("activities/<int:activity_id>/scores/save/", views.activity_scores_save_view, name="activity_scores_save"),
]
