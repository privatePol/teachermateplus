import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost/',pretendToBeVisual:true});
for (const key of ['window','document','DOMParser','Node','HTMLElement','Element','MutationObserver','getComputedStyle','navigator']) Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.requestAnimationFrame=callback => setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
const {createCaseEditor}=await import('./extensions.js');
const {serializeEditor,normalizeClipboard,semanticSignature}=await import('./compatibility.js');
const {mountCaseEditor}=await import('../../static/js/departmental_exam_case_editor.js');
function make(html='<p>Accounting ₱1,250</p>') { const host=document.createElement('div'); document.body.append(host); return createCaseEditor(host,html); }
function cellPositions(editor) { const result=[]; editor.state.doc.descendants((node,pos) => { if (['tableCell','tableHeader'].includes(node.type.name)) result.push(pos); }); return result; }
const legacy='<h3>Case ₱ − α \\(x^2\\)</h3><p class="tmp-align-center">Narrative<br>Next</p><ol start="3"><li>First</li><li>Second</li></ol><table><caption><strong>Accounting</strong></caption><thead><tr><th scope="col" colspan="2">Header</th></tr></thead><tbody><tr><td rowspan="2" class="tmp-align-right">Cash</td><td><p>First</p><p>Second</p></td></tr><tr><td><table><tr><td>Nested</td></tr></table></td></tr></tbody><tfoot><tr><td colspan="2">Total</td></tr></tfoot></table>';
test('legacy captions, groups, scope, nested/merged cells, Unicode and list starts round trip', () => {
  const editor=make(legacy);
  assert.equal(semanticSignature(serializeEditor(editor,{preserve:false})),semanticSignature(legacy));
  const canonical=serializeEditor(editor); const reopened=make(canonical);
  assert.equal(serializeEditor(reopened),canonical);
  editor.destroy(); reopened.destroy();
});
test('separate legacy row groups remain separate after edit and serialization', () => {
  const editor=make('<table><tbody><tr><td>A</td></tr></tbody><tbody><tr><td>B</td></tr></tbody></table>');
  editor.commands.setTextSelection(cellPositions(editor)[0]+2); editor.commands.insertContent('Edited ');
  const html=serializeEditor(editor); assert.equal((html.match(/<tbody>/g)||[]).length,2);
  assert.match(html,/Edited A/); const reopened=make(html); assert.equal(serializeEditor(reopened),html);
  editor.destroy(); reopened.destroy();
});
test('surviving empty tables are not populated or dropped', () => { const editor=make('<p>Keep</p><table></table>'); assert.match(serializeEditor(editor),/<table><\/table>/); editor.destroy(); });
test('empty-table captions and equivalent inline mark nesting survive initialization', () => {
  for (const html of ['<p>Keep</p><table><caption>Empty schedule</caption></table>', '<p><em><strong>A</strong></em><strong><em>B</em></strong></p>']) {
    const editor=make(html); assert.equal(semanticSignature(serializeEditor(editor,{preserve:false})),semanticSignature(html)); editor.destroy();
  }
  assert.notEqual(semanticSignature('<table><tr><td><p>A</p><p>B</p></td></tr></table>'),semanticSignature('<table><tr><td>AB</td></tr></table>'));
});
test('legacy grids needing upstream repair fail closed before any editing transaction', () => {
  assert.throws(()=>make('<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>'),/geometry change/);
  assert.throws(()=>make('<table><tr><td rowspan="2">A</td></tr></table>'),/geometry change/);
});
test('format commands, alignment, bounded indent and clear formatting', () => {
  const editor=make(); editor.commands.selectAll();
  for (const name of ['toggleBold','toggleItalic','toggleUnderline','toggleSuperscript']) assert.equal(editor.commands[name](),true);
  let html=serializeEditor(editor); for (const tag of ['strong','em','u','sup']) assert.ok(html.includes('<'+tag+'>'));
  editor.commands.toggleSuperscript(); editor.commands.toggleSubscript(); assert.match(serializeEditor(editor),/<sub>/);
  for (const value of ['left','center','right','justify']) { editor.commands.setTextAlign(value); assert.ok(serializeEditor(editor).includes('tmp-align-'+value)); }
  for (let i=0;i<12;i++) editor.commands.indentParagraph(1);
  assert.match(serializeEditor(editor),/tmp-indent-8/); assert.equal(editor.can().indentParagraph(1),false);
  for (let i=0;i<12;i++) editor.commands.indentParagraph(-1);
  assert.doesNotMatch(serializeEditor(editor),/tmp-indent/);
  const text=editor.getText(); editor.commands.clearTextFormatting(); assert.equal(editor.getText(),text);
  assert.doesNotMatch(serializeEditor(editor),/<(?:strong|em|u|sup|sub)>|tmp-align/); editor.destroy();
});
test('lists, hard breaks and deliberate blanks survive reopen', () => {
  const editor=make('<p class="tmp-preserve">A<br><br><br>B</p><p class="tmp-preserve"></p><p>C</p>');
  const html=serializeEditor(editor); const reopened=make(html); assert.equal(serializeEditor(reopened),html);
  editor.commands.setTextSelection(1); editor.commands.toggleBulletList(); assert.match(serializeEditor(editor),/<ul>/);
  editor.commands.toggleOrderedList(); assert.match(serializeEditor(editor),/<ol>/);
  editor.commands.setHardBreak(); assert.match(serializeEditor(editor),/<br>/); editor.destroy(); reopened.destroy();
});
test('table engine insert, select, merge, split, geometry editing and independent alignment', () => {
  const editor=make(); editor.commands.insertTable({rows:2,cols:2,withHeaderRow:true});
  let positions=cellPositions(editor); assert.equal(positions.length,4);
  editor.commands.setCellSelection({anchorCell:positions[0],headCell:positions[1]});
  assert.equal(editor.commands.mergeCells(),true); assert.match(serializeEditor(editor),/colspan="2"/);
  assert.equal(editor.commands.splitCell(),true); assert.equal(cellPositions(editor).length,4);
  editor.commands.setTextSelection(cellPositions(editor)[0]+2);
  editor.commands.setCellAttribute('cellAlign','right'); editor.commands.setCellAttribute('verticalAlign','bottom'); editor.commands.setTextAlign('left');
  assert.match(serializeEditor(editor),/tmp-align-right tmp-valign-bottom/); assert.match(serializeEditor(editor),/tmp-align-left/);
  for (const command of ['addRowBefore','addRowAfter','addColumnBefore','addColumnAfter','deleteColumn','deleteRow']) assert.equal(editor.commands[command](),true,command);
  editor.commands.selectAll(); const tables=serializeEditor(editor).match(/<table/g).length;
  editor.commands.clearTextFormatting(); assert.equal(serializeEditor(editor).match(/<table/g).length,tables);
  editor.commands.setTextSelection(cellPositions(editor)[0]+2); assert.equal(editor.commands.deleteTable(),true); editor.destroy();
});
test('undo and redo restore document state', () => {
  const editor=make(); const before=serializeEditor(editor);
  editor.commands.insertContent('New'); const after=serializeEditor(editor); assert.notEqual(after,before);
  assert.equal(editor.commands.undo(),true); assert.equal(serializeEditor(editor),before);
  assert.equal(editor.commands.redo(),true); assert.equal(serializeEditor(editor),after); editor.destroy();
});
test('Word paste strips active markup, preserves supported formatting, compacts only Word blanks', () => {
  const html=normalizeClipboard('<p class="MsoNormal">&nbsp;</p><p class="MsoNormal"><span style="font-weight:bold;text-decoration:underline">₱500</span></p><script>bad()</script>');
  assert.doesNotMatch(html,/script|bad|&nbsp;/); assert.match(html,/<strong><u>₱500<\/u><\/strong>/);
  assert.match(normalizeClipboard('<p>A</p><p></p><p>B</p>'),/<p class="tmp-preserve"><\/p>/);
  assert.throws(()=>normalizeClipboard('<img src="https://bad.invalid/image">'),/not supported/);
});
test('ordinary Word numeric/bullet pseudo-lists preserve explicit starts and nesting', () => {
  const raw='<p style="text-align:right;mso-list:l0 level1 lfo1"><span style="mso-list:Ignore">3.</span>Third</p>'+
    '<p style="mso-list:l0 level2 lfo1"><span style="mso-list:Ignore">•</span>Nested</p>'+
    '<p style="mso-list:l0 level1 lfo1"><span style="mso-list:Ignore">4.</span>Fourth</p>';
  const html=normalizeClipboard(raw); assert.match(html,/<ol start="3">/); assert.match(html,/<ul>/); assert.match(html,/tmp-align-right/);
  const editor=make(html); assert.match(serializeEditor(editor),/Third/); assert.match(serializeEditor(editor),/Fourth/); editor.destroy();
  assert.throws(()=>normalizeClipboard('<p style="mso-list:l0 level1 lfo1"><span style="mso-list:Ignore">iv.</span>Roman</p>'),/unsupported numbering/);
});
test('14, 25, 50 Word-style tables retain table count through editor serialization', () => {
  for (const count of [14,25,50]) {
    const raw='<p class="MsoNormal">Case</p>'+('<table><tr><td><p class="MsoNormal">&nbsp;</p><p class="MsoNormal">₱1,250</p></td><td>−25</td></tr></table>').repeat(count);
    const editor=make(normalizeClipboard(raw)); assert.equal((serializeEditor(editor).match(/<table>/g)||[]).length,count); editor.destroy();
  }
});
function formFor(html) {
  const form=document.createElement('form'); form.dataset.caseEditorForm='';
  form.innerHTML='<div data-case-rich-editor></div><input data-case-source><div data-case-toolbar></div><div data-case-editor-status></div><div data-case-editor-errors hidden></div><div data-case-editor-warnings hidden></div><div data-case-preview></div><button type="button" data-case-preview-button disabled>Preview</button><button data-case-save disabled>Save</button><details data-case-original-source hidden><textarea></textarea></details>';
  form.querySelector('[data-case-rich-editor]').innerHTML=html; form.querySelector('[data-case-source]').value=html; document.body.append(form); return form;
}
test('mounted form preserves untouched original, provides accessible controls, serializes changes', () => {
  const form=formFor('<p>Original</p>'); const mounted=mountCaseEditor(form);
  assert.equal(form.querySelector('[data-case-save]').disabled,false);
  assert.equal(mounted.currentContent(),'<p>Original</p>');
  mounted.editor.commands.selectAll();
  const bold=[...form.querySelectorAll('button')].find(button=>button.textContent==='Bold'); bold.click();
  assert.equal(bold.getAttribute('aria-pressed'),'true'); assert.match(mounted.currentContent(),/<strong>Original/);
  mounted.editor.destroy(); form.remove();
});
test('mounted toolbar and keyboard events exercise formatting and the existing grid engine', () => {
  const form=formFor('<p>Toolbar text</p>'); const {editor,currentContent}=mountCaseEditor(form);
  const click = label => {
    const control=[...form.querySelectorAll('[data-case-toolbar] button')].find(el=>el.textContent===label);
    assert.ok(control,label); assert.equal(control.disabled,false,label); control.click();
    assert.equal(form.querySelector('[data-case-save]').disabled,false,label);
  };
  editor.commands.selectAll();
  for (const label of ['Bold','Italic','Underline','Superscript','Subscript','Paragraph left','Paragraph center','Paragraph right','Paragraph justify','Increase indent','Decrease indent','Bulleted list','Numbered list','Clear text formatting']) click(label);
  assert.match(currentContent(),/<ol>/); assert.doesNotMatch(currentContent(),/<strong>|<em>|<u>|tmp-indent/);
  editor.commands.setTextSelection(3); editor.view.dom.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,shiftKey:true,bubbles:true,cancelable:true}));
  assert.match(currentContent(),/<br>/);
  click('Insert table'); const positions=cellPositions(editor); assert.equal(positions.length,4);
  editor.commands.setTextSelection(positions[0]+2); click('Start cell selection');
  editor.view.dom.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Tab',code:'Tab',keyCode:9,bubbles:true,cancelable:true}));
  const selection=editor.state.selection.$from;
  const cellDepth=Array.from({length:selection.depth},(_,i)=>selection.depth-i).find(depth=>['tableCell','tableHeader'].includes(selection.node(depth).type.name));
  assert.equal(selection.before(cellDepth),positions[1]);
  click('Extend cell selection'); click('Merge cells'); assert.match(currentContent(),/colspan="2"/); click('Split cell');
  editor.commands.setTextSelection(cellPositions(editor)[0]+2);
  for (const label of ['Cell left','Cell center','Cell right','Cell justify','Cell vertical top','Cell vertical middle','Cell vertical bottom','Add row above','Add row below','Add column before','Add column after','Delete column','Delete row','Undo','Redo','Delete table']) click(label);
  assert.equal(cellPositions(editor).length,0);
  editor.destroy(); form.remove();
});
test('lossy schema conversion fails closed and retains source', () => {
  const form=formFor('<p>Before</p><unknown>Keep me</unknown>'); mountCaseEditor(form);
  assert.equal(form.querySelector('[data-case-save]').disabled,true);
  assert.equal(form.querySelector('[data-case-original-source]').hidden,false);
  assert.match(form.querySelector('textarea').value,/Keep me/);
  assert.match(form.querySelector('[data-case-editor-errors]').textContent,/preserved/); form.remove();
});
test('mounted paste handler inserts safe Word content and rejects unsupported paste without replacement', () => {
  const form=formFor('<p>Original</p>'); const {editor,currentContent}=mountCaseEditor(form);
  const paste=html => {
    const event=new dom.window.Event('paste',{bubbles:true,cancelable:true});
    Object.defineProperty(event,'clipboardData',{value:{files:[],getData:type=>type==='text/html' ? html : ''}});
    editor.view.dom.dispatchEvent(event); assert.equal(event.defaultPrevented,true);
  };
  editor.commands.selectAll(); paste('<p class="MsoNormal"><u>Word</u></p><table><tr><td>Cash ₱500</td></tr></table>');
  assert.match(currentContent(),/<u>Word<\/u>/); assert.match(currentContent(),/<table>/);
  const before=currentContent(); editor.commands.selectAll(); paste('<img src="https://example.invalid/never-load">');
  assert.equal(currentContent(),before); assert.match(form.querySelector('[data-case-editor-errors]').textContent,/not supported/);
  paste('<script>bad()</script>'); assert.equal(currentContent(),before);
  editor.destroy(); form.remove();
});
test('mounted Preview submits current HTML and CSRF to the existing same-origin endpoint', async () => {
  const form=formFor('<p>Original</p>'); form.dataset.previewUrl='/faculty/case-preview/';
  const csrf=document.createElement('input'); csrf.name='csrfmiddlewaretoken'; csrf.value='synthetic-test-token'; form.append(csrf);
  const {editor,currentContent}=mountCaseEditor(form); editor.commands.selectAll(); editor.commands.toggleUnderline();
  const originalFetch=globalThis.fetch; let request;
  globalThis.fetch=async (url,options) => { request={url,options}; return {ok:true,json:async()=>({html:'<p><u>Server canonical</u></p>',warnings:[]})}; };
  try {
    form.querySelector('[data-case-preview-button]').click(); await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(request.url,form.dataset.previewUrl); assert.equal(request.options.method,'POST'); assert.equal(request.options.credentials,'same-origin');
    assert.equal(request.options.body.get('csrfmiddlewaretoken'),'synthetic-test-token'); assert.equal(request.options.body.get('stimulus'),currentContent());
    assert.equal(form.querySelector('[data-case-preview]').innerHTML,'<p><u>Server canonical</u></p>');
  } finally { globalThis.fetch=originalFetch; editor.destroy(); form.remove(); }
});
test('built self-hosted bundle initializes the rendered form without injected CSS or fetch', () => {
  const form=formFor('<p>Built editor</p>'); form.querySelector('[data-case-source]').setAttribute('value','<p>Built editor</p>');
  const page=new JSDOM(form.outerHTML,{url:'http://localhost/',pretendToBeVisual:true,runScripts:'outside-only'}); form.remove();
  page.window.TextEncoder=TextEncoder;
  page.window.fetch=()=>{ throw new Error('Unexpected runtime fetch'); };
  try {
    page.window.eval(readFileSync(new URL('../../static/vendor/tiptap/3.31.3/tmp-case-editor.bundle.js',import.meta.url),'utf8'));
    assert.equal(page.window.document.querySelector('[data-case-save]').disabled,false);
    assert.equal(page.window.document.querySelector('.tiptap').textContent,'Built editor');
    assert.ok(page.window.document.querySelectorAll('[data-case-toolbar] button').length>30);
    assert.equal(page.window.document.querySelectorAll('style').length,0);
  } finally { page.window.close(); }
});

