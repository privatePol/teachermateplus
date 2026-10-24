import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import {JSDOM} from "jsdom";

const read = (name) => fs.readFileSync(new URL(`../../${name}`, import.meta.url), "utf8");
const session = "11111111-1111-4111-8111-111111111111";
const commandUrl = `/faculty/quitizz/sessions/${session}/command/`;
const value = (extra = {}) => ({status: "ANSWER_REVEALED", version: 5, playback_mode: "MANUAL", results_screen: "ANSWER", presentation_options: {show_fastest: true, show_scores: true},
  participant_count: 0, answered_count: 0, question_count: 3, position: 3, participants: [], pending_count: 2, completed_count: 1,
  navigator: [{id: "pending", position: 1, preview: "<img onerror=alert(1)>", status: "Pending", can_select: true}, {id: "done", position: 3, preview: "Played", status: "Completed", can_select: false, can_review: true}], ...extra});
function harness() {
  const template = read("templates/quitizz/host.html");
  const nav = template.slice(template.indexOf('<section id="qt-navigator"'), template.indexOf('<section id="qt-audio"')).replace(/{%[\s\S]*?%}/g, "").replace(/{{[\s\S]*?}}/g, "");
  const dom = new JSDOM(`<section id="qt-host"><p id="qt-count"></p><p id="qt-answered"></p><p id="qt-session-status"></p><p id="qt-connection"></p><p id="qt-host-error"></p><div id="qt-joining"></div><ul id="qt-participants"></ul><form id="qt-command"><input name="csrfmiddlewaretoken" value="csrf"><input name="version"><select id="qt-playback-mode"><option value="MANUAL">Manual</option><option value="AUTOMATIC">Automatic</option></select></form>${nav}</section>`, {url: "https://school.test/host/", runScripts: "outside-only"});
  const w = dom.window, root = w.document.getElementById("qt-host"), requests = [];
  Object.assign(root.dataset, {sessionId: session, version: "5", stateUrl: "/state/", reviewUrl: `/faculty/quitizz/sessions/${session}/review/${session}/`});
  root.querySelectorAll("form").forEach((form) => form.setAttribute("action", commandUrl));
  let transport;
  w.QuiTizzRealtime = (options) => { transport = {...options, refresh: options.recover, stop() { this.stopped = true; }}; return transport; };
  w.fetch = (url, options) => new Promise((resolve) => requests.push({url, options, resolve}));
  vm.runInContext(read("static/js/quitizz_host.js"), dom.getInternalVMContext());
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  const reply = (data, status=200) => requests.shift().resolve({ok: status===200, status, headers: {get: () => "application/json"}, json: async () => data});
  const refresh = async (state) => { const promise = transport.refresh(); reply(state); await promise; };
  return {w, root, requests, flush, reply, refresh, get transport() { return transport; }, close: () => w.close()};
}
test("reload at the same version rebuilds result controls and navigator uses escaped original labels", async () => {
  const h = harness();
  try {
    await h.refresh(value());
    const buttons = [...h.root.querySelectorAll('#qt-command button[data-screen]')];
    assert.deepEqual(buttons.map((button) => button.dataset.screen), ["ANSWER", "FASTEST_FIVE", "HIGHEST_SCORES"]);
    const select = h.w.document.getElementById("qt-next-question");
    assert.match(select.textContent, /1\. <img onerror/); assert.equal(select.querySelector("img"), null);
    assert.equal(h.w.document.getElementById("qt-select-next").disabled, false);
    assert.equal(h.w.document.getElementById("qt-open-selected").disabled, false);
  } finally { h.close(); }
});
test("screen submission preserves endpoint attribute, CSRF, screen and current version before locking", async () => {
  const h = harness();
  try {
    await h.refresh(value());
    const button = h.root.querySelector('[data-screen="FASTEST_FIVE"]');
    button.form.dispatchEvent(new h.w.SubmitEvent("submit", {bubbles: true, cancelable: true, submitter: button}));
    const request = h.requests[0]; assert.equal(request.url, commandUrl);
    assert.equal(request.options.body.get("screen"), "FASTEST_FIVE"); assert.equal(request.options.body.get("action"), "show_screen");
    assert.equal(request.options.body.get("csrfmiddlewaretoken"), "csrf"); assert.equal(request.options.body.get("version"), "5");
    assert.equal(button.disabled, true); h.reply({}); await h.flush(); h.reply(value({version: 6, results_screen: "FASTEST_FIVE"})); await h.flush();
  } finally { h.close(); }
});
test("review stays historical, does not POST or change live selection, and ignores late revoked responses", async () => {
  const h = harness();
  try {
    await h.refresh(value());
    const select = h.w.document.getElementById("qt-next-question"); select.value = "done"; select.dispatchEvent(new h.w.Event("change", {bubbles:true}));
    assert.equal(h.w.document.getElementById("qt-select-next").disabled, true);
    h.w.document.getElementById("qt-review-question").click();
    assert.equal(h.requests[0].url, `/faculty/quitizz/sessions/${session}/review/done/`);
    assert.equal(h.requests[0].options.method, undefined);
    const reviewRequest = h.requests.shift();
    const revoke = h.transport.refresh(); h.reply({}, 403); await revoke;
    reviewRequest.resolve({ok:true,json:async()=>({question:{position:3,prompt:"Protected",choices:{A:"Secret"}},correct_choice:"B",fastest_five:[]})}); await h.flush();
    assert.equal(h.w.document.getElementById("qt-navigator").hidden, true);
    assert.doesNotMatch(h.w.document.getElementById("qt-review").textContent, /Protected|Secret/);
  } finally { h.close(); }
});
test("automatic and paused sessions can designate upcoming but cannot open it from the browser", async () => {
  const h = harness();
  try {
    await h.refresh(value({playback_mode:"AUTOMATIC",status:"QUESTION_OPEN",paused:true}));
    assert.equal(h.w.document.getElementById("qt-select-next").disabled, false);
    assert.equal(h.w.document.getElementById("qt-open-selected").disabled, true);
    assert.equal(h.root.querySelector('[data-screen="FASTEST_FIVE"]'), null);
    await h.refresh(value({version:6,status:"COMPLETED"}));
    assert.equal(h.w.document.getElementById("qt-select-next").disabled, true);
  } finally { h.close(); }
});

