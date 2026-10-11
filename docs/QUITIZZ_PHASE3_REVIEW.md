# QuiTizz Phase 3 implementation review

## Identity and gate

Windows-local worktree `D:\codex-worktrees\TMP-QuiTizz-Attendance-Integration`, branch `integration/quitizz-faculty-attendance`, unchanged HEAD `487fb2a3a512bdeec99d5c4090f9131673e3729f`. Verified clean baseline. Implementation and disposable validation only; no staging, commit, push, deployment, service installation/restart, persistent-local migrations, dependency installation, secrets or Attendance/DTR functional changes. Original checkout/unrelated work preserved.

## Persisted flow and authority

Manual is the default for existing and newly launched sessions. Automatic requires explicit selection and default-OFF `FEATURE_QUITIZZ_AUTOMATIC_ENABLED`, inside the independent main QuiTizz availability gate and exact assigned host scope. Launch snapshots policy version 1: suspense 5s, results 7s, preparation 5s. Later configuration cannot mutate a launched policy.

The host starts and opens question 1. The database then runs ANSWERING until the question cutoff, SUSPENSE for 5s, authoritative reveal/RESULTS for 7s, PREPARING for 5s, and opens the next question. Final results go directly to COMPLETED/FINISHED and persisted final ranks, with no preparation/countdown to a nonexistent question. Existing statuses remain intact. Cancellation exposes neither an unrevealed key nor an invented champion.

`advance` uses one session transaction/row lock, backend-aware skip-locked, captured expected phase/version and a fresh server timestamp after lock acquisition. It rechecks lifecycle, mode, pause, main/automatic flags, host identity and active exact tenant/campus assigned permission/direct DENY, source ownership and shared throttle health. Writes/audit are atomic; duplicates/stale host work do not transition twice. One overdue phase executes; subsequent intervals start at actual transition time. Original expired answering cutoffs remain intact, including zero-time pause/resume. No per-second DB writes or per-player scheduler work.

The scheduler command defaults to 250ms active / 2s idle / 100 candidates. The due query uses `qts_auto_due_idx`. Active safety scans use bounded cursor batches every 2s. Detection latency grows with active batches, DB/cache delays and lock contention; no claim of instantaneous suspension. Connections are cleaned each pass, database outages use capped reconnect backoff, failed individual sessions do not stop others, and SIGTERM/SIGINT shut down cleanly. The staging systemd source retains underscore app/env paths and restarts failures after 5s; it was not installed or started.

Scheduler publication registers only on successful commit and enqueues constant wakeups through one daemon and at most 256 coalesced group entries. Queue overflow or Redis broadcast loss may drop wakeups; HTTP phase-boundary recovery replaces them. Channel publication is independently bounded by the retained 2s delivery timeout, outside the scheduler loop. WebSocket gameplay remains exactly `{"event":"sync_required"}`; no keys, question content, scores/ranks or private data are sent. Redis is not a second game-state machine. Confirmed shared-cache/throttle failure rejects public requests and safely suspends automatic sessions; no process-local production fallback is introduced. Staging/production automation also rejects a non-Redis dedicated throttle backend as unhealthy; local disposable LocMem tests remain supported. Authorized resume requires recovered protection. Broadcast loss alone does not suspend safe database automation.

## Pause, commands and snapshots

Pause persists remaining integer microseconds, clears the scheduled deadline and accumulates only active answering time through the original cutoff. Answers are rejected while paused. Resume restores a positive remaining deadline and active segment; zero remaining time retains the old cutoff and schedules immediate close. Correct-answer speed points use accumulated active time plus current segment; existing manual histories and accepted responses are never rewritten. Multiple pauses, stale prior-segment receipts and delayed recovery are covered.

Pause/Resume, Reveal Now, Next Now, End Challenge and mode controls share existing CSRF, exact owner/scope RBAC, locking and version validation. Reveal Now closes/reveals atomically; Next Now requires reveal and advances one question; paused reveal/next overrides require explicit Resume and automatic overrides reject unhealthy protection; early End Challenge cancels safely. Mode switching preserves question/position/points and does not reopen or rescore. Manual recovery still exposes Resume after automatic availability is switched off. Legacy host command URL uses `getAttribute("action")`, and FormData captures the named submitter/CSRF/version before locking.

Canonical host/player/projector snapshots include necessary phase/deadline/pause state and question identity. Lifecycle-version validation retries snapshots at most three times; no lock is held for projector/player reads. Public responses still exclude key/feedback/distribution/ranking before reveal. Frontend phase recovery uses one timer, the existing 100ms debounce, one in-flight recovery and one queued follow-up, with at most five boundary retries; browser timers do not reveal/advance or score. Existing disconnected fallback and 60s connected HTTP safety recovery remain.

