# Self-hosted Faculty Case editor

Scope: Case narratives under the existing structured Manual Review feature and
ownership/lifecycle gates. MCQ stems/choices, generation and printing are unchanged.

## Dependencies and build

All 16 direct Tiptap dependencies are pinned to **3.31.3**, esbuild to **0.28.2**,
and the test-only JSDOM to **26.1.0**. `package-lock.json` pins the resolved graph.
No Pro extension, paid UI, license key, editor cloud service or runtime CDN is used.
The toolbar and bounded semantics are TMP integration; selection, merge/split,
grid mutations, history and keyboard table navigation use the upstream engine.

From this directory, with an approved Node runtime (this session used an official
portable Node 24.21.0 outside the repository):

```powershell
npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
node build.mjs
node --test compatibility.test.mjs
```

Use isolated npm user/global config and cache paths where the workstation has
private registry credentials. Do not commit `node_modules` or runtime binaries.
The frontend tests also require the existing Python environment and `nh3`;
their Python bridge invokes only the pure canonicalizer, without Django setup,
database access or logging. It explicitly disables dotenv and uses UTF-8 pipes.

`build.mjs` emits `static/vendor/tiptap/3.31.3/tmp-case-editor.bundle.js` and an
exact shipped-package inventory with original license texts in
`THIRD_PARTY_NOTICES.txt`. It rejects non-MIT shipped packages or missing notices.
The emitted bundle contains 30 MIT packages; build/test tools are not shipped.
The local lockfile also includes permissively licensed build/test dependencies
(MIT, MIT-0, BSD-2-Clause, BSD-3-Clause, ISC and Apache-2.0).
License inventory is not a blanket legal-compliance opinion.

The browser receives one local IIFE asset plus the existing external Case CSS;
`injectCSS` and table resizing are disabled. No inline toolbar scripts, runtime
imports, eval-based loader, new CSP exception or external editor request is needed.
Existing same-origin, CSRF-protected Preview remains the only editor request.
Build targets are Chrome 110, Firefox 115 and Safari 16; transpilation targets
are not evidence of browser acceptance.

## Canonical compatibility

The Python sanitizer is authoritative and retains every prior safety budget.
The additive `RICH_HTML_V1` allowlist includes `u`, `tmp-align-justify`, bounded
`tmp-indent-1` through `tmp-indent-8`, cell `tmp-valign-top/middle/bottom`, and
the `tmp-preserve` block marker for intentional spacing/paragraph semantics.
Arbitrary style, URL, event and class attributes remain disallowed. Marked
content is still subject to every size, node, depth, table and grid limit.

Opening does not rewrite the original hidden source. Changed content is exported
through compatibility adapters, not raw `getHTML()`. Captions and row-group
provenance are retained outside the upstream grid structure and restored on
export. Captions are visible but read-only. Header scope, merged/nested tables,
empty tables, list starts, Unicode and TMP LaTeX remain represented. Equivalent
inline mark ordering and anonymous required wrappers are compared semantically;
meaningful paragraph boundaries are not ignored.

A failed semantic comparison or a grid requiring upstream repair disables Save
and retains the original source. Ragged rows and overlong rowspans are therefore
not silently rectangularized. This is deliberate fail-closed compatibility, not
a claim that every HTML table is editable.

Raw Word clipboard cleanup is separate from ordinary serialization. Basic decimal
and bullet `mso-list` paragraphs are adapted with explicit starts/nesting. Unknown
numbering, images/embedded objects and native Word equations reject before insertion.
No pixel-perfect or PDF-converted Word fidelity is claimed.

## Validation boundary

### Accounting rules and numbering (2026-09-10)

Faculty presentation numbers are read-only: frozen section display order,
Cases in creation order, members in saved order, then standalone questions in
stored order within that section. A standalone-only first section stays first.
Numbers continue across sections and agree between workspace and Case detail.
Neither stable IDs nor stored positions/answers/memberships/audits/generated
snapshots are rewritten. Interleaved authoring and returning to earlier Cases
are supported; final generated item numbers remain independent.

Imported/legacy standalone questions without a placement appear exactly once
after configured sections, in stored order with continuous numbering, under
**Section assignment required**. The existing authorized Edit form selects a
frozen section; readonly contributions show no edit action. Opening workspace
or Case detail never fabricates placements. Missing placements still block
Final Submission; foreign/invalid structure is not treated as a missing placement.

Cell-only `tmp-rule-single` / `tmp-rule-double` extend `RICH_HTML_V1` with fixed
black 2px solid / 3px double bottom borders across the cell, overriding the
existing collapsed 1px grid. Remove Rule removes only this override; it does
not remove the grid, underline, alignment or spans. Mixed selections use the
upstream CellSelection iterator without its reference-cell early-return trap.
Merge/split still use the upstream grid engine: merge requires uniform outer
bottom rules and no internal rule that would disappear; ambiguous merges reject
without dispatch. Split retains a rule on the bottommost resulting cells only.
Dedicated rule commands never change row/column counts or merged spans.

