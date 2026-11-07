# TMP-FACULTY-LOGIN-CONTROL-IMPLEMENTATION-01

## Gate 2 - Final targeted full-provisioning lock correction - 2026-11-07

This is the current gate record. It supersedes earlier descriptions of the full faculty provisioning path as account-first. The earlier role-assignment boundary was safe, but full provisioning inserted its account and creation audit before reaching that boundary.

Identity: `D:\tmp_branches\tmp-faculty-login-control`; branch `feat/faculty-login-control`; full HEAD `15bb8d87180ee5d0fe6ecb6017fa76717983b846`. All four requested initial Git commands matched. The initial dirty candidate contained 39 paths (25 modified, 14 untracked), with an empty index. Earlier changes remain preserved.

### Exact correction and regression evidence

- `apps/accounts/faculty_provisioning.py:251`: the first operation inside the provisioning transaction acquires the administrator account lock, before duplicate validation, account creation and audit insertion. This removes the late shared audit-FK to exclusive actor-lock upgrade. Duplicate checks run after the same-actor serialization boundary.
- `apps/accounts/faculty_provisioning.py:109`: a private checked assignment body retains active-role refresh, permission/scope validation, grant creation/reactivation and its audit. Existing-target public assignment still locks actor/target in ID order using the unchanged common helper. Full provisioning calls this body at line 294: its new INSERTed target is already exclusively owned by its transaction, so it avoids reacquiring its actor and opening another assignment-service transaction. Central model-level assignment guards remain intact.
- `apps/accounts/faculty_provisioning.py:212`: bounded SQLite retries wrap the complete provisioning unit. A rolled-back attempt cannot leave an account or creation audit behind. An existing outer transaction owns retry/rollback; nested failures propagate without replaying partial outer work.
- Eight regressions in `apps/accounts/tests_faculty_provisioning_lock.py`: SQL actor-lock order before user/audit INSERTs with no duplicate service actor lock (line 78); two competing full provisioning calls using one actor (152); competing duplicate input leaving one complete account (164); assignment authorization failure rolling back account/audit (171); outer rollback owning the whole unit (179); standalone existing-target assignment retaining both account locks (188); complete SQLite retry after creation audit without duplicate rows (201); nested lock failure propagating once without partial creation (222). Successful cases verify scoped role membership, unusable password/inactive account, unchanged independent login state/version, exact audit identities, scope and import metadata. Controlled competing threads cannot INSERT while the first actor lock is held and finish without deadlock or partial/duplicate records on disposable SQLite.

Exactly seven files changed in this gate:

```text
apps/accounts/faculty_provisioning.py
apps/accounts/tests_faculty_provisioning_lock.py (new)
CHANGE_LOG.md
HANDOFF.md
TEACHERMATEPLUS_CONTEXT.md
docs/FACULTY_LOGIN_CONTROL.md
docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```

No other application module, migration, template or guide changed in this gate. This is transaction ordering, with no user-facing workflow change. Existing login-control configuration, cancellation, manual override, session enforcement and historical academic behavior remain covered by the unchanged 95 existing login-control/review regressions.

### Executed validation

- Before correction, the new SQL-order test ran **1 test in 0.449s: 1 failure, 0 errors, 0 skips**. The actor lock was query index 11, after the account INSERT at index 7. This intentionally failing reproduction used the old loaded implementation, not the corrected code.
- After correction, the focused selection ran **140 tests in 51.443s: 140 passed, 0 failures, 0 errors, 0 skips**. It includes 8 new provisioning tests, 65 original login-control tests, 30 previous review regressions and 37 existing Faculty User Import tests. Expected CSRF/render-failure logs are exercised test scenarios; the unittest result is OK.
- Relevant broader validation ran **645 tests in 316.786s: 645 passed, 0 failures, 0 errors, 0 skips**. It covers all account modules, scoped Users flows, RBAC, faculty provisioning/import/assignment/replacement, session timeout, grade correction/locking/final clearance, relevant initializers and guides. Counts overlap with the focused selection and must not be added as distinct tests. A full repository suite was not run.
- `manage.py check`: zero issues. `manage.py makemigrations --check --dry-run`: no changes. `manage.py migrate --plan`: exit 0, inspected without application, against an explicitly empty in-memory database. Final whitespace/diff/status and fingerprint evidence is recorded below.

Commands use `D:\codex-runtime\quitizz-capacity-20261028-54c9ad77\venv\Scripts\python.exe -B` from the exact worktree. Environment: `DB_ENGINE=django.db.backends.sqlite3`, `DB_NAME=:memory:`, `DJANGO_ENV=local`, local-memory email, external `DJANGO_LOG_DIR`, and `PYTHONPATH=D:\tmp_branches\tmp-faculty-login-control-validation`. The external `tmp_login_validation_settings` enforces in-memory SQLite, local-memory email and test-only MD5 hashing. No application/production settings or secrets were edited.

Focused command:

```powershell
python -B manage.py test apps.accounts.tests_faculty_provisioning_lock apps.accounts.tests_login_control apps.accounts.tests_login_control_review apps.imports.tests_faculty_user_import --settings=tmp_login_validation_settings --noinput --verbosity 1
```

Relevant broader command:

```powershell
python -B manage.py test apps.accounts apps.core.tests_session_timeout apps.admin_portal.tests_users apps.admin_portal.tests_assignment_acceptance apps.admin_portal.tests_faculty_assignment_replacement apps.admin_portal.tests_academic_performance_demo_seed apps.admin_portal.tests_repair_seeded_rbac_navigation apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary apps.faculty_portal.tests_help_guide apps.admin_portal.tests_help_guide apps.academics.tests_assignment_workflow apps.imports.tests_faculty_user_import apps.imports.tests_faculty_assignment_import apps.notifications.tests_submission_readiness_demo apps.grading.tests.CorrectionWorkflowTests apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.FacultyFinalClearanceQrTests apps.grading.tests.CompletionGraceWindowTests apps.grading.tests.GradeEncodingAccessControlTests apps.departmental_exams.tests_stage4_authorization apps.rbac --settings=tmp_login_validation_settings --noinput --verbosity 1
```

