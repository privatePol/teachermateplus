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
  const layout = document.querySelector('[data-reuse-layout]');
  const topbar = document.querySelector('.faculty-topbar');
  const toolbar = form.querySelector('[data-reuse-toolbar]');
  const indexPanel = layout.querySelector('[data-reuse-index-panel]');
  const indexList = layout.querySelector('[data-reuse-index-list]');
  const indexEmpty = layout.querySelector('[data-reuse-index-empty]');
  const indexClose = layout.querySelector('[data-reuse-index-close]');
  const indexReopen = layout.querySelector('[data-reuse-index-reopen]');
  const narrow = window.matchMedia?.('(max-width: 1199.98px)');
  const remaining = Number(form.dataset.remaining);
  let nextPage = Number(form.dataset.nextPage) || null;
  let loading = false;
  let selectedOnly = false;
  let submitting = false;
  let filterChanged = false;
  let loadFailed = false;
  let generation = 0;
  let controller = null;
  let browseAnchor = null;
  let indexEntries = [];
  let currentTargetId = null;
  let currentFrame = null;
  const initialFilters = new URLSearchParams(new FormData(filters)).toString();
  const tokens = new Set([...cards.querySelectorAll('[data-reuse-item]')].map(item => item.dataset.reuseToken));

  function headerHeight() { return topbar?.getBoundingClientRect().height || 0; }
  function toolbarHeight() { return toolbar.getBoundingClientRect().height || 0; }
  function syncOffsets() {
    layout.style.setProperty('--reuse-header-height', `${headerHeight()}px`);
    layout.style.setProperty('--reuse-toolbar-height', `${toolbarHeight()}px`);
    scheduleCurrent();
  }

  function setIndexOpen(open) {
    layout.classList.toggle('index-collapsed', !open);
    indexPanel.setAttribute('aria-hidden', String(!open));
    indexPanel.inert = !open;
    indexReopen.setAttribute('aria-expanded', String(open));
    if (open) scheduleCurrent();
  }

  function indexLabel(element) {
    return (element?.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function renderIndex() {
    const previousScroll = indexPanel.scrollTop;
    const fragment = document.createDocumentFragment();
    const entries = [];
    let number = 0;
    function add(target, label, kind, selected) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `reuse-index-entry is-${kind}`;
      button.dataset.reuseIndexTarget = target.id;
      button.append(document.createTextNode(label));
      if (selected) {
        const marker = document.createElement('span');
        marker.className = 'reuse-index-selected';
        marker.setAttribute('aria-label', 'Selected for copy');
        marker.textContent = '✓';
        button.append(marker);
      }
      fragment.append(button);
      entries.push({ target, button });
    }
    cards.querySelectorAll('[data-reuse-item]').forEach(card => {
      if (card.hidden) return;
      const selected = card.querySelector('[data-reuse-checkbox]').checked;
      if (card.dataset.kind === 'case') {
        add(card, indexLabel(card.querySelector('[data-reuse-index-title]')), 'case', selected);
        card.querySelectorAll('[data-reuse-member]').forEach(member => {
          number += 1;
          add(member, `${number}. ${indexLabel(member.querySelector('.reuse-question-stem'))}`, 'member', selected);
        });
      } else {
        number += 1;
        add(card, `${number}. ${indexLabel(card.querySelector('[data-reuse-index-title]'))}`, 'question', selected);
      }
    });
    indexEntries = entries;
    indexList.replaceChildren(fragment);
    indexPanel.scrollTop = previousScroll;
    indexEmpty.hidden = entries.length > 0;
    currentTargetId = null;
    scheduleCurrent();
  }

  function revealIndexEntry(button) {
    if (layout.classList.contains('index-collapsed')) return;
    const panelRect = indexPanel.getBoundingClientRect();
    const headingBottom = indexPanel.querySelector('.reuse-index-heading').getBoundingClientRect().bottom;
    const entryRect = button.getBoundingClientRect();
    if (entryRect.top < headingBottom) indexPanel.scrollTop -= headingBottom - entryRect.top + 4;
    else if (entryRect.bottom > panelRect.bottom) indexPanel.scrollTop += entryRect.bottom - panelRect.bottom + 4;
  }

  function updateCurrent() {
    currentFrame = null;
    const readingLine = headerHeight() + toolbarHeight() + 20;
    let active = indexEntries[0];
    for (const entry of indexEntries) {
      if (entry.target.getBoundingClientRect().top <= readingLine) active = entry;
      else break;
    }
    const nextId = active?.target.id || null;
    if (nextId === currentTargetId) return;
    currentTargetId = nextId;
    indexEntries.forEach(entry => {
      const current = entry.target.id === nextId;
      entry.button.classList.toggle('is-current', current);
      if (current) entry.button.setAttribute('aria-current', 'location');
      else entry.button.removeAttribute('aria-current');
    });
    cards.querySelectorAll('[data-reuse-item].is-reading').forEach(card => card.classList.remove('is-reading'));
    active?.target.closest('[data-reuse-item]')?.classList.add('is-reading');
    if (active) revealIndexEntry(active.button);
  }

  function scheduleCurrent() {
    if (currentFrame !== null) return;
    currentFrame = window.requestAnimationFrame ? window.requestAnimationFrame(updateCurrent) : window.setTimeout(updateCurrent, 0);
  }

  function captureBrowseAnchor() {
    const line = headerHeight() + toolbarHeight() + 20;
    const visible = [...cards.querySelectorAll('[data-reuse-item]')].filter(card => !card.hidden);
    const card = visible.find(item => item.getBoundingClientRect().bottom > line) || visible.at(-1);
    return card ? { token: card.dataset.reuseToken, offset: card.getBoundingClientRect().top } : null;
  }

  function restoreBrowseAnchor() {
    if (!browseAnchor) return;
    const card = [...cards.querySelectorAll('[data-reuse-item]')]
      .find(item => item.dataset.reuseToken === browseAnchor.token && !item.hidden);
    if (card) {
      const displacement = card.getBoundingClientRect().top - browseAnchor.offset;
      if (Math.abs(displacement) > 1) window.scrollTo({ top: Math.max(0, window.scrollY + displacement), behavior: 'auto' });
    }
    browseAnchor = null;
    scheduleCurrent();
  }

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
    renderIndex();
    syncOffsets();
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
    if (!selectedOnly) browseAnchor = captureBrowseAnchor();
    selectedOnly = !selectedOnly;
    if (selectedOnly && controller) {
      generation += 1;
      controller.abort();
    }
    selectedOnlyButton.setAttribute('aria-pressed', String(selectedOnly));
    selectedOnlyButton.textContent = selectedOnly ? 'Show all questions' : 'Show all selected';
    update();
    if (!selectedOnly) restoreBrowseAnchor();
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
    browseAnchor = null;
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
  window.addEventListener('scroll', scheduleCurrent, { passive: true });
  window.addEventListener('resize', syncOffsets);
  if (window.ResizeObserver) {
    const sizes = new window.ResizeObserver(syncOffsets);
    if (topbar) sizes.observe(topbar);
    sizes.observe(toolbar);
    sizes.observe(cards);
  }
  cards.addEventListener('load', scheduleCurrent, true);
  narrow?.addEventListener?.('change', event => {
    if (event.matches) setIndexOpen(false);
    syncOffsets();
  });
  indexClose.addEventListener('click', () => {
    setIndexOpen(false);
    indexReopen.focus({ preventScroll: true });
  });
  indexReopen.addEventListener('click', () => {
    setIndexOpen(true);
    indexClose.focus({ preventScroll: true });
  });
  indexList.addEventListener('click', event => {
    const button = event.target.closest('[data-reuse-index-target]');
    if (!button) return;
    const target = document.getElementById(button.dataset.reuseIndexTarget);
    if (!target || target.closest('[data-reuse-item]')?.hidden) return;
    const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - headerHeight() - toolbarHeight() - 20);
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' });
    target.classList.add('reuse-nav-flash');
    window.setTimeout(() => target.classList.remove('reuse-nav-flash'), 1500);
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    if (narrow?.matches) setIndexOpen(false);
    scheduleCurrent();
  });
  setIndexOpen(!narrow?.matches);
  update();
  updateBackTop();
  status.textContent = nextPage ? `${boxes().length} items loaded. Scroll for more.` : 'All matching questions loaded.';
})();
