(() => {
  const groups = document.getElementById("midterm-course-groups");
  const link = document.getElementById("midterm-load-more");
  const status = document.getElementById("midterm-load-status");
  const form = document.getElementById("midterm-filters");
  if (!groups || !link) return;
  let busy = false;
  let obsolete = false;
  let restart = false;
  let controller = new AbortController();
  let generation = 0;
  let filtersChanged = false;
  const invalidate = () => {
    generation++;
    obsolete = true;
    controller.abort();
    busy = false;
    link.removeAttribute("aria-disabled");
    groups.removeAttribute("aria-busy");
  };
  const changed = () => { filtersChanged = true; invalidate(); };
  form.addEventListener("submit", changed);
  form.addEventListener("change", changed);
  window.addEventListener("pagehide", invalidate);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && !filtersChanged) {
      obsolete = false;
      controller = new AbortController();
      status.textContent = "";
    }
  });
  link.addEventListener("click", async (event) => {
    if (restart) return;
    event.preventDefault();
    if (busy) return;
    if (obsolete) { status.textContent = "Select View report to apply your filters."; return; }
    busy = true;
    const requestGeneration = generation;
    link.setAttribute("aria-disabled", "true");
    groups.setAttribute("aria-busy", "true");
    status.textContent = "Loading more results…";
    try {
      const response = await fetch(link.href, {
        headers: { "X-Midterm-Fragment": "1", "Accept": "application/json" },
        cache: "no-store", signal: controller.signal,
      });
      if (obsolete || requestGeneration !== generation) return;
      if (response.status !== 409 && !response.ok) throw new Error("Request failed");
      const data = await response.json();
      if (obsolete || requestGeneration !== generation) return;
      if (response.status === 409) {
        status.textContent = "Results or access changed. Reload the report to continue.";
        link.href = data.restart_url;
        link.textContent = "Reload report";
        restart = true;
        return;
      }
      const fragment = document.createElement("template");
      fragment.innerHTML = data.html;
      for (const incoming of fragment.content.querySelectorAll("section[data-course-id]")) {
        const existing = groups.querySelector(`section[data-course-id="${incoming.dataset.courseId}"]`);
        if (!existing) { groups.append(incoming); continue; }
        const body = existing.querySelector("tbody");
        for (const row of incoming.querySelectorAll("tbody tr")) {
          if (!body.querySelector(`[data-offering-id="${row.dataset.offeringId}"]`)) body.append(row);
        }
      }
      if (data.next_url) {
        link.href = data.next_url;
        status.textContent = "More results loaded.";
      } else {
        link.hidden = true;
        status.textContent = "All matching results loaded.";
        status.focus();
      }
    } catch (error) {
      if (!obsolete && requestGeneration === generation) status.textContent = "Could not load results. Select Load more to retry.";
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        link.removeAttribute("aria-disabled");
        groups.removeAttribute("aria-busy");
      }
    }
  });
})();
