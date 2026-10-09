import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {JSDOM} from "jsdom";
const script = readFileSync(new URL("../../static/faculty_attendance/dtr_review.js", import.meta.url),"utf8");
const adminScript = readFileSync(new URL("../../static/faculty_attendance/dtr_admin_hours.js", import.meta.url),"utf8");
const route = "/admin-portal/faculty-attendance/dtr/";
const reply = (payload, status=200, type="application/json") => ({status,ok:status<400,headers:{get:()=>type},json:async()=>payload});
const turn = () => new Promise(resolve=>setTimeout(resolve,0));
async function settle(){await turn();await turn();}
const form = action => `<form method="post" action="${route}" data-dtr-ajax="${action}"><input name="action" type="hidden" value="${action}"><input name="faculty" type="hidden" value="4"><input name="hours" value=""><button type="submit">Save</button></form>`;
const workspace = id => `<form data-dtr-selection><select id="cutoff-select" data-dtr-cutoff-select><option value="first">First</option><option value="second">Second</option></select><select id="faculty-select" data-dtr-faculty-select><option value="4" ${id===4?"selected":""}>Garcia, Juan</option><option value="7" ${id===7?"selected":""}>Lagman, Laarni Grace C.</option></select><button>Retry</button></form><table><tbody><tr id="dtr-summary-faculty-4"><td><button data-dtr-faculty-select data-faculty-id="4">Garcia, Juan</button></td></tr><tr id="dtr-summary-faculty-7"><td><button data-dtr-faculty-select data-faculty-id="7">Lagman, Laarni Grace C.</button></td></tr></tbody></table><div id="faculty-dtr-detail"><section id="faculty-dtr-card"><h2 id="faculty-dtr-title" tabindex="-1">Faculty ${id}</h2><table data-dtr-detail-table></table><a data-dtr-navigation href="${route}?cutoff=first&faculty=${id}&view=current">Current review</a><form method="get" data-dtr-version-form><input name="cutoff" value="first"><input name="faculty" value="${id}"><select id="dtr-version-select" name="version" data-dtr-version-select><option value="2">R2</option><option value="1">R1</option></select></form>${["adjustment","admin_hours","remove_adjustment","finalize"].map(form).join("")}</section></div>`;
const payload = (id=7) => ({ok:true,message:"Loaded",faculty_id:id,cutoff:"first",view:"saved",version:2,workspace_html:workspace(id)});
function page(fetchImpl,{reduced=false,beforeScript=()=>{}}={}) {
 const dom=new JSDOM(`<div data-dtr-review data-dtr-url="${route}"><div id="dtr-live"></div><div id="dtr-inline-error" class="d-none"></div><dialog id="dtr-loading-modal" hidden><h2>Loading DTR?</h2><progress></progress><button data-dtr-cancel-load>Cancel</button></dialog><div id="dtr-workspace">${workspace(4)}</div></div>`,{url:`http://localhost${route}?cutoff=first&faculty=4&version=2`,runScripts:"outside-only"});
 const w=dom.window;w.fetch=fetchImpl;w.matchMedia=()=>({matches:reduced});w.confirm=()=>false;
 w.HTMLElement.prototype.scrollIntoView=function(options){w.lastScroll={id:this.id,...options};};
 const modal=w.document.getElementById("dtr-loading-modal");modal.showModal=function(){this.open=true;};modal.close=function(){this.open=false;};
 beforeScript(w.document);w.eval(script);return {dom,w,doc:w.document,close:()=>w.close(),modal};
}
const choose = (state,id) => {const el=state.doc.getElementById("faculty-select");el.value=String(id);el.dispatchEvent(new state.w.Event("change",{bubbles:true}));};
const submit = (state,action) => state.doc.querySelector(`form[data-dtr-ajax='${action}']`).dispatchEvent(new state.w.Event("submit",{bubbles:true,cancelable:true}));

