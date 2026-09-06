(function () {
  "use strict";
  const form = document.querySelector("[data-case-editor-form]");
  if (!form) return;
  const editor = form.querySelector("[data-case-rich-editor]");
  const source = form.querySelector("[data-case-source]");
  const preview = form.querySelector("[data-case-preview]");
  const errorBox = form.querySelector("[data-case-editor-errors]");
  const warningBox = form.querySelector("[data-case-editor-warnings]");
  const previewButton = form.querySelector("[data-case-preview-button]");
  const allowed = new Set(["P", "H3", "H4", "STRONG", "EM", "UL", "OL", "LI", "TABLE", "CAPTION", "THEAD", "TBODY", "TFOOT", "TR", "TH", "TD", "BR", "SUP", "SUB"]);
  const alignable = new Set(["P", "H3", "H4", "TH", "TD"]);

  function show(box, messages) {
    box.textContent = messages.join(" ");
    box.hidden = messages.length === 0;
  }
  function alignment(node) {
    const value = (node.style && node.style.textAlign || "").toLowerCase();
    return ["left", "center", "right"].includes(value) ? "tmp-align-" + value : "";
  }
  function normalizeNode(node, documentRef) {
    if (node.nodeType === Node.TEXT_NODE) return documentRef.createTextNode(node.textContent || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return documentRef.createDocumentFragment();
    const name = node.tagName.toUpperCase();
    if (["IMG", "SVG", "OBJECT", "EMBED", "IFRAME"].includes(name)) throw new Error("Images, diagrams, and embedded objects are not supported in Case content.");
    if (name.includes("OMATH") || /office:math/i.test(node.namespaceURI || "")) throw new Error("A native Word equation was detected. Replace it with TMP LaTeX or Unicode.");
    if (["SCRIPT", "STYLE", "FORM", "INPUT", "BUTTON", "SELECT", "TEXTAREA"].includes(name)) return documentRef.createDocumentFragment();
    let targetName = { B: "STRONG", I: "EM", DIV: "P" }[name] || name;
    const wrappers = [];
    if (targetName === "SPAN") {
      const style = (node.getAttribute("style") || "").toLowerCase();
      if (/font-weight\s*:\s*(bold|[6-9]00)/.test(style)) wrappers.push("STRONG");
      if (/font-style\s*:\s*italic/.test(style)) wrappers.push("EM");
      if (/vertical-align\s*:\s*super/.test(style)) wrappers.push("SUP");
      if (/vertical-align\s*:\s*sub/.test(style)) wrappers.push("SUB");
    } else if (allowed.has(targetName)) wrappers.push(targetName);
    let result = documentRef.createDocumentFragment();
    let container = result;
    wrappers.forEach(tag => { const element=documentRef.createElement(tag.toLowerCase()); container.appendChild(element); container=element; });
    const element = container.nodeType === Node.ELEMENT_NODE ? container : null;
    if (element) {
      const tag = element.tagName.toUpperCase();
      if (["TH", "TD"].includes(tag)) ["rowspan", "colspan"].forEach(attr => { const value=parseInt(node.getAttribute(attr),10); if(value>=1&&value<=20) element.setAttribute(attr,String(value)); });
      if (tag === "TH" && ["row", "col", "rowgroup", "colgroup"].includes((node.getAttribute("scope") || "").toLowerCase())) element.setAttribute("scope", node.getAttribute("scope").toLowerCase());
      if (tag === "OL") { const start=parseInt(node.getAttribute("start"),10); if(start>=1&&start<=10000) element.setAttribute("start",String(start)); }
      const align = alignment(node); if (align && alignable.has(tag)) element.className=align;
    }
    [...node.childNodes].forEach(child => container.appendChild(normalizeNode(child, documentRef)));
    return result;
  }
  function normalizedClipboardHtml(raw) {
    const parsed = new DOMParser().parseFromString(raw, "text/html");
    const holder = document.createElement("div");
    [...parsed.body.childNodes].forEach(node => holder.appendChild(normalizeNode(node, document)));
    return holder.innerHTML;
  }
  editor.addEventListener("paste", event => {
    event.preventDefault(); show(errorBox, []);
    try {
      const html = event.clipboardData.getData("text/html");
      const text = event.clipboardData.getData("text/plain");
      const value = html ? normalizedClipboardHtml(html) : text.split(/\n\s*\n/).map(p => "<p>" + p.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>") + "</p>").join("");
      document.execCommand("insertHTML", false, value);
    } catch (error) { show(errorBox, [error.message]); }
  });
  function currentContent() { source.value = editor.innerHTML; return source.value; }
  async function requestPreview() {
    show(errorBox, []); show(warningBox, []);
    const body = new FormData(); body.append("stimulus", currentContent()); body.append("input_format", "html");
    body.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
    const response = await fetch(form.dataset.previewUrl, { method: "POST", body, credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
    const payload = await response.json();
    if (!response.ok) { show(errorBox, payload.errors || ["Preview could not be generated."]); return; }
    preview.innerHTML = payload.html; show(warningBox, payload.warnings || []);
    if (typeof window.renderMathInElement === "function") window.renderMathInElement(preview, { delimiters: [{left:"\\(",right:"\\)",display:false},{left:"\\[",right:"\\]",display:true}], trust:false, throwOnError:false, strict:"error", maxSize:10, maxExpand:1000 });
  }
  previewButton.addEventListener("click", () => requestPreview().catch(() => show(errorBox, ["Preview could not be generated. Try again."])));
  form.addEventListener("submit", currentContent);
})();
