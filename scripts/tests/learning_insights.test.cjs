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
    location: {pathname: '/admin-portal/grading/learning-performance-insights/preview/'},
    document: {getElementById: id => elements[id], querySelectorAll: selector => selector === '[data-learning-export]' ? links : [], addEventListener: (event, callback) => handlers[event] = callback},
    window: {Chart, matchMedia: () => ({matches: reducedMotion}), addEventListener: (event, callback) => windowHandlers[event] = callback},
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/admin_portal/learning_insights.js'), 'utf8'), context);
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

test('initial page and restored history never generate reports', () => {
  const h = harness();
  assert.equal(h.calls.length, 0);
  h.windowHandlers.pagehide(); h.windowHandlers.pageshow({persisted: true});
  assert.equal(h.calls.length, 0);
  assert.match(h.elements['learning-message'].textContent, /View Performance/);
});

test('explicit View Performance shows immediate progress and sends CSRF', async () => {
  const h = harness(); const pending = h.submit();
  assert.equal(h.elements['learning-progress'].hidden, false);
  assert.equal(h.elements['learning-results'].attributes['aria-busy'], 'true');
  assert.equal(h.calls[0].options.body.get('action'), 'report');
  assert.equal(h.calls[0].options.body.get('csrfmiddlewaretoken'), 'csrf-value');
  h.respond(0, report()); await pending;
  assert.equal(h.elements['learning-progress'].hidden, true);
  assert.match(h.elements['learning-results'].innerHTML, /Accessible/);
  assert.equal(h.drawn.length, 3);
});

test('only three charts render; missing is null and saved zero remains zero', async () => {
  const h = harness({reducedMotion: true}); const pending = h.submit(); h.respond(0, report()); await pending;
  assert.deepEqual(h.drawn.map(c => c.config.type), ['line', 'bar', 'doughnut']);
  assert.equal(h.drawn[0].config.data.datasets[0].data[0], 0);
  assert.equal(h.drawn[0].config.data.datasets[1].data[0], null);
  assert.equal(h.drawn[0].config.options.animation, false);
  assert.equal(h.drawn[0].config.data.datasets[0].spanGaps, false);
  assert.equal(h.drawn[1].config.data.datasets.length, 3);
  const tooltip = h.drawn[1].config.options.plugins.tooltip.callbacks.afterBody([{dataIndex: 0}]);
  assert.match(tooltip, /4/); // Count is textual context, never a score dataset.
});

test('four-category chart omits zero slices without shifting colors or altering historical values', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.charts.line = [{period: 'Prelim', quiz: '80.00', quiz_grade: '90.00', standing: '85.00', exam: '75.00', periodic: '82.00'},
    {period: 'Midterm', quiz: '82.99', quiz_grade: '91.50', standing: '87.99', exam: '77.01', periodic: '83.01'}];
  data.charts.bars = [{section: 'A', quizzes: 1, quiz: '82.99', quiz_grade: '91.50', standing: '87.99', exam: '77.01'}];
  data.charts.patterns = [{label: 'Moved Together', count: 2, percentage: '50.00'},
    {label: 'Moved Opposite', count: 0, percentage: '0.00'}, {label: 'Mixed Results', count: 1, percentage: '25.00'},
    {label: 'Stable Performance', count: 1, percentage: '25.00'}];
  h.respond(0, data); await pending;
  assert.equal(h.drawn.length, 3);
  assert.deepEqual(Array.from(h.drawn[0].config.data.datasets[0].data), [90, 91.5]);
  assert.deepEqual(Array.from(h.drawn[0].config.data.datasets[2].data), [75, 77.01]);
  assert.equal(h.drawn[1].config.data.datasets[0].data[0], 91.5);
  assert.equal(h.drawn[1].config.data.datasets[1].data[0], 87.99);
  assert.equal(h.drawn[1].config.data.datasets[2].data[0], 77.01);
  assert.deepEqual(Array.from(h.drawn[2].config.data.labels), ['Moved Together', 'Mixed Results', 'Stable Performance']);
  assert.deepEqual(Array.from(h.drawn[2].config.data.datasets[0].data), [2, 1, 1]);
  assert.deepEqual(Array.from(h.drawn[2].config.data.datasets[0].backgroundColor), ['#0072b2', '#b56b00', '#596773']);
  for (const chart of h.drawn) assert.match(chart.config.options.plugins.tooltip.callbacks.afterBody([{dataIndex: 0}]), /at least 3\.00 percentage points/);
  assert.match(h.drawn[2].config.options.plugins.title.text, /3\.00 percentage points/);
  assert.match(h.drawn[2].config.options.plugins.tooltip.callbacks.label({label: 'Stable Performance', dataIndex: 2}), /1 section \(25\.00%\)/);
  assert.match(h.drawn[2].config.options.plugins.tooltip.callbacks.afterBody(), /before Gradebook conversion/);
});

