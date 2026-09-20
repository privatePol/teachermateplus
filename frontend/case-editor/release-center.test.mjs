import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {JSDOM} from "jsdom";

const script = readFileSync(new URL("../../static/js/departmental_exam_release_center.js", import.meta.url), "utf8");

function row(kind, name, value, eligible = true) {
  const questionnaire = kind === "questionnaire";
  return `<tr data-${questionnaire ? 'release-row="questionnaire"' : 'answer-key-row="true"'}
    data-course-search="${name}" data-department-${questionnaire ? "id" : "ids"}="1"
    data-release-status="Not released" data-eligible="${eligible}">
    <td>${eligible ? `<input class="${questionnaire ? "bulk-release-selection" : "bulk-answer-key-selection"}" type="checkbox" name="selections" value="${value}">` : "Unavailable"}</td>
    <td>${name}</td></tr>`;
}

function pane(kind, body) {
  const q = kind === "questionnaire";
  const prefix = q ? "questionnaire" : "answer-key";
  const bulk = q ? "bulk" : "bulk-answer-key";
  return `<div id="${prefix}-releases-pane">
    <input id="${prefix}-course-search"><select id="${prefix}-department-filter"><option value=""></option><option value="1">Department</option></select>
    <select id="${prefix}-status-filter"><option value=""></option><option>Not released</option></select>
    <button type="button" ${q ? 'data-release-clear="questionnaire"' : 'id="answer-key-clear-filters"'}>Clear filters</button>
    <form id="${q ? "bulk-print-release-form" : "bulk-answer-key-release-form"}" data-release-ajax="true" action="/release/" method="post">
      <input id="${bulk}-select-all" type="checkbox"><table><tbody>${body}</tbody></table>
      <span id="${bulk}-selected-count"></span><span id="${bulk}-hidden-selected-count"></span>
      <span id="${q ? "questionnaire" : "bulk-answer-key"}-visible-count"></span>
      ${q ? "" : '<span id="bulk-answer-key-visible-eligible-count"></span>'}
      <button type="button" data-release-show-selected="${kind}">Show selected</button>
      <button type="button" data-release-clear-selection="${kind}">Clear selection</button>
      <button type="submit" data-review-release="${kind}">Review</button>
    </form>
    ${q
      ? '<div id="questionnaire-details-modal"><div id="questionnaire-details-body"></div></div>'
      : '<div id="answer-key-details-modal"><div id="answer-key-details-body"></div></div>'}
  </div>`;
}

function setup({
  fetch = () => Promise.reject(new Error("unmocked fetch")),
  url = "https://example.test/release/"
} = {}) {
  const html = `<!doctype html><div id="departmental-exam-release-center">
    <div id="release-center-feedback"></div><select id="exam-cycle-status"><option value="1">One</option><option value="2">Two</option></select>
    <form id="answer-key-target-form"><select id="answer-key-campus-filter"><option value="1">One</option><option value="2">Two</option></select></form>
    ${pane("questionnaire", row("questionnaire", "Course A", "11:101") + row("questionnaire", "Course B", "12:102") + row("questionnaire", "Course C", "13:103", false))}
    ${pane("answer-key", row("answer-key", "Key A", "21:201:31:1") + row("answer-key", "Key B", "22:202:32:1"))}
    <form id="action-one" data-release-ajax="true" action="/release/"><input name="action" value="release"></form>
    <form id="action-two" data-release-ajax="true" action="/release/"><input name="action" value="release"></form>
    <a href="/details/a/" data-questionnaire-details-url="/details/a/">Details A</a>
    <a href="/details/b/" data-questionnaire-details-url="/details/b/">Details B</a>
    <a href="/key-details/a/" data-answer-key-details-url="/key-details/a/">Key Details A</a>
    <a href="/key-details/b/" data-answer-key-details-url="/key-details/b/">Key Details B</a>
  </div>`;
  const dom = new JSDOM(html, {url, runScripts: "outside-only"});
  const {window} = dom;
  window.fetch = fetch;
  window.bootstrap = {Modal: {getOrCreateInstance: element => ({show() {element.dataset.open = "true";}})}};
  window.document.getElementById("answer-key-target-form").requestSubmit = function () { this.dataset.submitted = "true"; };
  window.eval(script);
  const doc = window.document;
  const click = selector => doc.querySelector(selector).click();
  const change = (selector, value, eventType = "change") => {
    const el = doc.querySelector(selector);
    if (el.type === "checkbox") el.checked = value;
    else el.value = value;
    el.dispatchEvent(new window.Event(eventType, {bubbles: true}));
  };
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  return {dom, window, doc, click, change, tick};
}

