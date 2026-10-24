# QuiTizz Phase 4 implementation review

## Identity and scope

Worktree `D:\codex-worktrees\TMP-QuiTizz-Attendance-Integration`, branch `integration/quitizz-faculty-attendance`, baseline/unchanged HEAD `fed75a3f5c1366e09647946fac0e2ac885762080`. The baseline was clean. Implementation and disposable local validation only; index stays empty. No commit, push, deployment, restart, persistent migration, secrets/environment/repository logs or Attendance/DTR/Grade Distribution functional changes.

## Fastest Five and awards

Host/projector HTTP state exposes Fastest Five only after authoritative reveal. It reads existing accepted `QuiTizzResponse.elapsed_ms`, filters correct/nonremoved respondents, orders by elapsed milliseconds, receipt timestamp and response PK, and limits to five. Stored whole milliseconds represent server-observed active answering time including network latency and excluding pauses. Equal displayed milliseconds are recorded ties, not sub-millisecond measurements. No response time or awarded point is rewritten. Empty results do not invent a Question Winner.

The separate Fastest Five screen shows authoritative rank, escaped nickname, prominent milliseconds, a gold Question Winner and time-taken bars (shorter is faster). Existing finite effects and reduced motion apply. Highest Scores uses the original server ranking; JavaScript scales bars without sorting participants or calculating scores. The overall champion/podium retains existing points and tie rules.

Optional final Fastest Correct recognition defaults OFF. It requires at least one correct response and correct answers on at least `ceil(all launched questions / 2)`, including unplayed questions in an early end. Removed players are excluded. Aggregate integer sums/counts are compared using Python `Fraction`, then greater correct count, greater existing total points and stable participant UUID. Only the displayed average is rounded to two decimal places; it is explicitly an average of recorded whole-ms times. Nobody eligible means no award. Dual winners are allowed. Recognition does not record physical prize delivery or alter scores/ranks.

## Immutable settings and presentation

New launches save policy version 2 in the existing immutable `automation_policy_snapshot`: Fastest Five ON, Highest Scores ON, final award OFF; Answer/Fastest/Scores durations 2/5/5 seconds with strict integer bounds 1 to 60. Suspense/preparation retain 5 seconds. Settings are chosen at launch; no launched JSON is filled or mutated by the version-aware reader. Empty/version-1 snapshots retain 7-second combined results and no new awards. Original regression fixtures explicitly launch version 1; dedicated Phase 4 fixtures use the real version-2 service default.

Manual remains default and recommended for prize awarding. After reveal, Answer/Fastest Five/Highest Scores selection is persisted without a transition deadline or `paused_at` awarding hold. Refresh/recovery restores it while the session is valid and authorization remains. The host explicitly opens the next question; prompt and A-D choices open together with the answering timer.

Automatic uses the existing session-row-locked scheduler: answering cutoff, 5-second suspense, Answer 2s, Fastest Five 5s if enabled, Highest Scores 5s if enabled, 5-second preparation, next pending question. Result substeps stay in existing RESULTS phase and persist selector/start/deadline/version. Disabled screens are skipped. Restart recovery executes one overdue transition and gives the newly entered screen its full configured interval. Final enabled results lead directly to completion, without preparation. No browser timer advances gameplay; no new polling loop or per-second write exists. Audio source is unchanged.

## Navigator and lifecycle

Host navigator shows original number, preview and Completed/Current/Pending status. Pending means `opened_at IS NULL`; an opened zero-response question cannot replay. Selection validates an unopened same-session UUID under the existing session lock, increments the version and audits the command without interrupting the current question or changing its deadline. Selection remains possible while paused. Manual opening is explicit; Automatic consumes it only at the next scheduled opening.

The shared resolver chooses valid saved upcoming question, otherwise lowest original unopened position. Opening clears the reference; snapshots and positions never reorder. Skipped questions remain pending. Normal completion refuses pending questions. Early End Challenge/Cancel requires explicit confirmation when pending remain and records confirmation/pending count in audit metadata. End after reveal completes played results; ending an unrevealed question cancels. No responses/results are fabricated for unplayed questions.

The uncached, host-owned/scoped GET review endpoint is `/faculty/quitizz/sessions/<session UUID>/review/<question UUID>/`. Only revealed questions from that session are returned. The separate Historical review panel displays saved choices, key and fastest results without issuing a gameplay command, changing live state/projector, reopening answers or modifying scheduler/scores. Late responses after revocation/selection changes are ignored. Historical cumulative points are not reconstructed or invented.

## Security and concurrency

New commands use existing `command()` atomic session-row lock, ownership/tenant/campus lookup, post-lock assigned RBAC/direct-DENY check, feature/expiry checks, expected state version, audit and post-commit wakeup. CSRF remains in the normal POST route. Selected reference is validated in the locked service and model validation; the FK alone cannot encode same-session/unopened semantics. Player HTTP state never gains Fastest Five, navigator or secondary award. Existing scoring/answer acceptance paths are unchanged. WebSocket gameplay remains the constant notification `{"event":"sync_required"}`; no protected payload was added.

