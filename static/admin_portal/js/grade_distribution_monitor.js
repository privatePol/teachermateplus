/* Aggregate-only progressive enhancement. Bootstrap owns modal focus/Escape. */
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("gradeDistributionFilterForm");
  const results = document.getElementById("gdm-results");
  const status = document.getElementById("gdm-filter-status");
  const modal = document.getElementById("gradeDistributionAnalyticsModal");
  const body = document.getElementById("gdm-modal-body");
  if (!form || !results || !modal || form.dataset.initialized) return;
  form.dataset.initialized = "true";
  const submit = form.querySelector('button[type="submit"]');
  let applied = Boolean(results.querySelector("[data-dataset-id]"));
  let resultsAbort, optionsAbort, detailAbort, opener, pendingKey, generation = 0, modalGeneration = 0;
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const loading = label => {
    const wrap = node("div", undefined, "gdm-loading");
    wrap.append(node("span", label));
    const progress = node("div", undefined, "progress mt-2");
    const bar = node("div", undefined, "progress-bar bg-success gdm-indeterminate");
    bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-label", label);
    progress.append(bar); wrap.append(progress); return wrap;
  };
  const readJSON = async response => {
    if (response.redirected) throw new Error("Your session may have expired. Sign in again, then reload this page.");
    if (!(response.headers.get("Content-Type") || "").includes("application/json")) {
      throw new Error(response.status === 403 ? "You no longer have permission to view these results." : "The server returned an unexpected response. Reload this page.");
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.errors?.join(" ") || "Results are unavailable. Apply Filters again.");
    return data;
  };
  const fetchJSON = async (url, controller, init = {}) => readJSON(await fetch(url, {
    signal: controller.signal, cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}, ...init,
  }));
  const closeDetail = () => {
    detailAbort?.abort(); modalGeneration++;
    body.replaceChildren(); body.removeAttribute("aria-busy");
    window.bootstrap?.Modal.getInstance(modal)?.hide();
  };
  const invalidate = () => {
    applied = false; generation++; resultsAbort?.abort(); pendingKey = null;
    results.hidden = true; results.removeAttribute("aria-busy");
    form.removeAttribute("aria-busy"); submit.disabled = Boolean(optionsAbort);
    closeDetail();
  };
  const formURL = () => {
    const url = new URL(location.pathname, location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    url.searchParams.delete("render"); url.searchParams.delete("csrfmiddlewaretoken"); url.searchParams.delete("manual_intent"); return url;
  };
  const applySelected = selected => {
    for (const [name, value] of Object.entries(selected || {})) {
      if (form.elements.namedItem(name)) form.elements.namedItem(name).value = value;
    }
  };
  const updateOptions = async source => {
    optionsAbort?.abort(); const controller = new AbortController(); optionsAbort = controller;
    submit.disabled = true;
    const url = new URL(source, location.href);
    for (const key of ["apply", "context", "page", "export", "results", "render"]) url.searchParams.delete(key);
    url.searchParams.set("options", "1");
    try {
      const data = await fetchJSON(url, controller);
      if (controller.signal.aborted || optionsAbort !== controller) return false;
      for (const [name, choices, placeholder] of [["faculty_id", data.faculty, "All Eligible Faculty"],
        ["department_id", data.departments, "All Eligible Departments"], ["term_id", data.terms, "Select Term"]]) {
        const select = form.elements.namedItem(name), old = select.value;
        select.replaceChildren(new Option(placeholder, ""));
        choices.forEach(choice => select.add(new Option(choice.label, choice.id)));
        if ([...select.options].some(option => option.value === old)) select.value = old;
      }
      return true;
    } catch (error) {
      if (optionsAbort === controller && !controller.signal.aborted) status.textContent = error.message;
      return false;
    } finally {
      if (optionsAbort === controller) { optionsAbort = null; submit.disabled = Boolean(pendingKey); }
    }
  };
  const loadResults = async (source, historyMode = "push", explicitApply = false) => {
    const url = new URL(source, location.href);
    for (const key of ["render", "export", "options"]) url.searchParams.delete(key);
    url.searchParams.set("results", "1");
    const key = url.href;
    if (pendingKey === key) return;
    invalidate(); const requestGeneration = generation;
    const controller = new AbortController(); resultsAbort = controller; pendingKey = key;
    submit.disabled = true; form.setAttribute("aria-busy", "true"); results.setAttribute("aria-busy", "true");
    status.replaceChildren(loading("Retrieving grade distribution"));
    try {
      const init = explicitApply ? {method: "POST", body: new URLSearchParams(url.searchParams),
        headers: {Accept: "application/json", "X-CSRFToken": form.elements.namedItem("csrfmiddlewaretoken").value}} : {};
      const data = await fetchJSON(explicitApply ? url.pathname : url, controller, init);
      if (controller.signal.aborted || requestGeneration !== generation) return;
      const canonical = new URL(data.url, location.href);
      if (canonical.origin !== location.origin || canonical.pathname !== location.pathname) throw new Error("Invalid results URL.");
      // Same-origin server-rendered, autoescaped fragment; no student/raw payload.
      results.innerHTML = data.html; results.hidden = false; applied = true; status.replaceChildren();
      if (data.manual_intent && form.elements.namedItem("manual_intent")) form.elements.namedItem("manual_intent").value = data.manual_intent;
      if (historyMode === "push" && canonical.href !== location.href) history.pushState(null, "", canonical);
      if (historyMode === "replace") history.replaceState(null, "", canonical);
      if (historyMode === "clear") { applySelected(data.selected); history.pushState(null, "", location.pathname); }
    } catch (error) {
      if (!controller.signal.aborted && requestGeneration === generation) {
        results.replaceChildren(); results.hidden = false;
        const alert = node("div", error.message || "Network error. Try Apply Filters again.", "alert alert-warning");
        alert.setAttribute("role", "alert");
        const fallback = node("button", "Apply without JavaScript", "btn btn-outline-secondary");
        fallback.type = "button";
        fallback.addEventListener("click", () => HTMLFormElement.prototype.submit.call(form));
        status.replaceChildren(alert, fallback);
      }
    } finally {
      if (requestGeneration === generation) {
        pendingKey = null; submit.disabled = Boolean(optionsAbort);
        results.removeAttribute("aria-busy"); form.removeAttribute("aria-busy");
      }
    }
  };
  form.addEventListener("submit", event => {
    event.preventDefault(); if (!optionsAbort && form.reportValidity()) loadResults(formURL(), "push", true);
  });
  form.addEventListener("change", async event => {
    invalidate(); status.textContent = "Filters changed. Click Apply Filters to refresh results.";
    if (["campus_id", "academic_year_id"].includes(event.target.name)) {
      if (event.target.name === "academic_year_id") form.elements.term_id.value = "";
      await updateOptions(formURL());
    }
  });
  form.addEventListener("input", event => {
    if (["grade_min", "grade_max"].includes(event.target.name)) {
      invalidate(); status.textContent = "Grade Range changed. Click Apply Filters to refresh results.";
    }
  });
  document.getElementById("gdm-clear").addEventListener("click", async event => {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); invalidate();
    applySelected({campus_id: form.dataset.defaultCampus || "all", academic_year_id: "", term_id: "", period: "",
      faculty_id: "", department_id: "", status: "all", sort: "range_count_desc", grade_min: "97", grade_max: "100"});
    results.replaceChildren(node("p", "Select criteria and click Apply Filters to view grade distributions."));
    results.hidden = false; status.textContent = "Filters cleared. Click Apply Filters to load records.";
    history.pushState(null, "", location.pathname); await updateOptions(formURL());
  });
  results.addEventListener("submit", event => {
    const pageForm = event.target.closest(".gdm-page-form");
    if (pageForm) { event.preventDefault(); if (applied && !results.hidden) loadResults(pageForm.action); }
  });
  const restore = async () => {
    invalidate(); const restoreGeneration = generation;
    const url = new URL(location.href);
    const selected = {campus_id: form.dataset.defaultCampus || "all", academic_year_id: "", term_id: "", period: "",
      faculty_id: "", department_id: "", status: "all", sort: "range_count_desc", grade_min: "97", grade_max: "100"};
    for (const name of Object.keys(selected)) if (url.searchParams.has(name)) selected[name] = url.searchParams.get(name);
    applySelected(selected);
    results.replaceChildren();
    status.textContent = "Filters restored. Click Apply Filters to load records.";
    if (await updateOptions(url) && generation === restoreGeneration) applySelected(selected);
  };
  window.addEventListener("popstate", restore);
  window.addEventListener("pageshow", event => { if (event.persisted) restore(); });
  const renderMeasure = measure => {
    const section = node("section", undefined, "gdm-measure");
    section.dataset.datasetId = measure.dataset_id;
    section.append(node("h5", measure.label || "Period Grades", "h6"));
    section.append(node("p", measure.gradebook_context, "small text-muted"));
    section.append(node("p", measure.range_label, "fw-semibold"));
    if (measure.range_state === "disclosed") {
      section.append(node("p", `${measure.range_count} / ${measure.range_denominator} (${Number(measure.range_percentage).toFixed(2)}%)`));
    } else section.append(node("p", "Withheld", "fw-semibold"));
    for (const key of ["source_notice", "calculation_note", "unit"]) {
      if (measure[key]) section.append(node("p", measure[key], "small text-muted"));
    }
    return section;
  };
  modal.addEventListener("show.bs.modal", async event => {
    if (!applied || results.hidden) { event.preventDefault(); return; }
    opener = event.relatedTarget; detailAbort?.abort();
    const controller = new AbortController(); detailAbort = controller; const requestGeneration = ++modalGeneration;
    body.replaceChildren(loading("Retrieving class analytics")); body.setAttribute("aria-busy", "true");
    try {
      const data = await fetchJSON(opener.dataset.analyticsUrl, controller);
      if (controller.signal.aborted || requestGeneration !== modalGeneration) return;
      const header = node("header", undefined, "gdm-class-header");
      header.append(node("h4", data.faculty_name, "h5"), node("p", `${data.course_code} — ${data.course_title} / ${data.section}`),
        node("p", `${data.campus} · ${data.school_year} / ${data.term} · ${data.status}`, "small mb-0"));
      header.append(node("p", "Counts require an approved 97–100 saved Period Grade release with All Statuses. Other analytics and unavailable releases are withheld.", "small text-muted mt-2 mb-0"));
      if (data.history_notice) header.append(node("p", data.history_notice, "small text-muted"));
      body.replaceChildren(header);
      const groups = [["period", "PERIOD GRADES", [data]],
        ["examination", "EXAMINATION GRADES", data.measures.filter(m => m.group === "examination")],
        ["standing", "CLASS STANDING GRADES", [...data.measures.filter(m => m.group === "standing"), ...(data.quiz ? [data.quiz] : [])]]];
      for (const [group, label, measures] of groups) {
        const section = node("section", undefined, "gdm-accordion");
        const heading = node("h4"), toggle = node("button", undefined, `gdm-accordion-toggle gdm-${group}`);
        const id = `gdm-panel-${requestGeneration}-${group}`; toggle.type = "button"; toggle.id = `${id}-toggle`;
        toggle.setAttribute("aria-expanded", String(group === "period")); toggle.setAttribute("aria-controls", id);
        toggle.append(node("span", label)); const chevron = node("span", "▸", "gdm-chevron"); chevron.setAttribute("aria-hidden", "true"); toggle.append(chevron);
        const panel = node("div"); panel.id = id; panel.hidden = group !== "period";
        panel.setAttribute("role", "region"); panel.setAttribute("aria-labelledby", toggle.id);
        toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; toggle.setAttribute("aria-expanded", String(!panel.hidden)); });
        measures.forEach(measure => panel.append(renderMeasure(measure)));
        if (!measures.length) panel.append(node("p", "No eligible saved measures are available.", "p-3"));
        heading.append(toggle); section.append(heading, panel); body.append(section);
      }
    } catch (error) {
      if (!controller.signal.aborted && requestGeneration === modalGeneration) {
        const alert = node("div", error.message, "alert alert-warning"); alert.setAttribute("role", "alert"); body.replaceChildren(alert);
      }
    } finally { if (requestGeneration === modalGeneration) body.removeAttribute("aria-busy"); }
  });
  modal.addEventListener("hidden.bs.modal", () => {
    detailAbort?.abort(); modalGeneration++; body.replaceChildren(); body.removeAttribute("aria-busy");
    if (opener?.isConnected && !results.hidden) opener.focus();
  });

});
