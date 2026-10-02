import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../static/faculty_attendance/checklist_route.js", import.meta.url), "utf8");

function page() {
  const events = {};
  const addEvents = {};
  const documentEvents = {};
  const inputs = [0, 1, 2, 3, 4, 5, 6].map(value => ({value, checked: value === 0 || value === 2, focus() { this.focused = true; }}));
  const summary = {textContent: "", focus() { this.focused = true; }};
  const add = {hidden: true, addEventListener(name, handler) { addEvents[name] = handler; }};
  const message = {textContent: "", setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; }};
  const picker = {
    open: true, parentElement: {querySelector: () => message},
    querySelector(selector) { return selector === "summary" ? summary : selector === "input" ? inputs[0] : add; },
    querySelectorAll: () => inputs.filter(input => input.checked),
    addEventListener(name, handler) { events[name] = handler; },
    contains: target => target === add,
  };
  const document = {
    getElementById: () => null,
    querySelectorAll: selector => selector === "[data-checklist-days]" ? [picker] : [],
    addEventListener(name, handler) { documentEvents[name] = handler; },
  };
  vm.runInNewContext(source, {document});
  return {inputs, picker, summary, add, message, events, addEvents, documentEvents};
}

test("Add confirms days and closes the picker without a form submission", () => {
  const p = page();
  assert.equal(p.summary.textContent, "Monday / Wednesday");
  p.inputs.forEach(input => { input.checked = input.value === 1 || input.value === 6; });
  let prevented = false;
  p.addEvents.click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(p.summary.textContent, "Tuesday / Sunday");
  assert.equal(p.picker.open, false);
  assert.equal(p.summary.focused, true);
  assert.match(p.message.textContent, /Click Show Checklist/);
  assert.deepEqual(p.inputs.filter(input => input.checked).map(input => input.value), [1, 6]);
});

test("Empty Add keeps the picker open and shows an actionable error", () => {
  const p = page();
  p.inputs.forEach(input => { input.checked = false; });
  p.addEvents.click({preventDefault() {}});
  assert.equal(p.picker.open, true);
  assert.equal(p.message.role, "alert");
  assert.match(p.message.textContent, /at least one day/);
  assert.equal(p.inputs[0].focused, true);
});

test("Escape and outside click close the picker without changing days", () => {
  const p = page();
  p.events.keydown({key: "Escape"});
  assert.equal(p.picker.open, false);
  assert.equal(p.summary.focused, true);
  p.picker.open = true;
  p.documentEvents.click({target: {}});
  assert.equal(p.picker.open, false);
  assert.deepEqual(p.inputs.filter(input => input.checked).map(input => input.value), [0, 2]);
});
