// Clipboard adaptation is UX, never an alternative to the Python sanitizer.
const tags = new Set('p h3 h4 strong em u sup sub br ul ol li table caption thead tbody tfoot tr th td'.split(' '));
const blocks = new Set(['p', 'h3', 'h4']);
const alignments = ['left', 'center', 'right', 'justify'];
const classes = /^(tmp-align-(left|center|right|justify)|tmp-indent-[1-8]|tmp-valign-(top|middle|bottom)|tmp-preserve)$/;
export const preservationError = 'This Case could not be loaded without changing supported content. The original source is preserved. Do not save; recover the source or ask an administrator to inspect this Case.';

export function compatibilityError(phase, category, message = preservationError) {
  const error = new Error(message);
  const phases = ['normalize','prepare','compare','serialize','geometry','insert'];
  const categories = ['text','whitespace','paragraph','attribute','caption','row_group','geometry'];
  error.diagnosticCode = `CASE_${phases.includes(phase) ? phase.toUpperCase() : 'COMPARE'}_${categories.includes(category) ? category.toUpperCase() : 'ATTRIBUTE'}`;
  return error;
}

// Explicit bottom shorthands only. Ordinary all-edge solid grid borders are
// not accounting rules. Never copy source CSS into the canonical document.
function rejectBorder() { throw compatibilityError('normalize','attribute',
    'This border cannot be preserved as an accounting rule. Use explicit single/double bottom rules on amount cells; paragraph, stylesheet and conflicting rules are not supported.'); };
