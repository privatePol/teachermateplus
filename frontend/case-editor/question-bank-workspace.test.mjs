import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('../../static/js/departmental_exam_question_bank_workspace.js',import.meta.url),'utf8');
const selectionSource=readFileSync(new URL('../../static/js/departmental_exam_bulk_question_delete.js',import.meta.url),'utf8');

function sectionPage(caseMemberCount=2) {
  const question=(id,linked=false,difficulty='EASY')=>`<article class="question-card" id="qb-question-${id}" data-question-id="${id}" data-linked="${linked?1:0}" data-difficulty="${difficulty}"><span class="question-position">${id}</span><div data-question-index-label>Synthetic item</div><input type="checkbox" data-bulk-question name="selected_questions" form="bulk-question-delete-form" value="${id}:1"></article>`;
  const section=(id,contents)=>`<section data-qb-section aria-label="Section ${id}"><h2><button class="qb-section-header" type="button" data-qb-section-toggle aria-expanded="true" aria-controls="section-${id}" aria-label="Collapse section: Section ${id}" disabled><span class="qb-section-title">Section ${id}</span><span class="qb-section-counts"><span class="qb-section-count">Saved count</span></span><span data-qb-section-icon aria-hidden="true" hidden><svg data-qb-icon-collapse><path/></svg><svg data-qb-icon-expand><path/></svg></span></button></h2><div id="section-${id}" data-qb-section-content>${contents}</div></section>`;
  const ownedCase=(id,contents,open=false)=>`<article class="qb-case-card" id="qb-case-${id}" data-qb-case-title="Synthetic Case ${id}"><input type="checkbox" data-section-move-case name="selected_cases" form="bulk-question-delete-form" value="case-${id}"><div class="collapse tmp-case-collapse${open?' show':''}">${contents}</div></article>`;
  const counts=['move-standalone','move-case','move-question','hidden-question','hidden-standalone','hidden-case','hidden-affected'].map(name=>`<span data-${name}-count>0</span>`).join('');
  const dom=new JSDOM(`<!doctype html><div data-contribution-workspace data-question-bank-workspace>
    <form id="bulk-question-delete-form" data-bulk-question-form><select data-bulk-question-filter><option value="all">All</option><option value="MODERATE">Moderate</option><option value="standalone">Standalone</option></select>
      <input type="checkbox" data-bulk-select-all><span data-bulk-selected-count>0</span><button data-bulk-delete-button>Delete</button><button data-section-move-button>Move</button><p data-section-move-error hidden></p>
      ${counts}<p data-section-hidden-summary hidden></p></form>
    <div data-qb-section-controls hidden><button type="button" data-qb-expand-sections>Expand all sections</button><button type="button" data-qb-collapse-sections>Collapse all sections</button></div>
    <div data-qb-layout><main data-qb-question-list>${section(1,ownedCase(7,question(11,true)+Array.from({length:caseMemberCount-1},(_,index)=>question(15+index,true)).join(''))+question(12,false,'MODERATE'))}${section(2,ownedCase(8,question(14,true),true)+question(13))}</main>
    <aside data-qb-index-panel><div class="qb-index-heading"><button data-qb-index-close>Close</button></div><div data-qb-index-list></div><p data-qb-index-empty hidden></p></aside><button data-qb-index-reopen>Index</button></div></div>`,{pretendToBeVisual:true,runScripts:'outside-only'});
  const {window}=dom;
  window.matchMedia=()=>({matches:false,addEventListener(){}});
  window.scrollTo=options=>{window.lastScroll=options;window.navigationOrder.push('scroll');};
  window.navigationOrder=[];
  window.confirm=()=>true;
  window.HTMLElement.prototype.getClientRects=function() {
    return this.hidden || this.closest('[data-qb-section-content][hidden]') || (this.closest('.tmp-case-collapse') && !this.closest('.tmp-case-collapse').classList.contains('show'))?[]:[{}];
  };
  window.HTMLElement.prototype.getBoundingClientRect=()=>({top:100,bottom:300,height:20});
  window.bootstrap={Collapse:{getOrCreateInstance(element){return {show(){
    assert.equal(element.closest('[data-qb-section-content]').hidden,false,'section must expand before Case');
    window.navigationOrder.push('case');
    element.classList.add('show');element.dispatchEvent(new window.Event('shown.bs.collapse',{bubbles:true}));
  }};}}};
  // The server markup is accessible without JS; controls activate only on init.
  assert.equal(window.document.querySelector('[data-qb-section-content]').hidden,false);
  assert.equal(window.document.querySelector('[data-qb-section-toggle]').hidden,false);
  assert.equal(window.document.querySelector('[data-qb-section-toggle]').disabled,true);
  assert.equal(window.document.querySelector('[data-qb-section-controls]').hidden,true);
  window.eval(selectionSource);
  window.eval(source);
  return dom;
}

