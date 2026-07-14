# Faculty User CSV Import (V1)

## Purpose

This workflow creates Faculty login accounts using only `accounts.User`, `UserRole`, and the existing tenant, campus, and department scope. It does not create a faculty master record, employee number, or HR record.

## Production settings

```env
FACULTY_IMPORT_EMAIL_ENABLED=False
FACULTY_INVITATION_EXPIRY_HOURS=24
```

Keep `FACULTY_IMPORT_EMAIL_ENABLED=False` in development, staging, testing, and UAT. Enable it in production only after SMTP, public `SITE_URL`, authorization, privacy, and operational ownership are verified. The Admin Portal cannot override a disabled environment switch.

## CSV and workflow

Use the exact official header:

```csv
tenant_code,campus_code,department_code,first_name,middle_name,last_name,email,username
```

`middle_name` and `username` are optional. A blank username is the lowercase local part of the email. Conflicting usernames are rejected; no suffix is generated.

1. Open `Security -> Users -> Import Faculty CSV` or `Tools -> Bulk Imports -> Faculty Users`.
2. Download the official template, upload UTF-8 CSV, and review the preview. Preview does not create users, roles, invitations, or email.
3. Fix invalid, duplicate, or cross-scope rows. Exact existing Faculty identity and role-scope matches are marked for safe skip; other existing-user conflicts require manual reconciliation.
4. Confirm the batch. Every valid row is revalidated and processed in its own transaction. New users are inactive, have unusable passwords, and receive only the active scoped `FACULTY` role.
5. Invitation sending occurs after account creation commits and only when the environment switch is on, the per-batch checkbox is selected, and the actor has `faculty_users.send_import_invitations`.
6. The recipient follows the secure link and sets a password. The account becomes active only after a valid setup completes, then returns directly to the public Faculty Portal landing page at `/faculty/` to sign in. The legacy `/faculty/login/` page is disabled and redirects to `/faculty/`.
7. For a later send or resend, open `Security -> Users`, edit the Faculty user, and use the persistent Faculty Account Invitation panel. The panel shows Not sent, Email disabled, Sent, Failed, Expired, or Accepted plus attempt and expiry information.

Preview labels exact existing Faculty identity/role/scope matches as `Existing matching Faculty account — will be skipped`. Confirmation records `SKIPPED_EXISTING`; it does not count the row as a newly created account and does not send another invitation automatically.

## Invitation safety

- The signed link is single-use, superseded by resend, and expires from the latest successful-send timestamp.
- Faculty invitation email uses the standard NCBA TeacherMate+ `ACCOUNT ONBOARDING` card design while retaining invitation-specific password-setup and expiry wording.
- Resend requires `faculty_users.resend_invitation`, rejects accepted accounts, and is throttled for five minutes.
- SMTP failure leaves the inactive user and scoped Faculty role intact for later authorized resend.
- The signed token is carried in the browser URL fragment, which is not sent in the initial HTTP request or normal access-log path; the setup page posts it as a sensitive field and removes the fragment from browser history.
- Tokens, passwords, and full setup URLs are not stored in the database, audit events, reports, logs, or Admin Portal pages.

## Data retention and reports

The Faculty CSV is parsed in memory and is never saved to the configured import media storage. TeacherMate+ retains normalized staged rows, row results, batch metadata, and audit records. Error-report cells beginning with `=`, `+`, `-`, or `@` are prefixed to prevent spreadsheet formula execution.

## Permissions

- `faculty_users.view_import`
- `faculty_users.import`
- `faculty_users.send_import_invitations`
- `faculty_users.resend_invitation`

The default migration grants these permissions to active Super Admin, Tenant Admin, and Campus Admin roles. Tenant, campus, and department scope is still enforced for every row and invitation action.