## UX, audio and links

Branding remains QuiTizz / Powered by TeacherMate+ / Scan. Play. Spark. Win. Original scoped gradients, finite swirls, glass-like panels, colorful choices, large display countdown, suspense/preparation interludes, animated authoritative Top 5 score bars and final champion trophy/Top 3 improve the projector. Player interludes hide prior choices and lock answers; pause freezes visual remaining time, including first/reconnect recovery. Player countdown intervals stop at terminal state/revocation. Deterministic server rankings handle ties, zero scores, removed players and fewer than three participants; no JavaScript ranking/scoring. Original rocket trail/glow and larger burst geometry retain 12 regular / 36 finale particles, cleanup on phase exit/pagehide and reduced motion. CSS targets wide presentation layouts; real 16:9/browser rendering acceptance remains pending.

Actual sound: synthesized drum-like roll during fresh suspense and four-note champion fanfare. No applause asset is supplied or fabricated. Direct Enable Sound in the projector tab unlocks Web Audio; mute/volume preferences can be shared with the host in the same browser through BroadcastChannel, carrying preferences only. It cannot remotely unlock audio or control gameplay. Initial/reconnect/late recovery, duplicate signals, roster updates and pause/resume do not replay cues. Nodes are bounded and cleaned on pause, disconnection, revocation and pagehide. Real browser permission/audio acceptance was not executed.

Copy Join Link uses the QR's server URL builder at a host-only uncached GET, with current feature, assigned host/direct DENY, exact owner/tenant/campus, live joining, expiry and generation checks. Full signed capability stays in the fragment, never query/public state/error text. Clipboard success is claimed only after writeText succeeds; unavailable/rejected clipboard provides a readonly selectable full-link fallback. Joining closure or version changes clear the retained fallback and ignore stale fetches. A copied OS clipboard cannot be recalled; rotated/closed capability remains invalid. No capability is logged.

## Migration

New `apps/quitizz/migrations/0005_automatic_game_show.py` depends on QuiTizz `0004_quitizzparticipant_quitizzresponse_and_more`, tenants `0005_enable_existing_sis_api_feature` and swappable user graph. Session fields: mode default MANUAL, phase default NONE, nullable phase entry/deadline/pause timestamp/remaining microseconds, reason default empty, immutable JSON timing policy default empty for historical rows. Question fields: nullable active segment start and accumulated microseconds default zero. New launches snapshot policy. One composite due index; five checks constrain mode, phase, paired pause/reason/deadline, unscheduled terminals and unscheduled manual sessions; positive integer fields retain nonnegative DB checks. No existing status or question/key snapshot is changed.

Historical 0003 -> 0004 -> 0005 migration regression verifies question contents/UUIDs and Manual/NONE/empty-policy/null timing/zero elapsed defaults. Only disposable SQLite test databases were migrated. Normal local migrations were NOT applied. Attendance 0001-0020 and QuiTizz 0001-0004 are preserved. Schema changes require separately authorized operational migration; large-table index/check locking and real MariaDB constraints are unmeasured. Reversal removes timing fields and is not a safe automatic rollback. Keep schema, disable Automatic, verify suspension, then switch sessions to Manual with compatible code; do not run older code over active automatic games. See deployment documentation for safe order.

## Validation commands and evidence

Validation uses the already-existing Python 3 virtual environment and external dependency directory; no packages were installed. `quitizz_phase2b_settings` overrides normal/test database to SQLite `:memory:`, caches to LocMem, Channels to in-memory and test credentials/password hashing. This is source/disposable evidence only. Environment: `PYTHON_DOTENV_DISABLED=1`, `PYTHONDONTWRITEBYTECODE=1`, `DJANGO_ENV=local`, `DB_ENGINE=django.db.backends.sqlite3`, `DB_NAME=:memory:`; `DJANGO_LOG_DIR` points outside the repository to the Phase 3 runtime logs. `PYTHONPATH` contains the existing Phase 2B settings directory and Attendance print-dependencies directory.

