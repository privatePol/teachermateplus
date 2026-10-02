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
    const card = document.getElementById("faculty-dtr-card");
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
        `The server did not confirm this DTR update (HTTP ${response.status}). No record was confirmed saved; retry or use the normal form submit.`,
      );
    }
    return { response, payload: await response.json() };
  };
  const loadFaculty = async (facultyId) => {
    const query = new URLSearchParams(window.location.search);
    query.set("faculty", facultyId);
    query.set("partial", "faculty");
    try {
      root.setAttribute("aria-busy", "true");
      clearInlineError();
      const { response, payload } = await jsonRequest(`${url}?${query.toString()}`, {
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
      });
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Unable to load this faculty DTR.");
      replacePayload(payload);
      query.delete("partial");
      window.history.replaceState({}, "", `${url}?${query.toString()}`);
      announce(payload.message || "Faculty DTR loaded.");
      focusCard();
    } catch (error) {
      const message = error.message || "Unable to load this faculty DTR.";
      announce(message);
      showInlineError(message);
    } finally {
      root.removeAttribute("aria-busy");
    }
  };

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-dtr-faculty-select]")) loadFaculty(event.target.value);
  });
  document.addEventListener("click", (event) => {
    const select = event.target.closest("[data-dtr-faculty-select][data-faculty-id]");
    if (!select) return;
    event.preventDefault();
    loadFaculty(select.dataset.facultyId);
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-dtr-ajax]");
    if (!form) return;
    event.preventDefault();
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
        if (formAction === "remove_adjustment") {
          const query = new URLSearchParams(window.location.search);
          query.delete("remove");
          const search = query.toString();
          window.history.replaceState(
            {}, "", `${window.location.pathname}${search ? `?${search}` : ""}#checker-entry`,
          );
        }
        focusCard();
      }
    } catch (error) {
      const message = error.message || "Unable to save the DTR change. No record was confirmed saved; use the normal form submit if this continues.";
      announce(message);
      showInlineError(message);
    } finally {
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