test('editor output round trips through the actual Python canonicalizer repeatedly', t => {
  const canonicalize = html => JSON.parse(execFileSync('python', ['-B','-c',
    'import json,sys; from apps.departmental_exams.scenario_content import canonicalize_scenario_content; print(json.dumps(canonicalize_scenario_content(json.load(sys.stdin)).html))'], {
    cwd:fileURLToPath(new URL('../..',import.meta.url)), input:JSON.stringify(html), encoding:'utf8',
    // No Django setup/database/logging is initialized by this pure canonicalizer.
    env:{...process.env, PYTHON_DOTENV_DISABLED:'1', PYTHONIOENCODING:'utf-8', DJANGO_SETTINGS_MODULE:'config.settings.base', DJANGO_SECRET_KEY:'tmp-editor-canonicalizer-test-only'}
  }));
  const fixtures=[legacy, '<p class="tmp-preserve tmp-align-justify tmp-indent-2"><u>Debit</u>  ₱1,250<br><br><br>\\(x^2\\)</p><p class="tmp-preserve"></p><table><tr><td class="tmp-align-right tmp-valign-middle"><p class="tmp-preserve tmp-align-left">Separate</p><p class="tmp-preserve">Paragraph</p></td></tr></table>'];
  // Mirrors the established Python accounting fixture: 4x6 tables, the same
  // merged cells, five narrative paragraphs and two meaningful p in column 3.
  for (const count of [14,25]) {
    let raw=Array.from({length:5},(_,i)=>`<p class="MsoNormal">Accounting narrative ${i+1}</p>`).join('');
    for (let table=1;table<=count;table++) {
      raw+='<table><tbody>';
      for (let row=1;row<=4;row++) {
        raw+='<tr>';
        for (let col=1;col<=6;col++) {
          if (table===1 && ((row===2 && col===1)||(row===3 && col===2))) continue;
          const span=table===1 && row===1 && col===1 ? ' rowspan="2"' : table===1 && row===3 && col===1 ? ' colspan="2"' : '';
          const value=`T${table}R${row}C${col} ₱1,250`;
          raw+=`<td${span}><p class="MsoNormal">&nbsp;</p><p class="MsoNormal" style="text-align:right">${col===3 ? '<strong>'+value+'</strong>' : '<span>'+value+'</span>'}</p>${col===3 ? '<p style="text-align:right">Adjustment −25</p>' : ''}<p class="MsoNormal"><br></p></td>`;
        }
        raw+='</tr>';
      }
      raw+='</tbody></table>';
    }
    const normalized=normalizeClipboard(raw);
    t.diagnostic(`${count}-table accounting probe: raw ${raw.length} chars; normalized ${normalized.length} chars; canonical ${canonicalize(normalized).length} chars.`);
    fixtures.push(normalized);
  }
  fixtures.push(normalizeClipboard('<table><tr><td>Account ₱500</td></tr></table>'.repeat(50)));
  for (const original of fixtures) {
    let value=canonicalize(original);
    for (let cycle=0;cycle<3;cycle++) {
      const editor=make(value); editor.commands.setTextSelection(1); editor.commands.toggleBold(); editor.commands.toggleBold();
      const exported=serializeEditor(editor), canonical=canonicalize(exported);
      assert.equal(semanticSignature(canonical),semanticSignature(exported));
      const reopened=make(canonical); assert.equal(canonicalize(serializeEditor(reopened)),canonical);
      editor.destroy(); reopened.destroy(); value=canonical;
    }
  }
});
