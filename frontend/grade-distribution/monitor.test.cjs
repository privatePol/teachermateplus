const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require(process.env.TMP_JSDOM_PATH || 'jsdom');
const script = readFileSync(join(__dirname, '../../static/admin_portal/js/grade_distribution_monitor.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const response = (data, status = 200, type = 'application/json') => ({ok: status < 400, status, redirected: false, headers: {get: () => type}, json: async () => data});
const empty = {html:'<p>Select criteria</p>', url:'/monitor', selected:{grade_min:'97',grade_max:'100'}};
const options = {faculty:[{id:9,label:'Faculty'}],departments:[{id:8,label:'Department'}],terms:[{id:1,label:'Term'}]};
function setup(auto = false) {
  const dom = new JSDOM(`<form id="gradeDistributionFilterForm" data-default-campus="1"><input name="csrfmiddlewaretoken" value="test-csrf"><input name="apply" value="1"><input name="render" value="full">
    <select name="campus_id"><option value="1">A</option><option value="2">B</option></select>
    <select name="academic_year_id"><option value="">Year</option><option value="1" selected>AY</option></select><select name="term_id"><option value="">Term</option><option value="1" selected>Term</option></select>
    <select name="period"><option value="">Period</option><option value="PRELIM" selected>Prelim</option></select>
    <select name="faculty_id"><option value="">All</option></select><select name="department_id"><option value="">All</option></select>
    <input name="grade_min" type="number" min="0" max="100" value="97"><input name="grade_max" type="number" min="0" max="100" value="100">
    <select name="sort"><option value="range_count_desc">Count</option><option value="faculty_asc">Faculty</option></select><select name="status"><option value="all">All</option><option value="finalized">Finalized</option></select><button type="submit">Apply</button></form>
    <a id="gdm-clear" href="/monitor">Clear</a><div id="gdm-results" data-auto-load="${auto}" data-initial-url="/monitor?apply=1&amp;results=1"><button data-dataset-id="test" id="open" data-analytics-url="/detail?context=signed">View</button><nav class="pagination"><form class="gdm-page-form" action="?page=2&amp;context=signed"><button class="page-link" type="submit">Next</button></form></nav></div><div id="gdm-filter-status"></div>
    <div id="gradeDistributionAnalyticsModal"><div id="gdm-modal-body"></div></div>`, {url:'https://example.test/monitor', runScripts:'outside-only'});
  const w = dom.window, d = w.document, requests = [];
  w.fetch = async (url, init) => {requests.push({url:String(url), init}); return response(String(url).includes('options=1') ? options : empty);};
  w.eval(script); d.dispatchEvent(new w.Event('DOMContentLoaded'));
  return {dom,w,d,requests,
    submit:()=>d.getElementById('gradeDistributionFilterForm').dispatchEvent(new w.Event('submit',{cancelable:true})),
    change:name=>d.querySelector(`[name="${name}"]`).dispatchEvent(new w.Event('change',{bubbles:true})),
    click:selector=>d.querySelector(selector).dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),
    open:()=>{const e=new w.Event('show.bs.modal');e.relatedTarget=d.getElementById('open');d.getElementById('gradeDistributionAnalyticsModal').dispatchEvent(e);},
  };
}
const measure = {dataset_id:'offering:1/period:1/period_grade/status:all',gradebook_context:'Prelim',range_label:'Grades 97–100',range_state:'withheld',privacy_message:'Grade statistics withheld pending verified release history.',label:'Period Grades'};
const payload = {...measure, faculty_name:'Faculty', course_code:'ACC',course_title:'<img src=x onerror=alert(1)>',section:'A',campus:'FV',school_year:'AY',term:'1',status:'Unfinalized',
  measures:[{...measure,dataset_id:'exam',label:'Standing title',group:'examination'},{...measure,dataset_id:'standing',label:'Exam title',group:'standing'}],quiz:{...measure,label:'Quizzes',group:'standing'}};

test('bare shell makes no analytics request and initialization is idempotent',async()=>{
  const s=setup();await tick();assert.equal(s.requests.length,0);s.d.dispatchEvent(new s.w.Event('DOMContentLoaded'));s.submit();await tick();assert.equal(s.requests.length,1);s.dom.window.close();
});
test('bookmarked applied initial shell never fetches automatically',async()=>{
  const s=setup(true);await tick();assert.equal(s.requests.length,0);s.submit();await tick();assert.equal(s.requests[0].init.method,'POST');assert.equal(s.requests[0].init.headers['X-CSRFToken'],'test-csrf');assert.equal(s.requests[0].init.cache,'no-store');assert.equal(s.requests[0].init.credentials,'same-origin');assert.doesNotMatch(s.w.location.href,/csrf/);s.dom.window.close();
});

test('double Apply is deduplicated and progress is indeterminate',async()=>{
  const s=setup();let resolve;s.w.fetch=()=>{s.requests.push(1);return new Promise(r=>{resolve=r;});};s.submit();s.submit();assert.equal(s.requests.length,1);assert.equal(s.d.querySelector('[role="progressbar"]').hasAttribute('aria-valuenow'),false);resolve(response(empty));await tick();assert.equal(s.d.querySelector('button[type="submit"]').disabled,false);s.dom.window.close();
});
test('late result cannot restore data after a range edit even if fetch ignores abort',async()=>{
  const s=setup();let resolve;s.w.fetch=()=>new Promise(r=>{resolve=r;});s.submit();s.d.querySelector('[name="grade_min"]').dispatchEvent(new s.w.Event('input',{bubbles:true}));resolve(response({...empty,html:'SECRET OLD'}));await tick();assert.doesNotMatch(s.d.getElementById('gdm-results').textContent,/SECRET OLD/);s.dom.window.close();
});
test('out of order Apply responses cannot overwrite the newer result',async()=>{
  const s=setup();const pending=[];s.w.fetch=()=>new Promise(r=>pending.push(r));s.submit();s.d.querySelector('[name="grade_min"]').value='98';s.change('grade_min');s.submit();pending[1](response({...empty,html:'NEW'}));await tick();pending[0](response({...empty,html:'OLD'}));await tick();assert.equal(s.d.getElementById('gdm-results').textContent,'NEW');s.dom.window.close();
});
test('sort control changes invalidate results until explicit Apply',async()=>{
  const s=setup();s.w.history.replaceState(null,'','?page=2&context=old');s.d.querySelector('[name="sort"]').value='faculty_asc';s.change('sort');await tick();assert.equal(s.requests.length,0);assert.equal(s.d.getElementById('gdm-results').hidden,true);s.submit();await tick();assert.match(String(s.requests[0].init.body),/sort=faculty_asc/);assert.doesNotMatch(String(s.requests[0].init.body),/context=|page=|render=|csrf/);s.dom.window.close();
});

test('pagination uses its signed selection without navigating the page',async()=>{
  const s=setup();s.d.querySelector('.gdm-page-form').dispatchEvent(new s.w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.match(s.requests[0].url,/page=2/);assert.match(s.requests[0].url,/context=signed/);assert.doesNotMatch(s.requests[0].url,/render=/);s.dom.window.close();
});
test('Clear restores defaults and clears records without analytics',async()=>{
  const s=setup();s.d.querySelector('[name="grade_min"]').value='99';s.click('#gdm-clear');await tick();await tick();assert.equal(s.d.querySelector('[name="grade_min"]').value,'97');assert.equal(s.d.querySelector('[name="grade_max"]').value,'100');assert.equal(s.d.querySelector('[name="academic_year_id"]').value,'');assert.equal(s.requests.length,1);assert.match(s.requests[0].url,/options=1/);assert.equal(s.w.location.search,'');assert.match(s.d.getElementById('gdm-results').textContent,/Select criteria/);s.dom.window.close();
});
test('Back Forward restores controls without executing analytics',async()=>{
  const s=setup();s.w.history.pushState(null,'','?apply=1&academic_year_id=1&term_id=1&period=PRELIM&grade_min=98&grade_max=99&status=finalized');s.w.dispatchEvent(new s.w.PopStateEvent('popstate'));await tick();await tick();assert.equal(s.d.querySelector('[name="grade_min"]').value,'98');assert.equal(s.d.querySelector('[name="status"]').value,'finalized');assert.equal(s.requests.length,1);assert.match(s.requests[0].url,/options=1/);s.dom.window.close();
});
test('stale option responses cannot replace the current scope',async()=>{
  const s=setup(),pending=[];s.w.fetch=()=>new Promise(r=>pending.push(r));s.change('campus_id');s.d.querySelector('[name="campus_id"]').value='2';s.change('campus_id');pending[1](response({...options,faculty:[{id:12,label:'New scope'}]}));await tick();pending[0](response({...options,faculty:[{id:99,label:'Old scope'}]}));await tick();assert.match(s.d.querySelector('[name="faculty_id"]').textContent,/New scope/);assert.doesNotMatch(s.d.querySelector('[name="faculty_id"]').textContent,/Old scope/);s.dom.window.close();
});
for (const [label,make,pattern] of [
  ['validation',()=>response({errors:['Invalid Grade Range']},400),/Invalid Grade Range/],
  ['permission',()=>response({},403,'text/html'),/permission/],
  ['login redirect',()=>({...response({}),redirected:true}),/Sign in again/],
  ['unexpected HTML',()=>response({},500,'text/html'),/unexpected response/],
]) test(label+' errors clear busy state and provide explicit POST fallback',async()=>{
  const s=setup();s.w.fetch=async()=>make();s.submit();await tick();assert.match(s.d.getElementById('gdm-filter-status').textContent,pattern);assert.match(s.d.querySelector('#gdm-filter-status button').textContent,/Apply without JavaScript/);assert.equal(s.d.getElementById('gdm-results').hasAttribute('aria-busy'),false);s.dom.window.close();
});
test('network failure allows a retry',async()=>{
  const s=setup();s.w.fetch=async()=>{throw Error('Network disconnected');};s.submit();await tick();assert.match(s.d.getElementById('gdm-filter-status').textContent,/Network/);s.w.fetch=async()=>response(empty);s.submit();await tick();assert.equal(s.d.getElementById('gdm-filter-status').textContent,'');s.dom.window.close();
});
test('modal groups by structural type with independent accessible accordions and text-only labels',async()=>{
  const s=setup();s.w.fetch=async()=>response(payload);s.open();await tick();const buttons=[...s.d.querySelectorAll('.gdm-accordion-toggle')];assert.deepEqual(buttons.map(b=>b.firstChild.textContent),['PERIOD GRADES','EXAMINATION GRADES','CLASS STANDING GRADES']);assert.equal(buttons[0].getAttribute('aria-expanded'),'true');buttons[1].click();buttons[2].click();assert.equal(buttons[1].getAttribute('aria-expanded'),'true');assert.equal(buttons[2].getAttribute('aria-expanded'),'true');assert.equal(buttons[0].getAttribute('aria-expanded'),'true');for(const b of buttons){assert.equal(b.tagName,'BUTTON');assert.ok(s.d.getElementById(b.getAttribute('aria-controls')));assert.ok(b.querySelector('[aria-hidden="true"]'));}assert.match(s.d.getElementById(buttons[1].getAttribute('aria-controls')).textContent,/Standing title/);assert.match(s.d.getElementById(buttons[2].getAttribute('aria-controls')).textContent,/Exam title/);assert.equal(s.d.querySelectorAll('#gdm-modal-body img, #gdm-modal-body svg, #gdm-modal-body table').length,0);assert.doesNotMatch(s.d.getElementById('gdm-modal-body').textContent,/Data Notes/);s.dom.window.close();
});
test('late modal response after close is discarded and focus restored',async()=>{
  const s=setup();let resolve;s.w.fetch=()=>new Promise(r=>{resolve=r;});s.open();s.d.getElementById('gradeDistributionAnalyticsModal').dispatchEvent(new s.w.Event('hidden.bs.modal'));resolve(response(payload));await tick();assert.equal(s.d.getElementById('gdm-modal-body').textContent,'');assert.equal(s.d.activeElement.id,'open');s.dom.window.close();
});
test('actual Django internal payload renders authorized aggregate numbers without identities',async()=>{
  assert.ok(process.env.TMP_UX_DOM_PAYLOAD,'Run with the current Django-generated payload');
  const data=JSON.parse(readFileSync(process.env.TMP_UX_DOM_PAYLOAD,'utf8'));
  for(const m of [data,...data.measures,data.quiz]){
    assert.equal(m.privacy_suppressed,false);assert.equal('student_id' in m,false);
    if(m.graded_count){assert.notEqual(m.range_count,null);assert.notEqual(m.average,null);assert.ok(m.frequency.length);}
    else {assert.equal(m.range_state,'unavailable');assert.equal(m.range_count,null);}
  }
  const s=setup();s.w.fetch=async()=>response(data);s.open();await tick();
  assert.equal(s.d.querySelectorAll('.gdm-accordion').length,3);assert.ok(s.d.querySelectorAll('#gdm-modal-body table').length);
  assert.doesNotMatch(s.d.getElementById('gdm-modal-body').textContent,/Withheld|approved.*release/);s.dom.window.close();
});

test('restored bfcache form cannot trigger analytics or reuse modal/page actions',async()=>{
 const s=setup();const e=new s.w.Event('pageshow');Object.defineProperty(e,'persisted',{value:true});s.w.dispatchEvent(e);await tick();await tick();assert.equal(s.requests.length,1);assert.match(s.requests[0].url,/options=1/);s.open();s.d.querySelector('.gdm-page-form')?.dispatchEvent(new s.w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(s.requests.length,1);s.dom.window.close();
});
test('actual Django numeric period and unavailable child sources retain their own states',async()=>{
 assert.ok(process.env.TMP_UX_DOM_PAYLOAD);const data=JSON.parse(readFileSync(process.env.TMP_UX_DOM_PAYLOAD+'.released','utf8'));
 assert.equal(data.range_count,8);assert.equal(data.range_denominator,20);const s=setup();s.w.fetch=async()=>response(data);s.open();await tick();assert.match(s.d.getElementById('gdm-modal-body').textContent,/8 \/ 20 \(40.00%\)/);for(const group of ['examination','standing']){const b=s.d.querySelector('.gdm-'+group);const content=s.d.getElementById(b.getAttribute('aria-controls')).textContent;assert.doesNotMatch(content,/8 \/ 20/);assert.match(content,/Unavailable/);}s.dom.window.close();
});

test('small-class zero is numeric while absent sources are Unavailable',async()=>{
 const data={...payload,range_state:'disclosed',range_count:0,range_denominator:1,range_percentage:'0.00',average:'0.00',minimum:'0.00',maximum:'0.00',frequency:[{grade:'0.00',count:1,percentage:'100.00'}],
 measures:payload.measures.map(m=>({...m,range_state:'unavailable',source_notice:'No eligible saved values are available for this measure.'})),quiz:null};
 const s=setup();s.w.fetch=async()=>response(data);s.open();await tick();
 const body=s.d.getElementById('gdm-modal-body');assert.match(body.textContent,/0 \/ 1 \(0.00%\)/);assert.match(body.textContent,/Average: 0.00/);assert.match(body.textContent,/Unavailable/);
 assert.equal(body.querySelectorAll('table').length,1);assert.equal(body.querySelector('th').scope,'col');assert.equal(body.querySelector('tbody td').textContent,'0.00');s.dom.window.close();
});
