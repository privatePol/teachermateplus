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
import {closeHistory} from '@tiptap/pm/history';
import {fixTables, inSameTable, pointsAtCell, CellSelection, cellAround, selectedRect,
  TableMap, mergeCells, splitCell} from '@tiptap/pm/tables';
import {parse, prepareLegacy, assertCompatible, compatibilityError} from './compatibility.js';

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
        accountingRule: semanticAttribute('accountingRule','tmp-rule-', ['single','double']),
        cellAlign: semanticAttribute('cellAlign','tmp-align-', ['left','center','right','justify']),
        verticalAlign: semanticAttribute('verticalAlign','tmp-valign-', ['top','middle','bottom']),
        scope: {default:null, parseHTML: el => ['row','col','rowgroup','colgroup'].includes(el.getAttribute('scope')) ? el.getAttribute('scope') : null,
          renderHTML: attrs => attrs.scope ? {scope:attrs.scope} : {}}
      }}
    ];
  },
  addCommands() {
    return {
      setAccountingRule: value => ({state,tr,dispatch}) => {
        if (![null,'single','double'].includes(value)) return false;
        const cells=[];
        if (state.selection instanceof CellSelection) state.selection.forEachCell((node,pos)=>cells.push({node,pos}));
        else { const cell=cellAround(state.selection.$from); if (cell) cells.push({node:cell.nodeAfter,pos:cell.pos}); }
        const changed=cells.filter(({node})=>node.attrs.accountingRule!==value);
        if (dispatch && changed.length) closeHistory(tr);
        if (dispatch) for (const {node,pos} of changed) tr.setNodeMarkup(pos,null,{...node.attrs,accountingRule:value});
        return changed.length>0;
      },
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
  addCommands() {
    return {
      ...this.parent?.(),
      mergeCells: () => ({state,dispatch}) => {
        if (!(state.selection instanceof CellSelection) || !mergeCells(state)) return false;
        const rect=selectedRect(state), seen=new Set(), bottom=new Set();
        let ambiguous=false;
        for (let row=rect.top;row<rect.bottom;row++) for (let col=rect.left;col<rect.right;col++) {
          const pos=rect.map.map[row*rect.map.width+col]; if (seen.has(pos)) continue; seen.add(pos);
          const cell=rect.table.nodeAt(pos), edge=rect.map.findCell(pos).bottom;
          if (edge===rect.bottom) bottom.add(cell.attrs.accountingRule);
          else if (cell.attrs.accountingRule) ambiguous=true;
        }
        if (ambiguous || bottom.size>1) {
          if (!dispatch) return true;
          const error=new Error('Merge would lose or conflict with accounting rules. Remove the affected rules first, then merge and apply the required bottom rule.');
          error.accountingConflict=true; throw error;
        }
        const rule=[...bottom][0] || null;
        return mergeCells(state,dispatch ? transaction => {
          const cell=transaction.selection.$anchorCell;
          transaction.setNodeMarkup(cell.pos,null,{...cell.nodeAfter.attrs,accountingRule:rule});
          dispatch(transaction);
        } : undefined);
      },
      splitCell: () => ({state,dispatch}) => {
        if (!splitCell(state)) return false;
        const rect=selectedRect(state);
        const cell=state.selection instanceof CellSelection ? state.selection.$anchorCell : cellAround(state.selection.$from);
        const rule=cell?.nodeAfter.attrs.accountingRule;
        return splitCell(state,dispatch ? transaction => {
          if (rule) {
            const table=transaction.doc.nodeAt(rect.tableStart-1), map=TableMap.get(table), seen=new Set();
            for (let row=rect.top;row<rect.bottom;row++) for (let col=rect.left;col<rect.right;col++) {
              const offset=map.map[row*map.width+col]; if (seen.has(offset)) continue; seen.add(offset);
              const node=table.nodeAt(offset);
              transaction.setNodeMarkup(rect.tableStart+offset,null,{
                ...node.attrs,accountingRule:map.findCell(offset).bottom===rect.bottom ? rule : null
              });
            }
          }
          dispatch(transaction);
        } : undefined);
      }
    };
  },
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
    if (fixTables(editor.state)?.docChanged) throw compatibilityError('geometry','geometry','This table would require a geometry change to edit safely. The original source is preserved.');
  }
  catch (error) { editor.destroy(); throw error; }
  return editor;
}
