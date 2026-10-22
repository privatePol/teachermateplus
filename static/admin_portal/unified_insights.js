/* Isolated preview: explicit generation, fresh authorization, no student data. */
(() => {
  "use strict";
  const form = document.getElementById("learning-filters");
  if (!form) return;
  const results = document.getElementById("learning-results");
  const progress = document.getElementById("learning-progress");
  const message = document.getElementById("learning-message");
  const view = document.getElementById("learning-view");
  const retry = document.getElementById("learning-retry");
  const overall = document.getElementById("learning-overall");
  const csvControls = document.getElementById("learning-csv-controls");
  const csvPeriod = document.getElementById("learning-csv-period");
  const links = [...document.querySelectorAll("[data-learning-export]")];
  const exportBase = links[0]?.href.split("?")[0];
  const labels = {campus_id: "Campus", academic_year_id: "Academic Year", term_id: "Term", course_code: "Course", offering_id: "Section"};
  const empty = {campus_id: "All authorized campuses", academic_year_id: "Select academic year", term_id: "Select term", course_code: "All authorized courses", offering_id: "All sections in selected course"};
  const selected = JSON.parse(document.getElementById("learning-selected").textContent);
  const field = name => form.elements.namedItem(name);
  form.querySelectorAll("select").forEach(select => {
    select.value = selected[select.name] || "";
    select.options[0].textContent = empty[select.name];
  });
  document.querySelectorAll("[data-learning-label]").forEach(label => { label.textContent = labels[label.dataset.learningLabel]; });
  let epoch = 0, token = null, verified = true, generated = false, activeTab = "overview";
  const tokens = new Map();
  const tabButtons = [...document.querySelectorAll("[data-unified-tab]")];
  const engine = () => activeTab === "overview" ? "overview" : "assessment";
  const pending = new Map();
  const charts = [];
  function status(text, error = false) {
    message.textContent = text;
    message.className = error ? "alert alert-danger" : "alert alert-info";
  }
  function exports() {
    const enabled = verified && tokens.has("overview") && csvPeriod.value;
    const params = new URLSearchParams();
    for (const name of ["campus_id", "academic_year_id", "term_id", "course_code"]) {
      if (field(name).value) params.set(name, field(name).value);
    }
    if (csvPeriod.value) params.set("period_code", csvPeriod.value);
    links.forEach(link => {
      params.set("export", link.dataset.learningExport);
      link.href = `${exportBase}?${params}`;
      link.setAttribute("aria-disabled", String(!enabled));
    });
  }
  function destroyCharts() { charts.splice(0).forEach(chart => chart.destroy()); }
  function clearResults() {
    epoch += 1;
    pending.forEach(controller => controller.abort()); pending.clear();
    destroyCharts(); results.replaceChildren(); results.setAttribute("aria-busy", "false");
    token = null; progress.hidden = true;
  }
  function clear() {
    clearResults(); tokens.clear(); generated = false; csvControls.hidden = true;
    csvPeriod.replaceChildren(new Option("Select a concluded period", ""));
    overall.hidden = !field("offering_id").value;
    exports(); status("Select your filters and click View Performance.");
  }
  async function request(action, values = {}) {
    const version = epoch;
    const controller = new AbortController();
    pending.get(action)?.abort(); pending.set(action, controller);
    progress.hidden = false; results.setAttribute("aria-busy", "true");
    const body = new FormData(form);
    body.set("action", action); body.set("tab", activeTab);
    if (token) body.set("token", token);
    Object.entries(values).forEach(([name, value]) => body.set(name, value));
    try {
      const response = await fetch(location.pathname, {method: "POST", body, credentials: "same-origin", signal: controller.signal, headers: {Accept: "application/json"}});
      if (version !== epoch || pending.get(action) !== controller) return;
      if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Your session may have expired. Reload the page and try again.");
      const data = await response.json();
      if (version !== epoch || pending.get(action) !== controller) return;
      if (response.status === 409 && action !== "options") {
        clear(); status(data.error || "Academic records changed. Click View Performance again.", true); return;
      }
      if (!response.ok) throw new Error(data.error || "Unable to load learning performance.");
      return data;
    } catch (error) {
      if (version !== epoch || pending.get(action) !== controller) return;
      if (error.name === "AbortError" && action !== "options") return;
      status(error.name === "AbortError" ? "Selection verification was interrupted. Use Retry Selections." : error.message, true);
      return null;
    } finally {
      if (pending.get(action) === controller) {
        pending.delete(action); progress.hidden = pending.size === 0;
        results.setAttribute("aria-busy", String(pending.size !== 0));
      }
    }
  }
  async function verify() {
    verified = false; view.disabled = true; retry.hidden = true; retry.disabled = true; exports();
    const version = epoch;
    const data = await request("options");
    if (version !== epoch || data === undefined) return;
    if (data === null) { retry.hidden = false; retry.disabled = false; return; }
    Object.entries(data.options).forEach(([name, options]) => {
      const select = field(name);
      select.replaceChildren(new Option(empty[name], ""));
      options.forEach(option => select.add(new Option(option.label, option.value)));
      select.value = data.filters[name] || "";
    });
    verified = true; view.disabled = false; retry.hidden = true;
    overall.hidden = !field("offering_id").value;
    exports(); status("Select your filters and click View Performance.");
  }
  const number = value => value === null || value === undefined ? null : Number(value);
  function renderCharts(data) {
    destroyCharts();
    if (!window.Chart) { status("Results are ready. Charts are unavailable; equivalent values are shown below.", true); return; }
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const common = {responsive: true, maintainAspectRatio: false, animation: reduce ? false : {duration: 350},
      plugins: {legend: {position: "bottom"}}, interaction: {mode: "index", intersect: false}};
    const scoreScale = {min: 0, max: 100, title: {display: true, text: "Gradebook quiz / official grade points (0–100)"}};
    const add = (id, config) => {
      const canvas = document.getElementById(id);
      if (canvas) charts.push(new window.Chart(canvas, config));
    };
    const palette = ["#3884e9", "#239963", "#ed8545", "#8c65c6"];
    const overviewRule = `Meaningful change: at least ${data.threshold} percentage points. Quiz trends use original quiz percentages before Gradebook conversion. Displayed quiz averages follow the Gradebook. Class-standing and examination trends use their official comparable scores. Comparisons are calculated before rounding.`;
    if (activeTab === "exams") {
      add("unified-exams", {type: "line", data: {labels: data.exams.labels, datasets: data.exams.datasets.map((row, i) => ({
        label: row.label, data: row.values.map(number), showLine: row.connected, spanGaps: false,
        borderColor: palette[i % palette.length], backgroundColor: palette[i % palette.length], tension: 0
      }))}, options: {...common, scales: {y: scoreScale}}});
      return;
    }
    const patternRule = activeTab === "overview" ? overviewRule : "Meaningful change: at least 3 Gradebook percentage points before rounding. Movement uses the same students across all six independent Quiz, Participation/Output and Examination measures.";
    add("learning-line", {type: "line", data: {labels: data.line.map(r => r.period), datasets:
      [["quiz_grade", "Quiz average (Gradebook points)"], ["standing", "Class standing (grade points)"], ["exam", "Exam (grade points)"], ["periodic", "Periodic grade"]].map(([key, label], i) => ({label, data: data.line.map(r => number(r[key])), borderColor: palette[i], backgroundColor: palette[i], tension: .15, spanGaps: false, showLine: data.line_mode !== "descriptive"}))}, options: {...common, scales: {y: scoreScale}, plugins: {...common.plugins, tooltip: {callbacks: {afterBody: items => `Original quiz percentage: ${items.length ? data.line[items[0].dataIndex].quiz ?? "Not Available" : "Not Available"}%\n${patternRule}`}}}}});
    add("learning-bars", {type: "bar", data: {labels: data.bars.map(r => r.section.length > 32 ? r.section.slice(0, 30) + "…" : r.section), datasets:
      [["quiz_grade", "Quiz average (Gradebook points)"], ["standing", activeTab === "alignment" ? "Participation/Output" : "Class-standing average"], ["exam", "Exam average"]].map(([key, label], i) => ({label, data: data.bars.map(r => number(r[key])), backgroundColor: palette[i], borderRadius: 5}))},
      options: {...common, scales: {y: scoreScale, x: {ticks: {autoSkip: true, maxTicksLimit: 8, maxRotation: 25}}}, plugins: {...common.plugins, tooltip: {callbacks: {title: items => items.length ? data.bars[items[0].dataIndex].section : "", afterBody: items => activeTab === "alignment" ? patternRule : items.length ? `Recorded quizzes: ${data.bars[items[0].dataIndex].quizzes ?? "Not Available"}\nOriginal quiz percentage: ${data.bars[items[0].dataIndex].quiz ?? "Not Available"}%\n${patternRule}` : ""}}}}});
    const patternColors = ["#0072b2", "#8b509b", "#b56b00", "#596773"];
    const visiblePatterns = data.patterns.map((row, index) => ({...row, color: patternColors[index]})).filter(row => Number(row.count) > 0);
    if (visiblePatterns.length) add("learning-patterns", {type: "doughnut", data: {labels: visiblePatterns.map(r => r.label), datasets: [{data: visiblePatterns.map(r => r.count), backgroundColor: visiblePatterns.map(r => r.color), borderColor: "#fff", borderWidth: 2}]}, options: {...common, cutout: "64%", plugins: {...common.plugins, title: {display: true, text: `Meaningful change: at least ${data.threshold} percentage points`}, tooltip: {callbacks: {label: item => `${item.label}: ${visiblePatterns[item.dataIndex].count} section${Number(visiblePatterns[item.dataIndex].count) === 1 ? "" : "s"} (${visiblePatterns[item.dataIndex].percentage}%)`, afterBody: () => patternRule}}}}});
  }
  function render(data) {
    token = data.token; tokens.set(engine(), token); results.innerHTML = data.html;
    if (activeTab === "overview") {
    const previous = csvPeriod.value;
    csvPeriod.replaceChildren(new Option("Select a concluded period", ""));
    data.export_periods.forEach(([value, label]) => csvPeriod.add(new Option(label, value)));
    csvPeriod.value = data.export_periods.some(([value]) => value === previous) ? previous : "";
    csvControls.hidden = !data.export_periods.length;
    }
    exports(); status("Concluded-period results are ready. Refresh explicitly after academic records change.");
    try { renderCharts(data.charts); } catch (_) { destroyCharts(); status("Results are ready. Charts could not be drawn; equivalent values are shown below.", true); }
  }
  form.addEventListener("change", () => { clear(); return verify(); });
  retry.addEventListener("click", () => { if (!retry.disabled && !verified) return verify(); });
  overall.addEventListener("click", () => { field("offering_id").value = ""; clear(); return verify(); });
  async function load(page = 1) {
    if (!verified || view.disabled || !form.reportValidity()) return;
    clearResults(); token = tokens.get(engine()) || null;
    view.disabled = true;
    const version = epoch;
    const data = await request(token ? "page" : "report", {page});
    if (version !== epoch) { if (verified) view.disabled = false; return; }
    view.disabled = false;
    if (data) {
      generated = true;
      tokens.set(data.tab === "overview" ? "overview" : "assessment", data.token);
      // A rapid switch within the assessment engine waits for the first build,
      // then requests the other presentation from that completed generation.
      if (data.tab !== activeTab) return load();
      render(data);
    }
  }
  async function selectTab(name) {
    if (!tabButtons.some(button => button.dataset.unifiedTab === name)) return;
    if (name === activeTab) return;
    const sameEnginePending = (name === "overview" ? "overview" : "assessment") === engine()
      && (pending.has("report") || pending.has("page"));
    activeTab = name;
    results.setAttribute("aria-labelledby", `tab-${name}`);
    tabButtons.forEach(button => {
      const selected = button.dataset.unifiedTab === name;
      button.setAttribute("aria-selected", String(selected));
      button.setAttribute("tabindex", selected ? "0" : "-1");
      button.className = selected ? "nav-link active" : "nav-link";
    });
    // Filter validation belongs to the shared selection, not to a tab. Keep
    // its controller/epoch alive so the current response can restore controls.
    if (pending.has("options")) return;
    if (sameEnginePending) { destroyCharts(); results.replaceChildren(); return; }
    clearResults(); view.disabled = !verified;
    if (generated) await load();
  }
  tabButtons.forEach((button, index) => {
    button.addEventListener("click", () => selectTab(button.dataset.unifiedTab));
    button.addEventListener("keydown", event => {
      const target = event.key === "ArrowRight" ? (index + 1) % tabButtons.length :
        event.key === "ArrowLeft" ? (index + tabButtons.length - 1) % tabButtons.length :
        event.key === "Home" ? 0 : event.key === "End" ? tabButtons.length - 1 : null;
      if (target === null) return;
      event.preventDefault(); tabButtons[target].focus(); selectTab(tabButtons[target].dataset.unifiedTab);
    });
  });
  form.addEventListener("submit", event => { event.preventDefault(); return load(); });
  csvPeriod.addEventListener("change", exports);
  document.addEventListener("click", async event => {
    const link = event.target.closest("[data-learning-export]");
    if (link && link.getAttribute("aria-disabled") === "true") {
      event.preventDefault(); status("View performance, then select a CSV grading period before downloading.", true); return;
    }
    const course = event.target.closest("[data-unified-course]");
    const section = event.target.closest("[data-unified-section]");
    if (course || section) {
      event.preventDefault();
      if (course) { field("course_code").value = course.dataset.unifiedCourse; field("offering_id").value = ""; }
      else field("offering_id").value = section.dataset.unifiedSection;
      clear(); await verify(); if (verified) await load(); return;
    }
    const button = event.target.closest("[data-unified-page], [data-learning-page]");
    if (!button || !generated || !verified) return;
    event.preventDefault(); return load(button.dataset.unifiedPage || button.dataset.learningPage);
  });
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", event => {
    if (event.persisted) { clear(); if (!verified) verify(); else view.disabled = false; }
  });
  overall.hidden = !field("offering_id").value;
  exports();
})();
