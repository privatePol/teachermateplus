import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {JSDOM} from 'jsdom';

const script = readFileSync(new URL('../../static/faculty_attendance/round_encoding.js', import.meta.url), 'utf8');
const route = 'http://127.0.0.1:8013/admin-portal/faculty-attendance/rounds/example/';
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };
const card = (id, value = '', error = '') => `<article id="meeting-row-${id}" tabindex="-1" data-meeting-row data-meeting-id="${id}">
  <form data-row-save method="post" action="${route}"><input name="action" type="hidden" value="exception">
  <input name="meeting_id" value="${id}" type="hidden"><input name="csrfmiddlewaretoken" value="test-csrf" type="hidden">
  <input name="late_minutes" value="${value}"><button type="submit" data-save-label="Save finding">Save finding</button>
  <div data-row-feedback role="status" aria-live="polite">${error}</div></form></article>`;

function page({mobile = false, fetchImpl} = {}) {
  const dom = new JSDOM(`<main data-daily-round><details data-round-index open><summary>Attendance index</summary>
    <nav><a data-round-index-link href="#meeting-row-1">Course (Section) — Room 201</a>
    <a data-round-index-link href="#meeting-row-2">Course (Section) — Room 202</a></nav></details>
    <span data-count="unverified">2</span><span data-count="exception">0</span>${card(1)}${card(2, '17')}</main>`,
    {url: route, runScripts: 'outside-only'});
  const {window} = dom;
  const requests = [], scroll = [];
  const media = {matches: mobile, addEventListener: (_name, fn) => {media.change = fn;}};
  window.matchMedia = query => query.includes('991.98px') ? media : {matches: false};
  window.HTMLElement.prototype.scrollIntoView = function (options) { scroll.push({id: this.id, options}); };
  window.fetch = fetchImpl ? async (...args) => {requests.push(args); return fetchImpl(...args);} : undefined;
  window.eval(script);
  const submit = id => {
    const form = window.document.querySelector(`#meeting-row-${id} form`);
    const event = new window.Event('submit', {bubbles: true, cancelable: true});
    form.dispatchEvent(event);
    return event;
  };
  return {window, dom, requests, scroll, media, submit, document: window.document};
}

test('desktop index focuses the actual card and scrolls without covering a form', () => {
  const p = page();
  assert.equal(p.document.querySelector('[data-round-index]').open, true);
  p.document.querySelector('[href="#meeting-row-2"]').click();
  assert.equal(p.document.activeElement.id, 'meeting-row-2');
  assert.deepEqual(p.scroll.map(s => s.id), ['meeting-row-2']);
  assert.equal(p.scroll[0].options.block, 'start');
  p.dom.window.close();
});

test('mobile index starts collapsed, can expand, and closes after navigation', () => {
  const p = page({mobile: true});
  const index = p.document.querySelector('[data-round-index]');
  assert.equal(index.open, false);
  index.querySelector('summary').click();
  assert.equal(index.open, true);
  p.document.querySelector('[href="#meeting-row-1"]').click();
  assert.equal(index.open, false);
  assert.equal(p.document.activeElement.id, 'meeting-row-1');
  p.media.matches = false;
  p.media.change();
  assert.equal(index.open, true);
  p.dom.window.close();
});

test('AJAX replacement retains the index target, CSRF, other input and visible counts', async () => {
  const p = page({fetchImpl: async () => ({ok: true, json: async () => ({
    row_html: card(1, '5'), counts: {unverified: 1, exception: 1}, message: 'Finding saved.',
  })})});
  assert.equal(p.submit(1).defaultPrevented, true);
  await flush();
  assert.equal(p.requests.length, 1);
  assert.equal(p.requests[0][0], route);
  assert.equal(p.requests[0][1].body.get('csrfmiddlewaretoken'), 'test-csrf');
  assert.equal(p.requests[0][1].headers['X-Requested-With'], 'XMLHttpRequest');
  assert.equal(p.document.querySelector('#meeting-row-2 [name=late_minutes]').value, '17');
  assert.equal(p.document.querySelector('[data-count=unverified]').textContent, '1');
  assert.equal(p.document.querySelector('[data-count=exception]').textContent, '1');
  p.document.querySelector('[href="#meeting-row-1"]').click();
  assert.equal(p.document.activeElement.id, 'meeting-row-1');
  assert.equal(p.document.querySelector('#meeting-row-1 [data-row-feedback]').textContent, 'Finding saved.');
  p.dom.window.close();
});

test('server validation preserves the card anchor and reports an error without success', async () => {
  const p = page({fetchImpl: async () => ({ok: false, json: async () => ({
    row_html: card(1, '-1', 'Minutes cannot be negative.'),
    counts: {unverified: 2, exception: 0}, message: 'Save was not completed. Review the highlighted fields.',
  })})});
  p.submit(1);
  await flush();
  const feedback = p.document.querySelector('#meeting-row-1 [data-row-feedback]');
  assert.equal(feedback.classList.contains('text-danger'), true);
  assert.equal(feedback.classList.contains('text-success'), false);
  assert.match(feedback.textContent, /not completed/);
  assert.equal(p.document.querySelector('#meeting-row-1 [name=late_minutes]').value, '-1');
  assert.equal(p.document.querySelector('[data-count=exception]').textContent, '0');
  p.document.querySelector('[href="#meeting-row-1"]').click();
  assert.equal(p.document.activeElement.id, 'meeting-row-1');
  p.dom.window.close();
});

test('network and non-JSON errors never report success and allow normal retry', async () => {
  for (const fetchImpl of [async () => {throw new Error('offline');},
    async () => ({ok: false, json: async () => {throw new SyntaxError('HTML error response');}})]) {
    const p = page({fetchImpl});
    p.submit(1);
    await flush();
    const form = p.document.querySelector('#meeting-row-1 form');
    assert.match(form.querySelector('[data-row-feedback]').textContent, /Refresh and verify/);
    assert.equal(form.querySelector('[data-row-feedback]').classList.contains('text-success'), false);
    assert.equal(form.querySelector('button').disabled, false);
    assert.equal(form.dataset.saving, 'false');
    p.dom.window.close();
  }
});

test('normal POST fallback is preserved without fetch', () => {
  const p = page();
  assert.equal(p.submit(1).defaultPrevented, false);
  assert.equal(p.document.querySelector('#meeting-row-1 form').method, 'post');
  assert.equal(p.document.querySelector('#meeting-row-1 form').getAttribute('action'), route);
  p.dom.window.close();
});