Supported Word imports are explicit `td/th` inline `border-bottom` and/or
`mso-border-bottom-alt` shorthands: three tokens in any order, `solid` or `double`,
one positive width up to 6 in `pt` or `px`, and `black`, `windowtext`, `#000` or
`#000000`. These map to single/double semantics, not original pixel thickness.
`none`, `0`, `0pt`, `0px` mean no rule. Equivalent declarations must agree on
single/double/unset. A bottom shorthand duplicating an all-edge solid `border`
or `mso-border-alt` grid, or matching explicit top/left/right edges, is not inferred
to be an accounting rule (token order, equivalent decimal width spelling and
the supported black color aliases are ignored for this comparison).
Ordinary solid grids remain normalized to TMP's grid. Unsupported bottom
longhands, colors, widths, dashed/dotted/double non-bottom borders, conflicting
rule declarations, and rules on paragraphs/other wrappers reject explicitly.
Style blocks containing bottom-rule or double/dashed/dotted declarations reject
conservatively: no stylesheet cascade or Word paragraph-border inference is
implemented. External stylesheet fidelity is unsupported; no external resources
are fetched. This is not a whole-table border editor or universal Word fidelity.

Review hardening: a linear inspection scanner masks quoted strings and replaces
closed CSS comments only at safe token boundaries; it does not rewrite stored
content or join split identifiers/values. Comments before a property/colon or
between whitespace-separated tokens preserve supported rules. Split tokens,
unclosed comments/strings and unquoted escapes reject. Rule-bearing style blocks
are detected after this scan and still reject conservatively.

Later all-edge shorthands conflicting with an earlier bottom declaration reject
atomically instead of inventing an accounting rule. Reversed order (grid/none,
then an explicit supported bottom rule) is supported. Identical repeated rules
and equivalent solid grids work; conflicting repeated bottom/all-edge properties,
bottom longhands and border `!important` combinations reject. This is not a CSS
cascade engine. Zero-width or `none` style three-token shorthands also mean no
rule. Python and JavaScript use the same test-only `border-fixtures.json` cases.
Grid-width comparison removes only insignificant decimal zeros, without float
rounding; distinct supported decimal widths cannot collapse differently between
the client and server.

Uncertain Word-marked blank paragraphs, NBSP and intentional breaks now receive
the existing preserve marker rather than being deleted. Safe ASCII indentation
around block-only cell children still compacts; existing legacy server compaction
and all budgets remain. More preserved content can legitimately hit existing
limits: the synthetic padded 25-table fixture now rejects instead of losing
blanks, while the supported 14/25-table fixtures and 14-table padded variant
are covered independently. Actual failing Word clipboard HTML/text is absent;
its root cause and acceptance remain unresolved, not fixed by these fixtures.

The original padded 25-table fixture from HEAD has a leading NBSP paragraph and
trailing break paragraph per cell. Regression coverage distinguishes its raw
request and newly preserved oversized output from the historical saved canonical
representation, which must reopen and serialize unchanged. Rejection asserts the
canonical-character budget specifically, not an interchangeable generic error.

Failure diagnostics expose finite phase/category codes only (text, whitespace,
paragraph, attribute, caption, row group, geometry; UNKNOWN when no comparison
category is established); no clipboard logging or
academic-content diagnostics. Existing atomic rejection, page-local bounded
HTML/text recovery, retry/dismiss, truthful Preview status and fatal submission
blocking remain. Opening existing saved content alone never rewrites its source.

Deploy the server, rebuilt editor and versioned shared display CSS together.
Older releases strip the accounting classes: rolling back after rule-bearing
content is saved can hide/remove rules on render or subsequent save. Do not
perform an uncoordinated downgrade; retain compatible readers/writers or plan
an explicitly authorized recovery. No database migration/format-version bump,
new package, license change, CSP relaxation or safety-budget increase is needed.
Migration 0025 and package/lock/notice files are unchanged.

Case workspace/detail/Preview browser Print and Save as PDF require real browser
acceptance for border width/intersections, pagination, selection and focus. The
released questionnaire reads generated question snapshots, not rich faculty
Case narratives; monitoring reports contain summaries, not Case text. Case-aware
generation/printing remains deferred and is not implemented by these changes.

The frontend suite runs the actual editor, toolbar handlers and keyboard events
in **JSDOM**, plus repeated calls to the actual Python canonicalizer. Django Case
tests exercise authenticated Create/Edit/reopen/Preview/detail and existing
rejection, ownership, revision, XSS and Stage 6 isolation contracts.

Real Firefox/Chromium checks remain necessary: layout, focus visibility and
screen-reader announcements; selection/merge with mouse and keyboard; all toolbar
features; justified text and rich-block whitespace; CSP and zero external editor
requests; rejected Save and reload; real Word clipboard samples with 14+ tables.
The current session had no connected browser. DOM tests are not browser acceptance.

See the current HANDOFF entry for exact executed commands, results and remaining gates.
