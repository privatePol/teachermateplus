(() => {
  const form = document.querySelector("[data-bulk-question-form]");
  if (!form) return;
  const filter = form.querySelector("[data-bulk-question-filter]");
  const selectAll = form.querySelector("[data-bulk-select-all]");
  const count = form.querySelector("[data-bulk-selected-count]");
  const button = form.querySelector("[data-bulk-delete-button]");
  const boxes = [...document.querySelectorAll("[data-bulk-question]")];
  const card = box => box.closest(".question-card");
  const visible = box => !card(box).hidden && card(box).getClientRects().length > 0;

  function refresh() {
    boxes.forEach(box => { if (!visible(box)) box.checked = false; });
    const shown = boxes.filter(visible);
    const selected = boxes.filter(box => box.checked);
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
  document.addEventListener("hidden.bs.collapse", refresh);
  document.addEventListener("shown.bs.collapse", refresh);
  form.addEventListener("submit", event => {
    refresh();
    const selected = boxes.filter(box => box.checked).length;
    if (!selected || !window.confirm(
      `Delete ${selected} selected question${selected === 1 ? "" : "s"}? Linked questions will be removed from their Cases; empty Cases remain for separate resolution.`
    )) event.preventDefault();
  });
  refresh();
})();
