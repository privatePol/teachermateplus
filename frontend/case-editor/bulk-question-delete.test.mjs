import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(new URL('../../static/js/departmental_exam_bulk_question_delete.js', import.meta.url), 'utf8');

function page() {
  const dom = new JSDOM(`<!doctype html><form id="bulk-question-delete-form" data-bulk-question-form>
    <select data-bulk-question-filter><option value="all">All</option><option value="EASY">Easy</option><option value="MODERATE">Moderate</option><option value="linked">Case linked</option></select>
    <input type="checkbox" data-bulk-select-all><span data-bulk-selected-count></span>
    <button data-bulk-delete-button disabled>Delete</button>
    <button data-section-move-button disabled>Move</button><p data-section-move-error hidden></p></form>
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

test('Move goes to server confirmation without a Delete prompt; linked selection blocks only Move', () => {
  const { dom, document, Event } = page();
  const box = document.querySelector('[data-bulk-question]');
  const move = document.querySelector('[data-section-move-button]');
  box.checked = true;
  box.dispatchEvent(new Event('change'));
  assert.equal(move.disabled, false);
  dom.window.confirm = () => { throw new Error('Move must not invoke Delete confirmation'); };
  const submit = new dom.window.SubmitEvent('submit', { cancelable: true, submitter: move });
  assert.equal(document.querySelector('form').dispatchEvent(submit), true);
  document.querySelector('.collapse').classList.add('show');
  const linked = [...document.querySelectorAll('[data-bulk-question]')][2];
  linked.checked = true;
  linked.dispatchEvent(new Event('change'));
  assert.equal(move.disabled, true);
  assert.equal(document.querySelector('[data-section-move-error]').hidden, false);
  assert.equal(document.querySelector('[data-bulk-delete-button]').disabled, false);
  assert.equal(document.querySelector('form').dispatchEvent(new dom.window.SubmitEvent('submit', { cancelable: true, submitter: move })), false);
});

test('explicit whole Case selection permits Move while Delete still uses only question selections', () => {
  const { document } = page();
  // Reinitialize with a Case checkbox present, as in the real workspace.
  const caseCard = document.createElement('article');
  caseCard.className = 'qb-case-card';
  const collapse = document.querySelector('.collapse');
  collapse.before(caseCard);
  caseCard.append(collapse);
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.setAttribute('data-section-move-case', '');
  caseCard.prepend(checkbox);
  // A fresh DOM avoids duplicate event handlers from the fixture's first load.
  const fresh = new JSDOM(document.documentElement.outerHTML, { runScripts: 'outside-only' });
  for (const card of fresh.window.document.querySelectorAll('.question-card')) card.getClientRects = () => card.hidden ? [] : [{}];
  fresh.window.eval(script);
  const root = fresh.window.document;
  const whole = root.querySelector('[data-section-move-case]');
  whole.checked = true;
  whole.dispatchEvent(new fresh.window.Event('change'));
  assert.equal(root.querySelector('[data-section-move-button]').disabled, false);
  assert.equal(root.querySelector('[data-bulk-delete-button]').disabled, true);
  const standalone = root.querySelector('[data-bulk-question]');
  standalone.checked = true;
  standalone.dispatchEvent(new fresh.window.Event('change'));
  let prompt = '';
  fresh.window.confirm = message => { prompt = message; return false; };
  assert.equal(root.querySelector('form').dispatchEvent(new fresh.window.SubmitEvent('submit', {
    cancelable: true, submitter: root.querySelector('[data-bulk-delete-button]')
  })), false);
  assert.match(prompt, /Delete 1 selected question\?/);
  assert.equal(whole.checked, true);
  root.querySelector('[data-bulk-question-filter]').value = 'MODERATE';
  root.querySelector('[data-bulk-question-filter]').dispatchEvent(new fresh.window.Event('change'));
  assert.equal(whole.checked, false);
  assert.equal(root.querySelector('[data-section-move-button]').disabled, true);
});
