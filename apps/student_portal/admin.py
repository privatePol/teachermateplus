from django.contrib import admin

from .models import StudentAccountLink


@admin.register(StudentAccountLink)
class StudentAccountLinkAdmin(admin.ModelAdmin):
    list_display = ("student", "user", "tenant", "campus", "is_active", "linked_at", "linked_by_user")
    list_filter = ("tenant", "campus", "is_active")
    search_fields = ("student__student_no", "student__last_name", "student__first_name", "user__username", "user__email")
