# Submission Readiness Email Alerts

TeacherMate+ can send an early exception report to scoped Area Chairs, College Deans, and Chief Academic Officers. It reuses the Admin Portal Grade Submission Readiness calculation and includes only active, accepted, unsubmitted assignments whose readiness is strictly below the configured threshold on the configured day before the deadline.

## Safe defaults

- Enabled: No
- Days before deadline: 5
- Threshold: below 50% (exactly 50% is excluded)
- Recipient roles: Area Chair, College Dean, and CAO
- Empty reports: No
- Dashboard link: Yes
- Repeat reminders: No

Settings are tenant-scoped under Admin Portal > Configuration Management. Enabling requires at least one recipient role.

## Command

```bash
python manage.py send_submission_readiness_alerts --dry-run
python manage.py send_submission_readiness_alerts
python manage.py send_submission_readiness_alerts --as-of-date 2026-07-20
python manage.py send_submission_readiness_alerts --tenant-id 1
```

`--force` bypasses only the successful-delivery duplicate guard. It never bypasses readiness eligibility or recipient scope and creates a separate logged attempt.

## Production scheduler

The repository cron template sets `CRON_TZ=Asia/Manila`, runs the command at `0 1 * * *`, and uses `flock` to prevent overlapping runs. Confirm the application user can read `/etc/teachermateplus/teachermateplus.env`, enter `/opt/teachermateplus`, execute the virtual environment, write the log destination, and create the `/run/lock` lock file.

## Deployment checklist

1. Back up the database and apply migrations.
2. Run `python manage.py check` and the focused tests.
3. Confirm SMTP settings, `DEFAULT_FROM_EMAIL`, and a public HTTPS `SITE_URL`.
4. Review active recipient roles, scope assignments, and email addresses.
5. Leave the policy disabled, run a dry run, then enable it for the intended tenant.
6. Send a controlled test and inspect `SubmissionReadinessNotificationLog` records.
7. Install the cron entry, confirm its timezone and lock, and monitor the first live execution.

Disable the tenant policy to stop delivery immediately. The email contains no student names, scores, grades, averages, or distributions and is only a scheduled snapshot. The live Grade Book and dashboard remain the current sources of truth.

## DEBUG-only orientation dataset

The focused seeder refuses to run unless `DEBUG=True` and `--confirm-demo-data` is supplied. It reuses an existing tenant/campus but owns every new record under the `TEST-READINESS-EMAIL` namespace. The supplied recipient email is stored in the normal `accounts.User.email` field; do not supply a real personal address unless conducting an authorized controlled test.

```powershell
python manage.py seed_submission_readiness_email_demo `
  --confirm-demo-data `
  --tenant NCBA `
  --campus NCBA-01 `
  --recipient-email readiness-head@example.invalid `
  --as-of-date 2026-07-17
```

Created identifiers:

- Faculty: `test-faculty-readiness-01`, `test-faculty-readiness-02`
- Academic Head: `test-readiness-area-chair`
- Academic year/term/period: `TEST-READINESS-EMAIL-AY` / `TEST-READINESS-EMAIL-TERM` / `PRELIM`
- Courses and sections: `TEST-READINESS-EMAIL-A` through `TEST-READINESS-EMAIL-F`
- Students: three active demo enrollments per assignment, 18 total
- Deadline for the example snapshot: July 22, 2026 at 11:59 PM Asia/Manila
- Lock: active campus PRELIM deadline lock with encoding open (`is_locked=False`)

The real readiness service computes:

| Faculty | Assignment | Readiness | Status | Below-50 email |
| --- | --- | ---: | --- | --- |
| `test-faculty-readiness-01` | A | 16.67% | Needs Attention | Yes |
| `test-faculty-readiness-01` | B | 33.33% | Needs Attention | Yes |
| `test-faculty-readiness-01` | C | 50.00% | Needs Attention | No |
| `test-faculty-readiness-02` | D | 16.67% | Needs Attention | Yes |
| `test-faculty-readiness-02` | E | 33.33% | Needs Attention | Yes |
| `test-faculty-readiness-02` | F | 100.00% | Submitted | No |

A and D have all three student records complete for one of six required grading buckets, producing 16.67% setup coverage; B and E have all six buckets configured with one of three students complete, producing 33.33%; C has all student records complete for three of six required buckets, producing exactly 50%; F is complete and submitted. Each assignment has one attendance session, and completed students have a matching attendance record.

Inspect the existing demo dataset without changing it:

```powershell
python manage.py seed_submission_readiness_email_demo --tenant NCBA --confirm-demo-data --inspect
```

Test without sending:

```powershell
python manage.py send_submission_readiness_alerts --as-of-date 2026-07-17 --tenant-id <tenant-id> --dry-run
```

For one controlled email, first reseed with an authorized test mailbox, confirm the dry-run output says `eligible_assignments=4` and `dry_run=1`, then run the same command without `--dry-run`. Never use the `.invalid` example address for delivery.

The seeder is safe to rerun. It backs up the tenant's readiness-email policy before enabling the demo policy. Reset removes only owned records and restores that policy:

```powershell
python manage.py seed_submission_readiness_email_demo --tenant NCBA --confirm-demo-data --reset
```

To change campus, reset first. To reopen repeated data preparation, keep the demo lock at `is_locked=False`; reset and reseed recreates the open lock and deterministic records.
