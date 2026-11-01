const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require(process.env.TMP_JSDOM_PATH || 'jsdom');
const script = readFileSync(join(__dirname, '../../static/js/faculty_sidebar.js'), 'utf8');
function setup({mobile = true, collapsed = false, locked = false, brokenStorage = false} = {}) {
  const dom = new JSDOM(`<nav class="faculty-topbar"><button id="faculty-sidebar-toggle">Menu</button></nav>
  <div id="faculty-layout" data-navigation-lock="${locked ? '1' : '0'}"><button id="faculty-sidebar-backdrop" hidden></button>
  <aside id="faculty-sidebar" tabindex="-1"><button id="faculty-sidebar-close">Close</button><a id="first-link" href="/faculty/">Dashboard</a><a id="last-link" href="/faculty/courses/">Classes</a></aside>
  <main class="faculty-main"><button>Background action</button></main></div><div class="faculty-utility-stack"></div>`, {url: 'https://test.local', runScripts: 'outside-only'});
  const {window} = dom;
  const query = {matches: mobile, listener: null, addEventListener(type, callback) { this.listener = callback; }};
  window.matchMedia = () => query;
  window.HTMLElement.prototype.getClientRects = function () { return [1]; };
  const storage = {value: collapsed ? '1' : '0', writes: 0, getItem() { return this.value; }, setItem(key, value) { this.value = value; this.writes++; }};
  Object.defineProperty(window, 'localStorage', {get() { if (brokenStorage) throw new Error('storage unavailable'); return storage; }});
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  const get = id => window.document.getElementById(id);
  return {dom, window, query, storage, get, resize(value) { query.matches = value; query.listener(); }, key(key, shiftKey = false) { window.document.dispatchEvent(new window.KeyboardEvent('keydown', {key, shiftKey, bubbles: true, cancelable: true})); }};
}

test('mobile defaults closed for either desktop preference', () => {
  for (const collapsed of [false, true]) {
    const s = setup({collapsed});
    assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(s.get('faculty-sidebar').inert, true);
    assert.equal(s.get('faculty-sidebar-backdrop').hidden, true);
    assert.equal(s.get('faculty-layout').classList.contains('sidebar-collapsed'), false);
    s.dom.window.close();
  }
});
test('hamburger opens modal drawer, backdrop and background scroll lock', () => {
  const s = setup(); s.get('faculty-sidebar-toggle').focus(); s.get('faculty-sidebar-toggle').click();
  assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(s.get('faculty-sidebar').getAttribute('aria-modal'), 'true');
  assert.equal(s.get('faculty-sidebar-backdrop').hidden, false);
  assert.equal(s.window.document.body.classList.contains('faculty-drawer-open'), true);
  assert.equal(s.window.document.querySelector('.faculty-main').inert, true);
  assert.equal(s.window.document.activeElement, s.get('faculty-sidebar-close'));
  assert.equal(s.storage.writes, 0); s.dom.window.close();
});
test('close button, backdrop and Escape each close and restore focus', () => {
  const s = setup();
  for (const action of [() => s.get('faculty-sidebar-close').click(), () => s.get('faculty-sidebar-backdrop').click(), () => s.key('Escape')]) {
    s.get('faculty-sidebar-toggle').focus(); s.get('faculty-sidebar-toggle').click(); action();
    assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(s.window.document.activeElement, s.get('faculty-sidebar-toggle'));
    assert.equal(s.window.document.body.classList.contains('faculty-drawer-open'), false);
    assert.equal(s.get('faculty-sidebar').inert, true);
  } s.dom.window.close();
});
test('Tab and Shift+Tab stay inside the open drawer', () => {
  const s = setup(); s.get('faculty-sidebar-toggle').click();
  s.get('last-link').focus(); s.key('Tab');
  assert.equal(s.window.document.activeElement, s.get('faculty-sidebar-close'));
  s.key('Tab', true); assert.equal(s.window.document.activeElement, s.get('last-link'));
  s.dom.window.close();
});
test('close restores the hamburger when the initial focus was the document body', () => {
  const s = setup(); s.get('faculty-sidebar-toggle').click(); s.get('faculty-sidebar-close').click();
  assert.equal(s.window.document.activeElement, s.get('faculty-sidebar-toggle'));
  s.dom.window.close();
});
test('viewport transitions clear mobile state and retain desktop preference', () => {
  const s = setup({mobile: false}); s.get('faculty-sidebar-toggle').click();
  assert.equal(s.storage.value, '1'); s.resize(true);
  assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'false');
  s.get('faculty-sidebar-toggle').focus(); s.get('faculty-sidebar-toggle').click(); s.resize(false);
  assert.equal(s.get('faculty-layout').classList.contains('sidebar-collapsed'), true);
  assert.equal(s.get('faculty-sidebar').inert, false);
  assert.equal(s.window.document.body.classList.contains('faculty-drawer-open'), false);
  assert.equal(s.get('faculty-sidebar-backdrop').hidden, true);
  s.resize(true); assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(s.storage.writes, 1); s.dom.window.close();
});
test('blocked storage does not prevent either mobile or desktop operation', () => {
  const s = setup({brokenStorage: true}); s.get('faculty-sidebar-toggle').click();
  assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'true');
  s.resize(false); s.get('faculty-sidebar-toggle').click();
  assert.equal(s.get('faculty-layout').classList.contains('sidebar-collapsed'), true);
  s.dom.window.close();
});
test('privacy/password navigation lock cannot be opened', () => {
  const s = setup({locked: true}); s.get('faculty-sidebar-toggle').click();
  assert.equal(s.get('faculty-sidebar-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(s.get('faculty-sidebar-backdrop').hidden, true); s.dom.window.close();
});