// Inspection copy only: mask strings and replace comments with token boundaries.
// Never concatenate partial identifiers or interpret escapes as property names.
function inspectBorderCSS(css) {
  const out=[]; let quote=null;
  for (let i=0;i<css.length;i++) {
    const c=css[i];
    if (quote) {
      if (c==='\\') { if (++i>=css.length) rejectBorder(); out.push('  '); }
      else if (c===quote) { out.push(c); quote=null; }
      else out.push(' ');
    } else if (c==='"' || c==="'") { quote=c; out.push(c); }
    else if (c==='/' && css[i+1]==='*') {
      const end=css.indexOf('*/',i+2); if (end<0) rejectBorder();
      if (/[\w-]/.test(css[i-1] || '') && /[\w-]/.test(css[end+2] || '')) rejectBorder();
      out.push(' '); i=end+1;
    } else { if (c==='\\') rejectBorder(); out.push(c); }
  }
  if (quote) rejectBorder();
  return out.join('');
}
export function accountingRule(node) {
  const reject=rejectBorder;
  const selected = [...node.classList].filter(c => /^tmp-rule-/.test(c));
  if (selected.some(c => !['tmp-rule-single','tmp-rule-double'].includes(c)) || selected.length > 1) reject();
  if (selected.length && !['td','th'].includes(node.localName)) reject();
  let rule = selected[0]?.replace('tmp-rule-','') || null;
  const declarations = inspectBorderCSS(node.getAttribute('style') || '').toLowerCase().split(';')
    .filter(part=>part.includes(':'))
    .map(part => { const index=part.indexOf(':'); return [part.slice(0,index).trim(),part.slice(index+1).trim()]; })
    .filter(([key]) => /^(?:mso-)?border/.test(key));
  const bottom = declarations.filter(([key]) => key.includes('bottom'));
  if (declarations.some(([,value])=>value.includes('!'))) reject();
  if (declarations.some(([key,value]) => !key.includes('bottom') && /\b(double|dashed|dotted|groove|ridge|hidden)\b/.test(value))) reject();
  if (!bottom.length) return rule;
  if (bottom.some(([key])=>!['border-bottom','mso-border-bottom-alt'].includes(key))) reject();
  if (!selected.length && bottom.every(([,value])=>/^(none|0(?:px|pt)?)$/.test(value)) &&
      declarations.filter(([key])=>['border','mso-border-alt'].includes(key))
        .every(([,value])=>/^(none|0(?:px|pt)?)$/.test(value))) return null;
  if (!['td','th'].includes(node.localName)) reject();
  const parseBorder = value => {
    if (/^(none|0(?:px|pt)?)$/.test(value)) return null;
    const tokens=value.split(/\s+/), kind=tokens.find(t=>['none','solid','double'].includes(t));
    const width=tokens.find(t=>/^(?:\d+(?:\.\d+)?|\.\d+)(pt|px)$/.test(t));
    const color=tokens.find(t=>['black','windowtext','#000','#000000'].includes(t));
    if (tokens.length!==3 || !kind || !width || !color || parseFloat(width)>6) reject();
    if (kind==='none' || parseFloat(width)===0) return null;
    return kind==='solid' ? 'single' : 'double';
  };
  let explicit;
  for (const [key,value] of bottom) {
    if (!['border-bottom','mso-border-bottom-alt'].includes(key)) reject();
    const parsed=parseBorder(value);
    if (explicit !== undefined && explicit !== parsed) reject();
    explicit=parsed;
  }
  if (selected.length && rule !== explicit) reject();
  // An explicit bottom duplicating a complete solid grid is not a total rule.
  const grid=declarations.find(([key]) => ['border','mso-border-alt'].includes(key));
  const normalizedWidth = token => {
    const [whole,fraction='']=token.slice(0,-2).split('.'), tail=fraction.replace(/0+$/,'');
    return (whole.replace(/^0+/,'') || '0')+(tail ? '.'+tail : '')+token.slice(-2);
  };
  const normalizedTokens = value => value.split(/\s+/).map(token=>['windowtext','#000','#000000'].includes(token) ? 'black' :
    /^(?:\d+(?:\.\d+)?|\.\d+)(pt|px)$/.test(token) ? normalizedWidth(token) : token).sort().join(' ');
  const same = value => normalizedTokens(value)===normalizedTokens(bottom[0][1]);
  // A later all-edge shorthand can erase/change a bottom rule. Accept only
  // equivalent grids; ambiguous CSS/MSO overrides fail before any insertion.
  const firstBottom=declarations.findIndex(([key])=>key.includes('bottom'));
  if (declarations.some(([key,value],index)=>['border','mso-border-alt'].includes(key) &&
      index>firstBottom && !same(value))) reject();
  const grids=declarations.filter(([key])=>['border','mso-border-alt'].includes(key));
  const seenGrids=new Map();
  for (const [key,value] of grids) {
    const normalized=normalizedTokens(value);
    if (seenGrids.has(key) && seenGrids.get(key)!==normalized) reject();
    seenGrids.set(key,normalized);
  }
  const completeGrid=(grid && same(grid[1])) || ['top','left','right'].every(side=>
    declarations.some(([key,value])=>["border-"+side,"mso-border-"+side+"-alt"].includes(key) && same(value)));
  if (completeGrid && explicit === 'single' && !selected.length) return null;
  rule=explicit;
  return rule;
}

export function parse(html) {
  return new DOMParser().parseFromString(html, 'text/html').body;
}

function blockOnlyCell(node) {
  const cellBlocks = ['p','h3','h4','table','ul','ol'];
  return ['td','th'].includes(node.localName) && node.children.length > 0 &&
    [...node.childNodes].every(child => child.nodeType === 8 ||
      (child.nodeType === 3 && /^[\t\n\r ]*$/.test(child.textContent)) ||
      (child.nodeType === 1 && cellBlocks.includes(child.localName)));
}

