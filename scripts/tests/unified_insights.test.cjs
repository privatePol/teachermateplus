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
    focus() { this.focused = true; }
  }
  const elements = {'unified-exams': new Element()};
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
  const tabs = ['overview','exams','alignment'].map(name => Object.assign(new Element(), {dataset: {unifiedTab: name}}));
  const context = {URLSearchParams, FormData: Data, Option: function(text, value) { this.textContent = text; this.value = value; }, AbortController,
    location: {pathname: '/admin-portal/grading/academic-performance-insights/unified-preview/'},
    document: {getElementById: id => elements[id], querySelectorAll: selector => selector === '[data-learning-export]' ? links : selector === '[data-unified-tab]' ? tabs : [], addEventListener: (event, callback) => handlers[event] = callback},
    window: {Chart, matchMedia: () => ({matches: reducedMotion}), addEventListener: (event, callback) => windowHandlers[event] = callback},
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/admin_portal/unified_insights.js'), 'utf8'), context);
  const respond = (index, data, status = 200, type = 'application/json') => calls[index].resolve({status, ok: status < 400, headers: {get: () => type}, json: async () => data});
  const submit = () => formHandlers.submit({preventDefault() {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const click = (kind, value) => handlers.click({preventDefault() { if (kind === 'export') links[0].prevented = true; }, target: {closest: selector => selector === '[data-learning-export]' && kind === 'export' ? links[0] : selector === '[data-unified-page], [data-learning-page]' && kind === 'page' ? {dataset: {unifiedPage: value}} : selector === '[data-unified-course]' && kind === 'course' ? {dataset: {unifiedCourse: value}} : selector === '[data-unified-section]' && kind === 'section' ? {dataset: {unifiedSection: value}} : null}});
  return {tabs, calls, elements, selects, links, drawn, respond, submit, tick, click, formHandlers, windowHandlers};
}

function report(tab = 'overview', token = 'generation') {
  return {token, tab, html: '<p>Accessible academic results</p>', export_periods: tab === 'overview' ? [['MIDTERM','Midterm']] : [],
    charts: {threshold: '3.00', line: [], line_mode: 'descriptive',
      bars: [{section: 'A',quiz_grade: '0.00',standing: null,exam:'80.00'}],
      patterns: [{label:'Moved Together',count:1,percentage:'100.00'}],
      exams: {labels:['Prelim','Midterm','Final'],datasets:[
        {label:'Period averages',connected:false,values:['80.00',null,'90.00']},
        {label:'Matched students',connected:true,values:['80.00','85.00',null]}]}}};
}

test('one filter form and three lazy tabs send CSRF to the unified route', async () => {
  const h=harness(); assert.equal(h.calls.length,0);
  await h.tabs[1].click(); assert.equal(h.calls.length,0);
  const pending=h.submit();
  assert.match(h.calls[0].url,/academic-performance-insights\/unified-preview/);
  assert.equal(h.calls[0].options.body.get('tab'),'exams');
  assert.equal(h.calls[0].options.body.get('csrfmiddlewaretoken'),'csrf-value');
  h.respond(0,report('exams')); await pending;
});

test('exam and alignment reuse one engine token while overview loads separately', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.tabs[1].click(); assert.equal(h.calls[1].options.body.get('action'),'report');
  h.respond(1,report('exams','assessment-token')); await pending;
  pending=h.tabs[2].click(); assert.equal(h.calls[2].options.body.get('action'),'page');
  assert.equal(h.calls[2].options.body.get('token'),'assessment-token');
  h.respond(2,report('alignment','assessment-token')); await pending;
  pending=h.tabs[0].click(); assert.equal(h.calls[3].options.body.get('token'),'generation');
  h.respond(3,report()); await pending;
  assert.equal(h.elements['learning-results'].attributes['aria-labelledby'],'tab-overview');
});

test('exam chart keeps gaps and uses connections only for matched datasets', async () => {
  const h=harness(); await h.tabs[1].click(); const pending=h.submit();
  h.respond(0,report('exams')); await pending;
  // The fake DOM supplies every canvas; select the dedicated exam chart.
  const chart=h.drawn.find(c=>c.canvas===h.elements['unified-exams']);
  assert.ok(chart);
  assert.deepEqual(Array.from(chart.config.data.datasets[0].data),[80,null,90]);
  assert.equal(chart.config.data.datasets[0].showLine,false);
  assert.equal(chart.config.data.datasets[1].showLine,true);
  assert.equal(chart.config.data.datasets[1].spanGaps,false);
});

test('alignment uses independent output and omits an all-zero movement pie', async () => {
  const h=harness(); await h.tabs[2].click(); const pending=h.submit(); const data=report('alignment');
  data.charts.patterns=[{label:'Moved Together',count:0,percentage:null}];
  h.respond(0,data); await pending;
  const bar=h.drawn.find(c=>c.config.type==='bar');
  assert.equal(bar.config.data.datasets[1].label,'Participation/Output');
  assert.deepEqual(Array.from(bar.config.data.datasets,d=>d.data[0]),[0,null,80]);
  assert.equal(h.drawn.some(c=>c.config.type==='doughnut'),false);
});

test('rapid assessment tab switch waits for one cold generation and discards the old presentation', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  const old=h.tabs[1].click(); const current=h.tabs[2].click();
  assert.equal(h.calls.length,2); // no second cold assessment report
  h.respond(1,{...report('exams','assessment-token'),html:'old exam response'}); await h.tick();
  assert.notEqual(h.elements['learning-results'].innerHTML,'old exam response');
  assert.equal(h.calls[2].options.body.get('action'),'page');
  assert.equal(h.calls[2].options.body.get('token'),'assessment-token');
  h.respond(2,{...report('alignment'),html:'current alignment'}); await old; await current;
  assert.equal(h.elements['learning-results'].innerHTML,'current alignment');
});

test('filter change invalidates every tab token and requires explicit generation', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.formHandlers.change(); h.respond(1,{filters:{},options:{}}); await pending;
  await h.tabs[1].click(); assert.equal(h.calls.length,2);
  pending=h.submit(); assert.equal(h.calls[2].options.body.get('action'),'report');
  assert.equal(h.calls[2].options.body.get('token'),undefined);
  h.respond(2,report('exams')); await pending;
});

