# TeacherMate+ Database Schema Dictionary

This document is generated from the current Django model registry so it reflects the actual TeacherMate+ schema at generation time.

## Notes

- **Django Type** shows the Django field class used by the model.
- Production MySQL/MariaDB column types may differ slightly at the storage level from Django field names.
- Relationship targets are shown using both the database table name and the Django model label.
- This dictionary includes both TeacherMate+ application tables and the small number of built-in Django framework tables used by the project.

## Application Areas

- **Security and access:** accounts, RBAC, audit, navigation
- **Institution structure:** tenants, campuses, departments, programs, terms
- **Operations:** course offerings, faculty assignments, enrollments, imports
- **Grading:** templates, activities, scores, summaries, submissions, correction governance
- **Support workflows:** attendance, notifications, reminders, prediction snapshots

## `accounts`

### `login_otp_challenges`

- **Model:** `accounts.LoginOtpChallenge`
- **Purpose:** TeacherMate+ application table.

**Relationships**
- `user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `portal_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `code_hash` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `sent_to_email` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `expires_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `consumed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `attempt_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `last_attempt_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |

### `portal_login_lockout_states`

- **Model:** `accounts.PortalLoginLockoutState`
- **Purpose:** Tracks repeated failed login attempts and temporary portal-specific lockout state.

**Relationships**
- `user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `username, portal_code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Related user account. |
| `username` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `portal_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `failed_attempt_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `window_started_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `last_failed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `locked_until` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `last_ip` | `GenericIPAddressField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |

### `user_deactivation_schedules`

- **Model:** `accounts.UserDeactivationSchedule`
- **Purpose:** TeacherMate+ application table.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `scheduled_by_user` -> `users` (`accounts.User`)
- `cancelled_by_user` -> `users` (`accounts.User`)
- `applied_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `scheduled_for` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `reason` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `scheduled_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `cancelled_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `applied_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `cancelled_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `applied_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |

### `user_signature_credentials`

- **Model:** `accounts.UserSignatureCredential`
- **Purpose:** Encrypted account-level signature image credential for approved printable documents.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `uploaded_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `OneToOneField` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `encrypted_blob` | `BinaryField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `encryption_nonce` | `BinaryField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `original_filename` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `mime_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `image_format` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `image_width` | `PositiveIntegerField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `image_height` | `PositiveIntegerField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `file_size_bytes` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `content_sha256` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `uploaded_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `uploaded_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `last_used_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `is_enabled` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |

### `user_signature_usage_logs`

- **Model:** `accounts.UserSignatureUsageLog`
- **Purpose:** Audit trail for stored signature placement on official generated documents.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `actor` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `document_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `document_reference` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `usage_role` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `portal_code` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `actor` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `used_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `metadata_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload for extra metadata. |

### `users`

- **Model:** `accounts.User`
- **Purpose:** Primary application user account for Admin Portal and Faculty Portal access.

**Relationships**
- `default_tenant` -> `tenants` (`tenants.Tenant`)
- `default_campus` -> `campuses` (`tenants.Campus`)
- `default_department` -> `departments` (`tenants.Department`)
- `groups` -> many-to-many with `auth_group` (`auth.Group`)
- `user_permissions` -> many-to-many with `auth_permission` (`auth.Permission`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `password` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `last_login` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `is_superuser` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `username` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `email` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `first_name` | `CharField` | `No` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `last_name` | `CharField` | `No` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `middle_name` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `default_tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Foreign-key reference to `tenants.Tenant`. |
| `default_campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Foreign-key reference to `tenants.Campus`. |
| `default_department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Foreign-key reference to `tenants.Department`. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `is_staff` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `must_change_password` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `faculty_quick_tour_disabled` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `privacy_consent_version` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `privacy_consent_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `privacy_consent_ip` | `GenericIPAddressField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `groups` | `ManyToManyField` | `-` | `-` | `No` | ``auth_group` (`auth.Group`)` | Many-to-many relationship managed through an intermediate table. |
| `user_permissions` | `ManyToManyField` | `-` | `-` | `No` | ``auth_permission` (`auth.Permission`)` | Many-to-many relationship managed through an intermediate table. |

## `rbac`

### `permissions`

- **Model:** `rbac.Permission`
- **Purpose:** Permission catalog used for action-level access checks.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `module` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `action` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `description` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `role_permissions`

- **Model:** `rbac.RolePermission`
- **Purpose:** Maps permissions to roles.

**Relationships**
- `role` -> `roles` (`rbac.Role`)
- `permission` -> `permissions` (`rbac.Permission`)

**Unique / Structural Notes**
- `role, permission`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `role` | `ForeignKey` | `No` | `No` | `No` | `roles` (`rbac.Role`) | Foreign-key reference to `rbac.Role`. |
| `permission` | `ForeignKey` | `No` | `No` | `No` | `permissions` (`rbac.Permission`) | Foreign-key reference to `rbac.Permission`. |

### `roles`

- **Model:** `rbac.Role`
- **Purpose:** Role catalog used for RBAC and scoped governance.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `description` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `is_system` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `user_permissions`

- **Model:** `rbac.UserPermission`
- **Purpose:** Direct per-user permission grants or overrides.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `permission` -> `permissions` (`rbac.Permission`)
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)

**Unique / Structural Notes**
- `user, permission, grant_type, tenant, campus`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `permission` | `ForeignKey` | `No` | `No` | `No` | `permissions` (`rbac.Permission`) | Foreign-key reference to `rbac.Permission`. |
| `grant_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |

### `user_roles`

- **Model:** `rbac.UserRole`
- **Purpose:** Scoped assignment of a role to a user, optionally limited by tenant, campus, or department.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `role` -> `roles` (`rbac.Role`)
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)

**Unique / Structural Notes**
- `user, role, tenant, campus, department`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `role` | `ForeignKey` | `No` | `No` | `No` | `roles` (`rbac.Role`) | Foreign-key reference to `rbac.Role`. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `assigned_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |

## `auditlog`

### `audit_logs`

- **Model:** `auditlog.AuditLog`
- **Purpose:** Audit trail for sensitive portal actions and governance decisions.