function safeNode(node, doc) {
  if (node.nodeType === 3) return doc.createTextNode(node.textContent);
  if (node.nodeType !== 1) return doc.createDocumentFragment();
  const name = node.localName.toLowerCase();
  if (/^(img|svg|object|embed|iframe|.*:shape|.*:imagedata)$/.test(name)) throw new Error('Images and embedded objects are not supported.');
  if (/omath/i.test(name)) throw new Error('Native Word equations are not supported. Use TMP LaTeX or Unicode.');
  if (['script', 'style', 'form', 'input', 'button', 'select', 'textarea', 'meta', 'link'].includes(name)) return doc.createDocumentFragment();
  const bottomRule = accountingRule(node);
  let names = [];
  const tag = ({b: 'strong', i: 'em', div: 'p'})[name] || name;
  if (tags.has(tag)) names = [tag];
  if (name === 'div' && node.querySelector('p,div,table,ul,ol,h3,h4')) names = [];
  if (name === 'span') {
    if (/^(bold|[6-9]00)$/.test(node.style.fontWeight)) names.push('strong');
    if (node.style.fontStyle === 'italic') names.push('em');
    if (/underline/.test(node.style.textDecoration + node.style.textDecorationLine)) names.push('u');
    if (node.style.verticalAlign === 'super') names.push('sup');
    if (node.style.verticalAlign === 'sub') names.push('sub');
  }
  const fragment = doc.createDocumentFragment();
  let target = fragment;
  for (const name of names) { const el = doc.createElement(name); target.append(el); target = el; }
  if (target.nodeType === 1) {
    for (const attr of ['rowspan', 'colspan', 'start', 'scope']) {
      if ((['td', 'th'].includes(tag) && ['rowspan', 'colspan'].includes(attr)) || (tag === 'ol' && attr === 'start') || (tag === 'th' && attr === 'scope')) {
        if (node.hasAttribute(attr)) target.setAttribute(attr, node.getAttribute(attr));
      }
    }
    if (blocks.has(tag) || ['th', 'td'].includes(tag)) {
      const approved = [...node.classList].filter(value => classes.test(value));
      if (!approved.some(value => value.startsWith('tmp-align-')) && alignments.includes(node.style.textAlign)) approved.push('tmp-align-' + node.style.textAlign);
      if (['th', 'td'].includes(tag) && ['top', 'middle', 'bottom'].includes(node.style.verticalAlign)) approved.push('tmp-valign-' + node.style.verticalAlign);
      if (bottomRule) approved.push('tmp-rule-' + bottomRule);
      if (approved.length) target.className = [...new Set(approved)].join(' ');
    }
  }
  for (const child of node.childNodes) target.append(safeNode(child, doc));
  return fragment;
}

export function normalizeClipboard(html, text = '') {
  if (html.length > 100000 || new TextEncoder().encode(html || text).length > 200000 || text.length > 100000) throw new Error('Pasted Case exceeds the request limit.');
  if (!html) {
    const holder = document.createElement('div');
    for (const line of text.replace(/\r\n?/g, '\n').split('\n')) {
      const p = document.createElement('p'); p.className = 'tmp-preserve'; p.textContent = line; holder.append(p);
    }
    return holder.innerHTML;
  }
  // Reject resource-bearing elements before even building an inert clipboard DOM.
  if (/<(?:img|svg|object|embed|iframe|[\w-]+:shape|[\w-]+:imagedata)\b/i.test(html)) throw new Error('Images and embedded objects are not supported.');
  const body = parse(html);
  // Word may place CSS in the document head. Do not silently discard a rule
  // requiring cascade evaluation; ordinary solid grid styles remain eligible.
  const stylesheetRule=[...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].some(([,css])=>
    [...inspectBorderCSS(css).matchAll(/((?:mso-)?border[\w-]*)\s*:\s*([^;{}]+)/gi)].some(([,key,value])=>
      value.includes('!') || (key.toLowerCase().includes('bottom') && !/^(none|0(?:px|pt)?)$/i.test(value.trim())) ||
      /\b(double|dashed|dotted|groove|ridge|hidden)\b/i.test(value)));
  if (stylesheetRule) {
    throw compatibilityError('normalize','attribute','Stylesheet-defined accounting borders are not supported. Apply explicit bottom rules to amount cells in Word or TMP.');
  }
  normalizeWordLists(body);
  const clean = document.createElement('div');
  for (const child of body.childNodes) clean.append(safeNode(child, document));
  // A Word class alone cannot distinguish a spacer from an intentional blank.
  // Preserve paragraph nodes here; safe block-cell boundary indentation is
  // still compacted by prepareLegacy, not by deleting uncertain paragraphs.
  for (const p of clean.querySelectorAll('p,h3,h4')) p.classList.add('tmp-preserve');
  return clean.innerHTML;
}

