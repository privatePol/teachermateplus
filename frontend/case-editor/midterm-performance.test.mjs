import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const { JSDOM } = createRequire(import.meta.url)('jsdom');
const script = readFileSync(new URL('../../static/js/midterm_exam_performance.js', import.meta.url), 'utf8');
const course = (id, ids) => `<section data-course-id="${id}"><h2>COURSE ${id} | TITLE</h2><table><tbody>${ids.map(n => `<tr data-offering-id="${n}"><td>${n}</td></tr>`).join('')}</tbody></table></section>`;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function setup({ observer = false } = {}) {
  const dom = new JSDOM(`<form id="midterm-filters"><select><option>Cycle</option></select></form>
    <div id="midterm-course-groups" data-total-rows="4">${course(1, [1])}</div>
    <a id="midterm-load-more" href="?cycle_id=2&course_code=IS&row_page=2">Load more</a>
    <button id="midterm-load-all" hidden>Load All</button><button id="midterm-cancel" hidden>Cancel</button>
    <progress id="midterm-load-progress"></progress><div id="midterm-scroll-sentinel"></div>
    <dialog id="midterm-top-dialog"><p id="midterm-top-status"></p><div id="midterm-top-results"></div><button id="midterm-top-close">Close</button></dialog>
    <span id="midterm-load-status" tabindex="-1"></span>`, {
    url: 'https://example.test/admin-portal/grading/midterm-exam-performance/?cycle_id=2&course_code=IS',
    runScripts: 'outside-only',
  });
  let observe;
  if (observer) dom.window.IntersectionObserver = class {
    constructor(callback, options) { observe = callback; assert.equal(options.rootMargin, '600px'); }
    observe() {}
  };
  const dialog = dom.window.document.getElementById('midterm-top-dialog');
  dialog.showModal = () => dialog.setAttribute('open', '');
  dialog.close = () => dialog.removeAttribute('open');
  dom.window.eval(script);
  return { dom, window: dom.window, document: dom.window.document, observe: () => observe([{ isIntersecting: true }]),
    click: () => dom.window.document.getElementById('midterm-load-more').click() };
}
test('append courses once, deduplicate rows, keep navigation and prevent concurrent loads', async () => {
  const { dom, window, document, click } = setup();
  let release, calls = 0;
  window.fetch = async (url, options) => {
    calls++;
    assert.match(url, /cycle_id=2&course_code=IS&row_page=2/);
    assert.equal(options.headers['X-Midterm-Fragment'], '1');
    await new Promise(resolve => { release = resolve; });
    return { ok: true, status: 200, json: async () => ({ html: course(1, [1, 2]) + course(2, [3]), next_url: '' }) };
  };
  click(); click();
  assert.equal(calls, 1);
  release(); await tick();
  assert.equal(document.querySelectorAll('h2').length, 2);
  assert.deepEqual([...document.querySelectorAll('tr')].map(n => n.dataset.offeringId), ['1', '2', '3']);
  assert.equal(window.location.search, '?cycle_id=2&course_code=IS');
  assert.equal(document.getElementById('midterm-load-more').hidden, true);
  dom.window.close();
});
test('late JSON after filter changes cannot append old rows', async () => {
  const { dom, window, document, click } = setup();
  let release;
  window.fetch = async () => ({ ok: true, status: 200,
    json: () => new Promise(resolve => { release = resolve; }) });
  click(); await tick();
  document.querySelector('select').dispatchEvent(new window.Event('change', { bubbles: true }));
  release({ html: course(1, [2]), next_url: '?row_page=3' }); await tick();
  assert.equal(document.querySelectorAll('tr').length, 1);
  assert.match(document.getElementById('midterm-load-more').href, /row_page=2/);
  dom.window.close();
});
test('failed load retries the same URL and stale result offers filtered restart', async () => {
  const { dom, window, document, click } = setup();
  window.fetch = async () => { throw new Error('Network'); };
  click(); await tick();
  assert.match(document.getElementById('midterm-load-status').textContent, /retry/);
  window.fetch = async () => ({ ok: false, status: 409,
    json: async () => ({ restart_url: '?cycle_id=2&course_code=IS', html: '', next_url: '' }) });
  click(); await tick();
  assert.equal(document.getElementById('midterm-load-more').textContent, 'Reload report');
  assert.equal(document.querySelectorAll('tr').length, 1);
  dom.window.close();
});
test('browser back restores loading and ignores the previous in-flight response', async () => {
  const { dom, window, document, click } = setup();
  let release;
  window.fetch = async () => ({ ok: true, status: 200,
    json: () => new Promise(resolve => { release = resolve; }) });
  click(); await tick();
  window.dispatchEvent(new window.PageTransitionEvent('pagehide', { persisted: true }));
  window.dispatchEvent(new window.PageTransitionEvent('pageshow', { persisted: true }));
  release({ html: course(1, [999]), next_url: '?row_page=99' }); await tick();
  window.fetch = async () => ({ ok: true, status: 200,
    json: async () => ({ html: course(1, [2]), next_url: '' }) });
  click(); await tick();
  assert.deepEqual([...document.querySelectorAll('tr')].map(n => n.dataset.offeringId), ['1', '2']);
  dom.window.close();
});


