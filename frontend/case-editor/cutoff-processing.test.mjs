import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {JSDOM} from 'jsdom';

const script = readFileSync(new URL('../../static/faculty_attendance/processing.js', import.meta.url), 'utf8');
function page() {
  const dom = new JSDOM(`<form method="post" data-attendance-processing="Saving. Please wait.">
    <input name="csrfmiddlewaretoken" value="synthetic-csrf"><input name="submission_key" value="stable-key">
    <input name="ready_faculty_ids" value="[7]"><textarea name="reason">Retained note</textarea>
    <button name="action" value="publish_selected">Selected</button>
    <button name="action" value="publish_ready">All ready</button></form>`, {runScripts:'outside-only'});
  dom.window.eval(script);
  const form = dom.window.document.querySelector('form');
  return {window: dom.window, form, buttons: form.querySelectorAll('button')};
}
for (const action of ['publish_selected', 'publish_ready', 'finalize_selected', 'finalize_ready']) {
  test(`native ${action} preserves clicked action, CSRF, batch and inputs`, () => {
    const {window, form, buttons} = page();
    buttons[0].value = action;
    const event = new window.SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:buttons[0]});
    form.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
    const data = new window.FormData(form);
    assert.deepEqual(data.getAll('action'), [action]);
    assert.equal(data.get('csrfmiddlewaretoken'), 'synthetic-csrf');
    assert.equal(data.get('submission_key'), 'stable-key');
    assert.equal(data.get('ready_faculty_ids'), '[7]');
    assert.equal(data.get('reason'), 'Retained note');
    assert.equal(form.getAttribute('aria-busy'), 'true');
    assert.equal(form.querySelector('progress').hasAttribute('value'), false);
    assert.equal(form.querySelector('[role="status"]').getAttribute('aria-live'), 'polite');
    const duplicate = new window.SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:buttons[1]});
    form.dispatchEvent(duplicate);
    assert.equal(duplicate.defaultPrevented, true);
    assert.deepEqual(new window.FormData(form).getAll('action'), [action]);
  });
}
test('review GET retains cutoff inputs and an indeterminate indicator', () => {
  const {window, form, buttons} = page();
  form.method = 'get'; buttons[0].removeAttribute('name');
  form.dispatchEvent(new window.SubmitEvent('submit', {bubbles:true, cancelable:true, submitter:buttons[0]}));
  assert.equal(form.querySelector('progress').hasAttribute('value'), false);
  assert.equal(new window.FormData(form).get('submission_key'), 'stable-key');
});
test('validation restores controls and retains values without success', () => {
  const {window, form, buttons} = page();
  window.AttendanceProcessing.begin(form, buttons[0]);
  const field = form.querySelector('textarea');
  field.dispatchEvent(new window.Event('invalid', {bubbles:false}));
  assert.equal(form.querySelector('[role="status"]').textContent, 'Review the highlighted fields before continuing.');
  assert.equal(buttons[0].disabled, false);
  assert.equal(field.value, 'Retained note');
  assert.equal(form.querySelector('[role="status"]').classList.contains('text-success'), false);
});
test('offline outcome is unknown and cannot silently resubmit the batch', () => {
  const {window, form, buttons} = page();
  window.AttendanceProcessing.begin(form, buttons[0]);
  window.dispatchEvent(new window.Event('offline'));
  assert.match(form.querySelector('[role="status"]').textContent, /outcome is unknown.*Reload/);
  assert.equal(window.AttendanceProcessing.begin(form, buttons[1]), false);
  assert.equal(new window.FormData(form).get('submission_key'), 'stable-key');
});
test('back navigation after submission asks to check saved outcome', () => {
  const {window, form, buttons} = page();
  window.AttendanceProcessing.begin(form, buttons[0]);
  window.dispatchEvent(new window.PageTransitionEvent('pageshow', {persisted:true}));
  assert.match(form.querySelector('[role="status"]').textContent, /Reload and review saved statuses/);
  assert.equal(window.AttendanceProcessing.begin(form, buttons[0]), false);
});
