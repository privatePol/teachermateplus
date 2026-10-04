import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {JSDOM} from "jsdom";

const script = readFileSync(new URL("../../static/faculty_attendance/dtr_admin_hours.js", import.meta.url), "utf8");
const row = i => `<div data-admin-hours-row><input name="admin_hours-${i}-entry_date" type="date"><input name="admin_hours-${i}-hours" type="number" step=".01"><input type="checkbox" name="admin_hours-${i}-DELETE"><button type="button" data-admin-hours-remove>Remove</button></div>`;
function page() {
  const dom = new JSDOM(`<div data-dtr-review><form data-admin-hours><input name="admin_hours-TOTAL_FORMS" value="1"><div data-admin-hours-rows>${row(0)}</div><template data-admin-hours-template>${row("__prefix__")}</template><button type="button" data-admin-hours-add>Add</button><input type="checkbox" data-admin-hours-date value="2026-01-05"><input type="checkbox" data-admin-hours-date value="2026-01-06"><input type="number" step=".01" min="0" data-admin-hours-same><button type="button" data-admin-hours-apply>Apply</button><strong data-admin-hours-total></strong><p data-admin-hours-message></p></form></div>`, {runScripts:"outside-only"});
  dom.window.eval(script);
  return dom;
}
test("multiple dates reuse rows; repeated apply does not duplicate hours", async () => {
  const dom = page(), doc = dom.window.document;
  doc.querySelectorAll("[data-admin-hours-date]").forEach(e => {e.checked = true;});
  doc.querySelector("[data-admin-hours-same]").value = "1.25";
  doc.querySelector("[data-admin-hours-apply]").click();
  doc.querySelector("[data-admin-hours-apply]").click();
  assert.equal(doc.querySelectorAll("[data-admin-hours-row]").length, 2);
  assert.equal(doc.querySelector("[data-admin-hours-total]").textContent, "2.50");
  assert.equal(doc.querySelector("[name='admin_hours-TOTAL_FORMS']").value, "2");
  await new Promise(resolve => dom.window.setTimeout(resolve, 0));
  dom.window.close();
});
test("remove marks reversal, excludes total, and preserves form identity", async () => {
  const dom = page(), doc = dom.window.document;
  const hours = doc.querySelector("[name$='-hours']");
  hours.value = "2.75";
  hours.dispatchEvent(new dom.window.Event("input", {bubbles:true}));
  assert.equal(doc.querySelector("[data-admin-hours-total]").textContent, "2.75");
  doc.querySelector("[data-admin-hours-remove]").click();
  assert.equal(doc.querySelector("[name$='-DELETE']").checked, true);
  assert.equal(doc.querySelector("[data-admin-hours-row]").hidden, true);
  assert.equal(doc.querySelector("[data-admin-hours-total]").textContent, "0.00");
  assert.equal(doc.querySelector("[name='admin_hours-TOTAL_FORMS']").value, "1");
  await new Promise(resolve => dom.window.setTimeout(resolve, 0));
  dom.window.close();
});
test("new row has independent labels and focus; invalid shortcut retains entries", async () => {
  const dom = page(), doc = dom.window.document;
  doc.querySelector("[data-admin-hours-add]").click();
  assert.equal(doc.activeElement.name, "admin_hours-1-entry_date");
  doc.querySelector("[data-admin-hours-same]").value = "-1";
  doc.querySelector("[data-admin-hours-date]").checked = true;
  doc.querySelector("[data-admin-hours-apply]").click();
  assert.match(doc.querySelector("[data-admin-hours-message]").textContent, /non-negative/);
  assert.equal(doc.querySelector("[name='admin_hours-0-hours']").value, "");
  await new Promise(resolve => dom.window.setTimeout(resolve, 0));
  dom.window.close();
});
test("delegated controls and totals survive AJAX workspace replacement", async () => {
  const dom = page(), doc = dom.window.document;
  const form = doc.querySelector("form");
  form.outerHTML = form.outerHTML;
  const hours = doc.querySelector("[name$='-hours']");
  hours.value = "1.10";
  hours.dispatchEvent(new dom.window.Event("input", {bubbles:true}));
  doc.querySelector("[data-admin-hours-add]").click();
  await new Promise(resolve => dom.window.setTimeout(resolve, 0));
  assert.equal(doc.querySelectorAll("[data-admin-hours-row]").length, 2);
  assert.equal(doc.querySelector("[data-admin-hours-total]").textContent, "1.10");
  dom.window.close();
});