Checks use the same Python/environment and `--settings=tmp_login_validation_settings`. Test runner migration application is confined to disposable databases. No new migration was generated; existing accounts 0012/0013/0014 and RBAC 0034 were inspected and remain unchanged from this gate's baseline. The plan includes them because the explicitly empty in-memory database has no ledger; it is not a normal/deployed database status claim. There is no worktree `db.sqlite3`; deployed migration status was not inspected or changed.

Evidence directory: `D:\tmp_branches\tmp-faculty-login-control-provisioning-lock-validation\` (`baseline-targeted.txt`, `focused.txt`, `broader.txt`, `migration-plan.txt`, initial `baseline.json` fingerprints and captured tracked diff). The previous unrelated reminder SQLite concurrency failure remains unresolved; this broader selection excludes that module and does not supersede its failure evidence below. Real MariaDB/InnoDB multi-process acceptance, catalogue-lock contention, browser/device and operational activation remain separate unverified gates. No commit, push, persistent migration, deployment or restart is authorized.

### Final preservation and Git status

Compared all 983 baseline file/protected-path fingerprints: exactly the six existing files listed above changed, and the only added candidate path is the provisioning test module. No missing or unexpected path, protected database/log change, unrelated edit or index entry was found. Accounts 0012/0013/0014, RBAC 0034 and all existing login-control tests/writers except the authorized provisioning source are byte-for-byte unchanged from the gate start. `preservation-final.json` records the audit. Git diff whitespace passed; the full tracked diff and stat were inspected and captured as `final-tracked.diff` and `final-diff-stat.txt`. Against HEAD the accumulated candidate has 25 tracked modified files, 294 insertions and 35 deletions; this includes earlier work and excludes untracked files, so it is not the seven-file incremental gate diff.

Final branch and full HEAD remain `feat/faculty-login-control` / `15bb8d87180ee5d0fe6ecb6017fa76717983b846`; index empty. Final `git status --short` (25 modified, 15 untracked; 40 paths):

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/academics/models.py
 M apps/academics/services.py
 M apps/accounts/faculty_provisioning.py
 M apps/accounts/models.py
 M apps/accounts/services.py
 M apps/accounts/views.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/management/commands/seed_academic_performance_insights_demo.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/core/management/commands/seed_stage_0_1.py
 M apps/faculty_portal/help_guide.py
 M apps/faculty_portal/views.py
 M apps/grading/reporting.py
 M apps/imports/services.py
 M apps/notifications/management/commands/seed_submission_readiness_email_demo.py
 M apps/rbac/models.py
 M config/settings/base.py
 M ops/cron/teachermateplus.cron
 M templates/admin_portal/security/user_table.html
 M templates/admin_portal/tools/configurable_features.html
 M templates/faculty_portal/period_final_clearance.html
?? apps/accounts/backends.py
?? apps/accounts/login_control.py
?? apps/accounts/login_obligations.py
?? apps/accounts/management/commands/process_clearance_login_deadlines.py
?? apps/accounts/middleware.py
?? apps/accounts/migrations/0012_faculty_login_control.py
?? apps/accounts/migrations/0013_clearance_login_policy.py
?? apps/accounts/migrations/0014_protect_login_control_fields.py
?? apps/accounts/tests_faculty_provisioning_lock.py
?? apps/accounts/tests_login_control.py
?? apps/accounts/tests_login_control_review.py
?? apps/admin_portal/login_control_views.py
?? apps/rbac/migrations/0034_seed_login_access_permission.py
?? docs/FACULTY_LOGIN_CONTROL.md
?? docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```

No remaining failing regression or code blocker is known for this narrow finding. Independent review and real InnoDB concurrency acceptance remain next steps; the unrelated reminder error and earlier operational limits remain unresolved. No staging, commit, push, normal-database migration, deployment, service restart, cron installation, protected secret/log edit or unrelated change occurred. Stop after this validation; do not publish.

## Gate 2 - Independent review findings remediation - 2026-11-07

This is the earlier remediation record for the four supplied findings from `TMP-FACULTY-LOGIN-CONTROL-INDEPENDENT-REVIEW-01`. The final targeted provisioning correction above supersedes its full-path locking claim. Earlier requirement-verification sections and line references below are historical and superseded where subsequent sections change behavior.

Identity verified with the requested initial four Git commands: worktree `D:\tmp_branches\tmp-faculty-login-control`, branch `feat/faculty-login-control`, unchanged full HEAD `15bb8d87180ee5d0fe6ecb6017fa76717983b846`. Initial candidate status was 17 modified and 11 untracked paths. This remediation preserves the existing feature work and changes only the findings' write paths, regressions, persistence metadata, affected wording and required documentation. No release action is authorized.

### Exact remediation and source/test evidence

#### 1 - Stale User saves

Ordinary saves remove login_disabled and login_access_version at Django's actual UPDATE boundary, including reconstructed existing-PK instances. The fields are non-editable and refreshed after save. Native INSERT, deferred-field, force-save, clone and password behavior is retained; authorized login-control transitions still use conditional QuerySet updates. Stale account/password writes cannot reset access or decrease the version, and old unregistered legacy cookies remain invalid.

Source: `apps/accounts/models.py:91`; `apps/accounts/models.py:99`; `apps/accounts/migrations/0014_protect_login_control_fields.py:14`.

Regressions: `apps/accounts/tests_login_control_review.py:45`, `apps/accounts/tests_login_control_review.py:59`, `apps/accounts/tests_login_control_review.py:159`, `apps/accounts/tests_login_control_review.py:298`, `apps/accounts/tests_login_control_review.py:312`, `apps/accounts/tests_login_control_review.py:323`.

#### 2 - Continuing assignments

Only the cleared campus/term is excluded. Active/open loading in another campus or any other term (including the next term at this same campus) is a continuing obligation. Exceptions are evaluated both on successful generation and under the worker account lock before application. Closed/inactive loading is excluded; elapsed dates alone do not retire active obligations.

Source: `apps/accounts/login_control.py:216`; `apps/accounts/login_control.py:258`; `apps/accounts/login_control.py:302`.

Regressions: `apps/accounts/tests_login_control_review.py:71`, `apps/accounts/tests_login_control_review.py:77`, `apps/accounts/tests_login_control_review.py:182`, `apps/accounts/tests_login_control_review.py:185`.

#### 3 - Activation race / lock order

