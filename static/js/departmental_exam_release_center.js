(function () {
  "use strict";

  const root = document.getElementById("departmental-exam-release-center");
  if (!root) return;

  const feedback = document.getElementById("release-center-feedback");

  function tokens(value) {
    return (value || "").trim().split(/\s+/).filter(Boolean);
  }

  function showFeedback(kind, message, errors) {
    if (!feedback) return;
    feedback.className = "alert alert-" + kind;
    feedback.replaceChildren();
    const text = document.createElement("div");
    text.textContent = message;
    feedback.appendChild(text);
    if (errors && errors.length > 1) {
      const list = document.createElement("ul");
      list.className = "mb-0 mt-2";
      errors.slice(1).forEach(function (error) {
        const item = document.createElement("li");
        item.textContent = error;
        list.appendChild(item);
      });
      feedback.appendChild(list);
    }
  }

  function focusWithoutScrolling(element) {
    if (!element) return;
    try {
      element.focus({preventScroll: true});
    } catch (error) {
      element.focus();
    }
  }

  function initializeQuestionnaireSelection() {
    const form = document.getElementById("bulk-print-release-form");
    if (!form || form.dataset.selectionInitialized === "true") return;
    const selections = Array.from(
      form.querySelectorAll(".bulk-release-selection:not(:disabled)")
    );
    const count = form.querySelector("#bulk-selected-count");
    const selectAll = form.querySelector("#bulk-select-all");
    if (!count || !selectAll) return;
    const updateSelectionState = function () {
      const selectedCount = selections.filter(function (selection) {
        return selection.checked;
      }).length;
      count.textContent = selectedCount;
      selectAll.checked = selections.length > 0 && selectedCount === selections.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < selections.length;
    };
    selectAll.addEventListener("change", function () {
      selections.forEach(function (selection) {
        selection.checked = selectAll.checked;
      });
      updateSelectionState();
    });
    selections.forEach(function (selection) {
      selection.addEventListener("change", updateSelectionState);
    });
    form.dataset.selectionInitialized = "true";
    updateSelectionState();
  }

  function answerKeyFilterState() {
    const search = document.getElementById("answer-key-course-search");
    const department = document.getElementById("answer-key-department-filter");
    const campus = document.getElementById("answer-key-campus-filter");
    const status = document.getElementById("answer-key-status-filter");
    return {
      search: search ? search.value : "",
      department: department ? department.value : "",
      campus: campus ? campus.value : "",
      status: status ? status.value : ""
    };
  }

  function restoreAnswerKeyFilterState(state) {
    if (!state) return;
    [
      ["answer-key-course-search", state.search],
      ["answer-key-department-filter", state.department],
      ["answer-key-campus-filter", state.campus],
      ["answer-key-status-filter", state.status]
    ].forEach(function (entry) {
      const control = document.getElementById(entry[0]);
      if (control) control.value = entry[1] || "";
    });
  }

  function initializeAnswerKeyFilters() {
    const form = document.getElementById("bulk-answer-key-release-form");
    if (!form || form.dataset.filterInitialized === "true") return;
    const rows = Array.from(form.querySelectorAll('[data-answer-key-row="true"]'));
    const search = document.getElementById("answer-key-course-search");
    const department = document.getElementById("answer-key-department-filter");
    const campus = document.getElementById("answer-key-campus-filter");
    const status = document.getElementById("answer-key-status-filter");
    const clear = document.getElementById("answer-key-clear-filters");
    const visibleCount = document.getElementById("bulk-answer-key-visible-count");
    const visibleEligibleCount = document.getElementById(
      "bulk-answer-key-visible-eligible-count"
    );
    const selectedCount = document.getElementById("bulk-answer-key-selected-count");
    const selectAll = document.getElementById("bulk-answer-key-select-all");
    const noResults = document.getElementById("bulk-answer-key-no-filter-results");
    if (!search || !department || !campus || !status || !selectAll) return;

    const visibleEligibleRows = function () {
      return rows.filter(function (row) {
        const selection = row.querySelector(".bulk-answer-key-selection");
        return !row.hidden && row.dataset.eligible === "true" &&
          selection && !selection.disabled;
      });
    };

    const deselectUnavailableRows = function () {
      rows.forEach(function (row) {
        const selection = row.querySelector(".bulk-answer-key-selection");
        if (
          selection &&
          (row.hidden || row.dataset.eligible !== "true" || selection.disabled)
        ) {
          selection.checked = false;
        }
      });
    };

    const updateSelectionState = function () {
      const visibleRows = visibleEligibleRows();
      const visibleSelected = visibleRows.filter(function (row) {
        return row.querySelector(".bulk-answer-key-selection").checked;
      }).length;
      if (selectedCount) selectedCount.textContent = visibleSelected;
      if (visibleEligibleCount) visibleEligibleCount.textContent = visibleRows.length;
      selectAll.disabled = visibleRows.length === 0;
      selectAll.checked = visibleRows.length > 0 && visibleSelected === visibleRows.length;
      selectAll.indeterminate = visibleSelected > 0 && visibleSelected < visibleRows.length;
    };

    const applyFilters = function () {
      const query = search.value.trim().toLocaleLowerCase();
      const departmentValue = department.value;
      const campusValue = campus.value;
      const statusValue = status.value;
      rows.forEach(function (row) {
        const departmentIds = tokens(row.dataset.departmentIds);
        const matchesDepartment = !departmentValue ||
          (departmentValue === "__none__" ? departmentIds.length === 0 :
            departmentIds.includes(departmentValue));
        const matchesCampus = !campusValue ||
          tokens(row.dataset.campusIds).includes(campusValue);
        row.hidden = !(
          (!query || (row.dataset.courseSearch || "").toLocaleLowerCase().includes(query)) &&
          matchesDepartment &&
          matchesCampus &&
          (!statusValue || row.dataset.releaseStatus === statusValue)
        );
      });
      deselectUnavailableRows();
      const shown = rows.filter(function (row) { return !row.hidden; }).length;
      if (visibleCount) visibleCount.textContent = shown;
      if (noResults) noResults.hidden = shown !== 0 || rows.length === 0;
      updateSelectionState();
    };

    selectAll.addEventListener("change", function () {
      visibleEligibleRows().forEach(function (row) {
        const selection = row.querySelector(".bulk-answer-key-selection");
        selection.checked = selectAll.checked;
      });
      updateSelectionState();
    });
    rows.forEach(function (row) {
      const selection = row.querySelector(".bulk-answer-key-selection");
      if (selection) selection.addEventListener("change", updateSelectionState);
    });
    form.addEventListener("submit", function () {
      deselectUnavailableRows();
      updateSelectionState();
    });
    [search, department, campus, status].forEach(function (control) {
      control.addEventListener(control === search ? "input" : "change", applyFilters);
    });
    if (clear) {
      clear.addEventListener("click", function () {
        search.value = "";
        department.value = "";
        campus.value = "";
        status.value = "";
        applyFilters();
        search.focus();
      });
    }
    form.dataset.filterInitialized = "true";
    applyFilters();
  }

  function initializeReleaseControls() {
    initializeQuestionnaireSelection();
    initializeAnswerKeyFilters();
  }

  function selectedValues(form, selector) {
    if (!form) return [];
    return Array.from(form.querySelectorAll(selector + ":checked")).map(function (input) {
      return input.value;
    });
  }

  function restoreSelectedValues(form, selector, values) {
    if (!form || !values) return;
    const selected = new Set(values);
    form.querySelectorAll(selector).forEach(function (input) {
      input.checked = !input.disabled && selected.has(input.value);
    });
  }

  async function refreshReleaseSection(payload, submittedAction) {
    const section = payload.section;
    const currentPane = document.getElementById(section + "-pane");
    if (!currentPane) throw new Error("Release section is unavailable.");
    const filterState = answerKeyFilterState();
    const currentBulkForm = document.getElementById(
      section === "answer-key-releases" ?
        "bulk-answer-key-release-form" : "bulk-print-release-form"
    );
    const selectionSelector = section === "answer-key-releases" ?
      ".bulk-answer-key-selection" : ".bulk-release-selection";
    const preservedSelections = submittedAction.indexOf("bulk_") === 0 ? [] :
      selectedValues(currentBulkForm, selectionSelector);
    const refreshUrl = payload.refresh_url + "?section=" + encodeURIComponent(section);
    const response = await window.fetch(refreshUrl, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html"}
    });
    if (!response.ok) throw new Error("Updated release status could not be loaded.");
    const documentText = await response.text();
    const parsed = new DOMParser().parseFromString(documentText, "text/html");
    const refreshedPane = parsed.getElementById(section + "-pane");
    if (!refreshedPane) throw new Error("Updated release section is unavailable.");

    const bulkId = section === "answer-key-releases" ?
      "bulk-answer-key-release" : "bulk-print-release";
    const currentBulk = document.getElementById(bulkId);
    const refreshedBulk = refreshedPane.querySelector("#" + bulkId);
    if (currentBulk && refreshedBulk) currentBulk.replaceWith(refreshedBulk);

    (payload.affected_course_ids || []).forEach(function (courseId) {
      const prefix = section === "answer-key-releases" ?
        "answer-key-course-" : "questionnaire-course-";
      const currentCourse = document.getElementById(prefix + courseId);
      const refreshedCourse = refreshedPane.querySelector("#" + prefix + courseId);
      if (currentCourse && refreshedCourse) currentCourse.replaceWith(refreshedCourse);
    });

    restoreAnswerKeyFilterState(filterState);
    const newBulkForm = document.getElementById(
      section === "answer-key-releases" ?
        "bulk-answer-key-release-form" : "bulk-print-release-form"
    );
    restoreSelectedValues(newBulkForm, selectionSelector, preservedSelections);
    initializeReleaseControls();
  }

  function processingButtons(form, submitter) {
    const buttons = Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]'));
    if (submitter && !buttons.includes(submitter)) buttons.push(submitter);
    buttons.forEach(function (button) {
      button.dataset.releaseOriginalDisabled = button.disabled ? "true" : "false";
      button.dataset.releaseOriginalText = button.textContent;
      button.disabled = true;
    });
    if (submitter && submitter.dataset.processingLabel) {
      submitter.textContent = submitter.dataset.processingLabel;
    }
    form.setAttribute("aria-busy", "true");
    document.body.classList.add("de-release-processing");
    return buttons;
  }

  function restoreProcessing(form, buttons) {
    buttons.forEach(function (button) {
      button.disabled = button.dataset.releaseOriginalDisabled === "true";
      if (button.dataset.releaseOriginalText !== undefined) {
        button.textContent = button.dataset.releaseOriginalText;
      }
      delete button.dataset.releaseOriginalDisabled;
      delete button.dataset.releaseOriginalText;
    });
    form.removeAttribute("aria-busy");
    delete form.dataset.ajaxSubmitting;
    document.body.classList.remove("de-release-processing");
  }

  async function submitReleaseForm(form, submitter) {
    if (form.dataset.ajaxSubmitting === "true") return;
    form.dataset.ajaxSubmitting = "true";
    const scrollPosition = window.scrollY;
    const action = form.dataset.releaseAction || "";
    const courseId = form.dataset.releaseCourseId || "";
    const section = form.dataset.releaseSection || "questionnaire-releases";
    const buttons = processingButtons(form, submitter);
    let requestPromise;
    try {
      requestPromise = window.fetch(form.action || window.location.href, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
      });
    } catch (error) {
      restoreProcessing(form, buttons);
      HTMLFormElement.prototype.submit.call(form);
      return;
    }

    try {
      const response = await requestPromise;
      const contentType = response.headers.get("content-type") || "";
      const payload = contentType.includes("application/json") ? await response.json() : null;
      if (!response.ok || !payload || payload.success !== true) {
        const message = payload && payload.message ? payload.message :
          "The request was denied or could not be completed.";
        showFeedback("danger", message, payload && payload.errors ? payload.errors : []);
        focusWithoutScrolling(feedback);
        window.scrollTo(0, scrollPosition);
        return;
      }
      await refreshReleaseSection(payload, action);
      showFeedback("success", payload.message, []);
      const focusSelector = 'form[data-release-section="' + section + '"]' +
        '[data-release-action="' + action + '"]' +
        (courseId ? '[data-release-course-id="' + courseId + '"]' : "");
      const refreshedForm = root.querySelector(focusSelector);
      const focusTarget = refreshedForm ? refreshedForm.querySelector('button[type="submit"]') : feedback;
      focusWithoutScrolling(focusTarget || feedback);
      window.scrollTo(0, scrollPosition);
    } catch (error) {
      showFeedback(
        "warning",
        "The action may have completed, but the updated release status could not be displayed. Review the current status before retrying.",
        []
      );
      focusWithoutScrolling(feedback);
      window.scrollTo(0, scrollPosition);
    } finally {
      if (document.documentElement.contains(form)) restoreProcessing(form, buttons);
      else document.body.classList.remove("de-release-processing");
    }
  }

  if (window.fetch && window.FormData && window.DOMParser) {
    root.addEventListener("submit", function (event) {
      const form = event.target.closest('form[data-release-ajax="true"]');
      if (!form) return;
      event.preventDefault();
      submitReleaseForm(form, event.submitter || null);
    });
  }

  root.addEventListener("shown.bs.tab", function (event) {
    const target = event.target.getAttribute("data-bs-target");
    if (target && window.history && window.history.replaceState) {
      window.history.replaceState(null, "", target);
    }
  });

  const requestedHash = window.location.hash === "#bulk-answer-key-release" ?
    "#answer-key-releases-pane" : window.location.hash;
  const hashTab = root.querySelector('[data-bs-target="' + requestedHash + '"]');
  if (hashTab && !hashTab.classList.contains("active")) hashTab.click();
  initializeReleaseControls();
})();
