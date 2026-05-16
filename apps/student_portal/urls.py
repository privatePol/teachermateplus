from django.urls import path

from . import views

app_name = "student_portal"

urlpatterns = [
    path("student/", views.dashboard_view, name="dashboard"),
    path("student/courses/", views.courses_view, name="courses"),
    path("student/courses/<int:offering_id>/", views.course_detail_view, name="course_detail"),
    path("student/grades/", views.grades_view, name="grades"),
    path("student/grades/<int:offering_id>/", views.grade_detail_view, name="grade_detail"),
    path("student/attendance/", views.attendance_view, name="attendance"),
    path("student/profile/", views.profile_view, name="profile"),
    path("student/account/", views.account_view, name="account"),
]
