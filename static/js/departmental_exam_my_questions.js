(function () {
  "use strict";

  const root = document.querySelector("[data-myq-progressive]");
  if (!root) return;

  const list = root.querySelector("[data-myq-list]");
  const button = root.querySelector("[data-myq-load-more]");
  const spinner = root.querySelector("[data-myq-spinner]");
  const status = root.querySelector("[data-myq-status]");
  const sentinel = root.querySelector("[data-myq-sentinel]");
  const restart = root.querySelector("[data-myq-restart]");
  const filterForm = document.querySelector('form[method="get"]');
  const pagination = document.querySelector("[data-myq-pagination]");
  if (!list || !button || !spinner || !status || !sentinel) return;

  let nextUrl = root.dataset.nextUrl || "";
  let loading = false;
  let controller = null;
  let generation = 0;
  const keys = new Set(
    Array.from(list.querySelectorAll("[data-myq-card]"), card => card.dataset.myqKey)
      .filter(Boolean)
  );

  if (pagination) pagination.hidden = true;

  function showEnd() {
    nextUrl = "";
    root.dataset.nextUrl = "";
    button.hidden = true;
    button.disabled = false;
    button.textContent = "Load more";
    spinner.hidden = true;
    if (restart) restart.hidden = true;
    status.textContent = keys.size
      ? "All matching questions are loaded."
      : "No matching questions to load.";
  }

  function showRetry() {
    button.hidden = false;
    button.disabled = false;
    button.textContent = "Retry loading results";
    spinner.hidden = true;
    if (restart) restart.hidden = true;
    status.textContent = "Could not load more results. Check your connection and try again.";
  }

  function showRestart(payload) {
    nextUrl = "";
    root.dataset.nextUrl = "";
    button.hidden = true;
    button.disabled = false;
    spinner.hidden = true;
    if (restart) {
      restart.href = payload.restart_url || window.location.pathname;
      restart.hidden = false;
    }
    status.textContent = payload.message ||
      "My Questions changed while loading. Restart to see the current results.";
  }

  function requestIsCurrent(requestGeneration, requestController) {
    return generation === requestGeneration && !requestController.signal.aborted;
  }

  async function loadMore() {
    if (loading || !nextUrl) return;
    loading = true;
    button.hidden = false;
    button.disabled = true;
    button.textContent = "Loading…";
    spinner.hidden = false;
    if (restart) restart.hidden = true;
    status.textContent = "Loading more questions…";
    root.setAttribute("aria-busy", "true");
    const requestGeneration = generation;
    const requestController = new AbortController();
    controller = requestController;
    try {
      const response = await fetch(nextUrl, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: requestController.signal,
      });
      if (!requestIsCurrent(requestGeneration, requestController)) return;
      const payload = await response.json();
      if (!requestIsCurrent(requestGeneration, requestController)) return;
      if (response.status === 409 && payload.catalogue_changed) {
        showRestart(payload);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (typeof payload.html !== "string") throw new Error("Invalid response");
      const template = document.createElement("template");
      template.innerHTML = payload.html;
      if (!requestIsCurrent(requestGeneration, requestController)) return;
      for (const card of template.content.querySelectorAll("[data-myq-card]")) {
        const key = card.dataset.myqKey;
        if (!key || keys.has(key)) continue;
        keys.add(key);
        list.appendChild(card);
      }
      if (!requestIsCurrent(requestGeneration, requestController)) return;
      nextUrl = typeof payload.next_url === "string" ? payload.next_url : "";
      root.dataset.nextUrl = nextUrl;
      if (!nextUrl) {
        showEnd();
      } else {
        button.hidden = false;
        button.disabled = false;
        button.textContent = "Load more";
        spinner.hidden = true;
        if (restart) restart.hidden = true;
        status.textContent = "More matching questions are available.";
      }
    } catch (error) {
      if (requestIsCurrent(requestGeneration, requestController) &&
          error.name !== "AbortError") showRetry();
    } finally {
      if (generation === requestGeneration && controller === requestController) {
        loading = false;
        controller = null;
        root.removeAttribute("aria-busy");
      }
    }
  }

  button.addEventListener("click", loadMore);
  if (filterForm) {
    filterForm.addEventListener("submit", () => {
      generation += 1;
      if (controller) controller.abort();
      controller = null;
      loading = false;
      nextUrl = "";
      root.dataset.nextUrl = "";
      spinner.hidden = true;
      button.hidden = true;
      button.disabled = false;
      button.textContent = "Load more";
      status.textContent = "Applying filters...";
      if (restart) restart.hidden = true;
      root.removeAttribute("aria-busy");
    });
  }
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadMore();
    }, { rootMargin: "0px 0px 320px 0px" });
    observer.observe(sentinel);
  }
})();
