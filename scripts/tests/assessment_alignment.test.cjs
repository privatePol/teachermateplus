const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({offering = '', chartFailure = false, reducedMotion = false} = {}) {
  const calls = [], handlers = {}, formHandlers = {}, windowHandlers = {}, drawn = [];
  class Element {
    constructor() { this.hidden = true; this.value = ''; this.dataset = {}; this.attributes = {}; this.options = [{textContent: ''}]; }
    replaceChildren(...children) { this.innerHTML = ''; this.children = children; this.options = children; this.value = ''; }
    add(option) { this.options.push(option); }
    addEventListener(event, callback) { this[event] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
  }
  const elements = {};
  for (const id of ['learning-filters', 'learning-results', 'learning-progress', 'learning-message', 'learning-view', 'learning-retry', 'learning-overall', 'learning-csv-controls', 'learning-csv-period', 'learning-selected', 'learning-line', 'learning-bars', 'learning-patterns']) elements[id] = new Element();
  const selects = ['campus_id', 'academic_year_id', 'term_id', 'course_code', 'offering_id'].map(name => Object.assign(new Element(), {name}));
  const form = elements['learning-filters'];
  form.elements = {namedItem: name => selects.find(s => s.name === name)};
  form.querySelectorAll = () => selects;
  form.reportValidity = () => true;
  form.addEventListener = (event, callback) => formHandlers[event] = callback;
  elements['learning-selected'].textContent = JSON.stringify({campus_id: 1, academic_year_id: 1, term_id: 1, course_code: 'IT', offering_id: offering});
  const links = ['sections', 'students'].map(kind => Object.assign(new Element(), {href: `/admin-portal/grading/performance-insights/?export=${kind}`, dataset: {learningExport: kind}}));
  class Data extends Map { constructor() { super(selects.map(s => [s.name, s.value])); this.set('csrfmiddlewaretoken', 'csrf-value'); } }
  class Chart {
    constructor(canvas, config) { if (chartFailure) throw new Error('drawing failed'); this.config = config; this.canvas = canvas; drawn.push(this); }
    destroy() { this.destroyed = true; }
  }
  const context = {URLSearchParams, FormData: Data, Option: function(text, value) { this.textContent = text; this.value = value; }, AbortController,
    location: {pathname: '/admin-portal/grading/assessment-alignment/preview/'},
    document: {getElementById: id => elements[id], querySelectorAll: selector => selector === '[data-learning-export]' ? links : [], addEventListener: (event, callback) => handlers[event] = callback},
    window: {Chart, matchMedia: () => ({matches: reducedMotion}), addEventListener: (event, callback) => windowHandlers[event] = callback},
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/admin_portal/assessment_alignment.js'), 'utf8'), context);
  const respond = (index, data, status = 200, type = 'application/json') => calls[index].resolve({status, ok: status < 400, headers: {get: () => type}, json: async () => data});
  const submit = () => formHandlers.submit({preventDefault() {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const click = (kind, value) => handlers.click({preventDefault() { if (kind === 'export') links[0].prevented = true; }, target: {closest: selector => selector === '[data-learning-export]' && kind === 'export' ? links[0] : selector === '[data-learning-page]' && kind === 'page' ? {dataset: {learningPage: value}} : null}});
  return {calls, elements, selects, links, drawn, respond, submit, tick, click, formHandlers, windowHandlers};
}

function report(token = 'generation') {
  return {token, html: '<p>Accessible period results</p>', export_periods: [['PRELIM', 'Prelim']], charts: {
    threshold: '3.00',
    line: [{period: 'Prelim', quiz: '0.00', quiz_grade: '0.00', standing: null, exam: '75.00', periodic: '82.00'}],
    bars: [{section: 'A', quizzes: 4, quiz: '0.00', quiz_grade: '0.00', standing: '82.00', exam: '75.00'}],
    patterns: [{label: 'Moved Together', count: 1, percentage: '100.00'}],
  }};
}

test('alignment preview remains explicit and sends CSRF to its own endpoint', async () => {
  const h = harness(); assert.equal(h.calls.length, 0);
  const pending = h.submit();
  assert.match(h.calls[0].url, /assessment-alignment\/preview/);
  assert.equal(h.calls[0].options.body.get('csrfmiddlewaretoken'), 'csrf-value');
  h.respond(0, report()); await pending;
  assert.equal(h.elements['learning-progress'].hidden, true);
});

test('independent indicator charts preserve zero and gaps and never plot periodic grades', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.charts.bars[0].standing = null;
  h.respond(0, data); await pending;
  const bar = h.drawn.find(c => c.config.type === 'bar');
  assert.deepEqual(Array.from(bar.config.data.datasets, d => d.label), ['Quiz', 'Participation/Output', 'Examination']);
  assert.deepEqual(Array.from(bar.config.data.datasets, d => d.data[0]), [0, null, 75]);
  assert.equal(h.drawn.some(c => c.config.type === 'line'), false);
  assert.match(bar.config.options.plugins.tooltip.callbacks.afterBody(), /same students across all six/);
});

test('four categories retain colors when zero slices are omitted', async () => {
  const h=harness(); const pending=h.submit(); const data=report();
  data.charts.patterns=['Moved Together','Moved Opposite','Mixed Results','Stable Performance'].map((label,i)=>({label,count:i===1?0:1,percentage:'33.33'}));
  h.respond(0,data); await pending;
  const chart=h.drawn.find(c=>c.config.type==='doughnut');
  assert.deepEqual(Array.from(chart.config.data.labels),['Moved Together','Mixed Results','Stable Performance']);
  assert.deepEqual(Array.from(chart.config.data.datasets[0].backgroundColor),['#0072b2','#b56b00','#596773']);
});

test('privacy or stale generation responses erase older grades and charts', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.click('page',2); h.respond(1,{code:'records_changed',error:'Refresh'},409); await pending;
  assert.equal(h.elements['learning-results'].innerHTML,'');
  assert.ok(h.drawn.every(c=>c.destroyed));
});

test('filter changes discard late reports and retry preserves valid choices', async () => {
  const h=harness(); const pending=h.submit(); const changed=h.formHandlers.change();
  h.respond(0,report()); await pending;
  h.respond(1,{error:'Unavailable'},503); await changed;
  assert.equal(h.elements['learning-results'].innerHTML,'');
  assert.equal(h.elements['learning-retry'].hidden,false);
  const retried=h.elements['learning-retry'].click();
  h.respond(2,{filters:{},options:{}}); await retried;
  assert.equal(h.elements['learning-view'].disabled,false);
});