test("dropdown automatically shows indeterminate modal, inserts detail, closes and focuses heading",async()=>{
 let resolve;const state=page(()=>new Promise(r=>resolve=r));choose(state,7);
 assert.equal(state.modal.hidden,false);assert.equal(state.modal.open,true);assert.equal(state.doc.querySelector("progress").hasAttribute("value"),false);
 assert.equal(state.doc.getElementById("faculty-select").disabled,false);
 resolve(reply(payload()));await settle();
 assert.equal(state.modal.hidden,true);assert.equal(state.doc.activeElement.id,"faculty-dtr-title");assert.equal(state.w.lastScroll.behavior,"smooth");
 assert.equal(state.doc.querySelectorAll("[data-dtr-detail-table]").length,1);assert.match(state.w.location.search,/faculty=7/);state.close();
});
test("summary name uses same loader and selection request without page navigation",async()=>{
 const calls=[];const state=page((url)=>{calls.push(url);return Promise.resolve(reply(payload()));});
 state.doc.querySelector("[data-faculty-id='7']").click();await settle();
 assert.equal(calls.length,1);assert.match(calls[0],/faculty=7/);assert.equal(state.doc.getElementById("faculty-select").value,"7");state.close();
});
test("rapid A to B aborts the first request and ignores a late response and its cleanup",async()=>{
 const pending=[];const state=page((url,options)=>new Promise(resolve=>pending.push({resolve,options})));
 choose(state,4);choose(state,7);assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve(reply(payload(4)));await settle();assert.equal(state.modal.hidden,false);
 pending[1].resolve(reply(payload(7)));await settle();assert.equal(state.doc.getElementById("faculty-dtr-title").textContent,"Faculty 7");assert.match(state.w.location.search,/faculty=7/);state.close();
});
test("non-JSON loading failure restores selection, closes loader and retry succeeds",async()=>{
 let fails=true;const state=page(()=>Promise.resolve(fails?reply({},502,"text/html"):reply(payload())));
 choose(state,7);await settle();assert.equal(state.modal.hidden,true);assert.equal(state.doc.getElementById("faculty-select").value,"4");assert.equal(state.doc.getElementById("faculty-dtr-title").textContent,"Faculty 4");assert.match(state.doc.getElementById("dtr-inline-error").textContent,/Retry/);
 fails=false;choose(state,7);await settle();assert.equal(state.doc.getElementById("faculty-select").value,"7");state.close();
});
test("network failure and explicit cancellation retain old identity and allow retry",async()=>{
 let fail=true,resolve;const state=page(()=>fail?Promise.reject(new Error("Offline; retry selection")):new Promise(r=>resolve=r));
 choose(state,7);await settle();assert.equal(state.modal.hidden,true);assert.equal(state.doc.getElementById("faculty-select").value,"4");
 fail=false;choose(state,7);state.doc.querySelector("[data-dtr-cancel-load]").click();resolve(reply(payload()));await settle();assert.equal(state.doc.getElementById("faculty-select").value,"4");assert.equal(state.modal.hidden,true);state.close();
});
test("reduced-motion preference scrolls without smooth animation",async()=>{
 const state=page(()=>Promise.resolve(reply(payload())),{reduced:true});choose(state,7);await settle();assert.equal(state.w.lastScroll.behavior,"auto");state.close();
});
test("unsaved form values prevent silent navigation; confirmed discard permits selection",async()=>{
 const calls=[];const state=page(url=>{calls.push(url);return Promise.resolve(reply(payload()));});state.doc.querySelector("[name='hours']").value="1.25";
 choose(state,7);await settle();assert.equal(calls.length,0);assert.equal(state.doc.getElementById("faculty-select").value,"4");assert.equal(state.doc.querySelector("[name='hours']").value,"1.25");
 state.w.confirm=()=>true;choose(state,7);await settle();assert.equal(calls.length,1);state.close();
});
test("delegated handlers survive replacement and repeated script execution without duplicates",async()=>{
 const calls=[];const state=page(url=>{calls.push(url);return Promise.resolve(reply(payload(new URL(url,"http://localhost").searchParams.get("faculty")==="4"?4:7)));});
 choose(state,7);await settle();state.w.eval(script);choose(state,4);await settle();assert.equal(calls.length,2);state.close();
});
for(const action of ["adjustment","admin_hours","remove_adjustment","finalize"]) test(`AJAX ${action} posts literal route and still works after faculty replacement`,async()=>{
 const calls=[];const state=page((url,options)=>{calls.push({url,options});return Promise.resolve(options.method==="POST"?reply({ok:true,faculty_id:options.body.get("faculty"),cutoff:"first",view:"current",faculty_html:workspace(4),summary_row_html:'<tr id="dtr-summary-faculty-4"><td>1 Garcia</td></tr>'}):reply(payload(4)));});
 choose(state,4);await settle();submit(state,action);await settle();assert.equal(calls.length,2);assert.equal(calls[1].url,route);assert.equal(calls[1].options.method,"POST");assert.equal(calls[1].options.body.get("action"),action);assert.match(state.w.location.search,/view=current/);assert.equal(state.w.location.search.includes("version="),false);state.close();
});
test("cutoff resets faculty/version and returned canonical cutoff updates URL",async()=>{
 let called;const state=page(url=>{called=url;return Promise.resolve(reply({...payload(),cutoff:"second"}));});const cutoff=state.doc.getElementById("cutoff-select");cutoff.value="second";cutoff.dispatchEvent(new state.w.Event("change",{bubbles:true}));await settle();
 const query=new URL(called,"http://localhost").searchParams;assert.equal(query.has("faculty"),false);assert.equal(query.has("version"),false);assert.equal(query.get("reset_faculty"),"1");assert.match(state.w.location.search,/cutoff=second/);state.close();
});
test("previous version selector and current-review links use AJAX and preserve their requested state",async()=>{
 const calls=[];const state=page(url=>{calls.push(url);const q=new URL(url,"http://localhost").searchParams;return Promise.resolve(reply({...payload(4),view:q.get("view")||"saved",version:q.get("version")||2}));});
 const version=state.doc.getElementById("dtr-version-select");version.value="1";version.dispatchEvent(new state.w.Event("change",{bubbles:true}));await settle();assert.match(calls[0],/version=1/);assert.match(state.w.location.search,/version=1/);
 state.doc.querySelector("[data-dtr-navigation]").click();await settle();assert.match(state.w.location.search,/view=current/);state.close();
});
test("loading and duplicate saves are protected; unsaved other forms block replacement",async()=>{
 const pending=[];const state=page((url,options)=>new Promise(resolve=>pending.push({resolve,options})));
 choose(state,4);submit(state,"adjustment");assert.equal(pending.length,1);pending[0].resolve(reply(payload(4)));await settle();
 state.doc.querySelector("form[data-dtr-ajax='admin_hours'] [name='hours']").value="2.00";submit(state,"adjustment");assert.equal(pending.length,1);assert.match(state.doc.getElementById("dtr-inline-error").textContent,/other unsaved/);
 state.doc.querySelector("form[data-dtr-ajax='admin_hours'] [name='hours']").value="";submit(state,"adjustment");submit(state,"adjustment");assert.equal(pending.length,2);pending[1].resolve(reply({ok:true,faculty_id:4,cutoff:"first",view:"current",faculty_html:workspace(4)}));await settle();state.close();
});
test("unconfirmed POST leaves entered values intact and duplicate-save controls recover",async()=>{
 const state=page(()=>Promise.resolve(reply({},502,"text/html")));state.doc.querySelector("[name='hours']").value="1.50";submit(state,"adjustment");await settle();assert.equal(state.doc.querySelector("[name='hours']").value,"1.50");assert.match(state.doc.getElementById("dtr-inline-error").textContent,/No record was confirmed saved/);assert.equal(state.doc.querySelector("button[type='submit']").disabled,false);state.close();
});

