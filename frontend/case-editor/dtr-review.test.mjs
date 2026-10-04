import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../../static/faculty_attendance/dtr_review.js", import.meta.url), "utf8");
const route = "/admin-portal/faculty-attendance/dtr/";

function classList(...initial) {
  const values = new Set(initial);
  return {add: (...items) => items.forEach((item) => values.add(item)), remove: (...items) => items.forEach((item) => values.delete(item)), contains: (item) => values.has(item)};
}

function response(payload, {status = 200, contentType = "application/json"} = {}) {
  return {status, ok: status >= 200 && status < 300, headers: {get: (name) => name === "content-type" ? contentType : null}, json: async () => payload};
}

function page(fetchImpl) {
  const listeners = {};
  const inlineError = {textContent: "", classList: classList("alert", "alert-danger", "d-none")};
  const live = {textContent: ""};
  const card = {scrollIntoView: () => {}, focus: () => {}};
  const detail = {innerHTML: "Old total"};
  const workspace = {innerHTML: "Old workspace"};
  const cutoff = {value: "1:1:2026-01-05:2026-01-05"};
  const row = {outerHTML: "old summary"};
  const selector = {value: "4"};
  const root = {dataset: {dtrUrl: route}, setAttribute: () => {}, removeAttribute: () => {}};
  const buttons = {adjustment: {disabled: false}, admin_hours: {disabled: false}, remove_adjustment: {disabled: false}, finalize: {disabled: false}};
  const forms = {};
  for (const action of ["adjustment", "admin_hours", "remove_adjustment", "finalize"]) {
    forms[action] = {
      action: {tagName: "INPUT", name: "action"}, // Simulates browser named-property shadowing.
      getAttribute: (name) => name === "action" ? route : null,
      querySelector: () => buttons[action],
      setAttribute: () => {}, removeAttribute: () => {},
      closest: (query) => query.includes("form[data-dtr-ajax]") ? forms[action] : null,
      actionValue: action,
    };
  }
  const document = {
    querySelector: (query) => query === "[data-dtr-review]" ? root : null,
    getElementById: (id) => ({"dtr-live": live, "dtr-inline-error": inlineError, "faculty-dtr-card": card, "faculty-dtr-detail": detail, "dtr-summary-faculty-4": row, "faculty-select": selector, "cutoff-select": cutoff, "dtr-workspace": workspace}[id] || null),
    addEventListener: (name, listener) => { listeners[name] = listener; },
  };
  class FormData { constructor(form) { this.form = form; } get(name) { return name === "action" ? this.form.actionValue : null; } }
  const historyState = {url: ""};
  const window = {fetch: async (requestUrl, options) => fetchImpl(requestUrl, options), location: {pathname: route, search: "?publication=1&faculty=4&remove=22&version=1", href: `http://127.0.0.1:8012${route}`}, history: {replaceState: (_state, _title, nextUrl) => { historyState.url = nextUrl; }}};
  vm.runInNewContext(script, {window, document, FormData, URLSearchParams, fetch: window.fetch, console});
  return {forms, listeners, inlineError, detail, row, buttons, historyState, cutoff, selector, workspace, live};
}

async function submit(pageState, action) {
  const event = {target: pageState.forms[action], preventDefault: () => {}};
  await pageState.listeners.submit(event);
}

for (const action of ["adjustment", "admin_hours", "remove_adjustment", "finalize"]) {
  test(`DTR AJAX ${action} posts to the literal DTR route despite name=action`, async () => {
    const calls = [];
    const state = page((requestUrl, options) => {
      calls.push({requestUrl, options});
      return response({ok: true, message: `${action} saved`, faculty_id: 4, faculty_html: "Updated total", summary_row_html: "Updated R2"});
    });
    assert.equal(typeof state.listeners.submit, "function");
    await submit(state, action);
    assert.equal(calls.length, 1, state.inlineError.textContent);
    assert.equal(calls[0].requestUrl, route);
    assert.equal(calls[0].options.method, "POST");
    assert.equal(calls[0].options.body.get("action"), action);
    assert.equal(state.detail.innerHTML, "Updated total");
    assert.equal(state.row.outerHTML, "Updated R2");
    assert.equal(state.inlineError.classList.contains("d-none"), true, state.inlineError.textContent);
    assert.equal(state.historyState.url.includes("version="), false);
    if (action === "remove_adjustment") {
      assert.equal(state.historyState.url, `${route}?publication=1&faculty=4#checker-entry`);
    }
  });
}

