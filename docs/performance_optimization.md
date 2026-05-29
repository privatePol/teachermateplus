# TeacherMate+ Performance Optimization Notes

This document tracks application-level performance work that can be done inside the Django codebase without assuming direct production-server access.

## Current Status

TeacherMate+ already has several production-friendly patterns in place:

- Admin and Faculty list pages generally use server-side pagination through `_get_page(...)`.
- The SIS periodic-grades API already supports `page` and `page_size` and caps `page_size` at 2000.
- Many admin scope querysets already use `select_related()` and `prefetch_related()` through `AdminScopeService`.
- Faculty score and attendance writes already recompute affected student grades instead of forcing full-class recomputation.
- Faculty grade pages load one offering and one grading period at a time.
- Bulk import services already use runtime lookup caches to avoid repeated reference lookups per CSV row.

## Implemented in This Pass

### Query Optimization

1. Portal menu rendering now computes effective permission codes once per request and passes them into menu building.
2. Menu groups now prefetch active menu items with permissions in one structured prefetch instead of filtering each group again.
3. Faculty activity setup no longer performs `exists()` checks inside component/subcomponent loops. It builds in-memory sets for components that have subcomponents and subcomponents that have details.
4. Faculty Dashboard enrollment status counts now use one aggregate query instead of separate count queries for dropped, withdrawn, and incomplete students.
5. Faculty Dashboard assigned-course count now uses already-loaded active/archived offering lists instead of issuing another count query.

### Summary Page Recompute Scope

The Faculty Summary page no longer recomputes every student in the selected period on every page load when stored period-grade rows already exist.

Current behavior:

- If stored period summary rows exist, the page reads the stored rows.
- If rows are missing for enrolled students, TeacherMate+ recomputes only the missing student rows for that offering and period.
- Grade formulas are unchanged.
- Audit logging is preserved when a recompute is performed.

This relies on the existing write-time recomputation behavior for score and attendance updates.

### Indexes Added

Safe composite indexes were added for high-traffic filters and joins:

- `course_offerings`: scope/status and department-term lookups
- `faculty_assignments`: faculty assignment status and scope lookups
- `enrollments`: offering/status, scope/term, and student/status lookups
- `students`: scoped active-status lookups
- `grade_activities`: offering-period and scope lookups
- `student_activity_scores`: activity-active and student-active lookups
- `student_period_grades`: scope/updated, offering-period-grade, and student-offering lookups
- `student_final_grades`: scope/submitted and student-offering lookups
- `grading_period_locks`: scope/term and deadline lookups
- `grade_submissions`: scope/status, offering/status/updated, and period/status lookups
- `grade_submission_reopen_requests`: scope/status and offering-period-status lookups
- `grade_correction_requests`: scope/status, offering-period-status, and requester/status lookups
- `audit_logs`: scope/created, actor/portal/created, and entity/action/created lookups

Foreign keys and existing unique constraints already create additional indexes, so broad duplicate indexes were intentionally avoided.

### Caching

Django cache configuration is now environment-driven:

```env
DJANGO_CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache
DJANGO_CACHE_LOCATION=TeacherMate+-local
```

Development defaults to local memory cache. Production may use Redis by setting a Redis cache backend after IT installs and configures the required package/service.

Grade data is not cached in this pass because grade cache invalidation must be scoped by tenant, campus, faculty, offering, period, student, role, and release policy.

### Dev Diagnostics

Optional Django Debug Toolbar support is available only when:

1. `DEBUG=True`
2. `DJANGO_DEBUG_TOOLBAR=True`
3. `django-debug-toolbar` is installed

Example dev setup:

```bash
pip install django-debug-toolbar
export DJANGO_DEBUG=True
export DJANGO_DEBUG_TOOLBAR=True
export DJANGO_INTERNAL_IPS=127.0.0.1
```

Do not enable Debug Toolbar in production.

## Already Implemented or Not Necessary

### Pagination

Already implemented for major Admin list pages through `_get_page(...)`, including students, enrollments, grading templates, submissions, correction requests, import batches, and similar governance screens.

Faculty gradebook pages intentionally load one selected offering and period/activity at a time. That is appropriate for score entry because faculty need the full class roster for one activity.

### API Pagination

Already implemented in `apps/admin_portal/api_views.py` for periodic grade export:

- `page`
- `page_size`
- maximum `page_size` of 2000
- tenant token protection
- campus/section guardrails for student identity ambiguity

### Static Assets

TeacherMate+ uses Django static files. Production should serve static files through nginx or equivalent, and `collectstatic` remains part of deployment.

## Deferred Production Enhancements

These items are useful but should be planned separately:

1. Redis cache: useful for menu/template lookup caching, but requires IT/server setup and careful tenant/user-aware invalidation.
2. Background report/export workers: Celery/RQ can move large exports out of web requests, but require Redis/RabbitMQ and worker process management.
3. MySQL slow query log: configure at the database/server level and review after real production data volume exists.
4. APM/log monitoring: Gunicorn, nginx, database slow query logs, and optional hosted APM.

## Production Monitoring Checklist

Ask IT to configure or confirm:

1. MySQL/MariaDB slow query log enabled with an agreed threshold.
2. Gunicorn and nginx logs retained and rotated.
3. Dashboard/report endpoints monitored for response time.
4. Database CPU, memory, disk I/O, and connection count monitored.
5. Backup jobs monitored separately from web request performance.
6. `DEBUG=False` in production.
7. Debug Toolbar disabled in production.
8. Static files served by nginx or equivalent after `collectstatic`.

## Query Count Testing Notes

Use Django's test utilities for focused regression tests:

```python
from django.test import TestCase

class DashboardQueryTests(TestCase):
    def test_faculty_dashboard_query_count(self):
        self.client.force_login(self.faculty_user)
        with self.assertNumQueries(50):
            self.client.get("/faculty/dashboard/")
```

The exact query budget should be based on a seeded fixture that resembles a real faculty load. Keep query budgets scoped to one workflow at a time so they remain useful instead of fragile.

## Safety Rules Preserved

- No grading formulas were changed.
- No grade result math was changed.
- No audit logging was bypassed.
- Tenant, campus, and RBAC scoping remain intact.
- No soft-delete or `is_active` filters were removed.
- No sensitive grade data was cached across tenants/users.
