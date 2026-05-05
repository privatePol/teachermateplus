# Live Production Update Guide

Use this guide **after EduGradesPro is officially live**.

This is different from the testing-phase guide. Once production is live, do **not** replace the production database with a development database, and do **not** run broad seed or reset commands unless there is a planned, approved maintenance activity.

## Core Rule

For live production, normal updates should follow this pattern:

```powershell
git pull
python manage.py migrate
python manage.py check
python manage.py collectstatic
restart application service
smoke test
```

Never copy a development `db.sqlite3` over the live production database.

## Commands To Avoid On Live Production

Do not run this on a live production database:

```powershell
python manage.py seed_stage_0_1
```

This command is for bootstrap scenarios and may create default tenant/campus/program/user/menu records that do not belong in a live production database.

Do not use this casually:

```text
Admin Portal -> Tools -> Actual Data Reset
```

Actual Data Reset is destructive and should only be used before go-live, during rehearsals, or during an explicitly approved rebuild.

## Before Every Live Update

### 1. Announce The Maintenance Window

Tell users when the system may be unavailable.

Recommended message:

```text
EduGradesPro will undergo maintenance on [date/time]. Please save your work and avoid encoding grades during the maintenance window.
```

### 2. Check Current Git State

On production:

```powershell
git status
```

Expected result:

```text
nothing to commit, working tree clean
```

If production has local file changes, stop and review them before pulling.

### 3. Back Up The Live Database

For SQLite:

```powershell
copy db.sqlite3 backups\db.sqlite3.before_update_YYYYMMDD_HHMM
```

For another database engine, use the official database dump command for that engine.

### 4. Back Up Media

Back up:

```text
media/
```

This can contain:

- logos
- signatures
- correction attachments
- generated reports
- import files

### 5. Back Up Production `.env`

Back up:

```text
.env
```

Important production values include:

```env
DJANGO_SECRET_KEY=...
SIGNATURE_ENCRYPTION_KEY=...
DEBUG=False
ALLOWED_HOSTS=...
CSRF_TRUSTED_ORIGINS=...
EMAIL_HOST=...
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=...
```

Keep `SIGNATURE_ENCRYPTION_KEY` stable. Changing it can break existing encrypted signatures.

## Review The Incoming Update

Before pulling on production, review the update from your development or staging machine.

Check whether the update includes migrations:

```powershell
git diff --name-only HEAD..origin/main
```

Look for files like:

```text
apps/*/migrations/*.py
```

If migrations exist, review whether they:

- add fields
- remove fields
- backfill data
- seed permissions or menus
- alter constraints
- create indexes

For high-risk migrations, test them on a staging copy first.

## Recommended Staging Test Before Live Deployment

Before deploying to live production, test the update on a staging copy of production data.

1. Copy the latest production backup to staging.
2. Pull the new code on staging.
3. Run:

```powershell
python manage.py migrate
python manage.py check
python manage.py collectstatic
```

4. Smoke test the affected modules.

Only proceed to live production after staging passes.

## Live Update Steps

### 1. Put The App In Maintenance Mode If Available

If you have web-server maintenance mode, enable it before restarting or migrating.

If no maintenance mode exists, perform the update during a low-use window.

### 2. Pull Code

```powershell
git pull
```

### 3. Install Dependencies If Needed

If `requirements.txt`, `pyproject.toml`, or dependency lock files changed, update dependencies using the production-approved method.

Example:

```powershell
pip install -r requirements.txt
```

Only do this inside the correct production virtual environment.

### 4. Run Migrations

```powershell
python manage.py migrate
```

If migration fails, stop. Do not continue to `collectstatic` or restart as if the update succeeded.

### 5. Run Django Check

```powershell
python manage.py check
```

Expected:

```text
System check identified no issues
```

### 6. Collect Static Files

```powershell
python manage.py collectstatic
```

### 7. Restart The Application

Restart the service based on your hosting setup.

Examples:

```powershell
Restart-Service edugradespro
```

or, on Linux:

```bash
sudo systemctl restart edugradespro
sudo systemctl restart nginx
```

Use the actual service names configured on the production server.

## Post-Update Smoke Test

After restart, verify the system immediately.

### Login And Navigation

- Admin login works.
- Faculty login works.
- Logout works.
- Admin menus load correctly.
- Faculty menus load correctly.

### Security And Permissions

Verify important permissions still exist:

```text
admin_portal.access
faculty_portal.access
inactive_records.delete
actual_data_reset.run
corrections.create_on_behalf
grade_distribution_monitor.read
faculty_analytics.read
grading_analytics.read
```

Verify only authorized roles have sensitive permissions such as:

```text
inactive_records.delete
actual_data_reset.run
users.create
roles.update
system_settings.update
```

### Admin Portal Checks