test("DTR AJAX non-JSON and network failures show visible no-save-confirmation errors", async () => {
  const calls = [];
  const nonJson = page((requestUrl, options) => {
    calls.push({requestUrl, options});
    return response({}, {status: 502, contentType: "text/html"});
  });
  assert.equal(typeof nonJson.listeners.submit, "function");
  await submit(nonJson, "adjustment");
  assert.equal(calls[0].requestUrl, route);
  assert.equal(nonJson.inlineError.classList.contains("d-none"), false);
  assert.match(nonJson.inlineError.textContent, /No record was confirmed saved/);
  assert.equal(nonJson.detail.innerHTML, "Old total");

  const network = page(async () => { throw new Error("network offline"); });
  await submit(network, "finalize");
  assert.equal(network.inlineError.classList.contains("d-none"), false);
  assert.match(network.inlineError.textContent, /network offline/);
});

test("changing cutoff drops the stale publication/faculty and refreshes both selectors and DTR", async () => {
  const calls = [];
  const state = page((url) => {
    calls.push(url);
    return response({ok:true, message:"Loaded", faculty_id:7, workspace_html:"New cutoff selectors, summary and DTR"});
  });
  state.cutoff.value = "1:1:2026-01-12:2026-01-12";
  await state.listeners.change({target:{matches: q => q === "[data-dtr-cutoff-select]"}});
  const params = new URLSearchParams(calls[0].split("?")[1]);
  assert.equal(params.get("cutoff"), state.cutoff.value);
  assert.equal(params.has("publication"), false);
  assert.equal(params.has("faculty"), false);
  assert.equal(params.has("remove"), false);
  assert.equal(params.get("reset_faculty"), "1");
  assert.equal(state.workspace.innerHTML, "New cutoff selectors, summary and DTR");
  assert.match(state.historyState.url, /faculty=7/);
  assert.equal(state.historyState.url.includes("reset_faculty"), false);
});

test("a slower old faculty response cannot overwrite the newest selection", async () => {
  const pending = [];
  const state = page(() => new Promise(resolve => pending.push(resolve)));
  const target = value => ({value, matches:q => q === "[data-dtr-faculty-select]"});
  const first = state.listeners.change({target:target("4")});
  const second = state.listeners.change({target:target("7")});
  pending[1](response({ok:true, faculty_id:7, workspace_html:"Latest faculty"}));
  await second;
  pending[0](response({ok:true, faculty_id:4, workspace_html:"Stale faculty"}));
  await first;
  assert.equal(state.workspace.innerHTML, "Latest faculty");
  assert.match(state.historyState.url, /faculty=7/);
});

test("Review DTR submits the current cutoff/faculty through GET without the stale publication", async () => {
  const calls = [];
  const state = page((url, options) => {
    calls.push({url, options});
    return response({ok:true, faculty_id:7, workspace_html:"Reviewed faculty"});
  });
  state.selector.value = "7";
  let prevented = false;
  await state.listeners.submit({
    target:{closest:q => q.includes("form[data-dtr-selection]")},
    preventDefault:() => {prevented = true;},
  });
  assert.equal(prevented, true);
  assert.equal(calls.length, 1);
  const query = new URLSearchParams(calls[0].url.split("?")[1]);
  assert.equal(query.get("faculty"), "7");
  assert.equal(query.get("cutoff"), state.cutoff.value);
  assert.equal(query.has("publication"), false);
  assert.equal(calls[0].options.method, undefined);
  assert.equal(state.workspace.innerHTML, "Reviewed faculty");
});

test("cutoff loading failure retains the current page and shows an inline error", async () => {
  const state = page(() => response({}, {status:502, contentType:"text/html"}));
  await state.listeners.change({target:{matches:q => q === "[data-dtr-cutoff-select]"}});
  assert.equal(state.workspace.innerHTML, "Old workspace");
  assert.equal(state.inlineError.classList.contains("d-none"), false);
  assert.equal(state.selector.disabled, false);
});

test("save is withheld while the selection is loading and duplicate saves are withheld", async () => {
  const pending = [];
  const calls = [];
  const state = page((url, options) => {
    calls.push(options);
    return new Promise(resolve => pending.push(resolve));
  });
  const loading = state.listeners.change({target:{matches:q => q === "[data-dtr-cutoff-select]"}});
  await submit(state, "adjustment");
  assert.equal(calls.length, 1);
  assert.match(state.live.textContent, /finish loading/);
  pending[0](response({ok:true, faculty_id:4, workspace_html:"Loaded"}));
  await loading;
  const saving = submit(state, "adjustment");
  await submit(state, "adjustment");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].method, "POST");
  pending[1](response({ok:true, faculty_id:4, faculty_html:"Saved"}));
  await saving;
  assert.equal(state.buttons.adjustment.disabled, false);
});
