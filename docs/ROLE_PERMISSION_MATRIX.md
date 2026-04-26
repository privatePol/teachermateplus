# Role Permission Matrix

Generated from the current EduGradesPro development database.

Legend: `X` means the permission is directly assigned to the role. Blank means it is not assigned.

> Note: Django superusers may still receive broad permission behavior outside direct role assignment. This table shows explicit role-permission records only.

| Permission | CAMPUS_ADMIN | CAO | DEAN | FACULTY | NCBA_CAO | NCBA_CUBAO_AC | NCBA_FAIRVIEW_AC | REGISTRAR | SUPER_ADMIN | TENANT_ADMIN | TMP_ADMIN_140655 | TMP_ADMIN_X |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academic_years.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `academic_years.read` | X |  | X |  | X |  |  | X | X |  |  |  |
| `academic_years.update` | X |  |  |  |  |  |  |  | X |  |  |  |
| `admin_portal.access` | X |  | X |  | X | X | X | X | X |  |  |  |
| `audit_logs.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `campuses.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `campuses.read` | X |  |  |  | X |  |  |  | X |  |  |  |
| `campuses.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `corrections.create` |  |  |  | X |  |  |  |  | X |  |  |  |
| `corrections.read` | X |  | X |  | X |  |  | X | X |  |  |  |
| `corrections.review` |  |  |  |  | X |  |  | X | X |  |  |  |
| `course_base_overrides.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `course_base_overrides.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `course_base_overrides.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `course_offerings.import` | X |  |  |  |  |  |  |  | X |  |  |  |
| `course_template_assignments.create` | X |  |  |  |  |  |  |  | X |  |  |  |
| `course_template_assignments.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `course_template_assignments.update` | X |  |  |  |  |  |  |  | X |  |  |  |
| `courses.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `courses.import` |  |  |  |  |  |  |  |  | X |  |  |  |
| `courses.read` | X |  | X |  | X | X | X | X | X |  |  |  |
| `courses.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `dashboard.read` | X |  | X | X | X | X | X | X | X |  |  |  |
| `departments.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `departments.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `departments.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `enrollment.create` | X |  |  |  |  |  |  |  | X |  |  |  |
| `enrollment.import` | X |  |  |  |  |  |  |  | X |  |  |  |
| `enrollment.read` | X |  | X |  | X |  |  |  | X |  |  |  |
| `enrollment.update` | X |  |  |  |  |  |  |  | X |  |  |  |
| `faculty_analytics.read` |  |  |  | X |  |  |  |  |  |  |  |  |
| `faculty_assignments.create` |  |  | X |  |  |  |  |  | X |  |  |  |
| `faculty_assignments.import` | X |  |  |  |  |  |  |  | X |  |  |  |
| `faculty_assignments.read` | X |  | X | X | X | X | X | X | X |  |  |  |
| `faculty_assignments.update` |  |  | X |  |  |  |  |  | X |  |  |  |
| `faculty_portal.access` |  |  |  | X | X |  |  |  | X |  |  |  |
| `grade_distribution_monitor.read` |  |  |  |  |  |  |  |  |  |  |  |  |
| `grade_submissions.read` |  |  |  |  |  |  |  | X | X |  |  |  |
| `grade_submissions.reopen` | X |  |  |  |  |  |  | X | X |  |  |  |
| `grade_submissions.revert_before_deadline` | X |  | X |  | X |  |  |  | X |  |  |  |
| `grading_analytics.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `grading_governance_settings.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `grading_periods.lock` | X |  |  |  |  |  |  |  | X |  |  |  |
| `grading_periods.read` | X |  |  |  |  |  |  | X | X |  |  |  |
| `grading_periods.reopen` | X |  |  |  |  |  |  |  | X |  |  |  |
| `grading_templates.approve` | X |  | X |  | X |  |  | X | X |  |  |  |
| `grading_templates.create` |  |  | X |  |  |  |  |  | X |  |  |  |
| `grading_templates.publish` |  |  |  |  |  |  |  |  | X |  |  |  |
| `grading_templates.read` | X |  | X |  | X |  |  | X | X | X |  |  |
| `grading_templates.submit_for_approval` | X |  |  |  |  |  |  |  | X | X |  |  |
| `grading_templates.update` | X |  | X |  |  |  |  |  | X | X |  |  |
| `import_batches.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `menus.read` | X |  |  | X |  |  |  |  | X |  |  |  |
| `menus.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `offerings.create` | X |  | X |  | X |  |  |  | X |  |  |  |
| `offerings.read` | X |  | X |  | X | X | X | X | X |  |  |  |
| `offerings.update` | X |  | X |  | X |  |  |  | X |  |  |  |
| `permissions.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `programs.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `programs.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `programs.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `reopen_requests.create` | X |  |  |  |  |  |  | X | X | X |  |  |
| `reopen_requests.read` | X |  | X |  | X |  |  | X | X | X |  |  |
| `reopen_requests.review` | X |  | X |  | X |  |  |  | X |  |  |  |
| `roles.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `roles.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `roles.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `sections.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `sections.import` |  |  |  |  |  |  |  |  | X |  |  |  |
| `sections.read` | X |  | X | X | X | X | X | X | X |  |  |  |
| `sections.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `students.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `students.read` | X |  | X |  | X |  |  | X | X |  |  |  |
| `students.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `system_settings.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_components.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_components.read` |  |  |  |  |  |  | X |  | X |  |  |  |
| `template_components.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_details.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_details.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_details.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_hotfixes.create` | X |  |  |  |  |  |  |  | X | X |  |  |
| `template_hotfixes.read` | X |  | X |  | X |  |  | X | X | X |  |  |
| `template_hotfixes.review` | X |  | X |  | X |  |  | X | X |  |  |  |
| `template_periods.create` |  |  | X |  |  |  |  |  | X |  |  |  |
| `template_periods.read` |  |  | X |  | X |  | X |  | X |  |  |  |
| `template_periods.update` |  |  | X |  |  |  |  |  | X |  |  |  |
| `template_subcomponents.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `template_subcomponents.read` |  |  |  |  |  |  | X |  | X |  |  |  |
| `template_subcomponents.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenant_grading_profiles.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenant_grading_profiles.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenant_grading_profiles.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenants.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenants.read` |  |  |  |  |  |  |  |  | X |  |  |  |
| `tenants.update` |  |  |  |  |  |  |  |  | X |  |  |  |
| `terms.create` |  |  |  |  |  |  |  |  | X |  |  |  |
| `terms.read` | X |  | X |  | X |  |  | X | X |  |  |  |
| `terms.update` | X |  |  |  |  |  |  |  | X |  |  |  |
| `users.create` | X |  |  |  |  |  |  |  | X |  |  |  |
| `users.read` | X |  |  |  |  |  |  |  | X |  |  |  |
| `users.update` | X |  |  |  |  |  |  |  | X |  |  |  |

## Role Summary

| Role | Active | Permission Count |
| --- | --- | ---: |
| `CAMPUS_ADMIN` - Campus Admin | Yes | 48 |
| `CAO` - Chief Academic Offier | No | 0 |
| `DEAN` - Academic Dean | Yes | 27 |
| `FACULTY` - Faculty | Yes | 7 |
| `NCBA_CAO` - Chief Academic Offier | Yes | 24 |
| `NCBA_CUBAO_AC` - NCBA CUBAO Area Chairman | Yes | 6 |
| `NCBA_FAIRVIEW_AC` - NCBA Fairview Area Chairpersons | Yes | 9 |
| `REGISTRAR` - Registrar | Yes | 20 |
| `SUPER_ADMIN` - Super Admin | Yes | 100 |
| `TENANT_ADMIN` - Tenant Admin | Yes | 7 |
| `TMP_ADMIN_140655` - Tmp Admin | Yes | 0 |
| `TMP_ADMIN_X` - Tmp Admin | Yes | 0 |
