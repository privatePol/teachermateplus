const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require(process.env.TMP_JSDOM_PATH || 'jsdom');
const source = name => readFileSync(join(__dirname, '../../static/admin_portal/', name+'.js'),'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function ajax() {
  const dom = new JSDOM('<div id="dashboard-reopen" data-refresh-url="/fragment/?scope_token=signed"><div data-reopen-content>Initial server card</div><p data-reopen-status></p></div><div id="other">Unchanged</div><form class="admin-scope-form"><select><option>Scope</option></select></form>', {url:'https://test.local',runScripts:'outside-only'});
  const w=dom.window, timers=new Map(); let next=0, hidden=false, calls=[];
  Object.defineProperty(w.document,'hidden',{get:()=>hidden});
  w.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;};
  w.clearTimeout=id=>timers.delete(id);
  w.AbortController=AbortController;
  let resolve, reject;
  w.fetch=(url, options)=>{calls.push({url,options});return new Promise((a,b)=>{resolve=a;reject=b;options.signal.addEventListener('abort',()=>b(new Error('aborted')));});};
  w.eval(source('dashboard'));
  return {dom,w,timers,calls, timer:()=>[...timers.values()].find(t=>t.delay===600000), resolve:(status=200,html='<strong>Updated requests</strong>')=>resolve({status,ok:status===200,headers:{get:()=> 'text/html; charset=utf-8'},text:async()=>html}), reject:()=>reject(new Error('offline')), visibility(value){hidden=value;w.document.dispatchEvent(new w.Event('visibilitychange'));}, content:()=>w.document.querySelector('[data-reopen-content]').innerHTML};
}
test('initial server content remains and only 600-second refresh is scheduled',()=>{const s=ajax();assert.equal(s.content(),'Initial server card');assert.equal(s.calls.length,0);assert.equal(s.timers.size,1);assert.equal(s.timer().delay,600000);s.dom.window.close();});
test('refresh replaces only card and prevents overlapping requests',async()=>{const s=ajax(), tick=s.timer();tick.fn();tick.fn();assert.equal(s.calls.length,1);assert.equal(s.calls[0].options.redirect,'error');assert.equal(s.calls[0].options.credentials,'same-origin');s.resolve();await flush();assert.match(s.content(),/Updated requests/);assert.equal(s.w.document.querySelector('#other').textContent,'Unchanged');assert.ok(s.timer());s.dom.window.close();});
test('hidden tab aborts and pauses, visible tab schedules the next 10-minute check',async()=>{const s=ajax();s.timer().fn();s.visibility(true);await flush();assert.equal(s.calls[0].options.signal.aborted,true);assert.equal(s.timer(),undefined);s.visibility(false);assert.equal(s.calls.length,2);s.resolve();await flush();assert.equal(s.timer().delay,600000);s.dom.window.close();});
for(const status of [401,403,409]) test(`authorization/scope status ${status} clears actions and stops polling`,async()=>{const s=ajax();s.timer().fn();s.resolve(status);await flush();assert.equal(s.content(),'');assert.equal(s.timer(),undefined);assert.match(s.w.document.querySelector('[data-reopen-status]').textContent,/Reload/);s.dom.window.close();});
test('offline or expired-login redirect cannot leave stale actions or inject a login page',async()=>{const s=ajax();s.timer().fn();s.reject();await flush();assert.equal(s.content(),'');assert.match(s.w.document.querySelector('[data-reopen-status]').textContent,/sign-in/);s.dom.window.close();});
test('local academic selector change aborts, clears card and stops polling',async()=>{const s=ajax();s.timer().fn();s.w.document.querySelector('select').dispatchEvent(new s.w.Event('change'));await flush();assert.equal(s.content(),'');assert.equal(s.calls[0].options.signal.aborted,true);assert.equal(s.timer(),undefined);s.dom.window.close();});
function drawer(mobile=true) {
  const dom = new JSDOM('<div class="admin-shell sidebar-collapsed"><aside id="admin-sidebar"><button class="admin-drawer-close">Close</button><a id="last" href="/">Dashboard</a></aside><button class="admin-drawer-backdrop" hidden></button><main class="admin-main"><button id="admin-mobile-nav">Menu</button></main></div>',{runScripts:'outside-only'});
  const w=dom.window, query={matches:mobile,addEventListener(type,fn){this.change=fn;}};w.matchMedia=()=>query;
  w.HTMLElement.prototype.getClientRects=()=>[1];w.eval(source('sidebar'));
  return {dom,w,query,q:selector=>w.document.querySelector(selector), key(key,shiftKey=false){w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key,shiftKey,cancelable:true}));}};
}
test('mobile opens modal drawer, focuses Close and restores focus/scroll on Escape',()=>{const s=drawer();const opener=s.q('#admin-mobile-nav');opener.focus();opener.click();assert.equal(s.q('.admin-main').inert,true);assert.equal(s.w.document.body.style.overflow,'hidden');assert.equal(s.w.document.activeElement,s.q('.admin-drawer-close'));s.key('Escape');assert.equal(s.w.document.activeElement,opener);assert.equal(s.q('.admin-main').inert,false);assert.equal(s.w.document.body.style.overflow,'');assert.equal(s.q('#admin-sidebar').inert,true);s.dom.window.close();});
test('Tab containment and close/backdrop controls work',()=>{const s=drawer();for(const control of ['.admin-drawer-close','.admin-drawer-backdrop']) {s.q('#admin-mobile-nav').click();s.q('#last').focus();s.key('Tab');assert.equal(s.w.document.activeElement,s.q('.admin-drawer-close'));s.key('Tab',true);assert.equal(s.w.document.activeElement,s.q('#last'));s.q(control).click();assert.equal(s.q('.admin-drawer-backdrop').hidden,true);}s.dom.window.close();});
test('resize clears drawer lock and keeps desktop preference',()=>{const s=drawer();s.q('#admin-mobile-nav').click();s.query.matches=false;s.query.change();assert.equal(s.q('#admin-sidebar').inert,false);assert.equal(s.q('.admin-main').inert,false);assert.equal(s.w.document.body.style.overflow,'');assert.equal(s.q('.admin-shell').classList.contains('sidebar-collapsed'),true);s.dom.window.close();});
test('desktop sidebar remains usable',()=>{const s=drawer(false);assert.equal(s.q('#admin-sidebar').inert,false);s.q('#admin-mobile-nav').click();assert.equal(s.q('.admin-main').inert,false);s.dom.window.close();});

