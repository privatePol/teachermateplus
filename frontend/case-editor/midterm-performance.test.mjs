import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const { JSDOM } = createRequire(import.meta.url)('jsdom');
const script = readFileSync(new URL('../../static/js/midterm_exam_performance.js', import.meta.url), 'utf8');
const course = (id, ids) => `<section data-course-id="${id}"><h2>COURSE ${id} | TITLE</h2><table><tbody>${ids.map(n => `<tr data-offering-id="${n}"><td>${n}</td></tr>`).join('')}</tbody></table></section>`;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function setup() {
  const dom = new JSDOM(`<form id="midterm-filters"><select><option>Cycle</option></select></form>
    <div id="midterm-course-groups">${course(1, [1])}</div>
    <a id="midterm-load-more" href="?cycle_id=2&course_code=IS&row_page=2">Load more</a>
    <span id="midterm-load-status" tabindex="-1"></span>`, {
    url: 'https://example.test/admin-portal/grading/midterm-exam-performance/?cycle_id=2&course_code=IS',
    runScripts: 'outside-only',
  });
  dom.window.eval(script);
  return { dom, window: dom.window, document: dom.window.document,
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