function normalizeWordLists(body) {
  const unsupported = () => { throw new Error('This Word list uses unsupported numbering. Paste the affected list as text, then apply Bullets or Numbered list in TMP. No content was inserted.'); };
  for (const parent of [body,...body.querySelectorAll('td,th,div')]) {
    let stack=[], identity=null;
    for (const paragraph of [...parent.children]) {
      const rule=/mso-list\s*:\s*l(\d+)\s+level(\d+)\s+lfo(\d+)/i.exec(paragraph.getAttribute('style') || '');
      if (!rule) { stack=[]; identity=null; continue; }
      if (paragraph.localName !== 'p') unsupported();
      const level=Number(rule[2]), id=rule[1]+':'+rule[3];
      if (level<1 || level>8) unsupported();
      const marker=[...paragraph.querySelectorAll('span')].find(el=>/mso-list\s*:\s*ignore/i.test(el.getAttribute('style')||''));
      if (!marker) unsupported();
      const label=marker.textContent.trim();
      const number=/^(\d+)[.)]$/.exec(label);
      const kind=number ? 'ol' : /^[•·o●▪]$/.test(label) ? 'ul' : null;
      if (!kind || (number && (Number(number[1])<1 || Number(number[1])>10000))) unsupported();
      if (identity !== id) { stack=[]; identity=id; }
      if (level>stack.length+1) unsupported();
      stack=stack.slice(0,level);
      let entry=stack[level-1];
      if (!entry || entry.list.localName !== kind || (number && entry.next !== Number(number[1]))) {
        const list=document.createElement(kind);
        if (number && number[1] !== '1') list.setAttribute('start',number[1]);
        if (level===1) parent.insertBefore(list,paragraph);
        else { if (!stack[level-2]?.last) unsupported(); stack[level-2].last.append(list); }
        entry={list,last:null,next:number ? Number(number[1]) : null}; stack[level-1]=entry;
      }
      marker.remove();
      const li=document.createElement('li'); entry.list.append(li); li.append(paragraph);
      entry.last=li; if (number) entry.next++;
      paragraph.setAttribute('style', (paragraph.getAttribute('style') || '').replace(/mso-list\s*:[^;]*(;|$)/gi, ''));
    }
  }
  if ([...body.querySelectorAll('[style]')].some(el=>/mso-list\s*:/i.test(el.getAttribute('style')))) unsupported();
}

// The ProseMirror grid remains tableRow+. Group provenance belongs to rows,
// not a new row-group node that would invalidate the established table engine.
export function prepareLegacy(html) {
  const body = parse(html);
  // Remove only the same non-semantic boundaries allowed by the comparison,
  // before the schema parser can wrap indentation in invented paragraphs.
  for (const cell of body.querySelectorAll('td,th')) {
    if (blockOnlyCell(cell)) for (const child of [...cell.childNodes]) {
      if (child.nodeType === 3 && /^[\t\n\r ]*$/.test(child.textContent)) child.remove();
    }
  }
  let group = 0;
  for (const table of body.querySelectorAll('table')) {
    const captions = [...table.children].filter(el => el.localName === 'caption');
    if (captions.length > 1) throw compatibilityError('prepare','caption');
    if (captions.length) {
      table.setAttribute('data-tmp-caption', captions[0].innerHTML);
      captions[0].remove();
    }
    for (const section of [...table.children]) {
      if (!['thead', 'tbody', 'tfoot'].includes(section.localName)) continue;
      const key = `${section.localName}:${++group}`;
      for (const row of section.children) row.setAttribute('data-tmp-group', key);
    }
    if (!table.rows.length) table.setAttribute('data-tmp-empty', 'true');
  }
  return body.innerHTML;
}

export function serializeEditor(editor, {preserve = true} = {}) {
  const body = parse(editor.getHTML());
  // Inner tables first: never absorb nested rows into a parent's group.
  for (const table of [...body.querySelectorAll('table')].reverse()) {
    const caption = table.getAttribute('data-tmp-caption');
    const rows = [...table.rows];
    table.replaceChildren();
    if (caption !== null) {
      const node = document.createElement('caption');
      node.append(...parse(caption).childNodes); table.append(node);
    }
    let previous = null, container = null;
    for (const row of rows) {
      const key = row.getAttribute('data-tmp-group') || 'tbody:new';
      const tag = key.split(':')[0];
      if (!['thead', 'tbody', 'tfoot'].includes(tag)) throw compatibilityError('serialize','row_group');
      if (previous !== key) { container = document.createElement(tag); table.append(container); previous = key; }
      row.removeAttribute('data-tmp-group'); container.append(row);
    }
    table.removeAttribute('data-tmp-caption'); table.removeAttribute('data-tmp-empty');
  }
  for (const el of body.querySelectorAll('*')) {
    if (preserve && blocks.has(el.localName)) el.classList.add('tmp-preserve');
    el.removeAttribute('style'); el.removeAttribute('colwidth');
    if (el.getAttribute('class') === '') el.removeAttribute('class');
    for (const name of ['rowspan', 'colspan', 'start']) if (el.getAttribute(name) === '1') el.removeAttribute(name);
  }
  return body.innerHTML;
}

