import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(new URL('../../static/js/departmental_exam_question_reuse.js', import.meta.url), 'utf8');
const item = (token, size = 1, kind = 'question') => `<article data-reuse-item data-reuse-token="${token}" data-size="${size}" data-kind="${kind}"><input type="checkbox" data-reuse-checkbox value="${token}"></article>`;

function page({ remaining = 3, nextPage = 2 } = {}) {
  const dom = new JSDOM(`<!doctype html><form id="reuse-filters"><input name="search" value=""></form>
    <form data-reuse-form data-remaining="${remaining}" data-next-page="${nextPage}">
      <input type="checkbox" data-reuse-select-all><button type="button" data-reuse-selected-only></button>
      <span data-reuse-selected-count></span><span data-reuse-case-count></span>
      <p data-reuse-overflow hidden></p><button data-reuse-copy disabled>Copy</button>
      <div data-reuse-cards>${item('q1')}${item('c2', 2, 'case')}</div>
      <p data-reuse-no-results hidden></p><p data-reuse-selected-empty hidden></p>
      <div data-reuse-sentinel></div><p data-reuse-load-status></p><button type="button" data-reuse-retry hidden>Retry</button>
      <button data-reuse-copy disabled>Copy</button>
    </form><button data-reuse-back-top hidden></button>`, {
    url: 'https://local.test/reuse/', runScripts: 'outside-only',
  });
  let observer;
  dom.window.IntersectionObserver = class {
    constructor(callback) { observer = callback; }
    observe() {}
  };
  const requests = [];
  dom.window.fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({ url, options, resolve, reject });
  });
  dom.window.eval(script);
  return { dom, requests, load: () => observer([{ isIntersecting: true }]) };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function change(dom, box) {
  box.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
}

test('whole Case size and both copy buttons share capacity and submitting state', () => {
  const { dom } = page({ remaining: 2 });
  const { document } = dom.window;
  const boxes = [...document.querySelectorAll('[data-reuse-checkbox]')];
  const buttons = [...document.querySelectorAll('[data-reuse-copy]')];
  boxes[1].checked = true; change(dom, boxes[1]);
  assert.equal(document.querySelector('[data-reuse-selected-count]').textContent, '2');
  assert.equal(document.querySelector('[data-reuse-case-count]').textContent, '1');
  assert.deepEqual(buttons.map(button => button.disabled), [false, false]);
  assert.ok(boxes[1].closest('[data-reuse-item]').classList.contains('is-selected'));
  boxes[0].checked = true; change(dom, boxes[0]);
  assert.match(document.querySelector('[data-reuse-overflow]').textContent, /Deselect 1 question/);
  assert.deepEqual(buttons.map(button => button.disabled), [true, true]);
  boxes[0].checked = false; change(dom, boxes[0]);
  const form = document.querySelector('[data-reuse-form]');
  assert.equal(form.dispatchEvent(new dom.window.Event('submit', { cancelable: true })), true);
  assert.deepEqual(buttons.map(button => button.disabled), [true, true]);
  assert.equal(form.dispatchEvent(new dom.window.Event('submit', { cancelable: true })), false);
});

test('lazy batches retain selection, discard duplicate cards, and render the end state', async () => {
  const fixture = page();
  const { dom, requests, load } = fixture;
  const { document } = dom.window;
  const first = document.querySelector('[data-reuse-checkbox]');
  first.checked = true; change(dom, first);
  load(); load();
  assert.equal(requests.length, 1);
  assert.equal(new URL(requests[0].url).searchParams.get('page'), '2');
  requests[0].resolve({ ok: true, json: async () => ({ html: item('q1') + item('q3'), next_page: null }) });
  await tick();
  assert.deepEqual([...document.querySelectorAll('[data-reuse-item]')].map(card => card.dataset.reuseToken), ['q1', 'c2', 'q3']);
  assert.equal(first.checked, true);
  assert.equal(document.querySelector('[data-reuse-selected-count]').textContent, '1');
  assert.match(document.querySelector('[data-reuse-load-status]').textContent, /All matching/);
  load();
  assert.equal(requests.length, 1);
});

test('select all affects loaded visible cards; selected view pauses loading and handles last uncheck', () => {
  const { dom, requests, load } = page();
  const { document } = dom.window;
  const all = document.querySelector('[data-reuse-select-all]');
  const toggle = document.querySelector('[data-reuse-selected-only]');
  const boxes = [...document.querySelectorAll('[data-reuse-checkbox]')];
  boxes[0].checked = true; change(dom, boxes[0]);
  toggle.click();
  assert.equal(toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(boxes[1].closest('[data-reuse-item]').hidden, true);
  load();
  assert.equal(requests.length, 0);
  all.checked = false; change(dom, all);
  assert.equal(boxes[0].checked, false);
  assert.equal(document.querySelector('[data-reuse-selected-empty]').hidden, false);
  toggle.click();
  assert.equal(boxes[1].closest('[data-reuse-item]').hidden, false);
  all.checked = true; change(dom, all);
  assert.deepEqual(boxes.map(box => box.checked), [true, true]);
  assert.equal(document.querySelector('[data-reuse-selected-count]').textContent, '3');
});

test('filter changes clear selection and ignore a stale in-flight batch', async () => {
  const { dom, requests, load } = page();
  const { document } = dom.window;
  const first = document.querySelector('[data-reuse-checkbox]');
  first.checked = true; change(dom, first);
  load();
  const search = document.querySelector('#reuse-filters input');
  search.value = 'new filter';
  search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  assert.equal(requests[0].options.signal.aborted, true);
  requests[0].resolve({ ok: true, json: async () => ({ html: item('stale'), next_page: null }) });
  await tick();
  assert.equal(document.querySelector('[data-reuse-item][data-reuse-token="stale"]'), null);
  assert.equal(first.checked, false);
  load();
  assert.equal(requests.length, 1);
});

test('entering selected-only view cancels an in-flight unselected batch', async () => {
  const { dom, requests, load } = page();
  load();
  dom.window.document.querySelector('[data-reuse-selected-only]').click();
  assert.equal(requests[0].options.signal.aborted, true);
  requests[0].resolve({ ok: true, json: async () => ({ html: item('unselected'), next_page: null }) });
  await tick();
  assert.equal(dom.window.document.querySelector('[data-reuse-token="unselected"]'), null);
  assert.match(dom.window.document.querySelector('[data-reuse-load-status]').textContent, /Return to all questions/);
});

test('failed batch exposes retry and retry requests the same page', async () => {
  const { dom, requests, load } = page();
  load();
  requests[0].reject(new Error('offline'));
  await tick();
  const retry = dom.window.document.querySelector('[data-reuse-retry]');
  assert.equal(retry.hidden, false);
  retry.click();
  assert.equal(requests.length, 2);
  assert.equal(new URL(requests[1].url).searchParams.get('page'), '2');
});

test('floating back-to-top honors reduced motion and returns to filters', () => {
  const { dom } = page();
  const { document } = dom.window;
  let options;
  document.querySelector('#reuse-filters').scrollIntoView = value => { options = value; };
  dom.window.matchMedia = () => ({ matches: true });
  Object.defineProperty(dom.window, 'scrollY', { value: 400, configurable: true });
  dom.window.dispatchEvent(new dom.window.Event('scroll'));
  const button = document.querySelector('[data-reuse-back-top]');
  assert.equal(button.hidden, false);
  button.click();
  assert.equal(options.behavior, 'auto');
  assert.equal(options.block, 'start');
});
