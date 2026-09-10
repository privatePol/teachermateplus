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
  function fail() {
    failed = true; saveButton.disabled = true; previewButton.disabled = true;
    for (const control of toolbar.querySelectorAll('button,input')) control.disabled = true;
    if (editor && !editor.isDestroyed) editor.setEditable(false);
    show(errors, [preservationError]);
    const recovery = form.querySelector('[data-case-original-source]');
    recovery.hidden = false; recovery.querySelector('textarea').value = original;
    status.textContent = 'Editor unavailable. Original source preserved; Save disabled.';
    retryPaste.disabled=true; dismissPaste.disabled=true;
  }
  function currentContent() {
    if (failed) throw new Error(preservationError);
    if (dirty) {
      try { source.value = serializeEditor(editor); }
      catch (error) { fail(); throw error; }
    }
    return source.value;
  }
  function insertPaste(html,text,files=false) {
    if (failed) return;
    const before=editor.state, wasDirty=dirty, previousSource=source.value, previousAnchor=anchorCell;
    try {
      if (files) throw new Error('Images and embedded objects are not supported.');
      const normalized=normalizeClipboard(html,text);
      if (!normalized.trim()) throw new Error('No supported Case content was found.');
      const probe=createCaseEditor(document.createElement('div'),normalized); probe.destroy();
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
      const reason=error.message===preservationError ? 'The clipboard could not be inserted without changing supported content.' : error.message;
      show(errors,['Paste was not inserted. Your existing content is unchanged.',reason]);
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
      catch { fail(); }
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
    group('Undo/redo and clear formatting');
    button('Undo', () => editor.chain().focus().undo().run(), null, () => editor.can().undo());
    button('Redo', () => editor.chain().focus().redo().run(), null, () => editor.can().redo());
    button('Clear text formatting', () => editor.chain().focus().clearTextFormatting().run());
    saveButton.disabled = false; previewButton.disabled = false;
    status.textContent='Editor ready. Enter creates a paragraph; Shift+Enter inserts a line break.';
    refresh();
  } catch { host.innerHTML = safeHtml; fail(); }
  form.addEventListener('submit', event => {
    try { currentContent(); } catch { event.preventDefault(); fail(); }
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
const form = document.querySelector('[data-case-editor-form]');
if (form) mountCaseEditor(form);