test("loading timeout closes modal even without AbortController support",async()=>{
 const state=page(()=>new Promise(()=>{}));state.w.AbortController=undefined;
 const timer=state.w.setTimeout.bind(state.w);state.w.setTimeout=(fn,ms)=>timer(fn,ms===30000?0:ms);
 choose(state,7);await settle();assert.equal(state.modal.hidden,true);assert.equal(state.doc.getElementById("faculty-select").value,"4");assert.match(state.doc.getElementById("dtr-inline-error").textContent,/timed out/);state.close();
});
test("mismatched faculty response cannot rename or replace the previous detail",async()=>{
 const state=page(()=>Promise.resolve(reply(payload(4))));choose(state,7);await settle();assert.equal(state.doc.getElementById("faculty-dtr-title").textContent,"Faculty 4");assert.equal(state.doc.getElementById("faculty-select").value,"4");assert.match(state.doc.getElementById("dtr-inline-error").textContent,/confirmed/);state.close();
});
test("server validation keeps bound values and guards them from unsaved navigation",async()=>{
 let calls=0;const state=page(()=>{calls++;return Promise.resolve(reply({ok:false,faculty_id:4,cutoff:"first",view:"current",faculty_html:workspace(4).replace('name="hours" value=""','name="hours" value="-1"')},400));});
 submit(state,"adjustment");await settle();assert.equal(state.doc.querySelector("[name='hours']").value,"-1");choose(state,7);await settle();assert.equal(calls,1);assert.equal(state.doc.querySelector("[name='hours']").value,"-1");state.close();
});

