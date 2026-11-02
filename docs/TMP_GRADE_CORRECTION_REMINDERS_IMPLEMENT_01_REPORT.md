# TMP-GRADE-CORRECTION-REMINDERS-IMPLEMENT-01

## Latest gate: expired-claim durability remediation (2026-11-02)

**Narrow remediation completed; independent review is next.** Branch remains
`feat/departmental-exam-builder-stage5-8`, full HEAD
`56a370bea4eda9250f0dd55b8e058d208182a6e1`.

### Root cause and minimal correction

`preflight` returned `claim_lost` both for an expired same-worker lease and for a
replacement owner. Dispatch treated every non-ready outcome as SKIPPED. If the
expired worker still owned the token, its existing token/status-guarded final
UPDATE succeeded, storing SKIPPED / claim_lost and clearing the claim. That
outcome was absent from the rediscovery allowlist, so the unsent email stayed
stranded permanently.

The production correction consists of two scoped changes in
`apps/grading/correction_reminders.py`:

1. On `claim_lost`, leave the row untouched and bypass finalization. An expired
   PROCESSING row remains reclaimable by the existing atomic claim UPDATE;
   another worker's replacement claim and audit fields remain untouched.
2. Permit current eligible rediscovery to requeue earlier SKIPPED / claim_lost
   rows, using the existing delivery identity. This repairs previously stranded
   initial and currently due reminder rows without a schema/data migration.

Claim acquisition, UUID ownership, lease duration, retry interval, token/status
completion predicates, attempt counts/timestamps and SENT terminal status are
unchanged. Current-step, recipient and live configuration validation still run
before every actual send. No UI, notification policy, scheduler, model or schema
change was made. Historic reminder dates are not replayed.

### Exact files changed in this remediation gate

