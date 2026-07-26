from django.urls import path

from . import views

app_name = "departmental_exams"

urlpatterns = [
    path("admin-portal/departmental-exams/", views.cycle_list_view, name="cycle_list"),
    path("admin-portal/departmental-exams/cycles/create/", views.cycle_create_view, name="cycle_create"),
    path(
        "admin-portal/departmental-exams/assigned-courses/",
        views.assigned_course_examinations_view,
        name="assigned_course_examinations",
    ),
    path("admin-portal/departmental-exams/cycles/<int:cycle_id>/courses/", views.cycle_course_list_view, name="cycle_course_list"),
    path("admin-portal/departmental-exams/courses/<int:cycle_course_id>/administration/", views.cycle_course_administration_view, name="cycle_course_administration"),
]
