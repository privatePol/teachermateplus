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
