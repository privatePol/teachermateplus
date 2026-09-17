import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const script = readFileSync(new URL('../../static/js/departmental_exam_question_reuse.js', import.meta.url), 'utf8');

function page() {
  const dom = new JSDOM(`<!doctype html><form id="reuse-filters"></form>
    <form data-reuse-form data-remaining="2">
      <input type="checkbox" data-reuse-select-all>
      <span data-reuse-selected-count></span><span data-reuse-case-count></span>
      <p data-reuse-overflow hidden></p><button data-reuse-copy disabled>Copy</button>
      <article data-reuse-item data-size="1" data-kind="question"><input type="checkbox" data-reuse-checkbox></article>
      <article data-reuse-item data-size="2" data-kind="case"><input type="checkbox" data-reuse-checkbox></article>
    </form>`, { runScripts: 'outside-only' });
  dom.window.eval(script);
  return dom;
}

test('whole Case counts every question and over-capacity selection is blocked', () => {
  const dom = page();
  const { document, Event } = dom.window;
  const boxes = [...document.querySelectorAll('[data-reuse-checkbox]')];
  boxes[1].checked = true;
  boxes[1].dispatchEvent(new Event('change'));
  assert.equal(document.querySelector('[data-reuse-selected-count]').textContent, '2');
  assert.equal(document.querySelector('[data-reuse-case-count]').textContent, '1');
  assert.equal(document.querySelector('[data-reuse-copy]').disabled, false);
  boxes[0].checked = true;
  boxes[0].dispatchEvent(new Event('change'));
  assert.match(document.querySelector('[data-reuse-overflow]').textContent, /Deselect 1 question/);
  assert.equal(document.querySelector('[data-reuse-copy]').disabled, true);
});

test('Select all covers this page and applying filters clears its selections', () => {
  const dom = page();
  const { document, Event } = dom.window;
  const all = document.querySelector('[data-reuse-select-all]');
  all.checked = true;
  all.dispatchEvent(new Event('change'));
  const boxes = [...document.querySelectorAll('[data-reuse-checkbox]')];
  assert.deepEqual(boxes.map(box => box.checked), [true, true]);
  document.querySelector('#reuse-filters').dispatchEvent(new Event('submit'));
  assert.deepEqual(boxes.map(box => box.checked), [false, false]);
});
