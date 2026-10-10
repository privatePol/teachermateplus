import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = (name) => fs.readFileSync(new URL(`../../static/js/${name}.js`, import.meta.url), "utf8");
class Element {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; this.hidden = false; this.disabled = false; this.style = {setProperty: (key, value) => { this.style[key] = value; }}; }
  set textContent(text) { this.text = String(text); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(" "); }
  append(...children) { children.forEach((child) => { child.parent = this; this.children.push(child); }); }
  replaceChildren(...children) { this.children = []; this.text = ""; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  focus() { this.focused = true; }
  querySelectorAll(selector) { return this.children.flatMap((child) => [...(child.tagName === selector ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return selector === "[name=csrfmiddlewaretoken]" ? {value: "csrf"} : this.querySelectorAll(selector)[0]; }
}
function harness(reduced = false) {
  const elements = new Map(), timers = new Map(), intervals = new Map(), listeners = {}, motion = {}; let next = 0;
  const document = {getElementById: (id) => elements.get(id), createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag)};
  const window = {location: {hash: "", pathname: "/play/"}, history: {replaceState() {}},
    matchMedia: () => ({matches: reduced, addEventListener: (key, fn) => { motion[key] = fn; }}),
    addEventListener(key, fn) { (listeners[key] ||= []).push(fn); },
    setTimeout(fn, delay) { const id = ++next; timers.set(id, {fn, delay}); return id; }, clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { const id = ++next; intervals.set(id, fn); return id; }, clearInterval(id) { intervals.delete(id); }};
  const requests = []; let transport;
  window.QuiTizzRealtime = (options) => { transport = {...options, refresh: options.recover, stop() { this.stopped = true; }}; return transport; };
  const context = vm.createContext({window, document, Date, JSON, URLSearchParams, fetch: (url) => new Promise((resolve) => requests.push({url, resolve}))});
  const load = (name) => vm.runInContext(source(name), context);
  load("quitizz_presentation");
  const add = (id, tag) => { const element = new Element(tag); elements.set(id, element); return element; };
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  const reply = (state, status = 200, index = 0) => requests.splice(index, 1)[0].resolve({ok: status === 200, status, headers: {get: () => "application/json"}, json: async () => state});
  return {window, document, elements, timers, intervals, listeners, motion, add, load, flush, reply, requests, get transport() { return transport; }};
}
const state = (extra = {}) => ({status: "LOBBY", title: "Spark night", version: 3, participant_count: 0,
  answered_count: 0, question_count: 2, position: 0, joining_open: true, server_now: new Date().toISOString(), ...extra});
const question = () => ({position: 1, prompt: "What sparks?", choices: {A: "Alpha", B: "Beta", C: "Gamma", D: "Delta"}, deadline: new Date(Date.now() + 60000).toISOString()});
const leaders = (count) => Array.from({length: count}, (_, i) => ({rank: i + 1, nickname: `Player ${i + 1}`, score: 0}));

test("welcome and lobby show title, authoritative count and QR only while joining is open", () => {
  const h = harness(); const root = h.add("stage"); const view = h.window.QuiTizzPresentation.create(root, {qrUrl: "/qr/"});
  view.render(state({participant_count: 7})); assert.match(root.textContent, /7 joined/); assert.match(root.textContent, /Spark night/);
  assert.equal(root.querySelectorAll("img")[0].alt, "Scan this QR code, enter your nickname and join QuiTizz");
  view.render(state({joining_open: false, version: 4})); assert.equal(root.querySelectorAll("img").length, 0); assert.match(root.textContent, /Joining is closed/);
});
test("question has four readable choices, server deadline display and no invented feedback", () => {
  const h = harness(); const root = h.add("stage"); const view = h.window.QuiTizzPresentation.create(root);
  view.render(state({status: "QUESTION_OPEN", question: question(), position: 1, answered_count: 4}));
  for (const text of ["A. Alpha", "B. Beta", "C. Gamma", "D. Delta", "4 answered", "Seconds remaining"]) assert.ok(root.textContent.includes(text));
  assert.ok(!root.textContent.includes("Correct answer")); assert.equal(root.querySelectorAll("strong").find((el) => el.attributes.role === "timer").attributes["aria-live"], "off");
});
test("reveal renders authoritative distribution including zero responses and Top 5", () => {
  const h = harness(); const root = h.add("stage"); const view = h.window.QuiTizzPresentation.create(root);
  view.render(state({status: "ANSWER_REVEALED", question: question(), reveal: {correct_choice: "B", distribution: {A: 0, B: 0, C: 0, D: 0}}, leaderboard: leaders(5)}));
  assert.match(root.textContent, /B. Beta ✓ Correct answer/); assert.match(root.textContent, /No responses received/); assert.match(root.textContent, /Top 5/);
  assert.match(root.textContent, /5. Player 5/); assert.equal(root.querySelectorAll("li").length, 5);
});
test("distribution widths derive solely from supplied counts", () => {
  const h = harness(); const root = h.add("stage");
  h.window.QuiTizzPresentation.create(root).render(state({status: "ANSWER_REVEALED", question: question(), reveal: {correct_choice: "B", distribution: {A: 1, B: 3, C: 0, D: 0}}, leaderboard: []}));
  const bars = root.querySelectorAll("div").filter((el) => el.className === "qt-bar");
  assert.deepEqual(bars.map((el) => el.style["--percent"]), ["25%", "75%", "0%", "0%"]);
});
for (const count of [0, 1, 2, 5]) test(`final podium handles ${count} zero-score participants without phantom places`, () => {
  const h = harness(true); const root = h.add("stage"); const view = h.window.QuiTizzPresentation.create(root);
  view.render(state({status: "COMPLETED", leaderboard: leaders(count)}));
  assert.equal(root.querySelectorAll("li").length, Math.min(count, 3)); assert.match(root.textContent, /Final Top 3/);
  if (!count) assert.match(root.textContent, /No participants/);
  else { assert.match(root.textContent, /Gold · Champion/); assert.match(root.textContent, /0 points/); }
  assert.equal(h.timers.size, 0);
});
test("success effect is bounded, replaced safely and cleaned automatically", () => {
  const h = harness(); const root = h.add("root");
  h.window.QuiTizzPresentation.celebrate(root); assert.equal(root.children[0].children.length, 13); assert.equal(h.timers.size, 1);
  h.window.QuiTizzPresentation.celebrate(root, true); assert.equal(root.children.length, 1); assert.equal(root.children[0].children.length, 37); assert.equal(h.timers.size, 1);
  [...h.timers.values()][0].fn(); assert.equal(root.children.length, 0); assert.equal(h.timers.size, 0);
});
test("reduced motion avoids particles and timers; preference changes clean current effects", () => {
  const reduced = harness(true); const root = reduced.add("root"); reduced.window.QuiTizzPresentation.celebrate(root, true);
  assert.equal(root.children.length, 0); assert.equal(reduced.timers.size, 0);
  const h = harness(); const active = h.add("root"); h.window.QuiTizzPresentation.celebrate(active);
  h.motion.change(); assert.equal(active.children.length, 0); assert.equal(h.timers.size, 0);
});
test("finale plays once per completed version and pagehide cleans effects and countdown", () => {
  const h = harness(); const root = h.add("stage"); const view = h.window.QuiTizzPresentation.create(root);
  const final = state({status: "COMPLETED", leaderboard: leaders(3)}); view.render(final); assert.equal(h.timers.size, 1);
  [...h.timers.values()][0].fn(); view.render({...final, server_now: "later"}); assert.equal(h.timers.size, 0);
  h.listeners.pagehide.forEach((fn) => fn()); assert.equal(h.intervals.size, 0);
});
test("projector renders only canonical HTTP and denies late success after revocation", async () => {
  const h = harness(); const root = h.add("qt-projector"); root.dataset = {stateUrl: "/state/", socketUrl: "/socket/", qrUrl: "/qr/"};
  const screen = h.add("qt-presentation"); h.add("qt-connection"); h.load("quitizz_projector");
  assert.equal(screen.children.length, 0); assert.equal(h.transport.onEvent, undefined);
  const initial = h.transport.recover(); h.reply(state()); await initial; assert.match(screen.textContent, /Spark night/);
  const late = h.transport.recover(); const denied = h.transport.recover(); h.reply({}, 403, 1); await denied;
  assert.equal(screen.textContent, "Session unavailable."); assert.equal(h.intervals.size, 0); assert.equal(h.transport.stopped, true);
  h.reply(state()); await late; assert.equal(screen.textContent, "Session unavailable.");
});
test("projector retains HTTP answer-count ordering and resets on next lifecycle", async () => {
  const h = harness(); const root = h.add("qt-projector"); root.dataset = {}; const screen = h.add("qt-presentation"); h.add("qt-connection"); h.load("quitizz_projector");
  const older = h.transport.recover(); const newer = h.transport.recover(); h.reply(state({answered_count: 8}), 200, 1); await newer;
  h.reply(state({answered_count: 2})); await older; assert.match(screen.textContent, /8 answered/);
  const next = h.transport.recover(); h.reply(state({version: 4, position: 2, answered_count: 0})); await next; assert.match(screen.textContent, /0 answered/);
});
function player(reduced = false) {
  const h = harness(reduced);
  for (const id of ["qt-play", "qt-message", "qt-error", "qt-join", "qt-question", "qt-answers", "qt-feedback", "qt-connection", "qt-prompt", "qt-countdown", "qt-timer-block", "qt-game-title", "qt-nickname", "qt-result", "qt-result-score"]) h.add(id);
  h.elements.get("qt-play").dataset = {stateUrl: "/state/", answerUrl: "/answer/"};
  h.elements.get("qt-question").hidden = true;
  h.load("quitizz_play");
  return h;
}
test("player lobby/open/submitted/reveal/final states include text and private authorized rank", async () => {
  const h = player(); h.reply(state({nickname: "You"})); await h.flush(); assert.match(h.elements.get("qt-message").textContent, /Waiting for the host/);
  const open = {...state({nickname: "You", status: "QUESTION_OPEN", question: {...question(), id: "q1"}}), can_answer: true};
  const recover = h.transport.recover(); h.reply(open); await recover;
  assert.equal(h.elements.get("qt-question").hidden, false); const buttons = h.elements.get("qt-answers").children; assert.equal(buttons.length, 4); assert.equal(buttons[0].disabled, false);
  const locked = h.transport.recover(); h.reply({...open, accepted: true, can_answer: false}); await locked;
  assert.match(h.elements.get("qt-message").textContent, /submitted and locked/); assert.ok(buttons.every((el) => el.disabled)); assert.equal(h.elements.get("qt-feedback").hidden, true);
  const reveal = h.transport.recover(); h.reply({...open, status: "ANSWER_REVEALED", can_answer: false, feedback: {answered: true, is_correct: true, correct_choice: "B", points: 930}}); await reveal;
  assert.equal(h.elements.get("qt-timer-block").hidden, true);
  assert.match(h.elements.get("qt-feedback").textContent, /Correct! Correct answer: B. \+930 points/); assert.equal(h.timers.size, 1);
  const complete = h.transport.recover(); h.reply({...open, status: "COMPLETED", feedback: {answered: true, is_correct: true, correct_choice: "B", points: 930}, summary: {total_score: 930, correct_count: 1, rank: 2}}); await complete;
  assert.match(h.elements.get("qt-feedback").textContent, /Your rank: 2/); assert.equal(h.timers.size, 1);
  h.listeners.pagehide.forEach((fn) => fn()); assert.equal(h.timers.size, 0); assert.equal(h.intervals.size, 0);
});
test("answer click locks every button before HTTP and never reveals from acknowledgment", async () => {
  const h = player(); const open = state({status: "QUESTION_OPEN", nickname: "You", can_answer: true, question: {...question(), id: "q1"}});
  h.reply(open); await h.flush(); const buttons = h.elements.get("qt-answers").children;
  const click = buttons[1].listeners.click(); assert.ok(buttons.every((el) => el.disabled)); assert.equal(h.requests[0].url, "/answer/");
  h.reply({accepted: true}); await h.flush(); assert.equal(h.elements.get("qt-feedback").hidden, true); assert.match(h.elements.get("qt-message").textContent, /submitted and locked/);
  h.reply({...open, accepted: true, can_answer: false}); await click; assert.ok(buttons.every((el) => el.disabled)); assert.equal(h.timers.size, 0);
});
test("new browser consumes QR fragment, focuses nickname and waits for joining HTTP", async () => {
  const h = harness();
  for (const id of ["qt-play", "qt-message", "qt-error", "qt-join", "qt-question", "qt-answers", "qt-feedback", "qt-connection", "qt-prompt", "qt-countdown", "qt-nickname"]) h.add(id);
  h.elements.get("qt-play").dataset = {stateUrl: "/state/", exchangeUrl: "/exchange/", joinUrl: "/join/"};
  h.elements.get("qt-join").append(new Element("button")); h.elements.get("qt-nickname").value = "You";
  h.window.location.hash = "#synthetic-capability"; h.load("quitizz_play");
  h.reply({error: "Not joined"}, 404); await h.flush(); assert.equal(h.requests[0].url, "/exchange/");
  h.reply({ready: true}); await h.flush(); assert.equal(h.elements.get("qt-join").hidden, false); assert.equal(h.elements.get("qt-nickname").focused, true);
  const submit = h.elements.get("qt-join").listeners.submit({preventDefault() {}}); assert.equal(h.elements.get("qt-join").children[0].disabled, true);
  h.reply({joined: true}); await h.flush(); h.reply(state({nickname: "You"})); await submit;
  assert.equal(h.elements.get("qt-join").hidden, true); assert.match(h.elements.get("qt-message").textContent, /Waiting for the host/);
});
test("incorrect and unanswered feedback never celebrates; reduced-motion correct remains static", async () => {
  for (const scenario of [{answered: true, is_correct: false}, {answered: false, is_correct: false}, {answered: true, is_correct: true, reduced: true}]) {
    const h = player(scenario.reduced); h.reply(state({nickname: "You", status: "ANSWER_REVEALED", question: {...question(), id: "q1"}, feedback: {...scenario, correct_choice: "B", points: 0}})); await h.flush();
    const feedback = h.elements.get("qt-feedback"); assert.equal(feedback.hidden, false); assert.match(feedback.textContent, /Correct answer: B/); assert.equal(h.timers.size, 0);
    assert.ok(feedback.textContent.startsWith(scenario.is_correct ? "Correct!" : scenario.answered ? "Incorrect." : "No accepted answer."));
  }
});
test("authorized private champion result has one finale and revocation hides the result", async () => {
  const h = player(); h.reply(state({nickname: "You"})); await h.flush();
  const final = state({status: "COMPLETED", nickname: "You", question: {...question(), id: "q1"}, feedback: {answered: true, is_correct: true, correct_choice: "B", points: 700}, summary: {total_score: 700, correct_count: 1, rank: 1}});
  const complete = h.transport.recover(); h.reply(final); await complete;
  assert.equal(h.elements.get("qt-result").hidden, false); assert.match(h.elements.get("qt-result-score").textContent, /Champion! 700 points · Your rank: 1/);
  assert.equal(h.elements.get("qt-play").children[0].children.length, 37);
  [...h.timers.values()][0].fn(); const repeat = h.transport.recover(); h.reply(final); await repeat; assert.equal(h.timers.size, 0);
  const denied = h.transport.recover(); h.reply({error: "Unavailable"}, 404); await denied; assert.equal(h.elements.get("qt-result").hidden, true);
});
