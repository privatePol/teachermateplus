from django.contrib import admin

from .models import ImportBatch, ImportBatchRow


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "import_type",
        "status",
        "tenant",
        "campus",
        "uploaded_by_user",
        "total_rows",
        "valid_rows",
        "invalid_rows",
        "imported_rows",
        "created_at",
    )
    search_fields = ("id", "import_type", "uploaded_by_user__username", "tenant__code", "campus__code")
    list_filter = ("import_type", "status", "tenant", "campus")


@admin.register(ImportBatchRow)
class ImportBatchRowAdmin(admin.ModelAdmin):
    list_display = ("batch", "row_number", "row_status", "imported_entity_type", "imported_entity_id")
    search_fields = ("batch__id", "row_number", "imported_entity_type", "imported_entity_id")
    list_filter = ("row_status", "batch__import_type")

