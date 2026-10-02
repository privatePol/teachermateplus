import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(
  new URL('../../static/js/departmental_exam_my_questions.js', import.meta.url),
  'utf8',
);

const card = key => `<article data-myq-card data-myq-key="${key}">${key}</article>`;

function fixture({ nextUrl = '/my-questions/?page=2&partial=1', evaluate = true } = {}) {
  const dom = new JSDOM(`<!doctype html>
    <form method="get"><input name="search" value=""></form>
    <section data-myq-progressive data-next-url="${nextUrl}">
      <div data-myq-list>${card('bank-1')}${card('history-question-2')}</div>
      <div data-myq-controls>
        <p data-myq-status role="status">More matching questions are available.</p>
        <span data-myq-spinner hidden></span>
        <button type="button" data-myq-load-more>Load more</button>
        <a href="/my-questions/" data-myq-restart hidden>Restart updated results</a>
        <div data-myq-sentinel></div>
      </div>
    </section>
    <nav data-myq-pagination><a href="?page=2&search=">Next</a></nav>`, {
    url: 'https://local.test/my-questions/',
    runScripts: 'outside-only',
  });
  const requests = [];
  let observerCallback;
  dom.window.IntersectionObserver = class {
    constructor(callback) { observerCallback = callback; }
    observe() {}
  };
  dom.window.fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({ url, options, resolve, reject });
  });
  if (evaluate) dom.window.eval(script);
  return {
    dom,
    requests,
    evaluate: () => dom.window.eval(script),
    intersect: () => observerCallback([{ isIntersecting: true }]),
  };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('server pagination remains usable without JavaScript', () => {
  const { dom, evaluate } = fixture({ evaluate: false });
  const pagination = dom.window.document.querySelector('[data-myq-pagination]');
  assert.equal(pagination.hidden, false);
  assert.equal(pagination.querySelector('a').getAttribute('href'), '?page=2&search=');
  evaluate();
  assert.equal(pagination.hidden, true);
});

test('scroll loading prevents duplicate requests and cards and announces the end', async () => {
  const { dom, requests, intersect } = fixture();
  const { document } = dom.window;
  intersect();
  intersect();
  assert.equal(requests.length, 1);
  assert.equal(document.querySelector('[data-myq-spinner]').hidden, false);
  assert.equal(document.querySelector('[data-myq-load-more]').disabled, true);
  requests[0].resolve({
    ok: true,
    json: async () => ({
      html: card('bank-1') + card('history-case-3'),
      next_url: null,
    }),
  });
  await tick();
  assert.deepEqual(
    [...document.querySelectorAll('[data-myq-card]')].map(node => node.dataset.myqKey),
    ['bank-1', 'history-question-2', 'history-case-3'],
  );
  assert.equal(document.querySelector('[data-myq-load-more]').hidden, true);
  assert.match(document.querySelector('[data-myq-status]').textContent, /All matching/);
  intersect();
  assert.equal(requests.length, 1);
});

test('failed loading exposes a useful manual retry and succeeds without skipping the page', async () => {
  const { dom, requests, intersect } = fixture();
  const { document } = dom.window;
  intersect();
  requests[0].resolve({ ok: false, status: 503 });
  await tick();
  const button = document.querySelector('[data-myq-load-more]');
  assert.equal(button.hidden, false);
  assert.equal(button.disabled, false);
  assert.match(button.textContent, /Retry/);
  assert.match(document.querySelector('[data-myq-status]').textContent, /Could not load/);
  button.click();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, requests[0].url);
  requests[1].resolve({
    ok: true,
    json: async () => ({ html: card('bank-4'), next_url: null }),
  });
  await tick();
  assert.ok(document.querySelector('[data-myq-key="bank-4"]'));
  assert.match(document.querySelector('[data-myq-status]').textContent, /All matching/);
});

test('submitting changed filters aborts an in-flight old result set', () => {
  const { dom, requests, intersect } = fixture();
  const { document, Event } = dom.window;
  intersect();
  assert.equal(requests.length, 1);
  document.querySelector('input[name="search"]').value = 'new filter';
  document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.equal(requests[0].options.signal.aborted, true);
});

test('an old-filter response resolving after json starts cannot mutate the new result set', async () => {
  const { dom, requests, intersect } = fixture();
  const { document, Event } = dom.window;
  let resolveJson;
  intersect();
  requests[0].resolve({
    ok: true,
    status: 200,
    json: () => new Promise(resolve => { resolveJson = resolve; }),
  });
  await tick();
  document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.equal(requests[0].options.signal.aborted, true);
  resolveJson({
    html: card('bank-stale'),
    next_url: '/my-questions/?page=3&catalogue=old&partial=1',
  });
  await tick();
  assert.equal(document.querySelector('[data-myq-key="bank-stale"]'), null);
  assert.equal(document.querySelector('[data-myq-progressive]').dataset.nextUrl, '');
});

test('a rejected old JSON response cannot change the new filter state', async () => {
  const { dom, requests, intersect } = fixture();
  const { document, Event } = dom.window;
  let rejectJson;
  intersect();
  requests[0].resolve({
    ok: true,
    status: 200,
    json: () => new Promise((_resolve, reject) => { rejectJson = reject; }),
  });
  await tick();
  document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }));
  const root = document.querySelector('[data-myq-progressive]');
  const button = document.querySelector('[data-myq-load-more]');
  const status = document.querySelector('[data-myq-status]');
  const before = {
    cards: [...document.querySelectorAll('[data-myq-card]')].map(node => node.dataset.myqKey),
    nextUrl: root.dataset.nextUrl,
    buttonHidden: button.hidden,
    buttonDisabled: button.disabled,
    buttonText: button.textContent,
    statusText: status.textContent,
    spinnerHidden: document.querySelector('[data-myq-spinner]').hidden,
    busy: root.hasAttribute('aria-busy'),
  };
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(before.statusText, 'Applying filters...');

  rejectJson(new Error('late JSON failure'));
  await tick();

  assert.deepEqual({
    cards: [...document.querySelectorAll('[data-myq-card]')].map(node => node.dataset.myqKey),
    nextUrl: root.dataset.nextUrl,
    buttonHidden: button.hidden,
    buttonDisabled: button.disabled,
    buttonText: button.textContent,
    statusText: status.textContent,
    spinnerHidden: document.querySelector('[data-myq-spinner]').hidden,
    busy: root.hasAttribute('aria-busy'),
  }, before);
});

test('a changed catalogue exposes a visible restart instead of appending stale cards', async () => {
  const { dom, requests, intersect } = fixture();
  const { document } = dom.window;
  intersect();
  requests[0].resolve({
    ok: false,
    status: 409,
    json: async () => ({
      catalogue_changed: true,
      message: 'Results changed. Restart.',
      restart_url: '/my-questions/?search=current&catalogue_changed=1',
    }),
  });
  await tick();
  const restart = document.querySelector('[data-myq-restart]');
  assert.equal(restart.hidden, false);
  assert.equal(
    restart.getAttribute('href'),
    '/my-questions/?search=current&catalogue_changed=1',
  );
  assert.equal(document.querySelector('[data-myq-load-more]').hidden, true);
  assert.match(document.querySelector('[data-myq-status]').textContent, /Results changed/);
});
