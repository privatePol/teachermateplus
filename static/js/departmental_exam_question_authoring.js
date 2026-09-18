(() => {
  const form = document.querySelector('[data-question-editor-form]');
  if (!form) return;
  // Keep the editor selection when expanding an advanced group with a pointer.
  // Tiptap's focus command then applies the next tool to that stored selection.
  form.querySelectorAll('.qb-author-advanced > summary').forEach(summary => {
    summary.addEventListener('mousedown', event => event.preventDefault());
  });
  const topbar = document.querySelector('.faculty-topbar');
  if (!topbar) return;
  const sync = () => form.style.setProperty('--qb-author-header-height', `${topbar.getBoundingClientRect().height}px`);
  window.addEventListener('resize', sync);
  if (window.ResizeObserver) new window.ResizeObserver(sync).observe(topbar);
  sync();
})();
