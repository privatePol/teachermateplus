// Clipboard adaptation is UX, never an alternative to the Python sanitizer.
const tags = new Set('p h3 h4 strong em u sup sub br ul ol li table caption thead tbody tfoot tr th td'.split(' '));
const blocks = new Set(['p', 'h3', 'h4']);
const alignments = ['left', 'center', 'right', 'justify'];
const classes = /^(tmp-align-(left|center|right|justify)|tmp-indent-[1-8]|tmp-valign-(top|middle|bottom)|tmp-preserve)$/;
export const preservationError = 'This Case could not be loaded without changing supported content. The original source is preserved. Do not save; recover the source or ask an administrator to inspect this Case.';

export function parse(html) {
  return new DOMParser().parseFromString(html, 'text/html').body;
}

function safeNode(node, doc) {
  if (node.nodeType === 3) return doc.createTextNode(node.textContent);
  if (node.nodeType !== 1) return doc.createDocumentFragment();
  const name = node.localName.toLowerCase();
  if (/^(img|svg|object|embed|iframe|.*:shape|.*:imagedata)$/.test(name)) throw new Error('Images and embedded objects are not supported.');
  if (/omath/i.test(name)) throw new Error('Native Word equations are not supported. Use TMP LaTeX or Unicode.');
  if (['script', 'style', 'form', 'input', 'button', 'select', 'textarea', 'meta', 'link'].includes(name)) return doc.createDocumentFragment();
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
  normalizeWordLists(body);
  const clean = document.createElement('div');
  for (const child of body.childNodes) clean.append(safeNode(child, document));
  // Only the raw Word clipboard path compacts Office spacer paragraphs.
  if (/\bmso-|\bMsoNormal|xmlns:w=/i.test(html)) {
    for (const p of clean.querySelectorAll('p')) {
      if (!p.textContent.replace(/\u00a0/g, ' ').trim() && !p.querySelector('table,ul,ol')) p.remove();
    }
  }
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
  let group = 0;
  for (const table of body.querySelectorAll('table')) {
    const captions = [...table.children].filter(el => el.localName === 'caption');
    if (captions.length > 1) throw new Error(preservationError);
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
      if (!['thead', 'tbody', 'tfoot'].includes(tag)) throw new Error(preservationError);
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
    const children = [];
    for (const child of [...node.childNodes].filter(child => !(child.nodeType === 3 && !child.textContent.trim() && ['body','table','thead','tbody','tfoot','tr','ul','ol'].includes(tag))).map(child => visit(child, activeMarks)).filter(x => x !== null).flat()) {
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
  if (semanticSignature(original) !== semanticSignature(serializeEditor(editor, {preserve: false}))) throw new Error(preservationError);
}
