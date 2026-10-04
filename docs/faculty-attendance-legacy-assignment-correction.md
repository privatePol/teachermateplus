# Existing assignment attribution correction — October 4, 2026

Local implementation on `integration/faculty-attendance-main`, base HEAD
`2c1e16b0f79771cae953ed825189b9e8e9a5cd92`. Staging browser acceptance of that
base FAILED. This document does not certify staging acceptance of the correction.

## Confirmed cause and origin limits

An exact existing October 1 meeting takes precedence in `resolve_occurrence_faculty`.
Its saved NULL faculty/coverage is returned as unresolved even though a single
active, primary, accepted academic assignment has existed since July. A supplementary
coverage interval starting October 2 cannot match that meeting. The encoding view
then omits the saved row; the manifest itself is not empty. Both supplied structures
were reproduced before editing in disposable SQLite. The 168 manifest rows and
107 distinct assigned offerings are different counts; this work does not establish
an individual cause for every affected staging offering.

Creation-path trace:

| Path | Boundary and evidence |
| --- | --- |
| `CoverageInitializationService.apply` | Takes the submitted initialization boundary; creates coverage linked to the existing assignment through `CoverageService.create`. End is midnight on `term.end_date + 1 day`. Earlier meetings are outside the recovery query. No implicit use of July assignment/acceptance dates. New initialization operations now append an explicit supplementary-origin audit. |
| Direct attendance coverage form / `CoverageService.create` | Explicit submitted boundary/end, overlap validation, creation audit and impacted-meeting reconciliations. Manual origin stays binding. |
| `AcademicCoverageIntegrationService.resolve` | Explicit effective time on an assignment reconciliation; creates/supersedes coverage or closes it for unassignment, with resolved change evidence. |
| College `sync_assignment` | Explicit validated actual teaching boundary; closes previous owner, creates next interval until the next coverage or midnight after term end, refreshes only unprotected meetings. Saved findings/history remain protected. |
| Assignment create/reactivate/replace/unassign/accept and import | Academic services/views call the integration bridge. Import parses `attendance_effective_at`; acceptance alone is not a teaching-effective date. Enabled-module mutations record change evidence. |
| Adoption / guarded historical recovery | Does not create an inferred teaching interval. Uses an existing dated interval and retains frozen meeting/manifest evidence. |
| Migrations and bootstrap | Coverage migrations create schema/permissions, not a coverage-data bootstrap. No additional production coverage creator was found in repository apps/scripts. Out-of-repository scripts remain unknown. |

The October 2 stored boundary could come from initialization, a manual operation,
a real dated academic change, or an external bootstrap. The supplied inspection
does not include coverage reason/creator/creation audits or term end. If staging
term end is October 30, October 31 midnight is the correct exclusive end; if term
end is October 31, that interval omits October 31 and needs a separately reviewed
source correction. This implementation never silently extends an existing end.

## Correction and recovery

The shared reader first respects saved result attribution, substitutions, adoption,
saved meeting ownership and exact dated coverage. For a NULL initial snapshot or
an unmaterialized occurrence, it can use one retained, active, primary, accepted,
scope-consistent assignment already established by the requested date. Assignment
and acceptance timestamps prove prior existence only; neither becomes a teaching
start. Multiple assignments, inactive/declined faculty, foreign scope, differing
coverage owner/source, a manual/synchronized origin, an explicit dated event,
or a recorded meeting-history decision prevents this fallback. A future single
supplementary interval with the same source assignment alone is not an activation
date. Tagged initialization is supplementary, but genuine academic changes remain
authoritative. No fallback past a coverage end or outside term dates.

Daily preview, existing OPEN list attribution, exception saves, cutoff publication,
DTR and term monitoring use this reader. Evidence is loaded in batches for cutoff
and list review and reread under writer locks. Existing meetings/manifest snapshots,
combined links, observations, exception revisions, publications and finals are not
rewritten. New publication snapshots record established-assignment provenance.
No persistent repair or migration is required for the supplied no-change/no-origin
structure. Resume the existing OPEN list; rebuilding it is unnecessary.

