(() => {
  'use strict';
  const card = document.querySelector('#dashboard-reopen');
  if (!card) return;
  const content = card.querySelector('[data-reopen-content]');
  const status = card.querySelector('[data-reopen-status]');
  const interval = 600000;
  let timer, controller, stopped = false;
  function schedule() {
    clearTimeout(timer);
    if (!stopped && !document.hidden) timer = setTimeout(refresh, interval);
  }
  function invalidate(message) {
    stopped = true;
    content.replaceChildren();
    status.textContent = message;
    clearTimeout(timer);
  }
  async function refresh() {
    if (stopped || document.hidden || controller) return;
    const current = controller = new AbortController();
    const timeout = setTimeout(() => current.abort(), 20000);
    try {
      const response = await fetch(card.dataset.refreshUrl, {
        credentials: 'same-origin', redirect: 'error', signal: current.signal,
        headers: {'X-Requested-With': 'XMLHttpRequest'}, cache: 'no-store'
      });
      if ([401, 403, 409].includes(response.status)) {
        invalidate('Access or academic scope changed. Reload the dashboard to continue.');
        return;
      }
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw new Error('Unavailable');
      const html = await response.text();
      if (document.hidden || stopped) return;
      content.innerHTML = html;
      status.textContent = 'Updated. Next check in 10 minutes.';
    } catch (error) {
      if (!document.hidden && !stopped) {
        // A redirect (including expired login) must never inject a sign-in page.
        content.replaceChildren();
        status.textContent = 'Unable to verify current requests. Reload to check your connection and sign-in.';
      }
    } finally {
      clearTimeout(timeout);
      if (controller === current) controller = null;
      schedule();
    }
  }
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    if (document.hidden) controller?.abort(); else schedule();
  });
  document.querySelectorAll('.admin-scope-form select').forEach(select => select.addEventListener('change', () => {
    invalidate('Scope changed. Loading dashboard…'); controller?.abort();
  }));
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); controller?.abort(); });
  schedule();
})();
