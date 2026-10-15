const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require(process.env.TMP_JSDOM_PATH || 'jsdom');
const script = readFileSync(join(__dirname, '../../static/admin_portal/js/grade_distribution_monitor.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function setup() {
  const dom = new JSDOM(`<form id="gradeDistributionFilterForm"><input name="apply" value="1">
    <select name="campus_id"><option value="1">A</option><option value="2">B</option></select>
    <select name="academic_year_id"><option value="1">AY</option></select><select name="term_id"><option value="1">Term</option></select>
    <select name="faculty_id"><option value="">All</option></select><select name="department_id"><option value="">All</option></select>
    <select name="sort"><option value="exact_100_desc">Exactly 100 Count</option><option value="faculty_asc">Faculty</option></select><select name="status"><option value="all">All</option><option value="finalized">Finalized</option></select><button type="submit">Apply</button></form>
    <div id="gdm-results"><button id="open" data-analytics-url="/detail?context=signed">View</button><nav class="pagination"><a class="page-link" href="?page=2">Next</a></nav></div><div id="gdm-filter-status"></div>
    <div id="gradeDistributionAnalyticsModal"><div id="gdm-modal-body"></div></div>`, {url:'https://example.test/monitor', runScripts:'outside-only'});
  const w = dom.window, d = w.document;
  w.eval(script); d.dispatchEvent(new w.Event('DOMContentLoaded'));
  const change = name => d.querySelector(`[name="${name}"]`).dispatchEvent(new w.Event('change', {bubbles:true}));
  const open = () => { const e = new w.Event('show.bs.modal'); e.relatedTarget = d.getElementById('open'); d.getElementById('gradeDistributionAnalyticsModal').dispatchEvent(e); };
  return {dom, w, d, change, open};
}
const metric = {dataset_id:'offering:1/period:1/period_grade/status:all',offering_id:1,physical_period_id:1,activity_id:null,selection_status:'all',gradebook_context:'Prelim; Period #1',analytics_state:'disclosed', graded_count:10, eligible_count:10, average:'75.00', minimum:'75.00', maximum:'75.00', exact_100_count:0, exact_100_percentage:'0.00', all_100:false,
  finalized_count:0, unfinalized_count:10, status:'Unfinalized', high_grade_breakdown:[{grade:'Exactly 100',count:0,total_graded_students:10,percentage:'0.00'}, {grade:'Combined 95–99',count:0,total_graded_students:10,percentage:'0.00'},{grade:'90 and above',count:0,total_graded_students:10,percentage:'0.00'}], frequency:[{grade:'75.00',count:10,percentage:'100.00'}]};
const payload = {...metric, faculty_name:'Person, Faculty', course_code:'CODE', course_title:'<img src=x onerror=alert(1)>', section:'A', campus:'C', school_year:'AY', term:'1', period:'PRELIM',
  missing_count:0, status_excluded_count:0, incomplete_source_count:0, notes:['Saved Gradebook grades including Base 50'], measures:[{...metric,dataset_id:'offering:1/period:1/activity:10/status:all',activity_id:10,gradebook_context:'Prelim; Period #1; Written; Activity #10',label:'Written assessment',unit:'Saved Gradebook activity grade',equivalent_to:'Period Grades',equivalent_dataset_id:metric.dataset_id,equivalent_context:metric.gradebook_context}]};
const activityMetric = (label,id,overrides={}) => ({...metric,...overrides,dataset_id:`offering:1/period:1/activity:${id}/status:all`,activity_id:id,gradebook_context:overrides.gradebook_context || `Prelim; Period #1; Written; Activity #${id}`,label});
const hidden = {...metric, analytics_state:'privacy_suppressed',privacy_suppressed:true,privacy_message:'Withheld because grading formulas could reveal grades for fewer than five students.',average:null,minimum:null,maximum:null,exact_100_count:null,exact_100_percentage:null,all_100:null,frequency:[],high_grade_breakdown:metric.high_grade_breakdown.map(i=>({...i,count:null,percentage:null}))};
async function render(data) {
  const s = setup(); s.w.fetch = async()=>({ok:true,json:async()=>data}); s.open(); await tick(); return s;
}
test('initial state makes no request and filter change clears stale results', async()=>{
  const s = setup(); let calls = 0; s.w.fetch=()=>{calls++; throw Error('unexpected request');}; await tick();
  assert.equal(calls,0); s.change('status'); assert.equal(calls,0); assert.equal(s.d.getElementById('gdm-results').hidden,true); assert.match(s.d.getElementById('gdm-filter-status').textContent,/Apply Filters/); s.dom.window.close();
});
test('campus options update stable IDs without applying the search',async()=>{
  const s=setup(); let url; s.w.fetch=async u=>{url=u;return {ok:true,json:async()=>({faculty:[{id:9,label:'Faculty'}],departments:[{id:8,label:'Department'}],terms:[{id:1,label:'Term'}]})};};
  s.change('campus_id');await tick(); assert.match(url,/options=1/);assert.doesNotMatch(url,/apply=/);assert.equal(s.d.querySelector('[name="faculty_id"]').options[1].value,'9');assert.equal(s.d.querySelector('button[type="submit"]').disabled,false);s.dom.window.close();
});
test('one numerical chart per distinct measure with matching accessible frequencies',async()=>{
  const s=await render(payload), body=s.d.getElementById('gdm-modal-body');
  assert.equal(body.querySelectorAll('svg').length,1); assert.equal(body.querySelector('svg rect').getAttribute('height'),'150');
  assert.match(body.querySelector('.gdm-frequency table').textContent,/75.0010100.00/); assert.match(body.querySelector('svg').getAttribute('aria-label'),/Gradebook grade/);
  assert.match(body.textContent,/Same Gradebook values.*Period Grades/); assert.equal(body.querySelectorAll('section details').length,0); assert.equal(body.hasAttribute('aria-busy'),false); s.dom.window.close();
});
test('period exam standing and individual activities are expanded and clearly named',async()=>{
  const s=await render({...payload,measures:['Examination Grades','Class Standing Grades','Written assessment','Quiz'].map((label,i)=>activityMetric(label,i+10))});
  const body=s.d.getElementById('gdm-modal-body');assert.equal(body.querySelectorAll(':scope > section').length,5);assert.equal(body.querySelectorAll('svg').length,5);
  assert.equal(body.querySelector('h5').textContent,payload.faculty_name);assert.equal(body.querySelectorAll('img').length,0);assert.match(body.textContent,/<img src=x/);
  assert.equal(body.querySelectorAll('details').length,1);assert.equal(body.querySelector('details summary').textContent,'Data Notes');assert.doesNotMatch(body.textContent,/Threshold reference|component grade/);s.dom.window.close();
});
test('suppressed measures produce no numeric charts tables or metric tiles',async()=>{
  const s=await render({...payload,...hidden,measures:[activityMetric('Small assessment',10,hidden)]}); const body=s.d.getElementById('gdm-modal-body');
  assert.equal(body.querySelectorAll('svg, table, .gdm-metric').length,0);assert.match(body.textContent,/fewer than five/);assert.doesNotMatch(body.textContent,/75.00|Exactly 100/);s.dom.window.close();
});
test('safe sibling charts remain while dependent period and examination totals are suppressed',async()=>{
  const s=await render({...payload,...hidden,measures:[activityMetric('Examination Grades',10,hidden),activityMetric('Common assessment',11)]});
  const sections=s.d.querySelectorAll('section');assert.equal(sections[0].querySelector('svg'),null);assert.equal(sections[1].querySelector('svg'),null);assert.ok(sections[2].querySelector('svg'));s.dom.window.close();
});
test('frequency pages show every value in the accessible table without a second chart',async()=>{
  const frequency=Array.from({length:30},(_,i)=>({grade:String(i+50),count:1,percentage:'3.33'}));const s=await render({...payload,frequency,measures:[]});
  const wrap=s.d.querySelector('.gdm-frequency');assert.equal(wrap.querySelectorAll('svg rect').length,25);assert.equal(wrap.querySelectorAll('tbody tr').length,30);
  [...wrap.querySelectorAll('button')].find(b=>b.textContent==='Next grades').click();assert.equal(wrap.querySelectorAll('svg rect').length,5);assert.equal(wrap.querySelectorAll('tbody tr').length,30);s.dom.window.close();
});
test('late response cannot restore data after modal closes',async()=>{
  const s=setup();let resolve;s.w.fetch=async()=>({ok:true,json:()=>new Promise(r=>{resolve=r;})});s.open();await tick();
  s.d.getElementById('gradeDistributionAnalyticsModal').dispatchEvent(new s.w.Event('hidden.bs.modal'));resolve(payload);await tick();assert.equal(s.d.getElementById('gdm-modal-body').textContent,'');s.dom.window.close();
});
test('HTTP errors clear loading and restore the keyboard opener',async()=>{
  const s=setup();s.w.fetch=async()=>({ok:false,json:async()=>({errors:['Apply Filters again.']})});s.open();await tick();
  assert.match(s.d.querySelector('[role="alert"]').textContent,/Apply Filters/);assert.equal(s.d.getElementById('gdm-modal-body').hasAttribute('aria-busy'),false);
  s.d.getElementById('gradeDistributionAnalyticsModal').dispatchEvent(new s.w.Event('hidden.bs.modal'));assert.equal(s.d.activeElement.id,'open');s.dom.window.close();
});
test('submit and pagination show indeterminate progress and pageshow clears it',()=>{
  const s=setup();const form=s.d.getElementById('gradeDistributionFilterForm');form.dispatchEvent(new s.w.Event('submit'));
  assert.equal(form.getAttribute('aria-busy'),'true');assert.equal(s.d.querySelector('[role="progressbar"]').hasAttribute('aria-valuenow'),false);
  s.w.dispatchEvent(new s.w.Event('pageshow'));assert.equal(form.hasAttribute('aria-busy'),false);assert.equal(s.d.querySelector('[role="progressbar"]'),null);
  const link=s.d.querySelector('.pagination a');link.addEventListener('click',e=>e.preventDefault());link.dispatchEvent(new s.w.MouseEvent('click',{bubbles:true,cancelable:true}));assert.equal(form.getAttribute('aria-busy'),'true');s.dom.window.close();
});
test('actual Django formula-privacy payload renders only safe distinct charts',async()=>{
  assert.ok(process.env.TMP_MONITOR_DOM_PAYLOAD,'Run the Django payload fixture first and set TMP_MONITOR_DOM_PAYLOAD.');
  const data=JSON.parse(readFileSync(process.env.TMP_MONITOR_DOM_PAYLOAD,'utf8')), s=await render(data), body=s.d.getElementById('gdm-modal-body');
  const metrics=[data,...data.measures];assert.equal(data.analytics_state,'privacy_suppressed');
  const expected=metrics.filter(m=>m.analytics_state==='disclosed' && !m.equivalent_dataset_id && m.frequency.length).length;
  assert.equal(body.querySelectorAll('svg').length,expected);assert.ok(expected>0);assert.doesNotMatch(body.textContent,/43.00|788/);
  for(const [i,m] of metrics.entries()) if(m.analytics_state!=='disclosed') assert.equal(body.querySelectorAll('section')[i].querySelector('svg,table,.gdm-metric'),null);
  s.dom.window.close();
});

test('actual same-title Quiz datasets keep separate route context charts and frequency identities',async()=>{
  assert.ok(process.env.TMP_DATASET_DOM_PAYLOAD,'Run the Django duplicate Quiz fixture first.');
  const data=JSON.parse(readFileSync(process.env.TMP_DATASET_DOM_PAYLOAD,'utf8')),s=await render(data);
  const sections=[...s.d.querySelectorAll('section')].filter(el=>el.querySelector('h4').textContent==='Quiz');
  assert.equal(sections.length,2);assert.notEqual(sections[0].dataset.datasetId,sections[1].dataset.datasetId);assert.notEqual(sections[0].id,sections[1].id);
  assert.match(sections[0].textContent,/Written Quizzes/);assert.match(sections[1].textContent,/Oral Quizzes/);
  for(const section of sections){
    assert.equal(section.querySelectorAll('svg').length,1);
    assert.equal(section.querySelector('svg').dataset.datasetId,section.dataset.datasetId);
    const table=section.querySelector('.gdm-frequency table');assert.equal(table.dataset.datasetId,section.dataset.datasetId);
    const cells=[...table.querySelector('tbody tr').cells];assert.equal(cells[1].textContent,'10');assert.equal(cells[2].textContent,'100.00');
  }
  s.dom.window.close();
});
test('same-title equivalent Quiz links to the exact dataset and retains its own record identity',async()=>{
  const first=activityMetric('Quiz',10),second=activityMetric('Quiz',11,{gradebook_context:'Prelim; Period #1; Oral; Activity #11',equivalent_to:'Quiz',equivalent_dataset_id:first.dataset_id,equivalent_context:first.gradebook_context});
  const s=await render({...payload,measures:[first,second]});const sections=s.d.querySelectorAll('section'),target=sections[1],alias=sections[2];
  assert.notEqual(target.dataset.datasetId,alias.dataset.datasetId);assert.equal(alias.dataset.equivalentDatasetId,target.dataset.datasetId);
  assert.equal(alias.querySelector('a').getAttribute('href'),`#${target.id}`);assert.match(alias.textContent,/Activity #10/);assert.match(alias.textContent,/Activity #11/);
  assert.equal(alias.querySelector('svg'),null);assert.ok(target.querySelector('svg'));s.dom.window.close();
});
test('actual distinct physical Prelim contexts remain separate when opening another modal dataset',async()=>{
  assert.ok(process.env.TMP_PERIOD_DATASET_DOM_PAYLOAD,'Run the Django two-Prelim fixture first.');
  const datasets=JSON.parse(readFileSync(process.env.TMP_PERIOD_DATASET_DOM_PAYLOAD,'utf8')),s=setup();
  assert.equal(datasets.length,2);assert.equal(datasets[0].period,datasets[1].period);assert.notEqual(datasets[0].dataset_id,datasets[1].dataset_id);
  for(const data of datasets){
    s.w.fetch=async()=>({ok:true,json:async()=>data});s.open();await tick();
    const section=s.d.querySelector('section');assert.equal(section.dataset.datasetId,data.dataset_id);
    assert.equal(section.querySelector('svg').dataset.datasetId,data.dataset_id);assert.match(section.textContent,new RegExp(`Period #${data.physical_period_id}`));
    assert.equal(s.d.querySelectorAll('section[data-dataset-id="'+data.dataset_id+'"]').length,1);
  }
  assert.equal(s.d.querySelector('section[data-dataset-id="'+datasets[0].dataset_id+'"]'),null);s.dom.window.close();
});


test('sort change clears stale results and modal without fetching until Apply', async()=>{
  const s=setup();let calls=0;s.w.fetch=()=>{calls++;throw Error('unexpected fetch');};
  s.d.getElementById('gdm-modal-body').textContent='old analytics';
  s.d.querySelector('[name="sort"]').value='faculty_asc';s.change('sort');await tick();
  assert.equal(calls,0);assert.equal(s.d.getElementById('gdm-results').hidden,true);
  assert.equal(s.d.getElementById('gdm-modal-body').textContent,'');
  assert.match(s.d.getElementById('gdm-filter-status').textContent,/Apply Filters/);
  assert.equal(new s.w.FormData(s.d.querySelector('form')).get('sort'),'faculty_asc');
  assert.equal(s.d.querySelector('[name="page"]'),null);s.dom.window.close();
});