**Relationships**
- `actor_user` -> `users` (`accounts.User`)
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `actor_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `portal` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `action` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `entity_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `entity_id` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `route_name` | `CharField` | `Yes` | `Yes` | `No` | - | Django route name captured for audit or navigation tracking. |
| `http_method` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `ip_address` | `GenericIPAddressField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `user_agent` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `before_json` | `JSONField` | `Yes` | `Yes` | `No` | - | JSON snapshot before a change. |
| `after_json` | `JSONField` | `Yes` | `Yes` | `No` | - | JSON snapshot after a change. |
| `metadata_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload for extra metadata. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |

## `navigation`

### `menu_groups`

- **Model:** `navigation.MenuGroup`
- **Purpose:** Top-level portal menu grouping.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- `portal, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `portal` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `icon` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `sort_order` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |

### `menu_item_permissions`

- **Model:** `navigation.MenuItemPermission`
- **Purpose:** Permission requirement mapping for menu visibility.

**Relationships**
- `menu_item` -> `menu_items` (`navigation.MenuItem`)
- `permission` -> `permissions` (`rbac.Permission`)

**Unique / Structural Notes**
- `menu_item, permission`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `menu_item` | `ForeignKey` | `No` | `No` | `No` | `menu_items` (`navigation.MenuItem`) | Foreign-key reference to `navigation.MenuItem`. |
| `permission` | `ForeignKey` | `No` | `No` | `No` | `permissions` (`rbac.Permission`) | Foreign-key reference to `rbac.Permission`. |

### `menu_items`

- **Model:** `navigation.MenuItem`
- **Purpose:** Portal navigation item, including nested/sidebar structure.

**Relationships**
- `menu_group` -> `menu_groups` (`navigation.MenuGroup`)
- `parent` -> `menu_items` (`navigation.MenuItem`)

**Unique / Structural Notes**
- `portal, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `menu_group` | `ForeignKey` | `No` | `No` | `No` | `menu_groups` (`navigation.MenuGroup`) | Foreign-key reference to `navigation.MenuGroup`. |
| `portal` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `route_name` | `CharField` | `Yes` | `Yes` | `No` | - | Django route name captured for audit or navigation tracking. |
| `icon` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `parent` | `ForeignKey` | `Yes` | `Yes` | `No` | `menu_items` (`navigation.MenuItem`) | Foreign-key reference to `navigation.MenuItem`. |
| `sort_order` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |

## `tenants`

### `campuses`

- **Model:** `tenants.Campus`
- **Purpose:** Campus under a tenant.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)

**Unique / Structural Notes**
- `tenant, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `address` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `departments`

- **Model:** `tenants.Department`
- **Purpose:** Department under a tenant and campus.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `parent` -> `departments` (`tenants.Department`)

**Unique / Structural Notes**
- `tenant, campus, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `parent` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Foreign-key reference to `tenants.Department`. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `operation_branch` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `unit_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |

### `programs`

- **Model:** `tenants.Program`
- **Purpose:** Academic program under a tenant/campus/department.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)

**Unique / Structural Notes**
- `tenant, campus, department, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `No` | `No` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `level` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `system_settings`

- **Model:** `tenants.SystemSetting`
- **Purpose:** Tenant-scoped key/value setting store used by configurable features and governance.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)

**Unique / Structural Notes**
- `tenant, setting_key`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `setting_key` | `CharField` | `No` | `No` | `No` | - | System-setting key name. |
| `setting_value` | `CharField` | `No` | `No` | `No` | - | Stored value for the setting key. |
| `value_type` | `CharField` | `No` | `No` | `No` | - | Type hint used to interpret the stored setting value. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |

### `tenant_api_keys`

- **Model:** `tenants.TenantApiKey`
- **Purpose:** TeacherMate+ application table.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `created_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `purpose` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `key_prefix` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `key_hash` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `expires_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `revoked_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `last_used_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `created_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

### `tenants`

- **Model:** `tenants.Tenant`
- **Purpose:** Top-level institution or tenant record.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |

## `academics`

### `academic_years`

- **Model:** `academics.AcademicYear`
- **Purpose:** Academic year master record.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)

**Unique / Structural Notes**
- `tenant, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `start_date` | `DateField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `end_date` | `DateField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |

### `active_grading_period_settings`

- **Model:** `academics.ActiveGradingPeriodSetting`
- **Purpose:** Current active grading period per tenant/campus/term, used for governance and faculty access rules.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `term` -> `terms` (`academics.Term`)
- `period` -> `tenant_term_grading_periods` (`academics.TenantTermGradingPeriod`)
- `set_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `tenant, campus, term`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `period` | `ForeignKey` | `No` | `No` | `No` | `tenant_term_grading_periods` (`academics.TenantTermGradingPeriod`) | Canonical grading period selected for the term/campus setting. |
| `set_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who set the active governance value. |
| `set_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `auto_advanced_from_deadline` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `course_offerings`

- **Model:** `academics.CourseOffering`
- **Purpose:** Concrete class offering for a course, section, term, and campus.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)
- `program` -> `programs` (`tenants.Program`)
- `academic_year` -> `academic_years` (`academics.AcademicYear`)
- `term` -> `terms` (`academics.Term`)
- `course` -> `courses` (`academics.Course`)
- `section` -> `sections` (`academics.Section`)

**Unique / Structural Notes**
- `tenant, campus, department, term, course, section`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `No` | `No` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `program` | `ForeignKey` | `Yes` | `Yes` | `No` | `programs` (`tenants.Program`) | Owning or effective academic program scope for the record. |
| `academic_year` | `ForeignKey` | `No` | `No` | `No` | `academic_years` (`academics.AcademicYear`) | Academic year for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `course` | `ForeignKey` | `No` | `No` | `No` | `courses` (`academics.Course`) | Foreign-key reference to `academics.Course`. |
| `section` | `ForeignKey` | `No` | `No` | `No` | `sections` (`academics.Section`) | Foreign-key reference to `academics.Section`. |
| `room` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `schedule_text` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |

### `courses`

- **Model:** `academics.Course`
- **Purpose:** Course or subject master record.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)

**Unique / Structural Notes**
- `tenant, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `title` | `CharField` | `No` | `No` | `No` | - | Human-readable title. |
| `units` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `course_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `default_base_value` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |

### `faculty_assignments`

- **Model:** `academics.FacultyAssignment`
- **Purpose:** Faculty load assignment to an offering, including acceptance workflow and reminder state.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `faculty_user` -> `users` (`accounts.User`)
- `accepted_by` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `offering, faculty_user`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `faculty_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Faculty user assigned to the record. |
| `assignment_note` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `accepted_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `response_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `faculty_response_note` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `responded_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `accepted_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `response_due_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `last_reminded_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `reminder_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `is_primary` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `assigned_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |

### `sections`

- **Model:** `academics.Section`
- **Purpose:** Section/class grouping for students.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)
- `program` -> `programs` (`tenants.Program`)

**Unique / Structural Notes**
- `tenant, campus, department, program, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `No` | `No` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `program` | `ForeignKey` | `No` | `No` | `No` | `programs` (`tenants.Program`) | Owning or effective academic program scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `year_level` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `tenant_term_grading_periods`

- **Model:** `academics.TenantTermGradingPeriod`
- **Purpose:** Canonical grading period catalog per tenant and term, separate from template period codes.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `term` -> `terms` (`academics.Term`)

**Unique / Structural Notes**
- `tenant, term, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `sequence_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |

### `terms`

- **Model:** `academics.Term`
- **Purpose:** Academic term or semester within an academic year.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `academic_year` -> `academic_years` (`academics.AcademicYear`)

**Unique / Structural Notes**
- `tenant, academic_year, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `academic_year` | `ForeignKey` | `No` | `No` | `No` | `academic_years` (`academics.AcademicYear`) | Academic year for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `term_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `sequence_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `start_date` | `DateField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `end_date` | `DateField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |

## `students`

### `students`

- **Model:** `students.Student`
- **Purpose:** Student master record scoped by tenant/campus/department.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)
- `program` -> `programs` (`tenants.Program`)

**Unique / Structural Notes**
- `tenant, campus, student_no`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `No` | `No` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `program` | `ForeignKey` | `Yes` | `Yes` | `No` | `programs` (`tenants.Program`) | Owning or effective academic program scope for the record. |
| `student_no` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `last_name` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `first_name` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `middle_name` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `official_email` | `EmailField` | `Yes` | `Yes` | `No` | - | Trusted official student email used for Student Portal account provisioning. |
| `official_email_verified_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Timestamp confirming the official email was verified for provisioning use. |
| `sex` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `year_level` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |

## `enrollment`

### `enrollments`

- **Model:** `enrollment.Enrollment`
- **Purpose:** Enrollment record linking a student to a course offering.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `academic_year` -> `academic_years` (`academics.AcademicYear`)
- `term` -> `terms` (`academics.Term`)
- `student` -> `students` (`students.Student`)
- `course_offering` -> `course_offerings` (`academics.CourseOffering`)
- `encoded_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `course_offering, student`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `academic_year` | `ForeignKey` | `No` | `No` | `No` | `academic_years` (`academics.AcademicYear`) | Academic year for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `course_offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Foreign-key reference to `academics.CourseOffering`. |
| `enrollment_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `encoded_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `encoded_via_portal` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |

## `imports`

### `import_batch_rows`

- **Model:** `imports.ImportBatchRow`
- **Purpose:** Row-level result for a bulk import batch.

**Relationships**
- `batch` -> `import_batches` (`imports.ImportBatch`)

**Unique / Structural Notes**
- `batch, row_number`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `batch` | `ForeignKey` | `No` | `No` | `No` | `import_batches` (`imports.ImportBatch`) | Foreign-key reference to `imports.ImportBatch`. |
| `row_number` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `row_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `raw_data_json` | `JSONField` | `No` | `No` | `No` | - | Flexible JSON payload used for variable structured data. |
| `normalized_data_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `errors_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `imported_entity_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `imported_entity_id` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `import_batches`

- **Model:** `imports.ImportBatch`
- **Purpose:** Bulk import batch header.

**Relationships**
- `uploaded_by_user` -> `users` (`accounts.User`)
- `confirmed_by_user` -> `users` (`accounts.User`)
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `import_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `uploaded_by_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `confirmed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `source_file` | `FileField` | `Yes` | `Yes` | `No` | - | Application field used by TeacherMate+. |
| `original_filename` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `expected_headers_json` | `JSONField` | `No` | `No` | `No` | - | Flexible JSON payload used for variable structured data. |
| `actual_headers_json` | `JSONField` | `No` | `No` | `No` | - | Flexible JSON payload used for variable structured data. |
| `total_rows` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `valid_rows` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `invalid_rows` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `imported_rows` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `error_summary_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `confirmed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `metadata_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload for extra metadata. |

## `grading`

### `correction_approval_routes`

- **Model:** `grading.CorrectionApprovalRouteRule`
- **Purpose:** Department-sensitive approval-route rule for grade correction requests.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `faculty_department` -> `departments` (`tenants.Department`)
- `step1_role` -> `roles` (`rbac.Role`)
- `final_role` -> `roles` (`rbac.Role`)

**Unique / Structural Notes**
- `tenant, faculty_department`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `faculty_department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Foreign-key reference to `tenants.Department`. |
| `route_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `step1_role` | `ForeignKey` | `No` | `No` | `No` | `roles` (`rbac.Role`) | Foreign-key reference to `rbac.Role`. |
| `step1_requires_same_department` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `final_role` | `ForeignKey` | `Yes` | `Yes` | `No` | `roles` (`rbac.Role`) | Foreign-key reference to `rbac.Role`. |
| `final_requires_same_department` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `notes` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `course_base_value_overrides`

- **Model:** `grading.CourseBaseValueOverride`
- **Purpose:** Course-specific base value override for grade computation.

**Relationships**
- `course` -> `courses` (`academics.Course`)
- `effective_from_term` -> `terms` (`academics.Term`)

**Unique / Structural Notes**
- `course, effective_from_term`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `course` | `ForeignKey` | `No` | `No` | `No` | `courses` (`academics.Course`) | Foreign-key reference to `academics.Course`. |
| `base_value` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `effective_from_term` | `ForeignKey` | `Yes` | `Yes` | `No` | `terms` (`academics.Term`) | Foreign-key reference to `academics.Term`. |

### `course_template_assignments`

- **Model:** `grading.CourseTemplateAssignment`
- **Purpose:** Assignment of a grading template to a course, optionally term-scoped.

**Relationships**
- `course` -> `courses` (`academics.Course`)
- `grading_template` -> `grading_templates` (`grading.GradingTemplate`)
- `effective_from_term` -> `terms` (`academics.Term`)

**Unique / Structural Notes**
- `course, grading_template, effective_from_term`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `course` | `ForeignKey` | `No` | `No` | `No` | `courses` (`academics.Course`) | Foreign-key reference to `academics.Course`. |
| `grading_template` | `ForeignKey` | `No` | `No` | `No` | `grading_templates` (`grading.GradingTemplate`) | Foreign-key reference to `grading.GradingTemplate`. |
| `effective_from_term` | `ForeignKey` | `Yes` | `Yes` | `No` | `terms` (`academics.Term`) | Foreign-key reference to `academics.Term`. |

### `faculty_final_clearance_reports`

- **Model:** `grading.FacultyFinalClearanceReport`
- **Purpose:** TeacherMate+ application table.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `academic_year` -> `academic_years` (`academics.AcademicYear`)
- `term` -> `terms` (`academics.Term`)
- `faculty_user` -> `users` (`accounts.User`)
- `generated_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `academic_year` | `ForeignKey` | `No` | `No` | `No` | `academic_years` (`academics.AcademicYear`) | Academic year for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `faculty_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Faculty user assigned to the record. |
| `generated_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `report_uuid` | `UUIDField` | `No` | `No` | `No` | - | Application field used by TeacherMate+. |
| `reference_no` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `verification_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `clearance_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `total_assigned_courses` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `complete_courses` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `incomplete_courses` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `snapshot_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |

### `grade_activities`

- **Model:** `grading.GradeActivity`
- **Purpose:** Faculty-created graded activity under a class offering and template period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `template_component` -> `grading_template_components` (`grading.GradingTemplateComponent`)
- `template_subcomponent` -> `grading_template_subcomponents` (`grading.GradingTemplateSubcomponent`)
- `template_detail` -> `grading_template_details` (`grading.GradingTemplateDetail`)
- `created_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `template_component` | `ForeignKey` | `No` | `No` | `No` | `grading_template_components` (`grading.GradingTemplateComponent`) | Foreign-key reference to `grading.GradingTemplateComponent`. |
| `template_subcomponent` | `ForeignKey` | `Yes` | `Yes` | `No` | `grading_template_subcomponents` (`grading.GradingTemplateSubcomponent`) | Foreign-key reference to `grading.GradingTemplateSubcomponent`. |
| `template_detail` | `ForeignKey` | `Yes` | `Yes` | `No` | `grading_template_details` (`grading.GradingTemplateDetail`) | Foreign-key reference to `grading.GradingTemplateDetail`. |
| `title` | `CharField` | `No` | `No` | `No` | - | Human-readable title. |
| `total_score` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `activity_date` | `DateField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `created_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