Real InnoDB-only tests cover scheduler/selection and selection/resume races. Existing scheduler/host and dual-scheduler races are retained. SQLite covers stale/duplicate commands, selection during live/paused phases, scope/denial, answer retry/deadline and progression invariants; it does not prove row-lock serialization. Real host/override/completion/answer-close contention and operational load remain acceptance work.

## Migration 0006

`apps/quitizz/migrations/0006_phase4_presentation_navigation.py` depends on QuiTizz 0005, tenants 0005 and the swappable user model (the generated state dependencies).

- Session `results_screen`: non-null `CharField(16)`, default `ANSWER`; allowed values ANSWER, FASTEST_FIVE, HIGHEST_SCORES; DB check `qts_results_screen_valid`.
- Session `upcoming_question`: nullable/blank FK to saved SessionQuestion, `PROTECT`, `related_name='+'`; default NULL and normal FK index.
- Response composite lookup index `qtr_fastest_idx`: session_question, is_correct, elapsed_ms, received_at. Deterministic response PK remains the final ORDER BY key.
- Schema additions only: no RunPython, answer timestamps/points rewrite, policy JSON rewrite or question reorder. Historical migration regression preserves empty policy and receives ANSWER/NULL defaults.

The test runner may apply/reverse/reapply migrations only in its disposable database. **No migration was applied to any persistent database.** Later activation requires schema 0006 before this code. MariaDB DDL/index creation can lock or rebuild populated tables; exact engine/version, duration, disk space, FK/check behavior, EXPLAIN/index effectiveness, rollback order and full disposable migration/race acceptance remain unverified. Preserve schema/snapshots on operational rollback; do not run old code against pending Phase 4 sessions without a reviewed compatibility plan.

## Validation environment and commands

External settings: `D:\codex-runtime\quitizz-local-20261024-tsiul6lw\quitizz_validation_settings.py`. Django 5.2.12/Python 3.14.3, in-memory SQLite (normal and TEST NAME both `:memory:`), process-local caches and in-memory Channels, console logging, locmem email, dotenv disabled and bytecode disabled. No repository dependencies/configuration were installed/changed. This reconstructed external runtime is not deployment parity. No local MariaDB/MySQL/Redis service/listener or Docker/Podman was available; no production database was accessed.

Commands use `PYTHONPATH` containing that directory, its `deps`, and the existing external print dependencies. `DJANGO_LOG_DIR` points outside the repository; `DB_ENGINE=django.db.backends.sqlite3`, `DB_NAME=:memory:`, `QUITIZZ_DEPLOYMENT=local`, `QUITIZZ_REDIS_URL=''`, `PYTHON_DOTENV_DISABLED=1` and `PYTHONDONTWRITEBYTECODE=1`.

```powershell
python -B manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.faculty_attendance.tests_dtr_selection.DTRSelectionTests apps.faculty_attendance.tests_exceptions_only.AdminHoursBatchTests apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_saves_independent_quitizz_automatic_switch --settings=quitizz_validation_settings --noinput -v 1
python -B manage.py test apps.quitizz.tests.QuiTizzFoundationTests.test_faculty_guide_explains_authoring_and_snapshot_boundaries apps.faculty_portal.tests_help_guide --settings=quitizz_validation_settings --noinput -v 1
node --import file:///D:/codex-runtime/TMP-Faculty-Attendance-Integration/dtr-workspace-20261009/jsdom-register.mjs --test frontend/quitizz/*.test.mjs
node --import file:///D:/codex-runtime/TMP-Faculty-Attendance-Integration/dtr-workspace-20261009/jsdom-register.mjs --test frontend/case-editor/dtr-review.test.mjs frontend/case-editor/dtr-admin-hours.test.mjs
python -B manage.py check --settings=quitizz_validation_settings
python -B manage.py makemigrations --check --dry-run --settings=quitizz_validation_settings
python -B manage.py migrate --plan --settings=quitizz_validation_settings
git diff --check
```

Results are finalized below after execution. Expected negative-path HTTP permission/CSRF/expired/invalid-form warnings are test stimuli. Git's LF-to-CRLF notices reflect the existing checkout policy, not whitespace failures. InnoDB-only skips are not InnoDB acceptance. CSSOM/numerical focus/contrast and jsdom tests are not browser pixels, mobile/projector, real reconnect, audio or staging acceptance.

During validation, the first 16 focused tests had one new assertion using the wrong audit field (`metadata` instead of `metadata_json`); this was corrected. The first full 290-test run had 284 passes, two failures and four deliberate InnoDB skips. One failure was the obsolete assertion that a new automatic launch snapshots version 1; it now asserts the version-2 launch contract while historical fixtures stay version 1. The other checked `admitted` before deliberately delayed transport-accept test work finished. The helper now awaits the actual admission task with a bounded timeout before asserting no output/admitted state; all revocation, HTTP-denial and constant-wakeup assertions remain. Consumer/application authorization code is unchanged. Final rerun evidence supersedes these intermediate failures; totals from overlapping runs are not added.

