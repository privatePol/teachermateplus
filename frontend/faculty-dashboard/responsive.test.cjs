// Offline Chrome layout checks against real Django-rendered synthetic fixtures.
// Set TMP_FACULTY_RESPONSIVE_EVIDENCE and optionally TMP_CHROME_BIN to run.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {writeFileSync, mkdtempSync, existsSync} = require('node:fs');
const {join} = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const root = process.env.TMP_FACULTY_RESPONSIVE_EVIDENCE;
const chrome = process.env.TMP_CHROME_BIN || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const unavailable = !root ? 'Set TMP_FACULTY_RESPONSIVE_EVIDENCE to generated Django fixtures'
  : !existsSync(chrome) ? 'Set TMP_CHROME_BIN to an installed Chrome executable' : false;
let processHandle, socket, nextId = 0;
const pending = new Map();
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Chrome timeout: ' + method)); }, 15000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params}));
  });
}
before(async () => {
  if (!root || !existsSync(chrome)) return;
  const profile = mkdtempSync(join(root, 'chrome-profile-'));
  processHandle = spawn(chrome, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--hide-scrollbars', 'about:blank'],
    {stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true});
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Chrome did not start')), 15000);
    let output = '';
    processHandle.stderr.on('data', data => {
      output += data;
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(new URL(match[1])); }
    });
    processHandle.once('error', error => { clearTimeout(timer); reject(error); });
  });
  const target = await (await fetch(`http://${endpoint.host}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); });
  socket.addEventListener('message', event => {
    const result = JSON.parse(event.data), item = pending.get(result.id);
    if (!item) return;
    clearTimeout(item.timer); pending.delete(result.id);
    if (result.error) item.reject(new Error(JSON.stringify(result.error))); else item.resolve(result.result);
  });
  await command('Page.enable');
});
after(() => { if (socket) socket.close(); if (processHandle) processHandle.kill(); });

async function browserChecks(width, variant) {
  const errors = [];
  function check(value, message) { if (!value) errors.push(message); }
  const q = selector => document.querySelector(selector);
  const style = element => getComputedStyle(element);
  const overflow = label => check(document.documentElement.scrollWidth <= innerWidth + 1, label + ': page overflow');
  check(innerWidth === width, 'unexpected viewport: ' + innerWidth);
  const stack = q('.faculty-utility-stack'), main = q('.faculty-main');
  check(style(stack).position === (width < 992 ? 'static' : 'fixed'), 'support position');
  if (width < 992) check(stack.getBoundingClientRect().top >= main.getBoundingClientRect().bottom - 1, 'support overlaps main');
  const disclosure = q('.faculty-utility-toggle');
  check((style(disclosure).display !== 'none') === (width < 768), 'phone disclosure breakpoint');
  if (width < 768) {
    check(style(q('.faculty-utility-actions')).display === 'none', 'phone starts collapsed');
    disclosure.click();
    check(disclosure.getAttribute('aria-expanded') === 'true', 'disclosure opens');
  }
  for (const selector of ['#faculty-feedback-open', '#faculty-quick-guide-link', '.privacy-chip']) {
    const element = q(selector), rect = element.getBoundingClientRect();
    check(rect.width > 0 && rect.left >= 0 && rect.right <= innerWidth + 1, 'support inaccessible: ' + selector);
  }
  overflow('support expanded');
  const table = q('.dashboard-grade-table'), scroll = q('.dashboard-table-scroll');
  check(table.tBodies[0].rows.length === 8, 'one row per class');
  for (const row of table.tBodies[0].rows) {
    const actions = row.cells[4].querySelector('.dashboard-inline-actions');
    check(style(actions).display === 'inline-flex' && style(actions).flexWrap === 'nowrap', 'inline actions');
    const links = actions.querySelectorAll('a');
    check(links.length >= 2 && Math.abs(links[0].getBoundingClientRect().top - links[1].getBoundingClientRect().top) < 1,
          'grade/performance buttons wrap');
  }
  check(style(scroll).overflowX === 'auto', 'internal table scroll');
  if (scroll.scrollWidth > scroll.clientWidth) {
    scroll.scrollLeft = 60;
    check(scroll.scrollLeft > 0, 'horizontal scrolling blocked');
    scroll.scrollLeft = 0;
  }
  const toggle = q('#faculty-sidebar-toggle'), sidebar = q('#faculty-sidebar');
  const close = q('#faculty-sidebar-close'), backdrop = q('#faculty-sidebar-backdrop');
  if (width < 992) {
    check(sidebar.inert && toggle.getAttribute('aria-expanded') === 'false', 'drawer starts closed');
    toggle.focus(); toggle.click();
    await new Promise(resolve => setTimeout(resolve, 250));
    check(document.activeElement === close, 'drawer opening focus: ' + document.activeElement.id);
    check(!backdrop.hidden && main.inert && stack.inert, 'drawer background/backdrop');
    check(style(document.body).overflow === 'hidden', 'drawer scroll lock');
    check(style(q('.faculty-sidebar-inner')).overflowY === 'auto', 'drawer internal scrolling');
    overflow('drawer open');
  }
  for (const selector of ['[data-tour-id="performance-trends"]', '[data-tour-id="activity-history"]']) {
    const link = q(selector);
    check(link && style(link).visibility === 'visible' && style(link).display !== 'none', 'navigation hidden: ' + selector);
  }
  const analytics = q('[data-menu-code="FACULTY_ANALYTICS"]');
  check(Boolean(analytics) === (variant !== 'denied'), 'analytics authorization');
  if (analytics) check(style(analytics).display !== 'none' && style(analytics).visibility === 'visible', 'analytics hidden');
  const trends = q('[data-tour-id="performance-trends"]');
  const shortcut = Array.from(document.querySelectorAll('.faculty-dashboard-actions a')).find(a => a.href === trends.href);
  check(shortcut && style(shortcut).display !== 'none', 'dashboard trends shortcut hidden');
  check(Boolean(q('[data-tour-id="at-risk-monitor"]')) === (variant !== 'feature-disabled'), 'feature flag visibility');
  if (width < 992) {
    const focusable = Array.from(sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex="0"]'))
      .filter(e => !e.hidden && e.getClientRects().length);
    const last = focusable[focusable.length - 1];
    last.focus(); document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}));
    check(document.activeElement === close, 'forward focus containment');
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
    check(document.activeElement === last, 'reverse focus containment');
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
    check(document.activeElement === toggle && backdrop.hidden && !main.inert && !stack.inert, 'Escape restores focus/background');
    for (const control of [backdrop, close]) { toggle.click(); control.click(); check(backdrop.hidden, 'close/backdrop'); }
    check(style(document.body).overflow !== 'hidden', 'scroll unlock');
    await new Promise(resolve => setTimeout(resolve, 250));
    check(style(sidebar).visibility === 'hidden', 'closed drawer visibility');
  }
  overflow('finished');
  window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'});
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return {width: innerWidth, variant, pageWidth: document.documentElement.scrollWidth,
          supportPosition: style(stack).position, rows: table.tBodies[0].rows.length, errors};
}

for (const width of [375, 390, 440, 767, 768, 992]) {
  for (const variant of ['authorized', 'denied', 'feature-disabled']) {
    test(`${variant} menus and layout at ${width}px`, {skip: unavailable}, async () => {
      const fixture = join(root, `${variant}.html`);
      assert.ok(existsSync(fixture), 'Generate Django fixtures first');
      await command('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: false});
      await command('Page.navigate', {url: pathToFileURL(fixture).href});
      for (let attempt = 0; attempt < 100; attempt++) {
        const state = await command('Runtime.evaluate', {expression: 'document.readyState', returnByValue: true});
        if (state.result.value === 'complete') break;
        await pause(50);
      }
      const result = await command('Runtime.evaluate', {expression: `(${browserChecks.toString()})(${width}, ${JSON.stringify(variant)})`,
        awaitPromise: true, returnByValue: true});
      assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
      const evidence = result.result.value;
      writeFileSync(join(root, `${variant}-${width}.json`), JSON.stringify(evidence, null, 2));
      const screenshot = await command('Page.captureScreenshot', {format: 'png'});
      writeFileSync(join(root, `${variant}-${width}.png`), Buffer.from(screenshot.data, 'base64'));
      if (variant === 'authorized') {
        await command('Runtime.evaluate', {expression: "window.scrollTo({top: 0, behavior: 'instant'}); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
          awaitPromise: true});
        const top = await command('Page.captureScreenshot', {format: 'png'});
        writeFileSync(join(root, `${variant}-${width}-top.png`), Buffer.from(top.data, 'base64'));
      }
      assert.deepEqual(evidence.errors, [], JSON.stringify(evidence));
    });
  }
}
