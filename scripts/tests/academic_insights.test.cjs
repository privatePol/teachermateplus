const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const calls = [], handlers = {}, windowHandlers = {}, formHandlers = {};
  class Element {
    constructor() { this.children = []; this.dataset = {}; this.hidden = true; this.value = ''; }
    replaceChildren(...children) { this.innerHTML = ''; this.children = children; }
    add(option) { this.children.push(option); }
    close() { this.open = false; }
    showModal() { this.open = true; }
    addEventListener(event, callback) { this[event] = callback; }
    setAttribute(name, value) { (this.attributes ||= {})[name] = value; }
  }
  const elements = {};
  for (const id of ['insights-filters', 'insights-summary', 'insights-classes', 'insights-detail', 'insights-dialog', 'insights-message', 'insights-progress', 'insights-view', 'insights-selected', 'insights-close', 'insights-retry-selections']) elements[id] = new Element();
  const form = elements['insights-filters'];
  const selects = ['academic_year_id', 'term_id', 'campus_id', 'department_id', 'course_code', 'faculty_id', 'period_code'].map(name => Object.assign(new Element(), {name}));
  form.querySelectorAll = selector => selector === 'select' ? selects : [];
  form.elements = {namedItem: name => selects.find(select => select.name === name)};
  form.reportValidity = () => true;
  form.addEventListener = (event, callback) => formHandlers[event] = callback;
  elements['insights-selected'].textContent = JSON.stringify({academic_year_id: 1, term_id: 1, period_code: 'PRELIM', campus_id: 1});
  class Data extends Map {
    constructor() { super(selects.map(select => [select.name, select.value])); this.set('csrfmiddlewaretoken', 'csrf-test'); }
  }
  const exports = ['sections', 'students'].map(kind => Object.assign(new Element(), {dataset: {export: kind}}));
  const context = {URLSearchParams, FormData: Data, Option: function(label, value) { this.label = label; this.value = value; }, AbortController,
    document: {getElementById: id => elements[id], querySelectorAll: () => exports, addEventListener: (event, callback) => handlers[event] = callback},
    location: {pathname: '/admin-portal/grading/performance-insights/'},
    window: {addEventListener: (event, callback) => windowHandlers[event] = callback},
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/admin_portal/academic_insights.js'), 'utf8'), context);
  function respond(index, result, status = 200, type = 'application/json') {
    calls[index].resolve({status, ok: status < 400, headers: {get: () => type}, json: async () => result});
  }
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const submit = () => formHandlers.submit({preventDefault() {}});
  const click = (kind, value) => handlers.click({target: {closest: selector => selector === `[data-${kind}]` ? {dataset: {[kind]: value}} : null}});
  const exportClick = () => handlers.click({preventDefault() { exports[0].prevented = true; }, target: {closest: selector => selector === '[data-export]' ? exports[0] : null}});
  return {elements, selects, calls, exports, respond, tick, submit, click, exportClick, formHandlers, windowHandlers};
}

test('initial load and history restoration never fetch reports', async () => {
  const h = harness();
  assert.equal(h.calls.length, 0);
  h.windowHandlers.pageshow({persisted: true});
  assert.equal(h.calls.length, 0);
  assert.match(h.elements['insights-message'].textContent, /Select your filters/);
  assert.match(h.exports[0].href, /export=sections/);
  assert.doesNotMatch(h.exports[0].href, /csrf/);
});

test('only View Performance submits a report; classes and details load on demand', async () => {
  const h = harness();
  const submitted = h.submit();
  assert.equal(h.calls[0].options.body.get('action'), 'report');
  assert.equal(h.calls[0].options.body.get('csrfmiddlewaretoken'), 'csrf-test');
  assert.equal(h.elements['insights-progress'].hidden, false);
  h.respond(0, {token: 'snapshot', html: '<summary>'});
  await submitted;
  assert.equal(h.calls.length, 1);
  assert.equal(h.elements['insights-summary'].innerHTML, '<summary>');
  assert.equal(h.elements['insights-progress'].hidden, true);
  const page = h.click('page', '2');
  assert.equal(h.calls[1].options.body.get('token'), 'snapshot');
  h.respond(1, {html: '<classes>'}); await page;
  assert.equal(h.elements['insights-classes'].innerHTML, '<classes>');
  const detail = h.click('detail', '8');
  h.respond(2, {html: '<details>'}); await detail;
  assert.equal(h.elements['insights-dialog'].open, true);
});

