(() => {
  "use strict";

  const reminder = "Question import is in progress. Please keep this page open and do not navigate away.";
  let importActive = false;

  const beforeUnload = event => {
    if (!importActive) return;
    event.preventDefault();
    event.returnValue = reminder;
    return reminder;
  };

  const lockPage = active => {
    importActive = active;
    document.querySelectorAll("[data-import-page]").forEach(root => {
      root.setAttribute("aria-busy", active ? "true" : "false");
      root.querySelectorAll("button, input:not([type='hidden']), select, textarea").forEach(control => {
        if (active) {
          control.dataset.importWasDisabled = control.disabled ? "true" : "false";
          control.disabled = true;
        } else if (control.dataset.importWasDisabled !== "true") {
          control.disabled = false;
        }
      });
      root.querySelectorAll("a[href]").forEach(link => {
        if (active) {
          link.dataset.importPreviousTabindex = link.getAttribute("tabindex") || "";
          link.setAttribute("aria-disabled", "true");
          link.setAttribute("tabindex", "-1");
        } else {
          link.removeAttribute("aria-disabled");
          const previous = link.dataset.importPreviousTabindex;
          if (previous) link.setAttribute("tabindex", previous);
          else link.removeAttribute("tabindex");
        }
      });
    });
    document.querySelectorAll("[data-import-overlay]").forEach(overlay => {
      overlay.hidden = !active;
    });
    if (active) window.addEventListener("beforeunload", beforeUnload);
    else window.removeEventListener("beforeunload", beforeUnload);
  };

  document.addEventListener("click", event => {
    if (importActive && event.target.closest("a[href]")) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  const showError = message => {
    const error = document.querySelector("[data-import-error]");
    if (!error) return;
    error.textContent = message;
    error.hidden = false;
  };

  const setProgress = payload => {
    document.querySelectorAll("[data-import-progress-text]").forEach(node => {
      node.textContent = `${payload.committed_rows} / ${payload.total_rows} rows committed (${payload.percentage}%).`;
    });
    document.querySelectorAll("[data-import-progress-bar]").forEach(bar => {
      bar.style.width = `${payload.percentage}%`;
      const progress = bar.closest("[role='progressbar']");
      if (progress) progress.setAttribute("aria-valuenow", String(payload.percentage));
    });
    document.querySelectorAll("[data-import-overlay-progress]").forEach(node => {
      node.textContent = `${payload.committed_rows} / ${payload.total_rows} rows committed (${payload.percentage}%).`;
    });
    const message = document.querySelector("[data-import-server-message]");
    if (message) {
      message.textContent = payload.failure_message || "";
      message.hidden = !payload.failure_message;
    }
  };

  const uploadForm = document.querySelector("[data-csv-upload-form]");
  if (uploadForm) {
    let uploading = false;
    uploadForm.addEventListener("submit", event => {
      event.preventDefault();
      if (uploading) return;
      uploading = true;
      const data = new FormData(uploadForm);
      const xhr = new XMLHttpRequest();
      const phase = document.querySelector("[data-import-phase]");
      const bar = document.querySelector("[data-import-progress-bar]");
      const progress = bar ? bar.closest("[role='progressbar']") : null;
      lockPage(true);
      xhr.upload.addEventListener("progress", progressEvent => {
        if (!progressEvent.lengthComputable || !bar) return;
        const percentage = Math.round((progressEvent.loaded / progressEvent.total) * 100);
        if (phase) phase.textContent = `Uploading CSV... ${percentage}%`;
        bar.classList.remove("progress-bar-striped", "progress-bar-animated");
        bar.style.width = `${percentage}%`;
        if (progress) progress.setAttribute("aria-valuenow", String(percentage));
      });
      xhr.upload.addEventListener("load", () => {
        if (phase) phase.textContent = "Validating CSV...";
        if (bar) {
          bar.classList.add("progress-bar-striped", "progress-bar-animated");
          bar.style.width = "100%";
        }
        if (progress) progress.removeAttribute("aria-valuenow");
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          lockPage(false);
          window.location.assign(xhr.responseURL);
          return;
        }
        uploading = false;
        lockPage(false);
        showError("The CSV could not be validated. Review the selected file and try again.");
      });
      xhr.addEventListener("error", () => {
        uploading = false;
        lockPage(false);
        showError("The connection was interrupted before validation completed. No question import was started.");
      });
      xhr.addEventListener("abort", () => {
        uploading = false;
        lockPage(false);
        showError("CSV upload was cancelled safely. No question import was started.");
      });
      xhr.open((uploadForm.method || "POST").toUpperCase(), uploadForm.action || window.location.href);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.send(data);
    });
  }

  const controller = document.querySelector("[data-question-import-controller]");
  if (!controller) return;
  const resumeForm = controller.querySelector("[data-import-resume-form]");
  const statusUrl = controller.dataset.statusUrl;
  let running = false;

  const readStatus = async () => {
    const response = await fetch(statusUrl, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    });
    if (!response.ok) throw new Error("status-unavailable");
    return response.json();
  };

  const postChunk = async () => {
    const response = await fetch(resumeForm.action, {
      method: "POST",
      credentials: "same-origin",
      headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
      body: new FormData(resumeForm)
    });
    const payload = await response.json();
    setProgress(payload);
    if (!response.ok) {
      const error = new Error(payload.error || "import-paused");
      error.payload = payload;
      throw error;
    }
    return payload;
  };

  const recoverStatus = async () => {
    try {
      const payload = await readStatus();
      setProgress(payload);
      if (payload.completed && payload.workspace_url) {
        lockPage(false);
        window.location.assign(payload.workspace_url);
        return true;
      }
    } catch (error) {
      // The resume control remains available for a later safe retry.
    }
    return false;
  };

  const runImport = async event => {
    event.preventDefault();
    if (running) return;
    running = true;
    lockPage(true);
    try {
      let payload;
      do {
        payload = await postChunk();
      } while (payload.can_resume && !payload.completed);
      if (payload.completed) {
        lockPage(false);
        window.location.assign(payload.workspace_url);
        return;
      }
      throw new Error("import-not-complete");
    } catch (error) {
      const recovered = await recoverStatus();
      if (!recovered) {
        showError(
          error.payload && error.payload.failure_message
            ? error.payload.failure_message
            : "The connection was interrupted. Persisted progress is safe. Use Resume import to continue."
        );
        running = false;
        lockPage(false);
      }
    }
  };

  if (resumeForm) resumeForm.addEventListener("submit", runImport);
  recoverStatus();
})();
