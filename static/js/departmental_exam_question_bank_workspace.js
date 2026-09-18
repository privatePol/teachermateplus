(() => {
  const workspace = document.querySelector('[data-question-bank-workspace]');
  if (!workspace) return;
  const layout = workspace.querySelector('[data-qb-layout]');
  const list = workspace.querySelector('[data-qb-question-list]');
  const panel = workspace.querySelector('[data-qb-index-panel]');
  const indexList = workspace.querySelector('[data-qb-index-list]');
  const empty = workspace.querySelector('[data-qb-index-empty]');
  const close = workspace.querySelector('[data-qb-index-close]');
  const reopen = workspace.querySelector('[data-qb-index-reopen]');
  const topbar = document.querySelector('.faculty-topbar');
  const toolbar = workspace.querySelector('[data-qb-action-toolbar]');
  const filter = workspace.querySelector('[data-bulk-question-filter]');
  const narrow = window.matchMedia?.('(max-width: 1199.98px)');
  let entries = [];
  let currentId = null;
  let frame = null;

  const headerHeight = () => topbar?.getBoundingClientRect().height || 0;
  const toolbarHeight = () => toolbar?.getBoundingClientRect().height || 0;
  const label = element => (element?.textContent || '').replace(/\s+/g, ' ').trim();
  const visibleForFilter = card => !card.hidden;
  function setOpen(open) {
    layout.classList.toggle('index-collapsed', !open);
    panel.setAttribute('aria-hidden', String(!open));
    panel.inert = !open;
    reopen.setAttribute('aria-expanded', String(open));
    if (open) scheduleCurrent();
  }
  function syncHeader() {
    workspace.style.setProperty('--qb-header-height', `${headerHeight()}px`);
    workspace.style.setProperty('--qb-toolbar-height', `${toolbarHeight()}px`);
    scheduleCurrent();
  }
  function addEntry(fragment, target, text, kind, selected) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `qb-index-entry is-${kind}`;
    button.dataset.qbIndexTarget = target.id;
    button.append(document.createTextNode(text));
    if (selected) {
      const mark = document.createElement('span');
      mark.className = 'qb-index-selected';
      mark.setAttribute('aria-label', 'Selected for deletion');
      mark.textContent = '✓';
      button.append(mark);
    }
    fragment.append(button);
    entries.push({target, button});
  }
  function renderIndex() {
    const scroll = panel.scrollTop;
    const fragment = document.createDocumentFragment();
    entries = [];
    list.querySelectorAll('.question-card').forEach(card => card.classList.toggle('is-deletion-selected', Boolean(card.querySelector('[data-bulk-question]')?.checked)));
    list.querySelectorAll('.qb-case-card, .question-card').forEach(item => {
      if (item.classList.contains('qb-case-card')) {
        const members = [...item.querySelectorAll('.question-card')].filter(visibleForFilter);
        if (!members.length && item.querySelector('.question-card')) return;
        if (!members.length && filter?.value !== 'all') return;
        addEntry(fragment, item, item.dataset.qbCaseTitle || 'Case / Scenario', 'case', false);
        members.forEach(card => addEntry(fragment, card, `Question ${label(card.querySelector('.question-position'))}: ${label(card.querySelector('[data-question-index-label]'))}`, 'member', card.querySelector('[data-bulk-question]')?.checked));
      } else if (!item.closest('.qb-case-card') && visibleForFilter(item)) {
        addEntry(fragment, item, `Question ${label(item.querySelector('.question-position'))}: ${label(item.querySelector('[data-question-index-label]'))}`, 'question', item.querySelector('[data-bulk-question]')?.checked);
      }
    });
    indexList.replaceChildren(fragment);
    panel.scrollTop = scroll;
    empty.hidden = entries.length > 0;
    currentId = null;
    scheduleCurrent();
  }
  function reveal(button) {
    if (layout.classList.contains('index-collapsed')) return;
    const bottom = panel.getBoundingClientRect().bottom;
    const heading = panel.querySelector('.qb-index-heading').getBoundingClientRect().bottom;
    const rect = button.getBoundingClientRect();
    if (rect.top < heading) panel.scrollTop -= heading - rect.top + 4;
    else if (rect.bottom > bottom) panel.scrollTop += rect.bottom - bottom + 4;
  }
  function updateCurrent() {
    frame = null;
    const line = headerHeight() + toolbarHeight() + 20;
    let active = null;
    for (const entry of entries) {
      if (!entry.target.getClientRects().length) continue;
      if (entry.target.getBoundingClientRect().top <= line) active = entry;
      else if (active) break;
    }
    if (!active) active = entries.find(entry => entry.target.getClientRects().length);
    if (active?.target.id === currentId) return;
    currentId = active?.target.id || null;
    entries.forEach(entry => {
      const reading = entry.target.id === currentId;
      entry.button.classList.toggle('is-current', reading);
      if (reading) entry.button.setAttribute('aria-current', 'location');
      else entry.button.removeAttribute('aria-current');
      entry.target.classList.toggle('is-reading', reading && entry.target.classList.contains('question-card'));
    });
    if (active) reveal(active.button);
  }
  function scheduleCurrent() {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(updateCurrent);
  }
  function jump(target) {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - headerHeight() - toolbarHeight() - 20);
    window.scrollTo({top, behavior: reduced ? 'auto' : 'smooth'});
    target.classList.add('qb-nav-flash');
    window.setTimeout(() => target.classList.remove('qb-nav-flash'), 1500);
    target.tabIndex = -1;
    target.focus({preventScroll: true});
    if (narrow?.matches) setOpen(false);
    scheduleCurrent();
  }
  function navigate(target) {
    const disclosure = target.closest('.qb-case-card')?.querySelector('.tmp-case-collapse');
    if (disclosure && !disclosure.classList.contains('show')) {
      disclosure.addEventListener('shown.bs.collapse', () => jump(target), {once: true});
      if (window.bootstrap?.Collapse) window.bootstrap.Collapse.getOrCreateInstance(disclosure, {toggle: false}).show();
      else { disclosure.classList.add('show'); jump(target); }
    } else jump(target);
  }
  indexList.addEventListener('click', event => {
    const button = event.target.closest('[data-qb-index-target]');
    if (!button) return;
    const target = document.getElementById(button.dataset.qbIndexTarget);
    if (target && (!target.classList.contains('question-card') || visibleForFilter(target))) navigate(target);
  });
  close.addEventListener('click', () => { setOpen(false); reopen.focus({preventScroll: true}); });
  reopen.addEventListener('click', () => { setOpen(true); close.focus({preventScroll: true}); });
  filter?.addEventListener('change', renderIndex);
  workspace.querySelectorAll('[data-bulk-question]').forEach(box => box.addEventListener('change', () => {
    box.closest('.question-card').classList.toggle('is-deletion-selected', box.checked);
    renderIndex();
  }));
  workspace.querySelector('[data-bulk-select-all]')?.addEventListener('change', () => {
    workspace.querySelectorAll('[data-bulk-question]').forEach(box => box.closest('.question-card').classList.toggle('is-deletion-selected', box.checked));
    renderIndex();
  });
  document.addEventListener('tmp:question-bank-order-changed', renderIndex);
  document.addEventListener('shown.bs.collapse', () => { renderIndex(); scheduleCurrent(); });
  document.addEventListener('hidden.bs.collapse', () => { renderIndex(); scheduleCurrent(); });
  window.addEventListener('scroll', scheduleCurrent, {passive: true});
  window.addEventListener('resize', syncHeader);
  if (window.ResizeObserver) {
    const sizes = new window.ResizeObserver(syncHeader);
    if (topbar) sizes.observe(topbar);
    if (toolbar) sizes.observe(toolbar);
  }
  narrow?.addEventListener?.('change', event => { if (event.matches) setOpen(false); syncHeader(); });
  setOpen(!narrow?.matches);
  syncHeader();
  renderIndex();
})();
