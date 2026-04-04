from django.urls import path

from .views import (
    activity_scores_view,
    analytics_view,
    dashboard_view,
    faculty_assignment_accept_view,
    faculty_assignment_response_view,
    guide_view,
    guide_manual_view,
    my_courses_view,
    offering_enrollment_view,
    offering_periods_view,
    period_activity_delete_view,
    public_index_view,
    period_activities_view,
    period_attendance_view,
    period_prediction_view,
    period_prediction_guide_view,
    period_correction_official_report_view,
    period_correction_finalize_view,
    period_corrections_view,
    period_self_reopen_view,
    period_submit_view,
    period_summary_view,
)

app_name = "faculty_portal"

urlpatterns = [
    path("faculty/", public_index_view, name="public_index"),
    path("faculty/index/", public_index_view, name="public_index_alias"),
    path("faculty/guide/", guide_view, name="guide"),
    path("faculty/guide/manual/", guide_manual_view, name="guide_manual"),
    path("faculty/dashboard/", dashboard_view, name="dashboard"),
    path("faculty/analytics/", analytics_view, name="analytics"),
    path("faculty/my-courses/", my_courses_view, name="my_courses"),
    path(
        "faculty/my-courses/assignments/<int:assignment_id>/accept/",
        faculty_assignment_accept_view,
        name="faculty_assignment_accept",
    ),
    path(
        "faculty/my-courses/assignments/<int:assignment_id>/respond/",
        faculty_assignment_response_view,
        name="faculty_assignment_response",
    ),
    path("faculty/my-courses/<int:offering_id>/periods/", offering_periods_view, name="offering_periods"),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/activities/",
        period_activities_view,
        name="period_activities",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/activities/<int:activity_id>/scores/",
        activity_scores_view,
        name="activity_scores",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/activities/<int:activity_id>/edit/",
        period_activities_view,
        name="period_activity_edit",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/activities/<int:activity_id>/delete/",
        period_activity_delete_view,
        name="period_activity_delete",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/attendance/",
        period_attendance_view,
        name="period_attendance",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/summary/",
        period_summary_view,
        name="period_summary",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/prediction/",
        period_prediction_view,
        name="period_prediction",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/prediction/guide/",
        period_prediction_guide_view,
        name="period_prediction_guide",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/submit/",
        period_submit_view,
        name="period_submit",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/corrections/",
        period_corrections_view,
        name="period_corrections",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/corrections/self-reopen/",
        period_self_reopen_view,
        name="period_self_reopen",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/corrections/<int:request_id>/finalize/",
        period_correction_finalize_view,
        name="period_correction_finalize",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/periods/<int:period_id>/corrections/<int:request_id>/official-report/",
        period_correction_official_report_view,
        name="period_correction_official_report",
    ),
    path(
        "faculty/my-courses/<int:offering_id>/enrollment/",
        offering_enrollment_view,
        name="offering_enrollment",
    ),
]
