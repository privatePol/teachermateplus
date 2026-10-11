import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import {JSDOM, VirtualConsole} from "jsdom";

const read = (path) => fs.readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
const css = read("static/css/quitizz.css");
// Exact relevant Bootstrap 5.3.3 declarations, verified against the template's
// pinned CDN asset. Keep the important utility even when it is loaded last.
const bootstrap = process.env.QUITIZZ_TEST_BOOTSTRAP_CSS
  ? fs.readFileSync(process.env.QUITIZZ_TEST_BOOTSTRAP_CSS, "utf8")
  : `:root { --bs-secondary-color: rgba(33, 37, 41, 0.75); }
.text-muted { --bs-text-opacity: 1; color: var(--bs-secondary-color) !important; }
.btn:focus-visible { color: var(--bs-btn-hover-color); background-color: var(--bs-btn-hover-bg); border-color: var(--bs-btn-hover-border-color); outline: 0; box-shadow: var(--bs-btn-focus-box-shadow); }`;

// jsdom parses CSSOM/DOM but does not implement browser keyboard modality or a
// reliable complete cascade. Resolve the actual matching declarations below,
// including !important, specificity (:is/:not/:where) and source order. This
// deliberately is not a claim about browser-computed styles or painted pixels.
function splitSelectors(text) {
  const parts = []; let depth = 0, start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "(" || text[i] === "[") depth++;
    if (text[i] === ")" || text[i] === "]") depth--;
    if (text[i] === "," && !depth) { parts.push(text.slice(start, i).trim()); start = i + 1; }
  }
  parts.push(text.slice(start).trim()); return parts;
}
const compare = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
function specificity(selector) {
  const result = [0, 0, 0];
  for (let i = 0; i < selector.length;) {
    const rest = selector.slice(i);
    const name = rest.match(/^[#.][\w-]+/);
    if (name) { result[name[0][0] === "#" ? 0 : 1]++; i += name[0].length; continue; }
    if (rest[0] === "[") { result[1]++; i += rest.indexOf("]") + 1; continue; }
    const pseudo = rest.match(/^(:{1,2})([\w-]+)/);
    if (pseudo) {
      i += pseudo[0].length;
      if (selector[i] !== "(") { result[pseudo[1] === "::" ? 2 : 1]++; continue; }
      const start = ++i; let depth = 1;
      while (depth) { if (selector[i] === "(") depth++; if (selector[i] === ")") depth--; i++; }
      if (["is", "not", "has"].includes(pseudo[2])) {
        const maximum = splitSelectors(selector.slice(start, i - 1)).map(specificity).sort(compare).at(-1);
        maximum.forEach((value, index) => { result[index] += value; });
      } else if (pseudo[2] !== "where") result[1]++;
      continue;
    }
    const tag = rest.match(/^[\w-]+/);
    if (tag) { result[2]++; i += tag[0].length; } else i++;
  }
  return result;
}
function fixture(markup, styles = [bootstrap, css]) {
  const errors = [], console = new VirtualConsole(); console.on("jsdomError", (error) => errors.push(error.message));
  const dom = new JSDOM(`<!doctype html><html><head></head><body>${markup}</body></html>`, {virtualConsole: console});
  for (const text of styles) {
    const style = dom.window.document.createElement("style"); style.textContent = text; dom.window.document.head.append(style);
  }
  assert.deepEqual(errors, []);
  const declarations = [];
  for (const sheet of dom.window.document.styleSheets) {
    for (const rule of sheet.cssRules) {
      // The properties under test have no media overrides; exclude keyframes
      // and media conditions instead of pretending to compute a viewport.
      if (rule.type !== 1) continue;
      for (const selector of splitSelectors(rule.selectorText)) {
        // Pseudo-elements and vendor input/select states cannot style the
        // paragraph or command buttons; jsdom lacks these vendor selectors.
        if (/::|:-webkit-autofill|\.form-select:-moz-focusring/.test(selector)) continue;
        declarations.push({selector, style: rule.style, specificity: specificity(selector)});
      }
    }
  }
  function winner(element, property) {
    let best;
    for (const declaration of declarations) {
      // A border shorthand participates in the border-color cascade too.
      const value = declaration.style.getPropertyValue(property) || (property === "border-color"
        ? declaration.style.getPropertyValue("border").match(/#[\da-f]+$/i)?.[0] : "");
      if (!value) continue;
      let matches;
      try { matches = element.matches(declaration.selector.replaceAll(":focus-visible", "[data-keyboard-focus]").replaceAll(":hover", "[data-hover]")); }
      catch (error) { throw new Error(`CSSOM selector matching unavailable: ${declaration.selector}: ${error.message}`); }
      if (!matches) continue;
      const candidate = {...declaration, value, important: declaration.style.getPropertyPriority(property) === "important"};
      if (!best || Number(candidate.important) > Number(best.important) ||
          (candidate.important === best.important && compare(candidate.specificity, best.specificity) >= 0)) best = candidate;
    }
    return best;
  }
  return {dom, document: dom.window.document, winner, close: () => dom.window.close()};
}
function rgb(value) {
  const hex = value.replace(/^#/, "");
  assert.match(hex, /^(?:[\da-f]{3}|[\da-f]{6})$/i);
  return (hex.length === 3 ? [...hex].map((c) => c + c).join("") : hex).match(/../g).map((c) => parseInt(c, 16));
}
function luminance(color) {
  return color.map((c) => c / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
}
function contrast(a, b) { const [lo, hi] = [luminance(a), luminance(b)].sort((a, b) => a - b); return (hi + .05) / (lo + .05); }
function gradientSamples(value) {
  const stops = (value.match(/#[\da-f]{6}\b/gi) || []).map(rgb);
  assert.ok(stops.length >= 2, value);
  const samples = [];
  for (const a of stops) for (const b of stops) for (let t = 0; t <= 10; t++) samples.push(a.map((v, i) => v * t / 10 + b[i] * (1 - t / 10)));
  // A conservative channel envelope also covers layered stop mixtures and
  // transparency over the underlying gradient, even though it may not occur.
  samples.push([0, 1, 2].map((i) => Math.max(...stops.map((stop) => stop[i]))));
  return samples;
}
const connectionMarkup = read("templates/quitizz/play.html").match(/<p id="qt-connection"[^>]*>[\s\S]*?<\/p>/)[0];
const playerMarkup = `<main class="qt-player">${connectionMarkup}</main>`;
const rawCommand = read("templates/quitizz/host.html").match(/<form[^>]*id="qt-command"[\s\S]*?<\/form>/)[0];
const hostMarkup = `<div class="qt-wrap"><section id="qt-host">${rawCommand.replace(/{%[\s\S]*?%}|{{[\s\S]*?}}/g, "")}</section></div>`;

test("CSSOM cascade resolver handles important, functional specificity and source order", () => {
  assert.deepEqual(specificity("#qt-host #qt-command button:focus-visible"), [2, 1, 1]);
  assert.deepEqual(specificity("#qt-host :is(button, a, summary):focus-visible"), [1, 1, 1]);
  assert.deepEqual(specificity(".qt-wrap :where(.btn:not(#qt-copy-link))"), [0, 1, 0]);
  const h = fixture('<p id="probe" class="note"></p>', ['#probe {color: red} .note {color: blue !important} .note {color: green !important}']);
  try { assert.equal(h.winner(h.document.querySelector("p"), "color").value, "green"); } finally { h.close(); }
});
test("actual player connection ID beats Bootstrap muted important utility in either stylesheet order", () => {
  for (const styles of [[bootstrap, css], [css, bootstrap]]) {
    const h = fixture(playerMarkup + '<p id="other-status" class="text-muted">Other module</p>', styles);
    try {
      const status = h.document.querySelector("#qt-connection"), effective = h.winner(status, "color");
      assert.equal(status.classList.contains("qt-connection"), false); assert.equal(status.classList.contains("text-muted"), true);
      assert.equal(effective.selector, ".qt-player #qt-connection"); assert.equal(effective.important, true);
      assert.deepEqual(effective.specificity, [1, 1, 0]); assert.equal(effective.value, "#f4f0ff");
      assert.equal(h.winner(h.document.querySelector("#other-status"), "color").selector, ".text-muted");
      assert.equal(status.getAttribute("role"), "status"); assert.equal(status.getAttribute("aria-live"), "polite");
    } finally { h.close(); }
  }
});
test("winning player connection color clears 4.5:1 for gradient stops, mixtures and conservative envelope", () => {
  const h = fixture(playerMarkup);
  try {
    h.document.body.className = "qt-player-body";
    const effective = h.winner(h.document.querySelector("#qt-connection"), "color");
    const samples = gradientSamples(h.winner(h.document.body, "background").value);
    const minimum = Math.min(...samples.map((bg) => contrast(rgb(effective.value), bg)));
    assert.ok(minimum >= 4.5, `player connection minimum ${minimum}`);
    console.log(`QUITIZZ_CONNECTION_CASCADE_CONTRAST minimum=${minimum.toFixed(2)}:1 samples=${samples.length}; CSSOM/numerical, not browser pixels`);
  } finally { h.close(); }
});
test("actual player connection element still displays transport and HTTP-recovery messages", async () => {
  const h = fixture(playerMarkup);
  let transport; const sockets = [];
  class Socket { static OPEN = 1; constructor() { sockets.push(this); } close() { this.onclose?.(); } }
  try {
    const indicator = h.document.querySelector("#qt-connection");
    const window = {location: {href: "https://school.test/play/", protocol: "https:"}, setTimeout: () => 1, clearTimeout() {}, addEventListener() {}};
    const vm = await import("node:vm");
    vm.runInNewContext(read("static/js/quitizz_realtime.js"), {window, document: h.document, URL, WebSocket: Socket, Math});
    transport = window.QuiTizzRealtime({url: "/ws/quitizz/test/player/", indicator, recover: async () => ({version: 1})});
    assert.equal(indicator.textContent, "Connecting; HTTP recovery active.");
    sockets[0].onopen(); await Promise.resolve(); assert.equal(indicator.textContent, "Realtime connected; HTTP recovery active.");
    sockets[0].close(); assert.equal(indicator.textContent, "Reconnecting; HTTP recovery active.");
    assert.equal(h.winner(indicator, "color").value, "#f4f0ff");
  } finally { transport?.stop(); h.close(); }
});
for (const [category, action] of [["primary", "start"], ["secondary", "set_mode"], ["pause", "pause"], ["destructive", "cancel"]]) {
  test(`${category} host command wins keyboard-focus cascade and retains gradient, dimensions and disabled state`, () => {
    const h = fixture(hostMarkup, [css, bootstrap]);
    try {
      const button = h.document.querySelector(`button[value="${action}"]`), panel = h.document.querySelector("#qt-command");
      const stable = ["background", "border", "padding", "min-height", "font-weight", "opacity"];
      const before = stable.map((property) => h.winner(button, property)?.value);
      button.dataset.keyboardFocus = "true";
      const outline = h.winner(button, "outline"), shadow = h.winner(button, "box-shadow");
      assert.equal(outline.selector, "#qt-host #qt-command button:focus-visible");
      assert.equal(shadow.selector, outline.selector); assert.deepEqual(outline.specificity, [2, 1, 1]);
      assert.equal(outline.value, "3px solid #ffdc73"); assert.equal(h.winner(button, "outline-offset").value, "4px");
      assert.equal(shadow.value, "0 0 0 4px #17113c");
      assert.deepEqual(stable.map((property) => h.winner(button, property)?.value), before);
      assert.equal(button.name, "action"); assert.equal(button.disabled, false);
      const gold = rgb("#ffdc73"), keyline = rgb("#17113c");
      const panelMinimum = Math.min(...gradientSamples(h.winner(panel, "background").value).map((bg) => contrast(gold, bg)), contrast(gold, keyline));
      // Outer gold contrasts with the adjacent panel/keyline. For gold pause
      // buttons, the dark keyline distinguishes the button from the gold ring.
      const buttonMinimum = Math.min(...gradientSamples(h.winner(button, "background").value)
        .map((bg) => Math.max(contrast(gold, bg), contrast(keyline, bg))));
      assert.ok(panelMinimum >= 3, `gold outline vs panel/keyline ${panelMinimum}`);
      assert.ok(buttonMinimum >= 3, `two-color cue vs ${category} gradient ${buttonMinimum}`);
      const borderMinimum = contrast(keyline, rgb(h.winner(button, "border-color").value));
      assert.ok(borderMinimum >= 3, `keyline vs adjacent ${category} border ${borderMinimum}`);
      console.log(`QUITIZZ_HOST_FOCUS ${category} panel/keyline=${panelMinimum.toFixed(2)}:1 two-color/button=${buttonMinimum.toFixed(2)}:1 keyline/border=${borderMinimum.toFixed(2)}:1; CSSOM/numerical`);
      delete button.dataset.keyboardFocus; button.disabled = true;
      assert.equal(h.winner(button, "opacity").value, ".65"); assert.equal(h.winner(button, "box-shadow").value, "none");
      assert.equal(h.winner(button, "background").value, before[0]);
    } finally { h.close(); }
  });
}
test("host focus override stays inside command controls", () => {
  const h = fixture(hostMarkup + '<section id="other-module"><button class="btn" data-keyboard-focus>Other module</button></section>');
  try {
    assert.equal(h.winner(h.document.querySelector("#other-module button"), "outline").value, "0");
  } finally { h.close(); }
});