If origin inspection finds an explicit real teaching date, retain it and use the
existing authorized dated-change/correction workflow. If it finds old initialization
audits that cannot be distinguished from a manual boundary, the conservative reader
keeps those records unresolved. Do not backdate all 107 records, append synthetic
origin audits, or change coverage dates without reviewing the returned evidence and
separate authorization. No staging recovery/write was executed or prepared as an
automatic bulk repair.

## Narrow staging origin inspection — prepared, NOT executed

This prints only the two supplied offerings' relevant provenance. Run only on
staging with the existing application interpreter. It enforces a read-only MariaDB
transaction and validates database/scope identity. It does not print environment
variables or credentials, migrate, grant permissions, repair, deploy or restart.

```bash
APP=/opt/teachermateplus_staging
OWNER=teachermateplus_staging
ENV=/etc/teachermateplus_staging/teachermateplus_staging.env
DB=teachermateplus_staging_db
PY="$APP/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$APP/venv/bin/python"; fi
test -x "$PY" || { echo 'STOP: existing application interpreter not found'; exit 1; }
cd "$APP" || exit 1
sudo -u "$OWNER" env TMP_INSPECTION_ENV="$ENV" TMP_EXPECTED_DB="$DB" "$PY" -B - <<'PY'
import os, json
from dotenv import load_dotenv
assert load_dotenv(os.environ['TMP_INSPECTION_ENV'], override=True), 'STOP: staging environment not loaded'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection, transaction
from django.db.models import Q
from apps.academics.models import CourseOffering, FacultyAssignment
from apps.faculty_attendance.models import FacultyCoverage, CoverageReconciliation, TeachingMeeting, MeetingReconciliation
from apps.auditlog.models import AuditLog
assert connection.vendor == 'mysql', 'STOP: expected MariaDB/MySQL'
assert connection.settings_dict['NAME'] == os.environ['TMP_EXPECTED_DB'], 'STOP: unexpected database'
with connection.cursor() as cursor:
    cursor.execute('SET TRANSACTION READ ONLY')
with transaction.atomic():
    offerings = list(CourseOffering.objects.filter(pk__in=[103,67], tenant_id=2,
        campus_id=2, academic_year_id=1, term_id=1).select_related('term'))
    assert {o.pk for o in offerings} == {103,67}, 'STOP: supplied offering scope differs'
    def show(label, rows):
        print(label, json.dumps(list(rows), default=str, sort_keys=True))
    print('TERM_DATES', [(o.pk, o.term.start_date, o.term.end_date) for o in offerings])
    show('ASSIGNMENTS', FacultyAssignment.objects.filter(offering_id__in=[103,67]).values(
        'id','offering_id','tenant_id','campus_id','faculty_user_id','is_active','is_primary',
        'response_status','assigned_at','accepted_at'))
    coverages = FacultyCoverage.objects.filter(offering_id__in=[103,67])
    show('COVERAGE_ORIGINS', coverages.values('id','offering_id','tenant_id','campus_id',
        'department_id','faculty_user_id','source_assignment_id','effective_from','effective_until',
        'reason','created_by_id','created_at','updated_at'))
    changes = CoverageReconciliation.objects.filter(offering_id__in=[103,67])
    show('ASSIGNMENT_CHANGES', changes.values('id','offering_id','source_assignment_id','event_type',
        'source_reference','prior_faculty_id','proposed_faculty_id','effective_at','status',
        'reason','resolution_reason','created_by_id','resolved_by_id','created_at','resolved_at'))
    meetings = TeachingMeeting.objects.filter(pk__in=[509,354], tenant_id=2,campus_id=2)
    show('MEETINGS', meetings.values('id','source_kind','faculty_snapshot','coverage_id','faculty_user_id'))
    meeting_changes = MeetingReconciliation.objects.filter(meeting_id__in=meetings)
    show('MEETING_CHANGES', meeting_changes.values('id','meeting_id','source_type','source_reference','status','decision'))
    refs = (Q(entity_type='FacultyCoverage',entity_id__in=[str(c.pk) for c in coverages])
        | Q(entity_type='CoverageReconciliation',entity_id__in=[str(c.pk) for c in changes])
        | Q(entity_type='FacultyAssignment',entity_id__in=['286','126']))
    show('ORIGIN_AUDITS', AuditLog.objects.filter(refs,tenant_id=2,campus_id=2).order_by('created_at','pk').values(
        'id','action','entity_type','entity_id','actor_user_id','created_at','before_json','after_json','metadata_json'))
PY
```

