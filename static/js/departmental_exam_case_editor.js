import {createCaseEditor, cellsShareTable} from '../../frontend/case-editor/extensions.js';
import {normalizeClipboard, prepareLegacy, serializeEditor, preservationError} from '../../frontend/case-editor/compatibility.js';

export function mountCaseEditor(form) {
  const host = form.querySelector('[data-case-rich-editor]');
  const source = form.querySelector('[data-case-source]');
  const preview = form.querySelector('[data-case-preview]');
  const errors = form.querySelector('[data-case-editor-errors]');
  const warnings = form.querySelector('[data-case-editor-warnings]');
  const previewButton = form.querySelector('[data-case-preview-button]');
  const saveButton = form.querySelector('[data-case-save]');
  const toolbar = form.querySelector('[data-case-toolbar]');
  const status = form.querySelector('[data-case-editor-status]');
  const original = source.value;
  const safeHtml = host.innerHTML;
  let dirty = false, editor, anchorCell = null, failed = false;
  let rejectedPaste = null, previewRequest = 0;
  const controls = [];
  const show = (box, messages) => { box.textContent = messages.join(' '); box.hidden = !messages.length; };
  const previewErrors = document.createElement('div');
  previewErrors.className='alert alert-warning'; previewErrors.hidden=true;
  previewErrors.setAttribute('role','alert'); previewErrors.dataset.casePreviewErrors='';
  warnings.after(previewErrors);
  const recovery = document.createElement('section');
  recovery.className='tmp-case-paste-recovery'; recovery.hidden=true;
  recovery.setAttribute('aria-label','Rejected paste recovery'); recovery.dataset.casePasteRecovery='';
  errors.after(recovery);
  const recoveryNote=document.createElement('p'); recovery.append(recoveryNote);
  const payloadFields = {};
  for (const [format,label] of [['html','Clipboard HTML source'],['text','Clipboard plain text']]) {
    const details=document.createElement('details'), summary=document.createElement('summary');
    summary.textContent=label; const textarea=document.createElement('textarea');
    textarea.readOnly=true; textarea.rows=4; textarea.className='form-control font-monospace';
    textarea.setAttribute('aria-label',label); textarea.dataset.caseClipboard=format;
    details.append(summary,textarea); recovery.append(details); payloadFields[format]=textarea;
  }
  function recoveryAction(label, action) {
    const control=document.createElement('button'); control.type='button';
    control.className='btn btn-sm btn-outline-secondary'; control.textContent=label;
    control.addEventListener('click',action); recovery.append(control); return control;
  }
  const retryPaste=recoveryAction('Retry retained paste at current selection', () => {
    if (!failed && rejectedPaste?.retained) insertPaste(rejectedPaste.html,rejectedPaste.text,rejectedPaste.files);
  });
  const dismissPaste=recoveryAction('Dismiss paste error and discard recovery copy', () => {
    if (failed) return;
    rejectedPaste=null; recovery.hidden=true;
    payloadFields.html.value=''; payloadFields.text.value=''; show(errors,[]);
    status.textContent='Paste error dismissed. Existing content remains editable. Use Preview before saving.';
    editor.view.focus();
  });
  function diagnostic(error, phase) {
    return /^CASE_(NORMALIZE|PREPARE|COMPARE|SERIALIZE|GEOMETRY|INSERT)_(TEXT|WHITESPACE|PARAGRAPH|ATTRIBUTE|CAPTION|ROW_GROUP|GEOMETRY)$/.test(error?.diagnosticCode)
      ? error.diagnosticCode : `CASE_${phase}_UNKNOWN`;
  }
  function fail(error, phase='SERIALIZE') {
    failed = true; form.dataset.caseEditorFailed = 'true'; saveButton.disabled = true; previewButton.disabled = true;
    for (const control of toolbar.querySelectorAll('button,input')) control.disabled = true;
    if (editor && !editor.isDestroyed) editor.setEditable(false);
    show(errors, [preservationError, '['+diagnostic(error,phase)+']']);
    const recovery = form.querySelector('[data-case-original-source]');
    recovery.hidden = false; recovery.querySelector('textarea').value = original;
    status.textContent = 'Editor unavailable. Original source preserved; Save disabled.';
    retryPaste.disabled=true; dismissPaste.disabled=true;
  }
  function currentContent() {
    if (failed) throw new Error(preservationError);
    if (dirty) {
      try { source.value = serializeEditor(editor); }
      catch (error) { fail(error); throw error; }
    }
    return source.value;
  }
  function insertPaste(html,text,files=false) {
    if (failed) return;
    const before=editor.state, wasDirty=dirty, previousSource=source.value, previousAnchor=anchorCell;
    let phase='NORMALIZE';
    try {
      if (files) throw new Error('Images and embedded objects are not supported.');
      const normalized=normalizeClipboard(html,text);
      if (!normalized.trim()) throw new Error('No supported Case content was found.');
      phase='PREPARE';
      const probe=createCaseEditor(document.createElement('div'),normalized); probe.destroy();
      phase='INSERT';
      if (!editor.commands.insertContent(prepareLegacy(normalized),{parseOptions:{preserveWhitespace:'full'}})) {
        throw new Error('Paste could not be inserted at this position.');
      }
      rejectedPaste=null; recovery.hidden=true;
      payloadFields.html.value=''; payloadFields.text.value=''; show(errors,[]);
      status.textContent='Paste inserted. Use Preview to check server validation.';
    } catch (error) {
      // Roll back any failed insertion transaction, including selection/history.
      if (editor.state !== before) editor.view.updateState(before);
      dirty=wasDirty; source.value=previousSource; anchorCell=previousAnchor; refresh();
      const retained=html.length<=100000 && text.length<=100000 &&
        new TextEncoder().encode(html).length+new TextEncoder().encode(text).length<=200000;
      rejectedPaste={retained,files,html:retained ? html : '',text:retained ? text : ''};
      payloadFields.html.value=rejectedPaste.html; payloadFields.text.value=rejectedPaste.text;
      recovery.hidden=false; retryPaste.disabled=!retained || files;
      recoveryNote.textContent=retained
        ? 'Recovery copies are kept only in this page. Expand a source below to select and copy it. Retry at the current selection, paste corrected content, or dismiss this error. Save and Preview apply only to your existing content.'
        : 'The clipboard exceeds the recovery size limit; no recovery copy was retained. Copy a smaller selection from Word and paste again. Save and Preview apply only to your existing content.';
      const publicMessages = new Set([
        'Images and embedded objects are not supported.',
        'Native Word equations are not supported. Use TMP LaTeX or Unicode.',
        'Pasted Case exceeds the request limit.', 'No supported Case content was found.',
        'Paste could not be inserted at this position.',
        'This Word list uses unsupported numbering. Paste the affected list as text, then apply Bullets or Numbered list in TMP. No content was inserted.',
        'This pasted border cannot be represented safely. Keep accounting lines as a single or double bottom border on an amount cell or its final paragraph, then paste again. No content was inserted.',
        'This border cannot be preserved as an accounting rule. Use explicit single/double bottom rules on amount cells; paragraph, stylesheet and conflicting rules are not supported.',
        'Stylesheet-defined accounting borders are not supported. Apply explicit bottom rules to amount cells in Word or TMP.',
        'This table would require a geometry change to edit safely. The original source is preserved.'
      ]);
      const reason=error.message===preservationError ? 'The clipboard could not be inserted without changing supported content.' :
        publicMessages.has(error.message) ? error.message : 'Paste could not be inserted safely. Your existing content is unchanged.';
      show(errors,['Paste was not inserted. Your existing content is unchanged.',reason,'['+diagnostic(error,phase)+']']);
      status.textContent='Paste rejected. Existing content is unchanged; Save and Preview remain available for that content only.';
    }
  }
  let groupControls;
  function group(label) {
    const section=document.createElement('div'); section.className='tmp-case-tool-group';
    section.setAttribute('role','group'); section.setAttribute('aria-label',label);
    const heading=document.createElement('span'); heading.className='tmp-case-tool-label'; heading.textContent=label;
    groupControls=document.createElement('div'); groupControls.className='tmp-case-tool-controls';
    section.append(heading,groupControls); toolbar.append(section);
  }
  const compactLabels={Bold:'B',Italic:'I',Underline:'U',Superscript:'x²',Subscript:'x₂',
    'Bulleted list':'• List','Numbered list':'1. List','Decrease indent':'− Indent','Increase indent':'+ Indent',
    'Paragraph left':'Left','Paragraph center':'Center','Paragraph right':'Right','Paragraph justify':'Justify',
    'Cell left':'Left','Cell center':'Center','Cell right':'Right','Cell justify':'Justify',
    'Cell vertical top':'Top','Cell vertical middle':'Middle','Cell vertical bottom':'Bottom'};
  toolbar.addEventListener('keydown', event => {
    if (event.target.tagName!=='BUTTON' || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const buttons=[...event.target.closest('[role=group]').querySelectorAll('button:not(:disabled)')];
    const index=buttons.indexOf(event.target);
    const next=event.key==='Home' ? 0 : event.key==='End' ? buttons.length-1 :
      (index+(event.key==='ArrowRight' ? 1 : -1)+buttons.length)%buttons.length;
    event.preventDefault(); buttons[next]?.focus();
  });
  function button(label, command, active = null, enabled = null) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'btn btn-sm btn-outline-secondary'; button.textContent = compactLabels[label] || label;
    button.title=label;
    button.setAttribute('aria-label',label);
    button.addEventListener('click', () => {
      try { command(); editor.commands.focus(); refresh(); }
      catch (error) {
        if (error.accountingConflict) { show(errors,[error.message]); status.textContent='Merge rejected. Existing content and rules are unchanged.'; }
        else fail(error);
      }
    });
    groupControls.append(button); controls.push({button,active,enabled}); return button;
  }
  function refresh() {
    if (!editor || editor.isDestroyed) return;
    for (const control of controls) {
      control.button.disabled = failed || (control.enabled ? !control.enabled() : false);
      if (control.active) control.button.setAttribute('aria-pressed',String(control.active()));
    }
  }
  function cellPosition() {
    const position = editor.state.selection.$from;
    for (let depth=position.depth; depth>0; depth--) if (['tableCell','tableHeader'].includes(position.node(depth).type.name)) return position.before(depth);
    return editor.state.selection.$anchorCell?.pos ?? null;
  }
  try {
    if (form.dataset.caseEditorDisplayUnavailable === 'true') throw new Error(preservationError);
    host.replaceChildren();
    editor = createCaseEditor(host, safeHtml, {
      onUpdate: () => {
        dirty = true; previewRequest++;
        if (!failed && !rejectedPaste) status.textContent='Content changed. Use Preview to validate the current content.';
      },
      onTransaction: ({transaction}) => {
        if (anchorCell !== null && transaction.docChanged) {
          const mapped = transaction.mapping.mapResult(anchorCell); anchorCell = mapped.deleted ? null : mapped.pos;
        }
        refresh();
      },
      editorProps: {
        attributes: {role:'textbox','aria-label':'Case narrative','aria-labelledby':'case-editor-label','aria-multiline':'true','aria-describedby':'case-editor-help'},
        handlePaste: (_view,event) => {
          event.preventDefault();
          insertPaste(event.clipboardData?.getData('text/html') || '',event.clipboardData?.getData('text/plain') || '',Boolean(event.clipboardData?.files?.length));
          return true;
        }
      }
    });
    // Only wrapper-background clicks are redirected. Native paragraph/cell
    // clicks and drag selections stay entirely under the table/editor engine.
    host.addEventListener('click', event => {
      if (!failed && event.target===host) editor.view.focus();
    });
    group('Text formatting');
    const toggle = (label,name,command) => button(label, () => editor.chain().focus()[command]().run(), () => editor.isActive(name));
    toggle('Bold','bold','toggleBold'); toggle('Italic','italic','toggleItalic'); toggle('Underline','underline','toggleUnderline');
    toggle('Superscript','superscript','toggleSuperscript'); toggle('Subscript','subscript','toggleSubscript');
    group('Lists and indentation');
    toggle('Bulleted list','bulletList','toggleBulletList'); toggle('Numbered list','orderedList','toggleOrderedList');
    for (const [label,delta] of [['Decrease indent',-1],['Increase indent',1]]) button(label,
      () => editor.chain().focus().indentParagraph(delta).run(), null, () => editor.can().indentParagraph(delta));
    group('Paragraph alignment');
    for (const alignment of ['left','center','right','justify']) button('Paragraph '+alignment,
      () => editor.chain().focus().setTextAlign(alignment).run(), () => editor.isActive({textAlign:alignment}));
    group('Table structure');
    function numberInput(label,max) {
      const wrapper = document.createElement('label'); wrapper.className = 'tmp-case-table-size'; wrapper.textContent = label+' ';
      const input = document.createElement('input'); input.type='number'; input.min='1'; input.max=String(max); input.value='2';
      wrapper.append(input); groupControls.append(wrapper); return input;
    }
    const rows=numberInput('Table rows',100), columns=numberInput('Table columns',20);
    button('Insert table', () => {
      if (!rows.checkValidity() || !columns.checkValidity()) { show(errors,['Choose 1–100 rows and 1–20 columns.']); return; }
      editor.chain().focus().insertTable({rows:Number(rows.value),cols:Number(columns.value),withHeaderRow:true}).run();
    });
    for (const [label,command] of [
      ['Add row above','addRowBefore'],['Add row below','addRowAfter'],['Delete row','deleteRow'],
      ['Add column before','addColumnBefore'],['Add column after','addColumnAfter'],['Delete column','deleteColumn'],
      ['Delete table','deleteTable'],['Merge cells','mergeCells'],['Split cell','splitCell']
    ]) button(label, () => editor.chain().focus()[command]().run(), null, () => editor.can()[command]());
    button('Start cell selection', () => { anchorCell = cellPosition(); status.textContent='Selection start recorded. Move to another cell, then choose Extend cell selection.'; }, null, () => cellPosition() !== null);
    button('Extend cell selection', () => editor.commands.setCellSelection({anchorCell,headCell:cellPosition()}), null, () => cellsShareTable(editor,anchorCell,cellPosition()));
    group('Cell alignment');
    for (const value of ['left','center','right','justify']) button('Cell '+value,
      () => editor.chain().focus().setCellAttribute('cellAlign',value).run(),
      () => editor.isActive('tableCell',{cellAlign:value}) || editor.isActive('tableHeader',{cellAlign:value}), () => cellPosition() !== null);
    for (const value of ['top','middle','bottom']) button('Cell vertical '+value,
      () => editor.chain().focus().setCellAttribute('verticalAlign',value).run(),
      () => editor.isActive('tableCell',{verticalAlign:value}) || editor.isActive('tableHeader',{verticalAlign:value}), () => cellPosition() !== null);
    group('Accounting rules');
    for (const [label,value] of [['Single Rule','single'],['Double Rule','double'],['Remove Rule',null]]) {
      button(label,()=>editor.chain().focus().setAccountingRule(value).run(),null,()=>cellPosition()!==null);
    }
    group('Undo/redo and clear formatting');
    button('Undo', () => editor.chain().focus().undo().run(), null, () => editor.can().undo());
    button('Redo', () => editor.chain().focus().redo().run(), null, () => editor.can().redo());
    button('Clear text formatting', () => editor.chain().focus().clearTextFormatting().run());
    saveButton.disabled = false; previewButton.disabled = false;
    status.textContent='Editor ready. Enter creates a paragraph; Shift+Enter inserts a line break.';
    refresh();
  } catch (error) { host.innerHTML = safeHtml; fail(error,'PREPARE'); }
  form.addEventListener('submit', event => {
    try { currentContent(); } catch (error) { event.preventDefault(); fail(error); }
  });
  previewButton.addEventListener('click', async () => {
    const request=++previewRequest;
    try {
      show(previewErrors,[]); show(warnings,[]);
      const body = new FormData(); body.append('stimulus',currentContent()); body.append('input_format','html');
      body.append('csrfmiddlewaretoken',form.querySelector('[name=csrfmiddlewaretoken]').value);
      const response = await fetch(form.dataset.previewUrl,{method:'POST',body,credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      const payload = await response.json();
      if (failed || request!==previewRequest) return;
      if (!response.ok) {
        show(previewErrors,payload.errors || ['Preview could not be generated.']);
        status.textContent=(rejectedPaste ? 'Paste remains rejected. ' : '')+'Preview failed. Current content has not passed this validation; any displayed preview is from an earlier request.';
        return;
      }
      preview.innerHTML=payload.html; show(warnings,payload.warnings || []);
      status.textContent=rejectedPaste ? 'Paste remains rejected. Preview validates only the existing content, not the rejected clipboard.' : 'Server-authoritative Preview updated.';
      if (typeof window.renderMathInElement === 'function') window.renderMathInElement(preview,{delimiters:[{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],trust:false,throwOnError:false,strict:'error',maxSize:10,maxExpand:1000});
    } catch {
      if (failed || request!==previewRequest) return;
      show(previewErrors,['Preview could not be generated. Current content has not been validated.']);
      status.textContent=(rejectedPaste ? 'Paste remains rejected. ' : '')+'Preview failed. Current content has not been validated; any displayed preview is from an earlier request.';
    }
  });
  return {editor,currentContent};
}
function questionPublicPasteMessage(error) {
  const supported = new Set([
    'Images and embedded objects are not supported.',
    'Native Word equations are not supported. Use TMP LaTeX or Unicode.',
    'This Word list uses unsupported numbering. Paste the affected list as text, then apply Bullets or Numbered list in TMP. No content was inserted.',
    'This pasted border cannot be represented safely. Keep accounting lines as a single or double bottom border on an amount cell or its final paragraph, then paste again. No content was inserted.',
    'This border cannot be preserved as an accounting rule. Use explicit single/double bottom rules on amount cells; paragraph, stylesheet and conflicting rules are not supported.',
    'Stylesheet-defined accounting borders are not supported. Apply explicit bottom rules to amount cells in Word or TMP.',
    'This table would require a geometry change to edit safely. The original source is preserved.'
  ]);
  return supported.has(error?.message)
    ? error.message
    : 'Paste could not be inserted safely. Existing content is unchanged.';
}

function createQuestionToolbar({editor, toolbar, errorBox, status, controls, getAnchor, setAnchor}) {
  let groupControls;
  const compact = {Bold:'B',Italic:'I',Underline:'U',Superscript:'x²',Subscript:'x₂',
    'Bulleted list':'• List','Numbered list':'1. List','Decrease indent':'− Indent','Increase indent':'+ Indent'};
  const group = label => {
    const advanced=['Table structure','Cell alignment','Accounting rules'].includes(label);
    const section=document.createElement(advanced?'details':'div'); section.className='tmp-case-tool-group';
    section.setAttribute('role','group'); section.setAttribute('aria-label',label);
    const heading=document.createElement(advanced?'summary':'span'); heading.className='tmp-case-tool-label'; heading.textContent=label;
    groupControls=document.createElement('div'); groupControls.className='tmp-case-tool-controls';
    section.append(heading,groupControls); toolbar.append(section);
  };
  const cellPosition = () => {
    const position=editor.state.selection.$from;
    for (let depth=position.depth;depth>0;depth--) if (['tableCell','tableHeader'].includes(position.node(depth).type.name)) return position.before(depth);
    return editor.state.selection.$anchorCell?.pos ?? null;
  };
  const button = (label, command, active=null, enabled=null) => {
    const control=document.createElement('button'); control.type='button'; control.className='btn btn-sm btn-outline-secondary';
    control.textContent=compact[label] || label; control.title=label; control.setAttribute('aria-label',label);
    control.addEventListener('mousedown', event => event.preventDefault());
    control.addEventListener('click', () => {
      try { command(); editor.commands.focus(); refresh(); }
      catch (error) { errorBox.textContent=error.accountingConflict ? error.message : 'The formatting action could not be completed safely.'; errorBox.hidden=false; status.textContent='Existing content is unchanged.'; }
    });
    groupControls.append(control); controls.push({control,active,enabled}); return control;
  };
  const refresh = () => controls.forEach(item => {
    item.control.disabled=Boolean(item.enabled && !item.enabled());
    if (item.active) item.control.setAttribute('aria-pressed',String(item.active()));
  });
  toolbar.addEventListener('keydown', event => {
    if (event.target.tagName!=='BUTTON' || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const buttons=[...event.target.closest('[role=group]').querySelectorAll('button:not(:disabled)')], index=buttons.indexOf(event.target);
    const next=event.key==='Home' ? 0 : event.key==='End' ? buttons.length-1 : (index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    event.preventDefault(); buttons[next]?.focus();
  });
  const toggle=(label,name,command)=>button(label,()=>editor.chain().focus()[command]().run(),()=>editor.isActive(name));
  group('Text formatting');
  toggle('Bold','bold','toggleBold'); toggle('Italic','italic','toggleItalic'); toggle('Underline','underline','toggleUnderline');
  toggle('Superscript','superscript','toggleSuperscript'); toggle('Subscript','subscript','toggleSubscript');
  group('Lists and indentation');
  toggle('Bulleted list','bulletList','toggleBulletList'); toggle('Numbered list','orderedList','toggleOrderedList');
  for (const [label,delta] of [['Decrease indent',-1],['Increase indent',1]]) button(label,()=>editor.chain().focus().indentParagraph(delta).run(),null,()=>editor.can().indentParagraph(delta));
  group('Paragraph alignment');
  for (const value of ['left','center','right','justify']) button('Paragraph '+value,()=>editor.chain().focus().setTextAlign(value).run(),()=>editor.isActive({textAlign:value}));
  group('Table structure');
  const numberInput=(label,max)=>{ const wrapper=document.createElement('label'); wrapper.className='tmp-case-table-size'; wrapper.textContent=label+' '; const input=document.createElement('input'); input.type='number'; input.min='1'; input.max=String(max); input.value='2'; wrapper.append(input); groupControls.append(wrapper); return input; };
  const rows=numberInput('Rows',40), columns=numberInput('Columns',12);
  button('Insert table',()=>{ if (!rows.checkValidity() || !columns.checkValidity()) { errorBox.textContent='Choose 1–40 rows and 1–12 columns.'; errorBox.hidden=false; return; } editor.chain().focus().insertTable({rows:Number(rows.value),cols:Number(columns.value),withHeaderRow:true}).run(); });
  for (const [label,command] of [['Add row above','addRowBefore'],['Add row below','addRowAfter'],['Delete row','deleteRow'],['Add column before','addColumnBefore'],['Add column after','addColumnAfter'],['Delete column','deleteColumn'],['Delete table','deleteTable'],['Merge cells','mergeCells'],['Split cell','splitCell']]) button(label,()=>editor.chain().focus()[command]().run(),null,()=>editor.can()[command]());
  button('Start cell selection',()=>{ setAnchor(cellPosition()); status.textContent='Selection start recorded. Move to another cell, then extend the selection.'; },null,()=>cellPosition()!==null);
  button('Extend cell selection',()=>editor.commands.setCellSelection({anchorCell:getAnchor(),headCell:cellPosition()}),null,()=>cellsShareTable(editor,getAnchor(),cellPosition()));
  group('Cell alignment');
  for (const value of ['left','center','right','justify']) button('Cell '+value,()=>editor.chain().focus().setCellAttribute('cellAlign',value).run(),()=>editor.isActive('tableCell',{cellAlign:value})||editor.isActive('tableHeader',{cellAlign:value}),()=>cellPosition()!==null);
  for (const value of ['top','middle','bottom']) button('Cell vertical '+value,()=>editor.chain().focus().setCellAttribute('verticalAlign',value).run(),()=>editor.isActive('tableCell',{verticalAlign:value})||editor.isActive('tableHeader',{verticalAlign:value}),()=>cellPosition()!==null);
  group('Accounting rules');
  for (const [label,value] of [['Single Rule','single'],['Double Rule','double'],['Remove Rule',null]]) button(label,()=>editor.chain().focus().setAccountingRule(value).run(),null,()=>cellPosition()!==null);
  group('Undo, redo and clear');
  button('Undo',()=>editor.chain().focus().undo().run(),null,()=>editor.can().undo()); button('Redo',()=>editor.chain().focus().redo().run(),null,()=>editor.can().redo()); button('Clear text formatting',()=>editor.chain().focus().clearTextFormatting().run());
  return refresh;
}

export function mountQuestionEditors(form) {
  const saveButton=form.querySelector('[data-question-save]'), previewButton=form.querySelector('[data-question-preview-button]');
  const globalErrors=form.querySelector('[data-question-editor-errors]'), status=form.querySelector('[data-question-editor-status]');
  const format=form.querySelector('[name=content_format]'), csrf=form.querySelector('[name=csrfmiddlewaretoken]');
  const initialFormat=format.value;
  const fieldErrors={}; const states=[];
  let previewSequence=0, previewPending=false, previewInvalid=false, failed=false;
  const show=(box,messages)=>{ box.textContent=(messages||[]).join(' '); box.hidden=!(messages||[]).length; };
  const setBusy=busy=>{ previewPending=busy; previewButton.disabled=busy||failed; saveButton.disabled=busy||failed||form.dataset.caseEditorFailed==='true'; form.setAttribute('aria-busy',String(busy)); };
  const clearFieldErrors=()=>{ Object.values(fieldErrors).forEach(box=>show(box,[])); show(globalErrors,[]); };
  const invalidatePreview=()=>{ previewSequence++; previewInvalid=false; clearFieldErrors(); };
  for (const field of form.querySelectorAll('[data-question-rich-field]')) {
    const name=field.dataset.questionRichField, source=form.querySelector(`[name="${name}"]`), host=field.querySelector('[data-question-rich-editor]');
    const toolbar=field.querySelector('[data-question-toolbar]'), errorBox=field.querySelector('[data-question-field-errors]'), recovery=field.querySelector('[data-question-paste-recovery]');
    fieldErrors[name]=errorBox;
    const safeHtml=host.innerHTML, original=source.value; let editor, dirty=false, anchorCell=null, rejectedPaste=null;
    let refresh=()=>{};
    let initialEditorContent;
    const state={name,source,host,original,get editor(){ return editor; },unchanged(){
      if (!editor || editor.isDestroyed) throw new Error('Editor unavailable.');
      return serializeEditor(editor)===initialEditorContent;
    },sync(){
      if (!editor || editor.isDestroyed) throw new Error('Editor unavailable.');
      source.value=serializeEditor(editor); return source.value;
    }};
    function showRecovery(html,text,message) {
      const htmlTarget=recovery.querySelector('[data-question-clipboard-html]'), textTarget=recovery.querySelector('[data-question-clipboard-text]');
      const retained=html.length<=100000 && text.length<=100000 && new TextEncoder().encode(html).length+new TextEncoder().encode(text).length<=200000;
      rejectedPaste={html:retained?html:'',text:retained?text:'',retained}; htmlTarget.value=rejectedPaste.html; textTarget.value=rejectedPaste.text;
      recovery.hidden=false; recovery.querySelector('[data-question-paste-note]').textContent=retained ? 'Clipboard copies are retained only in this page. Existing field content is unchanged.' : 'The clipboard was too large to retain. Existing field content is unchanged.';
      show(errorBox,['Paste was not inserted.',message]);
    }
    function insertPaste(html,text,files=false) {
      const before=editor.state, prior=source.value, priorDirty=dirty, priorAnchor=anchorCell;
      try {
        if (files) throw new Error('Images and embedded objects are not supported.');
        const normalized=normalizeClipboard(html,text); if (!normalized.trim()) throw new Error('No supported rich content was found.');
        const probe=createCaseEditor(document.createElement('div'),normalized); probe.destroy();
        if (!editor.commands.insertContent(prepareLegacy(normalized),{parseOptions:{preserveWhitespace:'full'}})) throw new Error('Paste could not be inserted at this position.');
        rejectedPaste=null; recovery.hidden=true; show(errorBox,[]); status.textContent=`${field.dataset.questionFieldLabel} paste inserted. Use Preview to validate.`;
      } catch (error) {
        if (editor.state!==before) editor.view.updateState(before); dirty=priorDirty; source.value=prior; anchorCell=priorAnchor;
        showRecovery(html,text,questionPublicPasteMessage(error)); status.textContent='Paste rejected. Existing content remains available.';
      }
    }
    try {
      if (host.dataset.questionEditorDisplayUnavailable==='true') throw new Error('Unsafe editor source.');
      host.replaceChildren();
      editor=createCaseEditor(host,safeHtml,{onUpdate:()=>{dirty=true; invalidatePreview(); status.textContent='Content changed. Use Preview to validate the current question.';},onTransaction:({transaction})=>{if(anchorCell!==null&&transaction.docChanged){const mapped=transaction.mapping.mapResult(anchorCell);anchorCell=mapped.deleted?null:mapped.pos;} refresh();},editorProps:{attributes:{role:'textbox','aria-label':field.dataset.questionFieldLabel,'aria-multiline':'true','aria-describedby':field.dataset.questionEditorHelp},handlePaste:(_view,event)=>{event.preventDefault(); insertPaste(event.clipboardData?.getData('text/html')||'',event.clipboardData?.getData('text/plain')||'',Boolean(event.clipboardData?.files?.length)); return true;}}});
      initialEditorContent=serializeEditor(editor);
      const controls=[]; refresh=createQuestionToolbar({editor,toolbar,errorBox,status,controls,getAnchor:()=>anchorCell,setAnchor:value=>{anchorCell=value;}}); refresh();
      host.TMPScientificEditor={insertText(text){editor.chain().focus().insertContent(text).run();},insertTemplate(before,after,placeholder){const {from,to}=editor.state.selection,selected=editor.state.doc.textBetween(from,to,'\n');editor.chain().focus().insertContent(before+(selected||placeholder)+after).run();}};
      recovery.querySelector('[data-question-paste-dismiss]').addEventListener('click',()=>{rejectedPaste=null; recovery.hidden=true; show(errorBox,[]); editor.commands.focus();});
      recovery.querySelector('[data-question-paste-retry]').addEventListener('click',()=>{if(rejectedPaste?.retained) insertPaste(rejectedPaste.html,rejectedPaste.text);});
    } catch (_error) {
      failed=true; host.innerHTML=safeHtml; field.querySelector('[data-question-original-source]').hidden=false; field.querySelector('[data-question-original-source] textarea').value=original;
      show(errorBox,['This editor could not load without changing saved content. The original source is preserved and Save is disabled.']);
    }
    states.push(state);
  }
  if (failed) { setBusy(false); saveButton.disabled=true; previewButton.disabled=true; return; }
  saveButton.disabled=form.dataset.caseEditorFailed==='true'; previewButton.disabled=false; status.textContent='Five editors ready. Preview validates the complete question before Save.';
  form.addEventListener('submit',event=>{
    if (previewPending || previewInvalid || failed) { event.preventDefault(); show(globalErrors,[previewPending ? 'Preview processing is still in progress.' : 'Resolve the rich-text validation errors or change the affected field before saving.']); return; }
    try {
      if (initialFormat==='PLAIN_TEXT' && states.every(state=>state.unchanged())) {
        for (const state of states) state.source.value=state.original;
        format.value='PLAIN_TEXT';
      } else {
        for (const state of states) state.sync();
        format.value='RICH_HTML_V1';
      }
    }
    catch (_error) { event.preventDefault(); show(globalErrors,['The question editor could not preserve content safely.']); }
  });
  previewButton.addEventListener('click',async()=>{
    let request; try { for (const state of states) state.sync(); format.value='RICH_HTML_V1'; request=++previewSequence; clearFieldErrors(); setBusy(true); }
    catch (_error) { show(globalErrors,['The question editor could not preserve content safely.']); return; }
    try {
      const body=new FormData(form); body.set('content_format','RICH_HTML_V1');
      const response=await fetch(form.dataset.previewUrl,{method:'POST',body,credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      const payload=await response.json(); if(request!==previewSequence||failed)return;
      setBusy(false);
      if(!response.ok){ previewInvalid=true; Object.entries(payload.errors||{}).forEach(([name,messages])=>show(fieldErrors[name]||globalErrors,messages)); if(!Object.keys(payload.errors||{}).length)show(globalErrors,['Preview could not be generated.']); status.textContent='Preview rejected. Correct the field errors before saving.'; return; }
      previewInvalid=false; Object.entries(payload.fields||{}).forEach(([name,html])=>{ const state=states.find(item=>item.name===name); if(!state)return; state.source.value=html; const preview= form.querySelector(`[data-question-preview-field="${name}"]`); if(preview){preview.innerHTML=html;if(typeof window.renderMathInElement==='function')window.renderMathInElement(preview,{delimiters:[{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],trust:false,throwOnError:false,strict:'error',maxSize:10,maxExpand:1000});} });
      status.textContent='Server-authoritative Preview updated. Save will validate again.';
    } catch (_error) { if(request!==previewSequence||failed)return; setBusy(false); previewInvalid=true; show(globalErrors,['Preview could not be generated. Current content has not passed server validation.']); status.textContent='Preview failed. Save remains blocked until content changes or Preview succeeds.'; }
  });
  document.dispatchEvent(new CustomEvent('tmp:scientific-rich-editor-ready'));
  return {states};
}

const caseForm = document.querySelector('[data-case-editor-form]');
if (caseForm) mountCaseEditor(caseForm);
document.querySelectorAll('[data-question-editor-form]').forEach(mountQuestionEditors);
