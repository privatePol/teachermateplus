import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(new URL('../../static/js/departmental_exam_bulk_question_delete.js', import.meta.url), 'utf8');

function page() {
  const dom = new JSDOM(`<!doctype html><form id="bulk-question-delete-form" data-bulk-question-form>
    <select data-bulk-question-filter><option value="all">All</option><option value="EASY">Easy</option><option value="MODERATE">Moderate</option><option value="linked">Case linked</option></select>
    <input type="checkbox" data-bulk-select-all><span data-bulk-selected-count></span>
    <button data-bulk-delete-button disabled>Delete</button></form>
    <article class="question-card" data-difficulty="EASY" data-linked="0"><input type="checkbox" data-bulk-question name="selected_questions" form="bulk-question-delete-form" value="1:1"></article>
    <article class="question-card" data-difficulty="MODERATE" data-linked="0"><input type="checkbox" data-bulk-question name="selected_questions" form="bulk-question-delete-form" value="2:1"></article>
    <div class="collapse"><article class="question-card" data-difficulty="EASY" data-linked="1"><input type="checkbox" data-bulk-question name="selected_questions" form="bulk-question-delete-form" value="3:1"></article></div>`, { runScripts: 'outside-only' });
  const { document, Event } = dom.window;
  for (const card of document.querySelectorAll('.question-card')) {
    card.getClientRects = () => card.hidden || (card.closest('.collapse') && !card.closest('.collapse').classList.contains('show')) ? [] : [{}];
  }
  dom.window.confirm = () => true;
  dom.window.eval(script);
  return { dom, document, Event };
}

test('Select all covers visible cards and filtering clears hidden selections', () => {
  const { document, Event } = page();
  const boxes = [...document.querySelectorAll('[data-bulk-question]')];
  const all = document.querySelector('[data-bulk-select-all]');
  all.checked = true;
  all.dispatchEvent(new Event('change'));
  assert.deepEqual(boxes.map(box => box.checked), [true, true, false]);
  assert.equal(document.querySelector('[data-bulk-selected-count]').textContent, '2');
  const filter = document.querySelector('[data-bulk-question-filter]');
  filter.value = 'EASY';
  filter.dispatchEvent(new Event('change'));
  assert.deepEqual(boxes.map(box => box.checked), [true, false, false]);
  assert.equal(document.querySelector('[data-bulk-selected-count]').textContent, '1');
  filter.value = 'all';
  filter.dispatchEvent(new Event('change'));
  document.querySelector('.collapse').classList.add('show');
  document.dispatchEvent(new Event('shown.bs.collapse'));
  all.checked = true;
  all.dispatchEvent(new Event('change'));
  assert.deepEqual(boxes.map(box => box.checked), [true, true, true]);
});

test('confirmation uses the live selected count and cancel prevents submission', () => {
  const { dom, document, Event } = page();
  const box = document.querySelector('[data-bulk-question]');
  box.checked = true;
  box.dispatchEvent(new Event('change'));
  let prompt = '';
  dom.window.confirm = message => { prompt = message; return false; };
  const submitted = document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }));
  assert.equal(submitted, false);
  assert.match(prompt, /Delete 1 selected question\?/);
});