test("navigator submits the selected question and fresh version without submitting a review action", async () => {
  const h = harness();
  try {
    await h.refresh(value({version: 8}));
    const button = h.w.document.getElementById("qt-select-next");
    button.form.dispatchEvent(new h.w.SubmitEvent("submit", {bubbles:true,cancelable:true,submitter:button}));
    const request = h.requests[0];
    assert.equal(request.url, commandUrl); assert.equal(request.options.method, "POST");
    assert.equal(request.options.body.get("question"), "pending");
    assert.equal(request.options.body.get("action"), "select_question");
    assert.equal(request.options.body.get("version"), "8");
    h.reply({}); await h.flush(); h.reply(value({version:9})); await h.flush();
  } finally { h.close(); }
});

test("early termination includes confirmation only after an explicit checked choice", async () => {
  const h = harness();
  try {
    await h.refresh(value());
    const raw = read("templates/quitizz/host.html").match(/<input form="qt-command"[^>]+name="confirm_end"[^>]*>/)[0];
    const form = h.w.document.getElementById("qt-command"); form.insertAdjacentHTML("beforeend", raw);
    const checkbox = form.querySelector('[name="confirm_end"]'), button = form.querySelector('[value="end_challenge"]');
    assert.equal(new h.w.FormData(form, button).has("confirm_end"), false);
    checkbox.checked = true;
    form.dispatchEvent(new h.w.SubmitEvent("submit", {bubbles:true,cancelable:true,submitter:button}));
    assert.equal(h.requests[0].options.body.get("confirm_end"), "1");
    assert.equal(h.requests[0].options.body.get("action"), "end_challenge");
    h.reply({}); await h.flush(); h.reply(value({version:6,status:"COMPLETED"})); await h.flush();
  } finally { h.close(); }
});