test("both tabs retain filtered selections through review and visible-only controls", () => {
  const {doc, click, change, window} = setup();
  change("#questionnaire-course-search", "Course A", "input");
  change('input[value="11:101"]', true);
  change("#questionnaire-course-search", "Course B", "input");
  change('input[value="12:102"]', true);
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "2");
  assert.equal(doc.getElementById("bulk-hidden-selected-count").textContent, "1");
  click('[data-release-show-selected="questionnaire"]');
  assert.equal(doc.querySelector('[data-course-search="Course A"]').hidden, false);
  assert.equal(doc.querySelector('[data-course-search="Course B"]').hidden, false);
  click('[data-release-show-selected="questionnaire"]');
  change("#questionnaire-course-search", "Course A", "input");
  change("#bulk-select-all", false);
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "1");
  assert.equal(doc.querySelector('input[value="12:102"]').checked, true);
  change("#bulk-select-all", true);
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "2");
  change('input[value="21:201:31:1"]', true);
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
  const form = doc.getElementById("bulk-print-release-form");
  const submitter = form.querySelector('[data-review-release="questionnaire"]');
  form.dispatchEvent(new window.SubmitEvent("submit", {bubbles: true, cancelable: true, submitter}));
  assert.deepEqual([...form.querySelectorAll('input[name="selections"]:checked')].map(x => x.value), ["11:101", "12:102"]);
  click('[data-release-clear-selection="questionnaire"]');
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "0");
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
  change("#answer-key-course-search", "Key B", "input");
  assert.equal(doc.getElementById("bulk-answer-key-hidden-selected-count").textContent, "1");
  click("#answer-key-clear-filters");
  assert.equal(doc.getElementById("bulk-answer-key-hidden-selected-count").textContent, "0");
});

test("cycle and target-campus changes clear only affected selections with notice", () => {
  const {doc, change, window} = setup();
  change('input[value="11:101"]', true);
  change('input[value="21:201:31:1"]', true);
  change("#answer-key-campus-filter", "2");
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "0");
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "1");
  assert.equal(doc.getElementById("answer-key-target-form").dataset.submitted, "true");
  assert.match(window.sessionStorage.getItem("tmp-release-center-notice"), /campus changed/i);
  change("#exam-cycle-status", "2");
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "0");
  assert.match(window.sessionStorage.getItem("tmp-release-center-notice"), /cycle changed/i);
});

test("Answer Key visible-only select and unselect retain hidden exact targets for review", () => {
  const {doc, change, window} = setup();
  change('input[value="21:201:31:1"]', true);
  change("#answer-key-course-search", "Key B", "input");
  change("#bulk-answer-key-select-all", true);
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "2");
  assert.equal(doc.getElementById("bulk-answer-key-hidden-selected-count").textContent, "1");
  change("#bulk-answer-key-select-all", false);
  assert.equal(doc.querySelector('input[value="21:201:31:1"]').checked, true);
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
  const form = doc.getElementById("bulk-answer-key-release-form");
  form.dispatchEvent(new window.SubmitEvent("submit", {
    bubbles: true, cancelable: true,
    submitter: form.querySelector('[data-review-release="answer-key"]')
  }));
  assert.deepEqual([...form.querySelectorAll('input[name="selections"]:checked')].map(x => x.value), ["21:201:31:1"]);
});

