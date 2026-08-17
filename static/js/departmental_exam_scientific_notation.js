(function () {
  "use strict";

  const SCIENTIFIC_FIELD_NAMES = new Set([
    "question_text",
    "choice_a",
    "choice_b",
    "choice_c",
    "choice_d",
  ]);

  const RENDER_OPTIONS = Object.freeze({
    delimiters: Object.freeze([
      Object.freeze({ left: "\\(", right: "\\)", display: false }),
      Object.freeze({ left: "\\[", right: "\\]", display: true }),
    ]),
    trust: false,
    throwOnError: false,
    strict: "error",
    maxSize: 10,
    maxExpand: 1000,
    output: "htmlAndMathml",
  });

  const INSERTS = Object.freeze([
    Object.freeze({ label: "Fraction", before: "\\(\\frac{", after: "}{b}\\)", placeholder: "a" }),
    Object.freeze({ label: "Square root", before: "\\(\\sqrt{", after: "}\\)", placeholder: "x" }),
    Object.freeze({ label: "Superscript", before: "\\(x^{", after: "}\\)", placeholder: "2" }),
    Object.freeze({ label: "Subscript", before: "\\(x_{", after: "}\\)", placeholder: "n" }),
    Object.freeze({ label: "±", text: "\\(\\pm\\)" }),
    Object.freeze({ label: "π", text: "\\(\\pi\\)" }),
    Object.freeze({ label: "θ", text: "\\(\\theta\\)" }),
    Object.freeze({ label: "Σ", text: "\\(\\sum\\)" }),
    Object.freeze({ label: "Integral", text: "\\(\\int\\)" }),
    Object.freeze({ label: "∞", text: "\\(\\infty\\)" }),
    Object.freeze({ label: "≤", text: "\\(\\le\\)" }),
    Object.freeze({ label: "≥", text: "\\(\\ge\\)" }),
    Object.freeze({ label: "≠", text: "\\(\\ne\\)" }),
    Object.freeze({
      label: "Matrix",
      before: "\\[\\begin{bmatrix}",
      after: " & b \\\\ c & d\\end{bmatrix}\\]",
      placeholder: "a",
    }),
  ]);

  const GREEK_INSERTS = Object.freeze([
    Object.freeze(["α Alpha", "\\(\\alpha\\)"]),
    Object.freeze(["β Beta", "\\(\\beta\\)"]),
    Object.freeze(["γ Gamma", "\\(\\gamma\\)"]),
    Object.freeze(["δ Delta", "\\(\\delta\\)"]),
    Object.freeze(["θ Theta", "\\(\\theta\\)"]),
    Object.freeze(["λ Lambda", "\\(\\lambda\\)"]),
    Object.freeze(["μ Mu", "\\(\\mu\\)"]),
    Object.freeze(["π Pi", "\\(\\pi\\)"]),
    Object.freeze(["σ Sigma", "\\(\\sigma\\)"]),
    Object.freeze(["φ Phi", "\\(\\phi\\)"]),
    Object.freeze(["ω Omega", "\\(\\omega\\)"]),
  ]);

  function renderElement(element) {
    if (!element || typeof window.renderMathInElement !== "function") return false;
    try {
      window.renderMathInElement(element, RENDER_OPTIONS);
      const group = element.closest("[data-scientific-group]");
      if (group && element.querySelector(".katex")) {
        group.classList.add("has-scientific-notation");
      }
      return true;
    } catch (_error) {
      return false;
    }
  }

  function renderAll(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-scientific-content]").forEach(renderElement);
  }

  function selectionBounds(input) {
    const end = typeof input.selectionEnd === "number" ? input.selectionEnd : input.value.length;
    const start = typeof input.selectionStart === "number" ? input.selectionStart : end;
    return { start, end };
  }

  function notifyInputChanged(input) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function insertText(input, text) {
    const bounds = selectionBounds(input);
    input.setRangeText(text, bounds.start, bounds.end, "end");
    notifyInputChanged(input);
  }

  function insertTemplate(input, before, after, placeholder) {
    const bounds = selectionBounds(input);
    const selected = input.value.slice(bounds.start, bounds.end);
    const body = selected || placeholder;
    input.setRangeText(before + body + after, bounds.start, bounds.end, "end");
    const bodyStart = bounds.start + before.length;
    input.setSelectionRange(bodyStart, bodyStart + body.length);
    notifyInputChanged(input);
  }

  function toolButton(label, title, action, primary) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = primary
      ? "btn btn-sm btn-outline-primary scientific-primary-action"
      : "btn btn-sm btn-outline-secondary";
    button.textContent = label;
    button.title = title;
    button.setAttribute("aria-label", title);
    button.addEventListener("click", action);
    return button;
  }

  function buildToolbar(input, toolbar, label) {
    toolbar.classList.add("scientific-editor-tools");
    toolbar.setAttribute("role", "toolbar");
    toolbar.setAttribute("aria-label", label + " scientific notation tools");

    toolbar.appendChild(toolButton(
      "Insert Equation",
      "Insert an inline equation in " + label,
      function () { insertTemplate(input, "\\(", "\\)", "x + y"); },
      true
    ));
    toolbar.appendChild(toolButton(
      "Chemical Formula",
      "Insert a chemical formula or reaction in " + label,
      function () { insertTemplate(input, "\\(\\ce{", "}\\)", "H2O"); },
      true
    ));

    INSERTS.forEach(function (item) {
      toolbar.appendChild(toolButton(
        item.label,
        "Insert " + item.label.toLowerCase() + " in " + label,
        function () {
          if (item.text) insertText(input, item.text);
          else insertTemplate(input, item.before, item.after, item.placeholder);
        },
        false
      ));
    });

    const greek = document.createElement("select");
    greek.className = "form-select form-select-sm scientific-greek-select";
    greek.setAttribute("aria-label", "Insert Greek symbol in " + label);
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = "Greek symbols…";
    greek.appendChild(prompt);
    GREEK_INSERTS.forEach(function (item) {
      const option = document.createElement("option");
      option.value = item[1];
      option.textContent = item[0];
      greek.appendChild(option);
    });
    greek.addEventListener("change", function () {
      if (greek.value) insertText(input, greek.value);
      greek.value = "";
    });
    toolbar.appendChild(greek);
  }

  function updatePreview(input, preview) {
    const value = input.value;
    preview.classList.toggle("is-empty", !value);
    preview.textContent = value || "Preview will appear here.";
    if (value) renderElement(preview);
  }

  function initialiseEditors() {
    document.querySelectorAll("[data-scientific-field]").forEach(function (wrapper) {
      const input = wrapper.querySelector("textarea, input[type='text']");
      const toolbar = wrapper.querySelector("[data-scientific-toolbar]");
      const preview = wrapper.querySelector("[data-scientific-preview]");
      if (!input || !toolbar || !preview || !SCIENTIFIC_FIELD_NAMES.has(input.name)) return;
      const label = wrapper.dataset.scientificLabel || input.name;
      buildToolbar(input, toolbar, label);
      input.addEventListener("input", function () { updatePreview(input, preview); });
      updatePreview(input, preview);
    });
  }

  function fontsReady() {
    if (!document.fonts || !document.fonts.ready) return Promise.resolve();
    return document.fonts.ready.catch(function () { return undefined; });
  }

  function initialisePrintActions(initialReady) {
    document.querySelectorAll("[data-scientific-print]").forEach(function (button) {
      button.addEventListener("click", function () {
        button.disabled = true;
        initialReady.then(function () {
          window.print();
          window.setTimeout(function () { button.disabled = false; }, 0);
        });
      });
      initialReady.then(function () { button.disabled = false; });
    });
  }

  function initialise() {
    renderAll(document);
    initialiseEditors();
    const initialReady = fontsReady();
    initialisePrintActions(initialReady);
    initialReady.then(function () {
      document.dispatchEvent(new CustomEvent("tmp:scientific-notation-ready"));
    });
    window.TMPScientificNotation = Object.freeze({
      version: "0.18.4",
      options: RENDER_OPTIONS,
      renderElement: renderElement,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise, { once: true });
  } else {
    initialise();
  }
}());