### `grade_correction_approval_steps`

- **Model:** `grading.GradeCorrectionApprovalStep`
- **Purpose:** Approval chain step for a correction request.

**Relationships**
- `correction_request` -> `grade_correction_requests` (`grading.GradeCorrectionRequest`)
- `approver_role` -> `roles` (`rbac.Role`)
- `reviewed_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `correction_request, step_order`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `correction_request` | `ForeignKey` | `No` | `No` | `No` | `grade_correction_requests` (`grading.GradeCorrectionRequest`) | Foreign-key reference to `grading.GradeCorrectionRequest`. |
| `step_order` | `PositiveSmallIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `approver_role` | `ForeignKey` | `No` | `No` | `No` | `roles` (`rbac.Role`) | Foreign-key reference to `rbac.Role`. |
| `approver_label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `requires_same_department` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `reviewed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who reviewed or decided the request. |
| `reviewed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `review_remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `grade_correction_attachments`

- **Model:** `grading.GradeCorrectionAttachment`
- **Purpose:** Supporting file attached to a correction request.

**Relationships**
- `correction_request` -> `grade_correction_requests` (`grading.GradeCorrectionRequest`)
- `uploaded_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `correction_request` | `ForeignKey` | `No` | `No` | `No` | `grade_correction_requests` (`grading.GradeCorrectionRequest`) | Foreign-key reference to `grading.GradeCorrectionRequest`. |
| `file` | `FileField` | `No` | `No` | `No` | - | Application field used by TeacherMate+. |
| `original_filename` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `content_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `file_size_bytes` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `uploaded_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

### `grade_correction_request_items`

- **Model:** `grading.GradeCorrectionRequestItem`
- **Purpose:** Specific correction item inside a correction request.

**Relationships**
- `correction_request` -> `grade_correction_requests` (`grading.GradeCorrectionRequest`)
- `student` -> `students` (`students.Student`)
- `grade_activity` -> `grade_activities` (`grading.GradeActivity`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `correction_request` | `ForeignKey` | `No` | `No` | `No` | `grade_correction_requests` (`grading.GradeCorrectionRequest`) | Foreign-key reference to `grading.GradeCorrectionRequest`. |
| `requested_action` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `student` | `ForeignKey` | `Yes` | `Yes` | `No` | `students` (`students.Student`) | Related student record. |
| `grade_activity` | `ForeignKey` | `Yes` | `Yes` | `No` | `grade_activities` (`grading.GradeActivity`) | Foreign-key reference to `grading.GradeActivity`. |
| `old_value` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `new_value` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `grade_correction_requests`

- **Model:** `grading.GradeCorrectionRequest`
- **Purpose:** Grade correction petition header.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `requested_by_user` -> `users` (`accounts.User`)
- `initiated_by_user` -> `users` (`accounts.User`)
- `faculty_department` -> `departments` (`tenants.Department`)
- `approval_route` -> `correction_approval_routes` (`grading.CorrectionApprovalRouteRule`)
- `reviewed_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `requested_by_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | User who created the request. |
| `initiated_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `request_source` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `on_behalf_reason` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `faculty_department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Foreign-key reference to `tenants.Department`. |
| `approval_route` | `ForeignKey` | `Yes` | `Yes` | `No` | `correction_approval_routes` (`grading.CorrectionApprovalRouteRule`) | Foreign-key reference to `grading.CorrectionApprovalRouteRule`. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `justification` | `TextField` | `No` | `No` | `No` | - | Reason supplied to support the request or decision. |
| `reviewed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who reviewed or decided the request. |
| `reviewed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `review_remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `grade_correction_unlock_windows`

