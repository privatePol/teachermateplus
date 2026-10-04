/* Scoped attendance progress for normal navigation and AJAX. No invented completion. */
(function () {
  "use strict";
  function status(form) {
    var element = form.querySelector("[data-processing-status]");
    if (!element) {
      element = document.createElement("div");
      element.dataset.processingStatus = "";
      element.className = "attendance-processing mt-2";
      element.setAttribute("role", "status");
      element.setAttribute("aria-live", "polite");
      form.appendChild(element);
    }
    return element;
  }
  function end(form, message, error) {
    form.dataset.processing = "false";
    form.removeAttribute("aria-busy");
    form.querySelectorAll("[data-processing-disabled]").forEach(function (button) {
      button.disabled = false; delete button.dataset.processingDisabled;
    });
    var element = status(form);
    element.replaceChildren();
    element.textContent = message || "";
    element.classList.toggle("text-danger", Boolean(error));
    element.classList.toggle("text-success", Boolean(message) && !error);
  }
  function begin(form, submitter) {
    if (form.dataset.processing === "true" || form.dataset.processingUnknown === "true") return false;
    if (!form.checkValidity()) return false;
    // Disabled submit buttons are not successful controls in a native POST.
    // Freeze the actual clicked action before disabling either action button.
    if (submitter && submitter.name) {
      var clicked = form.querySelector("[data-processing-submitter]");
      if (!clicked) {
        clicked = document.createElement("input");
        clicked.type = "hidden"; clicked.dataset.processingSubmitter = "";
        form.appendChild(clicked);
      }
      clicked.name = submitter.name; clicked.value = submitter.value;
    }
    form.dataset.processing = "true";
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button:not([type="button"]), input[type="submit"]').forEach(function (button) {
      if (!button.disabled) { button.dataset.processingDisabled = ""; button.disabled = true; }
    });
    var element = status(form);
    element.replaceChildren();
    element.classList.remove("text-danger", "text-success");
    var progress = document.createElement("progress");
    progress.setAttribute("aria-label", "Processing attendance request");
    progress.style.marginRight = ".5rem";
    element.append(progress, document.createTextNode(form.dataset.attendanceProcessing || "Processing… Please wait."));
    return true;
  }
  window.AttendanceProcessing = {begin: begin, end: end};
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("form[data-attendance-processing]") || event.defaultPrevented) return;
    // The rounds AJAX handler owns progress when fetch is available.
    if (form.id === "present-form" && window.fetch) return;
    if (!begin(form, event.submitter)) event.preventDefault();
  });
  document.addEventListener("invalid", function (event) {
    var form = event.target.form;
    if (form && form.matches("[data-attendance-processing]")) end(form, "Review the highlighted fields before continuing.", true);
  }, true);
  window.addEventListener("offline", function () {
    document.querySelectorAll('form[data-attendance-processing][data-processing="true"]').forEach(function (form) {
      end(form, "Connection lost: the outcome is unknown. Reload and check the current statuses before retrying.", true);
      form.dataset.processingUnknown = "true";
    });
  });
  window.addEventListener("pageshow", function (event) {
    document.querySelectorAll("form[data-attendance-processing]").forEach(function (form) {
      if (event.persisted && form.dataset.processing === "true") {
        end(form, "The submitted outcome may be unknown. Reload and review saved statuses before retrying.", true);
        form.dataset.processingUnknown = "true";
      } else { end(form); }
    });
  });
}());