- `apps/grading/correction_reminders.py`
- `apps/grading/tests_correction_reminders.py`
- `CHANGE_LOG.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `HANDOFF.md`
- `docs/TMP_GRADE_CORRECTION_REMINDERS_IMPLEMENT_01_REPORT.md` (this latest-gate section)

No new repository path was created. All other starting file fingerprints,
including normal `db.sqlite3`, protected `logs/system.log`, cron and grading
migration 0036, remain unchanged. Earlier implementation/unrelated dirty work
was preserved. The original implementation inventory below is historical; the
six-file inventory above is the exact scope of this remediation gate.

### Tests and validation

| Execution | Result |
| --- | --- |
| Six new regression tests against the unfixed source | 3 passed / 3 failed, reproducing expired-owner finalization and stranded initial/reminder rows |
| Focused reminder module after remediation | **38/38 passed**, 17.427s, 0 failures/errors/skips |
| Previous 198-test regression selection plus six new regressions | **204/204 passed**, 94.836s, 0 failures/errors/skips |
| `python -B manage.py check` | Passed, 0 issues, exit 0 |
| `python -B manage.py makemigrations --check --dry-run` | Passed, no changes detected, exit 0 |
| `python -B manage.py migrate --plan` | Inspected successfully, exit 0; nothing applied |
| `git diff --check` | Passed, exit 0; line-ending notices only |
| `git diff --stat`, relevant source/test and gate-start diffs | Inspected; existing untracked service/test files compared against gate-start snapshots |

The six new tests cover: expiry with the same token followed by successful
reclaim/send; replacement-token rejection at stale preflight; stale sender
completion fencing after replacement; terminal SENT preservation; and recovery
of earlier SKIPPED / claim_lost initial and due reminder rows. Existing tests
also exercise simultaneous claim/dispatch, recipient/approval guards and normal
initial/daily behavior. The 38 focused tests are included in the 204-test total;
overlapping runs are not added together.

Both passing test runs used the existing `correction_test_settings`, isolated
SQLite test database `D:/codex-tmp/grade-correction-reminders-20261102/test.sqlite3`,
locmem email, fast test passwords and existing external dependency paths. No
normal database or external email backend was used by tests. All Django file
logs were redirected to the new evidence directory.

The broad command was the original selection recorded below, with the same
module/class labels and `--settings=correction_test_settings --keepdb --noinput
--verbosity 1`; discovery increased from 198 to 204 solely because six tests were
added to `apps.grading.tests_correction_reminders`.

### Migration status, remaining gates and operations boundary

`apps/grading/migrations/0036_correction_email_outbox.py` is byte-for-byte unchanged:
SHA-256 `55e3265256e368162ff7f90f3faa0e2ad97d46cbf09ea647b34a7aa295976a65`.
The normal migration plan still contains only pre-existing Departmental Exam
0013/0014 and grading 0036. No migration was added or applied to the normal DB.

No remaining blocker is known for the confirmed expired-claim defect. The nine
previously reproduced, unrelated GradeExplanation fixture/Admin Guide issues
remain unresolved and were not rerun or changed in this gate. MariaDB/InnoDB,
SMTP/inbox, browser and deployment/cron acceptance remain unverified. Existing
SMTP uncertain-outcome and final-preflight concurrency limitations are unchanged.

No staging, commit, push, deployment, cron installation/activation, restart,
destructive Git operation, normal-database migration, protected log or secret
modification occurred. Independently review this narrow diff before any
separately authorized publication/operational gate.

Evidence: `D:/codex-tmp/grade-correction-claim-remediation-20261102/` contains
`before-fix.log`, `focused.log`, `regression.log`, regression exit status, normal
migration plan, gate-start snapshots/fingerprints, exact remediation diff and
final status. Current `git status --short` is identical to the captured gate-start
status and to the full status block at the bottom of this report; only the six
listed file contents changed during this gate.

## Original implementation gate (historical)

Implementation and focused validation completed on 2026-11-02. Ready for independent source review; operational acceptance remains pending.

## Baseline and scope

- Repository: `D:\teachermateplus`.
- Branch: `feat/departmental-exam-builder-stage5-8`.
- Full HEAD (unchanged): `56a370bea4eda9250f0dd55b8e058d208182a6e1`.
- Actual starting status: 24 modified tracked files and 12 untracked status paths. The attachment's inspection counts were older. No initial index changes were present; final index remains unstaged.
- This session modified 23 existing files and created 9 files, listed below. Unrelated baseline files retained their SHA-256 fingerprints, including protected `logs/system.log`. Shared dirty documentation/help files received only the scoped additions/wording changes shown by the session-start diff. Prior unresolved handoff entries were retained.

## Delivered behavior

- **Configuration Management -> Grade Correction Email Reminders**, `/admin-portal/tools/correction-email-reminders/`: tenant settings persist through existing SystemSetting JSON. Defaults: enabled, every calendar day, 08:00 Asia/Manila. Alternatives: every 2, 3 or 7 calendar days, any HH:MM. Settings are read dynamically without restarts. Admin Portal access and `system_settings.update` are required; direct DENY is enforced, including global NULL scopes.
- The existing initial-email toggle remains independent, with its existing default off. Enabled initial emails now follow the active governance step after commit. Legacy selected recipient roles remain stored/displayed for compatibility but cannot broaden recipients; their former mandatory selection validation was removed. Registrar/faculty-decision policies remain unchanged and outside this approval-step outbox.
- Request creation and intermediate approval persist current-step intent/initial recipient rows transactionally. Post-commit dispatch sends immediately; polling recovers a process crash after commit and discovers existing pending requests or newly eligible approvers. Only the first pending step in a valid sequence is actionable. Final closure/rejection and former steps fail dispatch preflight. Viewing a request does not stop reminders.
- Reminder intervals are anchored to step activation's Manila date, beginning on a later eligible calendar date. Weekends/holidays count. Only today's currently due reminder is generated, without historical backlog. An initial email accepted today suppresses that recipient's reminder today.
- Actual step-role assignment, active account/role, tenant/campus/department ancestry, ordinary review/portal permission grants, direct DENY, governance/on-behalf guards, and the authoritative Admin request selector/supervision intersect recipient eligibility. Overrides alone never add recipients. All guards/address/configuration are rechecked immediately before dispatch.
- **Email history** on each scoped correction review page shows per-recipient schedule, status, attempt counts/times, backend acceptance and sanitized outcome codes. It reuses the review selector, selected tenant/campus and review/portal permissions, including global direct DENY. No grades, bodies, justification or raw backend exception text are stored in operational delivery history.
- From / To labels cover Faculty/Admin correction forms, review, grade explanation correction history and official PDF; saved grade fields/history, numeric values and governance/computation rules are preserved.

## Scheduler and tracking

Command: `python manage.py process_correction_reminders --limit 500`.

Prepared entry in `ops/cron/teachermateplus.cron` (not installed/activated):

```cron
* * * * * flock -n /run/lock/teachermateplus-correction-reminders.lock bash -lc 'cd /opt/teachermateplus && set -a && source /etc/teachermateplus/teachermateplus.env && set +a && /opt/teachermateplus/.venv/bin/python manage.py process_correction_reminders' >> /var/log/teachermateplus/correction_reminders.log 2>&1
```

Database uniqueness covers step + recipient + notification kind + scheduled local date. Conditional atomic claims use UUID owners, 15-minute recovery leases and counters/timestamps. Failed rows retry after five minutes, independently for each recipient. Final status updates require the same owner. SMTP is bounded to 60 seconds and runs outside database transactions. Legacy successful step audit records are imported per recipient. Console-based email backends are refused to prevent body output in cron logs; worker output contains counts and sanitized errors only.

**Sent means accepted by the configured email backend, not guaranteed inbox delivery.** SMTP acceptance followed by process/connection loss before the database update can lead to a duplicate on recovery. No exactly-once promise is made. A decision/configuration/permission change after the locked final preflight can race with an already authorized in-flight email; subsequent attempts observe the new state. SQLite can defer an attempt on lock contention; that path was exercised with retry. MariaDB behavior remains unverified. See `docs/GRADE_CORRECTION_REMINDERS.md` for the complete boundary and operating contract.

## Migration and schema

New: `apps/grading/migrations/0036_correction_email_outbox.py`.

- Adds `grading_correctionstepnotificationintent`: unique protected step reference and activation timestamps.
- Adds `grading_correctionemaildelivery`: protected request/step/tenant/campus/recipient references, recipient address at dispatch, initial/reminder kind, local scheduled date/aware time, pending/processing/sent/failed/skipped statuses, attempt and backend acceptance timestamps/counts, retry time, claim UUID/expiry and sanitized outcome code.
- Adds delivery identity uniqueness plus status/retry and tenant/campus/schedule indexes. No grade-data rewrite or existing column rename.
- Normal-database `migrate --plan` shows new grading 0036 and pre-existing Departmental Exam 0013/0014. **No normal-database migration was applied.** Full schema migrations and migration regressions ran only against isolated disposable SQLite test databases.

## Validation and limitations

| Final validation | Result |
| --- | --- |
| `python -B manage.py check` | Passed, 0 issues |
| `python -B manage.py makemigrations --check --dry-run` | Passed, no model drift |
| `python -B manage.py migrate --plan` | Inspected only; no application |
| `git diff --check` / final `git -c core.safecrlf=false diff --check` | Passed; normal Git line-ending notices only |
| Selected regression command below | **198/198 passed**, 55.928s, 0 failures/errors/skips; includes **32 new focused tests** |
| Separate authenticated on-behalf/history rendering and actual PDF text-label test | **1/1 passed**, 0.784s; actual PDF has PG (From) / PG (To) |
| Baseline reproduction of unrelated broader failures | 9 tests: 8 fixture errors, 1 guide-anchor failure, reproduced unchanged |
| MariaDB/InnoDB, real SMTP/inbox, browser/physical device, staging/production | Not executed / unverified |

Selected final regression command (the 32 focused tests are included; overlapping earlier runs are not added):

```powershell
python -B manage.py test apps.grading.tests_correction_reminders apps.grading.tests.CorrectionWorkflowTests apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.FacultyFinalClearanceQrTests apps.grading.tests.CompletionGraceWindowTests apps.grading.tests.GradeEncodingAccessControlTests apps.grading.tests_correction_governance_migrations apps.admin_portal.tests_correction_governance_department_labels apps.faculty_portal.tests_help_guide --settings=correction_test_settings --keepdb --noinput --verbosity 1
```

External `correction_test_settings` restricts tests to `D:/codex-tmp/grade-correction-reminders-20261102/test.sqlite3`, uses locmem email and fast test passwords, and leaves the normal database outside test execution. Existing external Channels/print dependencies supplied missing local packages through PYTHONPATH; no application dependency files were changed. Django logs were redirected outside the repository for all manage.py commands.

An exploratory broader run executed 222 tests with 212 passes, 2 failures and 8 errors. The concurrent SQLite test initially assumed immediate delivery; it now verifies one claim, no duplicate SMTP and durable retry after SQLite lock contention, and passes in the final selected run. The remaining nine issues are pre-existing: all eight GradeExplanation tests assume a `faculty_portal.access` fixture row absent from this baseline, and one Admin Guide test expects a missing `assignment-acceptance` anchor. They were reproduced using the HEAD GradeExplanation test source and session-start guide helper/templates. No unrelated fix was made. The full broader suite is therefore not reported green.

The separate artifact test verified server-rendered on-behalf and correction-history headers and parsed an actual generated PDF. It did not execute browser JavaScript or visually inspect a PDF/browser. Existing approval, score application, rejection, final closure, formula and gradebook locking regressions passed in the selected suite. Normal menu/view permissions were exercised through Django's authenticated test client; production RBAC data and SMTP were not exercised.

Evidence directory (outside the repository): `D:/codex-tmp/grade-correction-reminders-20261102/`, including `regression-final-198.log`, `artifact-labels.log`, `baseline-failures.log`, normal migration plans, baseline/final manifests and `session.patch`. Actual session diffs, new files and full worktree diff statistics were inspected; protected log contents and unrelated source diffs were excluded from content review.

## Exact session file inventory

Modified existing files (23):

- `CHANGE_LOG.md`
- `HANDOFF.md`
- `TEACHERMATEPLUS_CONTEXT.md`
- `apps/admin_portal/forms.py`
- `apps/admin_portal/help_guide.py`
- `apps/admin_portal/urls.py`
- `apps/admin_portal/views.py`
- `apps/faculty_portal/forms.py`
- `apps/faculty_portal/help_guide.py`
- `apps/faculty_portal/views.py`
- `apps/grading/models.py`
- `apps/grading/notifications.py`
- `apps/grading/reporting.py`
- `apps/grading/services.py`
- `apps/grading/tests.py`
- `ops/cron/teachermateplus.cron`
- `templates/admin_portal/grading/correction_request_create_on_behalf.html`
- `templates/admin_portal/grading/correction_request_review.html`
- `templates/admin_portal/guide.html`
- `templates/admin_portal/tools/configurable_features.html`
- `templates/faculty_portal/guide.html`
- `templates/faculty_portal/period_corrections.html`
- `templates/grading/grade_explanation_detail.html`

Created files (9):

- `apps/admin_portal/correction_email_views.py`
- `apps/grading/correction_reminders.py`
- `apps/grading/management/commands/process_correction_reminders.py`
- `apps/grading/migrations/0036_correction_email_outbox.py`
- `apps/grading/tests_correction_reminders.py`
- `docs/GRADE_CORRECTION_REMINDERS.md`
- `docs/TMP_GRADE_CORRECTION_REMINDERS_IMPLEMENT_01_REPORT.md`
- `templates/admin_portal/grading/correction_email_history.html`
- `templates/admin_portal/tools/correction_email_settings.html`

## Remaining gates

Independent review is next. Separately authorize any remediation/publication, normal schema application, real MariaDB multi-process concurrency tests, SMTP/backend checks, authenticated browser acceptance and cron installation. The pre-existing broader fixture/guide failures remain unresolved. No implementation blocker remains for source review, but local SQLite tests are not operational acceptance.

No commit, staging, push, deployment, normal-database migration, service restart or cron installation/activation occurred. Existing unrelated work was preserved.

## Final git status --short

```text
 M .gitignore
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/admin_portal/forms.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/core/context_processors.py
 M apps/core/services/features.py
 M apps/departmental_exams/automatic_workflow.py
 M apps/departmental_exams/contribution_selectors.py
 M apps/departmental_exams/faculty_views.py
 M apps/departmental_exams/forms.py
 M apps/departmental_exams/models.py
 M apps/departmental_exams/monitoring_views.py
 M apps/departmental_exams/stage6_views.py
 M apps/departmental_exams/urls.py
 M apps/faculty_portal/forms.py
 M apps/faculty_portal/help_guide.py
 M apps/faculty_portal/tests_help_guide.py
 M apps/faculty_portal/views.py
 M apps/grading/models.py
 M apps/grading/notifications.py
 M apps/grading/reporting.py
 M apps/grading/services.py
 M apps/grading/tests.py
 M apps/notifications/submission_readiness.py
 M apps/notifications/tests_submission_readiness.py
 M config/settings/base.py
 M logs/system.log
 M ops/cron/teachermateplus.cron
 M templates/admin_portal/grading/correction_request_create_on_behalf.html
 M templates/admin_portal/grading/correction_request_review.html
 M templates/admin_portal/guide.html
 M templates/admin_portal/tools/configurable_features.html
 M templates/departmental_exams/admin/automatic_generation_summary.html
 M templates/departmental_exams/admin/contributor_monitoring.html
 M templates/faculty_portal/guide.html
 M templates/faculty_portal/period_corrections.html
 M templates/grading/grade_explanation_detail.html
