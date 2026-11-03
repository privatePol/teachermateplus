// Real Bootstrap collapse, form ownership and responsive UI on Django-rendered fixtures.
// TMP_CORRECTION_UI_EVIDENCE contains configuration/dedicated/invalid.html and Bootstrap 5.3.3 assets.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync, writeFileSync, mkdtempSync, existsSync} = require('node:fs');
const {join} = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const root = process.env.TMP_CORRECTION_UI_EVIDENCE;
const chrome = process.env.TMP_CHROME_BIN || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const skip = !root ? 'Set TMP_CORRECTION_UI_EVIDENCE to generated Django fixtures'
  : !existsSync(chrome) ? 'Chrome is unavailable' : false;
let child, socket, serial = 0;
const pending = new Map();
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error('Chrome timeout: ' + method));}, 15000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await command('Runtime.evaluate', {expression, returnByValue: true});
  assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
before(async () => {
  if (skip) return;
  const css = readFileSync(join(root, 'bootstrap.min.css'), 'utf8');
  const bootstrap = readFileSync(join(root, 'bootstrap.bundle.min.js'), 'utf8');
  const featureCss = readFileSync(join(__dirname, '../../static/admin_portal/feature_settings.css'), 'utf8');
  for (const page of ['configuration', 'dedicated', 'invalid']) {
    let html = readFileSync(join(root, page + '.html'), 'utf8')
      .replace(/<link href="https:\/\/cdn\.jsdelivr\.net\/[^>]+>/g, () => '<style>' + css + '</style>')
      .replace(/<script src="https:\/\/cdn\.jsdelivr\.net\/[^>]+><\/script>/g, () => '<script>' + bootstrap + '</script>')
      .replace(/<link rel="stylesheet" href="\/static\/admin_portal\/feature_settings.css">/, () => '<style>' + featureCss + '</style>');
    // Keep the page's actual inline scripts; other external assets are outside this focused fixture.
    html = html.replace(/<script src="[^\"]+"[^>]*><\/script>/g, '');
    writeFileSync(join(root, page + '-offline.html'), html);
  }
  child = spawn(chrome, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--user-data-dir=' + mkdtempSync(join(root, 'chrome-profile-')), '--remote-debugging-port=0', 'about:blank'],
    {stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true});
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Chrome did not start')), 15000);
    let output = '';
    child.stderr.on('data', data => {
      output += data;
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {clearTimeout(timer); resolve(new URL(match[1]));}
    });
    child.once('error', error => {clearTimeout(timer); reject(error);});
  });
  const target = await (await fetch(`http://${endpoint.host}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  socket.addEventListener('message', event => {
    const response = JSON.parse(event.data), item = pending.get(response.id);
    if (response.method === 'Runtime.exceptionThrown') console.error('Fixture script exception:', JSON.stringify(response.params.exceptionDetails));
    if (!item) return;
    clearTimeout(item.timer); pending.delete(response.id);
    if (response.error) item.reject(new Error(JSON.stringify(response.error))); else item.resolve(response.result);
  });
  await command('Page.enable');
  await command('Runtime.enable');
  await command('Page.bringToFront');
  writeFileSync(join(root, 'chrome-version.json'), JSON.stringify(await command('Browser.getVersion'), null, 2));
});
after(() => {if (socket) socket.close(); if (child) child.kill();});
async function open(page, width, hash = '') {
  await command('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: false});
  await command('Page.navigate', {url: 'about:blank'});
  await command('Page.navigate', {url: pathToFileURL(join(root, page + '-offline.html')).href + hash});
  for (let i = 0; i < 60; i++) {
    if (await evaluate('document.readyState === "complete" && !!document.querySelector(".feature-settings-form")')) break;
    await pause(50);
  }
  await pause(450);
}
async function screenshot(name, selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start',behavior:'instant'})`);
  await pause(100);
  const {data} = await command('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  writeFileSync(join(root, name + '.png'), Buffer.from(data, 'base64'));
}
for (const width of [375, 768, 992, 1440]) {
  test('configuration card, keyboard, independent save and responsive layout at ' + width, {skip}, async () => {
    await open('configuration', width);
    assert.equal(await evaluate('document.querySelectorAll(".settings-card-grid").length'), 1);
    assert.equal(await evaluate('getComputedStyle(document.querySelector(".settings-card-grid")).gridTemplateColumns.split(" ").length'), width >= 992 ? 3 : 1);
    assert.equal(await evaluate('document.querySelector("#feature-card-correction-reminders").classList.contains("show")'), false);
    await evaluate('document.querySelector("#correction-reminders-card .settings-card-toggle").focus()');
    assert.equal(await evaluate('document.activeElement.getAttribute("aria-controls")'), 'feature-card-correction-reminders');
    await command('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13});
    await command('Input.dispatchKeyEvent', {type: 'char', text: '\r', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13});
    await command('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13});
    await pause(450);
    const expanded = await evaluate(`(() => {
      const q=s=>document.querySelector(s), card=q('#correction-reminders-card'), header=card.querySelector('.settings-card-toggle');
      const panel=q('#feature-card-correction-reminders'), grid=q('.settings-card-grid');
      const errors=[], check=(ok,label)=>{if(!ok)errors.push(label);};
      check(card.classList.contains('is-expanded') && panel.classList.contains('show'),'Bootstrap expansion');
      check(header.getAttribute('aria-expanded')==='true','expanded ARIA');
      check(getComputedStyle(header).backgroundImage.includes('linear-gradient'),'colored header');
      check(getComputedStyle(card).borderRadius==='16px','shared rounded corners');
      check(Math.abs(card.getBoundingClientRect().width-grid.getBoundingClientRect().width)<1,'expanded full width');
      check(document.documentElement.scrollWidth<=innerWidth+1,'no page overflow');
      check(new Set([...document.querySelectorAll('[id]')].map(e=>e.id)).size===document.querySelectorAll('[id]').length,'unique IDs');
      for(const name of ['enabled','interval_days','send_time']) {
        const input=q('[name="'+name+'"]');
        check(input.form.id==='correction-reminder-settings','independent form '+name);
        check(input.labels.length===1,'associated label '+name);
        check(input.getBoundingClientRect().right<=card.getBoundingClientRect().right,'input fits '+name);
        for(const id of (input.getAttribute('aria-describedby')||'').split(' ').filter(Boolean))check(!!document.getElementById(id),'description exists '+name);
      }
      const keys=[...new FormData(q('#correction-reminder-settings')).keys()];
      check(keys.includes('csrfmiddlewaretoken') && keys.includes('return_to'),'CSRF and return target');
      check(keys.includes('interval_days') && !keys.includes('quitizz_enabled'),'reminder form excludes unrelated controls');
      check(!new FormData(q('form.feature-settings-form')).has('interval_days'),'general form excludes reminders');
      check(q('#id_send_time').value==='08:00' && q('#id_enabled').checked,'saved defaults');
      return errors;
    })()`);
    assert.deepEqual(expanded, []);
    await screenshot('configuration-expanded-' + width, '#correction-reminders-card');
    // A real click must submit the external reminder form, without navigating this fixture.
    await evaluate(`window.submittedForm=null;document.addEventListener('submit',e=>{e.preventDefault();window.submittedForm=e.target.id;},{once:true});document.querySelector('#correction-reminders-card [type="submit"]').click()`);
    assert.equal(await evaluate('window.submittedForm'), 'correction-reminder-settings');
    await evaluate('document.querySelector("#collapse-all-settings").click()');
    await pause(450);
    assert.equal(await evaluate('document.querySelectorAll(".settings-card .collapse.show").length'), 0);
    await evaluate('document.querySelector("#expand-all-settings").click()');
    await pause(450);
    assert.equal(await evaluate('document.querySelectorAll(".settings-card .collapse:not(.show)").length'), 0);
  });
}
test('saved-card anchor expands the reminder card', {skip}, async () => {
  await open('configuration', 1280, '#correction-reminders-card');
  assert.equal(await evaluate('document.querySelector("#feature-card-correction-reminders").classList.contains("show")'), true);
});
for (const page of ['dedicated', 'invalid']) {
  for (const width of [375, 1440]) {
    test(page + ' styling and accessible controls at ' + width, {skip}, async () => {
      await open(page, width);
      assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'), true);
      assert.equal(await evaluate('document.querySelectorAll("form.feature-settings-form").length'), 1);
      assert.equal(await evaluate('getComputedStyle(document.querySelector(".settings-card .card-header")).backgroundImage.includes("linear-gradient")'), true);
      assert.equal(await evaluate('[...document.querySelectorAll("#id_enabled,#id_interval_days,#id_send_time")].every(e=>e.labels.length===1)'), true);
      if (page === 'invalid') assert.equal(await evaluate('!!document.querySelector("[role=alert]") && document.querySelectorAll("[aria-invalid=true]").length===2'), true);
      await screenshot(page + '-' + width, '.feature-settings-form');
    });
  }
}