Exception-affecting role, permission, direct-grant, assignment and offering writes share ordered account locks with scheduling/application. Model save, update, bulk_create, bulk_update and update_or_create paths are covered. Existing-target role assignment locks actor/target before grant lookup/reactivation. The full provisioning path still locked its actor too late at this earlier gate; the final targeted correction is documented above. Import/replacement/initializers acquire participant locks before their existing row locks. Current locking reads recheck assignment ownership after waiting; newly discovered unlocked owners are rejected for reload instead of locking users out of order. SQLite obtains a writer fence with a no-op version UPDATE; no revocation/version increment or implicit login enabling occurs. Root retries cover complete rolled-back units only.

Source: `apps/accounts/login_obligations.py:35`; `apps/accounts/login_obligations.py:46`; `apps/accounts/login_obligations.py:118`; `apps/accounts/faculty_provisioning.py:87`; `apps/academics/services.py:672`; `apps/imports/services.py:2295`.

Regressions: `apps/accounts/tests_login_control_review.py:400`, `apps/accounts/tests_login_control_review.py:408`, `apps/accounts/tests_login_control_review.py:415`, `apps/accounts/tests_login_control_review.py:420`, `apps/accounts/tests_login_control_review.py:428`, `apps/accounts/tests_login_control_review.py:437`, `apps/accounts/tests_login_control_review.py:335`.

#### 4 - RBAC seed

RBAC 0034 grants established SUPER_ADMIN and CAMPUS_ADMIN roles. No unsupported SUPERADMIN alias was added: seed definitions and existing migration conventions use SUPER_ADMIN. Fresh initialization also defines/grants the permission after creating roles, covering migration-before-initializer ordering. Real migration application with pre-existing roles, idempotent seeding, a non-is_superuser Super Admin actor, tenant isolation and direct DENY are tested.

Source: `apps/rbac/migrations/0034_seed_login_access_permission.py:12`; `apps/core/management/commands/seed_stage_0_1.py:14`, `:34`, `:329`.

Regressions: `apps/accounts/tests_login_control_review.py:86`, `apps/accounts/tests_login_control_review.py:219`, `apps/accounts/tests_login_control_review.py:241`, `apps/accounts/tests_login_control_review.py:251`, `apps/accounts/tests_login_control_review.py:470`.

### Writer inventory and ordering

- `apps/rbac/models.py`: UserRole and UserPermission direct-subject writes; Role and Permission shared-catalogue edits.
- `apps/academics/models.py`: FacultyAssignment direct-subject writes; shared CourseOffering changes affecting active/open term/campus obligations.
- `apps/accounts/faculty_provisioning.py`: existing-target role creation/reactivation uses an account-first transaction and refreshes inactive role state under its lock. The earlier claim that full provisioning was also account-first was inaccurate: its account and creation audit preceded that lock. See the final targeted correction above.
- `apps/academics/services.py`: replacement locks all affected old/new faculty and actor IDs in order, then current assignment rows; a changed participant set is rejected before adding out-of-order account locks.
- `apps/imports/services.py`: faculty/actor locks precede the existing offering lock for each confirmation row.
- Baseline initializer and both demo seed/reset writers prelock the account domain before any mixed account/role/loading writes in their existing outer transactions. Their data-generation logic is unchanged.
- Login transitions lock accounts in ID order, then tenant rows in ID order, then deadline rows. Manual control includes actor/target and explicitly locks the selected and historical-deadline tenants before overriding events; OFF includes its actor before tenant/events, avoiding audit foreign-key inversions. The manual path has a real SQL-order regression at `apps/accounts/tests_login_control_review.py:207`. Shared catalogue updates deliberately lock the account domain; this may add contention and needs real-database performance acceptance.

Controlled thread tests pause automatic processing after it reads exceptions and try to commit role/loading activation before account update. Activation cannot commit through that account lock. The reverse ordering waits for activation and then produces EXEMPT. If the worker commits first, subsequent provisioning keeps that already-disabled login disabled; independent Manual Enable Login is still required. The new thread suite also controls stale profile/password saves and unregistered legacy cookies, and rejects an assignment owner changing while its account lock is being acquired. The new controlled thread probes set SQLite read_uncommitted=1 only on their isolated test-thread connections to expose stale check/update interleavings; the explicit writer fence still serializes writes. These are SQLite tests, not a MariaDB/InnoDB certification.

### Approved requirements preserved

All original 65 login-control regressions remain in the executed selection: separate login state/activation, tenant-only default OFF, default 2 and minimum 1 whole days, first-successful-PDF elapsed-hour cutoff, per-event immutable days/deadline, permanent OFF cancellation and no rearm/backfill, manual permission/direct-DENY controls, audit/session revocation and preserved historical grades/contributions/attendance/loading. Three old new-cycle fixtures now explicitly close prior active/open offerings before expecting another term's event to schedule; production governance is not weakened to satisfy those fixtures. Guide/policy/clearance wording now mentions continuing terms.

### Exact files modified by this remediation (24)

```text
CHANGE_LOG.md
HANDOFF.md
TEACHERMATEPLUS_CONTEXT.md
apps/academics/models.py
apps/academics/services.py
apps/accounts/faculty_provisioning.py
apps/accounts/login_control.py
apps/accounts/login_obligations.py                         (new)
apps/accounts/models.py
apps/accounts/migrations/0014_protect_login_control_fields.py (new)
apps/accounts/tests_login_control.py
apps/accounts/tests_login_control_review.py               (new)
apps/admin_portal/help_guide.py
apps/admin_portal/management/commands/seed_academic_performance_insights_demo.py
apps/core/management/commands/seed_stage_0_1.py
apps/faculty_portal/help_guide.py
apps/imports/services.py
apps/notifications/management/commands/seed_submission_readiness_email_demo.py
apps/rbac/migrations/0034_seed_login_access_permission.py
apps/rbac/models.py
docs/FACULTY_LOGIN_CONTROL.md
docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
templates/admin_portal/tools/configurable_features.html
templates/faculty_portal/period_final_clearance.html
```

### Validation and migration status