test('sections default expanded; independent and all controls preserve nested Case states and DOM order', () => {
  const dom=sectionPage();
  try {
    const {document}=dom.window;
    const contents=[...document.querySelectorAll('[data-qb-section-content]')];
    const toggles=[...document.querySelectorAll('[data-qb-section-toggle]')];
    const order=()=>[...document.querySelectorAll('.question-card')].map(item=>item.id);
    const before=order();
    assert.deepEqual(contents.map(item=>item.hidden),[false,false]);
    assert.equal(document.querySelector('[data-qb-section-controls]').hidden,false);
    assert.equal(document.querySelector('[data-qb-collapse-sections]').getAttribute('aria-controls'),'section-1 section-2');
    assert.equal(document.querySelector('[data-qb-expand-sections]').getAttribute('aria-controls'),'section-1 section-2');
    assert.deepEqual(toggles.map(item=>[item.hidden,item.getAttribute('aria-expanded'),item.getAttribute('aria-controls')]),[[false,'true','section-1'],[false,'true','section-2']]);
    toggles[0].click();
    assert.deepEqual(contents.map(item=>item.hidden),[true,false]);
    assert.equal(toggles[0].getAttribute('aria-expanded'),'false');
    assert.equal(toggles[0].querySelector('.qb-section-title').textContent,'Section 1');
    assert.equal(toggles[0].querySelector('.qb-section-count').textContent,'Saved count');
    assert.equal(toggles[0].getAttribute('aria-label'),'Expand section: Section 1');
    document.querySelector('[data-qb-collapse-sections]').click();
    assert.deepEqual(contents.map(item=>item.hidden),[true,true]);
    document.querySelector('[data-qb-expand-sections]').click();
    assert.deepEqual(contents.map(item=>item.hidden),[false,false]);
    assert.deepEqual([...document.querySelectorAll('.tmp-case-collapse')].map(item=>item.classList.contains('show')),[false,true]);
    assert.deepEqual(order(),before);
  } finally {dom.window.close();}
});

test('header title badge background and SVG paths toggle once; native Enter/Space activation preserves header and ARIA', () => {
  const dom=sectionPage();
  try {
    const {document,KeyboardEvent}=dom.window;
    const toggle=document.querySelector('[data-qb-section-toggle]');
    const content=document.querySelector('#section-1');
    const headerHTML=toggle.innerHTML;
    let changes=0;
    document.addEventListener('tmp:section-visibility-changed',()=>changes++);
    assert.equal(toggle.tagName,'BUTTON');assert.equal(toggle.type,'button');assert.equal(toggle.disabled,false);
    assert.equal(toggle.querySelector('button'),null);
    assert.equal(toggle.querySelector('[data-qb-section-icon]').hidden,false);
    assert.equal(toggle.querySelector('[data-qb-section-icon]').getAttribute('aria-hidden'),'true');
    const surfaces=[toggle.querySelector('.qb-section-title'),toggle.querySelector('.qb-section-count'),toggle.querySelector('[data-qb-icon-collapse] path'),toggle.querySelector('[data-qb-icon-expand] path'),toggle];
    surfaces.forEach((surface,index)=>{
      surface.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
      assert.equal(changes,index+1);
      assert.equal(content.hidden,index%2===0);
      assert.equal(toggle.getAttribute('aria-expanded'),String(!content.hidden));
      assert.equal(toggle.getAttribute('aria-label'),`${content.hidden?'Expand':'Collapse'} section: Section 1`);
      assert.equal(toggle.innerHTML,headerHTML,'toggling must retain badges/icons');
    });
    toggle.focus();assert.equal(document.activeElement,toggle);
    // JSDOM does not synthesize keyboard default actions. Ensure keys remain
    // unprevented, then model the one click produced by a native button.
    for(const key of ['Enter',' ']) {
      for(const type of ['keydown','keyup']) {
        const event=new KeyboardEvent(type,{key,bubbles:true,cancelable:true});
        assert.equal(toggle.dispatchEvent(event),true);
        assert.equal(event.defaultPrevented,false);
      }
      const before=changes;toggle.click();assert.equal(changes,before+1);
      assert.equal(toggle.getAttribute('aria-expanded'),String(!content.hidden));
      assert.equal(toggle.innerHTML,headerHTML);
    }
  } finally {dom.window.close();}
});

