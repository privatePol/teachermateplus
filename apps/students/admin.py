from django.contrib import admin

from .models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_no", "last_name", "first_name", "tenant", "campus", "program", "status", "is_active")
    search_fields = ("student_no", "last_name", "first_name")
    list_filter = ("tenant", "campus", "department", "program", "status", "is_active")
