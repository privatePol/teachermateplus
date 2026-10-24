/* Insights only. No automatic report requests, including browser history restoration. */
(() => {
  "use strict";
  const form = document.getElementById("insights-filters");
  if (!form) return;
  const summary = document.getElementById("insights-summary");
  const classes = document.getElementById("insights-classes");
  const detail = document.getElementById("insights-detail");
  const dialog = document.getElementById("insights-dialog");
  const message = document.getElementById("insights-message");
  const progress = document.getElementById("insights-progress");
  const view = document.getElementById("insights-view");
  const retry = document.getElementById("insights-retry-selections");
  const labels = {academic_year_id: "Academic Year", term_id: "Term", campus_id: "Campus", department_id: "Department", course_code: "Course", faculty_id: "Faculty", period_code: "Grading Period"};
  const emptyLabels = {academic_year_id: "Select academic year", term_id: "Select term", period_code: "Select grading period", campus_id: "All authorized campuses", department_id: "All authorized departments", course_code: "All course codes", faculty_id: "All supervised faculty"};
  let generation = 0;
  let token = null;
  let selectionsVerified = true;
  const pending = new Map();
  const selected = JSON.parse(document.getElementById("insights-selected").textContent);
  form.querySelectorAll("select").forEach(select => { select.value = selected[select.name] || ""; if (select.options?.[0]) select.options[0].textContent = emptyLabels[select.name]; });
  form.querySelectorAll("[data-label]").forEach(label => { label.textContent = labels[label.dataset.label]; });
  function status(text, error = false) {
    message.textContent = text;
    message.className = error ? "alert alert-danger" : "alert alert-info";
  }
  function exports() {
    const params = new URLSearchParams(new FormData(form));
    params.delete("csrfmiddlewaretoken");
    if (token) params.set("token", token);
    document.querySelectorAll("[data-export]").forEach(link => {
      params.set("export", link.dataset.export);
      link.href = `${location.pathname}?${params}`;
      link.setAttribute("aria-disabled", String(!selectionsVerified));
    });
  }
  function clear() {
    generation += 1;
    pending.forEach(controller => controller.abort());
    pending.clear();
    token = null;
    summary.replaceChildren(); classes.replaceChildren(); detail.replaceChildren();
    if (dialog.open) dialog.close();
    progress.hidden = true;
    status("Select your filters and click View Performance.");
  }
  async function request(action, values = {}) {
    const epoch = generation;
    const controller = new AbortController();
    pending.get(action)?.abort();
    pending.set(action, controller);
    progress.hidden = false;
    const data = new FormData(form);
    data.set("action", action);
    if (token) data.set("token", token);
    Object.entries(values).forEach(([key, value]) => data.set(key, value));
    try {
      const response = await fetch(location.pathname, {method: "POST", body: data, signal: controller.signal, credentials: "same-origin", headers: {"Accept": "application/json"}});
      if (epoch !== generation || pending.get(action) !== controller) return;
      if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Your session may have expired. Reload the page and try again.");
      const result = await response.json();
      if (epoch !== generation || pending.get(action) !== controller) return;
      if (response.status === 409 && action !== "options") {
        clear(); exports(); view.disabled = !selectionsVerified;
        status(result.error || "Academic records have changed. Please click View Performance to refresh the report.", true);
        return;
      }
      if (!response.ok) throw new Error(result.error || "Unable to load academic information.");
      return result;
    } catch (error) {
      if (error.name === "AbortError" || epoch !== generation || pending.get(action) !== controller) return;
      status(error.message, true);
      return null;
    } finally {
      if (pending.get(action) === controller) {
        pending.delete(action);
        progress.hidden = pending.size === 0;
      }
    }
  }
  async function verifySelections() {
    const epoch = generation;
    selectionsVerified = false;
    view.disabled = true; retry.hidden = true; retry.disabled = true;
    exports();
    const result = await request("options");
    if (epoch !== generation || result === undefined) return;
    if (result === null) {
      status("Unable to load the selections. Please try again.", true);
      retry.hidden = false; retry.disabled = false;
      return;
    }
    Object.entries(result.options).forEach(([name, options]) => {
      const select = form.elements.namedItem(name);
      select.replaceChildren(new Option(emptyLabels[name], ""));
      options.forEach(option => select.add(new Option(option.label, option.value)));
      select.value = result.filters[name] || "";
    });
    selectionsVerified = true;
    exports(); view.disabled = false; retry.hidden = true;
    status("Select your filters and click View Performance.");
  }
  form.addEventListener("change", () => {
    clear();
    return verifySelections();
  });
  retry.addEventListener("click", () => {
    if (retry.disabled || selectionsVerified) return;
    return verifySelections();
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!selectionsVerified || view.disabled || !form.reportValidity()) return;
    clear(); exports(); view.disabled = true;
    const epoch = generation;
    const result = await request("report");
    if (epoch !== generation) return;
    view.disabled = false;
    if (!result) return;
    token = result.token;
    exports();
    summary.innerHTML = result.html;
    status("Performance snapshot ready. Open classes or details when needed. Refresh with View Performance after grade changes.");
  });
  document.addEventListener("click", async event => {
    if (event.target.closest("[data-export]") && !selectionsVerified) {
      event.preventDefault();
      status("Unable to load the selections. Please try again.", true);
      return;
    }
    const page = event.target.closest("[data-page]");
    const target = event.target.closest("[data-detail]");
    if ((!page && !target) || !token) return;
    const epoch = generation;
    const result = await request(page ? "classes" : "detail", page ? {page: page.dataset.page} : {offering_id: target.dataset.detail});
    if (!result || epoch !== generation) return;
    if (page) classes.innerHTML = result.html;
    else { detail.innerHTML = result.html; if (!dialog.open) dialog.showModal(); }
  });
  document.getElementById("insights-close").addEventListener("click", () => dialog.close());
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", event => {
    if (event.persisted) {
      clear(); exports();
      // A page hidden during an options request still needs verified choices.
      if (!selectionsVerified) verifySelections();
      else view.disabled = false;
    }
  });
  exports();
})();
