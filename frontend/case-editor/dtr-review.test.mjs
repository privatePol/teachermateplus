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
  const row = {outerHTML: "old summary"};
  const selector = {value: "4"};
  const root = {dataset: {dtrUrl: route}, setAttribute: () => {}, removeAttribute: () => {}};
  const buttons = {adjustment: {disabled: false}, remove_adjustment: {disabled: false}, finalize: {disabled: false}};
  const forms = {};
  for (const action of ["adjustment", "remove_adjustment", "finalize"]) {
    forms[action] = {
      action: {tagName: "INPUT", name: "action"}, // Simulates browser named-property shadowing.
      getAttribute: (name) => name === "action" ? route : null,
      querySelector: () => buttons[action],
      setAttribute: () => {}, removeAttribute: () => {},
      closest: (query) => query === "form[data-dtr-ajax]" ? forms[action] : null,
      actionValue: action,
    };
  }
  const document = {
    querySelector: (query) => query === "[data-dtr-review]" ? root : null,
    getElementById: (id) => ({"dtr-live": live, "dtr-inline-error": inlineError, "faculty-dtr-card": card, "faculty-dtr-detail": detail, "dtr-summary-faculty-4": row, "faculty-select": selector}[id] || null),
    addEventListener: (name, listener) => { listeners[name] = listener; },
  };
  class FormData { constructor(form) { this.form = form; } get(name) { return name === "action" ? this.form.actionValue : null; } }
  const historyState = {url: ""};
  const window = {fetch: async (requestUrl, options) => fetchImpl(requestUrl, options), location: {pathname: route, search: "?publication=1&faculty=4&remove=22", href: `http://127.0.0.1:8012${route}`}, history: {replaceState: (_state, _title, nextUrl) => { historyState.url = nextUrl; }}};
  vm.runInNewContext(script, {window, document, FormData, URLSearchParams, fetch: window.fetch, console});
  return {forms, listeners, inlineError, detail, row, buttons, historyState};
}

async function submit(pageState, action) {
  const event = {target: pageState.forms[action], preventDefault: () => {}};
  await pageState.listeners.submit(event);
}

for (const action of ["adjustment", "remove_adjustment", "finalize"]) {
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