test("questionnaire department and status filters update visible counts without dropping selection", () => {
  const {doc, click, change, window} = setup();
  const second = doc.querySelector('[data-course-search="Course B"]');
  second.dataset.departmentId = "2";
  second.dataset.releaseStatus = "Scheduled";
  doc.getElementById("questionnaire-department-filter").add(new window.Option("Other", "2"));
  doc.getElementById("questionnaire-status-filter").add(new window.Option("Scheduled", "Scheduled"));
  change('input[value="11:101"]', true);
  change("#questionnaire-department-filter", "2");
  change("#questionnaire-status-filter", "Scheduled");
  assert.equal(doc.getElementById("questionnaire-visible-count").textContent, "1");
  assert.equal(doc.getElementById("bulk-hidden-selected-count").textContent, "1");
  click('[data-release-clear="questionnaire"]');
  assert.equal(doc.getElementById("questionnaire-visible-count").textContent, "3");
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "1");
});

test("details load on demand, retry after failure, ignore late responses and retain selection", async () => {
  const pending = [];
  const {doc, click, change, tick, window} = setup({fetch: url => new Promise((resolve, reject) => pending.push({url, resolve, reject}))});
  change('input[value="11:101"]', true);
  click('[data-questionnaire-details-url="/details/a/"]');
  assert.match(doc.getElementById("questionnaire-details-body").textContent, /Loading/);
  pending[0].reject(new Error("offline"));
  await tick();
  assert.match(doc.getElementById("questionnaire-details-body").textContent, /could not be loaded/);
  click("#questionnaire-details-body button");
  assert.equal(pending[1].url, "/details/a/");
  click('[data-questionnaire-details-url="/details/b/"]');
  pending[2].resolve({ok: true, text: async () => "<p>Course B details</p>"});
  await tick();
  pending[1].resolve({ok: true, text: async () => "<p>Stale A details</p>"});
  await tick();
  assert.match(doc.getElementById("questionnaire-details-body").textContent, /Course B details/);
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "1");
  doc.getElementById("questionnaire-details-modal").dispatchEvent(new window.Event("hidden.bs.modal", {bubbles: true}));
  assert.equal(doc.activeElement.textContent, "Details B");
});

test("Answer Key details keep filters and selections through retry, late responses and close", async () => {
  const pending = [];
  const {doc, click, change, tick, window} = setup({
    fetch: url => new Promise((resolve, reject) => pending.push({url, resolve, reject}))
  });
  change('input[value="21:201:31:1"]', true);
  change("#answer-key-course-search", "Key B", "input");
  click('[data-answer-key-details-url="/key-details/a/"]');
  assert.match(doc.getElementById("answer-key-details-body").textContent, /Loading Answer Key/);
  pending[0].reject(new Error("offline"));
  await tick();
  assert.match(doc.getElementById("answer-key-details-body").textContent, /could not be loaded/);
  click("#answer-key-details-body button");
  assert.equal(pending[1].url, "/key-details/a/");
  click('[data-answer-key-details-url="/key-details/b/"]');
  pending[2].resolve({ok: true, text: async () => "<p>Key B target details</p>"});
  await tick();
  pending[1].resolve({ok: true, text: async () => "<p>Stale key A details</p>"});
  await tick();
  assert.match(doc.getElementById("answer-key-details-body").textContent, /Key B target details/);
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
  assert.equal(doc.getElementById("bulk-answer-key-hidden-selected-count").textContent, "1");
  doc.getElementById("answer-key-details-modal").dispatchEvent(
    new window.Event("hidden.bs.modal", {bubbles: true})
  );
  assert.equal(doc.activeElement.textContent, "Key Details B");
});