## Local evidence

Runtime artifacts: `D:\codex-runtime\TMP-Faculty-Attendance-Integration\legacy-boundary-20261004`.
`before.log` records both unchanged-source reproductions. `after.html` is the
authenticated Django-rendered two-class list (synthetic records matching the
supplied relationships, not a live staging query). It displays Ramirez/Joy at
19:00–20:30 and Chavez/Gil at 07:30–09:00 with A/N, decimal missed hours and L/E
controls, Present by default, and no Present form. `final-focused.log` records
the focused verification. No connected browser was available (`iab` unavailable;
browser inventory empty), so visual/browser interaction acceptance is pending.

Final validation: 58 distinct passing cases, not summed overlapping run counts:
`final-focused.log` 50/50, `batch-final.log` 17/17 (ten overlaps),
`monitor-final.log` one additional case. Django check reports zero issues;
`makemigrations --check --dry-run` reports No changes detected. No new migration
exists, so no persistent `migrate` is needed or authorized. Earlier failed
development runs remain in the external logs and are superseded by these results.

Focused checks cover both 1.50-hour cases on existing OPEN rows, missing coverage
and unmaterialized occurrences, 15-minute lateness producing 1.25 hours while
preserving prior final, legacy combined sections counted once, dated closure,
exclusive end, manual/synchronized boundaries and ownership conflict, unassigned
and scope-conflicting records, direct DENY, existing initialization/adoption,
encoding/correction, leave and real replacement/unassignment behavior. No full
application suite, persistent MariaDB/InnoDB races, staging browser, deployment,
mobile/PDF/printer or unrelated faculty-grading flow acceptance is claimed.

Next: inspect exact diff and origin evidence; obtain a separately authorized release
gate; then verify the October 1 existing Fairview OPEN list, both faculty cases,
exceptions and cutoff/DTR in the staging browser. Preserve earlier unresolved
InnoDB/printing/older-baseline issues in HANDOFF.md.
## Focused later-unassignment correction - October 4, 2026

Historical scope: the 18-pass run in this section covered the College bridge only.
The original resolve/close path remained defective until the completion below.

The reviewer's disposable structure was reproduced before the fix: an October 1
90-minute legacy class with NULL meeting faculty/coverage and a valid saved
15-minute lateness result became unready after authorized October 10 unassignment.
Saved attribution correctly stayed SAVED_RESULT, but readiness omitted that evidence.
The confirmed reproduction failed at ready=True and showed a DTR source-change
blocker. This is structural local evidence, not a query of actual staging records.

Cutoff readiness now passes its existing current result and immutable revision to
the shared reader. Only a matching validated saved owner can preserve an earlier
baseline across resolved, scope-consistent later unassignment of that same retained
assignment. Retirement, pending reconciliation, foreign scope, genuine owner
conflicts, incomplete/mismatched result revisions and changes on/before/within the
recorded class remain protected. No per-row SELECT or lock-order change was added.
Other readiness callers without supplied saved-result evidence keep their existing
conservative behavior. No new staff workflow, schema migration or repair.

Final focused run: **18 passed, zero failures/errors/skips**, 24.702s; nine new cases
and nine directly affected existing cases. The earlier one-case successful run and
eight-case successful run overlap and are not added. Before-fix confirmed run had
one intentional regression failure; its earlier fixture-only failure is retained
separately. The final tests verify 1.25 net hours, 0.25 lateness deduction, unchanged
saved exception/history and frozen manifest, byte-identical complete existing final,
publication/finalization retry reuse, at/after-boundary rejection and zero-query
primed readiness. Scope includes tenant/campus/department, conflict, revision,
reconciliation, retirement, source-waiting, substitution and unassigned exclusions.

Exact final PowerShell command (from the explicit integration worktree):

