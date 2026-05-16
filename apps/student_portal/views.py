from __future__ import annotations

from django.shortcuts import render

from apps.academics.services import AcademicGovernanceService

from .decorators import student_portal_required
from .services import StudentPortalService


def _course_rows(enrollments):
    enrollments = list(enrollments)
    faculty_map = StudentPortalService.primary_faculty_by_offering(
        [row.course_offering_id for row in enrollments]
    )
    rows = []
    for enrollment in enrollments:
        offering = enrollment.course_offering
        rows.append(
            {
                "enrollment": enrollment,
                "offering": offering,
                "faculty": faculty_map.get(offering.id),
            }
        )
    return rows


def _base_context(request, *, title):
    link = request.student_link
    active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=link.tenant_id)
    return {
        "title": title,
        "link": link,
        "student": link.student,
        "active_academic_year": active_academic_year,
        "active_term": active_term,
    }


@student_portal_required
def dashboard_view(request):
    link = request.student_link
    context = _base_context(request, title="Student Dashboard")
    active_academic_year = context["active_academic_year"]
    active_term = context["active_term"]
    enrollments = StudentPortalService.active_enrollments(link)
    if active_academic_year and active_term:
        enrollments = enrollments.filter(academic_year=active_academic_year, term=active_term)
    rows = _course_rows(enrollments[:8])
    context.update(
        {
            "course_rows": rows,
            "active_enrollment_count": len(rows),
            "total_active_enrollment_count": StudentPortalService.active_enrollments(link).count(),
        }
    )
    return render(request, "student_portal/dashboard.html", context)


@student_portal_required
def courses_view(request):
    rows = _course_rows(StudentPortalService.active_enrollments(request.student_link))
    context = _base_context(request, title="My Courses")
    context["course_rows"] = rows
    return render(request, "student_portal/courses.html", context)


@student_portal_required
def course_detail_view(request, offering_id: int):
    enrollment = StudentPortalService.get_owned_enrollment_for_offering(request.student_link, offering_id)
    rows = _course_rows([enrollment])
    context = _base_context(request, title="Course Detail")
    context.update({"enrollment": enrollment, "course_row": rows[0] if rows else None})
    return render(request, "student_portal/course_detail.html", context)


@student_portal_required
def grades_view(request):
    context = _base_context(request, title="My Grades")
    context["grade_rows"] = StudentPortalService.visible_grade_rows(request.student_link)
    return render(request, "student_portal/grades.html", context)


@student_portal_required
def grade_detail_view(request, offering_id: int):
    StudentPortalService.get_owned_enrollment_for_offering(request.student_link, offering_id)
    grade_rows = StudentPortalService.visible_grade_rows(request.student_link, offering_id=offering_id)
    context = _base_context(request, title="Grade Detail")
    context["grade_row"] = grade_rows[0] if grade_rows else None
    return render(request, "student_portal/grade_detail.html", context)


@student_portal_required
def attendance_view(request):
    context = _base_context(request, title="My Attendance")
    context["attendance_rows"] = StudentPortalService.attendance_rows(request.student_link)
    return render(request, "student_portal/attendance.html", context)


@student_portal_required
def profile_view(request):
    context = _base_context(request, title="My Profile")
    return render(request, "student_portal/profile.html", context)


@student_portal_required
def account_view(request):
    context = _base_context(request, title="My Account")
    return render(request, "student_portal/account.html", context)