- **Model:** `grading.GradeCorrectionUnlockWindow`
- **Purpose:** Governed manual unlock window for approved correction follow-up.

**Relationships**
- `correction_request` -> `grade_correction_requests` (`grading.GradeCorrectionRequest`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `correction_request` | `OneToOneField` | `No` | `No` | `No` | `grade_correction_requests` (`grading.GradeCorrectionRequest`) | Foreign-key reference to `grading.GradeCorrectionRequest`. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `start_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `end_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `is_consumed` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `closed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |

### `grade_submission_reopen_requests`

- **Model:** `grading.GradeSubmissionReopenRequest`
- **Purpose:** Request to reopen a submitted gradebook period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `submission` -> `grade_submissions` (`grading.GradeSubmission`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `requested_by_user` -> `users` (`accounts.User`)
- `reviewed_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `submission` | `ForeignKey` | `No` | `No` | `No` | `grade_submissions` (`grading.GradeSubmission`) | Foreign-key reference to `grading.GradeSubmission`. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `requested_by_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | User who created the request. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `justification` | `TextField` | `No` | `No` | `No` | - | Reason supplied to support the request or decision. |
| `reviewed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who reviewed or decided the request. |
| `reviewed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `review_remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `grade_submissions`

- **Model:** `grading.GradeSubmission`
- **Purpose:** Faculty submission snapshot for a class-period gradebook.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `submitted_by_user` -> `users` (`accounts.User`)
- `reopened_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `offering, template_period`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `submitted_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who submitted the record. |
| `submitted_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `reopened_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `reopened_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `submission_snapshot_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `template_snapshot_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `remarks` | `CharField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `grading_period_locks`

- **Model:** `grading.GradingPeriodLock`
- **Purpose:** Admin period lock/deadline rule for submission governance.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `academic_year` -> `academic_years` (`academics.AcademicYear`)
- `term` -> `terms` (`academics.Term`)
- `course_offering` -> `course_offerings` (`academics.CourseOffering`)
- `locked_by_user` -> `users` (`accounts.User`)
- `reopened_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `tenant, campus, academic_year, term, period_code, scope_type, course_offering`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `academic_year` | `ForeignKey` | `No` | `No` | `No` | `academic_years` (`academics.AcademicYear`) | Academic year for the record. |
| `term` | `ForeignKey` | `No` | `No` | `No` | `terms` (`academics.Term`) | Academic term for the record. |
| `period_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `scope_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `course_offering` | `ForeignKey` | `Yes` | `Yes` | `No` | `course_offerings` (`academics.CourseOffering`) | Foreign-key reference to `academics.CourseOffering`. |
| `is_locked` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `deadline_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `locked_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `locked_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `reopened_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `reopened_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `remarks` | `CharField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `grading_template_approval_steps`

- **Model:** `grading.GradingTemplateApprovalStep`
- **Purpose:** Step-by-step record for a template approval workflow.

**Relationships**
- `workflow` -> `grading_template_approval_workflows` (`grading.GradingTemplateApprovalWorkflow`)
- `acted_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `workflow, step_no`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `workflow` | `ForeignKey` | `No` | `No` | `No` | `grading_template_approval_workflows` (`grading.GradingTemplateApprovalWorkflow`) | Foreign-key reference to `grading.GradingTemplateApprovalWorkflow`. |
| `step_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `step_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `step_label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `role_codes_json` | `JSONField` | `No` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `acted_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `acted_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `grading_template_approval_workflows`

- **Model:** `grading.GradingTemplateApprovalWorkflow`
- **Purpose:** Workflow header for template sequential approval.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `template` -> `grading_templates` (`grading.GradingTemplate`)
- `submitted_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `template` | `ForeignKey` | `No` | `No` | `No` | `grading_templates` (`grading.GradingTemplate`) | Foreign-key reference to `grading.GradingTemplate`. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `submitted_by_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | User who submitted the record. |
| `submitted_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `current_step_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `completed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |

### `grading_template_components`

- **Model:** `grading.GradingTemplateComponent`
- **Purpose:** Top-level weighted grading component within a template period.

**Relationships**
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)

**Unique / Structural Notes**
- `template_period, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `weight_percentage` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `sort_order` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `score_input_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `is_exam_component` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `grading_template_details`

- **Model:** `grading.GradingTemplateDetail`
- **Purpose:** Lowest grading detail or activity grouping node under a subcomponent/component.

**Relationships**
- `template_subcomponent` -> `grading_template_subcomponents` (`grading.GradingTemplateSubcomponent`)

**Unique / Structural Notes**
- `template_subcomponent, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `template_subcomponent` | `ForeignKey` | `No` | `No` | `No` | `grading_template_subcomponents` (`grading.GradingTemplateSubcomponent`) | Foreign-key reference to `grading.GradingTemplateSubcomponent`. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `weight_percentage` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `sort_order` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `score_input_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `admin_locked` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `grading_template_periods`

- **Model:** `grading.GradingTemplatePeriod`
- **Purpose:** Template period, such as prelim or midterm, under a grading template.

**Relationships**
- `template` -> `grading_templates` (`grading.GradingTemplate`)

**Unique / Structural Notes**
- `template, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `template` | `ForeignKey` | `No` | `No` | `No` | `grading_templates` (`grading.GradingTemplate`) | Foreign-key reference to `grading.GradingTemplate`. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `sequence_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `weight_percentage` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |

### `grading_template_subcomponents`

