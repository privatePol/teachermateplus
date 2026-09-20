(function () {
  "use strict";

  const root = document.getElementById("departmental-exam-release-center");
  if (!root) return;
  const feedback = document.getElementById("release-center-feedback");
  const selected = {questionnaire: new Set(), "answer-key": new Set()};
  const showingSelected = {questionnaire: false, "answer-key": false};
  const detailRequest = {questionnaire: 0, "answer-key": 0};
  const refreshRequest = {questionnaire: 0, "answer-key": 0};
  const detailState = {
    questionnaire: {url: null, opener: null, lifecycle: 0, open: false},
    "answer-key": {url: null, opener: null, lifecycle: 0, open: false}
  };

  const config = {
    questionnaire: {
      pane: "questionnaire-releases-pane",
      form: "bulk-print-release-form",
      row: '[data-release-row="questionnaire"]',
      checkbox: ".bulk-release-selection",
      search: "questionnaire-course-search",
      department: "questionnaire-department-filter",
      status: "questionnaire-status-filter",
      selected: "bulk-selected-count",
      hidden: "bulk-hidden-selected-count",
      visible: "questionnaire-visible-count",
      eligible: null,
      selectAll: "bulk-select-all",
      empty: "questionnaire-no-filter-results"
    },
    "answer-key": {
      pane: "answer-key-releases-pane",
      form: "bulk-answer-key-release-form",
      row: '[data-answer-key-row="true"]',
      checkbox: ".bulk-answer-key-selection",
      search: "answer-key-course-search",
      department: "answer-key-department-filter",
      status: "answer-key-status-filter",
      selected: "bulk-answer-key-selected-count",
      hidden: "bulk-answer-key-hidden-selected-count",
      visible: "bulk-answer-key-visible-count",
      eligible: "bulk-answer-key-visible-eligible-count",
      selectAll: "bulk-answer-key-select-all",
      empty: "bulk-answer-key-no-filter-results"
    }
  };

  function byId(id) { return document.getElementById(id); }
  function rows(kind) {
    const form = byId(config[kind].form);
    return form ? Array.from(form.querySelectorAll(config[kind].row)) : [];
  }
  function inputFor(kind, row) { return row.querySelector(config[kind].checkbox); }
  function eligible(kind, row) {
    const input = inputFor(kind, row);
    return row.dataset.eligible === "true" && input && !input.disabled;
  }
  function announce(type, message) {
    if (!feedback) return;
    feedback.className = "alert alert-" + type;
    feedback.textContent = message;
  }
  function rememberNotice(message) {
    try { window.sessionStorage.setItem("tmp-release-center-notice", message); }
    catch (error) { announce("info", message); }
  }
  function restoreNotice() {
    try {
      const message = window.sessionStorage.getItem("tmp-release-center-notice");
      if (message) {
        window.sessionStorage.removeItem("tmp-release-center-notice");
        announce("info", message);
      }
    } catch (error) { /* Storage is optional. */ }
  }
  function filterValues(kind) {
    const c = config[kind];
    return {
      search: byId(c.search) ? byId(c.search).value : "",
      department: byId(c.department) ? byId(c.department).value : "",
      status: byId(c.status) ? byId(c.status).value : ""
    };
  }
  function restoreFilters(kind, values) {
    const c = config[kind];
    [[c.search, values.search], [c.department, values.department], [c.status, values.status]]
      .forEach(function (pair) { if (byId(pair[0])) byId(pair[0]).value = pair[1]; });
  }
  function matches(kind, row, values) {
    const query = values.search.trim().toLocaleLowerCase();
    const searchText = (row.dataset.courseSearch || "").toLocaleLowerCase();
    const departmentIds = kind === "questionnaire"
      ? [row.dataset.departmentId || ""]
      : (row.dataset.departmentIds || "").trim().split(/\s+/).filter(Boolean);
    const departmentMatch = !values.department ||
      (values.department === "__none__"
        ? departmentIds.length === 0 || departmentIds[0] === ""
        : departmentIds.includes(values.department));
    return (!query || searchText.includes(query)) && departmentMatch &&
      (!values.status || row.dataset.releaseStatus === values.status);
  }
  function update(kind) {
    const c = config[kind];
    const allRows = rows(kind);
    const visibleEligible = allRows.filter(function (row) { return !row.hidden && eligible(kind, row); });
    const visibleSelected = visibleEligible.filter(function (row) {
      return selected[kind].has(inputFor(kind, row).value);
    }).length;
    allRows.forEach(function (row) {
      const input = inputFor(kind, row);
      if (input) input.checked = selected[kind].has(input.value);
    });
    if (byId(c.selected)) byId(c.selected).textContent = selected[kind].size;
    if (byId(c.hidden)) byId(c.hidden).textContent = selected[kind].size - visibleSelected;
    if (byId(c.visible)) byId(c.visible).textContent =
      allRows.filter(function (row) { return !row.hidden; }).length;
    if (c.eligible && byId(c.eligible)) byId(c.eligible).textContent = visibleEligible.length;
    const all = byId(c.selectAll);
    if (all) {
      all.disabled = visibleEligible.length === 0;
      all.checked = visibleEligible.length > 0 && visibleSelected === visibleEligible.length;
      all.indeterminate = visibleSelected > 0 && visibleSelected < visibleEligible.length;
    }
    const empty = byId(c.empty);
    if (empty) empty.hidden = allRows.length === 0 || allRows.some(function (row) { return !row.hidden; });
    const show = root.querySelector('[data-release-show-selected="' + kind + '"]');
    if (show) {
      show.setAttribute("aria-pressed", showingSelected[kind] ? "true" : "false");
      show.textContent = showingSelected[kind] ? "Show all matching" : "Show selected";
    }
  }
  function applyFilters(kind) {
    const values = filterValues(kind);
    rows(kind).forEach(function (row) {
      const input = inputFor(kind, row);
      row.hidden = showingSelected[kind]
        ? !(input && selected[kind].has(input.value))
        : !matches(kind, row, values);
    });
    update(kind);
  }
  function reconcile(kind) {
    const available = new Set(rows(kind).filter(function (row) {
      return eligible(kind, row);
    }).map(function (row) { return inputFor(kind, row).value; }));
    let removed = 0;
    selected[kind].forEach(function (value) {
      if (!available.has(value)) { selected[kind].delete(value); removed += 1; }
    });
    if (removed) announce("warning", removed + " stale or unavailable selection" +
      (removed === 1 ? " was" : "s were") + " removed.");
  }
  function initialize(kind) {
    const c = config[kind];
    const form = byId(c.form);
    if (!form || form.dataset.releaseInitialized === "true") return;
    form.querySelectorAll(c.checkbox).forEach(function (input) {
      if (input.checked && !input.disabled) selected[kind].add(input.value);
      input.addEventListener("change", function () {
        if (input.checked) selected[kind].add(input.value);
        else selected[kind].delete(input.value);
        update(kind);
      });
    });
    reconcile(kind);
    [c.search, c.department, c.status].forEach(function (id) {
      const control = byId(id);
      if (control) control.addEventListener(id === c.search ? "input" : "change", function () {
        applyFilters(kind);
      });
    });
    const all = byId(c.selectAll);
    if (all) all.addEventListener("change", function () {
      rows(kind).forEach(function (row) {
        if (!row.hidden && eligible(kind, row)) {
          const value = inputFor(kind, row).value;
          if (all.checked) selected[kind].add(value);
          else selected[kind].delete(value);
        }
      });
      update(kind);
    });
    form.dataset.releaseInitialized = "true";
    applyFilters(kind);
  }
  function clearSelection(kind, message) {
    selected[kind].clear();
    showingSelected[kind] = false;
    applyFilters(kind);
    if (message) announce("info", message);
  }
  function detailBody(kind) { return byId(kind + "-details-body"); }
  function detailsModal(kind) { return byId(kind + "-details-modal"); }
  function invalidateRefresh(kind) { refreshRequest[kind] += 1; }
  function invalidateDetails(kind) {
    detailRequest[kind] += 1;
    detailState[kind].lifecycle += 1;
    detailState[kind].url = null;
    detailState[kind].open = false;
  }
  function detailError(kind) {
    const body = detailBody(kind);
    if (!body) return;
    body.replaceChildren();
    const alert = document.createElement("div");
    alert.className = "alert alert-warning";
    alert.setAttribute("role", "alert");
    alert.append("Details could not be loaded. ");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn-sm btn-outline-secondary";
    retry.textContent = "Retry";
    retry.addEventListener("click", function () {
      loadDetails(kind, detailState[kind].url, detailState[kind].opener, false);
    });
    alert.append(retry);
    body.append(alert);
  }
  function loadDetails(kind, url, opener, openModal, preserveLifecycle) {
    const body = detailBody(kind);
    const element = detailsModal(kind);
    if (!body || !element || !url) return;
    detailState[kind].url = url;
    detailState[kind].opener = opener;
    if (!preserveLifecycle) detailState[kind].lifecycle += 1;
    if (openModal) detailState[kind].open = true;
    const request = ++detailRequest[kind];
    body.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "text-muted";
    loading.setAttribute("role", "status");
    loading.textContent = "Loading " + (kind === "answer-key" ? "Answer Key" : "questionnaire") + " release details…";
    body.append(loading);
    if (openModal && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(element).show(opener);
    }
    window.fetch(url, {
      method: "GET", credentials: "same-origin", cache: "no-store",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html"}
    }).then(function (response) {
      if (!response.ok) throw new Error("Details request failed.");
      return response.text();
    }).then(function (html) {
      if (request !== detailRequest[kind]) return;
      body.innerHTML = html;
    }).catch(function () {
      if (request === detailRequest[kind]) detailError(kind);
    });
  }
  function refresh(section) {
    const kind = section === "answer-key-releases" ? "answer-key" : "questionnaire";
    const pane = byId(config[kind].pane);
    if (!pane) return Promise.resolve(false);
    const values = filterValues(kind);
    const selectedBefore = selected[kind].size;
    const url = new URL(window.location.href);
    url.searchParams.set("section", section);
    if (kind === "answer-key" && !url.searchParams.get("target_campus_id")) {
      const campus = byId("answer-key-campus-filter");
      if (campus && campus.value) url.searchParams.set("target_campus_id", campus.value);
    }
    const request = ++refreshRequest[kind];
    return window.fetch(url.pathname + url.search, {
      method: "GET", credentials: "same-origin", cache: "no-store",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html"}
    }).then(function (response) {
      if (!response.ok) throw new Error("Updated release status could not be loaded.");
      return response.text();
    }).then(function (html) {
      if (request !== refreshRequest[kind]) return false;
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const fresh = parsed.getElementById(config[kind].pane);
      if (!fresh) throw new Error("Updated release section is unavailable.");
      const existingModal = detailsModal(kind);
      const freshModal = fresh.querySelector("#" + kind + "-details-modal");
      if (existingModal && freshModal) freshModal.remove();
      pane.innerHTML = fresh.innerHTML;
      if (existingModal) pane.append(existingModal);
      restoreFilters(kind, values);
      if (!byId(config[kind].form)) {
        reconcile(kind);
        showingSelected[kind] = false;
        return {removed: selectedBefore - selected[kind].size};
      }
      initialize(kind);
      return {removed: selectedBefore - selected[kind].size};
    });
  }

  root.addEventListener("click", function (event) {
    const clearFilters = event.target.closest("[data-release-clear], #answer-key-clear-filters");
    if (clearFilters) {
      const kind = clearFilters.dataset.releaseClear || "answer-key";
      const c = config[kind];
      [c.search, c.department, c.status].forEach(function (id) { if (byId(id)) byId(id).value = ""; });
      showingSelected[kind] = false;
      applyFilters(kind);
      return;
    }
    const show = event.target.closest("[data-release-show-selected]");
    if (show) {
      const kind = show.dataset.releaseShowSelected;
      showingSelected[kind] = !showingSelected[kind];
      applyFilters(kind);
      return;
    }
    const clear = event.target.closest("[data-release-clear-selection]");
    if (clear) { clearSelection(clear.dataset.releaseClearSelection, "Selection cleared."); return; }
    const detail = event.target.closest("[data-questionnaire-details-url], [data-answer-key-details-url]");
    if (detail && window.fetch && window.bootstrap && window.bootstrap.Modal) {
      event.preventDefault();
      const kind = detail.dataset.answerKeyDetailsUrl ? "answer-key" : "questionnaire";
      loadDetails(
        kind,
        detail.dataset.answerKeyDetailsUrl || detail.dataset.questionnaireDetailsUrl,
        detail,
        true
      );
    }
  });
  ["questionnaire", "answer-key"].forEach(function (kind) {
    const modal = detailsModal(kind);
    if (!modal) return;
    modal.addEventListener("hide.bs.modal", function () { invalidateDetails(kind); });
    modal.addEventListener("hidden.bs.modal", function () {
      invalidateDetails(kind);
      const opener = detailState[kind].opener;
      if (opener && document.contains(opener)) opener.focus();
    });
  });
  const targetForm = byId("answer-key-target-form");
  if (targetForm) {
    const campus = byId("answer-key-campus-filter");
    if (campus) campus.addEventListener("change", function () {
      invalidateDetails("answer-key");
      invalidateRefresh("answer-key");
      if (selected["answer-key"].size) rememberNotice("Target campus changed; Answer Key selections were cleared.");
      clearSelection("answer-key");
      targetForm.requestSubmit();
    });
  }
  const cycle = byId("exam-cycle-status");
  if (cycle) cycle.addEventListener("change", function () {
    invalidateDetails("questionnaire");
    invalidateDetails("answer-key");
    invalidateRefresh("questionnaire");
    invalidateRefresh("answer-key");
    if (selected.questionnaire.size || selected["answer-key"].size) {
      rememberNotice("Cycle changed; Release Center selections were cleared.");
    }
    clearSelection("questionnaire");
    clearSelection("answer-key");
  });
  root.addEventListener("shown.bs.tab", function (event) {
    const target = event.target.getAttribute("data-bs-target");
    if (target && window.history && window.history.replaceState) {
      window.history.replaceState(null, "", target);
    }
  });
  const requestedHash = window.location.hash === "#bulk-answer-key-release"
    ? "#answer-key-releases-pane" : window.location.hash;
  const hashTab = root.querySelector('[data-bs-target="' + requestedHash + '"]');
  if (hashTab && !hashTab.classList.contains("active")) hashTab.click();

  root.addEventListener("submit", function (event) {
    const form = event.target.closest('form[data-release-ajax="true"]');
    if (!form || !window.fetch || !window.FormData) return;
    const submitter = event.submitter || null;
    if (submitter && submitter.dataset.reviewRelease) {
      const kind = submitter.dataset.reviewRelease;
      if (!selected[kind].size) {
        event.preventDefault();
        announce("warning", "Select at least one eligible target before review.");
        return;
      }
      const c = config[kind];
      form.querySelectorAll(c.checkbox).forEach(function (input) {
        input.checked = selected[kind].has(input.value);
      });
      // Keep the ordinary CSRF POST so the server renders the complete review.
      return;
    }
    event.preventDefault();
    if (form.dataset.releaseSubmitting === "true") return;
    form.dataset.releaseSubmitting = "true";
    const requestUrl = form.getAttribute("action") || window.location.href;
    const detailKind = form.dataset.releaseSection === "answer-key-releases"
      ? "answer-key" : "questionnaire";
    const detailAfter = detailState[detailKind].url;
    const openerAfter = detailState[detailKind].opener;
    const detailLifecycleAfter = detailState[detailKind].lifecycle;
    const body = submitter ? new FormData(form, submitter) : new FormData(form);
    window.fetch(requestUrl, {
      method: "POST", body: body, credentials: "same-origin",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok || payload.success !== true) {
          throw new Error(payload.message || "The release could not be completed.");
        }
        return payload;
      });
    }).then(function (payload) {
      return refresh(payload.section).then(function (applied) {
        if (!applied) return;
        announce("success", payload.message + (applied.removed
          ? " " + applied.removed + " stale or unavailable selection" +
            (applied.removed === 1 ? " was" : "s were") + " removed."
          : ""));
        const refreshedKind = payload.section === "answer-key-releases"
          ? "answer-key" : "questionnaire";
        if (refreshedKind === detailKind && detailAfter &&
            detailState[detailKind].open &&
            detailState[detailKind].url === detailAfter &&
            detailState[detailKind].lifecycle === detailLifecycleAfter) {
          const attribute = refreshedKind === "answer-key"
            ? "data-answer-key-details-url" : "data-questionnaire-details-url";
          const newOpener = root.querySelector(
            "[" + attribute + '=\"' + detailAfter + '\"]'
          );
          loadDetails(refreshedKind, detailAfter, newOpener || openerAfter, true, true);
        }
      });
    }).catch(function (error) {
      announce("warning", error.message ||
        "The action may have completed. Review current status before retrying.");
    }).finally(function () { delete form.dataset.releaseSubmitting; });
  });

  initialize("questionnaire");
  initialize("answer-key");
  restoreNotice();
}());
