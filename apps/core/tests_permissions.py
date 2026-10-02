"""Focused nullable scope/direct-DENY regressions, including real consumers."""
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.models import User
from apps.core.services.menu import MenuService
from apps.core.services.permissions import PermissionService
from apps.faculty_attendance.permissions import VIEW_PERMISSION, require_attendance_permission
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, SystemSetting, Tenant


class NullableScopePermissionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code='SCOPE', name='Scope test')
        self.campus = Campus.objects.create(tenant=self.tenant, code='C', name='Campus')
        self.other_campus = Campus.objects.create(tenant=self.tenant, code='O', name='Other')
        self.department = Department.objects.create(tenant=self.tenant, campus=self.campus, code='D', name='Department')
        self.user = User.objects.create_user('scope-user', 'scope@example.invalid')
        self.permission = Permission.objects.get(code=VIEW_PERMISSION)
        self.scope = {'tenant_id': self.tenant.pk, 'campus_id': self.campus.pk}
        UserPermission.objects.create(user=self.user, permission=self.permission, grant_type='ALLOW',
                                      tenant=self.tenant, campus=self.campus)

    def deny(self, **scope):
        return UserPermission.objects.create(user=self.user, permission=self.permission,
                                             grant_type='DENY', **scope)

    def assert_denied(self):
        self.assertFalse(PermissionService.has_permission(self.user, VIEW_PERMISSION, **self.scope))
        self.assertFalse(PermissionService.has_any_permission(self.user, [VIEW_PERMISSION], **self.scope))
        self.assertFalse(PermissionService.has_assigned_permission(self.user, VIEW_PERMISSION, **self.scope))

    def test_global_deny_overrides_exact_campus_allow(self):
        self.deny()
        self.assert_denied()

    def test_tenant_wide_deny_overrides_exact_campus_allow(self):
        self.deny(tenant=self.tenant)
        self.assert_denied()

    def test_exact_campus_deny_does_not_leak_into_another_campus(self):
        self.deny(tenant=self.tenant, campus=self.campus)
        UserPermission.objects.create(user=self.user, permission=self.permission, grant_type='ALLOW', tenant=self.tenant)
        self.assert_denied()
        self.assertTrue(PermissionService.has_permission(self.user, VIEW_PERMISSION,
            tenant_id=self.tenant.pk, campus_id=self.other_campus.pk))

    def test_global_role_and_tenant_role_match_but_other_tenant_does_not(self):
        UserPermission.objects.filter(user=self.user).delete()
        role = Role.objects.create(code='NULL_SCOPE_TEST', name='Scoped test')
        RolePermission.objects.create(role=role, permission=self.permission)
        grant = UserRole.objects.create(user=self.user, role=role)
        self.assertTrue(PermissionService.has_permission(self.user, VIEW_PERMISSION, **self.scope))
        grant.tenant = self.tenant; grant.save(update_fields=['tenant'])
        self.assertTrue(PermissionService.has_permission(self.user, VIEW_PERMISSION, **self.scope))
        other = Tenant.objects.create(code='OTHER-SCOPE', name='Other scope')
        self.assertFalse(PermissionService.has_permission(self.user, VIEW_PERMISSION,
            tenant_id=other.pk, campus_id=self.other_campus.pk))

    def test_attendance_department_helper_honors_wildcard_allow_and_global_deny(self):
        from apps.core.services.features import FeatureSettingsService
        SystemSetting.objects.create(tenant=self.tenant, setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
                                      setting_value='true', value_type='BOOL')
        UserPermission.objects.filter(user=self.user).delete()
        UserPermission.objects.create(user=self.user, permission=self.permission, grant_type='ALLOW', tenant=self.tenant)
        require_attendance_permission(user=self.user, permission_code=VIEW_PERMISSION,
                                      department_id=self.department.pk, **self.scope)
        self.deny()
        with self.assertRaises(PermissionDenied):
            require_attendance_permission(user=self.user, permission_code=VIEW_PERMISSION,
                                          department_id=self.department.pk, **self.scope)

    def test_menu_consumer_hides_globally_denied_exact_allow(self):
        group = MenuGroup.objects.create(portal='ADMIN', code='SCOPE_TEST', label='Scope test')
        item = MenuItem.objects.create(portal='ADMIN', menu_group=group, code='SCOPE_ITEM', label='Scoped item')
        MenuItemPermission.objects.create(menu_item=item, permission=self.permission)
        self.assertIn(group.pk, [n['group'].pk for n in MenuService.get_menu_tree(self.user, 'ADMIN', **self.scope)])
        self.deny()
        self.assertNotIn(group.pk, [n['group'].pk for n in MenuService.get_menu_tree(self.user, 'ADMIN', **self.scope)])

    def test_explicit_exact_assignment_contract_is_not_widened(self):
        # This distinct contributor-eligibility API intentionally ignores NULL rows.
        UserPermission.objects.filter(user=self.user).delete()
        UserPermission.objects.create(user=self.user, permission=self.permission, grant_type='ALLOW')
        self.assertFalse(PermissionService.has_assigned_permission(self.user, VIEW_PERMISSION,
            exact_scope=True, **self.scope))