test('four nonzero patterns render in fixed order with distinct colors and accurate tooltips', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  const labels = ['Moved Together', 'Moved Opposite', 'Mixed Results', 'Stable Performance'];
  data.charts.patterns = labels.map(label => ({label, count: 1, percentage: '25.00'}));
  h.respond(0, data); await pending;
  const chart = h.drawn.find(item => item.config.type === 'doughnut');
  assert.deepEqual(Array.from(chart.config.data.labels), labels);
  assert.deepEqual(Array.from(chart.config.data.datasets[0].data), [1, 1, 1, 1]);
  assert.equal(new Set(chart.config.data.datasets[0].backgroundColor).size, 4);
  labels.forEach((label, dataIndex) => {
    assert.equal(chart.config.options.plugins.tooltip.callbacks.label({label, dataIndex}), `${label}: 1 section (25.00%)`);
  });
});

test('Gradebook series and raw context stay distinct; descriptive points are not connected', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.charts.line_mode = 'descriptive';
  data.charts.line = [{period: 'Midterm', quiz: '36.05', quiz_grade: '68.03', standing: '78.88', exam: '80.00'}];
  data.charts.bars = [{section: 'BSA 1C', quiz: '36.05', quiz_grade: '68.03', quizzes: 2}];
  h.respond(0, data); await pending;
  assert.equal(h.drawn[0].config.data.datasets[0].data[0], 68.03);
  assert.equal(h.drawn[1].config.data.datasets[0].data[0], 68.03);
  assert.match(h.drawn[0].config.data.datasets[0].label, /Gradebook points/);
  assert.equal(h.drawn[0].config.data.datasets.every(series => series.showLine === false), true);
  assert.match(h.drawn[0].config.options.plugins.tooltip.callbacks.afterBody([{dataIndex: 0}]), /36\.05/);
  assert.match(h.drawn[1].config.options.plugins.tooltip.callbacks.afterBody([{dataIndex: 0}]), /36\.05/);
});

test('an unavailable Gradebook quiz stays a gap even when raw attainment exists', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.charts.line[0].quiz = '36.05'; data.charts.line[0].quiz_grade = null;
  data.charts.bars[0].quiz_grade = null;
  h.respond(0, data); await pending;
  assert.equal(h.drawn[0].config.data.datasets[0].data[0], null);
  assert.equal(h.drawn[1].config.data.datasets[0].data[0], null);
});

test('unsupported mixed quiz rollup leaves gaps and preserves other component series', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.charts.line_mode = 'descriptive';
  data.charts.line[0].quiz = null; data.charts.line[0].quiz_grade = null;
  data.charts.line[0].standing = '85';
  data.charts.bars[0].quiz = null; data.charts.bars[0].quiz_grade = null;
  data.charts.bars[0].exam = '75';
  data.charts.patterns = [];
  delete h.elements['learning-patterns'];
  h.respond(0, data); await pending;
  assert.equal(h.drawn[0].config.data.datasets[0].data[0], null);
  assert.equal(h.drawn[1].config.data.datasets[0].data[0], null);
  assert.equal(h.drawn[0].config.data.datasets[1].data[0], 85);
  assert.equal(h.drawn[1].config.data.datasets[2].data[0], 75);
  for (const chart of h.drawn) {
    assert.match(chart.config.options.plugins.tooltip.callbacks.afterBody([{dataIndex: 0}]), /Original quiz percentage: Not Available/);
    assert.equal(chart.config.data.datasets.some(series => series.data.includes(42.5)), false);
  }
  assert.equal(h.drawn.length, 2);
});

