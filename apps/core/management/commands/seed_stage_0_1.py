from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class Command(BaseCommand):
    help = "Seed TeacherMate+ Stage 0.1 baseline data (idempotent)."

    roles = [
        ("SUPER_ADMIN", "Super Admin"),
        ("TENANT_ADMIN", "Tenant Admin"),
        ("CAMPUS_ADMIN", "Campus Admin"),
        ("REGISTRAR", "Registrar"),
        ("DEAN", "Academic Dean"),
        ("COLLEGE_DEAN", "College Dean"),
        ("FACULTY", "Faculty"),
    ]

    permissions = [
        ("admin_portal.access", "admin_portal", "access"),
        ("faculty_portal.access", "faculty_portal", "access"),
        ("dashboard.read", "dashboard", "read"),
        ("grading_analytics.read", "grading_analytics", "read"),
        ("grade_distribution_monitor.read", "grade_distribution_monitor", "read"),
        ("faculty_analytics.read", "faculty_analytics", "read"),
        ("users.read", "users", "read"),
        ("users.create", "users", "create"),
        ("users.update", "users", "update"),
        ("user_roles.update", "user_roles", "update"),
        ("roles.read", "roles", "read"),
        ("roles.create", "roles", "create"),
        ("roles.update", "roles", "update"),
        ("permissions.read", "permissions", "read"),
        ("menus.read", "menus", "read"),
        ("menus.update", "menus", "update"),
        ("tenants.read", "tenants", "read"),
        ("tenants.create", "tenants", "create"),
        ("tenants.update", "tenants", "update"),
        ("campuses.read", "campuses", "read"),
        ("campuses.create", "campuses", "create"),
        ("campuses.update", "campuses", "update"),
        ("departments.read", "departments", "read"),
        ("departments.create", "departments", "create"),
        ("departments.update", "departments", "update"),
        ("programs.read", "programs", "read"),
        ("programs.create", "programs", "create"),
        ("programs.update", "programs", "update"),
        ("academic_years.read", "academic_years", "read"),
        ("academic_years.create", "academic_years", "create"),
        ("academic_years.update", "academic_years", "update"),
        ("terms.read", "terms", "read"),
        ("terms.create", "terms", "create"),
        ("terms.update", "terms", "update"),
        ("courses.read", "courses", "read"),
        ("courses.create", "courses", "create"),
        ("courses.update", "courses", "update"),
        ("courses.import", "courses", "import"),
        ("sections.read", "sections", "read"),
        ("sections.create", "sections", "create"),
        ("sections.update", "sections", "update"),
        ("sections.import", "sections", "import"),
        ("offerings.read", "offerings", "read"),
        ("offerings.create", "offerings", "create"),
        ("offerings.update", "offerings", "update"),
        ("course_offerings.import", "course_offerings", "import"),
        ("faculty_assignments.read", "faculty_assignments", "read"),
        ("faculty_assignments.create", "faculty_assignments", "create"),
        ("faculty_assignments.update", "faculty_assignments", "update"),
        ("faculty_assignments.import", "faculty_assignments", "import"),
        ("faculty_activity_monitor.read", "faculty_activity_monitor", "read"),
        ("faculty_gradebook_monitor.read", "faculty_gradebook_monitor", "read"),
        ("faculty_replacement.view", "faculty_replacement", "view"),
        ("faculty_replacement.process", "faculty_replacement", "process"),
        ("faculty_final_clearance.read", "faculty_final_clearance", "read"),
        ("grade_prediction_monitor.read", "grade_prediction_monitor", "read"),
        ("gradebook.view_student_identity", "gradebook", "view_student_identity"),
        ("students.read", "students", "read"),
        ("students.create", "students", "create"),
        ("students.update", "students", "update"),
        ("students.import", "students", "import"),
        ("student_enrollment_query.read", "student_enrollment_query", "read"),
        ("enrollment.read", "enrollment", "read"),
        ("enrollment.create", "enrollment", "create"),
        ("enrollment.update", "enrollment", "update"),
        ("enrollment.import", "enrollment", "import"),
        ("enrollment_adjustment.view", "enrollment_adjustment", "view"),
        ("enrollment_adjustment.process", "enrollment_adjustment", "process"),
        ("import_batches.read", "import_batches", "read"),
        ("actual_data_reset.run", "actual_data_reset", "run"),
        ("inactive_records.delete", "inactive_records", "delete"),
        ("system_settings.update", "system_settings", "update"),
        ("grading_governance_settings.update", "grading_governance_settings", "update"),
        ("grading_templates.read", "grading_templates", "read"),
        ("grading_templates.create", "grading_templates", "create"),
        ("grading_templates.update", "grading_templates", "update"),
        ("grading_templates.submit_for_approval", "grading_templates", "submit_for_approval"),
        ("grading_templates.approve", "grading_templates", "approve"),
        ("grading_templates.publish", "grading_templates", "publish"),
        ("template_hotfixes.read", "template_hotfixes", "read"),
        ("template_hotfixes.create", "template_hotfixes", "create"),
        ("template_hotfixes.review", "template_hotfixes", "review"),
        ("template_periods.read", "template_periods", "read"),
        ("template_periods.create", "template_periods", "create"),
        ("template_periods.update", "template_periods", "update"),
        ("template_components.read", "template_components", "read"),
        ("template_components.create", "template_components", "create"),
        ("template_components.update", "template_components", "update"),
        ("template_subcomponents.read", "template_subcomponents", "read"),
        ("template_subcomponents.create", "template_subcomponents", "create"),
        ("template_subcomponents.update", "template_subcomponents", "update"),
        ("template_details.read", "template_details", "read"),
        ("template_details.create", "template_details", "create"),
        ("template_details.update", "template_details", "update"),
        ("course_template_assignments.read", "course_template_assignments", "read"),
        ("course_template_assignments.create", "course_template_assignments", "create"),
        ("course_template_assignments.update", "course_template_assignments", "update"),
        ("course_base_overrides.read", "course_base_overrides", "read"),
        ("course_base_overrides.create", "course_base_overrides", "create"),
        ("course_base_overrides.update", "course_base_overrides", "update"),
        ("tenant_grading_profiles.read", "tenant_grading_profiles", "read"),
        ("tenant_grading_profiles.create", "tenant_grading_profiles", "create"),
        ("tenant_grading_profiles.update", "tenant_grading_profiles", "update"),
        ("grading_periods.read", "grading_periods", "read"),
        ("grading_periods.lock", "grading_periods", "lock"),
        ("grading_periods.reopen", "grading_periods", "reopen"),
        ("grading_encoding_control.manage", "grading_encoding_control", "manage"),
        ("grade_submissions.read", "grade_submissions", "read"),
        ("grade_submissions.reopen", "grade_submissions", "reopen"),
        ("grade_submissions.revert_before_deadline", "grade_submissions", "revert_before_deadline"),
        ("reopen_requests.read", "reopen_requests", "read"),
        ("reopen_requests.create", "reopen_requests", "create"),
        ("reopen_requests.review", "reopen_requests", "review"),
        ("corrections.read", "corrections", "read"),
        ("corrections.review", "corrections", "review"),
        ("corrections.create", "corrections", "create"),
        ("corrections.create_on_behalf", "corrections", "create_on_behalf"),
        ("audit_logs.read", "audit_logs", "read"),
    ]

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Seeding Stage 0.1 data..."))

        tenant, _ = Tenant.objects.get_or_create(
            code="DEFAULT",
            defaults={"name": "Default Tenant", "is_active": True},
        )
        campus, _ = Campus.objects.get_or_create(
            tenant=tenant,
            code="MAIN",
            defaults={"name": "Main Campus", "is_active": True},
        )
        department, _ = Department.objects.get_or_create(
            tenant=tenant,
            campus=campus,
            code="COLLEGE",
            defaults={"name": "College Department", "is_active": True},
        )
        Program.objects.get_or_create(
            tenant=tenant,
            campus=campus,
            department=department,
            code="BSIT",
            defaults={"name": "BS Information Technology", "level": "COLLEGE", "is_active": True},
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="ENROLLMENT_OWNERSHIP_MODE",
            defaults={
                "setting_value": "ADMIN_ONLY",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="ENROLLMENT_STUDENT_MODE",
            defaults={
                "setting_value": "STRICT_EXISTING",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="USER_EMAIL_ALLOWED_DOMAINS",
            defaults={
                "setting_value": "ncba.edu.ph",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="ACTIVE_ACADEMIC_YEAR_CODE",
            defaults={
                "setting_value": "",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": False,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="ACTIVE_TERM_CODE",
            defaults={
                "setting_value": "",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": False,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="CORRECTION_MODE",
            defaults={
                "setting_value": "SYSTEM_REQUEST",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="PASSING_GRADE_THRESHOLD",
            defaults={
                "setting_value": "75",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="ENROLLMENT_OWNERSHIP_MODE",
            defaults={
                "setting_value": "ADMIN_ONLY",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="ENROLLMENT_STUDENT_MODE",
            defaults={
                "setting_value": "STRICT_EXISTING",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="USER_EMAIL_ALLOWED_DOMAINS",
            defaults={
                "setting_value": "ncba.edu.ph",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="ACTIVE_ACADEMIC_YEAR_CODE",
            defaults={
                "setting_value": "",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": False,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="ACTIVE_TERM_CODE",
            defaults={
                "setting_value": "",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": False,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="CORRECTION_MODE",
            defaults={
                "setting_value": "SYSTEM_REQUEST",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )
        SystemSetting.objects.update_or_create(
            tenant=tenant,
            setting_key="PASSING_GRADE_THRESHOLD",
            defaults={
                "setting_value": "75",
                "value_type": SystemSetting.ValueType.STRING,
                "is_active": True,
            },
        )

        role_map = {}
        for code, name in self.roles:
            role, _ = Role.objects.get_or_create(
                code=code,
                defaults={"name": name, "description": name, "is_system": True, "is_active": True},
            )
            role_map[code] = role

        perm_map = {}
        for code, module, action in self.permissions:
            perm, _ = Permission.objects.get_or_create(
                code=code,
                defaults={
                    "module": module,
                    "action": action,
                    "description": f"{action.title()} {module}",
                    "is_active": True,
                },
            )
            perm_map[code] = perm

        super_admin_role = role_map["SUPER_ADMIN"]
        for perm in perm_map.values():
            RolePermission.objects.get_or_create(role=super_admin_role, permission=perm)

        group_specs = [
            ("ADMIN", "DASHBOARD", "Dashboard", 10),
            ("ADMIN", "SECURITY", "Security", 20),
            ("ADMIN", "ORGANIZATION", "Organization", 30),
            ("ADMIN", "ACADEMICS", "Academics", 40),
            ("ADMIN", "STUDENTS", "Students", 50),
            ("ADMIN", "ENROLLMENT", "Enrollment", 60),
            ("ADMIN", "IMPORTS", "Tools", 95),
            ("ADMIN", "GRADING", "Grading", 80),
            ("ADMIN", "NAVIGATION", "Navigation", 90),
            ("ADMIN", "AUDIT", "Audit", 100),
            ("FACULTY", "DASHBOARD", "Dashboard", 10),
            # The faculty sidebar uses the dedicated "Classes" block in the template.
            # Keep this seeded group inactive so it does not duplicate the main page link.
            ("FACULTY", "COURSES", "My Classes", 20),
        ]
        groups = {}
        for portal, code, label, sort_order in group_specs:
            group, _ = MenuGroup.objects.update_or_create(
                portal=portal,
                code=code,
                defaults={
                    "label": label,
                    "sort_order": sort_order,
                    "is_active": False if portal == "FACULTY" and code == "COURSES" else True,
                },
            )
            groups[code if portal == "ADMIN" else f"{portal}_{code}"] = group

        item_specs = [
            (
                "ADMIN",
                "ADMIN_DASHBOARD",
                groups["DASHBOARD"],
                "Dashboard",
                "admin_portal:dashboard",
                10,
                "dashboard.read",
            ),
            (
                "ADMIN",
                "USERS",
                groups["SECURITY"],
                "Users",
                "admin_portal:user_list",
                20,
                "users.read",
            ),
            (
                "ADMIN",
                "LOGIN_LOCKOUTS",
                groups["SECURITY"],
                "Login Lockouts",
                "admin_portal:login_lockout_list",
                25,
                "users.read",
            ),
            (
                "ADMIN",
                "FACULTY_DEACTIVATION",
                groups["SECURITY"],
                "Faculty Deactivation",
                "admin_portal:faculty_deactivation_schedule",
                27,
                "users.update",
            ),
            (
                "ADMIN",
                "ROLES",
                groups["SECURITY"],
                "Roles",
                "admin_portal:role_list",
                30,
                "roles.read",
            ),
            (
                "ADMIN",
                "TENANTS",
                groups["ORGANIZATION"],
                "Tenants",
                "admin_portal:tenant_list",
                10,
                "tenants.read",
            ),
            (
                "ADMIN",
                "CAMPUSES",
                groups["ORGANIZATION"],
                "Campuses",
                "admin_portal:campus_list",
                20,
                "campuses.read",
            ),
            (
                "ADMIN",
                "DEPARTMENTS",
                groups["ORGANIZATION"],
                "Departments",
                "admin_portal:department_list",
                30,
                "departments.read",
            ),
            (
                "ADMIN",
                "PROGRAMS",
                groups["ORGANIZATION"],
                "Programs",
                "admin_portal:program_list",
                40,
                "programs.read",
            ),
            (
                "ADMIN",
                "ACADEMIC_YEARS",
                groups["ACADEMICS"],
                "Academic Years",
                "admin_portal:academic_year_list",
                10,
                "academic_years.read",
            ),
            (
                "ADMIN",
                "TERMS",
                groups["ACADEMICS"],
                "Terms",
                "admin_portal:term_list",
                20,
                "terms.read",
            ),
            (
                "ADMIN",
                "COURSES",
                groups["ACADEMICS"],
                "Courses",
                "admin_portal:course_list",
                30,
                "courses.read",
            ),
            (
                "ADMIN",
                "SECTIONS",
                groups["ACADEMICS"],
                "Sections",
                "admin_portal:section_list",
                40,
                "sections.read",
            ),
            (
                "ADMIN",
                "OFFERINGS",
                groups["ACADEMICS"],
                "Course Offerings",
                "admin_portal:offering_list",
                50,
                "offerings.read",
            ),
            (
                "ADMIN",
                "FACULTY_ASSIGNMENTS",
                groups["ACADEMICS"],
                "Faculty Assignments",
                "admin_portal:faculty_assignment_list",
                60,
                "faculty_assignments.read",
            ),
            (
                "ADMIN",
                "FACULTY_ACTIVITY_MONITOR",
                groups["ACADEMICS"],
                "Faculty Activity Monitor",
                "admin_portal:faculty_activity_monitor",
                65,
                "faculty_activity_monitor.read",
            ),
            (
                "ADMIN",
                "FACULTY_FINAL_CLEARANCE",
                groups["ACADEMICS"],
                "Faculty Final Clearance",
                "admin_portal:faculty_final_clearance",
                66,
                "faculty_final_clearance.read",
            ),
            (
                "ADMIN",
                "STUDENTS",
                groups["STUDENTS"],
                "Students",
                "admin_portal:student_list",
                10,
                "students.read",
            ),
            (
                "ADMIN",
                "STUDENT_ENROLLMENT_QUERY",
                groups["ENROLLMENT"],
                "Student Enrollment Query",
                "admin_portal:student_enrollment_query",
                20,
                "student_enrollment_query.read",
            ),
            (
                "ADMIN",
                "ENROLLMENTS",
                groups["ENROLLMENT"],
                "Enrollment",
                "admin_portal:enrollment_list",
                10,
                "enrollment.read",
            ),
            (
                "ADMIN",
                "ENROLLMENT_ADJUSTMENTS",
                groups["ENROLLMENT"],
                "Enrollment Adjustments",
                "admin_portal:enrollment_adjustments",
                25,
                "enrollment_adjustment.view",
            ),
            (
                "ADMIN",
                "BULK_IMPORTS",
                groups["IMPORTS"],
                "Bulk Imports",
                "admin_portal:import_batch_list",
                10,
                "import_batches.read",
            ),
            (
                "ADMIN",
                "ACTIVE_ACADEMIC_SCOPE",
                groups["IMPORTS"],
                "Active Academic Scope",
                "admin_portal:active_academic_term_settings",
                20,
                "system_settings.update",
            ),
            (
                "ADMIN",
                "ACTIVE_GRADING_PERIOD",
                groups["IMPORTS"],
                "Active Grading Period",
                "admin_portal:active_grading_period_settings",
                25,
                "system_settings.update",
            ),
            (
                "ADMIN",
                "CORRECTION_GOVERNANCE",
                groups["IMPORTS"],
                "Correction Governance",
                "admin_portal:correction_governance_settings",
                30,
                "grading_governance_settings.update",
            ),
            (
                "ADMIN",
                "DOCUMENT_PRINT_SETTINGS",
                groups["IMPORTS"],
                "Document Print Settings",
                "admin_portal:document_print_settings",
                40,
                "system_settings.update",
            ),
            (
                "ADMIN",
                "CONFIGURABLE_FEATURES",
                groups["IMPORTS"],
                "Configuration Management",
                "admin_portal:configurable_features_settings",
                50,
                "system_settings.update",
            ),
            (
                "ADMIN",
                "TEMPLATE_GOVERNANCE",
                groups["IMPORTS"],
                "Template Governance",
                "admin_portal:template_governance_settings",
                55,
                "system_settings.update",
            ),
            (
                "ADMIN",
                "EMAIL_DIAGNOSTICS",
                groups["IMPORTS"],
                "Email Diagnostics",
                "admin_portal:email_diagnostics",
                60,
                "import_batches.read",
            ),
            (
                "ADMIN",
                "ACTUAL_DATA_RESET",
                groups["IMPORTS"],
                "Actual Data Reset",
                "admin_portal:actual_data_reset",
                70,
                "actual_data_reset.run",
            ),
            (
                "ADMIN",
                "GRADING_TEMPLATES",
                groups["GRADING"],
                "Grading Templates",
                "admin_portal:grading_template_list",
                10,
                "grading_templates.read",
            ),
            (
                "ADMIN",
                "TEMPLATE_PERIODS",
                groups["GRADING"],
                "Template Periods",
                "admin_portal:template_period_list",
                20,
                "template_periods.read",
            ),
            (
                "ADMIN",
                "TEMPLATE_COMPONENTS",
                groups["GRADING"],
                "Template Components",
                "admin_portal:template_component_list",
                30,
                "template_components.read",
            ),
            (
                "ADMIN",
                "TEMPLATE_SUBCOMPONENTS",
                groups["GRADING"],
                "Template Subcomponents",
                "admin_portal:template_subcomponent_list",
                40,
                "template_subcomponents.read",
            ),
            (
                "ADMIN",
                "TEMPLATE_DETAILS",
                groups["GRADING"],
                "Template Details",
                "admin_portal:template_detail_list",
                45,
                "template_details.read",
            ),
            (
                "ADMIN",
                "COURSE_TEMPLATE_ASSIGNMENTS",
                groups["GRADING"],
                "Course Template Assignments",
                "admin_portal:course_template_assignment_list",
                50,
                "course_template_assignments.read",
            ),
            (
                "ADMIN",
                "COURSE_BASE_OVERRIDES",
                groups["GRADING"],
                "Course Base Overrides",
                "admin_portal:course_base_override_list",
                60,
                "course_base_overrides.read",
            ),
            (
                "ADMIN",
                "TENANT_GRADING_PROFILES",
                groups["GRADING"],
                "Tenant Grading Profiles",
                "admin_portal:tenant_grading_profile_list",
                65,
                "tenant_grading_profiles.read",
            ),
            (
                "ADMIN",
                "GRADING_PERIOD_LOCKS",
                groups["GRADING"],
                "Period Locks",
                "admin_portal:grading_period_lock_list",
                70,
                "grading_periods.read",
            ),
            (
                "ADMIN",
                "GRADE_ENCODING_CONTROL",
                groups["GRADING"],
                "Grade Encoding Access Control",
                "admin_portal:grade_encoding_control_list",
                72,
                "grading_encoding_control.manage",
            ),
            (
                "ADMIN",
                "GRADING_ANALYTICS",
                groups["GRADING"],
                "Grading Analytics",
                "admin_portal:grading_analytics",
                75,
                "grading_analytics.read",
            ),
            (
                "ADMIN",
                "ACADEMIC_PERFORMANCE_INSIGHTS",
                groups["GRADING"],
                "Academic Performance Insights",
                "admin_portal:academic_performance_insights",
                76,
                "grading_analytics.read",
            ),
            (
                "ADMIN",
                "GRADE_DISTRIBUTION_MONITOR",
                groups["GRADING"],
                "Grade Distribution Monitor",
                "admin_portal:grade_distribution_monitor",
                78,
                "grade_distribution_monitor.read",
            ),
            (
                "ADMIN",
                "GRADE_SUBMISSIONS",
                groups["GRADING"],
                "Submissions",
                "admin_portal:grade_submission_list",
                80,
                "grade_submissions.read",
            ),
            (
                "ADMIN",
                "GRADE_REOPEN_REQUESTS",
                groups["GRADING"],
                "Reopen Requests",
                "admin_portal:grade_submission_reopen_request_list",
                85,
                "reopen_requests.read",
            ),
            (
                "ADMIN",
                "GRADE_CORRECTIONS",
                groups["GRADING"],
                "Correction Queue",
                "admin_portal:grade_correction_request_list",
                90,
                "corrections.read",
            ),
            (
                "ADMIN",
                "GRADE_CORRECTION_ON_BEHALF",
                groups["GRADING"],
                "Create Correction On Behalf",
                "admin_portal:grade_correction_request_create_on_behalf",
                91,
                "corrections.create_on_behalf",
            ),
            (
                "ADMIN",
                "TEMPLATE_HOTFIX_REQUESTS",
                groups["GRADING"],
                "Template Hotfix Requests",
                "admin_portal:template_hotfix_list",
                95,
                "template_hotfixes.read",
            ),
            (
                "ADMIN",
                "MENU_GROUPS",
                groups["NAVIGATION"],
                "Menu Groups",
                "admin_portal:menu_group_list",
                10,
                "menus.read",
            ),
            (
                "ADMIN",
                "MENU_ITEMS",
                groups["NAVIGATION"],
                "Menu Items",
                "admin_portal:menu_item_list",
                20,
                "menus.read",
            ),
            (
                "ADMIN",
                "AUDIT_LOGS",
                groups["AUDIT"],
                "Audit Logs",
                "admin_portal:audit_log_list",
                10,
                "audit_logs.read",
            ),
            (
                "FACULTY",
                "FACULTY_DASHBOARD",
                groups["FACULTY_DASHBOARD"],
                "Dashboard",
                "faculty_portal:dashboard",
                10,
                "dashboard.read",
            ),
            (
                "FACULTY",
                "FACULTY_ANALYTICS",
                groups["FACULTY_DASHBOARD"],
                "Grading Analytics",
                "faculty_portal:analytics",
                15,
                "faculty_analytics.read",
            ),
            (
                "FACULTY",
                "MY_COURSES",
                groups["FACULTY_COURSES"],
                "My Classes",
                "faculty_portal:my_courses",
                20,
                "faculty_portal.access",
            ),
        ]

        menu_permission_pairs = []
        for portal, code, menu_group, label, route_name, sort_order, permission_code in item_specs:
            item, _ = MenuItem.objects.update_or_create(
                portal=portal,
                code=code,
                defaults={
                    "menu_group": menu_group,
                    "label": label,
                    "route_name": route_name,
                    "sort_order": sort_order,
                    "is_active": False if portal == "FACULTY" and code == "MY_COURSES" else True,
                },
            )
            menu_permission_pairs.append((item, permission_code))

        for menu_item, permission_code in menu_permission_pairs:
            MenuItemPermission.objects.get_or_create(menu_item=menu_item, permission=perm_map[permission_code])

        # Hide legacy low-level grading entries from sidebar.
        # These are still accessible by route when needed, but the builder flow now owns template composition.
        retired_grading_menu_codes = [
            "TEMPLATE_PERIODS",
            "TEMPLATE_COMPONENTS",
            "TEMPLATE_SUBCOMPONENTS",
            "TEMPLATE_DETAILS",
        ]
        MenuItem.objects.filter(portal="ADMIN", code__in=retired_grading_menu_codes).update(is_active=False)

        faculty_role = role_map["FACULTY"]
        for faculty_perm in ["faculty_portal.access", "dashboard.read", "corrections.create"]:
            RolePermission.objects.get_or_create(role=faculty_role, permission=perm_map[faculty_perm])

        college_dean_role = role_map["COLLEGE_DEAN"]
        college_dean_permissions = [
            "admin_portal.access",
            "dashboard.read",
            "courses.read",
            "sections.read",
            "offerings.read",
            "faculty_assignments.read",
            "faculty_activity_monitor.read",
            "faculty_gradebook_monitor.read",
            "faculty_final_clearance.read",
            "grade_prediction_monitor.read",
            "grading_analytics.read",
            "grade_distribution_monitor.read",
            "grading_templates.read",
            "template_components.read",
            "template_subcomponents.read",
            "template_details.read",
            "template_hotfixes.read",
            "course_template_assignments.read",
            "course_base_overrides.read",
            "grade_submissions.read",
            "corrections.read",
            "reopen_requests.read",
        ]
        for permission_code in college_dean_permissions:
            RolePermission.objects.get_or_create(
                role=college_dean_role,
                permission=perm_map[permission_code],
            )

        approver_permissions = [
            "grading_templates.read",
            "grading_templates.approve",
            "template_hotfixes.read",
            "template_hotfixes.review",
        ]
        requestor_permissions = [
            "grading_templates.read",
            "grading_templates.update",
            "grading_templates.submit_for_approval",
            "template_hotfixes.read",
            "template_hotfixes.create",
        ]
        for role_code in ["COLLEGE_DEAN", "DEAN", "REGISTRAR", "CAMPUS_ADMIN"]:
            role_obj = role_map[role_code]
            for perm_code in approver_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["TENANT_ADMIN", "CAMPUS_ADMIN"]:
            role_obj = role_map[role_code]
            for perm_code in requestor_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])

        correction_monitor_permissions = ["corrections.read"]
        correction_on_behalf_permissions = ["corrections.read", "corrections.create_on_behalf"]
        correction_reviewer_permissions = ["corrections.review"]
        reopen_requestor_permissions = ["reopen_requests.read", "reopen_requests.create"]
        reopen_reviewer_permissions = [
            "reopen_requests.read",
            "reopen_requests.review",
            "grade_submissions.revert_before_deadline",
        ]
        for role_code in ["DEAN", "COLLEGE_DEAN", "REGISTRAR", "CAMPUS_ADMIN"]:
            role_obj = role_map[role_code]
            for perm_code in correction_monitor_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["AC", "COLLEGE_DEAN", "DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            for perm_code in correction_on_behalf_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["SUPER_ADMIN", "COLLEGE_DEAN", "DEAN"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            RolePermission.objects.get_or_create(
                role=role_obj,
                permission=perm_map["gradebook.view_student_identity"],
            )
        for role_code in ["REGISTRAR"]:
            role_obj = role_map[role_code]
            for perm_code in correction_reviewer_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["REGISTRAR", "CAMPUS_ADMIN", "TENANT_ADMIN"]:
            role_obj = role_map[role_code]
            for perm_code in reopen_requestor_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["REGISTRAR", "CAMPUS_ADMIN", "TENANT_ADMIN"]:
            role_obj = role_map[role_code]
            RolePermission.objects.get_or_create(
                role=role_obj,
                permission=perm_map["student_enrollment_query.read"],
            )
        for role_code in ["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            for perm_code in ["faculty_replacement.view", "faculty_replacement.process"]:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            RolePermission.objects.get_or_create(role=role_obj, permission=perm_map["faculty_replacement.view"])
        academic_monitor_permissions = [
            "faculty_activity_monitor.read",
            "faculty_gradebook_monitor.read",
            "faculty_final_clearance.read",
            "grade_prediction_monitor.read",
        ]
        for role_code in ["AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            for perm_code in academic_monitor_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["COLLEGE_DEAN", "DEAN", "CAMPUS_ADMIN"]:
            role_obj = role_map[role_code]
            for perm_code in reopen_reviewer_permissions:
                RolePermission.objects.get_or_create(role=role_obj, permission=perm_map[perm_code])
        for role_code in ["TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR", "AC", "DEAN", "COLLEGE_DEAN", "CAO"]:
            role_obj = role_map.get(role_code)
            if not role_obj:
                continue
            RolePermission.objects.get_or_create(
                role=role_obj,
                permission=perm_map["grading_encoding_control.manage"],
            )

        User = get_user_model()
        default_username = "superadmin"
        default_email = "superadmin@teachermateplus.local"
        default_password = "Admin@12345"

        user, created = User.objects.get_or_create(
            username=default_username,
            defaults={
                "email": default_email,
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
                "default_tenant": tenant,
                "default_campus": campus,
            },
        )
        if created:
            user.set_password(default_password)
            user.save(update_fields=["password"])
        elif not user.has_usable_password():
            user.set_password(default_password)
            user.save(update_fields=["password"])

        UserRole.objects.get_or_create(
            user=user,
            role=super_admin_role,
            tenant=tenant,
            campus=campus,
            defaults={"is_active": True},
        )

        self.stdout.write(self.style.SUCCESS("Stage 0.1 seed complete."))
        self.stdout.write(self.style.SUCCESS(f"Admin login URL: /admin-portal/login/"))
        self.stdout.write(self.style.SUCCESS(f"Faculty login URL: /faculty/login/"))
        self.stdout.write(self.style.SUCCESS(f"Seeded user: {default_username}"))
        self.stdout.write(self.style.SUCCESS(f"Seeded password: {default_password}"))
