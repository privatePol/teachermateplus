(() => {
  'use strict';
  const interval = 600000;
  const scopeKey = 'TeacherMate+_admin_scope';
  const pollers = [];
  const readScope = () => { try { return localStorage.getItem(scopeKey); } catch (_) { return null; } };
  const initialScope = readScope();
  function poll(card, content, status, apply) {
    let timer, controller, stopped = false, generation = 0, resume = false;
    function schedule() {
      clearTimeout(timer);
      if (!stopped && !document.hidden) timer = setTimeout(refresh, interval);
    }
    function clear(message) {
      content.replaceChildren();
      status.textContent = message;
    }
    function invalidate(message) {
      stopped = true;
      generation++;
      clearTimeout(timer);
      controller?.abort();
      clear(message);
    }
    async function refresh() {
      if (stopped || document.hidden || controller) return;
      if (initialScope !== readScope()) { invalidate('Scope changed. Reload the dashboard to continue.'); return; }
      const current = controller = new AbortController(), version = generation;
      let expired = false;
      const timeout = setTimeout(() => { expired = true; current.abort(); clear('Unable to verify current requests. Reload to check your connection and sign-in.'); }, 20000);
      try {
        const response = await fetch(card.dataset.refreshUrl, {
          credentials: 'same-origin', redirect: 'error', signal: current.signal,
          headers: {'X-Requested-With': 'XMLHttpRequest'}, cache: 'no-store'
        });
        if (stopped || expired || document.hidden || version !== generation) return;
        if (initialScope !== readScope()) { invalidate('Scope changed. Reload the dashboard to continue.'); return; }
        if ([401, 403, 409].includes(response.status)) {
          invalidate('Access or academic scope changed. Reload the dashboard to continue.'); return;
        }
        if (!response.ok) throw new Error('Unavailable');
        await apply(response, () => {
          if (initialScope !== readScope()) invalidate('Scope changed. Reload the dashboard to continue.');
          return !stopped && !expired && !document.hidden && version === generation;
        });
        if (!stopped && !expired && !document.hidden && version === generation) status.textContent = 'Updated. Next check in 10 minutes.';
      } catch (_) {
        if (!document.hidden && !stopped && version === generation) clear('Unable to verify current requests. Reload to check your connection and sign-in.');
      } finally {
        clearTimeout(timeout);
        if (controller === current) controller = null;
        if (resume && !stopped && !document.hidden) { resume = false; refresh(); }
        else schedule();
      }
    }
    pollers.push({invalidate, visibility() {
      clearTimeout(timer);
      generation++;
      if (document.hidden) controller?.abort();
      else {
        // A hidden tab may have missed a scope/permission change. Verify before
        // exposing its previous counts/actions, including when storage is blocked.
        clear('Verifying current access and scope…');
        if (!controller) refresh();
        else { resume = true; controller.abort(); }
      }
    }});
    schedule();
  }
  const reopen = document.querySelector('#dashboard-reopen');
  if (reopen) poll(reopen, reopen.querySelector('[data-reopen-content]'), reopen.querySelector('[data-reopen-status]'), async (response, valid) => {
    if (!response.headers.get('content-type')?.includes('text/html')) throw new Error('Unavailable');
    const html = await response.text();
    if (valid()) reopen.querySelector('[data-reopen-content]').innerHTML = html;
  });
  const queues = document.querySelector('#dashboard-queue-counts');
  if (queues) poll(queues, queues.querySelector('[data-queue-content]'), queues.querySelector('[data-queue-status]'), async (response, valid) => {
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Unavailable');
    const data = await response.json();
    if (!valid()) return;
    if (data.scope_token !== queues.dataset.scopeToken) throw new Error('Scope mismatch');
    // DOM nodes retained privately allow a verified refresh to restore content
    // cleared while hidden/offline, but never create initially unauthorized cards.
    for (const [name, section] of queueSections) {
      const row = data[name];
      if (row === null) { section.remove(); continue; }
      if (!row || typeof row.configured !== 'boolean' || (row.pending_count !== null && (!Number.isInteger(row.pending_count) || row.pending_count < 0))) throw new Error('Invalid queue');
      const link = section.querySelector('[data-queue-link]');
      if (typeof row.url !== 'string' || !row.url.startsWith('/admin-portal/') || row.url.startsWith('//')) throw new Error('Invalid destination');
      link.href = row.url;
      section.querySelector('[data-queue-count]').textContent = row.pending_count === null ? 'Unavailable' : `${row.pending_count} pending`;
      section.querySelector('[data-queue-config]').hidden = row.configured;
      queues.querySelector('[data-queue-content]').append(section);
    }
  });
  const queueSections = queues ? [...queues.querySelectorAll('[data-queue]')].map(section => [section.dataset.queue, section]) : [];
  function invalidate() { pollers.forEach(p => p.invalidate('Access or academic scope changed. Reload the dashboard to continue.')); }
  document.addEventListener('visibilitychange', () => pollers.forEach(p => p.visibility()));
  window.addEventListener('focus', () => { if (!document.hidden) pollers.forEach(p => p.visibility()); });
  window.addEventListener('admin-scope-changed', invalidate);
  let channel;
  try {
    channel = new BroadcastChannel(scopeKey);
    channel.onmessage = event => {
      if (event.data?.changed || event.data?.scope !== window.adminDashboardScope) invalidate();
    };
  } catch (_) { /* Server token, storage and focus checks remain authoritative. */ }
  window.addEventListener('storage', event => { if (event.key === scopeKey || event.key === null) invalidate(); });
  document.querySelectorAll('.admin-scope-form select').forEach(select => select.addEventListener('change', invalidate));
  window.addEventListener('pagehide', () => { invalidate(); channel?.close(); });
  window.addEventListener('pageshow', event => { if (event.persisted) invalidate(); });
})();
