# EduGradesPro Production Data Promotion Guide

This guide explains how to safely promote your existing EduGradesPro data from your current local environment into staging and production.

Use this if your current database already contains real records such as:

- tenants and campuses
- users and faculty accounts
- courses and sections
- course offerings
- faculty assignments
- grading templates
- approved governance settings

## 1. Recommended Strategy

Do **not** blindly copy the entire development database into production.

Recommended strategy:

1. freeze and review the current local data
2. export an approved **data bundle**
3. load the bundle into **staging first**
4. verify staging
5. load the same approved bundle into **production**

## 2. Bundle Types

### Setup bundle

Recommended for first go-live.

Includes:

- tenant/campus/department/program structure
- users
- RBAC roles/permissions/user-role assignments
- portal menus
- academic year/term
- active grading period setup
- courses/sections/offerings
- faculty assignments
- students and enrollments
- grading templates and template assignments
- grading governance records such as correction routes and period locks

Use this if you want to launch production with clean official master/setup data.

### Operational bundle

Use only if you also need to carry active operational records such as:

- grade activities
- encoded scores
- period/final grades
- submissions
- correction requests
- attendance
- faculty reminders/memos

This is broader and riskier.

Use it only when you truly need to carry over in-progress grading history.

## 3. What Is Intentionally Excluded

The promotion scripts intentionally avoid treating these as first-choice go-live data:

- audit logs
- import-batch history
- portal login lockout state
- prediction snapshots and dirty queues
- generic notification queues
- Django sessions

Those are environment/runtime records, not clean setup records.

## 4. Step 1. Freeze and Review Local Data

Before exporting anything:

1. stop making new structural edits in the local DB
2. remove obvious junk/test records if needed
3. confirm which tenant/campus data should really go live
4. confirm admin accounts and passwords are ready
5. confirm grading templates and assignments are final enough to deploy

## 5. Step 2. Back Up the Local Database

If your local DB is SQLite:

```powershell
Copy-Item .\db.sqlite3 ".\\db.sqlite3.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
```

## 6. Step 3. Export the Data Bundle From Local

From `D:\edugradespro`, use the provided helper script.

### Export setup bundle

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\scripts\export_data_bundle.ps1 -Mode setup -OutputPath .\setup_bundle.json
```

### Export operational bundle

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\scripts\export_data_bundle.ps1 -Mode operational -OutputPath .\operational_bundle.json
```

