import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import {JSDOM} from "jsdom";

const read = (name) => fs.readFileSync(new URL(`../../${name}`, import.meta.url), "utf8");
const css = read("static/css/quitizz.css");
const endpoint = "/faculty/quitizz/sessions/11111111-1111-4111-8111-111111111111/qr/";
const lobby = (extra = {}) => ({status:"LOBBY", version:7, title:"Management meeting", participant_count:60,
  answered_count:0, position:0, question_count:5, joining_open:true, server_now:new Date().toISOString(), ...extra});
function fixture() {
  const dom = new JSDOM('<style></style><main class="qt-projector"><section id="stage" class="qt-stage"></section></main>', {url:"http://localhost/", runScripts:"outside-only"});
  const w=dom.window; w.document.querySelector("style").textContent=css;
  w.matchMedia=()=>({matches:true,addEventListener(){}});
  w.setInterval=()=>1; w.clearInterval=()=>{};
  vm.runInContext(read("static/js/quitizz_presentation.js"),dom.getInternalVMContext());
  const root=w.document.getElementById("stage"), view=w.QuiTizzPresentation.create(root,{projector:true,qrUrl:endpoint});
  return {w,root,view,close:()=>w.close()};
}

test("projector QR keeps the real protected endpoint, square geometry and accessible scan instructions",()=>{
  const h=fixture();try {
    h.view.render(lobby());const image=h.root.querySelector(".qt-qr");
    assert.equal(image.getAttribute("src"),endpoint+"?v=7");
    assert.equal(image.width,440);assert.equal(image.height,440);
    assert.match(image.alt,/Scan this QR code.*nickname/);
    const caption=h.root.querySelector("figcaption");
    assert.equal(caption.firstChild.textContent,"SCAN TO JOIN");
    assert.equal(h.root.querySelector("figure").lastElementChild,caption);
    assert.match(h.root.querySelector(".qt-qr-help").textContent,/camera.*nickname/);
    assert.equal(h.root.querySelector(".qt-qr-frame").contains(image),true);
    assert.equal(h.root.querySelector("a[href*='#']"),null);
  } finally {h.close();}
});

test("QR canvas stays opaque white without inversion, blending or overlays",()=>{
  const h=fixture();try {
    h.view.render(lobby());
    for (const node of [h.root.querySelector(".qt-qr-frame"),h.root.querySelector(".qt-qr")]) {
      const style=h.w.getComputedStyle(node);
      assert.equal(style.backgroundColor,"rgb(255, 255, 255)");
      assert.equal(style.opacity,"1");assert.equal(style.filter,"none");assert.equal(style.mixBlendMode,"normal");
      assert.equal(style.colorScheme,"light");assert.equal(style.forcedColorAdjust,"none");
    }
    assert.equal(h.w.getComputedStyle(h.root.querySelector(".qt-qr")).imageRendering,"pixelated");
    assert.equal(h.root.querySelector(".qt-qr-frame").children.length,1);
  } finally {h.close();}
});

test("projector QR has a 440px target plus extra white padding and a bounded responsive width",()=>{
  const h=fixture();try {
    h.view.render(lobby());
    const rules=[...h.w.document.styleSheets[0].cssRules];
    const frame=rules.find((rule)=>rule.selectorText===".qt-qr-frame").style;
    const projector=rules.find((rule)=>rule.selectorText===".qt-projector .qt-qr-frame").style;
    const image=rules.find((rule)=>rule.selectorText===".qt-qr-frame .qt-qr").style;
    assert.equal(projector.width,"min(100%, 472px, 50vh)");assert.equal(frame.padding,"16px");
    assert.equal(frame.getPropertyValue("box-sizing"),"border-box");assert.equal(frame.getPropertyValue("max-width"),"100%");
    assert.equal(image.width,"100%");assert.equal(image.height,"auto");
    assert.equal(472-2*16,440);
    assert.ok(rules.some((rule)=>rule.conditionText==="(min-width: 900px)" && [...rule.cssRules].some((child)=>child.style?.display==="grid")));
    assert.ok(!css.includes("width: min(36vh, 380px)"));
  } finally {h.close();}
});

test("60-plus joined counts recover at the same version without replacing the joining payload",()=>{
  const h=fixture();try {
    for (const count of [0,60,65,120]) {
      h.view.render(lobby({participant_count:count}));
      assert.match(h.root.querySelector(".qt-metrics").textContent,new RegExp(`^${count} joined`));
      assert.equal(h.root.querySelector("img").getAttribute("src"),endpoint+"?v=7");
      assert.equal(h.root.querySelectorAll("img").length,1);
    }
    h.view.render(lobby({participant_count:65,version:8}));
    assert.equal(h.root.querySelector("img").getAttribute("src"),endpoint+"?v=8");
  } finally {h.close();}
});

test("closed joining and live gameplay never retain a stale lobby QR",()=>{
  const h=fixture();try {
    h.view.render(lobby());h.view.render(lobby({joining_open:false,version:8}));
    assert.equal(h.root.querySelector("img"),null);assert.match(h.root.textContent,/Joining is closed/);
    h.view.render(lobby({status:"QUESTION_OPEN",joining_open:false,version:9}));
    assert.equal(h.root.querySelector("img"),null);assert.equal(h.root.querySelector(".qt-qr-heading"),null);
  } finally {h.close();}
});

test("QR viewport model bounds full-HD, desktop and mobile sizes without horizontal clipping",()=>{
  // CSS geometry model, not browser layout: default/large root font, actual
  // 2vw outer padding, stage clamp padding, borders and the QR's 50vh cap.
  for (const rootFont of [16,24]) for (const [width,height] of [[1920,1080],[1600,900],[1366,768],[1024,768],[390,844],[320,568]]) {
    const stagePadding=Math.min(2*rootFont,Math.max(rootFont,.024*width));
    const available=width-2*.02*width-2*stagePadding-4;
    const frame=Math.min(available,472,.5*height), image=frame-32;
    assert.ok(frame<=available);assert.ok(image>0 && image<=440);
    assert.equal(image+32,frame);
    if (width===1920) assert.equal(image,440);
    if (rootFont===16) console.log(`QUITIZZ_QR_VIEWPORT_MODEL ${width}x${height}: QR=${image.toFixed(1)}px, white panel=${frame.toFixed(1)}px, available=${available.toFixed(1)}px`);
  }
});