?? apps/admin_portal/correction_email_views.py
?? apps/departmental_exams/migrations/0014_courseexamconfiguration_extended_contribution_deadline.py
?? apps/departmental_exams/tests_automatic_deadline_extension.py
?? apps/departmental_exams/tests_contributor_monitoring_draft_print.py
?? apps/departmental_exams/tests_resources.py
?? apps/grading/correction_reminders.py
?? apps/grading/management/commands/process_correction_reminders.py
?? apps/grading/migrations/0036_correction_email_outbox.py
?? apps/grading/tests_correction_reminders.py
?? docs/CODEX_WORKFLOW_RUNNER.md
?? docs/GRADE_CORRECTION_REMINDERS.md
?? docs/TMP_GRADE_CORRECTION_REMINDERS_IMPLEMENT_01_REPORT.md
?? docs/codex_workflow_task.example.toml
?? scripts/codex_workflow.py
?? scripts/tests/
?? templates/admin_portal/grading/correction_email_history.html
?? templates/admin_portal/tools/correction_email_settings.html
?? templates/departmental_exams/admin/automatic_contribution_deadline_extension.html
?? templates/departmental_exams/admin/contributor_monitoring_draft_print.html
?? templates/departmental_exams/faculty/answer_sheet.html
?? templates/departmental_exams/faculty/resources.html
```