test('near-bottom observation automatically loads bounded batches without overlapping fetches', async () => {
  const { dom, window, document, observe } = setup({ observer: true });
  let calls = 0, active = 0, maxActive = 0;
  window.fetch = async () => {
    calls++; active++; maxActive = Math.max(active, maxActive);
    await tick(); active--;
    return { ok: true, status: 200, json: async () => ({ html: course(1, [calls + 1]), next_url: calls < 2 ? '?row_page=3' : '', total_rows: 3 }) };
  };
  observe(); observe();
  for (let i = 0; i < 12; i++) await tick();
  assert.equal(calls, 2);
  assert.equal(maxActive, 1);
  assert.equal(document.getElementById('midterm-load-progress').value, 3);
  dom.window.close();
});

test('Load All renders each response and reports actual acknowledged progress', async () => {
  const { dom, window, document } = setup();
  let second;
  window.fetch = async url => ({ ok: true, status: 200,
    json: url.includes('row_page=3') ? () => new Promise(resolve => { second = resolve; })
      : async () => ({ html: course(1, [2]), next_url: '?row_page=3', total_rows: 3 }) });
  document.getElementById('midterm-load-all').click(); await tick();
  assert.equal(document.querySelectorAll('tr').length, 2);
  assert.equal(document.getElementById('midterm-load-progress').value, 2);
  second({ html: course(2, [3]), next_url: '', total_rows: 3 }); await tick();
  assert.equal(document.getElementById('midterm-load-progress').value, 3);
  assert.match(document.getElementById('midterm-load-status').textContent, /3 of 3/);
  dom.window.close();
});

test('Cancel discards late response, stops automatic loads, and resumes the same URL', async () => {
  const { dom, window, document, observe, click } = setup({ observer: true });
  let release, signal, calls = 0;
  window.fetch = async (url, options) => { calls++; signal = options.signal;
    return { ok: true, status: 200, json: () => new Promise(resolve => { release = resolve; }) }; };
  document.getElementById('midterm-load-all').click(); await tick();
  document.getElementById('midterm-cancel').click();
  assert.equal(signal.aborted, true);
  release({ html: course(1, [999]), next_url: '?row_page=99', total_rows: 4 }); await tick();
  observe(); await tick();
  assert.equal(calls, 1);
  assert.equal(document.querySelectorAll('tr').length, 1);
  window.fetch = async url => {
    assert.match(url, /row_page=2/);
    return { ok: true, status: 200, json: async () => ({ html: course(1, [2]), next_url: '', total_rows: 2 }) };
  };
  click(); await tick();
  assert.equal(document.querySelectorAll('tr').length, 2);
  dom.window.close();
});

test('filter change stops Load All and late responses never update progress', async () => {
  const { dom, window, document } = setup();
  let release;
  window.fetch = async () => ({ ok: true, status: 200, json: () => new Promise(resolve => { release = resolve; }) });
  document.getElementById('midterm-load-all').click(); await tick();
  document.querySelector('select').dispatchEvent(new window.Event('change', { bubbles: true }));
  release({ html: course(1, [3]), next_url: '?row_page=3', total_rows: 99 }); await tick();
  assert.equal(document.getElementById('midterm-load-progress').value, 1);
  assert.equal(document.getElementById('midterm-load-all').hidden, true);
  dom.window.close();
});

test('Top Performers is lazy, escapes its message, and discards responses after closing', async () => {
  const { dom, window, document } = setup();
  let calls = 0, release;
  window.fetch = async () => { calls++; return { ok: true, json: () => new Promise(resolve => { release = resolve; }) }; };
  const button = document.createElement('button'); button.dataset.topUrl = '/top/';
  document.getElementById('midterm-course-groups').append(button);
  assert.equal(calls, 0);
  button.click(); await tick();
  document.getElementById('midterm-top-close').click();
  release({ available: false, message: 'late' }); await tick();
  assert.notEqual(document.getElementById('midterm-top-status').textContent, 'late');
  button.click(); await tick();
  release({ available: false, message: '<img src=x onerror=alert(1)>' }); await tick();
  assert.equal(document.querySelector('#midterm-top-status img'), null);
  assert.match(document.getElementById('midterm-top-status').textContent, /<img/);
  dom.window.close();
});


test('Top Performers renders saved grades and available raw scores as text, preserving ties', async () => {
  const { dom, window, document } = setup();
  window.fetch = async () => ({ ok: true, json: async () => ({ available: true, message: 'Saved results',
    campuses: [{ campus: 'FV', rows: [
      { rank: 10, name: '<img src=x onerror=alert(1)>', student_no: 'S-1', section: 'A', exam_grade: '85.00', raw_score: '35.00', maximum_points: '40.00' },
      { rank: 10, name: 'Other', student_no: 'S-2', section: 'A', exam_grade: '85.00', raw_score: null, maximum_points: null },
    ] }], overall: null, overall_note: 'Shared assessment is not recorded',
  }) });
  const button = document.createElement('button'); button.dataset.topUrl = '/top/';
  document.getElementById('midterm-course-groups').append(button); button.click(); await tick();
  const output = document.getElementById('midterm-top-results');
  assert.equal(output.querySelectorAll('tbody tr').length, 2);
  assert.equal(output.querySelector('img'), null);
  assert.match(output.textContent, /85.00/);
  assert.match(output.textContent, /35.00 \/ 40.00/);
  assert.match(output.textContent, /Shared assessment/);
  document.getElementById('midterm-top-close').click();
  assert.equal(output.textContent, '');
  dom.window.close();
});