```powershell
$qtPython='C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase2b-20261010\venv\Scripts\python.exe'
& $qtPython -B manage.py test apps.quitizz apps.core.tests_menu_performance apps.core.tests_settings apps.admin_portal.tests_roles.RolePermissionBoundaryTests apps.admin_portal.tests_users.UserRolePermissionSeparationTests apps.faculty_portal.tests_help_guide apps.faculty_attendance.tests_dtr_selection.DTRSelectionTests apps.faculty_attendance.tests_exceptions_only.AdminHoursBatchTests apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_can_store_assignment_workflow_settings apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_renders_standard_cards_for_targeted_sections apps.admin_portal.tests_assignment_acceptance.AdminFacultyAssignmentAcceptanceViewTests.test_configurable_features_saves_independent_quitizz_automatic_switch --settings=quitizz_phase2b_settings --noinput -v 1
& $qtPython -B manage.py test apps.quitizz --settings=quitizz_phase2b_settings --noinput -v 1
& $qtPython -B manage.py test apps.quitizz.tests_automation --settings=quitizz_phase2b_settings --noinput -v 1
& $qtPython -B manage.py test apps.quitizz.tests_automation_concurrency.SchedulerCommandTests --settings=quitizz_phase2b_settings --noinput -v 1
& $qtPython -B manage.py check --settings=quitizz_phase2b_settings
& $qtPython -B manage.py makemigrations --check --dry-run --settings=quitizz_phase2b_settings
& $qtPython -B manage.py migrate --plan --settings=quitizz_phase2b_settings
node --import file:///D:/codex-runtime/TMP-Faculty-Attendance-Integration/dtr-workspace-20261009/jsdom-register.mjs --test frontend/quitizz/*.test.mjs frontend/case-editor/dtr-review.test.mjs frontend/case-editor/dtr-admin-hours.test.mjs
foreach ($script in @('quitizz','quitizz_host','quitizz_play','quitizz_presentation','quitizz_projector','quitizz_realtime','quitizz_audio')) { node --check "static/js/$script.js" }
git diff --check
```

Final counts and inventory follow below. External evidence directory: `C:\Users\Lenovo\AppData\Local\Temp\tmp-quitizz-phase3-20261011`, including django-final.txt, quitizz-final-guarded.txt, automation-final.txt, automation-isolation-final.txt, scheduler-final.txt, node-combined.txt and migration-plan.txt. Earlier overlapping runs are not added. The initial DTR Node launch lacked local jsdom; the existing external resolver was then used and the suite passed without installation. Expected CSRF-denial and injected-notification warnings are intentional negative tests, not acceptance failures. Prior full Attendance/Admin inherited baseline failures remain unresolved in the earlier HANDOFF; this focused passing selection does not erase them or claim all baseline suites pass.

## Review limits and next gates

Independent review is pending. No real Redis fan-out/outage/latency, MariaDB/InnoDB contention, actual browser/mobile/clipboard/QR/projector/audio/Wi-Fi acceptance, production migration timing or deployment acceptance was performed. Two real locking tests are deliberately skipped on SQLite; do not call them PASS. Run these separately against an authorized disposable InnoDB database. No production-ready claim. Operations must monitor scheduler health/overdue sessions and confirm database/namespace identity before activation. Applause is an optional future licensed asset. Documentation/earlier unresolved baseline evidence is preserved.


## Final accepted results

| Check | Result |
| --- | --- |
| Combined then-current QuiTizz + 39 focused shared/Attendance Django checks | 263 total: 261 passed, 2 skipped, 0 failures/errors; 112.682s, exit 0. Complete QuiTizz was included at that point; final expanded suite is below. |
| Final complete QuiTizz after all backend guards | 226 total: 224 passed, 2 skipped, 0 failures/errors; 77.125s, exit 0. Includes the final protection/paused-override guards, both strengthened assertions and historical migration. Overlapping runs are not added. |
| Automation rerun after zero-remaining cutoff correction | 29/29 passed, no failures/errors/skips; 8.658s, exit 0. |
| Strengthened immediate feature-off suspension and two automatic-session isolation | 2/2 passed, 0.780s, exit 0; overlapping tests, do not add to the aggregate. |
| QuiTizz Node | 68/68 passed, no skips. |
| Retained DTR AJAX / Admin Hours Node | 23/23 and 4/4 passed; combined final Node 95/95, 2194.9801ms, exit 0. |
| Final scheduler/configuration smoke | 4/4 passed, 0.008s, including both staging/production rejection of process-local protection and local disposable support. |
| Seven affected JS syntax checks | PASS. |
| Five affected template compilations | PASS. |
| Django check | PASS, zero issues. |
| Migration drift / plan | No changes / PASS against empty disposable SQLite; no persistent-local migrate. |
| Tracked diff and new-file whitespace, new Python syntax | PASS. |
| Final identity/index/protected path review | PASS; expected branch/HEAD, empty index, all changes unstaged. |

The two skips are real MariaDB/MySQL row-lock races: scheduler/scheduler and scheduler/host. They were not run or passed on SQLite. Final strengthened assertions were executed with:

```powershell
& $qtPython -B manage.py test apps.quitizz.tests_automation.AutomationTests.test_independent_sessions_are_not_advanced apps.quitizz.tests_automation.AutomationTests.test_automatic_off_suspends_and_manual_recovery --settings=quitizz_phase2b_settings --noinput -v 1
```

