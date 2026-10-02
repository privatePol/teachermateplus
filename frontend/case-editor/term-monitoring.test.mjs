import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script = readFileSync(new URL('../../static/faculty_attendance/term_monitoring.js', import.meta.url), 'utf8');
const base = 'http://127.0.0.1:8013';
const route = '/admin-portal/faculty-attendance/summary/';
const query = 'academic_year=3&term=4&as_of=2026-09-30&scope_tenant_id=1&scope_campus_id=1';
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const response = (payload, status = 200, type = 'application/json') => ({
  ok: status >= 200 && status < 300, status, headers: {get: () => type}, json: async () => payload,
});
const deferred = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };
function page(fetchImpl) {
  const listeners = {}, windowListeners = {}, history = [], focus = [], scroll = [], requests = [];
  const busy = new Set();
  const node = name => ({focus: () => focus.push(name), scrollIntoView: () => scroll.push(name)});
  const field = node('invalid-field');
  const content = {innerHTML: 'Old results', querySelector: () => field};
  const status = {textContent: ''};
  const error = {...node('error'), hidden: true, textContent: ''};
  const root = {setAttribute: key => busy.add(key), removeAttribute: key => busy.delete(key), addEventListener: (key, fn) => {listeners[key] = fn;}};
  const nodes = {'term-monitoring-content': content, 'term-monitoring-status': status, 'term-monitoring-error': error,
    'term-faculty-details': node('details'), 'term-summary-card': node('summary')};
  const location = {origin: base, href: base + route + '?' + query, pathname: route};
  const window = {location, AbortController, addEventListener: (key, fn) => {windowListeners[key] = fn;},
    history: {pushState: (_state, _title, url) => {history.push(url); location.href = url;}},
    fetch: async (url, options) => {requests.push({url, options}); return fetchImpl(url, options);}};
  const document = {querySelector: () => root, getElementById: id => nodes[id]};
  class FormData {constructor(form) {return new URLSearchParams(form.fields);}}
  vm.runInNewContext(script, {window, document, URL, URLSearchParams, FormData, console});
  const form = {fields: query, getAttribute: () => route, closest: () => form};
  const click = (url, modifiers = {}) => {
    let prevented = false;
    const link = {getAttribute: () => url, closest: () => link};
    listeners.click({target: link, button: 0, preventDefault: () => {prevented = true;}, ...modifiers});
    return prevented;
  };
  const submit = () => {let prevented = false; listeners.submit({target: form, preventDefault: () => {prevented = true;}}); return prevented;};
  return {window, listeners, windowListeners, form, click, submit, content, status, error, history, focus, scroll, requests, busy};
}

test('filter submit keeps all filters and scope in GET URL, announces loading and focuses summary without navigation', async () => {
  const pending = deferred(); const state = page(() => pending.promise);
  assert.equal(state.submit(), true);
  assert.match(state.status.textContent, /Loading/); assert.equal(state.busy.has('aria-busy'), true);
  assert.equal(state.requests[0].url, base + route + '?' + query);
  assert.equal(state.requests[0].options.method, 'GET');
  assert.equal(state.requests[0].options.credentials, 'same-origin');
  pending.resolve(response({ok: true, html: 'New summary', has_details: false, message: 'Summary loaded.'})); await flush();
  assert.equal(state.content.innerHTML, 'New summary'); assert.deepEqual(state.focus, ['summary']);
  assert.equal(state.busy.size, 0); assert.equal(state.history.length, 0);
});
test('detail links and links after replacement use AJAX and focus/scroll the loaded faculty details', async () => {
  const state = page(() => response({ok: true, html: 'Details', has_details: true}));
  const path = route + 'faculty/3/?' + query;
  assert.equal(state.click(path), true); await flush();
  assert.equal(state.history[0], base + path); assert.deepEqual(state.focus, ['details']); assert.deepEqual(state.scroll, ['details']);
  state.click(route + 'faculty/4/?' + query); await flush();
  assert.equal(state.requests.length, 2); assert.equal(state.history.length, 2);
});
test('Back/Forward loads the current history URL without pushing another history entry', async () => {
  const state = page(() => response({ok: true, html: 'Restored filters', has_details: false}));
  state.window.location.href = base + route + '?' + query.replace('09-30', '08-31');
  state.windowListeners.popstate(); await flush();
  assert.equal(state.requests[0].url, state.window.location.href);
  assert.equal(state.content.innerHTML, 'Restored filters'); assert.equal(state.history.length, 0);
});
test('older selections are aborted and cannot replace newer results or clear their loading state', async () => {
  const older = deferred(), newer = deferred(); let n = 0;
  const state = page(() => (++n === 1 ? older.promise : newer.promise));
  state.click(route + 'faculty/3/?' + query); state.click(route + 'faculty/4/?' + query);
  assert.equal(state.requests[0].options.signal.aborted, true);
  older.resolve(response({ok: true, html: 'STALE', has_details: true})); await flush();
  assert.equal(state.content.innerHTML, 'Old results'); assert.equal(state.busy.has('aria-busy'), true);
  newer.resolve(response({ok: true, html: 'Newest', has_details: true})); await flush();
  assert.equal(state.content.innerHTML, 'Newest'); assert.equal(state.history.length, 1);
});
test('stale JSON parsing completion cannot replace a later selection', async () => {
  const parsed = deferred(); let n = 0;
  const state = page(() => ++n === 1 ? {...response(null), json: () => parsed.promise} : response({ok: true, html: 'Newest', has_details: true}));
  state.click(route + 'faculty/3/?' + query); await flush();
  state.click(route + 'faculty/4/?' + query); await flush();
  parsed.resolve({ok: true, html: 'STALE', has_details: true}); await flush();
  assert.equal(state.content.innerHTML, 'Newest'); assert.equal(state.history.length, 1);
});
test('validation response redisplays bound filters/errors and focuses the invalid field', async () => {
  const state = page(() => response({ok: false, html: 'Bound invalid date and field error', has_details: false, message: 'Check filters.'}, 400));
  state.form.fields = query.replace('2026-09-30', 'invalid'); state.submit(); await flush();
  assert.match(state.content.innerHTML, /Bound invalid/); assert.equal(state.status.textContent, 'Check filters.');
  assert.deepEqual(state.focus, ['invalid-field']); assert.match(state.history[0], /as_of=invalid/);
});
test('network, non-JSON, denied and invalid JSON responses show actionable errors without replacing results/history', async () => {
  for (const impl of [() => {throw new Error('network offline');}, () => response({}, 502, 'text/html'),
    () => response({ok: false, message: 'Permission denied'}, 403), () => ({...response(null), json: async () => {throw new Error('Invalid JSON');}})]) {
    const state = page(impl); state.click(route + 'faculty/3/?' + query); await flush();
    assert.equal(state.content.innerHTML, 'Old results'); assert.equal(state.history.length, 0);
    assert.equal(state.error.hidden, false); assert.match(state.error.textContent, /Retry or reload/);
    assert.deepEqual(state.focus, ['error']); assert.match(state.status.textContent, /not updated/);
  }
});
test('modified clicks/cross-origin links keep normal navigation; no fetch keeps GET fallback untouched', async () => {
  const state = page(() => response({ok: true, html: ''}));
  assert.equal(state.click(route + 'faculty/3/?' + query, {ctrlKey: true}), false);
  assert.equal(state.click('https://example.com/', {}), false); assert.equal(state.requests.length, 0);
  const handlers = [];
  vm.runInNewContext(script, {window: {history: {pushState() {}}}, document: {querySelector: () => ({addEventListener: (...args) => handlers.push(args)})}});
  assert.equal(handlers.length, 0);
});
