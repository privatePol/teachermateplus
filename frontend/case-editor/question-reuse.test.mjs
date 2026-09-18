import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(new URL('../../static/js/departmental_exam_question_reuse.js', import.meta.url), 'utf8');
const item = (token, size = 1, kind = 'question') => `<article id="reuse-card-${kind}-${token}" data-reuse-item data-reuse-token="${token}" data-size="${size}" data-kind="${kind}"><input type="checkbox" data-reuse-checkbox value="${token}"><div data-reuse-index-title>${kind === 'case' ? 'Whole Case' : 'Question'} ${token}</div>${kind === 'case' ? Array.from({ length: size }, (_, index) => `<div id="reuse-member-${token}-${index + 1}" data-reuse-member><div class="reuse-question-stem">Linked ${index + 1}</div></div>`).join('') : ''}</article>`;

function page({ remaining = 3, nextPage = 2, narrow = false } = {}) {
  const dom = new JSDOM(`<!doctype html><nav class="faculty-topbar"></nav><div data-reuse-layout class="reuse-layout index-collapsed"><form id="reuse-filters"><input name="search" value=""></form>
    <form data-reuse-form data-remaining="${remaining}" data-next-page="${nextPage}">
      <div data-reuse-toolbar>
      <input type="checkbox" data-reuse-select-all><button type="button" data-reuse-selected-only></button>
      <span data-reuse-selected-count></span><span data-reuse-case-count></span>
      <p data-reuse-overflow hidden></p><button data-reuse-copy disabled>Copy</button></div>
      <div data-reuse-cards>${item('q1')}${item('c2', 2, 'case')}</div>
      <p data-reuse-no-results hidden></p><p data-reuse-selected-empty hidden></p>
      <div data-reuse-sentinel></div><p data-reuse-load-status></p><button type="button" data-reuse-retry hidden>Retry</button>
      <button data-reuse-copy disabled>Copy</button>
    </form><aside data-reuse-index-shell><section data-reuse-index-panel><div class="reuse-index-heading"><button type="button" data-reuse-index-close>Close</button></div><div data-reuse-index-list></div><p data-reuse-index-empty hidden></p></section><button type="button" data-reuse-index-reopen aria-expanded="false">Open</button></aside></div><button data-reuse-back-top hidden></button>`, {
    url: 'https://local.test/reuse/', runScripts: 'outside-only',
  });
  let observer;
  dom.window.IntersectionObserver = class {
    constructor(callback) { observer = callback; }
    observe() {}
  };
  const requests = [];
  const scrolls = [];
  dom.window.scrollTo = options => { scrolls.push(options); };
  dom.window.matchMedia = query => ({ matches: query.includes('1199.98') ? narrow : false, addEventListener() {} });
  dom.window.fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({ url, options, resolve, reject });
  });
  dom.window.eval(script);
  return { dom, requests, scrolls, load: () => observer([{ isIntersecting: true }]) };
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

test('index numbers loaded questions, includes Case members, and follows lazy and selected views', async () => {
  const { dom, requests, load } = page();
  const { document } = dom.window;
  const entries = () => [...document.querySelectorAll('[data-reuse-index-target]')];
  assert.deepEqual(entries().map(entry => entry.dataset.reuseIndexTarget), [
    'reuse-card-question-q1', 'reuse-card-case-c2', 'reuse-member-c2-1', 'reuse-member-c2-2',
  ]);
  assert.match(entries()[0].textContent, /^1\. Question q1/);
  assert.match(entries()[2].textContent, /^2\. Linked 1/);
  assert.match(entries()[3].textContent, /^3\. Linked 2/);
  const caseBox = document.querySelector('[data-reuse-token="c2"] [data-reuse-checkbox]');
  caseBox.checked = true; change(dom, caseBox);
  assert.equal(entries().filter(entry => entry.querySelector('.reuse-index-selected')).length, 3);
  document.querySelector('[data-reuse-selected-only]').click();
  assert.equal(entries().length, 3);
  load();
  assert.equal(requests.length, 0);
  document.querySelector('[data-reuse-selected-only]').click();
  load();
  requests[0].resolve({ ok: true, json: async () => ({ html: item('q1') + item('q3'), next_page: null }) });
  await tick();
  assert.equal(entries().length, 5);
  assert.equal(entries().filter(entry => entry.dataset.reuseIndexTarget === 'reuse-card-question-q1').length, 1);
  assert.match(entries()[4].textContent, /^4\. Question q3/);
});

