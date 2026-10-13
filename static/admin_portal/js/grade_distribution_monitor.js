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
  const table = (headings, rows, caption) => {
    const wrap = node("div", undefined, "table-responsive");
    const t = node("table", undefined, "table table-sm");
    t.append(node("caption", caption));
    const head = node("thead"), tr = node("tr");
    headings.forEach(label => { const th = node("th", label); th.scope = "col"; tr.append(th); });
    head.append(tr); t.append(head);
    const tb = node("tbody");
    rows.forEach(row => { const r = node("tr"); row.forEach(value => r.append(node("td", display(value)))); tb.append(r); });
    t.append(tb); wrap.append(t); return wrap;
  };
  const chart = (items, title, xLabel) => {
    const wrap = node("div", undefined, "gdm-chart-wrap");
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.classList.add("gdm-chart"); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${title}. Vertical bars; horizontal axis ${xLabel}; vertical axis student count. Values are in the following table.`);
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
    section.append(node("h4", title, "h6"));
    if (measure.unit) section.append(node("p", measure.unit, "small text-muted"));
    if (measure.analytics_state && measure.analytics_state !== "disclosed") {
      section.append(node("p", measure.privacy_message, "small text-muted"));
      section.append(table(["Reference category", "Numerical range", "Count", "% of graded"], measure.distribution.map(b => [b.label, b.range, "Withheld", "Withheld"]), `${title}: reference definitions only`));
      return section;
    }
    section.append(node("p", `Class Ave.: ${display(measure.average)} · Eligible graded: ${measure.graded_count} · Min: ${display(measure.minimum)} · Max: ${display(measure.maximum)} · Exactly 100: ${measure.exact_100_count} (${measure.exact_100_percentage}%)${measure.all_100 ? " · All eligible graded values are 100" : ""}`));
    if (measure.finalized_count || measure.unfinalized_count) section.append(node("p", `Saved status: ${measure.status}; ${measure.finalized_count} finalized, ${measure.unfinalized_count} unfinalized.`, "small"));
    if (!measure.graded_count) section.append(node("p", "No eligible values are available for this measure.", "text-muted"));
    const bands = measure.distribution.map((b, i) => ({...b, label: b.range, shortLabel: ["Strongly above", "Above", "Near", "Below", "Well below"][i]}));
    section.append(chart(bands, `${title}: distribution`, "Threshold reference range"));
    section.append(table(["Reference category", "Numerical range", "Count", "% of graded"], measure.distribution.map(b => [b.label, b.range, b.count, b.percentage]), `${title} — accessible distribution data`));
    if (includeFrequency && measure.frequency.length) {
      // A frequency chart adds exact-value concentration to the range chart.
      const details = node("details"); details.append(node("summary", "Exact-grade frequency"));
      const frequencyChart = node("div");
      let offset = 0;
      const drawFrequency = () => frequencyChart.replaceChildren(chart(measure.frequency.slice(offset, offset + 25).map(f => ({...f, label: String(f.grade)})), `${title}: exact frequency (${offset + 1}–${Math.min(offset + 25, measure.frequency.length)})`, "Exact grade"));
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
      details.append(table(["Exact grade", "Count", "% of graded"], measure.frequency.map(f => [f.grade, f.count, f.percentage]), "Aggregate grade frequencies"));
      section.append(details);
    }
    return section;
  };
  form.addEventListener("change", async event => {
    results.hidden = true;
    detailAbort?.abort();
    body.replaceChildren();
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
  form.addEventListener("submit", () => { results.hidden = true; status.textContent = "Loading grade distribution…"; form.setAttribute("aria-busy", "true"); });
  modal.addEventListener("show.bs.modal", async event => {
    opener = event.relatedTarget;
    detailAbort?.abort(); detailAbort = new AbortController();
    const thisAbort = detailAbort;
    body.replaceChildren(node("p", "Loading class analytics…", "text-muted")); body.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(opener.dataset.analyticsUrl, {signal: thisAbort.signal, cache: "no-store", headers: {Accept: "application/json"}});
      const data = await response.json();
      if (thisAbort.signal.aborted || detailAbort !== thisAbort) return;
      if (!response.ok) throw new Error(data.errors?.join(" ") || "Analytics are unavailable. Apply Filters again.");
      body.replaceChildren(node("p", `${data.faculty_name} · ${data.course_code} — ${data.course_title} · ${data.section} · ${data.campus} · ${data.school_year} / ${data.term} · ${data.period} · ${data.physical_period_label} · ${data.status}`));
      body.append(node("p", `Coverage: ${data.graded_count}/${data.eligible_count} selected saved grades; ${data.missing_count} missing; ${data.status_excluded_count} excluded by status. Finalized: ${data.finalized_count}; unfinalized: ${data.unfinalized_count}. Incomplete underlying assessment sources: ${display(data.incomplete_source_count)}. Threshold: ${data.passing_threshold} (${data.threshold_source}).`));
      data.notes.forEach(note => body.append(node("p", note, "small text-muted")));
      body.append(renderMeasure(data, "Period grade", true));
      data.measures.forEach((measure, index) => {
        const section = renderMeasure(measure, measure.label, false);
        if (index < 2) body.append(section); // Saved examination and class-standing comparisons.
        else {
          const details = node("details", undefined, "mb-3");
          details.append(node("summary", measure.analytics_state && measure.analytics_state !== "disclosed" ? `${measure.label}: ${measure.privacy_suppressed ? "Privacy suppressed" : "Unavailable"}` : `${measure.label}: Class Ave. ${display(measure.average)}; exactly 100: ${measure.exact_100_count}`), section);
          body.append(details);
        }
      });
    } catch (error) {
      if (error.name !== "AbortError") { const msg = node("div", error.message, "alert alert-warning"); msg.setAttribute("role", "alert"); body.replaceChildren(msg); }
    } finally { if (detailAbort === thisAbort) body.removeAttribute("aria-busy"); }
  });
  modal.addEventListener("hidden.bs.modal", () => { detailAbort?.abort(); body.replaceChildren(); if (opener?.isConnected && !results.hidden) opener.focus(); });
  window.addEventListener("pageshow", event => { if (event.persisted) { results.hidden = true; body.replaceChildren(); form.removeAttribute("aria-busy"); status.textContent = "Apply Filters again to refresh authorized results."; } });
});