- **Model:** `grading.GradingTemplateSubcomponent`
- **Purpose:** Nested weighted subcomponent under a component.

**Relationships**
- `template_component` -> `grading_template_components` (`grading.GradingTemplateComponent`)

**Unique / Structural Notes**
- `template_component, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `template_component` | `ForeignKey` | `No` | `No` | `No` | `grading_template_components` (`grading.GradingTemplateComponent`) | Foreign-key reference to `grading.GradingTemplateComponent`. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `weight_percentage` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `sort_order` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `score_input_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `is_attendance_component` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `admin_locked` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `grading_templates`

- **Model:** `grading.GradingTemplate`
- **Purpose:** Top-level grading template definition.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `approval_requested_by` -> `users` (`accounts.User`)
- `approval_reviewed_by` -> `users` (`accounts.User`)
- `published_by` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `tenant, code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `code` | `CharField` | `No` | `No` | `No` | - | Short code used as an operational identifier. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `description` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `default_base_value` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `passing_grade_threshold` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `approval_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `approval_requested_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `approval_requested_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `approval_reviewed_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `approval_reviewed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `approval_remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `is_published` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `published_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `published_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

### `student_activity_scores`

- **Model:** `grading.StudentActivityScore`
- **Purpose:** Student raw score or computed score entry for a grade activity.

**Relationships**
- `activity` -> `grade_activities` (`grading.GradeActivity`)
- `student` -> `students` (`students.Student`)
- `encoded_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `activity, student`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `activity` | `ForeignKey` | `No` | `No` | `No` | `grade_activities` (`grading.GradeActivity`) | Foreign-key reference to `grading.GradeActivity`. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `raw_score` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `computed_score` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `encoded_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `remarks` | `CharField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |
| `is_excused` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `student_final_grades`

- **Model:** `grading.StudentFinalGrade`
- **Purpose:** Computed final grade summary per student and offering.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `student` -> `students` (`students.Student`)
- `computed_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `offering, student`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `final_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `remarks` | `CharField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |
| `computed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `computed_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `is_submitted` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `student_period_grades`

- **Model:** `grading.StudentPeriodGrade`
- **Purpose:** Computed class-standing, exam, and period grade summary per student and period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `student` -> `students` (`students.Student`)
- `computed_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `offering, template_period, student`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `class_standing_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `exam_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `period_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `computed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `computed_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `is_finalized` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

### `template_hotfix_requests`

- **Model:** `grading.TemplateHotfixRequest`
- **Purpose:** Hotfix request for published grading templates.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `template` -> `grading_templates` (`grading.GradingTemplate`)
- `requested_by_user` -> `users` (`accounts.User`)
- `reviewed_by_user` -> `users` (`accounts.User`)
- `applied_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `template` | `ForeignKey` | `No` | `No` | `No` | `grading_templates` (`grading.GradingTemplate`) | Foreign-key reference to `grading.GradingTemplate`. |
| `apply_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `justification` | `TextField` | `No` | `No` | `No` | - | Reason supplied to support the request or decision. |
| `selected_offering_ids_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `requested_by_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | User who created the request. |
| `reviewed_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | User who reviewed or decided the request. |
| `reviewed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `review_remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `applied_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `applied_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `affected_offering_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `recomputed_offering_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `impact_snapshot_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |

### `template_hotfix_workflow_steps`

- **Model:** `grading.TemplateHotfixWorkflowStep`
- **Purpose:** Sequential workflow step for template hotfix review/apply.

**Relationships**
- `hotfix_request` -> `template_hotfix_requests` (`grading.TemplateHotfixRequest`)
- `acted_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `hotfix_request, step_no`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `hotfix_request` | `ForeignKey` | `No` | `No` | `No` | `template_hotfix_requests` (`grading.TemplateHotfixRequest`) | Foreign-key reference to `grading.TemplateHotfixRequest`. |
| `step_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `step_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `step_label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `role_codes_json` | `JSONField` | `No` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `acted_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `acted_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `remarks` | `TextField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `tenant_grading_profiles`

- **Model:** `grading.TenantGradingProfile`
- **Purpose:** Tenant/campus/department grading defaults such as passing threshold.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)
- `program` -> `programs` (`tenants.Program`)
- `course` -> `courses` (`academics.Course`)
- `grading_template` -> `grading_templates` (`grading.GradingTemplate`)
- `effective_from_term` -> `terms` (`academics.Term`)

**Unique / Structural Notes**
- `tenant, profile_code`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `program` | `ForeignKey` | `Yes` | `Yes` | `No` | `programs` (`tenants.Program`) | Owning or effective academic program scope for the record. |
| `course` | `ForeignKey` | `Yes` | `Yes` | `No` | `courses` (`academics.Course`) | Foreign-key reference to `academics.Course`. |
| `course_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `term_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `profile_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `profile_name` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `grading_template` | `ForeignKey` | `No` | `No` | `No` | `grading_templates` (`grading.GradingTemplate`) | Foreign-key reference to `grading.GradingTemplate`. |
| `default_base_value` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `passing_grade_threshold` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `final_grade_formula_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `final_grade_formula_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `priority` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `effective_from_term` | `ForeignKey` | `Yes` | `Yes` | `No` | `terms` (`academics.Term`) | Foreign-key reference to `academics.Term`. |
| `is_default` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |

## `attendance`

### `attendance_records`

- **Model:** `attendance.AttendanceRecord`
- **Purpose:** Attendance entry for a student in one attendance session.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `session` -> `attendance_sessions` (`attendance.AttendanceSession`)
- `student` -> `students` (`students.Student`)
- `recorded_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `session, student`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `Yes` | `Yes` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `session` | `ForeignKey` | `No` | `No` | `No` | `attendance_sessions` (`attendance.AttendanceSession`) | Foreign-key reference to `attendance.AttendanceSession`. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `status_code` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `recorded_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `remarks` | `CharField` | `Yes` | `Yes` | `No` | - | Free-text remarks or notes for operational context. |

### `attendance_sessions`

- **Model:** `attendance.AttendanceSession`
- **Purpose:** Attendance session under a class offering and period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `created_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `offering, template_period, session_date`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `session_date` | `DateField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `title` | `CharField` | `Yes` | `Yes` | `No` | - | Human-readable title. |
| `created_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

