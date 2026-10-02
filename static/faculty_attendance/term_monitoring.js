(() => {
  const root = document.querySelector('[data-term-monitoring]');
  if (!root || !window.fetch || !window.history.pushState) return;
  const content = document.getElementById('term-monitoring-content');
  const status = document.getElementById('term-monitoring-status');
  const errorBox = document.getElementById('term-monitoring-error');
  if (!content || !status || !errorBox) return;
  let sequence = 0;
  let controller;
  const focus = (node) => {
    if (!node) return;
    node.focus({preventScroll: true});
    node.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  const load = async (url, push = true) => {
    const id = ++sequence;
    if (controller) controller.abort();
    controller = window.AbortController ? new window.AbortController() : null;
    root.setAttribute('aria-busy', 'true');
    status.textContent = 'Loading attendance summary and details…';
    errorBox.hidden = true;
    errorBox.textContent = '';
    try {
      const response = await window.fetch(url.href, {
        method: 'GET', credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json'},
        ...(controller ? {signal: controller.signal} : {}),
      });
      if (id !== sequence) return;
      if (response.redirected || !(response.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Unable to load attendance (HTTP ${response.status}). Reload the page and sign in again if needed.`);
      }
      const payload = await response.json();
      if (id !== sequence) return;
      const validation = response.status === 400 && payload.ok === false && typeof payload.html === 'string';
      if ((!response.ok || payload.ok !== true) && !validation) {
        throw new Error(payload.message || `Unable to load attendance (HTTP ${response.status}). Check your authorized scope and retry.`);
      }
      if (typeof payload.html !== 'string') throw new Error('Incomplete attendance response. Retry or reload the page.');
      content.innerHTML = payload.html;
      if (push && url.href !== window.location.href) window.history.pushState({}, '', url.href);
      status.textContent = payload.message || 'Attendance loaded.';
      if (validation) {
        focus(content.querySelector('[aria-invalid="true"]') || content.querySelector('.errorlist'));
      } else if (payload.has_details) {
        focus(document.getElementById('term-faculty-details'));
      } else {
        focus(document.getElementById('term-summary-card'));
      }
    } catch (error) {
      if (id !== sequence || error.name === 'AbortError') return;
      status.textContent = 'Attendance was not updated.';
      errorBox.textContent = `${error.message || 'Connection problem.'} Existing results have not been updated. Retry or reload this page.`;
      errorBox.hidden = false;
      focus(errorBox);
    } finally {
      if (id === sequence) root.removeAttribute('aria-busy');
    }
  };
  root.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-term-filter]');
    if (!form) return;
    const url = new URL(form.getAttribute('action') || window.location.pathname, window.location.href);
    if (url.origin !== window.location.origin) return;
    url.search = new URLSearchParams(new FormData(form)).toString();
    event.preventDefault();
    load(url);
  });
  root.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-term-details]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.getAttribute('href'), window.location.href);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    load(url);
  });
  window.addEventListener('popstate', () => load(new URL(window.location.href), false));
})();