test("Answer Key refresh keeps an open target detail coherent and reconciles selections", async () => {
  const {doc, click, change, tick, window} = setup({fetch: (url, options = {}) => {
    if (options.method === "POST") {
      return Promise.resolve({ok: true, json: async () => ({
        success: true, section: "answer-key-releases", message: "Updated"
      })});
    }
    if (url === "/key-details/a/") {
      return Promise.resolve({ok: true, text: async () => "<p>Refreshed Key A details</p>"});
    }
    return Promise.resolve({ok: true, text: async () => pane(
      "answer-key", row("answer-key", "Key A", "21:201:31:1")
    )});
  }});
  change('input[value="21:201:31:1"]', true);
  click('[data-answer-key-details-url="/key-details/a/"]');
  await tick();
  const form = doc.getElementById("bulk-answer-key-release-form");
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  await tick();
  await tick();
  assert.equal(doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
  assert.match(doc.getElementById("answer-key-details-body").textContent, /Refreshed Key A details/);
});

test("AJAX refresh reconciles stale selections and ignores older same-tab responses", async () => {
  const pending = [];
  const {doc, change, window, tick} = setup({fetch: (url, options) => {
    if (options.method === "POST") return Promise.resolve({ok: true, json: async () => ({success: true, section: "questionnaire-releases", message: "Updated"})});
    return new Promise(resolve => pending.push(resolve));
  }});
  change('input[value="11:101"]', true);
  for (const id of ["action-one", "action-two"]) {
    doc.getElementById(id).dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
    await tick();
  }
  assert.equal(pending.length, 2);
  pending[1]({ok: true, text: async () => pane("questionnaire", row("questionnaire", "Course B", "12:102"))});
  await tick();
  assert.equal(doc.getElementById("bulk-selected-count").textContent, "0");
  assert.match(doc.getElementById("release-center-feedback").textContent, /Updated|stale/);
  pending[0]({ok: true, text: async () => pane("questionnaire", row("questionnaire", "Course A", "11:101"))});
  await tick();
  assert.equal(doc.querySelector('[data-course-search="Course A"]'), null);
  assert.equal(doc.querySelector('[data-course-search="Course B"]').textContent.includes("Course B"), true);
});

async function completeDeferredPostAfter(transition, kind = "answer-key", action = null) {
  const pendingPosts = [];
  const detailGets = [];
  let refreshes = 0;
  const detailPrefix = kind === "answer-key" ? "/key-details/" : "/details/";
  const {doc, click, change, tick, window} = setup({fetch: (url, options = {}) => {
    if (options.method === "POST") {
      return new Promise(resolve => pendingPosts.push(resolve));
    }
    if (url.startsWith(detailPrefix)) {
      detailGets.push(url);
      return Promise.resolve({ok: true, text: async () => `<p>${url} current details</p>`});
    }
    refreshes += 1;
    return Promise.resolve({ok: true, text: async () => pane(
      kind, kind === "answer-key"
        ? row(kind, "Key A", "21:201:31:1") + row(kind, "Key B", "22:202:32:1")
        : row(kind, "Course A", "11:101") + row(kind, "Course B", "12:102")
    )});
  }});
  const selector = kind === "answer-key"
    ? '[data-answer-key-details-url="/key-details/a/"]'
    : '[data-questionnaire-details-url="/details/a/"]';
  click(selector);
  await tick();
  change(kind === "answer-key" ? 'input[value="21:201:31:1"]' : 'input[value="11:101"]', true);
  const form = doc.getElementById("action-one");
  form.dataset.releaseSection = kind === "answer-key"
    ? "answer-key-releases" : "questionnaire-releases";
  form.querySelector('input[name="action"]').value = action || (kind === "answer-key"
    ? "answer_key_release" : "release");
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  assert.equal(pendingPosts.length, 1);
  await transition({doc, click, change, tick, window});
  const detailCountBeforeCompletion = detailGets.length;
  pendingPosts[0]({ok: true, json: async () => ({
    success: true,
    section: form.dataset.releaseSection,
    message: "Updated"
  })});
  await tick();
  await tick();
  return {doc, detailGets, detailCountBeforeCompletion, refreshes};
}

test("deferred Answer Key POST cannot reopen a closed modal; summary still reconciles", async () => {
  const result = await completeDeferredPostAfter(async ({doc, window}) => {
    doc.getElementById("answer-key-details-modal").dispatchEvent(
      new window.Event("hide.bs.modal", {bubbles: true})
    );
    doc.getElementById("answer-key-details-modal").dispatchEvent(
      new window.Event("hidden.bs.modal", {bubbles: true})
    );
  }, "answer-key", "answer_key_revoke");
  assert.equal(result.detailGets.length, result.detailCountBeforeCompletion);
  assert.equal(result.refreshes, 1);
  assert.equal(result.doc.getElementById("bulk-answer-key-selected-count").textContent, "1");
});

test("deferred Answer Key POST cannot replace a reopened same target or a switched target", async () => {
  for (const transition of [
    async ({doc, click, tick, window}) => {
      const modal = doc.getElementById("answer-key-details-modal");
      modal.dispatchEvent(new window.Event("hidden.bs.modal", {bubbles: true}));
      click('[data-answer-key-details-url="/key-details/a/"]');
      await tick();
    },
    async ({click, tick}) => {
      click('[data-answer-key-details-url="/key-details/b/"]');
      await tick();
    }
  ]) {
    const result = await completeDeferredPostAfter(transition);
    assert.equal(result.detailGets.length, result.detailCountBeforeCompletion);
    assert.equal(result.refreshes, 1);
    assert.match(result.doc.getElementById("answer-key-details-body").textContent, /current details/);
  }
});

test("deferred POST cannot reload details after campus or cycle changes", async () => {
  const campus = await completeDeferredPostAfter(async ({change}) => {
    change("#answer-key-campus-filter", "2");
  });
  assert.equal(campus.detailGets.length, campus.detailCountBeforeCompletion);
  assert.equal(campus.refreshes, 1);
  const cycle = await completeDeferredPostAfter(async ({change}) => {
    change("#exam-cycle-status", "2");
  }, "questionnaire");
  assert.equal(cycle.detailGets.length, cycle.detailCountBeforeCompletion);
  assert.equal(cycle.refreshes, 1);
});

test("closing a modal while POST summary refresh is pending prevents a later detail reload", async () => {
  const pendingRefresh = [];
  let detailGets = 0;
  const {doc, click, tick, window} = setup({fetch: (url, options = {}) => {
    if (options.method === "POST") return Promise.resolve({ok: true, json: async () => ({
      success: true, section: "answer-key-releases", message: "Revoked"
    })});
    if (url === "/key-details/a/") {
      detailGets += 1;
      return Promise.resolve({ok: true, text: async () => "<p>Key A details</p>"});
    }
    return new Promise(resolve => pendingRefresh.push(resolve));
  }});
  click('[data-answer-key-details-url="/key-details/a/"]');
  await tick();
  const form = doc.getElementById("action-one");
  form.dataset.releaseSection = "answer-key-releases";
  form.querySelector('input[name="action"]').value = "answer_key_revoke";
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  await tick();
  assert.equal(pendingRefresh.length, 1);
  doc.getElementById("answer-key-details-modal").dispatchEvent(
    new window.Event("hidden.bs.modal", {bubbles: true})
  );
  pendingRefresh[0]({ok: true, text: async () => pane(
    "answer-key", row("answer-key", "Key A", "21:201:31:1")
  )});
  await tick();
  assert.equal(detailGets, 1);
  assert.match(doc.getElementById("release-center-feedback").textContent, /Revoked/);
});

test("refresh removes selections when an Answer Key campus becomes history only", async () => {
  const {doc, change, tick, window} = setup({fetch: (url, options = {}) => {
    if (options.method === "POST") return Promise.resolve({ok: true, json: async () => ({
      success: true, section: "answer-key-releases", message: "Updated"
    })});
    return Promise.resolve({ok: true, text: async () => `<div id="answer-key-releases-pane">
      <div id="answer-key-details-modal"><div id="answer-key-details-body"></div></div>
      <p>History only</p></div>`});
  }});
  change('input[value="21:201:31:1"]', true);
  const form = doc.getElementById("bulk-answer-key-release-form");
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  await tick();
  assert.equal(doc.getElementById("bulk-answer-key-release-form"), null);
  assert.match(doc.getElementById("release-center-feedback").textContent, /1 stale or unavailable selection/);
});

test("inactive-campus history revoke refresh preserves URL context and its open detail", async () => {
  const requests = [];
  let detailLoads = 0;
  const {doc, click, tick, window} = setup({
    url: "https://example.test/release/?cycle_status=CLOSED&section=answer-key-releases&target_campus_id=99#answer-key-releases-pane",
    fetch: (url, options = {}) => {
      if (options.method === "POST") return Promise.resolve({ok: true, json: async () => ({
        success: true, section: "answer-key-releases", message: "Revoked"
      })});
      if (url.startsWith("/key-details/a/")) {
        detailLoads += 1;
        return Promise.resolve({ok: true, text: async () => (
          detailLoads === 1 ? "<p>Initial inactive-campus history</p>" : "<p>Refreshed inactive-campus history</p>"
        )});
      }
      requests.push(url);
      return Promise.resolve({ok: true, text: async () => `<div id="answer-key-releases-pane">
        <p id="history-context">Inactive-campus history 99</p>
        <div id="answer-key-details-modal"><div id="answer-key-details-body"></div></div>
      </div>`});
    }
  });
  click('[data-answer-key-details-url="/key-details/a/"]');
  await tick();
  const form = doc.getElementById("action-one");
  form.dataset.releaseSection = "answer-key-releases";
  form.querySelector('input[name="action"]').value = "answer_key_revoke";
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  await tick();
  await tick();
  assert.equal(requests.length, 1);
  const refreshUrl = new URL(requests[0], "https://example.test");
  assert.equal(refreshUrl.searchParams.get("cycle_status"), "CLOSED");
  assert.equal(refreshUrl.searchParams.get("section"), "answer-key-releases");
  assert.equal(refreshUrl.searchParams.get("target_campus_id"), "99");
  assert.match(doc.getElementById("history-context").textContent, /99/);
  assert.equal(detailLoads, 2);
  assert.match(doc.getElementById("answer-key-details-body").textContent, /Refreshed inactive-campus history/);
});

test("late Answer Key refresh cannot restore an old campus or cycle context", async () => {
  const pendingRefresh = [];
  const {doc, change, tick, window} = setup({
    url: "https://example.test/release/?cycle_status=CLOSED&section=answer-key-releases&target_campus_id=99",
    fetch: (url, options = {}) => {
      if (options.method === "POST") return Promise.resolve({ok: true, json: async () => ({
        success: true, section: "answer-key-releases", message: "Revoked"
      })});
      return new Promise(resolve => pendingRefresh.push(resolve));
    }
  });
  const form = doc.getElementById("action-one");
  form.dataset.releaseSection = "answer-key-releases";
  form.querySelector('input[name="action"]').value = "answer_key_revoke";
  form.dispatchEvent(new window.Event("submit", {bubbles: true, cancelable: true}));
  await tick();
  assert.equal(pendingRefresh.length, 1);
  change("#answer-key-campus-filter", "2");
  change("#exam-cycle-status", "2");
  pendingRefresh[0]({ok: true, text: async () => `<div id="answer-key-releases-pane">
    <p id="old-history-context">Old context</p>
  </div>`});
  await tick();
  assert.equal(doc.getElementById("old-history-context"), null);
  assert.equal(doc.getElementById("answer-key-target-form").dataset.submitted, "true");
});
