import {Editor, Extension, Node, mergeAttributes} from '@tiptap/core';
import Document from '@tiptap/extension-document';
import Text from '@tiptap/extension-text';
import Paragraph from '@tiptap/extension-paragraph';
import Heading from '@tiptap/extension-heading';
import Bold from '@tiptap/extension-bold';
import Italic from '@tiptap/extension-italic';
import Underline from '@tiptap/extension-underline';
import Superscript from '@tiptap/extension-superscript';
import Subscript from '@tiptap/extension-subscript';
import {BulletList, OrderedList, ListItem, ListKeymap} from '@tiptap/extension-list';
import HardBreak from '@tiptap/extension-hard-break';
import TextAlign from '@tiptap/extension-text-align';
import {Table, TableRow, TableCell, TableHeader} from '@tiptap/extension-table';
import {UndoRedo, Gapcursor} from '@tiptap/extensions';
import {fixTables, inSameTable, pointsAtCell} from '@tiptap/pm/tables';
import {parse, prepareLegacy, assertCompatible} from './compatibility.js';

const classAttribute = (prefix, values) => ({
  default: null,
  parseHTML: el => values.find(value => el.classList.contains(prefix + value)) || null,
  renderHTML: attrs => ({})
});
function semanticAttribute(name, prefix, values) {
  return {...classAttribute(prefix, values), renderHTML: attrs => values.includes(String(attrs[name])) ? {class: prefix + attrs[name]} : {}};
}
const Alignment = TextAlign.extend({
  addGlobalAttributes() {
    return [{types: ['paragraph', 'heading'], attributes: {
      textAlign: semanticAttribute('textAlign', 'tmp-align-', ['left','center','right','justify'])
    }}];
  }
}).configure({types: ['paragraph', 'heading']});
const Formatting = Extension.create({
  name: 'tmpFormatting',
  addGlobalAttributes() {
    return [
      {types: ['paragraph','heading'], attributes: {
        indent: semanticAttribute('indent', 'tmp-indent-', ['1','2','3','4','5','6','7','8']),
        keepSpacing: {default: false, parseHTML: el => el.classList.contains('tmp-preserve'), renderHTML: attrs => attrs.keepSpacing ? {class:'tmp-preserve'} : {}}
      }},
      {types: ['tableCell','tableHeader'], attributes: {
        cellAlign: semanticAttribute('cellAlign','tmp-align-', ['left','center','right','justify']),
        verticalAlign: semanticAttribute('verticalAlign','tmp-valign-', ['top','middle','bottom']),
        scope: {default:null, parseHTML: el => ['row','col','rowgroup','colgroup'].includes(el.getAttribute('scope')) ? el.getAttribute('scope') : null,
          renderHTML: attrs => attrs.scope ? {scope:attrs.scope} : {}}
      }}
    ];
  },
  addCommands() {
    return {
      indentParagraph: delta => ({tr, state, dispatch}) => {
        let changed = false;
        state.doc.nodesBetween(state.selection.from, state.selection.to, (node, pos) => {
          if (!['paragraph','heading'].includes(node.type.name)) return;
          const next = Math.min(8, Math.max(0, Number(node.attrs.indent || 0) + delta));
          if (next === Number(node.attrs.indent || 0)) return;
          changed = true;
          if (dispatch) tr.setNodeMarkup(pos, undefined, {...node.attrs, indent:next ? String(next) : null});
        });
        return changed;
      },
      clearTextFormatting: () => ({chain}) => chain().unsetAllMarks().unsetTextAlign()
        .resetAttributes('paragraph', ['indent']).resetAttributes('heading', ['indent']).run()
    };
  }
});
const GridRow = TableRow.extend({addAttributes() { return {
  ...this.parent?.(),
  rowGroup: {default: null, parseHTML: el => el.getAttribute('data-tmp-group'), renderHTML: attrs => attrs.rowGroup ? {'data-tmp-group':attrs.rowGroup} : {}}
}; }});
const GridTable = Table.extend({
  parseHTML() { return [{tag:'table', getAttrs: el => el.hasAttribute('data-tmp-empty') ? false : null}]; },
  addAttributes() { return {
    ...this.parent?.(),
    captionHtml: {default: null, parseHTML: el => el.getAttribute('data-tmp-caption'), renderHTML: attrs => attrs.captionHtml === null ? {} : {'data-tmp-caption':attrs.captionHtml}}
  }; },
  renderHTML({node, HTMLAttributes}) {
    const children = [];
    if (node.attrs.captionHtml !== null) {
      const caption = document.createElement('caption'); caption.contentEditable = 'false';
      caption.append(...parse(node.attrs.captionHtml).childNodes); children.push(caption);
    }
    return ['table', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), ...children, ['tbody',0]];
  }
}).configure({resizable:false});
// Preserve a surviving empty legacy table without inventing a row/cell.
const EmptyTable = Node.create({
  name:'emptyTable', group:'block', atom:true, priority:50,
  addAttributes: () => ({captionHtml: {default:null, parseHTML: el => el.getAttribute('data-tmp-caption'), renderHTML: attrs => attrs.captionHtml === null ? {} : {'data-tmp-caption':attrs.captionHtml}}}),
  parseHTML: () => [{tag:'table[data-tmp-empty="true"]'}],
  renderHTML({node, HTMLAttributes}) {
    const children = [];
    if (node.attrs.captionHtml !== null) {
      const caption = document.createElement('caption'); caption.contentEditable = 'false';
      caption.append(...parse(node.attrs.captionHtml).childNodes); children.push(caption);
    }
    return ['table', {...HTMLAttributes, 'data-tmp-empty':'true'}, ...children];
  }
});

export function cellsShareTable(editor, anchor, head) {
  if (anchor === null || head === null) return false;
  const first = editor.state.doc.resolve(anchor), last = editor.state.doc.resolve(head);
  return pointsAtCell(first) && pointsAtCell(last) && inSameTable(first,last);
}

export function createCaseEditor(element, html, options = {}) {
  const editor = new Editor({
    element, injectCSS:false, enableInputRules:false, enablePasteRules:false,
    extensions:[Document, Text, Paragraph, Heading.configure({levels:[3,4]}), Bold, Italic, Underline,
      Superscript, Subscript, BulletList, OrderedList, ListItem, ListKeymap, HardBreak, Alignment,
      Formatting, GridTable, GridRow, TableCell, TableHeader, EmptyTable, UndoRedo, Gapcursor],
    content: prepareLegacy(html), parseOptions:{preserveWhitespace:'full'},
    editorProps: {attributes: {role:'textbox','aria-multiline':'true','aria-label':'Case narrative'}, ...options.editorProps},
    ...options
  });
  try {
    if (html.trim()) assertCompatible(html, editor);
    // Do not let the upstream engine silently repair an accepted legacy grid
    // on the first edit (for example filling ragged rows or shrinking rowspans).
    if (fixTables(editor.state)?.docChanged) throw new Error('This table would require a geometry change to edit safely. The original source is preserved.');
  }
  catch (error) { editor.destroy(); throw error; }
  return editor;
}
