from django.urls import path

from . import coverage_views, dtr_views, monitoring_views, views

app_name = "faculty_attendance"

urlpatterns = [
    path('admin-portal/faculty-attendance/coverage/initialize/', coverage_views.coverage_initialization_view, name='coverage_initialize'),
    path('admin-portal/faculty-attendance/summary/', monitoring_views.term_monitoring_view, name='term_summary'),
    path('admin-portal/faculty-attendance/summary/faculty/<int:faculty_id>/', monitoring_views.term_monitoring_view, name='term_faculty_details'),
    path("admin-portal/faculty-attendance/setup/", views.setup_view, name="setup"),
    path("admin-portal/faculty-attendance/checklists/", views.checklist_view, name="checklist"),
    path("admin-portal/faculty-attendance/daily/", views.daily_encoding_view, name="daily_encoding"),
    path("admin-portal/faculty-attendance/cutoffs/", views.cutoff_review_view, name="cutoff_review"),
    path("admin-portal/faculty-attendance/checklists/corrections/", views.corrections_view, name="corrections"),
    path("admin-portal/faculty-attendance/checklists/arrangement/save/", views.monthly_arrangement_save_view, name="monthly_arrangement_save"),
    path("admin-portal/faculty-attendance/checklists/monthly/print/", views.monthly_print_view, name="monthly_print"),
    path("admin-portal/faculty-attendance/checklists/monthly/export/", views.monthly_export_view, name="monthly_export"),
    path("admin-portal/faculty-attendance/checklists/sections-taught-together/", views.combined_classes_view, name="combined_classes"),
    path("admin-portal/faculty-attendance/routes/save/", views.route_save_view, name="route_create"),
    path("admin-portal/faculty-attendance/routes/<int:route_id>/save/", views.route_save_view, name="route_update"),
    path("admin-portal/faculty-attendance/routes/<int:route_id>/reset/", views.route_reset_view, name="route_reset"),
    path("admin-portal/faculty-attendance/rounds/<uuid:public_id>/", views.round_view, name="round"),
    path("admin-portal/faculty-attendance/rounds/<uuid:public_id>/print/", views.print_view, name="print"),
    path("admin-portal/faculty-attendance/reconciliation/", views.reconciliation_view, name="reconciliation"),
    path("faculty/my-attendance/", views.my_attendance_view, name="my_attendance"),
    path("admin-portal/faculty-attendance/dtr/", dtr_views.dtr_review_view, name="dtr_review"),
    path("admin-portal/faculty-attendance/dtr/summary/", dtr_views.dtr_cutoff_summary_view, name="dtr_summary"),
    path("admin-portal/faculty-attendance/dtr/ac-summary/", dtr_views.dtr_ac_summary_view, name="dtr_ac_summary"),
    path("admin-portal/faculty-attendance/dtr/<uuid:public_id>/print/", dtr_views.dtr_print_view, name="dtr_print"),
    path("faculty/my-dtr/", dtr_views.my_dtr_view, name="my_dtr"),
    path("faculty/my-dtr/<uuid:public_id>/print/", dtr_views.my_dtr_print_view, name="my_dtr_print"),
]