Final source review corrected paused/unsafe override progression, initial player interlude countdowns and terminal timer cleanup, production-like non-Redis protection acceptance, zero-time resume cutoff extension, Manual fallback Resume visibility, player interlude/effect cleanup, manual closed-question timer visibility and asset cache invalidation. No unresolved source blocker remains. Source/disposable tests do not close operational gates. Known full-suite Attendance/Admin baseline failures below the current HANDOFF remain inherited and unresolved; none are reclassified as Phase 3 failures.

## Measured performance and limits

| Measurement | Current evidence |
| --- | --- |
| Scheduler due candidate selection | One query, bounded result; schema contains `(playback_mode, next_transition_at)` index. Real engine query-plan/throughput unmeasured. |
| 1/20/100 participant lobby domain host/player state | 5/4 queries at each population. |
| Open player domain state / accepted answer | 6 / 13 queries; answer including commit callback 14 at each population. |
| Full host/player lobby HTTP | 39/4 at 1/20/100. Full open-question HTTP: 41/6 at each population. |
| Host/player WebSocket forwarding | 0/0 database queries at 1/20/100. Admission 32/6 and heartbeat 16/3 remain bounded. |
| 100-player lifecycle recovery | 100 HTTP requests, 5 queries each, 500 total; no per-player scheduler or per-second writes. |
| Synthetic 100 responses | 100,000 points / 100 deterministic ranks. This is test data, not production throughput. |
| 100 rapid answer wakeups | One additional host HTTP recovery after retained 100ms debounce. |
| In-memory sockets | 100 authorized sockets, identical constant wakeups and clean disconnect. |
| Queue | 100 repeated enqueues coalesce, capacity enforced, one worker; commit-only publication. Real Redis latency/outage unmeasured. |
| Independent automatic games | Advancing either locked session preserves the other session version/phase; sequential SQLite evidence, not concurrent engine locking. |

## Exact final inventory and status

45 paths: 33 modified tracked, 12 new. All changes are unstaged; no untracked database/log/dependency artifacts. `git status --short --untracked-files=all`:

```text
 M CHANGE_LOG.md
 M HANDOFF.md
 M TEACHERMATEPLUS_CONTEXT.md
 M apps/admin_portal/forms.py
 M apps/admin_portal/help_guide.py
 M apps/admin_portal/tests_assignment_acceptance.py
 M apps/admin_portal/views.py
 M apps/core/services/features.py
 M apps/faculty_portal/help_guide.py
 M apps/quitizz/gameplay.py
 M apps/quitizz/models.py
 M apps/quitizz/realtime.py
 M apps/quitizz/services.py
 M apps/quitizz/tests_gameplay.py
 M apps/quitizz/urls.py
 M apps/quitizz/views.py
 M config/settings/base.py
 M docs/DEPLOYMENT_UBUNTU.md
 M frontend/quitizz/presentation.test.mjs
 M frontend/quitizz/realtime.test.mjs
 M static/css/quitizz.css
 M static/js/quitizz_host.js
 M static/js/quitizz_play.js
 M static/js/quitizz_presentation.js
 M static/js/quitizz_projector.js
 M static/js/quitizz_realtime.js
 M static/quitizz/icons.svg
 M templates/admin_portal/tools/configurable_features.html
 M templates/quitizz/base.html
 M templates/quitizz/host.html
 M templates/quitizz/launch.html
 M templates/quitizz/play.html
 M templates/quitizz/projector.html
?? apps/quitizz/automation.py
?? apps/quitizz/management/__init__.py
?? apps/quitizz/management/commands/__init__.py
?? apps/quitizz/management/commands/run_quitizz_scheduler.py
?? apps/quitizz/migrations/0005_automatic_game_show.py
?? apps/quitizz/tests_automation.py
?? apps/quitizz/tests_automation_concurrency.py
?? docs/QUITIZZ_ASSET_LICENSES.md
?? docs/QUITIZZ_PHASE3_REVIEW.md
?? frontend/quitizz/audio.test.mjs
?? ops/systemd/teachermateplus-staging-quitizz-scheduler.service
?? static/js/quitizz_audio.js
```

Tracked `git diff --stat`: 33 files, +569/-92. New files: 12 files, 1183 lines. Complete tracked/new review: 45 paths, +1752/-92 (including two empty package initializer files).

Final verdict: **B - Complete with non-blocking observations, ready for independent review.** Real Redis/MariaDB/browser/audio/operational gates remain pending, not implementation acceptance claims. No stage, commit, push, deployment, installation, restart, normal-local migration, dependency installation, Attendance/DTR functional changes, secrets/.env/repository-log edits or discarded unrelated work.
