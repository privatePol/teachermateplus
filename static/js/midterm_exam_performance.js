(() => {
  const groups = document.getElementById("midterm-course-groups");
  const link = document.getElementById("midterm-load-more");
  const status = document.getElementById("midterm-load-status");
  const form = document.getElementById("midterm-filters");
  if (!groups || !status || !form) return;
  const all = document.getElementById("midterm-load-all");
  const cancel = document.getElementById("midterm-cancel");
  const progress = document.getElementById("midterm-load-progress");
  const sentinel = document.getElementById("midterm-scroll-sentinel");
  const dialog = document.getElementById("midterm-top-dialog");
  const topStatus = document.getElementById("midterm-top-status");
  let topResults = document.getElementById("midterm-top-results");
  if (!topResults && dialog) {
    topResults = document.createElement("div"); topResults.id = "midterm-top-results";
    dialog.insertBefore(topResults, document.getElementById("midterm-top-close"));
  }
  let busy = false, obsolete = false, restart = false, paused = false, loadingAll = false;
  let controller = new AbortController(), topController = new AbortController();
  let generation = 0, topGeneration = 0, filtersChanged = false;
  let total = Number(groups.dataset.totalRows || 0);
  let next = link ? link.getAttribute("href") : "";
  const rendered = () => groups.querySelectorAll("tbody tr[data-offering-id]").length;
  const reportProgress = () => {
    if (progress) { progress.max = Math.max(total, 1); progress.value = rendered(); }
    status.textContent = `${rendered()} of ${total} class results loaded.${!next ? " All matching results loaded." : ""}`;
  };
  const controls = () => {
    if (all) { all.hidden = !next || obsolete || restart; all.disabled = busy || loadingAll; }
    if (cancel) cancel.hidden = !busy && !loadingAll;
    if (link) { link.hidden = !next; link.setAttribute("aria-disabled", String(busy)); }
    groups.setAttribute("aria-busy", String(busy));
  };
  const abort = () => {
    generation++;
    controller.abort(); controller = new AbortController();
    busy = false; loadingAll = false;
    controls();
  };
  const closeTop = () => {
    topResults?.replaceChildren();
    topGeneration++;
    topController.abort(); topController = new AbortController();
    if (dialog?.open) dialog.close();
  };
  const invalidate = () => {
    obsolete = true; abort(); closeTop();
  };
  form.addEventListener("submit", () => { filtersChanged = true; invalidate(); });
  form.addEventListener("change", event => {
    filtersChanged = true; invalidate();
    if (event.target.name === "exam_period") {
      const cycle = form.querySelector('[name="cycle_id"]');
      if (cycle) cycle.value = "";
      form.requestSubmit();
    }
  });
  window.addEventListener("pagehide", invalidate);
  window.addEventListener("pageshow", event => {
    if (event.persisted && !filtersChanged) { obsolete = false; controls(); }
  });
  const append = html => {
    const fragment = document.createElement("template");
    fragment.innerHTML = html;
    for (const incoming of fragment.content.querySelectorAll("section[data-course-id]")) {
      const existing = [...groups.querySelectorAll("section[data-course-id]")].find(
        item => item.dataset.courseId === incoming.dataset.courseId);
      if (!existing) { groups.append(incoming); continue; }
      const body = existing.querySelector("tbody");
      const present = new Set([...body.querySelectorAll("tr[data-offering-id]")].map(row => row.dataset.offeringId));
      for (const row of incoming.querySelectorAll("tbody tr[data-offering-id]")) {
        if (!present.has(row.dataset.offeringId)) { body.append(row); present.add(row.dataset.offeringId); }
      }
    }
  };
  const load = async () => {
    if (busy || obsolete || restart || !next) return false;
    busy = true; controls();
    const requestGeneration = generation;
    status.textContent = `Loading results... ${rendered()} of ${total} loaded.`;
    try {
      const response = await fetch(next, {
        headers: { "X-Midterm-Fragment": "1", "Accept": "application/json" },
        cache: "no-store", signal: controller.signal,
      });
      if (obsolete || requestGeneration !== generation) return false;
      if (response.status !== 409 && !response.ok) throw new Error("Request failed");
      const data = await response.json();
      if (obsolete || requestGeneration !== generation) return false;
      if (response.status === 409) {
        restart = true; loadingAll = false;
        status.textContent = "Results or access changed. Reload the report to continue.";
        link.href = data.restart_url; link.textContent = "Reload report"; link.hidden = false;
        return false;
      }
      if (typeof data.html !== "string" || typeof data.next_url !== "string") throw new Error("Invalid response");
      append(data.html);
      next = data.next_url;
      if (Number.isFinite(data.total_rows)) total = data.total_rows;
      if (link && next) link.href = next;
      reportProgress();
      return true;
    } catch (error) {
      if (!obsolete && requestGeneration === generation) {
        paused = true;
        status.textContent = "Could not load results. Select Continue loading to retry.";
      }
      return false;
    } finally {
      if (requestGeneration === generation) { busy = false; controls(); }
    }
  };
  link?.addEventListener("click", async event => {
    if (restart) return;
    event.preventDefault();
    if (obsolete) { status.textContent = "Select View report to apply your filters."; return; }
    paused = false;
    await load();
  });
  all?.addEventListener("click", async () => {
    if (busy || loadingAll || obsolete || restart) return;
    paused = false; loadingAll = true; controls();
    const run = generation;
    // One bounded server batch at a time; each is rendered and acknowledged.
    while (loadingAll && next && !obsolete && run === generation) {
      if (!await load()) break;
    }
    if (run === generation) { loadingAll = false; controls(); }
  });
  cancel?.addEventListener("click", () => {
    paused = true; abort(); reportProgress();
    status.textContent += " Loading cancelled. Select Continue loading or Load All to resume.";
  });
  if (link && sentinel && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && !paused && !loadingAll) load();
    }, { rootMargin: "600px" });
    observer.observe(sentinel);
    // A short fragment may leave the sentinel in view without another observer event.
    const auto = async () => {
      if (!paused && !loadingAll && !obsolete && !busy && next && !restart
          && sentinel.getBoundingClientRect().top < window.innerHeight + 600) {
        if (await load()) window.setTimeout(auto, 0);
      }
    };
    window.addEventListener("scroll", auto, { passive: true });
    window.addEventListener("resize", auto);
    // Each successful append rechecks proximity, including at initial paint.
    const mutation = new MutationObserver(() => window.setTimeout(auto, 0));
    mutation.observe(groups, { childList: true, subtree: true });
  }
  const renderTop = data => {
    if (!topResults) return 0;
    topResults.replaceChildren();
    let displayed = 0;
    const show = (title, rows) => {
      if (!Array.isArray(rows) || !rows.length) return;
      const heading = document.createElement("h3");
      heading.className = "h6 mt-3"; heading.textContent = title;
      const table = document.createElement("table");
      table.className = "table table-sm";
      const raw = rows.some(row => row.maximum_points !== null && row.maximum_points !== undefined);
      const head = table.createTHead().insertRow();
      for (const label of ["Rank", "Student", "Section", "Converted exam grade", ...(raw ? ["Stored raw / maximum"] : [])]) {
        const th = document.createElement("th"); th.scope = "col"; th.textContent = label; head.append(th);
      }
      const body = table.createTBody();
      for (const row of rows) {
        const tr = body.insertRow();
        for (const value of [row.rank, `${row.name} (${row.student_no})`, row.section, row.exam_grade,
          ...(raw ? [row.maximum_points !== null && row.maximum_points !== undefined ? `${row.raw_score ?? "-"} / ${row.maximum_points}` : "-"] : [])]) {
          tr.insertCell().textContent = String(value ?? "-");
        }
      }
      const wrapper = document.createElement("div"); wrapper.className = "table-responsive"; wrapper.append(table);
      topResults.append(heading, wrapper);
      displayed += rows.length;
    };
    for (const campus of data.campuses || []) show(`Top 10 - ${campus.campus}`, campus.rows);
    return displayed;
  };
  groups.addEventListener("click", async event => {
    const button = event.target.closest("[data-top-url]");
    if (!button || obsolete || !dialog || !topStatus) return;
    closeTop();
    const requestGeneration = topGeneration;
    topStatus.hidden = false;
    topStatus.textContent = "Loading Top Performers...";
    dialog.showModal();
    try {
      const response = await fetch(button.dataset.topUrl, {
        headers: { "Accept": "application/json" }, cache: "no-store", signal: topController.signal,
      });
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (requestGeneration !== topGeneration || obsolete) return;
      if (data.available && renderTop(data)) {
        topStatus.textContent = "";
        topStatus.hidden = true;
      } else {
        topStatus.textContent = data.available ? "No confirmed exam results are available." : data.message;
      }
    } catch (error) {
      if (requestGeneration === topGeneration && !obsolete) topStatus.textContent = "Top Performers are unavailable for this scope. Close and retry.";
    }
  });
  document.getElementById("midterm-top-close")?.addEventListener("click", closeTop);
  dialog?.addEventListener("cancel", closeTop);
  reportProgress(); controls();
})();