```powershell
$taskTests = @('test_later_unassignment_keeps_saved_exception_ready_and_final_unchanged', 'test_saved_exception_after_unassignment_has_no_historical_fallback', 'test_saved_exception_does_not_bypass_on_or_before_class_change', 'test_saved_exception_does_not_bypass_pending_reconciliation', 'test_saved_exception_does_not_bypass_conflicting_ownership', 'test_saved_exception_does_not_bypass_foreign_scope', 'test_saved_exception_requires_matching_current_revision', 'test_saved_exception_readiness_reuses_batched_evidence_without_queries', 'test_saved_exception_does_not_bypass_retirement_or_source_waiting', 'test_saved_exception_republication_preserves_prior_final_and_manifest', 'test_genuine_boundaries_conflicts_and_end_remain_binding', 'test_unassigned_wrong_scope_and_direct_deny') | ForEach-Object { "apps.faculty_attendance.tests_legacy_assignments.LegacyAssignmentTests.$_" }
$taskTests += @('apps.faculty_attendance.tests_faculty_cutoffs.FacultyCutoffTests.test_conflicting_combined_coverage_blocks_every_dated_candidate', 'apps.faculty_attendance.tests_faculty_cutoffs.FacultyCutoffTests.test_department_and_tenant_scope_fail_closed', 'apps.faculty_attendance.tests.FacultyAttendanceFoundationTests.test_cutoff_substitute_requires_matching_revision_checked_result_attribution', 'apps.faculty_attendance.tests.FacultyAttendanceFoundationTests.test_cutoff_substitute_does_not_bypass_pending_reconciliation', 'apps.faculty_attendance.tests.FacultyAttendanceFoundationTests.test_cutoff_substitute_recovery_preserves_scope_and_direct_deny', 'apps.faculty_attendance.tests_exceptions_only.ExceptionsOnlyTests.test_unassigned_does_not_block_or_add_hours')
& 'D:\codex-runtime\TMP-Faculty-Attendance-Integration\faculty-cutoff-20261004\run-django.ps1' later-unassignment-final test @taskTests --keepdb --noinput --verbosity=2
& 'D:\codex-runtime\TMP-Faculty-Attendance-Integration\faculty-cutoff-20261004\run-django.ps1' later-unassignment-check check
git diff --check
```

Reproduction command: same wrapper with log name
`later-unassignment-before-confirmed`, `test` label
`apps.faculty_attendance.tests_legacy_assignments.LegacyAssignmentTests.test_later_unassignment_keeps_saved_exception_ready_and_final_unchanged`
and `--keepdb --noinput --verbosity=2`; one expected failure before source correction.
Logs are external under `D:\codex-runtime\TMP-Faculty-Attendance-Integration\faculty-cutoff-20261004`:
`later-unassignment-{before,before-confirmed,after,focused,final,check}.log`.
Reusable disposable DB: that directory's `test.sqlite3`; no migrations were pending
or applied in these runs. Python 3.14 uses -B with dotenv/bytecode disabled.
PowerShell wraps normal Django stderr in NativeCommandError text; terminal exit
codes and Django summaries establish actual outcomes, not that wrapper text.

Final combined manifest remains the pre-existing 17-file manifest in HANDOFF.
No persistent database, coverage backdating, permission expansion, staging repair,
Git staging/commit/push, deployment or restart. Actual staging/browser/print and
InnoDB acceptance remain pending; SQLite is not concurrency proof.

## Original authorized path completion - October 4, 2026

Before this correction, the new regression called the actual
`AcademicCoverageIntegrationService.resolve` -> `CoverageService.close` path and
asserted its persisted `FACULTY_ATTENDANCE_COVERAGE_CLOSED` audit and October 10
boundary. It failed October 1 readiness with `MEETING_RECONCILIATION_PENDING` and
the DTR source-change blocker: **one expected failure**, 2.595s, exit 1 in
`before-original.log`. Coverage fields were not edited to simulate unassignment.

The shared reader now retains closure audit rows from its existing locked/batched
query. After validating the saved result/revision and same assignment, faculty,
offering and tenant/campus/department later-unassignment evidence for the whole
recorded class, it accepts this closure origin only if every closure audit matches
the coverage end and reconciliation effective boundary, tenant/campus, resolution
actor, and the reconciliation's creation-to-resolution time window. It does not
globally allow the tag or skip remaining manual/synchronization origin checks.
No extra query, changed lock order, source repair, schema or workflow was added.