test('Move button label follows deduplicated selections, whole Cases, hidden items, filters and invalid linked selections', () => {
  const dom=sectionPage();
  try {
    const {document,Event}=dom.window;
    const move=document.querySelector('[data-section-move-button]');
    const expected=(count,disabled=false)=>{
      assert.equal(move.textContent,count?`Move selected ${count} question${count===1?'':'s'} to section`:'Move to section');
      assert.equal(move.disabled,disabled);
    };
    const select=(selector,checked=true)=>{const box=document.querySelector(selector);box.checked=checked;box.dispatchEvent(new Event('change'));};
    expected(0,true);
    select('#qb-question-12 input');expected(1);
    select('#qb-case-7 [data-section-move-case]');expected(3);
    document.querySelector('[data-qb-section-toggle]').click();expected(3);
    select('[data-bulk-select-all]');expected(5,true); // Includes a visible linked item, still invalid.
    select('[data-bulk-select-all]',false);expected(3);
    const filter=document.querySelector('[data-bulk-question-filter]');filter.value='MODERATE';filter.dispatchEvent(new Event('change'));expected(1);
    select('#qb-question-12 input',false);expected(0,true);
    filter.value='all';filter.dispatchEvent(new Event('change'));
    document.querySelector('[data-qb-expand-sections]').click();
    const disclosure=document.querySelector('#qb-case-7 .tmp-case-collapse');disclosure.classList.add('show');disclosure.dispatchEvent(new Event('shown.bs.collapse',{bubbles:true}));
    select('#qb-question-11 input');expected(1,true);
    select('#qb-case-7 [data-section-move-case]');expected(2,true); // Member overlap counted once, but Move is still invalid.
    select('#qb-question-11 input',false);expected(2);
    select('#qb-case-7 [data-section-move-case]',false);expected(0,true);
    const ten=sectionPage(10);
    try {
      const whole=ten.window.document.querySelector('#qb-case-7 [data-section-move-case]');
      whole.checked=true;whole.dispatchEvent(new ten.window.Event('change'));
      const tenMove=ten.window.document.querySelector('[data-section-move-button]');
      assert.equal(tenMove.textContent,'Move selected 10 questions to section');
      assert.equal(tenMove.disabled,false);
      ten.window.document.querySelector('[data-qb-section-toggle]').click();
      assert.equal(tenMove.textContent,'Move selected 10 questions to section');
    } finally {ten.window.close();}
  } finally {dom.window.close();}
});

test('section collapse preserves standalone and whole-Case selections with hidden counts and separate Delete scope', () => {
  const dom=sectionPage();
  try {
    const {document,Event}=dom.window;
    const standalone=document.querySelector('#qb-question-12 input');
    const whole=document.querySelector('#qb-case-7 [data-section-move-case]');
    for(const box of [standalone,whole]){box.checked=true;box.dispatchEvent(new Event('change'));}
    const count=name=>document.querySelector(`[data-${name}-count]`).textContent;
    assert.equal(count('move-standalone'),'1');assert.equal(count('move-case'),'1');assert.equal(count('move-question'),'3');
    document.querySelector('[data-qb-section-toggle]').click();
    assert.equal(standalone.checked,true);assert.equal(whole.checked,true);
    assert.equal(count('hidden-question'),'1');assert.equal(count('hidden-standalone'),'1');assert.equal(count('hidden-case'),'1');assert.equal(count('hidden-affected'),'3');
    assert.equal(document.querySelector('[data-section-hidden-summary]').hidden,false);
    assert.equal(document.querySelector('[data-bulk-selected-count]').textContent,'1');
    assert.equal(document.querySelector('[data-section-move-button]').disabled,false);
    assert.ok(document.querySelector('[data-qb-index-target="qb-case-7"] .qb-index-selected'));
    assert.ok(document.querySelector('[data-qb-index-target="qb-question-12"] .qb-index-selected'));
    const all=document.querySelector('[data-bulk-select-all]');all.checked=true;all.dispatchEvent(new Event('change'));
    all.checked=false;all.dispatchEvent(new Event('change'));
    assert.equal(standalone.checked,true,'deselect all visible must preserve hidden section selections');
    const posted=new dom.window.FormData(document.querySelector('form'));
    assert.deepEqual(posted.getAll('selected_questions'),['12:1']);
    assert.deepEqual(posted.getAll('selected_cases'),['case-7']);
    let prompt='';dom.window.confirm=message=>{prompt=message;return false;};
    assert.equal(document.querySelector('form').dispatchEvent(new dom.window.SubmitEvent('submit',{cancelable:true,submitter:document.querySelector('[data-bulk-delete-button]')})),false);
    assert.match(prompt,/Delete 1 selected question\?/);
    assert.equal(whole.checked,true);
    document.querySelector('[data-qb-section-toggle]').click();
    assert.equal(document.querySelector('[data-section-hidden-summary]').hidden,true);
    assert.equal(standalone.checked,true);assert.equal(whole.checked,true);
  } finally {dom.window.close();}
});

