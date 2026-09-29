(() => {
  const endpoints = document.getElementById("equivalency-endpoints");
  if (!endpoints) return;
  const list = document.getElementById("equivalency-list");
  const editor = document.getElementById("equivalency-editor");
  const form = document.getElementById("equivalency-form");
  const label = document.getElementById("equivalency-label");
  const membersBox = document.getElementById("equivalency-members");
  const primary = document.getElementById("equivalency-primary");
  const searchPanel = document.getElementById("equivalency-search-panel");
  const search = document.getElementById("equivalency-search");
  const results = document.getElementById("equivalency-search-results");
  const reasonPanel = document.getElementById("equivalency-reason-panel");
  const reasonForm = document.getElementById("equivalency-reason-form");
  const reason = document.getElementById("equivalency-reason");
  const recoveryPanel = document.getElementById("equivalency-recovery-panel");
  const recoveryEvidence = document.getElementById("equivalency-recovery-evidence");
  const recoveryReason = document.getElementById("equivalency-recovery-reason");
  const notice = document.getElementById("equivalency-notice");
  const existingPanel = document.getElementById("equivalency-existing-review");
  const existingEvidence = document.getElementById("equivalency-existing-evidence");
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]').value;
  let definitions = JSON.parse(document.getElementById("equivalency-initial-definitions").textContent);
  let selected = [];
  let editing = null;
  let action = null;
  let recovery = null;
  let existingReview = null;
  let searchSequence = 0;

  function endpoint(name) {
    const url = new URL(endpoints.dataset[name], window.location.origin);
    if (endpoints.dataset.cycle) url.searchParams.set("cycle_id", endpoints.dataset.cycle);
    return url;
  }

  function message(value, danger = false) {
    notice.className = value ? `alert ${danger ? "alert-warning" : "alert-success"}` : "";
    notice.textContent = value;
  }

  function renderExistingEvidence(evidence) {
    existingEvidence.replaceChildren();
    const line = (parent, tag, value) => {
      const node = document.createElement(tag);
      node.textContent = value;
      parent.append(node);
      return node;
    };
    line(existingEvidence, "p", `${evidence.label} · saved version ${evidence.definition_version} · cycle ${evidence.cycle_id}`);
    evidence.members.forEach(member => {
      const card = document.createElement("div");
      card.className = "border-top pt-2 mt-2";
      line(card, "h3", `${member.code} — ${member.title}${member.is_primary ? " (primary)" : ""}`);
      line(card, "p", `CycleCourse ${member.cycle_course_id} · Course ${member.course_id} · ${member.classification} · ${member.inclusion}`);
      const campuses = document.createElement("ul");
      member.campuses.forEach(campus => line(campuses, "li",
        `${campus.campus} (campus ${campus.campus_id}, frozen offering ${campus.offering_id}, snapshot ${campus.snapshot_id})`));
      card.append(campuses);
      line(card, "h4", "Course configuration").className = "h6";
      line(card, "pre", JSON.stringify(member.configuration_state, null, 2)).className = "text-wrap small";
      existingEvidence.append(card);
    });
    line(existingEvidence, "h3", "Blueprint ownership and ordered sections").className = "h6 mt-3";
    if (!evidence.blueprints.length) line(existingEvidence, "p", "No blueprint exists for these members.");
    evidence.blueprints.forEach(blueprint => {
      line(existingEvidence, "p", `${blueprint.ownership} · blueprint ${blueprint.id} · CycleCourse ${blueprint.cycle_course_id} · revision ${blueprint.revision} · ${blueprint.mode}`);
      const sections = document.createElement("ol");
      blueprint.sections.forEach(section => line(sections, "li",
        `${section[0]}. ${section[1]} · ${section[3]} items · ${section[2] || "No instructions"}`));
      existingEvidence.append(sections);
    });
  }

  function clearErrors() {
    form.querySelectorAll("[data-error]").forEach(node => { node.textContent = ""; });
    form.querySelectorAll(".is-invalid").forEach(node => node.classList.remove("is-invalid"));
    reasonPanel.querySelector("[data-reason-error]").textContent = "";
  }

  function fieldErrors(errors) {
    clearErrors();
    Object.entries(errors || {}).forEach(([name, values]) => {
      const node = form.querySelector(`[data-error="${name}"]`);
      if (node) node.textContent = Array.isArray(values) ? values.join(" ") : String(values);
      const input = name === "label" ? label : name === "primary" ? primary : null;
      if (input) input.classList.add("is-invalid");
      if (!node) message(Array.isArray(values) ? values.join(" ") : String(values), true);
    });
  }

  function renderMembers() {
    membersBox.replaceChildren();
    primary.replaceChildren();
    primary.add(new Option("Select a member", ""));
    selected.forEach(course => {
      const row = document.createElement("div");
      row.className = "d-flex align-items-center gap-2 mb-1";
      const text = document.createElement("span");
      text.textContent = `${course.code} — ${course.title}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-outline-danger";
      remove.textContent = `Remove ${course.code}`;
      remove.setAttribute("aria-label", `Remove ${course.code}`);
      remove.addEventListener("click", () => {
        selected = selected.filter(row => row.id !== course.id);
        const oldPrimary = primary.value;
        renderMembers();
        if (oldPrimary !== String(course.id)) primary.value = oldPrimary;
      });
      row.append(text, remove);
      membersBox.append(row);
      primary.add(new Option(course.code, String(course.id)));
    });
  }

  function openEditor(row = null) {
    editing = row;
    selected = row ? row.members.map(member => ({ ...member })) : [];
    label.value = row ? row.label : "";
    renderMembers();
    primary.value = row ? String(row.primary_id) : "";
    editor.querySelector("h2").textContent = row ? "Edit equivalent group" : "Create equivalent group";
    editor.hidden = false;
    reasonPanel.hidden = true;
    searchPanel.hidden = true;
    clearErrors();
    message("");
    label.focus();
  }

  async function refresh() {
    const url = new URL(window.location.href);
    url.searchParams.set("format", "fragment");
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("The saved group list could not be refreshed.");
    const data = await response.json();
    list.innerHTML = data.html;
    definitions = data.definitions;
  }

  async function submit(name, payload) {
    const response = await fetch(endpoint(name), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Accept": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = response.headers.get("Content-Type")?.includes("application/json")
      ? await response.json()
      : { ok: false, errors: { __all__: [response.status === 403
        ? "Your access changed. Refresh and review this action."
        : "The action is unavailable. Refresh and review the current list."] } };
    if (!response.ok || !data.ok) {
      if (response.status === 409) await refresh();
      return { ok: false, data, status: response.status };
    }
    list.innerHTML = data.html;
    definitions = data.definitions;
    message(data.notice);
    return { ok: true, data };
  }

  document.getElementById("equivalency-create").addEventListener("click", () => openEditor());
  document.getElementById("equivalency-cancel").addEventListener("click", () => { editor.hidden = true; });
  document.getElementById("equivalency-add-course").addEventListener("click", () => {
    if (!label.value.trim()) {
      fieldErrors({ label: ["Enter the equivalent label before adding courses."] });
      label.focus();
      return;
    }
    searchPanel.hidden = false;
    search.value = "";
    results.replaceChildren();
    search.focus();
  });

  search.addEventListener("input", async () => {
    const sequence = ++searchSequence;
    const query = search.value.trim();
    results.replaceChildren();
    if (query.length < 2) return;
    const url = endpoint("search");
    url.searchParams.set("q", query);
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Search is unavailable.");
      const data = await response.json();
      if (sequence !== searchSequence) return;
      data.courses.filter(course => !selected.some(row => row.id === course.id)).forEach(course => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.textContent = `${course.code} — ${course.title}`;
        button.addEventListener("click", () => {
          selected.push(course);
          const oldPrimary = primary.value;
          renderMembers();
          primary.value = oldPrimary;
          searchPanel.hidden = true;
          results.replaceChildren();
          document.getElementById("equivalency-add-course").focus();
        });
        results.append(button);
      });
      if (!results.childElementCount) results.textContent = "No accessible matching courses.";
    } catch (error) { if (sequence === searchSequence) results.textContent = error.message; }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    clearErrors();
    if (!label.value.trim()) { fieldErrors({ label: ["Enter an equivalent label."] }); label.focus(); return; }
    if (selected.length < 2) { fieldErrors({ members: ["Add at least two distinct courses."] }); return; }
    if (!selected.some(row => String(row.id) === primary.value)) {
      fieldErrors({ primary: ["Choose a primary from the selected courses."] }); primary.focus(); return;
    }
    try {
      const evidence = { ...(editing?.evidence || {}) };
      selected.forEach(row => { evidence[String(row.id)] = row.evidence; });
      const result = await submit("save", { id: editing?.id || null, version: editing?.version || null,
        label: label.value, members: selected.map(row => row.id), primary: Number(primary.value), evidence });
      if (result.ok) editor.hidden = true;
      else fieldErrors(result.data.errors);
    } catch (error) { message(error.message, true); }
  });

  list.addEventListener("click", async event => {
    const existingButton = event.target.closest("[data-existing-cycle-review]");
    if (existingButton) {
      existingPanel.hidden = true;
      existingReview = null;
      const url = endpoint("existingReview");
      url.searchParams.set("definition_id", existingButton.dataset.existingCycleReview);
      try {
        const response = await fetch(url, { credentials: "same-origin" });
        const data = await response.json();
        if (!response.ok) {
          message(Object.values(data.errors || {}).flat().join(" ") || "Review is unavailable.", true);
          return;
        }
        existingReview = { definitionId: Number(existingButton.dataset.existingCycleReview), token: data.review_token };
        renderExistingEvidence(data.evidence);
        existingPanel.hidden = false;
        existingPanel.scrollIntoView({ block: "nearest" });
      } catch (error) { message(error.message, true); }
      return;
    }
    const edit = event.target.closest("[data-equivalency-edit]");
    if (edit) {
      const row = definitions.find(item => item.id === Number(edit.dataset.equivalencyEdit));
      if (row) openEditor(row);
      return;
    }
    const applyButton = event.target.closest("[data-equivalency-apply]");
    if (applyButton) {
      applyButton.disabled = true;
      try {
        const result = await submit("apply", {
          cycle_id: Number(endpoints.dataset.cycle),
          plan_id: Number(applyButton.dataset.equivalencyApply),
        });
        if (!result.ok) message(Object.values(result.data.errors || {}).flat().join(" "), true);
      } catch (error) { message(error.message, true); }
      finally { applyButton.disabled = false; }
      return;
    }
    const recoveryButton = event.target.closest("[data-equivalency-recovery]");
    if (recoveryButton) {
      const url = endpoint("recoveryReview");
      url.searchParams.set("plan_id", recoveryButton.dataset.equivalencyRecovery);
      try {
        const response = await fetch(url, { credentials: "same-origin" });
        const data = await response.json();
        if (!response.ok) {
          message(Object.values(data.errors || {}).flat().join(" ") || "Blueprint review is unavailable.", true);
          return;
        }
        recovery = data;
        recoveryEvidence.replaceChildren();
        [data.primary, data.secondary].forEach((row, index) => {
          const heading = document.createElement("h3");
          heading.className = "h6";
          heading.textContent = `${index ? "Secondary (retained)" : "Primary (used)"}: ${row.code} · blueprint ${row.blueprint_id} · revision ${row.revision} · ${row.mode}`;
          const items = document.createElement("ol");
          row.sections.forEach(section => {
            const item = document.createElement("li");
            item.textContent = `${section.display_order}. ${section.title} — ${section.item_quota} items; ${section.instructions || "No instructions"}`;
            items.append(item);
          });
          recoveryEvidence.append(heading, items);
        });
        recoveryReason.value = "";
        recoveryPanel.querySelector("[data-recovery-error]").textContent = "";
        recoveryPanel.hidden = false;
        editor.hidden = true;
        reasonPanel.hidden = true;
        recoveryReason.focus();
      } catch (error) { message(error.message, true); }
      return;
    }
    const button = event.target.closest("[data-equivalency-action]");
    if (!button) return;
    action = { type: button.dataset.equivalencyAction, id: Number(button.dataset.id),
      version: Number(button.dataset.version), reviewToken: button.dataset.reviewToken || "" };
    reasonPanel.querySelector("h2").textContent = action.type === "adopt" ? "Review historical adoption" :
      action.type === "retire" ? "Retire saved group" : "Record cycle exception";
    document.getElementById("equivalency-reason-context").textContent = button.closest("article")?.innerText || "";
    reason.value = "";
    reasonPanel.hidden = false;
    editor.hidden = true;
    clearErrors();
    reason.focus();
  });
  document.getElementById("equivalency-reason-cancel").addEventListener("click", () => { reasonPanel.hidden = true; });
  document.getElementById("equivalency-existing-cancel").addEventListener("click", () => {
    existingPanel.hidden = true;
    existingReview = null;
  });
  document.getElementById("equivalency-existing-confirm").addEventListener("click", async event => {
    if (!existingReview) return;
    event.target.disabled = true;
    try {
      const result = await submit("existingApply", {
        cycle_id: Number(endpoints.dataset.cycle),
        definition_id: existingReview.definitionId,
        review_token: existingReview.token,
      });
      existingPanel.hidden = true;
      existingReview = null;
      if (!result.ok) message("The application was not made. Review fresh cycle facts before retrying. " +
        Object.values(result.data.errors || {}).flat().join(" "), true);
    } catch (error) { message(error.message, true); }
    finally { event.target.disabled = false; }
  });
  document.getElementById("equivalency-recovery-cancel").addEventListener("click", () => { recoveryPanel.hidden = true; });
  document.getElementById("equivalency-recovery-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!recovery || recoveryReason.value.trim().length < 10) {
      recoveryPanel.querySelector("[data-recovery-error]").textContent = "Enter a reason of at least 10 characters.";
      return;
    }
    try {
      const result = await submit("recoveryRetain", {
        cycle_id: Number(endpoints.dataset.cycle), plan_id: recovery.plan_id,
        primary_cycle_course_id: recovery.primary.cycle_course_id,
        secondary_cycle_course_id: recovery.secondary.cycle_course_id,
        primary_revision: recovery.primary.revision,
        secondary_revision: recovery.secondary.revision,
        primary_digest: recovery.primary.digest,
        secondary_digest: recovery.secondary.digest,
        reason: recoveryReason.value,
      });
      if (result.ok) recoveryPanel.hidden = true;
      else recoveryPanel.querySelector("[data-recovery-error]").textContent =
        Object.values(result.data.errors || {}).flat().join(" ");
    } catch (error) { message(error.message, true); }
  });
  reasonForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (reason.value.trim().length < 10) {
      reasonPanel.querySelector("[data-reason-error]").textContent = "Enter at least 10 characters.";
      reason.focus();
      return;
    }
    const payload = { reason: reason.value };
    if (action.type === "retire") Object.assign(payload, { id: action.id, version: action.version });
    if (action.type === "adopt") Object.assign(payload, {
      group_id: action.id, review_token: action.reviewToken,
    });
    if (action.type === "exception") Object.assign(payload, { plan_id: action.id, cycle_id: Number(endpoints.dataset.cycle) });
    try {
      const result = await submit(action.type, payload);
      if (result.ok) reasonPanel.hidden = true;
      else if (action.type === "adopt" && result.status === 409) {
        reasonPanel.hidden = true;
        message("The historical group changed. Review the refreshed details before adopting it.", true);
      }
      else reasonPanel.querySelector("[data-reason-error]").textContent =
        Object.values(result.data.errors || {}).flat().join(" ");
    } catch (error) { message(error.message, true); }
  });
})();
