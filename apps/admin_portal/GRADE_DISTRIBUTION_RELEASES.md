# Grade Distribution v1 operator gate

This source implementation does not authorize a deployment, a normal database
migration, or publication. These remain separate operations. HTTP requests never
create release records. No release has been prepared/published outside disposable
tests by this session.

## Policy and limits

Only the inclusive interval 97–100 for saved **Period Grades**, with **All Statuses**,
can disclose count / graded denominator / percentage. The saved two-decimal grade
is authoritative; no rounding, clamping, transmutation or formula changes occur.
At least five distinct students must have eligible saved values, and each nonempty
qualifying/complementary cell must contain at least five students. Safe zero and
homogeneous groups can disclose. Existing status, source and formula suppression
must also admit the period measure. Exam, standing, Quiz, averages, extrema and
frequencies stay withheld. Custom bounds are retained exactly and withheld.

Publication rejects any cohort intersection with any earlier numeric release in
the tenant, even across campuses, classes or physical periods. Equivalent cohorts
are also blocked. One physical source can be published only once, even when its
decision is Withheld. This is intentionally restrictive: it does not attempt to
prove that combinations of arbitrary overlapping aggregates are safe. Changing
student membership, saved values/status, or relevant saved revision timestamps
invalidates a published source fingerprint; readers withhold instead of refreshing
the release. A key rotation blocks publication when old commitments cannot be
compared. Old releases remain in history, including after source changes.

Operators must first review known legacy histograms, exact-100 reports, exports
and other disclosures. **Do not attest history clearance when prior disclosure
history is unknown or unsafe.** The ledger cannot reconstruct previously downloaded
files or external knowledge. The attestation and independent approval are trusted
operational controls, not mathematical proof of absolute individual privacy.

## Separately authorized commands

After schema deployment and explicit operational authorization, prepare one source:

```powershell
python manage.py grade_distribution_release prepare --actor <preparer-user-id> --tenant <tenant-id> --offering <offering-id> --period <physical-period-id> --note "Evidence reference and prior-disclosure review" --legacy-history-cleared
```

Review the private snapshot through trusted operator tooling. The command reports
only release identity/version; it does not print private membership commitments or
unsafe counts. Publication requires a **different active superuser**, with current
monitor scope and direct DENY rechecked:

```powershell
python manage.py grade_distribution_release publish --actor <approver-user-id> --tenant <tenant-id> --release <release-id> --note "Independent source, history and confidentiality approval"
```

These commands attribute trusted terminal/database operator actions to the supplied
accounts; they do not authenticate possession of those users' credentials. Restrict
command execution and database access accordingly. Preparation alone never enables
numeric output. Changed drafts require a new prepared version. Publication compares
the complete tenant publication history again and records its digest, under a
tenant-row transaction lock. SQLite tests do not establish MariaDB/InnoDB locking
or concurrent-publication acceptance.

## Storage and readers

`admin_portal/0001_initial.py` adds two new tables only: immutable release snapshots
and separate immutable approvals. Foreign keys use PROTECT. Unique constraints
cover source/version, approval/source, and one approval per release. Check
constraints enforce reviewed legacy history, positive versions, All Statuses,
nullable withheld values, valid disclosed values and safe complementary counts.
The source lookup has a tenant/offering/physical-period index; foreign-key and
unique indexes support other lookups. No academic table or saved record is altered.

Snapshots store only aggregate decisions, scoped source identifiers, revision
fingerprint, HMAC roster commitments, commitment-key epoch and review metadata.
They contain no individual grades or student names/IDs. Membership commitments are
still private data and must never be serialized. ORM instance/queryset mutations
are blocked, including conflicting bulk upserts. This does not protect against
privileged raw SQL or an operator rewriting the database: database custody and
backups remain required operational controls.
Backups/restores must preserve both release tables and the commitment signing key.
A tenant academic-data export is not proof that disclosure history is complete;
unknown history after a restore must not receive legacy-history clearance.

Readers batch matching publications once per request and compare fresh authorized
source fingerprints before exposing frozen values. No live statistic substitutes
for a snapshot. An opaque publication epoch in signed selections forces reapply
after publication, so pagination, modal and Excel cannot silently switch releases.
The matching-publication query is capped at the captured epoch's publication ID,
including when an approval races with an in-flight request.
Current tenant/campus, supervision, ownership, RBAC and DENY remain mandatory.
Missing/unpublished/changed/unsafe releases use the same public Withheld notice;
private fingerprints, cohort commitments and admission diagnostics stay private.

Initial GETs render controls without class/grade processing. Apply uses CSRF-protected
POST; signed continuation requests reauthorize the applied selection. Filter edits
require Apply again. Clear and history/BFCache restoration make at most an options
request and never a results request. AJAX improves interaction; full bounded source
planning and current-source verification still cost work on each explicit action.
