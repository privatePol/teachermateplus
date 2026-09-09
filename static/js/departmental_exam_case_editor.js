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
  const controls = [];
  const show = (box, messages) => { box.textContent = messages.join(' '); box.hidden = !messages.length; };
  function fail() {
    failed = true; saveButton.disabled = true; previewButton.disabled = true;
    for (const control of toolbar.querySelectorAll('button,input')) control.disabled = true;
    if (editor && !editor.isDestroyed) editor.setEditable(false);
    show(errors, [preservationError]);
    const recovery = form.querySelector('[data-case-original-source]');
    recovery.hidden = false; recovery.querySelector('textarea').value = original;
    status.textContent = 'Editor unavailable. Original source preserved; Save disabled.';
  }
  function currentContent() {
    if (failed) throw new Error(preservationError);
    if (dirty) source.value = serializeEditor(editor);
    return source.value;
  }
  function button(label, command, active = null, enabled = null) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'btn btn-sm btn-outline-secondary'; button.textContent = label;
    button.setAttribute('aria-label',label);
    button.addEventListener('click', () => {
      try { command(); editor.commands.focus(); refresh(); }
      catch { fail(); }
    });
    toolbar.append(button); controls.push({button,active,enabled}); return button;
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
      onUpdate: () => { dirty = true; },
      onTransaction: ({transaction}) => {
        if (anchorCell !== null && transaction.docChanged) {
          const mapped = transaction.mapping.mapResult(anchorCell); anchorCell = mapped.deleted ? null : mapped.pos;
        }
        refresh();
      },
      editorProps: {
        attributes: {role:'textbox','aria-label':'Case narrative','aria-multiline':'true','aria-describedby':'case-editor-help'},
        handlePaste: (_view,event) => {
          event.preventDefault(); show(errors,[]);
          try {
            if (event.clipboardData.files?.length) throw new Error('Images and embedded objects are not supported.');
            const html = normalizeClipboard(event.clipboardData.getData('text/html'), event.clipboardData.getData('text/plain'));
            if (!html.trim()) throw new Error('No supported Case content was found. No content was inserted.');
            const probe = createCaseEditor(document.createElement('div'), html); probe.destroy();
            if (!editor.commands.insertContent(prepareLegacy(html), {parseOptions:{preserveWhitespace:'full'}})) throw new Error('Paste could not be inserted at this position. Your content is unchanged.');
            status.textContent = 'Paste inserted. Use Preview to check server validation.';
          } catch (error) { show(errors,[error.message]); }
          return true;
        }
      }
    });
    const toggle = (label,name,command) => button(label, () => editor.chain().focus()[command]().run(), () => editor.isActive(name));
    toggle('Bold','bold','toggleBold'); toggle('Italic','italic','toggleItalic'); toggle('Underline','underline','toggleUnderline');
    toggle('Superscript','superscript','toggleSuperscript'); toggle('Subscript','subscript','toggleSubscript');
    toggle('Bulleted list','bulletList','toggleBulletList'); toggle('Numbered list','orderedList','toggleOrderedList');
    for (const alignment of ['left','center','right','justify']) button('Paragraph '+alignment,
      () => editor.chain().focus().setTextAlign(alignment).run(), () => editor.isActive({textAlign:alignment}));
    for (const [label,delta] of [['Decrease indent',-1],['Increase indent',1]]) button(label,
      () => editor.chain().focus().indentParagraph(delta).run(), null, () => editor.can().indentParagraph(delta));
    function numberInput(label,max) {
      const wrapper = document.createElement('label'); wrapper.className = 'tmp-case-table-size'; wrapper.textContent = label+' ';
      const input = document.createElement('input'); input.type='number'; input.min='1'; input.max=String(max); input.value='2';
      wrapper.append(input); toolbar.append(wrapper); return input;
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
    for (const value of ['left','center','right','justify']) button('Cell '+value,
      () => editor.chain().focus().setCellAttribute('cellAlign',value).run(),
      () => editor.isActive('tableCell',{cellAlign:value}) || editor.isActive('tableHeader',{cellAlign:value}), () => cellPosition() !== null);
    for (const value of ['top','middle','bottom']) button('Cell vertical '+value,
      () => editor.chain().focus().setCellAttribute('verticalAlign',value).run(),
      () => editor.isActive('tableCell',{verticalAlign:value}) || editor.isActive('tableHeader',{verticalAlign:value}), () => cellPosition() !== null);
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
    try {
      show(errors,[]); show(warnings,[]);
      const body = new FormData(); body.append('stimulus',currentContent()); body.append('input_format','html');
      body.append('csrfmiddlewaretoken',form.querySelector('[name=csrfmiddlewaretoken]').value);
      const response = await fetch(form.dataset.previewUrl,{method:'POST',body,credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      const payload = await response.json();
      if (!response.ok) { show(errors,payload.errors || ['Preview could not be generated.']); return; }
      preview.innerHTML=payload.html; show(warnings,payload.warnings || []); status.textContent='Server-authoritative Preview updated.';
      if (typeof window.renderMathInElement === 'function') window.renderMathInElement(preview,{delimiters:[{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],trust:false,throwOnError:false,strict:'error',maxSize:10,maxExpand:1000});
    } catch { show(errors,['Preview could not be generated. Your source is preserved.']); }
  });
  return {editor,currentContent};
}
const form = document.querySelector('[data-case-editor-form]');
if (form) mountCaseEditor(form);
