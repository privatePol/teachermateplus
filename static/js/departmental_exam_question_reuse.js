(() => {
  const form = document.querySelector('[data-reuse-form]');
  const filters = document.querySelector('#reuse-filters');
  if (!form || !filters) return;
  const cards = form.querySelector('[data-reuse-cards]');
  const all = form.querySelector('[data-reuse-select-all]');
  const selectedOnlyButton = form.querySelector('[data-reuse-selected-only]');
  const count = form.querySelector('[data-reuse-selected-count]');
  const cases = form.querySelector('[data-reuse-case-count]');
  const overflow = form.querySelector('[data-reuse-overflow]');
  const copyButtons = [...form.querySelectorAll('[data-reuse-copy]')];
  const noResults = form.querySelector('[data-reuse-no-results]');
  const selectedEmpty = form.querySelector('[data-reuse-selected-empty]');
  const status = form.querySelector('[data-reuse-load-status]');
  const retry = form.querySelector('[data-reuse-retry]');
  const sentinel = form.querySelector('[data-reuse-sentinel]');
  const backTop = document.querySelector('[data-reuse-back-top]');
  const remaining = Number(form.dataset.remaining);
  let nextPage = Number(form.dataset.nextPage) || null;
  let loading = false;
  let selectedOnly = false;
  let submitting = false;
  let filterChanged = false;
  let loadFailed = false;
  let generation = 0;
  let controller = null;
  const initialFilters = new URLSearchParams(new FormData(filters)).toString();
  const tokens = new Set([...cards.querySelectorAll('[data-reuse-item]')].map(item => item.dataset.reuseToken));

  function boxes() {
    return [...cards.querySelectorAll('[data-reuse-checkbox]')];
  }

  function update() {
    const current = boxes();
    const checked = current.filter(box => box.checked);
    const selectedQuestions = checked.reduce((sum, box) => sum + Number(box.closest('[data-reuse-item]').dataset.size), 0);
    const selectedCases = checked.filter(box => box.closest('[data-reuse-item]').dataset.kind === 'case').length;
    count.textContent = String(selectedQuestions);
    cases.textContent = String(selectedCases);
    current.forEach(box => {
      const item = box.closest('[data-reuse-item]');
      item.classList.toggle('is-selected', box.checked);
      item.hidden = selectedOnly && !box.checked;
    });
    const visible = current.filter(box => !box.disabled && !box.closest('[data-reuse-item]').hidden);
    const visibleChecked = visible.filter(box => box.checked);
    all.checked = visible.length > 0 && visibleChecked.length === visible.length;
    all.indeterminate = visibleChecked.length > 0 && visibleChecked.length < visible.length;
    selectedEmpty.hidden = !selectedOnly || checked.length > 0;
    noResults.hidden = current.length > 0 || selectedOnly;
    const excess = Math.max(0, selectedQuestions - remaining);
    overflow.hidden = excess === 0;
    overflow.textContent = excess ? `Deselect ${excess} question${excess === 1 ? '' : 's'} to fit the remaining Draft slots. A Case cannot be split.` : '';
    copyButtons.forEach(button => { button.disabled = submitting || checked.length === 0 || excess > 0; });
  }

  all.addEventListener('change', () => {
    boxes().filter(box => !box.disabled && !box.closest('[data-reuse-item]').hidden)
      .forEach(box => { box.checked = all.checked; });
    update();
  });
  cards.addEventListener('change', event => {
    if (event.target.matches('[data-reuse-checkbox]')) update();
  });
  selectedOnlyButton.addEventListener('click', () => {
    selectedOnly = !selectedOnly;
    if (selectedOnly && controller) {
      generation += 1;
      controller.abort();
    }
    selectedOnlyButton.setAttribute('aria-pressed', String(selectedOnly));
    selectedOnlyButton.textContent = selectedOnly ? 'Show all questions' : 'Show all selected';
    update();
    if (selectedOnly) {
      retry.hidden = true;
      status.textContent = 'Showing selected questions. Return to all questions to load more.';
    } else if (loadFailed) {
      retry.hidden = false;
      status.textContent = 'More questions could not be loaded.';
    } else {
      status.textContent = nextPage ? `${boxes().length} items loaded. Scroll for more.` : 'All matching questions loaded.';
    }
    if (!selectedOnly) maybeLoad();
  });

  function invalidateFilters() {
    if (filterChanged) return;
    filterChanged = true;
    generation += 1;
    if (controller) controller.abort();
    loadFailed = false;
    boxes().forEach(box => { box.checked = false; });
    update();
    retry.hidden = true;
    status.textContent = 'Apply filters to see the new results.';
  }
  filters.addEventListener('input', invalidateFilters);
  filters.addEventListener('change', invalidateFilters);
  filters.addEventListener('submit', invalidateFilters);

  form.addEventListener('submit', event => {
    if (submitting || copyButtons.every(button => button.disabled)) {
      event.preventDefault();
      return;
    }
    submitting = true;
    copyButtons.forEach(button => { button.disabled = true; });
  });

  async function loadNext() {
    if (loading || !nextPage || selectedOnly || filterChanged || submitting) return;
    loading = true;
    const page = nextPage;
    const requestGeneration = generation;
    controller = new AbortController();
    retry.hidden = true;
    status.textContent = 'Loading more questions…';
    cards.setAttribute('aria-busy', 'true');
    try {
      const url = new URL(window.location.href);
      url.search = initialFilters;
      url.searchParams.set('batch', '1');
      url.searchParams.set('page', String(page));
      const response = await fetch(url.toString(), {
        credentials: 'same-origin', signal: controller.signal,
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) throw new Error('Batch unavailable');
      const batch = await response.json();
      if (requestGeneration !== generation || new URLSearchParams(new FormData(filters)).toString() !== initialFilters) return;
      const fragment = document.createElement('template');
      fragment.innerHTML = batch.html;
      fragment.content.querySelectorAll('[data-reuse-item]').forEach(item => {
        const token = item.dataset.reuseToken;
        if (!token || tokens.has(token)) return;
        tokens.add(token);
        cards.appendChild(item);
        item.querySelectorAll('[data-scientific-content]').forEach(element => {
          window.TMPScientificNotation?.renderElement(element);
        });
      });
      nextPage = Number(batch.next_page) > page ? Number(batch.next_page) : null;
      loadFailed = false;
      status.textContent = nextPage ? `${boxes().length} items loaded. Scroll for more.` : 'All matching questions loaded.';
      update();
    } catch (error) {
      if (requestGeneration === generation && error.name !== 'AbortError') {
        loadFailed = true;
        status.textContent = 'More questions could not be loaded.';
        retry.hidden = false;
      }
    } finally {
      cards.removeAttribute('aria-busy');
      loading = false;
      controller = null;
      if (requestGeneration !== generation && !selectedOnly && !filterChanged) maybeLoad();
    }
  }

  function maybeLoad() {
    if (selectedOnly || !nextPage || filterChanged || loading) return;
    if (sentinel.getBoundingClientRect().top <= window.innerHeight + 400) loadNext();
  }
  retry.addEventListener('click', loadNext);
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadNext();
    }, { rootMargin: '400px' });
    observer.observe(sentinel);
  } else {
    window.addEventListener('scroll', maybeLoad, { passive: true });
    window.addEventListener('resize', maybeLoad);
  }
  backTop?.addEventListener('click', () => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    filters.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  });
  function updateBackTop() {
    if (backTop) backTop.hidden = window.scrollY < 300;
  }
  window.addEventListener('scroll', updateBackTop, { passive: true });
  update();
  updateBackTop();
  status.textContent = nextPage ? `${boxes().length} items loaded. Scroll for more.` : 'All matching questions loaded.';
})();