test('zero eligible sections keep the empty state without drawing fabricated pie slices', async () => {
  const h = harness(); const pending = h.submit(); const data = report();
  data.html = '<div role="status">No patterns available yet. Required measurements are unavailable.</div>';
  data.charts.patterns = ['Moved Together', 'Moved Opposite', 'Mixed Results', 'Stable Performance'].map(label => ({label, count: 0, percentage: null}));
  h.respond(0, data); await pending;
  assert.deepEqual(h.drawn.map(chart => chart.config.type), ['line', 'bar']);
  assert.match(h.elements['learning-results'].innerHTML, /No patterns available yet/);
});

test('withheld component response clears earlier grades and leaves a chart gap', async () => {
  const h = harness();
  const first = h.submit(); h.respond(0, report('earlier-authority')); await first;
  const previousCharts = [...h.drawn];
  const next = h.submit();
  const withheld = report('restricted-authority');
  withheld.html = '<p>Component results withheld by aggregate privacy rules.</p>';
  withheld.charts.line = [];
  withheld.charts.bars[0].exam = null;
  withheld.charts.patterns = [];
  // The server omits canvases for unavailable matched progression and patterns.
  delete h.elements['learning-line'];
  delete h.elements['learning-patterns'];
  h.respond(1, withheld); await next;
  assert.ok(previousCharts.every(chart => chart.destroyed));
  const visible = h.drawn.filter(chart => !chart.destroyed);
  assert.equal(visible.length, 1);
  assert.equal(visible[0].config.type, 'bar');
  assert.equal(visible[0].config.data.datasets[2].data[0], null);
  assert.equal(visible[0].config.data.datasets[1].data[0], 82);
  assert.match(h.elements['learning-results'].innerHTML, /withheld/);
  assert.doesNotMatch(h.elements['learning-results'].innerHTML, /75/);
});

test('filter change aborts and discards an old report even when transport ignores abort', async () => {
  const h = harness(); const first = h.submit(); const changed = h.formHandlers.change();
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.respond(0, report('stale')); await first;
  assert.equal(h.elements['learning-results'].innerHTML, '');
  assert.equal(h.drawn.length, 0);
  h.respond(1, {filters: {}, options: {}}); await changed;
  assert.equal(h.elements['learning-view'].disabled, false);
  assert.equal(h.calls.length, 2);
});

for (const mode of ['json', 'non-json', 'network']) {
  test(`options ${mode} failure offers retry without report generation`, async () => {
    const h = harness(); const failed = h.formHandlers.change();
    if (mode === 'network') h.calls[0].reject(new Error('Network unavailable'));
    else h.respond(0, {error: 'Temporarily unavailable'}, 503, mode === 'json' ? 'application/json' : 'text/html');
    await failed;
    assert.equal(h.elements['learning-retry'].hidden, false);
    assert.equal(h.elements['learning-retry'].disabled, false);
    assert.equal(h.elements['learning-view'].disabled, true);
    await h.submit(); assert.equal(h.calls.length, 1);
    const retry = h.elements['learning-retry'].click();
    h.respond(1, {filters: {}, options: {}}); await retry;
    assert.equal(h.elements['learning-view'].disabled, false);
    assert.equal(h.calls[1].options.body.get('action'), 'options');
    assert.equal(h.calls.length, 2);
  });
}