// Compare semantic shape, allowing only required anonymous p/tbody wrappers.
// Caption text, scope, groups, nested geometry, mark sets and whitespace remain
// represented. A failed comparison disables Save instead of silently adapting.
export function semanticSignature(html) {
  const body = parse(html);
  const marks = new Set(['strong','em','u','sup','sub']);
  const visit = (node, activeMarks = []) => {
    if (node.nodeType === 3) return [['#text', [...new Set(activeMarks)].sort(), node.textContent]];
    if (node.nodeType !== 1) return null;
    const tag = node.localName;
    if (marks.has(tag)) return [...node.childNodes].map(child => visit(child, [...activeMarks,tag])).filter(x => x !== null).flat();
    const attrs = [...node.attributes].filter(a => !(a.name === 'class' && !a.value) && !(['rowspan', 'colspan', 'start'].includes(a.name) && a.value === '1'));
    // HTML indentation around an exclusively block-based cell is not authored
    // spacing. Never ignore NBSP, inline separators or mixed direct cell text.
    const ignoreCellBoundaries = blockOnlyCell(node);
    const children = [];
    for (const child of [...node.childNodes].filter(child => !(child.nodeType === 3 &&
      ((ignoreCellBoundaries && /^[\t\n\r ]*$/.test(child.textContent)) ||
       (!child.textContent.trim() && ['body','table','thead','tbody','tfoot','tr','ul','ol'].includes(tag)))))
      .map(child => visit(child, activeMarks)).filter(x => x !== null).flat()) {
      const last = children.at(-1);
      if (child[0] === '#text' && last?.[0] === '#text' && JSON.stringify(child[1]) === JSON.stringify(last[1])) last[2] += child[2];
      else children.push(child);
    }
    const anonymousCellParagraph = tag === 'p' && !attrs.length && ['td','th','li'].includes(node.parentElement?.localName) && [...node.parentElement.children].filter(el => el.localName === 'p').length === 1;
    if (tag === 'body' || (tag === 'tbody' && !attrs.length) || anonymousCellParagraph) return children;
    return [[tag, attrs.map(a => [a.name, a.name === 'class' ? a.value.split(/\s+/).sort().join(' ') : a.value]).sort(), children]];
  };
  return JSON.stringify(visit(body));
}

export function assertCompatible(original, editor) {
  const left=JSON.parse(semanticSignature(original));
  const right=JSON.parse(semanticSignature(serializeEditor(editor, {preserve: false})));
  const categoryFor = tags => tags.includes('caption') ? 'caption' :
    tags.some(t=>['thead','tbody','tfoot'].includes(t)) ? 'row_group' :
    tags.some(t=>['p','h3','h4','br'].includes(t)) ? 'paragraph' :
    tags.some(t=>['table','tr','td','th'].includes(t)) ? 'geometry' : 'text';
  function difference(a,b) {
    for (let i=0;i<Math.max(a.length,b.length);i++) {
      const x=a[i],y=b[i];
      if (!x || !y || x[0]!==y[0]) return categoryFor([x?.[0],y?.[0]]);
      if (JSON.stringify(x[1])!==JSON.stringify(y[1])) return 'attribute';
      if (x[0]==='#text') {
        if (x[2]!==y[2]) return x[2].replace(/\s/g,'')===y[2].replace(/\s/g,'') ? 'whitespace' : 'text';
      } else { const found=difference(x[2],y[2]); if (found) return found; }
    }
    return null;
  }
  const category=difference(left,right);
  if (category) throw compatibilityError('compare',category);
}
