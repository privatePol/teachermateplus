import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const read = (name) => fs.readFileSync(new URL(`../../static/js/${name}.js`, import.meta.url), "utf8");
function harness({recover, prepare, failSocket = false} = {}) {
  let now = 0, next = 1, calls = 0;
  const timers = new Map(), listeners = {}, sockets = [], indicator = {};
  const window = {location: {href: "https://school.test/quitizz/play/session/", protocol: "https:"},
    setTimeout(fn, delay) { const id = next++; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout(id) { timers.delete(id); }, addEventListener(name, fn) { listeners[name] = fn; }};
  class WebSocket {
    static OPEN = 1;
    constructor(url) { if (failSocket) throw Error("Unavailable"); this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    open() { this.readyState = 1; this.onopen(); }
    message(event) { this.onmessage({data: JSON.stringify(event)}); }
    send(text) { this.sent.push(text); }
    close() { if (this.readyState === 3) return; this.readyState = 3; this.onclose(); }
  }
  const document = {hidden: false, addEventListener(name, fn) { listeners[name] = fn; }};
  const deterministicMath = Object.create(Math); deterministicMath.random = () => 0;
  vm.runInNewContext(read("quitizz_realtime"), {window, document, WebSocket, URL, Math: deterministicMath});
  const transport = window.QuiTizzRealtime({url: "/ws/quitizz/session/player/", prepare, indicator,
    recover: async () => { calls++; return recover ? recover() : {version: 5}; }});
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  async function advance(ms) {
    const target = now + ms;
    while (true) {
      const due = [...timers].filter(([, value]) => value.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); await due[1].fn(); await flush();
    }
    now = target; await flush();
  }
  return {sockets, transport, timers, indicator, listeners, advance, flush, get calls() { return calls; }};
}

test("socket open/reconnect immediately recover HTTP without ready; connected avoids five-second polls", async () => {
  let prepared = 0;
  const h = harness({prepare: async () => { prepared++; }}); await h.flush();
  assert.equal(prepared, 1); assert.equal(h.sockets[0].url, "wss://school.test/ws/quitizz/session/player/");
  h.sockets[0].open(); await h.flush(); assert.equal(h.calls, 1);
  await h.advance(5000); assert.equal(h.calls, 1);
  h.sockets[0].close(); await h.advance(100); assert.equal(h.calls, 2);
  await h.advance(900); assert.equal(prepared, 2); assert.equal(h.sockets.length, 2);
  h.sockets[1].open(); await h.flush(); assert.equal(h.calls, 3);
  await h.advance(5000); assert.equal(h.calls, 3);
  assert.equal(h.indicator.textContent, "Realtime connected; HTTP recovery active."); h.transport.stop();
});

test("only sync_required triggers recovery; ready/protected socket data have no rendering callback", async () => {
  const h = harness(); h.sockets[0].open(); await h.flush();
  for (const event of [{event: "ready", version: 5}, {event: "answer_received", answered_count: 100},
    {event: "question_opened", prompt: "SECRET", choices: {A: "SECRET"}, correct_choice: "A"}]) h.sockets[0].message(event);
  await h.advance(100); assert.equal(h.calls, 1);
  h.sockets[0].message({event: "sync_required"}); await h.advance(100); assert.equal(h.calls, 2); h.transport.stop();
});

test("rapid 100 answer wakeups collapse to one additional host HTTP recovery after 100ms", async () => {
  const h = harness(); h.sockets[0].open(); await h.flush();
  for (let i = 0; i < 100; i++) h.sockets[0].message({event: "sync_required"});
  await h.advance(99); assert.equal(h.calls, 1);
  await h.advance(1); assert.equal(h.calls, 2);
  await h.advance(1000); assert.equal(h.calls, 2); h.transport.stop();
  console.log("QUITIZZ_100_ANSWER_WAKEUPS 100 signals -> 1 additional host HTTP fetch after 100ms");
});

test("100 syncs during active HTTP fetch schedule exactly one follow-up without overlap", async () => {
  const releases = []; let active = 0, maximum = 0;
  const h = harness({recover: () => { active++; maximum = Math.max(maximum, active);
    return new Promise((resolve) => releases.push(() => { active--; resolve({version: 5}); })); }});
  h.sockets[0].open(); await h.flush(); assert.equal(h.calls, 1);
  for (let i = 0; i < 100; i++) h.sockets[0].message({event: "sync_required"});
  await h.advance(500); assert.equal(h.calls, 1);
  releases.shift()(); await h.flush(); await h.advance(100); assert.equal(h.calls, 2);
  releases.shift()(); await h.flush(); await h.advance(100); assert.equal(h.calls, 2);
  assert.equal(maximum, 1); h.transport.stop();
});

test("manual recovery drains an already queued debounce", async () => {
  const h = harness(); h.sockets[0].open(); await h.flush();
  h.sockets[0].message({event: "sync_required"}); await h.transport.refresh();
  await h.advance(100); assert.equal(h.calls, 2); h.transport.stop();
});

test("disconnection resumes five-second fallback while reconnect handshake waits", async () => {
  const h = harness(); h.sockets[0].open(); await h.flush(); h.sockets[0].close();
  await h.advance(5000); assert.equal(h.calls, 3); assert.equal(h.sockets[1].readyState, 0);
  assert.match(h.indicator.textContent, /Reconnecting/); h.transport.stop();
});

test("unavailable transport retains fallback; pagehide cancels every timer", async () => {
  const h = harness({failSocket: true}); await h.advance(100); assert.equal(h.calls, 1);
  await h.advance(5000); assert.ok(h.calls >= 2);
  h.listeners.pagehide(); const before = h.calls; await h.advance(60000);
  assert.equal(h.calls, before); assert.equal(h.timers.size, 0);
});

test("silent lost events recover at 60s; heartbeat detects half-open transport", async () => {
  const h = harness(); h.sockets[0].open(); await h.flush();
  await h.advance(30000); assert.deepEqual(h.sockets[0].sent, ["ping"]);
  h.sockets[0].message({event: "pong"}); await h.advance(30000); assert.equal(h.calls, 2);
  await h.advance(15000); assert.equal(h.sockets[0].readyState, 3);
  assert.match(h.indicator.textContent, /Reconnecting/); h.transport.stop();
});

test("generic revocation recovers HTTP and closes; rejected fetch does not leak tasks", async () => {
  const h = harness({recover: async () => { throw Error("HTTP unavailable"); }});
  h.sockets[0].open(); await h.flush();
  h.sockets[0].message({event: "participant_unavailable"}); await h.advance(100);
  assert.equal(h.calls, 2); assert.equal(h.sockets[0].readyState, 3); h.transport.stop();
});

function uiTransport(window, document) {
  const sockets = [];
  let next = 0;
  window.location = {href: "https://school.test/play/", protocol: "https:", hash: "", pathname: "/play/"};
  window.addEventListener = document.addEventListener = () => {};
  window.setTimeout = () => ++next; window.clearTimeout = () => {};
  class WebSocket {
    static OPEN = 1;
    constructor() { this.readyState = 0; sockets.push(this); }
    open() { this.readyState = 1; this.onopen(); }
    message(event) { this.onmessage({data: JSON.stringify(event)}); }
    close() { this.readyState = 3; this.onclose(); }
  }
  vm.runInNewContext(read("quitizz_realtime"), {window, document, WebSocket, URL, Math});
  return {sockets, factory: window.QuiTizzRealtime};
}

function hostHarness(realTransport = false) {
  const button = {disabled: false}, privateBlock = {hidden: false};
  const elements = new Map(["qt-host", "qt-count", "qt-answered", "qt-session-status", "qt-participants", "qt-connection"]
    .map((id) => [id, {textContent: "0", children: [], querySelectorAll: () => [], addEventListener() {}}]));
  const root = elements.get("qt-host"); root.dataset = {version: "0", stateUrl: "/host/state/", socketUrl: "/host/socket/"};
  root.querySelectorAll = (selector) => selector === "button" ? [button] : selector.includes("details") ? [privateBlock] : [];
  const requests = []; let callbacks, stopped = 0;
  const document = {getElementById: (id) => elements.get(id) || null}, window = {};
  const realtime = realTransport ? uiTransport(window, document) : null;
  window.QuiTizzRealtime = (options) => { callbacks = options;
    return realtime ? realtime.factory(options) : {refresh: options.recover, stop() { stopped++; }}; };
  vm.runInNewContext(read("quitizz_host"), {
    document, window,
    fetch: () => new Promise((resolve) => requests.push(resolve)),
  });
  const state = (overrides = {}) => ({version: 5, question_id: "question-1", answered_count: 1,
    participant_count: 0, participants: [], status: "QUESTION_OPEN", position: 1, ...overrides});
  const begin = () => callbacks.recover();
  const reply = (value, index = 0, status = 200) => requests.splice(index, 1)[0]({ok: status === 200, status,
    headers: {get: () => "application/json"}, json: async () => value});
  async function recover(value = state()) { const pending = begin(); reply(value, requests.length - 1); await pending; }
  return {button, privateBlock, callbacks, state, begin, reply, recover, sockets: realtime?.sockets, get stopped() { return stopped; },
    get count() { return Number(elements.get("qt-answered").textContent); }};
}

test("host stays locked until successful canonical HTTP; no socket rendering/count callback", async () => {
  const h = hostHarness(); assert.equal(h.button.disabled, true); assert.equal(h.callbacks.onEvent, undefined);
  const pending = h.begin(); assert.equal(h.button.disabled, true); assert.equal(h.count, 0);
  h.reply(h.state()); await pending; assert.equal(h.button.disabled, false); assert.equal(h.count, 1);
});

test("actual host socket open and data messages never unlock controls before HTTP succeeds", async () => {
  const h = hostHarness(true); h.sockets[0].open(); assert.equal(h.button.disabled, true);
  h.sockets[0].message({event: "ready", answered_count: 100});
  h.sockets[0].message({event: "answer_received", answered_count: 100});
  assert.equal(h.button.disabled, true); assert.equal(h.count, 0);
  h.reply(h.state()); for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.equal(h.button.disabled, false); assert.equal(h.count, 1);
});

test("older host in-flight HTTP cannot overwrite newer same-question answer count", async () => {
  const h = hostHarness(); await h.recover(); const older = h.begin();
  await h.recover(h.state({answered_count: 3})); h.reply(h.state({answered_count: 2})); await older;
  assert.equal(h.count, 3);
});

test("host next-question lifecycle resets count and older question HTTP stays ignored", async () => {
  const h = hostHarness(); await h.recover(h.state({answered_count: 99})); const pending = h.begin();
  await h.recover(h.state({version: 8, question_id: "question-2", answered_count: 0, position: 2}));
  h.reply(h.state({answered_count: 100})); await pending; assert.equal(h.count, 0);
});

test("fresh host canonical reconnect/lifecycle recovery may correct count", async () => {
  const h = hostHarness(); await h.recover(h.state({answered_count: 3}));
  await h.recover(h.state({answered_count: 1})); assert.equal(h.count, 1);
  await h.recover(h.state({version: 6, status: "QUESTION_CLOSED", answered_count: 0})); assert.equal(h.count, 0);
});

test("host HTTP 403/404 locks and hides protected UI; late success cannot unlock", async () => {
  for (const status of [403, 404]) {
    const h = hostHarness(); await h.recover(); const older = h.begin(); const denied = h.begin();
    h.reply({}, 1, status); await denied;
    assert.equal(h.button.disabled, true); assert.equal(h.privateBlock.hidden, true); assert.equal(h.stopped, 1);
    h.reply(h.state()); await older; assert.equal(h.button.disabled, true);
  }
});

// Model the browser's named form-property lookup and successful controls. The
// old host harness intentionally covers recovery without forms; these fixtures
// exercise the actual host script's submission and form-regeneration paths.
// This focused model does not replace real-browser acceptance.
class HostControlElement {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.disabled = false; this.hidden = false; this._value = "";
  }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  set textContent(value) { this.text = String(value); this.replaceChildren(); }
  get textContent() { return this.text || ""; }
  append(...nodes) { nodes.forEach((node) => { node.parent = this; this.children.push(node); }); }
  replaceChildren(...nodes) { this.children.forEach((node) => { node.parent = null; }); this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this); this.parent = null; }
  matches(selector) { const named = /^\[name=([^\]]+)\]$/.exec(selector); return selector === this.tagName || Boolean(named && named[1] === this.name); }
  querySelectorAll(selector) { return this.children.flatMap((node) => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
  select() { this.selected = true; }
  get action() {
    const controls = this.querySelectorAll("[name=action]");
    if (controls.length === 1) return controls[0];
    if (controls.length > 1) return {length: controls.length, toString: () => "[object RadioNodeList]"};
    return this.getAttribute("action");
  }
  set action(value) { this.setAttribute("action", value); }
  toString() { return this.tagName === "button" ? "[object HTMLButtonElement]" : "[object HTMLElement]"; }
}
class HostControlFormData {
  constructor(form, submitter) {
    this.submitterDisabledAtCapture = submitter.disabled;
    this.values = [...form.querySelectorAll("input"), ...form.querySelectorAll("button")]
      .filter((control) => control.name && !control.disabled && (control.tagName !== "button" || control === submitter))
      .map((control) => [control.name, control.value]);
  }
  get(name) { return this.values.find(([key]) => key === name)?.[1] ?? null; }
  getAll(name) { return this.values.filter(([key]) => key === name).map(([, value]) => value); }
  entries() { return this.values[Symbol.iterator](); }
}
function hostControlHarness({initialParticipant = null, clipboard = null} = {}) {
  const publicId = "11111111-1111-4111-8111-111111111111";
  const commandUrl = `/faculty/quitizz/sessions/${publicId}/command/`;
  const elements = new Map(["qt-host", "qt-command", "qt-count", "qt-answered", "qt-session-status", "qt-joining", "qt-participants", "qt-connection", "qt-host-error"]
    .map((id) => [id, new HostControlElement(id === "qt-command" ? "form" : "div")]));
  const root = elements.get("qt-host"), commands = elements.get("qt-command"), list = elements.get("qt-participants");
  root.dataset = {version: "1", stateUrl: `/faculty/quitizz/sessions/${publicId}/state/`, socketUrl: "/host/socket/", qrUrl: "/host/qr/", joinLinkUrl: "/host/join-link/"};
  root.append(...[...elements].filter(([id]) => id !== "qt-host").map(([, element]) => element));
  const field = (name, value) => { const input = new HostControlElement("input"); input.type = "hidden"; input.name = name; input.value = value; return input; };
  const button = (value) => { const control = new HostControlElement("button"); control.name = "action"; control.value = value; return control; };
  commands.setAttribute("action", commandUrl);
  commands.append(field("csrfmiddlewaretoken", "csrf-host-token"), field("version", 1), button("open_joining"), button("cancel"));
  if (initialParticipant) {
    const item = new HostControlElement("li"), form = new HostControlElement("form"); item.dataset.playerId = initialParticipant.public_id;
    form.setAttribute("action", commandUrl);
    form.append(field("csrfmiddlewaretoken", "csrf-host-token"), field("version", 1), field("participant", initialParticipant.public_id), button("remove"));
    item.append(form); list.append(item);
  }
  const requests = []; let callbacks;
  const find = (node, id) => node.id === id ? node : node.children.map((child) => find(child, id)).find(Boolean);
  const document = {getElementById: (id) => elements.get(id) || find(root, id) || null, createElement: (tag) => new HostControlElement(tag), activeElement: null};
  const window = {location: {href: "https://example.test/host/", origin: "https://example.test"}, navigator: {clipboard},
    QuiTizzRealtime(options) { callbacks = options; return {refresh: options.recover, stop() {}}; }};
  vm.runInNewContext(read("quitizz_host"), {document, window, URL, FormData: HostControlFormData,
    fetch: (url, options) => new Promise((resolve) => requests.push({url: String(url), options, resolve,
      allButtonsDisabled: root.querySelectorAll("button").every((control) => control.disabled)}))});
  const state = (overrides = {}) => ({version: 1, status: "READY", position: 0, question_count: 2,
    participant_count: initialParticipant ? 1 : 0, participants: initialParticipant ? [initialParticipant] : [],
    answered_count: 0, joining_open: false, ...overrides});
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  const reply = (value, status = 200) => { assert.ok(requests.length, "expected an HTTP request"); requests.shift().resolve({ok: status >= 200 && status < 300, status,
    headers: {get: () => "application/json"}, json: async () => value}); };
  async function recover(value = state()) { const pending = callbacks.recover(); reply(value); await pending; }
  function submit(form, action) {
    const submitter = form.querySelectorAll("button").find((control) => control.value === action);
    assert.ok(submitter, `missing ${action} control`); assert.equal(submitter.disabled, false);
    let prevented = false;
    const pending = root.listeners.submit({target: form, submitter, preventDefault() { prevented = true; }});
    assert.equal(prevented, true); return pending;
  }
  async function finish(pending, value, status = 200) { reply({}, status); await flush(); assert.equal(requests[0]?.url, root.dataset.stateUrl); reply(value); await pending; }
  return {root, commands, list, requests, commandUrl, state, recover, submit, finish, reply, document, error: elements.get("qt-host-error")};
}

test("copy join link retains the exact fragment, uses uncached HTTP and confirms only clipboard success", async () => {
  const copied = [], h = hostControlHarness({clipboard: {async writeText(value) { copied.push(value); }}});
  await h.recover(h.state({version: 2, status: "LOBBY", joining_open: true}));
  const pending = h.root.listeners.click({target: {id: "qt-copy-link"}});
  assert.equal(h.requests[0].url, "/host/join-link/"); assert.equal(h.requests[0].options.cache, "no-store");
  const url = "https://example.test/quitizz/play/session/#full.signed-capability";
  h.reply({join_url: url}); await pending;
  assert.deepEqual(copied, [url]); assert.equal(h.document.getElementById("qt-copy-status").textContent, "Join link copied");
});

test("clipboard unavailable or rejected provides a selectable full-fragment fallback without a success claim", async () => {
  for (const clipboard of [null, {async writeText() { throw new Error("denied"); }}]) {
    const h = hostControlHarness({clipboard}); await h.recover(h.state({version: 2, joining_open: true}));
    const pending = h.root.listeners.click({target: {id: "qt-copy-link"}});
    const url = "https://example.test/quitizz/play/session/#capability"; h.reply({join_url: url}); await pending;
    const status = h.document.getElementById("qt-copy-status"), field = status.querySelector("textarea");
    assert.doesNotMatch(status.textContent, /Join link copied/); assert.equal(field.value, url);
    assert.equal(field.readOnly, true); assert.equal(field.selected, true);
    await h.recover(h.state({version: 3, joining_open: false}));
    assert.equal(h.document.getElementById("qt-copy-status"), null);
  }
});

test("joining closure or capability version change during link retrieval prevents copying", async () => {
  for (const joining_open of [false, true]) {
    const copied = [], h = hostControlHarness({clipboard: {async writeText(url) { copied.push(url); }}});
    await h.recover(h.state({version: 2, joining_open: true}));
    const pending = h.root.listeners.click({target: {id: "qt-copy-link"}});
    const linkRequest = h.requests.shift(); await h.recover(h.state({version: 3, joining_open}));
    linkRequest.resolve({ok: true, json: async () => ({join_url: "https://example.test/play/#obsolete"})}); await pending;
    assert.deepEqual(copied, []);
  }
});

test("copy join link rejects query-string capabilities and foreign origins", async () => {
  for (const url of ["https://example.test/play/?cap=secret#token", "https://other.test/play/#token", "https://example.test/play/"]) {
    const copied = [], h = hostControlHarness({clipboard: {async writeText(value) { copied.push(value); }}});
    await h.recover(h.state({version: 2, joining_open: true}));
    const pending = h.root.listeners.click({target: {id: "qt-copy-link"}}); h.reply({join_url: url}); await pending;
    assert.deepEqual(copied, []); assert.match(h.document.getElementById("qt-copy-status").textContent, /unavailable/);
  }
});

test("manual fallback retains Resume after automatic availability is disabled", async () => {
  const h = hostControlHarness();
  await h.recover(h.state({version: 2, status: "QUESTION_OPEN", playback_mode: "MANUAL", paused: true,
    automatic_available: false, show_phase: "ANSWERING", question_opened: true}));
  const controls = h.commands.querySelectorAll("button").map((control) => control.value);
  assert.ok(controls.includes("resume")); assert.ok(!controls.includes("pause"));
  for (const action of ["close_question", "reveal", "reveal_now", "next", "next_now", "start"]) assert.ok(!controls.includes(action));
  const pending = h.submit(h.commands, "resume"); assertHostCommand(h, "resume", 2);
  await h.finish(pending, h.state({version: 3, status: "QUESTION_OPEN", playback_mode: "MANUAL", paused: false}));
});
function assertHostCommand(h, action, version, participant = null) {
  assert.equal(h.requests.length, 1);
  const {url, options, allButtonsDisabled} = h.requests[0];
  assert.equal(url, h.commandUrl); assert.ok(!url.includes("[object"));
  assert.equal(options.method, "POST"); assert.equal(options.credentials, "same-origin");
  assert.deepEqual(options.body.getAll("action"), [action]);
  assert.equal(options.body.get("version"), String(version));
  assert.equal(options.body.get("csrfmiddlewaretoken"), "csrf-host-token");
  assert.equal(options.body.get("participant"), participant);
  assert.equal(options.body.submitterDisabledAtCapture, false); assert.equal(allButtonsDisabled, true);
  assert.equal([...options.body.entries()].length, participant ? 4 : 3);
}
const hostCommandStates = {
  open_joining: {status: "READY"}, close_joining: {status: "LOBBY", joining_open: true},
  start: {status: "LOBBY"}, open_question: {status: "QUESTION_CLOSED", question_opened: false},
  close_question: {status: "QUESTION_OPEN"}, reveal: {status: "QUESTION_CLOSED", question_opened: true},
  next: {status: "ANSWER_REVEALED", position: 1}, complete: {status: "ANSWER_REVEALED", position: 1}, cancel: {status: "READY"},
};
for (const [action, overrides] of Object.entries(hostCommandStates)) test(`host command ${action} uses the URL attribute and captures successful controls before locking`, async () => {
  const h = hostControlHarness(); const state = h.state({...overrides, version: action === "open_joining" ? 1 : 2});
  await h.recover(state);
  assert.equal(String(h.commands.action), "[object RadioNodeList]", "fixture must reproduce named-property shadowing");
  const pending = h.submit(h.commands, action); assertHostCommand(h, action, state.version);
  await h.finish(pending, state); assert.ok(h.root.querySelectorAll("button").every((control) => !control.disabled));
});
test("host initial single-button Remove form posts the attribute URL with participant identity", async () => {
  const participant = {public_id: "participant-1", nickname: "Player"};
  const h = hostControlHarness({initialParticipant: participant}); await h.recover();
  const form = h.list.querySelector("form"); assert.equal(String(form.action), "[object HTMLButtonElement]");
  const pending = h.submit(form, "remove"); assertHostCommand(h, "remove", 1, participant.public_id);
  await h.finish(pending, h.state({version: 2, participants: [], participant_count: 0}));
});
test("host dynamically created Remove forms copy the URL attribute and submit their own participant", async () => {
  const h = hostControlHarness(), participants = [{public_id: "participant-1", nickname: "One"}, {public_id: "participant-2", nickname: "Two"}];
  const state = h.state({version: 2, status: "LOBBY", participants, participant_count: 2}); await h.recover(state);
  const forms = h.list.querySelectorAll("form"); assert.equal(forms.length, 2);
  forms.forEach((form) => assert.equal(form.getAttribute("action"), h.commandUrl));
  const pending = h.submit(forms[1], "remove"); assertHostCommand(h, "remove", 2, participants[1].public_id);
  await h.finish(pending, h.state({version: 3, participants: [participants[0]], participant_count: 1}));
  assert.equal(h.list.querySelector("form").getAttribute("action"), h.commandUrl);
});
test("host stale-version rejection recovers current controls and subsequent commands post the current version", async () => {
  const h = hostControlHarness(); await h.recover();
  const oldButton = h.commands.querySelectorAll("button")[0];
  const first = h.submit(h.commands, "open_joining"); assertHostCommand(h, "open_joining", 1);
  const recovered = h.state({version: 2, status: "LOBBY", joining_open: true});
  await h.finish(first, recovered, 409);
  assert.equal(h.error.hidden, false); assert.match(h.error.textContent, /current session has been recovered/);
  assert.ok(!h.commands.querySelectorAll("button").includes(oldButton));
  assert.deepEqual(h.commands.querySelectorAll("button").map((control) => control.value), ["close_joining", "start", "set_mode", "end_challenge", "cancel"]);
  const second = h.submit(h.commands, "start"); assertHostCommand(h, "start", 2);
  await h.finish(second, h.state({version: 3, status: "QUESTION_CLOSED", question_opened: false, position: 1}));
  assert.equal(h.error.hidden, true); assert.equal(h.commands.querySelector("[name=version]").value, "3");
});

function playerHarness(realTransport = false) {
  const buttons = [];
  const ids = ["qt-play", "qt-message", "qt-error", "qt-join", "qt-question", "qt-answers", "qt-feedback", "qt-connection", "qt-prompt", "qt-countdown"];
  const elements = new Map(ids.map((id) => [id, {textContent: "", hidden: true, addEventListener() {},
    querySelector: () => ({value: "csrf"}), querySelectorAll: () => buttons,
    replaceChildren() { buttons.length = 0; }, append(button) { buttons.push(button); }}]));
  elements.get("qt-play").dataset = {stateUrl: "/state/", socketUrl: "/socket/", socketIdentityUrl: "/bridge/"};
  const requests = []; let callbacks, stopped = 0;
  const document = {getElementById: (id) => elements.get(id), createElement: () => ({addEventListener() {}, disabled: false})};
  const window = {location: {hash: "", pathname: "/play/"}, history: {replaceState() {}}, setInterval() {}};
  const realtime = realTransport ? uiTransport(window, document) : null;
  window.QuiTizzRealtime = (options) => { callbacks = options;
    return realtime ? realtime.factory(options) : {refresh: options.recover, stop() { stopped++; }}; };
  vm.runInNewContext(read("quitizz_play"), {
    document, window,
    fetch: () => new Promise((resolve) => requests.push(resolve)), URLSearchParams, Date,
  });
  const state = (overrides = {}) => ({version: 5, status: "LOBBY", nickname: "Player", ...overrides});
  const open = () => state({status: "QUESTION_OPEN", question: {id: "q1", position: 1, prompt: "HTTP prompt",
    choices: {A: "First", B: "Second"}, deadline: new Date(Date.now() + 60000).toISOString()},
    server_now: new Date().toISOString(), can_answer: true});
  const reply = (value, status = 200) => requests.shift()({ok: status === 200, status, json: async () => value});
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  return {elements, buttons, state, open, reply, flush, sockets: realtime?.sockets, get callbacks() { return callbacks; }, get stopped() { return stopped; }};
}

test("player UI stays hidden pending HTTP; socket never supplies questions/enables answers", async () => {
  const h = playerHarness(); assert.equal(h.elements.get("qt-question").hidden, true);
  h.reply(h.state()); await h.flush(); assert.equal(h.callbacks.onEvent, undefined);
  const pending = h.callbacks.recover(); assert.equal(h.elements.get("qt-question").hidden, true);
  h.reply(h.open()); await pending;
  assert.equal(h.elements.get("qt-question").hidden, false); assert.equal(h.buttons[0].disabled, false);
});

test("actual player socket open and question payload never expose gameplay until canonical HTTP", async () => {
  const h = playerHarness(true); h.reply(h.state()); await h.flush();
  h.reply({ready: true}); await h.flush(); // authorized HTTP cookie bridge
  h.sockets[0].open(); assert.equal(h.elements.get("qt-question").hidden, true);
  h.sockets[0].message({event: "question_opened", prompt: "SECRET", choices: {A: "SECRET"}});
  assert.equal(h.elements.get("qt-question").hidden, true); assert.equal(h.buttons.length, 0);
  h.reply(h.open()); await h.flush(); assert.equal(h.elements.get("qt-question").hidden, false);
  assert.equal(h.buttons[0].disabled, false);
});

test("player HTTP 403/404 revocation hides gameplay and disables answers; late success stays locked", async () => {
  for (const status of [403, 404]) {
    const h = playerHarness(); h.reply(h.open()); await h.flush(); assert.equal(h.buttons[0].disabled, false);
    const pending = h.callbacks.recover(); h.reply({error: "Session unavailable."}, status); await pending;
    assert.equal(h.buttons[0].disabled, true); assert.equal(h.elements.get("qt-question").hidden, true);
    assert.equal(h.elements.get("qt-feedback").hidden, true); assert.equal(h.stopped, 1);
    const late = h.callbacks.recover(); h.reply(h.open()); await late;
    assert.equal(h.elements.get("qt-question").hidden, true); assert.equal(h.buttons[0].disabled, true);
  }
});

test("authoritative phase boundary adds one coalesced HTTP recovery and bounded retries", async () => {
  const h = harness({recover: () => ({version: 5, status: "QUESTION_OPEN", paused: false, phase_deadline: "2026-10-11T00:00:01Z", server_now: "2026-10-11T00:00:00Z"})});
  await h.flush(); h.sockets[0].open(); await h.flush(); assert.equal(h.calls, 1);
  await h.advance(1200); assert.equal(h.calls, 2);
  h.transport.stop(); assert.equal(h.timers.size, 0);
});
test("paused snapshots never schedule deadline recovery", async () => {
  const h = harness({recover: () => ({version: 5, paused: true, status: "QUESTION_OPEN", phase_deadline: "2026-10-11T00:00:01Z", server_now: "2026-10-11T00:00:00Z"})});
  await h.flush(); h.sockets[0].open(); await h.flush(); await h.advance(2000); assert.equal(h.calls, 1); h.transport.stop();
});
