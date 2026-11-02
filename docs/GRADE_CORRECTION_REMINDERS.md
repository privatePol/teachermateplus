# Grade Correction email reminders

## Administrator controls

Configuration Management links to **Grade Correction Email Reminders** at
`/admin-portal/tools/correction-email-reminders/`. It requires Admin Portal access
and `system_settings.update` in the selected tenant scope. Settings persist in the
existing tenant `SystemSetting` JSON key `GRADE_CORRECTION_REMINDER_POLICY`:

| Setting | Default | Alternatives |
| --- | --- | --- |
| Automatic reminders | On | Off |
| Frequency | Every calendar day | Every 2, 3, or 7 calendar days |
| Local time | 08:00 | Any valid HH:MM |
| Timezone | Asia/Manila | Fixed system policy |

No code change or service restart is needed after saving. Weekends and holidays
are included. Intervals are measured from the local date when the current step
became active. Reminders begin on the first eligible date after activation.
Changing the time before dispatch changes eligibility immediately. A reminder
already accepted that day is not resent after a time change.

The existing initial approval email toggle retains its default (off) and remains
independent of reminders (default on). Its legacy recipient-role list is retained
in configuration for compatibility, but no longer broadens correction approval
recipients: initial emails and reminders both follow the current governance
step and actual review authorization. No recipient-role selection is required
to enable initial emails. Registrar report and faculty decision emails retain
their existing policies and are outside this approval-step outbox.

Reviewers can inspect **Email history** on an authorized correction review page.
The view uses the review-page request selector plus selected tenant/campus;
`corrections.review` and Admin Portal access are required. It displays individual
recipient status, schedule, first/last attempts, counts, backend acceptance time,
and fixed outcome codes. It never displays message bodies, justification or grades.
The correction forms, review, grade explanation history and official PDF use
**From / To**; stored `old_value` and `new_value` fields and computation are unchanged.

## Worker and proposed cron

`python manage.py process_correction_reminders --limit 500` discovers existing
pending requests and their currently actionable steps, fills durable recipient
rows, then dispatches due rows. The sequence validator permits multiple future
PENDING rows but selects only the first valid current step. Malformed routes,
terminal requests, inactive roles and ineligible recipients produce no new emails.
Viewing a petition has no notification side effect and does not acknowledge it.

The prepared `ops/cron/teachermateplus.cron` entry polls every minute using the
existing environment/venv conventions and a host `flock`. This file was only
prepared; it must be reviewed and installed by operations after schema and backend
readiness checks. The command reads settings on each run and again before sending.
No service restart is required for tenant scheduling changes.

```cron
* * * * * flock -n /run/lock/teachermateplus-correction-reminders.lock bash -lc 'cd /opt/teachermateplus && set -a && source /etc/teachermateplus/teachermateplus.env && set +a && /opt/teachermateplus/.venv/bin/python manage.py process_correction_reminders' >> /var/log/teachermateplus/correction_reminders.log 2>&1
```

Only today's eligible scheduled reminder is queued; missed days are never replayed.
Old queued reminders are skipped at preflight. Initial notifications are durable
and may retry later while their step remains current. If an initial notification
has already been accepted on today's Manila date, that recipient's reminder for
today is skipped. Successful per-recipient legacy initial audit events are imported
when first discovering the step, so failed recipients are not hidden behind another
recipient's legacy success. Missing/changed approvers are rediscovered each poll.

## Durability, authorization and concurrency

Migration `grading.0036_correction_email_outbox` adds two tables only:
`grading_correctionstepnotificationintent` (one activation marker per step) and
`grading_correctionemaildelivery`. The latter has protected request/step/tenant/
campus/recipient references, initial/reminder kind, scheduled local date and aware
timestamp, recipient address at the last dispatch attempt, status, attempt counts,
first/last attempt times, backend acceptance time, retry time, UUID claim token,
lease expiry and a sanitized outcome code. A database unique constraint covers
step + recipient + kind + scheduled local date. Status/retry and tenant/campus/
schedule indexes support dispatch and inspection. There is no grading data migration.

Request creation and intermediate approval create the next step's durable intent
and currently eligible initial recipient rows inside the governance transaction.
SMTP runs from `on_commit`, after the entire enclosing transaction commits. A
rollback removes the intent and callback. An activation marker persists even with
no eligible recipients or with initial emails disabled. If a process dies after
commit or a callback fails, polling rediscovers the marker and retries the rows.

Recipients require active ordinary assignments to the actual step role, matching
tenant/campus and bounded department ancestry, explicit `corrections.review` and
`admin_portal.access` permissions
grant without superuser shortcuts, no scoped direct DENY (including NULL global
scopes), the governance request-level review guard and on-behalf initiator guard,
and membership in the Admin review selector (including faculty supervision).
Superuser/SUPER_ADMIN capability alone never selects a recipient. Recipient address,
account/assignment state and all guards are rechecked on every dispatch attempt.

A conditional atomic UPDATE claims a recipient row and increments its attempt
counter, with a UUID owner and a 15-minute recovery lease. Other workers cannot
claim that live lease. Failed attempts retry after five minutes; successful
recipients are independent. Expired leases can be reclaimed after a worker crash.
Final status updates require the same live owner token. Network timeout is bounded
at 60 seconds, well below the lease. SQLite claim tests are local evidence;
MariaDB/InnoDB multi-process acceptance remains a separate operational gate.

The final preflight locks the correction request and delivery row, verifies the
current step, recipient and live configuration, and releases the transaction
before constructing/sending SMTP. This defines the decision boundary: a decision
or permission/configuration change that commits after that preflight can race with
an already authorized in-flight email. Later attempts must observe the new state.
No database transaction is held open during SMTP. A bounded SMTP call can still
have an uncertain outcome: if a server accepts an email and the connection or
process dies before the SENT update, retry can deliver a duplicate. This is
recoverable at-least-once intent processing, not exactly-once email delivery.
“Sent” means accepted by the configured backend, not guaranteed inbox delivery.

Operational command output contains counts only; backend exception text is never
stored or printed by this worker. Fixed codes replace raw errors. The console
email backend is refused with `unsafe_console_backend` because cron would otherwise
write full bodies to its operational log. Verify a production SMTP backend before
activation; development locmem/dummy backends do not prove external delivery.

## Review and activation gates

Run Django checks, migration drift and plan inspection, focused outbox tests and
existing grading regressions. Apply schema only in a separately authorized release;
the implementation session must not migrate the normal database. Then validate
authenticated browser flows, production-like SMTP acceptance/failures and real
MariaDB/InnoDB concurrency before installing the cron entry. No deployment,
restart, scheduler installation, or external email acceptance is implied by local
SQLite tests.