test('select all excludes collapsed sections; filtering still deselects questions and incomplete whole Cases', () => {
  const dom=sectionPage();
  try {
    const {document,Event}=dom.window;
    const whole=document.querySelector('#qb-case-7 [data-section-move-case]');
    whole.checked=true;whole.dispatchEvent(new Event('change'));
    document.querySelector('[data-qb-section-toggle]').click();
    const all=document.querySelector('[data-bulk-select-all]');all.checked=true;all.dispatchEvent(new Event('change'));
    assert.deepEqual([...document.querySelectorAll('[data-bulk-question]:checked')].map(box=>box.closest('.question-card').id),['qb-question-14','qb-question-13']);
    assert.equal(whole.checked,true);
    const filter=document.querySelector('[data-bulk-question-filter]');filter.value='MODERATE';filter.dispatchEvent(new Event('change'));
    assert.equal(document.querySelectorAll('[data-bulk-question]:checked').length,0);
    assert.equal(whole.checked,false);
    assert.deepEqual([...document.querySelectorAll('[data-qb-index-target]')].map(item=>item.dataset.qbIndexTarget),['qb-question-12']);
    // A matching selection stays selected even if its section is collapsed.
    const matching=document.querySelector('#qb-question-12 input');matching.checked=true;matching.dispatchEvent(new Event('change'));
    filter.dispatchEvent(new Event('change'));assert.equal(matching.checked,true);
    filter.value='all';filter.dispatchEvent(new Event('change'));assert.equal(matching.checked,true);
  } finally {dom.window.close();}
});

test('collapsed partial linked selection stays invalid and whole-Case overlap is counted once', () => {
  const dom=sectionPage();
  try {
    const {document,Event}=dom.window;
    const disclosure=document.querySelector('#qb-case-7 .tmp-case-collapse');
    disclosure.classList.add('show');disclosure.dispatchEvent(new Event('shown.bs.collapse',{bubbles:true}));
    const member=document.querySelector('#qb-question-11 input');member.checked=true;member.dispatchEvent(new Event('change'));
    document.querySelector('[data-qb-section-toggle]').click();
    assert.equal(member.checked,true);
    const whole=document.querySelector('#qb-case-7 [data-section-move-case]');
    assert.equal(whole.checked,false,'a partial linked selection never auto-selects its Case');
    whole.checked=true;whole.dispatchEvent(new Event('change'));
    assert.equal(document.querySelector('[data-move-question-count]').textContent,'2');
    assert.equal(document.querySelector('[data-hidden-affected-count]').textContent,'2');
    assert.equal(document.querySelector('[data-section-move-button]').disabled,true);
    assert.equal(document.querySelector('[data-section-move-error]').hidden,false);
    assert.equal(document.querySelector('form').dispatchEvent(new dom.window.SubmitEvent('submit',{cancelable:true,submitter:document.querySelector('[data-section-move-button]')})),false);
    assert.equal(document.querySelector('[data-bulk-selected-count]').textContent,'1');
    document.querySelector('[data-qb-section-toggle]').click();
    assert.equal(member.checked,true);
    // Ordinary Case collapse keeps the pre-existing individual deselection rule.
    disclosure.classList.remove('show');disclosure.dispatchEvent(new Event('hidden.bs.collapse',{bubbles:true}));
    assert.equal(member.checked,false);assert.equal(whole.checked,true);
  } finally {dom.window.close();}
});

