import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
const source = fs.readFileSync(new URL("../../static/js/quitizz_audio.js", import.meta.url), "utf8");
function harness({supported = true, denied = false, speaker = true} = {}) {
  const events = {}, elements = new Map(), contexts = [], listeners = {};
  for (const id of ["enable", "mute", "volume", "status"]) elements.set(`[data-audio-${id}]`, {textContent: "", addEventListener(name, fn) { events[`${id}:${name}`] = fn; }});
  class Context {
    constructor() { this.state = "suspended"; this.currentTime = 0; this.destination = {}; this.oscillators = []; contexts.push(this); }
    async resume() { if (denied) throw Error(); this.state = "running"; }
    close() { this.state = "closed"; }
    createOscillator() { const node = {frequency: {}, connect() {}, disconnect() { this.disconnected = true; }, start() {}, stop() { this.stopped = true; }}; this.oscillators.push(node); return node; }
    createGain() { return {gain: {setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}, disconnect() {}}; }
  }
  const window = {AudioContext: supported ? Context : undefined, addEventListener(name, fn) { listeners[name] = fn; }};
  vm.runInNewContext(source, {window, Date, Number, Math, Set});
  const audio = window.QuiTizzAudio({querySelector: (key) => elements.get(key)}, {speaker});
  const state = (phase, version = 1, extra = {}) => ({show_phase: phase, version, position: 1, question: {id: "q1"},
    phase_started_at: "2026-10-11T00:00:00Z", server_now: "2026-10-11T00:00:00.200Z", ...extra});
  return {audio, state, events, contexts, listeners, status: elements.get("[data-audio-status]")};
}
test("no context or sound before explicit interaction; initial state never replays", async () => {
  const h = harness(); h.audio.update(h.state("SUSPENSE")); assert.equal(h.contexts.length, 0);
  await h.events["enable:click"](); h.audio.update(h.state("SUSPENSE", 2)); assert.equal(h.contexts[0].oscillators.length, 0);
});
test("fresh suspense generates bounded synthetic cue and deduplicates roster/version updates", async () => {
  const h = harness(); h.audio.update(h.state("ANSWERING")); await h.events["enable:click"]();
  h.audio.update(h.state("SUSPENSE")); assert.equal(h.contexts[0].oscillators.length, 12);
  h.audio.update(h.state("SUSPENSE", 9)); assert.equal(h.contexts[0].oscillators.length, 12);
});
test("mute and zero volume preserve lifecycle and stop existing cue", async () => {
  const h = harness(); h.audio.update(h.state("ANSWERING")); await h.events["enable:click"](); h.audio.update(h.state("SUSPENSE"));
  h.events["mute:click"]({currentTarget: {}}); assert.ok(h.contexts[0].oscillators.every((node) => node.stopped));
  h.events["volume:input"]({target: {value: 0}}); h.audio.update(h.state("FINISHED", 3, {status: "COMPLETED", leaderboard: [{nickname: "Winner"}]}));
  assert.equal(h.contexts[0].oscillators.length, 12);
});
test("pause/resume and reconnect do not replay phase sound", async () => {
  const h = harness(); h.audio.update(h.state("ANSWERING")); await h.events["enable:click"](); h.audio.update(h.state("SUSPENSE"));
  h.audio.update(h.state("SUSPENSE", 2, {paused: true})); h.audio.update(h.state("SUSPENSE", 3));
  h.audio.suspend(); h.audio.update(h.state("SUSPENSE", 4)); assert.equal(h.contexts[0].oscillators.length, 12);
});
test("late recovered transition stays silent and no applause is fabricated", async () => {
  const h = harness(); h.audio.update(h.state("ANSWERING")); await h.events["enable:click"]();
  h.audio.update(h.state("SUSPENSE", 2, {server_now: "2026-10-11T00:00:10Z"})); h.audio.update(h.state("RESULTS"));
  assert.equal(h.contexts[0].oscillators.length, 0); assert.match(h.status.textContent, /Applause asset not supplied/);
});
test("champion fanfare requires actual authoritative winner; pagehide closes audio", async () => {
  const h = harness(); h.audio.update(h.state("RESULTS")); await h.events["enable:click"]();
  h.audio.update(h.state("FINISHED", 2, {status: "COMPLETED", leaderboard: [{nickname: "Winner"}]}));
  assert.equal(h.contexts[0].oscillators.length, 4); h.listeners.pagehide(); assert.equal(h.contexts[0].state, "closed");
});
test("unsupported or denied audio fails gracefully", async () => {
  for (const options of [{supported: false}, {denied: true}]) { const h = harness(options); await h.events["enable:click"](); assert.match(h.status.textContent, /unavailable/); }
});
test("host preferences cannot remotely unlock projector audio", async () => {
  const h = harness({speaker: false}); await h.events["enable:click"](); assert.equal(h.contexts.length, 0); assert.match(h.status.textContent, /projector tab/);
});