- Final focused/guide run: **117/117 passed in 20.652 seconds**, zero failures/errors/skips. This includes all 30 new review regressions, the original 65 login-control tests and 22 guide tests; counts are not additive with the broader run.
- Final broader run: **675 executed in 134.305 seconds; 674 passed, 0 failures, 1 error, 0 skips**. The error is `apps.grading.tests_correction_reminders.CorrectionReminderTransactionTests.test_simultaneous_dispatch_sends_one_recipient_once`: `OperationalError: database table is locked: grading_correctionemaildelivery` during the unchanged `claim()` UPDATE. The broader selection is **not green**. The same error was independently reproduced on the unchanged-HEAD archive below.
- The exact errored reminder test passed in isolation: **1/1 in 0.336 seconds**. A separate source-only archive from unchanged HEAD ran the entire reminder module: **38/38 in 11.415 seconds**. Neither result cancels the broader error. Grading service/test hashes match the gate baseline; no reminder code/test was altered.
- Comparative unchanged-HEAD broader run: **580 executed in 101.444 seconds; 577 passed, 2 failures, 1 error, 0 skips**. It reproduced the exact same reminder simultaneous-dispatch `grading_correctionemaildelivery` table-lock error without any login-control implementation. The two additional failures assert an NCBA email logo; the source-only archive omitted `media/logos`, and `email_assets._resolve_logo_path` falls back to a text header when those assets are absent. Those two archive-fixture failures are not candidate failures or evidence of an application regression. The baseline selection has 95 fewer tests because the 65 original login-control and 30 new review tests are uncommitted candidate files, absent from HEAD.
- Final Django check: zero issues; migration drift: no changes; migration plan: exit 0; no normal migration application. `sqlmigrate accounts 0014` confirms two no-op metadata operations on SQLite.

Output evidence: `final-all-focused.txt`, `final-all-broader.txt`, `reminder-lock-rerun.txt`, `baseline-reminders.txt`, `baseline-broader.txt`, `migration-plan-current.txt`, and `accounts-0014-sql.txt` under `D:\tmp_branches\tmp-faculty-login-control-review-remediation-validation\`. Baseline results come from the separately labeled `baseline-head` source archive (no candidate changes, secrets, logs or normal DB copied); they are not candidate acceptance evidence.

All commands use `D:\codex-runtime\quitizz-capacity-20261028-54c9ad77\venv\Scripts\python.exe -B` with worktree CWD, explicit in-memory SQLite, local-memory email and external logs. Tests select the existing external `tmp_login_validation_settings` through temporary PYTHONPATH, including MD5 hashing for tests only. Application settings and production hashing were not changed. Test databases were created/migrated/destroyed only by Django's disposable test runner. No normal local database was created or migrated.

Focused command (30 new review tests, 65 existing login-control tests, 22 guide tests):

```powershell
python -B manage.py test apps.accounts.tests_login_control_review apps.accounts.tests_login_control apps.admin_portal.tests_help_guide apps.faculty_portal.tests_help_guide --settings=tmp_login_validation_settings --noinput --verbosity 1
```

Broader command:

```powershell
python -B manage.py test apps.accounts apps.core.tests_session_timeout apps.admin_portal.tests_users apps.admin_portal.tests_assignment_acceptance apps.admin_portal.tests_faculty_assignment_replacement apps.admin_portal.tests_academic_performance_demo_seed apps.admin_portal.tests_repair_seeded_rbac_navigation apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary apps.faculty_portal.tests_help_guide apps.admin_portal.tests_help_guide apps.academics.tests_assignment_workflow apps.imports.tests_faculty_user_import apps.imports.tests_faculty_assignment_import apps.notifications.tests_submission_readiness_demo apps.grading.tests.CorrectionWorkflowTests apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.FacultyFinalClearanceQrTests apps.grading.tests.CompletionGraceWindowTests apps.grading.tests.GradeEncodingAccessControlTests apps.grading.tests_correction_reminders apps.departmental_exams.tests_stage4_authorization apps.rbac --settings=tmp_login_validation_settings --noinput --verbosity 1
```

- Required `check` (zero issues), `makemigrations --check --dry-run` (no changes), `migrate --plan` (exit 0, isolated empty database plan) and diff whitespace checks executed. Full tracked diff/stat and new files inspected.
- Accounts 0012 and 0013 are byte-for-byte unchanged. RBAC 0034 is still an uncommitted migration, corrected to established roles; it was not applied to a normal database. Accounts 0014 is newly generated, additive model-state metadata (`editable=False` only). `sqlmigrate accounts 0014` produces two no-op operations, with no data or DDL rewrite on SQLite.
- The target worktree has no persistent `db.sqlite3` and no persistent migration ledger to mark applied. The isolated empty plan includes accounts 0012/0013/0014 and RBAC 0034. Test setup applies all four; the migration regression explicitly rolls back/reapplies RBAC 0034 in the disposable database with roles already present. Deployed/external migration ledgers were not inspected.
- Baseline reproduction: initial five tests had four failures and one test-harness schema-editor error; the harness was corrected without changing application code, then all five failed as expected (stale account/password overwrites, next-term scheduling/application and SUPER_ADMIN grant). Intermediate 88-, 91-, 113-, 115- and 671-test selections passed as regressions and edge cases were added. An intermediate 673-test broader run had one SQLite table-lock error in the unchanged correction-reminder dispatch concurrency test (delivery claim UPDATE); no grading/reminder code was changed to hide it. Final/isolated rerun outcomes are recorded above.

### Remaining risks and exact next steps

No publication, schema application, deployment, service restart, cron installation or feature activation is authorized. Review this uncommitted candidate at the exact branch/HEAD, then obtain separate authorization for any later gate. MariaDB/InnoDB multi-process behavior, shared-catalogue lock contention, deployed migration ledger/settings, authenticated browser/modal/mobile behavior and cron health remain unexecuted release gates. User UPDATE protection uses Django's ORM update hook, tested on the installed Django 5.2.12; retain these regressions when upgrading Django. Prior unresolved handoff items remain preserved. The four supplied findings have no remaining failure in the executed focused selection. Broader acceptance remains blocked by the reproduced baseline reminder SQLite concurrency error; that service/test is outside this remediation scope. Final preservation/status evidence follows.

### Final preservation and status

The gate changed exactly 21 existing allowed files and added three allowed files. All 959 other baseline fingerprints match, including normal source DB/protected log paths and the existing accounts migrations/reminder service/tests. The worktree normal DB remains absent. The index is empty. Existing unrelated candidate contents remain byte-for-byte unchanged. Evidence: external `baseline.json` and `preservation-final.json`.

The final candidate is 39 paths: 25 tracked modifications and 14 untracked files. The 24-file remediation inventory above distinguishes this gate from preserved earlier feature changes. Full tracked stat:

```text
CHANGE_LOG.md                                      | 13 +++++
 HANDOFF.md                                         |  9 +++
 TEACHERMATEPLUS_CONTEXT.md                         |  9 +++
 apps/academics/models.py                           |  8 ++-
 apps/academics/services.py                         | 14 +++++
 apps/accounts/faculty_provisioning.py              |  5 ++
 apps/accounts/models.py                            | 65 ++++++++++++++++++++++
 apps/accounts/services.py                          |  6 +-
 apps/accounts/views.py                             | 17 +++++-
 apps/admin_portal/help_guide.py                    |  3 +
 .../seed_academic_performance_insights_demo.py     |  4 ++
 apps/admin_portal/urls.py                          |  3 +
 apps/admin_portal/views.py                         | 12 +++-
 apps/core/management/commands/seed_stage_0_1.py    |  6 ++
 apps/faculty_portal/help_guide.py                  |  2 +-
 apps/faculty_portal/views.py                       | 22 +++-----
 apps/grading/reporting.py                          |  5 +-
 apps/imports/services.py                           |  5 +-
 .../seed_submission_readiness_email_demo.py        |  4 ++
 apps/rbac/models.py                                | 15 +++--
 config/settings/base.py                            |  5 ++
 ops/cron/teachermateplus.cron                      |  2 +
 templates/admin_portal/security/user_table.html    | 27 ++++++++-
 .../admin_portal/tools/configurable_features.html  | 12 ++++
 .../faculty_portal/period_final_clearance.html     | 13 +++++
 25 files changed, 257 insertions(+), 29 deletions(-)