test('Question Index expands collapsed section then nested Case before scrolling without changing selections', () => {
  const dom=sectionPage();
  try {
    const {document}=dom.window;
    document.querySelector('[data-qb-collapse-sections]').click();
    assert.equal(document.querySelectorAll('[data-qb-index-target]').length,7);
    document.querySelector('[data-qb-index-target="qb-question-11"]').click();
    assert.equal(document.querySelector('#section-1').hidden,false);
    assert.equal(document.querySelector('#section-2').hidden,true);
    assert.deepEqual(dom.window.navigationOrder,['case','scroll']);
    assert.ok(document.querySelector('#qb-question-11').classList.contains('qb-nav-flash'));
    assert.equal(document.activeElement.id,'qb-question-11');
    document.querySelector('[data-qb-index-target="qb-question-13"]').click();
    assert.equal(document.querySelector('#section-2').hidden,false);
    assert.equal(document.querySelector('#qb-case-8 .tmp-case-collapse').classList.contains('show'),true);
    assert.equal(document.querySelectorAll('input:checked').length,0);
  } finally {dom.window.close();}
});

test('workspace global reorder collects every rendered question, including filtered cards', () => {
  const template=readFileSync(new URL('../../templates/departmental_exams/faculty/contribution_workspace.html',import.meta.url),'utf8');
  const reorderScript=template.match(/<script>\s*(\(\(\) => \{\s*const list = document\.getElementById\("question-list"\);[\s\S]*?)<\/script>/)?.[1];
  assert.ok(reorderScript, 'exercise the actual workspace reorder script');
  const dom=new JSDOM(`<!doctype html><form id="question-order-form"><input id="ordered-question-ids"><button>Save displayed order</button></form>
    <div id="question-list">${[11,12,13].map((id,index)=>`<article class="question-card" data-question-id="${id}" data-section-id="${index===1?2:1}"><span class="question-position">${index+1}</span><button class="move-up">Move up</button><button class="move-down">Move down</button></article>`).join('')}</div>`,{runScripts:'outside-only'});
  try {
    const {document,Event}=dom.window;
    const placements=()=>[...document.querySelectorAll('.question-card')].map(card=>[card.dataset.questionId,card.dataset.sectionId]).sort();
    const before=placements();
    dom.window.eval(reorderScript);
    document.querySelector('[data-question-id="13"] .move-up').click();
    document.querySelector('[data-question-id="11"] .move-down').click();
    assert.deepEqual([...document.querySelectorAll('.question-card')].map(card=>card.dataset.questionId),['13','11','12']);
    assert.deepEqual([...document.querySelectorAll('.question-position')].map(span=>span.textContent),['1','2','3']);
    document.querySelector('[data-question-id="13"]').hidden=true;
    document.querySelector('form').dispatchEvent(new Event('submit',{cancelable:true}));
    assert.equal(document.querySelector('#ordered-question-ids').value,'13,11,12');
    assert.deepEqual(placements(),before);
  } finally {dom.window.close();}
});

function page(narrow=false) {
  const dom=new JSDOM(`<!doctype html><html><body>
    <nav class="faculty-topbar"></nav>
    <div data-question-bank-workspace><div data-qb-action-toolbar></div>
      <form><select data-bulk-question-filter><option value="all">All</option></select><input type="checkbox" data-bulk-select-all></form>
      <div data-qb-layout><main data-qb-question-list>
        <article class="qb-case-card" id="qb-case-7" data-qb-case-title="Accounting Case"><div class="tmp-case-collapse">
          <article class="question-card" id="qb-question-11"><span class="question-position">1</span><div data-question-index-label>1. Linked stem</div><input type="checkbox" data-bulk-question></article>
        </div></article>
        <article class="question-card" id="qb-question-12"><span class="question-position">2</span><div data-question-index-label>Question 2: Standalone stem</div><input type="checkbox" data-bulk-question></article>
      </main><aside><section data-qb-index-panel><div class="qb-index-heading"><button data-qb-index-close>Close</button></div><div data-qb-index-list></div><p data-qb-index-empty hidden></p></section><button data-qb-index-reopen>Open</button></aside></div>
    </div></body></html>`,{url:'http://localhost/',pretendToBeVisual:true,runScripts:'outside-only'});
  const {window}=dom;
  window.matchMedia=()=>({matches:narrow,addEventListener(){}});
  window.scrollTo=options=>{window.lastScroll=options;};
  window.document.querySelector('.faculty-topbar').getBoundingClientRect=()=>({height:80});
  window.document.querySelector('[data-qb-index-panel]').getBoundingClientRect=()=>({bottom:600});
  window.document.querySelector('.qb-index-heading').getBoundingClientRect=()=>({bottom:100});
  window.HTMLElement.prototype.getClientRects=function() {return this.hidden?[]:[{}];};
  window.HTMLElement.prototype.getBoundingClientRect=function() {return {top:this.id==='qb-question-12'?320:160,bottom:400,height:40};};
  window.bootstrap={Collapse:{getOrCreateInstance(element) {return {show() {element.classList.add('show');element.dispatchEvent(new window.Event('shown.bs.collapse',{bubbles:true}));}};}}};
  window.eval(source);
  return dom;
}

test('index navigates Case members without deleting, follows filter and reorder', async () => {
  const dom=page();
  try {
    const {document,Event}=dom.window;
    const entries=()=>[...document.querySelectorAll('[data-qb-index-target]')];
    assert.deepEqual(entries().map(item=>item.textContent),['Accounting Case','Question 1. Linked stem','Question 2. Standalone stem']);
    assert.equal(entries()[1].querySelector('.qb-index-number')?.textContent,'Question 1.');
    entries()[1].click();
    assert.equal(document.querySelector('.tmp-case-collapse').classList.contains('show'),true);
    assert.equal(dom.window.lastScroll.top,20);
    assert.equal(document.querySelectorAll('[data-bulk-question]:checked').length,0);
    const linked=document.querySelector('#qb-question-11 [data-bulk-question]');
    linked.checked=true;linked.dispatchEvent(new Event('change',{bubbles:true}));
    assert.equal(entries()[1].querySelector('.qb-index-selected')?.getAttribute('aria-label'),'Selected for an action');
    const standalone=document.querySelector('#qb-question-12');
    standalone.hidden=true;
    document.querySelector('[data-bulk-question-filter]').dispatchEvent(new Event('change',{bubbles:true}));
    assert.equal(entries().length,2);
    standalone.hidden=false;
    const list=document.querySelector('[data-qb-question-list]');
    list.prepend(standalone);
    document.dispatchEvent(new Event('tmp:question-bank-order-changed'));
    assert.match(entries()[0].textContent,/Standalone stem/);
  } finally {dom.window.close();}
});

test('narrow index starts closed, reopens, and closes after a jump', () => {
  const dom=page(true);
  try {
    const {document}=dom.window;
    const layout=document.querySelector('[data-qb-layout]');
    assert.equal(layout.classList.contains('index-collapsed'),true);
    document.querySelector('[data-qb-index-reopen]').click();
    assert.equal(layout.classList.contains('index-collapsed'),false);
    document.querySelector('[data-qb-index-target="qb-question-12"]').click();
    assert.equal(layout.classList.contains('index-collapsed'),true);
    assert.equal(document.querySelectorAll('[data-bulk-question]:checked').length,0);
  } finally {dom.window.close();}
});

test('current reading highlight reveals its index row without scrolling the page', async () => {
  const dom=page();
  try {
    const {window}=dom;
    const {document}=window;
    await new Promise(resolve=>window.setTimeout(resolve,30));
    document.querySelector('#qb-case-7').getBoundingClientRect=()=>({top:-100,bottom:0});
    document.querySelector('#qb-question-11').getBoundingClientRect=()=>({top:-50,bottom:0});
    document.querySelector('#qb-question-12').getBoundingClientRect=()=>({top:30,bottom:80});
    const current=document.querySelector('[data-qb-index-target="qb-question-12"]');
    current.getBoundingClientRect=()=>({top:620,bottom:650});
    window.dispatchEvent(new window.Event('scroll'));
    await new Promise(resolve=>window.setTimeout(resolve,30));
    assert.equal(current.getAttribute('aria-current'),'location');
    assert.equal(document.querySelector('#qb-question-12').classList.contains('is-reading'),true);
    assert.equal(document.querySelector('[data-qb-index-panel]').scrollTop,54);
    assert.equal(window.lastScroll,undefined);
  } finally {dom.window.close();}
});
