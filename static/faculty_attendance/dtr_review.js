(() => {
  const root = document.querySelector("[data-dtr-review]");
  if (!root || !window.fetch || root.dataset.dtrInitialized) return;
  root.dataset.dtrInitialized = "true";
  const url = root.dataset.dtrUrl || window.location.pathname;
  const live = document.getElementById("dtr-live");
  const inlineError = document.getElementById("dtr-inline-error");
  const modal = document.getElementById("dtr-loading-modal");
  const announce = message => { if (live) live.textContent = message || ""; };
  const showError = message => { announce(message); inlineError.textContent = message; inlineError.classList.remove("d-none"); };
  const clearError = () => { inlineError.textContent = ""; inlineError.classList.add("d-none"); };
  const motion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  const focusDetail = () => {
    const heading = document.getElementById("faculty-dtr-title");
    const card = document.getElementById("faculty-dtr-card");
    if (heading) heading.focus({preventScroll:true});
    if (card) card.scrollIntoView({behavior:motion(), block:"start"});
  };
  const controls = () => ["cutoff-select", "faculty-select", "dtr-version-select"].map(id => document.getElementById(id));
  let committed = controls().map(control => control && control.value);
  const restoreSelection = () => controls().forEach((control, index) => { if (control && committed[index] != null) control.value = committed[index]; });
  const forms = () => [...root.querySelectorAll("form[method='post']")];
  const signature = form => JSON.stringify([...new FormData(form).entries()]);
  let baselines = new WeakMap();
  let retained = new WeakSet();
  const rememberForms = () => {
    baselines = new WeakMap(); retained = new WeakSet();
    forms().forEach(form => { baselines.set(form, signature(form)); if (form.querySelector(".errorlist, .dtr-date-error")) retained.add(form); });
  };
  const dirty = form => retained.has(form) || signature(form) !== baselines.get(form);
  const dirtyForms = () => forms().filter(dirty);
  rememberForms();
  let selectionVersion = 0, controller, pending = false, saving = false, activeSave;
  const lockSave = form => {
    const detail = document.getElementById("faculty-dtr-detail");
    const fields = new Set([...(detail ? detail.querySelectorAll("input, select, textarea, button, fieldset") : form.elements), ...controls()].filter(Boolean));
    const lock = {detail, form, disabled:new Map([...fields].map(field => [field, field.disabled])),
      inert:!!(detail && detail.inert), busy:detail && detail.getAttribute("aria-busy"), formBusy:form.getAttribute("aria-busy")};
    activeSave = lock; saving = true;
    fields.forEach(field => { field.disabled = true; });
    if (detail) { detail.inert = true; detail.setAttribute("aria-busy", "true"); }
    form.setAttribute("aria-busy", "true");
    return lock;
  };
  const unlockSave = lock => {
    if (activeSave !== lock) return;
    const restoreBusy = (node, value) => {
      if (!node || !root.contains(node)) return;
      if (value == null) node.removeAttribute("aria-busy"); else node.setAttribute("aria-busy", value);
    };
    // Restore only this request's retained nodes, never fresh response controls.
    lock.disabled.forEach((disabled, field) => { if (root.contains(field)) field.disabled = disabled; });
    if (lock.detail && root.contains(lock.detail)) lock.detail.inert = lock.inert;
    restoreBusy(lock.detail, lock.busy); restoreBusy(lock.form, lock.formBusy);
    activeSave = null; saving = false;
  };
  // Capture before delegated Admin Hours/correction handlers can mutate entries.
  ["click", "beforeinput", "input", "change", "keydown", "submit"].forEach(type => root.addEventListener(type, event => {
    if (activeSave && activeSave.detail && activeSave.detail.contains(event.target)) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true));
  const loader = loading => {
    if (loading) root.setAttribute("aria-busy", "true"); else root.removeAttribute("aria-busy");
    const detail = document.getElementById("faculty-dtr-detail");
    if (detail) detail.inert = loading || !!(activeSave && activeSave.detail === detail);
    if (!modal) return;
    if (loading) { modal.hidden = false; if (!modal.open && modal.showModal) modal.showModal(); }
    else { if (modal.open && modal.close) modal.close(); modal.hidden = true; }
  };
  const replacePayload = payload => {
    const workspace = document.getElementById("dtr-workspace");
    if (payload.workspace_html && workspace) workspace.innerHTML = payload.workspace_html;
    else {
      const detail = document.getElementById("faculty-dtr-detail");
      if (detail && payload.faculty_html) detail.innerHTML = payload.faculty_html;
      const row = document.getElementById(`dtr-summary-faculty-${payload.faculty_id}`);
      if (row && payload.summary_row_html) row.outerHTML = payload.summary_row_html;
    }
    const faculty = document.getElementById("faculty-select");
    if (faculty && payload.faculty_id) faculty.value = String(payload.faculty_id);
    rememberForms();
    committed = controls().map(control => control && control.value);
  };
  const updateUrl = (query, payload, hash = "") => {
    ["partial", "reset_faculty", "publication", "review"].forEach(key => query.delete(key));
    if (payload.cutoff) query.set("cutoff", payload.cutoff);
    if (payload.faculty_id) query.set("faculty", payload.faculty_id); else query.delete("faculty");
    if (payload.view === "current") { query.set("view", "current"); query.delete("version"); }
    else { query.delete("view"); if (payload.version) query.set("version", payload.version); else query.delete("version"); }
    window.history.replaceState({}, "", `${url}?${query.toString()}${hash}`);
  };
  const jsonRequest = async (requestUrl, options) => {
    const response = await fetch(requestUrl, options);
    if (!(response.headers.get("content-type") || "").includes("application/json")) {
      throw new Error(options.method === "POST"
        ? `The server did not confirm this DTR update (HTTP ${response.status}). No record was confirmed saved.`
        : `Unable to load this DTR (HTTP ${response.status}). Retry the faculty selection.`);
    }
    return {response, payload:await response.json()};
  };
  const cancelLoad = () => {
    ++selectionVersion; if (controller) controller.abort(); pending = false;
    loader(false); restoreSelection(); announce("DTR loading cancelled. The previous selection remains displayed.");
  };
  if (modal) modal.addEventListener("cancel", event => { event.preventDefault(); cancelLoad(); });
  const load = async (query, hash = "") => {
    if (saving) { restoreSelection(); showError("Wait for the current DTR save before changing selection."); return; }
    if (dirtyForms().length && !window.confirm("There are unsaved DTR entries. Discard them and change the selection?")) { restoreSelection(); return; }
    const version = ++selectionVersion;
    if (controller) controller.abort();
    controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const activeController = controller;
    let timedOut = false, rejectTimeout;
    const deadline = new Promise((_resolve, reject) => { rejectTimeout = reject; });
    const timeout = window.setTimeout(() => {
      timedOut = true; if (activeController) activeController.abort();
      rejectTimeout(new Error("DTR loading timed out. Retry the selection."));
    }, 30000);
    pending = true; query.set("partial", "faculty");
    clearError(); announce("Loading DTR?"); loader(true);
    try {
      const {response, payload} = await Promise.race([jsonRequest(`${url}?${query}`, {
        headers:{"X-Requested-With":"XMLHttpRequest", Accept:"application/json"},
        ...(activeController ? {signal:activeController.signal} : {}),
      }), deadline]);
      if (version !== selectionVersion) return;
      if (!response.ok || !payload.ok || !payload.workspace_html) throw new Error(payload.message || "Unable to load this DTR. Retry the selection.");
      if (!query.has("reset_faculty") && query.get("faculty") && String(payload.faculty_id) !== query.get("faculty")) throw new Error("The selected faculty could not be confirmed. Retry the selection.");
      replacePayload(payload); updateUrl(query, payload, hash);
      loader(false); announce(payload.message || "Faculty DTR loaded."); focusDetail();
    } catch (error) {
      if (version !== selectionVersion) return;
      restoreSelection();
      showError(timedOut ? "DTR loading timed out. Retry the selection."
        : /retry/i.test(error.message || "") ? error.message
        : "Unable to load this DTR. Check your connection and retry the selection.");
    } finally {
      window.clearTimeout(timeout);
      if (version === selectionVersion) { pending = false; loader(false); }
    }
  };
  const selectFaculty = (facultyId, reset = false) => {
    const query = new URLSearchParams();
    const cutoff = document.getElementById("cutoff-select");
    if (cutoff) query.set("cutoff", cutoff.value);
    if (reset) query.set("reset_faculty", "1"); else if (facultyId) query.set("faculty", facultyId);
    return load(query);
  };
  const navigate = link => { const target = new URL(link.href, window.location.href); return load(new URLSearchParams(target.search), target.hash); };
  document.addEventListener("change", event => {
    if (!root.contains(event.target)) return;
    if (event.target.matches("[data-dtr-cutoff-select]")) return selectFaculty(null, true);
    if (event.target.matches("select[data-dtr-faculty-select]")) return selectFaculty(event.target.value);
    if (event.target.matches("[data-dtr-version-select]")) {
      const form = event.target.form; return load(new URLSearchParams(new FormData(form)));
    }
  });
  document.addEventListener("click", event => {
    if (!root.contains(event.target)) return;
    if (event.target.closest("[data-dtr-cancel-load]")) return cancelLoad();
    const faculty = event.target.closest("[data-dtr-faculty-select][data-faculty-id]");
    if (faculty) { event.preventDefault(); return selectFaculty(faculty.dataset.facultyId); }
    const link = event.target.closest("[data-dtr-navigation]");
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); return navigate(link); }
    if (event.target.closest("[data-dtr-summary-return]")) {
      const summary = document.getElementById("checker-cutoff-summary");
      if (summary) { summary.focus({preventScroll:true}); summary.scrollIntoView({behavior:motion(), block:"start"}); }
    }
  });
  document.addEventListener("submit", async event => {
    const form = event.target;
    if (!root.contains(form)) return;
    if (form.matches("[data-dtr-selection]")) { event.preventDefault(); return selectFaculty(document.getElementById("faculty-select").value); }
    if (form.matches("[data-dtr-version-form]")) { event.preventDefault(); return load(new URLSearchParams(new FormData(form))); }
    if (form.method !== "post") return;
    if (pending || saving) { event.preventDefault(); announce("Wait for the DTR to finish loading or saving."); return; }
    if (dirtyForms().some(other => other !== form)) { event.preventDefault(); showError("Save or clear the other unsaved DTR form before submitting this one. Your entries remain on screen."); return; }
    if (!form.matches("[data-dtr-ajax]")) { saving = true; return; }
    event.preventDefault();
    // Successful controls (including a named submitter) must be captured first.
    const body = event.submitter ? new FormData(form, event.submitter) : new FormData(form);
    const action = body.get("action"), lock = lockSave(form);
    let focusAfterSave;
    try {
      clearError();
      const {response, payload} = await jsonRequest(form.getAttribute("action") || url, {
        method:"POST", body, headers:{"X-Requested-With":"XMLHttpRequest", Accept:"application/json"},
      });
      if (activeSave !== lock) return;
      if (String(payload.faculty_id) !== String(body.get("faculty")) || !payload.faculty_html) throw new Error("The saved faculty response could not be confirmed.");
      replacePayload(payload);
      const query = new URLSearchParams(window.location.search);
      if (response.ok && payload.ok) ["edit", "remove", "early", "mixed"].forEach(key => query.delete(key));
      updateUrl(query, payload, action === "remove_adjustment" && payload.ok ? "#checker-entry" : "");
      if (!response.ok || !payload.ok) {
        const revised = forms().find(item => new FormData(item).get("action") === action);
        if (revised) retained.add(revised);
        showError(payload.message || "Correct the highlighted fields and retry.");
        const invalid = root.querySelector("[aria-invalid='true'], .errorlist + input, .errorlist + select");
        if (invalid) focusAfterSave = () => invalid.focus();
      } else { announce(payload.message || "DTR saved."); focusAfterSave = focusDetail; }
    } catch (error) {
      if (activeSave === lock) showError(`${error.message || "Unable to save the DTR change."} The outcome is unconfirmed. Reload this cutoff and check its saved status before retrying.`);
    } finally {
      if (activeSave === lock) { unlockSave(lock); if (focusAfterSave) focusAfterSave(); }
    }
  });
  window.addEventListener("beforeunload", event => {
    if (!saving && dirtyForms().length) { event.preventDefault(); event.returnValue = ""; }
  });
})();