Check:

- Dashboard loads.
- Active tenant/campus scope selector works.
- Active Academic Scope displays correct AY and Term.
- Users list loads.
- User create page filters Default Department by Default Campus.
- User role assignment filters Department by Campus.
- Departments page separates active/inactive records.
- Courses, Sections, Course Offerings, Programs lists load.
- Course Template Assignments page loads.
- `Offerings with no grading template` filter works.
- Grade Distribution Monitor loads.
- Configuration Management loads.

### Faculty Portal Checks

Check:

- Faculty Dashboard loads.
- Faculty topbar shows active academic scope.
- My Classes loads.
- Pending assignment acceptance still works.
- Missing grading-template warning appears when applicable.
- Grading page opens for a valid class.
- Summary page opens for a valid class.
- Class List page opens.

### Email Checks

Check:

- SMTP settings are valid.
- Test email or onboarding email can send.
- Email logo displays.
- Password reset OTP email sends if forgot-password is used.

### Signature Checks

Check:

- My Signature page opens.
- Existing uploaded signature previews correctly.
- PDF/report signature rendering still works.

### File/Media Checks

Check:

- Logo images load.
- Report downloads work.
- Correction attachments still download only for authorized users.

## Scheduled Job Check

Confirm scheduled jobs are still configured after deployment.

Common jobs:

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

These are operational jobs, not seed commands.

## Rollback Plan

Always have a rollback plan before updating.

### If Code Update Fails Before Migration

You can usually roll back code:

```powershell
git checkout <previous-known-good-commit>
```

Then restart the app.

### If Migration Fails Midway

Stop and inspect the error.

Do not blindly retry without understanding the failure.

Use the database backup if needed.

For SQLite:

1. Stop the app.
2. Restore the backup:

```powershell
copy backups\db.sqlite3.before_update_YYYYMMDD_HHMM db.sqlite3
```

3. Return code to the previous known-good commit.
4. Restart the app.

### If Migration Succeeds But App Breaks

Prefer a forward fix if possible.

If rollback is required:

1. Stop the app.
2. Restore database backup.
3. Restore media backup if the update changed media behavior.
4. Return code to previous known-good commit.
5. Restart.
6. Run smoke tests.

## Handling New Permissions Or Menu Items

Preferred approach:

- New permissions and menu items should be added by migrations.
- Then `python manage.py migrate` applies them automatically.

Avoid broad seed commands on live production.

If a new permission is missing after deployment:

1. Confirm whether a migration exists for it.
2. Run `python manage.py migrate` again and check output.
3. If still missing, create a targeted migration or controlled admin action.
4. Assign the permission to the proper roles.

## Handling `.env` Changes

Since `.env` is ignored by Git, code updates will not update production `.env`.

When a release needs new environment variables:

1. Add the variable to production `.env`.
2. Restart the app.
3. Confirm the feature works.

Never commit real `.env` files.

Keep `.env.example` updated with placeholder values when new variables are required.

## Handling Database Changes

After go-live:

- Do not copy development DB to production.
- Do not run development cleanup scripts on production.
- Do not use Actual Data Reset unless this is a planned rebuild.
- Always back up before migrations.
- Always test risky migrations on staging first.

## Handling Media Changes

If the update affects logos, reports, signatures, or attachments:

1. Back up `media/`.
2. Confirm `MEDIA_ROOT` and `MEDIA_URL` are correct.
3. Confirm private media is not directly exposed.
4. Confirm protected download views still enforce permission checks.

## Recommended Release Checklist

Before deployment:

- [ ] Update tested locally.
- [ ] Staging test passed.
- [ ] Production DB backup completed.
- [ ] Production media backup completed.
- [ ] Production `.env` backup completed.
- [ ] Maintenance window announced.
- [ ] Rollback commit identified.

During deployment:

- [ ] `git pull`
- [ ] Dependencies updated if needed.
- [ ] `python manage.py migrate`
- [ ] `python manage.py check`
- [ ] `python manage.py collectstatic`
- [ ] Application restarted.

After deployment:

- [ ] Admin login tested.
- [ ] Faculty login tested.
- [ ] Main menus tested.
- [ ] Active Academic Scope checked.
- [ ] Key changed pages smoke-tested.
- [ ] Email checked if affected.
- [ ] Signature/report checked if affected.
- [ ] Scheduled jobs checked.
- [ ] Users informed that maintenance is complete.

## Emergency Notes

If production is unstable:

1. Stop new user activity if possible.
2. Preserve logs.
3. Identify whether the problem is code, migration, settings, static files, email, or media.
4. Use the rollback plan if a fast fix is not clear.
5. Document what happened in the incident notes.

For incident response details, also refer to:

```text
docs/PRODUCTION_INCIDENT_RUNBOOK.md
```