The next complete run (291 tests, 325.854s) passed both corrections: **286 passed, 1 failure, 4 expected InnoDB-only skips, 0 errors**. The remaining failure was the guide's exact wording assertion, `Open Question starts its server timer`, lost when expanding the Phase 4 explanation. That sentence was restored while retaining simultaneous prompt/choice opening and navigation instructions. Only this guide text changed afterward; the complete suite is not represented as rerun after that wording correction. The focused guide result below closes that assertion separately.

| Check | Executed result |
| --- | --- |
| Complete QuiTizz + focused shared Django | 291 total: 286 passed, 1 guide wording failure corrected afterward, 4 deliberate InnoDB skips, 0 errors. |
| Focused guide rerun after wording correction | 10/10 passed, 0 failures/errors/skips, 2.744s; includes the exact previously failing QuiTizz guide assertion and all 9 Faculty guide tests. Full suite was not rerun after this text-only correction. |
| New Phase 4 Django regressions | 20/20 passed within the complete run; 2 new race tests deliberately skipped. |
| Focused shared Django | 37/37 passed within the complete run, including 20 Attendance/DTR and 1 Admin automatic-feature configuration test. |
| Final QuiTizz Node | 94/94 passed, 0 failures/skips, 2741.9808ms. |
| Retained DTR AJAX / Admin Hours Node | 27/27 passed (23 + 4), 0 failures/skips, 4445.2986ms. |
| Runtime JS syntax | All six `static/js/quitizz_*.js` passed `node --check`. |
| Python syntax / templates | AST parse for all changed/new Python; four affected templates compiled. |
| Django system check | PASS, zero issues. |
| Migration drift / plan | No changes / PASS against empty disposable SQLite; 0006 included. |
| Whitespace / index / identity | Tracked and new text whitespace PASS; index empty; unchanged expected branch/HEAD. |

The four deliberate skips are the retained dual-scheduler and scheduler/host InnoDB races, plus new scheduler/selection and selection/resume races. No real engine race was executed or passed.

Measured 1/20/100 participant query counts remain population-independent: host/player lobby domain 6/4, full lobby HTTP 40/4, open-question HTTP 42/6, accepted answer 13 (14 including commit callback). Navigator adds one bounded host-state query over the earlier baseline. Host/player WebSocket forwarding stays **0/0 queries**. In-memory 100-socket and 100-request recovery tests pass; they are not real Redis/network throughput or MariaDB performance acceptance.

## Exact unstaged inventory

30 paths: 24 modified tracked files and 6 new files. `M` means unstaged tracked modification; `??` means untracked. Index remains empty.

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/faculty_portal/help_guide.py
 M apps/quitizz/automation.py
 M apps/quitizz/forms.py
 M apps/quitizz/gameplay.py
 M apps/quitizz/models.py
 M apps/quitizz/services.py
 M apps/quitizz/tests.py
 M apps/quitizz/tests_automation.py
 M apps/quitizz/tests_gameplay.py
 M apps/quitizz/tests_realtime.py
 M apps/quitizz/urls.py
 M apps/quitizz/views.py
 M frontend/quitizz/accessibility.test.mjs
 M frontend/quitizz/presentation.test.mjs
 M static/css/quitizz.css
 M static/js/quitizz_host.js
 M static/js/quitizz_presentation.js
 M templates/quitizz/base.html
 M templates/quitizz/host.html
 M templates/quitizz/launch.html
 M templates/quitizz/projector.html
?? apps/quitizz/migrations/0006_phase4_presentation_navigation.py
?? apps/quitizz/phase4.py
?? apps/quitizz/tests_phase4.py
?? apps/quitizz/tests_phase4_concurrency.py
?? docs/QUITIZZ_PHASE4_REVIEW.md
?? frontend/quitizz/phase4.test.mjs
```

## Pending acceptance

Independent full diff review; separately authorized disposable MariaDB migration/index/locking/load tests; real Redis fanout/protection/outage/restart; actual browsers/mobile/projector16:9/keyboard/reduced motion/manual awarding/reconnect/audio and stale control recovery. Existing inherited Attendance/Admin full-suite issues remain open; only the listed focused shared tests are exercised here. Keep Automatic behind its existing availability gate until operations accepts required schema, scheduler and shared protection. No deployment/startup action is authorized in this implementation gate.

Final source/diff review: all 30 modified/new paths inspected; no outstanding implementation blocker or known failing assertion after the focused guide correction. No protected application module, secrets/environment, repository logs, unrelated artifact or unresolved conflict marker is included. Index stays empty and HEAD/branch remain unchanged. **Verdict B - Complete with non-blocking observations; ready for independent review.** Operational acceptance remains pending. No staging, commit, push, deployment, restart or persistent migration occurred.