test('index jumps target standalone and linked questions without changing copy selection', () => {
  const { dom, scrolls } = page();
  const { document } = dom.window;
  let submitted = 0;
  document.querySelector('[data-reuse-form]').addEventListener('submit', () => { submitted += 1; });
  document.querySelector('[data-reuse-index-target="reuse-card-question-q1"]').click();
  document.querySelector('[data-reuse-index-target="reuse-member-c2-2"]').click();
  assert.equal(scrolls.length, 2);
  assert.ok(document.getElementById('reuse-member-c2-2').classList.contains('reuse-nav-flash'));
  assert.deepEqual([...document.querySelectorAll('[data-reuse-checkbox]')].map(box => box.checked), [false, false]);
  assert.equal(submitted, 0);
});

test('current-reading entry updates and reveals itself only within the index', async () => {
  const { dom, scrolls } = page();
  const { document } = dom.window;
  const rect = (top, bottom) => ({ top, bottom, height: bottom - top });
  document.querySelector('.faculty-topbar').getBoundingClientRect = () => rect(0, 80);
  document.querySelector('[data-reuse-toolbar]').getBoundingClientRect = () => rect(80, 140);
  document.getElementById('reuse-card-question-q1').getBoundingClientRect = () => rect(-300, -100);
  document.getElementById('reuse-card-case-c2').getBoundingClientRect = () => rect(-50, 600);
  let memberTop = 170;
  document.getElementById('reuse-member-c2-1').getBoundingClientRect = () => rect(memberTop, memberTop + 100);
  document.getElementById('reuse-member-c2-2').getBoundingClientRect = () => rect(500, 600);
  const panel = document.querySelector('[data-reuse-index-panel]');
  panel.getBoundingClientRect = () => rect(0, 300);
  document.querySelector('.reuse-index-heading').getBoundingClientRect = () => rect(0, 40);
  const caseEntry = document.querySelector('[data-reuse-index-target="reuse-card-case-c2"]');
  caseEntry.getBoundingClientRect = () => rect(310, 350);
  dom.window.dispatchEvent(new dom.window.Event('resize'));
  dom.window.dispatchEvent(new dom.window.Event('scroll'));
  await tick();
  assert.equal(document.querySelector('[aria-current="location"]').dataset.reuseIndexTarget, 'reuse-card-case-c2');
  assert.equal(panel.scrollTop, 54);
  const memberEntry = document.querySelector('[data-reuse-index-target="reuse-member-c2-1"]');
  memberEntry.getBoundingClientRect = () => rect(10, 30);
  memberTop = 100;
  dom.window.dispatchEvent(new dom.window.Event('scroll'));
  await tick();
  assert.equal(document.querySelector('[aria-current="location"]').dataset.reuseIndexTarget, 'reuse-member-c2-1');
  assert.equal(panel.scrollTop, 20);
  assert.equal(scrolls.length, 0);
});

test('index starts closed on narrow screens, reopens, and closes after a jump', () => {
  const { dom, scrolls } = page({ narrow: true });
  const { document } = dom.window;
  const layout = document.querySelector('[data-reuse-layout]');
  const reopen = document.querySelector('[data-reuse-index-reopen]');
  assert.ok(layout.classList.contains('index-collapsed'));
  reopen.click();
  assert.equal(reopen.getAttribute('aria-expanded'), 'true');
  document.querySelector('[data-reuse-index-target="reuse-member-c2-1"]').click();
  assert.equal(scrolls.length, 1);
  assert.ok(layout.classList.contains('index-collapsed'));
  assert.equal(reopen.getAttribute('aria-expanded'), 'false');
  reopen.click();
  document.querySelector('[data-reuse-index-close]').click();
  assert.ok(layout.classList.contains('index-collapsed'));
});

test('returning from selected view restores the card anchor after last uncheck; filters invalidate it', () => {
  const { dom, scrolls } = page();
  const { document } = dom.window;
  const firstCard = document.getElementById('reuse-card-question-q1');
  let firstTop = 100;
  firstCard.getBoundingClientRect = () => ({ top: firstTop, bottom: firstTop + 100 });
  document.getElementById('reuse-card-case-c2').getBoundingClientRect = () => ({ top: 500, bottom: 600 });
  const firstBox = firstCard.querySelector('[data-reuse-checkbox]');
  firstBox.checked = true; change(dom, firstBox);
  const toggle = document.querySelector('[data-reuse-selected-only]');
  toggle.click();
  firstBox.checked = false; change(dom, firstBox);
  assert.equal(document.querySelector('[data-reuse-selected-empty]').hidden, false);
  firstTop = 300;
  toggle.click();
  assert.equal(scrolls.at(-1).top, 200);
  scrolls.length = 0;
  toggle.click();
  document.querySelector('#reuse-filters input').dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  toggle.click();
  assert.equal(scrolls.length, 0);
});
