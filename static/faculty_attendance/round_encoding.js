(function () {
  "use strict";
  var index = document.querySelector("[data-round-index]");
  var layout = document.querySelector("[data-round-layout]");
  var indexPanel = document.querySelector("[data-round-index-panel]");
  var indexOpen = document.querySelector("[data-round-index-reopen]");
  var indexClose = document.querySelector("[data-round-index-close]");
  var mobileIndex = window.matchMedia ? window.matchMedia("(max-width: 991.98px)") : null;
  function setIndexOpen(open) {
    if (!layout || !indexPanel || !indexOpen) return;
    layout.classList.toggle("index-collapsed", !open);
    indexPanel.setAttribute("aria-hidden", String(!open));
    indexPanel.inert = !open;
    indexOpen.setAttribute("aria-expanded", String(open));
  }
  if (index) {
    var roundPage = index.closest("[data-daily-round]");
    if (roundPage) roundPage.classList.add("index-enhanced");
    setIndexOpen(!mobileIndex || !mobileIndex.matches);
    if (mobileIndex && mobileIndex.addEventListener) mobileIndex.addEventListener("change", function (event) { setIndexOpen(!event.matches); });
    if (indexOpen) indexOpen.addEventListener("click", function () { setIndexOpen(true); if (indexClose) indexClose.focus(); });
    if (indexClose) indexClose.addEventListener("click", function () { setIndexOpen(false); if (indexOpen) indexOpen.focus(); });
  }
  var roundRows = document.querySelector("#daily-round-rows");
  var timeFilter = document.querySelector("[data-round-filter-time]");
  var facultyFilter = document.querySelector("[data-round-filter-faculty]");
  var filterCount = document.querySelector("[data-round-filter-count]");
  var filterEmpty = document.querySelector("[data-round-filter-empty]");
  var filterBox = document.querySelector("[data-round-filters]");
  function matchesFilters(element) {
    return (!timeFilter || !timeFilter.value || element.dataset.filterTime === timeFilter.value) &&
      (!facultyFilter || !facultyFilter.value || element.dataset.filterFaculty === facultyFilter.value);
  }
  function applyRoundFilters() {
    if (!roundRows) return;
    var cards = Array.from(roundRows.querySelectorAll("[data-meeting-row]"));
    var visible = 0;
    cards.forEach(function (card) {
      var matches = matchesFilters(card);
      card.hidden = !matches;
      if (matches) visible += 1;
      else {
        var selection = card.querySelector('[name="present_rows"]');
        if (selection) selection.checked = false;
      }
    });
    document.querySelectorAll("[data-round-index-link]").forEach(function (link) {
      var card = document.getElementById((link.getAttribute("href") || "").slice(1));
      if (card) {
        link.dataset.filterTime = card.dataset.filterTime;
        link.dataset.filterFaculty = card.dataset.filterFaculty;
      }
      link.hidden = !card || card.hidden;
    });
    document.querySelectorAll("[data-round-index-group]").forEach(function (group) {
      group.hidden = !group.querySelector("[data-round-index-link]:not([hidden])");
    });
    if (filterCount) filterCount.textContent = "Showing " + visible + " of " + cards.length + " eligible classes.";
    if (filterEmpty) filterEmpty.hidden = visible > 0;
  }
  if (filterBox) filterBox.hidden = false;
  if (timeFilter) timeFilter.addEventListener("change", applyRoundFilters);
  if (facultyFilter) facultyFilter.addEventListener("change", applyRoundFilters);
  applyRoundFilters();
  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-round-index-link]");
    if (!link) return;
    var target = document.getElementById((link.getAttribute("href") || "").slice(1));
    if (!target || link.hidden || target.hidden) return;
    event.preventDefault();
    if (index && mobileIndex && mobileIndex.matches) setIndexOpen(false);
    target.focus({preventScroll: true});
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({behavior: reducedMotion ? "auto" : "smooth", block: "start"});
  });
  function replaceRow(markup, meetingId) {
    var current = document.querySelector('[data-meeting-row][data-meeting-id="' + meetingId + '"]');
    if (!current || !markup) return null;
    var holder = document.createElement("div"); holder.innerHTML = markup.trim();
    var replacement = holder.firstElementChild;
    if (!replacement) return null;
    current.replaceWith(replacement); return replacement;
  }
  function updateCounts(counts) { if (!counts) return; Object.keys(counts).forEach(function (name) { var element = document.querySelector('[data-count="' + name + '"]'); if (element) element.textContent = counts[name]; }); }
  function feedback(form, message, isError) { var element = form.closest("[data-meeting-row]").querySelector("[data-row-feedback]"); if (!element) return; element.textContent = message; element.classList.toggle("text-danger", Boolean(isError)); element.classList.toggle("text-success", !isError); }
  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-row-save]"); if (!form || !window.fetch) return;
    event.preventDefault(); if (form.dataset.saving === "true") return; form.dataset.saving = "true";
    var button = form.querySelector("button[type=submit]");
    if (button) { button.disabled = true; button.textContent = "Saving…"; }
    feedback(form, "Saving this class…", false);
    fetch(form.getAttribute("action") || window.location.href, {method: "POST", body: new FormData(form), credentials: "same-origin", headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}})
      .then(function (response) { return response.json().then(function (data) { return {response: response, data: data}; }); })
      .then(function (result) { var data = result.data || {}; var meetingId = form.querySelector('[name="meeting_id"]').value; if (data.row_html) { var replacement = replaceRow(data.row_html, meetingId); updateCounts(data.counts); applyRoundFilters(); if (replacement) { var status = replacement.querySelector("[data-row-feedback]"); if (status) { status.textContent = data.message || (result.response.ok ? "Finding saved." : "Save was not completed."); status.classList.toggle("text-danger", !result.response.ok); status.classList.toggle("text-success", result.response.ok); status.setAttribute("tabindex", "-1"); status.focus(); } } } else { feedback(form, data.message || "Save was not completed. Refresh and review this class.", true); } })
      .catch(function () { feedback(form, "Connection problem. Refresh and verify this class before submitting again.", true); })
      .finally(function () { if (document.body.contains(form)) { form.dataset.saving = "false"; if (button) { button.disabled = false; button.textContent = button.dataset.saveLabel || "Save finding"; } } });
  });
}());
