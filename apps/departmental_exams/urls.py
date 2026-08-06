from django.urls import path

from . import faculty_views, monitoring_views, views

app_name = "departmental_exams"

urlpatterns = [
    path(
        "faculty/departmental-exams/contributions/",
        faculty_views.contribution_list_view,
        name="contribution_list",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/",
        faculty_views.contribution_workspace_view,
        name="contribution_workspace",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/questions/add/",
        faculty_views.question_create_view,
        name="question_create",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/questions/<int:question_id>/edit/",
        faculty_views.question_edit_view,
        name="question_edit",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/questions/<int:question_id>/delete/",
        faculty_views.question_delete_view,
        name="question_delete",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/questions/reorder/",
        faculty_views.question_reorder_view,
        name="question_reorder",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/csv/template/",
        faculty_views.csv_template_view,
        name="csv_template",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/csv/upload/",
        faculty_views.csv_upload_view,
        name="csv_upload",
    ),
    path(
        "faculty/departmental-exams/imports/<uuid:token>/",
        faculty_views.csv_preview_view,
        name="csv_preview",
    ),
    path(
        "faculty/departmental-exams/imports/<uuid:token>/errors.csv",
        faculty_views.csv_error_report_view,
        name="csv_error_report",
    ),
    path(
        "faculty/departmental-exams/imports/<uuid:token>/confirm/",
        faculty_views.csv_confirm_view,
        name="csv_confirm",
    ),
    path(
        "faculty/departmental-exams/contributions/<int:contribution_id>/submit/",
        faculty_views.contribution_submit_view,
        name="contribution_submit",
    ),
    path("admin-portal/departmental-exams/", views.cycle_list_view, name="cycle_list"),
    path(
        "admin-portal/departmental-exams/contributor-monitoring/",
        monitoring_views.contributor_monitoring_view,
        name="contributor_monitoring",
    ),
    path(
        "admin-portal/departmental-exams/courses/<int:cycle_course_id>/roster/<str:action>/",
        monitoring_views.roster_action_view,
        name="roster_action",
    ),
    path("admin-portal/departmental-exams/cycles/create/", views.cycle_create_view, name="cycle_create"),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/configuration/", views.cycle_configuration_view, name="cycle_configuration"),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/configuration/apply-defaults/", views.cycle_apply_defaults_view, name="cycle_apply_defaults"),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/open/", views.cycle_open_view, name="cycle_open"),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/close/", views.cycle_close_view, name="cycle_close"),
    path(
        "admin-portal/departmental-exams/assigned-courses/",
        views.assigned_course_examinations_view,
        name="assigned_course_examinations",
    ),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/courses/", views.cycle_course_list_view, name="cycle_course_list"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/administration/", views.cycle_course_administration_view, name="cycle_course_administration"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/exempt/", views.cycle_course_exempt_view, name="cycle_course_exempt"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/restore/", views.cycle_course_restore_view, name="cycle_course_restore"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/configuration/", views.course_configuration_view, name="course_configuration"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/configuration/remove-overrides/", views.course_remove_overrides_view, name="course_remove_overrides"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/contribution/open/", views.course_contribution_open_view, name="course_contribution_open"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/contribution/close/", views.course_contribution_close_view, name="course_contribution_close"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/contribution/reopen/", views.course_contribution_reopen_view, name="course_contribution_reopen"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/configuration/revert/", views.course_configuration_revert_view, name="course_configuration_revert"),
]