## `notifications`

### `faculty_memos`

- **Model:** `notifications.FacultyMemo`
- **Purpose:** Private faculty memo/note linked to a class or student.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `faculty_user` -> `users` (`accounts.User`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `student` -> `students` (`students.Student`)
- `created_by` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `faculty_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Faculty user assigned to the record. |
| `offering` | `ForeignKey` | `Yes` | `Yes` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `student` | `ForeignKey` | `Yes` | `Yes` | `No` | `students` (`students.Student`) | Related student record. |
| `memo_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `title` | `CharField` | `No` | `No` | `No` | - | Human-readable title. |
| `body` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `is_pinned` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `created_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |

### `faculty_reminder_email_queue`

- **Model:** `notifications.FacultyReminderEmailQueue`
- **Purpose:** Queued outbound email for a faculty reminder.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `reminder` -> `faculty_reminders` (`notifications.FacultyReminder`)
- `recipient_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `dedupe_key`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `reminder` | `ForeignKey` | `No` | `No` | `No` | `faculty_reminders` (`notifications.FacultyReminder`) | Foreign-key reference to `notifications.FacultyReminder`. |
| `recipient_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `recipient_email` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `subject` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `text_body` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `html_body` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `scheduled_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `sent_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `attempt_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `last_attempt_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `error_message` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `dedupe_key` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `priority` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `metadata_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload for extra metadata. |

### `faculty_reminders`

- **Model:** `notifications.FacultyReminder`
- **Purpose:** Faculty reminder center item for deadlines, activities, or workflow follow-up.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `faculty_user` -> `users` (`accounts.User`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `grade_activity` -> `grade_activities` (`grading.GradeActivity`)
- `created_by` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `faculty_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Faculty user assigned to the record. |
| `offering` | `ForeignKey` | `Yes` | `Yes` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `grade_activity` | `ForeignKey` | `Yes` | `Yes` | `No` | `grade_activities` (`grading.GradeActivity`) | Foreign-key reference to `grading.GradeActivity`. |
| `reminder_type` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `title` | `CharField` | `No` | `No` | `No` | - | Human-readable title. |
| `period_label` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `notes` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `remind_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `due_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `snoozed_until` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `completed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `cancelled_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `send_email` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `email_last_queued_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `email_last_sent_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `email_attempt_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `created_by` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `is_active` | `BooleanField` | `No` | `No` | `No` | - | Active/inactive flag used for soft operational control. |

### `notification_queue`

- **Model:** `notifications.NotificationQueue`
- **Purpose:** Generic queued notification record.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `recipient_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- `recipient_user, channel, reference_type, reference_id, scheduled_at`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `recipient_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `channel` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `subject` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `body` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `scheduled_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `sent_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `reference_type` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `reference_id` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `metadata_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload for extra metadata. |

### `submission_non_compliance_notices`

- **Model:** `notifications.SubmissionNonComplianceNotice`
- **Purpose:** TeacherMate+ application table.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `department` -> `departments` (`tenants.Department`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `faculty_user` -> `users` (`accounts.User`)
- `submission` -> `grade_submissions` (`grading.GradeSubmission`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `department` | `ForeignKey` | `Yes` | `Yes` | `No` | `departments` (`tenants.Department`) | Owning or effective department scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `faculty_user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Faculty user assigned to the record. |
| `submission` | `ForeignKey` | `Yes` | `Yes` | `No` | `grade_submissions` (`grading.GradeSubmission`) | Foreign-key reference to `grading.GradeSubmission`. |
| `notice_level` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `sequence_no` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `title` | `CharField` | `No` | `No` | `No` | - | Human-readable title. |
| `message` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `deadline_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `issued_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `resolved_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `resolution_note` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `recipient_emails_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `recipient_roles_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `email_status` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `email_sent_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `email_attempt_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `email_error_message` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

## `predictions`

### `prediction_dirty_queue`

- **Model:** `predictions.PredictionDirtyQueue`
- **Purpose:** Queue of impacted records needing prediction recomputation.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `student` -> `students` (`students.Student`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `Yes` | `Yes` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `student` | `ForeignKey` | `Yes` | `Yes` | `No` | `students` (`students.Student`) | Related student record. |
| `reason` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `status` | `CharField` | `No` | `No` | `No` | - | Workflow or operational status code. |
| `processed_at` | `DateTimeField` | `Yes` | `Yes` | `No` | - | Date/time value used by the workflow or record. |
| `error_message` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |

### `prediction_setting_snapshots`

- **Model:** `predictions.PredictionSettingSnapshot`
- **Purpose:** Snapshot of prediction assumptions used to compute unofficial projections.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `generated_by_user` -> `users` (`accounts.User`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `Yes` | `Yes` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `assumption_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `show_best_case` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `show_worst_case` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `show_target_needed` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `generated_by_user` | `ForeignKey` | `Yes` | `Yes` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |

### `prediction_snapshots`

- **Model:** `predictions.PredictionSnapshot`
- **Purpose:** Per-student unofficial prediction snapshot for one offering and period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `student` -> `students` (`students.Student`)
- `setting_snapshot` -> `prediction_setting_snapshots` (`predictions.PredictionSettingSnapshot`)

**Unique / Structural Notes**
- `offering, template_period, student, setting_snapshot`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `student` | `ForeignKey` | `No` | `No` | `No` | `students` (`students.Student`) | Related student record. |
| `setting_snapshot` | `ForeignKey` | `No` | `No` | `No` | `prediction_setting_snapshots` (`predictions.PredictionSettingSnapshot`) | Foreign-key reference to `predictions.PredictionSettingSnapshot`. |
| `current_projected_period_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `best_case_period_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `worst_case_period_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `current_projected_final_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `best_case_final_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `worst_case_final_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `target_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `target_needed_percent` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `at_risk_flag` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `encoded_item_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `expected_item_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `remaining_item_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `coverage_percent` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `source_version` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `is_stale` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `computed_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |

### `prediction_summary_snapshots`

- **Model:** `predictions.PredictionSummarySnapshot`
- **Purpose:** Aggregated class-period prediction summary.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `setting_snapshot` -> `prediction_setting_snapshots` (`predictions.PredictionSettingSnapshot`)

**Unique / Structural Notes**
- `offering, template_period, setting_snapshot`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `setting_snapshot` | `ForeignKey` | `No` | `No` | `No` | `prediction_setting_snapshots` (`predictions.PredictionSettingSnapshot`) | Foreign-key reference to `predictions.PredictionSettingSnapshot`. |
| `student_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `students_with_projection` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `at_risk_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `passing_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `failing_count` | `PositiveIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `avg_projected_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `avg_best_case_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `avg_worst_case_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `avg_coverage_percent` | `DecimalField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `source_version` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `is_stale` | `BooleanField` | `No` | `No` | `No` | - | Boolean flag used by the workflow or record. |
| `computed_at` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |

### `prediction_view_logs`

- **Model:** `predictions.PredictionViewLog`
- **Purpose:** Audit log for prediction page access.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `viewer` -> `users` (`accounts.User`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `student` -> `students` (`students.Student`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `viewer` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Foreign-key reference to `accounts.User`. |
| `viewer_role_code` | `CharField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `student` | `ForeignKey` | `Yes` | `Yes` | `No` | `students` (`students.Student`) | Related student record. |
| `view_mode` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |

### `prediction_what_if_drafts`

- **Model:** `predictions.PredictionWhatIfDraft`
- **Purpose:** Saved what-if simulation draft for a user/class/period.

**Relationships**
- `tenant` -> `tenants` (`tenants.Tenant`)
- `campus` -> `campuses` (`tenants.Campus`)
- `user` -> `users` (`accounts.User`)
- `offering` -> `course_offerings` (`academics.CourseOffering`)
- `template_period` -> `grading_template_periods` (`grading.GradingTemplatePeriod`)
- `student` -> `students` (`students.Student`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `BigAutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `created_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was created. |
| `updated_at` | `DateTimeField` | `No` | `Yes` | `No` | - | Timestamp when the row was last updated. |
| `tenant` | `ForeignKey` | `No` | `No` | `No` | `tenants` (`tenants.Tenant`) | Owning tenant scope for the record. |
| `campus` | `ForeignKey` | `No` | `No` | `No` | `campuses` (`tenants.Campus`) | Owning or effective campus scope for the record. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `offering` | `ForeignKey` | `No` | `No` | `No` | `course_offerings` (`academics.CourseOffering`) | Related course offering/class record. |
| `template_period` | `ForeignKey` | `No` | `No` | `No` | `grading_template_periods` (`grading.GradingTemplatePeriod`) | Related grading-template period record. |
| `student` | `ForeignKey` | `Yes` | `Yes` | `No` | `students` (`students.Student`) | Related student record. |
| `scenario_name` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `assumed_remaining_score` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `target_grade` | `DecimalField` | `Yes` | `Yes` | `No` | - | Numeric value used by the workflow or computation. |
| `assumptions_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |
| `results_json` | `JSONField` | `Yes` | `Yes` | `No` | - | Flexible JSON payload used for variable structured data. |

## `admin`

### `django_admin_log`

- **Model:** `admin.LogEntry`
- **Purpose:** Django admin action log table.

**Relationships**
- `user` -> `users` (`accounts.User`)
- `content_type` -> `django_content_type` (`contenttypes.ContentType`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `AutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `action_time` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
| `user` | `ForeignKey` | `No` | `No` | `No` | `users` (`accounts.User`) | Related user account. |
| `content_type` | `ForeignKey` | `Yes` | `Yes` | `No` | `django_content_type` (`contenttypes.ContentType`) | Foreign-key reference to `contenttypes.ContentType`. |
| `object_id` | `TextField` | `Yes` | `Yes` | `No` | - | Text value used by the workflow or record. |
| `object_repr` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `action_flag` | `PositiveSmallIntegerField` | `No` | `No` | `No` | - | Numeric value used by the workflow or computation. |
| `change_message` | `TextField` | `No` | `Yes` | `No` | - | Text value used by the workflow or record. |

## `auth`

### `auth_group`

- **Model:** `auth.Group`
- **Purpose:** Django built-in auth group table.

**Relationships**
- `permissions` -> many-to-many with `auth_permission` (`auth.Permission`)

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `AutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `permissions` | `ManyToManyField` | `-` | `-` | `No` | ``auth_permission` (`auth.Permission`)` | Many-to-many relationship managed through an intermediate table. |

### `auth_permission`

- **Model:** `auth.Permission`
- **Purpose:** Django built-in permission table.

**Relationships**
- `content_type` -> `django_content_type` (`contenttypes.ContentType`)

**Unique / Structural Notes**
- `content_type, codename`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `AutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `name` | `CharField` | `No` | `No` | `No` | - | Human-readable name or label. |
| `content_type` | `ForeignKey` | `No` | `No` | `No` | `django_content_type` (`contenttypes.ContentType`) | Foreign-key reference to `contenttypes.ContentType`. |
| `codename` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |

## `contenttypes`

### `django_content_type`

- **Model:** `contenttypes.ContentType`
- **Purpose:** Django content-type registry table.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- `app_label, model`

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `id` | `AutoField` | `No` | `Yes` | `Yes` | - | Primary key for the table. |
| `app_label` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `model` | `CharField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |

## `sessions`

### `django_session`

- **Model:** `sessions.Session`
- **Purpose:** Django session store.

**Relationships**
- No outgoing foreign-key or many-to-many relationships.

**Unique / Structural Notes**
- No explicit unique constraint metadata beyond primary keys and field-level `unique=True` flags.

| Field | Django Type | Null | Blank | PK | Relationship | Explanation |
|---|---|---:|---:|---:|---|---|
| `session_key` | `CharField` | `No` | `No` | `Yes` | - | Text value used by the workflow or record. |
| `session_data` | `TextField` | `No` | `No` | `No` | - | Text value used by the workflow or record. |
| `expire_date` | `DateTimeField` | `No` | `No` | `No` | - | Date/time value used by the workflow or record. |