function postLockFixture(doc) {
 const adjustment=doc.querySelector("form[data-dtr-ajax='adjustment']");
 adjustment.insertAdjacentHTML("beforeend",'<input type="hidden" name="csrfmiddlewaretoken" value="synthetic-csrf"><input name="entry_date" value="2026-01-05"><select name="kind"><option value="OTHER">Other</option></select><textarea name="reason">Submitted note</textarea><input name="originally_disabled" value="locked" disabled>');
 adjustment.querySelector("button").name="submit_intent";adjustment.querySelector("button").value="save_entry";
 const admin=doc.querySelector("form[data-dtr-ajax='admin_hours']");admin.setAttribute("data-admin-hours","");
 admin.innerHTML=`<input name="action" value="admin_hours" type="hidden"><input name="faculty" value="4" type="hidden"><input name="admin_hours-TOTAL_FORMS" value="1" type="hidden"><div data-admin-hours-rows><div data-admin-hours-row><input name="admin_hours-0-entry_date" value="2026-01-05"><input name="admin_hours-0-hours" value="2.00"><input name="admin_hours-0-DELETE" type="checkbox"><button type="button" data-admin-hours-remove>Remove</button></div></div><template data-admin-hours-template><div data-admin-hours-row><input name="admin_hours-__prefix__-entry_date"><input name="admin_hours-__prefix__-hours"><input name="admin_hours-__prefix__-DELETE" type="checkbox"><button type="button" data-admin-hours-remove>Remove</button></div></template><button type="button" data-admin-hours-add>Add</button><strong data-admin-hours-total>2.00</strong><p data-admin-hours-message></p><button type="submit">Save admin</button>`;
 doc.getElementById("cutoff-select").disabled=true;
}
for(const outcome of ["success","validation","network"]) test(`pending POST locks all entry mutations and restores ${outcome} completion`,async()=>{
 let release,reject;const calls=[];
 const state=page((url,options)=>{calls.push({url,options});return new Promise((resolve,fail)=>{release=resolve;reject=fail;});},{beforeScript:postLockFixture});
 state.w.eval(adminScript);
 try {
  const doc=state.doc,detail=doc.getElementById("faculty-dtr-detail");
  const adjustment=doc.querySelector("form[data-dtr-ajax='adjustment']");
  adjustment.querySelector("[name='hours']").value="1.50";
  const html=detail.innerHTML.replace('name="hours" value=""','name="hours" value="1.50"');
  const expected=[...new state.w.FormData(adjustment,adjustment.querySelector("button")).entries()];
  adjustment.dispatchEvent(new state.w.SubmitEvent("submit",{bubbles:true,cancelable:true,submitter:adjustment.querySelector("button")}));
  assert.equal(calls.length,1);
  assert.deepEqual([...calls[0].options.body.entries()],expected,"payload captured before disabling, including CSRF/action/submitter");
  assert.equal(calls[0].options.body.get("csrfmiddlewaretoken"),"synthetic-csrf");
  assert.equal(calls[0].options.body.get("action"),"adjustment");
  assert.equal(calls[0].options.body.get("hours"),"1.50");
  assert.equal(detail.inert,true);
  for(const field of detail.querySelectorAll("input,select,textarea,button")) assert.equal(field.disabled,true);
  const admin=doc.querySelector("[data-admin-hours]");
  admin.querySelector("[name$='-hours']").focus();assert.notEqual(doc.activeElement,admin.querySelector("[name$='-hours']"));
  for(const button of admin.querySelectorAll("[data-admin-hours-add],[data-admin-hours-remove]")) {
   button.click();button.dispatchEvent(new state.w.MouseEvent("click",{bubbles:true,cancelable:true}));
  }
  assert.equal(admin.querySelectorAll("[data-admin-hours-row]").length,1);
  assert.equal(admin.querySelector("[name$='-DELETE']").checked,false);
  assert.equal(admin.querySelector("[name$='-hours']").value,"2.00");
  assert.equal(admin.dispatchEvent(new state.w.Event("submit",{bubbles:true,cancelable:true})),false);
  assert.equal(calls.length,1,"pending save prevents a second handler/request");
  if(outcome==="network") reject(new Error("Offline"));
  else {
   release(reply({ok:outcome==="success",message:outcome==="success"?"Saved":"Correct fields",faculty_id:4,cutoff:"first",view:"current",faculty_html:html},outcome==="success"?200:400));
  }
  await settle();
  assert.equal(detail.inert,false);assert.equal(detail.hasAttribute("aria-busy"),false);
  assert.equal(doc.getElementById("faculty-select").disabled,false);
  assert.equal(doc.getElementById("cutoff-select").disabled,true,"pre-existing disabled selector preserved");
  assert.equal(doc.querySelector("[name='originally_disabled']").disabled,true);
  if(outcome!=="network") assert.notEqual(doc.querySelector("form[data-dtr-ajax='adjustment']"),adjustment);
  if(outcome!=="network") assert.equal(adjustment.querySelector("[name='hours']").disabled,true,"detached nodes are not restored into a new request");
  assert.equal(doc.querySelector("form[data-dtr-ajax='adjustment'] [name='hours']").disabled,false);
  if(outcome==="network") {
   assert.equal(adjustment.querySelector("[name='hours']").disabled,false);
   assert.equal(adjustment.querySelector("[name='hours']").value,"1.50");
   assert.match(doc.getElementById("dtr-inline-error").textContent,/outcome is unconfirmed/);
  }
  state.w.eval(script);
  const restoredAdmin=doc.querySelector("[data-admin-hours]");
  restoredAdmin.querySelector("[data-admin-hours-add]").click();
  assert.equal(restoredAdmin.querySelectorAll("[data-admin-hours-row]").length,2,"one delegated add handler after restoration");
  restoredAdmin.querySelector("[data-admin-hours-remove]").click();
  assert.equal(restoredAdmin.querySelector("[name$='-DELETE']").checked,true);
  await settle();
 } finally {state.close();}
});