test('filter changes clear results and discard a late report even when abort is ignored', async () => {
  const h = harness();
  const submitted = h.submit();
  const changed = h.formHandlers.change();
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.equal(h.calls[1].options.body.get('action'), 'options');
  h.respond(0, {token: 'stale', html: '<stale>'});
  await submitted;
  assert.equal(h.elements['insights-summary'].innerHTML, '');
  h.respond(1, {options: {faculty_id: [{value: 2, label: 'New faculty'}]}, filters: {faculty_id: 2}});
  await changed;
  assert.equal(h.selects.find(select => select.name === 'faculty_id').value, 2);
  assert.equal(h.calls.length, 2);
  assert.equal(h.elements['insights-view'].disabled, false);
});

test('outdated options and page responses cannot overwrite a newer selection', async () => {
  const h = harness();
  let pending = h.submit(); h.respond(0, {token: 'snapshot', html: '<summary>'}); await pending;
  const page = h.click('page', '1');
  const first = h.formHandlers.change();
  const second = h.formHandlers.change();
  h.respond(1, {html: '<stale classes>'}); await page;
  h.respond(2, {options: {}, filters: {}}); await first;
  assert.equal(h.elements['insights-classes'].innerHTML, '');
  assert.equal(h.elements['insights-view'].disabled, true);
  h.respond(3, {options: {}, filters: {}}); await second;
  assert.equal(h.elements['insights-view'].disabled, false);
});

test('failures and expired snapshots show an error and finish the progress indicator', async () => {
  const h = harness();
  const pending = h.submit();
  h.respond(0, {error: 'Report expired'}, 409); await pending;
  assert.equal(h.elements['insights-progress'].hidden, true);
  assert.equal(h.elements['insights-view'].disabled, false);
  assert.equal(h.elements['insights-message'].textContent, 'Report expired');
  assert.equal(h.elements['insights-summary'].innerHTML, '');
});

test('503 options failure offers an explicit retry and blocks reports and exports until verified', async () => {
  const h = harness();
  h.selects.find(select => select.name === 'faculty_id').value = '7';
  const changed = h.formHandlers.change();
  h.respond(0, {}, 503, 'text/html'); await changed;
  assert.equal(h.elements['insights-progress'].hidden, true);
  assert.equal(h.elements['insights-view'].disabled, true);
  assert.equal(h.elements['insights-retry-selections'].hidden, false);
  assert.equal(h.elements['insights-retry-selections'].disabled, false);
  assert.equal(h.elements['insights-message'].textContent, 'Unable to load the selections. Please try again.');
  assert.equal(h.selects.find(select => select.name === 'faculty_id').value, '7');
  await h.submit(); await h.exportClick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.exports[0].prevented, true);
  assert.equal(h.exports[0].attributes['aria-disabled'], 'true');
  const retry = h.elements['insights-retry-selections'].click();
  assert.equal(h.calls[1].options.body.get('action'), 'options');
  assert.equal(h.elements['insights-retry-selections'].disabled, true);
  await h.elements['insights-retry-selections'].click();
  assert.equal(h.calls.length, 2);
  h.respond(1, {options: {faculty_id: [{value: '7', label: 'Authorized faculty'}]}, filters: {faculty_id: '7'}}); await retry;
  assert.equal(h.elements['insights-view'].disabled, false);
  assert.equal(h.elements['insights-retry-selections'].hidden, true);
  assert.equal(h.exports[0].attributes['aria-disabled'], 'false');
  assert.equal(h.calls.length, 2); // verification does not generate a report
  const report = h.submit();
  h.respond(2, {token: 'fresh', html: '<fresh>'}); await report;
  assert.equal(h.calls[2].options.body.get('action'), 'report');
});

