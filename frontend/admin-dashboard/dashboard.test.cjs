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
test('hidden tab aborts and pauses, visible tab schedules the next 10-minute check',async()=>{const s=ajax();s.timer().fn();s.visibility(true);await flush();assert.equal(s.calls[0].options.signal.aborted,true);assert.equal(s.timer(),undefined);s.visibility(false);assert.equal(s.timer().delay,600000);s.dom.window.close();});
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
