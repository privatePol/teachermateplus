# Production Testing-Phase Update Instructions

These instructions apply to the current EduGrade+ production server while it is **not yet live** and is being used only by limited testing users.

## Current Situation

- The production server already has EduGrade+ running.
- The production database was initially copied from development before the latest cleanup/reset work.
- Production already pulled code from the repository before the most recent updates.
- The local development `.env` is ignored by Git, so it will not be copied by `git pull`.
- Production is still in testing phase, so it is acceptable to replace the production database if the team wants production to match the latest cleaned development database.

## Important Rule

Do **not** run this command on production if you are copying an existing database:

```powershell
python manage.py seed_stage_0_1
```

That command creates baseline records such as `DEFAULT` tenant, `MAIN` campus, `COLLEGE` department, `BSIT` program, base menus, base permissions, and a default `superadmin` account. It is useful only for a fresh empty database bootstrap.

For the current testing-phase production server, use `migrate`, not the broad seed command.

## Before Updating Production

On production, make a backup first:

```powershell
copy db.sqlite3 db.sqlite3.backup_before_update
```

Also back up the production media folder if it contains uploaded files:

```text
media/
```

This may include logos, signatures, correction attachments, generated reports, and imported files.

## Production `.env`

Because `.env` is in `.gitignore`, it will not be updated by Git. Create or edit the production `.env` manually on the server.

Production should have its own values for:

```env
DEBUG=False
ALLOWED_HOSTS=your-domain-or-ip
CSRF_TRUSTED_ORIGINS=https://your-domain
EMAIL_HOST=...
EMAIL_PORT=...
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=...
```

If you copied the database and media from development and existing user signatures are encrypted, keep this value exactly the same as the source environment:

```env
SIGNATURE_ENCRYPTION_KEY=...
```

If this key changes, existing encrypted signatures may fail to open or print.

Do not commit `.env` to Git.

## Update Code on Production

On production:

```powershell
git pull
```

Then run:

```powershell
python manage.py migrate
python manage.py check
python manage.py collectstatic
```

Restart the application service after the update.

## Database Decision

Because production already has an older copied database, choose one of these paths.

### Option A: Keep The Current Production Testing Database

Use this if testers already created useful testing records on production and you want to preserve them.

Steps:

```powershell
git pull
python manage.py migrate
python manage.py check
python manage.py collectstatic
```

Then restart the application.

This keeps existing production testing data and applies new schema/code changes.

### Option B: Replace Production With The Latest Cleaned Development Database

Use this if production should match the current development database after the operational-data cleanup/reset.

Steps:

1. On development, confirm the database is current:

```powershell
python manage.py migrate
python manage.py check
```

2. Stop or pause the production app.

3. Back up the current production database and media.

4. Copy the development database to production:

```text
db.sqlite3
```

5. Copy the development media folder if needed.

6. On production, run:

```powershell
python manage.py migrate
python manage.py check
python manage.py collectstatic
```

7. Restart the application.

This replaces the older production testing data with the latest source database.

## If You Want To Clean Production Without Recopying The DB

If production should keep users, roles, permissions, menus, and global settings but remove actual operational data, use the Admin Portal:

```text
Admin Portal -> Tools -> Actual Data Reset
```

Only authorized users with `actual_data_reset.run` can use this page. Review the preview carefully before confirming.

Use this only during testing/pre-live setup.

## Post-Update Smoke Test

After updating production, verify:

- Admin login works.
- Faculty login works.
- Menus appear correctly.
- User create page filters Default Department by Default Campus.
- User role assignment filters Department by Campus.
- Active Academic Scope shows the correct AY and Term.
- Faculty Portal topbar shows the active academic scope.
- Faculty My Classes shows missing grading-template warnings when applicable.
- Course Template Assignments can list offerings with no grading template.
- Email sending works with production SMTP.
- Logos display in email.
- User signatures still load and print.

Also confirm these permissions exist:

```text
inactive_records.delete
actual_data_reset.run
corrections.create_on_behalf
grade_distribution_monitor.read
faculty_analytics.read
grading_analytics.read
```

If a permission is missing, add it through a targeted migration/seed or create it carefully through the Admin Portal permission maintenance flow if available.

## Scheduled Production Jobs

These are not seed commands. Configure them later as scheduled tasks/cron jobs if the feature is used:

```powershell
python manage.py apply_scheduled_user_deactivations
python manage.py process_faculty_assignment_reminders
python manage.py queue_period_reminders
python manage.py queue_faculty_reminder_emails
python manage.py process_faculty_reminder_email_queue
python manage.py issue_submission_non_compliance_notices
python manage.py auto_lock_period_deadlines
python manage.py auto_lapse_correction_windows
python manage.py process_grade_prediction_queue
```

## Recommended Current Path

Because production is not live yet but already running for limited testing users:

1. Back up production `db.sqlite3`, `media/`, and `.env`.
2. Pull latest code.
3. Run `migrate`, `check`, and `collectstatic`.
4. Decide whether to keep current testing data or replace production with the latest cleaned development database.
5. Do not run `seed_stage_0_1` unless production DB is empty and you intentionally want the default bootstrap data.
6. Perform the smoke test checklist before allowing more users to test.
