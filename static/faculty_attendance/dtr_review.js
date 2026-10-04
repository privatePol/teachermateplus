(() => {
  const root = document.querySelector("[data-dtr-review]");
  if (!root || !window.fetch) return;

  const url = root.dataset.dtrUrl || window.location.pathname;
  const live = document.getElementById("dtr-live");
  const inlineError = document.getElementById("dtr-inline-error");
  const announce = (message) => { if (live) live.textContent = message || ""; };
  const showInlineError = (message) => {
    if (!inlineError) return;
    inlineError.textContent = message;
    inlineError.classList.remove("d-none");
  };
  const clearInlineError = () => {
    if (!inlineError) return;
    inlineError.textContent = "";
    inlineError.classList.add("d-none");
  };
  const focusCard = () => {
    const card = document.getElementById("saved-faculty-dtr-card") || document.getElementById("faculty-dtr-card");
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.focus({ preventScroll: true });
  };
  const focusError = () => {
    const card = document.getElementById("faculty-dtr-card");
    const dateError = card && card.querySelector(".dtr-date-error");
    const target = (dateError && dateError.parentElement.querySelector("input, select, textarea"))
      || (card && card.querySelector("[aria-invalid='true'], .errorlist + input, .errorlist + select, .errorlist + textarea"));
    if (target) target.focus();
  };
  const replacePayload = (payload) => {
    const workspace = document.getElementById("dtr-workspace");
    if (workspace && payload.workspace_html) {
      workspace.innerHTML = payload.workspace_html;
      return;
    }
    const detail = document.getElementById("faculty-dtr-detail");
    if (detail && payload.faculty_html) detail.innerHTML = payload.faculty_html;
    if (payload.faculty_id && payload.summary_row_html) {
      const oldRow = document.getElementById(`dtr-summary-faculty-${payload.faculty_id}`);
      if (oldRow) oldRow.outerHTML = payload.summary_row_html;
    }
    const selector = document.getElementById("faculty-select");
    if (selector && payload.faculty_id) selector.value = String(payload.faculty_id);
  };
  const jsonRequest = async (requestUrl, options) => {
    const response = await fetch(requestUrl, options);
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(
        options.method === "POST"
          ? `The server did not confirm this DTR update (HTTP ${response.status}). No record was confirmed saved. Reload this cutoff and check its saved status before retrying.`
          : `The server could not load this DTR (HTTP ${response.status}). Your current DTR remains displayed; retry the selection.`,
      );
    }
    return { response, payload: await response.json() };
  };
  let selectionVersion = 0;
  let selectionController;
  let selectionPending = false;
  let saving = false;
  const loadFaculty = async (facultyId, resetFaculty = false) => {
    if (saving) { announce("Wait for the current DTR save before changing the selection."); return; }
    const version = ++selectionVersion;
    selectionPending = true;
    if (selectionController) selectionController.abort();
    selectionController = typeof AbortController !== "undefined" ? new AbortController() : null;
    const query = new URLSearchParams(window.location.search);
    const cutoff = document.getElementById("cutoff-select");
    if (cutoff) { query.set("cutoff", cutoff.value); query.delete("publication"); }
    ["edit", "remove", "early", "mixed", "review", "version"].forEach((key) => query.delete(key));
    if (resetFaculty) { query.delete("faculty"); query.set("reset_faculty", "1"); }
    else { query.set("faculty", facultyId); query.delete("reset_faculty"); }
    query.set("partial", "faculty");
    try {
      root.setAttribute("aria-busy", "true");
      const facultySelector = document.getElementById("faculty-select");
      if (facultySelector) facultySelector.disabled = true;
      clearInlineError();
      announce("Loading cutoff and faculty DTR…");
      const { response, payload } = await jsonRequest(`${url}?${query.toString()}`, {
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
        ...(selectionController ? {signal: selectionController.signal} : {}),
      });
      if (version !== selectionVersion) return;
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Unable to load this faculty DTR.");
      replacePayload(payload);
      query.delete("partial");
      query.delete("reset_faculty");
      if (payload.faculty_id) query.set("faculty", payload.faculty_id);
      else query.delete("faculty");
      window.history.replaceState({}, "", `${url}?${query.toString()}`);
      announce(payload.message || "Faculty DTR loaded.");
      focusCard();
    } catch (error) {
      if (version !== selectionVersion || error.name === "AbortError") return;
      const message = error.message || "Unable to load this faculty DTR.";
      announce(message);
      showInlineError(message);
    } finally {
      if (version === selectionVersion) {
        selectionPending = false;
        root.removeAttribute("aria-busy");
        const selector = document.getElementById("faculty-select");
        if (selector) selector.disabled = false;
      }
    }
  };

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-dtr-cutoff-select]")) return loadFaculty(null, true);
    if (event.target.matches("[data-dtr-faculty-select]")) return loadFaculty(event.target.value);
  });
  document.addEventListener("click", (event) => {
    const select = event.target.closest("[data-dtr-faculty-select][data-faculty-id]");
    if (!select) return;
    event.preventDefault();
    return loadFaculty(select.dataset.facultyId);
  });
  document.addEventListener("submit", async (event) => {
    if (selectionPending && event.target.closest("form[data-dtr-selection], form[data-dtr-ajax]")) {
      event.preventDefault();
      announce("Wait for the cutoff and faculty DTR to finish loading.");
      return;
    }
    if (event.target.closest("form[data-dtr-selection]")) {
      event.preventDefault();
      return loadFaculty(document.getElementById("faculty-select").value);
    }
    const form = event.target.closest("form[data-dtr-ajax]");
    if (!form) return;
    event.preventDefault();
    if (saving) return;
    saving = true;
    ["cutoff-select", "faculty-select"].forEach((id) => {
      const selector = document.getElementById(id);
      if (selector) selector.disabled = true;
    });
    ++selectionVersion;
    if (selectionController) selectionController.abort();
    // A hidden input named "action" shadows the DOM form.action property.
    // Read the literal attribute so AJAX and normal form posts share the route.
    const requestUrl = form.getAttribute("action") || window.location.href;
    const body = new FormData(form);
    const formAction = body.get("action");
    const submit = form.querySelector("button[type='submit'], button:not([type])");
    if (submit) submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      clearInlineError();
      const { response, payload } = await jsonRequest(requestUrl, {
        method: "POST", body,
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
      });
      replacePayload(payload);
      announce(payload.message);
      if (!response.ok || !payload.ok) {
        showInlineError(payload.message || "The DTR update was not saved. Correct the highlighted fields and retry.");
        focusError();
      } else {
        clearInlineError();
        const query = new URLSearchParams(window.location.search);
        if (query.has("version") || formAction === "remove_adjustment") {
          query.delete("version");
          if (formAction === "remove_adjustment") query.delete("remove");
          const search = query.toString();
          window.history.replaceState(
            {}, "", `${window.location.pathname}${search ? `?${search}` : ""}${formAction === "remove_adjustment" ? "#checker-entry" : ""}`,
          );
        }
        focusCard();
      }
    } catch (error) {
      const message = `${error.message || "Unable to save the DTR change."} The outcome is unconfirmed. Reload this cutoff and check its saved status before retrying.`;
      announce(message);
      showInlineError(message);
    } finally {
      saving = false;
      root.removeAttribute("aria-busy");
      ["cutoff-select", "faculty-select"].forEach((id) => {
        const selector = document.getElementById(id);
        if (selector) selector.disabled = false;
      });
      form.removeAttribute("aria-busy");
      if (submit) submit.disabled = false;
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-dtr-summary-return]")) return;
    const summary = document.getElementById("checker-cutoff-summary");
    if (!summary) return;
    summary.scrollIntoView({ behavior: "smooth", block: "start" });
    summary.focus({ preventScroll: true });
  });
})();