Final single focused run: **22 passed, zero failures/errors/skips**, 42.193s,
exit 0 in `final-original-path.log`. The earlier three-case development run
(`focused-final.log`, 17.769s) overlaps and is not added. Prior suites were not
rerun or summed. Both authorized paths retain October 1 readiness, **1.25 net
hours / 0.25 lateness deduction**, byte-identical existing final, exception history
and frozen manifest; locked publication/finalization retries reuse existing rows.
Both paths reject classes at/after unassignment, before/during-class changes,
ownership/scope conflicts, pending reconciliation, retirement/source waiting and
mismatched result revisions, and both pass zero-query primed readiness.
Audit correlation cases reject missing/malformed/naive/wrong boundary payloads,
wrong scope/actor or audit time, invalid event/assignment/faculty/offering/resolution
identity, extra uncorrelated closure audits and manual/sync origins.

Disposable runtime: `D:\codex-runtime\TMP-Faculty-Attendance-Integration\later-original-20261004`.
Only its copied `test.sqlite3` was used, via `original_path_test_settings.py` and
the `run-django.ps1` wrapper; Python 3.14 `-B`, dotenv/bytecode disabled.
Tests reported no migrations to apply. `original-path-check.log`: Django check
passed with zero issues. Tracked/new-file whitespace checks passed; migration
file inventory is unchanged. No persistent DB action or release/runtime action.

Exact commands from the integration worktree:

```powershell
& 'D:\codex-runtime\TMP-Faculty-Attendance-Integration\later-original-20261004\run-django.ps1' before-original test apps.faculty_attendance.tests_legacy_assignments.LegacyAssignmentTests.test_original_path_later_unassignment_keeps_saved_exception_ready_and_final_unchanged --keepdb --noinput --verbosity=2
& 'D:\codex-runtime\TMP-Faculty-Attendance-Integration\later-original-20261004\run-focused.ps1'
& 'D:\codex-runtime\TMP-Faculty-Attendance-Integration\later-original-20261004\run-django.ps1' original-path-check check
git diff --check
```

`run-focused.ps1` invokes the same wrapper with log name `final-original-path`,
`test`, the following 22 labels under
`apps.faculty_attendance.tests_legacy_assignments.LegacyAssignmentTests`, and
`--keepdb --noinput --verbosity=2`:

```text
test_later_unassignment_keeps_saved_exception_ready_and_final_unchanged
test_saved_exception_after_unassignment_has_no_historical_fallback
test_saved_exception_does_not_bypass_on_or_before_class_change
test_saved_exception_does_not_bypass_pending_reconciliation
test_saved_exception_does_not_bypass_conflicting_ownership
test_saved_exception_does_not_bypass_foreign_scope
test_saved_exception_requires_matching_current_revision
test_saved_exception_readiness_reuses_batched_evidence_without_queries
test_saved_exception_does_not_bypass_retirement_or_source_waiting
test_saved_exception_republication_preserves_prior_final_and_manifest
test_genuine_boundaries_conflicts_and_end_remain_binding
test_unassigned_wrong_scope_and_direct_deny
test_original_path_later_unassignment_keeps_saved_exception_ready_and_final_unchanged
test_original_path_after_unassignment_has_no_historical_fallback
test_original_path_does_not_bypass_on_or_before_class_change
test_original_path_does_not_bypass_pending_reconciliation
test_original_path_does_not_bypass_conflicting_ownership
test_original_path_does_not_bypass_foreign_scope
test_original_path_requires_matching_current_revision
test_original_path_readiness_reuses_batched_evidence_without_queries
test_original_path_does_not_bypass_retirement_or_source_waiting
test_original_path_requires_correlated_closure_audit
```

Only six of the pre-existing 17 dirty paths changed in this completion:
`assignment_attribution.py`, `tests_legacy_assignments.py`, this report,
`HANDOFF.md`, `CHANGE_LOG.md`, `TEACHERMATEPLUS_CONTEXT.md`. The other 11 retain
their session-entry SHA256 hashes, including the saved-result/revision handoff.
Full final manifest, unchanged branch/HEAD and pending staging/browser/InnoDB
acceptance are recorded in HANDOFF. No Git staging/commit/push, deployment or restart.
