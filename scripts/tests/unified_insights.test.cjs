const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({offering = '', chartFailure = false, reducedMotion = false} = {}) {
  const calls = [], handlers = {}, formHandlers = {}, windowHandlers = {}, drawn = [];
  class Element {
    constructor() { this.hidden = true; this.value = ''; this.style = {}; this.dataset = {}; this.attributes = {}; this.options = [{textContent: ''}]; this.childNodes = []; }
    set innerHTML(html) { this.childNodes = html ? [{html}] : []; }
    get innerHTML() { return this.childNodes.map(node => node.html || '').join(''); }
    replaceChildren(...children) { this.childNodes = children; this.children = children; this.options = children; this.value = ''; }
    add(option) { this.options.push(option); }
    addEventListener(event, callback) { this[event] = callback; }
    appendChild(child) { this.childNodes.push(child); child.parentElement = this; }
    getBoundingClientRect() { return {left:0,top:0,width:220,height:100}; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    focus() { this.focused = true; }
  }
  const elements = {'unified-exams': new Element(), 'learning-verification': new Element(), 'learning-result-shell': new Element()};
  for (const id of ['learning-filters', 'learning-results', 'learning-progress', 'learning-message', 'learning-view', 'learning-retry', 'learning-overall', 'learning-csv-controls', 'learning-csv-period', 'learning-selected', 'learning-line', 'learning-bars', 'learning-patterns']) elements[id] = new Element();
  elements['learning-bars'].parentElement = new Element();
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
    constructor(canvas, config) { if (chartFailure) throw new Error('drawing failed'); this.config = config; this.data=config.data; this.canvas = canvas; this.width=600; this.height=290; this.tooltip={setActiveElements:(active,point)=>{this.tooltipActive=active;this.point=point;}}; drawn.push(this); }
    destroy() { this.destroyed = true; }
    resize() { this.resized = true; }
    setActiveElements(active) { this.active=active; }
    getDatasetMeta() { return {data:this.data.labels.map(()=>({getCenterPoint:()=>({x:100,y:100})}))}; }
    update(mode) { this.updateMode=mode; }
  }
  const tabs = ['overview','exams','alignment'].map(name => Object.assign(new Element(), {dataset: {unifiedTab: name}}));
  const context = {URLSearchParams, FormData: Data, Option: function(text, value) { this.textContent = text; this.value = value; }, AbortController,
    location: {pathname: '/admin-portal/grading/academic-performance-insights/unified-preview/'},
    document: {getElementById: id => elements[id], createElement:()=>new Element(), querySelectorAll: selector => selector === '[data-learning-export]' ? links : selector === '[data-unified-tab]' ? tabs : [], addEventListener: (event, callback) => handlers[event] = callback},
    window: {Chart, matchMedia: () => ({matches: reducedMotion}), addEventListener: (event, callback) => windowHandlers[event] = callback},
    fetch: (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject})),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/admin_portal/unified_insights.js'), 'utf8'), context);
  const respond = (index, data, status = 200, type = 'application/json') => calls[index].resolve({status, ok: status < 400, headers: {get: () => type}, json: async () => data});
  const submit = () => formHandlers.submit({preventDefault() {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const click = (kind, value) => handlers.click({preventDefault() { if (kind === 'export') links[0].prevented = true; }, target: {closest: selector => selector === '[data-learning-export]' && kind === 'export' ? links[0] : selector === '[data-unified-page], [data-learning-page]' && kind === 'page' ? {dataset: {unifiedPage: value}} : selector === '[data-unified-course]' && kind === 'course' ? {dataset: {unifiedCourse: value}} : selector === '[data-unified-section]' && kind === 'section' ? {dataset: {unifiedSection: value}} : null}});
  return {tabs, calls, elements, selects, links, drawn, respond, submit, tick, click, formHandlers, windowHandlers, handlers};
}

function report(tab = 'overview', token = 'generation') {
  return {token, tab, generation: tab === 'overview' ? 'overview-generation' : 'assessment-generation', html: '<p>Accessible academic results</p>', export_periods: tab === 'overview' ? [['MIDTERM','Midterm']] : [],
    charts: {threshold: '3.00', bar_period:'Midterm', line: [], line_mode: 'descriptive',
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
  assert.equal(h.calls[3].options.body.get('action'),'verify');
  h.respond(3,{...report(),valid:true}); await pending;
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

test('return tab stays hidden until verification then restores identical nodes and charts', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  const nodes=[...h.elements['learning-results'].childNodes], charts=[...h.drawn];
  pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
  const count=h.drawn.length;
  pending=h.tabs[0].click();
  assert.equal(h.calls[2].options.body.get('action'),'verify');
  assert.equal(h.elements['learning-results'].hidden,true);
  assert.equal(h.elements['learning-results'].innerHTML,'');
  assert.equal(h.elements['learning-progress'].hidden,true);
  assert.equal(h.elements['learning-verification'].hidden,false);
  assert.ok(charts.every(chart=>!chart.destroyed));
  h.respond(2,{...report(),valid:true}); await pending;
  assert.equal(h.elements['learning-results'].hidden,false);
  assert.equal(h.elements['learning-results'].childNodes[0],nodes[0]);
  assert.equal(h.drawn.length,count);
  assert.ok(charts.every(chart=>chart.resized && !chart.destroyed));
  assert.equal(h.elements['learning-verification'].hidden,true);
});

test('first load uses progress while cached return reserves the prior layout quietly', async () => {
  const h=harness(); h.elements['learning-results'].getBoundingClientRect=()=>({height:640});
  let pending=h.submit();
  assert.equal(h.elements['learning-progress'].hidden,false);
  assert.equal(h.elements['learning-verification'].hidden,true);
  h.respond(0,report()); await pending;
  pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
  pending=h.tabs[0].click();
  assert.equal(h.elements['learning-result-shell'].style.minHeight,'640px');
  assert.equal(h.elements['learning-verification'].style.minHeight,undefined);
  assert.equal(h.elements['learning-verification'].hidden,false);
  assert.equal(h.elements['learning-progress'].hidden,true);
  assert.equal(h.elements['learning-results'].hidden,true);
  h.respond(2,{...report(),valid:true}); await pending;
  assert.equal(h.elements['learning-results'].hidden,false);
  assert.equal(h.elements['learning-result-shell'].style.minHeight,'');
});

test('bar hover uses a bounded light tooltip with exact section, period and three values', async () => {
  const h=harness(); const pending=h.submit(); const data=report();
  data.charts.bars[0]={section:'A very long section <script>text remains text</script>',quiz_grade:'92.03',standing:'94.35',exam:'68.48'};
  h.respond(0,data); await pending;
  const chart=h.drawn.find(c=>c.config.type==='bar'); const options=chart.config.options.plugins.tooltip;
  assert.equal(options.enabled,false); assert.equal(options.callbacks,undefined);
  options.external({chart,tooltip:{opacity:1,dataPoints:[{dataIndex:0}],caretX:595,caretY:285}});
  const tip=chart.academicTooltip;
  assert.equal(tip.className,'academic-chart-tooltip');
  assert.match(tip.childNodes[0].textContent,/Midterm/);
  assert.match(tip.childNodes[0].textContent,/<script>text remains text<\/script>/);
  assert.deepEqual(tip.childNodes[1].childNodes.map(n=>n.textContent),['Quiz: 92.03','Class Standing: 94.35','Examination: 68.48']);
  assert.ok(parseFloat(tip.style.left)+220<=600); assert.ok(parseFloat(tip.style.top)+100<=290);
  assert.equal(h.drawn.length,3);
  options.external({chart,tooltip:{opacity:0}}); assert.equal(tip.hidden,true);
  const css=fs.readFileSync(path.join(__dirname,'../../static/admin_portal/unified_insights.css'),'utf8');
  assert.match(css,/max-inline-size: min\(240px, calc\(100% - 16px\)\)/);
  assert.match(css,/background: #fff/);
});

test('keyboard and touch chart controls activate existing bars and Escape hides the tooltip', async () => {
  const h=harness(); const pending=h.submit(); h.respond(0,report()); await pending;
  const chart=h.drawn.find(c=>c.config.type==='bar'), count=h.drawn.length;
  const button={dataset:{learningBarIndex:'0'}};
  h.handlers.focusin({target:{closest:()=>button}});
  assert.equal(chart.active.length,2); assert.equal(chart.updateMode,'none');
  await h.handlers.click({target:{closest:()=>button}});
  assert.equal(chart.active.length,2); assert.equal(h.drawn.length,count);
  chart.academicTooltip={hidden:false}; h.handlers.keydown({key:'Escape'});
  assert.equal(chart.academicTooltip.hidden,true); assert.equal(chart.active.length,0);
  h.handlers.focusin({target:{closest:()=>({dataset:{learningBarIndex:'-1'}})}});
  assert.equal(chart.active.length,0);
});

test('cached chart tooltip remains hidden throughout quiet return authorization', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  const chart=h.drawn.find(c=>c.config.type==='bar');
  chart.academicTooltip={hidden:false};
  pending=h.tabs[1].click(); assert.equal(chart.academicTooltip.hidden,true);
  h.respond(1,report('exams')); await pending;
  const count=h.drawn.length;
  pending=h.tabs[0].click(); assert.equal(h.elements['learning-results'].hidden,true);
  assert.equal(h.elements['learning-progress'].hidden,true); assert.equal(chart.academicTooltip.hidden,true);
  h.respond(2,{...report(),valid:true}); await pending;
  assert.equal(h.drawn.length,count);
});

test('return preserves selected section and last page while pagination replaces only its tab', async () => {
  const h=harness({offering:'17'}); let pending=h.submit(); h.respond(0,report()); await pending;
  const firstCharts=[...h.drawn];
  pending=h.click('page',2); h.respond(1,{...report(),html:'page two'}); await pending;
  assert.ok(firstCharts.every(chart=>chart.destroyed));
  pending=h.tabs[1].click(); h.respond(2,report('exams')); await pending;
  pending=h.tabs[0].click();
  assert.equal(h.calls[3].options.body.get('page'),2);
  assert.equal(h.calls[3].options.body.get('offering_id'),'17');
  assert.equal(h.calls[3].options.body.get('course_code'),'IT');
  h.respond(3,{...report(),valid:true}); await pending;
  assert.equal(h.elements['learning-results'].innerHTML,'page two');
});

test('403 and 409 verification purge every presentation and allow a fresh retry', async () => {
  for (const code of [403,409]) {
    const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
    pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
    pending=h.tabs[0].click(); h.respond(2,{error:'Access or records changed'},code); await pending;
    assert.equal(h.elements['learning-results'].hidden,true);
    assert.equal(h.elements['learning-results'].innerHTML,'');
    assert.ok(h.drawn.every(chart=>chart.destroyed));
    assert.equal(h.elements['learning-retry'].hidden,false);
    assert.equal(h.elements['learning-view'].disabled,false);
    pending=h.elements['learning-retry'].click();
    assert.equal(h.calls[3].options.body.get('action'),'report');
    assert.equal(h.calls[3].options.body.get('token'),undefined);
    h.respond(3,report()); await pending;
    assert.equal(h.elements['learning-results'].hidden,false);
  }
});

test('failed or interrupted return verification keeps data hidden and retry revalidates', async () => {
  for (const failure of ['abort','network','server']) {
    const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
    pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
    const count=h.drawn.length;
    pending=h.tabs[0].click();
    if(failure==='server') h.respond(2,{error:'Unavailable'},503);
    else h.calls[2].reject(Object.assign(new Error('Interrupted'),{name:failure==='abort'?'AbortError':'TypeError'}));
    await pending;
    assert.equal(h.elements['learning-results'].hidden,true);
    assert.equal(h.elements['learning-retry'].hidden,false);
    assert.equal(h.elements['learning-progress'].hidden,true);
    pending=h.elements['learning-retry'].click();
    assert.equal(h.calls[3].options.body.get('action'),'verify');
    h.respond(3,{...report(),valid:true}); await pending;
    assert.equal(h.drawn.length,count);
    assert.equal(h.elements['learning-results'].hidden,false);
  }
});

test('rapid switches across engines serialize requests and restore only the final tab', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  const first=h.tabs[1].click(); await h.tabs[2].click(); await h.tabs[0].click();
  assert.equal(h.calls.length,2);
  assert.equal(h.calls[1].options.signal.aborted,false);
  h.respond(1,report('exams')); await h.tick();
  assert.equal(h.calls.length,3);
  assert.equal(h.calls[2].options.body.get('action'),'verify');
  assert.equal(h.calls[2].options.body.get('tab'),'overview');
  assert.equal(h.elements['learning-results'].hidden,true);
  h.respond(2,{...report(),valid:true}); await first;
  assert.equal(h.elements['learning-results'].hidden,false);
  assert.equal(h.drawn.length,3);
});

test('late return verification cannot restore cached grades after a section change', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
  const old=h.tabs[0].click();
  h.selects.find(s=>s.name==='offering_id').value='99';
  const changed=h.formHandlers.change();
  h.respond(2,{...report(),valid:true}); await old;
  assert.equal(h.elements['learning-results'].innerHTML,'');
  assert.equal(h.elements['learning-view'].disabled,true);
  h.respond(3,{filters:{offering_id:'99'},options:{}}); await changed;
  assert.equal(h.elements['learning-results'].hidden,true);
  pending=h.submit(); assert.equal(h.calls[4].options.body.get('action'),'report');
  h.respond(4,report()); await pending;
});

test('verification generation mismatch and expired-session HTML never restore cached grades', async () => {
  for (const failure of ['generation','session']) {
    const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
    pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
    pending=h.tabs[0].click();
    if(failure==='generation') h.respond(2,{...report(),valid:true,generation:'changed'});
    else h.respond(2,'Login',200,'text/html');
    await pending;
    assert.equal(h.elements['learning-results'].hidden,true);
    assert.ok(h.drawn.every(chart=>chart.destroyed));
    assert.equal(h.elements['learning-retry'].hidden,false);
    assert.equal(h.elements['learning-view'].disabled,false);
  }
});

test('explicit View Performance and pagehide discard retained presentations', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  const old=[...h.drawn]; pending=h.submit();
  assert.equal(h.calls[1].options.body.get('action'),'report');
  assert.ok(old.every(chart=>chart.destroyed));
  h.respond(1,report()); await pending;
  h.windowHandlers.pagehide();
  assert.ok(h.drawn.every(chart=>chart.destroyed));
  assert.equal(h.elements['learning-results'].hidden,true);
});

test('late prior verification cannot enable refresh during a newer report request', async () => {
  const h=harness(); let pending=h.submit(); h.respond(0,report()); await pending;
  pending=h.tabs[1].click(); h.respond(1,report('exams')); await pending;
  const old=h.tabs[0].click();
  const changed=h.formHandlers.change(); h.respond(3,{filters:{},options:{}}); await changed;
  const current=h.submit();
  h.respond(2,{...report(),valid:true}); await old;
  assert.equal(h.elements['learning-view'].disabled,true);
  await h.submit(); assert.equal(h.calls.length,5);
  h.respond(4,report()); await current;
  assert.equal(h.elements['learning-view'].disabled,false);
});
