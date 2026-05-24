from django.contrib import admin

from .models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("tenant", "code", "name", "start_date", "end_date", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "is_active")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("tenant", "academic_year", "code", "name", "term_type", "sequence_no", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "academic_year", "term_type", "is_active")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("tenant", "campus", "department", "code", "title", "units", "has_syllabus", "is_active")
    search_fields = ("code", "title", "syllabus_url")
    list_filter = ("tenant", "campus", "department", "is_active")

    @admin.display(boolean=True, description="Syllabus")
    def has_syllabus(self, obj):
        return bool(obj.syllabus_url)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "campus", "department", "program", "code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "campus", "department", "program", "is_active")


@admin.register(CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display = ("tenant", "campus", "department", "term", "course", "section", "status", "is_active")
    search_fields = ("course__code", "section__code")
    list_filter = ("tenant", "campus", "department", "term", "status", "is_active")


@admin.register(FacultyAssignment)
class FacultyAssignmentAdmin(admin.ModelAdmin):
    list_display = ("offering", "faculty_user", "is_primary", "is_active", "assigned_at")
    search_fields = ("offering__course__code", "faculty_user__username")
    list_filter = ("is_primary", "is_active")
