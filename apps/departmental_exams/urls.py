from django.urls import path

from . import views

app_name = "departmental_exams"

urlpatterns = [
    path("admin-portal/departmental-exams/", views.cycle_list_view, name="cycle_list"),
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