test('JSON HTTP failures and network errors keep retry usable without changing valid choices', async () => {
  const h = harness();
  const changed = h.formHandlers.change();
  h.respond(0, {error: 'Service unavailable'}, 503); await changed;
  const retry = h.elements['insights-retry-selections'].click();
  h.calls[1].reject(new Error('Network unavailable')); await retry;
  assert.equal(h.elements['insights-retry-selections'].hidden, false);
  assert.equal(h.elements['insights-retry-selections'].disabled, false);
  assert.equal(h.elements['insights-view'].disabled, true);
  assert.equal(h.elements['insights-progress'].hidden, true);
  assert.equal(h.elements['insights-message'].textContent, 'Unable to load the selections. Please try again.');
});

test('an outdated options error cannot disable controls or expose retry after a newer success', async () => {
  const h = harness();
  const old = h.formHandlers.change();
  const current = h.formHandlers.change();
  h.respond(1, {options: {}, filters: {}}); await current;
  h.respond(0, {error: 'Old failure'}, 503); await old;
  assert.equal(h.elements['insights-view'].disabled, false);
  assert.equal(h.elements['insights-retry-selections'].hidden, true);
  assert.equal(h.elements['insights-progress'].hidden, true);
  assert.doesNotMatch(h.elements['insights-message'].textContent, /failure|Unable/);
});

test('outdated retry success cannot restore controls or selections during a newer request', async () => {
  const h = harness();
  const failed = h.formHandlers.change();
  h.respond(0, {}, 503); await failed;
  const retry = h.elements['insights-retry-selections'].click();
  const current = h.formHandlers.change();
  h.respond(1, {options: {faculty_id: [{value: 'old', label: 'Old'}]}, filters: {faculty_id: 'old'}}); await retry;
  assert.equal(h.elements['insights-view'].disabled, true);
  assert.equal(h.elements['insights-progress'].hidden, false);
  assert.notEqual(h.selects.find(select => select.name === 'faculty_id').value, 'old');
  h.respond(2, {options: {faculty_id: [{value: 'new', label: 'New'}]}, filters: {faculty_id: 'new'}}); await current;
  assert.equal(h.selects.find(select => select.name === 'faculty_id').value, 'new');
  assert.equal(h.elements['insights-view'].disabled, false);
});

test('records-changed responses clear the entire report and discard concurrent late details', async () => {
  const h = harness();
  const generated = h.submit(); h.respond(0, {token: 'old-generation', html: '<old summary>'}); await generated;
  assert.match(h.exports[0].href, /token=old-generation/);
  const detail = h.click('detail', '2');
  const page = h.click('page', '1');
  const message = 'Academic records have changed. Please click View Performance to refresh the report.';
  h.respond(2, {code: 'records_changed', error: message}, 409); await page;
  h.respond(1, {html: '<old detail>'}); await detail;
  for (const id of ['insights-summary', 'insights-classes', 'insights-detail']) assert.equal(h.elements[id].innerHTML, '');
  assert.equal(h.elements['insights-message'].textContent, message);
  assert.equal(h.elements['insights-view'].disabled, false);
  assert.equal(h.elements['insights-progress'].hidden, true);
  assert.doesNotMatch(h.exports[0].href, /token=/);
  const refreshed = h.submit(); h.respond(3, {token: 'new-generation', html: '<new summary>'}); await refreshed;
  assert.equal(h.elements['insights-summary'].innerHTML, '<new summary>');
});

test('history restoration retries unverified selections without generating a report', async () => {
  const h = harness();
  const old = h.formHandlers.change();
  h.windowHandlers.pagehide();
  h.windowHandlers.pageshow({persisted: true});
  h.respond(0, {options: {}, filters: {}}); await old;
  assert.equal(h.elements['insights-view'].disabled, true);
  assert.equal(h.calls[1].options.body.get('action'), 'options');
  h.respond(1, {options: {}, filters: {}}); await h.tick();
  assert.equal(h.elements['insights-view'].disabled, false);
  assert.equal(h.calls.length, 2);
});
