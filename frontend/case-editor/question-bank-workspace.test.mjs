import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('../../static/js/departmental_exam_question_bank_workspace.js',import.meta.url),'utf8');

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
    assert.equal(entries()[1].querySelector('.qb-index-selected')?.getAttribute('aria-label'),'Selected for deletion');
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