```

Final `git status --short`:

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/academics/models.py
 M apps/academics/services.py
 M apps/accounts/faculty_provisioning.py
 M apps/accounts/models.py
 M apps/accounts/services.py
 M apps/accounts/views.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/management/commands/seed_academic_performance_insights_demo.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/core/management/commands/seed_stage_0_1.py
 M apps/faculty_portal/help_guide.py
 M apps/faculty_portal/views.py
 M apps/grading/reporting.py
 M apps/imports/services.py
 M apps/notifications/management/commands/seed_submission_readiness_email_demo.py
 M apps/rbac/models.py
 M config/settings/base.py
 M ops/cron/teachermateplus.cron
 M templates/admin_portal/security/user_table.html
 M templates/admin_portal/tools/configurable_features.html
 M templates/faculty_portal/period_final_clearance.html
?? apps/accounts/backends.py
?? apps/accounts/login_control.py
?? apps/accounts/login_obligations.py
?? apps/accounts/management/commands/process_clearance_login_deadlines.py
?? apps/accounts/middleware.py
?? apps/accounts/migrations/0012_faculty_login_control.py
?? apps/accounts/migrations/0013_clearance_login_policy.py
?? apps/accounts/migrations/0014_protect_login_control_fields.py
?? apps/accounts/tests_login_control.py
?? apps/accounts/tests_login_control_review.py
?? apps/admin_portal/login_control_views.py
?? apps/rbac/migrations/0034_seed_login_access_permission.py
?? docs/FACULTY_LOGIN_CONTROL.md
?? docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```

Final branch/HEAD were rechecked in the named candidate worktree: `feat/faculty-login-control` at `15bb8d87180ee5d0fe6ecb6017fa76717983b846`. No staging, commit, push, normal-database migration, deployment, restart, cron installation, secret/protected-log edit or unrelated change occurred. Stop at Gate 2.


## Earlier Gate 2 evidence (historical)

## Gate 2 requirement verification and focused remediation - 2026-11-07

This section supersedes the original fixed-48-hour / pause-and-resume policy recorded below. Worktree `D:\tmp_branches\tmp-faculty-login-control`; branch `feat/faculty-login-control`; unchanged full HEAD `15bb8d87180ee5d0fe6ecb6017fa76717983b846`. The requested first four Git commands matched the expected identity. The initial 17 modified and 10 untracked feature files were preserved; this follow-up adds accounts migration 0013.

Inspection found missing configurable days, cancellation/re-enable semantics and strict tenant-only activation. Remediation is confined to these requirements, their persistence/display/tests and required documentation. PDF construction, successful timestamp capture, download audit and cycle recording now share account/tenant locks, preventing policy changes from interleaving between successful generation and recording. Workers take tenant locks before deadline locks, matching OFF cancellation.

### All eleven requirements verified

All test references below are in `apps/accounts/tests_login_control.py`. Line numbers refer to this final uncommitted candidate.

| # | Confirmed behavior | Implementation evidence | Focused regression evidence |
| --- | --- | --- | --- |
| 1 | Automatic Faculty Login Disabling is in Configurable Features | `templates/admin_portal/tools/configurable_features.html:316`; `apps/admin_portal/views.py:5739`; `apps/admin_portal/login_control_views.py:20` | `:266` renders controls and persisted days |
| 2 | Tenant-only, default OFF; no global activation fallback | `apps/accounts/login_control.py:44` | `:231`, `:321`, `:425` |
| 3 | Administrators persist whole-number days in tenant INT settings | `apps/admin_portal/login_control_views.py:22`; `apps/accounts/login_control.py:77` | `:254`, `:266` |
| 4 | Minimum 1 enforced on POST, in service and database snapshot constraint | `apps/admin_portal/login_control_views.py:23`; `apps/accounts/login_control.py:55`; `apps/accounts/models.py:122`; `apps/accounts/migrations/0013_clearance_login_policy.py:29` | `:278`, `:292`, `:297`, `:414` |
| 5 | Default delay 2 | `apps/accounts/login_control.py:41`; `apps/accounts/models.py:122` | `:266`, `:321` |
| 6 | First successful PDF timestamp plus configured days times 24 elapsed hours, calculated in UTC | `apps/accounts/login_control.py:241`, `:277`; `apps/faculty_portal/views.py:4706` | `:101`, `:116`, `:123`, `:133`, `:297`, `:345` (real PDF, failure exclusion, exact cutoff and DST) |
| 7 | Existing cycle timestamps/delay/deadline never change; new cycles use new days | `apps/accounts/login_control.py:274` (get-or-create and return existing event) | `:307` tests old 2-day versus new 5-day events and old faculty display |
| 8 | Saving OFF atomically cancels all tenant PENDING events; disabled users/APPLIED events remain disabled | `apps/accounts/login_control.py:77`, `:87` | `:205`, `:219`, `:389`, `:398`, `:745` |
| 9 | Canceled cycles stay terminal; successful OFF PDFs retain terminal markers; no backfill/reprint rearm | `apps/accounts/login_control.py:280`, `:283`; unique cycle constraint `apps/accounts/models.py:133` | `:205`, `:226`, `:330`, `:734` |
| 10 | Manual Enable/Disable Login is independent of automatic setting | `apps/accounts/login_control.py:190`; `apps/admin_portal/login_control_views.py:34` | `:379` executes both operations with ON and OFF |
| 11 | Tenant/campus target restrictions, RBAC/direct DENY, atomic audit, manual overrides and history remain | `apps/accounts/login_control.py:113`, `:150`, `:190`, `:304`; `apps/admin_portal/login_control_views.py:55` | `:166`, `:177`, `:364`, `:419`, `:429`, `:443`, `:506`, `:608`, `:619`, `:628`, `:640` plus broader tests |

