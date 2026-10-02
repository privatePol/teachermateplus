(function () {
  "use strict";
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
      .then(function (result) { var data = result.data || {}; var meetingId = form.querySelector('[name="meeting_id"]').value; if (data.row_html) { var replacement = replaceRow(data.row_html, meetingId); updateCounts(data.counts); if (replacement) { var status = replacement.querySelector("[data-row-feedback]"); if (status) { status.textContent = data.message || (result.response.ok ? "Finding saved." : "Save was not completed."); status.classList.toggle("text-danger", !result.response.ok); status.classList.toggle("text-success", result.response.ok); status.setAttribute("tabindex", "-1"); status.focus(); } } } else { feedback(form, data.message || "Save was not completed. Refresh and review this class.", true); } })
      .catch(function () { feedback(form, "Connection problem. Refresh and verify this class before submitting again.", true); })
      .finally(function () { if (document.body.contains(form)) { form.dataset.saving = "false"; if (button) { button.disabled = false; button.textContent = button.dataset.saveLabel || "Save finding"; } } });
  });
}());