test('latest options alone may restore controls', async () => {
  const h = harness(); const first = h.formHandlers.change(); const second = h.formHandlers.change();
  h.respond(0, {filters: {}, options: {}}); await first;
  assert.equal(h.elements['learning-view'].disabled, true);
  h.respond(1, {filters: {course_code: 'IT'}, options: {course_code: [{value: 'IT', label: 'IT'}]}}); await second;
  assert.equal(h.elements['learning-view'].disabled, false);
  assert.equal(h.selects.find(s => s.name === 'course_code').value, 'IT');
});

test('record-change page response clears charts, results, export state and token', async () => {
  const h = harness(); let pending = h.submit(); h.respond(0, report()); await pending;
  pending = h.click('page', '2');
  assert.equal(h.calls[1].options.body.get('token'), 'generation');
  h.respond(1, {code: 'records_changed', error: 'Academic records changed. Refresh.'}, 409); await pending;
  assert.equal(h.elements['learning-results'].innerHTML, '');
  assert.equal(h.drawn.every(c => c.destroyed), true);
  assert.equal(h.links[0].attributes['aria-disabled'], 'true');
  assert.match(h.elements['learning-message'].textContent, /records changed/);
  await h.click('page', '3'); assert.equal(h.calls.length, 2);
});

test('a report changed during generation can be explicitly retried', async () => {
  const h = harness(); let pending = h.submit(); h.respond(0, {error: 'Records changed'}, 409); await pending;
  assert.equal(h.elements['learning-view'].disabled, false);
  pending = h.submit(); h.respond(1, report()); await pending;
  assert.match(h.elements['learning-results'].innerHTML, /Accessible/);
});

test('late page result cannot overwrite new filter state and destroys old charts', async () => {
  const h = harness(); let pending = h.submit(); h.respond(0, report()); await pending;
  pending = h.click('page', '2'); const changed = h.formHandlers.change();
  h.respond(1, report('old-page')); await pending;
  h.respond(2, {filters: {}, options: {}}); await changed;
  assert.equal(h.elements['learning-results'].innerHTML, '');
  assert.equal(h.drawn.every(c => c.destroyed), true);
});

test('CSVs keep original destination and explicit period without preview section or tokens', async () => {
  const h = harness({offering: '22'});
  await h.click('export'); assert.equal(h.links[0].prevented, true);
  const pending = h.submit(); h.respond(0, report()); await pending;
  assert.equal(h.links[0].attributes['aria-disabled'], 'true');
  h.elements['learning-csv-period'].value = 'PRELIM'; h.elements['learning-csv-period'].change();
  assert.equal(h.links[0].attributes['aria-disabled'], 'false');
  assert.match(h.links[0].href, /^\/admin-portal\/grading\/performance-insights\/\?/);
  assert.match(h.links[0].href, /period_code=PRELIM/);
  assert.match(h.links[1].href, /export=students/);
  assert.doesNotMatch(h.links[0].href, /offering_id|csrf|token/);
});

test('return to overall clears section and asks for explicit generation', async () => {
  const h = harness({offering: '22'});
  assert.equal(h.elements['learning-overall'].hidden, false);
  const pending = h.elements['learning-overall'].click();
  assert.equal(h.selects.find(s => s.name === 'offering_id').value, '');
  assert.equal(h.calls[0].options.body.get('action'), 'options');
  h.respond(0, {filters: {}, options: {}}); await pending;
  assert.equal(h.calls.length, 1);
});

test('chart failure keeps accessible results and reports the fallback', async () => {
  const h = harness({chartFailure: true}); const pending = h.submit(); h.respond(0, report()); await pending;
  assert.match(h.elements['learning-results'].innerHTML, /Accessible/);
  assert.match(h.elements['learning-message'].textContent, /equivalent values/);
});

test('report network error ends progress and re-enables explicit retry', async () => {
  const h = harness(); const pending = h.submit(); h.calls[0].reject(new Error('Network unavailable')); await pending;
  assert.equal(h.elements['learning-progress'].hidden, true);
  assert.equal(h.elements['learning-view'].disabled, false);
  assert.match(h.elements['learning-message'].textContent, /Network unavailable/);
});