CANCELED OFF-generation rows retain generation/delay/cutoff metadata for audit and unique-cycle identity; they are never pending automatic actions. Re-enabling schedules only genuinely new qualifying cycles. Manual controls preserve the original account and existing historical records.

### Files changed by this follow-up (16)

```text
CHANGE_LOG.md
HANDOFF.md
TEACHERMATEPLUS_CONTEXT.md
apps/accounts/login_control.py
apps/accounts/models.py
apps/accounts/tests_login_control.py
apps/accounts/migrations/0013_clearance_login_policy.py  (new)
apps/admin_portal/help_guide.py
apps/admin_portal/login_control_views.py
apps/admin_portal/views.py
apps/faculty_portal/help_guide.py
apps/faculty_portal/views.py
docs/FACULTY_LOGIN_CONTROL.md
docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
templates/admin_portal/tools/configurable_features.html
templates/faculty_portal/period_final_clearance.html
```

### Current validation

- `git diff --check`: passed. Full tracked `git diff --stat` and `git diff` inspected, alongside all new feature code/migrations/tests. Final status and preservation verification follow below.
- `python -B manage.py check`: passed, zero issues.
- `python -B manage.py makemigrations --check --dry-run`: passed, no changes detected.
- `python -B manage.py migrate --plan`: exit 0; accounts 0012/0013 and RBAC 0034 appear in the isolated empty in-memory database plan. No normal database ledger was inspected or migrated.
- Final focused run: **65/65 passed in 7.052 seconds**, zero failures/errors/skips, including 15 additional regression tests and six SQLite thread races.
- Final broader run: **566/566 passed in 80.349 seconds**, zero failures/errors/skips. It includes the 65 focused tests; counts are not additive. The selection covers authentication/OTP/session behavior, Admin list/create/edit and role controls, faculty assignment/clearance/attendance/guide behavior, grading/correction/locks, reminders and departmental authorization. `apps.rbac` has no separate test module in this checkout; relevant RBAC assertions run in the included suites.
- Superseded 61-test run: three errors caused by editing the PDF integration while the test process was still importing modules (old service module plus newly loaded view). It is not acceptance evidence. The fresh, settled 65-test run above passed.

The default Python lacked Channels; validation used `D:\codex-runtime\quitizz-capacity-20261028-54c9ad77\venv\Scripts\python.exe`. Every Django command explicitly used `DB_ENGINE=django.db.backends.sqlite3`, `DB_NAME=:memory:`, `DJANGO_ENV=local`, local-memory email and external `DJANGO_LOG_DIR`. Tests used the existing external `tmp_login_validation_settings` module via temporary PYTHONPATH, including MD5 test password hashing only; application settings were not changed by this follow-up. Migrations were applied only automatically in disposable Django test databases. Test output and the migration plan are under `D:\tmp_branches\tmp-faculty-login-control-requirement-validation\`.

Broader command:

```powershell
python -B manage.py test apps.accounts apps.core.tests_session_timeout apps.admin_portal.tests_users apps.admin_portal.tests_assignment_acceptance apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary apps.faculty_portal.tests_help_guide apps.admin_portal.tests_help_guide apps.grading.tests.CorrectionWorkflowTests apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.FacultyFinalClearanceQrTests apps.grading.tests.CompletionGraceWindowTests apps.grading.tests.GradeEncodingAccessControlTests apps.grading.tests_correction_reminders apps.departmental_exams.tests_stage4_authorization apps.rbac --settings=tmp_login_validation_settings --noinput --verbosity 1
```

### Migration status and remaining gates

- Existing `accounts.0012_faculty_login_control` and `rbac.0034_seed_login_access_permission` are unchanged from this follow-up's baseline.
- New additive `accounts.0013_clearance_login_policy` adds saved `delay_days` default 2, minimum-1 CHECK and the CANCELED state choice. Existing deadline timestamps remain unchanged. It seeds no enabled feature and performs no grade/history rewrite.
- No known unmet numbered requirement remains in source/focused evidence. MariaDB/InnoDB multi-process acceptance (including legacy RBAC/loading writers), authenticated browser/modal/mobile checks, normal/deployed migration-ledger inspection, cron installation/health and feature activation remain separate gates. Rendered Django test-client pages and SQLite races do not establish operational acceptance. Earlier unresolved handoff items remain preserved.
- No commit, staging, push, normal-database migration, deployment, application-service restart, cron installation, secret/log edit or unrelated worktree change is included. Stop at Gate 2.

### Final preservation and Git status

Fingerprint comparison: 15 existing files changed within the explicit follow-up allowlist, one new migration, and 964 other baseline fingerprints unchanged. This includes both existing feature migrations, protected worktree/source database and log paths; the worktree normal database remains absent. The index is empty. No unrelated file changed. Evidence: external `preservation.json` and `baseline.json`.

Final `git diff --stat`: 17 tracked files, 182 insertions and 22 deletions. Git stat excludes the 11 untracked files. Final total status: 28 paths, all unstaged.

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/accounts/models.py
 M apps/accounts/services.py
 M apps/accounts/views.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/faculty_portal/help_guide.py
 M apps/faculty_portal/views.py
 M apps/grading/reporting.py
 M config/settings/base.py
 M ops/cron/teachermateplus.cron
 M templates/admin_portal/security/user_table.html
 M templates/admin_portal/tools/configurable_features.html
 M templates/faculty_portal/period_final_clearance.html
?? apps/accounts/backends.py
?? apps/accounts/login_control.py
?? apps/accounts/management/commands/process_clearance_login_deadlines.py
?? apps/accounts/middleware.py
?? apps/accounts/migrations/0012_faculty_login_control.py
?? apps/accounts/migrations/0013_clearance_login_policy.py
?? apps/accounts/tests_login_control.py
?? apps/admin_portal/login_control_views.py
?? apps/rbac/migrations/0034_seed_login_access_permission.py
?? docs/FACULTY_LOGIN_CONTROL.md
?? docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```


