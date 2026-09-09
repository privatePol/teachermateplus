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