test("POST cleanup preserves server-disabled replacements and a subsequent request's lock",async()=>{
 const pending=[];const state=page((_url,options)=>new Promise(resolve=>pending.push({resolve,options})));
 try {
  const original=state.doc.querySelector("form[data-dtr-ajax='adjustment']");
  submit(state,"adjustment");
  pending[0].resolve(reply({ok:true,faculty_id:4,cutoff:"first",view:"current",faculty_html:workspace(4).replace('name="hours" value=""','name="hours" value="" disabled')}));
  await settle();
  const replacement=state.doc.querySelector("form[data-dtr-ajax='adjustment']");
  assert.notEqual(replacement,original);
  assert.equal(replacement.querySelector("[name='hours']").disabled,true,"server-disabled control is not enabled by old cleanup");
  assert.equal(replacement.querySelector("button").disabled,false);
  submit(state,"admin_hours");await settle();
  assert.equal(pending.length,2);assert.equal(state.doc.getElementById("faculty-dtr-detail").inert,true);
  for(const field of state.doc.querySelectorAll("#faculty-dtr-detail input, #faculty-dtr-detail button")) assert.equal(field.disabled,true);
  pending[1].resolve(reply({},502,"text/html"));await settle();
  assert.equal(replacement.querySelector("[name='hours']").disabled,true);
  assert.equal(replacement.querySelector("button").disabled,false);
  assert.equal(state.doc.getElementById("faculty-dtr-detail").inert,false);
 } finally {state.close();}
});
