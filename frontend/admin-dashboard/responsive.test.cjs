// Offline Chrome layout checks against real Django-rendered Admin fixtures.
// Set TMP_ADMIN_EVIDENCE and optionally TMP_CHROME_BIN to run.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {writeFileSync, readFileSync, mkdtempSync, existsSync} = require('node:fs');
const {join} = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const root = process.env.TMP_ADMIN_EVIDENCE;
const chrome = process.env.TMP_CHROME_BIN || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const unavailable = !root ? 'Set TMP_ADMIN_EVIDENCE to generated Django fixtures'
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
  const cssPath = process.env.TMP_ADMIN_BOOTSTRAP_CSS || join(root, 'bootstrap.min.css');
  assert.ok(existsSync(cssPath), 'Provide Bootstrap 5.3.3 CSS; see README.md');
  const css = readFileSync(cssPath, 'utf8');
  for (const variant of ['authorized', 'denied']) {
    let html = readFileSync(join(root, variant + '.html'), 'utf8');
    html = html.replace(/<link href="https:\/\/cdn\.jsdelivr\.net\/[^>]+>/g, () => '<style>' + css + '</style>')
      .replace(/<script src="https:\/\/cdn\.jsdelivr\.net\/[^>]+><\/script>/g, '');
    for (const name of ['sidebar', 'dashboard']) {
      const script = readFileSync(join(__dirname, '../../static/admin_portal/', name + '.js'), 'utf8');
      html = html.replace('<script src="/static/admin_portal/' + name + '.js" defer></script>', () => '<script>' + script + '</script>');
    }
    const logoPath = process.env.TMP_ADMIN_LOGO || join(__dirname, '../../media/logos/teachermate_logo_text_official.png');
    if (existsSync(logoPath)) html = html.replace('src="/media/logos/teachermate_logo_text_official.png"', 'src="' + pathToFileURL(logoPath).href + '"');
    writeFileSync(join(root, variant + '-offline.html'), html);
  }
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
  const errors=[];
  const check=(value,label)=>{if(!value)errors.push(label);};
  const q=selector=>document.querySelector(selector);
  const css=element=>getComputedStyle(element);
  const overflow=()=>check(document.documentElement.scrollWidth<=innerWidth+1,'page overflow');
  check(innerWidth===width,'viewport mismatch'); overflow();
  check(css(q('.admin-dashboard')).gridTemplateColumns.split(' ').length===(width>=1400?3:width>=768?2:1),'card columns');
  check(Boolean(q('[data-menu-code="USERS"]'))===(variant==='authorized'),'user menu permission');
  check(Boolean(q('[data-menu-code="AUDIT"]'))===(variant==='authorized'),'audit menu permission');
  check(Boolean([...document.querySelectorAll('a')].find(a=>a.textContent==='View Logged-in Users'))===(variant==='authorized'),'logged-in summary permission');
  const opener=q('#admin-mobile-nav'), sidebar=q('#admin-sidebar'), close=q('.admin-drawer-close');
  check((css(opener).display!=='none' && opener.getBoundingClientRect().width>0)===(width<992),'menu button breakpoint');
  if(width<992) {
    check(css(sidebar).visibility==='hidden' && sidebar.inert,'starts closed/inert');
    opener.focus();opener.click();
    await new Promise(resolve=>setTimeout(resolve,240));
    check(css(sidebar).visibility==='visible','drawer visible');
    check(document.activeElement===close,'drawer initial focus');
    check(q('.admin-main').inert && document.body.style.overflow==='hidden','background lock');
    const links=[...sidebar.querySelectorAll('a[href]')].filter(a=>a.getClientRects().length);
    check(links.length>=1,'authorized menu reachable');
    const last=links[links.length-1];last.focus();
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',cancelable:true,bubbles:true}));
    check(document.activeElement===close,'Tab wraps');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,cancelable:true,bubbles:true}));
    check(document.activeElement===last,'Shift Tab wraps');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true,bubbles:true}));
    check(document.activeElement===opener,'Escape restores focus');
    check(!q('.admin-main').inert && document.body.style.overflow==='','unlocks');
    opener.click();q('.admin-drawer-backdrop').click();check(opener.getAttribute('aria-expanded')==='false','backdrop closes');
    opener.click();close.click();check(opener.getAttribute('aria-expanded')==='false','Close closes');
  } else {check(!sidebar.inert,'desktop navigation enabled');}
  for(const table of document.querySelectorAll('.admin-dashboard .table-responsive')) {
    check(css(table).overflowX==='auto','table scrolling');
    if(table.scrollWidth>table.clientWidth) {table.scrollLeft=40;check(table.scrollLeft>0,'table scroll accessible');table.scrollLeft=0;}
  }
  overflow();
  await new Promise(resolve=>setTimeout(resolve,240));
  const navigation=performance.getEntriesByType('navigation')[0];
  return {width,variant,pageWidth:document.documentElement.scrollWidth,errors,
    navigation:{domContentLoaded: navigation.domContentLoadedEventEnd,load:navigation.loadEventEnd},
    resources:performance.getEntriesByType('resource').map(r=>({name:r.name,duration:r.duration,blocking:r.renderBlockingStatus}))};
}
for(const width of [375,390,440,767,768,992,1440]) {
  for(const variant of ['authorized','denied']) {
    test(`${variant} admin layout and drawer at ${width}px`,{skip:unavailable},async()=>{
      await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await command('Page.navigate',{url:pathToFileURL(join(root,variant+'-offline.html')).href});
      for(let i=0;i<100;i++) {const state=await command('Runtime.evaluate',{expression:'document.readyState',returnByValue:true});if(state.result.value==='complete')break;await pause(30);}
      const result=await command('Runtime.evaluate',{expression:`(${browserChecks.toString()})(${width},${JSON.stringify(variant)})`,awaitPromise:true,returnByValue:true});
      assert.ok(!result.exceptionDetails,JSON.stringify(result.exceptionDetails));
      const evidence=result.result.value;
      writeFileSync(join(root,`${variant}-${width}.json`),JSON.stringify(evidence,null,2));
      const screenshot=await command('Page.captureScreenshot',{format:'png'});writeFileSync(join(root,`${variant}-${width}.png`),Buffer.from(screenshot.data,'base64'));
      assert.deepEqual(evidence.errors,[],JSON.stringify(evidence));
    });
  }
}
