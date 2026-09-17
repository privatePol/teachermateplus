(() => {
  const form = document.querySelector('[data-reuse-form]');
  if (!form) return;
  const boxes = [...form.querySelectorAll('[data-reuse-checkbox]')];
  const all = form.querySelector('[data-reuse-select-all]');
  const count = form.querySelector('[data-reuse-selected-count]');
  const cases = form.querySelector('[data-reuse-case-count]');
  const overflow = form.querySelector('[data-reuse-overflow]');
  const copy = form.querySelector('[data-reuse-copy]');
  const remaining = Number(form.dataset.remaining);
  function update() {
    const checked = boxes.filter(box => box.checked);
    const selectedQuestions = checked.reduce((sum, box) => sum + Number(box.closest('[data-reuse-item]').dataset.size), 0);
    const selectedCases = checked.filter(box => box.closest('[data-reuse-item]').dataset.kind === 'case').length;
    count.textContent = String(selectedQuestions);
    cases.textContent = String(selectedCases);
    all.checked = boxes.length > 0 && checked.length === boxes.length;
    all.indeterminate = checked.length > 0 && checked.length < boxes.length;
    const excess = Math.max(0, selectedQuestions - remaining);
    overflow.hidden = excess === 0;
    overflow.textContent = excess ? `Deselect ${excess} question${excess === 1 ? '' : 's'} to fit the remaining Draft slots. A Case cannot be split.` : '';
    if (copy) copy.disabled = checked.length === 0 || excess > 0;
  }
  all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  boxes.forEach(box => box.addEventListener('change', update));
  document.querySelector('#reuse-filters')?.addEventListener('submit', () => { boxes.forEach(box => { box.checked = false; }); });
  update();
})();
