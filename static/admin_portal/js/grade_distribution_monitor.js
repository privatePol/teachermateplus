/* Aggregate-only analytics. Bootstrap owns modal focus, Escape, and restoration. */
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("gradeDistributionFilterForm");
  const results = document.getElementById("gdm-results");
  const status = document.getElementById("gdm-filter-status");
  const modal = document.getElementById("gradeDistributionAnalyticsModal");
  const body = document.getElementById("gdm-modal-body");
  if (!form || !modal) return;
  let optionsAbort, detailAbort, opener;
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const display = value => value === null || value === undefined ? "Unavailable" : String(value);
  const percent = value => value === null || value === undefined ? "Withheld" : `${Number(value).toFixed(2)}%`;
  const loading = label => {
    const wrap = node("div", undefined, "gdm-loading");
    wrap.append(node("span", label, "small text-success"));
    const progress = node("div", undefined, "progress mt-2");
    const bar = node("div", undefined, "progress-bar bg-success gdm-indeterminate");
    bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-label", label);
    // Indeterminate: no aria-valuenow or fabricated numerical percentage.
    progress.append(bar); wrap.append(progress); return wrap;
  };
  const tile = (label, value, detail, gold = false) => {
    const card = node("div", undefined, `gdm-metric${gold ? " gdm-exact-100" : ""}`);
    card.append(node("div", label, "small fw-semibold"), node("div", value, "h5 mb-1"));
    if (detail) card.append(node("div", detail, "small"));
    return card;
  };
  const datasetDomId = id => `gdm-dataset-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const table = (headings, rows, caption, datasetId) => {
    const wrap = node("div", undefined, "table-responsive");
    const t = node("table", undefined, "table table-sm");
    t.dataset.datasetId = datasetId;
    t.append(node("caption", caption));
    const head = node("thead"), tr = node("tr");
    headings.forEach(label => { const th = node("th", label); th.scope = "col"; tr.append(th); });
    head.append(tr); t.append(head);
    const tb = node("tbody");
    rows.forEach(row => { const r = node("tr"); row.forEach(value => r.append(node("td", display(value)))); tb.append(r); });
    t.append(tb); wrap.append(t); return wrap;
  };
  const chart = (items, title, xLabel, datasetId, context) => {
    const wrap = node("div", undefined, "gdm-chart-wrap");
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.dataset.datasetId = datasetId;
    svg.classList.add("gdm-chart"); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${title}. ${context}. Vertical bars; horizontal axis ${xLabel}; vertical axis student count. Values are in the following table.`);
    const width = Math.max(600, items.length * 55 + 80);
    svg.setAttribute("viewBox", `0 0 ${width} 280`);
    svg.style.minWidth = `${Math.min(width, 1200)}px`;
    const max = Math.max(1, ...items.map(i => Number(i.count)));
    const text = (x, y, value, anchor = "middle") => {
      const t = document.createElementNS(ns, "text"); t.setAttribute("x", x); t.setAttribute("y", y); t.setAttribute("text-anchor", anchor); t.textContent = value; svg.append(t);
    };
    text(width / 2, 18, title); text(7, 40, "Students", "start");
    text(48, 65, max); text(48, 225, "0");
    items.forEach((item, i) => {
      const spacing = (width - 80) / Math.max(items.length, 1);
      const x = 65 + i * spacing, height = Number(item.count) * 150 / max;
      const bar = document.createElementNS(ns, "rect");
      bar.setAttribute("x", x); bar.setAttribute("y", 220 - height); bar.setAttribute("width", Math.max(8, spacing - 15)); bar.setAttribute("height", height);
      const tip = document.createElementNS(ns, "title"); tip.textContent = `${item.label}: ${item.count} (${item.percentage}%)`; bar.append(tip); svg.append(bar);
      text(x + (spacing - 15) / 2, 214 - height, item.count);
      text(x + (spacing - 15) / 2, 242, item.shortLabel || item.label);
    });
    text(width / 2, 272, xLabel); wrap.append(svg); return wrap;
  };
  const renderMeasure = (measure, title, includeFrequency) => {
    const section = node("section", undefined, "gdm-measure");
    section.id = datasetDomId(measure.dataset_id);
    section.dataset.datasetId = measure.dataset_id;
    section.append(node("h4", title, "h6"));
    section.append(node("p", measure.gradebook_context, "small text-muted"));
    if (measure.unit) section.append(node("p", measure.unit, "small text-muted"));
    if (measure.analytics_state && measure.analytics_state !== "disclosed") {
      section.append(node("p", measure.privacy_message, "small text-muted"));
      return section;
    }
    if (measure.equivalent_dataset_id) {
      section.dataset.equivalentDatasetId = measure.equivalent_dataset_id;
      const reference = node("p", undefined, "small text-muted");
      const link = node("a", measure.equivalent_to);
      link.href = `#${datasetDomId(measure.equivalent_dataset_id)}`;
      reference.append(node("span", "Same Gradebook values for the same students as "), link,
        node("span", `. ${measure.equivalent_context}. See that distribution.`));
      section.append(reference);
      return section;
    }
    const high = measure.high_grade_breakdown || [];
    const combined = high.find(item => item.grade === "Combined 95–99");
    const above = high.find(item => item.grade === "90 and above");
    const tiles = node("div", undefined, "gdm-summary mb-3");
    tiles.append(tile("Average Grade", display(measure.average), title),
      tile("Graded Students", display(measure.graded_count), "Saved Gradebook grades"),
      tile("Lowest / Highest", `${display(measure.minimum)} / ${display(measure.maximum)}`, title),
      tile("Exactly 100", `${display(measure.exact_100_count)} / ${measure.graded_count}`, `${percent(measure.exact_100_percentage)} · ${title}`, true),
      tile("95–99", `${display(combined?.count)} / ${measure.graded_count}`, `${percent(combined?.percentage)} · 95 to below 100`),
      tile("90 and above", `${display(above?.count)} / ${measure.graded_count}`, percent(above?.percentage)));
    section.append(tiles);
    if (measure.all_100) section.append(node("p", "All eligible graded values are 100 for this measure.", "small"));
    if (measure.finalized_count || measure.unfinalized_count) section.append(node("p", `Saved status: ${measure.status}; ${measure.finalized_count} finalized, ${measure.unfinalized_count} unfinalized.`, "small"));
    if (!measure.graded_count) section.append(node("p", "No eligible values are available for this measure.", "text-muted"));
    section.append(table(["Grade", "Count", "Total graded students", "Percentage"],
      high.map(item => [item.grade, item.count, item.total_graded_students, percent(item.percentage)]), `${title}: high-grade breakdown (${measure.gradebook_context})`, measure.dataset_id));
    section.append(node("p", "Combined categories overlap; do not add their counts. Grades are compared without rounding. Exactly 100 refers to this measure, not necessarily a perfect raw assessment score.", "small text-muted"));
    if (includeFrequency && measure.frequency.length) {
      // One numerical chart per distinct Gradebook measure; table retains all bins.
      const details = node("div", undefined, "gdm-frequency");
      const frequencyChart = node("div");
      let offset = 0;
      const drawFrequency = () => frequencyChart.replaceChildren(chart(measure.frequency.slice(offset, offset + 25).map(f => ({...f, label: String(f.grade)})), `${title}: exact frequency (${offset + 1}–${Math.min(offset + 25, measure.frequency.length)})`, "Gradebook grade", measure.dataset_id, measure.gradebook_context));
      drawFrequency(); details.append(frequencyChart);
      if (measure.frequency.length > 25) {
        const controls = node("div", undefined, "d-flex gap-2");
        const previous = node("button", "Previous grades", "btn btn-sm btn-outline-secondary");
        const next = node("button", "Next grades", "btn btn-sm btn-outline-secondary");
        previous.type = next.type = "button";
        const update = () => { previous.disabled = offset === 0; next.disabled = offset + 25 >= measure.frequency.length; drawFrequency(); };
        previous.addEventListener("click", () => { offset -= 25; update(); });
        next.addEventListener("click", () => { offset += 25; update(); });
        update(); controls.append(previous, next); details.append(controls);
      }
      details.append(table(["Gradebook grade", "Count", "% of graded"], measure.frequency.map(f => [f.grade, f.count, f.percentage]), `${title}: Gradebook grade frequencies (${measure.gradebook_context})`, measure.dataset_id));
      section.append(details);
    }
    return section;
  };
  form.addEventListener("change", async event => {
    results.hidden = true;
    form.removeAttribute("aria-busy");
    detailAbort?.abort();
    body.replaceChildren();
    body.removeAttribute("aria-busy");
    window.bootstrap?.Modal.getInstance(modal)?.hide();
    status.textContent = "Filters changed. Click Apply Filters to refresh results.";
    if (!["campus_id", "academic_year_id"].includes(event.target.name)) return;
    optionsAbort?.abort(); optionsAbort = new AbortController();
    const thisAbort = optionsAbort;
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    if (event.target.name === "academic_year_id") form.elements.term_id.value = "";
    const params = new URLSearchParams(new FormData(form)); params.delete("apply"); params.set("options", "1");
    try {
      const response = await fetch(`${location.pathname}?${params}`, {signal: thisAbort.signal, cache: "no-store", headers: {Accept: "application/json"}});
      if (!response.ok) throw new Error("Filter choices are unavailable. Reload this page.");
      const data = await response.json();
      if (thisAbort.signal.aborted || optionsAbort !== thisAbort) return;
      for (const [key, choices, placeholder] of [["faculty_id", data.faculty, "All Eligible Faculty"], ["department_id", data.departments, "All Eligible Departments"], ["term_id", data.terms, "Select Term"]]) {
        const select = form.elements[key], old = select.value;
        select.replaceChildren(new Option(placeholder, ""));
        choices.forEach(choice => select.add(new Option(choice.label, choice.id)));
        if ([...select.options].some(o => o.value === old)) select.value = old;
      }
    } catch (error) {
      if (error.name !== "AbortError") status.textContent = error.message;
    } finally { if (optionsAbort === thisAbort) button.disabled = false; }
  });
  const beginSearch = () => { results.hidden = true; status.replaceChildren(loading("Retrieving grade distribution")); form.setAttribute("aria-busy", "true"); };
  form.addEventListener("submit", beginSearch);
  results.addEventListener("click", event => {
    if (event.target.closest('.pagination a.page-link') && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) beginSearch();
  });
  modal.addEventListener("show.bs.modal", async event => {
    opener = event.relatedTarget;
    detailAbort?.abort(); detailAbort = new AbortController();
    const thisAbort = detailAbort;
    body.replaceChildren(loading("Retrieving class analytics")); body.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(opener.dataset.analyticsUrl, {signal: thisAbort.signal, cache: "no-store", headers: {Accept: "application/json"}});
      const data = await response.json();
      if (thisAbort.signal.aborted || detailAbort !== thisAbort) return;
      if (!response.ok) throw new Error(data.errors?.join(" ") || "Analytics are unavailable. Apply Filters again.");
      const header = node("header", undefined, "gdm-class-header");
      const labels = node("dl", undefined, "row mb-0");
      for (const [label, value] of [["Faculty", data.faculty_name], ["Course", `${data.course_code} — ${data.course_title}`],
        ["Class/section", data.section], ["Campus", data.campus], ["Academic year and term", `${data.school_year} / ${data.term}`],
        ["Grading period", data.gradebook_context], ["Grade status", data.status]]) {
        labels.append(node("dt", label, "col-sm-4"));
        const dd = node("dd", undefined, "col-sm-8");
        dd.append(node(label === "Faculty" ? "h5" : "span", value, label === "Faculty" ? "mb-0" : "")); labels.append(dd);
      }
      header.append(labels); body.replaceChildren(header);
      const coverage = node("dl", undefined, "gdm-coverage");
      for (const [label, value] of [["Selected saved grades / eligible students", `${data.graded_count} / ${data.eligible_count}`],
        ["Missing period grades", data.missing_count], ["Excluded by status", data.status_excluded_count],
        ["Incomplete assessment sources", display(data.incomplete_source_count)],
        ["Activity grades", "Saved Gradebook values, including the configured base"]]) {
        const row = node("div"); row.append(node("dt", label), node("dd", value)); coverage.append(row);
      }
      body.append(coverage);
      body.append(renderMeasure(data, "Period Grades", true));
      data.measures.forEach(measure => body.append(renderMeasure(measure, measure.label, true)));
      const notes = node("details", undefined, "gdm-collapsible gdm-data-notes mt-3");
      notes.append(node("summary", "Data Notes"));
      const notesBody = node("div", undefined, "p-3");
      notesBody.append(node("p", `Finalized: ${data.finalized_count}; unfinalized: ${data.unfinalized_count}.`, "small"));
      data.notes.forEach(note => notesBody.append(node("p", note, "small text-muted")));
      notes.append(notesBody); body.append(notes);
    } catch (error) {
      if (error.name !== "AbortError") { const msg = node("div", error.message, "alert alert-warning"); msg.setAttribute("role", "alert"); body.replaceChildren(msg); }
    } finally { if (detailAbort === thisAbort) body.removeAttribute("aria-busy"); }
  });
  modal.addEventListener("hidden.bs.modal", () => { detailAbort?.abort(); body.replaceChildren(); body.removeAttribute("aria-busy"); if (opener?.isConnected && !results.hidden) opener.focus(); });
  window.addEventListener("pageshow", event => { form.removeAttribute("aria-busy"); status.replaceChildren(); if (event.persisted) { results.hidden = true; body.replaceChildren(); status.textContent = "Apply Filters again to refresh authorized results."; } });
});
