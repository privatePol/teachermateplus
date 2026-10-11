/* Read-only event transport. HTTP recovery owns every game-state decision. */
(() => {
  "use strict";
  window.QuiTizzRealtime = function ({url, prepare, recover, indicator, onDisconnect}) {
    let socket = null, stopped = false, connected = false, attempt = 0;
    let recoveryTimer, retryTimer, heartbeatTimer, pongTimer, eventTimer;
    let boundaryTimer, boundaryKey = "", boundaryRetries = 0;
    let recovering = false, dirty = false;
    const label = (text) => { if (indicator) indicator.textContent = text; };
    async function refresh() {
      if (stopped) return;
      window.clearTimeout(eventTimer); eventTimer = null;
      if (recovering) { dirty = true; return; }
      recovering = true;
      try {
        const state = await recover();
        if (state) phaseBoundary(state);
        return state;
      } catch (_) {
        // Recovery failures retain the modest fallback cadence.
      } finally {
        recovering = false;
        if (dirty) { dirty = false; queueRecovery(); }
      }
    }
    function phaseBoundary(state) {
      const nextKey = `${state.version}:${state.phase_deadline}:${Boolean(state.paused)}`;
      if (nextKey !== boundaryKey) { boundaryRetries = 0; boundaryKey = nextKey; }
      window.clearTimeout(boundaryTimer);
      if (stopped || state.paused || !state.phase_deadline || ["COMPLETED", "CANCELLED"].includes(state.status)) return;
      const remaining = Date.parse(state.phase_deadline) - Date.parse(state.server_now);
      if (!Number.isFinite(remaining)) return;
      // One timer, capped retries, single existing HTTP recovery path. The
      // browser cannot reveal/advance even when this display deadline expires.
      if (remaining <= 0 && boundaryRetries++ >= 5) return;
      boundaryTimer = window.setTimeout(() => { boundaryTimer = null; queueRecovery(); },
        remaining > 0 ? remaining + 100 : Math.min(5000, 500 * 2 ** Math.min(boundaryRetries, 3)));
    }
    function queueRecovery() {
      if (recovering) { dirty = true; return; }
      if (eventTimer || stopped) return;
      eventTimer = window.setTimeout(() => { eventTimer = null; void refresh(); }, 100);
    }
    function scheduleRecovery() {
      window.clearTimeout(recoveryTimer);
      if (stopped) return;
      recoveryTimer = window.setTimeout(async () => {
        // Low-frequency canonical recovery also handles silent Redis event loss,
        // missed revocation, background tabs and server permission changes.
        if (!document.hidden) await refresh();
        scheduleRecovery();
      }, connected ? 60000 : 5000);
    }
    function heartbeat() {
      heartbeatTimer = window.setTimeout(() => {
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send("ping");
        pongTimer = window.setTimeout(() => socket.close(), 15000);
        heartbeat();
      }, 30000);
    }
    async function connect() {
      if (stopped) return;
      label(attempt ? "Reconnecting; HTTP recovery active." : "Connecting; HTTP recovery active.");
      try {
        if (prepare) await prepare();
        if (stopped) return;
        const target = new URL(url, window.location.href);
        target.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(target.href);
        socket.onopen = () => {
          // Transport connectivity has no gameplay authorization meaning.
          connected = true; attempt = 0; label("Realtime connected; HTTP recovery active.");
          scheduleRecovery(); heartbeat(); void refresh();
        };
        socket.onmessage = (message) => {
          let event;
          try { event = JSON.parse(message.data); } catch (_) { socket.close(); return; }
          if (event.event === "pong") { window.clearTimeout(pongTimer); return; }
          if (["feature_unavailable", "participant_unavailable"].includes(event.event)) {
            // Toggle can also be ON or an inherited setting changing; HTTP
            // decides availability. A rejected bridge/connect stays on fallback.
            queueRecovery(); socket.close(); return;
          }
          if (!connected) return;
          // Only this constant wakeup is understood. No socket payload reaches
          // host/player renderers, even if a stale/malicious sender adds data.
          if (event.event === "sync_required") queueRecovery();
        };
        socket.onclose = disconnected;
        socket.onerror = () => socket.close();
      } catch (_) { disconnected(); }
    }
    function disconnected() {
      connected = false;
      onDisconnect?.();
      window.clearTimeout(boundaryTimer); boundaryKey = "";
      window.clearTimeout(heartbeatTimer); window.clearTimeout(pongTimer);
      if (stopped) return;
      label("Reconnecting; HTTP recovery active.");
      scheduleRecovery(); queueRecovery();
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, Math.min(30000, 1000 * (2 ** Math.min(attempt++, 5))) + Math.random() * 500);
    }
    function stop() {
      stopped = true;
      [recoveryTimer, retryTimer, heartbeatTimer, pongTimer, eventTimer, boundaryTimer].forEach(window.clearTimeout);
      if (socket) socket.close();
    }
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) queueRecovery(); });
    window.addEventListener("online", () => { queueRecovery(); });
    scheduleRecovery(); void connect();
    return {refresh, stop};
  };
})();
