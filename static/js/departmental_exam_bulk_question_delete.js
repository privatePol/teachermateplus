(() => {
  const form = document.querySelector("[data-bulk-question-form]");
  if (!form) return;
  const filter = form.querySelector("[data-bulk-question-filter]");
  const selectAll = form.querySelector("[data-bulk-select-all]");
  const count = form.querySelector("[data-bulk-selected-count]");
  const button = form.querySelector("[data-bulk-delete-button]");
  const moveButton = form.querySelector("[data-section-move-button]");
  const moveError = form.querySelector("[data-section-move-error]");
  const caseBoxes = [...document.querySelectorAll("[data-section-move-case]")];
  const boxes = [...document.querySelectorAll("[data-bulk-question]")];
  const card = box => box.closest(".question-card");
  const visible = box => !card(box).hidden && card(box).getClientRects().length > 0;
  const inCollapsedSection = element => Boolean(element.closest('[data-qb-section-content]')?.hidden);
  const setCount = (attribute, value) => {
    const target = form.querySelector(`[${attribute}]`);
    if (target) target.textContent = String(value);
  };

  function refresh() {
    // Filtering still deselects. A section wrapper hiding an otherwise selected
    // card must not clear it; ordinary nested Case collapse retains its rules.
    boxes.forEach(box => { if (card(box).hidden || (!visible(box) && !inCollapsedSection(box))) box.checked = false; });
    const shown = boxes.filter(visible);
    const selected = boxes.filter(box => box.checked);
    caseBoxes.forEach(box => {
      const members = [...box.closest('.qb-case-card').querySelectorAll('.question-card')];
      if (!members.length || members.some(item => item.hidden)) box.checked = false;
    });
    const linkedSelected = selected.some(box => card(box).dataset.linked === "1");
    const selectedCases = caseBoxes.filter(box => box.checked);
    const affected = new Set(selected.map(card));
    selectedCases.forEach(box => box.closest('.qb-case-card').querySelectorAll('.question-card').forEach(item => affected.add(item)));
    const hiddenQuestions = selected.filter(inCollapsedSection);
    const hiddenCases = selectedCases.filter(inCollapsedSection);
    setCount('data-move-standalone-count', selected.filter(box => card(box).dataset.linked === '0').length);
    setCount('data-move-case-count', selectedCases.length);
    setCount('data-move-question-count', affected.size);
    setCount('data-hidden-question-count', hiddenQuestions.length);
    setCount('data-hidden-standalone-count', hiddenQuestions.filter(box => card(box).dataset.linked === '0').length);
    setCount('data-hidden-case-count', hiddenCases.length);
    setCount('data-hidden-affected-count', [...affected].filter(inCollapsedSection).length);
    const hiddenSummary = form.querySelector('[data-section-hidden-summary]');
    if (hiddenSummary) hiddenSummary.hidden = !hiddenQuestions.length && !hiddenCases.length;
    if (moveButton) moveButton.disabled = linkedSelected || (!selected.length && !selectedCases.length);
    if (moveError) moveError.hidden = !linkedSelected;
    count.textContent = String(selected.length);
    button.disabled = selected.length === 0;
    selectAll.checked = shown.length > 0 && shown.every(box => box.checked);
    selectAll.indeterminate = shown.some(box => box.checked) && !selectAll.checked;
  }

  filter.addEventListener("change", () => {
    boxes.forEach(box => {
      const item = card(box);
      const value = filter.value;
      item.hidden = value !== "all" && value !== item.dataset.difficulty &&
        !(value === "linked" && item.dataset.linked === "1") &&
        !(value === "standalone" && item.dataset.linked === "0");
    });
    refresh();
  });
  selectAll.addEventListener("change", () => {
    boxes.filter(visible).forEach(box => { box.checked = selectAll.checked; });
    refresh();
  });
  boxes.forEach(box => box.addEventListener("change", refresh));
  caseBoxes.forEach(box => box.addEventListener("change", refresh));
  document.addEventListener("hidden.bs.collapse", refresh);
  document.addEventListener("shown.bs.collapse", refresh);
  document.addEventListener("tmp:section-visibility-changed", refresh);
  form.addEventListener("submit", event => {
    refresh();
    if (event.submitter?.matches('[data-section-move-button]')) {
      if (moveButton.disabled) event.preventDefault();
      return;
    }
    const selected = boxes.filter(box => box.checked).length;
    if (!selected || !window.confirm(
      `Delete ${selected} selected question${selected === 1 ? "" : "s"}? Linked questions will be removed from their Cases; empty Cases remain for separate resolution.`
    )) event.preventDefault();
  });
  refresh();
})();