## Original implementation evidence (historical; policy and counts superseded above)

Gate 2 implementation and focused validation, 2026-11-07 (Asia/Manila). No commit or release gate was executed.

## Verified identity and scope

- Source: `D:\teachermateplus`, branch `feat/departmental-exam-builder-stage5-8`, HEAD `15bb8d87180ee5d0fe6ecb6017fa76717983b846`.
- Implementation: `D:\tmp_branches\tmp-faculty-login-control`, branch `feat/faculty-login-control`, unchanged HEAD `15bb8d87180ee5d0fe6ecb6017fa76717983b846`.
- Initial source commands were `git status --short`, `git branch --show-current`, `git log -1 --oneline`, and `git rev-parse HEAD`. All expected baseline values matched. A clean worktree was created with `git worktree add -b feat/faculty-login-control D:\tmp_branches\tmp-faculty-login-control 15bb8d87180ee5d0fe6ecb6017fa76717983b846`; it was reused after the requested pause. No dirty source files were copied.
- Critical dependencies were reinspected inside the clean worktree: existing clearance evaluator/PDF renderer, session registry/revocation, OTP pipeline, scope/RBAC/direct-deny services, SystemSetting support, Security Users actions, audit service, migration chains and cron architecture were present. No unrelated changes were imported.

## Implementation

- Separate `login_disabled` and `login_access_version` user fields preserve activation and identity. Version zero preserves existing session compatibility; changing login access permanently invalidates earlier authentication hashes, including fallback-key hashes. Registered sessions are also revoked atomically.
- The backend, OTP epoch check, completion/registration rechecks and middleware enforce password, OTP, Django Admin and existing-session restrictions. Middleware performs exact-cutoff processing, avoiding a session-loader deletion/save conflict found and corrected during the first focused run.
- One persistent clearance event per user/tenant/campus/term records successful CLEARED PDF generation and exactly 48 hours. Preview, failed/incomplete rendering and old report records do not schedule. Reprinting does not reset or rearm a cycle. Random bounded report reference suffixes avoid rapid regeneration collisions.
- Manual enable overrides all existing pending/applied deadlines across the account. An in-flight generation with an obsolete access version becomes terminally overridden; new qualifying cycles may establish later deadlines.
- Active/open loading in another campus, another active role or administrative access yields an auditable MIS exception. Checks run at generation and application. Active/open loading is treated conservatively until operations closes/unassigns it.
- Users row actions require a dedicated permission, explicit confirmation, CSRF-protected POST and expected version. Target authorization rejects uncovered/global/multi-scope targets for scoped operators and protects self/superuser/administrative accounts. Direct DENY takes precedence. UI facts are batched; writes reload and independently authorize under a user lock.
- User/version/deadline/session/audit transitions are atomic. User-first locks, conditional version updates, unique cycle identity and bounded whole-unit SQLite retries protect competing operations. Automatic transitions share tenant policy locks with the configuration form.
- Configuration is default OFF and requires an explicitly typed BOOL true. OFF pauses new deadlines/application without restoring disabled login; pending deadlines resume upon reactivation. No feature activation occurred outside disposable tests.
- Added a management command and prepared cron template entry, with no new background framework. Cron was not installed. See `FACULTY_LOGIN_CONTROL.md` for full policy and operational limits.

## Files modified (17)

```text
CHANGE_LOG.md
HANDOFF.md
TEACHERMATEPLUS_CONTEXT.md
apps/accounts/models.py
apps/accounts/services.py
apps/accounts/views.py
apps/admin_portal/help_guide.py
apps/admin_portal/urls.py
apps/admin_portal/views.py
apps/faculty_portal/help_guide.py
apps/faculty_portal/views.py
apps/grading/reporting.py
config/settings/base.py
ops/cron/teachermateplus.cron
templates/admin_portal/security/user_table.html
templates/admin_portal/tools/configurable_features.html
templates/faculty_portal/period_final_clearance.html
```

## Files created (10)

```text
apps/accounts/backends.py
apps/accounts/login_control.py
apps/accounts/management/commands/process_clearance_login_deadlines.py
apps/accounts/middleware.py
apps/accounts/migrations/0012_faculty_login_control.py
apps/accounts/tests_login_control.py
apps/admin_portal/login_control_views.py
apps/rbac/migrations/0034_seed_login_access_permission.py
docs/FACULTY_LOGIN_CONTROL.md
docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```

## Validation actually executed

All Django invocations set `DB_ENGINE=django.db.backends.sqlite3`, `DB_NAME=:memory:`, `DJANGO_ENV=local`, `EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend`, and `DJANGO_LOG_DIR=D:\tmp_branches\tmp-faculty-login-control-validation\logs`. `python -B` avoided bytecode artifacts. No ordinary database was opened for writes. Automatic migrations ran only in disposable Django test databases.