### Optional natural-key flags

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\scripts\export_data_bundle.ps1 -Mode setup -OutputPath .\setup_bundle.json -IncludeNaturalKeys
```

## 7. What the Export Script Uses

The helper script is:

- [export_data_bundle.ps1](/d:/edugradespro/ops/scripts/export_data_bundle.ps1)

It exports these model groups:

### Setup mode

- `tenants.Tenant`
- `tenants.Campus`
- `tenants.Department`
- `tenants.Program`
- `tenants.SystemSetting`
- `accounts.User`
- `rbac.Role`
- `rbac.Permission`
- `rbac.RolePermission`
- `rbac.UserRole`
- `rbac.UserPermission`
- `navigation.MenuGroup`
- `navigation.MenuItem`
- `navigation.MenuItemPermission`
- `academics.AcademicYear`
- `academics.Term`
- `academics.TenantTermGradingPeriod`
- `academics.ActiveGradingPeriodSetting`
- `academics.Course`
- `academics.Section`
- `academics.CourseOffering`
- `academics.FacultyAssignment`
- `students.Student`
- `enrollment.Enrollment`
- `grading.GradingTemplate`
- `grading.GradingTemplateApprovalWorkflow`
- `grading.GradingTemplateApprovalStep`
- `grading.GradingTemplatePeriod`
- `grading.GradingTemplateComponent`
- `grading.GradingTemplateSubcomponent`
- `grading.GradingTemplateDetail`
- `grading.CourseTemplateAssignment`
- `grading.CourseBaseValueOverride`
- `grading.TenantGradingProfile`
- `grading.CorrectionApprovalRouteRule`
- `grading.GradingPeriodLock`

### Operational mode adds

- `grading.TemplateHotfixRequest`
- `grading.TemplateHotfixWorkflowStep`
- `grading.GradeActivity`
- `grading.StudentActivityScore`
- `grading.StudentPeriodGrade`
- `grading.StudentFinalGrade`
- `grading.GradeSubmission`
- `grading.GradeSubmissionReopenRequest`
- `grading.GradeCorrectionRequest`
- `grading.GradeCorrectionApprovalStep`
- `grading.GradeCorrectionRequestItem`
- `grading.GradeCorrectionAttachment`
- `grading.GradeCorrectionUnlockWindow`
- `attendance.AttendanceSession`
- `attendance.AttendanceRecord`
- `notifications.FacultyReminder`
- `notifications.FacultyMemo`

## 8. Step 4. Review the Exported JSON

Before importing:

1. open the JSON file
2. confirm no test users or junk rows are included
3. confirm the tenant/campus data is correct
4. confirm the file is not accidentally committed to GitHub

Recommended:

- keep bundle files outside the repo if possible
- transfer them securely to staging and production

## 9. Step 5. Deploy Code to Staging First

Before importing the data, staging must already have:

1. code pulled from GitHub
2. Python environment installed
3. MariaDB staging DB ready
4. env file configured
5. migrations applied

Example:

```bash
cd /opt/edugradespro-staging
source /etc/edugradespro-staging/edugradespro.env
/opt/edugradespro-staging/.venv/bin/python manage.py migrate --noinput
/opt/edugradespro-staging/.venv/bin/python manage.py collectstatic --noinput
/opt/edugradespro-staging/.venv/bin/python manage.py check
```

## 10. Step 6. Copy the Bundle to Staging

Example from your local machine:

```powershell
scp .\setup_bundle.json youruser@your-server:/tmp/setup_bundle.json
```

## 11. Step 7. Import the Bundle Into Staging

Use the provided helper:

- [import_data_bundle.sh](/d:/edugradespro/ops/scripts/import_data_bundle.sh)

Example:

```bash
cd /opt/edugradespro-staging
bash ./ops/scripts/import_data_bundle.sh /etc/edugradespro-staging/edugradespro.env /tmp/setup_bundle.json
```

If you are loading into a fresh staging DB that may safely be cleared first:

```bash
cd /opt/edugradespro-staging
bash ./ops/scripts/import_data_bundle.sh /etc/edugradespro-staging/edugradespro.env /tmp/setup_bundle.json --flush
```

### Important

Use `--flush` only if:

- the target DB is intentionally disposable
- you already have a backup
- you really want to replace that environment's data

## 12. Step 8. Verify Staging

After import, test:

1. Admin login
2. Faculty login
3. users and scoped roles
4. courses and offerings
5. faculty assignments
6. grading templates
7. course template assignments
8. one faculty class flow
9. one admin governance page

If the bundle was operational:

10. grade activities
11. summary pages
12. submissions
13. corrections
14. attendance

## 13. Step 9. Promote the Same Bundle to Production

After staging is confirmed:

1. deploy the same approved code revision to production
2. run production migrations
3. copy the approved bundle to production
4. import the bundle
5. smoke-test production immediately

Example import:

```bash
cd /opt/edugradespro
bash ./ops/scripts/import_data_bundle.sh /etc/edugradespro/edugradespro.env /tmp/setup_bundle.json
```

## 14. First Go-Live Recommendation

For your first production launch, I recommend:

1. use the **setup bundle** first
2. avoid importing operational history unless you truly need it
3. confirm the platform boots and governance/settings are correct
4. only then consider carrying more historical operational records

That gives you a cleaner and safer launch.

## 15. If Production Is Already Live and You Need Schema Changes Later

Use Django migrations only.

### Local workflow

```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py check
```

Then test the affected workflows locally.

### Staging workflow

Deploy the updated code to staging, then:

```bash
cd /opt/edugradespro-staging
source /etc/edugradespro-staging/edugradespro.env
/opt/edugradespro-staging/.venv/bin/python manage.py migrate --noinput
/opt/edugradespro-staging/.venv/bin/python manage.py check
```

Then smoke-test the affected staging flows.

### Production workflow

Before migrating production:

1. back up the production DB
2. identify the exact code revision
3. schedule a maintenance window if the migration is risky

Then:

```bash
cd /opt/edugradespro
source /etc/edugradespro/edugradespro.env
/opt/edugradespro/.venv/bin/python manage.py migrate --noinput
/opt/edugradespro/.venv/bin/python manage.py check
```

Then smoke-test production immediately.

## 16. Safe Production Migration Sequence

Use this sequence every time:

1. make the model change locally
2. create migration locally
3. test locally
4. push to GitHub
5. deploy to staging
6. migrate staging
7. test staging
8. back up production DB
9. deploy to production
10. migrate production
11. smoke-test production

## 17. Recommended Backup Commands

### MariaDB backup

```bash
mysqldump -u edugradespro_user -p --databases edugradespro > edugradespro_backup.sql
```

### Staging MariaDB backup

```bash
mysqldump -u edugradespro_staging_user -p --databases edugradespro_staging > edugradespro_staging_backup.sql
```

## 18. Final Guidance

### For first deployment

- use a **setup bundle**
- deploy to **staging first**
- then promote to **production**

### For later schema changes

- always use **migrations**
- always test **staging before production**

### For later data promotions

- export a fresh reviewed bundle
- never guess directly in production
- always keep a DB backup before import or migration