test('pending filter verification survives rapid switches across all tabs', async () => {
  const h=harness();
  const verifying=h.formHandlers.change();
  assert.equal(h.elements['learning-view'].disabled,true);
  await h.tabs[1].click(); await h.tabs[2].click(); await h.tabs[0].click();
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].options.signal.aborted,false);
  h.respond(0,{filters:{},options:{}}); await verifying;
  assert.equal(h.elements['learning-view'].disabled,false);
  assert.equal(h.elements['learning-retry'].hidden,true);
  assert.equal(h.elements['learning-progress'].hidden,true);
  const pending=h.submit();
  h.respond(1,report()); await pending;
});

test('failed verification keeps retry accessible across tabs and recovers current filters', async () => {
  const h=harness();
  h.selects.find(s=>s.name==='course_code').value='AE105-FAR';
  const verifying=h.formHandlers.change(); await h.tabs[1].click();
  h.respond(0,{error:'Selection service unavailable'},503); await verifying;
  await h.tabs[2].click();
  assert.equal(h.elements['learning-view'].disabled,true);
  assert.equal(h.elements['learning-retry'].hidden,false);
  assert.equal(h.elements['learning-retry'].disabled,false);
  const retried=h.elements['learning-retry'].click();
  assert.equal(h.calls[1].options.body.get('course_code'),'AE105-FAR');
  h.respond(1,{filters:{},options:{}}); await retried;
  assert.equal(h.elements['learning-view'].disabled,false);
  const pending=h.submit();
  assert.equal(h.calls[2].options.body.get('tab'),'alignment');
  h.respond(2,report('alignment')); await pending;
});

test('interrupted current verification shows retry and never leaves progress stuck', async () => {
  const h=harness(); const verifying=h.formHandlers.change();
  await h.tabs[1].click();
  h.calls[0].reject(Object.assign(new Error('interrupted'),{name:'AbortError'})); await verifying;
  assert.equal(h.elements['learning-view'].disabled,true);
  assert.equal(h.elements['learning-retry'].hidden,false);
  assert.equal(h.elements['learning-progress'].hidden,true);
  const retried=h.elements['learning-retry'].click();
  await h.tabs[2].click();
  assert.equal(h.calls.length,2);
  h.respond(1,{filters:{},options:{}}); await retried;
  assert.equal(h.elements['learning-view'].disabled,false);
  assert.equal(h.elements['learning-retry'].hidden,true);
});

test('stale options cannot overwrite a newer filter while tabs switch', async () => {
  const h=harness(); const older=h.formHandlers.change();
  h.selects.find(s=>s.name==='course_code').value='NEW';
  const newer=h.formHandlers.change();
  await h.tabs[1].click(); await h.tabs[2].click();
  assert.equal(h.calls.length,2);
  assert.equal(h.calls[0].options.signal.aborted,true);
  assert.equal(h.calls[1].options.signal.aborted,false);
  h.respond(0,{filters:{course_code:'OLD'},options:{course_code:[{label:'Old',value:'OLD'}]}}); await older;
  assert.equal(h.elements['learning-view'].disabled,true);
  assert.equal(h.selects.find(s=>s.name==='course_code').value,'NEW');
  h.respond(1,{filters:{course_code:'NEW'},options:{course_code:[{label:'New',value:'NEW'}]}}); await newer;
  assert.equal(h.selects.find(s=>s.name==='course_code').value,'NEW');
  assert.equal(h.elements['learning-view'].disabled,false);
});

test('late success cannot hide retry after the newer selection fails', async () => {
  const h=harness(); const older=h.formHandlers.change(); const newer=h.formHandlers.change();
  await h.tabs[1].click();
  h.respond(1,{error:'Unavailable'},503); await newer;
  h.respond(0,{filters:{},options:{}}); await older;
  assert.equal(h.elements['learning-view'].disabled,true);
  assert.equal(h.elements['learning-retry'].hidden,false);
  const retried=h.elements['learning-retry'].click();
  h.respond(2,{filters:{},options:{}}); await retried;
  assert.equal(h.elements['learning-view'].disabled,false);
});

test('stale token clears all old results and keyboard tabs remain accessible', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.click('page',2); h.respond(1,{error:'Records changed'},409); await pending;
  assert.equal(h.elements['learning-results'].innerHTML,'');
  assert.ok(h.drawn.every(c=>c.destroyed));
  h.tabs[0].keydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(h.tabs[1].focused,true);
  assert.equal(h.tabs[1].attributes['aria-selected'],'true');
});

test('course drilldown validates shared selections before generating details', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.click('course','AE105-FAR');
  assert.equal(h.calls[1].options.body.get('course_code'),'AE105-FAR');
  assert.equal(h.calls[1].options.body.get('offering_id'),'');
  h.respond(1,{filters:{},options:{}}); await h.tick();
  assert.equal(h.calls[2].options.body.get('action'),'report');
  h.respond(2,report()); await pending;
});