| Command/run | Actual result |
| --- | --- |
| `git diff --check` | Passed; final full diff/stat/status also inspected |
| `python -B manage.py check` | Passed, zero issues |
| `python -B manage.py makemigrations accounts --name faculty_login_control` | Created accounts 0012 only |
| `python -B manage.py makemigrations --check --dry-run` | Passed, no changes detected |
| `python -B manage.py migrate --plan` | Exit 0; inspected plan against isolated empty in-memory SQLite, not the normal DB ledger |
| Initial focused module | 35 run, one session-loader HTTP-400 failure; corrected before completion |
| Corrected focused module | 35/35 passed |
| Expanded focused module | 46/46 passed |
| `python -B manage.py test apps.accounts.tests_login_control apps.departmental_exams.tests_stage4_authorization --noinput --verbosity 1` | 63/63 passed in 75.332 s (then 49 login-control plus 14 departmental authorization tests) |
| Final `python -B manage.py test apps.accounts.tests_login_control --noinput --verbosity 1` | **50/50 passed in 6.147 s**, no failures/errors/skips |
| Final broader selection below | **537/537 passed in 104.515 s**, no failures/errors/skips |

The final broader run used an external validation-only settings module at `D:\tmp_branches\tmp-faculty-login-control-validation\tmp_login_validation_settings.py`, selected through temporary `PYTHONPATH`. It imports the worktree settings, forces in-memory SQLite and local-memory email, and uses MD5 password hashing for test speed only. Production password hashing configuration was not changed.

```powershell
python -B manage.py test apps.accounts apps.core.tests_session_timeout apps.admin_portal.tests_users apps.admin_portal.tests_assignment_acceptance apps.faculty_portal.tests_assignment_acceptance apps.faculty_portal.tests_attendance_summary apps.faculty_portal.tests_help_guide apps.admin_portal.tests_help_guide apps.grading.tests.CorrectionWorkflowTests apps.grading.tests.FinalGradeFormulaTests apps.grading.tests.FacultyFinalClearanceQrTests apps.grading.tests.CompletionGraceWindowTests apps.grading.tests.GradeEncodingAccessControlTests apps.grading.tests_correction_reminders --settings=tmp_login_validation_settings --noinput --verbosity 1
```

The 50 focused tests are included in the 537; counts are not additive. A superseded default-hasher broader run was stopped before a summary after the final faster run completed; it is not counted as passed. Only that verified test process was stopped, not an application service. Expected negative-test tracebacks (rendering failure, CSRF, rejected SMTP mocks and oversized request bodies) do not represent suite failures.

Coverage includes real successful/repeated PDF responses; preview/render failure/incomplete/report-history exclusion; fixed cutoff and late-cron authentication enforcement; stale epochs and manual overrides; new cycles; feature OFF and malformed setting types; other roles/campuses and tenant isolation; POST/CSRF/direct-DENY/target protections; legacy and registered sessions, OTP, async authentication and Django Admin; rollback on audit failure; and immutable submitted grades, contributions, attendance, loading and prior audit rows. Four thread-based tests cover competing generation, manual requests, due workers, and manual enable versus due work. These are SQLite tests, not MariaDB/InnoDB multi-process acceptance.

Logs/plans: external `focused-50.txt`, `focused-final.txt`, `broader-final.txt`, and `migration-plan.txt` under the validation directory. No repository log was modified.

## Migration impact/status

- `accounts.0012_faculty_login_control`: adds independent Boolean login state (false) and positive version (zero); creates the deadline table with protected scope/source links, unique cycle identity, positive deadline check and two due/user indexes. Generated dependencies include accounts 0011, academics 0011, grading 0036 and tenants 0005.
- `rbac.0034_seed_login_access_permission`: additively creates `users.login_access.update` and grants it to existing SUPERADMIN/CAMPUS_ADMIN roles without replacing custom grants/denies. No setting is enabled and no historical deadline is backfilled.
- Both migrations applied successfully in disposable test databases. **Neither was applied to the normal local database.** The ordinary database's applied migration ledger was not queried; the validation plan was for an empty disposable database.
- No grade, attendance, contribution, employment or history schema/data conversion.

## Preservation evidence and remaining gates

- All 36 source dirty-file contents matched their initial combined SHA-256 manifest: `B2D8473A0C252362554CB7B4F73CC3ADE3789EFD9189B4BF441EA1BAA4FFBCD2`.
- Source `db.sqlite3`: `166E9DDF4AEC99E891B306AC1103D041D843A38EA0E0E5E796CFDC2F1B176122` unchanged.
- Source `logs/system.log`: `41065D8706BB507B2855D88E74341A2A84C2002601D2770A0DCD0928939BA214` unchanged. Worktree tracked logs also remain unchanged; no worktree ordinary `db.sqlite3` was created.
- No known failing test remains in the executed selections. This is not the full project suite or operational acceptance. MariaDB/InnoDB multi-process locking, concurrent legacy assignment/RBAC writers, authenticated browser/modal/mobile inspection, deployed settings, cron installation/health and activation remain separate gates. Already-running requests cannot be recalled; revocation applies to subsequent requests.
- Exact next step: independent review of this uncommitted 27-file diff at the pinned worktree/HEAD. Keep commit/push, normal schema application, deployment, cron installation and feature activation separately authorized. All previous unresolved handoff items remain below the new entry.

## Final worktree status

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/accounts/models.py
 M apps/accounts/services.py
 M apps/accounts/views.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/urls.py
 M apps/admin_portal/views.py
 M apps/faculty_portal/help_guide.py
 M apps/faculty_portal/views.py
 M apps/grading/reporting.py
 M config/settings/base.py
 M ops/cron/teachermateplus.cron
 M templates/admin_portal/security/user_table.html
 M templates/admin_portal/tools/configurable_features.html
 M templates/faculty_portal/period_final_clearance.html
?? apps/accounts/backends.py
?? apps/accounts/login_control.py
?? apps/accounts/management/commands/process_clearance_login_deadlines.py
?? apps/accounts/middleware.py
?? apps/accounts/migrations/0012_faculty_login_control.py
?? apps/accounts/tests_login_control.py
?? apps/admin_portal/login_control_views.py
?? apps/rbac/migrations/0034_seed_login_access_permission.py
?? docs/FACULTY_LOGIN_CONTROL.md
?? docs/TMP_FACULTY_LOGIN_CONTROL_IMPLEMENTATION_01_REPORT.md
```

No commits, pushes, normal-database migrations, deployments, service restarts, credentials/secret changes or feature activation occurred. Source unrelated work remains preserved. Stop at Gate 2.