function queues(names=['correction','cml'], withReopen=false, broadcast=false) {
  const sections=names.map(name=>`<section data-queue="${name}"><a data-queue-link href="/admin-portal/${name}/">Action</a><p data-queue-count>1 pending</p><p data-queue-config hidden>Configure</p></section>`).join('');
  const html=`<div id="dashboard-queue-counts" data-scope-token="signed" data-refresh-url="/queue/?scope_token=signed"><div data-queue-content>${sections}</div><p data-queue-status></p></div>`+(withReopen?'<div id="dashboard-reopen" data-refresh-url="/reopen/"><div data-reopen-content>Initial reopen</div><p data-reopen-status></p></div>':'')+'<form class="admin-scope-form"><select></select></form>';
  const dom=new JSDOM(html,{url:'https://test.local',runScripts:'outside-only'}), w=dom.window;
  const timers=new Map(), calls=[];let next=0,hidden=false;
  Object.defineProperty(w.document,'hidden',{get:()=>hidden});
  w.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;};
  w.clearTimeout=id=>timers.delete(id); w.AbortController=AbortController;
  // Deliberately ignore abort: late network/body completions must remain harmless.
  w.fetch=(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}));
  let channel;
  if(broadcast)w.BroadcastChannel=class {constructor(){channel=this;} close(){this.closed=true;}};
  w.eval(source('dashboard'));
  const data={scope_token:'signed',correction:{pending_count:2,configured:true,url:'/admin-portal/corrections/?academic_scope=active&status=PENDING'},cml:{pending_count:3,configured:true,url:'/admin-portal/cml/?academic_scope=active&status=PENDING'}};
  return {dom,w,timers,calls,data,get channel(){return channel;},q:selector=>w.document.querySelector(selector),
    tick(){[...timers.entries()].filter(([,t])=>t.delay===600000).forEach(([id,t])=>{timers.delete(id);t.fn();});},
    respond(index=0,payload=data,status=200){calls[index].resolve({status,ok:status===200,headers:{get:()=>calls[index].url.startsWith('/queue')?'application/json':'text/html'},json:async()=>payload,text:async()=>'<strong>Fresh reopen</strong>'});},
    visibility(value){hidden=value;w.document.dispatchEvent(new w.Event('visibilitychange'));},
    close(){dom.window.close();}};
}
for(const names of [['correction','cml'],['correction'],['cml']]) test(`queue refresh supports ${names.join('+')} without reopen`,async()=>{
  const s=queues(names);assert.equal(s.calls.length,0);assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,1);
  s.tick();s.tick();assert.equal(s.calls.length,1);assert.equal(s.calls[0].options.redirect,'error');assert.equal(s.calls[0].options.cache,'no-store');s.respond();await flush();
  for(const name of names) {assert.equal(s.q(`[data-queue="${name}"] [data-queue-count]`).textContent,`${s.data[name].pending_count} pending`);assert.match(s.q(`[data-queue="${name}"] a`).href,/status=PENDING/);}
  assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,1);s.close();
});
test('mixed dashboard preserves separate reopen and one combined queue 600-second refresh',async()=>{
  const s=queues(undefined,true);assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,2);s.tick();assert.equal(s.calls.length,2);s.respond(0);s.respond(1);await flush();assert.match(s.q('[data-reopen-content]').innerHTML,/Fresh reopen/);assert.equal(s.q('[data-queue="cml"] [data-queue-count]').textContent,'3 pending');s.close();
});
for(const status of [401,403,409]) test(`queue authorization/scope ${status} removes counts/actions and stops`,async()=>{
  const s=queues();s.tick();s.respond(0,{},status);await flush();assert.equal(s.q('[data-queue-content]').children.length,0);assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,0);s.close();
});
test('permission revocation removes only the unauthorized queue',async()=>{
  const s=queues();s.tick();s.respond(0,{...s.data,correction:null});await flush();assert.equal(s.q('[data-queue="correction"]'),null);assert.equal(s.q('[data-queue="cml"] [data-queue-count]').textContent,'3 pending');s.close();
});
test('missing configuration is unavailable and never rendered as zero',async()=>{
  const s=queues();s.tick();s.respond(0,{...s.data,cml:{...s.data.cml,pending_count:null,configured:false}});await flush();assert.equal(s.q('[data-queue="cml"] [data-queue-count]').textContent,'Unavailable');assert.equal(s.q('[data-queue="cml"] [data-queue-config]').hidden,false);s.close();
});
for(const event of ['local','storage','clear-storage','pageshow']) test(`${event} scope invalidation rejects late queue and reopen responses`,async()=>{
  const s=queues(undefined,true);s.tick();
  if(event==='local')s.q('select').dispatchEvent(new s.w.Event('change'));
  if(event==='storage')s.w.dispatchEvent(new s.w.StorageEvent('storage',{key:'TeacherMate+_admin_scope',newValue:'other'}));
  if(event==='clear-storage')s.w.dispatchEvent(new s.w.StorageEvent('storage',{key:null}));
  if(event==='pageshow')s.w.dispatchEvent(new s.w.PageTransitionEvent('pageshow',{persisted:true}));
  assert.ok(s.calls.every(c=>c.options.signal.aborted));s.respond(0);s.respond(1);await flush();assert.equal(s.q('[data-queue-content]').children.length,0);assert.equal(s.q('[data-reopen-content]').innerHTML,'');assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,0);s.close();
});
test('scope changes while JSON body is awaited cannot restore stale actions',async()=>{
  const s=queues();s.tick();let body;
  s.calls[0].resolve({status:200,ok:true,headers:{get:()=> 'application/json'},json:()=>new Promise(resolve=>body=resolve)});await flush();
  s.w.localStorage.setItem('TeacherMate+_admin_scope','changed');body(s.data);await flush();assert.equal(s.q('[data-queue-content]').children.length,0);s.close();
});
test('hidden tabs pause; late responses are ignored; visible tab verifies before restoring',async()=>{
  const s=queues();s.tick();s.visibility(true);s.respond();await flush();assert.equal([...s.timers.values()].filter(t=>t.delay===600000).length,0);
  s.visibility(false);assert.equal(s.q('[data-queue-content]').children.length,0);assert.equal(s.calls.length,2);s.respond(1);await flush();assert.equal(s.q('[data-queue="cml"] [data-queue-count]').textContent,'3 pending');s.close();
});
test('timeout clears stale actions and late response cannot restore them',async()=>{
  const s=queues();s.tick();[...s.timers.values()].find(t=>t.delay===20000).fn();assert.ok(s.calls[0].options.signal.aborted);assert.equal(s.q('[data-queue-content]').children.length,0);s.respond();await flush();assert.equal(s.q('[data-queue-content]').children.length,0);s.close();
});
test('network failure clears actions; next verified response can restore authorized sections',async()=>{
  const s=queues();s.tick();s.calls[0].reject(new Error('offline'));await flush();assert.equal(s.q('[data-queue-content]').children.length,0);s.tick();s.respond(1);await flush();assert.ok(s.q('[data-queue="correction"] a'));s.close();
});
test('signed scope mismatch or malformed payload cannot leave actions',async()=>{
  for(const payload of [{scope_token:'wrong'}, {scope_token:'signed',correction:{pending_count:-1}}, {scope_token:'signed',correction:{pending_count:0,configured:true,url:'https://evil.test'}}]) {
    const s=queues();s.tick();s.respond(0,payload);await flush();assert.equal(s.q('[data-queue-content]').children.length,0);s.close();
  }
});

test('broadcast scope changes invalidate without localStorage events',async()=>{
  const s=queues(undefined,true,true);s.tick();s.channel.onmessage({data:{changed:true}});s.respond(0);s.respond(1);await flush();assert.equal(s.q('[data-queue-content]').children.length,0);assert.equal(s.q('[data-reopen-content]').innerHTML,'');s.close();
});
test('window focus verifies a still-visible dashboard before restoring counts',async()=>{
  const s=queues();s.w.dispatchEvent(new s.w.Event('focus'));assert.equal(s.q('[data-queue-content]').children.length,0);assert.equal(s.calls.length,1);s.respond();await flush();assert.equal(s.q('[data-queue="cml"] [data-queue-count]').textContent,'3 pending');s.close();
});
